// Playback engine: all stems in ONE multichannel Signalsmith Stretch node, so every stem shares
// the same read position and time-stretch map (no inter-stem drift at any speed).
//
//   stretch (2*N ch) -> splitter -> [merger(2) -> stem gain -> stem FX] x N
//     -> headroom gain -> master FX -> limiter -> master volume -> out
//
// Position model: the node maps output (context) time t to input time
//   p(t) = input + (t - output) * rate   (wrapped into the loop)
// and compensates its own latency. We keep the same list of scheduled segments on the main
// thread and evaluate it at the context time currently heard from the speakers
// (AudioContext.getOutputTimestamp), so every view draws from a single clock.

import SignalsmithStretch from '../vendor/SignalsmithStretch.mjs';
import { FxChain, measureCompressorLatency } from './fx.js';

export const SAMPLE_RATE = 44100;

export class AudioEngine extends EventTarget {
  constructor() {
    super();
    this.ctx = null;
    this.stretch = null;
    this.stemNames = [];
    this.gainNodes = {};
    this.duration = 0;
    this.playing = false;
    this.rate = 1;
    this.loop = { enabled: false, a: 0, b: 0 };
    this.pausedPos = 0;
    this.segs = [];
    this.ahead = 0.06;
    this.limiterGr = 0;
    this.limiterMaxOut = 0;
    this.headroom = 1;
    this.preset = 'default';
    this.limiterLookahead = 0.005; // the limiter delays the audio by its look-ahead
    this.fxLatency = 0; // compressor look-ahead of the stem + master FX chains (measured in init)
    this.stemFx = {};
    this.volume = 1;
  }

  // Everything between the stretch node and the speakers that delays the audio.
  get outputDelay() {
    return this.limiterLookahead + this.fxLatency;
  }

  async init() {
    if (this.ctx) return;
    this.ctx = new AudioContext({ sampleRate: SAMPLE_RATE, latencyHint: 'interactive' });
    await this.ctx.audioWorklet.addModule(new URL('./limiter-worklet.js', import.meta.url));
    this.headroomNode = this.ctx.createGain();
    this.limiter = new AudioWorkletNode(this.ctx, 'bss-limiter', {
      numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [2],
      processorOptions: { thresholdDb: -1, lookaheadMs: this.limiterLookahead * 1000, releaseMs: 80 },
    });
    this.limiter.port.onmessage = (e) => {
      this.limiterGr = -20 * Math.log10(Math.max(e.data.minGain, 1e-6));
      this.limiterMaxOut = e.data.maxOut;
      this.dispatchEvent(new CustomEvent('limiter', { detail: { grDb: this.limiterGr, maxOut: e.data.maxOut } }));
    };
    this.masterFx = new FxChain(this.ctx);
    this.volumeNode = this.ctx.createGain();
    this.volumeNode.gain.value = this.volume;
    this.headroomNode.connect(this.masterFx.input);
    this.masterFx.output.connect(this.limiter);
    this.limiter.connect(this.volumeNode);
    this.volumeNode.connect(this.ctx.destination);
    // each path crosses two compressors (stem chain + master chain)
    this.fxLatency = 2 * (await measureCompressorLatency(this.ctx.sampleRate));
  }

  async resume() {
    if (this.ctx && this.ctx.state !== 'running') await this.ctx.resume();
  }

  // ---- loading --------------------------------------------------------------------------
  async load(stems, onProgress = () => {}) {
    await this.init();
    this.stop();
    this._teardownStretch();
    const n = stems.length;
    const channels = [];
    let length = null;
    for (let i = 0; i < n; i++) {
      onProgress(i, n, `${stems[i].label || stems[i].name} を読み込み中`);
      const res = await fetch(stems[i].url, { cache: 'no-cache' });
      if (!res.ok) throw new Error(`${stems[i].name}: ${res.status}`);
      const buf = await this.ctx.decodeAudioData(await res.arrayBuffer());
      if (length === null) length = buf.length;
      const l = new Float32Array(length);
      const r = new Float32Array(length);
      l.set(buf.getChannelData(0).subarray(0, length));
      r.set((buf.numberOfChannels > 1 ? buf.getChannelData(1) : buf.getChannelData(0)).subarray(0, length));
      if (buf.length !== length) console.warn(`stem ${stems[i].name} length ${buf.length} != ${length}`);
      channels.push(l, r);
    }
    onProgress(n, n, '伸縮エンジンを準備中');
    this.duration = length / this.ctx.sampleRate;
    this.stemNames = stems.map((s) => s.name);
    const nch = 2 * n;
    this.stretch = await SignalsmithStretch(this.ctx, {
      numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [nch], channelCount: 2,
    });
    if (this.preset !== 'default') await this.stretch.configure({ preset: this.preset });
    this.stretchLatency = await this.stretch.latency();
    this.ahead = Math.max(0.05, Math.min(0.25, this.stretchLatency));
    // hand the sample data over without copying (transfer)
    await this.stretch.addBuffers(channels, channels.map((c) => c.buffer));
    this.splitter = this.ctx.createChannelSplitter(nch);
    this.stretch.connect(this.splitter);
    this.gainNodes = {};
    this.mergers = [];
    stems.forEach((s, i) => {
      const m = this.ctx.createChannelMerger(2);
      this.splitter.connect(m, 2 * i, 0);
      this.splitter.connect(m, 2 * i + 1, 1);
      const g = this.ctx.createGain();
      m.connect(g);
      const fx = this.stemFx[s.name] || new FxChain(this.ctx);
      this.stemFx[s.name] = fx;
      g.connect(fx.input);
      fx.output.connect(this.headroomNode);
      this.gainNodes[s.name] = g;
      this.mergers.push(m);
    });
    this.segs = [];
    this.pausedPos = 0;
    this.dispatchEvent(new Event('loaded'));
  }

  _teardownStretch() {
    if (this.stretch) {
      try { this.stretch.stop(); } catch (_) { /* ignore */ }
      try { this.stretch.dropBuffers(); } catch (_) { /* ignore */ }
      try { this.stretch.disconnect(); } catch (_) { /* ignore */ }
    }
    for (const g of Object.values(this.gainNodes)) g.disconnect();
    // only the bus connection: the chain keeps its analyser tap
    for (const fx of Object.values(this.stemFx)) { try { fx.output.disconnect(this.headroomNode); } catch (_) { /* not connected */ } }
    (this.mergers || []).forEach((m) => m.disconnect());
    if (this.splitter) this.splitter.disconnect();
    this.stretch = null;
    this.gainNodes = {};
  }

  get loaded() {
    return !!this.stretch;
  }

  // ---- clock ----------------------------------------------------------------------------
  // Context time of the sample currently coming out of the speakers.
  heardTime() {
    const ctx = this.ctx;
    if (!ctx) return 0;
    if (ctx.getOutputTimestamp) {
      const ts = ctx.getOutputTimestamp();
      if (ts.contextTime > 0 && ts.performanceTime > 0) {
        return ts.contextTime + (performance.now() - ts.performanceTime) / 1000;
      }
    }
    return ctx.currentTime - (ctx.outputLatency || 0) - (ctx.baseLatency || 0);
  }

  _segAt(t) {
    let seg = this.segs[0];
    for (const s of this.segs) {
      if (s.output <= t) seg = s;
      else break;
    }
    return seg;
  }

  _map(seg, t) {
    if (!seg) return this.pausedPos;
    const dt = Math.max(0, t - seg.output);
    let p = seg.input + (seg.active ? dt * seg.rate : 0);
    const L = seg.loopEnd - seg.loopStart;
    if (L > 0 && p >= seg.loopEnd) p = seg.loopStart + ((p - seg.loopStart) % L);
    return p;
  }

  inputAt(t) {
    return this._map(this._segAt(t), t);
  }

  position() {
    if (!this.playing || !this.segs.length) return this.pausedPos;
    const t = this.heardTime() - this.outputDelay;
    const first = this.segs[0];
    if (t < first.output) return first.input; // just (re)started: audio not out yet
    return Math.min(this.duration, Math.max(0, this.inputAt(t)));
  }

  _schedule(fields) {
    if (!this.stretch) return;
    const tNow = this.ctx.currentTime;
    const t = tNow + this.ahead;
    const prev = this._segAt(t);
    const input = fields.input !== undefined ? fields.input : this._map(prev, t);
    const loopOn = this.loop.enabled && this.loop.b - this.loop.a > 0.05;
    const seg = {
      active: fields.active !== undefined ? fields.active : prev ? prev.active : false,
      input,
      output: t,
      rate: this.rate,
      loopStart: loopOn ? this.loop.a : 0,
      loopEnd: loopOn ? this.loop.b : 0,
    };
    // Drop segments the new one supersedes. Keep older ones only while they may still be
    // audible (output latency can reach several hundred ms on Bluetooth devices).
    this.segs = this.segs.filter((s) => s.output < t);
    let first = 0;
    for (let i = 0; i < this.segs.length; i++) if (this.segs[i].output <= tNow - 1) first = i;
    this.segs = this.segs.slice(first);
    this.segs.push(seg);
    this.stretch.schedule({ ...seg });
    return seg;
  }

  // ---- transport ------------------------------------------------------------------------
  async play(from) {
    if (!this.stretch) return;
    await this.resume();
    let p = from !== undefined ? from : this.pausedPos;
    if (p >= this.duration - 0.01) p = 0;
    if (this.loop.enabled && (p < this.loop.a || p >= this.loop.b)) p = this.loop.a;
    this.segs = [];
    this._schedule({ active: true, input: p });
    this.playing = true;
    this.dispatchEvent(new Event('state'));
  }

  pause() {
    if (!this.stretch || !this.playing) return;
    const p = this.position();
    this._schedule({ active: false, input: p });
    this.pausedPos = p;
    this.playing = false;
    this.dispatchEvent(new Event('state'));
  }

  stop() {
    if (this.playing) this.pause();
    this.pausedPos = this.loop.enabled ? this.loop.a : 0;
    this.dispatchEvent(new Event('state'));
  }

  seek(p) {
    p = Math.max(0, Math.min(this.duration, p));
    if (this.playing) {
      this.segs = [];
      this._schedule({ active: true, input: p });
    } else {
      this.pausedPos = p;
    }
    this.dispatchEvent(new Event('seek'));
  }

  setRate(r) {
    this.rate = r;
    if (this.playing) this._schedule({});
  }

  setLoop(enabled, a, b) {
    this.loop = { enabled: !!enabled, a: Math.min(a, b), b: Math.max(a, b) };
    if (this.playing) {
      const p = this.position();
      if (this.loop.enabled && (p < this.loop.a || p >= this.loop.b)) {
        this.segs = [];
        this._schedule({ active: true, input: this.loop.a });
      } else {
        this._schedule({});
      }
    } else if (this.loop.enabled && (this.pausedPos < this.loop.a || this.pausedPos >= this.loop.b)) {
      this.pausedPos = this.loop.a;
    }
  }

  // Called every animation frame: stop at the end when not looping.
  tick() {
    if (this.playing && !this.loop.enabled && this.position() >= this.duration - 0.005) {
      this.pause();
      this.pausedPos = this.duration;
    }
  }

  // ---- gains ----------------------------------------------------------------------------
  setStemGain(name, linear) {
    const g = this.gainNodes[name];
    if (g) g.gain.setTargetAtTime(linear, this.ctx.currentTime, 0.015);
  }

  // Listening volume, after the limiter (does not change the peak control).
  setVolume(linear) {
    this.volume = linear;
    if (this.volumeNode) this.volumeNode.gain.setTargetAtTime(linear, this.ctx.currentTime, 0.02);
  }

  fxChain(target) {
    return target === 'master' ? this.masterFx : this.stemFx[target];
  }

  setHeadroom(linear) {
    this.headroom = linear;
    if (this.headroomNode) this.headroomNode.gain.setTargetAtTime(linear, this.ctx.currentTime, 0.03);
  }

  async setQuality(preset) {
    this.preset = preset;
    if (this.stretch) {
      await this.stretch.configure({ preset });
      this.stretchLatency = await this.stretch.latency();
    }
  }
}

// Channel-strip effects: HPF -> 6-band EQ -> LPF -> compressor -> output trim.
// One chain per stem plus one on the master bus. All nodes are native Web Audio nodes;
// bypassed filters become 0 dB peaking filters (exact identity), so every chain always has the
// same latency and stems stay sample-aligned whatever is switched on.

export const EQ_RANGE_DB = 15; // band gain range ±15 dB
export const EQ_STEP_DB = 5; // gain snaps to 5 dB steps (Shift = 0.5 dB)

export const BANDS = [
  { id: 'hpf', kind: 'hpf', label: 'HPF', color: '#9aa7b8', fmin: 20, fmax: 1000 },
  { id: 'low', kind: 'lowshelf', label: 'LOW', color: '#e0784a', fmin: 20, fmax: 500 },
  { id: 'lm', kind: 'peaking', label: 'LO-MID', color: '#e0a64a', fmin: 40, fmax: 2000 },
  { id: 'mid', kind: 'peaking', label: 'MID', color: '#d4c85a', fmin: 100, fmax: 5000 },
  { id: 'hm', kind: 'peaking', label: 'HI-MID', color: '#86c070', fmin: 300, fmax: 10000 },
  { id: 'pres', kind: 'peaking', label: 'PRES', color: '#5ab4c4', fmin: 1000, fmax: 16000 },
  { id: 'high', kind: 'highshelf', label: 'HIGH', color: '#9890e0', fmin: 1500, fmax: 20000 },
  { id: 'lpf', kind: 'lpf', label: 'LPF', color: '#9aa7b8', fmin: 500, fmax: 20000 },
];
const EQ_IDS = ['low', 'lm', 'mid', 'hm', 'pres', 'high'];
const BUTTERWORTH_4 = [0.5412, 1.3066]; // Q of the two sections of a 4th-order Butterworth

export function defaultFx() {
  return {
    eq: {
      on: true,
      trim: 0,
      bands: {
        hpf: { on: false, freq: 40, slope: 12 },
        low: { on: true, freq: 100, gain: 0 },
        lm: { on: true, freq: 250, gain: 0, q: 1.0 },
        mid: { on: true, freq: 800, gain: 0, q: 1.0 },
        hm: { on: true, freq: 2500, gain: 0, q: 1.0 },
        pres: { on: true, freq: 5000, gain: 0, q: 1.0 },
        high: { on: true, freq: 10000, gain: 0 },
        lpf: { on: false, freq: 16000, slope: 12 },
      },
    },
    comp: { on: false, threshold: -24, ratio: 4, attack: 10, release: 150, knee: 6, gain: 0 },
  };
}

// Fill missing keys (older projects / partial presets).
export function normalizeFx(fx) {
  const d = defaultFx();
  if (!fx) return d;
  const out = { eq: { ...d.eq, ...(fx.eq || {}), bands: {} }, comp: { ...d.comp, ...(fx.comp || {}) } };
  for (const b of BANDS) out.eq.bands[b.id] = { ...d.eq.bands[b.id], ...((fx.eq && fx.eq.bands && fx.eq.bands[b.id]) || {}) };
  return out;
}

export function isActive(fx) {
  if (!fx) return false;
  const eq = fx.eq;
  const eqActive = eq.on && (eq.trim !== 0 || BANDS.some((b) => {
    const s = eq.bands[b.id];
    return (b.kind === 'hpf' || b.kind === 'lpf') ? s.on : s.on && s.gain !== 0;
  }));
  return eqActive || fx.comp.on;
}

// ---- presets (gains in 5 dB steps) --------------------------------------------------------
const P = (bands, extra = {}) => ({ bands, ...extra });
export const EQ_PRESETS = [
  { name: 'フラット', eq: P({}) },
  { name: 'ベースを聞き取る', eq: P({ low: { gain: 5, freq: 80 }, lm: { gain: -5, freq: 300 }, mid: { gain: 5, freq: 900, q: 1.2 }, hm: { gain: -5, freq: 3000 } }) },
  { name: 'ベースのアタック（指・ピック）', eq: P({ mid: { gain: 5, freq: 1200, q: 1.4 }, hm: { gain: 5, freq: 2500, q: 1.4 } }) },
  { name: 'ギターを聞き取る', eq: P({ hpf: { on: true, freq: 100 }, lm: { gain: -5, freq: 250 }, hm: { gain: 5, freq: 2500 }, pres: { gain: 5, freq: 4500 } }) },
  { name: 'ボーカルを控えめに', eq: P({ mid: { gain: -5, freq: 1000, q: 0.8 }, hm: { gain: -10, freq: 2800, q: 0.8 } }) },
  { name: '低音ブースト', eq: P({ low: { gain: 10, freq: 90 } }) },
  { name: '小音量でも聞きやすく', eq: P({ low: { gain: 5, freq: 100 }, high: { gain: 5, freq: 8000 } }) },
  { name: 'ロック', eq: P({ low: { gain: 5 }, mid: { gain: -5, freq: 700 }, pres: { gain: 5 }, high: { gain: 5 } }) },
  { name: 'ジャズ', eq: P({ low: { gain: 5 }, lm: { gain: 5, freq: 300 }, high: { gain: -5 } }) },
  { name: 'ダンス / エレクトロ', eq: P({ low: { gain: 10, freq: 70 }, mid: { gain: -5, freq: 600 }, high: { gain: 5, freq: 9000 } }) },
  { name: 'ヒップホップ', eq: P({ low: { gain: 10, freq: 60 }, lm: { gain: 5, freq: 150 }, hm: { gain: -5 } }) },
  { name: 'アコースティック', eq: P({ lm: { gain: -5, freq: 300 }, hm: { gain: 5, freq: 3000 }, high: { gain: 5 } }) },
  { name: 'ラテン', eq: P({ low: { gain: 5 }, hm: { gain: 5, freq: 3500 }, high: { gain: 5 } }) },
  { name: 'こもり取り（ローカット）', eq: P({ hpf: { on: true, freq: 60, slope: 24 }, lm: { gain: -5, freq: 300 } }) },
  { name: '電話風（帯域制限）', eq: P({ hpf: { on: true, freq: 400, slope: 24 }, lpf: { on: true, freq: 3000, slope: 24 }, mid: { gain: 5, freq: 1200 } }) },
];
export const COMP_PRESETS = [
  { name: 'オフ', comp: { on: false } },
  { name: '軽く整える', comp: { on: true, threshold: -18, ratio: 2, attack: 20, release: 200, knee: 10, gain: 2 } },
  { name: '音量を揃える（AVO 相当）', comp: { on: true, threshold: -28, ratio: 12, attack: 3, release: 250, knee: 30, gain: 3 } },
  { name: 'ベースのアタックを残す', comp: { on: true, threshold: -20, ratio: 4, attack: 30, release: 100, knee: 6, gain: 3 } },
  { name: '音量ブースト（リミッター任せ）', comp: { on: true, threshold: -12, ratio: 3, attack: 5, release: 150, knee: 12, gain: 9 } },
];

export function applyEqPreset(fx, preset) {
  const d = defaultFx();
  const eq = { ...fx.eq, bands: {} };
  for (const b of BANDS) eq.bands[b.id] = { ...d.eq.bands[b.id], ...(preset.eq.bands[b.id] || {}) };
  return { ...fx, eq };
}

export function applyCompPreset(fx, preset) {
  return { ...fx, comp: { ...fx.comp, ...preset.comp } };
}

// ---- the chain ------------------------------------------------------------------------------
export class FxChain {
  constructor(ctx) {
    this.ctx = ctx;
    this.input = ctx.createGain();
    this.hpf = [ctx.createBiquadFilter(), ctx.createBiquadFilter()];
    this.lpf = [ctx.createBiquadFilter(), ctx.createBiquadFilter()];
    this.eq = {};
    for (const id of EQ_IDS) this.eq[id] = ctx.createBiquadFilter();
    this.comp = ctx.createDynamicsCompressor();
    this.makeup = ctx.createGain();
    this.trim = ctx.createGain();
    this.output = this.trim;
    // taps (an AnalyserNode only computes when read)
    this.preComp = ctx.createAnalyser();
    this.preComp.fftSize = 1024;
    this.post = ctx.createAnalyser();
    this.post.fftSize = 8192;
    this.post.smoothingTimeConstant = 0.8;
    this.post.minDecibels = -100;
    this.post.maxDecibels = -10;
    const seq = [this.input, ...this.hpf, ...EQ_IDS.map((id) => this.eq[id]), ...this.lpf, this.comp, this.makeup, this.trim];
    for (let i = 0; i < seq.length - 1; i++) seq[i].connect(seq[i + 1]);
    this.lpf[1].connect(this.preComp);
    this.trim.connect(this.post);
    this.fx = defaultFx();
    this.apply(this.fx, true);
  }

  _set(param, v, immediate) {
    if (immediate) param.value = v;
    else param.setTargetAtTime(v, this.ctx.currentTime, 0.02);
  }

  _identity(f, immediate) {
    f.type = 'peaking';
    this._set(f.gain, 0, immediate);
  }

  apply(fx, immediate = false) {
    this.fx = fx;
    const eq = fx.eq;
    const bands = eq.bands;
    const on = eq.on;
    for (const [kind, nodes, s] of [['highpass', this.hpf, bands.hpf], ['lowpass', this.lpf, bands.lpf]]) {
      if (on && s.on) {
        const qs = s.slope === 24 ? BUTTERWORTH_4 : [Math.SQRT1_2];
        nodes.forEach((f, i) => {
          if (i < qs.length) {
            f.type = kind;
            this._set(f.frequency, s.freq, immediate);
            this._set(f.Q, qs[i], immediate);
          } else this._identity(f, immediate);
        });
      } else nodes.forEach((f) => this._identity(f, immediate));
    }
    for (const id of EQ_IDS) {
      const f = this.eq[id];
      const s = bands[id];
      const kind = BANDS.find((b) => b.id === id).kind;
      f.type = kind;
      this._set(f.frequency, s.freq, immediate);
      if (kind === 'peaking') this._set(f.Q, s.q ?? 1, immediate);
      this._set(f.gain, on && s.on ? s.gain : 0, immediate);
    }
    this._set(this.trim.gain, Math.pow(10, (on ? eq.trim || 0 : 0) / 20), immediate);
    const c = fx.comp;
    if (c.on) {
      this._set(this.comp.threshold, c.threshold, immediate);
      this._set(this.comp.ratio, c.ratio, immediate);
      this._set(this.comp.knee, c.knee, immediate);
      this._set(this.comp.attack, c.attack / 1000, immediate);
      this._set(this.comp.release, c.release / 1000, immediate);
      this._set(this.makeup.gain, Math.pow(10, c.gain / 20), immediate);
    } else {
      this._set(this.comp.threshold, 0, immediate);
      this._set(this.comp.ratio, 1, immediate);
      this._set(this.comp.knee, 0, immediate);
      this._set(this.makeup.gain, 1, immediate);
    }
  }

  // Magnitude response (dB) of the whole chain's filters at the given frequencies.
  response(freqs) {
    const total = new Float32Array(freqs.length);
    const mag = new Float32Array(freqs.length);
    const ph = new Float32Array(freqs.length);
    for (const f of [...this.hpf, ...EQ_IDS.map((id) => this.eq[id]), ...this.lpf]) {
      f.getFrequencyResponse(freqs, mag, ph);
      for (let i = 0; i < freqs.length; i++) total[i] += 20 * Math.log10(Math.max(mag[i], 1e-6));
    }
    return total;
  }

  // Response of a single band as currently configured (for the per-band faint curves).
  bandResponse(id, freqs) {
    const nodes = id === 'hpf' ? this.hpf : id === 'lpf' ? this.lpf : [this.eq[id]];
    const total = new Float32Array(freqs.length);
    const mag = new Float32Array(freqs.length);
    const ph = new Float32Array(freqs.length);
    for (const f of nodes) {
      f.getFrequencyResponse(freqs, mag, ph);
      for (let i = 0; i < freqs.length; i++) total[i] += 20 * Math.log10(Math.max(mag[i], 1e-6));
    }
    return total;
  }

  get reduction() {
    return this.comp.reduction;
  }

  // Peak level (dBFS) entering the compressor, for the live operating point.
  inputPeakDb() {
    const buf = this._tbuf || (this._tbuf = new Float32Array(this.preComp.fftSize));
    this.preComp.getFloatTimeDomainData(buf);
    let p = 0;
    for (let i = 0; i < buf.length; i++) { const a = Math.abs(buf[i]); if (a > p) p = a; }
    return 20 * Math.log10(Math.max(p, 1e-6));
  }

  disconnect() {
    try { this.output.disconnect(); } catch (_) { /* ignore */ }
    try { this.input.disconnect(); } catch (_) { /* ignore */ }
  }
}

// Delay introduced by one DynamicsCompressorNode (Chromium adds a fixed look-ahead).
export async function measureCompressorLatency(sampleRate) {
  const n = 4096;
  const oc = new OfflineAudioContext(1, n, sampleRate);
  const buf = oc.createBuffer(1, n, sampleRate);
  buf.getChannelData(0)[64] = 0.25;
  const src = oc.createBufferSource();
  src.buffer = buf;
  const c = oc.createDynamicsCompressor();
  c.threshold.value = 0;
  c.ratio.value = 1;
  c.knee.value = 0;
  src.connect(c);
  c.connect(oc.destination);
  src.start();
  const out = (await oc.startRendering()).getChannelData(0);
  let idx = 0;
  for (let i = 1; i < n; i++) if (Math.abs(out[i]) > Math.abs(out[idx])) idx = i;
  return Math.max(0, (idx - 64) / sampleRate);
}

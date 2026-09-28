// Records what another browser tab (e.g. YouTube) is playing, via getDisplayMedia tab audio.
// The audio is kept as uncompressed PCM and uploaded as a 16-bit WAV, so separation quality is
// not reduced by a second lossy encode. Personal practice use only: nothing is shared or published.

export class TabCapture extends EventTarget {
  constructor() {
    super();
    this.chunksL = [];
    this.chunksR = [];
    this.samples = 0;
    this.recording = false;
    this.peak = 0;
  }

  static supported() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia && window.AudioWorkletNode);
  }

  async start() {
    const stream = await navigator.mediaDevices.getDisplayMedia({
      video: true, // Chrome requires video to offer tab audio; the video track is stopped right away
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, suppressLocalAudioPlayback: false },
      preferCurrentTab: false,
      selfBrowserSurface: 'exclude',
      systemAudio: 'include',
      surfaceSwitching: 'include',
    });
    const audio = stream.getAudioTracks();
    if (!audio.length) {
      stream.getTracks().forEach((t) => t.stop());
      throw new Error('タブの音声が共有されていません。共有ダイアログで「タブの音声も共有する」をONにしてください。');
    }
    stream.getVideoTracks().forEach((t) => t.stop());
    this.stream = stream;
    this.ctx = new AudioContext({ latencyHint: 'playback' });
    await this.ctx.audioWorklet.addModule(new URL('./audio/recorder-worklet.js', import.meta.url));
    this.src = this.ctx.createMediaStreamSource(new MediaStream(audio));
    this.node = new AudioWorkletNode(this.ctx, 'bss-recorder', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [2] });
    const mute = this.ctx.createGain();
    mute.gain.value = 0; // keep the node running without playing the tab twice
    this.src.connect(this.node);
    this.node.connect(mute);
    mute.connect(this.ctx.destination);
    this.node.port.onmessage = (e) => {
      if (!this.recording) return;
      this.chunksL.push(e.data.l);
      this.chunksR.push(e.data.r);
      this.samples += e.data.l.length;
      this.peak = Math.max(this.peak * 0.9, e.data.peak);
      this.dispatchEvent(new CustomEvent('level', { detail: { peak: e.data.peak, seconds: this.seconds } }));
    };
    audio[0].addEventListener('ended', () => {
      if (this.recording) this.dispatchEvent(new Event('ended'));
    });
    this.label = audio[0].label || '';
    this.recording = true;
    this.startedAt = performance.now();
  }

  get seconds() {
    return this.ctx ? this.samples / this.ctx.sampleRate : 0;
  }

  async stop() {
    if (!this.ctx) return null;
    this.node.port.postMessage('flush');
    await new Promise((r) => setTimeout(r, 120));
    this.recording = false;
    this.stream.getTracks().forEach((t) => t.stop());
    const sr = this.ctx.sampleRate;
    await this.ctx.close();
    const blob = encodeWav(this.chunksL, this.chunksR, this.samples, sr);
    this.chunksL = [];
    this.chunksR = [];
    return blob;
  }

  cancel() {
    this.recording = false;
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    if (this.ctx) this.ctx.close();
    this.chunksL = [];
    this.chunksR = [];
  }
}

function encodeWav(chunksL, chunksR, n, sr) {
  const bytes = 44 + n * 4;
  const buf = new ArrayBuffer(bytes);
  const v = new DataView(buf);
  const w = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  w(0, 'RIFF'); v.setUint32(4, bytes - 8, true); w(8, 'WAVE');
  w(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 2, true);
  v.setUint32(24, sr, true); v.setUint32(28, sr * 4, true); v.setUint16(32, 4, true); v.setUint16(34, 16, true);
  w(36, 'data'); v.setUint32(40, n * 4, true);
  let o = 44;
  for (let c = 0; c < chunksL.length; c++) {
    const l = chunksL[c];
    const r = chunksR[c];
    for (let i = 0; i < l.length; i++) {
      v.setInt16(o, Math.max(-1, Math.min(1, l[i])) * 32767, true);
      v.setInt16(o + 2, Math.max(-1, Math.min(1, r[i])) * 32767, true);
      o += 4;
    }
  }
  return new Blob([buf], { type: 'audio/wav' });
}

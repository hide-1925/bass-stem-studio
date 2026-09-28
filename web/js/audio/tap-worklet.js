// Measurement tap: detects click onsets per stereo pair of a multichannel signal and reports the
// context time of the first sample above the threshold (used by measure.html).
class BssTap extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = (options && options.processorOptions) || {};
    this.thr = o.threshold ?? 0.3;
    this.refractory = Math.round((o.refractorySec ?? 0.25) * sampleRate);
    this.pairs = o.pairs ?? 6;
    this.last = new Array(this.pairs).fill(-1e12);
    this.frame = 0;
    this.peak = 0;
    this.port.onmessage = (e) => { if (e.data === 'peak') { this.port.postMessage({ peak: this.peak }); this.peak = 0; } };
  }

  process(inputs) {
    const inp = inputs[0];
    if (!inp || !inp.length) return true;
    const n = inp[0].length;
    for (let p = 0; p < this.pairs; p++) {
      const l = inp[2 * p];
      const r = inp[2 * p + 1] || l;
      if (!l) continue;
      for (let i = 0; i < n; i++) {
        const a = Math.max(Math.abs(l[i]), Math.abs(r[i]));
        if (a > this.peak) this.peak = a;
        if (a >= this.thr && this.frame + i - this.last[p] > this.refractory) {
          this.last[p] = this.frame + i;
          this.port.postMessage({ pair: p, time: currentTime + i / sampleRate, amp: a });
        }
      }
    }
    this.frame += n;
    return true;
  }
}

registerProcessor('bss-tap', BssTap);

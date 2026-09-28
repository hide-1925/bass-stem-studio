// Look-ahead peak limiter (stereo). Guarantees |out| <= threshold for every sample:
//   required gain r[n] = min(1, thr / |x[n]|)
//   m[n] = min(r[n-L..n])          (sliding minimum over the look-ahead window)
//   s[n] = mean(m[n-L+1..n])       (box-smoothed: reaches the minimum exactly when the peak leaves the delay)
//   g[n] = min(s[n], release-smoothed previous gain)
//   out[n] = x[n-L] * g[n]
// Reports the deepest gain reduction every ~100 ms.

class BssLimiter extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = (options && options.processorOptions) || {};
    this.thr = Math.pow(10, (o.thresholdDb ?? -1) / 20);
    this.L = Math.max(8, Math.round((o.lookaheadMs ?? 5) * sampleRate / 1000));
    this.rel = Math.exp(-1 / ((o.releaseMs ?? 80) * sampleRate / 1000));
    const n = this.L + 1;
    this.delayL = new Float32Array(n);
    this.delayR = new Float32Array(n);
    this.dpos = 0;
    // monotonic deque for the sliding minimum
    this.dqVal = new Float32Array(n + 1);
    this.dqIdx = new Float64Array(n + 1);
    this.dqHead = 0;
    this.dqTail = 0;
    this.dqCap = n + 1;
    this.box = new Float32Array(this.L).fill(1);
    this.boxPos = 0;
    this.boxSum = this.L;
    this.g = 1;
    this.sample = 0;
    this.minG = 1;
    this.reportEvery = Math.round(sampleRate * 0.1);
    this.reportCount = 0;
    this.maxOut = 0;
  }

  pushMin(v, idx) {
    const cap = this.dqCap;
    while (this.dqTail !== this.dqHead) {
      const last = (this.dqTail - 1 + cap) % cap;
      if (this.dqVal[last] >= v) this.dqTail = last;
      else break;
    }
    this.dqVal[this.dqTail] = v;
    this.dqIdx[this.dqTail] = idx;
    this.dqTail = (this.dqTail + 1) % cap;
    while (this.dqIdx[this.dqHead] <= idx - this.L - 1) this.dqHead = (this.dqHead + 1) % cap;
    return this.dqVal[this.dqHead];
  }

  process(inputs, outputs) {
    const inp = inputs[0];
    const out = outputs[0];
    const oL = out[0];
    const oR = out[1] || out[0];
    const n = oL.length;
    const iL = inp && inp[0] ? inp[0] : null;
    const iR = inp && inp[1] ? inp[1] : iL;
    const L = this.L;
    const size = L + 1;
    for (let i = 0; i < n; i++) {
      const xl = iL ? iL[i] : 0;
      const xr = iR ? iR[i] : 0;
      const a = Math.max(Math.abs(xl), Math.abs(xr));
      const req = a > this.thr ? this.thr / a : 1;
      const m = this.pushMin(req, this.sample);
      this.boxSum += m - this.box[this.boxPos];
      this.box[this.boxPos] = m;
      this.boxPos = (this.boxPos + 1) % L;
      const s = Math.min(1, this.boxSum / L);
      const released = 1 - (1 - this.g) * this.rel;
      this.g = Math.min(s, released);
      // delay line: write current, read the sample from L samples ago
      this.delayL[this.dpos] = xl;
      this.delayR[this.dpos] = xr;
      const rp = (this.dpos + 1) % size;
      let yl = this.delayL[rp] * this.g;
      let yr = this.delayR[rp] * this.g;
      // numerical safety: never exceed the threshold
      const ay = Math.max(Math.abs(yl), Math.abs(yr));
      if (ay > this.thr) {
        const k = this.thr / ay;
        yl *= k;
        yr *= k;
      }
      oL[i] = yl;
      if (out[1]) oR[i] = yr;
      this.maxOut = Math.max(this.maxOut, Math.abs(yl), Math.abs(yr));
      this.dpos = rp;
      this.sample++;
      if (this.g < this.minG) this.minG = this.g;
    }
    this.reportCount += n;
    if (this.reportCount >= this.reportEvery) {
      this.port.postMessage({ minGain: this.minG, maxOut: this.maxOut });
      this.reportCount = 0;
      this.minG = 1;
      this.maxOut = 0;
    }
    return true;
  }
}

registerProcessor('bss-limiter', BssLimiter);

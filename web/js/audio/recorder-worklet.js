// Collects raw PCM from a MediaStream source and posts it to the main thread in ~250 ms batches.
class BssRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.batch = Math.round(sampleRate * 0.25);
    this.reset();
    this.port.onmessage = (e) => {
      if (e.data === 'flush') this.flush();
    };
  }

  reset() {
    this.bufL = new Float32Array(this.batch);
    this.bufR = new Float32Array(this.batch);
    this.n = 0;
    this.peak = 0;
  }

  flush() {
    if (this.n > 0) {
      const l = this.bufL.slice(0, this.n);
      const r = this.bufR.slice(0, this.n);
      this.port.postMessage({ l, r, peak: this.peak }, [l.buffer, r.buffer]);
    }
    this.reset();
  }

  process(inputs) {
    const inp = inputs[0];
    if (!inp || !inp[0]) return true;
    const l = inp[0];
    const r = inp[1] || inp[0];
    for (let i = 0; i < l.length; i++) {
      this.bufL[this.n] = l[i];
      this.bufR[this.n] = r[i];
      const a = Math.max(Math.abs(l[i]), Math.abs(r[i]));
      if (a > this.peak) this.peak = a;
      this.n++;
      if (this.n === this.batch) this.flush();
    }
    return true;
  }
}

registerProcessor('bss-recorder', BssRecorder);

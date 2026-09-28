// Rotary knob drawn on canvas (same look as the AVO compressor popup).
// Drag up/down or use the wheel; Shift = fine steps; double-click = default value.

const KC = {
  rim: '#3e3828', shadow: '#0a0908', face: '#242018', groove: '#141210',
  dot: '#e8c060', arcBg: '#22201a', arcOn: '#c08030', mark: '#483e2c', disabled: '#2a2620',
};

export class Knob {
  /**
   * @param {object} o {label, min, max, step, fineStep, log, value, def, format, onChange, size, bipolar}
   */
  constructor(o) {
    this.o = { size: 46, fineStep: o.step, ...o };
    this.value = o.value ?? o.def ?? o.min;
    this.enabled = true;
    const size = this.o.size;
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'knob';
    this.canvas.style.width = `${size}px`;
    this.canvas.style.height = `${size}px`;
    this.valEl = document.createElement('div');
    this.valEl.className = 'knob-val';
    this.labelEl = document.createElement('div');
    this.labelEl.className = 'knob-label';
    this.labelEl.textContent = o.label;
    this.el = document.createElement('div');
    this.el.className = 'knob-unit';
    this.el.append(this.valEl, this.canvas, this.labelEl);
    this._bind();
    this.draw();
  }

  norm(v = this.value) {
    const { min, max, log } = this.o;
    return log ? (Math.log(v) - Math.log(min)) / (Math.log(max) - Math.log(min)) : (v - min) / (max - min);
  }

  fromNorm(n, fine) {
    const { min, max, log } = this.o;
    const step = fine ? this.o.fineStep : this.o.step;
    let v = log ? Math.exp(Math.log(min) + n * (Math.log(max) - Math.log(min))) : min + n * (max - min);
    if (step) v = Math.round(v / step) * step;
    return Math.max(min, Math.min(max, +v.toFixed(4)));
  }

  set(v, notify = false) {
    this.value = Math.max(this.o.min, Math.min(this.o.max, v));
    this.draw();
    if (notify && this.o.onChange) this.o.onChange(this.value);
  }

  setEnabled(en) {
    this.enabled = en;
    this.draw();
  }

  setRange(min, max, value) {
    this.o.min = min;
    this.o.max = max;
    this.set(value);
  }

  draw() {
    const c = this.canvas;
    const size = this.o.size;
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(size * dpr)) {
      c.width = Math.round(size * dpr);
      c.height = Math.round(size * dpr);
    }
    const x = c.getContext('2d');
    x.setTransform(dpr, 0, 0, dpr, 0, 0);
    const W = size;
    const cx = W / 2;
    const cy = W / 2;
    const R = W / 2 - 3;
    const en = this.enabled;
    const n = Math.max(0, Math.min(1, this.norm()));
    x.clearRect(0, 0, W, W);
    const sa = Math.PI * 0.75;
    const ea = Math.PI * 2.25;
    const rng = ea - sa;
    const ang = sa + n * rng;
    x.beginPath(); x.arc(cx, cy + 1, R, 0, Math.PI * 2); x.fillStyle = KC.shadow; x.fill();
    x.beginPath(); x.arc(cx, cy, R, 0, Math.PI * 2); x.fillStyle = KC.rim; x.fill();
    const bR = R - 2;
    x.beginPath(); x.arc(cx, cy, bR, 0, Math.PI * 2); x.fillStyle = en ? KC.face : KC.groove; x.fill();
    x.beginPath(); x.arc(cx, cy, bR - 5, sa, ea); x.strokeStyle = KC.arcBg; x.lineWidth = 3; x.stroke();
    if (en) {
      // bipolar knobs (gain) light the arc from the centre
      const from = this.o.bipolar ? sa + this.norm(0) * rng : sa;
      if (Math.abs(ang - from) > 0.01) {
        x.beginPath();
        x.arc(cx, cy, bR - 5, Math.min(from, ang), Math.max(from, ang));
        x.strokeStyle = KC.arcOn; x.lineWidth = 3; x.stroke();
      }
    }
    for (let i = 0; i <= 8; i++) {
      const a = sa + (i / 8) * rng;
      x.beginPath();
      x.moveTo(cx + Math.cos(a) * (bR - 2), cy + Math.sin(a) * (bR - 2));
      x.lineTo(cx + Math.cos(a) * (bR - 5), cy + Math.sin(a) * (bR - 5));
      x.strokeStyle = en ? KC.mark : KC.disabled; x.lineWidth = 1; x.stroke();
    }
    x.beginPath(); x.arc(cx + Math.cos(ang) * (bR - 8), cy + Math.sin(ang) * (bR - 8), 2.5, 0, Math.PI * 2);
    x.fillStyle = en ? KC.dot : KC.disabled; x.fill();
    x.beginPath(); x.arc(cx, cy, 4, 0, Math.PI * 2); x.fillStyle = KC.groove; x.fill();
    this.valEl.textContent = this.o.format ? this.o.format(this.value) : String(this.value);
  }

  _bind() {
    let drag = null;
    this.canvas.addEventListener('pointerdown', (e) => {
      drag = { y: e.clientY, n: this.norm() };
      this.canvas.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    this.canvas.addEventListener('pointermove', (e) => {
      if (!drag) return;
      const n = Math.max(0, Math.min(1, drag.n + (drag.y - e.clientY) / (e.shiftKey ? 600 : 150)));
      const v = this.fromNorm(n, e.shiftKey);
      if (v !== this.value) this.set(v, true);
    });
    this.canvas.addEventListener('pointerup', () => { drag = null; });
    this.canvas.addEventListener('dblclick', () => this.set(this.o.def ?? this.o.min, true));
    this.canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const step = e.shiftKey ? this.o.fineStep : this.o.step;
      let v;
      if (this.o.log) v = this.value * Math.pow(1.06, e.deltaY < 0 ? 1 : -1);
      else v = this.value + (e.deltaY < 0 ? step : -step);
      if (step && !this.o.log) v = Math.round(v / step) * step;
      this.set(+v.toFixed(4), true);
    }, { passive: false });
  }
}

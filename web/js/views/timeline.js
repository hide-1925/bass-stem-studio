// Shared time axis for every lane, plus the ruler and the overview (mini-map).

import { barSeconds, clamp, cssVar, fitCanvas, fmtTime, tempoValid } from '../util.js';

export class TimeView extends EventTarget {
  constructor() {
    super();
    this.t0 = 0;
    this.pps = 60; // pixels per second
    this.width = 800;
    this.duration = 0;
  }

  x(t) { return (t - this.t0) * this.pps; }
  t(x) { return this.t0 + x / this.pps; }
  get t1() { return this.t0 + this.width / this.pps; }

  _emit() { this.dispatchEvent(new Event('change')); }

  setWidth(w) {
    if (w !== this.width) {
      this.width = w;
      this.clampT0();
      this._emit();
    }
  }

  clampT0() {
    const span = this.width / this.pps;
    this.t0 = clamp(this.t0, 0, Math.max(0, this.duration - span * 0.5));
  }

  scrollTo(t0) {
    this.t0 = t0;
    this.clampT0();
    this._emit();
  }

  scrollBy(dt) { this.scrollTo(this.t0 + dt); }

  zoomAt(factor, x) {
    const t = this.t(x);
    this.pps = clamp(this.pps * factor, 4, 1200);
    this.t0 = t - x / this.pps;
    this.clampT0();
    this._emit();
  }

  fitAll() {
    if (this.duration > 0) {
      this.pps = clamp(this.width / this.duration, 4, 1200);
      this.t0 = 0;
      this._emit();
    }
  }

  // Page-flip follow: keep the playhead between 5% and 85% of the view.
  follow(p, loop) {
    const span = this.width / this.pps;
    if (loop && loop.enabled && loop.b - loop.a < span * 0.9) {
      if (loop.a < this.t0 || loop.b > this.t1) this.scrollTo(loop.a - span * 0.05);
      return;
    }
    if (p < this.t0 || p > this.t0 + span * 0.85) this.scrollTo(p - span * 0.05);
  }
}

// Cached static layer + per-frame overlay.
export class CanvasView {
  constructor(canvas, app) {
    this.canvas = canvas;
    this.app = app;
    this.cache = document.createElement('canvas');
    this.cacheKey = null;
  }

  staticKey(w, h) {
    const v = this.app.view;
    return `${w}x${h}|${v.t0.toFixed(4)}|${v.pps.toFixed(3)}|${this.app.renderVersion}`;
  }

  drawStatic(_ctx, _w, _h) {}

  drawOverlay(ctx, w, h, pos) {
    drawTimeOverlay(ctx, w, h, this.app, pos);
  }

  render(pos) {
    const { ctx, w, h } = fitCanvas(this.canvas);
    const key = this.staticKey(w, h);
    if (key !== this.cacheKey) {
      this.cache.width = this.canvas.width;
      this.cache.height = this.canvas.height;
      const c = this.cache.getContext('2d');
      const dpr = window.devicePixelRatio || 1;
      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.clearRect(0, 0, w, h);
      this.drawStatic(c, w, h);
      this.cacheKey = key;
    }
    ctx.clearRect(0, 0, w, h);
    ctx.drawImage(this.cache, 0, 0, w, h);
    this.drawOverlay(ctx, w, h, pos);
  }
}

export function drawTimeOverlay(ctx, w, h, app, pos, { handles = false } = {}) {
  const v = app.view;
  const loop = app.loop;
  if (loop.b > loop.a) {
    const xa = v.x(loop.a);
    const xb = v.x(loop.b);
    ctx.fillStyle = loop.enabled ? 'rgba(99, 179, 237, 0.13)' : 'rgba(160, 174, 192, 0.07)';
    ctx.fillRect(xa, 0, xb - xa, h);
    ctx.strokeStyle = loop.enabled ? 'rgba(99, 179, 237, 0.8)' : 'rgba(160, 174, 192, 0.4)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(Math.round(xa) + 0.5, 0); ctx.lineTo(Math.round(xa) + 0.5, h);
    ctx.moveTo(Math.round(xb) + 0.5, 0); ctx.lineTo(Math.round(xb) + 0.5, h);
    ctx.stroke();
    if (handles) {
      ctx.fillStyle = loop.enabled ? '#63b3ed' : '#718096';
      ctx.font = 'bold 10px system-ui';
      ctx.fillRect(xa, 0, 14, 12); ctx.fillRect(xb - 14, 0, 14, 12);
      ctx.fillStyle = '#0b0f14';
      ctx.fillText('A', xa + 3, 10); ctx.fillText('B', xb - 11, 10);
    }
  }
  const x = v.x(pos);
  if (x >= -2 && x <= w + 2) {
    ctx.strokeStyle = cssVar('--playhead') || '#fff';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(Math.round(x) + 0.5, 0);
    ctx.lineTo(Math.round(x) + 0.5, h);
    ctx.stroke();
  }
}

export function tickStep(pps, minPx = 70) {
  const steps = [0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120];
  return steps.find((s) => s * pps >= minPx) || 300;
}

// Vertical grid (seconds or bars/beats) drawn behind lanes.
export function drawGrid(ctx, w, h, app, { strong = 'rgba(255,255,255,0.08)', weak = 'rgba(255,255,255,0.035)' } = {}) {
  const v = app.view;
  const tempo = app.project?.tempo;
  ctx.lineWidth = 1;
  if (tempoValid(tempo)) {
    const beat = 60 / tempo.bpm;
    const off = tempo.offset_sec || 0;
    const bpb = tempo.beats_per_bar || 4;
    if (beat * v.pps < 6) return drawSeconds();
    const k0 = Math.floor((v.t0 - off) / beat);
    for (let k = k0; ; k++) {
      const t = off + k * beat;
      const x = v.x(t);
      if (x > w) break;
      if (x < 0) continue;
      ctx.strokeStyle = ((k % bpb) + bpb) % bpb === 0 ? strong : weak;
      ctx.beginPath(); ctx.moveTo(Math.round(x) + 0.5, 0); ctx.lineTo(Math.round(x) + 0.5, h); ctx.stroke();
    }
    return;
  }
  drawSeconds();
  function drawSeconds() {
    const step = tickStep(v.pps);
    for (let t = Math.ceil(v.t0 / step) * step; t <= v.t1; t += step) {
      const x = v.x(t);
      ctx.strokeStyle = weak;
      ctx.beginPath(); ctx.moveTo(Math.round(x) + 0.5, 0); ctx.lineTo(Math.round(x) + 0.5, h); ctx.stroke();
    }
  }
}

export class RulerView extends CanvasView {
  drawStatic(ctx, w, h) {
    const v = this.app.view;
    ctx.fillStyle = cssVar('--panel-2');
    ctx.fillRect(0, 0, w, h);
    ctx.font = '10px system-ui';
    const tempo = this.app.project?.tempo;
    if (tempoValid(tempo)) {
      const bar = barSeconds(tempo);
      const off = tempo.offset_sec || 0;
      const every = Math.max(1, Math.ceil(50 / (bar * v.pps)));
      const k0 = Math.floor((v.t0 - off) / bar);
      for (let k = k0; ; k++) {
        const t = off + k * bar;
        const x = v.x(t);
        if (x > w) break;
        if (x < -40 || k % every) continue;
        ctx.fillStyle = cssVar('--text-dim');
        ctx.fillRect(Math.round(x), h - 10, 1, 10);
        ctx.fillText(String(this.app.barNumber ? this.app.barNumber(k) : k + 1), x + 3, h - 12);
      }
    }
    const step = tickStep(v.pps, 80);
    ctx.fillStyle = cssVar('--text-faint');
    for (let t = Math.ceil(v.t0 / step) * step; t <= v.t1; t += step) {
      const x = v.x(t);
      ctx.fillRect(Math.round(x), h - 5, 1, 5);
      ctx.fillText(fmtTime(t, step < 1).replace(/0+$/, '').replace(/\.$/, ''), x + 3, 10);
    }
  }

  drawOverlay(ctx, w, h, pos) {
    drawTimeOverlay(ctx, w, h, this.app, pos, { handles: true });
  }
}

export class OverviewView {
  constructor(canvas, app) {
    this.canvas = canvas;
    this.app = app;
  }

  render(pos) {
    const { ctx, w, h } = fitCanvas(this.canvas);
    const app = this.app;
    const dur = app.view.duration || 1;
    ctx.fillStyle = cssVar('--panel-2');
    ctx.fillRect(0, 0, w, h);
    const peaks = app.peaks?.mix;
    if (peaks && peaks.length) {
      ctx.fillStyle = 'rgba(160,174,192,0.45)';
      const per = peaks.length / w;
      for (let x = 0; x < w; x++) {
        let m = 0;
        const a = Math.floor(x * per);
        const b = Math.max(a + 1, Math.floor((x + 1) * per));
        for (let i = a; i < b && i < peaks.length; i++) if (peaks[i] > m) m = peaks[i];
        const bh = (m / 255) * (h - 4);
        ctx.fillRect(x, (h - bh) / 2, 1, bh);
      }
    }
    const sx = (t) => (t / dur) * w;
    if (app.loop.b > app.loop.a) {
      ctx.fillStyle = app.loop.enabled ? 'rgba(99,179,237,0.3)' : 'rgba(160,174,192,0.15)';
      ctx.fillRect(sx(app.loop.a), 0, Math.max(1, sx(app.loop.b) - sx(app.loop.a)), h);
    }
    ctx.strokeStyle = 'rgba(255,255,255,0.55)';
    ctx.lineWidth = 1;
    ctx.strokeRect(sx(app.view.t0) + 0.5, 0.5, Math.max(3, sx(app.view.t1) - sx(app.view.t0)) - 1, h - 1);
    ctx.fillStyle = cssVar('--playhead') || '#fff';
    ctx.fillRect(Math.round(sx(pos)), 0, 1.5, h);
  }

  attach() {
    let dragging = false;
    const go = (e) => {
      const r = this.canvas.getBoundingClientRect();
      const t = ((e.clientX - r.left) / r.width) * (this.app.view.duration || 0);
      const span = this.app.view.width / this.app.view.pps;
      this.app.view.scrollTo(t - span / 2);
    };
    this.canvas.addEventListener('pointerdown', (e) => {
      dragging = true;
      this.canvas.setPointerCapture(e.pointerId);
      go(e);
    });
    this.canvas.addEventListener('pointermove', (e) => dragging && go(e));
    this.canvas.addEventListener('pointerup', () => { dragging = false; });
  }
}

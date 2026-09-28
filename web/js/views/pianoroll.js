// Bass piano roll: time on x (shared axis), pitch on y.
//   click note = select + play from its start   drag = move (vertical = pitch)
//   drag edge = resize   drag empty = box select   double-click empty = add note
//   Shift+wheel = scroll pitch range

import { chooseForPitch } from '../notes/fingering.js';
import { clamp, cssVar, fitCanvas, gridSeconds, noteName, quantizeTime, tempoValid } from '../util.js';
import { CanvasView, drawGrid, drawTimeOverlay } from './timeline.js';

const BLACK = new Set([1, 3, 6, 8, 10]);
const EDGE_PX = 6;

export class PianoRollView extends CanvasView {
  constructor(canvas, keysCanvas, app) {
    super(canvas, app);
    this.keys = keysCanvas;
    this.pmin = 24;
    this.pmax = 60;
    this.drag = null;
    this.preview = null; // Map id -> {start_sec, end_sec, midi_pitch}
    this.previewVersion = 0;
    this.band = null;
  }

  fitRange() {
    const tun = this.app.tuning;
    let lo = Math.min(...tun.strings) - 2;
    let hi = Math.max(...tun.strings) + 14;
    for (const n of this.app.notes.sorted()) {
      lo = Math.min(lo, n.midi_pitch - 1);
      hi = Math.max(hi, n.midi_pitch + 1);
    }
    this.pmin = clamp(lo, 12, 100);
    this.pmax = clamp(hi, this.pmin + 12, 110);
  }

  staticKey(w, h) {
    return `${super.staticKey(w, h)}|${this.pmin}-${this.pmax}|${this.app.notes.version}|${this.previewVersion || 0}`;
  }

  rowH(h) { return h / (this.pmax - this.pmin + 1); }
  y(p, h) { return (this.pmax - p) * this.rowH(h); }
  pitchAt(y, h) { return this.pmax - Math.floor(y / this.rowH(h)); }

  times(n) {
    const pv = this.preview && this.preview.get(n.id);
    const src = pv ? { ...n, ...pv } : n;
    const d = this.app.displayTimes(src);
    return { s: d.s, e: d.e, p: src.midi_pitch };
  }

  drawStatic(ctx, w, h) {
    const app = this.app;
    const rh = this.rowH(h);
    for (let p = this.pmin; p <= this.pmax; p++) {
      const y = this.y(p, h);
      ctx.fillStyle = BLACK.has(((p % 12) + 12) % 12) ? cssVar('--roll-black') : cssVar('--roll-white');
      ctx.fillRect(0, y, w, rh);
      if (p % 12 === 0) {
        ctx.fillStyle = 'rgba(255,255,255,0.07)';
        ctx.fillRect(0, y + rh - 1, w, 1);
      }
    }
    // open strings
    ctx.fillStyle = 'rgba(79, 209, 197, 0.10)';
    for (const s of app.tuning.strings) ctx.fillRect(0, this.y(s, h), w, rh);
    drawGrid(ctx, w, h, app);
    const v = app.view;
    const thr = app.project?.confidence_threshold ?? 0.5;
    const sel = app.selection;
    ctx.font = `${Math.max(8, Math.min(11, rh - 2))}px system-ui`;
    for (const n of app.notes.sorted()) {
      const { s, e, p } = this.times(n);
      if (e < v.t0 || s > v.t1) continue;
      const x = v.x(s);
      const wN = Math.max(2, (e - s) * v.pps);
      const y = this.y(p, h);
      const low = n.confidence < thr;
      ctx.fillStyle = low ? cssVar('--low-conf') : cssVar('--c-bass');
      ctx.globalAlpha = low ? 0.85 : 0.95;
      ctx.fillRect(x, y + 1, wN, rh - 2);
      ctx.globalAlpha = 1;
      if (low) hatch(ctx, x, y + 1, wN, rh - 2);
      if (sel.has(n.id)) {
        ctx.strokeStyle = cssVar('--sel');
        ctx.lineWidth = 2;
        ctx.strokeRect(x + 1, y + 1.5, wN - 2, rh - 3);
      } else if (n.edited) {
        ctx.strokeStyle = 'rgba(255,255,255,0.9)';
        ctx.lineWidth = 1;
        ctx.strokeRect(x + 0.5, y + 1.5, wN - 1, rh - 3);
      }
      if ((n.flags || []).includes('out_of_range')) {
        ctx.strokeStyle = cssVar('--danger');
        ctx.lineWidth = 1.5;
        ctx.strokeRect(x + 0.5, y + 0.5, wN - 1, rh - 1);
      }
      if (wN > 26 && rh >= 9) {
        ctx.fillStyle = '#081418';
        ctx.fillText(noteName(p), x + 3, y + rh - 3);
      }
    }
  }

  drawOverlay(ctx, w, h, pos) {
    drawTimeOverlay(ctx, w, h, this.app, pos);
    if (this.band) {
      const b = this.band;
      ctx.strokeStyle = cssVar('--sel');
      ctx.setLineDash([4, 3]);
      ctx.strokeRect(Math.min(b.x0, b.x1) + 0.5, Math.min(b.y0, b.y1) + 0.5, Math.abs(b.x1 - b.x0), Math.abs(b.y1 - b.y0));
      ctx.setLineDash([]);
    }
    if (this.app.notes.locked) {
      ctx.fillStyle = 'rgba(11,15,20,0.55)';
      ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = cssVar('--text');
      ctx.font = '13px system-ui';
      ctx.fillText('採譜中…（完了すると結果が表示されます。手修正は保持されます）', 16, 24);
    }
  }

  renderKeys() {
    const { ctx, w, h } = fitCanvas(this.keys);
    const rh = this.rowH(h);
    ctx.clearRect(0, 0, w, h);
    const open = new Set(this.app.tuning.strings);
    ctx.font = `${Math.max(8, Math.min(10, rh - 1))}px system-ui`;
    for (let p = this.pmin; p <= this.pmax; p++) {
      const y = this.y(p, h);
      const black = BLACK.has(((p % 12) + 12) % 12);
      ctx.fillStyle = black ? '#1a2029' : '#cfd6df';
      ctx.fillRect(black ? 0 : 0, y, black ? w * 0.62 : w, rh - 0.5);
      if (p % 12 === 0 || open.has(p) || rh >= 11) {
        ctx.fillStyle = open.has(p) ? '#0f766e' : black ? '#cfd6df' : '#1a2029';
        ctx.fillText(noteName(p) + (open.has(p) ? ' ●' : ''), 3, y + rh - 2);
      }
    }
  }

  render(pos) {
    super.render(pos);
    const key = `${this.pmin}-${this.pmax}|${this.keys.clientHeight}|${this.app.tuning.strings.join(',')}`;
    if (key !== this.keysKey) {
      this.renderKeys();
      this.keysKey = key;
    }
  }

  // ---- interaction ----------------------------------------------------------------------
  hit(x, y, h) {
    const v = this.app.view;
    const p = this.pitchAt(y, h);
    const notes = this.app.notes.sorted();
    for (let i = notes.length - 1; i >= 0; i--) {
      const n = notes[i];
      const t = this.times(n);
      if (t.p !== p) continue;
      const x0 = v.x(t.s);
      const x1 = v.x(t.e);
      if (x >= x0 - 2 && x <= x1 + 2) {
        const edge = x1 - x0 > 3 * EDGE_PX ? (x <= x0 + EDGE_PX ? 'l' : x >= x1 - EDGE_PX ? 'r' : null) : (x >= x1 - 3 ? 'r' : null);
        return { note: n, edge };
      }
    }
    return null;
  }

  snap(t, e) {
    const app = this.app;
    const tempo = app.project?.tempo;
    if (e && e.altKey) return t;
    if (tempoValid(tempo)) return quantizeTime(t, tempo, app.project.quantize?.grid || '1/16');
    return Math.round(t * 100) / 100;
  }

  attach() {
    const c = this.canvas;
    const app = this.app;
    const local = (e) => {
      const r = c.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top, h: r.height };
    };
    c.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || app.notes.locked) return;
      const { x, y, h } = local(e);
      const hit = this.hit(x, y, h);
      c.setPointerCapture(e.pointerId);
      if (hit) {
        const id = hit.note.id;
        if (e.shiftKey || e.ctrlKey) app.toggleSelect(id);
        else if (!app.selection.has(id)) app.select([id]);
        const ids = app.selection.has(id) ? [...app.selection] : [id];
        this.drag = {
          mode: hit.edge ? `resize-${hit.edge}` : 'move', x0: x, y0: y, h, t0: app.view.t(x), p0: this.pitchAt(y, h),
          id, orig: ids.map((i) => ({ ...app.notes.get(i) })), moved: false,
        };
      } else {
        if (!e.shiftKey) app.select([]);
        this.drag = { mode: 'band', x0: x, y0: y, h, moved: false, base: new Set(app.selection) };
      }
    });
    c.addEventListener('pointermove', (e) => {
      const { x, y, h } = local(e);
      if (!this.drag) {
        const hit = this.hit(x, y, h);
        c.style.cursor = hit ? (hit.edge ? 'ew-resize' : 'grab') : 'crosshair';
        return;
      }
      const d = this.drag;
      if (!d.moved && Math.hypot(x - d.x0, y - d.y0) < 3) return;
      d.moved = true;
      if (d.mode === 'band') {
        this.band = { x0: d.x0, y0: d.y0, x1: x, y1: y };
        const t0 = app.view.t(Math.min(x, d.x0));
        const t1 = app.view.t(Math.max(x, d.x0));
        const pHi = this.pitchAt(Math.min(y, d.y0), h);
        const pLo = this.pitchAt(Math.max(y, d.y0), h);
        const ids = new Set(d.base);
        for (const n of app.notes.sorted()) {
          const t = this.times(n);
          if (t.e >= t0 && t.s <= t1 && t.p >= pLo && t.p <= pHi) ids.add(n.id);
        }
        app.select([...ids], false);
        return;
      }
      const main = d.orig.find((n) => n.id === d.id);
      let dt = app.view.t(x) - d.t0;
      const dp = d.mode === 'move' ? this.pitchAt(y, h) - d.p0 : 0;
      const pv = new Map();
      if (d.mode === 'move') {
        dt = this.snap(main.start_sec + dt, e) - main.start_sec;
        for (const n of d.orig) pv.set(n.id, { start_sec: Math.max(0, n.start_sec + dt), end_sec: Math.max(0.02, n.end_sec + dt), midi_pitch: clamp(n.midi_pitch + dp, 0, 127) });
      } else if (d.mode === 'resize-r') {
        for (const n of d.orig) pv.set(n.id, { start_sec: n.start_sec, end_sec: Math.max(n.start_sec + 0.02, this.snap(n.end_sec + dt, e)), midi_pitch: n.midi_pitch });
      } else {
        for (const n of d.orig) pv.set(n.id, { start_sec: Math.min(n.end_sec - 0.02, this.snap(n.start_sec + dt, e)), end_sec: n.end_sec, midi_pitch: n.midi_pitch });
      }
      this.preview = pv;
      this.previewVersion = (this.previewVersion || 0) + 1;
      c.style.cursor = d.mode === 'move' ? 'grabbing' : 'ew-resize';
    });
    c.addEventListener('pointerup', (e) => {
      const d = this.drag;
      this.drag = null;
      this.band = null;
      if (!d) return;
      if (!d.moved) {
        if (d.mode !== 'band') app.audition(app.notes.get(d.id));
        else app.seek(app.view.t(d.x0));
        return;
      }
      if (this.preview && d.mode !== 'band') {
        const changes = [];
        for (const [id, p] of this.preview) {
          const n = app.notes.get(id);
          const ch = { id, ...p };
          if (p.midi_pitch !== n.midi_pitch) {
            const { prev, next } = app.notes.neighbours(id);
            const f = chooseForPitch(n, p.midi_pitch, app.tuning, prev, next);
            ch.string = f.string;
            ch.fret = f.fret;
          }
          changes.push(ch);
        }
        app.notes.moveResize(changes, d.mode === 'move' ? '移動' : '長さ変更');
      }
      this.preview = null;
      this.previewVersion++;
      e.preventDefault();
    });
    c.addEventListener('dblclick', (e) => {
      if (app.notes.locked) return;
      const { x, y, h } = local(e);
      if (this.hit(x, y, h)) return;
      const tempo = app.project?.tempo;
      const len = tempoValid(tempo) ? gridSeconds(tempo, app.project.quantize?.grid || '1/16') * 2 : 0.25;
      const start = this.snap(app.view.t(x), e);
      const pitch = this.pitchAt(y, h);
      const f = chooseForPitch({ string: null, fret: null }, pitch, app.tuning, null, null);
      const n = app.notes.addNote(start, start + len, pitch, f);
      app.select([n.id]);
    });
    c.addEventListener('wheel', (e) => {
      if (!(e.shiftKey || e.altKey)) return;
      e.preventDefault();
      e.stopPropagation();
      const step = e.deltaY > 0 ? -2 : 2;
      const span = this.pmax - this.pmin;
      this.pmin = clamp(this.pmin + step, 0, 127 - span);
      this.pmax = this.pmin + span;
      this.cacheKey = null;
    }, { passive: false });
  }
}

function hatch(ctx, x, y, w, h) {
  ctx.save();
  ctx.beginPath();
  ctx.rect(x, y, w, h);
  ctx.clip();
  ctx.strokeStyle = 'rgba(0,0,0,0.35)';
  ctx.lineWidth = 1;
  for (let k = -h; k < w; k += 5) {
    ctx.beginPath();
    ctx.moveTo(x + k, y + h);
    ctx.lineTo(x + k + h, y);
    ctx.stroke();
  }
  ctx.restore();
}

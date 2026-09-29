// Estimated TAB on the shared time axis. String 1 (highest) on top.
// Click = select + play from the note; drag a fret number up/down = same pitch on another string.

import { candidates } from '../notes/fingering.js';
import { cssVar, fitCanvas, noteName } from '../util.js';
import { CanvasView, drawGrid, drawTimeOverlay } from './timeline.js';

export class TabView extends CanvasView {
  constructor(canvas, labelsCanvas, app) {
    super(canvas, app);
    this.labels = labelsCanvas;
    this.drag = null;
    this.dragVersion = 0;
  }

  get n() { return this.app.tuning.strings.length; }
  gap(h) { return h / (this.n + 1); }
  y(stringNo, h) { return stringNo * this.gap(h); }
  stringAt(y, h) { return Math.min(this.n, Math.max(1, Math.round(y / this.gap(h)))); }

  staticKey(w, h) {
    return `${super.staticKey(w, h)}|${this.app.notes.version}|${this.app.tuning.strings.join(',')}|${this.dragVersion}`;
  }

  drawStatic(ctx, w, h) {
    const app = this.app;
    const v = app.view;
    ctx.fillStyle = cssVar('--lane-bg');
    ctx.fillRect(0, 0, w, h);
    drawGrid(ctx, w, h, app);
    ctx.strokeStyle = 'rgba(203, 213, 224, 0.45)';
    ctx.lineWidth = 1;
    for (let s = 1; s <= this.n; s++) {
      const y = Math.round(this.y(s, h)) + 0.5;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }
    const thr = app.project?.confidence_threshold ?? 0.5;
    const fs = Math.max(10, Math.min(13, this.gap(h) - 3));
    ctx.font = `600 ${fs}px ui-monospace, Consolas, monospace`;
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'center';
    let prev = null;
    for (const n of app.notes.sorted()) {
      const before = prev;
      prev = n;
      const d = app.displayTimes(n);
      if (d.e < v.t0 || d.s > v.t1) continue;
      const x = v.x(d.s);
      const x1 = v.x(d.e);
      const low = n.confidence < thr;
      let stringNo = n.string;
      let fret = n.fret;
      if (this.drag && this.drag.id === n.id && this.drag.target) {
        stringNo = this.drag.target.string;
        fret = this.drag.target.fret;
      }
      if (stringNo == null || fret == null) {
        ctx.fillStyle = cssVar('--danger');
        ctx.fillText(`${noteName(n.midi_pitch)}?`, x + 10, 8);
        continue;
      }
      const y = this.y(stringNo, h);
      ctx.fillStyle = low ? 'rgba(246, 173, 85, 0.35)' : 'rgba(79, 209, 197, 0.35)';
      ctx.fillRect(x, y - 2, Math.max(2, x1 - x), 4);
      const text = String(fret);
      const tw = ctx.measureText(text).width + 6;
      const bx = x - 1;
      ctx.fillStyle = app.selection.has(n.id) ? cssVar('--sel') : low ? cssVar('--low-conf') : '#e2e8f0';
      roundRect(ctx, bx, y - fs / 2 - 2, tw, fs + 4, 3);
      ctx.fill();
      if (n.edited) {
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 1;
        ctx.stroke();
      }
      ctx.fillStyle = '#0b0f14';
      ctx.fillText(text, bx + tw / 2, y + 0.5);
      if (n.fingering_edited) {
        ctx.fillStyle = cssVar('--sel');
        ctx.fillRect(bx + 1, y + fs / 2 + 3, tw - 2, 2);
      }
      if (n.technique) {
        ctx.fillStyle = cssVar('--text-dim');
        ctx.font = `10px system-ui`;
        ctx.fillText(techMark(n, before), bx + tw / 2, y - fs / 2 - 7);
        ctx.font = `600 ${fs}px ui-monospace, Consolas, monospace`;
      }
    }
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
  }

  drawOverlay(ctx, w, h, pos) {
    drawTimeOverlay(ctx, w, h, this.app, pos);
  }

  renderLabels() {
    const { ctx, w, h } = fitCanvas(this.labels);
    ctx.clearRect(0, 0, w, h);
    ctx.font = '600 11px ui-monospace, Consolas, monospace';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = cssVar('--text-dim');
    const strings = [...this.app.tuning.strings].reverse();
    strings.forEach((p, i) => ctx.fillText(noteName(p), 4, this.y(i + 1, h)));
  }

  render(pos) {
    super.render(pos);
    const key = `${this.app.tuning.strings.join(',')}|${this.labels.clientHeight}`;
    if (key !== this.labelsKey) {
      this.renderLabels();
      this.labelsKey = key;
    }
  }

  hit(x, y, h) {
    const v = this.app.view;
    const s = this.stringAt(y, h);
    let best = null;
    for (const n of this.app.notes.sorted()) {
      if (n.string !== s) continue;
      const d = this.app.displayTimes(n);
      const x0 = v.x(d.s) - 2;
      const x1 = Math.max(v.x(d.s) + 18, Math.min(v.x(d.e), v.x(d.s) + 40));
      if (x >= x0 && x <= x1) best = n;
    }
    return best;
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
      const n = this.hit(x, y, h);
      c.setPointerCapture(e.pointerId);
      if (!n) {
        this.drag = { empty: true, x0: x };
        return;
      }
      if (e.shiftKey || e.ctrlKey) app.toggleSelect(n.id);
      else app.select([n.id]);
      this.drag = { id: n.id, y0: y, moved: false, target: null };
    });
    c.addEventListener('pointermove', (e) => {
      const d = this.drag;
      if (!d || d.empty) return;
      const { y, h } = local(e);
      if (!d.moved && Math.abs(y - d.y0) < 4) return;
      d.moved = true;
      const n = app.notes.get(d.id);
      const s = this.stringAt(y, h);
      const cand = candidates(n.midi_pitch, app.tuning).find((c2) => c2.string === s);
      d.target = cand || null;
      c.style.cursor = cand ? 'ns-resize' : 'not-allowed';
      this.dragVersion++;
    });
    c.addEventListener('pointerup', () => {
      const d = this.drag;
      this.drag = null;
      c.style.cursor = '';
      if (!d) return;
      if (d.empty) {
        app.seek(app.view.t(d.x0));
        app.select([]);
        return;
      }
      if (!d.moved) {
        app.audition(app.notes.get(d.id));
        return;
      }
      if (d.target && d.target.string !== app.notes.get(d.id).string) app.notes.setString(d.id, d.target.string, app.tuning);
      this.dragVersion++;
    });
  }
}

const TECH_MARKS = {
  hammer: 'h', pull: 'p', ghost: '( )', mute: 'x', bend: 'b', vibrato: '~',
  slide_in_below: '↗', slide_in_above: '↘', slide_out_down: '↘', slide_out_up: '↗',
};

// Slides from the previous note point the way the pitch goes (/ up, \ down).
function techMark(n, prev) {
  if (n.technique === 'slide' || n.technique === 'slide_shift') {
    const dir = prev && prev.midi_pitch > n.midi_pitch ? '\\' : '/';
    return n.technique === 'slide_shift' ? `${dir}s` : dir;
  }
  return TECH_MARKS[n.technique] || n.technique;
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

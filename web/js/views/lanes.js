// Stem lanes: mixer header (mute / solo / gain) + waveform on the shared time axis.
// Click = seek, drag = set the A/B loop range.

import { isActive, normalizeFx } from '../audio/fx.js';
import { cssVar, el } from '../util.js';
import { CanvasView, drawGrid } from './timeline.js';

export class StemLaneView extends CanvasView {
  constructor(canvas, app, stem) {
    super(canvas, app);
    this.stem = stem;
  }

  drawStatic(ctx, w, h) {
    const app = this.app;
    const v = app.view;
    ctx.fillStyle = cssVar('--lane-bg');
    ctx.fillRect(0, 0, w, h);
    drawGrid(ctx, w, h, app);
    const peaks = app.peaks?.stems?.[this.stem.name];
    if (!peaks) return;
    const per = app.peaks.per_sec || 100;
    const g = Math.min(2.2, app.effectiveGain(this.stem.name));
    const audible = g > 0;
    ctx.fillStyle = audible ? cssVar(`--c-${this.stem.name}`) || '#a0aec0' : 'rgba(160,174,192,0.25)';
    const mid = h / 2;
    const scale = (h / 2 - 2) * (audible ? Math.max(0.35, Math.sqrt(g)) : 0.6);
    for (let x = 0; x < w; x++) {
      const a = Math.floor(v.t(x) * per);
      if (a >= peaks.length) break;
      if (a < 0) continue;
      const b = Math.max(a + 1, Math.floor(v.t(x + 1) * per));
      let m = 0;
      for (let i = a; i < b && i < peaks.length; i++) if (peaks[i] > m) m = peaks[i];
      const bh = Math.min(h / 2 - 1, (m / 255) * scale);
      ctx.fillRect(x, mid - bh, 1, Math.max(1, bh * 2));
    }
  }

  attach() {
    attachSeekAndLoopDrag(this.canvas, this.app);
  }
}

export function attachSeekAndLoopDrag(canvas, app) {
  let start = null;
  let moved = false;
  canvas.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    const r = canvas.getBoundingClientRect();
    start = { x: e.clientX - r.left, t: app.view.t(e.clientX - r.left) };
    moved = false;
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointermove', (e) => {
    if (!start) return;
    const r = canvas.getBoundingClientRect();
    const x = e.clientX - r.left;
    if (!moved && Math.abs(x - start.x) < 4) return;
    moved = true;
    const t = app.view.t(x);
    app.setLoop(true, Math.max(0, Math.min(start.t, t)), Math.min(app.view.duration, Math.max(start.t, t)), false);
  });
  canvas.addEventListener('pointerup', () => {
    if (!start) return;
    if (!moved) app.seek(start.t);
    else app.setLoop(true, app.loop.a, app.loop.b, true);
    start = null;
  });
}

export function buildStemHeader(stem, app) {
  const mute = el('button', { class: 'tog mute', title: 'ミュート' }, 'M');
  const solo = el('button', { class: 'tog solo', title: 'ソロ' }, 'S');
  let gainCtl;
  if (stem.fixed_gain) {
    gainCtl = el('span', { class: 'gain-fixed', title: 'ベースは 0 dB 固定' }, '0 dB 固定');
  } else {
    gainCtl = el('select', { class: 'gain', title: '音量' },
      ...app.gainSteps.map((db) => el('option', { value: db }, `${db > 0 ? '+' : ''}${db} dB`)));
    gainCtl.addEventListener('change', () => app.setStemGainDb(stem.name, Number(gainCtl.value)));
  }
  const fxBtn = el('button', { class: 'tog fxb', title: 'このステムの EQ / コンプ' }, 'FX');
  fxBtn.addEventListener('click', () => app.openFx(stem.name));
  mute.addEventListener('click', () => app.toggleMute(stem.name));
  solo.addEventListener('click', () => app.toggleSolo(stem.name));
  const head = el('div', { class: 'head stem-head', dataset: { stem: stem.name } },
    el('span', { class: 'swatch', style: `background: var(--c-${stem.name})` }),
    el('span', { class: 'name', title: stem.name }, stem.label),
    el('span', { class: 'ctl' }, mute, solo, fxBtn, gainCtl));
  head.update = () => {
    const mx = app.project.mixer;
    mute.classList.toggle('on', !!mx.mute[stem.name]);
    solo.classList.toggle('on', !!mx.solo[stem.name]);
    if (!stem.fixed_gain) gainCtl.value = String(mx.gains_db[stem.name] ?? 0);
    head.classList.toggle('silent', app.effectiveGain(stem.name) === 0);
    fxBtn.classList.toggle('on', isActive(normalizeFx((app.project.fx || {})[stem.name])));
  };
  return head;
}

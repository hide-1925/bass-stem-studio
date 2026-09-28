// FX rack drawer: graphical EQ with live spectrum (CueMix-like) + filters, and a compressor with
// transfer curve, attack/release envelope and GR meter (AVO-like). One target at a time:
// the master bus or any stem.

import { BANDS, COMP_PRESETS, EQ_PRESETS, EQ_RANGE_DB, EQ_STEP_DB, applyCompPreset, applyEqPreset, defaultFx, isActive, normalizeFx } from '../audio/fx.js';
import { Knob } from '../ui/knob.js';
import { el, fitCanvas, noteName } from '../util.js';

const F_MIN = 20;
const F_MAX = 20000;
const DB_TOP = 18;
const DB_BOT = -18;
const SPEC_TOP = -10;
const SPEC_BOT = -100;
const C_MIN = -60;
const C_MAX = 0;
const AMBER = '#d4a030';

const fmtHz = (f) => (f >= 1000 ? `${(f / 1000).toFixed(f >= 10000 ? 1 : 2).replace(/\.?0+$/, '')}k` : `${Math.round(f)}`);
const fmtDb = (v) => `${v > 0 ? '+' : ''}${(+v).toFixed(Math.abs(v % 1) > 0.01 ? 1 : 0)}dB`;
const clone = (o) => JSON.parse(JSON.stringify(o));

export class FxPanel {
  constructor(root, app) {
    this.root = root;
    this.app = app;
    this.target = 'master';
    this.sel = 'mid';
    this.open = false;
    this.trail = [];
    this.grHist = new Float32Array(240);
    this.grPos = 0;
    this._build();
  }

  // ---- state ---------------------------------------------------------------------------
  get chain() {
    return this.app.engine.fxChain(this.target);
  }

  settings(target = this.target) {
    const all = this.app.project.fx || (this.app.project.fx = {});
    all[target] = normalizeFx(all[target]);
    return all[target];
  }

  update(fn) {
    const fx = fn(clone(this.settings()));
    this.app.project.fx[this.target] = fx;
    const ch = this.chain;
    if (ch) ch.apply(fx);
    this.app.onFxChanged(this.target);
    this.sync();
  }

  show(target) {
    if (target) this.target = target;
    this.open = true;
    this.root.hidden = false;
    this.sync();
  }

  hide() {
    this.open = false;
    this.root.hidden = true;
  }

  // ---- DOM -----------------------------------------------------------------------------
  _build() {
    const band = () => BANDS.find((b) => b.id === this.sel);
    const setBand = (patch) => this.update((fx) => { Object.assign(fx.eq.bands[this.sel], patch); return fx; });
    this.kFreq = new Knob({ label: 'Freq', min: 20, max: 20000, log: true, step: 1, value: 800, def: 800, format: fmtHz,
      onChange: (v) => setBand({ freq: Math.round(v) }) });
    this.kGain = new Knob({ label: 'Gain', min: -EQ_RANGE_DB, max: EQ_RANGE_DB, step: EQ_STEP_DB, fineStep: 0.5, value: 0, def: 0, bipolar: true, format: fmtDb,
      onChange: (v) => setBand({ gain: v }) });
    this.kQ = new Knob({ label: 'Q', min: 0.3, max: 8, log: true, step: 0.1, fineStep: 0.05, value: 1, def: 1, format: (v) => v.toFixed(2),
      onChange: (v) => {
        const b = band();
        if (b.kind === 'hpf' || b.kind === 'lpf') setBand({ slope: v >= 18 ? 24 : 12 });
        else setBand({ q: +v.toFixed(2) });
      } });
    this.kTrim = new Knob({ label: 'Output', min: -20, max: 20, step: 1, fineStep: 0.5, value: 0, def: 0, bipolar: true, format: fmtDb,
      onChange: (v) => this.update((fx) => { fx.eq.trim = v; return fx; }) });
    const comp = (key) => (v) => this.update((fx) => { fx.comp[key] = v; return fx; });
    this.kThr = new Knob({ label: 'Thresh', min: -60, max: 0, step: 1, fineStep: 0.5, value: -24, def: -24, format: fmtDb, onChange: comp('threshold') });
    this.kRatio = new Knob({ label: 'Ratio', min: 1, max: 20, step: 0.5, value: 4, def: 4, format: (v) => (v >= 20 ? '∞:1' : `${v % 1 ? v.toFixed(1) : v}:1`), onChange: comp('ratio') });
    this.kAtk = new Knob({ label: 'Attack', min: 0.5, max: 200, log: true, step: 0.1, value: 10, def: 10, format: (v) => (v < 10 ? `${v.toFixed(1)}ms` : `${Math.round(v)}ms`), onChange: comp('attack') });
    this.kRel = new Knob({ label: 'Release', min: 10, max: 1000, log: true, step: 1, value: 150, def: 150, format: (v) => `${Math.round(v)}ms`, onChange: comp('release') });
    this.kKnee = new Knob({ label: 'Knee', min: 0, max: 30, step: 1, value: 6, def: 6, format: (v) => `${Math.round(v)}dB`, onChange: comp('knee') });
    this.kMake = new Knob({ label: 'Gain', min: -6, max: 18, step: 0.5, value: 0, def: 0, bipolar: true, format: fmtDb, onChange: comp('gain') });

    this.ledEq = this._led('EQ 全体の ON/OFF', () => this.update((fx) => { fx.eq.on = !fx.eq.on; return fx; }));
    this.ledBand = this._led('選択バンドの ON/OFF', () => setBand({ on: !this.settings().eq.bands[this.sel].on }));
    this.ledComp = this._led('コンプの ON/OFF', () => this.update((fx) => { fx.comp.on = !fx.comp.on; return fx; }));
    this.targets = el('div', { class: 'fx-targets' });
    this.eqPreset = el('select', { title: 'EQ プリセット（選択中の対象に適用）' },
      el('option', { value: '' }, 'EQ プリセット…'), ...EQ_PRESETS.map((p, i) => el('option', { value: i }, p.name)));
    this.eqPreset.addEventListener('change', () => {
      const p = EQ_PRESETS[Number(this.eqPreset.value)];
      if (p) this.update((fx) => applyEqPreset(fx, p));
      this.eqPreset.value = '';
    });
    this.compPreset = el('select', { title: 'コンプ プリセット' },
      el('option', { value: '' }, 'コンプ プリセット…'), ...COMP_PRESETS.map((p, i) => el('option', { value: i }, p.name)));
    this.compPreset.addEventListener('change', () => {
      const p = COMP_PRESETS[Number(this.compPreset.value)];
      if (p) this.update((fx) => applyCompPreset(fx, p));
      this.compPreset.value = '';
    });
    this.chips = el('div', { class: 'band-chips' }, ...BANDS.map((b) => {
      const c = el('button', { class: 'chip', dataset: { band: b.id }, style: `--band:${b.color}` }, el('i'), b.label);
      c.addEventListener('click', () => { this.sel = b.id; this.sync(); });
      return c;
    }));
    this.eqCanvas = el('canvas', { class: 'eq-graph' });
    this.tfCanvas = el('canvas', { class: 'tf-graph' });
    this.envCanvas = el('canvas', { class: 'env-graph' });
    this.grCanvas = el('canvas', { class: 'gr-hist' });
    this.grFill = el('div', { class: 'meter-fill' });
    this.grVal = el('span', { class: 'meter-db' }, '0.0 dB');
    this.bandInfo = el('div', { class: 'band-info' });
    this.targetLabel = el('span', { class: 'fx-target-label' });

    this.root.replaceChildren(
      el('div', { class: 'fx-hd' },
        el('div', {}, el('div', { class: 'fx-brand' }, 'FX RACK · EQ / FILTER / COMP'), el('div', { class: 'fx-model' }, '対象を選んで調整（マスター＝全体、各ステム＝そのパートだけ）')),
        this.targets,
        el('div', { class: 'fx-hd-right' }, this.eqPreset, this.compPreset,
          el('button', { class: 'btn-r', title: 'この対象の EQ / フィルター / コンプを初期値に', onclick: () => this.update(() => defaultFx()) }, 'Reset'),
          el('button', { class: 'btn-r', title: '閉じる', onclick: () => this.app.toggleFx(false) }, '×'))),
      el('div', { class: 'fx-body' },
        el('div', { class: 'fx-unit eq-unit' },
          el('div', { class: 'unit-hd' }, this.ledEq, el('span', { class: 'unit-name' }, 'EQUALIZER'), this.targetLabel,
            el('span', { class: 'unit-hint' }, 'ドラッグ: 周波数 / ゲイン（5 dB 刻み・Shift で 0.5 dB）　ホイール: Q・スロープ　ダブルクリック: リセット')),
          el('div', { class: 'graph-wrap' }, this.eqCanvas),
          el('div', { class: 'eq-controls' }, this.chips,
            el('div', { class: 'knob-row' }, el('div', { class: 'band-led' }, this.ledBand, el('span', {}, 'BAND')), this.kFreq.el, this.kGain.el, this.kQ.el,
              el('div', { class: 'sep' }), this.kTrim.el),
            this.bandInfo)),
        el('div', { class: 'fx-unit comp-unit' },
          el('div', { class: 'unit-hd' }, this.ledComp, el('span', { class: 'unit-name' }, 'COMPRESSOR')),
          el('div', { class: 'comp-graphs' },
            el('div', { class: 'graph-wrap tf' }, this.tfCanvas, el('span', { class: 'gl-y' }, 'OUT'), el('span', { class: 'gl-x' }, 'IN →')),
            el('div', { class: 'graph-wrap env' }, this.envCanvas)),
          el('div', { class: 'knob-row' }, this.kThr.el, this.kRatio.el, this.kAtk.el, this.kRel.el, this.kKnee.el, this.kMake.el),
          el('div', { class: 'meter-wrap' },
            el('div', { class: 'meter-row' }, el('span', { class: 'meter-name' }, 'GR'), el('div', { class: 'meter-bg' }, this.grFill), this.grVal),
            this.grCanvas))),
    );
    this._bindEq();
  }

  _led(title, onclick) {
    const d = el('div', { class: 'led', title });
    d.addEventListener('click', onclick);
    return d;
  }

  // Refresh controls from the current target's settings.
  sync() {
    if (!this.app.project) return;
    const fx = this.settings();
    const b = BANDS.find((x) => x.id === this.sel);
    const s = fx.eq.bands[this.sel];
    const isFilter = b.kind === 'hpf' || b.kind === 'lpf';
    this.ledEq.classList.toggle('on', fx.eq.on);
    this.ledBand.classList.toggle('on', !!s.on);
    this.ledComp.classList.toggle('on', fx.comp.on);
    this.kFreq.o.min = b.fmin;
    this.kFreq.o.max = b.fmax;
    this.kFreq.set(s.freq);
    this.kGain.set(isFilter ? 0 : s.gain ?? 0);
    this.kGain.setEnabled(!isFilter && fx.eq.on && s.on);
    if (isFilter) Object.assign(this.kQ.o, { label: 'Slope', min: 12, max: 24, log: false, step: 12, fineStep: 12, def: 12, format: (v) => `${v}dB/oct` });
    else Object.assign(this.kQ.o, { label: 'Q', min: 0.3, max: 8, log: true, step: 0.1, fineStep: 0.05, def: 1, format: (v) => v.toFixed(2) });
    this.kQ.labelEl.textContent = this.kQ.o.label;
    this.kQ.set(isFilter ? s.slope : s.q ?? 0.7);
    this.kQ.setEnabled(b.kind !== 'lowshelf' && b.kind !== 'highshelf' && fx.eq.on && s.on);
    this.kFreq.setEnabled(fx.eq.on && s.on);
    this.kTrim.set(fx.eq.trim || 0);
    this.kTrim.setEnabled(fx.eq.on);
    const c = fx.comp;
    for (const [k, v] of [[this.kThr, c.threshold], [this.kRatio, c.ratio], [this.kAtk, c.attack], [this.kRel, c.release], [this.kKnee, c.knee], [this.kMake, c.gain]]) {
      k.set(v);
      k.setEnabled(c.on);
    }
    for (const chip of this.chips.children) {
      const id = chip.dataset.band;
      chip.classList.toggle('sel', id === this.sel);
      const bs = fx.eq.bands[id];
      const bd = BANDS.find((x) => x.id === id);
      const active = (bd.kind === 'hpf' || bd.kind === 'lpf') ? bs.on : bs.on && bs.gain !== 0;
      chip.classList.toggle('active', fx.eq.on && active);
      chip.classList.toggle('off', !bs.on);
    }
    const typeJa = { hpf: 'ハイパス（低域カット）', lpf: 'ローパス（高域カット）', lowshelf: 'ローシェルフ', highshelf: 'ハイシェルフ', peaking: 'ピーキング' }[b.kind];
    this.bandInfo.textContent = `${b.label}: ${typeJa} ${fmtHz(s.freq)} Hz` +
      (isFilter ? ` ${s.slope} dB/oct ${s.on ? '' : '（OFF）'}` : ` ${fmtDb(s.gain)}${b.kind === 'peaking' ? ` Q ${(+s.q).toFixed(2)}` : ''}${s.on ? '' : '（OFF）'}`);
    // targets
    const list = [{ name: 'master', label: 'マスター' }, ...this.app.stems.map((st) => ({ name: st.name, label: st.label }))];
    this.targets.replaceChildren(...list.map((t) => {
      const act = isActive(normalizeFx((this.app.project.fx || {})[t.name]));
      const btn = el('button', { class: `tgt ${t.name === this.target ? 'sel' : ''} ${act ? 'active' : ''}`, style: t.name === 'master' ? '' : `--dot: var(--c-${t.name})`, title: act ? 'FX 適用中' : '' },
        t.name === 'master' ? '' : el('i'), t.label);
      btn.addEventListener('click', () => { this.target = t.name; this.trail = []; this.sync(); });
      return btn;
    }));
    this.targetLabel.textContent = this.target === 'master' ? '— マスター（全体）' : `— ${this.app.stems.find((x) => x.name === this.target)?.label || this.target}`;
    this.envDirty = true;
  }

  // ---- EQ graph --------------------------------------------------------------------------
  xOf(f, w) { return (Math.log10(f / F_MIN) / Math.log10(F_MAX / F_MIN)) * w; }
  fOf(x, w) { return F_MIN * Math.pow(F_MAX / F_MIN, Math.max(0, Math.min(1, x / w))); }
  yOf(db, h) { return ((DB_TOP - db) / (DB_TOP - DB_BOT)) * h; }
  dbOf(y, h) { return DB_TOP - (y / h) * (DB_TOP - DB_BOT); }

  nodePos(b, s, w, h, total) {
    const x = this.xOf(s.freq, w);
    if (b.kind === 'hpf' || b.kind === 'lpf') {
      const i = Math.max(0, Math.min(total.length - 1, Math.round(x)));
      return { x, y: this.yOf(Math.max(DB_BOT + 1, Math.min(DB_TOP - 1, total[i] ?? -3)), h) };
    }
    return { x, y: this.yOf(s.gain, h) };
  }

  drawEq() {
    const { ctx, w, h } = fitCanvas(this.eqCanvas);
    const chain = this.chain;
    const fx = this.settings();
    if (!this.freqs || this.freqs.length !== w) {
      this.freqs = new Float32Array(w);
      for (let i = 0; i < w; i++) this.freqs[i] = this.fOf(i, w);
    }
    ctx.fillStyle = '#0e0d0b';
    ctx.fillRect(0, 0, w, h);
    // frequency grid
    ctx.lineWidth = 1;
    for (const dec of [10, 100, 1000, 10000]) {
      for (let m = 1; m < 10; m++) {
        const f = dec * m;
        if (f < F_MIN || f > F_MAX) continue;
        const x = Math.round(this.xOf(f, w)) + 0.5;
        ctx.strokeStyle = m === 1 ? '#2a261e' : '#1a1814';
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
      }
    }
    ctx.font = '9px ui-monospace, Consolas, monospace';
    ctx.fillStyle = '#5a5040';
    for (const f of [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]) {
      const x = this.xOf(f, w);
      ctx.fillText(fmtHz(f), Math.min(w - 22, x + 2), h - 4);
    }
    // dB grid every 5 dB
    for (let db = -15; db <= 15; db += EQ_STEP_DB) {
      const y = Math.round(this.yOf(db, h)) + 0.5;
      ctx.strokeStyle = db === 0 ? '#3a3428' : '#1e1b16';
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
      ctx.fillStyle = db === 0 ? '#7a6a52' : '#4a4232';
      ctx.fillText(`${db > 0 ? '+' : ''}${db}`, 3, y - 2);
    }
    // bass open strings as a reference on the frequency axis
    ctx.fillStyle = '#4fd1c5';
    ctx.globalAlpha = 0.55;
    for (const p of this.app.tuning.strings) {
      const f = 440 * Math.pow(2, (p - 69) / 12);
      const x = this.xOf(f, w);
      ctx.fillRect(Math.round(x), h - 22, 1, 7);
      ctx.fillText(noteName(p), x - 6, h - 25);
    }
    ctx.globalAlpha = 1;
    // live spectrum of the selected target (after its FX)
    if (chain && this.app.engine.playing) {
      const an = chain.post;
      const bins = this.spec || (this.spec = new Float32Array(an.frequencyBinCount));
      an.getFloatFrequencyData(bins);
      const binHz = this.app.engine.ctx.sampleRate / an.fftSize;
      ctx.beginPath();
      ctx.moveTo(0, h);
      for (let x = 0; x < w; x++) {
        const a = Math.floor(this.fOf(x, w) / binHz);
        const b = Math.max(a + 1, Math.floor(this.fOf(x + 1, w) / binHz));
        let m = -200;
        for (let i = a; i < b && i < bins.length; i++) if (bins[i] > m) m = bins[i];
        const y = h - ((Math.max(SPEC_BOT, Math.min(SPEC_TOP, m)) - SPEC_BOT) / (SPEC_TOP - SPEC_BOT)) * h;
        ctx.lineTo(x, y);
      }
      ctx.lineTo(w, h);
      ctx.closePath();
      ctx.fillStyle = 'rgba(200, 140, 50, 0.16)';
      ctx.fill();
      ctx.strokeStyle = 'rgba(220, 160, 70, 0.35)';
      ctx.lineWidth = 1;
      ctx.stroke();
    }
    if (!chain) {
      ctx.fillStyle = '#7a6a52';
      ctx.font = '12px system-ui';
      ctx.fillText('ステムを読み込むと調整できます', 16, 24);
      return;
    }
    const total = chain.response(this.freqs);
    // per-band curves
    for (const b of BANDS) {
      const s = fx.eq.bands[b.id];
      const isFilter = b.kind === 'hpf' || b.kind === 'lpf';
      if (!fx.eq.on || !s.on || (!isFilter && s.gain === 0)) continue;
      const r = chain.bandResponse(b.id, this.freqs);
      ctx.beginPath();
      for (let x = 0; x < w; x++) {
        const y = this.yOf(Math.max(DB_BOT - 2, Math.min(DB_TOP + 2, r[x])), h);
        if (x) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      }
      ctx.strokeStyle = b.color;
      ctx.globalAlpha = b.id === this.sel ? 0.8 : 0.35;
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    // total response (glow + line), including the output trim
    const trim = fx.eq.on ? fx.eq.trim || 0 : 0;
    for (const [style, lw] of [['rgba(180,120,20,0.18)', 6], [fx.eq.on ? AMBER : '#3a3428', 2]]) {
      ctx.beginPath();
      for (let x = 0; x < w; x++) {
        const y = this.yOf(Math.max(DB_BOT - 2, Math.min(DB_TOP + 2, total[x] + trim)), h);
        if (x) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      }
      ctx.strokeStyle = style;
      ctx.lineWidth = lw;
      ctx.stroke();
    }
    // nodes
    ctx.font = 'bold 9px ui-monospace, Consolas, monospace';
    for (const b of BANDS) {
      const s = fx.eq.bands[b.id];
      const { x, y } = this.nodePos(b, s, w, h, total);
      const on = fx.eq.on && s.on;
      const sel = b.id === this.sel;
      ctx.beginPath();
      ctx.arc(x, y, sel ? 8 : 6, 0, Math.PI * 2);
      ctx.fillStyle = on ? b.color : '#1c1a16';
      ctx.globalAlpha = on ? 0.9 : 1;
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.strokeStyle = sel ? '#f5e6b8' : on ? '#0e0d0b' : '#5a5040';
      ctx.lineWidth = sel ? 2 : 1;
      ctx.stroke();
      ctx.fillStyle = on ? '#e8d5a0' : '#5a5040';
      ctx.fillText(b.label, Math.min(w - 40, x + 10), Math.max(10, y - 8));
    }
    this._lastTotal = total;
  }

  _bindEq() {
    const c = this.eqCanvas;
    let drag = null;
    const hit = (x, y, w, h) => {
      const fx = this.settings();
      let best = null;
      let bd = 14;
      for (const b of BANDS) {
        const p = this.nodePos(b, fx.eq.bands[b.id], w, h, this._lastTotal || new Float32Array(w));
        const d = Math.hypot(p.x - x, p.y - y);
        if (d < bd) { bd = d; best = b; }
      }
      return best;
    };
    const local = (e) => {
      const r = c.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top, w: r.width, h: r.height };
    };
    c.addEventListener('pointerdown', (e) => {
      if (!this.chain) return;
      const { x, y, w, h } = local(e);
      const b = hit(x, y, w, h);
      if (!b) return;
      this.sel = b.id;
      drag = { id: b.id };
      c.setPointerCapture(e.pointerId);
      if (!this.settings().eq.bands[b.id].on) this.update((fx) => { fx.eq.bands[b.id].on = true; return fx; });
      else this.sync();
    });
    c.addEventListener('pointermove', (e) => {
      const { x, y, w, h } = local(e);
      if (!drag) {
        c.style.cursor = hit(x, y, w, h) ? 'grab' : 'default';
        return;
      }
      c.style.cursor = 'grabbing';
      const b = BANDS.find((bb) => bb.id === drag.id);
      const freq = Math.round(Math.max(b.fmin, Math.min(b.fmax, this.fOf(x, w))));
      const patch = { freq };
      if (b.kind !== 'hpf' && b.kind !== 'lpf') {
        const raw = this.dbOf(y, h);
        const step = e.shiftKey ? 0.5 : EQ_STEP_DB;
        patch.gain = Math.max(-EQ_RANGE_DB, Math.min(EQ_RANGE_DB, Math.round(raw / step) * step));
      }
      this.update((fx) => { Object.assign(fx.eq.bands[drag.id], patch); return fx; });
    });
    c.addEventListener('pointerup', () => { drag = null; });
    c.addEventListener('dblclick', (e) => {
      const { x, y, w, h } = local(e);
      const b = hit(x, y, w, h);
      if (!b) return;
      const d = defaultFx().eq.bands[b.id];
      this.update((fx) => { fx.eq.bands[b.id] = (b.kind === 'hpf' || b.kind === 'lpf') ? { ...fx.eq.bands[b.id], on: false } : { ...d }; return fx; });
    });
    c.addEventListener('wheel', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const { x, y, w, h } = local(e);
      const b = hit(x, y, w, h) || BANDS.find((bb) => bb.id === this.sel);
      this.sel = b.id;
      if (b.kind === 'hpf' || b.kind === 'lpf') {
        this.update((fx) => { fx.eq.bands[b.id].slope = e.deltaY < 0 ? 24 : 12; return fx; });
      } else if (b.kind === 'peaking') {
        this.update((fx) => {
          const s = fx.eq.bands[b.id];
          s.q = +Math.max(0.3, Math.min(8, (s.q || 1) * (e.deltaY < 0 ? 1.12 : 1 / 1.12))).toFixed(2);
          return fx;
        });
      }
    }, { passive: false });
  }

  // ---- compressor ----------------------------------------------------------------------
  transfer(inDb, c) {
    const knee = c.knee;
    const t = c.threshold;
    const r = c.ratio >= 20 ? 1000 : c.ratio;
    let out;
    if (inDb < t - knee / 2) out = inDb;
    else if (knee > 0 && inDb <= t + knee / 2) {
      const x = inDb - t + knee / 2;
      out = inDb + ((1 / r - 1) * x * x) / (2 * knee);
    } else out = t + (inDb - t) / r;
    return out;
  }

  drawTransfer() {
    const { ctx, w, h } = fitCanvas(this.tfCanvas);
    const c = this.settings().comp;
    const X = (db) => ((db - C_MIN) / (C_MAX - C_MIN)) * w;
    const Y = (db) => h - ((db - C_MIN) / (C_MAX - C_MIN)) * h;
    ctx.fillStyle = '#0e0d0b';
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = '#1a1814';
    ctx.lineWidth = 1;
    for (let db = C_MIN; db <= C_MAX; db += 6) {
      ctx.beginPath(); ctx.moveTo(X(db), 0); ctx.lineTo(X(db), h); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, Y(db)); ctx.lineTo(w, Y(db)); ctx.stroke();
    }
    ctx.fillStyle = '#3e382c';
    ctx.font = '8px ui-monospace, Consolas, monospace';
    for (let db = C_MIN + 12; db < C_MAX; db += 12) ctx.fillText(`${db}`, X(db) + 2, h - 3);
    const gain = c.on ? c.gain : 0;
    ctx.strokeStyle = '#2c261c';
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(X(C_MIN), Y(C_MIN + gain)); ctx.lineTo(X(C_MAX), Y(C_MAX + gain)); ctx.stroke();
    ctx.setLineDash([3, 3]);
    ctx.strokeStyle = '#4a3c24';
    ctx.beginPath(); ctx.moveTo(X(c.threshold), 0); ctx.lineTo(X(c.threshold), h); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#6a5838';
    ctx.fillText('THR', X(c.threshold) + 3, 10);
    const curve = (style, lw) => {
      ctx.beginPath();
      for (let i = 0; i <= w; i++) {
        const inDb = C_MIN + (i / w) * (C_MAX - C_MIN);
        const out = (c.on ? this.transfer(inDb, c) : inDb) + gain;
        const y = Math.max(0, Math.min(h, Y(out)));
        if (i) ctx.lineTo(i, y); else ctx.moveTo(i, y);
      }
      ctx.strokeStyle = style;
      ctx.lineWidth = lw;
      ctx.stroke();
    };
    if (c.on) { curve('rgba(180,120,20,0.18)', 6); curve(AMBER, 2); } else curve('#3a3428', 2);
    // live operating point: measured input peak and the gain actually applied
    const chain = this.chain;
    if (chain && this.app.engine.playing) {
      const inDb = chain.inputPeakDb();
      const gr = c.on ? chain.reduction : 0;
      if (inDb > C_MIN) {
        this.trail.push({ x: X(inDb), y: Y(inDb + gr + gain) });
        if (this.trail.length > 24) this.trail.shift();
      }
      this.trail.forEach((p, i) => {
        const a = (i + 1) / this.trail.length;
        ctx.beginPath();
        ctx.arc(p.x, Math.max(0, Math.min(h, p.y)), i === this.trail.length - 1 ? 4 : 2, 0, Math.PI * 2);
        ctx.fillStyle = i === this.trail.length - 1 ? '#e06030' : `rgba(224, 96, 48, ${0.35 * a})`;
        ctx.fill();
      });
    } else this.trail = [];
    ctx.fillStyle = '#4a4030';
    ctx.fillText('TRANSFER CURVE', 8, 12);
    ctx.fillStyle = '#8a7448';
    const r = c.ratio >= 20 ? '∞:1' : `${c.ratio % 1 ? c.ratio.toFixed(1) : c.ratio}:1`;
    ctx.fillText(c.on ? r : 'BYPASS', w - 44, 12);
  }

  // Attack / release shown on a test burst: input jumps up for 0.5 s, then drops.
  drawEnvelope() {
    const { ctx, w, h } = fitCanvas(this.envCanvas);
    const c = this.settings().comp;
    const T = 1.2;
    const t0 = 0.1;
    const t1 = 0.6;
    const lo = -40;
    const hi = Math.max(c.threshold + 18, -12);
    const top = 0;
    const bot = -60;
    const Y = (db) => ((top - db) / (top - bot)) * (h - 14) + 2;
    ctx.fillStyle = '#0e0d0b';
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = '#1a1814';
    for (let s = 0; s <= T; s += 0.1) {
      const x = (s / T) * w;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h - 12); ctx.stroke();
    }
    const n = w;
    const dt = T / n;
    const input = [];
    const output = [];
    let gr = 0;
    for (let i = 0; i < n; i++) {
      const t = i * dt;
      const inDb = t >= t0 && t < t1 ? hi : lo;
      const target = c.on ? this.transfer(inDb, c) - inDb : 0;
      const tau = (target < gr ? c.attack : c.release) / 1000;
      gr += (target - gr) * (1 - Math.exp(-dt / Math.max(tau, 1e-4)));
      input.push(inDb);
      output.push(inDb + gr + (c.on ? c.gain : 0));
    }
    const line = (arr, style, lw) => {
      ctx.beginPath();
      arr.forEach((v, i) => (i ? ctx.lineTo(i, Y(v)) : ctx.moveTo(i, Y(v))));
      ctx.strokeStyle = style;
      ctx.lineWidth = lw;
      ctx.stroke();
    };
    line(input, '#4a4232', 1);
    if (c.on) {
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = '#4a3c24';
      ctx.beginPath(); ctx.moveTo(0, Y(c.threshold)); ctx.lineTo(w, Y(c.threshold)); ctx.stroke();
      ctx.setLineDash([]);
    }
    line(output, c.on ? AMBER : '#3a3428', 2);
    ctx.font = '8px ui-monospace, Consolas, monospace';
    ctx.fillStyle = '#4a4030';
    ctx.fillText('ENVELOPE (test burst)', 8, 10);
    if (c.on) {
      const xa = (t0 / T) * w;
      const xr = (t1 / T) * w;
      ctx.fillStyle = '#c08030';
      ctx.fillText(`ATK ${c.attack < 10 ? c.attack.toFixed(1) : Math.round(c.attack)}ms`, xa + 3, h - 2);
      ctx.fillText(`REL ${Math.round(c.release)}ms`, xr + 3, h - 2);
      ctx.fillRect(xa, h - 12, 1, 4);
      ctx.fillRect(xr, h - 12, 1, 4);
    }
  }

  drawGr() {
    const chain = this.chain;
    const c = this.settings().comp;
    const gr = chain && c.on && this.app.engine.playing ? chain.reduction : 0;
    this.grFill.style.width = `${Math.min(100, (Math.abs(gr) / 24) * 100)}%`;
    this.grVal.textContent = `${gr < -0.05 ? gr.toFixed(1) : '0.0'} dB`;
    this.grHist[this.grPos] = gr;
    this.grPos = (this.grPos + 1) % this.grHist.length;
    const { ctx, w, h } = fitCanvas(this.grCanvas);
    ctx.fillStyle = '#0a0908';
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = '#b05020';
    const n = this.grHist.length;
    for (let i = 0; i < n; i++) {
      const v = this.grHist[(this.grPos + i) % n];
      const bh = Math.min(h, (Math.abs(v) / 24) * h);
      ctx.fillRect((i / n) * w, 0, Math.ceil(w / n), bh);
    }
    ctx.fillStyle = '#3a3228';
    ctx.font = '8px ui-monospace, Consolas, monospace';
    ctx.fillText('GR 履歴 (4 s)', 4, h - 3);
  }

  render() {
    if (!this.open || !this.app.project) return;
    this.drawEq();
    this.drawTransfer();
    this.drawEnvelope();
    this.drawGr();
  }
}

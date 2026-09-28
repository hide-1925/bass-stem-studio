// Score view: standard notation + TAB in the Guitar Pro style (rendered by alphaTab), with a
// playback cursor, GP-like editing (type fret numbers, change note values) and Guitar Pro export.
//
// The notes stay in seconds; notes/score.js places them on the tempo grid. alphaTab only draws
// (its player is disabled): the cursor follows our audio engine.

import { openPitch } from '../notes/fingering.js';
import { beatAtTime, buildScore, stepAtTick, tempoOk, valueName, valueOfTicks, valueTicks } from '../notes/score.js';
import { el } from '../util.js';

let AT = null; // alphaTab module, loaded on first use (about 1 MB)
async function loadAlphaTab() {
  if (!AT) AT = await import('../vendor/alphatab/alphaTab.mjs');
  return AT;
}
const FONT_DIR = new URL('../vendor/alphatab/font/', import.meta.url).href;

const LOW_COLOR = '#c05621';
const MANUAL_COLOR = '#1d4ed8';

export class ScoreView {
  constructor(scroll, app) {
    this.scroll = scroll;
    this.app = app;
    this.visible = false;
    this.dirty = true; // alphaTab render out of date
    this.modelDirty = true; // rhythm model out of date
    this.model = null; // latest rhythm model (edits use this)
    this.shown = null; // model of the current drawing (cursor / clicks use this)
    this._rendering = null;
    this.atBeats = [];
    this.atNotes = new Map();
    this.boxes = [];
    this.caret = null; // {tick, string}
    this.pending = null; // two-digit fret entry
    this.msg = el('div', { class: 'score-msg' });
    this.paper = el('div', { class: 'score-paper' });
    this.surface = el('div', { class: 'score-surface' });
    this.overlay = el('div', { class: 'score-overlay' });
    this.barHl = el('div', { class: 'score-barhl', hidden: true });
    this.cursorEl = el('div', { class: 'score-cursor', hidden: true });
    this.lineEl = el('div', { class: 'score-line', hidden: true });
    this.caretEl = el('div', { class: 'score-caret', hidden: true });
    this.selLayer = el('div', { class: 'score-sel' });
    this.overlay.append(this.barHl, this.selLayer, this.cursorEl, this.caretEl, this.lineEl);
    this.paper.append(this.surface, this.overlay);
    this.scroll.append(this.msg, this.paper);
    this._rebuildSoon = null;
    this._lastBeat = -2;
    this._selKey = '';
    this._attach();
  }

  get opts() { return { ...DEFAULT_OPTS, ...(this.app.project?.score || {}) }; }

  // ------------------------------------------------------------------------------------------
  show() {
    this.visible = true;
    if (this.dirty) this.rebuild();
    else if (this.api) this.api.render(); // width may have changed while hidden
  }

  hide() { this.visible = false; }

  // another project was opened
  reset() {
    this.caret = null;
    this.pending = null;
    this.shown = null;
    this.boxes = [];
    this._lastBeat = -2;
    this.selLayer.replaceChildren();
    this.caretEl.hidden = this.cursorEl.hidden = this.lineEl.hidden = this.barHl.hidden = true;
    this.scroll.scrollTop = 0;
    this.invalidate();
  }

  invalidate() {
    this.dirty = true;
    this.modelDirty = true;
    if (!this.visible) return;
    clearTimeout(this._rebuildSoon);
    this._rebuildSoon = setTimeout(() => this.rebuild(), 120);
  }

  _ensureApi() {
    // one instance even when two rebuilds overlap while the module is loading
    if (!this._apiPromise) this._apiPromise = this._createApi();
    return this._apiPromise;
  }

  async _createApi() {
    const at = await loadAlphaTab();
    const s = new at.Settings();
    s.core.fontDirectory = FONT_DIR;
    s.core.useWorkers = false;
    s.core.engine = 'svg';
    s.core.includeNoteBounds = true;
    s.display.layoutMode = at.LayoutMode.Page;
    s.display.staveProfile = at.StaveProfile.Default;
    s.player.enablePlayer = false;
    for (const e of [at.NotationElement.ScoreTitle, at.NotationElement.ScoreSubTitle, at.NotationElement.ScoreArtist,
      at.NotationElement.ScoreAlbum, at.NotationElement.ScoreWords, at.NotationElement.ScoreMusic,
      at.NotationElement.ScoreWordsAndMusic, at.NotationElement.ScoreCopyright, at.NotationElement.EffectDynamics]) {
      s.notation.elements.set(e, false);
    }
    this._applyDisplaySettings(s);
    this.api = new at.AlphaTabApi(this.surface, s);
    this.api.error.on((e) => this.app.status(`譜面の描画に失敗: ${e?.message || e}`, true));
    this.api.postRenderFinished.on(() => this._afterRender());
    return this.api;
  }

  _applyDisplaySettings(s) {
    const o = this.opts;
    s.display.scale = Math.min(2, Math.max(0.5, Number(o.scale) || 1));
    s.notation.rhythmMode = o.staves === 'tab' ? AT.TabRhythmMode.ShowWithBars : AT.TabRhythmMode.Hidden;
  }

  // Rhythm model (cheap); also used while the score is hidden (inspector, value edits).
  ensureModel() {
    if (this.modelDirty) {
      this.modelDirty = false;
      const app = this.app;
      this.model = app.project && app.notes.size ? buildScore(app.notes.sorted(), app.project.tempo, this.opts) : null;
    }
    return this.model;
  }

  async rebuild() {
    this.dirty = false;
    const app = this.app;
    if (!app.project) return;
    if (!app.notes.size) {
      this._message('採譜が完了すると、ここに譜面（五線＋TAB）を表示します。');
      return;
    }
    if (!tempoOk(app.project.tempo)) {
      this._message('譜面にはテンポ（BPM）と1小節目の位置が必要です。左の「テンポを自動推定」を押すか、設定で入力してください。');
      return;
    }
    const t = performance.now();
    this.ensureModel();
    await this._ensureApi();
    this._applyDisplaySettings(this.api.settings);
    this.api.updateSettings();
    const score = this._toAlphaTab(this.model, { display: true });
    this.msg.hidden = true;
    this.paper.hidden = false;
    this._t0 = t;
    this._rendering = this.model;
    this.api.renderScore(score, [0]);
    this.app.onScoreModel?.(this.model);
  }

  _message(text) {
    this.boxes = [];
    this.shown = null;
    this.msg.textContent = text;
    this.msg.hidden = false;
    this.paper.hidden = true;
    this.app.onScoreModel?.(null);
  }

  // ------------------------------------------------------------------------------------------
  // model -> alphaTab score
  _toAlphaTab(model, { display }) {
    const m = AT.model;
    const app = this.app;
    const o = this.opts;
    const tm = model.timing;
    const score = new m.Score();
    score.title = app.project.title || '';
    score.tab = 'Bass Stem Studio';
    const track = new m.Track();
    track.name = 'Bass';
    track.shortName = 'el.bs.';
    track.playbackInfo.program = 33; // Electric Bass (finger)
    track.playbackInfo.primaryChannel = 0;
    track.playbackInfo.secondaryChannel = 1;
    const staff = new m.Staff();
    staff.showTablature = !display || o.staves !== 'score';
    staff.showStandardNotation = !display || o.staves !== 'tab';
    const tuning = [...app.tuning.strings].reverse(); // alphaTab: highest string first
    const known = m.Tuning.findTuning(tuning);
    staff.stringTuning = new m.Tuning(known ? known.name : app.tuning.name || '', tuning, !!known?.isStandard);
    staff.displayTranspositionPitch = -12; // bass is written an octave above sounding pitch
    track.addStaff(staff);
    score.addTrack(track);
    const nStr = tuning.length;
    const thr = app.project.confidence_threshold ?? 0.5;
    const lowColor = m.Color.fromJson(LOW_COLOR);
    const manualColor = m.Color.fromJson(MANUAL_COLOR);
    const style = (note, color) => {
      note.style = new m.NoteStyle();
      for (const sub of [m.NoteSubElement.StandardNotationNoteHead, m.NoteSubElement.StandardNotationAccidentals, m.NoteSubElement.GuitarTabFretNumber]) {
        note.style.colors.set(sub, color);
      }
    };
    this.atBeats = [];
    this.atNotes = new Map();
    const lastOnString = new Map();
    model.bars.forEach((b, i) => {
      const mb = new m.MasterBar();
      mb.timeSignatureNumerator = tm.bpb;
      mb.timeSignatureDenominator = tm.unit;
      if (i === 0) mb.tempoAutomations.push(m.Automation.buildTempoAutomation(false, 0, Math.round(tm.bpm * 4 / tm.unit * 100) / 100, 2));
      score.addMasterBar(mb);
      const bar = new m.Bar();
      bar.clef = m.Clef.F4;
      bar.keySignature = model.key.fifths;
      bar.keySignatureType = model.key.minor ? m.KeySignatureType.Minor : m.KeySignatureType.Major;
      staff.addBar(bar);
      const voice = new m.Voice();
      bar.addVoice(voice);
      for (const bi of b.beats) {
        const mbeat = model.beats[bi];
        const beat = new m.Beat();
        beat.duration = mbeat.value.base;
        beat.dots = mbeat.value.dots;
        if (mbeat.value.tuplet) {
          beat.tupletNumerator = 3;
          beat.tupletDenominator = 2;
        }
        for (const n of mbeat.notes) {
          const note = new m.Note();
          note.string = nStr + 1 - n.string;
          note.fret = n.fret;
          if (mbeat.tieIn) note.isTieDestination = true;
          else this._technique(m, note, n, lastOnString);
          if (display && n.confidence < thr) style(note, lowColor);
          else if (display && n.source === 'manual') style(note, manualColor);
          beat.addNote(note);
          if (!this.atNotes.has(n.id)) this.atNotes.set(n.id, []);
          this.atNotes.get(n.id).push(note);
          lastOnString.set(n.string, note);
        }
        voice.addBeat(beat);
        this.atBeats[bi] = beat;
      }
    });
    score.finish(display ? this.api.settings : new AT.Settings());
    return score;
  }

  // Our technique marks how a note is reached (h / p / slide from the previous note) or played.
  _technique(m, note, n, lastOnString) {
    const prev = lastOnString.get(n.string);
    switch (n.technique) {
      case 'hammer':
      case 'pull':
        if (prev) prev.isHammerPullOrigin = true;
        break;
      case 'slide':
        if (prev) prev.slideOutType = m.SlideOutType.Legato;
        else note.slideInType = m.SlideInType.IntoFromBelow;
        break;
      case 'ghost':
        note.isGhost = true;
        break;
      case 'mute':
        note.isDead = true;
        break;
      case 'vibrato':
        note.vibrato = m.VibratoType.Slight;
        break;
      case 'bend':
        note.addBendPoint(new m.BendPoint(0, 0));
        note.addBendPoint(new m.BendPoint(30, 2));
        note.addBendPoint(new m.BendPoint(60, 2));
        break;
      default:
    }
  }

  // ------------------------------------------------------------------------------------------
  // geometry after each render (also after alphaTab re-layouts on resize)
  _afterRender() {
    const bl = this.api?.renderer?.boundsLookup;
    if (!bl || !this._rendering) return;
    this.shown = this._rendering;
    this.boxes = this.atBeats.map((beat) => {
      const list = bl.findBeats(beat);
      if (!list || !list.length) return null;
      const first = list[0];
      const last = list[list.length - 1];
      const fb = first.barBounds.visualBounds;
      const lb = last.barBounds.visualBounds;
      const mbb = first.barBounds.masterBarBounds;
      return {
        x: first.realBounds.x, w: first.realBounds.w, top: fb.y, bottom: lb.y + lb.h,
        sys: mbb.staffSystemBounds ? mbb.staffSystemBounds.index : 0,
        barX: mbb.visualBounds.x, barW: mbb.visualBounds.w,
        sysTop: mbb.staffSystemBounds ? mbb.staffSystemBounds.visualBounds.y : fb.y,
        tab: this.opts.staves === 'score' ? null : { y: lb.y, h: lb.h },
      };
    });
    this._lastBeat = -2;
    this._selKey = '';
    this.renderSelection(true);
    const ms = this._t0 ? Math.round(performance.now() - this._t0) : null;
    this._t0 = null;
    this.app.setScoreInfo?.(`${this.shown.bars.length} 小節${ms != null ? ` / 描画 ${ms} ms` : ''}`);
  }

  _box(i) { return i >= 0 && i < this.boxes.length ? this.boxes[i] : null; }

  // ------------------------------------------------------------------------------------------
  // per frame: playback cursor + auto scroll
  render(pos) {
    const m = this.shown;
    if (!this.visible || !m || !this.boxes.length) return;
    const i = beatAtTime(m, pos);
    const box = this._box(i);
    if (!box) {
      this.cursorEl.hidden = this.lineEl.hidden = this.barHl.hidden = true;
      this._lastBeat = i;
      return;
    }
    const b = m.beats[i];
    const frac = Math.min(1, Math.max(0, (pos - b.t0) / Math.max(1e-6, b.t1 - b.t0)));
    const next = this._box(i + 1);
    const x1 = next && next.sys === box.sys ? next.x : box.barX + box.barW;
    const x = box.x + (x1 - box.x) * frac;
    if (i !== this._lastBeat) {
      this._lastBeat = i;
      Object.assign(this.cursorEl.style, { left: `${box.x - 3}px`, top: `${box.top - 10}px`, width: `${Math.max(14, x1 - box.x)}px`, height: `${box.bottom - box.top + 20}px` });
      Object.assign(this.barHl.style, { left: `${box.barX}px`, top: `${box.top - 10}px`, width: `${box.barW}px`, height: `${box.bottom - box.top + 20}px` });
      this.cursorEl.hidden = this.barHl.hidden = false;
      if (this.app.follow) this._scrollInto(box); // only when the current system is out of view
    }
    Object.assign(this.lineEl.style, { left: `${x}px`, top: `${box.top - 12}px`, height: `${box.bottom - box.top + 24}px` });
    this.lineEl.hidden = false;
    this.renderSelection();
  }

  _scrollInto(box, force = false) {
    const sc = this.scroll;
    const top = box.sysTop - 12 + this.paper.offsetTop;
    const bottom = box.bottom + 30 + this.paper.offsetTop;
    if (force || top < sc.scrollTop || bottom > sc.scrollTop + sc.clientHeight) {
      sc.scrollTop = Math.max(0, top - 8);
    }
  }

  // selection highlight + edit caret (only redrawn when they change)
  renderSelection(force = false) {
    const app = this.app;
    const key = `${[...app.selection].join(',')}|${this.caret ? `${this.caret.tick}:${this.caret.string}` : ''}|${this.boxes.length}`;
    if (!force && key === this._selKey) return;
    this._selKey = key;
    this.selLayer.replaceChildren();
    const bl = this.api?.renderer?.boundsLookup;
    if (!bl || !this.shown) return;
    for (const id of app.selection) {
      for (const note of this.atNotes.get(id) || []) {
        for (const bb of bl.findBeats(note.beat) || []) {
          for (const nb of bb.notes || []) {
            if (nb.note !== note) continue;
            const r = nb.noteHeadBounds;
            this.selLayer.append(el('div', { class: 'score-selbox', style: `left:${r.x - 3}px;top:${r.y - 2}px;width:${r.w + 6}px;height:${r.h + 4}px` }));
          }
        }
      }
    }
    const c = this.caret ? this._caretBeat(this.shown) : -1;
    const box = this._box(c);
    if (box && box.tab) {
      const n = this.app.tuning.strings.length;
      const gap = box.tab.h / Math.max(1, n - 1);
      const y = box.tab.y + (this.caret.string - 1) * gap;
      Object.assign(this.caretEl.style, { left: `${box.x - 2}px`, top: `${y - gap / 2}px`, width: `${Math.max(14, box.w * 0.6)}px`, height: `${gap}px` });
      this.caretEl.hidden = false;
    } else this.caretEl.hidden = true;
  }

  _caretBeat(model = this.model) {
    if (!this.caret || !model) return -1;
    const bs = model.beats;
    for (let i = 0; i < bs.length; i++) {
      if (bs[i].tick <= this.caret.tick && this.caret.tick < bs[i].tick + bs[i].ticks) return i;
    }
    return -1;
  }

  // ------------------------------------------------------------------------------------------
  // mouse
  _attach() {
    this.paper.addEventListener('pointerdown', (e) => {
      const m = this.shown;
      if (e.button !== 0 || !m) return;
      const r = this.surface.getBoundingClientRect();
      const hit = this._hit(e.clientX - r.left, e.clientY - r.top);
      if (!hit) return;
      this.scroll.focus({ preventScroll: true });
      const b = m.beats[hit.beat];
      const note = !b.rest ? (hit.string != null ? b.notes.find((n) => n.string === hit.string) : null) || (hit.string == null ? b.notes[0] : null) : null;
      if (note) {
        this.caret = { tick: m.noteIndex.get(note.id)?.start ?? b.tick, string: note.string };
        if (e.shiftKey || e.ctrlKey) this.app.toggleSelect(note.id);
        else this.app.select([note.id]);
        this.app.audition(note);
      } else {
        this.caret = { tick: b.tick, string: hit.string ?? this.caret?.string ?? 1 };
        this.app.select([]);
        this.app.seek(b.t0);
      }
      this.pending = null;
      this.renderSelection(true);
    });
  }

  _hit(x, y) {
    let best = null;
    for (let i = 0; i < this.boxes.length; i++) {
      const b = this.boxes[i];
      if (!b || y < b.top - 24 || y > b.bottom + 24) continue;
      if (x < b.barX || x > b.barX + b.barW) continue;
      if (b.x - 4 <= x) best = i;
    }
    if (best == null) return null;
    const b = this.boxes[best];
    let string = null;
    if (b.tab) {
      const n = this.app.tuning.strings.length;
      const gap = b.tab.h / Math.max(1, n - 1);
      if (y >= b.tab.y - gap * 0.6 && y <= b.tab.y + b.tab.h + gap * 0.6) {
        string = Math.min(n, Math.max(1, Math.round((y - b.tab.y) / gap) + 1));
      }
    }
    return { beat: best, string };
  }

  // ------------------------------------------------------------------------------------------
  // keyboard (only while the score view is shown). Returns true when handled.
  onKey(e) {
    if (!this.ensureModel() || !this.boxes.length || this.app.notes.locked) return false;
    const k = e.key;
    const code = e.code || '';
    const digit = /^(Digit|Numpad)([0-9])$/.exec(code);
    if (digit && !e.ctrlKey && !e.altKey && !e.metaKey) {
      this.typeFret(Number(digit[2]));
      return true;
    }
    if (code === 'NumpadAdd') { this.stepValue(+1); return true; }
    if (code === 'NumpadSubtract') { this.stepValue(-1); return true; }
    if ((k === '.' || code === 'NumpadDecimal') && !e.ctrlKey) { this.toggleDot(); return true; }
    if ((k === '/' || code === 'NumpadDivide') && !e.ctrlKey) { this.toggleTriplet(); return true; }
    if ((k === 'ArrowLeft' || k === 'ArrowRight') && !e.ctrlKey && !e.altKey) {
      if (e.shiftKey) this.nudge(k === 'ArrowRight' ? 1 : -1);
      else this.moveCaret(k === 'ArrowRight' ? 1 : -1);
      return true;
    }
    if ((k === 'ArrowUp' || k === 'ArrowDown') && !e.ctrlKey && !e.altKey && !e.shiftKey) {
      this.moveString(k === 'ArrowUp' ? -1 : 1);
      return true;
    }
    if ((k === 'ArrowUp' || k === 'ArrowDown') && e.shiftKey && !e.ctrlKey && !e.altKey) {
      this.app.pitchSelected(k === 'ArrowUp' ? 1 : -1);
      return true;
    }
    return false;
  }

  _syncCaretFromSelection() {
    if (!this.model || this.app.selection.size !== 1) return;
    const id = [...this.app.selection][0];
    const info = this.model.noteIndex.get(id);
    const n = this.app.notes.get(id);
    if (info && n) this.caret = { tick: info.start, string: n.string ?? this.caret?.string ?? 1 };
  }

  moveCaret(dir) {
    this._syncCaretFromSelection();
    const bs = this.model.beats;
    let i = this._caretBeat();
    if (i < 0) i = Math.max(0, beatAtTime(this.model, this.app.engine.position()));
    else i = Math.min(bs.length - 1, Math.max(0, i + dir));
    // skip tied continuations: the caret sits on the start of a note
    while (bs[i] && bs[i].tieIn && i > 0 && i < bs.length - 1) i += dir;
    const b = bs[i];
    if (!b) return;
    const string = this.caret?.string ?? 1;
    this.caret = { tick: b.tick, string };
    const note = b.notes.find((n) => n.string === string) || (b.notes.length === 1 ? b.notes[0] : null);
    if (note) {
      this.caret.string = note.string;
      this.app.select([note.id]);
    } else this.app.select([]);
    if (!this.app.engine.playing) this.app.seek(b.t0);
    this._ensureVisible(i);
    this.pending = null;
    this.renderSelection(true);
  }

  moveString(dir) {
    this._syncCaretFromSelection();
    if (!this.caret) return;
    const n = this.app.tuning.strings.length;
    this.caret.string = Math.min(n, Math.max(1, this.caret.string + dir));
    const i = this._caretBeat();
    const b = this.model.beats[i];
    const note = b && b.notes.find((x) => x.string === this.caret.string);
    this.app.select(note ? [note.id] : []);
    this.pending = null;
    this.renderSelection(true);
  }

  _ensureVisible(i) {
    const box = this._box(i);
    if (box) this._scrollInto(box);
  }

  // GP-style fret entry: typing a number puts that fret on the caret string. Two digits typed
  // quickly (e.g. 1 then 2) make one two-digit fret.
  typeFret(d) {
    this._syncCaretFromSelection();
    if (!this.caret) {
      this.app.status('譜面をクリックして入力位置（拍と弦）を選んでください。');
      return;
    }
    const tun = this.app.tuning;
    const now = performance.now();
    let fret = d;
    const p = this.pending;
    if (p && p.tick === this.caret.tick && p.string === this.caret.string && now - p.t < 900 && p.fret * 10 + d <= tun.frets) fret = p.fret * 10 + d;
    this.pending = { tick: this.caret.tick, string: this.caret.string, fret, t: now };
    this.setFret(this.caret, fret);
  }

  setFret(caret, fret) {
    const app = this.app;
    const tun = app.tuning;
    const open = openPitch(caret.string, tun);
    if (open === undefined) return;
    if (fret > tun.frets) {
      app.status(`${tun.frets} フレットまでです。`, true);
      return;
    }
    const pitch = open + fret;
    const i = this._caretBeat();
    const b = this.model.beats[i];
    if (!b) return;
    const same = b.notes.find((n) => n.string === caret.string);
    if (same) {
      app.notes.setFields(same.id, {
        midi_pitch: pitch, string: caret.string, fret, edited: true, fingering_edited: true,
        flags: (same.flags || []).filter((f) => f !== 'out_of_range'),
      }, 'フレット入力');
      app.select([same.id]);
      return;
    }
    const tm = this.model.timing;
    let start;
    let end;
    const flags = ['dur_fixed'];
    if (b.rest) {
      start = tm.time(b.tick);
      end = tm.time(b.tick + b.ticks);
      if (b.value.tuplet) flags.push('triplet');
    } else {
      const head = b.notes[0];
      start = head.start_sec;
      end = head.end_sec;
    }
    const note = app.notes.addNote(start, end, pitch, { string: caret.string, fret });
    app.notes.setFields(note.id, { fingering_edited: true, flags }, 'フレット入力');
    app.select([note.id]);
  }

  // ---- note values ---------------------------------------------------------------------------
  _selectedEvents() {
    this.ensureModel();
    const out = [];
    const seen = new Set();
    for (const id of this.app.selection) {
      const info = this.model?.noteIndex.get(id);
      if (!info) continue;
      const b = this.model.beats[info.beat];
      if (!b || !b.event || seen.has(b.event)) continue;
      seen.add(b.event);
      out.push({ ev: b.event, info });
    }
    return out;
  }

  currentValue() {
    const evs = this._selectedEvents();
    if (!evs.length) return null;
    const { info } = evs[0];
    return valueOfTicks(info.end - info.start, info.triplet) || valueOfTicks(info.end - info.start, false);
  }

  // change: (value|null) => new value
  applyValue(change, label = '音価変更') {
    if (!this.ensureModel()) {
      this.app.status('音価の編集にはテンポ（BPM）の設定が必要です。');
      return;
    }
    const evs = this._selectedEvents();
    if (!evs.length) {
      this.app.status('音価を変える音符を選んでください（休符の長さは前後の音符で決まります）。');
      return;
    }
    const tm = this.model.timing;
    const starts = this.model.beats.filter((b) => b.event && !b.tieIn).map((b) => b.event.start);
    let clipped = 0;
    this.app.notes.transact(label, (tx) => {
      for (const { ev, info } of evs) {
        const cur = valueOfTicks(info.end - info.start, info.triplet) || valueOfTicks(info.end - info.start, false) ||
          { base: 8, dots: 0, tuplet: info.triplet };
        const v = change(cur);
        if (!v || (v.tuplet && v.base > 16)) continue; // triplets down to 16th triplets
        let endTick = info.start + valueTicks(v);
        const next = starts.find((s) => s > info.start);
        if (next != null && endTick > next) {
          endTick = next;
          clipped++;
        }
        for (const n of ev.notes) {
          let flags = (n.flags || []).filter((f) => f !== 'dur_fixed' && f !== 'triplet' && f !== 'straight');
          flags.push('dur_fixed');
          if (v.tuplet) flags.push('triplet');
          else if (cur.tuplet || info.triplet) flags.push('straight');
          tx.update(n.id, { start_sec: +tm.time(info.start).toFixed(4), end_sec: +tm.time(endTick).toFixed(4), edited: true, flags });
        }
      }
    });
    if (clipped) this.app.status('次の音符と重なるため、次の音符の手前までにしました。');
  }

  setBase(base) { this.applyValue((cur) => ({ base, dots: cur.dots, tuplet: cur.tuplet })); }
  stepValue(dir) {
    // +1 = shorter (8th -> 16th), -1 = longer, as on the Guitar Pro numeric keypad
    this.applyValue((cur) => {
      const base = dir > 0 ? cur.base * 2 : cur.base / 2;
      return base >= 1 && base <= 32 ? { base, dots: cur.dots, tuplet: cur.tuplet } : null;
    });
  }
  toggleDot() { this.applyValue((cur) => ({ base: cur.base, dots: cur.dots ? 0 : 1, tuplet: cur.tuplet }), '付点'); }
  toggleTriplet() { this.applyValue((cur) => ({ base: cur.base, dots: 0, tuplet: !cur.tuplet }), '3連符'); }

  nudge(dir) {
    const evs = this._selectedEvents();
    if (!evs.length) return;
    const tm = this.model.timing;
    this.app.notes.transact('位置を移動', (tx) => {
      for (const { ev, info } of evs) {
        const step = stepAtTick(this.model, info.start) * dir;
        for (const n of ev.notes) {
          const flags = [...new Set([...(n.flags || []), 'dur_fixed'])];
          tx.update(n.id, { start_sec: +tm.time(info.start + step).toFixed(4), end_sec: +tm.time(info.end + step).toFixed(4), edited: true, flags });
        }
      }
    });
    if (this.caret) this.caret.tick += stepAtTick(this.model, this.caret.tick) * dir;
  }

  describe(id) {
    const info = this.ensureModel()?.noteIndex.get(id);
    if (!info) return null;
    const b = this.model.beats[info.beat];
    const bar = this.model.bars[b.bar];
    const tm = this.model.timing;
    const beatInBar = (info.start - bar.tick) / tm.beatTicks + 1;
    const v = valueOfTicks(info.end - info.start, info.triplet) || valueOfTicks(info.end - info.start, false);
    // a note longer than one value is written as tied values (e.g. dotted quarter + 16th)
    const parts = [];
    for (let i = info.beat; i < this.model.beats.length; i++) {
      const x = this.model.beats[i];
      if (i > info.beat && !x.tieIn) break;
      if (x.event !== b.event) break;
      parts.push(valueName(x.value));
    }
    const beatText = Number.isInteger(beatInBar) ? `${beatInBar}拍目` : `${beatInBar.toFixed(2).replace(/0+$/, '')}拍目`;
    return { bar: bar.number, beat: beatText, value: parts.length > 1 ? `${parts.join('＋')}（タイ）` : v ? valueName(v) : parts[0] || '', v };
  }

  // ------------------------------------------------------------------------------------------
  async exportGp() {
    const app = this.app;
    if (!tempoOk(app.project.tempo)) throw new Error('テンポ（BPM）が未設定です。譜面の「テンポを自動推定」または設定で入力してください。');
    await loadAlphaTab();
    const model = this.ensureModel();
    const keep = [this.atBeats, this.atNotes];
    const score = this._toAlphaTab(model, { display: false });
    [this.atBeats, this.atNotes] = keep;
    const bytes = new AT.exporter.Gp7Exporter().export(score, new AT.Settings());
    return { bytes, bars: model.bars.length, notes: model.noteIndex.size, stats: model.stats };
  }
}

export const DEFAULT_OPTS = { grid: '1/16', triplets: 'auto', rest_min: '1/8', key: 'auto', staves: 'both', scale: 1 };

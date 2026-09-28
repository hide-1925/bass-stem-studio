// Bass Stem Studio – application controller.

import { api } from './api.js';
import { AudioEngine } from './audio/engine.js';
import { isActive, normalizeFx } from './audio/fx.js';
import { TabCapture } from './capture.js';
import { ErrorCapture, clientInfo } from './diag.js';
import { NoteStore } from './notes/store.js';
import { debounce, dbToLin, el, fmtTime, linToDb, noteName, parseNoteName, quantizeTime, tempoValid } from './util.js';
import { DiagPanel } from './views/diagpanel.js';
import { FxPanel } from './views/fxpanel.js';
import { Inspector } from './views/inspector.js';
import { StemLaneView, attachSeekAndLoopDrag, buildStemHeader } from './views/lanes.js';
import { PianoRollView } from './views/pianoroll.js';
import { TabView } from './views/tab.js';
import { OverviewView, RulerView, TimeView } from './views/timeline.js';

const $ = (id) => document.getElementById(id);
const SPEEDS = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5];

class App {
  constructor() {
    this.engine = new AudioEngine();
    this.view = new TimeView();
    this.notes = new NoteStore();
    this.selection = new Set();
    this.project = null;
    this.stems = [];
    this.peaks = null;
    this.loop = { enabled: false, a: 0, b: 0 };
    this.rate = 1;
    this.follow = true;
    this.clickToPlay = true;
    this.renderVersion = 0;
    this.tuning = { strings: [28, 33, 38, 43], frets: 24 };
    this.gainSteps = [-30, -20, -10, 0, 10];
    this.presets = [];
    this.laneViews = [];
    this.stemHeads = [];
    this.job = null;
    this.stagesSeen = new Set();
    this.persist = debounce(() => this._persistSettings(), 700);
    this.updateHeadroom = debounce(() => this._updateHeadroom(), 120);
    this.persistFx = debounce(() => this.project && api.patchProject(this.project.id, { fx: this.project.fx }).catch(() => {}), 600);
  }

  // =======================================================================================
  async start() {
    this.ruler = new RulerView($('ruler'), this);
    this.overview = new OverviewView($('overview'), this);
    this.roll = new PianoRollView($('roll'), $('roll-keys'), this);
    this.tab = new TabView($('tab'), $('tab-labels'), this);
    this.inspector = new Inspector($('inspector'), this);
    this.fxPanel = new FxPanel($('fx'), this);
    this.diag = new DiagPanel(this, capture);
    this.overview.attach();
    this.roll.attach();
    this.tab.attach();
    attachSeekAndLoopDrag($('ruler'), this);
    this._bindUi();
    this._bindKeys();
    this.notes.addEventListener('change', () => this._onNotesChanged());
    this.engine.addEventListener('limiter', (e) => this._onLimiter(e.detail));
    this.engine.addEventListener('state', () => this._updateTransport());
    new ResizeObserver(() => this.view.setWidth($('ruler').clientWidth)).observe($('ruler'));
    requestAnimationFrame(() => this._frame());
    try {
      const [sys, presets] = await Promise.all([api.system(), api.presets()]);
      this.system = sys;
      this.gainSteps = sys.gain_steps_db;
      this.presets = presets.presets;
    } catch (e) {
      this.status(`サーバーに接続できません: ${e.message}`, true);
    }
    this.inspector.render();
    const last = localStorage.getItem('bss:lastProject');
    if (last) this.openProject(last).catch(() => localStorage.removeItem('bss:lastProject'));
  }

  status(msg, error = false) {
    if (error) capture.push('error', msg, { kind: 'ui' });
    const s = $('status-msg');
    s.textContent = msg;
    s.classList.toggle('error', !!error);
    if (!error) {
      clearTimeout(this._statusTimer);
      this._statusTimer = setTimeout(() => { if (s.textContent === msg) s.textContent = ''; }, 6000);
    }
  }

  loading(text) {
    $('loading').hidden = !text;
    if (text) $('loading-text').textContent = text;
  }

  // =======================================================================================
  // project
  async openProject(id) {
    if (this.notes.dirty && this.project && this.project.id !== id) {
      const ok = await this.confirm('未保存の編集', '保存していない音符の編集があります。保存してから開きますか？',
        [['save', '保存して開く', 'primary'], ['discard', '保存せずに開く'], ['cancel', 'キャンセル']]);
      if (ok === 'cancel') return;
      if (ok === 'save') await this.save();
    }
    this.loading('プロジェクトを開いています…');
    try {
      const data = await api.getProject(id);
      this.engine.stop();
      // stop following the previous project's job
      clearTimeout(this._pollT);
      this.job = null;
      this.pendingType = null;
      this.notes.locked = false;
      this.project = data.project;
      this.stems = data.stems;
      this.selection.clear();
      localStorage.setItem('bss:lastProject', id);
      this._applyProjectSettings();
      this._buildStemRows();
      this.view.duration = data.project.source?.duration_sec || 0;
      this.view.t0 = 0;
      await this._loadPeaks();
      await this._loadNotes();
      $('empty-state').hidden = true;
      if (this.stems.length) await this._loadStems();
      else this.engine.duration = this.view.duration;
      this._applyAllFx();
      this.view.fitAll();
      if (this.view.duration > 60) {
        this.view.pps = Math.max(this.view.pps, this.view.width / 30);
        this.view.clampT0();
      }
      this.roll.fitRange();
      this._updateAll();
      const job = data.active_job || (data.last_job && data.last_job.status === 'error' ? data.last_job : null);
      if (job) this._watchJob(job);
      else if (!data.project.separation) this._showUnprocessed('pipeline');
      else if (!data.project.transcription) this._showUnprocessed('transcribe');
      else this._showJob(null);
    } finally {
      this.loading(null);
    }
  }

  _applyProjectSettings() {
    const p = this.project;
    $('project-title').textContent = p.title || '(無題)';
    document.title = `${p.title} – Bass Stem Studio`;
    this.tuning = { strings: p.tuning.strings, frets: p.tuning.frets, name: p.tuning.name };
    this.loop = { ...p.playback.loop };
    this.rate = p.playback.rate || 1;
    $('speed').value = String(this.rate);
    this.engine.setRate(this.rate);
    this.engine.setLoop(this.loop.enabled, this.loop.a, this.loop.b);
    this._updateTuningLabel();
  }

  async _loadPeaks() {
    try {
      this.peaks = await api.peaks(this.project.id);
    } catch (_) {
      this.peaks = null;
    }
    this.renderVersion++;
  }

  async _loadNotes() {
    try {
      const data = await api.getNotes(this.project.id);
      this.notes.load(data);
    } catch (_) {
      this.notes.reset();
    }
    this.notes.locked = !!(this.job && ['transcribe', 'pipeline'].includes(this.job.type) && ['queued', 'running'].includes(this.job.status));
    this._updateDirty();
    this.inspector.render();
  }

  async _loadStems() {
    await this.engine.load(this.stems, (i, n, msg) => this.loading(`ステムを読み込み中… ${i}/${n}　${msg}`));
    this.view.duration = this.engine.duration;
    this.engine.setRate(this.rate);
    this.engine.setLoop(this.loop.enabled, this.loop.a, this.loop.b);
    const pos = this.project.playback.position || 0;
    this.engine.seek(this.loop.enabled ? this.loop.a : pos);
    this._applyMixer(false);
  }

  _buildStemRows() {
    const box = $('stem-rows');
    box.replaceChildren();
    this.laneViews = [];
    this.stemHeads = [];
    const stems = this.stems.length ? this.stems : [];
    for (const s of stems) {
      const head = buildStemHeader(s, this);
      const canvas = el('canvas');
      box.append(el('div', { class: 'row stem-row' }, head, canvas));
      const lv = new StemLaneView(canvas, this, s);
      lv.attach();
      this.laneViews.push(lv);
      this.stemHeads.push(head);
    }
    this.stemHeads.forEach((h) => h.update());
  }

  // =======================================================================================
  // mixer
  effectiveGain(name) {
    const mx = this.project?.mixer;
    if (!mx) return 1;
    const anySolo = Object.values(mx.solo || {}).some(Boolean);
    if (mx.mute?.[name]) return 0;
    if (anySolo && !mx.solo?.[name]) return 0;
    const db = name === (this.system?.fixed_gain_stem || 'bass') ? 0 : mx.gains_db?.[name] ?? 0;
    return dbToLin(db);
  }

  toggleMute(name) {
    const mx = this.project.mixer;
    mx.mute[name] = !mx.mute[name];
    this._applyMixer();
  }

  toggleSolo(name) {
    const mx = this.project.mixer;
    mx.solo[name] = !mx.solo[name];
    this._applyMixer();
  }

  setStemGainDb(name, db) {
    if (name === 'bass') return; // fixed at 0 dB
    this.project.mixer.gains_db[name] = db;
    this._applyMixer();
  }

  // =======================================================================================
  // FX (EQ / filters / compressor) and listening volume
  _applyAllFx() {
    if (!this.engine.ctx || !this.project) return;
    this.project.fx = this.project.fx || {};
    for (const t of ['master', ...this.stems.map((s) => s.name)]) {
      const chain = this.engine.fxChain(t);
      if (chain) chain.apply(normalizeFx(this.project.fx[t]), true);
    }
    this._updateFxIndicators();
    if (this.fxPanel.open) this.fxPanel.sync();
  }

  onFxChanged() {
    this._updateFxIndicators();
    this.persistFx();
  }

  _updateFxIndicators() {
    const fx = this.project?.fx || {};
    const any = Object.values(fx).some((f) => isActive(normalizeFx(f)));
    $('fx-lamp').classList.toggle('on', any);
    this.stemHeads.forEach((h) => h.update());
  }

  toggleFx(open = !this.fxPanel.open) {
    if (!this.project) return;
    if (open) this.fxPanel.show();
    else this.fxPanel.hide();
    $('btn-fx').classList.toggle('on', open);
  }

  openFx(target) {
    this.fxPanel.show(target);
    $('btn-fx').classList.toggle('on', true);
  }

  // Listening volume: slider 0..100 -> -60..0 dB (0 = silent), applied after the limiter.
  setVolume(pos, save = true) {
    const db = (pos - 100) * 0.6;
    const silent = this.muted || pos <= 0;
    this.engine.setVolume(silent ? 0 : Math.pow(10, db / 20));
    $('volume').value = String(pos);
    $('vol-text').textContent = this.muted ? 'ミュート' : pos <= 0 ? '−∞ dB' : `${db === 0 ? '0' : db.toFixed(0)} dB`;
    $('btn-mute').textContent = silent ? '🔇' : '🔊';
    if (save) {
      try { localStorage.setItem('bss:volume', String(pos)); } catch (_) { /* storage unavailable */ }
    }
  }

  _applyMixer(persist = true) {
    for (const s of this.stems) this.engine.setStemGain(s.name, this.effectiveGain(s.name));
    this.stemHeads.forEach((h) => h.update());
    this.renderVersion++;
    this.updateHeadroom();
    if (persist) this.persist();
  }

  async _updateHeadroom() {
    if (!this.project || !this.stems.length) return;
    const gains = {};
    for (const s of this.stems) gains[s.name] = this.effectiveGain(s.name);
    try {
      const r = await api.mixPeak(this.project.id, gains);
      this.headroom = r;
      this.engine.setHeadroom(r.headroom_gain);
      this._renderPeakIndicator();
    } catch (e) {
      console.warn(e);
    }
  }

  _onLimiter(d) {
    this.limiterGr = d.grDb;
    this._renderPeakIndicator();
  }

  _renderPeakIndicator() {
    const hr = this.headroom ? linToDb(this.headroom.headroom_gain) : 0;
    const gr = this.limiterGr || 0;
    const lamp = $('limiter-lamp');
    lamp.classList.toggle('on', hr < -0.05 || gr > 0.05);
    lamp.classList.toggle('hot', gr > 0.5);
    $('peak-indicator').classList.toggle('active', hr < -0.05 || gr > 0.05);
    const parts = [];
    if (this.headroom) parts.push(hr < -0.05 ? `自動ヘッドルーム ${hr.toFixed(1)} dB` : `ヘッドルーム OK（加算ピーク ${this.headroom.peak_db.toFixed(1)} dBFS）`);
    if (gr > 0.05) parts.push(`リミッター −${gr.toFixed(1)} dB`);
    $('peak-text').textContent = parts.join(' / ') || 'ピーク制御: 待機';
    $('peak-indicator').title = 'ステムを加算した実ピークを全サンプルで計算し、−1 dBFS を超える分だけ全体を下げます（バランスは維持）。' +
      '\n伸縮処理による瞬間的な超過はルックアヘッド・リミッター（−1 dBFS）で抑えます。';
  }

  // =======================================================================================
  // transport
  async togglePlay() {
    if (!this.engine.loaded) return;
    if (this.engine.playing) this.engine.pause();
    else await this.engine.play();
    this._updateTransport();
  }

  seek(t) {
    this.engine.seek(t);
    this.persist();
  }

  async audition(note) {
    if (!note) return;
    this.engine.seek(Math.max(0, note.start_sec - 0.02));
    if (this.clickToPlay && !this.engine.playing && this.engine.loaded) await this.engine.play();
  }

  setRate(r) {
    this.rate = r;
    this.engine.setRate(r);
    $('speed').value = String(r);
    this.persist();
  }

  setLoop(enabled, a, b, commit = true) {
    this.loop = { enabled, a: Math.min(a, b), b: Math.max(a, b) };
    if (commit) {
      this.engine.setLoop(this.loop.enabled, this.loop.a, this.loop.b);
      this.persist();
    }
    this._updateTransport();
  }

  setLoopPoint(which) {
    const p = this.engine.position();
    let { a, b } = this.loop;
    if (which === 'a') a = p;
    else b = p;
    if (b <= a) {
      if (which === 'a') b = Math.min(this.view.duration, a + 4);
      else a = Math.max(0, b - 4);
    }
    this.setLoop(true, a, b, true);
  }

  _updateTransport() {
    const has = this.engine.loaded;
    for (const id of ['btn-play', 'btn-stop', 'speed', 'btn-a', 'btn-b', 'btn-loop']) $(id).disabled = !has;
    $('btn-play').textContent = this.engine.playing ? '❚❚' : '▶';
    $('btn-loop').classList.toggle('on', this.loop.enabled);
    $('loop-range').textContent = this.loop.b > this.loop.a ? `${fmtTime(this.loop.a)} – ${fmtTime(this.loop.b)}` : '–';
    $('time-dur').textContent = fmtTime(this.view.duration);
  }

  // =======================================================================================
  // selection & editing
  select(ids, render = true) {
    this.selection = new Set(ids);
    this.renderVersion++;
    if (render) this.inspector.render();
    else this._inspectorLater();
  }

  _inspectorLater() {
    clearTimeout(this._inspT);
    this._inspT = setTimeout(() => this.inspector.render(), 60);
  }

  toggleSelect(id) {
    if (this.selection.has(id)) this.selection.delete(id);
    else this.selection.add(id);
    this.renderVersion++;
    this.inspector.render();
  }

  displayTimes(n) {
    const q = this.project?.quantize;
    const tempo = this.project?.tempo;
    if (q && q.display && tempoValid(tempo)) {
      const s = quantizeTime(n.start_sec, tempo, q.grid);
      let e = quantizeTime(n.end_sec, tempo, q.grid);
      const step = 60 / tempo.bpm / 16;
      if (e <= s) e = s + step;
      return { s, e };
    }
    return { s: n.start_sec, e: n.end_sec };
  }

  pitchSelected(delta) {
    if (!this.selection.size || this.notes.locked) return;
    this.notes.setPitch([...this.selection], (p) => p + delta, this.tuning, Math.abs(delta) === 12 ? 'オクターブ変更' : '音高変更');
  }

  stringSelected(dir) {
    if (!this.selection.size || this.notes.locked) return;
    this.notes.stepString([...this.selection], dir, this.tuning);
  }

  deleteSelected() {
    if (!this.selection.size || this.notes.locked) return;
    this.notes.remove([...this.selection]);
    this.select([]);
  }

  splitSelected() {
    const p = this.engine.position();
    for (const id of this.selection) this.notes.split(id, p);
  }

  selectAdjacent(dir) {
    const list = this.notes.sorted();
    if (!list.length) return;
    let idx;
    if (!this.selection.size) {
      const p = this.engine.position();
      idx = dir > 0 ? list.findIndex((n) => n.start_sec >= p) : list.findLastIndex((n) => n.start_sec < p);
    } else {
      const cur = [...this.selection].map((id) => list.findIndex((n) => n.id === id)).filter((i) => i >= 0);
      idx = dir > 0 ? Math.max(...cur) + 1 : Math.min(...cur) - 1;
    }
    idx = Math.max(0, Math.min(list.length - 1, idx < 0 && dir > 0 ? list.length - 1 : idx));
    const n = list[idx];
    this.select([n.id]);
    this.engine.seek(n.start_sec);
    const span = this.view.width / this.view.pps;
    if (n.start_sec < this.view.t0 || n.start_sec > this.view.t0 + span * 0.9) this.view.scrollTo(n.start_sec - span * 0.2);
  }

  _onNotesChanged() {
    for (const id of [...this.selection]) if (!this.notes.get(id)) this.selection.delete(id);
    this.renderVersion++;
    this._updateDirty();
    this._inspectorLater();
  }

  _updateDirty() {
    $('dirty').hidden = !this.notes.dirty;
    $('btn-save').disabled = !this.project;
  }

  async save() {
    if (!this.project) return;
    try {
      const r = await api.putNotes(this.project.id, this.notes.toSave());
      this.notes.markSaved(r.revision);
      await this._persistSettings();
      this._updateDirty();
      this.status(`保存しました（${new Date().toLocaleTimeString()}）`);
      return true;
    } catch (e) {
      if (e.status === 409) {
        const c = await this.confirm('保存できません', e.message, [['reload', 'サーバーの内容を読み込む（手元の未保存編集は破棄）'], ['cancel', 'キャンセル', 'primary']]);
        if (c === 'reload') await this._loadNotes();
      } else this.status(`保存に失敗: ${e.message}`, true);
      return false;
    }
  }

  async _persistSettings() {
    if (!this.project) return;
    this.project.playback = { rate: this.rate, loop: this.loop, position: this.engine.position() };
    try {
      await api.patchProject(this.project.id, { mixer: this.project.mixer, playback: this.project.playback });
    } catch (e) {
      console.warn('persist failed', e);
    }
  }

  // =======================================================================================
  // fingering / tuning
  _updateTuningLabel() {
    $('tuning-label').textContent = `${[...this.tuning.strings].map(noteName).join(' ')} / ${this.tuning.frets}F`;
  }

  async reoptimize(respectLocks = true, label = '運指を再最適化', clearLocks = false) {
    const r = await api.optimize(this.notes.sorted(), this.tuning, this.project.fingering, respectLocks);
    this.notes.applyAssignments(r, label, clearLocks);
    const msgs = [];
    if (r.unplayable.length) msgs.push(`音域外 ${r.unplayable.length} 音`);
    if (r.dropped_locks.length) msgs.push(`成立しない手修正 ${r.dropped_locks.length} 件を解除`);
    const done = clearLocks || label !== '運指を再最適化' ? '運指を再計算しました' : '運指を再最適化しました';
    this.status(`${done}${msgs.length ? '（' + msgs.join('、') + '）' : ''}`);
  }

  async changeTuning(tuning) {
    const locked = this.notes.sorted().filter((n) => n.fingering_edited).length;
    let mode = 'keep';
    if (locked) {
      mode = await this.confirm('チューニング変更', `弦・フレットを手修正した音符が ${locked} 件あります。どう扱いますか？`, [
        ['keep', '新しいチューニングで成立する手修正は残す', 'primary'],
        ['discard', '手修正を破棄してすべて再計算', 'danger'],
        ['cancel', 'キャンセル']]);
      if (mode === 'cancel') return false;
    }
    this.tuning = { strings: tuning.strings, frets: tuning.frets, name: tuning.name };
    this.project.tuning = { ...this.tuning };
    await api.patchProject(this.project.id, { tuning: this.project.tuning });
    this._updateTuningLabel();
    if (this.notes.size) await this.reoptimize(mode === 'keep', 'チューニング変更（運指再計算）', mode === 'discard');
    this.roll.fitRange();
    this.renderVersion++;
    return true;
  }

  // =======================================================================================
  // jobs
  async startJob(type) {
    if (!this.project) return;
    if (type !== 'separate' && this.notes.dirty) {
      const ok = await this.save();
      if (!ok) return;
    }
    try {
      const job = await api.startJob(this.project.id, type);
      this._watchJob(job);
    } catch (e) {
      this.status(e.message, true);
    }
  }

  _watchJob(job) {
    this.job = job;
    this.stagesSeen = new Set(job.stages_done || []);
    this._showJob(job);
    clearTimeout(this._pollT);
    if (['queued', 'running'].includes(job.status)) {
      if (['transcribe', 'pipeline'].includes(job.type)) this.notes.locked = true;
      this._poll();
    }
  }

  async _poll() {
    if (!this.job || !this.project || this.job.project_id !== this.project.id) return;
    let job;
    try {
      job = await api.job(this.job.id);
    } catch (e) {
      this._pollT = setTimeout(() => this._poll(), 1500);
      return;
    }
    if (!this.project || job.project_id !== this.project.id) return; // project switched meanwhile
    this.job = job;
    this._showJob(job);
    for (const st of job.stages_done || []) {
      if (!this.stagesSeen.has(st)) {
        this.stagesSeen.add(st);
        await this._onStageDone(st);
      }
    }
    if (['queued', 'running'].includes(job.status)) {
      this._pollT = setTimeout(() => this._poll(), 500);
    } else {
      this.notes.locked = false;
      this.renderVersion++;
      if (job.status === 'done') this.status(`処理が完了しました（${job.elapsed_sec} 秒）`);
    }
  }

  async _onStageDone(stage) {
    const data = await api.getProject(this.project.id);
    this.project = { ...data.project, mixer: this.project.mixer, playback: this.project.playback, fx: this.project.fx };
    if (stage === 'prepare') {
      this.view.duration = data.project.source.duration_sec;
      await this._loadPeaks();
      this.view.fitAll();
    } else if (stage === 'separate') {
      this.stems = data.stems;
      this._buildStemRows();
      await this._loadPeaks();
      this.loading('ステムを読み込み中…');
      try { await this._loadStems(); } finally { this.loading(null); }
      this._applyAllFx();
      this.status('分離が完了しました。採譜の完了前でも再生・練習できます。');
    } else if (stage === 'transcribe') {
      this.notes.locked = false;
      await this._loadNotes();
      this.roll.fitRange();
      const t = data.project.transcription;
      this.status(`採譜完了: ${t.notes} 音（${t.name}${t.merge?.kept_manual ? `、手修正 ${t.merge.kept_manual} 音を保持` : ''}）`);
    }
    this._updateAll();
  }

  // Jobs live in server memory: after a server restart an unfinished project has no job.
  _showUnprocessed(type) {
    this.job = null;
    this.pendingType = type;
    const box = $('job');
    box.hidden = false;
    box.classList.remove('error');
    $('job-stage').textContent = '未処理';
    $('job-bar').style.width = '0%';
    $('job-msg').textContent = type === 'pipeline'
      ? '分離・採譜がまだ完了していません（処理が中断された可能性があります）'
      : '採譜がまだ完了していません（処理が中断された可能性があります）';
    $('job-time').textContent = '';
    $('job-cancel').hidden = true;
    $('job-detail').hidden = true;
    $('job-diag').hidden = true;
    $('job-retry').hidden = false;
    $('job-retry').textContent = type === 'pipeline' ? '解析を開始' : '採譜を開始';
  }

  _showJob(job) {
    const box = $('job');
    $('job-retry').textContent = '再試行';
    if (!job) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    const running = ['queued', 'running'].includes(job.status);
    box.classList.toggle('error', job.status === 'error');
    const stageLabel = job.status === 'queued' ? '待機中' : job.status === 'error' ? 'エラー' : job.status === 'cancelled' ? '中断' :
      job.status === 'done' ? '完了' : job.stage_label || '';
    $('job-stage').textContent = stageLabel;
    $('job-bar').style.width = `${Math.round((job.progress || 0) * 100)}%`;
    $('job-msg').textContent = job.status === 'error' ? job.error : job.message;
    $('job-time').textContent = job.elapsed_sec ? `${job.elapsed_sec}s` : '';
    $('job-cancel').hidden = !running;
    $('job-retry').hidden = !['error', 'cancelled'].includes(job.status);
    $('job-detail').hidden = !(job.error_detail || (job.log_tail && job.log_tail.length));
    $('job-diag').hidden = job.status !== 'error';
    if (job.status === 'done') setTimeout(() => { if (this.job === job) box.hidden = true; }, 5000);
  }

  // =======================================================================================
  // export
  async exportAs(kind) {
    if (!this.project) return;
    if (this.notes.dirty && !(await this.save())) return;
    const map = { midi: ['midi', {}], 'midi-strings': ['midi', { per_string: 1 }], csv: ['csv', {}], pdf: ['pdf', {}] };
    const [fmt, params] = map[kind];
    const a = el('a', { href: api.exportUrl(this.project.id, fmt, params), download: '' });
    document.body.append(a);
    a.click();
    a.remove();
    const q = this.project.quantize?.export && tempoValid(this.project.tempo);
    this.status(`${fmt.toUpperCase()} を書き出しました（量子化: ${q ? this.project.quantize.grid : 'なし'}）`);
  }

  // =======================================================================================
  // upload / capture
  async upload(file, opts = {}) {
    this.loading('アップロード中…');
    try {
      const r = await api.createProject(file, opts, opts.filename);
      this.loading(null);
      await this.openProject(r.project.id);
      if (r.job) this._watchJob(r.job);
    } catch (e) {
      this.status(`読み込みに失敗: ${e.message}`, true);
    } finally {
      this.loading(null);
    }
  }

  async showCapture() {
    if (!TabCapture.supported()) {
      this.alert('タブ録音', 'このブラウザはタブ音声の取り込みに対応していません。Chrome または Edge をお使いください。');
      return;
    }
    const cap = new TabCapture();
    const time = el('div', { class: 'rec-time' }, '0:00');
    const meterBar = el('div');
    const title = el('input', { type: 'text', placeholder: '曲名（任意）' });
    const startBtn = el('button', { class: 'primary' }, '共有するタブを選んで録音開始');
    const stopBtn = el('button', { class: 'primary', disabled: true }, '停止して解析');
    const info = el('p', { class: 'note' });
    const body = el('div', {},
      el('ol', {},
        el('li', {}, 'YouTube などの曲を別タブで開き、曲の頭に戻しておきます。'),
        el('li', {}, '「録音開始」→ 共有ダイアログでそのタブを選び、', el('b', {}, '「タブの音声も共有する」を ON'), 'にします。'),
        el('li', {}, '曲を再生し、終わったら「停止して解析」。録音は実時間かかります。')),
      el('div', { class: 'grid' }, el('label', {}, '曲名'), title),
      time, el('div', { class: 'meter' }, meterBar), info,
      el('p', { class: 'note' }, '録音は非圧縮で保存し、この PC 内でのみ処理します。取り込んだ音源は個人の練習用途に限り、共有・配布はしないでください。'));
    cap.addEventListener('level', (e) => {
      time.textContent = fmtTime(e.detail.seconds, false);
      meterBar.style.width = `${Math.min(100, Math.round(Math.sqrt(e.detail.peak) * 100))}%`;
      if (e.detail.peak >= 0.999) info.textContent = '⚠ 入力がクリップしています（再生側の音量を下げてください）';
    });
    cap.addEventListener('ended', () => stopBtn.click());
    const close = this.modal('ブラウザのタブから録音', body, [startBtn, stopBtn, el('button', { onclick: () => { cap.cancel(); close(); } }, 'キャンセル')]);
    startBtn.addEventListener('click', async () => {
      try {
        await cap.start();
        startBtn.disabled = true;
        stopBtn.disabled = false;
        info.textContent = `録音中: ${cap.label}`;
      } catch (e) {
        info.textContent = e.name === 'NotAllowedError' ? '共有がキャンセルされました。' : e.message;
      }
    });
    stopBtn.addEventListener('click', async () => {
      stopBtn.disabled = true;
      const blob = await cap.stop();
      close();
      if (!blob || cap.samples < 44100) {
        this.status('録音が短すぎます。', true);
        return;
      }
      const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '');
      await this.upload(blob, { title: title.value || `タブ録音 ${stamp}`, kind: 'tab_capture', filename: `tab-capture-${stamp}.wav` });
    });
  }

  // =======================================================================================
  // dialogs
  modal(title, body, buttons) {
    const back = $('modal-back');
    const m = $('modal');
    m.replaceChildren(el('header', {}, title), el('div', { class: 'body' }, body), el('footer', {}, ...buttons));
    back.hidden = false;
    const close = () => { back.hidden = true; m.replaceChildren(); };
    return close;
  }

  confirm(title, message, options) {
    return new Promise((resolve) => {
      let close;
      const btns = options.map(([v, label, cls]) => el('button', { class: cls || '', onclick: () => { close(); resolve(v); } }, label));
      close = this.modal(title, el('p', {}, message), btns);
    });
  }

  alert(title, message) {
    return this.confirm(title, message, [['ok', 'OK', 'primary']]);
  }

  async showProjects() {
    let list = [];
    try { list = await api.listProjects(); } catch (e) { this.status(e.message, true); }
    const table = el('table', { class: 'plist' },
      el('tr', {}, el('th', {}, '曲名'), el('th', {}, '長さ'), el('th', {}, '状態'), el('th', {}, '作成日時'), el('th', {})),
      ...list.map((p) => el('tr', {},
        el('td', {}, p.title || '(無題)', p.kind === 'tab_capture' ? el('span', { class: 'dim small' }, ' (タブ録音)') : ''),
        el('td', { class: 'mono' }, p.duration_sec ? fmtTime(p.duration_sec, false) : '–'),
        el('td', {}, p.active_job ? `処理中 ${Math.round(p.active_job.progress * 100)}%` : p.transcribed ? '採譜済み' : p.separated ? '分離済み' : '未処理'),
        el('td', { class: 'small dim' }, (p.created_at || '').replace('T', ' ').slice(0, 16)),
        el('td', { class: 'actions' },
          el('button', { class: 'primary small', onclick: () => { close(); this.openProject(p.id); } }, '開く'),
          el('button', { class: 'danger small', onclick: async () => {
            const c = await this.confirm('プロジェクトの削除', `「${p.title}」をごみ箱フォルダ（workspace/trash）へ移動します。`, [['del', '移動する', 'danger'], ['cancel', 'キャンセル']]);
            if (c === 'del') {
              try { await api.deleteProject(p.id); } catch (e) { this.status(e.message, true); }
              if (this.project && this.project.id === p.id) localStorage.removeItem('bss:lastProject');
            }
            this.showProjects();
          } }, '削除')))));
    const close = this.modal('プロジェクト', list.length ? table : el('p', { class: 'dim' }, 'まだプロジェクトがありません。'), [el('button', { onclick: () => close() }, '閉じる')]);
  }

  showSettings() {
    const p = this.project;
    const presetSel = el('select', {}, el('option', { value: '' }, 'カスタム'),
      ...this.presets.map((t, i) => el('option', { value: i, selected: JSON.stringify(t.strings) === JSON.stringify(this.tuning.strings) }, t.name)));
    const stringsIn = el('input', { type: 'text', value: this.tuning.strings.map(noteName).join(' ') });
    const fretsIn = el('input', { type: 'number', min: 12, max: 30, value: this.tuning.frets });
    const posIn = el('input', { type: 'number', min: 0, max: 24, value: p.fingering?.preferred_position ?? 3 });
    presetSel.addEventListener('change', () => {
      const t = this.presets[Number(presetSel.value)];
      if (t) { stringsIn.value = t.strings.map(noteName).join(' '); fretsIn.value = t.frets; }
    });
    const bpm = el('input', { type: 'number', min: 20, max: 400, step: 0.01, value: p.tempo?.bpm ?? '' , placeholder: '未設定' });
    const bpb = el('input', { type: 'number', min: 1, max: 16, value: p.tempo?.beats_per_bar ?? 4 });
    const unit = el('select', {}, ...[2, 4, 8, 16].map((u) => el('option', { value: u, selected: (p.tempo?.beat_unit ?? 4) === u }, String(u))));
    const off = el('input', { type: 'number', step: 0.001, value: (p.tempo?.offset_sec ?? 0).toFixed(3) });
    const offNow = el('button', { class: 'small', onclick: () => { off.value = this.engine.position().toFixed(3); } }, '現在位置を1拍目に');
    const grid = el('select', {}, ...['1/4', '1/8', '1/16', '1/32', '1/8T', '1/16T'].map((g) => el('option', { value: g, selected: (p.quantize?.grid || '1/16') === g }, g)));
    const qDisp = el('input', { type: 'checkbox', checked: !!p.quantize?.display });
    const qExp = el('input', { type: 'checkbox', checked: !!p.quantize?.export });
    const thr = el('input', { type: 'number', min: 0, max: 1, step: 0.05, value: p.confidence_threshold ?? 0.5 });
    const title = el('input', { type: 'text', value: p.title || '' });
    const tr = el('select', {}, ...[['fused', 'Basic Pitch + pYIN/onset 補正（推奨）'], ['basic_pitch', 'Basic Pitch のみ'], ['pyin', 'pYIN のみ（ベース単独録音向き）']]
      .map(([v, l]) => el('option', { value: v, selected: (p.transcriber || 'fused') === v, disabled: this.system && this.system.transcribers && !this.system.transcribers[v] }, l)));
    const dev = el('select', {}, ...[['auto', '自動（GPU があれば使用）'], ['cpu', 'CPU'], ['cuda', 'GPU (CUDA)']].map(([v, l]) => el('option', { value: v, selected: (p.separator?.device || 'auto') === v }, l)));
    const shifts = el('select', {}, ...[[0, '0（速い）'], [1, '1（標準）'], [2, '2（高品質・約2倍の時間）']].map(([v, l]) => el('option', { value: v, selected: (p.separator?.shifts ?? 1) === v }, l)));
    const quality = el('select', {}, el('option', { value: 'default', selected: this.engine.preset === 'default' }, '標準（高音質）'), el('option', { value: 'cheaper', selected: this.engine.preset === 'cheaper' }, '軽量（CPU 負荷が高い場合）'));
    const env = p.separation ? `${p.separation.model} / ${p.separation.device} / demucs ${p.separation.demucs} / torch ${p.separation.torch} / 分離 ${p.separation.elapsed_sec}s` : '未分離';
    const body = el('div', {},
      el('fieldset', {}, el('legend', {}, '曲'), el('div', { class: 'grid' }, el('label', {}, '曲名'), title)),
      el('fieldset', {}, el('legend', {}, 'チューニング・運指'), el('div', { class: 'grid' },
        el('label', {}, 'プリセット'), presetSel,
        el('label', {}, '開放弦（低→高）'), stringsIn,
        el('label', {}, 'フレット数'), fretsIn,
        el('label', {}, '優先ポジション'), el('div', { class: 'inline' }, posIn, el('span', { class: 'dim small' }, 'フレット（この付近を優先）'))),
        el('p', { class: 'note' }, '変更すると運指候補を再計算します。弦・フレットの手修正がある場合は扱いを確認します。')),
      el('fieldset', {}, el('legend', {}, 'テンポ・量子化（任意）'), el('div', { class: 'grid' },
        el('label', {}, 'BPM'), bpm,
        el('label', {}, '拍子'), el('div', { class: 'inline' }, bpb, el('span', {}, '/'), unit),
        el('label', {}, '1小節目の開始 (秒)'), el('div', { class: 'inline' }, off, offNow),
        el('label', {}, 'グリッド'), grid,
        el('label', {}, '量子化して表示'), qDisp,
        el('label', {}, '量子化して書き出し'), qExp),
        el('p', { class: 'note' }, '既定は量子化なし（原曲の揺れを保持）。テンポ未設定でも秒単位で表示・PDF化できます。')),
      el('fieldset', {}, el('legend', {}, '採譜'), el('div', { class: 'grid' },
        el('label', {}, '採譜方式'), tr,
        el('label', {}, '低信頼のしきい値'), thr)),
      el('fieldset', {}, el('legend', {}, '音源分離・再生'), el('div', { class: 'grid' },
        el('label', {}, 'デバイス'), dev,
        el('label', {}, 'shifts'), shifts,
        el('label', {}, '伸縮エンジン'), quality,
        el('label', {}, '現在の分離'), el('span', { class: 'small dim' }, env))),
    );
    const collect = () => {
      const strings = stringsIn.value.trim().split(/[\s,]+/).map(parseNoteName);
      if (strings.some((x) => x === null)) throw new Error('開放弦は E1 A1 D2 G2 のように音名で入力してください。');
      if (strings.some((x, i) => i && x <= strings[i - 1])) throw new Error('開放弦は低い方から順に入力してください。');
      return {
        tuning: { strings, frets: Number(fretsIn.value) || 24, name: presetSel.value ? this.presets[Number(presetSel.value)].name : 'カスタム' },
        patch: {
          title: title.value.trim() || p.title,
          fingering: { ...(p.fingering || {}), preferred_position: Number(posIn.value) },
          tempo: { bpm: bpm.value ? Number(bpm.value) : null, beats_per_bar: Number(bpb.value) || 4, beat_unit: Number(unit.value), offset_sec: Number(off.value) || 0 },
          quantize: { grid: grid.value, display: qDisp.checked, export: qExp.checked },
          confidence_threshold: Number(thr.value),
          transcriber: tr.value,
          separator: { ...(p.separator || {}), device: dev.value, shifts: Number(shifts.value) },
        },
      };
    };
    const apply = async () => {
      let c;
      try { c = collect(); } catch (e) { this.alert('入力エラー', e.message); return false; }
      const r = await api.patchProject(p.id, c.patch);
      this.project = { ...r.project, mixer: this.project.mixer, playback: this.project.playback, fx: this.project.fx };
      $('project-title').textContent = this.project.title;
      if (quality.value !== this.engine.preset) await this.engine.setQuality(quality.value);
      const tuningChanged = JSON.stringify(c.tuning.strings) !== JSON.stringify(this.tuning.strings) || c.tuning.frets !== this.tuning.frets;
      if (tuningChanged && !(await this.changeTuning(c.tuning))) return false;
      this.renderVersion++;
      return true;
    };
    const close = this.modal('設定', body, [
      el('button', { title: '手修正は保持して、ベースを採譜し直します', onclick: async () => { if (await apply()) { close(); this.startJob('transcribe'); } } }, '保存して再採譜'),
      el('button', { title: '分離からやり直します（採譜も続けて実行）', onclick: async () => { if (await apply()) { close(); this.startJob('pipeline'); } } }, '保存して再分離'),
      el('button', { onclick: () => close() }, 'キャンセル'),
      el('button', { class: 'primary', onclick: async () => { if (await apply()) close(); } }, '保存'),
    ]);
  }

  // =======================================================================================
  // UI wiring
  _bindUi() {
    SPEEDS.forEach((s) => $('speed').append(el('option', { value: String(s) }, `${Math.round(s * 100)}%`)));
    $('speed').value = '1';
    $('speed').addEventListener('change', () => this.setRate(Number($('speed').value)));
    $('btn-play').addEventListener('click', () => this.togglePlay());
    $('btn-stop').addEventListener('click', () => { this.engine.stop(); this._updateTransport(); });
    $('btn-a').addEventListener('click', () => this.setLoopPoint('a'));
    $('btn-b').addEventListener('click', () => this.setLoopPoint('b'));
    $('btn-loop').addEventListener('click', () => this._toggleLoop());
    $('chk-follow').addEventListener('change', (e) => { this.follow = e.target.checked; });
    $('chk-click-play').addEventListener('change', (e) => { this.clickToPlay = e.target.checked; });
    $('btn-zoom-in').addEventListener('click', () => this.view.zoomAt(1.5, this.view.width / 2));
    $('btn-zoom-out').addEventListener('click', () => this.view.zoomAt(1 / 1.5, this.view.width / 2));
    $('btn-zoom-fit').addEventListener('click', () => this.view.fitAll());
    $('btn-save').addEventListener('click', () => this.save());
    $('btn-settings').addEventListener('click', () => this.showSettings());
    $('btn-projects').addEventListener('click', () => this.showProjects());
    $('btn-capture').addEventListener('click', () => this.showCapture());
    $('btn-reopt').addEventListener('click', () => this.reoptimize(true));
    $('btn-fx').addEventListener('click', () => this.toggleFx());
    $('btn-diag').addEventListener('click', () => this.diag.open('check'));
    $('job-diag').addEventListener('click', () => {
      // a failed read is usually about the file itself; other failures -> job history with logs
      if (this.job && this.job.stage === 'prepare') this.diag.open('probe', { project: true });
      else this.diag.open('jobs');
    });
    $('volume').addEventListener('input', () => { this.muted = false; this.setVolume(Number($('volume').value)); });
    $('volume').addEventListener('dblclick', () => { this.muted = false; this.setVolume(100); });
    $('btn-mute').addEventListener('click', () => { this.muted = !this.muted; this.setVolume(Number($('volume').value), false); });
    let vol = 100;
    try { vol = Number(localStorage.getItem('bss:volume') ?? 100); } catch (_) { /* storage unavailable */ }
    this.setVolume(Number.isFinite(vol) ? vol : 100, false);
    $('file-input').addEventListener('change', (e) => {
      const f = e.target.files[0];
      e.target.value = '';
      if (f) this.upload(f);
    });
    $('btn-export').addEventListener('click', (e) => { e.stopPropagation(); $('export-menu').hidden = !$('export-menu').hidden; });
    document.addEventListener('click', () => { $('export-menu').hidden = true; });
    $('export-menu').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => this.exportAs(b.dataset.export)));
    $('job-cancel').addEventListener('click', async () => {
      if (this.job) this._showJob(await api.cancelJob(this.job.id));
    });
    $('job-retry').addEventListener('click', async () => {
      if (this.job) this._watchJob(await api.retryJob(this.job.id));
      else if (this.pendingType) this.startJob(this.pendingType);
    });
    $('job-detail').addEventListener('click', () => {
      const j = this.job;
      const close = this.modal('エラーの詳細', el('div', {}, el('p', {}, j.error || ''), el('pre', { class: 'log' }, j.error_detail || (j.log_tail || []).join('\n'))), [el('button', { onclick: () => close() }, '閉じる')]);
    });
    // drag & drop
    let depth = 0;
    window.addEventListener('dragenter', (e) => { if (e.dataTransfer?.types?.includes('Files')) { depth++; $('drop-hint').hidden = false; } });
    window.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) $('drop-hint').hidden = true; });
    window.addEventListener('dragover', (e) => e.preventDefault());
    window.addEventListener('drop', (e) => {
      e.preventDefault();
      depth = 0;
      $('drop-hint').hidden = true;
      const f = e.dataTransfer?.files?.[0];
      if (f) this.upload(f);
    });
    // horizontal scroll / zoom over lanes
    $('tracks').addEventListener('wheel', (e) => {
      if (e.target.tagName !== 'CANVAS') return;
      if (e.ctrlKey) {
        e.preventDefault();
        const r = e.target.getBoundingClientRect();
        this.view.zoomAt(e.deltaY < 0 ? 1.2 : 1 / 1.2, e.clientX - r.left);
      } else {
        e.preventDefault();
        const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
        this.view.scrollBy((d / this.view.pps) * 0.8);
      }
    }, { passive: false });
    window.addEventListener('beforeunload', (e) => {
      if (this.notes.dirty) { e.preventDefault(); e.returnValue = ''; }
      else if (this.project) this._persistSettings();
    });
  }

  _toggleLoop() {
    if (this.loop.b - this.loop.a < 0.05) {
      const p = this.engine.position();
      this.setLoop(true, p, Math.min(this.view.duration, p + 4));
    } else this.setLoop(!this.loop.enabled, this.loop.a, this.loop.b);
  }

  _bindKeys() {
    window.addEventListener('keydown', (e) => {
      if (!$('modal-back').hidden) {
        if (e.key === 'Escape') $('modal-back').hidden = true;
        return;
      }
      const tag = (e.target.tagName || '').toLowerCase();
      if (['input', 'select', 'textarea'].includes(tag) && e.key !== 'Escape') return;
      const k = e.key;
      const ctrl = e.ctrlKey || e.metaKey;
      let handled = true;
      if (k === ' ') this.togglePlay();
      else if (ctrl && k.toLowerCase() === 's') this.save();
      else if (ctrl && k.toLowerCase() === 'z' && !e.shiftKey) this.notes.locked || this.notes.undo();
      else if (ctrl && (k.toLowerCase() === 'y' || (k.toLowerCase() === 'z' && e.shiftKey))) this.notes.locked || this.notes.redo();
      else if (k === 'ArrowUp' || k === 'ArrowDown') {
        const dir = k === 'ArrowUp' ? 1 : -1;
        if (e.altKey) this.stringSelected(-dir);
        else this.pitchSelected(ctrl ? 12 * dir : dir);
      } else if (k === 'ArrowLeft' || k === 'ArrowRight') this.selectAdjacent(k === 'ArrowRight' ? 1 : -1);
      else if (k === 'Delete' || k === 'Backspace') this.deleteSelected();
      else if (k === 'Escape') this.select([]);
      else if (ctrl) handled = false;
      else if (k === 'a' || k === 'A') this.setLoopPoint('a');
      else if (k === 'b' || k === 'B') this.setLoopPoint('b');
      else if (k === 'l' || k === 'L') this._toggleLoop();
      else if (k === 's' || k === 'S') this.splitSelected();
      else if (k === 'm' || k === 'M') this.notes.merge([...this.selection]);
      else if (k === 'f' || k === 'F') { this.follow = !this.follow; $('chk-follow').checked = this.follow; }
      else if (k === 'e' || k === 'E') this.toggleFx();
      else if (k === 'Home') this.engine.seek(this.loop.enabled ? this.loop.a : 0);
      else if (k === '[' || k === ']') {
        const i = SPEEDS.indexOf(this.rate);
        const j = Math.max(0, Math.min(SPEEDS.length - 1, (i < 0 ? 5 : i) + (k === ']' ? 1 : -1)));
        this.setRate(SPEEDS[j]);
      } else if (k === '+' || k === '=') this.view.zoomAt(1.5, this.view.width / 2);
      else if (k === '-') this.view.zoomAt(1 / 1.5, this.view.width / 2);
      else handled = false;
      if (handled) e.preventDefault();
    });
  }

  _updateAll() {
    const has = !!this.project;
    $('btn-settings').disabled = !has;
    $('btn-fx').disabled = !has;
    $('btn-export').disabled = !has || !this.notes.size;
    $('btn-reopt').disabled = !has || !this.notes.size;
    this.renderVersion++;
    this._updateTransport();
    this._updateDirty();
    this.inspector.render();
  }

  // =======================================================================================
  _frame() {
    requestAnimationFrame(() => this._frame());
    const pos = this.engine.loaded ? this.engine.position() : 0;
    this.engine.tick();
    if (this.follow && this.engine.playing) this.view.follow(pos, this.loop);
    if (!this.project) return;
    $('time-pos').textContent = fmtTime(pos);
    this.overview.render(pos);
    this.ruler.render(pos);
    for (const lv of this.laneViews) lv.render(pos);
    this.roll.render(pos);
    this.tab.render(pos);
    this.fxPanel.render();
    if (this._lastPlaying !== this.engine.playing) {
      this._lastPlaying = this.engine.playing;
      this._updateTransport();
      $('btn-export').disabled = !this.notes.size;
      $('btn-reopt').disabled = !this.notes.size;
    }
  }
}

// Installed first so that errors during start-up are captured too.
const capture = new ErrorCapture(() => ({
  project: app && app.project ? app.project.id : null,
  title: app && app.project ? app.project.title : null,
  audio: app ? clientInfo(app.engine).audio : null,
  url: location.pathname,
}));
const app = new App();
window.bss = app; // for debugging / measurement from the console
app.start();

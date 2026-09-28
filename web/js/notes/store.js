// Note store with transactional edits and undo/redo.
// An undo entry is {label, time, changes: [{id, before, after}], suppress?: {add:[], remove:[]}}
// (the same shape the server writes for a re-analysis, so undo spans both).

import { chooseForPitch, stepString } from './fingering.js';

let seq = 0;
export const newId = () => `m_${Date.now().toString(36)}${(seq++).toString(36)}`;

const clone = (n) => (n ? JSON.parse(JSON.stringify(n)) : null);

export class NoteStore extends EventTarget {
  constructor() {
    super();
    this.reset();
  }

  reset() {
    this.map = new Map();
    this.suppressed = [];
    this.undoStack = [];
    this.redoStack = [];
    this.revision = 0;
    this.dirty = false;
    this.pendingLog = [];
    this._sorted = null;
    this.version = 0;
    this.locked = false;
  }

  load(data) {
    this.reset();
    for (const n of data.notes || []) this.map.set(n.id, n);
    this.suppressed = data.suppressed || [];
    this.undoStack = (data.undo && data.undo.undo) || [];
    this.redoStack = (data.undo && data.undo.redo) || [];
    this.revision = data.revision || 0;
    this._changed();
    this.dirty = false;
  }

  get size() {
    return this.map.size;
  }

  get(id) {
    return this.map.get(id);
  }

  sorted() {
    if (!this._sorted) {
      this._sorted = [...this.map.values()].sort((a, b) => a.start_sec - b.start_sec || a.midi_pitch - b.midi_pitch);
    }
    return this._sorted;
  }

  neighbours(id) {
    const s = this.sorted();
    const i = s.findIndex((n) => n.id === id);
    return { prev: i > 0 ? s[i - 1] : null, next: i >= 0 && i < s.length - 1 ? s[i + 1] : null, index: i };
  }

  _changed() {
    this._sorted = null;
    this.version++;
    this.dirty = true;
    this.dispatchEvent(new Event('change'));
  }

  // Run `fn(tx)`; tx.update(id, patch) / tx.add(note) / tx.remove(id) / tx.suppress(entry).
  transact(label, fn) {
    if (this.locked) throw new Error('解析中は編集できません');
    const before = new Map();
    const touched = new Set();
    const supAdd = [];
    const tx = {
      update: (id, patch) => {
        const n = this.map.get(id);
        if (!n) return;
        if (!before.has(id)) before.set(id, clone(n));
        Object.assign(n, patch);
        touched.add(id);
      },
      add: (note) => {
        if (!before.has(note.id)) before.set(note.id, null);
        this.map.set(note.id, note);
        touched.add(note.id);
      },
      remove: (id) => {
        const n = this.map.get(id);
        if (!n) return;
        if (!before.has(id)) before.set(id, clone(n));
        this.map.delete(id);
        touched.add(id);
      },
      suppress: (entry) => {
        this.suppressed.push(entry);
        supAdd.push(entry);
      },
    };
    fn(tx);
    const changes = [];
    for (const id of touched) {
      const b = before.get(id);
      const a = clone(this.map.get(id) || null);
      if (JSON.stringify(a) !== JSON.stringify(b)) changes.push({ id, before: b, after: a });
    }
    if (!changes.length && !supAdd.length) return null;
    const entry = { label, time: new Date().toISOString(), changes, suppress: supAdd.length ? { add: supAdd } : undefined };
    this.undoStack.push(entry);
    if (this.undoStack.length > 200) this.undoStack.shift();
    this.redoStack = [];
    this.pendingLog.push({ label, ids: changes.map((c) => c.id), t: entry.time });
    this._changed();
    return entry;
  }

  _applyEntry(entry, direction) {
    for (const c of entry.changes) {
      const target = direction === 'undo' ? c.before : c.after;
      if (target) this.map.set(c.id, clone(target));
      else this.map.delete(c.id);
    }
    if (entry.suppress && entry.suppress.add) {
      if (direction === 'undo') {
        const keys = new Set(entry.suppress.add.map((s) => JSON.stringify(s)));
        this.suppressed = this.suppressed.filter((s) => !keys.has(JSON.stringify(s)));
      } else {
        this.suppressed.push(...entry.suppress.add);
      }
    }
  }

  undo() {
    const e = this.undoStack.pop();
    if (!e) return null;
    this._applyEntry(e, 'undo');
    this.redoStack.push(e);
    this.pendingLog.push({ label: `取り消し: ${e.label}`, t: new Date().toISOString() });
    this._changed();
    return e;
  }

  redo() {
    const e = this.redoStack.pop();
    if (!e) return null;
    this._applyEntry(e, 'redo');
    this.undoStack.push(e);
    this.pendingLog.push({ label: `やり直し: ${e.label}`, t: new Date().toISOString() });
    this._changed();
    return e;
  }

  toSave() {
    return {
      notes: this.sorted(),
      suppressed: this.suppressed,
      undo: { undo: this.undoStack, redo: this.redoStack },
      base_revision: this.revision,
      log: this.pendingLog,
    };
  }

  markSaved(revision) {
    this.revision = revision;
    this.dirty = false;
    this.pendingLog = [];
    this.dispatchEvent(new Event('saved'));
  }

  // ---- edit operations ----------------------------------------------------------------
  // Pitch edit (the sounding note changes). String kept when possible.
  setPitch(ids, fn, tuning, label = '音高変更') {
    return this.transact(label, (tx) => {
      for (const id of ids) {
        const n = this.map.get(id);
        if (!n) continue;
        const p = Math.max(0, Math.min(127, fn(n.midi_pitch)));
        if (p === n.midi_pitch) continue;
        const { prev, next } = this.neighbours(id);
        const f = chooseForPitch(n, p, tuning, prev, next);
        const flags = (n.flags || []).filter((x) => x !== 'out_of_range');
        if (f.string == null) flags.push('out_of_range');
        tx.update(id, {
          midi_pitch: p, edited: true, string: f.string, fret: f.fret, flags,
          fingering_edited: n.fingering_edited && f.keptString,
        });
      }
    });
  }

  // Fingering edit (same pitch, other string).
  setString(id, stringNo, tuning, label = '弦・フレット変更') {
    const n = this.map.get(id);
    if (!n) return null;
    const open = tuning.strings[tuning.strings.length - stringNo];
    const fret = n.midi_pitch - open;
    if (open === undefined || fret < 0 || fret > tuning.frets) return null;
    return this.transact(label, (tx) => tx.update(id, { string: stringNo, fret, fingering_edited: true }));
  }

  stepString(ids, dir, tuning) {
    return this.transact('弦・フレット変更', (tx) => {
      for (const id of ids) {
        const n = this.map.get(id);
        const c = n && stepString(n, dir, tuning);
        if (c) tx.update(id, { string: c.string, fret: c.fret, fingering_edited: true });
      }
    });
  }

  moveResize(changes, label) {
    // changes: [{id, start_sec, end_sec, midi_pitch?}]
    return this.transact(label, (tx) => {
      for (const c of changes) {
        const patch = { start_sec: +c.start_sec.toFixed(4), end_sec: +c.end_sec.toFixed(4), edited: true };
        if (c.midi_pitch !== undefined && c.midi_pitch !== this.map.get(c.id)?.midi_pitch) {
          patch.midi_pitch = c.midi_pitch;
          if (c.string !== undefined) {
            patch.string = c.string;
            patch.fret = c.fret;
          }
        }
        tx.update(c.id, patch);
      }
    });
  }

  addNote(start, end, pitch, fingering) {
    const note = {
      id: newId(), start_sec: +start.toFixed(4), end_sec: +end.toFixed(4), midi_pitch: pitch, confidence: 1,
      source: 'manual', edited: true, string: fingering?.string ?? null, fret: fingering?.fret ?? null,
      technique: null, fingering_edited: false, flags: [],
    };
    this.transact('音符追加', (tx) => tx.add(note));
    return note;
  }

  remove(ids) {
    return this.transact('削除', (tx) => {
      for (const id of ids) {
        const n = this.map.get(id);
        if (!n) continue;
        if (n.source !== 'manual') tx.suppress({ start_sec: n.start_sec, midi_pitch: n.midi_pitch });
        tx.remove(id);
      }
    });
  }

  split(id, t) {
    const n = this.map.get(id);
    if (!n || t <= n.start_sec + 0.02 || t >= n.end_sec - 0.02) return null;
    return this.transact('分割', (tx) => {
      tx.update(id, { end_sec: +t.toFixed(4), edited: true });
      tx.add({ ...clone(n), id: newId(), start_sec: +t.toFixed(4), edited: true, source: 'manual' });
    });
  }

  merge(ids) {
    const notes = ids.map((id) => this.map.get(id)).filter(Boolean).sort((a, b) => a.start_sec - b.start_sec);
    if (notes.length < 2) return null;
    const first = notes[0];
    const end = Math.max(...notes.map((n) => n.end_sec));
    return this.transact('結合', (tx) => {
      tx.update(first.id, { end_sec: end, edited: true });
      for (const n of notes.slice(1)) tx.remove(n.id);
    });
  }

  setFields(id, patch, label = '編集') {
    return this.transact(label, (tx) => tx.update(id, patch));
  }

  applyAssignments(result, label, clearLocks = false) {
    return this.transact(label, (tx) => {
      for (const n of this.map.values()) {
        const a = result.assignments[n.id];
        const patch = {};
        if (a) {
          patch.string = a[0];
          patch.fret = a[1];
          patch.flags = (n.flags || []).filter((f) => f !== 'out_of_range');
        } else if (result.unplayable.includes(n.id)) {
          patch.string = null;
          patch.fret = null;
          patch.flags = [...new Set([...(n.flags || []), 'out_of_range'])];
        }
        if (clearLocks || result.dropped_locks.includes(n.id)) patch.fingering_edited = false;
        if (Object.keys(patch).length) tx.update(n.id, patch);
      }
    });
  }
}

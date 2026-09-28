// Rhythm quantization for the score view and Guitar Pro export.
//
// Notes keep their raw seconds (the audio is the reference). Here they are placed on the tempo
// grid and written as standard note values: bars -> beats (a note, a chord or a rest) with
// value (base 1/2/4/8/16/32, dots, triplet) and ties across bar lines / beat groups.
//
// Grid per beat: binary (16ths by default) or 8th-triplets. "auto" picks triplets for a beat only
// when its onsets fit the triplet grid clearly better; the note flags 'triplet' / 'straight' force
// it. Gaps shorter than `rest_min` are absorbed into the preceding note (bass lines are read legato)
// unless the note's value was set by hand ('dur_fixed').

export const PPQ = 960; // ticks per quarter note

const GRID_TICKS = { '1/8': 480, '1/16': 240, '1/32': 120 };
const FINE = 20; // ticks: divides every plain, dotted and triplet value down to 1/64 and 16th triplets
const REST_MIN_TICKS = { '1/32': 120, '1/16': 240, '1/8': 480, '1/4': 960 };
export const BASES = [1, 2, 4, 8, 16, 32];
export const VALUE_NAMES = { 1: '全音符', 2: '2分音符', 4: '4分音符', 8: '8分音符', 16: '16分音符', 32: '32分音符', 64: '64分音符' };

export const baseTicks = (base) => (PPQ * 4) / base;
export function valueTicks(v) {
  let t = baseTicks(v.base);
  if (v.dots === 1) t *= 1.5;
  if (v.tuplet) t = (t * 2) / 3;
  return Math.round(t);
}
export function valueName(v) {
  return `${v.dots ? '付点' : ''}${VALUE_NAMES[v.base] || `1/${v.base}`}${v.tuplet ? '（3連）' : ''}`;
}

export function tempoOk(tempo) {
  return !!(tempo && tempo.bpm && Number(tempo.bpm) > 0);
}

export class Timing {
  constructor(tempo) {
    this.bpm = Number(tempo.bpm);
    this.unit = Number(tempo.beat_unit) || 4;
    this.bpb = Number(tempo.beats_per_bar) || 4;
    this.offset = Number(tempo.offset_sec) || 0;
    this.beatSec = 60 / this.bpm;
    this.beatTicks = (PPQ * 4) / this.unit;
    this.barTicks = this.beatTicks * this.bpb;
    this.secPerTick = this.beatSec / this.beatTicks;
  }

  tick(t) { return (t - this.offset) / this.secPerTick; }
  time(tick) { return this.offset + tick * this.secPerTick; }
  barOf(tick) { return Math.floor(tick / this.barTicks); }
}

// First displayed bar (bar number 1): the bar containing the start of the song, or an earlier one
// when a note starts before it. Shared with the ruler so both show the same bar numbers.
export function firstBarIndex(timing, firstNoteSec = null) {
  let k = timing.barOf(timing.tick(0) + 1e-6);
  if (firstNoteSec != null) k = Math.min(k, timing.barOf(timing.tick(firstNoteSec)));
  return k;
}

// Values usable at a position (ticks from the bar start). Notes may be syncopated (start on half
// of their value), rests follow the beat structure.
const BIN_VALUES = [
  { base: 1, dots: 0 }, { base: 2, dots: 1 }, { base: 2, dots: 0 }, { base: 4, dots: 1 }, { base: 4, dots: 0 },
  { base: 8, dots: 1 }, { base: 8, dots: 0 }, { base: 16, dots: 1 }, { base: 16, dots: 0 }, { base: 32, dots: 0 },
  { base: 64, dots: 0 },
].map((v) => ({ ...v, tuplet: false, ticks: valueTicks(v) }));

function binaryPieces(pos, len, rest, tm) {
  const out = [];
  let guard = 0;
  while (len > 0 && guard++ < 64) {
    let v = null;
    for (const c of BIN_VALUES) {
      if (c.ticks > len) continue;
      const b = baseTicks(c.base);
      if (rest) {
        if (c.dots) continue;
        if (pos % b) continue;
        if (b > tm.beatTicks && pos % tm.beatTicks) continue;
        if (c.base === 1 && !(pos === 0 && c.ticks === tm.barTicks)) continue;
      } else if (pos % (b / 2)) continue;
      v = c;
      break;
    }
    if (!v) v = BIN_VALUES[BIN_VALUES.length - 1];
    const t = Math.min(v.ticks, len);
    out.push({ pos, ticks: t, value: { base: v.base, dots: v.dots, tuplet: false } });
    pos += t;
    len -= t;
  }
  return out;
}

function tripletValues(tm, unit) {
  const out = [];
  for (const base of [2, 4, 8, 16, 32]) {
    for (const dots of [1, 0]) {
      const v = { base, dots, tuplet: true };
      const t = valueTicks(v);
      if (t < tm.beatTicks && t % unit === 0) out.push({ ...v, ticks: t });
    }
  }
  return out.sort((a, b) => b.ticks - a.ticks);
}

function tripletPieces(pos, len, vals) {
  const out = [];
  let guard = 0;
  while (len > 0 && guard++ < 32) {
    const v = vals.find((c) => c.ticks <= len) || vals[vals.length - 1];
    const t = Math.min(v.ticks, len);
    out.push({ pos, ticks: t, value: { base: v.base, dots: v.dots, tuplet: true } });
    pos += t;
    len -= t;
  }
  return out;
}

// Krumhansl-Kessler key profiles on a duration-weighted pitch-class histogram.
const KK_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88];
const KK_MINOR = [6.33, 2.68, 3.52, 5.38, 2.6, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17];
const MAJOR_FIFTHS = { 0: 0, 7: 1, 2: 2, 9: 3, 4: 4, 11: 5, 6: -6, 1: -5, 8: -4, 3: -3, 10: -2, 5: -1 };
const MAJOR_NAMES = { 0: 'C', 1: 'D♭', 2: 'D', 3: 'E♭', 4: 'E', 5: 'F', 6: 'G♭', 7: 'G', 8: 'A♭', 9: 'A', 10: 'B♭', 11: 'B' };
const MINOR_NAMES = { 0: 'Cm', 1: 'C♯m', 2: 'Dm', 3: 'E♭m', 4: 'Em', 5: 'Fm', 6: 'F♯m', 7: 'Gm', 8: 'G♯m', 9: 'Am', 10: 'B♭m', 11: 'Bm' };

function corr(a, b) {
  const n = a.length;
  const ma = a.reduce((s, x) => s + x, 0) / n;
  const mb = b.reduce((s, x) => s + x, 0) / n;
  let num = 0, da = 0, db = 0;
  for (let i = 0; i < n; i++) {
    num += (a[i] - ma) * (b[i] - mb);
    da += (a[i] - ma) ** 2;
    db += (b[i] - mb) ** 2;
  }
  return da && db ? num / Math.sqrt(da * db) : 0;
}

export function estimateKey(notes) {
  const h = new Array(12).fill(0);
  for (const n of notes) h[((n.midi_pitch % 12) + 12) % 12] += Math.min(2, Math.max(0.05, n.end_sec - n.start_sec));
  if (!h.some((x) => x > 0)) return { fifths: 0, minor: false, name: 'C', score: 0 };
  let best = { score: -2 };
  for (let tonic = 0; tonic < 12; tonic++) {
    for (const minor of [false, true]) {
      const prof = minor ? KK_MINOR : KK_MAJOR;
      const rot = h.map((_, i) => prof[(i - tonic + 12) % 12]);
      const s = corr(h, rot);
      if (s > best.score) best = { tonic, minor, score: s };
    }
  }
  const majorTonic = best.minor ? (best.tonic + 3) % 12 : best.tonic;
  return { fifths: MAJOR_FIFTHS[majorTonic], minor: best.minor, name: best.minor ? MINOR_NAMES[best.tonic] : MAJOR_NAMES[best.tonic], score: best.score };
}

export function keyName(fifths) {
  const pc = Object.entries(MAJOR_FIFTHS).find(([, f]) => f === fifths);
  return pc ? MAJOR_NAMES[pc[0]] : '';
}

// notes: app note objects; tempo: project tempo; opts: project.score settings.
// Returns null when the tempo is not set.
export function buildScore(notes, tempo, opts = {}) {
  if (!tempoOk(tempo)) return null;
  const tm = new Timing(tempo);
  const bt = tm.beatTicks;
  const binStep = Math.min(GRID_TICKS[opts.grid] || 240, bt);
  const tripUnit = opts.grid === '1/32' ? bt / 6 : bt / 3;
  const autoTriplets = opts.triplets !== 'off';
  const restMin = REST_MIN_TICKS[opts.rest_min] ?? 480;
  const stats = { unplaced: [], hidden: [], tripletBeats: 0 };

  const placed = [];
  for (const n of notes) {
    if (n.string == null || n.fret == null) {
      stats.unplaced.push(n.id);
      continue;
    }
    placed.push({ n, s: tm.tick(n.start_sec), e: tm.tick(n.end_sec) });
  }
  placed.sort((a, b) => a.s - b.s || a.n.midi_pitch - b.n.midi_pitch);

  // ---- 1. grid per beat
  const beatOf = (tick) => Math.floor((tick + binStep / 2) / bt);
  const byBeat = new Map();
  for (const p of placed) {
    const j = beatOf(p.s);
    if (!byBeat.has(j)) byBeat.set(j, []);
    byBeat.get(j).push(p);
  }
  const triplet = new Set();
  for (const [j, ps] of byBeat) {
    const flags = ps.flatMap((p) => p.n.flags || []);
    if (flags.includes('triplet')) { triplet.add(j); continue; }
    if (flags.includes('straight') || !autoTriplets) continue;
    const rel = ps.map((p) => p.s - j * bt).filter((r) => r > binStep / 2 && r < bt - binStep / 2);
    if (!rel.length) continue;
    const err = (step) => rel.reduce((s, r) => s + Math.abs(r - Math.round(r / step) * step), 0) / rel.length;
    const eb = err(binStep);
    const et = err(tripUnit);
    if (et < eb * 0.5 && eb - et > binStep * 0.2) triplet.add(j);
  }
  stats.tripletBeats = triplet.size;
  const stepAt = (j) => (triplet.has(j) ? tripUnit : binStep);
  const snapIn = (tick, j) => j * bt + Math.round((tick - j * bt) / stepAt(j)) * stepAt(j);

  // ---- 2. events (a note or a chord) with snapped start / end
  const events = [];
  const rawStart = new Map();
  for (const p of placed) {
    const j = beatOf(p.s);
    // a note whose value was set by hand sits exactly where it was put (e.g. dotted 16ths)
    const fixed = (p.n.flags || []).includes('dur_fixed');
    let st = fixed ? Math.round(p.s / FINE) * FINE : snapIn(p.s, j);
    if (events.length && st < events[events.length - 1].start) st = events[events.length - 1].start;
    let ev = events.length && events[events.length - 1].start === st ? events[events.length - 1] : null;
    if (ev) {
      // A quick repeated note that rounded onto the previous note's slot: use the next slot
      // (when it is not much further away) instead of dropping it.
      const clash = ev.notes.find((x) => x.string === p.n.string);
      const step = stepAt(j);
      if (clash && p.s - rawStart.get(clash.id) > 0.35 * step && st + step - p.s < 0.9 * step) {
        st += step;
        ev = null;
      }
    }
    rawStart.set(p.n.id, p.s);
    if (!ev) {
      ev = { start: st, beat: j, notes: [], rawEnd: -Infinity, fixed: false };
      events.push(ev);
    }
    const clash = ev.notes.find((x) => x.string === p.n.string);
    if (clash) {
      // one note per string: keep the more confident one
      if ((p.n.confidence ?? 1) > (clash.confidence ?? 1)) {
        stats.hidden.push(clash.id);
        ev.notes[ev.notes.indexOf(clash)] = p.n;
      } else stats.hidden.push(p.n.id);
    } else ev.notes.push(p.n);
    ev.rawEnd = Math.max(ev.rawEnd, p.e);
    if ((p.n.flags || []).includes('dur_fixed')) ev.fixed = true;
  }
  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    const je = Math.floor((ev.rawEnd - 1e-6) / bt);
    let end = ev.fixed ? Math.round(ev.rawEnd / FINE) * FINE : snapIn(ev.rawEnd, je);
    if (end <= ev.start) end = ev.start + stepAt(ev.beat);
    const next = events[i + 1];
    if (next) {
      if (end > next.start) end = next.start;
      const gap = next.start - end;
      if (gap > 0 && gap < restMin && !ev.fixed) end = next.start;
    }
    ev.end = end;
  }

  // ---- 3. bars
  const kFirst = firstBarIndex(tm, placed.length ? placed[0].n.start_sec : null);
  const kLast = events.length ? tm.barOf(events[events.length - 1].end - 1) : kFirst;
  const segs = [];
  let cur = kFirst * tm.barTicks;
  for (const ev of events) {
    if (ev.start > cur) segs.push({ s: cur, e: ev.start, ev: null });
    segs.push({ s: ev.start, e: ev.end, ev });
    cur = ev.end;
  }
  const endTick = (kLast + 1) * tm.barTicks;
  if (cur < endTick) segs.push({ s: cur, e: endTick, ev: null });
  const tripVals = tripletValues(tm, bt / 6); // 16th-triplet resolution (hand-set values)

  const bars = [];
  const beats = [];
  const noteIndex = new Map();
  let si = 0;
  for (let k = kFirst; k <= kLast; k++) {
    const b0 = k * tm.barTicks;
    const b1 = b0 + tm.barTicks;
    const bar = { k, number: k - kFirst + 1, tick: b0, t0: tm.time(b0), t1: tm.time(b1), beats: [] };
    while (si < segs.length && segs[si].e <= b0) si++;
    for (let i = si; i < segs.length && segs[i].s < b1; i++) {
      const sg = segs[i];
      const ps = Math.max(sg.s, b0);
      const pe = Math.min(sg.e, b1);
      if (pe <= ps) continue;
      // split at the edges of triplet beats
      const cuts = [ps];
      for (let j = Math.ceil(ps / bt); j * bt < pe; j++) {
        if ((triplet.has(j) || triplet.has(j - 1)) && j * bt > ps) cuts.push(j * bt);
      }
      cuts.push(pe);
      for (let c = 0; c < cuts.length - 1; c++) {
        const a = cuts[c];
        const z = cuts[c + 1];
        const j = Math.floor(a / bt);
        const inTriplet = triplet.has(j) && !(a === j * bt && z === (j + 1) * bt);
        const pieces = inTriplet ? tripletPieces(a - b0, z - a, tripVals) : binaryPieces(a - b0, z - a, !sg.ev, tm);
        for (const pc of pieces) {
          const tick = b0 + pc.pos;
          const beat = {
            index: beats.length, bar: bars.length, tick, ticks: pc.ticks, value: pc.value,
            t0: tm.time(tick), t1: tm.time(tick + pc.ticks), rest: !sg.ev,
            notes: sg.ev ? sg.ev.notes : [], tieIn: !!sg.ev && tick > sg.s, tieOut: !!sg.ev && tick + pc.ticks < sg.e,
            event: sg.ev,
          };
          beats.push(beat);
          bar.beats.push(beat.index);
          if (sg.ev && tick === sg.s) {
            for (const n of sg.ev.notes) noteIndex.set(n.id, { beat: beat.index, start: sg.s, end: sg.e, triplet: triplet.has(Math.floor(sg.s / bt)) });
          }
        }
      }
    }
    bars.push(bar);
  }

  let key;
  if (opts.key === 'auto' || opts.key == null) key = { ...estimateKey(notes), auto: true };
  else key = { fifths: Number(opts.key), minor: false, name: keyName(Number(opts.key)), auto: false };

  return { timing: tm, kFirst, bars, beats, noteIndex, key, stats, binStep, tripUnit, triplet };
}

// Beat containing time t (binary search); -1 before the first beat.
export function beatAtTime(model, t) {
  const bs = model.beats;
  let lo = 0;
  let hi = bs.length - 1;
  if (!bs.length || t < bs[0].t0) return -1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (bs[mid].t0 <= t) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

// Grid step (ticks) at a position: the triplet unit inside triplet beats, else the binary step.
export function stepAtTick(model, tick) {
  const j = Math.floor(tick / model.timing.beatTicks);
  return model.triplet.has(j) ? model.tripUnit : model.binStep;
}

export function valueOfTicks(ticks, triplet = false) {
  for (const base of BASES) {
    for (const dots of [0, 1]) {
      const v = { base, dots, tuplet: triplet };
      if (valueTicks(v) === ticks) return v;
    }
  }
  return null;
}

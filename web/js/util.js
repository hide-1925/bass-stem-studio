// Small shared helpers.

export const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

export function noteName(midi) {
  return `${NOTE_NAMES[((midi % 12) + 12) % 12]}${Math.floor(midi / 12) - 1}`;
}

export function parseNoteName(name) {
  const m = /^\s*([A-Ga-g])([#b♯♭]?)(-?\d+)\s*$/.exec(name || '');
  if (!m) return null;
  const base = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 }[m[1].toUpperCase()];
  const acc = m[2] === '#' || m[2] === '♯' ? 1 : m[2] === 'b' || m[2] === '♭' ? -1 : 0;
  return (parseInt(m[3], 10) + 1) * 12 + base + acc;
}

export const dbToLin = (db) => Math.pow(10, db / 20);
export const linToDb = (g) => 20 * Math.log10(Math.max(g, 1e-9));
export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

export function fmtTime(sec, withMs = true) {
  if (!isFinite(sec)) sec = 0;
  const neg = sec < 0;
  sec = Math.abs(sec);
  const m = Math.floor(sec / 60);
  const s = sec - m * 60;
  const str = withMs ? `${m}:${s.toFixed(3).padStart(6, '0')}` : `${m}:${Math.floor(s).toString().padStart(2, '0')}`;
  return (neg ? '-' : '') + str;
}

export function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'dataset') Object.assign(e.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
    else if (k === 'html') e.innerHTML = v;
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return e;
}

export function debounce(fn, ms) {
  let t = null;
  const d = (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
  d.flush = (...args) => {
    clearTimeout(t);
    fn(...args);
  };
  return d;
}

// Tempo / grid (mirrors bss/quantize.py)
const GRID_FRACTION = { '1/4': 1 / 4, '1/8': 1 / 8, '1/16': 1 / 16, '1/32': 1 / 32, '1/8T': 1 / 12, '1/16T': 1 / 24 };
export const tempoValid = (tempo) => !!(tempo && tempo.bpm && tempo.bpm > 0);
export function gridSeconds(tempo, grid) {
  const beats = (GRID_FRACTION[grid] ?? 1 / 16) * (tempo.beat_unit || 4);
  return beats * 60 / tempo.bpm;
}
export function quantizeTime(t, tempo, grid) {
  const step = gridSeconds(tempo, grid);
  const off = tempo.offset_sec || 0;
  return off + Math.round((t - off) / step) * step;
}
export function barSeconds(tempo) {
  return (tempo.beats_per_bar || 4) * 60 / tempo.bpm;
}

// Canvas helper: size a canvas to its CSS box at devicePixelRatio.
export function fitCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(canvas.clientWidth));
  const h = Math.max(1, Math.round(canvas.clientHeight));
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

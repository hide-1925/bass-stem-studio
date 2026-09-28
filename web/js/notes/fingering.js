// Client-side fingering helpers. The whole-song optimizer lives on the server
// (bss/fingering/optimizer.py); here we only list candidates and make local choices
// while editing, so single edits never reshuffle the rest of the TAB.

// tuning.strings: open MIDI pitches from LOW to HIGH. TAB string 1 = highest string.
export function candidates(pitch, tuning) {
  const out = [];
  const n = tuning.strings.length;
  tuning.strings.forEach((open, idxLow) => {
    const fret = pitch - open;
    if (fret >= 0 && fret <= tuning.frets) out.push({ string: n - idxLow, fret });
  });
  return out.sort((a, b) => a.string - b.string);
}

export function openPitch(stringNo, tuning) {
  const n = tuning.strings.length;
  return tuning.strings[n - stringNo];
}

// After a pitch edit: keep the string when it can still play the pitch, otherwise take the
// candidate whose fret is closest to the neighbouring fretted notes.
export function chooseForPitch(note, newPitch, tuning, prev, next) {
  const cands = candidates(newPitch, tuning);
  if (!cands.length) return { string: null, fret: null, keptString: false };
  if (note.string != null) {
    const same = cands.find((c) => c.string === note.string);
    if (same) return { ...same, keptString: true };
  }
  const refs = [prev, next].filter((n) => n && n.fret != null && n.fret > 0).map((n) => n.fret);
  const ref = refs.length ? refs.reduce((a, b) => a + b, 0) / refs.length : note.fret ?? 3;
  let best = cands[0];
  let bestCost = Infinity;
  for (const c of cands) {
    const cost = Math.abs((c.fret || ref) - ref) + (c.fret > 12 ? (c.fret - 12) * 0.5 : 0);
    if (cost < bestCost) {
      bestCost = cost;
      best = c;
    }
  }
  return { ...best, keptString: false };
}

// Same pitch on the neighbouring string. dir = -1: towards string 1 (higher strings), +1: lower.
export function stepString(note, dir, tuning) {
  const cands = candidates(note.midi_pitch, tuning);
  if (!cands.length) return null;
  const cur = note.string ?? cands[0].string;
  const ordered = dir < 0 ? cands.filter((c) => c.string < cur).sort((a, b) => b.string - a.string)
    : cands.filter((c) => c.string > cur).sort((a, b) => a.string - b.string);
  return ordered[0] || null;
}

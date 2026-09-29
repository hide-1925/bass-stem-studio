"""Default transcriber: Basic Pitch note events corrected with pYIN and onset detection.

Steps (see docs/SPEC.md §4.3):
  1. harmonic-ghost removal   2. octave-error suppression   3. attack alignment
  4. split (re-attacks / pitch changes)   5. merge fragments   6. prune short / silent
  7. fill gaps with pYIN-only notes   8. confidence   9. monophonic trim
"""

from __future__ import annotations

import numpy as np

from ..notes import make_note, midi_to_hz, overlap_ratio
from .analysis import BassAnalysis

DEFAULT_PARAMS = {
    "range_low": 23,  # B0; replaced by the tuning's lowest open string when known
    "range_high": 67,  # G4
    "odd_ratio_min": 0.16,  # true f0 >= 0.24, sub-octave <= 0.13 on the test set
    "odd_ratio_lower": 0.35,  # stronger evidence needed to move a note DOWN without pYIN support
    "onset_snap_sec": 0.03,
    # Fragment merging is off by default: on the test set it removed real repeated notes whenever
    # the onset detector missed a re-attack (F 0.939 -> 0.911). Set e.g. 0.025 to enable.
    "merge_gap_sec": -1.0,
    "min_note_sec": 0.05,
    "chord_window_sec": 0.03,
    "ghost_start_tol_sec": 0.08,
    "pyin_fill_min_sec": 0.10,
    "pyin_fill_vprob": 0.5,
    # Slap mode (project option): keep notes whose ATTACK only adds the upper octave's series, i.e.
    # pops over a ringing thumb note, instead of treating them as harmonics / octave errors of it.
    # Synthetic slap riff: pops 8/16 -> 16/16 (thumb muted), 0/16 -> 12/16 (thumb ringing). Off by
    # default: on songs without slap it also flips 2-3 % of notes and there is no ground truth to
    # show whether that helps.
    "octave_attack": False,
}

def is_harmonic_interval(semitones: int, max_h: int = 10) -> bool:
    """True if the interval is (within half a semitone) the h-th harmonic, h = 2..max_h."""
    if semitones < 11:
        return False
    h = round(2 ** (semitones / 12))
    return 2 <= h <= max_h and abs(12 * np.log2(h) - semitones) <= 0.5


def _bp_to_notes(events: list[dict]) -> list[dict]:
    out = []
    for e in events:
        n = make_note(e["start"], e["end"], e["pitch"], 0.0, "basic_pitch")
        n["_amp"] = e["amplitude"]
        out.append(n)
    return out


def _is_real_fundamental(an: BassAnalysis, t0: float, t1: float, pitch: int, thr: float) -> tuple[bool, float]:
    spec = an.mean_spectrum(t0, t1)
    r = an.odd_harmonic_ratio(spec, midi_to_hz(pitch))
    return r >= thr, r


def remove_harmonic_ghosts(an: BassAnalysis, notes: list[dict], p: dict) -> list[dict]:
    """Drop notes that are harmonics (octave, 12th, 2 octaves, ...) of a simultaneously sounding note.

    Pair (L lower, H higher, harmonic interval) where H lies mostly inside L and does not start
    before L:
      * L passes the fundamental test  -> H is a ghost. If H starts well after L, the ghost marks
        a re-attack of L (Basic Pitch often merges repeated low notes), so L is split there.
      * L fails (it is a sub-harmonic)  -> L is the ghost.
    """
    notes = sorted(notes, key=lambda n: n["start_sec"])
    tol = p["ghost_start_tol_sec"]
    dead: set[int] = set()
    cuts: dict[int, list[float]] = {}
    for i, a in enumerate(notes):
        for j in range(i + 1, len(notes)):
            b = notes[j]
            if b["start_sec"] > a["end_sec"]:
                break
            if i in dead or j in dead:
                continue
            interval = abs(a["midi_pitch"] - b["midi_pitch"])
            if not is_harmonic_interval(interval):
                continue
            lo, hi = (i, j) if a["midi_pitch"] < b["midi_pitch"] else (j, i)
            L, H = notes[lo], notes[hi]
            if H["start_sec"] < L["start_sec"] - tol:
                continue  # the higher note was already sounding: a real note, not a harmonic
            ov = min(L["end_sec"], H["end_sec"]) - max(L["start_sec"], H["start_sec"])
            if ov / max(H["end_sec"] - H["start_sec"], 1e-6) < 0.5:
                continue
            t0, t1 = max(L["start_sec"], H["start_sec"]), min(L["end_sec"], H["end_sec"])
            real, _ = _is_real_fundamental(an, t0, t1, L["midi_pitch"], p["odd_ratio_min"])
            if real:
                if p["octave_attack"] and interval == 12 and H["start_sec"] > L["start_sec"] + tol \
                        and an.attack_is_octave_up(H["start_sec"], L["midi_pitch"]):
                    # a new note an octave up over the ringing low note (slap pop, octave riff),
                    # not a harmonic of it: keep it (the monophonic trim ends L there)
                    H["flags"] = sorted(set(H["flags"]) | {"octave_attack"})
                    continue
                dead.add(hi)
                L["_amp"] = max(L.get("_amp", 0), H.get("_amp", 0))
                if H["start_sec"] > L["start_sec"] + tol:
                    win = _snap_window(L["midi_pitch"], p["onset_snap_sec"])
                    on = an.onset_near(H["start_sec"], win)
                    if on is not None and L["start_sec"] + p["min_note_sec"] < on < L["end_sec"] - p["min_note_sec"]:
                        cuts.setdefault(lo, []).append(on)
                else:
                    L["start_sec"] = min(L["start_sec"], H["start_sec"])
            elif abs(H["start_sec"] - L["start_sec"]) <= tol:
                dead.add(lo)
                H["_amp"] = max(L.get("_amp", 0), H.get("_amp", 0))
    out = []
    for k, n in enumerate(notes):
        if k in dead:
            continue
        if k in cuts:
            bounds = [n["start_sec"], *sorted(set(cuts[k])), n["end_sec"]]
            for m in range(len(bounds) - 1):
                if bounds[m + 1] - bounds[m] >= 0.02:
                    out.append(dict(n, start_sec=bounds[m], end_sec=bounds[m + 1],
                                    flags=sorted(set(n["flags"]) | ({"split_reattack"} if m else set()))))
        else:
            out.append(n)
    return out


def correct_octaves(an: BassAnalysis, notes: list[dict], p: dict) -> list[dict]:
    """Fix octave errors using pYIN and the odd-harmonic test.

    * pYIN confidently agrees with the detected pitch -> keep it.
    * pYIN confidently says another octave -> the lower of the two wins if it passes the test.
    * no usable pYIN -> lower only on strong evidence; raise only if the detected pitch fails.
    Out-of-range pitches (below the lowest open string) are always moved up if possible.
    """
    lo_r, hi_r, thr = p["range_low"], p["range_high"], p["odd_ratio_min"]
    for n in notes:
        orig = n["midi_pitch"]
        py_pitch, vfrac, _ = an.pyin_stats(n["start_sec"], n["end_sec"])
        confident = py_pitch is not None and vfrac > 0.6
        if confident and abs(py_pitch - orig) <= 0.5 and lo_r <= orig <= hi_r:
            continue
        cands = [c for c in (orig - 24, orig - 12, orig, orig + 12, orig + 24) if lo_r <= c <= hi_r]
        if not cands:
            continue
        spec = an.mean_spectrum(n["start_sec"], n["end_sec"])
        ratio = {c: an.odd_harmonic_ratio(spec, midi_to_hz(c)) for c in cands}
        choice = orig if orig in cands else None
        py_r = int(round(py_pitch)) if confident else None
        if py_r is not None and py_r in cands and (py_r - orig) % 12 == 0 and py_r != orig:
            low, high = min(py_r, orig), max(py_r, orig)
            choice = low if ratio[low] >= thr else high
        elif choice is None:
            passing = [c for c in cands if ratio[c] >= thr]
            choice = passing[0] if passing else cands[0]
        else:
            lower = [c for c in cands if c < orig and ratio[c] >= p["odd_ratio_lower"]]
            if lower:
                choice = lower[0]
            elif ratio[orig] < thr / 2:
                higher = [c for c in cands if c > orig and ratio[c] >= thr]
                if higher:
                    choice = higher[0]
        if p["octave_attack"] and choice != orig and orig - choice in (12, 24):
            # If the attack added only the upper octave's series, the note is higher than
            # ``choice`` (e.g. a slap pop while the thumb note an octave below still rings). The
            # test says "at least one octave up", so a two-octave move is checked step by step.
            up = choice
            while up < orig and ("octave_attack" in n["flags"] and up == choice
                                 or an.attack_is_octave_up(n["start_sec"], up)):
                up += 12
            if up != choice:
                n["flags"] = sorted(set(n["flags"]) | {"octave_attack"})
                choice = up
                if choice == orig:
                    continue
        if choice != orig:
            n["midi_pitch"] = choice
            n["flags"] = sorted(set(n["flags"]) | {"octave_fixed"})
    return notes


def _snap_window(pitch: int, base: float) -> float:
    # Basic Pitch start times get less precise for very low notes (long CQT windows):
    # measured SD 27 ms for E1-G#1 vs 8 ms for the onset detector.
    if pitch < 33:
        return max(base, 0.09)
    if pitch < 38:
        return max(base, 0.05)
    return base


def snap_onsets(an: BassAnalysis, notes: list[dict], p: dict) -> list[dict]:
    """Move note starts to the nearest strong attack not already used by the previous note."""
    notes = sorted(notes, key=lambda n: n["start_sec"])
    prev_start = -1.0
    for n in notes:
        tol = _snap_window(n["midi_pitch"], p["onset_snap_sec"])
        cands = [t for t in an.onsets_between(n["start_sec"] - tol, n["start_sec"] + tol)
                 if t > prev_start + 0.03 and t < n["end_sec"] - 0.02 and an.onset_strength_at(t) >= 0.15]
        if cands:
            n["start_sec"] = float(min(cands, key=lambda t: abs(t - n["start_sec"])))
        prev_start = n["start_sec"]
    return notes


def split_notes(an: BassAnalysis, notes: list[dict], p: dict) -> list[dict]:
    out = []
    for n in notes:
        pieces = [n]
        # (a) pitch change inside the note (pYIN stable >= 60 ms, >= 1 semitone away)
        sl = an.frame_range(n["start_sec"] + 0.04, n["end_sec"] - 0.02)
        m = an.f0_midi[sl]
        if len(m) >= 10:
            q = np.where(np.isnan(m), -999, np.round(m)).astype(int)
            run = 0
            for k in range(len(q)):
                if q[k] != -999 and abs(q[k] - n["midi_pitch"]) >= 1 and abs(q[k] - n["midi_pitch"]) != 12:
                    run = run + 1 if k > 0 and q[k] == q[k - 1] else 1
                    if run >= 5:
                        t_split = float(an.time(sl.start + k - run + 1))
                        new_pitch = int(q[k])
                        if t_split - n["start_sec"] >= p["min_note_sec"] and n["end_sec"] - t_split >= p["min_note_sec"]:
                            a = dict(n, end_sec=t_split)
                            b = dict(n, start_sec=t_split, midi_pitch=new_pitch,
                                     flags=sorted(set(n["flags"]) | {"split_pitch"}))
                            pieces = [a, b]
                        break
                else:
                    run = 0
        # (b) strong re-attack on the same pitch (Basic Pitch sometimes merges repeated notes)
        final = []
        for piece in pieces:
            cuts = []
            for t in an.onsets_between(piece["start_sec"] + p["min_note_sec"], piece["end_sec"] - p["min_note_sec"]):
                if an.onset_strength_at(t) < 0.2:
                    continue
                f = an.frame(t)
                before = an.rms_fast_db[max(0, f - 3):f + 1].min()
                after = an.rms_fast_db[f:f + 4].max()
                if after - before >= 3.0:
                    cuts.append(float(t))
            if not cuts:
                final.append(piece)
                continue
            bounds = [piece["start_sec"], *cuts, piece["end_sec"]]
            for k in range(len(bounds) - 1):
                final.append(dict(piece, start_sec=bounds[k], end_sec=bounds[k + 1],
                                  flags=sorted(set(piece["flags"]) | ({"split_reattack"} if k else set()))))
        out.extend(final)
    return out


def merge_fragments(an: BassAnalysis, notes: list[dict], p: dict) -> list[dict]:
    """Join consecutive same-pitch pieces that belong to ONE attack.

    Basic Pitch's own note boundaries usually mark real re-attacks, so pieces are joined only when
    the onset detector sees at most one attack around the two starts (fragments of a sustained
    note, or the short pre-echo Basic Pitch emits 60-90 ms before very low notes).
    """
    notes = sorted(notes, key=lambda n: (n["start_sec"], n["midi_pitch"]))
    out: list[dict] = []
    for n in notes:
        prev = out[-1] if out and out[-1]["midi_pitch"] == n["midi_pitch"] else None
        if prev is not None and n["start_sec"] - prev["end_sec"] < p["merge_gap_sec"]:
            win = _snap_window(n["midi_pitch"], p["onset_snap_sec"])
            attacks = [t for t in an.onsets_between(prev["start_sec"] - win, n["start_sec"] + win)
                       if an.onset_strength_at(t) >= 0.15]
            if len(attacks) <= 1 and not an.reattack_between(min(prev["end_sec"], n["start_sec"]), n["start_sec"]):
                if attacks:
                    prev["start_sec"] = float(attacks[0])
                prev["end_sec"] = max(prev["end_sec"], n["end_sec"])
                prev["_amp"] = max(prev.get("_amp", 0), n.get("_amp", 0))
                continue
        out.append(n)
    return out


def prune(an: BassAnalysis, notes: list[dict], p: dict) -> list[dict]:
    keep = []
    for n in notes:
        dur = n["end_sec"] - n["start_sec"]
        if an.active_fraction(n["start_sec"], n["end_sec"]) < 0.3:
            continue  # silence: never create notes
        if dur < p["min_note_sec"] and n.get("_amp", 0) < 0.4:
            continue
        keep.append(n)
    return keep


def fill_from_pyin(an: BassAnalysis, notes: list[dict], pyin_notes: list[dict], p: dict) -> list[dict]:
    added = []
    for q in pyin_notes:
        if q["end_sec"] - q["start_sec"] < p["pyin_fill_min_sec"]:
            continue
        _, vfrac, vprob = an.pyin_stats(q["start_sec"], q["end_sec"])
        if vprob < p["pyin_fill_vprob"] or vfrac < 0.7:
            continue
        if any(overlap_ratio(q, n) > 0.2 for n in notes):
            continue
        if not (p["range_low"] <= q["midi_pitch"] <= p["range_high"]):
            continue
        m = dict(q, source="pyin", flags=sorted(set(q["flags"]) | {"pyin_only"}))
        m["_amp"] = 0.0
        added.append(m)
    return notes + added


def score_confidence(an: BassAnalysis, notes: list[dict], p: dict) -> list[dict]:
    for n in notes:
        amp = min(1.0, n.get("_amp", 0.0) / 0.6)
        py_pitch, vfrac, vprob = an.pyin_stats(n["start_sec"], n["end_sec"])
        flags = set(n["flags"])
        if py_pitch is None:
            agree = 0.0
        else:
            d = abs(py_pitch - n["midi_pitch"])
            agree = vfrac * (1.0 if d <= 0.5 else 0.5 if d <= 1.0 else 0.0)
            if d > 1.0 and vfrac > 0.5:
                flags.add("pitch_disagree")
        onset = 1.0 if an.onset_near(n["start_sec"], p["onset_snap_sec"]) is not None else 0.0
        if onset == 0.0:
            # soft attack after a rest is still a plausible note start
            f = an.frame(n["start_sec"])
            if not an.active[max(0, f - 5):max(1, f - 1)].any():
                onset = 0.5
        if n["source"] == "pyin":
            conf = 0.6 * vprob * vfrac + 0.2 * onset
        else:
            conf = 0.45 * amp + 0.35 * agree + 0.20 * onset
        if "octave_fixed" in flags:
            conf -= 0.1
        if "poly" in flags:
            conf *= 0.8
        n["confidence"] = round(float(np.clip(conf, 0.0, 1.0)), 3)
        n["flags"] = sorted(flags)
    return notes


def drop_octave_twins(notes: list[dict], p: dict) -> list[dict]:
    """Slap mode: when a note kept by the attack test starts together with a note an octave below,
    that lower note is a false re-attack (the pop's loudness rise split the thumb note)."""
    if not p["octave_attack"]:
        return notes
    kept = [n for n in notes if "octave_attack" in n["flags"]]
    drop = set()
    for k in kept:
        for i, n in enumerate(notes):
            if n is not k and k["midi_pitch"] - n["midi_pitch"] in (12, 24) \
                    and abs(n["start_sec"] - k["start_sec"]) < p["chord_window_sec"] and "octave_attack" not in n["flags"]:
                drop.add(i)
    return [n for i, n in enumerate(notes) if i not in drop]


def monophonic_trim(notes: list[dict], p: dict) -> list[dict]:
    notes = sorted(notes, key=lambda n: (n["start_sec"], n["midi_pitch"]))
    deduped: list[dict] = []
    for n in notes:
        d = deduped[-1] if deduped else None
        if d and d["midi_pitch"] == n["midi_pitch"] and n["start_sec"] - d["start_sec"] < p["chord_window_sec"]:
            d["end_sec"] = max(d["end_sec"], n["end_sec"])
            d["_amp"] = max(d.get("_amp", 0), n.get("_amp", 0))
            continue
        deduped.append(n)
    notes = deduped
    for i in range(len(notes) - 1):
        a, b = notes[i], notes[i + 1]
        if b["start_sec"] < a["end_sec"]:
            if b["start_sec"] - a["start_sec"] > p["chord_window_sec"]:
                a["end_sec"] = max(a["start_sec"] + 0.02, b["start_sec"])
            else:
                for n in (a, b):
                    n["flags"] = sorted(set(n["flags"]) | {"poly"})
    return notes


def fuse(an: BassAnalysis, bp_events: list[dict], pyin_notes: list[dict], params: dict | None = None) -> list[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    notes = _bp_to_notes(bp_events)
    notes = remove_harmonic_ghosts(an, notes, p)
    notes = correct_octaves(an, notes, p)
    notes = merge_fragments(an, notes, p)
    notes = split_notes(an, notes, p)
    notes = snap_onsets(an, notes, p)
    notes = prune(an, notes, p)
    notes = fill_from_pyin(an, notes, pyin_notes, p)
    notes = drop_octave_twins(notes, p)
    notes = monophonic_trim(notes, p)
    notes = score_confidence(an, notes, p)
    for n in notes:
        n.pop("_amp", None)
        if n["source"] == "basic_pitch" and set(n["flags"]) & {"octave_fixed", "split_pitch", "split_reattack"}:
            n["source"] = "fused"
        n["start_sec"] = round(n["start_sec"], 4)
        n["end_sec"] = round(n["end_sec"], 4)
    return notes

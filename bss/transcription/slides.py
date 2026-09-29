"""Slide / glissando detection on the pYIN pitch contour (runs after every transcriber).

Note-based transcribers expect steady pitches. A slide moves the pitch continuously, so they
either chop it into short notes on the passing pitches (e.g. a slide G2 -> A1 came out as G2, a
phantom C2 and A1) or start the target note early at a wrong pitch.

A glide is a stretch where the contour keeps moving in one direction (>= ``min_semitones`` at
>= ``min_rate`` semitones/s, for ``min_sec``..``max_sec``) without being re-plucked on the way.
A run of separately plucked notes (chromatic run) also moves in one direction, but every step
starts with a new attack; those are left alone. So are instant pitch changes (hammer-on / pull-off,
new note), which happen in one or two frames, and vibrato, which is smaller than a semitone.

For each glide:
  * passing notes (starting inside the glide, pitch between its ends) are removed;
  * origin (note sounding when it starts) and destination (next note, near the pitch where it
    ends) are joined:
      reached without a new attack  -> legato slide    (destination technique ``slide``)
      reached, then plucked          -> shift slide     (``slide_shift``)
      not reached (or no next note)  -> slide out of the origin (``slide_out_down`` / ``_up``)
  * no origin (the glide starts the note) -> slide into the destination (``slide_in_below`` /
    ``_above``) from the attack where the glide begins.

Techniques follow the notes' convention: ``slide`` / ``slide_shift`` describe how the note is
reached from the previous note; ``slide_in_*`` / ``slide_out_*`` belong to the note itself.
"""

from __future__ import annotations

import numpy as np

from .analysis import BassAnalysis

DEFAULT_PARAMS = {
    "min_semitones": 1.8,
    "min_rate": 5.0,  # semitones per second; slower movement is drift, not a slide
    # pYIN's 93 ms window smears ANY pitch change (new note, hammer-on) into a 60-150 ms ramp.
    # Such ramps mix two pitches, so pYIN is unsure (voiced prob. ~0.01-0.1); a real slide is one
    # pitch at a time and stays voiced. Shorter slides cannot be told apart and are left as notes.
    "min_sec": 0.12,
    "min_vprob_median": 0.25,
    "min_vprob_p25": 0.12,
    "end_margin_sec": 0.06,  # plucks this close to either end belong to the origin / the target
    "max_sec": 1.0,
    "max_step_share": 0.5,  # one frame-to-frame jump may not carry more than this share of the move
    "big_step": 1.5,  # semitones in one frame: a jump to another note, not part of a glide
    "min_voiced": 0.6,
    "reattack_db": 2.0,  # loudness rise that marks a new pluck
    "reach_semitones": 1.0,  # glide end this close to the destination = it arrived there
    "connect_semitones": 3.0,  # farther than this: the next note is not the slide's target
    "min_origin_sec": 0.035,  # a note held shorter than this before the glide is its attack
}

SLIDE_TECHNIQUES = ("slide", "slide_shift", "slide_in_below", "slide_in_above", "slide_out_down", "slide_out_up")


def _smooth(f: np.ndarray) -> np.ndarray:
    """NaN-aware 3-frame median."""
    out = f.copy()
    for i in range(1, len(f) - 1):
        if np.isnan(f[i]):
            continue
        w = f[i - 1:i + 2]
        w = w[~np.isnan(w)]
        if len(w) >= 2:
            out[i] = float(np.median(w))
    return out


def _reattacks(an: BassAnalysis, a: int, b: int, rise_db: float) -> list[int]:
    """Frames in [a, b) where the loudness jumps up (a new pluck, not the pitch moving)."""
    out = []
    for f in range(max(a, 3), min(b, an.n_frames - 4)):
        e = an.onset_env
        if e[f] < 0.25 or e[f] < e[f - 1] or e[f] < e[f + 1]:
            continue
        before = float(np.min(an.rms_fast_db[f - 3:f + 1]))
        after = float(np.max(an.rms_fast_db[f:f + 4]))
        if after - before >= rise_db:
            out.append(f)
    return out


def _glide_from_run(an: BassAnalysis, f: np.ndarray, a: int, b: int, p: dict) -> dict | None:
    seg = f[a:b + 1]
    valid = ~np.isnan(seg)
    if valid.sum() < 3 or valid.mean() < p["min_voiced"]:
        return None
    vals = seg[valid]
    fr = a + np.flatnonzero(valid)
    # trim the steady ends (the held note before / after the glide, within 0.3 semitone)
    lo, hi = 0, len(vals) - 1
    first, final = float(np.median(vals[:3])), float(np.median(vals[-3:]))
    while lo < hi - 1 and abs(vals[lo + 1] - first) <= 0.3:
        lo += 1
    while hi > lo + 1 and abs(vals[hi - 1] - final) <= 0.3:
        hi -= 1
    # A big jump near either end is a new note (e.g. slide down, then pluck another note): the
    # glide stops there. A big jump in the middle means it was not one glide.
    while hi - lo >= 2:
        steps = np.abs(np.diff(vals[lo:hi + 1]))
        big = np.flatnonzero(steps > p["big_step"])
        if not big.size:
            break
        if big[-1] >= 0.6 * len(steps):
            hi = lo + int(big[-1])
        elif big[0] <= 0.4 * len(steps):
            lo = lo + int(big[0]) + 1
        else:
            return None
    if hi - lo < 2:
        return None
    p0, p1 = float(vals[lo]), float(vals[hi])
    move = p1 - p0
    t0, t1 = float(an.time(fr[lo])), float(an.time(fr[hi]))
    dur = t1 - t0
    if abs(move) < p["min_semitones"] or not (p["min_sec"] <= dur <= p["max_sec"]):
        return None
    if abs(move) / max(dur, 1e-6) < p["min_rate"]:
        return None
    if np.abs(np.diff(vals[lo:hi + 1])).max() > p["max_step_share"] * abs(move):
        return None  # mostly one jump (hammer-on / new note), not a glide
    vp = an.voiced_prob[int(fr[lo]):int(fr[hi]) + 1]
    if float(np.median(vp)) < p["min_vprob_median"] or float(np.percentile(vp, 25)) < p["min_vprob_p25"]:
        return None  # two pitches in the window: a smeared note change, not a slide
    # A run of plucked notes re-attacks on the way; a slide does not. The pluck of the origin note
    # (at the start) and of a shift slide's target (at the end) are allowed.
    m = int(round(p["end_margin_sec"] * an.sr / an.hop))
    if _reattacks(an, int(fr[lo]) + m, int(fr[hi]) - m, p["reattack_db"]):
        return None
    return {"t0": t0, "t1": t1, "p0": p0, "p1": p1, "dir": 1 if move > 0 else -1,
            "rate": round(abs(move) / dur, 1)}


def find_glides(an: BassAnalysis, params: dict | None = None) -> list[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    f = _smooth(np.asarray(an.f0_midi, dtype=np.float64))
    n = len(f)
    k = 2  # slope over 4 frames (46 ms): smooths pYIN's quarter-tone steps
    slope = np.full(n, np.nan)
    if n > 2 * k:
        slope[k:n - k] = (f[2 * k:] - f[:-2 * k]) / (2 * k)
    thr = p["min_rate"] * an.hop / an.sr
    sign = np.where(np.isnan(slope), 0, np.sign(slope) * (np.abs(slope) >= thr)).astype(int)
    glides = []
    i = 0
    while i < n:
        s = sign[i]
        if s == 0:
            i += 1
            continue
        last, gap, j = i, 0, i
        while j + 1 < n:
            j += 1
            if sign[j] == s:
                last, gap = j, 0
            elif sign[j] == -s:
                break
            else:
                gap += 1
                if gap > 2:
                    break
        g = _glide_from_run(an, f, max(0, i - k), min(n - 1, last + k), p)
        if g:
            glides.append(g)
        i = last + 1
    return glides


def _reattack_near(an: BassAnalysis, t: float, rise_db: float, win: float = 0.06) -> bool:
    a, b = an.frame(t - win), an.frame(t + win)
    return bool(_reattacks(an, a, b + 1, rise_db))


def apply(an: BassAnalysis, notes: list[dict], params: dict | None = None) -> tuple[list[dict], dict]:
    """Clean up notes around glides and mark slide techniques. Returns (notes, stats)."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    glides = find_glides(an, p)
    notes = sorted(notes, key=lambda n: (n["start_sec"], n["midi_pitch"]))
    removed: set[int] = set()
    stats = {"glides": len(glides), "slide": 0, "slide_shift": 0, "slide_in": 0, "slide_out": 0, "removed": 0}

    def mark(n: dict, technique: str | None) -> None:
        if technique and not n.get("technique"):
            n["technique"] = technique
        n["flags"] = sorted(set(n.get("flags") or []) | {"slide_detected"})

    for g in glides:
        t0, t1, p0, p1 = g["t0"], g["t1"], g["p0"], g["p1"]
        lo, hi = min(p0, p1) - 1.0, max(p0, p1) + 1.0
        alive = [(i, x) for i, x in enumerate(notes) if i not in removed]
        # origin: sounding when the glide starts, at its starting pitch
        origin = None
        for i, x in alive:
            if x["start_sec"] <= t0 + 0.03 and x["end_sec"] >= t0 - 0.08 and abs(x["midi_pitch"] - p0) <= 1.0:
                origin = (i, x)
        attack_note = None
        if origin is not None and t0 - origin[1]["start_sec"] < p["min_origin_sec"]:
            # not held before moving: that "note" is the attack of a slide into the next note
            attack_note, origin = origin, None
        oi = origin[0] if origin is not None else None
        # passing notes: start inside the glide, pitch on the way, gone by its end
        passing = [(i, x) for i, x in alive
                   if i != oi and t0 + 0.02 < x["start_sec"] < t1 - 0.01 and lo <= x["midi_pitch"] <= hi
                   and x["end_sec"] <= t1 + 0.12]
        if attack_note is not None:
            passing.append(attack_note)
        skip = {i for i, _ in passing} | ({oi} if oi is not None else set())
        # destination: the next note, near where the glide ends, in the glide's direction
        dest = None
        for i, x in alive:
            if i in skip:
                continue
            if t1 - 0.12 <= x["start_sec"] <= t1 + 0.15 and abs(x["midi_pitch"] - p1) <= p["connect_semitones"] \
                    and (x["midi_pitch"] - p0) * g["dir"] > 0:
                dest = (i, x)
                break
        if origin is None and dest is None:
            continue
        attack = attack_note[1]["start_sec"] if attack_note is not None else None
        removed |= {i for i, _ in passing}
        stats["removed"] += len(passing)
        if origin is not None and dest is not None:
            o, d = origin[1], dest[1]
            reached = abs(p1 - d["midi_pitch"]) <= p["reach_semitones"]
            plucked = _reattack_near(an, d["start_sec"], p["reattack_db"])
            o["end_sec"] = max(o["end_sec"], min(d["start_sec"], t1))
            if reached and not plucked:
                o["end_sec"] = max(o["end_sec"], d["start_sec"])
                mark(o, None)
                mark(d, "slide")
                stats["slide"] += 1
            elif reached:
                mark(o, None)
                mark(d, "slide_shift")
                stats["slide_shift"] += 1
            else:
                mark(o, "slide_out_up" if g["dir"] > 0 else "slide_out_down")
                stats["slide_out"] += 1
        elif origin is not None:
            o = origin[1]
            o["end_sec"] = max(o["end_sec"], t1)
            mark(o, "slide_out_up" if g["dir"] > 0 else "slide_out_down")
            stats["slide_out"] += 1
        else:
            d = dest[1]
            # the glide itself is the start of the note: begin at its attack
            if attack is None:
                attack = an.onset_near(t0, 0.06)
            d["start_sec"] = min(d["start_sec"], attack if attack is not None else t0)
            mark(d, "slide_in_below" if g["dir"] > 0 else "slide_in_above")
            stats["slide_in"] += 1
    out = sorted((x for i, x in enumerate(notes) if i not in removed), key=lambda n: (n["start_sec"], n["midi_pitch"]))
    # keep notes monophonic after the extensions above
    for a, b in zip(out, out[1:]):
        if a["end_sec"] > b["start_sec"] > a["start_sec"]:
            a["end_sec"] = b["start_sec"]
    return out, stats

"""Monophonic notes from the pYIN f0 track, segmented by onsets and stable pitch changes."""

from __future__ import annotations

import numpy as np

from ..notes import make_note
from .analysis import BassAnalysis

DEFAULT_PARAMS = {
    "voiced_prob_min": 0.25,
    "min_note_sec": 0.06,
    "pitch_change_frames": 3,
    "median_frames": 5,
}


def _nan_median_filter(x: np.ndarray, size: int) -> np.ndarray:
    if size <= 1 or len(x) == 0:
        return x.copy()
    half = size // 2
    padded = np.pad(x, half, mode="constant", constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(padded, size)
    with np.errstate(all="ignore"):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            out = np.nanmedian(windows, axis=1)
    out[np.isnan(x)] = np.nan
    return out


def segment(an: BassAnalysis, params: dict | None = None) -> list[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    midi = an.f0_midi.copy()
    midi[an.voiced_prob < p["voiced_prob_min"]] = np.nan
    midi = _nan_median_filter(midi, p["median_frames"])
    q = np.where(np.isnan(midi), -1, np.round(midi)).astype(int)
    onset_frames = set(int(round(t * an.sr / an.hop)) for t in an.onset_times)
    min_frames = max(1, int(round(p["min_note_sec"] * an.sr / an.hop)))
    k = p["pitch_change_frames"]

    segments: list[tuple[int, int, int]] = []  # (start_frame, end_frame_exclusive, pitch)
    n = len(q)
    i = 0
    while i < n:
        if q[i] < 0:
            i += 1
            continue
        start, pitch = i, q[i]
        j = i + 1
        while j < n and q[j] >= 0:
            if j - start >= min_frames and (j in onset_frames or j - 1 in onset_frames):
                break  # re-attack
            if q[j] != pitch and np.all(q[j:j + k] == q[j]) and j + k <= n:
                break  # stable pitch change
            j += 1
        seg_pitch = int(np.bincount(q[start:j][q[start:j] >= 0]).argmax())
        segments.append((start, j, seg_pitch))
        i = j

    notes = []
    for s, e, pitch in segments:
        if e - s < min_frames:
            continue
        t0, t1 = float(an.time(s)), float(an.time(e))
        # pYIN reports voicing late (long analysis window): pull the start back to the attack.
        on = an.onsets_between(t0 - 0.08, t0 + 0.02)
        if len(on):
            t0 = float(on[-1]) if on[-1] <= t0 else float(on[0])
        vp = float(np.mean(an.voiced_prob[s:e]))
        conf = 0.8 * vp * min(1.0, (t1 - t0) / 0.15) + 0.2 * (1.0 if an.onset_near(t0, 0.04) is not None else 0.0)
        notes.append(make_note(t0, t1, pitch, conf, "pyin"))
    return notes

"""Bass transcription: interchangeable transcribers producing the common note format.

Registered transcribers:
  * ``basic_pitch`` – Basic Pitch events only (harmonic ghosts / silence removed)
  * ``pyin``        – pYIN f0 segmented by onsets
  * ``fused``       – Basic Pitch corrected with pYIN + onsets (default)

To add a model, implement ``Transcriber.transcribe`` and register it in ``REGISTRY``.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Callable

import numpy as np

from ..notes import assign_ids
from . import basic_pitch_tr, fused, pyin_tr, slides
from .analysis import SR, BassAnalysis, analyze

Progress = Callable[[float, str], None]


class Transcriber(ABC):
    name: str = ""

    @abstractmethod
    def transcribe(self, an: BassAnalysis, params: dict, progress: Progress) -> list[dict]:
        ...

    def info(self) -> dict:
        return {"name": self.name}


class PyinTranscriber(Transcriber):
    name = "pyin"

    def transcribe(self, an, params, progress):
        notes = pyin_tr.segment(an, params.get("pyin"))
        lo, hi = params.get("range_low", 23), params.get("range_high", 67)
        return [n for n in notes if lo <= n["midi_pitch"] <= hi]


class BasicPitchTranscriber(Transcriber):
    name = "basic_pitch"

    def transcribe(self, an, params, progress):
        progress(0.6, "Basic Pitch 推論中")
        events = basic_pitch_tr.run(an.y, an.sr, params.get("basic_pitch"))
        p = {**fused.DEFAULT_PARAMS, **params.get("fused", {}), **_range(params)}
        notes = fused._bp_to_notes(events)
        notes = fused.remove_harmonic_ghosts(an, notes, p)
        notes = fused.prune(an, notes, p)
        notes = fused.monophonic_trim(notes, p)
        notes = fused.score_confidence(an, notes, p)
        for n in notes:
            n.pop("_amp", None)
        return notes

    def info(self):
        return {"name": self.name, "basic_pitch": basic_pitch_tr.version()}


class FusedTranscriber(Transcriber):
    name = "fused"

    def transcribe(self, an, params, progress):
        progress(0.6, "Basic Pitch 推論中")
        events = basic_pitch_tr.run(an.y, an.sr, params.get("basic_pitch"))
        progress(0.85, "pYIN・onset で補正中")
        pyin_notes = pyin_tr.segment(an, params.get("pyin"))
        return fused.fuse(an, events, pyin_notes, {**params.get("fused", {}), **_range(params)})

    def info(self):
        return {"name": self.name, "basic_pitch": basic_pitch_tr.version()}


def _range(params: dict) -> dict:
    out = {}
    if "range_low" in params:
        out["range_low"] = params["range_low"]
    if "range_high" in params:
        out["range_high"] = params["range_high"]
    return out


REGISTRY: dict[str, type[Transcriber]] = {
    "fused": FusedTranscriber,
    "basic_pitch": BasicPitchTranscriber,
    "pyin": PyinTranscriber,
}


def available() -> dict[str, bool]:
    bp = basic_pitch_tr.available()
    return {"fused": bp, "basic_pitch": bp, "pyin": True}


def prepare_bass(stereo: np.ndarray, sr: int) -> np.ndarray:
    """Stereo bass stem -> mono float32 at 22.05 kHz."""
    import soxr

    mono = stereo.mean(axis=0) if stereo.ndim == 2 else stereo
    if sr != SR:
        mono = soxr.resample(mono, sr, SR, quality="HQ")
    return np.ascontiguousarray(mono, dtype=np.float32)


def transcribe(name: str, bass_mono_22k: np.ndarray, params: dict | None = None,
               progress: Progress | None = None) -> tuple[list[dict], dict]:
    params = params or {}
    progress = progress or (lambda f, m: None)
    if name not in REGISTRY:
        raise ValueError(f"unknown transcriber: {name}")
    if name in ("fused", "basic_pitch") and not basic_pitch_tr.available():
        name = "pyin"  # graceful fallback; reported in info
    t0 = time.perf_counter()
    an = analyze(bass_mono_22k, SR, progress=lambda f, m: progress(f * 0.6, m))
    tr = REGISTRY[name]()
    notes = tr.transcribe(an, params, progress)
    slide_stats = None
    sp = params.get("slides", True)  # False = off, True = defaults, dict = parameters
    if sp is not False:
        notes, slide_stats = slides.apply(an, notes, sp if isinstance(sp, dict) else None)
    notes = assign_ids(notes)
    info = {**tr.info(), "elapsed_sec": round(time.perf_counter() - t0, 2),
            "analysis_timings": {k: round(v, 2) for k, v in an.timings.items()},
            "onsets": int(len(an.onset_times)), "notes": len(notes), "slides": slide_stats}
    return notes, info

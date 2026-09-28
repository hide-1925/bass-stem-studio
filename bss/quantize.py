"""Tempo grid helpers. Quantization is only applied on display/export; stored notes keep raw seconds."""

from __future__ import annotations

from fractions import Fraction

GRIDS = {
    "1/4": Fraction(1, 4),
    "1/8": Fraction(1, 8),
    "1/16": Fraction(1, 16),
    "1/32": Fraction(1, 32),
    "1/8T": Fraction(1, 12),
    "1/16T": Fraction(1, 24),
}


def tempo_valid(tempo: dict | None) -> bool:
    return bool(tempo) and bool(tempo.get("bpm")) and float(tempo["bpm"]) > 0


def seconds_per_beat(tempo: dict) -> float:
    return 60.0 / float(tempo["bpm"])


def grid_seconds(tempo: dict, grid: str) -> float:
    """Length of one grid step. ``bpm`` counts ``beat_unit`` notes (e.g. 6/8 at 120 = 120 eighths/min)."""
    frac = GRIDS.get(grid)
    if frac is None:
        raise ValueError(f"unknown grid {grid}")
    beat_unit = int(tempo.get("beat_unit", 4))
    beats = frac * beat_unit  # grid length measured in beats
    return float(beats) * seconds_per_beat(tempo)


def bar_seconds(tempo: dict) -> float:
    return int(tempo.get("beats_per_bar", 4)) * seconds_per_beat(tempo)


def quantize_time(t: float, tempo: dict, grid: str) -> float:
    step = grid_seconds(tempo, grid)
    off = float(tempo.get("offset_sec", 0.0))
    return off + round((t - off) / step) * step


def quantize_notes(notes: list[dict], tempo: dict, grid: str) -> list[dict]:
    """Return copies with ``q_start_sec`` / ``q_end_sec`` (end >= start + one step)."""
    step = grid_seconds(tempo, grid)
    out = []
    for n in notes:
        qs = quantize_time(n["start_sec"], tempo, grid)
        qe = quantize_time(n["end_sec"], tempo, grid)
        if qe < qs + step - 1e-9:
            qe = qs + step
        out.append({**n, "q_start_sec": round(qs, 6), "q_end_sec": round(qe, 6)})
    return out

"""Exports of the edited notes: MIDI, CSV and a printable TAB PDF."""

from __future__ import annotations

from ..quantize import quantize_notes, tempo_valid


def prepare_notes(notes: list[dict], tempo: dict | None, quantize: bool, grid: str) -> tuple[list[dict], bool]:
    """Sorted copies; with quantization, start/end are replaced by the grid-rounded times."""
    notes = sorted((dict(n) for n in notes), key=lambda n: (n["start_sec"], n["midi_pitch"]))
    if quantize and tempo_valid(tempo):
        q = quantize_notes(notes, tempo, grid)
        for n in q:
            n["raw_start_sec"], n["raw_end_sec"] = n["start_sec"], n["end_sec"]
            n["start_sec"], n["end_sec"] = n["q_start_sec"], n["q_end_sec"]
        return q, True
    return notes, False

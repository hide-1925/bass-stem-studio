"""Note data model helpers (plain dicts, JSON-friendly) and the re-analysis merge policy."""

from __future__ import annotations

import itertools
import math
from typing import Iterable

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLAT_TO_SHARP = {"DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#", "CB": "B", "FB": "E", "E#": "F", "B#": "C"}

NOTE_FIELDS = ("id", "start_sec", "end_sec", "midi_pitch", "confidence", "source", "edited",
               "string", "fret", "technique", "fingering_edited", "flags")

_id_counter = itertools.count()


def note_name(midi: int) -> str:
    return f"{NOTE_NAMES[int(midi) % 12]}{int(midi) // 12 - 1}"


def parse_note_name(name: str) -> int:
    """'E1' -> 28, 'Bb0' -> 22, 'C#2' -> 37. Raises ValueError."""
    s = name.strip().upper().replace("♯", "#").replace("♭", "B")
    if not s:
        raise ValueError("empty note name")
    i = 1
    if len(s) > 1 and s[1] in "#B" and not s[1:].lstrip("-").isdigit():
        i = 2
    pitch_class, octave = s[:i], s[i:]
    pitch_class = _FLAT_TO_SHARP.get(pitch_class, pitch_class)
    if pitch_class not in NOTE_NAMES or not octave.lstrip("-").isdigit():
        raise ValueError(f"invalid note name: {name}")
    pc = NOTE_NAMES.index(pitch_class)
    octave_i = int(octave)
    # Cb/B# wrap octave
    if s[:i] == "CB":
        octave_i -= 1
    elif s[:i] == "B#":
        octave_i += 1
    return (octave_i + 1) * 12 + pc


def make_note(start: float, end: float, pitch: int, confidence: float, source: str, *,
              note_id: str | None = None, flags: Iterable[str] = ()) -> dict:
    return {
        "id": note_id or f"n_{next(_id_counter):06d}",
        "start_sec": round(float(start), 4),
        "end_sec": round(float(end), 4),
        "midi_pitch": int(pitch),
        "confidence": round(float(min(1.0, max(0.0, confidence))), 3),
        "source": source,
        "edited": False,
        "string": None,
        "fret": None,
        "technique": None,
        "fingering_edited": False,
        "flags": sorted(set(flags)),
    }


def normalize_note(d: dict) -> dict:
    """Fill missing fields / coerce types (used when loading user-edited JSON)."""
    n = {
        "id": str(d.get("id") or f"m_{next(_id_counter):06d}"),
        "start_sec": float(d["start_sec"]),
        "end_sec": float(d["end_sec"]),
        "midi_pitch": int(d["midi_pitch"]),
        "confidence": float(d.get("confidence", 1.0)),
        "source": str(d.get("source", "manual")),
        "edited": bool(d.get("edited", False)),
        "string": None if d.get("string") is None else int(d["string"]),
        "fret": None if d.get("fret") is None else int(d["fret"]),
        "technique": d.get("technique") or None,
        "fingering_edited": bool(d.get("fingering_edited", False)),
        "flags": list(d.get("flags") or []),
    }
    if n["end_sec"] <= n["start_sec"]:
        n["end_sec"] = n["start_sec"] + 0.01
    return n


def assign_ids(notes: list[dict], prefix: str = "n") -> list[dict]:
    """Give sequential ids ordered by time (auto notes)."""
    notes.sort(key=lambda n: (n["start_sec"], n["midi_pitch"]))
    for i, n in enumerate(notes):
        n["id"] = f"{prefix}_{i:06d}"
    return notes


def overlap_ratio(a: dict, b: dict) -> float:
    """Overlap length divided by the shorter note's length."""
    ov = min(a["end_sec"], b["end_sec"]) - max(a["start_sec"], b["start_sec"])
    if ov <= 0:
        return 0.0
    shorter = min(a["end_sec"] - a["start_sec"], b["end_sec"] - b["start_sec"])
    return ov / max(shorter, 1e-6)


def is_manual(n: dict) -> bool:
    return bool(n.get("edited") or n.get("fingering_edited") or n.get("source") == "manual")


def matches_suppressed(n: dict, suppressed: list[dict], tol: float = 0.06) -> bool:
    return any(abs(n["start_sec"] - s["start_sec"]) <= tol and n["midi_pitch"] == s["midi_pitch"] for s in suppressed)


def merge_reanalysis(old_notes: list[dict], suppressed: list[dict], new_auto: list[dict],
                     overlap_threshold: float = 0.3) -> tuple[list[dict], dict]:
    """Merge a fresh automatic transcription into the current notes without losing edits.

    - Notes the user touched (``edited`` / ``fingering_edited`` / manual) are kept as-is.
    - Untouched automatic notes are replaced by the new result.
    - New auto notes overlapping a kept manual note, or matching a note the user deleted
      (``suppressed``), are not added.
    """
    kept = [n for n in old_notes if is_manual(n)]
    used_ids = {n["id"] for n in kept}
    added = []
    skipped_overlap = skipped_suppressed = 0
    for n in new_auto:
        if matches_suppressed(n, suppressed):
            skipped_suppressed += 1
            continue
        if any(overlap_ratio(n, k) > overlap_threshold for k in kept):
            skipped_overlap += 1
            continue
        if n["id"] in used_ids:
            n = dict(n, id=f"{n['id']}_r")
        used_ids.add(n["id"])
        added.append(n)
    merged = sorted(kept + added, key=lambda n: (n["start_sec"], n["midi_pitch"]))
    stats = {"kept_manual": len(kept), "added_auto": len(added), "replaced_auto": len(old_notes) - len(kept),
             "skipped_overlap": skipped_overlap, "skipped_suppressed": skipped_suppressed}
    return merged, stats


def hz_to_midi(f: float) -> float:
    return 69.0 + 12.0 * math.log2(f / 440.0)


def midi_to_hz(m: float) -> float:
    return 440.0 * 2.0 ** ((m - 69.0) / 12.0)

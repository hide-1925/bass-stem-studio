"""Note list as CSV (UTF-8 with BOM so Excel opens it correctly)."""

from __future__ import annotations

import csv
import io

from ..notes import note_name

COLUMNS = ["id", "start_sec", "end_sec", "duration_sec", "midi_pitch", "note_name", "confidence", "source",
           "edited", "string", "fret", "technique", "fingering_edited", "flags"]


def to_csv_bytes(notes: list[dict], quantized: bool = False) -> bytes:
    cols = COLUMNS + (["raw_start_sec", "raw_end_sec"] if quantized else [])
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for n in notes:
        row = {
            **n,
            "start_sec": f"{n['start_sec']:.4f}",
            "end_sec": f"{n['end_sec']:.4f}",
            "duration_sec": f"{n['end_sec'] - n['start_sec']:.4f}",
            "note_name": note_name(n["midi_pitch"]),
            "confidence": f"{float(n.get('confidence', 0)):.3f}",
            "edited": int(bool(n.get("edited"))),
            "fingering_edited": int(bool(n.get("fingering_edited"))),
            "string": "" if n.get("string") is None else n["string"],
            "fret": "" if n.get("fret") is None else n["fret"],
            "technique": n.get("technique") or "",
            "flags": " ".join(n.get("flags") or []),
        }
        if quantized:
            row["raw_start_sec"] = f"{n['raw_start_sec']:.4f}"
            row["raw_end_sec"] = f"{n['raw_end_sec']:.4f}"
        w.writerow([row.get(c, "") for c in cols])
    return ("﻿" + buf.getvalue()).encode("utf-8")

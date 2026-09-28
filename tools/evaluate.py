"""Evaluate bass transcription (and TAB) against a reference.

    # bass-only recording
    python tools/evaluate.py --audio bass.wav --reference ref.csv
    # full mix: separate with Demucs first, then transcribe the bass stem
    python tools/evaluate.py --audio mix.wav --reference ref.mid --separate
    # all synthetic test songs (see tools/make_testset.py), both conditions, all transcribers
    python tools/evaluate.py --testsets

Reference: CSV with start_sec,end_sec,midi_pitch[,string,fret] or a MIDI file.
Metrics (mir_eval, onset ±50 ms, pitch ±50 cents, offsets ignored):
  note P/R/F, onset error, pitch accuracy of onset-matched notes, octave errors,
  missed / extra notes, TAB string+fret agreement (when the reference has string/fret).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bss import audio_io, transcription  # noqa: E402
from bss.config import SAMPLE_RATE, WORKSPACE  # noqa: E402
from bss.fingering import FingeringOptions, apply_assignments, optimize, tuning_from_dict  # noqa: E402
from bss.notes import midi_to_hz, note_name  # noqa: E402


def load_reference(path: Path) -> list[dict]:
    if path.suffix.lower() in (".mid", ".midi"):
        import pretty_midi

        pm = pretty_midi.PrettyMIDI(str(path))
        notes = [{"start_sec": n.start, "end_sec": n.end, "midi_pitch": n.pitch, "string": None, "fret": None}
                 for inst in pm.instruments for n in inst.notes]
    else:
        with open(path, encoding="utf-8") as f:
            notes = []
            for row in csv.DictReader(f):
                notes.append({"start_sec": float(row["start_sec"]), "end_sec": float(row["end_sec"]),
                              "midi_pitch": int(row["midi_pitch"]),
                              "string": int(row["string"]) if row.get("string") else None,
                              "fret": int(row["fret"]) if row.get("fret") else None})
    notes.sort(key=lambda n: n["start_sec"])
    for i, n in enumerate(notes):
        n["id"] = f"ref_{i:05d}"
    return notes


def _arrays(notes):
    iv = np.array([[n["start_sec"], max(n["end_sec"], n["start_sec"] + 0.01)] for n in notes]).reshape(-1, 2)
    hz = np.array([midi_to_hz(n["midi_pitch"]) for n in notes])
    return iv, hz


def evaluate(ref: list[dict], est: list[dict], tuning: dict | None = None) -> dict:
    import mir_eval

    ri, rp = _arrays(ref)
    ei, ep = _arrays(est)
    if len(est) == 0:
        return {"n_ref": len(ref), "n_est": 0, "precision": 0, "recall": 0, "f1": 0, "missed": len(ref), "extra": 0}
    p, r, f, _ = mir_eval.transcription.precision_recall_f1_overlap(ri, rp, ei, ep, onset_tolerance=0.05,
                                                                    pitch_tolerance=50.0, offset_ratio=None)
    matched = mir_eval.transcription.match_notes(ri, rp, ei, ep, onset_tolerance=0.05, pitch_tolerance=50.0,
                                                 offset_ratio=None)
    onset_err = [est[j]["start_sec"] - ref[i]["start_sec"] for i, j in matched]
    onset_pairs = mir_eval.transcription.match_note_onsets(ri, ei, onset_tolerance=0.05)
    pitch_ok = sum(1 for i, j in onset_pairs if est[j]["midi_pitch"] == ref[i]["midi_pitch"])
    octave = [(i, j) for i, j in onset_pairs if abs(est[j]["midi_pitch"] - ref[i]["midi_pitch"]) in (12, 24)]
    other_pitch = [(i, j) for i, j in onset_pairs
                   if est[j]["midi_pitch"] != ref[i]["midi_pitch"] and (i, j) not in octave]
    onset_ref = {i for i, _ in onset_pairs}
    onset_est = {j for _, j in onset_pairs}
    out = {
        "n_ref": len(ref), "n_est": len(est),
        "precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3),
        "onset_mae_ms": round(1000 * float(np.mean(np.abs(onset_err))), 1) if onset_err else None,
        "onset_bias_ms": round(1000 * float(np.mean(onset_err)), 1) if onset_err else None,
        "pitch_acc_onset_matched": round(pitch_ok / max(1, len(onset_pairs)), 3),
        "octave_errors": len(octave),
        "other_pitch_errors": len(other_pitch),
        "missed": len(ref) - len(onset_ref),
        "extra": len(est) - len(onset_est),
        "_examples": {
            "missed": [_fmt(ref[i]) for i in range(len(ref)) if i not in onset_ref][:8],
            "extra": [_fmt(est[j]) for j in range(len(est)) if j not in onset_est][:8],
            "octave": [f"{_fmt(ref[i])} -> {note_name(est[j]['midi_pitch'])}" for i, j in octave][:8],
            "pitch": [f"{_fmt(ref[i])} -> {note_name(est[j]['midi_pitch'])}" for i, j in other_pitch][:8],
        },
    }
    if tuning is not None and any(n.get("string") for n in ref):
        tun = tuning_from_dict(tuning)
        est_f = [dict(n, fingering_edited=False) for n in est]
        apply_assignments(est_f, optimize(est_f, tun, FingeringOptions()))
        tab_ok = sum(1 for i, j in matched
                     if (est_f[j]["string"], est_f[j]["fret"]) == (ref[i]["string"], ref[i]["fret"]))
        out["tab_match_on_correct_notes"] = round(tab_ok / max(1, len(matched)), 3)
        # optimizer alone, with perfect pitches
        ref_f = [dict(n, fingering_edited=False) for n in ref]
        apply_assignments(ref_f, optimize(ref_f, tun, FingeringOptions()))
        oracle_ok = sum(1 for a, b in zip(ref, ref_f) if (a["string"], a["fret"]) == (b["string"], b["fret"]))
        out["tab_match_oracle_pitch"] = round(oracle_ok / max(1, len(ref)), 3)
    return out


def _fmt(n):
    return f"{n['start_sec']:.2f}s {note_name(n['midi_pitch'])}"


def separate_bass(mix_path: Path) -> tuple[np.ndarray, dict]:
    from bss.separation import get_separator

    mix = audio_io.load_for_processing(mix_path)
    sep = get_separator({"name": "demucs", "model": "htdemucs_6s", "device": "auto"})
    t = time.perf_counter()
    stems = sep.separate(mix, SAMPLE_RATE, lambda f, m: None, lambda: False)
    return stems["bass"], {"separation_sec": round(time.perf_counter() - t, 1)}


def run_one(audio: Path, ref_path: Path, transcriber: str, separate: bool, bass_cache: dict | None = None) -> dict:
    ref = load_reference(ref_path)
    info = {}
    key = (str(audio), separate)
    if bass_cache is not None and key in bass_cache:
        bass = bass_cache[key]
    elif separate:
        bass, info = separate_bass(audio)
    else:
        bass = audio_io.load_for_processing(audio)
    if bass_cache is not None:
        bass_cache[key] = bass
    y = transcription.prepare_bass(bass, SAMPLE_RATE)
    notes, tinfo = transcription.transcribe(transcriber, y, {"range_low": 28, "range_high": 67})
    res = evaluate(ref, notes, tuning={"strings": [28, 33, 38, 43], "frets": 24})
    res.update({"audio": audio.name, "condition": "mix+separation" if separate else "bass-only",
                "transcriber": transcriber, "transcribe_sec": tinfo["elapsed_sec"], **info})
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", type=Path)
    ap.add_argument("--reference", type=Path)
    ap.add_argument("--separate", action="store_true")
    ap.add_argument("--transcriber", default="fused")
    ap.add_argument("--testsets", action="store_true", help="evaluate all songs in workspace/testsets")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    results = []
    if args.testsets:
        cache: dict = {}
        for d in sorted((WORKSPACE / "testsets").iterdir()):
            if not (d / "reference.csv").exists() or d.name.startswith("long"):
                continue
            for cond, audio, sep in (("bass-only", d / "bass_only.wav", False), ("mix", d / "mix.wav", True)):
                for tr in ("fused", "basic_pitch", "pyin"):
                    r = run_one(audio, d / "reference.csv", tr, sep, cache)
                    r["song"] = d.name
                    results.append(r)
                    print(f"{d.name:14s} {r['condition']:15s} {tr:11s} F={r['f1']:.3f} P={r['precision']:.3f} "
                          f"R={r['recall']:.3f} onset={r.get('onset_mae_ms')}ms pitch={r.get('pitch_acc_onset_matched', 0):.3f} "
                          f"oct={r.get('octave_errors')} miss={r.get('missed')} extra={r.get('extra')} "
                          f"tab={r.get('tab_match_on_correct_notes')}", flush=True)
    else:
        if not args.audio or not args.reference:
            ap.error("--audio and --reference are required (or use --testsets)")
        r = run_one(args.audio, args.reference, args.transcriber, args.separate)
        results.append(r)
        print(json.dumps(r, ensure_ascii=False, indent=1))
    out = args.out or (WORKSPACE / "eval" / f"eval-{time.strftime('%Y%m%d-%H%M%S')}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {out}")


if __name__ == "__main__":
    main()

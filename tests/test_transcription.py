"""Transcription on a short synthetic bass line with known notes (regression guard, not a
statement about real recordings)."""

import numpy as np
import pytest

from bss import transcription
from bss.transcription import basic_pitch_tr

import make_testset as mt
from evaluate import evaluate

SR = 44100


@pytest.fixture(scope="module")
def synthetic():
    beat = 0.6  # 100 BPM
    ref = []
    for b0, blen, st, fr in mt.RIFFS[0] + [(16 + b, l, s, f) for b, l, s, f in mt.RIFFS[2]]:
        s = 0.5 + b0 * beat
        ref.append({"start_sec": s, "end_sec": s + blen * beat * 0.92, "midi_pitch": mt.OPEN[st] + fr,
                    "string": st, "fret": fr, "id": f"r{len(ref)}"})
    total = ref[-1]["end_sec"] + 1.0
    audio = mt.render_bass([(r["start_sec"], r["end_sec"], r["midi_pitch"]) for r in ref], total, "warm")
    y = transcription.prepare_bass(np.stack([audio, audio]) * 0.8, SR)
    return ref, y


def test_pyin_on_clean_bass(synthetic):
    ref, y = synthetic
    notes, info = transcription.transcribe("pyin", y, {"range_low": 28, "range_high": 67})
    r = evaluate(ref, notes)
    assert r["f1"] >= 0.9, r
    assert r["octave_errors"] == 0


@pytest.mark.skipif(not basic_pitch_tr.available(), reason="basic-pitch not installed")
def test_fused_on_clean_bass(synthetic):
    ref, y = synthetic
    notes, info = transcription.transcribe("fused", y, {"range_low": 28, "range_high": 67})
    r = evaluate(ref, notes)
    assert r["recall"] >= 0.9, r
    assert r["f1"] >= 0.8, r
    assert r["onset_mae_ms"] < 20
    assert all(n["source"] in ("basic_pitch", "fused", "pyin") for n in notes)
    assert all(0 <= n["confidence"] <= 1 for n in notes)


def test_silence_produces_no_notes():
    y = np.zeros(22050 * 3, dtype=np.float32)
    y += np.random.default_rng(0).normal(0, 1e-5, y.shape).astype(np.float32)
    notes, _ = transcription.transcribe("pyin", y, {})
    assert notes == []

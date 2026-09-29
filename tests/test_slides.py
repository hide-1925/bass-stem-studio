"""Slide detection on a synthetic bass line with known slides and look-alikes."""

import numpy as np
import pytest

from bss import transcription
from bss.notes import midi_to_hz
from bss.transcription import analysis, basic_pitch_tr, slides

SR = 44100


def tone(path, sr=SR, decay=2.5, vibrato=0.0):
    """One pluck whose pitch follows ``path`` = [(seconds, midi_from, midi_to), ...]."""
    midi = np.concatenate([np.linspace(a, b, int(d * sr), endpoint=False) for d, a, b in path])
    t = np.arange(len(midi)) / sr
    if vibrato:
        midi = midi + vibrato * np.sin(2 * np.pi * 5.0 * t)
    f = midi_to_hz(midi)
    phase = 2 * np.pi * np.cumsum(f) / sr
    y = sum((0.8 ** h) / h * np.sin(h * phase) for h in range(1, 9))
    env = np.minimum(1.0, t / 0.004) * np.exp(-decay * t)
    fade = np.minimum(1.0, (len(t) - np.arange(len(t))) / (0.01 * sr))
    return y * env * fade


def render(events, total):
    out = np.zeros(int(total * SR))
    for start, sig in events:
        i = int(start * SR)
        out[i:i + len(sig)] += sig[: len(out) - i]
    return 0.3 * out / np.max(np.abs(out))


def song():
    ev = []
    # Slides of 0.16 s and more: shorter ones look like any note change to pYIN (see slides.py).
    ev.append((0.5, tone([(0.40, 45, 45), (0.16, 45, 50), (0.50, 50, 50)])))  # A2 -legato slide-> D3
    ev.append((2.2, tone([(0.25, 43, 43), (0.30, 43, 36)])))  # G2, slide out downwards
    ev.append((3.4, tone([(0.20, 38, 43), (0.50, 43, 43)])))  # slide into G2 from below
    for k, m in enumerate([40, 41, 42, 43]):  # chromatic run, every note plucked
        ev.append((4.6 + 0.12 * k, tone([(0.11, m, m)], decay=6)))
    ev.append((5.8, tone([(0.30, 33, 33), (0.30, 36, 36)])))  # hammer-on: instant change, no pluck
    ev.append((6.8, tone([(0.40, 28, 28)])))
    ev.append((7.3, tone([(0.40, 33, 33)])))
    ev.append((8.0, tone([(0.80, 45, 45)], vibrato=0.3)))  # vibrato is not a slide
    return render(ev, 9.5)


@pytest.fixture(scope="module")
def signal():
    x = song()
    return transcription.prepare_bass(np.stack([x, x]), SR)


def test_find_glides(signal):
    an = analysis.analyze(signal)
    gl = slides.find_glides(an)
    ups = [g for g in gl if g["dir"] > 0]
    downs = [g for g in gl if g["dir"] < 0]
    assert any(0.8 < g["t0"] < 1.0 and g["p1"] > 48.5 for g in ups), gl  # A2 -> D3
    assert any(2.4 < g["t0"] < 2.6 and g["p1"] < 39 for g in downs), gl  # G2 slide out
    assert any(3.35 < g["t0"] < 3.55 and g["p1"] > 41.5 for g in ups), gl  # slide in
    # plucked run, hammer-on, separate notes and vibrato are not glides
    assert not any(4.55 < g["t0"] < 5.2 or 5.7 < g["t0"] < 7.9 or g["t0"] > 7.95 for g in gl), gl


@pytest.mark.skipif(not basic_pitch_tr.available(), reason="basic-pitch not installed")
def test_fused_marks_slides_and_drops_passing_notes(signal):
    notes, info = transcription.transcribe("fused", signal, {"range_low": 23, "range_high": 67})
    at = lambda t0, t1: [n for n in notes if t0 <= n["start_sec"] < t1]  # noqa: E731
    # legato slide up: target D3 reached from A2, nothing on the passing pitches
    d3 = [n for n in at(0.85, 1.25) if n["midi_pitch"] == 50]
    assert d3 and d3[0]["technique"] == "slide", at(0.4, 1.6)
    assert not [n for n in at(0.8, 1.06) if 46 <= n["midi_pitch"] <= 49]
    # slide out downwards from G2
    g2 = [n for n in at(2.1, 2.35) if n["midi_pitch"] == 43]
    assert g2 and g2[0]["technique"] == "slide_out_down", at(2.1, 3.0)
    assert not [n for n in at(2.45, 2.9) if 36 <= n["midi_pitch"] <= 42]
    # slide into G2 from below: one note, starting at the attack
    g2b = [n for n in at(3.3, 3.7) if n["midi_pitch"] == 43]
    assert g2b and g2b[0]["technique"] == "slide_in_below" and g2b[0]["start_sec"] < 3.46, at(3.3, 4.2)
    assert not [n for n in at(3.3, 3.7) if 38 <= n["midi_pitch"] <= 42]
    # look-alikes keep their notes and get no slide
    run = at(4.55, 5.15)
    assert [n["midi_pitch"] for n in run] == [40, 41, 42, 43], run
    for n in at(4.5, 9.5):
        assert n.get("technique") not in slides.SLIDE_TECHNIQUES, n
    assert info["slides"]["slide"] >= 1 and info["slides"]["removed"] >= 1


def test_slides_can_be_switched_off(signal):
    notes, info = transcription.transcribe("pyin", signal, {"range_low": 23, "range_high": 67, "slides": False})
    assert info["slides"] is None
    assert not any(n.get("technique") for n in notes)

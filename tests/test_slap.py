"""Slap option: pops an octave above the thumb note must survive the harmonic-ghost and
octave-error corrections (synthetic riff with known notes)."""

import numpy as np
import pytest

from bss import transcription
from bss.notes import midi_to_hz
from bss.transcription import basic_pitch_tr

SR = 44100


def pluck(midi, dur, bright, decay, click, rng):
    t = np.arange(int(dur * SR)) / SR
    f = midi_to_hz(midi)
    y = np.zeros_like(t)
    for h in range(1, 25):
        if f * h > 8000:
            break
        y += h ** -bright * np.sin(2 * np.pi * f * h * t + rng.uniform(0, 6.28)) * np.exp(-decay * (1 + 0.15 * h) * t)
    y *= np.minimum(1, t / 0.002)
    n = int(0.004 * SR)
    y[:n] += click * rng.uniform(-1, 1, n)
    return y * np.minimum(1.0, (len(t) - np.arange(len(t))) / (0.005 * SR))


def riff(ring, bpm=100, bars=4):
    """16ths per beat: thumb, pop an octave up, thumb, rest. ``ring``: the thumb note sounds under the pop."""
    rng = np.random.default_rng(1)
    six = 60 / bpm / 4
    events, ref = [], []
    t = 0.5
    for b in range(bars * 4):
        r = [28, 31, 33, 26][(b // 4) % 4]
        events.append((t, pluck(r, 3 * six if ring else 0.9 * six, 1.0, 5.0, 0.3, rng)))
        events.append((t + six, pluck(r + 12, 0.9 * six, 0.55, 9.0, 0.6, rng)))
        events.append((t + 2 * six, pluck(r, 0.9 * six, 1.0, 5.0, 0.3, rng)))
        ref += [(t, r, "thumb"), (t + six, r + 12, "pop"), (t + 2 * six, r, "thumb")]
        t += 4 * six
    out = np.zeros(int((t + 1) * SR))
    for s, sig in events:
        i = int(s * SR)
        out[i:i + len(sig)] += sig
    x = 0.3 * out / np.abs(out).max()
    return transcription.prepare_bass(np.stack([x, x]), SR), ref


def score(notes, ref):
    hit = {"thumb": 0, "pop": 0}
    for s, m, kind in ref:
        if any(abs(n["start_sec"] - s) < 0.05 and n["midi_pitch"] == m for n in notes):
            hit[kind] += 1
    return hit


@pytest.mark.skipif(not basic_pitch_tr.available(), reason="basic-pitch not installed")
@pytest.mark.parametrize("ring,min_pops", [(False, 15), (True, 10)])
def test_slap_option_keeps_pops(ring, min_pops):
    y, ref = riff(ring)
    base = {"range_low": 23, "range_high": 67}
    off, _ = transcription.transcribe("fused", y, base)
    on, _ = transcription.transcribe("fused", y, {**base, "fused": {"octave_attack": True}})
    s_off, s_on = score(off, ref), score(on, ref)
    assert s_on["pop"] >= min_pops, (s_off, s_on)  # 16 pops in the riff
    assert s_on["pop"] > s_off["pop"] + 4, (s_off, s_on)
    assert s_on["thumb"] >= s_off["thumb"] - 1, (s_off, s_on)  # thumbs are not traded for pops

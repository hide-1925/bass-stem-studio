"""Tempo / downbeat estimation on synthetic songs with a known grid, and the 5-string default."""

import numpy as np
import pytest

from bss import tempo
from bss.fingering import PRESETS, tuning_from_dict
from bss.project import DEFAULT_TUNING

import make_testset as mt


def _song(bpm: float, lead: float, bars: int = 20):
    beat = 60 / bpm
    total = lead + bars * 4 * beat + 1
    d = mt.render_drums(total - lead, bpm)
    drums = np.concatenate([np.zeros(int(lead * mt.SR)), d])[: int(total * mt.SR)]
    roots = [28, 33, 31, 26]  # one chord per bar: the harmony changes on the downbeat
    notes = []
    for b in range(bars):
        for off, ln, iv in [(0, 1, 0), (1.5, .5, 0), (2, 1, 7), (3, .5, 12), (3.5, .5, 7)]:
            s = lead + (b * 4 + off) * beat
            notes.append({"start_sec": s, "end_sec": s + ln * beat * .9, "midi_pitch": roots[b % 4] + iv})
    bass = mt.render_bass([(n["start_sec"], n["end_sec"], n["midi_pitch"]) for n in notes], total, "warm")
    return tempo._mono22(drums, mt.SR), tempo._mono22(bass, mt.SR), notes


@pytest.mark.parametrize("bpm,lead", [(123.4, 0.37), (96.0, 1.1), (78.5, 2.3)])
def test_estimate_bpm_and_downbeat(bpm, lead):
    drums, bass, notes = _song(bpm, lead)
    est = tempo.estimate(drums, bass, notes=notes)
    assert abs(est["bpm"] - bpm) < 0.05, est
    bar = 4 * 60 / bpm
    err = (est["offset_sec"] - lead + bar / 2) % bar - bar / 2  # distance to the true bar line
    assert abs(err) < 0.01, est
    assert est["stable"] and est["source"] == "auto"
    assert -0.1 * 60 / bpm <= est["offset_sec"] < bar  # bar 1 starts near the song start


def test_estimate_rejects_silence():
    z = np.zeros(tempo.SR * 5, dtype=np.float32)
    with pytest.raises(ValueError):
        tempo.estimate(z, z)


def test_five_string_is_default():
    assert DEFAULT_TUNING["strings"] == [23, 28, 33, 38, 43]
    assert PRESETS[0]["strings"] == [23, 28, 33, 38, 43]
    assert tuning_from_dict(None).strings == (23, 28, 33, 38, 43)

import pytest

from bss.fingering import FingeringOptions, apply_assignments, candidates, optimize, tuning_from_dict
from bss.fingering.tuning import PRESETS
from bss.notes import make_note, parse_note_name

STD4 = tuning_from_dict({"strings": [28, 33, 38, 43], "frets": 24})


def seq(pitches, dur=0.25, gap=0.0):
    notes, t = [], 0.0
    for p in pitches:
        notes.append(make_note(t, t + dur, p, 1.0, "manual"))
        t += dur + gap
    return notes


def test_parse_note_names():
    assert parse_note_name("E1") == 28
    assert parse_note_name("B0") == 23
    assert parse_note_name("Bb0") == 22
    assert parse_note_name("C#2") == 37
    assert parse_note_name("Eb1") == 27
    assert parse_note_name("G2") == 43


def test_candidates_standard_tuning():
    # A2 (45) on a 4-string: G string fret 2, D fret 7, A fret 12, E fret 17
    assert sorted(candidates(45, STD4)) == [(1, 2), (2, 7), (3, 12), (4, 17)]
    assert candidates(27, STD4) == []  # below low E
    assert candidates(28, STD4) == [(4, 0)]


def test_every_assignment_is_playable_and_correct():
    notes = seq([28, 31, 33, 36, 38, 40, 43, 45, 47, 50, 55, 67])
    res = optimize(notes, STD4)
    assert not res["unplayable"]
    for n in notes:
        s, f = res["assignments"][n["id"]]
        assert STD4.open_pitch(s) + f == n["midi_pitch"]
        assert 0 <= f <= STD4.frets


def test_prefers_compact_positions():
    # a scale fragment around the 5th position should not jump up the neck
    notes = seq([33, 35, 36, 38, 40, 41, 43])
    res = optimize(notes, STD4, FingeringOptions(preferred_position=5))
    frets = [res["assignments"][n["id"]][1] for n in notes]
    fretted = [f for f in frets if f > 0]
    assert max(fretted) - min(fretted) <= 5


def test_simple_riff_matches_bassist_choice():
    # E open, G (E string 3), A open, C (A string 3)
    notes = seq([28, 31, 33, 36])
    res = optimize(notes, STD4)
    got = [tuple(res["assignments"][n["id"]]) for n in notes]
    assert got == [(4, 0), (4, 3), (3, 0), (3, 3)]


def test_locks_are_respected_and_dropped_when_impossible():
    notes = seq([33, 38, 40])
    notes[1].update(string=3, fret=5, fingering_edited=True)  # D on the A string, locked
    res = optimize(notes, STD4)
    assert res["assignments"][notes[1]["id"]] == [3, 5]
    # new tuning where string 3 cannot play D2 below its open pitch -> lock is dropped
    drop = tuning_from_dict({"strings": [26, 31, 40, 45], "frets": 24})
    notes2 = seq([38])
    notes2[0].update(string=1, fret=0, fingering_edited=True)
    res2 = optimize(notes2, drop)
    assert notes2[0]["id"] in res2["dropped_locks"]
    apply_assignments(notes2, res2)
    assert notes2[0]["fingering_edited"] is False


def test_simultaneous_notes_use_different_strings():
    a = make_note(0.0, 1.0, 40, 1, "manual")  # E2
    b = make_note(0.0, 1.0, 45, 1, "manual")  # A2
    res = optimize([a, b], STD4)
    assert res["assignments"][a["id"]][0] != res["assignments"][b["id"]][0]


def test_out_of_range_marked():
    notes = seq([20, 40])
    res = optimize(notes, STD4)
    apply_assignments(notes, res)
    assert notes[0]["string"] is None and "out_of_range" in notes[0]["flags"]
    assert notes[1]["string"] is not None


@pytest.mark.parametrize("preset", PRESETS)
def test_presets_are_valid(preset):
    t = tuning_from_dict(preset)
    assert list(t.strings) == sorted(t.strings)
    assert t.labels()[0]  # names from string 1


def test_slide_stays_on_one_string_and_avoids_open_strings():
    # G2 slides down to A1 (the case from a real song): without the technique the optimizer would
    # take the open A string; a slide needs both notes on one string and no open string
    notes = seq([43, 33])
    free = optimize(notes, STD4)["assignments"]
    assert free[notes[1]["id"]][1] == 0  # plain notes: open A
    notes[1]["technique"] = "slide_shift"
    a = optimize(notes, STD4)["assignments"]
    (s1, f1), (s2, f2) = a[notes[0]["id"]], a[notes[1]["id"]]
    assert s1 == s2 and f1 > 0 and f2 > 0 and f1 - f2 == 10


def test_five_string_keeps_string_numbers():
    five = tuning_from_dict(next(p for p in PRESETS if p["strings"] == [23, 28, 33, 38, 43]))  # B E A D G
    assert five.open_pitch(1) == 43 and five.open_pitch(4) == 28 and five.open_pitch(5) == 23
    assert candidates(23, five) == [(5, 0)]

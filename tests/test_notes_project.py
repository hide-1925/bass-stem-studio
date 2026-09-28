import pytest

from bss.notes import make_note, merge_reanalysis, normalize_note
from bss.project import ProjectStore, RevisionConflict
from bss.quantize import grid_seconds, quantize_notes, quantize_time


def test_merge_keeps_manual_edits_and_respects_deletions():
    auto_old = [make_note(0.0, 0.5, 40, 0.9, "basic_pitch", note_id="n_1"),
                make_note(1.0, 1.5, 43, 0.9, "basic_pitch", note_id="n_2"),
                make_note(2.0, 2.5, 45, 0.9, "basic_pitch", note_id="n_3")]
    auto_old[1].update(midi_pitch=44, edited=True)  # user fixed this one
    suppressed = [{"start_sec": 2.0, "midi_pitch": 45}]  # user deleted n_3
    old = [auto_old[0], auto_old[1]]
    new_auto = [make_note(0.01, 0.5, 40, 0.8, "fused", note_id="n_1"),
                make_note(1.0, 1.5, 43, 0.8, "fused", note_id="n_2"),
                make_note(2.0, 2.5, 45, 0.8, "fused", note_id="n_3"),
                make_note(3.0, 3.5, 47, 0.8, "fused", note_id="n_4")]
    merged, stats = merge_reanalysis(old, suppressed, new_auto)
    by_start = {round(n["start_sec"], 2): n for n in merged}
    assert by_start[1.0]["midi_pitch"] == 44 and by_start[1.0]["edited"]  # manual edit survives
    assert 2.0 not in by_start  # deleted note not resurrected
    assert 3.0 in by_start and 0.01 in by_start
    assert stats["kept_manual"] == 1 and stats["skipped_overlap"] == 1 and stats["skipped_suppressed"] == 1
    assert len({n["id"] for n in merged}) == len(merged)


def test_normalize_note_fills_defaults():
    n = normalize_note({"start_sec": 1, "end_sec": 0.5, "midi_pitch": 40})
    assert n["end_sec"] > n["start_sec"] and n["source"] == "manual" and n["string"] is None


def test_quantize():
    tempo = {"bpm": 120, "beats_per_bar": 4, "beat_unit": 4, "offset_sec": 0.1}
    assert grid_seconds(tempo, "1/16") == pytest.approx(0.125)
    assert grid_seconds(tempo, "1/8T") == pytest.approx(1 / 6)
    assert quantize_time(0.33, tempo, "1/8") == pytest.approx(0.35)
    q = quantize_notes([make_note(0.11, 0.12, 40, 1, "manual")], tempo, "1/16")[0]
    assert q["q_start_sec"] == pytest.approx(0.1) and q["q_end_sec"] == pytest.approx(0.225)
    assert q["start_sec"] == 0.11  # raw time untouched


def test_project_store_roundtrip(tmp_path):
    src = tmp_path / "song.wav"
    src.write_bytes(b"RIFF0000WAVE")
    store = ProjectStore(tmp_path / "projects", tmp_path / "trash")
    meta = store.create(src, "曲.wav")
    pid = meta["id"]
    assert meta["title"] == "曲"
    store.patch(pid, {"mixer": {"gains_db": {"vocals": -10}}, "tempo": {"bpm": 98}})
    m2 = store.load(pid)
    assert m2["mixer"]["gains_db"]["vocals"] == -10 and m2["mixer"]["gains_db"]["drums"] == 0
    with pytest.raises(ValueError):
        store.patch(pid, {"separation": {}})  # server-only field
    store.patch(pid, {"fx": {"master": {"comp": {"on": True}}, "bass": {"eq": {}}}})
    store.patch(pid, {"fx": {"master": {"comp": {"on": False}}}})
    assert store.load(pid)["fx"] == {"master": {"comp": {"on": False}}}  # replaced, not merged
    notes = [make_note(0, 1, 40, 1, "manual")]
    undo = {"undo": [{"label": "追加", "changes": [{"id": notes[0]["id"], "before": None, "after": notes[0]}]}], "redo": []}
    d = store.save_notes(pid, notes, undo=undo, base_revision=0, log=[{"label": "追加"}])
    assert d["revision"] == 1
    loaded = store.load_notes(pid)
    assert loaded["notes"][0]["midi_pitch"] == 40 and loaded["undo"]["undo"][0]["label"] == "追加"
    with pytest.raises(RevisionConflict):
        store.save_notes(pid, notes, base_revision=0)
    assert len(list(store.paths(pid).snapshots.glob("*.json"))) == 1
    assert store.paths(pid).edits_log.exists()
    store.delete(pid)
    assert not (tmp_path / "projects" / pid).exists() and any((tmp_path / "trash").iterdir())

"""Standard MIDI File export. Time zero of the MIDI file is time zero of the audio (mix.wav), so the
file lines up with the original song when both are imported into a DAW."""

from __future__ import annotations

import io

import mido

TICKS_PER_BEAT = 480
PROGRAM_FINGER_BASS = 33  # GM "Electric Bass (finger)" (0-based)


def to_midi_bytes(notes: list[dict], tempo: dict | None, title: str = "", per_string_channels: bool = False,
                  n_strings: int = 4) -> bytes:
    bpm = float(tempo["bpm"]) if tempo and tempo.get("bpm") else 120.0
    beats_per_bar = int((tempo or {}).get("beats_per_bar", 4))
    beat_unit = int((tempo or {}).get("beat_unit", 4))
    ticks_per_sec = TICKS_PER_BEAT * bpm / 60.0

    # UTF-8 so Japanese titles survive (mido defaults to latin-1 and would fail)
    mid = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT, charset="utf-8")
    meta = mido.MidiTrack()
    mid.tracks.append(meta)
    if title:
        meta.append(mido.MetaMessage("track_name", name=title[:120], time=0))
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    meta.append(mido.MetaMessage("time_signature", numerator=beats_per_bar, denominator=beat_unit, time=0))
    meta.append(mido.MetaMessage("end_of_track", time=0))

    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Bass (estimated)", time=0))
    channels = range(n_strings) if per_string_channels else [0]
    for ch in channels:
        track.append(mido.Message("program_change", program=PROGRAM_FINGER_BASS, channel=ch, time=0))

    events = []
    for n in notes:
        ch = 0
        if per_string_channels and n.get("string"):
            ch = max(0, min(15, int(n["string"]) - 1))  # ch1 = string 1 (highest), Guitar Pro convention
        on = round(n["start_sec"] * ticks_per_sec)
        off = max(on + 1, round(n["end_sec"] * ticks_per_sec))
        events.append((on, 1, mido.Message("note_on", note=int(n["midi_pitch"]), velocity=96, channel=ch)))
        events.append((off, 0, mido.Message("note_off", note=int(n["midi_pitch"]), velocity=0, channel=ch)))
    events.sort(key=lambda e: (e[0], e[1]))  # note_off before note_on at the same tick
    last = 0
    for tick, _, msg in events:
        track.append(msg.copy(time=tick - last))
        last = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()

import io

import av
import mido
import numpy as np
import pytest

from bss import audio_io, peaks
from bss.export import prepare_notes
from bss.export.csv_export import to_csv_bytes
from bss.export.midi import to_midi_bytes
from bss.export.pdf_tab import to_pdf_bytes
from bss.notes import make_note
from bss.separation import alignment_check

SR = 44100


def _encode(path, codec, fmt, x, sr=SR):
    with av.open(str(path), "w", format=fmt) as out:
        st = out.add_stream(codec, rate=sr)
        st.layout = "stereo"
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(x.astype(np.float32)), format="fltp", layout="stereo")
        frame.sample_rate = sr
        for p in st.encode(frame):
            out.mux(p)
        for p in st.encode(None):
            out.mux(p)


@pytest.mark.parametrize("ext,codec,fmt", [("flac", "flac", "flac"), ("mp3", "libmp3lame", "mp3"), ("m4a", "aac", "ipod")])
def test_decode_compressed_formats(tmp_path, ext, codec, fmt):
    t = np.arange(SR * 2) / SR
    x = np.stack([0.3 * np.sin(2 * np.pi * 110 * t), 0.3 * np.sin(2 * np.pi * 220 * t)])
    path = tmp_path / f"t.{ext}"
    try:
        _encode(path, codec, fmt, x)
    except Exception as e:  # encoder missing in this PyAV build
        pytest.skip(f"{codec} encoder unavailable: {e}")
    y = audio_io.load_for_processing(path)
    assert y.shape[0] == 2
    assert abs(y.shape[1] / SR - 2.0) < 0.1


def test_wav_resample_and_mono(tmp_path):
    x = np.random.default_rng(0).uniform(-0.5, 0.5, 48000).astype(np.float32)
    import soundfile as sf

    sf.write(tmp_path / "m.wav", x, 48000)
    y = audio_io.load_for_processing(tmp_path / "m.wav")
    assert y.shape == (2, 44100) and np.allclose(y[0], y[1])


def test_broken_file_message(tmp_path):
    p = tmp_path / "bad.mp3"
    p.write_bytes(b"not audio at all")
    with pytest.raises(audio_io.AudioLoadError):
        audio_io.decode_file(p)


def test_alignment_check_detects_lag():
    rng = np.random.default_rng(1)
    mix = rng.normal(0, 0.1, (2, SR * 3)).astype(np.float32)
    stems = {"a": mix * 0.5, "b": mix * 0.5}
    ok = alignment_check(mix, stems)
    assert ok["length_match"] and ok["lag_samples"] == 0
    shifted = {"a": np.roll(mix, 100, axis=1) * 0.5, "b": np.roll(mix, 100, axis=1) * 0.5}
    assert abs(alignment_check(mix, shifted)["lag_samples"]) == 100


def test_mix_peak(tmp_path):
    a = np.full((2, SR), 0.4, dtype=np.float32)
    audio_io.write_wav(tmp_path / "a.wav", a)
    audio_io.write_wav(tmp_path / "b.wav", a)
    calc = peaks.MixPeakCalculator()
    assert calc.peak([tmp_path / "a.wav", tmp_path / "b.wav"], [1, 1]) == pytest.approx(0.8, abs=1e-3)
    assert calc.peak([tmp_path / "a.wav", tmp_path / "b.wav"], [3.1623, 0]) == pytest.approx(1.265, abs=1e-3)


def _notes():
    ns = [make_note(0.5, 0.9, 28, 0.9, "fused"), make_note(1.0, 1.4, 33, 0.3, "basic_pitch"),
          make_note(1.5, 2.0, 45, 1.0, "manual")]
    for n, (s, f) in zip(ns, [(4, 0), (3, 0), (1, 2)]):
        n["string"], n["fret"] = s, f
    ns[2]["edited"] = True
    ns[2]["technique"] = "slide"
    return ns


def test_midi_export_times_and_channels():
    data = to_midi_bytes(_notes(), {"bpm": 100, "beats_per_bar": 4, "beat_unit": 4}, "テスト", per_string_channels=True)
    mid = mido.MidiFile(file=io.BytesIO(data))
    t, ons = 0.0, []
    tempo = 500000
    for msg in mido.merge_tracks(mid.tracks):
        t += mido.tick2second(msg.time, mid.ticks_per_beat, tempo)
        if msg.type == "set_tempo":
            tempo = msg.tempo
        if msg.type == "note_on" and msg.velocity:
            ons.append((round(t, 3), msg.note, msg.channel))
    assert ons == [(0.5, 28, 3), (1.0, 33, 2), (1.5, 45, 0)]


def test_csv_and_pdf_export():
    csv = to_csv_bytes(_notes()).decode("utf-8-sig").splitlines()
    assert csv[0].startswith("id,start_sec,end_sec")
    assert ",A2," in csv[3] and ",slide," in csv[3]
    pdf = to_pdf_bytes(_notes(), {"strings": [28, 33, 38, 43], "frets": 24}, None, "テスト曲", False, None)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000
    pdf2 = to_pdf_bytes(_notes(), {"strings": [23, 28, 33, 38, 43], "frets": 24},
                        {"bpm": 120, "beats_per_bar": 4, "beat_unit": 4, "offset_sec": 0.5}, "5弦", True, "1/16")
    assert pdf2.startswith(b"%PDF")


def test_prepare_notes_quantized_export():
    notes, q = prepare_notes(_notes(), {"bpm": 120, "beats_per_bar": 4, "beat_unit": 4, "offset_sec": 0.0}, True, "1/8")
    assert q and notes[0]["start_sec"] == pytest.approx(0.5) and notes[0]["raw_start_sec"] == 0.5
    notes2, q2 = prepare_notes(_notes(), None, True, "1/8")
    assert not q2  # no tempo -> no quantization


def test_wav_with_shift_jis_tags(tmp_path):
    """WAV files tagged on Japanese PCs carry Shift-JIS LIST/INFO text; decoding must not fail on it."""
    import struct

    import soundfile as sf

    x = (0.2 * np.sin(2 * np.pi * 110 * np.arange(SR) / SR)).astype(np.float32)
    plain = tmp_path / "plain.wav"
    sf.write(plain, np.stack([x, x]).T, SR, subtype="PCM_16")
    raw = plain.read_bytes()
    title = "日本語".encode("cp932") + b"\x00"
    info = b"INFO" + b"INAM" + struct.pack("<I", len(title)) + title + (b"\x00" if len(title) % 2 else b"")
    chunk = b"LIST" + struct.pack("<I", len(info)) + info
    body = raw[12:] + chunk
    tagged = tmp_path / "tagged.wav"
    tagged.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)
    y, sr = audio_io.decode_file(tagged)
    assert sr == SR and y.shape == (2, SR)

"""Generate synthetic test songs with ground-truth bass MIDI/TAB.

Outputs (per song) into workspace/testsets/<name>/:
  mix.wav            full mix (bass + drums + guitar + piano + vocals + pad)
  bass_only.wav      the bass part alone (for the "bass solo" evaluation)
  stems/<stem>.wav   ground-truth stems
  reference.csv      start_sec,end_sec,midi_pitch,string,fret  (4-string E A D G)
  reference.mid

Synthetic audio is easier than real recordings: treat scores as an upper bound.

    python tools/make_testset.py                 # 16-bar songs (short) for transcription tests
    python tools/make_testset.py --bars 72 --name long   # ~3 min song for separation timing
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bss import audio_io  # noqa: E402
from bss.config import WORKSPACE  # noqa: E402

SR = 44100
OPEN = {4: 28, 3: 33, 2: 38, 1: 43}  # string number (1 = G) -> open MIDI pitch

# 4-bar riffs as (beat_start, beats, string, fret); 4/4, beats in quarter notes.
RIFFS = [
    # root-fifth-octave groove in A with a passing tone
    [(0, 1, 3, 0), (1, .5, 3, 0), (1.5, .5, 2, 2), (2, 1, 1, 2), (3, .5, 2, 2), (3.5, .5, 3, 0),
     (4, 1, 4, 3), (5, .5, 4, 3), (5.5, .5, 3, 5), (6, 1, 2, 5), (7, 1, 3, 5),
     (8, 1, 4, 5), (9, .5, 4, 5), (9.5, .5, 3, 7), (10, 1, 2, 7), (11, .5, 3, 7), (11.5, .5, 4, 7),
     (12, .75, 3, 0), (12.75, .25, 3, 0), (13, 1, 4, 0), (14, .5, 4, 3), (14.5, .5, 4, 5), (15, 1, 3, 2)],
    # eighth-note pedal with octave jumps (octave-error trap)
    [(i * .5, .5, 4 if i % 4 != 3 else 2, 0 if i % 4 != 3 else 2) for i in range(8)] +
    [(4 + i * .5, .5, 4 if i % 4 != 3 else 2, 3 if i % 4 != 3 else 5) for i in range(8)] +
    [(8 + i * .5, .5, 3 if i % 4 != 3 else 1, 0 if i % 4 != 3 else 2) for i in range(8)] +
    [(12, 1.5, 3, 2), (13.5, .5, 3, 3), (14, 1, 3, 4), (15, 1, 4, 4)],
    # walking line in the 5th position with rests and a high note
    [(0, 1, 4, 5), (1, 1, 4, 7), (2, 1, 3, 4), (3, 1, 3, 5),
     (4, 1, 3, 7), (5, 1, 2, 4), (6, 1, 2, 5), (7, 1, 2, 7),
     (8, 1, 1, 5), (9, .5, 1, 7), (10, 1, 1, 9), (11, 1, 2, 7),
     (12, 1, 3, 5), (13, .5, 3, 7), (14.5, .5, 4, 7), (15, 1, 4, 5)],
    # sixteenth-note funk with ghost-free staccato and rests
    [(0, .25, 4, 0), (0.5, .25, 4, 0), (0.75, .25, 4, 12), (1.5, .25, 3, 2), (2, .5, 4, 0), (3, .25, 3, 5),
     (3.25, .25, 3, 7), (3.5, .5, 2, 5),
     (4, .25, 4, 5), (4.5, .25, 4, 5), (4.75, .25, 4, 17), (5.5, .25, 3, 7), (6, .5, 4, 5), (7, .25, 3, 3),
     (7.25, .25, 3, 5), (7.5, .5, 3, 7),
     (8, .25, 4, 0), (8.5, .25, 4, 0), (8.75, .25, 4, 12), (9.5, .25, 3, 2), (10, .5, 4, 0), (11, .5, 2, 2),
     (12, 1, 4, 3), (13, 1, 4, 5), (14, 2, 3, 0)],
]


def karplus_strong(freq: float, dur: float, sr: int = SR, brightness: float = 0.5, decay: float = 0.996,
                   rng: np.random.Generator | None = None) -> np.ndarray:
    rng = rng or np.random.default_rng(0)
    n = int(dur * sr)
    L = max(2, int(round(sr / freq - 0.5)))
    exc = rng.uniform(-1, 1, L)
    # soften the pluck (finger) with a one-pole lowpass
    a = 1.0 - brightness
    for i in range(1, L):
        exc[i] = (1 - a) * exc[i] + a * exc[i - 1]
    exc -= exc.mean()
    # y[0] is a zero guard sample so y[k - L - 1] always exists; the delay line is L samples long.
    y = np.zeros(n + L + 2)
    y[1:L + 1] = exc
    k = L + 1
    while k < n + 1:
        e = min(k + L, n + 1)
        y[k:e] = decay * 0.5 * (y[k - L:e - L] + y[k - L - 1:e - L - 1])
        k = e
    y = y[1:n + 1]
    return y / (np.max(np.abs(y)) + 1e-9)


def additive_bass(freq: float, dur: float, sr: int = SR, n_harm: int = 9, rolloff: float = 0.8,
                  damp: float = 0.8, click: float = 0.0, rng: np.random.Generator | None = None) -> np.ndarray:
    """Electric-bass-like tone: strong fundamental, decaying harmonics, pluck envelope.

    (Demucs separates this as "bass" at ~14 dB SDR; a raw Karplus-Strong pluck ended up in
    "other"/"piano", which made mix evaluations meaningless.)
    """
    rng = rng or np.random.default_rng(0)
    t = np.arange(int(dur * sr)) / sr
    tone = np.zeros_like(t)
    for h in range(1, n_harm + 1):
        if freq * h > sr / 2 - 1000:
            break
        f_h = freq * h * (1 + 0.0004 * h * h)  # slight string inharmonicity
        tone += np.sin(2 * np.pi * f_h * t + rng.uniform(0, 2 * np.pi)) * rolloff ** (h - 1) * np.exp(-t * (1.2 + damp * h))
    env = np.minimum(1, t / 0.004)
    if click > 0:
        n = min(len(t), int(0.006 * sr))
        tone[:n] += rng.uniform(-1, 1, n) * click * np.linspace(1, 0, n)
    return tone * env


def render_bass(notes: list[tuple[float, float, int]], total: float, timbre: str = "warm",
                seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.zeros(int(total * SR) + SR)
    for s, e, p in notes:
        f = 440 * 2 ** ((p - 69) / 12)
        dur = e - s + 0.03  # 30 ms release tail after the reference note end
        if timbre == "ks":
            tone = karplus_strong(f, dur, brightness=0.35, decay=0.998 if p < 40 else 0.996, rng=rng)
        elif timbre == "bright":
            tone = additive_bass(f, dur, n_harm=14, rolloff=0.88, damp=0.5, click=0.3, rng=rng)
        else:  # warm / thin
            tone = additive_bass(f, dur, rng=rng)
        rel = int(0.03 * SR)
        tone[-rel:] *= np.linspace(1, 0, rel)
        tone = tone / (np.max(np.abs(tone)) + 1e-9) * (0.75 + 0.25 * rng.random())
        i = int(s * SR)
        out[i:i + len(tone)] += tone
    out = np.tanh(1.2 * out / (np.max(np.abs(out)) + 1e-9))  # light amp saturation
    if timbre == "thin":  # weak fundamental (small speaker / thin tone): octave-error stress test
        from scipy.signal import butter, sosfilt

        out = sosfilt(butter(2, 90, "highpass", fs=SR, output="sos"), out)
    return out[: int(total * SR)]


def render_drums(total: float, bpm: float, seed: int = 2) -> np.ndarray:
    rng = np.random.default_rng(seed)
    beat = 60 / bpm
    out = np.zeros(int(total * SR) + SR)
    t = np.arange(int(0.35 * SR)) / SR
    kick = np.sin(2 * np.pi * (45 * t + 75 * (1 - np.exp(-t * 30)) / 30)) * np.exp(-t * 9)
    kick[:60] += rng.uniform(-0.3, 0.3, 60)
    ts = np.arange(int(0.2 * SR)) / SR
    snare = (rng.uniform(-1, 1, len(ts)) * 0.6 + np.sin(2 * np.pi * 185 * ts) * 0.5) * np.exp(-ts * 22)
    th = np.arange(int(0.05 * SR)) / SR
    hat = np.diff(rng.uniform(-1, 1, len(th) + 1)) * np.exp(-th * 80) * 0.35
    n_beats = int(total / beat)
    for b in range(n_beats):
        tb = b * beat
        if b % 4 in (0, 2) or (b % 8 == 7):
            i = int(tb * SR); out[i:i + len(kick)] += kick
        if b % 4 in (1, 3):
            i = int(tb * SR); out[i:i + len(snare)] += snare
        for h in (0, 0.5):
            i = int((tb + h * beat) * SR); out[i:i + len(hat)] += hat
    return out[: int(total * SR)] * 0.8


def _chord_roots(riff_notes, bars):
    return [riff_notes[0][2] for _ in range(bars)]


def render_guitar(total: float, bpm: float, roots: list[int], seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    beat = 60 / bpm
    out = np.zeros(int(total * SR) + SR)
    for bar, root in enumerate(roots):
        for k in range(8):  # eighth-note power-chord strums
            s = (bar * 4 + k * 0.5) * beat
            if s >= total:
                break
            for iv in (0, 7, 12):
                p = root + 12 + iv
                tone = karplus_strong(440 * 2 ** ((p - 69) / 12), 0.5 * beat + 0.05, brightness=0.9, decay=0.995, rng=rng)
                tone = np.tanh(3 * tone) * 0.25
                i = int((s + 0.004 * iv / 7) * SR)
                out[i:i + len(tone)] += tone
    return out[: int(total * SR)]


def render_piano(total: float, bpm: float, roots: list[int], seed: int = 4) -> np.ndarray:
    beat = 60 / bpm
    out = np.zeros(int(total * SR) + SR)
    for bar, root in enumerate(roots):
        s = (bar * 4 + 1) * beat
        if s >= total:
            break
        t = np.arange(int(2.5 * beat * SR)) / SR
        for iv in (0, 4, 7):
            f = 440 * 2 ** ((root + 24 + iv - 69) / 12)
            tone = sum(np.sin(2 * np.pi * f * h * t) * (0.5 ** h) * np.exp(-t * (1.5 + h)) for h in range(1, 7))
            i = int(s * SR)
            out[i:i + len(tone)] += tone * 0.25
    return out[: int(total * SR)]


def render_vocal(total: float, bpm: float, roots: list[int], seed: int = 5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    beat = 60 / bpm
    out = np.zeros(int(total * SR) + SR)
    for bar, root in enumerate(roots):
        for k in range(2):
            s = (bar * 4 + k * 2) * beat
            if s >= total:
                break
            p = root + 36 + int(rng.choice([0, 2, 4, 7]))
            t = np.arange(int(1.8 * beat * SR)) / SR
            f = 440 * 2 ** ((p - 69) / 12) * (1 + 0.01 * np.sin(2 * np.pi * 5.5 * t))
            ph = 2 * np.pi * np.cumsum(f) / SR
            tone = sum(np.sin(h * ph) * a for h, a in ((1, 1), (2, .5), (3, .35), (4, .15), (5, .1)))
            env = np.minimum(1, t / 0.05) * np.minimum(1, (t[-1] - t) / 0.1)
            i = int(s * SR)
            out[i:i + len(tone)] += tone * env * 0.18
    return out[: int(total * SR)]


def render_pad(total: float, bpm: float, roots: list[int]) -> np.ndarray:
    beat = 60 / bpm
    out = np.zeros(int(total * SR) + SR)
    t_bar = 4 * beat
    for bar, root in enumerate(roots):
        s = bar * t_bar
        if s >= total:
            break
        t = np.arange(int(t_bar * SR)) / SR
        f = 440 * 2 ** ((root + 19 - 69) / 12)
        tone = sum(((2 * ((f * d * t) % 1) - 1)) for d in (0.995, 1.0, 1.006)) / 3
        env = np.minimum(1, t / 0.3) * np.minimum(1, (t[-1] - t) / 0.3)
        i = int(s * SR)
        out[i:i + len(tone)] += tone * env * 0.06
    from scipy.signal import butter, sosfilt

    return sosfilt(butter(2, 1500, "lowpass", fs=SR, output="sos"), out[: int(total * SR)])


def build_song(name: str, bars: int, bpm: float, riff_order: list[int], timbre: str, lead_in_beats: float = 2.0):
    beat = 60 / bpm
    offset = lead_in_beats * beat
    ref = []  # (start, end, pitch, string, fret)
    roots = []
    bar = 0
    while bar < bars:
        riff = RIFFS[riff_order[(bar // 4) % len(riff_order)]]
        for b0, blen, string, fret in riff:
            s = offset + (bar * 4 + b0) * beat
            e = s + blen * beat * 0.92
            ref.append((s, e, OPEN[string] + fret, string, fret))
        for k in range(4):
            roots.append(OPEN[riff[0][2]] + riff[0][3])
        bar += 4
    total = offset + bars * 4 * beat + 1.5
    bass = render_bass([(s, e, p) for s, e, p, _, _ in ref], total, timbre)
    stems = {
        "bass": bass * 0.55,
        "drums": render_drums(total, bpm),
        "guitar": render_guitar(total, bpm, roots),
        "piano": render_piano(total, bpm, roots),
        "vocals": render_vocal(total, bpm, roots),
        "other": render_pad(total, bpm, roots),
    }
    # shift the non-bass parts by the lead-in so the groove lines up with the bass
    shift = int(offset * SR)
    for k in stems:
        if k != "bass":
            stems[k] = np.concatenate([np.zeros(shift), stems[k][:-shift]])
    mix = sum(stems.values())
    norm = 0.89 / (np.max(np.abs(mix)) + 1e-9)
    out_dir = WORKSPACE / "testsets" / name
    (out_dir / "stems").mkdir(parents=True, exist_ok=True)
    for k, v in stems.items():
        audio_io.write_wav(out_dir / "stems" / f"{k}.wav", np.stack([v, v]) * norm, SR)
    audio_io.write_wav(out_dir / "mix.wav", np.stack([mix, mix * 0.98]) * norm, SR)
    bnorm = 0.89 / (np.max(np.abs(bass)) + 1e-9)
    audio_io.write_wav(out_dir / "bass_only.wav", np.stack([bass, bass]) * bnorm, SR)
    with open(out_dir / "reference.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["start_sec", "end_sec", "midi_pitch", "string", "fret"])
        for s, e, p, st, fr in ref:
            w.writerow([f"{s:.4f}", f"{e:.4f}", p, st, fr])
    import mido

    mid = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack(); mid.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm)))
    events = []
    for s, e, p, _, _ in ref:
        events.append((round(s / beat * 480), "on", p)); events.append((round(e / beat * 480), "off", p))
    events.sort(key=lambda x: (x[0], x[1] == "on"))
    last = 0
    for tick, kind, p in events:
        tr.append(mido.Message("note_on" if kind == "on" else "note_off", note=p, velocity=96 if kind == "on" else 0,
                               time=tick - last)); last = tick
    mid.save(out_dir / "reference.mid")
    print(f"{name}: {total:.1f}s, {len(ref)} notes -> {out_dir}")
    return out_dir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=16)
    ap.add_argument("--bpm", type=float, default=100)
    ap.add_argument("--name", default=None)
    args = ap.parse_args()
    if args.name:
        build_song(args.name, args.bars, args.bpm, [0, 1, 2, 3], "warm")
        return
    build_song("groove_warm", 16, 100, [0, 1, 2, 3], "warm")
    build_song("groove_bright", 16, 112, [3, 2, 1, 0], "bright")
    build_song("groove_thin", 16, 92, [1, 0, 3, 2], "thin")


if __name__ == "__main__":
    main()

"""Move bass sound that the separator gave to other stems back into the bass stem.

htdemucs_6s often puts the bright part of the bass (slap pops, pick / finger attacks) into its piano
and guitar stems. From those "source" stems we take the parts that
  * lie on a harmonic of a bass note sounding at that moment (from the transcription),
  * lie in the chosen frequency range,
  * belong to an attack (energy that just appeared in the sources, decaying over ``attack_ms``);
    pops are percussive while pianos / guitars in the same register mostly sustain,
and move them to the bass. What is moved is subtracted from its source, so all stems still add up
to the same signal. Every parameter is the user's to tune by ear: a harmonic of the bass is also a
note a keyboard may play, so pulling more always also pulls some of the other instruments.
"""

from __future__ import annotations

import numpy as np

DEFAULT_PARAMS = {
    "amount": 0.8,  # 0..1, share of the matching energy moved
    "f_lo": 500.0,  # Hz
    "f_hi": 6000.0,
    "width_cents": 60.0,  # tolerance around each harmonic
    "attack_ms": 150.0,  # 0 = whole note length (sustained parts too)
    "sources": ["piano", "guitar", "other"],
    "max_harmonic": 48,
}
N_FFT = 2048
HOP = 512


def _stft(x: np.ndarray) -> np.ndarray:
    import librosa

    return librosa.stft(x, n_fft=N_FFT, hop_length=HOP, center=True).astype(np.complex64)


def _istft(X: np.ndarray, n: int) -> np.ndarray:
    import librosa

    return librosa.istft(X, n_fft=N_FFT, hop_length=HOP, center=True, length=n).astype(np.float32)


def _note_comb(notes: list[dict], times: np.ndarray, freqs: np.ndarray, p: dict) -> np.ndarray:
    """[freq, frame] weight 0..1: closeness to a harmonic of a bass note sounding at that frame."""
    comb = np.zeros((len(freqs), len(times)), dtype=np.float32)
    band = (freqs >= p["f_lo"]) & (freqs <= p["f_hi"])
    fb = freqs[band]
    width = max(5.0, float(p["width_cents"]))
    for n in notes:
        a = np.searchsorted(times, n["start_sec"] - 0.02)
        b = np.searchsorted(times, n["end_sec"] + 0.05)
        if b <= a:
            continue
        f0 = 440.0 * 2.0 ** ((n["midi_pitch"] - 69) / 12.0)
        k = np.maximum(1.0, np.round(fb / f0))
        cents = 1200.0 * np.log2(fb / (k * f0))
        w = np.exp(-0.5 * (cents / width) ** 2) * (k <= p["max_harmonic"])
        col = np.zeros(len(freqs), dtype=np.float32)
        col[band] = w
        comb[:, a:b] = np.maximum(comb[:, a:b], col[:, None])
    return comb


def _attack_weight(power: np.ndarray, times: np.ndarray, attack_ms: float) -> np.ndarray:
    """[frame] 0..1: 1 right after an attack in the sources, decaying with ``attack_ms``."""
    if attack_ms <= 0:
        return np.ones(len(times), dtype=np.float32)
    e = np.log(power + 1e-10)
    flux = np.maximum(0.0, np.diff(e, prepend=e[:1]))
    thr = max(0.3, float(np.percentile(flux, 80)))
    onset = flux > thr
    w = np.zeros(len(times), dtype=np.float32)
    last = -1e9
    tau = attack_ms / 1000.0
    dt = times[1] - times[0] if len(times) > 1 else HOP / 44100
    for i, t in enumerate(times):
        if onset[i]:
            last = t - dt  # the frame's window already contains the attack
        w[i] = np.exp(-max(0.0, t - last) / tau) if last > -1e8 else 0.0
    return w


def recapture(bass: np.ndarray, sources: dict[str, np.ndarray], notes: list[dict], sr: int,
              params: dict | None = None, chunk_sec: float = 30.0) -> tuple[np.ndarray, dict[str, np.ndarray], dict]:
    """bass / sources: float32 [2, N]. Returns (new bass, new sources, stats). Processed in chunks."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    names = [s for s in p["sources"] if s in sources]
    n = bass.shape[-1]
    new_bass = bass.astype(np.float32).copy()
    new_src = {s: sources[s].astype(np.float32).copy() for s in names}
    moved_e = {s: 0.0 for s in names}
    src_e = {s: float(np.sum(sources[s].astype(np.float64) ** 2)) for s in names}
    amount = float(np.clip(p["amount"], 0.0, 1.0))
    if amount <= 0 or not names or not notes:
        return new_bass, {**sources, **new_src}, {"moved_share": {s: 0.0 for s in names}}
    step = int(chunk_sec * sr)
    pad = 4 * N_FFT
    freqs = np.fft.rfftfreq(N_FFT, 1 / sr)
    for c0 in range(0, n, step):
        a, b = max(0, c0 - pad), min(n, c0 + step + pad)
        seg_len = b - a
        t_off = a / sr
        X = {s: [_stft(sources[s][ch, a:b]) for ch in range(sources[s].shape[0])] for s in names}
        frames = X[names[0]][0].shape[1]
        times = t_off + np.arange(frames) * HOP / sr
        local = [x for x in notes if x["end_sec"] > times[0] - 1 and x["start_sec"] < times[-1] + 1]
        comb = _note_comb(local, times, freqs, p)
        band = (freqs >= p["f_lo"]) & (freqs <= p["f_hi"])
        power = sum(np.sum(np.abs(X[s][ch][band]) ** 2, axis=0) for s in names for ch in range(len(X[s])))
        mask = amount * comb * _attack_weight(power, times, float(p["attack_ms"]))[None, :]
        keep_a, keep_b = c0 - a, min(n, c0 + step) - a
        for s in names:
            for ch in range(len(X[s])):
                moved = _istft(X[s][ch] * mask, seg_len)[keep_a:keep_b]
                new_bass[ch, c0:c0 + len(moved)] += moved
                new_src[s][ch, c0:c0 + len(moved)] -= moved
                moved_e[s] += float(np.sum(moved.astype(np.float64) ** 2))
    stats = {"moved_share": {s: round(moved_e[s] / (src_e[s] + 1e-12), 4) for s in names},
             "bass_gain_db": round(10 * np.log10((np.sum(new_bass.astype(np.float64) ** 2) + 1e-12) /
                                                 (np.sum(bass.astype(np.float64) ** 2) + 1e-12)), 2)}
    return new_bass, {**sources, **new_src}, stats

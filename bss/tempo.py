"""Tempo and downbeat estimation for the score view (constant tempo).

The score needs a beat grid: ``bpm`` (beats of ``beat_unit`` per minute), ``beats_per_bar`` and
``offset_sec`` (time of beat 1 of bar 1). Estimated from the drums + bass stems (the mix when
there are no stems):

1. librosa beat tracking on the combined onset envelope -> approximate beat times
2. one straight line through the beats (robust fit) -> constant period and phase; a fine grid
   search on the smoothed onset envelope then refines both (a few hundred beats pin the period to
   well under 0.1 %, so the grid does not drift over a whole song)
3. downbeat: of the ``beats_per_bar`` possible phases, the one where bass notes change pitch and
   the kick / bass attacks are strongest

Songs whose tempo really changes (live takes, rubato) get ``stable: false``; the grid then drifts
and the UI says so.
"""

from __future__ import annotations

import numpy as np

SR = 22050
HOP = 256  # 11.6 ms


def _norm(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    ref = float(np.percentile(x, 99)) if len(x) else 0.0
    return x / ref if ref > 1e-9 else x


def _onset_env(y: np.ndarray, fmax: float | None = None) -> np.ndarray:
    import librosa

    if fmax is None:
        return librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    # low-band flux (kick drum / bass attacks)
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP, center=True))
    S = np.log1p(100.0 * S[: max(2, int(fmax / (SR / 2048)))])
    return np.maximum(0.0, np.diff(S, axis=1, prepend=S[:, :1])).sum(axis=0)


def _fit_line(beat_times: np.ndarray, period0: float) -> tuple[float, float, np.ndarray, np.ndarray]:
    """Integer beat indices for (possibly gappy) beat times, then a trimmed least-squares line."""
    idx = [0]
    for i in range(1, len(beat_times)):
        step = max(1, int(round((beat_times[i] - beat_times[i - 1]) / period0)))
        idx.append(idx[-1] + step)
    n = np.asarray(idx, dtype=np.float64)
    t = np.asarray(beat_times, dtype=np.float64)
    keep = np.ones(len(t), dtype=bool)
    p, a = period0, float(t[0])
    for _ in range(4):
        if keep.sum() < 4:
            break
        p, a = np.polyfit(n[keep], t[keep], 1)
        res = t - (a + p * n)
        keep = np.abs(res) < max(0.25 * p, 3 * np.median(np.abs(res[keep])) + 1e-3)
    return float(p), float(a), n, t - (a + p * n)


def _refine(env: np.ndarray, p: float, a: float, n_max: int) -> tuple[float, float]:
    """Grid search around (p, a) maximising the smoothed onset envelope sampled on the grid."""
    from scipy.ndimage import gaussian_filter1d

    e = gaussian_filter1d(env, sigma=0.015 * SR / HOP)
    fr = SR / HOP
    n = np.arange(0, n_max + 1, dtype=np.float64)
    for span_p, span_a in ((0.004, 0.3), (0.0004, 0.03)):  # coarse, then fine
        best = (-1.0, p, a)
        for pp in p * (1 + np.linspace(-span_p, span_p, 81)):
            phases = a + p * np.linspace(-span_a, span_a, 61)
            times = phases[:, None] + pp * n[None, :]
            f = np.clip(np.round(times * fr).astype(int), 0, len(e) - 1)
            valid = (times >= 0) & (times * fr < len(e))
            vals = np.where(valid, e[f], 0.0).sum(axis=1) / np.maximum(valid.sum(axis=1), 1)
            j = int(np.argmax(vals))
            if vals[j] > best[0]:
                best = (float(vals[j]), float(pp), float(phases[j]))
        _, p, a = best
    return p, a


def _feature_at(env: np.ndarray, times: np.ndarray, radius_sec: float = 0.05) -> np.ndarray:
    fr = SR / HOP
    r = max(1, int(round(radius_sec * fr)))
    out = np.zeros(len(times))
    for i, t in enumerate(times):
        c = int(round(t * fr))
        if 0 <= c < len(env):
            out[i] = float(np.max(env[max(0, c - r):c + r + 1]))
    return out


def _pc_hist(starts: np.ndarray, ends: np.ndarray, pcs: np.ndarray, t0: float, t1: float) -> np.ndarray:
    ov = np.clip(np.minimum(ends, t1) - np.maximum(starts, t0), 0.0, None)
    h = np.zeros(12)
    np.add.at(h, pcs, ov)
    return h


def _note_features(notes: list[dict], times: np.ndarray, period: float, beats_per_bar: int,
                   tol: float) -> tuple[np.ndarray, np.ndarray]:
    """Per grid beat: (a note starts here, harmonic novelty between the bar before and the bar after).

    Chord changes usually fall on bar lines, so the pitch-class content of the bass one bar before
    a downbeat differs most from the bar after it; mid-bar beats mix the two bars and score lower.
    """
    has = np.zeros(len(times))
    nov = np.zeros(len(times))
    if not notes:
        return has, nov
    starts = np.array([n["start_sec"] for n in notes])
    ends = np.array([n["end_sec"] for n in notes])
    pcs = np.array([int(n["midi_pitch"]) % 12 for n in notes])
    w = period * beats_per_bar
    for i, t in enumerate(times):
        j = int(np.argmin(np.abs(starts - t)))
        if abs(starts[j] - t) <= tol:
            has[i] = 1.0
        a = _pc_hist(starts, ends, pcs, t - w, t)
        b = _pc_hist(starts, ends, pcs, t, t + w)
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na > 1e-6 and nb > 1e-6:
            nov[i] = 1.0 - float(a @ b) / (na * nb)
    return has, nov


def estimate(drums: np.ndarray | None, bass: np.ndarray | None, mix: np.ndarray | None = None,
             notes: list[dict] | None = None, beats_per_bar: int = 4, beat_unit: int = 4) -> dict:
    """Mono float signals at 22.05 kHz (any may be None, at least one required)."""
    import librosa

    sig = [s for s in (drums, bass, mix) if s is not None and len(s)]
    if not sig:
        raise ValueError("テンポ推定に使える音声がありません。")
    n = max(len(s) for s in sig)
    duration = n / SR
    envs = []
    if drums is not None and float(np.max(np.abs(drums))) > 1e-4:
        envs.append(_norm(_onset_env(drums)))
    if bass is not None and float(np.max(np.abs(bass))) > 1e-4:
        envs.append(0.6 * _norm(_onset_env(bass, fmax=1500.0)))
    if not envs and mix is not None:
        envs.append(_norm(_onset_env(mix)))
    if not envs:
        raise ValueError("音声がほぼ無音のため、テンポを推定できません。")
    m = min(len(e) for e in envs)
    env = np.sum([e[:m] for e in envs], axis=0)

    _tempo, beats = librosa.beat.beat_track(onset_envelope=env, sr=SR, hop_length=HOP, tightness=100, units="time")
    beats = np.asarray(beats, dtype=np.float64)
    if len(beats) < 8:
        raise ValueError("拍を十分に検出できませんでした（打楽器・ベースがほとんど無い曲など）。BPM を手入力してください。")
    p0 = float(np.median(np.diff(beats)))
    p, a, idx, res = _fit_line(beats, p0)
    # jitter / drift of the tracked beats around the straight line
    jitter_ms = float(np.sqrt(np.mean(res ** 2)) * 1000)
    k = max(3, len(res) // 16)
    smooth = np.convolve(res, np.ones(k) / k, mode="valid") if len(res) > k else res
    drift_ms = float(np.max(np.abs(smooth)) * 1000) if len(smooth) else 0.0
    n_max = int(idx[-1]) + 1
    a0 = a - p * np.floor(a / p)  # phase at the first grid beat >= 0
    p, a0 = _refine(env, p, a0, int((duration - a0) / p))

    # beat grid over the whole song, then choose the downbeat phase
    grid = a0 + p * np.arange(0, int((duration - a0) / p) + 1)
    grid = grid[grid < duration]
    bass_env = _norm(_onset_env(bass, fmax=300.0)) if bass is not None and len(bass) else None
    kick_env = _norm(_onset_env(drums, fmax=150.0)) if drums is not None and len(drums) else None
    if bass_env is None and kick_env is None and mix is not None:
        kick_env = _norm(_onset_env(mix, fmax=150.0))
    f_bass = _feature_at(bass_env, grid) if bass_env is not None else np.zeros(len(grid))
    f_kick = _feature_at(kick_env, grid) if kick_env is not None else np.zeros(len(grid))
    bpb = max(1, int(beats_per_bar))
    notes = sorted(notes or [], key=lambda x: x["start_sec"])
    has_note, novelty = _note_features(notes, grid, p, bpb, tol=min(0.07, p * 0.2))
    feat = 2.0 * novelty + 0.3 * has_note + 0.3 * np.clip(f_bass, 0, 2) + 0.3 * np.clip(f_kick, 0, 2)
    scores = np.array([float(np.mean(feat[ph::bpb])) if len(feat[ph::bpb]) else 0.0 for ph in range(bpb)])
    if notes and len(grid):
        # weak prior: songs usually start on beat 1
        first = int(np.argmin(np.abs(grid - notes[0]["start_sec"])))
        scores[first % bpb] += 0.05
    phase = int(np.argmax(scores))
    srt = np.sort(scores)[::-1]
    downbeat_conf = float((srt[0] - srt[1]) / (srt[0] + 1e-9)) if bpb > 1 else 1.0
    offset = a0 + p * phase
    grid_fit = None
    if len(notes) >= 16:
        # The onset envelope peaks a frame or so after the attack, and its best period can be off by
        # a few 1e-4. Fit phase and period to the note starts (what gets quantized): residuals of
        # notes near the 16th grid should have no offset and no trend over the song.
        st = np.array([x["start_sec"] for x in notes])
        for tol in (0.3, 0.2, 0.15):
            step = p / 4
            r = (st - offset + step / 2) % step - step / 2
            m = np.abs(r) < tol * step
            if m.sum() < 16:
                break
            d, c = np.polyfit(st[m] - offset, r[m], 1)
            offset += float(c)
            p *= 1.0 + float(d)
        step = p / 4
        r = (st - offset + step / 2) % step - step / 2
        near = np.abs(r) < 0.2 * step
        grid_fit = float(np.mean(np.abs(r) < 0.025))
        meds = [float(np.median(r[near & (st >= w0) & (st < w0 + 20.0)]))
                for w0 in np.arange(st[0], st[-1], 20.0) if (near & (st >= w0) & (st < w0 + 20.0)).sum() >= 8]
        if meds:
            drift_ms = float(np.max(np.abs(meds)) * 1000)
            jitter_ms = float(np.median(np.abs(r[near])) * 1000)
    if offset > p * bpb - 0.1 * p:  # keep beat 1 of bar 1 near the start of the song
        offset -= p * bpb
    # beat tracking strength: onset energy on the grid vs. between grid points
    fr = SR / HOP
    on = env[np.clip(np.round(grid * fr).astype(int), 0, len(env) - 1)].mean() if len(grid) else 0.0
    off = env[np.clip(np.round((grid + p / 2) * fr).astype(int), 0, len(env) - 1)].mean() if len(grid) else 0.0
    beat_conf = float(max(0.0, min(1.0, (on - off) / (on + 1e-9))))
    bpm = 60.0 / p
    offset = float(offset)
    return {
        "bpm": round(bpm, 3),
        "beats_per_bar": bpb,
        "beat_unit": int(beat_unit),
        "offset_sec": round(float(offset), 4),
        "source": "auto",
        "stable": bool(drift_ms <= max(25.0, 0.2 * p / 4 * 1000)),
        "confidence": round(beat_conf, 3),
        "downbeat_confidence": round(downbeat_conf, 3),
        "jitter_ms": round(jitter_ms, 1),
        "drift_ms": round(drift_ms, 1),
        "grid_fit": None if grid_fit is None else round(grid_fit, 3),
        "beats_detected": int(len(beats)),
        "beats_span": n_max,
    }


def _mono22(x: np.ndarray, sr: int) -> np.ndarray:
    import soxr

    mono = x.mean(axis=0) if x.ndim == 2 else x
    if sr != SR:
        mono = soxr.resample(mono, sr, SR, quality="HQ")
    return np.ascontiguousarray(mono, dtype=np.float32)


def estimate_for_project(root, meta: dict, notes: list[dict] | None = None,
                         beats_per_bar: int | None = None) -> dict:
    """Estimate from a project folder (stems when separated, otherwise audio/mix.wav)."""
    import time
    from pathlib import Path

    from . import audio_io

    t0 = time.perf_counter()
    root = Path(root)
    sep = meta.get("separation") or {}
    stem_dir = root / sep.get("dir", "stems/htdemucs_6s") if sep else None
    drums = bass = mix = None
    if stem_dir and (stem_dir / "drums.wav").exists():
        x, sr = audio_io.read_wav(stem_dir / "drums.wav")
        drums = _mono22(x, sr)
    if stem_dir and (stem_dir / "bass.wav").exists():
        x, sr = audio_io.read_wav(stem_dir / "bass.wav")
        bass = _mono22(x, sr)
    if drums is None and bass is None:
        mix_path = root / "audio" / "mix.wav"
        if not mix_path.exists():
            raise FileNotFoundError("音声の変換がまだ完了していません。")
        x, sr = audio_io.read_wav(mix_path)
        mix = _mono22(x, sr)
    tempo = meta.get("tempo") or {}
    bpb = int(beats_per_bar or tempo.get("beats_per_bar") or 4)
    unit = int(tempo.get("beat_unit") or 4)
    est = estimate(drums, bass, mix, notes=notes, beats_per_bar=bpb, beat_unit=unit)
    est["elapsed_sec"] = round(time.perf_counter() - t0, 2)
    est["from"] = "stems" if (drums is not None or bass is not None) else "mix"
    return est

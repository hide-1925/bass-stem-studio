"""Source separation behind a small interface so the model can be swapped later.

A separator takes a float32 stereo mix ``[2, N]`` at 44.1 kHz and returns a dict of stems with
exactly the same shape. Register new implementations in ``REGISTRY``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

import numpy as np

Progress = Callable[[float, str], None]
CancelCheck = Callable[[], bool]


class SeparationCancelled(Exception):
    pass


class Separator(ABC):
    name: str = ""

    def __init__(self, model: str, device: str = "auto", **options):
        self.model_name = model
        self.device_pref = device
        self.options = options

    @property
    @abstractmethod
    def stems(self) -> list[str]:
        ...

    @abstractmethod
    def separate(self, mix: np.ndarray, sr: int, progress: Progress, cancelled: CancelCheck) -> dict[str, np.ndarray]:
        ...

    @abstractmethod
    def env_info(self) -> dict:
        ...


def get_separator(spec: dict) -> Separator:
    from .demucs_sep import DemucsSeparator

    registry = {"demucs": DemucsSeparator}
    name = spec.get("name", "demucs")
    if name not in registry:
        raise ValueError(f"unknown separator: {name}")
    opts = {k: v for k, v in spec.items() if k not in ("name", "model", "device")}
    return registry[name](model=spec.get("model", "htdemucs_6s"), device=spec.get("device", "auto"), **opts)


def fit_length(x: np.ndarray, n: int) -> np.ndarray:
    if x.shape[-1] == n:
        return x
    if x.shape[-1] > n:
        return x[..., :n]
    pad = [(0, 0)] * (x.ndim - 1) + [(0, n - x.shape[-1])]
    return np.pad(x, pad)


def replace_bass(stems: dict[str, np.ndarray], bass: np.ndarray, residual: str = "other") -> dict[str, np.ndarray]:
    """Use another model's bass. The residual stem absorbs the difference, so the stems still add
    up to exactly the same signal (mixer, headroom and "mute everything but ..." stay consistent)."""
    out = dict(stems)
    old = stems["bass"]
    out["bass"] = np.asarray(bass, dtype=np.float32)
    if residual in out:
        out[residual] = (out[residual] + old - out["bass"]).astype(np.float32)
    return out


def alignment_check(mix: np.ndarray, stems: dict[str, np.ndarray], max_lag: int = 2048) -> dict:
    """Verify the stems share the mix's length and start: cross-correlate sum(stems) with the mix."""
    total = sum(stems.values())
    n = mix.shape[-1]
    lengths = {k: int(v.shape[-1]) for k, v in stems.items()}
    a = mix.mean(axis=0)
    b = total.mean(axis=0)
    # use the loudest ~12 s region to keep this fast
    win = min(n, 44100 * 12)
    frame = 4096
    energy = np.convolve(a[: n - n % frame].reshape(-1, frame).std(axis=1), np.ones(3), "same") if n >= frame else [0]
    start = int(np.argmax(energy)) * frame
    start = max(0, min(start - win // 2, n - win))
    seg_a = a[start:start + win]
    seg_b = b[start:start + win]
    spec = np.fft.rfft(seg_a, 2 * win) * np.conj(np.fft.rfft(seg_b, 2 * win))
    xc = np.fft.irfft(spec)
    lags = np.concatenate([xc[: max_lag + 1], xc[-max_lag:]])
    idx = int(np.argmax(lags))
    lag = idx if idx <= max_lag else idx - 2 * max_lag - 1
    resid = mix - total
    resid_db = 10 * np.log10((np.mean(resid ** 2) + 1e-12) / (np.mean(mix ** 2) + 1e-12))
    return {"length_match": all(v == n for v in lengths.values()), "lengths": lengths, "mix_length": int(n),
            "lag_samples": int(lag), "residual_db": round(float(resid_db), 2)}

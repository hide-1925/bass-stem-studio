"""Waveform peaks for display and exact mix-peak computation for the headroom control."""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np
import soundfile as sf

PEAKS_PER_SEC = 100


def compute_peaks(x: np.ndarray, sr: int, per_sec: int = PEAKS_PER_SEC) -> list[int]:
    """Max |sample| over both channels per bucket, quantized to 0..255 (linear, 255 = 0 dBFS)."""
    mono_abs = np.max(np.abs(x), axis=0) if x.ndim == 2 else np.abs(x)
    hop = max(1, sr // per_sec)
    n = int(np.ceil(len(mono_abs) / hop))
    pad = n * hop - len(mono_abs)
    if pad:
        mono_abs = np.concatenate([mono_abs, np.zeros(pad, dtype=mono_abs.dtype)])
    peaks = mono_abs.reshape(n, hop).max(axis=1)
    return np.clip(np.round(peaks * 255), 0, 255).astype(np.uint8).tolist()


def peak_db(x: np.ndarray) -> float:
    p = float(np.max(np.abs(x))) if x.size else 0.0
    return 20 * np.log10(max(p, 1e-9))


class MixPeakCalculator:
    """Peak of sum_i g_i * stem_i over the whole song, computed from the stem files.

    Uses int16 block reads so a 5-minute 6-stem song takes well under a second.
    Results are cached per gain vector.
    """

    def __init__(self, max_cache: int = 256):
        self._cache: OrderedDict[tuple, float] = OrderedDict()
        self._lock = threading.Lock()
        self.max_cache = max_cache

    def peak(self, stem_paths: list[Path], gains: list[float], block: int = 1 << 18) -> float:
        key = (tuple(str(p) for p in stem_paths), tuple(round(g, 6) for g in gains),
               tuple(p.stat().st_mtime_ns for p in stem_paths))
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        active = [(p, g) for p, g in zip(stem_paths, gains) if g > 0]
        if not active:
            return 0.0
        files = [sf.SoundFile(str(p)) for p, _ in active]
        try:
            peak = 0.0
            scale = np.array([g / 32768.0 for _, g in active], dtype=np.float32)
            while True:
                acc = None
                for f, s in zip(files, scale):
                    data = f.read(block, dtype="int16", always_2d=True)
                    if data.shape[0] == 0:
                        continue
                    part = data.astype(np.float32) * s
                    if acc is None:
                        acc = part
                    else:
                        m = min(len(acc), len(part))
                        acc = acc[:m] + part[:m]
                if acc is None or acc.shape[0] == 0:
                    break
                peak = max(peak, float(np.max(np.abs(acc))))
        finally:
            for f in files:
                f.close()
        with self._lock:
            self._cache[key] = peak
            if len(self._cache) > self.max_cache:
                self._cache.popitem(last=False)
        return peak

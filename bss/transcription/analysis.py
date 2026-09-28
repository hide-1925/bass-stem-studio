"""Shared frame-level analysis of the bass stem: loudness gate, onsets, pYIN f0, low-band spectrum."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

SR = 22050
HOP = 256  # 11.6 ms
N_FFT_SPEC = 4096  # 5.4 Hz bins for harmonic checks
SPEC_FMAX = 1600.0
FMIN = 27.5  # A0 (below B0 = 30.9 Hz)
FMAX = 523.25  # C5
ONSET_FMAX = 2500.0


@dataclass
class BassAnalysis:
    y: np.ndarray
    sr: int
    hop: int
    rms_db: np.ndarray
    rms_fast_db: np.ndarray  # 23 ms frames, for dips between repeated notes
    active: np.ndarray
    onset_env: np.ndarray  # normalized 0..1
    onset_times: np.ndarray
    f0_midi: np.ndarray  # NaN when unvoiced
    voiced_prob: np.ndarray
    spec: np.ndarray  # magnitude [bins, frames] up to SPEC_FMAX
    spec_bin_hz: float
    timings: dict = field(default_factory=dict)

    @property
    def n_frames(self) -> int:
        return len(self.rms_db)

    def frame(self, t: float) -> int:
        return int(np.clip(round(t * self.sr / self.hop), 0, self.n_frames - 1))

    def frame_range(self, t0: float, t1: float) -> slice:
        a, b = self.frame(t0), self.frame(t1)
        return slice(a, max(a + 1, b))

    def time(self, frame: int | np.ndarray) -> float | np.ndarray:
        return np.asarray(frame) * self.hop / self.sr

    # ---- note-level measurements ------------------------------------
    def active_fraction(self, t0: float, t1: float) -> float:
        return float(np.mean(self.active[self.frame_range(t0, t1)]))

    def onset_near(self, t: float, tol: float) -> float | None:
        if len(self.onset_times) == 0:
            return None
        i = int(np.argmin(np.abs(self.onset_times - t)))
        return float(self.onset_times[i]) if abs(self.onset_times[i] - t) <= tol else None

    def onsets_between(self, t0: float, t1: float) -> np.ndarray:
        return self.onset_times[(self.onset_times > t0) & (self.onset_times < t1)]

    def onset_strength_at(self, t: float, radius: int = 2) -> float:
        f = self.frame(t)
        return float(np.max(self.onset_env[max(0, f - radius):f + radius + 1]))

    def reattack_between(self, t0: float, t1: float, min_strength: float = 0.15, rise_db: float = 3.0) -> bool:
        """Is there a new attack around the gap [t0, t1] (onset, or a loudness dip followed by a rise)?"""
        if any(self.onset_strength_at(t) >= min_strength for t in self.onsets_between(t0 - 0.02, t1 + 0.02)):
            return True
        a, b = self.frame(t0 - 0.01), self.frame(t1 + 0.01)
        dip = float(np.min(self.rms_fast_db[a:b + 1]))
        after = float(np.max(self.rms_fast_db[b:b + 5])) if b < self.n_frames else dip
        return after - dip >= rise_db

    def pyin_stats(self, t0: float, t1: float) -> tuple[float | None, float, float]:
        """(median MIDI of voiced frames or None, voiced fraction, mean voiced prob)."""
        sl = self.frame_range(t0, t1)
        m = self.f0_midi[sl]
        vp = self.voiced_prob[sl]
        voiced = ~np.isnan(m)
        frac = float(np.mean(voiced)) if len(m) else 0.0
        if not voiced.any():
            return None, 0.0, float(np.mean(vp)) if len(vp) else 0.0
        return float(np.median(m[voiced])), frac, float(np.mean(vp[voiced]))

    def mean_spectrum(self, t0: float, t1: float) -> np.ndarray:
        """Average magnitude spectrum over the body of a note (skips the attack transient)."""
        body0 = t0 + min(0.03, (t1 - t0) * 0.25)
        body1 = min(t1, t0 + 0.35)
        sl = self.frame_range(body0, max(body1, body0 + self.hop / self.sr))
        return self.spec[:, sl].mean(axis=1)

    def mag_at(self, spectrum: np.ndarray, f_hz: float) -> float:
        b = f_hz / self.spec_bin_hz
        if b >= len(spectrum) - 1:
            return 0.0
        i = int(round(b))
        return float(np.max(spectrum[max(0, i - 1):i + 2]))

    def odd_harmonic_ratio(self, spectrum: np.ndarray, f0: float) -> float:
        """Energy at odd multiples of f0 relative to even multiples.

        If f0 is really an octave below the sounding note, its odd multiples fall between the
        true harmonics and the ratio is small (< ~0.2). A real fundamental gives ~0.5 or more.
        """
        odd = [self.mag_at(spectrum, f0 * h) for h in (1, 3, 5)]
        even = [self.mag_at(spectrum, f0 * h) for h in (2, 4, 6)]
        return float(np.mean(odd) / (np.mean(even) + 1e-9))

    def harmonic_energy(self, spectrum: np.ndarray, f0: float, n: int = 6) -> float:
        return float(sum(self.mag_at(spectrum, f0 * h) / h ** 0.5 for h in range(1, n + 1)))


def analyze(y: np.ndarray, sr: int = SR, progress=None) -> BassAnalysis:
    """Run all frame-level analyses on a mono bass signal at 22.05 kHz."""
    import time

    import librosa

    timings = {}
    t = time.perf_counter()
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP, center=True)[0]
    rms_db = 20 * np.log10(np.maximum(rms, 1e-10))
    ref = float(np.percentile(rms_db, 99)) if len(rms_db) else -60.0
    active = (rms_db > ref - 40.0) & (rms_db > -60.0)
    # close 1-2 frame gaps so a note is not chopped by a momentary dip
    active = _binary_close(active, 2)
    rms_fast = librosa.feature.rms(y=y, frame_length=512, hop_length=HOP, center=True)[0]
    rms_fast_db = _fit(20 * np.log10(np.maximum(rms_fast, 1e-10)), len(rms_db), -200.0)
    timings["rms"] = time.perf_counter() - t

    t = time.perf_counter()
    # Low-band log-spectral flux. On bass this found far more attacks (incl. repeated notes)
    # than librosa's default mel flux: onset F 0.98 vs 0.44-0.58 on the synthetic test set.
    S_on = np.abs(librosa.stft(y, n_fft=1024, hop_length=HOP, center=True))
    S_on = np.log1p(100.0 * S_on[: int(ONSET_FMAX / (sr / 1024))])
    onset_env = np.maximum(0.0, np.diff(S_on, axis=1, prepend=S_on[:, :1])).sum(axis=0)
    onset_env = _fit(onset_env, len(rms_db), 0.0)
    onset_env = onset_env / (np.max(onset_env) + 1e-9)
    onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=HOP, units="frames",
                                              normalize=False, wait=3, delta=0.03, pre_max=3, post_max=3,
                                              pre_avg=10, post_avg=10)
    # Express strength relative to a typical attack (90th percentile of detected peaks) rather than
    # the single loudest one, so thresholds mean the same thing across songs.
    if len(onset_frames):
        typical = float(np.percentile(onset_env[onset_frames], 90))
        onset_env = np.minimum(onset_env / (typical + 1e-9), 1.5)
    onset_frames = np.array([f for f in onset_frames if active[min(f + 2, len(active) - 1)]], dtype=int)
    onset_times = librosa.frames_to_time(onset_frames, sr=sr, hop_length=HOP)
    timings["onset"] = time.perf_counter() - t
    if progress:
        progress(0.1, "onset 検出完了")

    t = time.perf_counter()
    # resolution 0.25 semitone: ~5x faster than the default 0.1 and enough for semitone rounding
    f0, _vflag, vprob = librosa.pyin(y, fmin=FMIN, fmax=FMAX, sr=sr, frame_length=2048, hop_length=HOP,
                                      center=True, fill_na=np.nan, resolution=0.25)
    n = len(rms_db)
    f0 = _fit(f0, n, np.nan)
    vprob = _fit(vprob, n, 0.0)
    f0_midi = librosa.hz_to_midi(f0)
    f0_midi[~active] = np.nan
    timings["pyin"] = time.perf_counter() - t
    if progress:
        progress(0.5, "pYIN 完了")

    t = time.perf_counter()
    S = np.abs(librosa.stft(y, n_fft=N_FFT_SPEC, hop_length=HOP, center=True))
    bin_hz = sr / N_FFT_SPEC
    S = _fit2(S[: int(SPEC_FMAX / bin_hz) + 2].astype(np.float32), n)
    timings["stft"] = time.perf_counter() - t

    return BassAnalysis(y=y, sr=sr, hop=HOP, rms_db=rms_db, rms_fast_db=rms_fast_db, active=active, onset_env=onset_env,
                        onset_times=onset_times, f0_midi=f0_midi, voiced_prob=vprob, spec=S,
                        spec_bin_hz=bin_hz, timings=timings)


def _fit(a: np.ndarray, n: int, fill) -> np.ndarray:
    a = np.asarray(a, dtype=np.float64)
    if len(a) >= n:
        return a[:n].copy()
    return np.concatenate([a, np.full(n - len(a), fill)])


def _fit2(S: np.ndarray, n: int) -> np.ndarray:
    if S.shape[1] >= n:
        return S[:, :n]
    return np.pad(S, ((0, 0), (0, n - S.shape[1])))


def _binary_close(mask: np.ndarray, width: int) -> np.ndarray:
    from scipy.ndimage import binary_closing

    if width <= 0 or not mask.any():
        return mask
    return binary_closing(mask, structure=np.ones(2 * width + 1, dtype=bool)) | mask

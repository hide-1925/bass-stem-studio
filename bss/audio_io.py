"""Audio decoding / writing.

Decoding goes through PyAV (bundled FFmpeg), so WAV / MP3 / FLAC / M4A (AAC) and
browser recordings (WebM/Opus) load without a system ffmpeg.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from .config import SAMPLE_RATE

SUPPORTED_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".mp4", ".ogg", ".oga", ".opus", ".webm", ".aif", ".aiff"}


class AudioLoadError(Exception):
    """Raised with a user-facing (Japanese) message."""


def decode_file(path: str | Path) -> tuple[np.ndarray, int]:
    """Decode any audio file to float32 stereo ``[2, N]`` at its native sample rate."""
    import av

    path = Path(path)
    try:
        # Tags are often Shift-JIS on Japanese PCs; they are irrelevant here, so never fail on them.
        container = av.open(str(path), metadata_errors="ignore")
    except Exception as e:  # av.error.* subclasses vary between versions
        fallback = _decode_with_soundfile(path)
        if fallback is not None:
            return fallback
        raise AudioLoadError(f"音声ファイルを開けませんでした（{path.suffix or '拡張子なし'}）：{e}") from e
    try:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise AudioLoadError("音声トラックが見つかりません（壊れている・音声以外のファイルの可能性）。"
                                 "「診断」→「ファイル診断」で中身を確認できます。")
        sr = int(stream.codec_context.sample_rate or stream.rate or 0)
        if sr <= 0:
            raise AudioLoadError("音声として認識できませんでした（ファイルが壊れているか、中身が拡張子と違う形式の可能性があります）。"
                                 "「診断」→「ファイル診断」で中身を確認できます。")
        # Format/layout conversion only; resampling is done with soxr (higher quality).
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=sr)
        chunks: list[np.ndarray] = []
        try:
            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    chunks.append(out.to_ndarray())
            for out in resampler.resample(None):
                chunks.append(out.to_ndarray())
        except Exception as e:
            if not chunks:
                raise AudioLoadError(f"音声をデコードできませんでした：{e}") from e
            # Truncated file: keep what was decoded.
    finally:
        container.close()
    if not chunks:
        raise AudioLoadError("音声データが空です。")
    x = np.concatenate(chunks, axis=1).astype(np.float32, copy=False)
    if x.shape[1] == 0:
        raise AudioLoadError("音声データが空です。")
    return x, sr


def _decode_with_soundfile(path: Path) -> tuple[np.ndarray, int] | None:
    """Second chance for WAV / FLAC / AIFF / OGG that FFmpeg refused."""
    try:
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception:
        return None
    if data.shape[0] == 0:
        return None
    x = data.T
    if x.shape[0] == 1:
        x = np.vstack([x, x])
    elif x.shape[0] > 2:
        x = x[:2]
    return np.ascontiguousarray(x, dtype=np.float32), int(sr)


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """Resample ``[C, N]`` float audio with soxr (HQ)."""
    if sr_in == sr_out:
        return x
    import soxr

    y = soxr.resample(x.T, sr_in, sr_out, quality="HQ")
    return np.ascontiguousarray(y.T, dtype=np.float32)


def load_for_processing(path: str | Path) -> np.ndarray:
    """Decode + resample to the project rate. Returns float32 ``[2, N]``."""
    x, sr = decode_file(path)
    return resample(x, sr, SAMPLE_RATE)


def write_wav(path: str | Path, x: np.ndarray, sr: int = SAMPLE_RATE, subtype: str = "PCM_16") -> None:
    """Write ``[C, N]`` float audio. Values are clipped to [-1, 1] for integer subtypes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = x.T
    if subtype.startswith("PCM"):
        data = np.clip(data, -1.0, 1.0)
    sf.write(str(path), data, sr, subtype=subtype)


def read_wav(path: str | Path, dtype: str = "float32") -> tuple[np.ndarray, int]:
    data, sr = sf.read(str(path), dtype=dtype, always_2d=True)
    return np.ascontiguousarray(data.T), sr


def to_mono(x: np.ndarray) -> np.ndarray:
    return x.mean(axis=0) if x.ndim == 2 else x


def wav_info(path: str | Path) -> dict:
    info = sf.info(str(path))
    return {"sample_rate": info.samplerate, "channels": info.channels, "length_samples": info.frames,
            "duration_sec": info.frames / info.samplerate}

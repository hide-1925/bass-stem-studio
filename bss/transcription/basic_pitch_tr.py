"""Basic Pitch (Spotify, Apache-2.0) note events via the bundled ONNX model."""

from __future__ import annotations

import logging
import os
import tempfile
import warnings

import numpy as np
import soundfile as sf

DEFAULT_PARAMS = {
    "onset_threshold": 0.5,
    "frame_threshold": 0.3,
    "minimum_note_length_ms": 58.0,
    "minimum_frequency": 27.0,
    "maximum_frequency": 450.0,
    "melodia_trick": True,
}


def available() -> bool:
    try:
        _import()
        return True
    except Exception:
        return False


def _import():
    logging.getLogger().setLevel(logging.ERROR)  # basic_pitch warns about every missing backend
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import basic_pitch
        from basic_pitch.inference import predict
    return basic_pitch, predict


def version() -> str:
    try:
        from importlib.metadata import version as v

        return v("basic-pitch")
    except Exception:
        return "unknown"


def run(y: np.ndarray, sr: int, params: dict | None = None) -> list[dict]:
    """Return raw events ``[{start, end, pitch, amplitude}]`` sorted by start."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    basic_pitch, predict = _import()
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="bss_bp_")
    os.close(fd)
    try:
        sf.write(path, y.astype(np.float32), sr, subtype="FLOAT")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _, _, events = predict(
                path,
                basic_pitch.ICASSP_2022_MODEL_PATH,
                onset_threshold=p["onset_threshold"],
                frame_threshold=p["frame_threshold"],
                minimum_note_length=p["minimum_note_length_ms"],
                minimum_frequency=p["minimum_frequency"],
                maximum_frequency=p["maximum_frequency"],
                multiple_pitch_bends=False,
                melodia_trick=p["melodia_trick"],
            )
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    out = [{"start": float(s), "end": float(e), "pitch": int(pitch), "amplitude": float(a)}
           for s, e, pitch, a, *_ in events]
    out.sort(key=lambda d: (d["start"], d["pitch"]))
    return out

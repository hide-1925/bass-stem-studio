"""Paths and constants shared by the server and the worker process."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
WORKSPACE = Path(os.environ.get("BSS_WORKSPACE", ROOT / "workspace")).resolve()
PROJECTS_DIR = WORKSPACE / "projects"
TRASH_DIR = WORKSPACE / "trash"
JOBS_DIR = WORKSPACE / "jobs"

HOST = os.environ.get("BSS_HOST", "127.0.0.1")
PORT = int(os.environ.get("BSS_PORT", "8765"))

# Every processing step works on audio/mix.wav at this rate (htdemucs native rate).
SAMPLE_RATE = 44100

# Display / playback order of stems. "other" is the residual, not an instrument.
STEM_ORDER = ["vocals", "drums", "bass", "guitar", "piano", "other"]
STEM_LABELS_JA = {
    "vocals": "ボーカル",
    "drums": "ドラム",
    "bass": "ベース",
    "guitar": "ギター",
    "piano": "ピアノ",
    "other": "その他（残余）",
}
FIXED_GAIN_STEM = "bass"  # bass volume is fixed at 0 dB (mute/solo still allowed)
GAIN_STEPS_DB = [-30, -20, -10, 0, 10]

DEFAULT_SEPARATOR = {"name": "demucs", "model": "htdemucs_6s", "device": "auto", "shifts": 1, "overlap": 0.25,
                     "bass_model": "htdemucs_ft"}  # bass from htdemucs_ft's bass specialist (see demucs_sep.py)
DEFAULT_TRANSCRIBER = "fused"

MAX_SNAPSHOTS = 50
MAX_UNDO = 200


def ensure_dirs() -> None:
    for d in (PROJECTS_DIR, TRASH_DIR, JOBS_DIR):
        d.mkdir(parents=True, exist_ok=True)

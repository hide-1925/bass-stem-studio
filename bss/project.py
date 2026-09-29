"""Project store: one folder per song with meta, audio, stems, notes, history and settings."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from . import config
from .notes import normalize_note

SCHEMA_VERSION = 1

# Keys the UI may PATCH. Analysis results (source/separation/transcription) are written by the server only.
PATCHABLE_KEYS = {"title", "mixer", "playback", "tuning", "fingering", "tempo", "quantize",
                  "confidence_threshold", "view", "separator", "transcriber", "fx", "score",
                  "transcription_options"}

# Keys the client always sends whole: replaced instead of deep-merged (so removals stick).
REPLACE_KEYS = {"fx"}

DEFAULT_TUNING = {"name": "5弦 レギュラー (B E A D G)", "strings": [23, 28, 33, 38, 43], "frets": 24}


# Score (notation + TAB) view / Guitar Pro export: rhythm quantization and display options.
DEFAULT_SCORE = {
    "grid": "1/16",       # finest binary note value
    "triplets": "auto",   # "auto": per beat, when onsets fit an 8th-triplet grid clearly better; "off"
    "rest_min": "1/8",    # gaps shorter than this are absorbed into the previous note (legato reading)
    "key": "auto",        # "auto" or fifths as an int (-7..7, flats negative)
    "staves": "both",     # "both" / "tab" / "score"
    "scale": 1.0,
}


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def default_meta(project_id: str, title: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "id": project_id,
        "title": title,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "source": None,
        "separation": None,
        "transcription": None,
        "separator": copy.deepcopy(config.DEFAULT_SEPARATOR),
        "transcriber": config.DEFAULT_TRANSCRIBER,
        "transcription_options": {"slap": False},  # slap: keep pops an octave above the thumb note
        "mixer": {
            "gains_db": {s: 0 for s in config.STEM_ORDER if s != config.FIXED_GAIN_STEM},
            "mute": {s: False for s in config.STEM_ORDER},
            "solo": {s: False for s in config.STEM_ORDER},
        },
        "playback": {"rate": 1.0, "loop": {"enabled": False, "a": 0.0, "b": 0.0}, "position": 0.0},
        "tuning": copy.deepcopy(DEFAULT_TUNING),
        "fingering": {"preferred_position": 3},
        # bpm counts beat_unit notes; offset_sec = beat 1 of bar 1. Estimated after transcription
        # (source "auto") unless the user sets it (source "manual").
        "tempo": {"bpm": None, "beats_per_bar": 4, "beat_unit": 4, "offset_sec": 0.0, "source": None},
        "quantize": {"display": False, "export": False, "grid": "1/16"},
        "confidence_threshold": 0.5,
        "score": copy.deepcopy(DEFAULT_SCORE),
        "fx": {},  # per target ("master" / stem name): EQ, filters, compressor (web/js/audio/fx.js)
        "view": {},
    }


def deep_merge(base: dict, patch: dict) -> dict:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{secrets.token_hex(3)}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: file briefly opened by a reader
            time.sleep(0.05 * (attempt + 1))
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class ProjectPaths:
    def __init__(self, root: Path):
        self.root = root

    @property
    def meta(self) -> Path:
        return self.root / "project.json"

    @property
    def source_dir(self) -> Path:
        return self.root / "source"

    @property
    def mix(self) -> Path:
        return self.root / "audio" / "mix.wav"

    def stems_dir(self, model: str) -> Path:
        return self.root / "stems" / model

    @property
    def peaks(self) -> Path:
        return self.root / "analysis" / "peaks.json"

    def auto_notes(self, transcriber: str) -> Path:
        return self.root / "notes" / f"auto-{transcriber}.json"

    @property
    def notes(self) -> Path:
        return self.root / "notes" / "notes.json"

    @property
    def undo(self) -> Path:
        return self.root / "history" / "undo.json"

    @property
    def snapshots(self) -> Path:
        return self.root / "history" / "snapshots"

    @property
    def edits_log(self) -> Path:
        return self.root / "history" / "edits.jsonl"

    @property
    def exports(self) -> Path:
        return self.root / "exports"


class ProjectNotFound(KeyError):
    pass


class RevisionConflict(Exception):
    pass


class ProjectStore:
    def __init__(self, projects_dir: Path | None = None, trash_dir: Path | None = None):
        self.projects_dir = projects_dir or config.PROJECTS_DIR
        self.trash_dir = trash_dir or config.TRASH_DIR
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    # ---- helpers -------------------------------------------------------
    def lock(self, project_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(project_id, threading.RLock())

    def paths(self, project_id: str) -> ProjectPaths:
        if not project_id or any(c in project_id for c in "/\\.:"):
            raise ProjectNotFound(project_id)
        root = self.projects_dir / project_id
        if not (root / "project.json").exists():
            raise ProjectNotFound(project_id)
        return ProjectPaths(root)

    # ---- lifecycle -----------------------------------------------------
    def create(self, source_file: Path, filename: str, kind: str = "file", title: str | None = None) -> dict:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        project_id = f"{stamp}-{secrets.token_hex(3)}"
        root = self.projects_dir / project_id
        (root / "source").mkdir(parents=True)
        ext = Path(filename).suffix.lower() or ".bin"
        dest = root / "source" / f"original{ext}"
        shutil.copyfile(source_file, dest)
        meta = default_meta(project_id, title or Path(filename).stem)
        meta["source"] = {"filename": filename, "kind": kind, "path": f"source/original{ext}",
                          "sha256": sha256_file(dest), "bytes": dest.stat().st_size}
        atomic_write_json(root / "project.json", meta)
        return meta

    def list(self) -> list[dict]:
        out = []
        for d in sorted(self.projects_dir.iterdir(), reverse=True):
            meta = read_json(d / "project.json")
            if not meta:
                continue
            src = meta.get("source") or {}
            out.append({
                "id": meta["id"], "title": meta.get("title"), "created_at": meta.get("created_at"),
                "updated_at": meta.get("updated_at"), "duration_sec": src.get("duration_sec"),
                "kind": src.get("kind"), "separated": bool(meta.get("separation")),
                "transcribed": bool(meta.get("transcription")),
            })
        return out

    def load(self, project_id: str) -> dict:
        return read_json(self.paths(project_id).meta)

    def save(self, meta: dict) -> dict:
        meta["updated_at"] = now_iso()
        atomic_write_json(self.paths(meta["id"]).meta, meta)
        return meta

    def patch(self, project_id: str, patch: dict) -> dict:
        bad = set(patch) - PATCHABLE_KEYS
        if bad:
            raise ValueError(f"変更できない項目です: {sorted(bad)}")
        with self.lock(project_id):
            meta = self.load(project_id)
            for k in REPLACE_KEYS & set(patch):
                meta[k] = patch[k]
            deep_merge(meta, {k: v for k, v in patch.items() if k not in REPLACE_KEYS})
            return self.save(meta)

    def update_server_fields(self, project_id: str, **fields: Any) -> dict:
        """Write analysis results (source/separation/transcription)."""
        with self.lock(project_id):
            meta = self.load(project_id)
            for k, v in fields.items():
                if isinstance(v, dict) and isinstance(meta.get(k), dict):
                    deep_merge(meta[k], v)
                else:
                    meta[k] = v
            return self.save(meta)

    def delete(self, project_id: str) -> None:
        """Move to workspace/trash (not a hard delete)."""
        p = self.paths(project_id)
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p.root), str(self.trash_dir / f"{project_id}-{int(time.time())}"))

    # ---- notes ---------------------------------------------------------
    def load_notes(self, project_id: str) -> dict:
        p = self.paths(project_id)
        data = read_json(p.notes) or {"schema_version": SCHEMA_VERSION, "revision": 0, "notes": [], "suppressed": []}
        undo = read_json(p.undo) or {"undo": [], "redo": []}
        data.setdefault("revision", 0)
        data.setdefault("suppressed", [])
        data["undo"] = undo
        return data

    def save_notes(self, project_id: str, notes: list[dict], suppressed: list[dict] | None = None,
                   undo: dict | None = None, base_revision: int | None = None,
                   log: list[dict] | None = None, reason: str = "save") -> dict:
        """Persist notes. ``base_revision`` enables optimistic concurrency (409 on mismatch)."""
        with self.lock(project_id):
            p = self.paths(project_id)
            current = read_json(p.notes) or {"revision": 0, "suppressed": []}
            if base_revision is not None and int(base_revision) != int(current.get("revision", 0)):
                raise RevisionConflict(
                    f"サーバー側の音符が更新されています（rev {current.get('revision')} ≠ {base_revision}）。再読込してください。")
            data = {
                "schema_version": SCHEMA_VERSION,
                "revision": int(current.get("revision", 0)) + 1,
                "saved_at": now_iso(),
                "reason": reason,
                "notes": [normalize_note(n) for n in notes],
                "suppressed": suppressed if suppressed is not None else current.get("suppressed", []),
            }
            atomic_write_json(p.notes, data)
            if undo is not None:
                undo = {"undo": list(undo.get("undo", []))[-config.MAX_UNDO:],
                        "redo": list(undo.get("redo", []))[-config.MAX_UNDO:]}
                atomic_write_json(p.undo, undo)
            self._snapshot(p, data)
            if log:
                p.edits_log.parent.mkdir(parents=True, exist_ok=True)
                with open(p.edits_log, "a", encoding="utf-8") as f:
                    for entry in log:
                        f.write(json.dumps({"saved_at": data["saved_at"], "revision": data["revision"], **entry},
                                           ensure_ascii=False) + "\n")
            return data

    def push_undo(self, project_id: str, entry: dict) -> None:
        with self.lock(project_id):
            p = self.paths(project_id)
            undo = read_json(p.undo) or {"undo": [], "redo": []}
            undo["undo"] = (undo.get("undo", []) + [entry])[-config.MAX_UNDO:]
            undo["redo"] = []
            atomic_write_json(p.undo, undo)

    def _snapshot(self, p: ProjectPaths, data: dict) -> None:
        p.snapshots.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        atomic_write_json(p.snapshots / f"notes-{stamp}-r{data['revision']}.json", data)
        snaps = sorted(p.snapshots.glob("notes-*.json"))
        for old in snaps[:-config.MAX_SNAPSHOTS]:
            old.unlink(missing_ok=True)

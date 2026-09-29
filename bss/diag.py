"""Diagnostics: persistent logs, environment report, self-check, file probe and a report ZIP.

Everything stays on this PC. The report never contains audio, but it does contain file names,
paths and log text, so review it before sending it to someone.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import logging.handlers
import os
import platform
import shutil
import struct
import tempfile
import threading
import time
import traceback
import zipfile
from pathlib import Path

import numpy as np

from . import __version__, config

LOG_DIR = config.WORKSPACE / "logs"
AUDIO_EXT = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".mp4", ".ogg", ".oga", ".opus", ".webm", ".aif", ".aiff"}
logger = logging.getLogger("bss")


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


# ---------------------------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------------------------
def setup_logging() -> None:
    """Rotating text log (workspace/logs/app.log) for the server and uvicorn errors."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if any(getattr(h, "_bss", False) for h in logger.handlers):
        return
    h = logging.handlers.RotatingFileHandler(LOG_DIR / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    h._bss = True  # type: ignore[attr-defined]
    logger.setLevel(logging.INFO)
    logger.addHandler(h)
    for name in ("uvicorn.error",):
        logging.getLogger(name).addHandler(h)


class JsonlLog:
    """Append-only JSON-lines log with size-based rotation (one backup)."""

    def __init__(self, name: str, max_bytes: int = 3_000_000):
        self.path = LOG_DIR / name
        self.max_bytes = max_bytes
        self.lock = threading.Lock()

    def append(self, entry: dict) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        with self.lock:
            try:
                if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                    os.replace(self.path, self.path.with_suffix(self.path.suffix + ".1"))
            except OSError:
                pass
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)

    def tail(self, n: int = 200) -> list[dict]:
        out: list[dict] = []
        for p in (self.path.with_suffix(self.path.suffix + ".1"), self.path):
            if p.exists():
                with open(p, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        try:
                            out.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        return out[-n:]


events = JsonlLog("events.jsonl")
client_log = JsonlLog("client.jsonl")


def event(event_kind: str, level: str = "info", **data) -> None:
    """Structured event (upload, job start/end, errors) kept across server restarts.
    Never raises: diagnostics must not break the operation being logged."""
    try:
        entry = {**data, "t": now_iso(), "kind": event_kind, "level": level}
        events.append(entry)
        msg = f"{event_kind} " + json.dumps({k: v for k, v in data.items() if k != "detail"}, ensure_ascii=False, default=str)[:600]
        (logger.error if level == "error" else logger.warning if level == "warn" else logger.info)(msg)
    except Exception:  # noqa: BLE001
        logger.exception("failed to record event %s", event_kind)


def app_log_tail(lines: int = 300) -> list[str]:
    p = LOG_DIR / "app.log"
    if not p.exists():
        return []
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()[-lines:]


# ---------------------------------------------------------------------------------------------
# environment
# ---------------------------------------------------------------------------------------------
def _version(pkg: str) -> str | None:
    try:
        from importlib.metadata import version

        return version(pkg)
    except Exception:
        return None


def system_report() -> dict:
    from .separation.demucs_sep import cpu_name

    rep: dict = {
        "app": __version__, "time": now_iso(), "python": platform.python_version(),
        "platform": platform.platform(), "machine": platform.machine(), "cpu": cpu_name(),
        "cpu_count": os.cpu_count(), "workspace": str(config.WORKSPACE),
        "packages": {p: _version(p) for p in ("torch", "demucs", "basic-pitch", "onnxruntime", "librosa", "numpy",
                                               "scipy", "av", "soundfile", "fastapi", "uvicorn", "reportlab", "mido")},
    }
    try:
        import psutil

        vm = psutil.virtual_memory()
        rep["memory_gb"] = {"total": round(vm.total / 1e9, 1), "available": round(vm.available / 1e9, 1)}
    except Exception:
        pass
    try:
        du = shutil.disk_usage(config.WORKSPACE)
        rep["disk_gb"] = {"total": round(du.total / 1e9, 1), "free": round(du.free / 1e9, 1)}
    except Exception:
        pass
    try:
        import av

        rep["ffmpeg"] = {k: ".".join(map(str, v)) if isinstance(v, tuple) else str(v) for k, v in av.library_versions.items()}
    except Exception:
        pass
    try:
        import soundfile as sf

        rep["libsndfile"] = sf.__libsndfile_version__
    except Exception:
        pass
    try:
        import torch

        rep["torch"] = {"version": torch.__version__, "cuda": torch.cuda.is_available(), "threads": torch.get_num_threads(),
                        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}
    except Exception as e:  # noqa: BLE001
        rep["torch"] = {"error": str(e)}
    rep["model_cache_mb"] = _model_cache_mb()
    rep["projects"] = len([d for d in config.PROJECTS_DIR.iterdir() if (d / "project.json").exists()]) if config.PROJECTS_DIR.exists() else 0
    return rep


def _model_cache_mb(repo: str = "HTDemucs-6s") -> float | None:
    try:
        from huggingface_hub import constants

        total = 0
        for dirpath, _, files in os.walk(constants.HF_HUB_CACHE):
            if repo in dirpath:
                total += sum(os.path.getsize(os.path.join(dirpath, f)) for f in files)
        return round(total / 1e6, 1)
    except Exception:
        return None


# ---------------------------------------------------------------------------------------------
# self-check
# ---------------------------------------------------------------------------------------------
def _check(label: str, fn) -> dict:
    t = time.perf_counter()
    try:
        status, detail = fn()
    except Exception as e:  # noqa: BLE001
        status, detail = "fail", f"{type(e).__name__}: {e}"
    return {"label": label, "status": status, "detail": detail, "ms": round(1000 * (time.perf_counter() - t))}


def self_check() -> list[dict]:
    from . import audio_io

    checks = []

    def workspace():
        config.ensure_dirs()
        p = config.WORKSPACE / f".write-test-{os.getpid()}"
        p.write_text("ok", encoding="utf-8")
        p.unlink()
        return "ok", str(config.WORKSPACE)

    def disk():
        free = shutil.disk_usage(config.WORKSPACE).free / 1e9
        st = "ok" if free > 5 else "warn" if free > 1 else "fail"
        return st, f"空き {free:.1f} GB（1 曲あたり約 0.2〜0.5 GB を使います）"

    def memory():
        import psutil

        vm = psutil.virtual_memory()
        st = "ok" if vm.total > 8e9 else "warn"
        return st, f"合計 {vm.total / 1e9:.1f} GB / 空き {vm.available / 1e9:.1f} GB（分離時は 3 分の曲で約 2.4 GB 使用）"

    checks.append(_check("作業フォルダへの書き込み", workspace))
    checks.append(_check("ディスクの空き", disk))
    checks.append(_check("メモリ", memory))

    tmp = Path(tempfile.mkdtemp(prefix="bss-selfcheck-"))
    try:
        t = np.arange(22050) / 44100
        x = np.stack([0.3 * np.sin(2 * np.pi * 220 * t)] * 2).astype(np.float32)
        for ext, codec, fmt in (("wav", None, None), ("flac", "flac", "flac"), ("mp3", "libmp3lame", "mp3"), ("m4a", "aac", "ipod")):
            def roundtrip(ext=ext, codec=codec, fmt=fmt):
                path = tmp / f"t.{ext}"
                if codec is None:
                    audio_io.write_wav(path, x, 44100)
                else:
                    _encode(path, codec, fmt, x)
                y, sr = audio_io.decode_file(path)
                if y.shape[0] != 2 or abs(y.shape[1] / sr - 0.5) > 0.1:
                    return "fail", f"読込結果が不正です: {y.shape} @ {sr}"
                return "ok", f"{ext.upper()} の読込 OK"
            checks.append(_check(f"{ext.upper()} の読込", roundtrip))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    def torch_check():
        import torch

        gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
        return "ok", f"torch {torch.__version__} / スレッド {torch.get_num_threads()} / " + (f"GPU {gpu}" if gpu else "CPU で分離")

    def demucs_check():
        import demucs  # noqa: F401

        mb = _model_cache_mb()
        ft = _model_cache_mb("HTDemucs-ft")
        bass = f"、ベース用 htdemucs_ft も取得済み（{ft} MB）" if ft and ft > 50 else \
            "。ベース用 htdemucs_ft は初回の分離で約 84 MB を取得します"
        if mb and mb > 50:
            return "ok", f"htdemucs_6s のモデルはダウンロード済み（{mb} MB）{bass}"
        return "warn", "モデル未取得：初回の分離時に約 110 MB（＋ベース用 84 MB）をダウンロードします（ネット接続が必要）"

    def bp_check():
        from .transcription import basic_pitch_tr

        if basic_pitch_tr.available():
            return "ok", f"Basic Pitch {basic_pitch_tr.version()}（ONNX）利用可"
        return "warn", "Basic Pitch が使えません（pYIN のみで採譜します）。setup.bat を再実行してください"

    def pyin_check():
        import librosa

        y = np.sin(2 * np.pi * 55 * np.arange(11025) / 22050).astype(np.float32)
        f0, _, _ = librosa.pyin(y, fmin=27.5, fmax=500, sr=22050, frame_length=2048, resolution=0.25)
        est = float(np.nanmedian(f0))
        return ("ok" if abs(est - 55) < 2 else "warn"), f"pYIN 55 Hz → {est:.1f} Hz"

    def font_check():
        from .export import pdf_tab

        pdf_tab._ensure_font()
        return ("ok" if pdf_tab.JP_FONT == "BSSJapanese" else "warn"), f"PDF 用フォント: {pdf_tab.JP_FONT}"

    checks.append(_check("音源分離エンジン（torch）", torch_check))
    checks.append(_check("分離モデル（Demucs）", demucs_check))
    checks.append(_check("採譜（Basic Pitch）", bp_check))
    checks.append(_check("採譜（pYIN）", pyin_check))
    checks.append(_check("PDF の日本語フォント", font_check))
    return checks


def _encode(path: Path, codec: str, fmt: str, x: np.ndarray, sr: int = 44100) -> None:
    import av

    with av.open(str(path), "w", format=fmt) as out:
        st = out.add_stream(codec, rate=sr)
        st.layout = "stereo"
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(x), format="fltp", layout="stereo")
        frame.sample_rate = sr
        for p in st.encode(frame):
            out.mux(p)
        for p in st.encode(None):
            out.mux(p)


# ---------------------------------------------------------------------------------------------
# file probe
# ---------------------------------------------------------------------------------------------
MAGIC = [(b"RIFF", 0, "WAV (RIFF)"), (b"RF64", 0, "WAV (RF64)"), (b"fLaC", 0, "FLAC"), (b"ID3", 0, "MP3 (ID3 タグ付き)"),
         (b"OggS", 0, "Ogg"), (b"FORM", 0, "AIFF"), (b"ftyp", 4, "MP4 / M4A"), (b"\x1aE\xdf\xa3", 0, "WebM / Matroska")]


def _decode_text(raw: bytes) -> tuple[str, str]:
    raw = raw.rstrip(b"\x00")
    for enc in ("utf-8", "cp932", "latin-1"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "unknown"


def _riff_chunks(path: Path, limit: int = 64) -> dict:
    out: dict = {"chunks": [], "info_tags": []}
    with open(path, "rb") as f:
        head = f.read(12)
        out["riff_size"] = struct.unpack("<I", head[4:8])[0]
        pos = 12
        size_total = path.stat().st_size
        while len(out["chunks"]) < limit:
            f.seek(pos)
            hd = f.read(8)
            if len(hd) < 8:
                break
            cid = hd[:4].decode("latin-1")
            size = struct.unpack("<I", hd[4:])[0]
            out["chunks"].append({"id": cid, "size": size, "offset": pos})
            if cid == "fmt ":
                fmt = f.read(min(size, 40))
                tag, ch, sr, byterate, align, bits = struct.unpack("<HHIIHH", fmt[:16])
                out["fmt"] = {"format_tag": tag, "format": {1: "PCM", 3: "IEEE float", 0xFFFE: "EXTENSIBLE"}.get(tag, f"0x{tag:04x}"),
                              "channels": ch, "sample_rate": sr, "bits": bits}
            elif cid == "LIST":
                body = f.read(min(size, 16384))
                if body[:4] == b"INFO":
                    i = 4
                    while i + 8 <= len(body):
                        sid = body[i:i + 4].decode("latin-1", errors="replace")
                        ssz = struct.unpack("<I", body[i + 4:i + 8])[0]
                        raw = body[i + 8:i + 8 + ssz]
                        text, enc = _decode_text(raw)
                        out["info_tags"].append({"id": sid, "encoding": enc, "text": text[:200]})
                        i += 8 + ssz + (ssz & 1)
            if pos + 8 + size > size_total + 1:
                out["truncated_chunk"] = cid
                break
            pos += 8 + size + (size & 1)
    return out


def probe_file(path: str | Path, decode_seconds: float = 20.0, name: str | None = None) -> dict:
    """Inspect an audio file without importing it. Returns details and human-readable findings."""
    from . import audio_io

    path = Path(path)
    findings: list[dict] = []
    add = lambda level, msg: findings.append({"level": level, "message": msg})  # noqa: E731
    size = path.stat().st_size
    with open(path, "rb") as f:
        head = f.read(16)
    detected = next((label for magic, off, label in MAGIC if head[off:off + len(magic)] == magic), None)
    if detected is None and len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0:
        detected = "MP3 (フレーム)"
    ext = Path(name or path.name).suffix.lower()
    rep: dict = {"file": {"name": name or path.name, "size_bytes": size, "size_mb": round(size / 1e6, 2), "ext": ext,
                          "magic_hex": head.hex(" "), "detected": detected or "不明"}}
    if size == 0:
        add("error", "ファイルが空です（0 バイト）。")
    if ext and ext not in AUDIO_EXT:
        add("warn", f"拡張子 {ext} は対応リストにありません。")
    if detected is None and size > 0:
        add("warn", "先頭のバイトが既知の音声形式（WAV / FLAC / MP3 / M4A / Ogg など）と一致しません。"
                    "壊れているか、音声以外のファイルの可能性があります。")
    exp = {".wav": "WAV", ".flac": "FLAC", ".mp3": "MP3", ".m4a": "MP4", ".mp4": "MP4", ".aac": None, ".ogg": "Ogg", ".opus": "Ogg",
           ".webm": "WebM", ".aif": "AIFF", ".aiff": "AIFF"}.get(ext)
    if exp and detected and exp not in detected:
        add("warn", f"拡張子（{ext}）と中身（{detected}）が一致しません。")

    if detected and detected.startswith("WAV"):
        try:
            rep["wav"] = _riff_chunks(path)
            bad = [t for t in rep["wav"]["info_tags"] if t["encoding"] not in ("utf-8",)]
            if bad:
                ids = ", ".join(f"{t['id']}={t['text'][:30]!r}（{t['encoding']}）" for t in bad[:4])
                add("info", f"WAV のタグが UTF-8 以外の文字コードです: {ids}。タグは無視して読み込みます（v0.1.1 以降）。")
            if rep["wav"].get("truncated_chunk"):
                add("warn", f"チャンク {rep['wav']['truncated_chunk']} がファイル末尾を越えています（途中で切れている可能性）。")
            if rep["wav"]["riff_size"] + 8 != size:
                add("info", f"RIFF ヘッダのサイズ（{rep['wav']['riff_size'] + 8}）と実サイズ（{size}）が違います。")
        except Exception as e:  # noqa: BLE001
            add("warn", f"WAV のチャンク解析に失敗: {e}")

    import av

    ff: dict = {}
    try:
        with av.open(str(path)) as c:
            ff["strict_open"] = "ok"
    except UnicodeDecodeError as e:
        ff["strict_open"] = f"UnicodeDecodeError: {e}"
        add("info", "FFmpeg の標準設定ではタグの文字コードで失敗します（旧バージョンで「開けない」原因）。現在はタグを無視して読み込みます。")
    except Exception as e:  # noqa: BLE001
        ff["strict_open"] = f"{type(e).__name__}: {e}"
    try:
        with av.open(str(path), metadata_errors="replace") as c:
            ff["format"] = c.format.name
            ff["format_long"] = c.format.long_name
            ff["duration_sec"] = round(c.duration / 1e6, 3) if c.duration else None
            ff["bit_rate"] = c.bit_rate
            ff["tags"] = {k: str(v)[:200] for k, v in (c.metadata or {}).items()}
            ff["streams"] = []
            for s in c.streams:
                d = {"index": s.index, "type": s.type, "codec": s.codec_context.name if s.codec_context else None}
                if s.type == "audio":
                    cc = s.codec_context
                    d.update({"sample_rate": cc.sample_rate, "channels": cc.channels, "layout": getattr(cc.layout, "name", None),
                              "format": getattr(cc.format, "name", None), "bit_rate": cc.bit_rate,
                              "duration_sec": round(float(s.duration * s.time_base), 3) if s.duration and s.time_base else None})
                ff["streams"].append(d)
        ff["ok"] = True
        audio_streams = [s for s in ff["streams"] if s["type"] == "audio"]
        if not audio_streams:
            add("error", "音声トラックがありません（映像のみ・またはデータのみのファイル）。")
        else:
            a = audio_streams[0]
            if a.get("sample_rate") and a["sample_rate"] != config.SAMPLE_RATE:
                add("info", f"サンプルレート {a['sample_rate']} Hz → 44.1 kHz に変換して処理します。")
            if a.get("channels") == 1:
                add("info", "モノラル音源です（左右に複製して処理します）。")
            elif a.get("channels") and a["channels"] > 2:
                add("info", f"{a['channels']} ch 音源です（ステレオにダウンミックスします）。")
        if ff.get("duration_sec") and ff["duration_sec"] > 15 * 60:
            add("warn", f"長い曲です（{ff['duration_sec'] / 60:.1f} 分）。分離のメモリとブラウザのメモリ（1 分あたり約 130 MB）に注意してください。")
    except Exception as e:  # noqa: BLE001
        ff["ok"] = False
        ff["error"] = f"{type(e).__name__}: {e}"
        add("error", f"FFmpeg で開けません: {e}")
    rep["ffmpeg"] = ff

    try:
        import soundfile as sf

        info = sf.info(str(path))
        rep["soundfile"] = {"ok": True, "format": info.format, "subtype": info.subtype, "sample_rate": info.samplerate,
                            "channels": info.channels, "frames": info.frames}
    except Exception as e:  # noqa: BLE001
        rep["soundfile"] = {"ok": False, "error": str(e)[:300]}

    dec: dict = {"seconds_tested": decode_seconds}
    t = time.perf_counter()
    try:
        x, sr = audio_io.decode_file(path)
        dec.update({"ok": True, "sample_rate": sr, "channels": int(x.shape[0]), "duration_sec": round(x.shape[1] / sr, 3)})
        seg = x[:, : int(sr * decode_seconds)]
        peak = float(np.max(np.abs(x))) if x.size else 0.0
        rms = float(np.sqrt(np.mean(seg ** 2))) if seg.size else 0.0
        clip = float(np.mean(np.abs(x) >= 0.999)) if x.size else 0.0
        dec.update({"peak_dbfs": round(float(20 * np.log10(max(peak, 1e-9))), 2),
                    "rms_dbfs_head": round(float(20 * np.log10(max(rms, 1e-9))), 2),
                    "clipped_ratio": round(clip, 6), "decode_ms": round(1000 * (time.perf_counter() - t))})
        if peak < 1e-4:
            add("error", "中身が無音です（最大音量が −80 dBFS 未満）。")
        if clip > 0.001:
            add("warn", f"元音源が 0 dBFS に張り付いている箇所があります（{clip * 100:.2f}%）。分離の品質に影響することがあります。")
        if not findings or all(f["level"] == "info" for f in findings):
            add("ok", f"読み込めます（{dec['duration_sec']:.1f} 秒、{sr} Hz、{x.shape[0]} ch）。")
    except Exception as e:  # noqa: BLE001
        dec.update({"ok": False, "error": str(e)})
        add("error", f"アプリの読込処理で失敗: {e}")
    rep["decode"] = dec
    rep["findings"] = findings
    rep["verdict"] = "error" if any(f["level"] == "error" for f in findings) else "warn" if any(f["level"] == "warn" for f in findings) else "ok"
    return rep


# ---------------------------------------------------------------------------------------------
# job history (persisted) and report
# ---------------------------------------------------------------------------------------------
def save_job_record(public: dict) -> None:
    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.JOBS_DIR / f"{public['id']}.result.json"
    path.write_text(json.dumps(public, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def job_history(limit: int = 50) -> list[dict]:
    if not config.JOBS_DIR.exists():
        return []
    files = sorted(config.JOBS_DIR.glob("*.result.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    out = []
    for p in files:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def build_report_zip(project_id: str | None = None, client: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        rep = {"generated_at": now_iso(), "note": "音声ファイルは含まれていません。ファイル名・パス・ログ本文は含まれます。",
               "system": system_report(), "self_check": self_check(), "client": client or {}}
        if project_id:
            proj_dir = config.PROJECTS_DIR / project_id
            meta_path = proj_dir / "project.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                z.writestr(f"project/{project_id}/project.json", json.dumps(meta, ensure_ascii=False, indent=1))
                notes = proj_dir / "notes" / "notes.json"
                if notes.exists():
                    z.write(notes, f"project/{project_id}/notes.json")
                src = proj_dir / (meta.get("source") or {}).get("path", "")
                if src.is_file():
                    try:
                        rep["source_probe"] = probe_file(src, name=(meta.get("source") or {}).get("filename"))
                    except Exception as e:  # noqa: BLE001
                        rep["source_probe"] = {"error": str(e)}
        z.writestr("report.json", json.dumps(rep, ensure_ascii=False, indent=1, default=str))
        if LOG_DIR.exists():
            for p in sorted(LOG_DIR.iterdir()):
                if p.is_file():
                    z.write(p, f"logs/{p.name}")
        if config.JOBS_DIR.exists():
            recent = sorted((p for p in config.JOBS_DIR.iterdir() if p.is_file() and p.suffix.lower() not in AUDIO_EXT),
                            key=lambda p: p.stat().st_mtime, reverse=True)[:60]
            for p in recent:
                z.write(p, f"jobs/{p.name}")
    return buf.getvalue()


def exception_text(e: BaseException) -> str:
    return "".join(traceback.format_exception(type(e), e, e.__traceback__))[-6000:]

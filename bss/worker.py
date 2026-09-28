"""Child-process entry point for heavy jobs.

    python -m bss.worker <job.json>

Events are written as JSON lines to the ORIGINAL stdout; everything libraries print is redirected
to stderr so it cannot corrupt the event stream. The server applies stage results to project.json.

Job types: prepare (decode) / separate / transcribe / pipeline (all three).
Outputs are written to temporary names and moved into place at the end of each stage, so a
cancelled job never leaves half-written results behind.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

# --- event channel (set up before importing noisy libraries) --------------------------------
_EVENT_FD = os.dup(1)
os.dup2(2, 1)
sys.stdout = sys.stderr
_events = os.fdopen(_EVENT_FD, "w", encoding="utf-8", buffering=1)


def emit(kind: str, **data) -> None:
    _events.write(json.dumps({"event": kind, **data}, ensure_ascii=False) + "\n")
    _events.flush()


def progress(stage: str, frac: float, message: str = "") -> None:
    emit("progress", stage=stage, progress=round(float(frac), 4), message=message)


def peak_memory_mb() -> float | None:
    try:
        import psutil

        mi = psutil.Process().memory_info()
        peak = getattr(mi, "peak_wset", None) or getattr(mi, "rss", 0)
        return round(peak / 1e6, 1)
    except Exception:
        return None


# --- stages ---------------------------------------------------------------------------------
def stage_prepare(root: Path, params: dict) -> dict:
    from . import audio_io, peaks
    from .config import SAMPLE_RATE

    meta = json.loads((root / "project.json").read_text(encoding="utf-8"))
    src = root / meta["source"]["path"]
    progress("prepare", 0.05, "音声を読み込み中")
    t = time.perf_counter()
    x, sr_in = audio_io.decode_file(src)
    channels_in = int(x.shape[0])
    x = audio_io.resample(x, sr_in, SAMPLE_RATE)
    n = x.shape[1]
    if n < SAMPLE_RATE * 1:
        raise audio_io.AudioLoadError("音声が短すぎます（1秒未満）。")
    progress("prepare", 0.6, "mix.wav を書き出し中")
    tmp = root / "audio" / "mix.tmp.wav"
    audio_io.write_wav(tmp, x, SAMPLE_RATE)
    os.replace(tmp, root / "audio" / "mix.wav")
    warnings = []
    if n < SAMPLE_RATE * 10:
        warnings.append("10秒未満の音声です。分離・採譜の精度が下がることがあります。")
    peak = float(abs(x).max())
    if peak >= 0.999:
        warnings.append("元音源にクリップ（0 dBFS 到達）があります。")
    return {
        "source": {"duration_sec": round(n / SAMPLE_RATE, 3), "sample_rate": SAMPLE_RATE,
                   "original_sample_rate": sr_in, "original_channels": channels_in, "length_samples": int(n),
                   "peak_dbfs": round(peaks.peak_db(x), 2)},
        "peaks_mix": peaks.compute_peaks(x, SAMPLE_RATE),
        "warnings": warnings,
        "elapsed_sec": round(time.perf_counter() - t, 2),
    }


def stage_separate(root: Path, params: dict, job_id: str) -> dict:
    import numpy as np

    from . import audio_io, peaks
    from .config import SAMPLE_RATE, STEM_ORDER
    from .separation import alignment_check, get_separator

    spec = params.get("separator") or {}
    sep = get_separator(spec)
    progress("separate", 0.01, f"モデル {sep.model_name} を読み込み中（初回はダウンロードします）")
    t_load = time.perf_counter()
    sep.load()
    load_sec = time.perf_counter() - t_load
    mix, _ = audio_io.read_wav(root / "audio" / "mix.wav")
    t = time.perf_counter()
    stems = sep.separate(mix, SAMPLE_RATE, lambda f, m: progress("separate", 0.02 + 0.9 * f, m), lambda: False)
    elapsed = time.perf_counter() - t
    progress("separate", 0.93, "ステムを書き出し中")

    # Keep every stem within PCM16 range with ONE common factor (relative balance unchanged).
    peak = max(float(np.max(np.abs(v))) for v in stems.values())
    gain = 1.0 if peak <= 0.999 else 0.999 / peak
    align = alignment_check(mix, stems)
    model_dir = root / "stems" / sep.model_name
    tmp_dir = root / "stems" / f"{sep.model_name}.tmp-{job_id}"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True)
    names = [s for s in STEM_ORDER if s in stems] + [s for s in stems if s not in STEM_ORDER]
    stem_peaks = {}
    for name in names:
        v = stems[name] * gain
        audio_io.write_wav(tmp_dir / f"{name}.wav", v, SAMPLE_RATE)
        stem_peaks[name] = peaks.compute_peaks(v, SAMPLE_RATE)
    info = {**sep.env_info(), "stems": names, "sample_rate": SAMPLE_RATE, "length_samples": int(mix.shape[1]),
            "elapsed_sec": round(elapsed, 2), "model_load_sec": round(load_sec, 2),
            "realtime_factor": round((mix.shape[1] / SAMPLE_RATE) / max(elapsed, 1e-6), 2),
            "stem_gain_db": round(20 * float(np.log10(gain)), 2), "alignment": align,
            "model_cache_mb": model_cache_mb(sep.model_name), "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "dir": f"stems/{sep.model_name}"}
    (tmp_dir / "stems.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    _swap_dir(tmp_dir, model_dir)
    info["peak_rss_mb"] = peak_memory_mb()
    return {"separation": info, "peaks_stems": stem_peaks}


def stage_transcribe(root: Path, params: dict) -> dict:
    from . import audio_io, transcription
    from .fingering import FingeringOptions, apply_assignments, optimize, tuning_from_dict

    meta = json.loads((root / "project.json").read_text(encoding="utf-8"))
    sep = meta.get("separation") or {}
    stem_dir = root / sep.get("dir", "stems/htdemucs_6s")
    bass_path = stem_dir / "bass.wav"
    if not bass_path.exists():
        raise FileNotFoundError("ベースのステムがありません。先に分離を実行してください。")
    name = params.get("transcriber") or meta.get("transcriber") or "fused"
    tuning = tuning_from_dict(params.get("tuning") or meta.get("tuning"))
    progress("transcribe", 0.02, "ベースを読み込み中")
    x, sr = audio_io.read_wav(bass_path)
    y = transcription.prepare_bass(x, sr)
    tparams = {"range_low": tuning.lowest, "range_high": tuning.highest, **(params.get("transcriber_params") or {})}
    notes, info = transcription.transcribe(name, y, tparams, lambda f, m: progress("transcribe", 0.05 + 0.85 * f, m))
    progress("transcribe", 0.93, "運指を最適化中")
    fopts = FingeringOptions.from_dict(params.get("fingering") or meta.get("fingering"))
    result = optimize(notes, tuning, fopts)
    apply_assignments(notes, result)
    out = {"schema_version": 1, "transcriber": info, "tuning": tuning.to_dict(), "notes": notes}
    path = root / "notes" / f"auto-{info['name']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    progress("transcribe", 0.96, "テンポ・小節線を推定中")
    tempo_est = None
    try:
        from . import tempo

        tempo_est = tempo.estimate_for_project(root, meta, notes)
    except Exception as e:  # noqa: BLE001 - the score can still be set up by hand
        tempo_est = {"error": f"{type(e).__name__}: {e}"}
    info["peak_rss_mb"] = peak_memory_mb()
    info["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    info["auto_notes"] = str(path.relative_to(root)).replace("\\", "/")
    return {"transcription": info, "tempo_estimate": tempo_est}


def _swap_dir(tmp_dir: Path, final_dir: Path) -> None:
    old = final_dir.with_name(final_dir.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    for attempt in range(40):
        try:
            if final_dir.exists():
                os.replace(final_dir, old)
            os.replace(tmp_dir, final_dir)
            break
        except PermissionError:  # Windows: a stem may be open for reading (browser download)
            time.sleep(0.25)
    else:
        raise PermissionError(f"{final_dir} を置き換えられませんでした（他のプロセスが使用中）。")
    shutil.rmtree(old, ignore_errors=True)


def model_cache_mb(model: str) -> float | None:
    try:
        from huggingface_hub import constants

        total = 0
        for dirpath, _, files in os.walk(constants.HF_HUB_CACHE):
            if model.replace("htdemucs_", "HTDemucs-") in dirpath or model in dirpath:
                total += sum(os.path.getsize(os.path.join(dirpath, f)) for f in files)
        return round(total / 1e6, 1) if total else None
    except Exception:
        return None


def main() -> int:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    root = Path(job["project_dir"])
    params = job.get("params") or {}
    stages = {"pipeline": ["prepare", "separate", "transcribe"]}.get(job["type"], [job["type"]])
    try:
        for st in stages:
            emit("stage", stage=st)
            t = time.perf_counter()
            if st == "prepare":
                res = stage_prepare(root, params)
            elif st == "separate":
                res = stage_separate(root, params, job["id"])
            elif st == "transcribe":
                res = stage_transcribe(root, params)
            else:
                raise ValueError(f"unknown stage {st}")
            emit("result", stage=st, result=res, elapsed_sec=round(time.perf_counter() - t, 2))
        emit("done")
        return 0
    except Exception as e:  # noqa: BLE001 - reported to the UI
        from .audio_io import AudioLoadError

        msg = str(e) if isinstance(e, (AudioLoadError, FileNotFoundError, ValueError, PermissionError)) else \
            f"{type(e).__name__}: {e}"
        if isinstance(e, MemoryError):
            msg = "メモリ不足です。長い曲は分割するか、他のアプリを閉じてから再試行してください。"
        emit("error", message=msg, detail=traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())

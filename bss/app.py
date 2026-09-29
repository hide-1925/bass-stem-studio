"""FastAPI server: REST API + static frontend. Listens on 127.0.0.1 only.

    python -m bss.app          (or run.bat)
"""

from __future__ import annotations

import shutil
import tempfile
import time
import urllib.parse
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, config, diag
from .audio_io import SUPPORTED_EXTENSIONS
from .export import prepare_notes
from .export.csv_export import to_csv_bytes
from .export.midi import to_midi_bytes
from .export.pdf_tab import to_pdf_bytes
from .fingering import PRESETS, FingeringOptions, apply_assignments, optimize, tuning_from_dict
from .jobs import Job, JobManager
from .notes import merge_reanalysis, normalize_note
from .peaks import PEAKS_PER_SEC, MixPeakCalculator
from .project import (ProjectNotFound, ProjectStore, RevisionConflict, atomic_write_json, now_iso,
                      read_json)

config.ensure_dirs()
diag.setup_logging()
store = ProjectStore()
mixpeak = MixPeakCalculator()
app = FastAPI(title="Bass Stem Studio", version=__version__)


# ---------------------------------------------------------------------------------------------
# job results -> project
# ---------------------------------------------------------------------------------------------
def _update_peaks(project_id: str, **parts) -> None:
    p = store.paths(project_id)
    with store.lock(project_id):
        data = read_json(p.peaks) or {"per_sec": PEAKS_PER_SEC, "stems": {}}
        if "mix" in parts:
            data["mix"] = parts["mix"]
        if "stems" in parts:
            data["stems"] = parts["stems"]
        atomic_write_json(p.peaks, data)


def apply_stage_result(job: Job, stage: str, result: dict) -> None:
    pid = job.project_id
    if stage == "prepare":
        store.update_server_fields(pid, source=result["source"], warnings=result.get("warnings", []))
        _update_peaks(pid, mix=result["peaks_mix"])
    elif stage == "separate":
        _update_peaks(pid, stems=result["peaks_stems"])
        store.update_server_fields(pid, separation=result["separation"])
    elif stage == "transcribe":
        _merge_transcription(pid, result["transcription"])
        _apply_tempo_estimate(pid, result.get("tempo_estimate"))


def _merge_transcription(project_id: str, info: dict) -> None:
    p = store.paths(project_id)
    auto = read_json(p.root / info["auto_notes"])
    with store.lock(project_id):
        meta = store.load(project_id)
        current = store.load_notes(project_id)
        old_notes = current["notes"]
        merged, stats = merge_reanalysis(old_notes, current.get("suppressed", []), auto["notes"])
        tuning = tuning_from_dict(meta.get("tuning"))
        apply_assignments(merged, optimize(merged, tuning, FingeringOptions.from_dict(meta.get("fingering"))))
        before = {n["id"]: n for n in old_notes}
        after = {n["id"]: n for n in merged}
        changes = [{"id": i, "before": before.get(i), "after": after.get(i)}
                   for i in sorted(set(before) | set(after)) if before.get(i) != after.get(i)]
        store.save_notes(project_id, merged, reason="reanalysis",
                         log=[{"label": f"自動採譜（{info['name']}）", "stats": stats}])
        if old_notes:
            store.push_undo(project_id, {"label": "再解析", "time": now_iso(), "changes": changes})
        store.update_server_fields(project_id, transcription={**info, "merge": stats})


TEMPO_KEYS = ("bpm", "beats_per_bar", "beat_unit", "offset_sec", "source", "stable", "confidence")


def _apply_tempo_estimate(project_id: str, est: dict | None, force: bool = False) -> dict:
    """Store the estimate; use it as the tempo unless the user set the tempo by hand."""
    if not est:
        return store.load(project_id)
    if est.get("error"):
        diag.event("tempo_estimate_failed", level="warn", project=project_id, error=est["error"])
        return store.update_server_fields(project_id, tempo_estimate=est)
    meta = store.load(project_id)
    tempo = meta.get("tempo") or {}
    fields = {"tempo_estimate": est}
    if force or not tempo.get("bpm") or tempo.get("source") == "auto":
        fields["tempo"] = {k: est[k] for k in TEMPO_KEYS if k in est}
    return store.update_server_fields(project_id, **fields)


def on_job_finish(job: Job) -> None:
    pub = job.public()
    diag.save_job_record(pub)
    level = "error" if job.status == "error" else "warn" if job.status == "cancelled" else "info"
    diag.event("job_finished", level=level, job=job.id, project=job.project_id, type=job.type, status=job.status,
               stage=job.stage, stage_times=job.stage_times, error=job.error, detail=job.error_detail)


jobs = JobManager(on_result=apply_stage_result, on_finish=on_job_finish)
diag.event("server_start", version=__version__, workspace=str(config.WORKSPACE))


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    diag.event("server_error", level="error", path=request.url.path, method=request.method,
               error=f"{type(exc).__name__}: {exc}", detail=diag.exception_text(exc))
    return JSONResponse({"detail": f"サーバー内部エラー: {type(exc).__name__}: {exc}（診断ログに記録しました）"}, status_code=500)


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
def _meta_or_404(project_id: str) -> dict:
    try:
        return store.load(project_id)
    except ProjectNotFound:
        raise HTTPException(404, "プロジェクトが見つかりません。")


def _stems_dir(meta: dict) -> Path | None:
    sep = meta.get("separation")
    if not sep:
        return None
    return config.PROJECTS_DIR / meta["id"] / sep.get("dir", f"stems/{sep.get('model', 'htdemucs_6s')}")


def _project_view(meta: dict) -> dict:
    active = jobs.active_for_project(meta["id"])
    last = (jobs.for_project(meta["id"]) or [None])[-1]
    stems = []
    sd = _stems_dir(meta)
    if sd is not None:
        for name in meta["separation"].get("stems", []):
            if (sd / f"{name}.wav").exists():
                stems.append({"name": name, "label": config.STEM_LABELS_JA.get(name, name),
                              "url": f"/api/projects/{meta['id']}/stems/{name}.wav",
                              "fixed_gain": name == config.FIXED_GAIN_STEM})
    return {"project": meta, "stems": stems, "active_job": active.public() if active else None,
            "last_job": last.public() if last else None}


def _attachment(data: bytes, filename: str, media_type: str) -> Response:
    quoted = urllib.parse.quote(filename)
    ascii_name = "".join(ch if ch.isascii() and ch not in '"\\' else "_" for ch in filename)
    return Response(content=data, media_type=media_type,
                    headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"})


def _safe_filename(title: str) -> str:
    bad = '<>:"/\\|?*\n\r\t'
    return "".join("_" if ch in bad else ch for ch in (title or "project")).strip()[:80] or "project"


# ---------------------------------------------------------------------------------------------
# system
# ---------------------------------------------------------------------------------------------
@app.get("/api/system")
def system_info():
    import platform

    from . import transcription
    from .separation.demucs_sep import cpu_name

    info = {"app": __version__, "python": platform.python_version(), "cpu": cpu_name(),
            "transcribers": transcription.available(), "separators": ["demucs:htdemucs_6s"],
            "workspace": str(config.WORKSPACE), "stem_order": config.STEM_ORDER,
            "gain_steps_db": config.GAIN_STEPS_DB, "fixed_gain_stem": config.FIXED_GAIN_STEM,
            "supported_extensions": sorted(SUPPORTED_EXTENSIONS)}
    try:
        import torch

        info["torch"] = torch.__version__
        info["cuda"] = torch.cuda.is_available()
        info["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except Exception as e:  # noqa: BLE001
        info["torch_error"] = str(e)
    return info


# ---------------------------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------------------------
@app.get("/api/projects")
def list_projects():
    items = store.list()
    for it in items:
        a = jobs.active_for_project(it["id"])
        it["active_job"] = a.public() if a else None
    return items


@app.post("/api/projects")
async def create_project(file: UploadFile = File(...), title: str | None = Form(None), kind: str = Form("file"),
                         auto: int = Form(1)):
    filename = file.filename or "audio.wav"
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        diag.event("upload_rejected", level="warn", filename=filename, reason="extension", content_type=file.content_type)
        raise HTTPException(400, f"未対応の形式です（{ext or '拡張子なし'}）。対応: WAV / MP3 / FLAC / M4A など")
    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, dir=config.JOBS_DIR, suffix=ext) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)
    try:
        if tmp_path.stat().st_size == 0:
            raise HTTPException(400, "ファイルが空です。")
        meta = store.create(tmp_path, filename, kind="tab_capture" if kind == "tab_capture" else "file",
                            title=title or None)
    finally:
        tmp_path.unlink(missing_ok=True)
    job = None
    if auto:
        job = jobs.submit(meta["id"], "pipeline", _job_params(meta, "pipeline"))
    diag.event("upload", filename=filename, bytes=meta["source"]["bytes"], content_type=file.content_type,
               source_kind=meta["source"]["kind"], project=meta["id"], job=job.id if job else None)
    return {**_project_view(meta), "job": job.public() if job else None}


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    return _project_view(_meta_or_404(project_id))


@app.patch("/api/projects/{project_id}")
def patch_project(project_id: str, patch: dict = Body(...)):
    _meta_or_404(project_id)
    try:
        meta = store.patch(project_id, patch)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"project": meta}


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str):
    _meta_or_404(project_id)
    if jobs.active_for_project(project_id):
        raise HTTPException(409, "処理中のジョブがあります。先に中断してください。")
    store.delete(project_id)
    return {"ok": True}


@app.get("/api/projects/{project_id}/stems/{name}.wav")
def get_stem(project_id: str, name: str):
    meta = _meta_or_404(project_id)
    sd = _stems_dir(meta)
    if sd is None or name not in (meta["separation"].get("stems") or []):
        raise HTTPException(404, "ステムがありません。")
    return FileResponse(sd / f"{name}.wav", media_type="audio/wav", headers={"Cache-Control": "no-cache"})


@app.get("/api/projects/{project_id}/mix.wav")
def get_mix(project_id: str):
    _meta_or_404(project_id)
    path = store.paths(project_id).mix
    if not path.exists():
        raise HTTPException(404, "まだ読込処理が完了していません。")
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-cache"})


@app.get("/api/projects/{project_id}/peaks")
def get_peaks(project_id: str):
    _meta_or_404(project_id)
    data = read_json(store.paths(project_id).peaks)
    if data is None:  # not analysed yet: empty peaks (a 404 would only add console noise)
        data = {"per_sec": PEAKS_PER_SEC, "stems": {}}
    return JSONResponse(data)


@app.get("/api/projects/{project_id}/mixpeak")
def get_mixpeak(project_id: str, g: str = Query(..., description="stem:linear_gain,...")):
    meta = _meta_or_404(project_id)
    sd = _stems_dir(meta)
    if sd is None:
        raise HTTPException(404, "ステムがありません。")
    gains = {}
    for part in g.split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            gains[k] = max(0.0, float(v))
    names = [n for n in meta["separation"]["stems"] if (sd / f"{n}.wav").exists()]
    t = time.perf_counter()
    peak = mixpeak.peak([sd / f"{n}.wav" for n in names], [gains.get(n, 0.0) for n in names])
    target = 10 ** (-1.0 / 20)  # -1 dBFS
    import math

    return {"peak": peak, "peak_db": round(20 * math.log10(max(peak, 1e-9)), 2),
            "headroom_gain": min(1.0, target / peak) if peak > 0 else 1.0, "target_db": -1.0,
            "elapsed_ms": round(1000 * (time.perf_counter() - t), 1)}


# ---------------------------------------------------------------------------------------------
# notes
# ---------------------------------------------------------------------------------------------
@app.get("/api/projects/{project_id}/notes")
def get_notes(project_id: str):
    _meta_or_404(project_id)
    return store.load_notes(project_id)


@app.put("/api/projects/{project_id}/notes")
def put_notes(project_id: str, body: dict = Body(...)):
    _meta_or_404(project_id)
    if jobs.active_for_project(project_id) and (jobs.active_for_project(project_id).type in ("transcribe", "pipeline")):
        raise HTTPException(409, "採譜処理中は保存できません。完了後に再読込してください。")
    try:
        notes = [normalize_note(n) for n in body.get("notes", [])]
        data = store.save_notes(project_id, notes, suppressed=body.get("suppressed"), undo=body.get("undo"),
                                base_revision=body.get("base_revision"), log=body.get("log"))
    except RevisionConflict as e:
        diag.event("notes_conflict", level="warn", project=project_id, error=str(e))
        raise HTTPException(409, str(e))
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(400, f"音符データが不正です: {e}")
    return {"revision": data["revision"], "saved_at": data["saved_at"]}


@app.post("/api/projects/{project_id}/tempo/estimate")
def estimate_tempo(project_id: str, body: dict = Body(default={})):
    """Estimate BPM / downbeat from the stems (a few seconds). ``apply`` writes it as the tempo."""
    from . import tempo

    meta = _meta_or_404(project_id)
    notes = store.load_notes(project_id)["notes"]
    bpb = body.get("beats_per_bar")
    try:
        est = tempo.estimate_for_project(store.paths(project_id).root, meta, notes,
                                         beats_per_bar=int(bpb) if bpb else None)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    diag.event("tempo_estimate", project=project_id, bpm=est["bpm"], stable=est["stable"],
               elapsed_sec=est.get("elapsed_sec"))
    meta = _apply_tempo_estimate(project_id, est, force=True) if body.get("apply", True) else         store.update_server_fields(project_id, tempo_estimate=est)
    return {"estimate": est, "project": meta}


# ---------------------------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------------------------
def _job_params(meta: dict, job_type: str, overrides: dict | None = None) -> dict:
    params = {"separator": meta.get("separator") or config.DEFAULT_SEPARATOR,
              "transcriber": meta.get("transcriber") or config.DEFAULT_TRANSCRIBER,
              "tuning": meta.get("tuning"), "fingering": meta.get("fingering")}
    if (meta.get("transcription_options") or {}).get("slap"):
        params["transcriber_params"] = {"fused": {"octave_attack": True}}
    params.update(overrides or {})
    return params


@app.post("/api/projects/{project_id}/jobs")
def start_job(project_id: str, body: dict = Body(...)):
    meta = _meta_or_404(project_id)
    job_type = body.get("type")
    if job_type not in ("prepare", "separate", "transcribe", "pipeline"):
        raise HTTPException(400, "type は prepare / separate / transcribe / pipeline のいずれかです。")
    if jobs.active_for_project(project_id):
        raise HTTPException(409, "このプロジェクトは処理中です。")
    if job_type == "separate" and not store.paths(project_id).mix.exists():
        job_type = "pipeline"
    if job_type == "transcribe" and not meta.get("separation"):
        raise HTTPException(400, "先に音源分離を実行してください。")
    job = jobs.submit(project_id, job_type, _job_params(meta, job_type, body.get("params")))
    return job.public()


@app.get("/api/projects/{project_id}/jobs")
def project_jobs(project_id: str):
    return [j.public() for j in jobs.for_project(project_id)]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "ジョブが見つかりません（サーバー再起動で履歴は消えます）。")
    return job.public()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    job = jobs.cancel(job_id)
    if job is None:
        raise HTTPException(404, "ジョブが見つかりません。")
    return job.public()


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str):
    job = jobs.retry(job_id)
    if job is None:
        raise HTTPException(409, "再試行できません（実行中、または存在しないジョブです）。")
    return job.public()


# ---------------------------------------------------------------------------------------------
# fingering
# ---------------------------------------------------------------------------------------------
@app.get("/api/fingering/presets")
def fingering_presets():
    return {"presets": PRESETS, "defaults": FingeringOptions().to_dict()}


@app.post("/api/fingering/optimize")
def fingering_optimize(body: dict = Body(...)):
    try:
        tuning = tuning_from_dict(body.get("tuning"))
        notes = [normalize_note(n) for n in body.get("notes", [])]
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    return optimize(notes, tuning, FingeringOptions.from_dict(body.get("options")),
                    respect_locks=bool(body.get("respect_locks", True)))


# ---------------------------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------------------------
@app.get("/api/projects/{project_id}/export/{fmt}")
def export(project_id: str, fmt: str, quantize: int | None = None, per_string: int = 0):
    meta = _meta_or_404(project_id)
    data = store.load_notes(project_id)
    tempo = meta.get("tempo")
    qcfg = meta.get("quantize") or {}
    use_q = bool(qcfg.get("export")) if quantize is None else bool(quantize)
    grid = qcfg.get("grid", "1/16")
    notes, quantized = prepare_notes(data["notes"], tempo, use_q, grid)
    base = _safe_filename(meta.get("title"))
    if fmt == "midi":
        body = to_midi_bytes(notes, tempo, meta.get("title", ""), per_string_channels=bool(per_string),
                             n_strings=len((meta.get("tuning") or {}).get("strings", [0] * 4)))
        return _attachment(body, f"{base}_bass.mid", "audio/midi")
    if fmt == "csv":
        return _attachment(to_csv_bytes(notes, quantized), f"{base}_bass_notes.csv", "text/csv; charset=utf-8")
    if fmt == "pdf":
        body = to_pdf_bytes(notes, meta.get("tuning"), tempo, meta.get("title", ""), quantized, grid,
                            float(meta.get("confidence_threshold", 0.5)))
        return _attachment(body, f"{base}_bass_tab.pdf", "application/pdf")
    raise HTTPException(404, "形式は midi / csv / pdf のいずれかです。")


# ---------------------------------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------------------------------
@app.get("/api/diag/system")
def diag_system():
    return diag.system_report()


@app.get("/api/diag/selfcheck")
def diag_selfcheck():
    return {"checks": diag.self_check()}


@app.get("/api/diag/logs")
def diag_logs(source: str = "events", n: int = 300):
    n = max(1, min(2000, n))
    if source == "events":
        return {"entries": diag.events.tail(n)}
    if source == "client":
        return {"entries": diag.client_log.tail(n)}
    if source == "app":
        return {"lines": diag.app_log_tail(n)}
    raise HTTPException(400, "source は events / client / app のいずれかです。")


@app.post("/api/diag/client")
def diag_client(body: dict = Body(...)):
    ctx = body.get("context") or {}
    for e in (body.get("entries") or [])[:200]:
        if isinstance(e, dict):
            diag.client_log.append({**{k: (str(v)[:4000] if isinstance(v, str) else v) for k, v in e.items()}, "context": ctx,
                                    "received": diag.now_iso()})
    return {"ok": True}


@app.get("/api/diag/jobs")
def diag_jobs(limit: int = 50):
    live = {j.id: j.public() for j in jobs.jobs.values()}
    hist = [h for h in diag.job_history(limit) if h["id"] not in live]
    merged = sorted([*live.values(), *hist], key=lambda j: j.get("created_at") or 0, reverse=True)
    return {"jobs": merged[:limit]}


@app.post("/api/diag/probe")
async def diag_probe(file: UploadFile = File(...)):
    """Inspect a file without creating a project (the temporary copy is deleted afterwards)."""
    name = file.filename or "audio"
    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, dir=config.JOBS_DIR, suffix=Path(name).suffix.lower() or ".bin") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)
    try:
        rep = diag.probe_file(tmp_path, name=name)
    finally:
        tmp_path.unlink(missing_ok=True)
    diag.event("probe", level="warn" if rep["verdict"] != "ok" else "info", filename=name, verdict=rep["verdict"],
               findings=[f["message"] for f in rep["findings"]])
    return rep


@app.get("/api/projects/{project_id}/probe")
def project_probe(project_id: str):
    meta = _meta_or_404(project_id)
    src = config.PROJECTS_DIR / project_id / meta["source"]["path"]
    if not src.is_file():
        raise HTTPException(404, "元ファイルがありません。")
    return diag.probe_file(src, name=meta["source"].get("filename"))


@app.post("/api/diag/report")
def diag_report(body: dict = Body(default={})):
    data = diag.build_report_zip(body.get("project_id"), body.get("client"))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return _attachment(data, f"bss-diagnostics-{stamp}.zip", "application/zip")


# ---------------------------------------------------------------------------------------------
# frontend
# ---------------------------------------------------------------------------------------------
@app.middleware("http")
async def no_cache_static(request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/", StaticFiles(directory=str(config.WEB_DIR), html=True), name="web")


def _port_state(host: str, port: int) -> str:
    """'free', 'bss' (Bass Stem Studio already answers there) or 'other' (another program)."""
    import json
    import socket
    import urllib.request

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        if s.connect_ex((host, port)) != 0:
            return "free"
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/system", timeout=2) as r:
            d = json.loads(r.read().decode("utf-8"))
        return "bss" if isinstance(d, dict) and "stem_order" in d and "app" in d else "other"
    except Exception:  # noqa: BLE001
        return "other"


def main():
    import sys
    import threading
    import webbrowser

    import uvicorn

    try:
        sys.stdout.reconfigure(line_buffering=True)  # messages appear even when output is piped
    except Exception:  # noqa: BLE001
        pass
    open_browser = "--no-browser" not in sys.argv
    port = config.PORT
    state = _port_state(config.HOST, port)
    if state == "bss":
        # Started twice (e.g. run.bat double-clicked again): reuse the running server.
        url = f"http://{config.HOST}:{port}/"
        print(f"Bass Stem Studio はすでに起動しています: {url}")
        print("ブラウザで開きます（この画面は閉じて構いません）。")
        if open_browser:
            webbrowser.open(url)
        return
    if state == "other":
        free = next((p for p in range(port + 1, port + 30) if _port_state(config.HOST, p) == "free"), None)
        if free is None:
            print(f"ポート {port}〜{port + 29} がすべて使用中のため起動できません。BSS_PORT で番号を指定してください。")
            sys.exit(2)
        print(f"ポート {port} は別のアプリが使用中のため、{free} で起動します。")
        diag.event("port_in_use", level="warn", port=port, using=free)
        port = free
    url = f"http://{config.HOST}:{port}/"
    print(f"Bass Stem Studio {__version__} を起動しました: {url}")
    print(f"  作業フォルダ: {config.WORKSPACE}")
    print("  終了するには、この画面を閉じるか Ctrl+C を押してください。")
    if open_browser:
        t = threading.Timer(1.5, lambda: webbrowser.open(url))
        t.daemon = True  # never keep a failed start alive just to open the browser
        t.start()
    uvicorn.run(app, host=config.HOST, port=port, log_level="warning")


if __name__ == "__main__":
    main()

"""Job queue: runs bss.worker in a child process, tracks progress, supports cancel and retry.

One heavy job runs at a time (separation saturates the CPU); others wait in FIFO order.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Callable

from . import config

STAGE_LABELS = {"prepare": "読込", "separate": "音源分離", "transcribe": "採譜"}
# rough share of total time per stage, for a pipeline progress bar
STAGE_WEIGHTS = {"prepare": 0.05, "separate": 0.65, "transcribe": 0.30}


@dataclass
class Job:
    id: str
    project_id: str
    type: str
    params: dict
    status: str = "queued"  # queued | running | done | error | cancelled
    stage: str | None = None
    stage_progress: float = 0.0
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    error_detail: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    attempt: int = 1
    retry_of: str | None = None
    stage_times: dict = field(default_factory=dict)
    stages_done: list = field(default_factory=list)
    log_tail: list = field(default_factory=list)

    def public(self) -> dict:
        d = asdict(self)
        d.pop("params", None)
        d["stage_label"] = STAGE_LABELS.get(self.stage or "", self.stage)
        d["elapsed_sec"] = round((self.finished_at or time.time()) - self.started_at, 1) if self.started_at else 0
        return d


class JobManager:
    def __init__(self, on_result: Callable[[Job, str, dict], None], on_finish: Callable[[Job], None] | None = None):
        self.jobs: dict[str, Job] = {}
        self.queue: deque[str] = deque()
        self.cv = threading.Condition()
        self.on_result = on_result
        self.on_finish = on_finish
        self.current: Job | None = None
        self.proc: subprocess.Popen | None = None
        self._cancel_requested: set[str] = set()
        threading.Thread(target=self._run_loop, daemon=True, name="job-runner").start()

    # ---- public API ---------------------------------------------------------------
    def submit(self, project_id: str, job_type: str, params: dict | None = None, retry_of: Job | None = None) -> Job:
        job = Job(id=f"job-{time.strftime('%H%M%S')}-{secrets.token_hex(3)}", project_id=project_id,
                  type=job_type, params=params or {})
        if retry_of is not None:
            job.attempt = retry_of.attempt + 1
            job.retry_of = retry_of.id
        with self.cv:
            self.jobs[job.id] = job
            self.queue.append(job.id)
            self.cv.notify_all()
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def for_project(self, project_id: str) -> list[Job]:
        return sorted((j for j in self.jobs.values() if j.project_id == project_id), key=lambda j: j.created_at)

    def active_for_project(self, project_id: str) -> Job | None:
        for j in self.for_project(project_id):
            if j.status in ("queued", "running"):
                return j
        return None

    def cancel(self, job_id: str) -> Job | None:
        with self.cv:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if job.status == "queued":
                try:
                    self.queue.remove(job_id)
                except ValueError:
                    pass
                job.status = "cancelled"
                job.finished_at = time.time()
                job.message = "キャンセルしました"
                return job
            if job.status == "running":
                self._cancel_requested.add(job_id)
                proc = self.proc
        if job.status == "running" and proc is not None:
            _kill_tree(proc)
        return job

    def retry(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        if job is None or job.status in ("queued", "running"):
            return None
        return self.submit(job.project_id, job.type, dict(job.params), retry_of=job)

    # ---- runner -------------------------------------------------------------------
    def _run_loop(self):
        while True:
            with self.cv:
                while not self.queue:
                    self.cv.wait()
                job = self.jobs[self.queue.popleft()]
                self.current = job
            try:
                self._run(job)
            except Exception as e:  # noqa: BLE001
                job.status = "error"
                job.error = f"ジョブの起動に失敗しました: {e}"
            finally:
                job.finished_at = job.finished_at or time.time()
                self.current = None
                self.proc = None
                if self.on_finish:
                    try:
                        self.on_finish(job)
                    except Exception:  # noqa: BLE001
                        pass

    def _run(self, job: Job):
        from .project import ProjectStore  # noqa: F401  (import check for clearer errors)

        config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
        spec_path = config.JOBS_DIR / f"{job.id}.json"
        log_path = config.JOBS_DIR / f"{job.id}.log"
        spec = {"id": job.id, "type": job.type, "params": job.params,
                "project_dir": str(config.PROJECTS_DIR / job.project_id)}
        spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        job.status = "running"
        job.started_at = time.time()
        job.message = "開始"
        from . import diag

        diag.event("job_started", job=job.id, project=job.project_id, type=job.type, attempt=job.attempt)
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        with open(log_path, "w", encoding="utf-8", errors="replace") as log:
            proc = subprocess.Popen([sys.executable, "-m", "bss.worker", str(spec_path)], cwd=str(config.ROOT),
                                    stdout=subprocess.PIPE, stderr=log, env=env, creationflags=flags,
                                    text=True, encoding="utf-8", errors="replace", bufsize=1)
            self.proc = proc
            stage_start = time.time()
            for line in proc.stdout:  # type: ignore[union-attr]
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = ev.get("event")
                if kind == "stage":
                    job.stage = ev["stage"]
                    job.stage_progress = 0.0
                    stage_start = time.time()
                    job.message = f"{STAGE_LABELS.get(job.stage, job.stage)}を開始"
                elif kind == "progress":
                    job.stage = ev.get("stage", job.stage)
                    job.stage_progress = float(ev.get("progress", 0))
                    job.message = ev.get("message", "")
                elif kind == "result":
                    st = ev["stage"]
                    job.stage_times[st] = ev.get("elapsed_sec", round(time.time() - stage_start, 2))
                    # apply first: clients react to stages_done by re-reading the project
                    try:
                        self.on_result(job, st, ev.get("result") or {})
                    except Exception as e:  # noqa: BLE001 - surfaced to the UI
                        job.error = f"結果の反映に失敗しました: {e}"
                        job.error_detail = traceback.format_exc()
                    job.stages_done.append(st)
                elif kind == "error":
                    job.error = ev.get("message")
                    job.error_detail = ev.get("detail")
                elif kind == "done":
                    pass
                job.progress = self._overall(job)
            proc.wait()
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-30:] if log_path.exists() else []
        job.log_tail = tail
        job.finished_at = time.time()
        if job.id in self._cancel_requested:
            job.status = "cancelled"
            job.message = "キャンセルしました（途中の結果は破棄し、以前の結果を保持しています）"
            self._cancel_requested.discard(job.id)
        elif proc.returncode == 0 and not job.error:
            job.status = "done"
            job.progress = 1.0
            job.message = "完了"
        else:
            job.status = "error"
            if not job.error:
                job.error = f"処理が異常終了しました（終了コード {proc.returncode}）"
                job.error_detail = "\n".join(tail)
            job.message = job.error or ""

    def _overall(self, job: Job) -> float:
        stages = ["prepare", "separate", "transcribe"] if job.type == "pipeline" else [job.type]
        if len(stages) == 1:
            return round(job.stage_progress if job.stage not in job.stages_done else 1.0, 4)
        total = sum(STAGE_WEIGHTS[s] for s in stages)
        done = sum(STAGE_WEIGHTS[s] for s in job.stages_done)
        cur = STAGE_WEIGHTS.get(job.stage or "", 0) * job.stage_progress if job.stage not in job.stages_done else 0
        return round(min(1.0, (done + cur) / total), 4)


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=10)
        else:
            proc.kill()
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass

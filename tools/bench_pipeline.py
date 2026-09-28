"""End-to-end benchmark through the running server (same path as the UI).

    python tools/make_testset.py --bars 72 --name long_3min     # ~3 min synthetic song
    python tools/bench_pipeline.py workspace/testsets/long_3min/mix.wav
    python tools/bench_pipeline.py song.mp3 --cancel-test        # also exercise cancel + retry

Reports stage times, realtime factor, worker peak memory, model cache size, stem length /
start alignment, and note count. Results are appended to workspace/eval/bench.jsonl.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import secrets
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8765"


def call(method: str, path: str, body: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode("utf-8"))


def upload(path: Path, title: str) -> dict:
    boundary = "----bss" + secrets.token_hex(8)
    parts = []
    for name, value in (("title", title), ("kind", "file"), ("auto", "1")):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8"))
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
                 f"Content-Type: {ctype}\r\n\r\n".encode("utf-8") + path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return call("POST", "/api/projects", b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def wait(job_id: str, until=("done", "error", "cancelled"), timeout=1800) -> dict:
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        job = call("GET", f"/api/jobs/{job_id}")
        msg = f"{job['status']:9s} {job.get('stage') or '':10s} {job['progress'] * 100:5.1f}% {job['message'][:50]}"
        if msg != last:
            print("  ", msg, flush=True)
            last = msg
        if job["status"] in until:
            return job
        time.sleep(1.0)
    raise TimeoutError(job_id)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", type=Path)
    ap.add_argument("--title", default=None)
    ap.add_argument("--cancel-test", action="store_true")
    args = ap.parse_args()
    title = args.title or f"ベンチ {args.audio.stem}"
    print(f"upload {args.audio} ({args.audio.stat().st_size / 1e6:.1f} MB)")
    t = time.time()
    r = upload(args.audio, title)
    pid, job = r["project"]["id"], r["job"]
    print(f"  project {pid}, title stored as {r['project']['title']!r}, upload {time.time() - t:.1f}s")
    job = wait(job["id"])
    total = time.time() - t
    meta = call("GET", f"/api/projects/{pid}")["project"]
    sep, tr, src = meta.get("separation") or {}, meta.get("transcription") or {}, meta.get("source") or {}
    result = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "audio": str(args.audio), "project": pid, "status": job["status"],
        "error": job.get("error"), "duration_sec": src.get("duration_sec"), "stage_times": job.get("stage_times"),
        "total_sec": round(total, 1), "separation": {k: sep.get(k) for k in (
            "model", "device", "cpu", "gpu", "torch", "demucs", "threads", "shifts", "elapsed_sec", "model_load_sec",
            "realtime_factor", "peak_rss_mb", "model_cache_mb", "stem_gain_db", "alignment")},
        "transcription": {k: tr.get(k) for k in ("name", "elapsed_sec", "notes", "peak_rss_mb", "analysis_timings")},
    }
    print(json.dumps(result, ensure_ascii=False, indent=1))
    if args.cancel_test:
        print("cancel test: start transcription and cancel it after 2 s")
        j = call("POST", f"/api/projects/{pid}/jobs", json.dumps({"type": "transcribe"}).encode(), {"Content-Type": "application/json"})
        time.sleep(2)
        call("POST", f"/api/jobs/{j['id']}/cancel")
        jc = wait(j["id"])
        print("  after cancel:", jc["status"], "| notes still readable:", len(call("GET", f"/api/projects/{pid}/notes")["notes"]))
        jr = call("POST", f"/api/jobs/{j['id']}/retry")
        jr = wait(jr["id"])
        print("  retry:", jr["status"], f"attempt {jr['attempt']}", jr.get("stage_times"))
        result["cancel_test"] = {"cancelled": jc["status"], "retry": jr["status"]}
    out = Path(__file__).resolve().parent.parent / "workspace" / "eval" / "bench.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    sys.exit(main())

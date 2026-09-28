"""API smoke test in an isolated workspace (runs the server app in a subprocess with BSS_WORKSPACE
pointing to a temp dir, so the real projects and logs are never touched)."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCRIPT = textwrap.dedent(r'''
    import io, json, struct, sys, zipfile
    import numpy as np, soundfile as sf
    sys.path.insert(0, sys.argv[1])
    from fastapi.testclient import TestClient
    from bss.app import app

    c = TestClient(app)
    sr = 44100
    x = (0.2 * np.sin(2 * np.pi * 110 * np.arange(sr * 2) / sr)).astype(np.float32)
    buf = io.BytesIO(); sf.write(buf, np.stack([x, x]).T, sr, format="WAV", subtype="PCM_16"); raw = buf.getvalue()
    # add a Shift-JIS LIST/INFO tag like WAVs written by Japanese software
    title = "日本語".encode("cp932") + b"\x00"
    info = b"INFO" + b"INAM" + struct.pack("<I", len(title)) + title + (b"\x00" if len(title) % 2 else b"")
    body = raw[12:] + b"LIST" + struct.pack("<I", len(info)) + info
    wav = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body

    out = {}
    r = c.post("/api/projects", files={"file": ("曲 テスト.wav", wav, "audio/wav")}, data={"title": "テスト曲", "auto": "0"})
    out["upload"] = r.status_code
    pid = r.json()["project"]["id"]
    out["title"] = r.json()["project"]["title"]
    r = c.get(f"/api/projects/{pid}/probe"); out["probe"] = r.status_code; out["verdict"] = r.json()["verdict"]
    out["tag_enc"] = [t["encoding"] for t in r.json()["wav"]["info_tags"]]
    r = c.post("/api/diag/probe", files={"file": ("broken.mp3", b"\x00" * 5000, "audio/mpeg")})
    out["probe_broken"] = (r.status_code, r.json()["verdict"])
    r = c.post("/api/projects", files={"file": ("x.txt", b"abc", "text/plain")}); out["reject"] = r.status_code
    r = c.post("/api/diag/client", json={"entries": [{"level": "error", "message": "test error"}], "context": {"p": 1}})
    out["client"] = r.status_code
    ev = c.get("/api/diag/logs?source=events&n=50").json()["entries"]
    out["events"] = sorted({e["kind"] for e in ev})
    out["client_log"] = [e["message"] for e in c.get("/api/diag/logs?source=client").json()["entries"]]
    r = c.patch(f"/api/projects/{pid}", json={"fx": {"master": {"comp": {"on": True}}}}); out["patch_fx"] = r.status_code
    r = c.post("/api/diag/report", json={"project_id": pid})
    z = zipfile.ZipFile(io.BytesIO(r.content))
    out["report"] = (r.status_code, "report.json" in z.namelist(), any(n.endswith(".wav") for n in z.namelist()))
    rep = json.loads(z.read("report.json"))
    out["report_probe"] = rep["source_probe"]["verdict"]
    print("RESULT" + json.dumps(out, ensure_ascii=False))
''')


def test_api_smoke(tmp_path):
    env = dict(os.environ, BSS_WORKSPACE=str(tmp_path / "ws"), PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-c", SCRIPT, str(ROOT)], capture_output=True, text=True, encoding="utf-8",
                       env=env, cwd=str(tmp_path), timeout=300)
    line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT")), None)
    assert line, p.stdout[-2000:] + p.stderr[-4000:]
    out = json.loads(line[len("RESULT"):])
    assert out["upload"] == 200 and out["title"] == "テスト曲"
    assert out["probe"] == 200 and out["verdict"] == "ok" and out["tag_enc"] == ["cp932"]
    assert out["probe_broken"] == [200, "error"]
    assert out["reject"] == 400
    assert out["client"] == 200 and "test error" in out["client_log"]
    assert {"server_start", "upload", "upload_rejected", "probe"} <= set(out["events"])
    assert out["patch_fx"] == 200
    assert out["report"] == [200, True, False]  # report.json present, no audio inside
    assert out["report_probe"] == "ok"
    assert not (ROOT / "workspace" / "projects" / "should-not-exist").exists()

"""Security regression tests: secret/PII scanner and ignore rules for private outputs."""

import subprocess
import sys
from pathlib import Path

import security_scan as scan

ROOT = Path(__file__).resolve().parent.parent


def _fake(prefix: str, n: int) -> str:
    # built at runtime so no secret-looking literal is ever committed
    return prefix + ("Ab3" * n)[:n]


def test_scanner_detects_each_rule_without_leaking_values():
    samples = {
        "openai-key": _fake("sk-proj-", 40),
        "anthropic-key": _fake("sk-ant-", 40),
        "huggingface-token": _fake("hf_", 34),
        "github-token": _fake("ghp_", 36),
        "google-api-key": _fake("AIza", 35),
        "aws-access-key": "AKIA" + "ABCDEFGHIJ234567",
        "bearer-token": "Authorization: Bearer " + _fake("", 24),
        "assigned-secret": 'api_key = "' + _fake("", 20) + '"',
        "url-userinfo": "https://user:" + "pw123@example.org/x",
        "email": "someone.private" + "@" + "mail.example.jp",
        "claude-session-url": "https://claude.ai/code/" + "session_" + _fake("", 20),
        "windows-home-path": "C:" + "\\Users\\" + "someone\\Music",
        "private-key": "-----BEGIN " + "PRIVATE KEY-----",
    }
    for rule, text in samples.items():
        hits = scan.scan_text(f"x = 1\n{text}\n", "t", "f", [])
        assert any(h.rule == rule for h in hits), rule
        for h in hits:
            assert text not in str(h.__dict__)  # only rule / location / length / fingerprint


def test_allowed_addresses_and_private_terms():
    for ok in ("Co-Authored-By: Claude <noreply@anthropic.com>", "author: 209939878+hide-1925@users.noreply.github.com"):
        assert scan.scan_text(ok, "t", "f", []) == []
    hits = scan.scan_text("title: Secret Song", "t", "f", ["secret song"])
    assert [h.rule for h in hits] == ["private-term#1"]


def test_working_tree_is_clean():
    hits = scan.scan_working_tree(scan.private_terms())
    assert hits == [], [(h.rule, h.location, h.fp) for h in hits]


def test_private_outputs_are_git_ignored():
    for path in [".audit-private-terms.txt", "workspace/projects/x/project.json", "bss-diagnostics-20260101-000000.zip",
                 ".env", ".env.local", "id.pem", "credentials-google.json", "../x.bundle", ".audit-work/scan.json"]:
        if path.startswith(".."):
            continue
        r = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
        assert r.returncode == 0, f"{path} is not ignored"
    r = subprocess.run(["git", "check-ignore", "-q", ".env.example"], cwd=ROOT)
    assert r.returncode == 1  # the example stays committable


def test_scanner_cli_exit_code(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "security_scan.py")], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout[-2000:]

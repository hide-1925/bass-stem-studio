"""Secret / PII scanner for this repository (fallback when gitleaks / trufflehog are absent).

Never prints raw values: each hit is reported as rule, location, length and the first 8 hex
digits of its SHA-256 ("fp").

    python tools/security_scan.py                 # working tree (tracked + untracked, not ignored)
    python tools/security_scan.py --history       # + every blob reachable from any ref
    python tools/security_scan.py --metadata      # + commit author/committer/message of every commit
    python tools/security_scan.py --all --json out.json

Private terms (real name, employer, local user name, ...) are read from
`.audit-private-terms.txt` (one literal per line, git-ignored, never committed).
Exit code 1 when anything is found.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TERMS_FILE = ROOT / ".audit-private-terms.txt"

RULES: list[tuple[str, str, re.Pattern]] = [
    ("Critical", "private-key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("Critical", "openai-key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}")),
    ("Critical", "anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("Critical", "huggingface-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("Critical", "github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("Critical", "google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("Critical", "aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Critical", "slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("Critical", "jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("Critical", "bearer-token", re.compile(r"(?i)authorization:\s*bearer\s+[A-Za-z0-9._~+/-]{16,}")),
    ("Critical", "assigned-secret", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|password|passwd|client_secret|access_token|hf_token)\b\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']")),
    ("Critical", "url-userinfo", re.compile(r"\bhttps?://[^/\s:@]+:[^/\s:@]+@")),
    ("High", "email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("High", "claude-session-url", re.compile(r"https://claude\.ai/code/session_[A-Za-z0-9_-]+")),
    ("Medium", "windows-home-path", re.compile(r"(?i)\b[A-Z]:[\\/]+Users[\\/]+(?!Public\b|Default\b|<)[^\\/\s\"'<>]+")),
    ("Medium", "mac-linux-home-path", re.compile(r"(?:/Users|/home)/(?!runner\b|user\b|<)[A-Za-z0-9._-]+/")),
]
# Addresses that are fine to publish.
EMAIL_ALLOW = re.compile(r"(?i)^(noreply@anthropic\.com|[0-9]+\+[A-Za-z0-9-]+@users\.noreply\.github\.com|noreply@github\.com|"
                         r"[^@]+@example\.(com|org|net)|git@github\.com)$")
# Binary / huge files are not text-scanned.
SKIP_EXT = {".wav", ".mp3", ".flac", ".m4a", ".png", ".jpg", ".ico", ".onnx", ".pdf", ".zip", ".bundle"}


@dataclass
class Hit:
    severity: str
    rule: str
    scope: str
    location: str
    length: int
    fp: str

    def key(self):
        return (self.rule, self.location, self.fp)


def fp(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:8]


def private_terms() -> list[str]:
    if not TERMS_FILE.exists():
        return []
    return [t.strip() for t in TERMS_FILE.read_text(encoding="utf-8").splitlines() if t.strip() and not t.startswith("#")]


def scan_text(text: str, scope: str, location: str, terms: list[str]) -> list[Hit]:
    hits = []
    for sev, rule, rx in RULES:
        for m in rx.finditer(text):
            val = m.group(0)
            if rule == "email" and EMAIL_ALLOW.match(val):
                continue
            line = text.count("\n", 0, m.start()) + 1
            hits.append(Hit(sev, rule, scope, f"{location}:{line}", len(val), fp(val)))
    low = text.lower()
    for i, t in enumerate(terms, 1):
        start = 0
        while (pos := low.find(t.lower(), start)) >= 0:
            line = text.count("\n", 0, pos) + 1
            hits.append(Hit("High", f"private-term#{i}", scope, f"{location}:{line}", len(t), fp(t)))
            start = pos + len(t)
    return hits


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout.decode("utf-8", "replace")


def scan_working_tree(terms):
    files = git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0")
    hits = []
    for f in filter(None, files):
        p = ROOT / f
        if not p.is_file() or p.suffix.lower() in SKIP_EXT or p.stat().st_size > 5_000_000:
            continue
        if f == "tools/security_scan.py":
            continue  # contains the patterns themselves
        hits += scan_text(p.read_text(encoding="utf-8", errors="replace"), "working-tree", f, terms)
    return hits


def scan_history(terms):
    objs = {}
    for line in git("rev-list", "--all", "--objects").splitlines():
        sha, _, path = line.partition(" ")
        if path:
            objs.setdefault(sha, path)
    hits = []
    for sha, path in objs.items():
        if git("cat-file", "-t", sha).strip() != "blob" or Path(path).suffix.lower() in SKIP_EXT:
            continue
        if path == "tools/security_scan.py":
            continue
        data = subprocess.run(["git", "cat-file", "-p", sha], cwd=ROOT, capture_output=True).stdout
        if len(data) > 5_000_000 or b"\0" in data[:8000]:
            continue
        hits += scan_text(data.decode("utf-8", "replace"), "history-blob", f"{path}@{sha[:10]}", terms)
    return hits


def scan_metadata(terms):
    hits = []
    raw = git("log", "--all", "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B%x1e")
    for rec in filter(str.strip, raw.split("\x1e")):
        h, an, ae, cn, ce, body = (rec.strip("\n").split("\x1f") + [""] * 6)[:6]
        for field, val in (("author-name", an), ("author-email", ae), ("committer-name", cn), ("committer-email", ce),
                           ("message", body)):
            hits += scan_text(val, "commit-metadata", f"{h[:10]}:{field}", terms)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--metadata", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    terms = private_terms()
    hits = scan_working_tree(terms)
    scopes = {"working_tree": True}
    if args.history or args.all:
        hits += scan_history(terms)
        scopes["git_history"] = True
    if args.metadata or args.all:
        hits += scan_metadata(terms)
        scopes["commit_metadata"] = True
    seen, uniq = set(), []
    for h in hits:
        if h.key() not in seen:
            seen.add(h.key())
            uniq.append(h)
    summary: dict = {}
    for h in uniq:
        summary.setdefault(h.severity, {}).setdefault(h.rule, {}).setdefault(h.scope, 0)
        summary[h.severity][h.rule][h.scope] += 1
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"private terms loaded: {len(terms)}  scopes: {', '.join(scopes)}")
    for h in uniq:
        print(f"[{h.severity}] {h.rule:22s} {h.scope:16s} {h.location}  len={h.length} fp={h.fp}")
    print(f"total hits: {len(uniq)}")
    if args.json:
        args.json.write_text(json.dumps({"scopes": scopes, "private_terms": len(terms), "summary": summary,
                                         "hits": [h.__dict__ for h in uniq]}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if uniq else 0


if __name__ == "__main__":
    sys.exit(main())

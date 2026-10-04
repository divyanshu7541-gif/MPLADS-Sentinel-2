"""
Secret scan for CI: fails if any TRACKED file contains a credential.

    python ops/ci/secret_scan.py            # scans `git ls-files` (all files when not in a git repo)
    python ops/ci/secret_scan.py --history  # also every blob in every commit of the git history

Never prints a secret: a finding is reported as file, line (or commit) and the pattern's name only.

Patterns: Google API keys (AIza…) and Gemini/Google tokens (AQ.…), private-key blocks, AWS access
keys, GitHub and Slack tokens, OpenAI/Anthropic-style keys, JWTs. It also flags any tracked `.env`
file, and any assignment of a secret-named variable (GEMINI_API_KEY, JWT_SECRET, *_TOKEN, *_SECRET,
*_PASSWORD, API_KEY) to a literal value that isn't an obvious placeholder.

Exit code: 0 = clean, 1 = findings.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = {
    "google-api-key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "google-oauth-token": re.compile(r"\bAQ\.[0-9A-Za-z_\-]{20,}"),
    "private-key-block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "aws-access-key": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github-token": re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[0-9A-Za-z]{36,}\b|\bgithub_pat_[0-9A-Za-z_]{40,}"),
    "slack-token": re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}"),
    "openai-or-anthropic-key": re.compile(r"\bsk-(ant-)?[0-9A-Za-z_\-]{32,}"),
    "jwt": re.compile(r"\beyJ[0-9A-Za-z_\-]{10,}\.eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}"),
}
SECRET_NAME = (
    r"(?P<name>GEMINI_API_KEY|GOOGLE_API_KEY|JWT_SECRET|[A-Za-z0-9_]*(?:API|ACCESS|PRIVATE|SECRET)_KEY"
    r"|API_KEY|[A-Za-z0-9_]*_(?:TOKEN|SECRET|PASSWORD))"
)
# Code (.py/.js/.jsx/.ts): only a quoted string literal counts -- `api_key = get_key()` is code, not a secret.
CODE_ASSIGNMENT = re.compile(r"(?i)\b" + SECRET_NAME + r"\b\s*[:=]\s*['\"](?P<value>[^'\"]*)['\"]")
# Config (.env, YAML, INI, TOML, shell, Dockerfile, JSON): an unquoted value counts too.
CONFIG_ASSIGNMENT = re.compile(r"(?i)\b" + SECRET_NAME + r"\b['\"]?\s*[:=]\s*['\"]?(?P<value>[^\s'\"#,]*)")
CODE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
PLACEHOLDER = re.compile(
    r"(?i)^(|your[-_].*|<.*>|\$.*|x+|\*+|changeme|example.*|placeholder|none|null|sentinel|"
    r"ci-only-signing-key.*|\.\.\.|dummy.*|fake.*)$"
)
IGNORE_MARKER = "secret-scan: ignore"
# Reviewed exceptions: (path, variable) -> why it is not a secret.
ALLOWLIST = {
    ("frontend/src/pages/Login.jsx", "DEMO_PASSWORD"): "public demo credential printed on the login page "
    "itself; the login is client-side only and grants nothing on the API",
}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".parquet", ".shp", ".shx", ".dbf",
                 ".zip", ".gz", ".woff", ".woff2", ".ttf", ".pyc", ".dump"}


def _is_env_file(name: str) -> bool:
    """.env, .env.<anything> and <anything>.env are env files; *.example templates are not."""
    if name.endswith(".example"):
        return False
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def tracked_files(root: Path, walk: bool = False) -> list[Path]:
    if walk:  # every file under root, e.g. a build output (dist/) that git ignores
        return [p for p in root.rglob("*") if p.is_file()]
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
        return [root / p for p in out.stdout.decode().split("\0") if p]
    except (subprocess.CalledProcessError, FileNotFoundError):
        skip_dirs = {".git", "node_modules", "__pycache__", ".ruff_cache", ".pytest_cache", "dist", "data",
                     "geo_data_src", "ci-artifacts", "e2e-logs"}
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for f in filenames:
                # untracked-by-rule files (.gitignore): real .env files are not part of a commit
                if _is_env_file(f):
                    continue
                files.append(Path(dirpath) / f)
        return files


def scan_text(text: str, rel: str = "") -> list[tuple[int, str]]:
    code = Path(rel).suffix.lower() in CODE_SUFFIXES
    assign = CODE_ASSIGNMENT if code else CONFIG_ASSIGNMENT
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        if IGNORE_MARKER in line:
            continue
        for name, pat in PATTERNS.items():
            if pat.search(line):
                hits.append((n, name))
        for m in assign.finditer(line):
            # Markdown code spans (`NAME=...`) end in a backtick that is not part of the value
            var, value = m.group("name"), m.group("value").strip().strip("`")
            if (rel, var) in ALLOWLIST or PLACEHOLDER.match(value):
                continue
            hits.append((n, f"secret-assignment:{var}"))
    return hits


def scan_files(root: Path, walk: bool = False) -> list[str]:
    findings = []
    for p in tracked_files(root, walk):
        rel = p.relative_to(root).as_posix()
        if _is_env_file(p.name):
            findings.append(f"{rel}: tracked .env file")
            continue
        if p.suffix.lower() in SKIP_SUFFIXES or not p.is_file():
            continue
        try:
            text = p.read_text("utf-8", errors="ignore")
        except OSError:
            continue
        findings += [f"{rel}:{n}: {name}" for n, name in scan_text(text, rel)]
    return findings


def scan_history(root: Path) -> list[str]:
    """Every blob reachable from any ref, in every commit."""
    try:
        revs = subprocess.run(["git", "rev-list", "--all"], cwd=root, capture_output=True, check=True, text=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ["(no git repository: there is no history to scan)"]
    findings = []
    for commit in revs.stdout.split():
        tree = subprocess.run(["git", "ls-tree", "-r", commit], cwd=root, capture_output=True, text=True).stdout
        for line in tree.splitlines():
            meta, path = line.split("\t", 1)
            _mode, kind, blob = meta.split()
            if kind != "blob" or Path(path).suffix.lower() in SKIP_SUFFIXES:
                continue
            if _is_env_file(Path(path).name):
                findings.append(f"{commit[:10]}:{path}: committed .env file")
            data = subprocess.run(["git", "cat-file", "-p", blob], cwd=root, capture_output=True).stdout
            findings += [
                f"{commit[:10]}:{path}:{n}: {name}" for n, name in scan_text(data.decode(errors="ignore"), path)
            ]
    return sorted(set(findings))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--walk", action="store_true", help="scan every file under --root (git-ignored build output)")
    args = ap.parse_args()
    root = Path(args.root)
    findings = scan_files(root, args.walk)
    print(f"scanned {len(tracked_files(root, args.walk))} file(s) under {root.name or root}")
    if args.history:
        hist = scan_history(root)
        if hist and hist[0].startswith("(no git"):
            print(hist[0])
        else:
            findings += hist
    for f in findings:
        print(f"SECRET? {f}")
    print(f"secret scan: {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

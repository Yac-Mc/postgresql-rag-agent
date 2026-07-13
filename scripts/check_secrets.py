#!/usr/bin/env python3
"""Standalone secret scanner for tracked repository files.

Scans every file tracked by git for hardcoded-credential patterns:
- Postgres/MySQL-style connection strings with a literal password
  (e.g. postgresql://user:realpassword@host)
- Known API key prefixes (Groq `gsk_`, Gemini `AIza`, GitLab `glpat-`)

Usage:
    python scripts/check_secrets.py

Exits with code 1 and prints `file:line: pattern` for each finding.
Exits with code 0 if no findings.

No third-party dependencies — stdlib only.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# (label, compiled regex). Groups are used to extract the matched secret span.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "connection-string-with-password",
        re.compile(
            r"(?:postgres(?:ql)?|mysql)://"
            r"(?P<user>[A-Za-z0-9_.\-]+):"
            r"(?P<password>[^@\s'\"]+)"
            r"@(?P<host>[A-Za-z0-9_.\-]+)"
        ),
    ),
    ("groq-api-key", re.compile(r"\bgsk_[A-Za-z0-9]{10,}\b")),
    ("gemini-api-key", re.compile(r"\bAIza[A-Za-z0-9_\-]{10,}\b")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{10,}\b")),
]

# Extensions/files that are binary or otherwise never worth scanning.
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".tar",
    ".woff", ".woff2", ".ttf", ".eot", ".lock", ".db", ".sqlite", ".sqlite3",
}

# Obvious placeholder / non-secret tokens that should never be flagged.
PLACEHOLDER_TOKENS = {
    "password", "passwd", "pass", "changeme", "your_password", "your-password",
    "xxx", "xxxx", "placeholder", "example", "secret", "<password>",
    "your_api_key", "your_api_key_here", "api_key", "<api_key>", "todo",
    "",
}

# Local/demo hosts commonly used in .env.example files (docker-compose, etc.).
# A user:pass@localhost combo is a documented local dev default, not a real secret.
LOCAL_DEV_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "postgres-local", "db"}


def get_tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [Path(line) for line in result.stdout.splitlines() if line.strip()]


def is_placeholder(password: str) -> bool:
    normalized = password.strip().strip("'\"").lower()
    if normalized in PLACEHOLDER_TOKENS:
        return True
    # Template-style placeholders like ${DB_PASSWORD} or {{password}} or <password>
    if re.fullmatch(r"[\$\{\}<>%\[\]A-Za-z0-9_\-\.]*", normalized) and (
        normalized.startswith("$") or normalized.startswith("{") or normalized.startswith("<")
    ):
        return True
    return False


def is_env_lookup_line(line: str) -> bool:
    """Skip lines that resolve secrets dynamically instead of hardcoding them."""
    return bool(re.search(r"os\.getenv\s*\(|os\.environ(\.get)?\s*\[|os\.environ\.get\s*\(", line))


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError):
        return findings

    for lineno, line in enumerate(text.splitlines(), start=1):
        if is_env_lookup_line(line):
            continue

        for label, pattern in PATTERNS:
            for match in pattern.finditer(line):
                if label == "connection-string-with-password":
                    password = match.group("password")
                    user = match.group("user")
                    host = match.group("host").lower()
                    if is_placeholder(password):
                        continue
                    # user==password and localhost/demo host => documented local dev default
                    if host in LOCAL_DEV_HOSTS and password.strip().lower() == user.strip().lower():
                        continue
                findings.append((lineno, label, line.strip()))

    return findings


def main() -> int:
    try:
        tracked_files = get_tracked_files()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"error: could not list tracked files via git: {exc}", file=sys.stderr)
        return 2

    all_findings: list[tuple[Path, int, str, str]] = []

    for file_path in tracked_files:
        if file_path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if not file_path.exists():
            continue
        for lineno, label, line in scan_file(file_path):
            all_findings.append((file_path, lineno, label, line))

    if not all_findings:
        print("check_secrets: no hardcoded secrets found.")
        return 0

    print("check_secrets: potential hardcoded secrets found:\n")
    for file_path, lineno, label, line in all_findings:
        print(f"{file_path}:{lineno}: [{label}] {line}")

    print(f"\n{len(all_findings)} finding(s). Remove or replace with environment variables before committing.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

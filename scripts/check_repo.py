"""Pre-commit repository hygiene check.

Lists the files that would be published (tracked and untracked files not
ignored by .gitignore) and fails when any of them is a forbidden artifact
(environment files, caches, logs, runtime data, OS junk) or contains
something that looks like a secret.

Usage:

    python scripts/check_repo.py

Exit code 0 when clean, 1 when findings exist. The tool only reads files.
A line can be exempted from the secret scan with the marker ``check-repo: allow``.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_SCAN_BYTES = 1_000_000
ALLOW_MARKER = "check-repo: allow"

FORBIDDEN_PATH_PATTERNS: tuple[tuple[str, str], ...] = (
    (".env", "environment file with secrets"),
    (".env.*", "environment file with secrets"),
    (".venv/*", "virtual environment"),
    ("venv/*", "virtual environment"),
    ("*__pycache__/*", "Python bytecode cache"),
    ("*.pyc", "Python bytecode"),
    ("*.pyo", "Python bytecode"),
    ("*.pytest_cache/*", "pytest cache"),
    ("*.mypy_cache/*", "tool cache"),
    ("*.ruff_cache/*", "tool cache"),
    ("*.log", "log file"),
    ("logs/*", "log directory"),
    ("data/*", "runtime data directory"),
    ("*.egg-info/*", "build metadata"),
    ("build/*", "build output"),
    ("dist/*", "build output"),
    ("wheelhouse/*", "offline wheel cache"),
    ("*.whl", "platform-specific wheel"),
    ("*.DS_Store", "macOS metadata"),
    ("*Thumbs.db", "Windows metadata"),
    ("*desktop.ini", "Windows metadata"),
    ("*.coverage", "coverage output"),
    ("htmlcov/*", "coverage output"),
)
ALLOWED_PATHS = frozenset({".env.example"})
ALLOWED_JSONL_PREFIXES = ("tests/fixtures/", "examples/")

SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "GitHub token"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "Slack token"),
    (re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{24,}"), "bearer token"),
    (
        re.compile(
            r"(?i)\b[A-Z0-9_]*(?:API_KEY|APIKEY|SECRET|TOKEN|PASSWORD|PASSWD|SALT)\b\s*[:=]\s*['\"]?([^\s'\"#,]{8,})"
        ),
        "credential assignment",
    ),
)
PLACEHOLDER_HINTS = (
    "change-me", "changeme", "replace", "example", "placeholder", "dummy",
    "test", "fake", "xxxx", "<", "${", "environ", "getenv", "settings.", "none", "null",
)

REQUIRED_GITIGNORE_ENTRIES = (".env", ".venv/", "__pycache__/", ".pytest_cache/", "data/", "*.log")


def list_publishable_files(root: Path) -> tuple[list[str], str]:
    """Return repository-relative POSIX paths and the listing mode used."""
    if (root / ".git").exists():
        try:
            output = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                cwd=root,
                capture_output=True,
                check=True,
            ).stdout
            files = [item for item in output.decode("utf-8").split("\0") if item]
            return sorted(files), "git"
        except (OSError, subprocess.CalledProcessError):
            pass
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in {".git", ".venv"}]
        for filename in filenames:
            files.append(Path(dirpath, filename).relative_to(root).as_posix())
    return sorted(files), "filesystem"


def path_findings(relative_path: str) -> list[str]:
    if relative_path in ALLOWED_PATHS:
        return []
    posix = PurePosixPath(relative_path)
    findings = []
    for pattern, reason in FORBIDDEN_PATH_PATTERNS:
        if fnmatch.fnmatch(relative_path, pattern) or fnmatch.fnmatch(posix.name, pattern):
            findings.append(reason)
            break
    if posix.suffix == ".jsonl" and not relative_path.startswith(ALLOWED_JSONL_PREFIXES):
        findings.append("runtime JSONL data outside tests/fixtures or examples")
    return findings


def secret_findings(path: Path) -> list[str]:
    try:
        if path.stat().st_size > MAX_SCAN_BYTES:
            return []
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    findings = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        if ALLOW_MARKER in line:
            continue
        for pattern, label in SECRET_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            value = match.group(1) if match.groups() else match.group(0)
            if label == "credential assignment" and _is_placeholder(value):
                continue
            findings.append(f"line {line_number}: possible {label}")
    return findings


def _is_placeholder(value: str) -> bool:
    """Treat documented placeholders and code expressions as non-secrets."""
    lowered = value.lower()
    if any(hint in lowered for hint in PLACEHOLDER_HINTS):
        return True
    if any(marker in value for marker in ("(", "[", "{")):  # calls, indexing, string interpolation
        return True
    # Plain identifiers such as `settings_value` or `self.api_key` contain no digits.
    return re.fullmatch(r"[A-Za-z_][A-Za-z_.]*", value) is not None


def gitignore_findings(root: Path) -> list[str]:
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return [".gitignore is missing"]
    entries = {line.strip() for line in gitignore.read_text(encoding="utf-8").splitlines()}
    return [f".gitignore lacks entry '{entry}'" for entry in REQUIRED_GITIGNORE_ENTRIES if entry not in entries]


def main() -> int:
    files, mode = list_publishable_files(PROJECT_ROOT)
    problems: list[str] = list(gitignore_findings(PROJECT_ROOT))
    for relative_path in files:
        for reason in path_findings(relative_path):
            problems.append(f"{relative_path}: forbidden file ({reason})")
        for finding in secret_findings(PROJECT_ROOT / relative_path):
            problems.append(f"{relative_path}: {finding}")

    print(f"[check_repo] mode={mode} files_checked={len(files)}")
    if problems:
        print("[check_repo] FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("[check_repo] OK: no forbidden files or secrets detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

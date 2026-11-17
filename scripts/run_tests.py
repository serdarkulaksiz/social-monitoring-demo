"""Cache-free test runner.

Runs pytest with the cacheprovider plugin disabled and bytecode generation
turned off, then verifies that no forbidden cache artifacts were created.

The runner never deletes files. If a forbidden artifact exists before the run
or appears during the run, the runner fails and reports the path.

Usage (from any directory, on Windows or macOS):

    python scripts/run_tests.py [extra pytest arguments]
    python scripts/run_tests.py --check-only

Exit codes: pytest's own exit code, or 3 when a cache artifact check fails.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKIPPED_DIRS = frozenset({".git", ".venv", "venv", "env", "node_modules"})
CACHE_CHECK_FAILED = 3


def find_pytest_caches(root: Path) -> list[Path]:
    return [path for path in _walk_dirs(root) if path.name == ".pytest_cache"]


def find_bytecode(root: Path) -> set[Path]:
    """Return all __pycache__ directories and .pyc/.pyo files inside the project."""
    found: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIPPED_DIRS]
        current = Path(dirpath)
        if current.name == "__pycache__":
            found.add(current)
        for filename in filenames:
            if filename.endswith((".pyc", ".pyo")):
                found.add(current / filename)
    return found


def _walk_dirs(root: Path) -> list[Path]:
    result: list[Path] = []
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIPPED_DIRS]
        result.extend(Path(dirpath) / name for name in dirnames)
    return result


def resolve_interpreter() -> str:
    """Prefer the project's own virtual environment when it exists."""
    candidates = [
        PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",  # Windows layout
        PROJECT_ROOT / ".venv" / "bin" / "python",  # POSIX layout
    ]
    running_in_project_venv = Path(sys.prefix).resolve() == (PROJECT_ROOT / ".venv").resolve()
    if not running_in_project_venv:
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    return sys.executable


def build_environment() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    # Make sure no inherited option re-enables the cache plugin.
    env.pop("PYTEST_ADDOPTS", None)
    return env


def report(paths: list[Path] | set[Path], message: str) -> None:
    print(f"[run_tests] {message}", file=sys.stderr)
    for path in sorted(paths):
        print(f"  - {path.relative_to(PROJECT_ROOT)}", file=sys.stderr)


def main(argv: list[str]) -> int:
    check_only = "--check-only" in argv
    pytest_args = [arg for arg in argv if arg != "--check-only"]

    existing_caches = find_pytest_caches(PROJECT_ROOT)
    if existing_caches:
        report(existing_caches, "FAILED: .pytest_cache exists before the test run.")
        return CACHE_CHECK_FAILED
    if check_only:
        print("[run_tests] OK: no .pytest_cache directory found.")
        return 0

    bytecode_before = find_bytecode(PROJECT_ROOT)
    command = [resolve_interpreter(), "-B", "-m", "pytest", "-p", "no:cacheprovider", *pytest_args]
    print(f"[run_tests] {' '.join(command)}")
    completed = subprocess.run(command, cwd=PROJECT_ROOT, env=build_environment(), check=False)

    failed = False
    created_caches = find_pytest_caches(PROJECT_ROOT)
    if created_caches:
        report(created_caches, "FAILED: .pytest_cache was created during the test run.")
        failed = True
    new_bytecode = find_bytecode(PROJECT_ROOT) - bytecode_before
    if new_bytecode:
        report(new_bytecode, "FAILED: bytecode files were created during the test run.")
        failed = True
    if bytecode_before:
        report(bytecode_before, "WARNING: bytecode existed before the run (not created by tests).")

    if failed:
        return CACHE_CHECK_FAILED
    print("[run_tests] Cache check passed: no .pytest_cache and no new bytecode.")
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

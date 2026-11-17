"""Tests for scripts/check_repo.py (pre-publication hygiene check)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_repo.py"


@pytest.fixture(scope="module")
def check_repo() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_repo_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


@pytest.mark.parametrize(
    "path",
    [".env", ".env.production", ".venv/bin/python", "src/pkg/__pycache__/x.pyc", ".pytest_cache/v/cache", "app.log",
     "data/posts.jsonl", "runtime.jsonl", "wheelhouse/x.whl", ".DS_Store", "docs/Thumbs.db"],
)
def test_forbidden_paths(check_repo: ModuleType, path: str) -> None:
    assert check_repo.path_findings(path)


@pytest.mark.parametrize("path", [".env.example", "src/pkg/module.py", "tests/fixtures/sample.jsonl", "examples/synthetic_posts.jsonl", "docs/API_CONTRACT.md"])
def test_allowed_paths(check_repo: ModuleType, path: str) -> None:
    assert check_repo.path_findings(path) == []


@pytest.mark.parametrize(
    "line",
    [
        "MONITOR_API_KEY=Xk29fjq8ZpL0vB7nRt3Wy5Hd",  # check-repo: allow (test fixture)
        'password = "S3cr3tPassw0rd!"',  # check-repo: allow (test fixture)
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",  # check-repo: allow (test fixture)
        "-----BEGIN RSA PRIVATE KEY-----",  # check-repo: allow (test fixture)
        "aws = AKIAABCDEFGHIJKLMNOP",  # check-repo: allow (test fixture)
    ],
)
def test_secrets_detected(check_repo: ModuleType, tmp_path: Path, line: str) -> None:
    sample = tmp_path / "sample.txt"
    sample.write_text(line + "\n", encoding="utf-8")
    assert check_repo.secret_findings(sample)


@pytest.mark.parametrize(
    "line",
    [
        "MONITOR_API_KEY=change-me-generate-a-random-value",
        "api_key = settings.api_key.get_secret_value()",
        'env_file.write_text(f"MONITOR_API_KEY={KEY}")',
        "TOKEN = os.environ['TOKEN']",
        "secret=Xk29fjq8ZpL0vB7nRt3Wy5Hd  # check-repo: allow",  # check-repo: allow (test fixture)
    ],
)
def test_placeholders_and_code_are_not_secrets(check_repo: ModuleType, tmp_path: Path, line: str) -> None:
    sample = tmp_path / "sample.txt"
    sample.write_text(line + "\n", encoding="utf-8")
    assert check_repo.secret_findings(sample) == []

"""Guards the mandatory 'no .pytest_cache' configuration."""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_pytest_ini_disables_cacheprovider() -> None:
    parser = configparser.ConfigParser()
    parser.read(PROJECT_ROOT / "pytest.ini", encoding="utf-8")
    assert "-p no:cacheprovider" in parser["pytest"]["addopts"]
    assert parser["pytest"]["testpaths"].strip() == "tests"


def test_cacheprovider_plugin_is_not_loaded(request: pytest.FixtureRequest) -> None:
    assert not request.config.pluginmanager.has_plugin("cacheprovider")
    assert not hasattr(request.config, "cache")


def test_no_pytest_cache_directory_exists() -> None:
    assert not (PROJECT_ROOT / ".pytest_cache").exists()


def test_test_runner_disables_cache_and_bytecode() -> None:
    runner = (PROJECT_ROOT / "scripts" / "run_tests.py").read_text(encoding="utf-8")
    assert '"-p", "no:cacheprovider"' in runner
    assert '"-B"' in runner
    assert 'env["PYTHONDONTWRITEBYTECODE"] = "1"' in runner

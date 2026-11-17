from __future__ import annotations

from pathlib import Path

import pytest

from social_incident_monitor.application.errors import ConfigurationError
from social_incident_monitor.config import Settings
from social_incident_monitor.container import build_classifier

KEY = "k" * 32


def test_defaults_from_minimal_environment() -> None:
    settings = Settings.from_environment(environ={"MONITOR_API_KEY": KEY})
    assert settings.host == "127.0.0.1"
    assert settings.detection_windows_minutes == [5, 15, 30]
    [threshold] = settings.detection_thresholds
    assert (threshold.window_minutes, threshold.warning_unique_users, threshold.critical_unique_users) == (15, 10, 25)
    assert settings.accept_synthetic is False
    assert settings.classifier == "rule_based"


def test_baseline_settings_are_parsed() -> None:
    settings = Settings.from_environment(
        environ={"MONITOR_API_KEY": KEY, "MONITOR_BASELINE_MIN_USERS": "3:6", "MONITOR_BASELINE_P_VALUES": "0.01:0.0001"}
    )
    assert settings.baseline_enabled is True and settings.baseline_windows_minutes == [5, 15]
    assert settings.baseline_min_users == (3, 6) and settings.baseline_p_values == (0.01, 0.0001)


def test_custom_windows_and_thresholds() -> None:
    settings = Settings.from_environment(
        environ={
            "MONITOR_API_KEY": KEY,
            "MONITOR_DETECTION_WINDOWS_MINUTES": "10,20",
            "MONITOR_DETECTION_THRESHOLDS": "10:5:9, 20:8:15",
        }
    )
    assert [t.window_minutes for t in settings.detection_thresholds] == [10, 20]


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        ({}, "MONITOR_API_KEY"),
        ({"MONITOR_API_KEY": "short"}, "at least 24"),
        ({"MONITOR_API_KEY": KEY, "MONITOR_DETECTION_THRESHOLDS": "15:30:10"}, "MONITOR_DETECTION_THRESHOLDS"),
        ({"MONITOR_API_KEY": KEY, "MONITOR_BASELINE_MIN_USERS": "9:8"}, "baseline_min_users"),
        ({"MONITOR_API_KEY": KEY, "MONITOR_BASELINE_MIN_USERS": "4"}, "MONITOR_BASELINE_MIN_USERS"),
        ({"MONITOR_API_KEY": KEY, "MONITOR_BASELINE_P_VALUES": "0.00001:0.001"}, "baseline_p_values"),
        ({"MONITOR_API_KEY": KEY, "MONITOR_BASELINE_MIN_HISTORY_DAYS": "20"}, "must not exceed"),
        ({"MONITOR_API_KEY": KEY, "MONITOR_BASELINE_WINDOWS_MINUTES": "0,15"}, "baseline_windows_minutes"),
        ({"MONITOR_API_KEY": KEY, "MONITOR_DETECTION_THRESHOLDS": "60:10:20"}, "not listed"),
        ({"MONITOR_API_KEY": KEY, "MONITOR_PORT": "99999"}, "MONITOR_PORT"),
        ({"MONITOR_API_KEY": KEY, "MONITOR_TYPO_SETTING": "1"}, "unknown setting"),
    ],
)
def test_invalid_configuration_is_reported(environ: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        Settings.from_environment(environ=environ)


def test_describe_masks_api_key() -> None:
    described = Settings.from_environment(environ={"MONITOR_API_KEY": KEY}).describe()
    assert described["api_key"] == "***"
    assert KEY not in str(described)


def test_env_file_is_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MONITOR_API_KEY", raising=False)
    monkeypatch.delenv("MONITOR_PORT", raising=False)
    env_file = tmp_path / "test.env"
    env_file.write_text(f"MONITOR_API_KEY={KEY}\nMONITOR_PORT=8123\n", encoding="utf-8")
    settings = Settings.from_environment(env_file)
    monkeypatch.delenv("MONITOR_API_KEY", raising=False)
    monkeypatch.delenv("MONITOR_PORT", raising=False)
    assert settings.port == 8123


def test_llm_classifier_without_provider_fails_fast() -> None:
    settings = Settings.from_environment(environ={"MONITOR_API_KEY": KEY, "MONITOR_CLASSIFIER": "llm"})
    with pytest.raises(ConfigurationError, match="no LLM provider"):
        build_classifier(settings)

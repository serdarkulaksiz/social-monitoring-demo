"""Configuration loading and validation.

Values come from process environment variables, optionally pre-loaded from a ``.env``
file (existing environment variables take precedence). Every setting has a safe
default except ``MONITOR_API_KEY``, which is required.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator

from .application.detection.rules import WindowThreshold
from .application.errors import ConfigurationError

# The text model shipped with the code, trained on the first labelled X data (see docs/MODEL.md).
# Used until promote-model writes <data_dir>/models/active.json.
BUNDLED_TEXT_MODEL = Path(__file__).resolve().parent / "classification" / "model" / "bundled" / "text-model.json"

ENV_PREFIX = "MONITOR_"
MIN_API_KEY_LENGTH = 24
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def _parse_str_list(value: object) -> object:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


def _parse_int_list(value: object) -> object:
    if isinstance(value, str):
        return [int(part) for part in value.split(",") if part.strip()]
    return value


def _parse_pair(value: object) -> object:
    """``"4:8"`` → (4, 8); ``"0.001:0.00001"`` → (0.001, 0.00001)."""
    if not isinstance(value, str):
        return value
    pieces = [piece.strip() for piece in value.split(":")]
    if len(pieces) != 2:
        raise ValueError("must have the form warning:critical")
    return tuple(pieces)


def _parse_thresholds(value: object) -> object:
    """``"15:10:25,30:20:40"`` → window:warning:critical triples."""
    if not isinstance(value, str):
        return value
    thresholds = []
    for part in (item.strip() for item in value.split(",")):
        if not part:
            continue
        pieces = part.split(":")
        if len(pieces) != 3:
            raise ValueError(f"threshold '{part}' must have the form window:warning:critical")
        window, warning, critical = (int(piece) for piece in pieces)
        thresholds.append(WindowThreshold(window, warning, critical))
    return thresholds


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    api_key: SecretStr
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    data_dir: Path = Path("data")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    accept_synthetic: bool = False
    classifier: Literal["rule_based", "hybrid", "llm"] = "rule_based"
    classification_rules_path: Path | None = None
    # Text model used with MONITOR_CLASSIFIER=hybrid (default: <data_dir>/models/active.json,
    # or the bundled model while no model has been promoted).
    text_model_path: Path | None = None
    bundled_text_model: bool = True
    # "active": the model may change the rules' answer; "shadow": it is computed and stored but posts keep the rules' answer.
    text_model_mode: Literal["shadow", "active"] = "active"
    text_model_rescue_p: float = Field(default=0.6, gt=0.5, le=1.0)
    text_model_veto_p: float = Field(default=0.7, gt=0.5, le=1.0)
    detection_windows_minutes: list[int] = Field(default_factory=lambda: [5, 15, 30])
    detection_thresholds: list[WindowThreshold] = Field(default_factory=lambda: [WindowThreshold(15, 10, 25)])
    baseline_enabled: bool = True
    baseline_history_days: int = Field(default=14, ge=1, le=90)
    baseline_min_history_days: int = Field(default=7, ge=1, le=90)
    baseline_windows_minutes: list[int] = Field(default_factory=lambda: [5, 15])
    baseline_min_users: tuple[int, int] = (4, 8)
    baseline_p_values: tuple[float, float] = (0.001, 0.00001)
    baseline_min_expected_users: float = Field(default=0.1, gt=0)
    reputation_enabled: bool = True
    reputation_window_minutes: int = Field(default=60, ge=5, le=1440)
    reputation_min_users: tuple[int, int] = (3, 10)
    reputation_amplification: tuple[int, int] = (50, 500)
    topics_window_minutes: int = Field(default=120, ge=15, le=1440)
    topics_history_days: int = Field(default=7, ge=1, le=60)
    topics_min_users: int = Field(default=3, ge=2)
    incident_resolve_after_minutes: int = Field(default=30, ge=1)
    evaluation_interval_seconds: int = Field(default=60, ge=5)
    collector_stale_after_minutes: int = Field(default=15, ge=0, description="0 disables stale detection")
    collector_failure_threshold: int = Field(default=3, ge=1)
    max_clock_skew_seconds: int = Field(default=300, ge=0)
    dashboard_enabled: bool = True
    # Who may acknowledge/resolve/label from the dashboard while there is no login:
    # "local" = requests from this computer only, "api_key" = the X-API-Key, "off" = read-only.
    dashboard_actions: Literal["local", "api_key", "off"] = "local"
    # Host names the server answers to (protects the local dashboard against DNS rebinding).
    # Empty = loopback names only; required when MONITOR_HOST is not a loopback address.
    allowed_hosts: list[str] = Field(default_factory=list)

    @field_validator("api_key")
    @classmethod
    def _api_key_strength(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < MIN_API_KEY_LENGTH:
            raise ValueError(f"must be at least {MIN_API_KEY_LENGTH} characters")
        return value

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _hosts(cls, value: object) -> object:
        return _parse_str_list(value)

    @field_validator("detection_windows_minutes", "baseline_windows_minutes", mode="before")
    @classmethod
    def _windows(cls, value: object) -> object:
        return _parse_int_list(value)

    @field_validator("baseline_min_users", "baseline_p_values", "reputation_min_users", "reputation_amplification", mode="before")
    @classmethod
    def _pairs(cls, value: object) -> object:
        return _parse_pair(value)

    @field_validator("detection_thresholds", mode="before")
    @classmethod
    def _thresholds(cls, value: object) -> object:
        return _parse_thresholds(value)

    @model_validator(mode="after")
    def _consistency(self) -> Settings:
        if not self.detection_windows_minutes or any(minutes <= 0 for minutes in self.detection_windows_minutes):
            raise ValueError("detection_windows_minutes must contain positive integers")
        unknown = {t.window_minutes for t in self.detection_thresholds} - set(self.detection_windows_minutes)
        if unknown:
            raise ValueError(f"thresholds reference windows not listed in detection_windows_minutes: {sorted(unknown)}")
        if not self.detection_thresholds:
            raise ValueError("at least one detection threshold is required")
        if not self.baseline_windows_minutes or any(minutes <= 0 for minutes in self.baseline_windows_minutes):
            raise ValueError("baseline_windows_minutes must contain positive integers")
        if self.baseline_min_history_days > self.baseline_history_days:
            raise ValueError("baseline_min_history_days must not exceed baseline_history_days")
        warning_users, critical_users = self.baseline_min_users
        if not 0 < warning_users <= critical_users:
            raise ValueError("baseline_min_users must be warning:critical with 0 < warning <= critical")
        warning_p, critical_p = self.baseline_p_values
        if not 0 < critical_p <= warning_p < 1:
            raise ValueError("baseline_p_values must be warning:critical with 0 < critical <= warning < 1")
        if not self.allowed_hosts and self.host not in LOOPBACK_HOSTS:
            raise ValueError("allowed_hosts (MONITOR_ALLOWED_HOSTS) must list the server's host names when MONITOR_HOST is not a loopback address")
        for name in ("reputation_min_users", "reputation_amplification"):
            warning, critical = getattr(self, name)
            if not 0 < warning <= critical:
                raise ValueError(f"{name} must be warning:critical with 0 < warning <= critical")
        return self

    @classmethod
    def from_environment(cls, env_file: Path | None = None, environ: Mapping[str, str] | None = None) -> Settings:
        if environ is None:
            if env_file is not None:
                if not env_file.is_file():
                    raise ConfigurationError(f"env file not found: {env_file}")
                load_dotenv(env_file, override=False, encoding="utf-8")
            else:
                load_dotenv(Path.cwd() / ".env", override=False, encoding="utf-8")
            environ = os.environ
        values = {
            key[len(ENV_PREFIX):].lower(): value
            for key, value in environ.items()
            if key.startswith(ENV_PREFIX) and value != ""
        }
        try:
            return cls.model_validate(values)
        except ValidationError as exc:
            raise ConfigurationError(_format_errors(exc)) from None

    @property
    def active_text_model_path(self) -> Path:
        """Where promote-model writes the model in use."""
        return self.text_model_path or self.data_dir / "models" / "active.json"

    @property
    def resolved_text_model_path(self) -> Path:
        """The model file to load: the promoted one, else the bundled one."""
        active = self.active_text_model_path
        if self.text_model_path is None and not active.exists() and self.bundled_text_model:
            return BUNDLED_TEXT_MODEL
        return active

    @property
    def trusted_hosts(self) -> list[str]:
        return list(self.allowed_hosts) if self.allowed_hosts else list(LOOPBACK_HOSTS)

    def describe(self) -> dict[str, object]:
        """Settings safe to print or log (secrets masked)."""
        data = self.model_dump(mode="json")
        data["api_key"] = "***"
        data["detection_thresholds"] = [
            f"{t.window_minutes}:{t.warning_unique_users}:{t.critical_unique_users}" for t in self.detection_thresholds
        ]
        return data


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        name = ENV_PREFIX + "_".join(str(part) for part in error["loc"][:1]).upper() if error["loc"] else "settings"
        message = error["msg"]
        if error["type"] == "extra_forbidden":
            message = "unknown setting"
        lines.append(f"{name}: {message}")
    return "invalid configuration: " + "; ".join(lines)

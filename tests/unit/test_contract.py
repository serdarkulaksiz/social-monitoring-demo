"""Contract v1 checks. The schema file is shared verbatim with social-data-collector."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from social_incident_monitor.api.schemas import (
    CONTRACT_VERSION,
    CollectorRunReportV1,
    EngagementBatchRequest,
    EngagementUpdateV1,
    EngagementV1,
    PostsBatchRequest,
    QueryRunResultV1,
    SocialPostV1,
)

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
SCHEMA_FILE = CONTRACTS / "collector-monitor.v1.schema.json"
# Identical value is pinned in the collector repository. Changing the schema requires a
# new contract version and updating both repositories.
SCHEMA_SHA256 = "03712167e823e93e1b10bcf2d91a6bcf1e7497f9537c8f7235a48e13abf52041"


def _schema() -> dict:  # type: ignore[type-arg]
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def test_schema_file_matches_pinned_hash() -> None:
    normalized = SCHEMA_FILE.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(normalized).hexdigest() == SCHEMA_SHA256


@pytest.mark.parametrize(
    ("definition", "model"),
    [
        ("SocialPost", SocialPostV1),
        ("PostsBatchRequest", PostsBatchRequest),
        ("Engagement", EngagementV1),
        ("EngagementUpdate", EngagementUpdateV1),
        ("EngagementBatchRequest", EngagementBatchRequest),
        ("QueryRunResult", QueryRunResultV1),
        ("CollectorRunReport", CollectorRunReportV1),
    ],
)
def test_models_match_contract_fields(definition: str, model: type[BaseModel]) -> None:
    contract = _schema()["$defs"][definition]
    generated = model.model_json_schema()
    assert set(generated["properties"]) == set(contract["properties"])
    assert set(generated.get("required", [])) == set(contract["required"])


def test_monitor_speaks_the_current_contract_version() -> None:
    assert _schema()["contract_version"] == CONTRACT_VERSION


def test_example_payloads_validate() -> None:
    batch = PostsBatchRequest.model_validate_json((CONTRACTS / "examples" / "posts-batch.v1.json").read_text(encoding="utf-8"))
    assert {SocialPostV1.model_validate(post).schema_version for post in batch.posts} == {"1.0", "1.1"}
    engagement = EngagementBatchRequest.model_validate_json(
        (CONTRACTS / "examples" / "engagement-batch.v1.json").read_text(encoding="utf-8")
    )
    for update in engagement.updates:
        EngagementUpdateV1.model_validate(update)
    CollectorRunReportV1.model_validate_json((CONTRACTS / "examples" / "collector-run.v1.json").read_text(encoding="utf-8"))

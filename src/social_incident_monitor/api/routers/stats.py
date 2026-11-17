"""Aggregated statistics."""

from __future__ import annotations

from fastapi import APIRouter

from ..dependencies import Queries
from ..schemas import StatsOut

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])


@router.get("", response_model=StatsOut)
def get_stats(queries: Queries) -> StatsOut:
    return StatsOut.model_validate(queries.stats())

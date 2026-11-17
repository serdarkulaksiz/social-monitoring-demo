"""Liveness endpoint (no authentication, no data)."""

from __future__ import annotations

from fastapi import APIRouter

from ... import __version__
from ..schemas import CONTRACT_VERSION, HealthOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(status="ok", version=__version__, contract_version=CONTRACT_VERSION)

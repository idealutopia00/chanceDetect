"""Pydantic request/response schemas for the FastAPI REST API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from chancedetect.strategies.engine import ScanRequestConfig


class PresetCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    config: dict[str, Any]


class SyncTriggerRequest(BaseModel):
    mode: Literal["daily", "metadata", "full", "features"] = "daily"
    full_refresh_bars: bool = False
    force_metadata: bool = False
    sample_limit: int | None = Field(default=None, ge=10, le=5000)


class ReplayRequest(BaseModel):
    config: ScanRequestConfig = Field(default_factory=ScanRequestConfig)
    lookback_days: int = Field(default=500, ge=60, le=2000)

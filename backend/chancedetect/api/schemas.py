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


class PositionCreateRequest(BaseModel):
    otc_code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    otc_name: str | None = Field(default=None, max_length=120)
    exchange_symbol: str | None = Field(default=None, max_length=20)
    trade_date: str | None = Field(default=None, description="YYYY-MM-DD, defaults to today (Asia/Shanghai)")
    nav: float | None = Field(default=None, gt=0)
    amount: float | None = Field(default=None, gt=0)
    shares: float | None = Field(default=None, gt=0)
    fee: float = Field(default=0.0, ge=0.0)
    notes: str = Field(default="", max_length=2000)
    lot_notes: str = Field(default="", max_length=1000)
    entry_signal_snapshot: dict[str, Any] = Field(default_factory=dict)
    custom_stop_loss_pct: float | None = Field(default=None, ge=-99.0, le=0.0)
    custom_trailing_drawdown_pct: float | None = Field(default=None, ge=-99.0, le=0.0)
    custom_take_profit_pct: float | None = Field(default=None, gt=0.0, le=1000.0)


class PositionUpdateRequest(BaseModel):
    otc_name: str | None = Field(default=None, max_length=120)
    exchange_symbol: str | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=2000)
    custom_stop_loss_pct: float | None = Field(default=None, ge=-99.0, le=0.0)
    custom_trailing_drawdown_pct: float | None = Field(default=None, ge=-99.0, le=0.0)
    custom_take_profit_pct: float | None = Field(default=None, gt=0.0, le=1000.0)


class TradeLotCreateRequest(BaseModel):
    side: Literal["buy", "sell"] = "buy"
    trade_date: str | None = Field(default=None, description="YYYY-MM-DD, defaults to today (Asia/Shanghai)")
    nav: float | None = Field(default=None, gt=0)
    amount: float | None = Field(default=None, gt=0)
    shares: float | None = Field(default=None, gt=0)
    sell_ratio: float | None = Field(default=None, gt=0.0, le=1.0)
    fee: float = Field(default=0.0, ge=0.0)
    notes: str = Field(default="", max_length=1000)


class TradeLotUpdateRequest(BaseModel):
    side: Literal["buy", "sell"] | None = None
    trade_date: str | None = None
    nav: float | None = Field(default=None, gt=0)
    amount: float | None = Field(default=None, gt=0)
    shares: float | None = Field(default=None, gt=0)
    sell_ratio: float | None = Field(default=None, gt=0.0, le=1.0)
    fee: float | None = Field(default=None, ge=0.0)
    notes: str | None = Field(default=None, max_length=1000)


class SyncNavRequest(BaseModel):
    otc_code: str | None = Field(default=None, min_length=6, max_length=6, pattern=r"^\d{6}$")

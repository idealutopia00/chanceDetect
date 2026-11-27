"""Core domain models for funds, OTC mappings, and sync telemetry."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

FundType = Literal["ETF", "LOF"]
ShareClass = Literal["A", "C", "E", "I", "OTHER"]
MappingType = Literal["FEEDER", "LOF_SELF", "LOF_SIBLING"]
PositionStatus = Literal["active", "closed"]
TradeSide = Literal["buy", "sell"]
NavStatus = Literal["pending", "confirmed", "manual"]


class ExchangeFund(BaseModel):
    """An on-exchange ETF or LOF that has at least one mapped OTC counterpart."""

    symbol: str  # e.g., sh510300, sz159915, sz161725
    code: str  # 6-digit fund code, e.g. 510300
    exchange: Literal["sh", "sz"]
    name: str
    fund_type: FundType
    index_code: str = ""
    index_name: str = ""
    company_id: str = ""
    company_name: str = ""
    establish_date: str = ""
    fund_scale: float | None = None  # 亿元
    latest_trade_date: str | None = None
    avg_amount_20d: float | None = None  # 万元
    is_active: bool = True
    updated_at: str | None = None


class OTCFundMapping(BaseModel):
    """An off-exchange fund mapped to a specific on-exchange ETF/LOF."""

    exchange_symbol: str
    otc_code: str
    otc_name: str
    share_class: ShareClass = "OTHER"
    mapping_type: MappingType
    purchase_status: str = ""
    fee_rate: str = ""
    updated_at: str | None = None


class SyncRunRecord(BaseModel):
    id: int | None = None
    job_type: str
    status: str = "running"
    started_at: str
    finished_at: str | None = None
    total_targets: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    primary_source_count: int = 0
    fallback_source_count: int = 0
    drift_refreshed_count: int = 0
    summary: dict[str, Any] = Field(default_factory=dict)


class SyncErrorRecord(BaseModel):
    id: int | None = None
    run_id: int
    symbol: str
    stage: str
    provider: str
    error_message: str
    created_at: str


class RawBar(BaseModel):
    symbol: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float = 0.0  # 万元
    turnover_pct: float = 0.0
    source: str = "tencent"


class StrategyPresetRecord(BaseModel):
    id: int | None = None
    name: str
    description: str = ""
    config: dict[str, Any]
    created_at: str | None = None
    updated_at: str | None = None


class ScanSnapshotRecord(BaseModel):
    id: int | None = None
    trade_date: str
    preset_id: int | None = None
    config: dict[str, Any]
    hit_fund_count: int
    hit_index_count: int
    elapsed_ms: float
    result: dict[str, Any]
    created_at: str | None = None


class TrackedPositionRecord(BaseModel):
    """A single holding lifecycle (Tracked Position) for an off-exchange mutual fund."""

    id: int
    otc_code: str
    otc_name: str = ""
    exchange_symbol: str | None = None
    status: PositionStatus = "active"
    opened_date: str
    closed_date: str | None = None
    entry_signal_snapshot: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    custom_stop_loss_pct: float | None = None
    custom_trailing_drawdown_pct: float | None = None
    custom_take_profit_pct: float | None = None
    created_at: str
    updated_at: str


class TradeLotRecord(BaseModel):
    """An individual buy or sell event (Trade Lot) within a Tracked Position."""

    id: int
    position_id: int
    side: TradeSide
    trade_date: str
    nav: float | None = None
    nav_status: NavStatus = "pending"
    amount: float | None = None
    shares: float | None = None
    sell_ratio: float | None = None
    fee: float = 0.0
    notes: str = ""
    created_at: str
    updated_at: str


class OTCNavBar(BaseModel):
    """Official daily net asset value bar for a tracked off-exchange mutual fund."""

    otc_code: str
    nav_date: str
    unit_nav: float
    accumulated_nav: float | None = None
    daily_return_pct: float | None = None
    dividend_note: str = ""
    updated_at: str | None = None


class OTCFundMetadata(BaseModel):
    """Lightweight cached metadata for arbitrary 6-digit off-exchange fund codes."""

    otc_code: str
    otc_name: str = ""
    last_nav_date: str | None = None
    last_synced_at: str | None = None
    updated_at: str | None = None


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")

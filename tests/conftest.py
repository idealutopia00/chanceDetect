"""Shared pytest fixtures for ChanceDetect tests."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from chancedetect.config import Settings
from chancedetect.data.models import ExchangeFund, OTCFundMapping
from chancedetect.storage.bar_store import BarStore
from chancedetect.storage.sqlite_store import SQLiteStore


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Settings:
    settings = Settings(
        data_dir=tmp_path / "data",
        frontend_dist_dir=tmp_path / "frontend_dist",
        enable_scheduler=False,
    )
    settings.ensure_dirs()
    return settings


@pytest.fixture
def sqlite_store(tmp_settings: Settings) -> SQLiteStore:
    return SQLiteStore(tmp_settings.sqlite_path)


@pytest.fixture
def bar_store(tmp_settings: Settings) -> BarStore:
    return BarStore(
        tmp_settings.daily_bars_path,
        tmp_settings.benchmarks_path,
        tmp_settings.features_path,
    )


def make_synthetic_bars(
    symbol: str,
    closes: list[float],
    *,
    start: date = date(2025, 1, 2),
    volumes: list[float] | None = None,
    amounts: list[float] | None = None,
) -> pl.DataFrame:
    """Create valid synthetic daily OHLCV bars on consecutive weekdays."""
    dates: list[date] = []
    cur = start
    while len(dates) < len(closes):
        if cur.weekday() < 5:
            dates.append(cur)
        cur += timedelta(days=1)

    vols = volumes if volumes is not None else [100_000.0] * len(closes)
    amts = amounts if amounts is not None else [5_000.0] * len(closes)

    opens = [closes[0]] + [closes[i - 1] for i in range(1, len(closes))]
    highs = [max(o, c) * 1.01 for o, c in zip(opens, closes, strict=True)]
    lows = [min(o, c) * 0.99 for o, c in zip(opens, closes, strict=True)]

    return pl.DataFrame(
        {
            "symbol": [symbol] * len(closes),
            "trade_date": dates,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": vols,
            "amount": amts,
            "turnover_pct": [1.5] * len(closes),
            "pct_chg": [0.0] * len(closes),
            "source": ["tencent"] * len(closes),
        }
    )


def sample_funds_and_mappings() -> tuple[list[ExchangeFund], list[OTCFundMapping]]:
    funds = [
        ExchangeFund(
            symbol="sh510300",
            code="510300",
            exchange="sh",
            name="沪深300ETF华泰柏瑞",
            fund_type="ETF",
            index_code="000300",
            index_name="沪深300指数",
            company_id="80000229",
            company_name="华泰柏瑞基金",
            establish_date="2012-05-04",
            fund_scale=3000.0,
        ),
        ExchangeFund(
            symbol="sh510330",
            code="510330",
            exchange="sh",
            name="沪深300ETF华夏",
            fund_type="ETF",
            index_code="000300",
            index_name="沪深300指数",
            company_id="80000222",
            company_name="华夏基金",
            establish_date="2012-12-25",
            fund_scale=1500.0,
        ),
        ExchangeFund(
            symbol="sz159915",
            code="159915",
            exchange="sz",
            name="创业板ETF易方达",
            fund_type="ETF",
            index_code="399006",
            index_name="创业板指",
            company_id="80000223",
            company_name="易方达基金",
            establish_date="2011-09-20",
            fund_scale=900.0,
        ),
        ExchangeFund(
            symbol="sz161725",
            code="161725",
            exchange="sz",
            name="招商中证白酒指数(LOF)A",
            fund_type="LOF",
            index_code="399997",
            index_name="中证白酒指数",
            company_id="80000226",
            company_name="招商基金",
            establish_date="2015-05-27",
            fund_scale=400.0,
        ),
    ]
    mappings = [
        OTCFundMapping(
            exchange_symbol="sh510300",
            otc_code="460300",
            otc_name="华泰柏瑞沪深300ETF联接A",
            share_class="A",
            mapping_type="FEEDER",
            purchase_status="开放申购",
            fee_rate="0.12%",
        ),
        OTCFundMapping(
            exchange_symbol="sh510300",
            otc_code="006131",
            otc_name="华泰柏瑞沪深300ETF联接C",
            share_class="C",
            mapping_type="FEEDER",
            purchase_status="开放申购",
            fee_rate="0.00%",
        ),
        OTCFundMapping(
            exchange_symbol="sh510330",
            otc_code="000051",
            otc_name="华夏沪深300ETF联接A",
            share_class="A",
            mapping_type="FEEDER",
            purchase_status="开放申购",
            fee_rate="0.12%",
        ),
        OTCFundMapping(
            exchange_symbol="sz159915",
            otc_code="110026",
            otc_name="易方达创业板ETF联接A",
            share_class="A",
            mapping_type="FEEDER",
            purchase_status="开放申购",
            fee_rate="0.12%",
        ),
        OTCFundMapping(
            exchange_symbol="sz159915",
            otc_code="004744",
            otc_name="易方达创业板ETF联接C",
            share_class="C",
            mapping_type="FEEDER",
            purchase_status="开放申购",
            fee_rate="0.00%",
        ),
        OTCFundMapping(
            exchange_symbol="sz161725",
            otc_code="161725",
            otc_name="招商中证白酒指数(LOF)A",
            share_class="A",
            mapping_type="LOF_SELF",
            purchase_status="限大额",
            fee_rate="0.10%",
        ),
        OTCFundMapping(
            exchange_symbol="sz161725",
            otc_code="012414",
            otc_name="招商中证白酒指数(LOF)C",
            share_class="C",
            mapping_type="LOF_SIBLING",
            purchase_status="限大额",
            fee_rate="0.00%",
        ),
    ]
    return funds, mappings

"""Tests for SQLiteStore and BarStore (Parquet + DuckDB)."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from tests.conftest import make_synthetic_bars, sample_funds_and_mappings

from chancedetect.data.models import ExchangeFund, OTCFundMapping
from chancedetect.storage.bar_store import BarStore
from chancedetect.storage.sqlite_store import SQLiteStore, UniverseDropProtectionError


def test_sqlite_universe_and_search(sqlite_store: SQLiteStore) -> None:
    funds, mappings = sample_funds_and_mappings()
    stats = sqlite_store.replace_universe(funds, mappings)
    assert stats["active_fund_count"] == 4
    assert stats["mapping_count"] == 7

    # Search by OTC code finds parent exchange fund
    items, total = sqlite_store.search_funds("012414")
    assert total == 1
    assert items[0]["symbol"] == "sz161725"
    assert len(items[0]["otc_mappings"]) == 2

    # Search by index name finds both 300 ETFs
    items_300, total_300 = sqlite_store.search_funds("沪深300")
    assert total_300 == 2
    assert {i["symbol"] for i in items_300} == {"sh510300", "sh510330"}

    # Stats check
    u_stats = sqlite_store.get_universe_stats()
    assert u_stats["total_exchange_funds"] == 4
    assert u_stats["etf_count"] == 3
    assert u_stats["lof_count"] == 1
    assert u_stats["unique_index_count"] == 3
    assert u_stats["unique_otc_funds"] == 7


def test_sqlite_drop_protection(sqlite_store: SQLiteStore) -> None:
    # Seed 25 funds
    funds = [
        ExchangeFund(
            symbol=f"sh510{i:03d}",
            code=f"510{i:03d}",
            exchange="sh",
            name=f"测试ETF{i}",
            fund_type="ETF",
            index_code=f"000{i:03d}",
            index_name=f"测试指数{i}",
        )
        for i in range(25)
    ]
    mappings = [
        OTCFundMapping(
            exchange_symbol=f.symbol,
            otc_code=f"00{i:04d}",
            otc_name=f"测试联接{i}C",
            share_class="C",
            mapping_type="FEEDER",
        )
        for i, f in enumerate(funds)
    ]
    sqlite_store.replace_universe(funds, mappings)

    # Shrinking to 5 funds without force must trigger drop protection
    with pytest.raises(UniverseDropProtectionError):
        sqlite_store.replace_universe(funds[:5], mappings[:5])

    # With force=True it succeeds
    res = sqlite_store.replace_universe(funds[:5], mappings[:5], force=True)
    assert res["active_fund_count"] == 5


def test_sqlite_presets_and_snapshots(sqlite_store: SQLiteStore) -> None:
    p = sqlite_store.save_preset("趋势共振", "突破+多头", {"combine_mode": "AND"})
    assert p.id is not None
    assert len(sqlite_store.list_presets()) == 1

    snap_id = sqlite_store.save_scan_snapshot(
        trade_date="2026-11-24",
        preset_id=p.id,
        config={"combine_mode": "AND"},
        hit_fund_count=3,
        hit_index_count=2,
        elapsed_ms=25.4,
        result={"groups": []},
    )
    snap = sqlite_store.get_scan_snapshot(snap_id)
    assert snap is not None
    assert snap.hit_index_count == 2

    assert sqlite_store.delete_preset(p.id) is True
    assert len(sqlite_store.list_presets()) == 0


def test_bar_store_upsert_replace_and_metrics(bar_store: BarStore) -> None:
    df1 = make_synthetic_bars("sh510300", [4.0, 4.1, 4.2, 4.3, 4.4], amounts=[1000.0] * 5)
    rows = bar_store.upsert_daily_bars(df1)
    assert rows == 5

    # Tail closes should return last 3 dates
    tails = bar_store.get_tail_closes_by_symbol(n=3)
    assert len(tails["sh510300"]) == 3

    # Upsert incremental update with overlap + 1 new day
    df2 = make_synthetic_bars(
        "sh510300",
        [4.0, 4.1, 4.2, 4.3, 4.4, 4.5],
        amounts=[2000.0] * 6,
    )
    rows2 = bar_store.upsert_daily_bars(df2.tail(2))
    assert rows2 == 6

    loaded = bar_store.load_daily_bars(symbols=["sh510300"])
    assert loaded.height == 6
    assert abs(loaded["close"][-1] - 4.5) < 1e-6
    assert loaded["pct_chg"][1] == pytest.approx(2.5, abs=0.01)

    # Full symbol replacement (e.g. after corporate action drift)
    df_adj = make_synthetic_bars("sh510300", [2.0, 2.05, 2.1, 2.15, 2.2, 2.25])
    bar_store.upsert_daily_bars(df_adj, replace_symbols={"sh510300"})
    reloaded = bar_store.load_daily_bars(symbols=["sh510300"])
    assert reloaded.height == 6
    assert abs(reloaded["close"][0] - 2.0) < 1e-6

    metrics = bar_store.compute_symbol_metrics()
    assert "sh510300" in metrics
    assert metrics["sh510300"][1] == pytest.approx(5000.0, abs=1.0)


def test_bar_store_benchmarks(bar_store: BarStore) -> None:
    bdf = pl.DataFrame(
        {
            "benchmark_code": ["sh000300", "sh000300"],
            "trade_date": [date(2025, 1, 2), date(2025, 1, 3)],
            "open": [3900.0, 3920.0],
            "high": [3950.0, 3980.0],
            "low": [3890.0, 3910.0],
            "close": [3920.0, 3960.0],
            "volume": [1e7, 1.2e7],
            "amount": [2e7, 2.5e7],
            "pct_chg": [0.0, 0.0],
        }
    )
    assert bar_store.upsert_benchmarks(bdf) == 2
    loaded = bar_store.load_benchmarks(["sh000300"])
    assert loaded.height == 2
    assert loaded["pct_chg"][1] == pytest.approx((3960 / 3920 - 1) * 100, abs=0.01)

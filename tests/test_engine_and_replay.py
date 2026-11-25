"""Tests for StrategyEngine (composition, index grouping, speed) and signal replay."""

from __future__ import annotations

import math

import polars as pl
from tests.conftest import make_synthetic_bars, sample_funds_and_mappings

from chancedetect.data.models import ExchangeFund, OTCFundMapping
from chancedetect.storage.bar_store import BarStore
from chancedetect.storage.sqlite_store import SQLiteStore
from chancedetect.strategies.engine import (
    BaseUniverseFilter,
    ScanRequestConfig,
    StrategyEngine,
    StrategySelection,
)
from chancedetect.strategies.indicators import compute_base_features
from chancedetect.strategies.replay import build_fund_replay


def _seed_market(sqlite_store: SQLiteStore, bar_store: BarStore) -> None:
    funds, mappings = sample_funds_and_mappings()
    sqlite_store.replace_universe(funds, mappings)

    n = 90
    # Both 300 ETFs rally strongly (sh510300 has 5x liquidity of sh510330!)
    up_closes = [3.0 + 0.002 * math.sin(i) for i in range(60)] + [3.0 + 0.04 * i for i in range(1, 31)]
    vols_high = [200_000.0] * 89 + [500_000.0]
    df_300a = make_synthetic_bars("sh510300", up_closes, volumes=vols_high, amounts=[50_000.0] * n)
    df_300b = make_synthetic_bars("sh510330", up_closes, volumes=vols_high, amounts=[10_000.0] * n)

    # 159915 steady flat
    df_cyb = make_synthetic_bars("sz159915", [2.0] * n, amounts=[20_000.0] * n)
    # 161725 illiquid (< 1000 万)
    df_baijiu = make_synthetic_bars("sz161725", up_closes, volumes=vols_high, amounts=[200.0] * n)

    all_bars = pl.concat([df_300a, df_300b, df_cyb, df_baijiu])
    bar_store.upsert_daily_bars(all_bars)
    daily = bar_store.load_daily_bars()

    dates = df_300a["trade_date"].to_list()
    bench = pl.DataFrame(
        {
            "benchmark_code": ["sh000300"] * n,
            "trade_date": dates,
            "open": [3900.0] * n,
            "high": [3910.0] * n,
            "low": [3890.0] * n,
            "close": [3900.0] * n,
            "volume": [1e7] * n,
            "amount": [1e7] * n,
            "pct_chg": [0.0] * n,
        }
    )
    bar_store.upsert_benchmarks(bench)
    feat = compute_base_features(daily, bar_store.load_benchmarks())
    bar_store.save_features(feat)
    sqlite_store.update_fund_bar_metrics(bar_store.compute_symbol_metrics())


def test_engine_index_grouping_and_otc_preference(
    sqlite_store: SQLiteStore, bar_store: BarStore
) -> None:
    _seed_market(sqlite_store, bar_store)
    engine = StrategyEngine(sqlite_store, bar_store)

    cfg = ScanRequestConfig(
        strategies=[
            StrategySelection(strategy_id="ma_bullish_alignment", enabled=True),
            StrategySelection(strategy_id="volume_high_breakout", enabled=True),
        ],
        combine_mode="AND",
        base_filter=BaseUniverseFilter(min_avg_amount_20d=1000.0, min_listing_days=60),
        share_class_preference="C",
        group_by_index=True,
        save_snapshot=True,
    )
    res = engine.scan(cfg)

    # sz161725 filtered out by min_avg_amount_20d=1000; sz159915 flat -> only 000300 index group hits!
    assert res["hit_fund_count"] == 2
    assert res["hit_index_count"] == 1
    grp = res["groups"][0]
    assert grp["index_code"] == "000300"
    # Representative must be sh510300 (50,000 万 > 10,000 万)
    assert grp["representative"]["symbol"] == "sh510300"
    assert grp["member_count"] == 2
    # Aggregated OTC funds across both 300 ETFs = 3 unique OTC codes, C-share first!
    assert grp["otc_count"] == 3
    assert grp["otc_funds"][0]["share_class"] == "C"
    assert grp["otc_funds"][0]["otc_code"] == "006131"

    # Switching preference to A puts A shares first
    cfg_a = cfg.model_copy(update={"share_class_preference": "A", "save_snapshot": False})
    res_a = engine.scan(cfg_a)
    assert res_a["groups"][0]["otc_funds"][0]["share_class"] == "A"

    # Snapshot was persisted
    snaps = sqlite_store.list_scan_snapshots()
    assert len(snaps) == 1


def test_fund_replay_and_forward_stats(sqlite_store: SQLiteStore, bar_store: BarStore) -> None:
    _seed_market(sqlite_store, bar_store)
    engine = StrategyEngine(sqlite_store, bar_store)

    cfg = ScanRequestConfig(
        strategies=[StrategySelection(strategy_id="ma_bullish_alignment", enabled=True)],
        combine_mode="OR",
        share_class_preference="C",
    )
    replay = build_fund_replay(engine, "sh510300", cfg, lookback_days=200)
    assert replay["fund"]["symbol"] == "sh510300"
    assert len(replay["bars"]) == 90
    assert replay["trigger_count"] > 0
    # Forward stats covers 1, 5, 10, 20 days and shows positive win rate on uptrend
    stat_5d = next(s for s in replay["forward_stats"] if s["horizon_days"] == 5)
    assert stat_5d["sample_count"] > 0
    assert stat_5d["win_rate_pct"] == 100.0
    assert stat_5d["avg_return_pct"] > 0
    # Same-index OTC mappings include both 510300's and 510330's feeders
    assert len(replay["direct_otc_mappings"]) == 2
    assert len(replay["index_otc_mappings"]) == 3


def test_engine_speed_benchmark_large_panel(
    sqlite_store: SQLiteStore, bar_store: BarStore
) -> None:
    """Verify full-panel scan across 200 funds x 300 days (60k rows) x 8 strategies is fast (< 500ms)."""
    num_funds = 200
    days = 300
    funds = []
    mappings = []
    frames = []
    base_closes = [2.0 + 0.002 * i + 0.03 * math.sin(i / 5.0) for i in range(days)]

    for i in range(num_funds):
        sym = f"sh51{i:04d}"
        funds.append(
            ExchangeFund(
                symbol=sym,
                code=f"51{i:04d}",
                exchange="sh",
                name=f"性能测试ETF{i}",
                fund_type="ETF",
                index_code=f"IDX{i // 2:03d}",
                index_name=f"测试指数{i // 2}",
            )
        )
        mappings.append(
            OTCFundMapping(
                exchange_symbol=sym,
                otc_code=f"00{i:04d}",
                otc_name=f"性能测试联接{i}C",
                share_class="C",
                mapping_type="FEEDER",
                purchase_status="开放申购",
            )
        )
        closes = [c * (1.0 + (i % 5) * 0.01) for c in base_closes]
        frames.append(make_synthetic_bars(sym, closes, amounts=[3000.0] * days))

    sqlite_store.replace_universe(funds, mappings, force=True)
    bar_store.upsert_daily_bars(pl.concat(frames))
    feat = compute_base_features(bar_store.load_daily_bars())
    bar_store.save_features(feat)

    engine = StrategyEngine(sqlite_store, bar_store)
    # Warm cache once
    engine.load_panel()

    res = engine.scan(ScanRequestConfig(combine_mode="OR"))
    assert res["scanned_fund_count"] == num_funds
    # Must be well under 500ms on cached Polars panel
    assert res["elapsed_ms"] < 500.0

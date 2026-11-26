"""Integration tests for FastAPI routes and scheduler helpers."""

from __future__ import annotations

import math
from datetime import date, datetime

import polars as pl
from fastapi.testclient import TestClient
from tests.conftest import make_synthetic_bars
from tests.test_engine_and_replay import _seed_market

from chancedetect.api.app import create_app
from chancedetect.config import Settings
from chancedetect.data.models import ExchangeFund, OTCFundMapping
from chancedetect.scheduler.jobs import SH_TZ, expected_latest_weekday
from chancedetect.storage.bar_store import BarStore
from chancedetect.storage.sqlite_store import SQLiteStore
from chancedetect.strategies.indicators import compute_base_features


def test_expected_latest_weekday() -> None:
    # Wednesday 16:30 -> Wednesday itself
    wed_after = datetime(2026, 11, 25, 16, 30, tzinfo=SH_TZ)
    assert expected_latest_weekday(wed_after, 16, 15) == date(2026, 11, 25)
    # Wednesday 14:00 -> Tuesday
    wed_before = datetime(2026, 11, 25, 14, 0, tzinfo=SH_TZ)
    assert expected_latest_weekday(wed_before, 16, 15) == date(2026, 11, 24)
    # Sunday 11:00 -> Friday
    sun = datetime(2026, 11, 29, 11, 0, tzinfo=SH_TZ)
    assert expected_latest_weekday(sun, 16, 15) == date(2026, 11, 27)


def test_api_end_to_end(tmp_settings: Settings) -> None:
    app = create_app(tmp_settings)
    _seed_market(app.state.sqlite_store, app.state.bar_store)

    with TestClient(app) as client:
        # 1. Health
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        assert r.json()["universe"]["total_exchange_funds"] == 4

        # 2. Strategies metadata
        r = client.get("/api/v1/strategies")
        assert r.status_code == 200
        strategies_meta = r.json()["strategies"]
        assert len(strategies_meta) == 10
        roles_by_id = {m["id"]: m["role"] for m in strategies_meta}
        assert roles_by_id == {
            "mid_term_uptrend": "regime",
            "momentum_top_rank": "regime",
            "relative_strength_rs": "regime",
            "trend_pullback_entry": "entry",
            "ma_bullish_alignment": "entry",
            "volume_high_breakout": "entry",
            "macd_golden_cross": "entry",
            "rsi_oversold_rebound": "entry",
            "boll_lower_reversal": "entry",
            "ma_negative_bias": "entry",
        }
        for m in strategies_meta:
            assert "properties" in m["params_schema"]
            assert isinstance(m["default_params"], dict)

        # 3. Scan
        r = client.post(
            "/api/v1/scan",
            json={
                "strategies": [{"strategy_id": "ma_bullish_alignment", "enabled": True}],
                "combine_mode": "OR",
                "share_class_preference": "C",
                "save_snapshot": True,
            },
        )
        assert r.status_code == 200
        scan_data = r.json()
        assert scan_data["hit_index_count"] == 1
        assert scan_data["groups"][0]["representative"]["symbol"] == "sh510300"

        # 4. Snapshots
        r = client.get("/api/v1/scan/snapshots")
        assert r.status_code == 200
        assert len(r.json()["snapshots"]) == 1

        # 5. Presets CRUD
        r = client.post(
            "/api/v1/presets",
            json={"name": "我的突破组合", "description": "均线+放量", "config": {"combine_mode": "AND"}},
        )
        assert r.status_code == 200
        pid = r.json()["preset"]["id"]
        assert len(client.get("/api/v1/presets").json()["presets"]) == 1
        assert client.delete(f"/api/v1/presets/{pid}").status_code == 200

        # 6. Fund replay
        r = client.post(
            "/api/v1/funds/sh510300/replay",
            json={
                "config": {
                    "strategies": [{"strategy_id": "ma_bullish_alignment", "enabled": True}],
                    "combine_mode": "OR",
                },
                "lookback_days": 120,
            },
        )
        assert r.status_code == 200
        rep = r.json()
        assert rep["fund"]["code"] == "510300"
        assert len(rep["bars"]) == 90
        assert len(rep["forward_stats"]) == 4

        # 7. Fund search (by OTC code)
        r = client.get("/api/v1/funds", params={"q": "000051"})
        assert r.status_code == 200
        assert r.json()["total"] == 1
        assert r.json()["items"][0]["symbol"] == "sh510330"

        # 8. Sync status
        r = client.get("/api/v1/sync/status")
        assert r.status_code == 200
        assert r.json()["progress"]["running"] is False


def _seed_two_stage_market(sqlite_store: SQLiteStore, bar_store: BarStore) -> None:
    """Seed four liquid funds representing distinct two-stage funnel roles:
    - sh510300: strong trend (both regimes) + 2 active right-side entries (MA alignment + vol breakout)
    - sh512480: strong trend (both regimes) + 1 active entry only (MA alignment, normal volume)
    - sh512800: strong trend (momentum_top_rank only, slight pause) + 0 active entries -> watchlist only
    - sz159915: weak trend (0 regimes) + oversold rebound entry -> left-side scan only
    """
    funds = [
        ExchangeFund(
            symbol="sh510300",
            code="510300",
            exchange="sh",
            name="沪深300ETF华泰柏瑞",
            fund_type="ETF",
            index_code="000300",
            index_name="沪深300指数",
            company_name="华泰柏瑞基金",
        ),
        ExchangeFund(
            symbol="sh512480",
            code="512480",
            exchange="sh",
            name="半导体ETF国联安",
            fund_type="ETF",
            index_code="H30184",
            index_name="中证全指半导体",
            company_name="国联安基金",
        ),
        ExchangeFund(
            symbol="sh512800",
            code="512800",
            exchange="sh",
            name="银行ETF华宝",
            fund_type="ETF",
            index_code="399986",
            index_name="中证银行指数",
            company_name="华宝基金",
        ),
        ExchangeFund(
            symbol="sz159915",
            code="159915",
            exchange="sz",
            name="创业板ETF易方达",
            fund_type="ETF",
            index_code="399006",
            index_name="创业板指",
            company_name="易方达基金",
        ),
    ]
    mappings = [
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
            exchange_symbol="sh512480",
            otc_code="007301",
            otc_name="国联安中证全指半导体ETF联接C",
            share_class="C",
            mapping_type="FEEDER",
            purchase_status="开放申购",
            fee_rate="0.00%",
        ),
        OTCFundMapping(
            exchange_symbol="sh512800",
            otc_code="006697",
            otc_name="华宝中证银行ETF联接C",
            share_class="C",
            mapping_type="FEEDER",
            purchase_status="开放申购",
            fee_rate="0.00%",
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
    ]
    sqlite_store.replace_universe(funds, mappings)

    n = 90
    # 1. sh510300: strong uptrend + brief 3-bar shakeout + fresh first-day MA alignment & 2.5x volume breakout (within bias20 cap, no MACD cross)
    c_300 = (
        [3.0 + 0.002 * math.sin(i) for i in range(60)]
        + [3.0 + 0.02 * i for i in range(1, 27)]
        + [3.40, 3.38, 3.41, 3.62]
    )
    v_300 = [200_000.0] * 89 + [500_000.0]
    df_300 = make_synthetic_bars("sh510300", c_300, volumes=v_300, amounts=[50_000.0] * n)

    # 2. sh512480: strong uptrend + brief 3-bar shakeout + fresh first-day MA alignment on steady 1.05x volume
    #    (DIF stays above DEA so macd_golden_cross does not fire; normal volume so vol breakout misses)
    c_semi = (
        [2.0 + 0.002 * math.sin(i) for i in range(60)]
        + [2.0 + 0.015 * i for i in range(1, 27)]
        + [2.30, 2.28, 2.31, 2.46]
    )
    v_semi = [150_000.0] * 89 + [160_000.0]
    df_semi = make_synthetic_bars("sh512480", c_semi, volumes=v_semi, amounts=[30_000.0] * n)

    # 3. sh512800: persistent rally then mild 1-day pause on shrinking volume (regime hits, 0 entries)
    c_bank = [1.0 + 0.001 * math.sin(i) for i in range(60)] + [1.0 + 0.012 * i for i in range(1, 30)] + [1.345]
    v_bank = [180_000.0] * 89 + [90_000.0]
    df_bank = make_synthetic_bars("sh512800", c_bank, volumes=v_bank, amounts=[40_000.0] * n)

    # 4. sz159915: sharp selloff then green bounce candle (oversold rebound entry, fails regimes)
    c_cyb = [4.0] * 77 + [4.0 - 0.10 * i for i in range(1, 13)] + [2.88]
    df_cyb = make_synthetic_bars("sz159915", c_cyb, amounts=[25_000.0] * n)
    cyb_opens = df_cyb["open"].to_list()
    cyb_opens[-1] = 2.80
    df_cyb = df_cyb.with_columns(pl.Series("open", cyb_opens))

    all_bars = pl.concat([df_300, df_semi, df_bank, df_cyb]).with_columns(
        pl.col("close").pct_change().over("symbol").mul(100.0).fill_null(0.0).alias("pct_chg")
    )
    bar_store.upsert_daily_bars(all_bars)
    daily = bar_store.load_daily_bars()

    dates = df_300["trade_date"].to_list()
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


def test_two_stage_funnel_routes_actionable_and_watchlist(tmp_settings: Settings) -> None:
    app = create_app(tmp_settings)
    _seed_two_stage_market(app.state.sqlite_store, app.state.bar_store)

    with TestClient(app) as client:
        r = client.post(
            "/api/v1/scan",
            json={
                "strategies": [
                    {"strategy_id": "momentum_top_rank", "enabled": True, "params": {"top_k": 3}},
                    {"strategy_id": "volume_high_breakout", "enabled": True},
                ],
                "regime_combine_mode": "OR",
                "combine_mode": "OR",
            },
        )
        assert r.status_code == 200
        data = r.json()

        actionable_syms = [g["representative"]["symbol"] for g in data["actionable_groups"]]
        watchlist_syms = [g["representative"]["symbol"] for g in data["watchlist_groups"]]

        assert actionable_syms == ["sh510300"]
        assert data["groups"] == data["actionable_groups"]
        assert set(watchlist_syms) == {"sh512480", "sh512800"}
        assert "sz159915" not in actionable_syms
        assert "sz159915" not in watchlist_syms

        assert data["actionable_index_count"] == 1
        assert data["actionable_fund_count"] == 1
        assert data["hit_index_count"] == 1
        assert data["hit_fund_count"] == 1
        assert data["watchlist_index_count"] == 2
        assert data["watchlist_fund_count"] == 2
        assert data["regime_combine_mode"] == "OR"

        # Actionable fund exposes matched_regimes and hit_strategies; watchlist exposes matched_regimes
        act_rep = data["actionable_groups"][0]["representative"]
        assert [s["strategy_id"] for s in act_rep["matched_regimes"]] == ["momentum_top_rank"]
        assert [s["strategy_id"] for s in act_rep["hit_strategies"]] == ["volume_high_breakout"]

        watch_rep = next(
            g["representative"]
            for g in data["watchlist_groups"]
            if g["representative"]["symbol"] == "sh512800"
        )
        assert [s["strategy_id"] for s in watch_rep["matched_regimes"]] == ["momentum_top_rank"]
        assert watch_rep["hit_strategies"] == []


def test_entry_only_and_regime_only_scans(tmp_settings: Settings) -> None:
    app = create_app(tmp_settings)
    _seed_two_stage_market(app.state.sqlite_store, app.state.bar_store)

    with TestClient(app) as client:
        # 1. Pure left-side oversold scan (all regimes disabled): oversold entry populates actionable directly
        r_left = client.post(
            "/api/v1/scan",
            json={
                "strategies": [
                    {"strategy_id": "momentum_top_rank", "enabled": False},
                    {"strategy_id": "relative_strength_rs", "enabled": False},
                    {
                        "strategy_id": "rsi_oversold_rebound",
                        "enabled": True,
                        "params": {"oversold_threshold": 35.0},
                    },
                ],
                "combine_mode": "OR",
            },
        )
        assert r_left.status_code == 200
        left_data = r_left.json()
        assert [g["representative"]["symbol"] for g in left_data["actionable_groups"]] == ["sz159915"]
        assert left_data["groups"] == left_data["actionable_groups"]
        assert left_data["watchlist_groups"] == []
        assert left_data["watchlist_index_count"] == 0
        assert left_data["watchlist_fund_count"] == 0

        # 2. Regime-only scan (no entry triggers enabled): all regime hits go to watchlist_groups
        r_regime = client.post(
            "/api/v1/scan",
            json={
                "strategies": [
                    {"strategy_id": "momentum_top_rank", "enabled": True, "params": {"top_k": 3}},
                ],
                "regime_combine_mode": "OR",
            },
        )
        assert r_regime.status_code == 200
        reg_data = r_regime.json()
        assert reg_data["actionable_groups"] == []
        assert reg_data["actionable_index_count"] == 0
        assert reg_data["hit_index_count"] == 0
        assert {g["representative"]["symbol"] for g in reg_data["watchlist_groups"]} == {
            "sh510300",
            "sh512480",
            "sh512800",
        }
        assert reg_data["watchlist_index_count"] == 3
        assert reg_data["watchlist_fund_count"] == 3


def test_regime_and_entry_combine_modes(tmp_settings: Settings) -> None:
    app = create_app(tmp_settings)
    _seed_two_stage_market(app.state.sqlite_store, app.state.bar_store)

    with TestClient(app) as client:
        # 1. Regime OR vs AND (sh512800 passes momentum_top_rank only; sh510300 & sh512480 pass both)
        regime_strats = [
            {"strategy_id": "momentum_top_rank", "enabled": True, "params": {"top_k": 3}},
            {
                "strategy_id": "relative_strength_rs",
                "enabled": True,
                "params": {"min_excess_return_pct": 2.0},
            },
        ]
        r_reg_or = client.post(
            "/api/v1/scan",
            json={"strategies": regime_strats, "regime_combine_mode": "OR"},
        )
        assert r_reg_or.status_code == 200
        assert {g["representative"]["symbol"] for g in r_reg_or.json()["watchlist_groups"]} == {
            "sh510300",
            "sh512480",
            "sh512800",
        }

        r_reg_and = client.post(
            "/api/v1/scan",
            json={"strategies": regime_strats, "regime_combine_mode": "AND"},
        )
        assert r_reg_and.status_code == 200
        assert {g["representative"]["symbol"] for g in r_reg_and.json()["watchlist_groups"]} == {
            "sh510300",
            "sh512480",
        }

        # 2. Entry OR vs RESONANCE (min_hits=2) vs AND under active regime
        # sh510300 hits 2 entries (ma_bullish_alignment + volume_high_breakout)
        # sh512480 hits 1 entry  (ma_bullish_alignment)
        # sh512800 hits 0 entries
        combo_strats = [
            {"strategy_id": "momentum_top_rank", "enabled": True, "params": {"top_k": 3}},
            {"strategy_id": "ma_bullish_alignment", "enabled": True},
            {"strategy_id": "volume_high_breakout", "enabled": True},
            {"strategy_id": "macd_golden_cross", "enabled": True},
        ]

        r_or = client.post(
            "/api/v1/scan",
            json={"strategies": combo_strats, "regime_combine_mode": "OR", "combine_mode": "OR"},
        )
        assert r_or.status_code == 200
        or_data = r_or.json()
        assert {g["representative"]["symbol"] for g in or_data["actionable_groups"]} == {
            "sh510300",
            "sh512480",
        }
        assert [g["representative"]["symbol"] for g in or_data["watchlist_groups"]] == ["sh512800"]

        r_res = client.post(
            "/api/v1/scan",
            json={
                "strategies": combo_strats,
                "regime_combine_mode": "OR",
                "combine_mode": "RESONANCE",
                "min_hits": 2,
            },
        )
        assert r_res.status_code == 200
        res_data = r_res.json()
        assert [g["representative"]["symbol"] for g in res_data["actionable_groups"]] == ["sh510300"]
        assert {g["representative"]["symbol"] for g in res_data["watchlist_groups"]} == {
            "sh512480",
            "sh512800",
        }

        r_and = client.post(
            "/api/v1/scan",
            json={"strategies": combo_strats, "regime_combine_mode": "OR", "combine_mode": "AND"},
        )
        assert r_and.status_code == 200
        and_data = r_and.json()
        assert and_data["actionable_groups"] == []
        assert {g["representative"]["symbol"] for g in and_data["watchlist_groups"]} == {
            "sh510300",
            "sh512480",
            "sh512800",
        }


def test_legacy_v1_preset_loads_and_scans_without_regime_combine_mode(
    tmp_settings: Settings,
) -> None:
    app = create_app(tmp_settings)
    _seed_two_stage_market(app.state.sqlite_store, app.state.bar_store)

    v1_preset_config = {
        "strategies": [
            {"strategy_id": "momentum_top_rank", "enabled": True, "params": {"top_k": 3}},
            {"strategy_id": "volume_high_breakout", "enabled": True, "params": {}},
        ],
        "combine_mode": "OR",
        "min_hits": 2,
        "base_filter": {
            "min_avg_amount_20d": 1000.0,
            "min_listing_days": 60,
            "fund_types": ["ETF", "LOF"],
        },
        "share_class_preference": "C",
        "group_by_index": True,
    }

    with TestClient(app) as client:
        r_save = client.post(
            "/api/v1/presets",
            json={
                "name": "v1 历史预设",
                "description": "不含 regime_combine_mode 的旧预设",
                "config": v1_preset_config,
            },
        )
        assert r_save.status_code == 200

        presets = client.get("/api/v1/presets").json()["presets"]
        assert len(presets) == 1
        loaded_cfg = presets[0]["config"]
        assert "regime_combine_mode" not in loaded_cfg

        r_scan = client.post("/api/v1/scan", json=loaded_cfg)
        assert r_scan.status_code == 200
        scan_data = r_scan.json()
        assert scan_data["regime_combine_mode"] == "OR"
        assert [g["representative"]["symbol"] for g in scan_data["actionable_groups"]] == ["sh510300"]
        assert {g["representative"]["symbol"] for g in scan_data["watchlist_groups"]} == {
            "sh512480",
            "sh512800",
        }


def test_v11_strategy_suite_and_scenario_templates_end_to_end(tmp_settings: Settings) -> None:
    """End-to-end HTTP test verifying mid_term_uptrend, trend_pullback_entry,
    first-day ma_bullish_alignment, anti-chase volume_high_breakout, and all 3 scenario templates.
    """
    app = create_app(tmp_settings)
    sqlite_store: SQLiteStore = app.state.sqlite_store
    bar_store: BarStore = app.state.bar_store

    funds = [
        ExchangeFund(
            symbol="sh510300",
            code="510300",
            exchange="sh",
            name="沪深300ETF",
            fund_type="ETF",
            index_code="000300",
            index_name="沪深300指数",
            company_name="华泰柏瑞基金",
        ),
        ExchangeFund(
            symbol="sh512480",
            code="512480",
            exchange="sh",
            name="半导体ETF",
            fund_type="ETF",
            index_code="H30184",
            index_name="中证全指半导体",
            company_name="国联安基金",
        ),
        ExchangeFund(
            symbol="sz159915",
            code="159915",
            exchange="sz",
            name="创业板ETF",
            fund_type="ETF",
            index_code="399006",
            index_name="创业板指",
            company_name="易方达基金",
        ),
    ]
    mappings = [
        OTCFundMapping(
            exchange_symbol="sh510300",
            otc_code="006131",
            otc_name="沪深300联接C",
            share_class="C",
            mapping_type="FEEDER",
            purchase_status="开放申购",
        ),
        OTCFundMapping(
            exchange_symbol="sh512480",
            otc_code="007301",
            otc_name="半导体联接C",
            share_class="C",
            mapping_type="FEEDER",
            purchase_status="开放申购",
        ),
        OTCFundMapping(
            exchange_symbol="sz159915",
            otc_code="004744",
            otc_name="创业板联接C",
            share_class="C",
            mapping_type="FEEDER",
            purchase_status="开放申购",
        ),
    ]
    sqlite_store.replace_universe(funds, mappings)

    n = 90
    # 1. sh510300: established mid-term uptrend that pulls back to MA10/MA20 and stabilizes on final bar
    c_pullback = [1.0 + 0.01 * i for i in range(87)] + [1.84, 1.81, 1.835]
    df_pullback = make_synthetic_bars(
        "sh510300", c_pullback, volumes=[150_000.0] * 89 + [135_000.0], amounts=[50_000.0] * n
    )
    pb_opens = df_pullback["open"].to_list()
    pb_opens[-1] = 1.815
    df_pullback = df_pullback.with_columns(pl.Series("open", pb_opens))

    # 2. sh512480: extended vertical rally (passes mid_term_uptrend, bias20 > 10% suppresses breakout,
    #    MA alignment formed weeks ago so first-day alignment is silent, stretched so no pullback) -> watchlist only
    c_extended = [1.0 + 0.005 * i for i in range(70)] + [1.35 + 0.045 * i for i in range(1, 21)]
    df_extended = make_synthetic_bars(
        "sh512480", c_extended, volumes=[150_000.0] * 89 + [400_000.0], amounts=[40_000.0] * n
    )

    # 3. sz159915: downtrending oversold rebound (fails mid_term_uptrend, hits rsi_oversold_rebound)
    c_oversold = [4.0] * 77 + [4.0 - 0.10 * i for i in range(1, 13)] + [2.88]
    df_oversold = make_synthetic_bars("sz159915", c_oversold, amounts=[30_000.0] * n)
    os_opens = df_oversold["open"].to_list()
    os_opens[-1] = 2.80
    df_oversold = df_oversold.with_columns(pl.Series("open", os_opens))

    all_bars = pl.concat([df_pullback, df_extended, df_oversold]).with_columns(
        pl.col("close").pct_change().over("symbol").mul(100.0).fill_null(0.0).alias("pct_chg")
    )
    bar_store.upsert_daily_bars(all_bars)
    daily = bar_store.load_daily_bars()

    dates = df_pullback["trade_date"].to_list()
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

    with TestClient(app) as client:
        # Template 1: 右侧主线买点 (default)
        r1 = client.post(
            "/api/v1/scan",
            json={
                "strategies": [
                    {"strategy_id": "mid_term_uptrend", "enabled": True},
                    {"strategy_id": "relative_strength_rs", "enabled": True},
                    {"strategy_id": "trend_pullback_entry", "enabled": True},
                    {"strategy_id": "ma_bullish_alignment", "enabled": True},
                    {"strategy_id": "volume_high_breakout", "enabled": True},
                    {"strategy_id": "macd_golden_cross", "enabled": True},
                ],
                "regime_combine_mode": "OR",
                "combine_mode": "OR",
            },
        )
        assert r1.status_code == 200
        d1 = r1.json()
        assert [g["representative"]["symbol"] for g in d1["actionable_groups"]] == ["sh510300"]
        assert [g["representative"]["symbol"] for g in d1["watchlist_groups"]] == ["sh512480"]
        act_rep = d1["actionable_groups"][0]["representative"]
        assert "mid_term_uptrend" in [s["strategy_id"] for s in act_rep["matched_regimes"]]
        assert "trend_pullback_entry" in [s["strategy_id"] for s in act_rep["hit_strategies"]]

        # Template 2: 左侧超跌抄底 (no regime filter)
        r2 = client.post(
            "/api/v1/scan",
            json={
                "strategies": [
                    {"strategy_id": "rsi_oversold_rebound", "enabled": True},
                    {"strategy_id": "boll_lower_reversal", "enabled": True},
                    {"strategy_id": "ma_negative_bias", "enabled": True},
                ],
                "combine_mode": "OR",
            },
        )
        assert r2.status_code == 200
        d2 = r2.json()
        assert [g["representative"]["symbol"] for g in d2["actionable_groups"]] == ["sz159915"]
        assert d2["watchlist_groups"] == []

        # Template 3: 强势动量聚焦 (momentum_top_rank AND relative_strength_rs + pullback OR breakout)
        r3 = client.post(
            "/api/v1/scan",
            json={
                "strategies": [
                    {"strategy_id": "momentum_top_rank", "enabled": True, "params": {"top_k": 2}},
                    {"strategy_id": "relative_strength_rs", "enabled": True},
                    {"strategy_id": "trend_pullback_entry", "enabled": True},
                    {
                        "strategy_id": "volume_high_breakout",
                        "enabled": True,
                        "params": {"max_bias20_pct": 0.0},
                    },
                ],
                "regime_combine_mode": "AND",
                "combine_mode": "OR",
            },
        )
        assert r3.status_code == 200
        d3 = r3.json()
        # With max_bias20_pct disabled, sh512480's high-momentum + RS + volume breakout surfaces!
        assert "sh512480" in [g["representative"]["symbol"] for g in d3["actionable_groups"]]

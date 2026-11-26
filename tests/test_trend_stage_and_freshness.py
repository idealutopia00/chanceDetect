"""End-to-end tests for Volatility-Normalized Trend Stage, Fresh Signal, anti-chase controls & CLI."""

from __future__ import annotations

import math

import polars as pl
import pytest
from fastapi.testclient import TestClient
from tests.conftest import make_synthetic_bars
from typer.testing import CliRunner

from chancedetect import cli as cli_module
from chancedetect.api.app import create_app
from chancedetect.config import Settings
from chancedetect.data.models import ExchangeFund, OTCFundMapping
from chancedetect.storage.bar_store import BarStore
from chancedetect.storage.sqlite_store import SQLiteStore
from chancedetect.strategies.indicators import compute_base_features

EXTENSION_FEATURE_COLS = ("bias60", "boll_pct_b", "bias20_z", "drawup_60d_low", "pos_60d")


def _seed_single_uptrend_fund(sqlite_store: SQLiteStore, bar_store: BarStore) -> None:
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
        )
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
        )
    ]
    sqlite_store.replace_universe(funds, mappings)

    n = 90
    closes = [3.0 + 0.002 * math.sin(i) for i in range(60)] + [3.0 + 0.03 * i for i in range(1, 31)]
    vols = [200_000.0] * 89 + [500_000.0]
    df = make_synthetic_bars("sh510300", closes, volumes=vols, amounts=[50_000.0] * n)
    bar_store.upsert_daily_bars(df)
    daily = bar_store.load_daily_bars()
    feat = compute_base_features(daily)
    bar_store.save_features(feat)
    sqlite_store.update_fund_bar_metrics(bar_store.compute_symbol_metrics())


def test_extension_features_and_legacy_cache_fallback(tmp_settings: Settings) -> None:
    app = create_app(tmp_settings)
    _seed_single_uptrend_fund(app.state.sqlite_store, app.state.bar_store)

    # 1. Recomputed features must contain the 5 new Float32 columns with accurate worked values
    feat = app.state.bar_store.load_features()
    for col in EXTENSION_FEATURE_COLS:
        assert col in feat.columns, f"Missing extension feature column: {col}"
        assert feat.schema[col] == pl.Float32

    last = feat.sort("trade_date").row(-1, named=True)
    # Independent worked checks against raw OHLCV/base cols on the final bar
    expected_bias60 = (last["close"] - last["ma60"]) / last["ma60"] * 100.0
    assert last["bias60"] == pytest.approx(expected_bias60, abs=1e-2)

    expected_pct_b = (last["close"] - last["boll_lower"]) / (last["boll_upper"] - last["boll_lower"])
    assert last["boll_pct_b"] == pytest.approx(expected_pct_b, abs=1e-3)

    expected_bias20_z = last["bias20"] / max(last["volatility_20d"], 0.5)
    assert last["bias20_z"] == pytest.approx(expected_bias20_z, abs=1e-2)

    tail60 = feat.sort("trade_date").tail(60)
    low60 = float(tail60["low"].min())
    high60 = float(tail60["high"].max())
    assert last["drawup_60d_low"] == pytest.approx((last["close"] / low60 - 1.0) * 100.0, abs=1e-2)
    assert last["pos_60d"] == pytest.approx(
        (last["close"] - low60) / (high60 - low60) * 100.0, abs=1e-2
    )

    # 2. Simulate a legacy v1 feature cache missing all 5 new columns -> scan recovers gracefully
    legacy_feat = feat.drop(list(EXTENSION_FEATURE_COLS))
    app.state.bar_store.save_features(legacy_feat)
    app.state.strategy_engine.invalidate_cache()

    with TestClient(app) as client:
        r = client.post(
            "/api/v1/scan",
            json={
                "strategies": [{"strategy_id": "volume_high_breakout", "enabled": True}],
                "combine_mode": "OR",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["actionable_fund_count"] == 1
        rep = data["actionable_groups"][0]["representative"]
        assert rep["trend_stage"] in {"oversold", "early", "mid", "overheated"}
        assert rep["bias20"] == pytest.approx(round(float(last["bias20"]), 2), abs=0.05)
        assert rep["bias60"] == pytest.approx(round(expected_bias60, 2), abs=0.05)
        assert rep["boll_pct_b"] == pytest.approx(round(expected_pct_b, 3), abs=0.01)
        assert rep["drawup_60d_low"] == pytest.approx(
            round((last["close"] / low60 - 1.0) * 100.0, 2), abs=0.05
        )
        assert rep["pos_60d"] == pytest.approx(
            round((last["close"] - low60) / (high60 - low60) * 100.0, 2), abs=0.05
        )


def test_extension_features_zero_volatility_guards() -> None:
    """Completely flat bars must yield finite neutral boll_pct_b (0.5) and pos_60d (50.0)."""
    flat = make_synthetic_bars("sh510000", [2.0] * 70, amounts=[20_000.0] * 70)
    # Force exact flat highs/lows too so 60d range == 0
    flat = flat.with_columns(pl.lit(2.0).alias("high"), pl.lit(2.0).alias("low"))
    feat = compute_base_features(flat)
    last = feat.sort("trade_date").row(-1, named=True)
    assert last["boll_pct_b"] == pytest.approx(0.5, abs=1e-4)
    assert last["pos_60d"] == pytest.approx(50.0, abs=1e-4)
    assert last["bias20_z"] == pytest.approx(0.0, abs=1e-4)
    assert last["drawup_60d_low"] == pytest.approx(0.0, abs=1e-4)


def _seed_volatility_fair_stage_market(sqlite_store: SQLiteStore, bar_store: BarStore) -> None:
    """Seed four funds spanning all four Trend Stages:
    - sh516310 (low-vol bank): modest ~+2.5% raw bias20, low vol -> overheated (watchlist/actionable)
    - sh512480 (high-vol semi): similar ~+3% raw bias20, high vol, >15% off 60d low -> mid (NOT overheated)
    - sh510300 (early startup): ma20 >= ma60, modest drawup <= 15%, controlled RSI & %B -> early
    - sz159915 (oversold): sharp selloff then small bounce -> oversold
    """
    funds = [
        ExchangeFund(
            symbol="sh516310",
            code="516310",
            exchange="sh",
            name="银行ETF易方达",
            fund_type="ETF",
            index_code="399986",
            index_name="中证银行指数",
            company_name="易方达基金",
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
            exchange_symbol="sh516310",
            otc_code="009860",
            otc_name="易方达中证银行ETF联接C",
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
            exchange_symbol="sh510300",
            otc_code="006131",
            otc_name="华泰柏瑞沪深300ETF联接C",
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
    # 1. Low-vol bank: calm 0.25%/day grind -> modest raw bias20 (~+2.5%) yet stretched on its own volatility
    c_bank = [1.0 + 0.0005 * i for i in range(60)] + [1.03 + 0.0026 * i for i in range(1, 31)]
    v_bank = [200_000.0] * 89 + [450_000.0]
    df_bank = make_synthetic_bars("sh516310", c_bank, volumes=v_bank, amounts=[40_000.0] * n)

    # 2. High-vol semi: wide ±4% swings after >20% advance from 60d low, positive 20d return,
    # ending ~+2.5% above MA20 (mid, NOT overheated because its own volatility is high)
    c_semi = (
        [2.0] * 45
        + [2.0 + 0.025 * i for i in range(1, 21)]  # climbs to 2.50
        + [
            2.50 + 0.006 * i + (0.13 if i % 2 == 0 else -0.10)
            for i in range(25)
        ]  # wide ±4-5% daily swings with gentle upward drift
    )
    c_semi[-1] = 2.72  # positive ret_20d, ~+2.5-3.5% above MA20, well inside wide Bollinger bands
    v_semi = [200_000.0] * 89 + [210_000.0]
    df_semi = make_synthetic_bars("sh512480", c_semi, volumes=v_semi, amounts=[40_000.0] * n)

    # 3. Early startup: realistic base with ±1% noise then modest uptrend (+7% off 60d low)
    # so MA20 >= MA60, drawup_60d_low <= 15%, RSI < 62, %B < 0.78 -> early
    c_early = (
        [3.0 + (0.03 if i % 2 == 0 else -0.03) for i in range(60)]
        + [3.0 + 0.007 * i + (0.04 if i % 2 == 0 else -0.03) for i in range(1, 28)]
        + [3.17, 3.15, 3.18]
    )
    v_early = [200_000.0] * 89 + [240_000.0]
    df_early = make_synthetic_bars("sh510300", c_early, volumes=v_early, amounts=[50_000.0] * n)

    # 4. Oversold: sharp selloff then green bounce candle
    c_over = [4.0] * 77 + [4.0 - 0.10 * i for i in range(1, 13)] + [2.88]
    df_over = make_synthetic_bars("sz159915", c_over, amounts=[30_000.0] * n)
    over_opens = df_over["open"].to_list()
    over_opens[-1] = 2.80
    df_over = df_over.with_columns(pl.Series("open", over_opens))

    all_bars = pl.concat([df_bank, df_semi, df_early, df_over])
    bar_store.upsert_daily_bars(all_bars)
    daily = bar_store.load_daily_bars()
    feat = compute_base_features(daily)
    bar_store.save_features(feat)
    sqlite_store.update_fund_bar_metrics(bar_store.compute_symbol_metrics())


def test_volatility_normalized_trend_stage_classification(tmp_settings: Settings) -> None:
    app = create_app(tmp_settings)
    _seed_volatility_fair_stage_market(app.state.sqlite_store, app.state.bar_store)

    with TestClient(app) as client:
        # Right-side regime scan captures sh516310, sh512480, sh510300 across actionable + watchlist
        r_right = client.post(
            "/api/v1/scan",
            json={
                "strategies": [
                    {
                        "strategy_id": "momentum_top_rank",
                        "enabled": True,
                        "params": {"top_k": 4, "risk_adjusted": False},
                    },
                    {"strategy_id": "volume_high_breakout", "enabled": True},
                ],
                "regime_combine_mode": "OR",
                "combine_mode": "OR",
            },
        )
        assert r_right.status_code == 200
        right_data = r_right.json()

        by_sym: dict[str, dict] = {}
        for g in right_data["actionable_groups"] + right_data["watchlist_groups"]:
            rep = g["representative"]
            by_sym[rep["symbol"]] = rep

        assert {"sh516310", "sh512480", "sh510300"}.issubset(by_sym.keys())

        bank = by_sym["sh516310"]
        semi = by_sym["sh512480"]
        early = by_sym["sh510300"]

        # Bank has modest raw bias20 (< 4%) yet is overheated relative to its own low volatility
        assert bank["bias20"] < 4.0
        assert bank["trend_stage"] == "overheated"

        # Semi has comparable raw bias20 (~2-4%) yet is NOT overheated due to higher own volatility
        assert semi["bias20"] >= 1.5
        assert semi["trend_stage"] == "mid"

        # Early startup has modest extension from 60d low and controlled RSI/%B -> early
        assert early["trend_stage"] == "early"
        assert early["drawup_60d_low"] <= 15.0

        # Standalone left-side scan captures sz159915 as oversold
        r_left = client.post(
            "/api/v1/scan",
            json={
                "strategies": [
                    {
                        "strategy_id": "rsi_oversold_rebound",
                        "enabled": True,
                        "params": {"oversold_threshold": 35.0},
                    }
                ],
                "combine_mode": "OR",
            },
        )
        assert r_left.status_code == 200
        left_groups = r_left.json()["actionable_groups"]
        assert len(left_groups) == 1
        assert left_groups[0]["representative"]["symbol"] == "sz159915"
        assert left_groups[0]["representative"]["trend_stage"] == "oversold"


def _make_breakout_series(symbol: str, spike_indices: set[int], n: int = 90) -> pl.DataFrame:
    """Build a synthetic uptrend series that fires volume_high_breakout strictly at `spike_indices`."""
    closes: list[float] = []
    vols: list[float] = []
    cur = 2.0
    for i in range(n):
        if i in spike_indices:
            cur = round(cur * 1.03, 4)
            vols.append(350_000.0)
        else:
            # Tiny calm drift after bar 60 so momentum_top_rank stays positive without breakout volume
            if i >= 60:
                cur = round(cur * 1.001, 4)
            vols.append(100_000.0)
        closes.append(cur)
    return make_synthetic_bars(symbol, closes, volumes=vols, amounts=[30_000.0] * n)


def test_fresh_signal_cooldown_and_hits_20d(tmp_settings: Settings) -> None:
    app = create_app(tmp_settings)
    specs = [
        ("sh510100", "510100", "IDX01", "全新启动指数", {89}),
        ("sh510200", "510200", "IDX02", "近期连发指数", {85, 89}),
        ("sh510300", "510300", "IDX03", "十日静默后新发指数", {78, 89}),
        ("sh510400", "510400", "IDX04", "五日静默边界指数", {83, 89}),
        ("sh510500", "510500", "IDX05", "主线观察历史触发指数", {84}),
    ]
    funds = [
        ExchangeFund(
            symbol=sym,
            code=code,
            exchange="sh",
            name=f"{name}ETF",
            fund_type="ETF",
            index_code=idx,
            index_name=name,
        )
        for sym, code, idx, name, _ in specs
    ]
    mappings = [
        OTCFundMapping(
            exchange_symbol=sym,
            otc_code=f"00{code[:4]}",
            otc_name=f"{name}联接C",
            share_class="C",
            mapping_type="FEEDER",
            purchase_status="开放申购",
        )
        for sym, code, _, name, _ in specs
    ]
    app.state.sqlite_store.replace_universe(funds, mappings)

    all_bars = pl.concat([_make_breakout_series(sym, spikes) for sym, _, _, _, spikes in specs])
    app.state.bar_store.upsert_daily_bars(all_bars)
    daily = app.state.bar_store.load_daily_bars()
    app.state.bar_store.save_features(compute_base_features(daily))
    app.state.sqlite_store.update_fund_bar_metrics(app.state.bar_store.compute_symbol_metrics())

    scan_strategies = [
        {"strategy_id": "momentum_top_rank", "enabled": True, "params": {"top_k": 10}},
        {
            "strategy_id": "volume_high_breakout",
            "enabled": True,
            "params": {"vol_multiplier": 1.8, "min_pct_chg": 1.0},
        },
    ]

    with TestClient(app) as client:
        # Default cooldown = 10 trading days
        r_default = client.post(
            "/api/v1/scan",
            json={
                "strategies": scan_strategies,
                "regime_combine_mode": "OR",
                "combine_mode": "OR",
            },
        )
        assert r_default.status_code == 200
        d10 = r_default.json()
        act10 = {g["representative"]["symbol"]: g["representative"] for g in d10["actionable_groups"]}
        wat10 = {g["representative"]["symbol"]: g["representative"] for g in d10["watchlist_groups"]}

        assert act10["sh510100"]["is_fresh"] is True
        assert act10["sh510100"]["hits_20d"] == 1

        assert act10["sh510200"]["is_fresh"] is False
        assert act10["sh510200"]["hits_20d"] == 2

        # Day 78 -> 10 quiet days (79..88) -> Day 89 is Fresh again!
        assert act10["sh510300"]["is_fresh"] is True
        assert act10["sh510300"]["hits_20d"] == 2

        # Day 83 -> only 5 quiet days (84..88) -> NOT Fresh under 10-day cooldown
        assert act10["sh510400"]["is_fresh"] is False
        assert act10["sh510400"]["hits_20d"] == 2

        # Watchlist fund had 1 actionable trigger 5 days ago, none today -> is_fresh=False, hits_20d=1
        assert wat10["sh510500"]["is_fresh"] is False
        assert wat10["sh510500"]["hits_20d"] == 1

        # Custom cooldown = 5 trading days: sh510400 (5 quiet days 84..88) becomes Fresh!
        r_cd5 = client.post(
            "/api/v1/scan",
            json={
                "strategies": scan_strategies,
                "regime_combine_mode": "OR",
                "combine_mode": "OR",
                "freshness_cooldown_days": 5,
            },
        )
        assert r_cd5.status_code == 200
        act5 = {
            g["representative"]["symbol"]: g["representative"]
            for g in r_cd5.json()["actionable_groups"]
        }
        assert act5["sh510400"]["is_fresh"] is True
        assert act5["sh510400"]["hits_20d"] == 2
        assert act5["sh510200"]["is_fresh"] is False


def test_anti_chase_ranking_hide_overheated_and_preset_persistence(
    tmp_settings: Settings,
) -> None:
    app = create_app(tmp_settings)
    funds = [
        ExchangeFund(
            symbol="sh516310",
            code="516310",
            exchange="sh",
            name="银行ETF易方达",
            fund_type="ETF",
            index_code="399986",
            index_name="中证银行指数",
        ),
        ExchangeFund(
            symbol="sh510300",
            code="510300",
            exchange="sh",
            name="沪深300ETF华泰柏瑞",
            fund_type="ETF",
            index_code="000300",
            index_name="沪深300指数",
        ),
        ExchangeFund(
            symbol="sh512800",
            code="512800",
            exchange="sh",
            name="银行ETF华宝",
            fund_type="ETF",
            index_code="930999",
            index_name="中证细分银行指数",
        ),
    ]
    mappings = [
        OTCFundMapping(
            exchange_symbol=f.symbol,
            otc_code=f"00{f.code[:4]}",
            otc_name=f"{f.name}联接C",
            share_class="C",
            mapping_type="FEEDER",
            purchase_status="开放申购",
        )
        for f in funds
    ]
    app.state.sqlite_store.replace_universe(funds, mappings)

    n = 90
    # 1. sh516310: overheated + repeat breakout (day 87 & 89), strong breakout vol (3.0x)
    c_hot = [1.0 + 0.0005 * i for i in range(60)] + [1.03 + 0.003 * i for i in range(1, 31)]
    c_hot[87] = 1.135
    c_hot[88] = 1.136
    c_hot[89] = 1.165
    v_hot = [200_000.0] * n
    v_hot[87] = 600_000.0
    v_hot[89] = 600_000.0
    df_hot = make_synthetic_bars("sh516310", c_hot, volumes=v_hot, amounts=[60_000.0] * n)

    # 2. sh510300: early + fresh breakout on day 89 only (ma20 >= ma60, drawup <= 15%, %B < 0.78, RSI < 62)
    c_early = (
        [3.0 + (0.04 if i % 2 == 0 else -0.04) for i in range(60)]
        + [3.08 + (0.11 if i % 2 == 0 else -0.10) for i in range(28)]
        + [3.14, 3.20]
    )
    v_early = [200_000.0] * 89 + [420_000.0]
    df_early = make_synthetic_bars("sh510300", c_early, volumes=v_early, amounts=[30_000.0] * n)

    # 3. sh512800: overheated watchlist leader (passes momentum_top_rank, no breakout on day 89)
    c_watch = [1.0 + 0.0005 * i for i in range(60)] + [1.03 + 0.0028 * i for i in range(1, 31)]
    v_watch = [200_000.0] * 89 + [120_000.0]
    df_watch = make_synthetic_bars("sh512800", c_watch, volumes=v_watch, amounts=[50_000.0] * n)

    all_bars = pl.concat([df_hot, df_early, df_watch])
    app.state.bar_store.upsert_daily_bars(all_bars)
    daily = app.state.bar_store.load_daily_bars()
    app.state.bar_store.save_features(compute_base_features(daily))
    app.state.sqlite_store.update_fund_bar_metrics(app.state.bar_store.compute_symbol_metrics())

    base_strategies = [
        {
            "strategy_id": "momentum_top_rank",
            "enabled": True,
            "params": {"top_k": 5, "risk_adjusted": False},
        },
        {
            "strategy_id": "volume_high_breakout",
            "enabled": True,
            "params": {"vol_multiplier": 1.6, "min_pct_chg": 0.5},
        },
    ]

    with TestClient(app) as client:
        # 1. hide_overheated=False (default): both actionable, but early+Fresh ranks ABOVE overheated!
        r_show = client.post(
            "/api/v1/scan",
            json={
                "strategies": base_strategies,
                "regime_combine_mode": "OR",
                "combine_mode": "OR",
                "hide_overheated": False,
            },
        )
        assert r_show.status_code == 200
        show = r_show.json()
        assert show["hide_overheated"] is False
        act_syms = [g["representative"]["symbol"] for g in show["actionable_groups"]]
        assert act_syms == ["sh510300", "sh516310"]

        early_rep = show["actionable_groups"][0]["representative"]
        hot_rep = show["actionable_groups"][1]["representative"]
        assert early_rep["trend_stage"] == "early"
        assert early_rep["is_fresh"] is True
        assert hot_rep["trend_stage"] == "overheated"
        assert hot_rep["is_fresh"] is False

        # Verify exact score adjustments: early (+5) + fresh (+8) vs overheated (-15)
        early_entry_score = early_rep["hit_strategies"][0]["score"]
        hot_entry_score = hot_rep["hit_strategies"][0]["score"]
        assert early_rep["composite_score"] == pytest.approx(
            round(early_entry_score + 5.0 + 5.0 + 8.0, 2), abs=0.05
        )
        assert hot_rep["composite_score"] == pytest.approx(
            round(hot_entry_score + 5.0 - 15.0, 2), abs=0.05
        )
        assert early_rep["composite_score"] > hot_rep["composite_score"]

        # Watchlist contains sh512800 tagged overheated
        assert [g["representative"]["symbol"] for g in show["watchlist_groups"]] == ["sh512800"]
        assert show["watchlist_groups"][0]["representative"]["trend_stage"] == "overheated"

        # 2. hide_overheated=True: removes sh516310 from actionable, keeps sh510300 & watchlist intact
        r_hide = client.post(
            "/api/v1/scan",
            json={
                "strategies": base_strategies,
                "regime_combine_mode": "OR",
                "combine_mode": "OR",
                "hide_overheated": True,
            },
        )
        assert r_hide.status_code == 200
        hidden = r_hide.json()
        assert hidden["hide_overheated"] is True
        assert [g["representative"]["symbol"] for g in hidden["actionable_groups"]] == ["sh510300"]
        assert hidden["actionable_fund_count"] == 1
        assert hidden["actionable_index_count"] == 1
        assert hidden["hit_fund_count"] == 1
        assert hidden["hit_index_count"] == 1
        # Mainline Watchlist stays intact (including overheated leader sh512800!)
        assert [g["representative"]["symbol"] for g in hidden["watchlist_groups"]] == ["sh512800"]
        assert hidden["watchlist_fund_count"] == 1

        # 3. Preset persistence of hide_overheated & freshness_cooldown_days
        preset_cfg = {
            "strategies": base_strategies,
            "regime_combine_mode": "OR",
            "combine_mode": "OR",
            "hide_overheated": True,
            "freshness_cooldown_days": 7,
        }
        r_save = client.post(
            "/api/v1/presets",
            json={"name": "防追高主线组合", "description": "过滤高位偏热", "config": preset_cfg},
        )
        assert r_save.status_code == 200
        loaded = client.get("/api/v1/presets").json()["presets"][0]["config"]
        assert loaded["hide_overheated"] is True
        assert loaded["freshness_cooldown_days"] == 7

        r_from_preset = client.post("/api/v1/scan", json=loaded)
        assert r_from_preset.status_code == 200
        assert [g["representative"]["symbol"] for g in r_from_preset.json()["actionable_groups"]] == [
            "sh510300"
        ]


def test_cli_scan_two_stage_trend_stage_fresh_and_hide_overheated(
    tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    sqlite_store = SQLiteStore(tmp_settings.sqlite_path)
    bar_store = BarStore(
        tmp_settings.daily_bars_path,
        tmp_settings.benchmarks_path,
        tmp_settings.features_path,
    )
    funds = [
        ExchangeFund(
            symbol="sh516310",
            code="516310",
            exchange="sh",
            name="银行ETF易方达",
            fund_type="ETF",
            index_code="399986",
            index_name="中证银行指数",
        ),
        ExchangeFund(
            symbol="sh510300",
            code="510300",
            exchange="sh",
            name="沪深300ETF华泰柏瑞",
            fund_type="ETF",
            index_code="000300",
            index_name="沪深300指数",
        ),
    ]
    mappings = [
        OTCFundMapping(
            exchange_symbol="sh516310",
            otc_code="009860",
            otc_name="易方达中证银行ETF联接C",
            share_class="C",
            mapping_type="FEEDER",
            purchase_status="开放申购",
        ),
        OTCFundMapping(
            exchange_symbol="sh510300",
            otc_code="006131",
            otc_name="华泰柏瑞沪深300ETF联接C",
            share_class="C",
            mapping_type="FEEDER",
            purchase_status="开放申购",
        ),
    ]
    sqlite_store.replace_universe(funds, mappings)

    n = 90
    # sh516310: overheated + fresh breakout on final bar (passes momentum_top_rank + volume_high_breakout)
    c_hot = [1.0 + 0.0005 * i for i in range(60)] + [1.03 + 0.003 * i for i in range(1, 30)] + [1.15]
    v_hot = [200_000.0] * 89 + [500_000.0]
    df_hot = make_synthetic_bars("sh516310", c_hot, volumes=v_hot, amounts=[40_000.0] * n)

    # sh510300: early + regime-only (passes momentum_top_rank, no breakout volume -> watchlist)
    c_early = (
        [3.0 + (0.03 if i % 2 == 0 else -0.03) for i in range(60)]
        + [3.0 + 0.007 * i + (0.04 if i % 2 == 0 else -0.03) for i in range(1, 28)]
        + [3.17, 3.15, 3.18]
    )
    v_early = [200_000.0] * n
    df_early = make_synthetic_bars("sh510300", c_early, volumes=v_early, amounts=[50_000.0] * n)

    bar_store.upsert_daily_bars(pl.concat([df_hot, df_early]))
    bar_store.save_features(compute_base_features(bar_store.load_daily_bars()))
    sqlite_store.update_fund_bar_metrics(bar_store.compute_symbol_metrics())

    monkeypatch.setattr(cli_module, "get_settings", lambda: tmp_settings)

    runner = CliRunner(env={"COLUMNS": "160"})
    res = runner.invoke(
        cli_module.app,
        [
            "scan",
            "-s",
            "momentum_top_rank",
            "-s",
            "volume_high_breakout",
            "--regime-combine",
            "OR",
            "--combine",
            "OR",
            "--cooldown",
            "10",
            "--share",
            "C",
        ],
    )
    assert res.exit_code == 0, res.output
    out = res.output
    assert "Actionable: 1 indices / 1 funds" in out
    assert "Watchlist: 1 indices / 1 funds" in out
    assert "Stage" in out
    assert "Fresh" in out
    assert "FRESH" in out
    assert "overheated" in out
    assert "early" in out
    assert "516310" in out
    assert "510300" in out
    assert "009860(C)" in out
    assert "006131(C)" in out

    # --hide-overheated excludes overheated actionable fund (516310) while keeping watchlist
    res_hidden = runner.invoke(
        cli_module.app,
        [
            "scan",
            "-s",
            "momentum_top_rank",
            "-s",
            "volume_high_breakout",
            "--hide-overheated",
        ],
    )
    assert res_hidden.exit_code == 0, res_hidden.output
    assert "Actionable: 0 indices / 0 funds" in res_hidden.output
    assert "Watchlist: 1 indices / 1 funds" in res_hidden.output
    assert "510300" in res_hidden.output
    assert "516310" not in res_hidden.output






"""Unit tests for all 8 built-in vectorized strategies & indicator precomputation."""

from __future__ import annotations

import math
from datetime import date

import polars as pl
from tests.conftest import make_synthetic_bars

from chancedetect.strategies.indicators import compute_base_features
from chancedetect.strategies.mean_reversion import (
    BollLowerReversalStrategy,
    MaNegativeBiasStrategy,
    RsiOversoldReboundStrategy,
)
from chancedetect.strategies.momentum import (
    MomentumTopRankStrategy,
    RelativeStrengthRsStrategy,
)
from chancedetect.strategies.registry import create_default_registry
from chancedetect.strategies.trend import (
    MaBullishAlignmentStrategy,
    MacdGoldenCrossStrategy,
    MidTermUptrendStrategy,
    TrendPullbackEntryStrategy,
    VolumeHighBreakoutStrategy,
)


def test_registry_has_all_ten_strategies_with_schemas() -> None:
    reg = create_default_registry()
    meta = reg.list_meta()
    assert len(meta) == 10
    regimes = [m for m in meta if m["role"] == "regime"]
    entries = [m for m in meta if m["role"] == "entry"]
    assert len(regimes) == 3
    assert len(entries) == 7
    assert {m["id"] for m in regimes} == {
        "mid_term_uptrend",
        "momentum_top_rank",
        "relative_strength_rs",
    }
    assert {m["id"] for m in entries} == {
        "trend_pullback_entry",
        "ma_bullish_alignment",
        "volume_high_breakout",
        "macd_golden_cross",
        "rsi_oversold_rebound",
        "boll_lower_reversal",
        "ma_negative_bias",
    }
    categories = {m["category"] for m in meta}
    assert categories == {"trend", "mean_reversion", "momentum"}
    for m in meta:
        assert "properties" in m["params_schema"]
        assert isinstance(m["default_params"], dict)


def test_ma_bullish_alignment_fires_only_on_first_day_formation_with_cooldown() -> None:
    # 50 days flat around 2.0, then 20-day steady rally -> alignment forms once and continues
    closes = [2.0] * 50 + [2.0 + 0.03 * i for i in range(1, 21)]
    bars = make_synthetic_bars("sh510300", closes, volumes=[100_000.0] * 70)
    bars = bars.with_columns(pl.col("close").pct_change().mul(100.0).fill_null(0.0).alias("pct_chg"))
    panel = compute_base_features(bars)
    empty_bench = pl.DataFrame(
        schema={"benchmark_code": pl.Utf8, "trade_date": pl.Date, "close": pl.Float64}
    )

    s1 = MaBullishAlignmentStrategy()
    r1 = s1.evaluate(panel, empty_bench, s1.default_params())
    hit_indices = [i for i, sig in enumerate(r1["signal"].to_list()) if sig]

    # Must fire strictly once on first formation day and remain silent on all continuation days
    assert len(hit_indices) == 1
    assert hit_indices[0] < len(closes) - 1
    assert r1["signal"][-1] is False
    assert r1["score"][hit_indices[0]] > 0

    # Brief 2-day break in alignment then re-alignment: cooldown_days=1 fires, cooldown_days=5 suppresses
    # Flat 40d -> rally 15d (aligned) -> dip (breaks MA5>MA10 for 2 days) -> strong rebound (re-aligns)
    closes_re = (
        [2.0] * 40
        + [2.0 + 0.03 * i for i in range(1, 16)]
        + [2.10, 2.05, 2.15]
        + [2.45, 2.55, 2.65, 2.75]
    )
    bars_re = make_synthetic_bars("sh510300", closes_re, volumes=[100_000.0] * len(closes_re))
    bars_re = bars_re.with_columns(
        pl.col("close").pct_change().mul(100.0).fill_null(0.0).alias("pct_chg")
    )
    panel_re = compute_base_features(bars_re)

    r_cd1 = s1.evaluate(panel_re, empty_bench, s1.parse_params({"cooldown_days": 1}))
    r_cd5 = s1.evaluate(panel_re, empty_bench, s1.parse_params({"cooldown_days": 5}))
    hits_cd1 = [i for i, sig in enumerate(r_cd1["signal"].to_list()) if sig]
    hits_cd5 = [i for i, sig in enumerate(r_cd5["signal"].to_list()) if sig]

    assert len(hits_cd1) == 2
    assert len(hits_cd5) == 1
    assert hits_cd5[0] == hits_cd1[0]


def test_trend_strategies_trigger_on_uptrend_breakout() -> None:
    # 50 days flat around 2.0, then gentle ramp and clean volume breakout at end (within bias20 cap)
    closes = (
        [2.0 + 0.002 * math.sin(i) for i in range(50)]
        + [2.0 + 0.01 * i for i in range(1, 20)]
        + [2.23]
    )
    vols = [100_000.0] * 69 + [260_000.0]
    bars = make_synthetic_bars("sh510300", closes, volumes=vols)
    bars = bars.with_columns(pl.col("close").pct_change().mul(100.0).fill_null(0.0).alias("pct_chg"))
    panel = compute_base_features(bars)
    empty_bench = pl.DataFrame(schema={"benchmark_code": pl.Utf8, "trade_date": pl.Date, "close": pl.Float64})

    # Volume high breakout should trigger on last day
    s2 = VolumeHighBreakoutStrategy()
    r2 = s2.evaluate(panel, empty_bench, s2.default_params())
    assert r2["signal"][-1] is True
    assert "突破" in r2["reason"][-1]


def test_volume_high_breakout_respects_max_bias20_pct_anti_chase_cap() -> None:
    # 50 days flat at 2.0, then steep 15-day surge so final bar breaks 20d high with bias20 > 12%
    closes = [2.0] * 50 + [2.0 + 0.05 * i for i in range(1, 16)]
    vols = [100_000.0] * 64 + [280_000.0]
    bars = make_synthetic_bars("sh510300", closes, volumes=vols)
    bars = bars.with_columns(pl.col("close").pct_change().mul(100.0).fill_null(0.0).alias("pct_chg"))
    panel = compute_base_features(bars)
    assert float(panel["bias20"][-1]) > 10.0

    empty_bench = pl.DataFrame(
        schema={"benchmark_code": pl.Utf8, "trade_date": pl.Date, "close": pl.Float64}
    )
    strat = VolumeHighBreakoutStrategy()

    # Default ceiling (8.0%) suppresses overextended vertical spike
    r_default = strat.evaluate(panel, empty_bench, strat.default_params())
    assert r_default["signal"][-1] is False

    # Higher ceiling (20.0%) permits the breakout
    r_loose = strat.evaluate(panel, empty_bench, strat.parse_params({"max_bias20_pct": 20.0}))
    assert r_loose["signal"][-1] is True

    # Setting max_bias20_pct=0 disables the anti-chase ceiling
    r_disabled = strat.evaluate(panel, empty_bench, strat.parse_params({"max_bias20_pct": 0.0}))
    assert r_disabled["signal"][-1] is True


def test_macd_golden_cross_triggers_on_turnaround() -> None:
    # Decline for 40 days then strong rally for 15 days -> MACD must cross upward during turn
    closes = [3.0 - 0.02 * i for i in range(40)] + [2.2 + 0.05 * i for i in range(1, 16)]
    bars = make_synthetic_bars("sh510300", closes)
    bars = bars.with_columns(pl.col("close").pct_change().mul(100.0).fill_null(0.0).alias("pct_chg"))
    panel = compute_base_features(bars)
    empty_bench = pl.DataFrame(schema={"benchmark_code": pl.Utf8, "trade_date": pl.Date, "close": pl.Float64})

    s = MacdGoldenCrossStrategy()
    r = s.evaluate(panel, empty_bench, s.default_params())
    assert r.filter(pl.col("signal")).height >= 1


def test_mean_reversion_strategies_trigger_on_oversold_rebound() -> None:
    # Stable at 4.0 for 30 days, sharp selloff for 12 days, then bounce day
    closes = [4.0] * 30 + [4.0 - 0.10 * i for i in range(1, 13)] + [2.88]
    bars = make_synthetic_bars("sz159915", closes)
    # Make last bar a clear green/up candle (close > open)
    opens = bars["open"].to_list()
    opens[-1] = 2.80
    bars = bars.with_columns(
        pl.Series("open", opens),
        pl.col("close").pct_change().mul(100.0).fill_null(0.0).alias("pct_chg"),
    )
    panel = compute_base_features(bars)
    empty_bench = pl.DataFrame(schema={"benchmark_code": pl.Utf8, "trade_date": pl.Date, "close": pl.Float64})

    # RSI oversold rebound
    s_rsi = RsiOversoldReboundStrategy()
    r_rsi = s_rsi.evaluate(panel, empty_bench, s_rsi.parse_params({"oversold_threshold": 35.0}))
    assert r_rsi["signal"][-1] is True

    # Negative bias extreme (close ~2.88 vs MA20 ~3.45 -> ~-16% bias)
    s_bias = MaNegativeBiasStrategy()
    r_bias = s_bias.evaluate(panel, empty_bench, s_bias.parse_params({"min_negative_bias_pct": 5.0}))
    assert r_bias["signal"][-1] is True

    # Bollinger lower band reversal (lower band was penetrated on day -2, then bounces above)
    closes_boll = [4.0 + 0.01 * (i % 3) for i in range(30)] + [3.95, 3.88, 3.80, 3.89]
    bars_b = make_synthetic_bars("sz159915", closes_boll)
    lows_b = bars_b["low"].to_list()
    lows_b[-2] = 3.70  # penetrate lower band clearly
    opens_b = bars_b["open"].to_list()
    opens_b[-1] = 3.82
    bars_b = bars_b.with_columns(
        pl.Series("low", lows_b),
        pl.Series("open", opens_b),
        pl.col("close").pct_change().mul(100.0).fill_null(0.0).alias("pct_chg"),
    )
    panel_b = compute_base_features(bars_b)
    s_boll = BollLowerReversalStrategy()
    r_boll = s_boll.evaluate(panel_b, empty_bench, s_boll.default_params())
    assert r_boll["signal"][-1] is True


def test_momentum_and_relative_strength_strategies() -> None:
    # Strong fund rallies +35%, weak fund drops -10%, benchmark stays flat
    n = 80
    strong_closes = [1.0 + 0.005 * i for i in range(n)]
    weak_closes = [1.0 - 0.001 * i for i in range(n)]
    bench_closes = [3900.0 + 2.0 * math.sin(i) for i in range(n)]

    df_strong = make_synthetic_bars("sh510300", strong_closes)
    df_weak = make_synthetic_bars("sz159915", weak_closes)
    bars = pl.concat([df_strong, df_weak]).with_columns(
        pl.col("close").pct_change().over("symbol").mul(100.0).fill_null(0.0).alias("pct_chg")
    )
    dates = df_strong["trade_date"].to_list()
    bench = pl.DataFrame(
        {
            "benchmark_code": ["sh000300"] * n,
            "trade_date": dates,
            "open": bench_closes,
            "high": bench_closes,
            "low": bench_closes,
            "close": bench_closes,
            "volume": [1e7] * n,
            "amount": [1e7] * n,
            "pct_chg": [0.0] * n,
        }
    )

    panel = compute_base_features(bars, bench, primary_benchmark="sh000300")

    s_mom = MomentumTopRankStrategy()
    r_mom = s_mom.evaluate(panel, bench, s_mom.parse_params({"top_k": 1}))
    last_dt: date = dates[-1]
    last_mom = r_mom.filter(pl.col("trade_date") == last_dt)
    hits_mom = last_mom.filter(pl.col("signal"))["symbol"].to_list()
    assert hits_mom == ["sh510300"]

    s_rs = RelativeStrengthRsStrategy()
    r_rs = s_rs.evaluate(panel, bench, s_rs.parse_params({"min_excess_return_pct": 2.0}))
    last_rs = r_rs.filter((pl.col("trade_date") == last_dt) & pl.col("signal"))["symbol"].to_list()
    assert last_rs == ["sh510300"]


def test_mid_term_uptrend_passes_uptrend_and_rejects_flat_or_declining() -> None:
    n = 90
    # 1. Established uptrend: close > MA60, MA20 > MA60, rising MA60
    up_closes = [1.0 + 0.01 * i for i in range(n)]
    # 2. Downtrend: close < MA60, MA20 < MA60, falling MA60
    down_closes = [3.0 - 0.01 * i for i in range(n)]
    # 3. Flat trend: constant price (no uptrend)
    flat_closes = [2.0] * n
    # 4. Declining MA60 despite short-term bounce (big drop earlier, recent bounce above MA60 while MA60 still falls)
    # 20 days at 4.0, 50 days falling to 1.5, 20 days bouncing to 2.4 -> close > MA60, MA20 > MA60, but MA60 still dropping as 4.0 rolls off
    declining_ma60_closes = (
        [4.0] * 20
        + [4.0 - 0.05 * i for i in range(1, 51)]
        + [1.5 + 0.055 * i for i in range(1, 21)]
    )

    bars = pl.concat(
        [
            make_synthetic_bars("sh510300", up_closes),
            make_synthetic_bars("sz159915", down_closes),
            make_synthetic_bars("sh512800", flat_closes),
            make_synthetic_bars("sh512480", declining_ma60_closes),
        ]
    ).with_columns(
        pl.col("close").pct_change().over("symbol").mul(100.0).fill_null(0.0).alias("pct_chg")
    )
    panel = compute_base_features(bars)
    empty_bench = pl.DataFrame(
        schema={"benchmark_code": pl.Utf8, "trade_date": pl.Date, "close": pl.Float64}
    )

    strat = MidTermUptrendStrategy()
    assert strat.id == "mid_term_uptrend"
    assert strat.role == "regime"

    res = strat.evaluate(panel, empty_bench, strat.default_params())
    last_dt = bars["trade_date"].max()
    last_day = {
        row["symbol"]: (row["signal"], row["score"], row["reason"])
        for row in res.filter(pl.col("trade_date") == last_dt).iter_rows(named=True)
    }

    assert last_day["sh510300"][0] is True
    assert last_day["sh510300"][1] > 0
    assert last_day["sh510300"][2] != ""
    assert last_day["sz159915"][0] is False
    assert last_day["sh512800"][0] is False
    assert last_day["sh512480"][0] is False


def test_trend_pullback_entry_triggers_on_uptrend_pullback_and_rejects_stretched_or_downtrend() -> None:
    # 1. Valid pullback: 75-day uptrend from 1.0 -> 1.75, 2-day pullback near MA10 (~1.70), then bullish stabilization bar
    pullback_closes = [1.0 + 0.01 * i for i in range(75)] + [1.72, 1.69, 1.715]
    df_pullback = make_synthetic_bars(
        "sh510300", pullback_closes, volumes=[100_000.0] * 77 + [90_000.0]
    )
    opens_pb = df_pullback["open"].to_list()
    opens_pb[-1] = 1.695  # bullish close >= open
    df_pullback = df_pullback.with_columns(pl.Series("open", opens_pb))

    # 2. Stretched rally far above MA10/MA20 (vertical spike at end)
    stretched_closes = [1.0 + 0.008 * i for i in range(68)] + [
        1.55 + 0.04 * i for i in range(1, 11)
    ]
    df_stretched = make_synthetic_bars("sh512480", stretched_closes)

    # 3. Downtrend with bounce near MA10 (fails mid-term uptrend context)
    downtrend_closes = [3.0 - 0.015 * i for i in range(75)] + [1.92, 1.90, 1.93]
    df_downtrend = make_synthetic_bars("sz159915", downtrend_closes)
    opens_dt = df_downtrend["open"].to_list()
    opens_dt[-1] = 1.905
    df_downtrend = df_downtrend.with_columns(pl.Series("open", opens_dt))

    # 4. Still falling on pullback (no stabilization: bearish close & negative return)
    falling_closes = [1.0 + 0.01 * i for i in range(75)] + [1.73, 1.71, 1.685]
    df_falling = make_synthetic_bars("sh512800", falling_closes)
    opens_fl = df_falling["open"].to_list()
    opens_fl[-1] = 1.71
    df_falling = df_falling.with_columns(pl.Series("open", opens_fl))

    # 5. Pullback with excessive distribution volume (vol_ratio > max_vol_ratio)
    heavy_vol_closes = list(pullback_closes)
    df_heavy_vol = make_synthetic_bars(
        "sh510500", heavy_vol_closes, volumes=[100_000.0] * 77 + [350_000.0]
    )
    opens_hv = df_heavy_vol["open"].to_list()
    opens_hv[-1] = 1.695
    df_heavy_vol = df_heavy_vol.with_columns(pl.Series("open", opens_hv))

    bars = pl.concat([df_pullback, df_stretched, df_downtrend, df_falling, df_heavy_vol]).with_columns(
        pl.col("close").pct_change().over("symbol").mul(100.0).fill_null(0.0).alias("pct_chg")
    )
    panel = compute_base_features(bars)
    empty_bench = pl.DataFrame(
        schema={"benchmark_code": pl.Utf8, "trade_date": pl.Date, "close": pl.Float64}
    )

    strat = TrendPullbackEntryStrategy()
    assert strat.id == "trend_pullback_entry"
    assert strat.role == "entry"

    res = strat.evaluate(panel, empty_bench, strat.default_params())
    last_dt = bars["trade_date"].max()
    last_day = {
        row["symbol"]: (row["signal"], row["score"], row["reason"])
        for row in res.filter(pl.col("trade_date") == last_dt).iter_rows(named=True)
    }

    assert last_day["sh510300"][0] is True
    assert last_day["sh510300"][1] > 0
    assert last_day["sh510300"][2] != ""
    assert last_day["sh512480"][0] is False
    assert last_day["sz159915"][0] is False
    assert last_day["sh512800"][0] is False
    assert last_day["sh510500"][0] is False

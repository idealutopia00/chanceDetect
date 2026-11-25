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
    VolumeHighBreakoutStrategy,
)


def test_registry_has_all_eight_strategies_with_schemas() -> None:
    reg = create_default_registry()
    meta = reg.list_meta()
    assert len(meta) == 8
    categories = {m["category"] for m in meta}
    assert categories == {"trend", "mean_reversion", "momentum"}
    for m in meta:
        assert "properties" in m["params_schema"]
        assert isinstance(m["default_params"], dict)


def test_trend_strategies_trigger_on_uptrend_breakout() -> None:
    # 70 days flat around 2.0, then steady ramp and sharp volume breakout at end
    closes = [2.0 + 0.002 * math.sin(i) for i in range(50)] + [2.0 + 0.03 * i for i in range(1, 21)]
    vols = [100_000.0] * 69 + [260_000.0]
    bars = make_synthetic_bars("sh510300", closes, volumes=vols)
    bars = bars.with_columns(pl.col("close").pct_change().mul(100.0).fill_null(0.0).alias("pct_chg"))
    panel = compute_base_features(bars)
    empty_bench = pl.DataFrame(schema={"benchmark_code": pl.Utf8, "trade_date": pl.Date, "close": pl.Float64})

    # MA bullish alignment should trigger on last day
    s1 = MaBullishAlignmentStrategy()
    r1 = s1.evaluate(panel, empty_bench, s1.default_params())
    assert r1["signal"][-1] is True
    assert r1["score"][-1] > 0

    # Volume high breakout should trigger on last day
    s2 = VolumeHighBreakoutStrategy()
    r2 = s2.evaluate(panel, empty_bench, s2.default_params())
    assert r2["signal"][-1] is True
    assert "突破" in r2["reason"][-1]


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

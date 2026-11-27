"""Vectorized Polars technical indicator computation for precomputed features and custom params."""

from __future__ import annotations

from typing import Literal

import polars as pl

TrendStage = Literal["oversold", "early", "mid", "overheated"]


def ma_expr(panel: pl.DataFrame, period: int) -> pl.Expr:
    """Return cached moving average column if present, otherwise compute lazily over each symbol."""
    col_name = f"ma{period}"
    if col_name in panel.columns:
        return pl.col(col_name)
    return pl.col("close").rolling_mean(period).over("symbol")


def bias20_expr(panel: pl.DataFrame) -> pl.Expr:
    """Return cached 20-day moving-average deviation (%) if present, otherwise compute lazily."""
    if "bias20" in panel.columns:
        return pl.col("bias20")
    ma20 = ma_expr(panel, 20)
    return (pl.col("close") - ma20) / ma20 * 100.0


def rsi_expr(close_col: str = "close", period: int = 14) -> pl.Expr:
    """Compute Wilder-smoothed RSI over each symbol partition."""
    diff = pl.col(close_col).diff().over("symbol")
    gain = pl.when(diff > 0).then(diff).otherwise(0.0)
    loss = pl.when(diff < 0).then(-diff).otherwise(0.0)
    avg_gain = gain.ewm_mean(alpha=1.0 / period, adjust=False, min_samples=period).over("symbol")
    avg_loss = loss.ewm_mean(alpha=1.0 / period, adjust=False, min_samples=period).over("symbol")
    rs = avg_gain / pl.when(avg_loss == 0.0).then(1e-12).otherwise(avg_loss)
    return (100.0 - (100.0 / (1.0 + rs))).round(4)


EXTENSION_FEATURE_COLS = (
    "bias60",
    "boll_pct_b",
    "bias20_z",
    "drawup_60d_low",
    "pos_60d",
)


def ensure_extension_features(df: pl.DataFrame, *, preserve_order: bool = False) -> pl.DataFrame:
    """Populate volatility-normalized extension & crowding features if missing (lazy legacy fallback)."""
    if df.is_empty():
        return df

    missing = [c for c in EXTENSION_FEATURE_COLS if c not in df.columns]
    if not missing:
        return df

    if preserve_order:
        out = df.with_row_index("_orig_row_idx").sort(["symbol", "trade_date"])
    else:
        out = df.sort(["symbol", "trade_date"])

    # Ensure prerequisite columns exist if a minimal frame was supplied
    prereq_exprs: list[pl.Expr] = []
    if "ma20" not in out.columns:
        prereq_exprs.append(pl.col("close").rolling_mean(20).over("symbol").alias("ma20"))
    if "ma60" not in out.columns:
        prereq_exprs.append(pl.col("close").rolling_mean(60).over("symbol").alias("ma60"))
    if "boll_std20" not in out.columns and ("boll_upper" not in out.columns or "boll_lower" not in out.columns):
        prereq_exprs.append(pl.col("close").rolling_std(20).over("symbol").alias("boll_std20"))
    if "volatility_20d" not in out.columns and "bias20_z" in missing:
        prereq_exprs.append(pl.col("pct_chg").rolling_std(20).over("symbol").alias("volatility_20d"))
    if prereq_exprs:
        out = out.with_columns(prereq_exprs)

    derived_prereqs: list[pl.Expr] = []
    if "boll_upper" not in out.columns and "boll_pct_b" in missing:
        derived_prereqs.append((pl.col("ma20") + 2.0 * pl.col("boll_std20")).alias("boll_upper"))
    if "boll_lower" not in out.columns and "boll_pct_b" in missing:
        derived_prereqs.append((pl.col("ma20") - 2.0 * pl.col("boll_std20")).alias("boll_lower"))
    if "bias20" not in out.columns and "bias20_z" in missing:
        derived_prereqs.append(((pl.col("close") - pl.col("ma20")) / pl.col("ma20") * 100.0).alias("bias20"))
    if derived_prereqs:
        out = out.with_columns(derived_prereqs)

    need_60d_range = "drawup_60d_low" in missing or "pos_60d" in missing
    low_60d = pl.col("low").rolling_min(60).over("symbol")
    high_60d = pl.col("high").rolling_max(60).over("symbol")
    if need_60d_range:
        out = out.with_columns(
            low_60d.alias("_tmp_low_60d"),
            high_60d.alias("_tmp_high_60d"),
        )

    exprs: list[pl.Expr] = []
    if "bias60" in missing:
        exprs.append(
            pl.when(pl.col("ma60") > 0)
            .then((pl.col("close") - pl.col("ma60")) / pl.col("ma60") * 100.0)
            .otherwise(None)
            .cast(pl.Float32)
            .alias("bias60")
        )
    if "boll_pct_b" in missing:
        band_width = pl.col("boll_upper") - pl.col("boll_lower")
        exprs.append(
            pl.when(pl.col("boll_upper").is_null() | pl.col("boll_lower").is_null())
            .then(None)
            .when(band_width.abs() <= 1e-12)
            .then(0.5)
            .otherwise((pl.col("close") - pl.col("boll_lower")) / band_width)
            .cast(pl.Float32)
            .alias("boll_pct_b")
        )
    if "bias20_z" in missing:
        exprs.append(
            pl.when(pl.col("bias20").is_null() | pl.col("volatility_20d").is_null())
            .then(None)
            .otherwise(pl.col("bias20") / pl.max_horizontal(pl.col("volatility_20d"), pl.lit(0.5)))
            .cast(pl.Float32)
            .alias("bias20_z")
        )
    if "drawup_60d_low" in missing:
        exprs.append(
            pl.when(pl.col("_tmp_low_60d") > 0)
            .then((pl.col("close") / pl.col("_tmp_low_60d") - 1.0) * 100.0)
            .otherwise(None)
            .cast(pl.Float32)
            .alias("drawup_60d_low")
        )
    if "pos_60d" in missing:
        range_60d = pl.col("_tmp_high_60d") - pl.col("_tmp_low_60d")
        exprs.append(
            pl.when(pl.col("_tmp_high_60d").is_null() | pl.col("_tmp_low_60d").is_null())
            .then(None)
            .when(range_60d.abs() <= 1e-12)
            .then(50.0)
            .otherwise((pl.col("close") - pl.col("_tmp_low_60d")) / range_60d * 100.0)
            .cast(pl.Float32)
            .alias("pos_60d")
        )

    out = out.with_columns(exprs)
    if need_60d_range:
        out = out.drop(["_tmp_low_60d", "_tmp_high_60d"])
    if preserve_order:
        out = out.sort("_orig_row_idx").drop("_orig_row_idx")
    return out


TREND_STAGE_THRESHOLDS: dict[str, dict[str, float]] = {
    "overheated": {
        "boll_pct_b_min": 0.85,
        "bias20_z_min": 2.2,
        "rsi14_min": 65.0,
        "pos_60d_min": 85.0,
    },
    "oversold": {
        "rsi14_max": 38.0,
        "boll_pct_b_max": 0.15,
        "bias20_z_max": -2.0,
    },
    "early": {
        "drawup_60d_low_max": 15.0,
        "rsi14_max": 62.0,
        "boll_pct_b_max": 0.78,
    },
}


def trend_stage_expr() -> pl.Expr:
    """Classify each (symbol, trade_date) row into volatility-normalized Trend Stage."""
    ov = TREND_STAGE_THRESHOLDS["overheated"]
    os_ = TREND_STAGE_THRESHOLDS["oversold"]
    ea = TREND_STAGE_THRESHOLDS["early"]

    is_overheated = (
        (pl.col("boll_pct_b") >= ov["boll_pct_b_min"]).fill_null(False)
        | (pl.col("bias20_z") >= ov["bias20_z_min"]).fill_null(False)
    ) & (
        (pl.col("rsi14") >= ov["rsi14_min"]).fill_null(False)
        | (pl.col("pos_60d") >= ov["pos_60d_min"]).fill_null(False)
    )
    is_oversold = (
        (pl.col("rsi14") <= os_["rsi14_max"]).fill_null(False)
        | (pl.col("boll_pct_b") <= os_["boll_pct_b_max"]).fill_null(False)
        | (pl.col("bias20_z") <= os_["bias20_z_max"]).fill_null(False)
    )
    is_early = (
        (pl.col("ma20") >= pl.col("ma60")).fill_null(False)
        & (pl.col("drawup_60d_low") <= ea["drawup_60d_low_max"]).fill_null(False)
        & (pl.col("rsi14") < ea["rsi14_max"]).fill_null(False)
        & (pl.col("boll_pct_b") < ea["boll_pct_b_max"]).fill_null(False)
    )
    return (
        pl.when(is_overheated)
        .then(pl.lit("overheated"))
        .when(is_oversold)
        .then(pl.lit("oversold"))
        .when(is_early)
        .then(pl.lit("early"))
        .otherwise(pl.lit("mid"))
    )


def compute_base_features(
    bars: pl.DataFrame,
    benchmarks: pl.DataFrame | None = None,
    primary_benchmark: str = "sh000300",
) -> pl.DataFrame:
    """Precompute standard technical indicators across the full multi-symbol panel.

    Expects `bars` sorted by (symbol, trade_date).
    """
    if bars.is_empty():
        return bars

    df = bars.sort(["symbol", "trade_date"])

    # Stage 1: Moving averages, EMAs, returns, volatility, volume/amount rolling stats
    df = df.with_columns(
        pl.int_range(1, pl.len() + 1).over("symbol").alias("listing_days"),
        pl.col("close").rolling_mean(5).over("symbol").alias("ma5"),
        pl.col("close").rolling_mean(10).over("symbol").alias("ma10"),
        pl.col("close").rolling_mean(20).over("symbol").alias("ma20"),
        pl.col("close").rolling_mean(60).over("symbol").alias("ma60"),
        pl.col("close").rolling_mean(120).over("symbol").alias("ma120"),
        pl.col("close").rolling_std(20).over("symbol").alias("boll_std20"),
        pl.col("close").ewm_mean(span=12, adjust=False).over("symbol").alias("ema12"),
        pl.col("close").ewm_mean(span=26, adjust=False).over("symbol").alias("ema26"),
        pl.col("volume").rolling_mean(5).over("symbol").alias("vol_ma5"),
        pl.col("volume").rolling_mean(20).over("symbol").alias("vol_ma20"),
        pl.col("amount").rolling_mean(20, min_samples=1).over("symbol").alias("avg_amount_20d"),
        (pl.col("close") / pl.col("close").shift(5).over("symbol") - 1.0).mul(100.0).alias("ret_5d"),
        (pl.col("close") / pl.col("close").shift(10).over("symbol") - 1.0).mul(100.0).alias("ret_10d"),
        (pl.col("close") / pl.col("close").shift(20).over("symbol") - 1.0).mul(100.0).alias("ret_20d"),
        (pl.col("close") / pl.col("close").shift(60).over("symbol") - 1.0).mul(100.0).alias("ret_60d"),
        pl.col("pct_chg").rolling_std(20).over("symbol").alias("volatility_20d"),
        pl.col("close").shift(1).rolling_max(20).over("symbol").alias("prev_high_20d"),
        rsi_expr("close", 14).alias("rsi14"),
    )

    # Stage 2: MACD, Bollinger bands, BIAS20, Volume Ratio
    df = df.with_columns(
        (pl.col("ema12") - pl.col("ema26")).alias("macd_dif"),
        pl.col("ma20").alias("boll_mid"),
        (pl.col("ma20") + 2.0 * pl.col("boll_std20")).alias("boll_upper"),
        (pl.col("ma20") - 2.0 * pl.col("boll_std20")).alias("boll_lower"),
        ((pl.col("close") - pl.col("ma20")) / pl.col("ma20") * 100.0).alias("bias20"),
        (
            pl.col("volume")
            / pl.when(pl.col("vol_ma20") > 0).then(pl.col("vol_ma20")).otherwise(None)
        ).alias("vol_ratio"),
    )

    df = df.with_columns(
        pl.col("macd_dif").ewm_mean(span=9, adjust=False).over("symbol").alias("macd_dea"),
    ).with_columns(
        ((pl.col("macd_dif") - pl.col("macd_dea")) * 2.0).alias("macd_hist"),
    )

    df = ensure_extension_features(df)

    # Stage 3: Benchmark-relative strength (RS)
    if benchmarks is not None and not benchmarks.is_empty():
        bench = (
            benchmarks.filter(pl.col("benchmark_code") == primary_benchmark)
            .sort("trade_date")
            .with_columns(
                (pl.col("close") / pl.col("close").shift(20) - 1.0)
                .mul(100.0)
                .alias("bench_ret_20d")
            )
            .select(
                pl.col("trade_date"),
                pl.col("close").alias("bench_close"),
                pl.col("bench_ret_20d"),
            )
        )
        if not bench.is_empty():
            df = (
                df.join(bench, on="trade_date", how="left")
                .with_columns(
                    (pl.col("close") / pl.col("bench_close")).alias("rs_ratio"),
                    (pl.col("ret_20d") - pl.col("bench_ret_20d")).alias("rs_excess_20d"),
                )
                .with_columns(
                    pl.col("rs_ratio").rolling_mean(20).over("symbol").alias("rs_ma20"),
                )
            )
            return _compact_feature_types(df)

    # Ensure benchmark columns exist even if benchmarks haven't been synced yet
    df = df.with_columns(
        pl.lit(None).cast(pl.Float32).alias("bench_close"),
        pl.lit(None).cast(pl.Float32).alias("bench_ret_20d"),
        pl.lit(None).cast(pl.Float32).alias("rs_ratio"),
        pl.lit(None).cast(pl.Float32).alias("rs_excess_20d"),
        pl.lit(None).cast(pl.Float32).alias("rs_ma20"),
    )
    return _compact_feature_types(df)


_COMPACT_FLOAT32_COLS = (
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "ma120",
    "boll_std20",
    "ema12",
    "ema26",
    "vol_ma5",
    "vol_ma20",
    "avg_amount_20d",
    "ret_5d",
    "ret_10d",
    "ret_20d",
    "ret_60d",
    "volatility_20d",
    "prev_high_20d",
    "rsi14",
    "macd_dif",
    "macd_dea",
    "macd_hist",
    "boll_mid",
    "boll_upper",
    "boll_lower",
    "bias20",
    "bias60",
    "boll_pct_b",
    "bias20_z",
    "drawup_60d_low",
    "pos_60d",
    "vol_ratio",
    "bench_close",
    "bench_ret_20d",
    "rs_ratio",
    "rs_excess_20d",
    "rs_ma20",
)


def _compact_feature_types(df: pl.DataFrame) -> pl.DataFrame:
    """Downcast derived indicator columns to Float32 (preserving ~7 significant digits) for smaller Parquet & faster scans."""
    exprs = [
        pl.col(c).cast(pl.Float32).alias(c)
        for c in _COMPACT_FLOAT32_COLS
        if c in df.columns
    ]
    return df.with_columns(exprs) if exprs else df

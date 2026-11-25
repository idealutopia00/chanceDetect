"""Vectorized Polars technical indicator computation for precomputed features and custom params."""

from __future__ import annotations

import polars as pl


def rsi_expr(close_col: str = "close", period: int = 14) -> pl.Expr:
    """Compute Wilder-smoothed RSI over each symbol partition."""
    diff = pl.col(close_col).diff().over("symbol")
    gain = pl.when(diff > 0).then(diff).otherwise(0.0)
    loss = pl.when(diff < 0).then(-diff).otherwise(0.0)
    avg_gain = gain.ewm_mean(alpha=1.0 / period, adjust=False, min_samples=period).over("symbol")
    avg_loss = loss.ewm_mean(alpha=1.0 / period, adjust=False, min_samples=period).over("symbol")
    rs = avg_gain / pl.when(avg_loss == 0.0).then(1e-12).otherwise(avg_loss)
    return (100.0 - (100.0 / (1.0 + rs))).round(4)


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

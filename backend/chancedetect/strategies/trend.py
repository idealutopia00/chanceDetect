"""Trend & breakout strategies (vectorized Polars)."""

from __future__ import annotations

import polars as pl
from pydantic import BaseModel, Field

from chancedetect.strategies.base import BaseStrategy


class MaBullishParams(BaseModel):
    fast_period: int = Field(default=5, ge=2, le=60, description="短期均线周期")
    mid_period: int = Field(default=10, ge=3, le=120, description="中期均线周期")
    slow_period: int = Field(default=20, ge=5, le=250, description="长期均线周期")
    min_vol_ratio: float = Field(default=1.0, ge=0.0, le=10.0, description="最小量比(相对20日均量)")


class MaBullishAlignmentStrategy(BaseStrategy):
    id = "ma_bullish_alignment"
    name = "均线多头启动"
    category = "trend"
    description = "短/中/长期均线呈多头排列，收盘价站上短期均线且短均线向上拐头，辅以成交量确认。"
    params_model = MaBullishParams

    def evaluate(
        self,
        panel: pl.DataFrame,
        benchmarks: pl.DataFrame,
        params: BaseModel,
    ) -> pl.DataFrame:
        p = params if isinstance(params, MaBullishParams) else MaBullishParams.model_validate(params)

        fast_col = (
            pl.col(f"ma{p.fast_period}")
            if f"ma{p.fast_period}" in panel.columns
            else pl.col("close").rolling_mean(p.fast_period).over("symbol")
        )
        mid_col = (
            pl.col(f"ma{p.mid_period}")
            if f"ma{p.mid_period}" in panel.columns
            else pl.col("close").rolling_mean(p.mid_period).over("symbol")
        )
        slow_col = (
            pl.col(f"ma{p.slow_period}")
            if f"ma{p.slow_period}" in panel.columns
            else pl.col("close").rolling_mean(p.slow_period).over("symbol")
        )

        df = panel.with_columns(
            fast_col.alias("_ma_f"),
            mid_col.alias("_ma_m"),
            slow_col.alias("_ma_s"),
        ).with_columns(
            pl.col("_ma_f").shift(1).over("symbol").alias("_ma_f_prev"),
            pl.col("_ma_m").shift(1).over("symbol").alias("_ma_m_prev"),
        )

        vol_ok = (
            (pl.col("vol_ratio").fill_null(0.0) >= p.min_vol_ratio)
            if p.min_vol_ratio > 0
            else pl.lit(True)
        )
        signal_expr = (
            (pl.col("_ma_f") > pl.col("_ma_m"))
            & (pl.col("_ma_m") > pl.col("_ma_s"))
            & (pl.col("close") >= pl.col("_ma_f"))
            & (pl.col("_ma_f") > pl.col("_ma_f_prev"))
            & (pl.col("_ma_m") >= pl.col("_ma_m_prev"))
            & vol_ok
        ).fill_null(False)

        spread = ((pl.col("_ma_f") - pl.col("_ma_s")) / pl.col("_ma_s") * 100.0).fill_null(0.0)
        score_expr = (
            pl.when(signal_expr)
            .then((spread.clip(0.0, 20.0) * 3.0 + pl.col("vol_ratio").fill_null(1.0).clip(0.0, 4.0) * 10.0).round(2))
            .otherwise(0.0)
        )

        reason_expr = pl.when(signal_expr).then(
            pl.format(
                "MA{}>{}>{} 多头向上，量比 {}",
                pl.lit(p.fast_period),
                pl.lit(p.mid_period),
                pl.lit(p.slow_period),
                pl.col("vol_ratio").fill_null(0.0).round(2),
            )
        ).otherwise(pl.lit(""))

        return df.select(
            pl.col("symbol"),
            pl.col("trade_date"),
            signal_expr.alias("signal"),
            score_expr.alias("score"),
            reason_expr.alias("reason"),
        )


class VolumeHighBreakoutParams(BaseModel):
    lookback_days: int = Field(default=20, ge=5, le=250, description="突破新高回看天数")
    vol_multiplier: float = Field(default=1.5, ge=1.0, le=8.0, description="放量倍数(相对20日均量)")
    min_pct_chg: float = Field(default=0.5, ge=-5.0, le=10.0, description="当日最小涨幅(%)")


class VolumeHighBreakoutStrategy(BaseStrategy):
    id = "volume_high_breakout"
    name = "放量突破N日新高"
    category = "trend"
    description = "收盘价有效突破过去 N 个交易日最高收盘价，同时成交量明显放大，捕捉趋势加速起点。"
    params_model = VolumeHighBreakoutParams

    def evaluate(
        self,
        panel: pl.DataFrame,
        benchmarks: pl.DataFrame,
        params: BaseModel,
    ) -> pl.DataFrame:
        p = (
            params
            if isinstance(params, VolumeHighBreakoutParams)
            else VolumeHighBreakoutParams.model_validate(params)
        )

        prev_high_expr = (
            pl.col("prev_high_20d")
            if p.lookback_days == 20 and "prev_high_20d" in panel.columns
            else pl.col("close").shift(1).rolling_max(p.lookback_days).over("symbol")
        )

        df = panel.with_columns(prev_high_expr.alias("_prev_high"))

        breakout_pct = ((pl.col("close") / pl.col("_prev_high") - 1.0) * 100.0).fill_null(0.0)
        signal_expr = (
            (pl.col("close") > pl.col("_prev_high"))
            & (pl.col("vol_ratio").fill_null(0.0) >= p.vol_multiplier)
            & (pl.col("pct_chg").fill_null(0.0) >= p.min_pct_chg)
        ).fill_null(False)

        score_expr = (
            pl.when(signal_expr)
            .then(
                (
                    50.0
                    + breakout_pct.clip(0.0, 10.0) * 3.0
                    + pl.col("vol_ratio").fill_null(1.0).clip(0.0, 5.0) * 6.0
                ).round(2)
            )
            .otherwise(0.0)
        )

        reason_expr = pl.when(signal_expr).then(
            pl.format(
                "放量{}x 突破{}日新高(+{}%)",
                pl.col("vol_ratio").fill_null(0.0).round(2),
                pl.lit(p.lookback_days),
                breakout_pct.round(2),
            )
        ).otherwise(pl.lit(""))

        return df.select(
            pl.col("symbol"),
            pl.col("trade_date"),
            signal_expr.alias("signal"),
            score_expr.alias("score"),
            reason_expr.alias("reason"),
        )


class MacdGoldenCrossParams(BaseModel):
    fast_span: int = Field(default=12, ge=4, le=50, description="MACD 快线 EMA 周期")
    slow_span: int = Field(default=26, ge=10, le=100, description="MACD 慢线 EMA 周期")
    signal_span: int = Field(default=9, ge=3, le=30, description="MACD 信号线 DEA 周期")
    above_zero_only: bool = Field(default=False, description="仅保留零轴上方强势金叉")


class MacdGoldenCrossStrategy(BaseStrategy):
    id = "macd_golden_cross"
    name = "MACD 金叉"
    category = "trend"
    description = "DIF 上穿 DEA 形成 MACD 金叉，柱体由负转正；可选限定零轴上方强势区间。"
    params_model = MacdGoldenCrossParams

    def evaluate(
        self,
        panel: pl.DataFrame,
        benchmarks: pl.DataFrame,
        params: BaseModel,
    ) -> pl.DataFrame:
        p = (
            params
            if isinstance(params, MacdGoldenCrossParams)
            else MacdGoldenCrossParams.model_validate(params)
        )

        use_cached = (
            p.fast_span == 12
            and p.slow_span == 26
            and p.signal_span == 9
            and "macd_dif" in panel.columns
            and "macd_dea" in panel.columns
        )
        if use_cached:
            df = panel.with_columns(
                pl.col("macd_dif").alias("_dif"),
                pl.col("macd_dea").alias("_dea"),
                pl.col("macd_hist").alias("_hist"),
            )
        else:
            df = (
                panel.with_columns(
                    (
                        pl.col("close").ewm_mean(span=p.fast_span, adjust=False).over("symbol")
                        - pl.col("close").ewm_mean(span=p.slow_span, adjust=False).over("symbol")
                    ).alias("_dif")
                )
                .with_columns(
                    pl.col("_dif").ewm_mean(span=p.signal_span, adjust=False).over("symbol").alias("_dea")
                )
                .with_columns(((pl.col("_dif") - pl.col("_dea")) * 2.0).alias("_hist"))
            )

        df = df.with_columns(
            pl.col("_dif").shift(1).over("symbol").alias("_dif_prev"),
            pl.col("_dea").shift(1).over("symbol").alias("_dea_prev"),
        )

        zero_ok = (pl.col("_dif") > 0) if p.above_zero_only else pl.lit(True)
        signal_expr = (
            (pl.col("_dif") > pl.col("_dea"))
            & (pl.col("_dif_prev") <= pl.col("_dea_prev"))
            & zero_ok
        ).fill_null(False)

        above_bonus = pl.when(pl.col("_dif") > 0).then(15.0).otherwise(0.0)
        score_expr = (
            pl.when(signal_expr)
            .then(
                (
                    55.0
                    + above_bonus
                    + pl.col("vol_ratio").fill_null(1.0).clip(0.0, 4.0) * 6.0
                ).round(2)
            )
            .otherwise(0.0)
        )

        reason_expr = pl.when(signal_expr).then(
            pl.when(pl.col("_dif") > 0)
            .then(pl.format("MACD 零轴上金叉 (DIF={})", pl.col("_dif").round(4)))
            .otherwise(pl.format("MACD 零轴下金叉 (DIF={})", pl.col("_dif").round(4)))
        ).otherwise(pl.lit(""))

        return df.select(
            pl.col("symbol"),
            pl.col("trade_date"),
            signal_expr.alias("signal"),
            score_expr.alias("score"),
            reason_expr.alias("reason"),
        )

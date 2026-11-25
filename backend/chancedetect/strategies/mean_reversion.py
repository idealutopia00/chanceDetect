"""Mean-reversion & oversold rebound strategies (vectorized Polars)."""

from __future__ import annotations

import polars as pl
from pydantic import BaseModel, Field

from chancedetect.strategies.base import BaseStrategy
from chancedetect.strategies.indicators import rsi_expr


class RsiOversoldParams(BaseModel):
    rsi_period: int = Field(default=14, ge=4, le=30, description="RSI 周期")
    oversold_threshold: float = Field(default=30.0, ge=10.0, le=45.0, description="超卖阈值")
    require_rebound: bool = Field(default=True, description="要求当日 RSI 拐头向上确认企稳")


class RsiOversoldReboundStrategy(BaseStrategy):
    id = "rsi_oversold_rebound"
    name = "RSI 低位超卖企稳"
    category = "mean_reversion"
    description = "近期 RSI 跌入超卖区间后出现拐头企稳迹象，捕捉短线超跌反弹机会。"
    params_model = RsiOversoldParams

    def evaluate(
        self,
        panel: pl.DataFrame,
        benchmarks: pl.DataFrame,
        params: BaseModel,
    ) -> pl.DataFrame:
        p = params if isinstance(params, RsiOversoldParams) else RsiOversoldParams.model_validate(params)

        r_col = (
            pl.col("rsi14")
            if p.rsi_period == 14 and "rsi14" in panel.columns
            else rsi_expr("close", p.rsi_period)
        )

        df = panel.with_columns(r_col.alias("_rsi")).with_columns(
            pl.col("_rsi").shift(1).over("symbol").alias("_rsi_prev"),
            pl.col("_rsi").rolling_min(3).over("symbol").alias("_rsi_min3"),
        )

        if p.require_rebound:
            signal_expr = (
                (pl.col("_rsi_min3") <= p.oversold_threshold)
                & (pl.col("_rsi") > pl.col("_rsi_prev"))
                & (pl.col("close") >= pl.col("open"))
            ).fill_null(False)
        else:
            signal_expr = (pl.col("_rsi") <= p.oversold_threshold).fill_null(False)

        depth = (p.oversold_threshold - pl.col("_rsi_min3")).clip(0.0, 25.0).fill_null(0.0)
        score_expr = (
            pl.when(signal_expr)
            .then((55.0 + depth * 1.8).round(2))
            .otherwise(0.0)
        )

        reason_expr = pl.when(signal_expr).then(
            pl.format(
                "RSI{} 触及低位 {} 后回升至 {}",
                pl.lit(p.rsi_period),
                pl.col("_rsi_min3").round(1),
                pl.col("_rsi").round(1),
            )
        ).otherwise(pl.lit(""))

        return df.select(
            pl.col("symbol"),
            pl.col("trade_date"),
            signal_expr.alias("signal"),
            score_expr.alias("score"),
            reason_expr.alias("reason"),
        )


class BollLowerReversalParams(BaseModel):
    period: int = Field(default=20, ge=10, le=60, description="布林带均线周期")
    std_dev: float = Field(default=2.0, ge=1.2, le=3.5, description="标准差倍数")
    touch_lookback: int = Field(default=3, ge=1, le=7, description="前几日触及下轨回看窗口")


class BollLowerReversalStrategy(BaseStrategy):
    id = "boll_lower_reversal"
    name = "布林带下轨反抽"
    category = "mean_reversion"
    description = "近几日股价下探触及或跌破布林带下轨，当日收阳重新站回下轨之上，体现支撑反抽。"
    params_model = BollLowerReversalParams

    def evaluate(
        self,
        panel: pl.DataFrame,
        benchmarks: pl.DataFrame,
        params: BaseModel,
    ) -> pl.DataFrame:
        p = (
            params
            if isinstance(params, BollLowerReversalParams)
            else BollLowerReversalParams.model_validate(params)
        )

        use_cached = p.period == 20 and abs(p.std_dev - 2.0) < 1e-6 and "boll_lower" in panel.columns
        if use_cached:
            df = panel.with_columns(
                pl.col("boll_mid").alias("_mid"),
                pl.col("boll_lower").alias("_lower"),
            )
        else:
            df = panel.with_columns(
                pl.col("close").rolling_mean(p.period).over("symbol").alias("_mid"),
                pl.col("close").rolling_std(p.period).over("symbol").alias("_std"),
            ).with_columns((pl.col("_mid") - p.std_dev * pl.col("_std")).alias("_lower"))

        touched = (pl.col("low") <= pl.col("_lower")) | (pl.col("close") <= pl.col("_lower"))
        df = df.with_columns(
            touched.cast(pl.Int8).shift(1).rolling_max(p.touch_lookback).over("symbol").alias("_touched_prev")
        )

        signal_expr = (
            (pl.col("_touched_prev") == 1)
            & (pl.col("close") > pl.col("_lower"))
            & (pl.col("close") > pl.col("open"))
            & (pl.col("close") < pl.col("_mid"))
        ).fill_null(False)

        upside_to_mid = ((pl.col("_mid") / pl.col("close") - 1.0) * 100.0).clip(0.0, 20.0).fill_null(0.0)
        score_expr = (
            pl.when(signal_expr)
            .then((50.0 + upside_to_mid * 2.2 + pl.col("pct_chg").clip(0.0, 8.0) * 2.0).round(2))
            .otherwise(0.0)
        )

        reason_expr = pl.when(signal_expr).then(
            pl.format(
                "触及布林下轨后收阳反抽，距中轨空间 +{}%",
                upside_to_mid.round(2),
            )
        ).otherwise(pl.lit(""))

        return df.select(
            pl.col("symbol"),
            pl.col("trade_date"),
            signal_expr.alias("signal"),
            score_expr.alias("score"),
            reason_expr.alias("reason"),
        )


class MaNegativeBiasParams(BaseModel):
    ma_period: int = Field(default=20, ge=5, le=120, description="均线周期")
    min_negative_bias_pct: float = Field(
        default=5.0, ge=1.0, le=25.0, description="最小负乖离幅度(%)"
    )
    min_day_pct_chg: float = Field(
        default=-1.0, ge=-10.0, le=5.0, description="当日最小涨跌幅(%，用于过滤暴跌接飞刀)"
    )


class MaNegativeBiasStrategy(BaseStrategy):
    id = "ma_negative_bias"
    name = "均线负乖离极值"
    category = "mean_reversion"
    description = "收盘价大幅低于均线形成深度负乖离，且当日跌势放缓或企稳，适合左侧逢低分批关注。"
    params_model = MaNegativeBiasParams

    def evaluate(
        self,
        panel: pl.DataFrame,
        benchmarks: pl.DataFrame,
        params: BaseModel,
    ) -> pl.DataFrame:
        p = (
            params
            if isinstance(params, MaNegativeBiasParams)
            else MaNegativeBiasParams.model_validate(params)
        )

        bias_expr = (
            pl.col("bias20")
            if p.ma_period == 20 and "bias20" in panel.columns
            else (
                (pl.col("close") - pl.col("close").rolling_mean(p.ma_period).over("symbol"))
                / pl.col("close").rolling_mean(p.ma_period).over("symbol")
                * 100.0
            )
        )

        df = panel.with_columns(bias_expr.alias("_bias"))
        signal_expr = (
            (pl.col("_bias") <= -p.min_negative_bias_pct)
            & (pl.col("pct_chg").fill_null(-99.0) >= p.min_day_pct_chg)
        ).fill_null(False)

        score_expr = (
            pl.when(signal_expr)
            .then((50.0 + (-pl.col("_bias")).clip(0.0, 20.0) * 2.5).round(2))
            .otherwise(0.0)
        )

        reason_expr = pl.when(signal_expr).then(
            pl.format(
                "相对 MA{} 负乖离 {}% (当日 {}%)",
                pl.lit(p.ma_period),
                pl.col("_bias").round(2),
                pl.col("pct_chg").fill_null(0.0).round(2),
            )
        ).otherwise(pl.lit(""))

        return df.select(
            pl.col("symbol"),
            pl.col("trade_date"),
            signal_expr.alias("signal"),
            score_expr.alias("score"),
            reason_expr.alias("reason"),
        )

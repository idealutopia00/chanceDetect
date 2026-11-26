"""Trend & breakout strategies (vectorized Polars)."""

from __future__ import annotations

import polars as pl
from pydantic import BaseModel, Field

from chancedetect.strategies.base import BaseStrategy


class MidTermUptrendParams(BaseModel):
    short_period: int = Field(default=20, ge=5, le=120, description="中期均线周期")
    long_period: int = Field(default=60, ge=20, le=250, description="长期均线周期")
    slope_lookback: int = Field(default=5, ge=1, le=60, description="长期均线斜率回看天数")


class MidTermUptrendStrategy(BaseStrategy):
    id = "mid_term_uptrend"
    name = "中期上升趋势"
    category = "trend"
    role = "regime"
    description = "收盘价站稳长期均线、中均线位于长均线上方，且长期均线保持非下行斜率，界定健康中期上升趋势。"
    params_model = MidTermUptrendParams

    def evaluate(
        self,
        panel: pl.DataFrame,
        benchmarks: pl.DataFrame,
        params: BaseModel,
    ) -> pl.DataFrame:
        p = (
            params
            if isinstance(params, MidTermUptrendParams)
            else MidTermUptrendParams.model_validate(params)
        )

        short_col = (
            pl.col(f"ma{p.short_period}")
            if f"ma{p.short_period}" in panel.columns
            else pl.col("close").rolling_mean(p.short_period).over("symbol")
        )
        long_col = (
            pl.col(f"ma{p.long_period}")
            if f"ma{p.long_period}" in panel.columns
            else pl.col("close").rolling_mean(p.long_period).over("symbol")
        )

        df = panel.with_columns(
            short_col.alias("_ma_short"),
            long_col.alias("_ma_long"),
        ).with_columns(
            pl.col("_ma_long").shift(p.slope_lookback).over("symbol").alias("_ma_long_prev"),
        )

        signal_expr = (
            (pl.col("close") > pl.col("_ma_long"))
            & (pl.col("_ma_short") > pl.col("_ma_long"))
            & (pl.col("_ma_long") >= pl.col("_ma_long_prev"))
        ).fill_null(False)

        spread_pct = (
            (pl.col("_ma_short") - pl.col("_ma_long")) / pl.col("_ma_long") * 100.0
        ).fill_null(0.0)
        slope_pct = (
            (pl.col("_ma_long") - pl.col("_ma_long_prev")) / pl.col("_ma_long_prev") * 100.0
        ).fill_null(0.0)

        score_expr = (
            pl.when(signal_expr)
            .then(
                (
                    55.0
                    + spread_pct.clip(0.0, 20.0) * 1.5
                    + slope_pct.clip(0.0, 10.0) * 1.5
                ).round(2)
            )
            .otherwise(0.0)
        )

        reason_expr = pl.when(signal_expr).then(
            pl.format(
                "MA{}>MA{} 且收盘站上MA{} (MA{} {}日斜率 +{}%)",
                pl.lit(p.short_period),
                pl.lit(p.long_period),
                pl.lit(p.long_period),
                pl.lit(p.long_period),
                pl.lit(p.slope_lookback),
                slope_pct.round(2),
            )
        ).otherwise(pl.lit(""))

        return df.select(
            pl.col("symbol"),
            pl.col("trade_date"),
            signal_expr.alias("signal"),
            score_expr.alias("score"),
            reason_expr.alias("reason"),
        )


class TrendPullbackEntryParams(BaseModel):
    pullback_lookback: int = Field(default=3, ge=1, le=10, description="近期回踩均线回看天数")
    band_pct: float = Field(default=1.5, ge=0.1, le=8.0, description="回踩MA10/MA20支撑容忍带宽(%)")
    max_bias20_pct: float = Field(default=5.0, ge=0.5, le=20.0, description="当日相对MA20最大正乖离(%)")
    max_vol_ratio: float = Field(default=2.0, ge=0.5, le=10.0, description="当日最大量比上限(防放量派发)")


class TrendPullbackEntryStrategy(BaseStrategy):
    id = "trend_pullback_entry"
    name = "强势回踩买点"
    category = "trend"
    role = "entry"
    description = "中期上升趋势中价格回踩至MA10/MA20均线支撑附近并出现当日企稳阳线或翻红，成交量温和可控。"
    params_model = TrendPullbackEntryParams

    def evaluate(
        self,
        panel: pl.DataFrame,
        benchmarks: pl.DataFrame,
        params: BaseModel,
    ) -> pl.DataFrame:
        p = (
            params
            if isinstance(params, TrendPullbackEntryParams)
            else TrendPullbackEntryParams.model_validate(params)
        )

        ma5_col = (
            pl.col("ma5")
            if "ma5" in panel.columns
            else pl.col("close").rolling_mean(5).over("symbol")
        )
        ma10_col = (
            pl.col("ma10")
            if "ma10" in panel.columns
            else pl.col("close").rolling_mean(10).over("symbol")
        )
        ma20_col = (
            pl.col("ma20")
            if "ma20" in panel.columns
            else pl.col("close").rolling_mean(20).over("symbol")
        )
        ma60_col = (
            pl.col("ma60")
            if "ma60" in panel.columns
            else pl.col("close").rolling_mean(60).over("symbol")
        )
        bias20_col = (
            pl.col("bias20")
            if "bias20" in panel.columns
            else ((pl.col("close") - ma20_col) / ma20_col * 100.0)
        )

        band = p.band_pct / 100.0

        df = panel.with_columns(
            ma5_col.alias("_ma5"),
            ma10_col.alias("_ma10"),
            ma20_col.alias("_ma20"),
            ma60_col.alias("_ma60"),
            bias20_col.alias("_bias20"),
        ).with_columns(
            pl.max_horizontal("_ma10", "_ma20").mul(1.0 + band).alias("_sup_upper"),
            pl.min_horizontal("_ma10", "_ma20").mul(1.0 - band).alias("_sup_lower"),
        ).with_columns(
            (
                (pl.col("low") <= pl.col("_sup_upper"))
                & (pl.col("close") >= pl.col("_sup_lower"))
            )
            .fill_null(False)
            .cast(pl.Int32)
            .alias("_touched_bar"),
            (
                (pl.col("pct_chg").fill_null(0.0) < 0.0)
                | (pl.col("close") < pl.col("open"))
                | (pl.col("close") < pl.col("_ma5"))
            )
            .fill_null(False)
            .cast(pl.Int32)
            .alias("_retreat_bar"),
        ).with_columns(
            (
                pl.col("_touched_bar")
                .rolling_max(p.pullback_lookback, min_samples=1)
                .over("symbol")
                == 1
            ).alias("_recent_touch"),
            (
                pl.col("_retreat_bar")
                .rolling_max(p.pullback_lookback, min_samples=1)
                .over("symbol")
                == 1
            ).alias("_recent_retreat"),
        )

        in_uptrend = (pl.col("_ma20") > pl.col("_ma60")) & (pl.col("close") > pl.col("_ma60"))
        stabilized = (
            ((pl.col("close") >= pl.col("open")) | (pl.col("pct_chg").fill_null(0.0) > 0.0))
            & (pl.col("close") >= pl.col("_ma20") * (1.0 - band))
        )
        not_stretched = pl.col("_bias20").fill_null(999.0) <= p.max_bias20_pct
        vol_controlled = pl.col("vol_ratio").fill_null(1.0) <= p.max_vol_ratio

        signal_expr = (
            in_uptrend
            & pl.col("_recent_touch")
            & pl.col("_recent_retreat")
            & stabilized
            & not_stretched
            & vol_controlled
        ).fill_null(False)

        dist_ma20 = pl.col("_bias20").fill_null(0.0)
        proximity_bonus = (10.0 - dist_ma20.abs().clip(0.0, 10.0)) * 2.5
        vol_bonus = pl.when(pl.col("vol_ratio").fill_null(1.0) <= 1.2).then(10.0).otherwise(4.0)
        score_expr = (
            pl.when(signal_expr)
            .then((55.0 + proximity_bonus + vol_bonus).round(2))
            .otherwise(0.0)
        )

        reason_expr = pl.when(signal_expr).then(
            pl.format(
                "中期趋势回踩均线企稳 (MA20乖离 {}%，量比 {})",
                dist_ma20.round(2),
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


class MaBullishParams(BaseModel):
    fast_period: int = Field(default=5, ge=2, le=60, description="短期均线周期")
    mid_period: int = Field(default=10, ge=3, le=120, description="中期均线周期")
    slow_period: int = Field(default=20, ge=5, le=250, description="长期均线周期")
    min_vol_ratio: float = Field(default=1.0, ge=0.0, le=10.0, description="最小量比(相对20日均量)")
    cooldown_days: int = Field(default=1, ge=1, le=30, description="启动前至少未多头排列天数")


class MaBullishAlignmentStrategy(BaseStrategy):
    id = "ma_bullish_alignment"
    name = "均线多头启动"
    category = "trend"
    role = "entry"
    description = "短/中/长期均线首日形成多头排列（前日未多头排列），收盘站上短均线且均线向上，辅以成交量确认。"
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

        df = (
            panel.with_columns(
                fast_col.alias("_ma_f"),
                mid_col.alias("_ma_m"),
                slow_col.alias("_ma_s"),
            )
            .with_columns(
                pl.col("_ma_f").shift(1).over("symbol").alias("_ma_f_prev"),
                pl.col("_ma_m").shift(1).over("symbol").alias("_ma_m_prev"),
                (
                    (pl.col("_ma_f") > pl.col("_ma_m"))
                    & (pl.col("_ma_m") > pl.col("_ma_s"))
                )
                .fill_null(False)
                .cast(pl.Int32)
                .alias("_ma_aligned"),
            )
            .with_columns(
                pl.col("_ma_aligned")
                .shift(1)
                .rolling_sum(p.cooldown_days, min_samples=1)
                .over("symbol")
                .fill_null(0)
                .alias("_prior_aligned_days"),
            )
        )

        vol_ok = (
            (pl.col("vol_ratio").fill_null(0.0) >= p.min_vol_ratio)
            if p.min_vol_ratio > 0
            else pl.lit(True)
        )
        signal_expr = (
            (pl.col("_ma_aligned") == 1)
            & (pl.col("_prior_aligned_days") == 0)
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
                "MA{}>{}>{} 首日多头启动，量比 {}",
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
    max_bias20_pct: float = Field(
        default=8.0,
        ge=0.0,
        le=30.0,
        description="防追高MA20最大正乖离上限(%，0为不限制)",
    )


class VolumeHighBreakoutStrategy(BaseStrategy):
    id = "volume_high_breakout"
    name = "放量突破N日新高"
    category = "trend"
    role = "entry"
    description = "收盘价有效突破过去 N 个交易日最高收盘价，同时成交量明显放大且未过度乖离，捕捉趋势加速起点。"
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
        bias20_expr = (
            pl.col("bias20")
            if "bias20" in panel.columns
            else (
                (pl.col("close") - pl.col("close").rolling_mean(20).over("symbol"))
                / pl.col("close").rolling_mean(20).over("symbol")
                * 100.0
            )
        )

        df = panel.with_columns(
            prev_high_expr.alias("_prev_high"),
            bias20_expr.alias("_bias20"),
        )

        bias_ok = (
            (pl.col("_bias20").fill_null(0.0) <= p.max_bias20_pct)
            if p.max_bias20_pct > 0
            else pl.lit(True)
        )
        breakout_pct = ((pl.col("close") / pl.col("_prev_high") - 1.0) * 100.0).fill_null(0.0)
        signal_expr = (
            (pl.col("close") > pl.col("_prev_high"))
            & (pl.col("vol_ratio").fill_null(0.0) >= p.vol_multiplier)
            & (pl.col("pct_chg").fill_null(0.0) >= p.min_pct_chg)
            & bias_ok
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
    role = "entry"
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

"""Cross-sectional momentum rotation & benchmark relative-strength strategies."""

from __future__ import annotations

import polars as pl
from pydantic import BaseModel, Field

from chancedetect.strategies.base import BaseStrategy


class MomentumTopRankParams(BaseModel):
    lookback_days: int = Field(default=20, ge=5, le=120, description="动量统计周期(交易日)")
    top_k: int = Field(default=20, ge=1, le=100, description="横截面动量排名前 K 名")
    risk_adjusted: bool = Field(default=True, description="使用波动率调整动量(收益/波动率)")
    require_above_ma60: bool = Field(default=True, description="要求收盘站上 60 日均线过滤弱势反弹")


class MomentumTopRankStrategy(BaseStrategy):
    id = "momentum_top_rank"
    name = "横截面动量轮动 Top K"
    category = "momentum"
    description = "每日在全市场基金池内按近 N 日（风险调整）动量得分横向排名，选取最强前 K 只强势品种。"
    params_model = MomentumTopRankParams

    def evaluate(
        self,
        panel: pl.DataFrame,
        benchmarks: pl.DataFrame,
        params: BaseModel,
    ) -> pl.DataFrame:
        p = (
            params
            if isinstance(params, MomentumTopRankParams)
            else MomentumTopRankParams.model_validate(params)
        )

        ret_col = (
            pl.col(f"ret_{p.lookback_days}d")
            if f"ret_{p.lookback_days}d" in panel.columns
            else ((pl.col("close") / pl.col("close").shift(p.lookback_days).over("symbol") - 1.0) * 100.0)
        )
        vol_col = (
            pl.col("volatility_20d")
            if "volatility_20d" in panel.columns
            else pl.col("pct_chg").rolling_std(20).over("symbol")
        )

        df = panel.with_columns(
            ret_col.alias("_mom_ret"),
            vol_col.alias("_vol"),
        )

        if p.risk_adjusted:
            metric_expr = pl.col("_mom_ret") / pl.col("_vol").clip(lower_bound=0.5)
        else:
            metric_expr = pl.col("_mom_ret")

        above_ma60 = (
            (pl.col("close") > pl.col("ma60"))
            if (p.require_above_ma60 and "ma60" in panel.columns)
            else pl.lit(True)
        )

        # Rank ONLY eligible rows (positive momentum & above ma60 if required) per trade_date
        eligible_metric = (
            pl.when((pl.col("_mom_ret") > 0) & above_ma60)
            .then(metric_expr)
            .otherwise(None)
        )

        df = df.with_columns(
            eligible_metric.alias("_mom_metric")
        ).with_columns(
            pl.col("_mom_metric")
            .rank(method="min", descending=True)
            .over("trade_date")
            .alias("_mom_rank")
        )

        signal_expr = (pl.col("_mom_rank").is_not_null() & (pl.col("_mom_rank") <= p.top_k)).fill_null(
            False
        )

        score_expr = (
            pl.when(signal_expr)
            .then((100.0 - (pl.col("_mom_rank") - 1.0) * (40.0 / max(1, p.top_k))).round(2))
            .otherwise(0.0)
        )

        reason_expr = pl.when(signal_expr).then(
            pl.format(
                "{}日动量排名第{} (涨幅 +{}%)",
                pl.lit(p.lookback_days),
                pl.col("_mom_rank").cast(pl.Int64),
                pl.col("_mom_ret").round(2),
            )
        ).otherwise(pl.lit(""))

        return df.select(
            pl.col("symbol"),
            pl.col("trade_date"),
            signal_expr.alias("signal"),
            score_expr.alias("score"),
            reason_expr.alias("reason"),
        )


class RelativeStrengthRsParams(BaseModel):
    benchmark_code: str = Field(default="sh000300", description="对比基准指数代码(如 sh000300)")
    rs_ma_period: int = Field(default=20, ge=5, le=120, description="相对强弱 RS 均线周期")
    excess_lookback: int = Field(default=20, ge=5, le=120, description="超额收益统计周期")
    min_excess_return_pct: float = Field(
        default=3.0, ge=0.0, le=30.0, description="相对基准最小超额收益(%)"
    )


class RelativeStrengthRsStrategy(BaseStrategy):
    id = "relative_strength_rs"
    name = "相对大盘超额强弱 RS"
    category = "momentum"
    description = "相对基准指数的强弱比值曲线(RS)站上均线且创近期新高，同时近 N 日持续跑赢基准。"
    params_model = RelativeStrengthRsParams

    def evaluate(
        self,
        panel: pl.DataFrame,
        benchmarks: pl.DataFrame,
        params: BaseModel,
    ) -> pl.DataFrame:
        p = (
            params
            if isinstance(params, RelativeStrengthRsParams)
            else RelativeStrengthRsParams.model_validate(params)
        )

        use_cached = (
            p.benchmark_code == "sh000300"
            and p.rs_ma_period == 20
            and p.excess_lookback == 20
            and "rs_ratio" in panel.columns
            and panel["rs_ratio"].null_count() < panel.height
        )

        if use_cached:
            df = panel.with_columns(
                pl.col("rs_ratio").alias("_rs"),
                pl.col("rs_ma20").alias("_rs_ma"),
                pl.col("rs_excess_20d").alias("_excess"),
            )
        else:
            bench = (
                benchmarks.filter(pl.col("benchmark_code") == p.benchmark_code)
                .sort("trade_date")
                .with_columns(
                    (
                        (pl.col("close") / pl.col("close").shift(p.excess_lookback) - 1.0)
                        * 100.0
                    ).alias("_b_ret")
                )
                .select(
                    pl.col("trade_date"),
                    pl.col("close").alias("_b_close"),
                    pl.col("_b_ret"),
                )
            )
            if bench.is_empty():
                return panel.select(
                    pl.col("symbol"),
                    pl.col("trade_date"),
                    pl.lit(False).alias("signal"),
                    pl.lit(0.0).alias("score"),
                    pl.lit("").alias("reason"),
                )
            df = (
                panel.join(bench, on="trade_date", how="left")
                .with_columns(
                    (pl.col("close") / pl.col("_b_close")).alias("_rs"),
                    (
                        (pl.col("close") / pl.col("close").shift(p.excess_lookback).over("symbol") - 1.0)
                        * 100.0
                        - pl.col("_b_ret")
                    ).alias("_excess"),
                )
                .with_columns(
                    pl.col("_rs").rolling_mean(p.rs_ma_period).over("symbol").alias("_rs_ma")
                )
            )

        df = df.with_columns(
            pl.col("_rs").shift(1).rolling_max(p.rs_ma_period).over("symbol").alias("_rs_prev_high")
        )

        signal_expr = (
            (pl.col("_rs") > pl.col("_rs_ma"))
            & (pl.col("_rs") >= pl.col("_rs_prev_high"))
            & (pl.col("_excess") >= p.min_excess_return_pct)
        ).fill_null(False)

        score_expr = (
            pl.when(signal_expr)
            .then((55.0 + pl.col("_excess").clip(0.0, 25.0) * 1.8).round(2))
            .otherwise(0.0)
        )

        reason_expr = pl.when(signal_expr).then(
            pl.format(
                "RS 创新高且{}日跑赢 {} +{}%",
                pl.lit(p.excess_lookback),
                pl.lit(p.benchmark_code),
                pl.col("_excess").round(2),
            )
        ).otherwise(pl.lit(""))

        return df.select(
            pl.col("symbol"),
            pl.col("trade_date"),
            signal_expr.alias("signal"),
            score_expr.alias("score"),
            reason_expr.alias("reason"),
        )

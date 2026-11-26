"""Vectorized strategy execution engine with caching, multi-strategy composition, and index grouping."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, Field

from chancedetect.data.models import ExchangeFund, OTCFundMapping
from chancedetect.storage.bar_store import BarStore
from chancedetect.storage.sqlite_store import SQLiteStore
from chancedetect.strategies.indicators import (
    compute_base_features,
    ensure_extension_features,
    trend_stage_expr,
)
from chancedetect.strategies.registry import StrategyRegistry, create_default_registry


class StrategySelection(BaseModel):
    strategy_id: str
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class BaseUniverseFilter(BaseModel):
    min_avg_amount_20d: float = Field(default=1000.0, ge=0.0, description="近20日日均成交额下限(万元)")
    min_listing_days: int = Field(default=60, ge=0, description="最少上市交易日数")
    fund_types: list[Literal["ETF", "LOF"]] = Field(default_factory=lambda: ["ETF", "LOF"])


class ScanRequestConfig(BaseModel):
    strategies: list[StrategySelection] = Field(default_factory=list)
    regime_combine_mode: Literal["AND", "OR"] = "OR"
    combine_mode: Literal["AND", "OR", "RESONANCE"] = "OR"
    min_hits: int = Field(default=2, ge=1)
    hide_overheated: bool = False
    freshness_cooldown_days: int = Field(default=10, ge=1, le=120)
    base_filter: BaseUniverseFilter = Field(default_factory=BaseUniverseFilter)
    share_class_preference: Literal["C", "A"] = "C"
    target_date: date | None = None
    group_by_index: bool = True
    save_snapshot: bool = False
    preset_id: int | None = None


def _file_signature(path: Path) -> tuple[int, int] | None:
    if not path.exists():
        return None
    st = path.stat()
    return (st.st_mtime_ns, st.st_size)


def sort_otc_mappings(
    mappings: list[OTCFundMapping],
    preference: Literal["C", "A"] = "C",
) -> list[OTCFundMapping]:
    """Deduplicate by otc_code and sort: open-to-buy first, preferred share class next, then code."""
    seen: dict[str, OTCFundMapping] = {}
    for m in mappings:
        if m.otc_code not in seen:
            seen[m.otc_code] = m

    preferred_order = (
        {"C": 0, "A": 1, "E": 2, "I": 3, "OTHER": 4}
        if preference == "C"
        else {"A": 0, "C": 1, "E": 2, "I": 3, "OTHER": 4}
    )

    def _key(m: OTCFundMapping) -> tuple[int, int, str]:
        suspended = 1 if ("暂停" in m.purchase_status or "封闭" in m.purchase_status) else 0
        cls_rank = preferred_order.get(m.share_class, 5)
        return (suspended, cls_rank, m.otc_code)

    return sorted(seen.values(), key=_key)


@dataclass(frozen=True)
class _GroupBuildContext:
    enabled_ids: list[str]
    fund_by_sym: dict[str, ExchangeFund]
    mappings_by_sym: dict[str, list[OTCFundMapping]]
    strat_names: dict[str, str]
    strat_roles: dict[str, str]
    config: ScanRequestConfig


def _aggregate_stage_exprs(
    sig_cols: list[str],
    score_cols: list[str],
    *,
    required_hits: int,
    empty_pass: bool,
) -> tuple[pl.Expr, pl.Expr, pl.Expr]:
    """Build (hit_count, pass_flag, stage_score) Polars expressions for a funnel stage."""
    if not sig_cols:
        return pl.lit(0, dtype=pl.Int32), pl.lit(empty_pass), pl.lit(0.0)
    hit_count = pl.sum_horizontal(
        [pl.col(c).fill_null(False).cast(pl.Int32) for c in sig_cols]
    )
    score_sum = pl.sum_horizontal([pl.col(c).fill_null(0.0) for c in score_cols])
    pass_expr = hit_count >= required_hits
    score_expr = (
        pl.when(hit_count > 0)
        .then((score_sum / hit_count + (hit_count - 1) * 12.0).round(2))
        .otherwise(0.0)
    )
    return hit_count, pass_expr, score_expr


class StrategyEngine:
    """High-speed Polars strategy scanner with mtime-invalidated in-memory panel cache."""

    def __init__(
        self,
        sqlite_store: SQLiteStore,
        bar_store: BarStore,
        registry: StrategyRegistry | None = None,
    ) -> None:
        self.sqlite = sqlite_store
        self.bars = bar_store
        self.registry = registry or create_default_registry()
        self._panel_sig: tuple[int, int] | None = None
        self._panel_cache: pl.DataFrame | None = None
        self._bench_sig: tuple[int, int] | None = None
        self._bench_cache: pl.DataFrame | None = None

    def invalidate_cache(self) -> None:
        self._panel_sig = None
        self._panel_cache = None
        self._bench_sig = None
        self._bench_cache = None

    def load_panel(self) -> tuple[pl.DataFrame, pl.DataFrame]:
        """Load precomputed features panel & benchmarks with mtime-based in-memory caching."""
        bench_sig = _file_signature(self.bars.benchmarks_path)
        if self._bench_cache is None or bench_sig != self._bench_sig:
            self._bench_cache = self.bars.load_benchmarks()
            self._bench_sig = bench_sig

        feat_sig = _file_signature(self.bars.features_path)
        if feat_sig is not None:
            if self._panel_cache is None or feat_sig != self._panel_sig:
                self._panel_cache = ensure_extension_features(self.bars.load_features())
                self._panel_sig = feat_sig
        else:
            bars_sig = _file_signature(self.bars.daily_bars_path)
            if self._panel_cache is None or bars_sig != self._panel_sig:
                raw = self.bars.load_daily_bars()
                self._panel_cache = compute_base_features(raw, self._bench_cache)
                self._panel_sig = bars_sig

        return self._panel_cache, self._bench_cache

    def evaluate_panel(
        self,
        config: ScanRequestConfig,
        *,
        window_calendar_days: int | None = None,
    ) -> tuple[pl.DataFrame, dict[str, pl.DataFrame], list[str], date | None]:
        """Run base universe filter + all enabled strategies across the historical panel.

        When `window_calendar_days` is provided (e.g. for latest-day scanning), trims older
        bars beyond the indicator warmup horizon to achieve sub-50ms scan latency.

        Returns:
          (filtered_panel, per_strategy_results_by_id, enabled_strategy_ids, effective_target_date)
        """
        panel, benchmarks = self.load_panel()
        if panel.is_empty():
            return panel, {}, [], None

        active_funds = self.sqlite.list_exchange_funds(active_only=True)
        allowed_types = set(config.base_filter.fund_types)
        allowed_symbols = {f.symbol for f in active_funds if f.fund_type in allowed_types}
        if not allowed_symbols:
            return panel.head(0), {}, [], None

        # Determine effective trade date
        all_dates = panel["trade_date"]
        max_date: date = all_dates.max()  # type: ignore[assignment]
        effective_date = config.target_date or max_date

        # Apply base universe filter based on latest stats up to effective_date
        date_cond = pl.col("trade_date") <= effective_date
        if window_calendar_days is not None:
            min_window_date = effective_date - timedelta(days=window_calendar_days)
            date_cond = date_cond & (pl.col("trade_date") >= min_window_date)

        sub_panel = panel.filter(
            pl.col("symbol").is_in(list(allowed_symbols)) & date_cond
        )
        if sub_panel.is_empty():
            return sub_panel, {}, [], effective_date

        bf = config.base_filter
        if bf.min_avg_amount_20d > 0 or bf.min_listing_days > 0:
            latest_per_sym = (
                sub_panel.group_by("symbol")
                .agg(
                    pl.col("avg_amount_20d").last().alias("_last_amt"),
                    pl.col("listing_days").max().alias("_max_days"),
                )
                .filter(
                    (pl.col("_last_amt").fill_null(0.0) >= bf.min_avg_amount_20d)
                    & (pl.col("_max_days").fill_null(0) >= bf.min_listing_days)
                )
            )
            passing_symbols = latest_per_sym["symbol"].to_list()
            if not passing_symbols:
                return sub_panel.head(0), {}, [], effective_date
            sub_panel = sub_panel.filter(pl.col("symbol").is_in(passing_symbols))

        # Resolve enabled strategies (default to all if none specified)
        if not config.strategies:
            selections = [
                StrategySelection(strategy_id=s.id, enabled=True, params={})
                for s in self.registry.list_all()
            ]
        else:
            selections = [s for s in config.strategies if s.enabled]

        per_strategy: dict[str, pl.DataFrame] = {}
        enabled_ids: list[str] = []
        for sel in selections:
            strat = self.registry.get(sel.strategy_id)
            parsed_params = strat.parse_params(sel.params)
            res = strat.evaluate(sub_panel, benchmarks, parsed_params)
            per_strategy[strat.id] = res
            enabled_ids.append(strat.id)

        return sub_panel, per_strategy, enabled_ids, effective_date

    def combine_signals(
        self,
        panel: pl.DataFrame,
        per_strategy: dict[str, pl.DataFrame],
        enabled_ids: list[str],
        combine_mode: Literal["AND", "OR", "RESONANCE"],
        min_hits: int,
        regime_combine_mode: Literal["AND", "OR"] = "OR",
        freshness_cooldown_days: int = 10,
    ) -> pl.DataFrame:
        """Combine per-strategy outputs through a two-stage funnel (Trend Regime -> Entry Trigger)."""
        if panel.is_empty() or not enabled_ids:
            return panel.head(0)

        # Preserve incoming row order so positional attachment of pre-evaluated strategy columns never misaligns
        panel = ensure_extension_features(panel, preserve_order=True)
        base = panel.select(
            [
                "symbol",
                "trade_date",
                "close",
                "pct_chg",
                "amount",
                "avg_amount_20d",
                "vol_ratio",
                "rsi14",
                "ma20",
                "ma60",
                "bias20",
                "bias60",
                "boll_pct_b",
                "bias20_z",
                "drawup_60d_low",
                "pos_60d",
            ]
        ).with_columns(trend_stage_expr().alias("trend_stage"))
        regime_sig_cols: list[str] = []
        regime_score_cols: list[str] = []
        entry_sig_cols: list[str] = []
        entry_score_cols: list[str] = []
        aligned_series: list[pl.Series] = []
        unaligned: list[tuple[int, pl.DataFrame]] = []

        for idx, sid in enumerate(enabled_ids):
            sdf = per_strategy[sid]
            role = self.registry.get(sid).role
            if role == "regime":
                regime_sig_cols.append(f"_sig_{idx}")
                regime_score_cols.append(f"_scr_{idx}")
            else:
                entry_sig_cols.append(f"_sig_{idx}")
                entry_score_cols.append(f"_scr_{idx}")

            if sdf.height == base.height:
                aligned_series.extend(
                    [
                        sdf["signal"].alias(f"_sig_{idx}"),
                        sdf["score"].alias(f"_scr_{idx}"),
                        sdf["reason"].alias(f"_rsn_{idx}"),
                    ]
                )
            else:
                unaligned.append((idx, sdf))

        if aligned_series:
            base = base.with_columns(aligned_series)

        for idx, sdf in unaligned:
            base = base.join(
                sdf.select(
                    pl.col("symbol"),
                    pl.col("trade_date"),
                    pl.col("signal").alias(f"_sig_{idx}"),
                    pl.col("score").alias(f"_scr_{idx}"),
                    pl.col("reason").alias(f"_rsn_{idx}"),
                ),
                on=["symbol", "trade_date"],
                how="left",
            )

        # Stage 1: Trend Regime filter evaluation
        has_regimes = len(regime_sig_cols) > 0
        regime_required = len(regime_sig_cols) if regime_combine_mode == "AND" else 1
        regime_hit_count_expr, regime_pass_expr, regime_score_expr = _aggregate_stage_exprs(
            regime_sig_cols,
            regime_score_cols,
            required_hits=regime_required,
            empty_pass=True,
        )

        # Stage 2: Entry Trigger evaluation
        has_entries = len(entry_sig_cols) > 0
        if combine_mode == "AND":
            entry_required = len(entry_sig_cols)
        elif combine_mode == "OR":
            entry_required = 1
        else:
            entry_required = max(1, min(min_hits, len(entry_sig_cols)))
        entry_hit_count_expr, entry_pass_expr, entry_score_expr = _aggregate_stage_exprs(
            entry_sig_cols,
            entry_score_cols,
            required_hits=entry_required,
            empty_pass=False,
        )

        cooldown = max(1, freshness_cooldown_days)
        act_int = pl.col("actionable_signal").cast(pl.Int32)
        prior_hits_expr = (
            act_int.shift(1, fill_value=0)
            .rolling_sum(window_size=cooldown, min_samples=1)
            .over("symbol")
        )
        hits_20d_expr = act_int.rolling_sum(window_size=20, min_samples=1).over("symbol")

        stage_adj_expr = (
            pl.when(pl.col("trend_stage") == "early")
            .then(5.0)
            .when(pl.col("trend_stage") == "overheated")
            .then(-15.0)
            .otherwise(0.0)
        )
        fresh_adj_expr = pl.when(pl.col("is_fresh")).then(8.0).otherwise(0.0)

        combined = base.with_columns(
            regime_hit_count_expr.alias("regime_hit_count"),
            regime_score_expr.alias("regime_score"),
            regime_pass_expr.alias("regime_pass"),
            entry_hit_count_expr.alias("entry_hit_count"),
            entry_score_expr.alias("entry_score"),
            entry_pass_expr.alias("entry_pass"),
        ).with_columns(
            (pl.col("regime_pass") & pl.col("entry_pass")).alias("actionable_signal"),
            (
                (pl.col("regime_pass") & (~pl.col("entry_pass")))
                if has_regimes
                else pl.lit(False)
            ).alias("watchlist_signal"),
        ).with_columns(
            (pl.col("actionable_signal") & (prior_hits_expr == 0)).alias("is_fresh"),
            hits_20d_expr.alias("hits_20d"),
        ).with_columns(
            (
                pl.when(pl.col("actionable_signal"))
                .then(
                    pl.max_horizontal(
                        pl.lit(0.0),
                        pl.col("entry_score")
                        + pl.col("regime_hit_count") * 5.0
                        + stage_adj_expr
                        + fresh_adj_expr,
                    ).round(2)
                )
                .otherwise(0.0)
            ).alias("actionable_score"),
        ).with_columns(
            (
                pl.col("actionable_signal") if has_entries else pl.col("regime_pass")
            ).alias("combined_signal"),
            (
                pl.col("entry_hit_count") if has_entries else pl.col("regime_hit_count")
            ).alias("hit_count"),
            (
                pl.col("actionable_score") if has_entries else pl.col("regime_score")
            ).alias("composite_score"),
        )

        return combined

    @staticmethod
    def _build_groups(
        day_rows: pl.DataFrame,
        *,
        bucket: Literal["actionable", "watchlist"],
        ctx: _GroupBuildContext,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Materialize fund items and deduplicated Index Opportunity Groups for a funnel bucket."""
        hit_col = "entry_hit_count" if bucket == "actionable" else "regime_hit_count"
        score_col = "actionable_score" if bucket == "actionable" else "regime_score"

        sorted_rows = day_rows.sort(
            [hit_col, score_col, "avg_amount_20d"],
            descending=[True, True, True],
        )

        items: list[dict[str, Any]] = []
        for row in sorted_rows.iter_rows(named=True):
            sym = row["symbol"]
            fund = ctx.fund_by_sym.get(sym)
            if fund is None:
                continue

            matched_regimes: list[dict[str, Any]] = []
            hit_entries: list[dict[str, Any]] = []
            for idx, sid in enumerate(ctx.enabled_ids):
                if row.get(f"_sig_{idx}"):
                    role = ctx.strat_roles.get(sid, "entry")
                    info = {
                        "strategy_id": sid,
                        "strategy_name": ctx.strat_names.get(sid, sid),
                        "role": role,
                        "score": round(float(row.get(f"_scr_{idx}") or 0.0), 2),
                        "reason": str(row.get(f"_rsn_{idx}") or ""),
                    }
                    if role == "regime":
                        matched_regimes.append(info)
                    else:
                        hit_entries.append(info)

            fund_mappings = sort_otc_mappings(
                ctx.mappings_by_sym.get(sym, []),
                preference=ctx.config.share_class_preference,
            )
            items.append(
                {
                    "symbol": sym,
                    "code": fund.code,
                    "name": fund.name,
                    "fund_type": fund.fund_type,
                    "index_code": fund.index_code,
                    "index_name": fund.index_name,
                    "company_name": fund.company_name,
                    "close": round(float(row["close"]), 4),
                    "pct_chg": round(float(row["pct_chg"] or 0.0), 2),
                    "amount": round(float(row["amount"] or 0.0), 2),
                    "avg_amount_20d": round(float(row["avg_amount_20d"] or 0.0), 2),
                    "vol_ratio": round(float(row["vol_ratio"] or 0.0), 2),
                    "rsi14": round(float(row["rsi14"]), 1) if row["rsi14"] is not None else None,
                    "trend_stage": str(row.get("trend_stage") or "mid"),
                    "is_fresh": bool(row.get("is_fresh", False)),
                    "hits_20d": int(row.get("hits_20d") or 0),
                    "bias20": round(float(row["bias20"]), 2) if row.get("bias20") is not None else None,
                    "bias60": round(float(row["bias60"]), 2) if row.get("bias60") is not None else None,
                    "bias20_z": round(float(row["bias20_z"]), 2) if row.get("bias20_z") is not None else None,
                    "drawup_60d_low": (
                        round(float(row["drawup_60d_low"]), 2)
                        if row.get("drawup_60d_low") is not None
                        else None
                    ),
                    "boll_pct_b": (
                        round(float(row["boll_pct_b"]), 3)
                        if row.get("boll_pct_b") is not None
                        else None
                    ),
                    "pos_60d": round(float(row["pos_60d"]), 2) if row.get("pos_60d") is not None else None,
                    "hit_count": int(row[hit_col]),
                    "composite_score": round(float(row[score_col]), 2),
                    "matched_regimes": matched_regimes,
                    "hit_strategies": hit_entries,
                    "otc_mappings": [m.model_dump() for m in fund_mappings],
                }
            )

        # Group by tracking index (or keep flat per symbol)
        groups_map: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            gkey = (
                item["index_code"]
                if (ctx.config.group_by_index and item["index_code"])
                else item["symbol"]
            )
            groups_map.setdefault(gkey, []).append(item)

        groups: list[dict[str, Any]] = []
        for gkey, members in groups_map.items():
            # Representative fund: highest 20d avg amount (most liquid), tie-break by composite_score
            members_sorted = sorted(
                members,
                key=lambda x: (x["avg_amount_20d"], x["composite_score"]),
                reverse=True,
            )
            rep = members_sorted[0]

            all_group_otc: list[OTCFundMapping] = []
            for m_item in members_sorted:
                all_group_otc.extend(ctx.mappings_by_sym.get(m_item["symbol"], []))
            sorted_otc = sort_otc_mappings(all_group_otc, preference=ctx.config.share_class_preference)

            max_hits = max(m["hit_count"] for m in members_sorted)
            max_score = max(m["composite_score"] for m in members_sorted)

            groups.append(
                {
                    "group_key": gkey,
                    "index_code": rep["index_code"],
                    "index_name": rep["index_name"] or rep["name"],
                    "representative": rep,
                    "member_count": len(members_sorted),
                    "members": members_sorted,
                    "max_hit_count": max_hits,
                    "max_composite_score": max_score,
                    "otc_funds": [m.model_dump() for m in sorted_otc],
                    "otc_count": len(sorted_otc),
                }
            )

        groups.sort(
            key=lambda g: (
                g["max_hit_count"],
                g["max_composite_score"],
                g["representative"]["avg_amount_20d"],
            ),
            reverse=True,
        )
        return items, groups

    def scan(self, config: ScanRequestConfig) -> dict[str, Any]:
        """Run full two-stage screening on target_date and return Actionable & Watchlist groups."""
        t0 = time.perf_counter()

        # 420 calendar days (~280 trading days) covers max 250-day lookback while making scans ~4x faster
        panel, per_strategy, enabled_ids, effective_date = self.evaluate_panel(
            config, window_calendar_days=420
        )
        if panel.is_empty() or not enabled_ids or effective_date is None:
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return {
                "trade_date": str(effective_date or ""),
                "elapsed_ms": elapsed_ms,
                "scanned_fund_count": 0,
                "hit_fund_count": 0,
                "hit_index_count": 0,
                "actionable_fund_count": 0,
                "actionable_index_count": 0,
                "watchlist_fund_count": 0,
                "watchlist_index_count": 0,
                "regime_combine_mode": config.regime_combine_mode,
                "combine_mode": config.combine_mode,
                "hide_overheated": config.hide_overheated,
                "share_class_preference": config.share_class_preference,
                "groups": [],
                "actionable_groups": [],
                "watchlist_groups": [],
            }

        scanned_fund_count = panel["symbol"].n_unique()
        combined = self.combine_signals(
            panel,
            per_strategy,
            enabled_ids,
            config.combine_mode,
            config.min_hits,
            regime_combine_mode=config.regime_combine_mode,
            freshness_cooldown_days=config.freshness_cooldown_days,
        )

        day_slice = combined.filter(pl.col("trade_date") == effective_date)
        day_actionable = day_slice.filter(pl.col("actionable_signal"))
        if config.hide_overheated:
            day_actionable = day_actionable.filter(pl.col("trend_stage") != "overheated")
        day_watchlist = day_slice.filter(pl.col("watchlist_signal"))

        all_strats = self.registry.list_all()
        group_ctx = _GroupBuildContext(
            enabled_ids=enabled_ids,
            fund_by_sym={f.symbol: f for f in self.sqlite.list_exchange_funds(active_only=True)},
            mappings_by_sym=self.sqlite.get_all_mappings_grouped(),
            strat_names={s.id: s.name for s in all_strats},
            strat_roles={s.id: s.role for s in all_strats},
            config=config,
        )

        actionable_items, actionable_groups = self._build_groups(
            day_actionable, bucket="actionable", ctx=group_ctx
        )
        watchlist_items, watchlist_groups = self._build_groups(
            day_watchlist, bucket="watchlist", ctx=group_ctx
        )

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        result = {
            "trade_date": effective_date.isoformat(),
            "elapsed_ms": elapsed_ms,
            "scanned_fund_count": int(scanned_fund_count),
            "hit_fund_count": len(actionable_items),
            "hit_index_count": len(actionable_groups),
            "actionable_fund_count": len(actionable_items),
            "actionable_index_count": len(actionable_groups),
            "watchlist_fund_count": len(watchlist_items),
            "watchlist_index_count": len(watchlist_groups),
            "regime_combine_mode": config.regime_combine_mode,
            "combine_mode": config.combine_mode,
            "hide_overheated": config.hide_overheated,
            "share_class_preference": config.share_class_preference,
            "groups": actionable_groups,
            "actionable_groups": actionable_groups,
            "watchlist_groups": watchlist_groups,
        }

        if config.save_snapshot:
            self.sqlite.save_scan_snapshot(
                trade_date=effective_date.isoformat(),
                preset_id=config.preset_id,
                config=config.model_dump(mode="json"),
                hit_fund_count=len(actionable_items),
                hit_index_count=len(actionable_groups),
                elapsed_ms=elapsed_ms,
                result=result,
            )

        return result

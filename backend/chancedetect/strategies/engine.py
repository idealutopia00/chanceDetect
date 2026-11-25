"""Vectorized strategy execution engine with caching, multi-strategy composition, and index grouping."""

from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, Field

from chancedetect.data.models import ExchangeFund, OTCFundMapping
from chancedetect.storage.bar_store import BarStore
from chancedetect.storage.sqlite_store import SQLiteStore
from chancedetect.strategies.indicators import compute_base_features
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
    combine_mode: Literal["AND", "OR", "RESONANCE"] = "OR"
    min_hits: int = Field(default=2, ge=1)
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
                self._panel_cache = self.bars.load_features()
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
        selections = [s for s in config.strategies if s.enabled]
        if not selections:
            selections = [
                StrategySelection(strategy_id=s.id, enabled=True, params={})
                for s in self.registry.list_all()
            ]

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
    ) -> pl.DataFrame:
        """Combine per-strategy outputs into a single panel DataFrame with combined_signal & composite_score."""
        if panel.is_empty() or not enabled_ids:
            return panel.head(0)

        base = panel.select(["symbol", "trade_date", "close", "pct_chg", "amount", "avg_amount_20d", "vol_ratio", "rsi14"])
        sig_cols: list[str] = []
        score_cols: list[str] = []
        aligned_series: list[pl.Series] = []
        unaligned: list[tuple[int, pl.DataFrame]] = []

        for idx, sid in enumerate(enabled_ids):
            sdf = per_strategy[sid]
            sig_cols.append(f"_sig_{idx}")
            score_cols.append(f"_scr_{idx}")
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

        hit_count_expr = pl.sum_horizontal([pl.col(c).fill_null(False).cast(pl.Int32) for c in sig_cols])
        score_sum_expr = pl.sum_horizontal([pl.col(c).fill_null(0.0) for c in score_cols])

        if combine_mode == "AND":
            required = len(enabled_ids)
        elif combine_mode == "OR":
            required = 1
        else:
            required = max(1, min(min_hits, len(enabled_ids)))

        combined = base.with_columns(
            hit_count_expr.alias("hit_count"),
            (
                pl.when(hit_count_expr > 0)
                .then((score_sum_expr / hit_count_expr + (hit_count_expr - 1) * 12.0).round(2))
                .otherwise(0.0)
            ).alias("composite_score"),
        ).with_columns((pl.col("hit_count") >= required).alias("combined_signal"))

        return combined

    def scan(self, config: ScanRequestConfig) -> dict[str, Any]:
        """Run full screening on target_date and return index-grouped opportunities with OTC funds."""
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
                "combine_mode": config.combine_mode,
                "share_class_preference": config.share_class_preference,
                "groups": [],
            }

        scanned_fund_count = panel["symbol"].n_unique()
        combined = self.combine_signals(
            panel, per_strategy, enabled_ids, config.combine_mode, config.min_hits
        )

        # Slice target_date (use each fund's exact effective_date row)
        day_hits = combined.filter(
            (pl.col("trade_date") == effective_date) & pl.col("combined_signal")
        ).sort(["hit_count", "composite_score", "avg_amount_20d"], descending=[True, True, True])

        fund_by_sym: dict[str, ExchangeFund] = {
            f.symbol: f for f in self.sqlite.list_exchange_funds(active_only=True)
        }
        mappings_by_sym = self.sqlite.get_all_mappings_grouped()
        strat_names = {s.id: s.name for s in self.registry.list_all()}

        # Materialize hit rows
        hit_items: list[dict[str, Any]] = []
        for row in day_hits.iter_rows(named=True):
            sym = row["symbol"]
            fund = fund_by_sym.get(sym)
            if fund is None:
                continue
            hit_strats: list[dict[str, Any]] = []
            for idx, sid in enumerate(enabled_ids):
                if row.get(f"_sig_{idx}"):
                    hit_strats.append(
                        {
                            "strategy_id": sid,
                            "strategy_name": strat_names.get(sid, sid),
                            "score": round(float(row.get(f"_scr_{idx}") or 0.0), 2),
                            "reason": str(row.get(f"_rsn_{idx}") or ""),
                        }
                    )

            fund_mappings = sort_otc_mappings(
                mappings_by_sym.get(sym, []),
                preference=config.share_class_preference,
            )
            hit_items.append(
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
                    "hit_count": int(row["hit_count"]),
                    "composite_score": round(float(row["composite_score"]), 2),
                    "hit_strategies": hit_strats,
                    "otc_mappings": [m.model_dump() for m in fund_mappings],
                }
            )

        # Group by tracking index (or keep flat per symbol)
        groups_map: dict[str, list[dict[str, Any]]] = {}
        for item in hit_items:
            gkey = (
                item["index_code"]
                if (config.group_by_index and item["index_code"])
                else item["symbol"]
            )
            groups_map.setdefault(gkey, []).append(item)

        groups: list[dict[str, Any]] = []
        for gkey, members in groups_map.items():
            # Representative ETF: highest 20d avg amount (most liquid), tie-break by composite_score
            members_sorted = sorted(
                members,
                key=lambda x: (x["avg_amount_20d"], x["composite_score"]),
                reverse=True,
            )
            rep = members_sorted[0]

            # Aggregate all OTC funds mapped to any hitting member in this index group
            all_group_otc: list[OTCFundMapping] = []
            for m_item in members_sorted:
                all_group_otc.extend(mappings_by_sym.get(m_item["symbol"], []))
            sorted_otc = sort_otc_mappings(all_group_otc, preference=config.share_class_preference)

            # Union of strategy tags across the group (anchored on representative)
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

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        result = {
            "trade_date": effective_date.isoformat(),
            "elapsed_ms": elapsed_ms,
            "scanned_fund_count": int(scanned_fund_count),
            "hit_fund_count": len(hit_items),
            "hit_index_count": len(groups),
            "combine_mode": config.combine_mode,
            "share_class_preference": config.share_class_preference,
            "groups": groups,
        }

        if config.save_snapshot:
            self.sqlite.save_scan_snapshot(
                trade_date=effective_date.isoformat(),
                preset_id=config.preset_id,
                config=config.model_dump(mode="json"),
                hit_fund_count=len(hit_items),
                hit_index_count=len(groups),
                elapsed_ms=elapsed_ms,
                result=result,
            )

        return result

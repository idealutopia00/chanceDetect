"""Lightweight historical signal replay & forward N-day return statistics for a single fund."""

from __future__ import annotations

import statistics
from typing import Any

import polars as pl

from chancedetect.strategies.engine import ScanRequestConfig, StrategyEngine, sort_otc_mappings


def _compute_forward_stats(
    closes: list[float],
    lows: list[float],
    trigger_indices: list[int],
    horizons: tuple[int, ...] = (1, 5, 10, 20),
) -> list[dict[str, Any]]:
    """Compute win rate, avg/median return, max gain/loss, and avg max drawdown after each trigger."""
    n = len(closes)
    stats_out: list[dict[str, Any]] = []

    for h in horizons:
        rets: list[float] = []
        mdds: list[float] = []
        for idx in trigger_indices:
            entry = closes[idx]
            target_idx = idx + h
            if entry <= 0 or target_idx >= n:
                continue
            exit_p = closes[target_idx]
            ret_pct = (exit_p / entry - 1.0) * 100.0
            rets.append(ret_pct)

            # Max drawdown during holding window (idx+1 .. target_idx)
            window_low = min(lows[idx + 1 : target_idx + 1])
            dd_pct = min(0.0, (window_low / entry - 1.0) * 100.0)
            mdds.append(dd_pct)

        if not rets:
            stats_out.append(
                {
                    "horizon_days": h,
                    "sample_count": 0,
                    "win_rate_pct": None,
                    "avg_return_pct": None,
                    "median_return_pct": None,
                    "max_gain_pct": None,
                    "max_loss_pct": None,
                    "avg_max_drawdown_pct": None,
                }
            )
            continue

        wins = sum(1 for r in rets if r > 0)
        stats_out.append(
            {
                "horizon_days": h,
                "sample_count": len(rets),
                "win_rate_pct": round(wins / len(rets) * 100.0, 1),
                "avg_return_pct": round(sum(rets) / len(rets), 2),
                "median_return_pct": round(statistics.median(rets), 2),
                "max_gain_pct": round(max(rets), 2),
                "max_loss_pct": round(min(rets), 2),
                "avg_max_drawdown_pct": round(sum(mdds) / len(mdds), 2),
            }
        )
    return stats_out


def build_fund_replay(
    engine: StrategyEngine,
    symbol: str,
    config: ScanRequestConfig,
    *,
    lookback_days: int = 500,
) -> dict[str, Any]:
    """Build K-line series, historical trigger markers, forward stats, and OTC fund mappings."""
    fund = engine.sqlite.get_exchange_fund(symbol)
    if fund is None:
        raise KeyError(f"Fund not found: {symbol}")

    # Evaluate across full panel (relaxed base filter if target fund itself wouldn't pass)
    relaxed_config = config.model_copy(
        update={
            "base_filter": config.base_filter.model_copy(
                update={"min_avg_amount_20d": 0.0, "min_listing_days": 0}
            )
        }
    )
    panel, per_strategy, enabled_ids, _ = engine.evaluate_panel(relaxed_config)
    sym_panel = panel.filter(pl.col("symbol") == symbol).sort("trade_date")
    if sym_panel.is_empty():
        raise ValueError(f"No daily bar history found for {symbol}")

    if enabled_ids:
        combined_full = engine.combine_signals(
            panel, per_strategy, enabled_ids, config.combine_mode, config.min_hits
        )
        sym_signals = combined_full.filter(pl.col("symbol") == symbol).sort("trade_date")
    else:
        sym_signals = sym_panel.with_columns(
            pl.lit(False).alias("combined_signal"),
            pl.lit(0).alias("hit_count"),
            pl.lit(0.0).alias("composite_score"),
        )

    # Trim to last lookback_days for chart + stats
    if sym_panel.height > lookback_days:
        sym_panel = sym_panel.tail(lookback_days)
        min_dt = sym_panel["trade_date"][0]
        sym_signals = sym_signals.filter(pl.col("trade_date") >= min_dt)

    strat_names = {s.id: s.name for s in engine.registry.list_all()}

    # Build K-line bars for Lightweight Charts
    bars_out: list[dict[str, Any]] = []
    closes: list[float] = []
    lows: list[float] = []
    date_to_idx: dict[str, int] = {}

    for idx, row in enumerate(sym_panel.iter_rows(named=True)):
        dt_str = row["trade_date"].isoformat()
        date_to_idx[dt_str] = idx
        c = float(row["close"])
        lo = float(row["low"])
        closes.append(c)
        lows.append(lo)
        bars_out.append(
            {
                "time": dt_str,
                "open": round(float(row["open"]), 4),
                "high": round(float(row["high"]), 4),
                "low": round(lo, 4),
                "close": round(c, 4),
                "volume": round(float(row["volume"]), 2),
                "amount": round(float(row["amount"]), 2),
                "pct_chg": round(float(row["pct_chg"] or 0.0), 2),
                "ma5": round(float(row["ma5"]), 4) if row.get("ma5") is not None else None,
                "ma10": round(float(row["ma10"]), 4) if row.get("ma10") is not None else None,
                "ma20": round(float(row["ma20"]), 4) if row.get("ma20") is not None else None,
                "ma60": round(float(row["ma60"]), 4) if row.get("ma60") is not None else None,
                "rsi14": round(float(row["rsi14"]), 1) if row.get("rsi14") is not None else None,
            }
        )

    # Extract triggers
    triggers: list[dict[str, Any]] = []
    trigger_indices: list[int] = []
    for row in sym_signals.filter(pl.col("combined_signal")).iter_rows(named=True):
        dt_str = row["trade_date"].isoformat()
        idx = date_to_idx.get(dt_str)
        if idx is None:
            continue
        trigger_indices.append(idx)
        hit_strats = []
        reasons = []
        for s_idx, sid in enumerate(enabled_ids):
            if row.get(f"_sig_{s_idx}"):
                hit_strats.append(strat_names.get(sid, sid))
                r = str(row.get(f"_rsn_{s_idx}") or "")
                if r:
                    reasons.append(r)
        triggers.append(
            {
                "date": dt_str,
                "close": round(float(row["close"]), 4),
                "hit_count": int(row["hit_count"]),
                "composite_score": round(float(row["composite_score"]), 2),
                "hit_strategies": hit_strats,
                "reasons": reasons,
            }
        )

    forward_stats = _compute_forward_stats(closes, lows, trigger_indices)

    # Direct OTC mappings + same-index OTC mappings
    direct_mappings = sort_otc_mappings(
        engine.sqlite.get_mappings_by_exchange(symbol),
        preference=config.share_class_preference,
    )
    same_index_otc = list(direct_mappings)
    if fund.index_code:
        all_funds = engine.sqlite.list_exchange_funds(active_only=True)
        all_map = engine.sqlite.get_all_mappings_grouped()
        for other in all_funds:
            if other.index_code == fund.index_code and other.symbol != symbol:
                same_index_otc.extend(all_map.get(other.symbol, []))
        same_index_otc = sort_otc_mappings(same_index_otc, preference=config.share_class_preference)

    return {
        "fund": fund.model_dump(),
        "lookback_days": len(bars_out),
        "trigger_count": len(triggers),
        "bars": bars_out,
        "triggers": triggers,
        "forward_stats": forward_stats,
        "direct_otc_mappings": [m.model_dump() for m in direct_mappings],
        "index_otc_mappings": [m.model_dump() for m in same_index_otc],
    }

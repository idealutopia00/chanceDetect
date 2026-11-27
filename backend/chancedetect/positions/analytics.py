"""Dual-curve review builder, post-exit rearview stats, and explainable 5-rule Exit Signal evaluator."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any, Literal

import polars as pl

from chancedetect.data.models import OTCNavBar, TrackedPositionRecord, TradeLotRecord
from chancedetect.positions.accounting import PositionAccountingResult
from chancedetect.strategies.guide_metadata import DEFAULT_EXIT_THRESHOLDS
from chancedetect.strategies.indicators import (
    compute_base_features,
    ensure_extension_features,
    trend_stage_expr,
)

DEFAULT_TRAILING_DRAWDOWN_PCT = abs(float(DEFAULT_EXIT_THRESHOLDS["trailing_drawdown_pct"]))
DEFAULT_COST_STOP_LOSS_PCT = abs(float(DEFAULT_EXIT_THRESHOLDS["cost_stop_loss_pct"]))
MA20_WATCH_DAYS = int(DEFAULT_EXIT_THRESHOLDS["ma20_watch_days"])
MA20_ALERT_DAYS = int(DEFAULT_EXIT_THRESHOLDS["ma20_alert_days"])
SHORT_TERM_FEE_DAYS = int(DEFAULT_EXIT_THRESHOLDS["short_term_fee_days"])
SHORT_TERM_FEE_RATE_PCT = float(DEFAULT_EXIT_THRESHOLDS["short_term_fee_rate_pct"])

ExitSeverity = Literal["alert", "warning", "ok"]
PRE_ENTRY_KLINE_LOOKBACK = 30


def _iso_date(val: Any) -> str:
    if isinstance(val, date):
        return val.isoformat()
    return str(val)


def _opt_float(val: Any, digits: int = 4) -> float | None:
    if val is None:
        return None
    f = float(val)
    if f != f:  # NaN guard
        return None
    return round(f, digits)


def extract_proxy_bars_by_symbol(
    bars_df: pl.DataFrame,
    features_df: pl.DataFrame | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Build per-symbol OHLCV + MA5/MA10/MA20/MA60 + Trend Stage rows for linked proxy funds.

    Uses precomputed `features_df` when present and complete, falling back to computing
    indicators on the fly over the tracked symbols so analytics stay accurate even if
    features.parquet has not been recomputed yet.
    """
    source_df: pl.DataFrame | None = None
    if features_df is not None and not features_df.is_empty():
        source_df = ensure_extension_features(features_df)
    elif not bars_df.is_empty():
        source_df = compute_base_features(bars_df)

    if source_df is None or source_df.is_empty():
        return {}

    if "trend_stage" not in source_df.columns:
        source_df = source_df.with_columns(trend_stage_expr().alias("trend_stage"))

    needed_cols = [
        "symbol",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "pct_chg",
        "ma5",
        "ma10",
        "ma20",
        "ma60",
        "trend_stage",
    ]
    available = [c for c in needed_cols if c in source_df.columns]
    ordered = source_df.select(available).sort(["symbol", "trade_date"])

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in ordered.iter_rows(named=True):
        sym = str(row["symbol"])
        grouped.setdefault(sym, []).append(
            {
                "time": _iso_date(row["trade_date"]),
                "open": _opt_float(row.get("open"), 4) or 0.0,
                "high": _opt_float(row.get("high"), 4) or 0.0,
                "low": _opt_float(row.get("low"), 4) or 0.0,
                "close": _opt_float(row.get("close"), 4) or 0.0,
                "volume": _opt_float(row.get("volume"), 2) or 0.0,
                "pct_chg": _opt_float(row.get("pct_chg"), 2) or 0.0,
                "ma5": _opt_float(row.get("ma5"), 4),
                "ma10": _opt_float(row.get("ma10"), 4),
                "ma20": _opt_float(row.get("ma20"), 4),
                "ma60": _opt_float(row.get("ma60"), 4),
                "trend_stage": str(row["trend_stage"]) if row.get("trend_stage") else None,
            }
        )
    return grouped


def _compute_otc_cumulative_returns(
    otc_bars: Sequence[OTCNavBar],
    opened_date: str,
) -> tuple[OTCNavBar | None, dict[str, float], dict[str, float]]:
    """Compute dividend-adjusted OTC cumulative return % anchored to 0.0% at baseline."""
    from_open = [b for b in otc_bars if b.nav_date >= opened_date]
    if not from_open:
        return None, {}, {}

    baseline = from_open[0]
    cum_factor = 1.0
    cum_by_date: dict[str, float] = {baseline.nav_date: 0.0}
    nav_by_date: dict[str, float] = {baseline.nav_date: baseline.unit_nav}

    prev = baseline
    for bar in from_open[1:]:
        if bar.daily_return_pct is not None:
            step = 1.0 + float(bar.daily_return_pct) / 100.0
        elif prev.unit_nav > 0:
            step = float(bar.unit_nav) / float(prev.unit_nav)
        else:
            step = 1.0
        cum_factor *= max(step, 0.0)
        cum_by_date[bar.nav_date] = round((cum_factor - 1.0) * 100.0, 2)
        nav_by_date[bar.nav_date] = float(bar.unit_nav)
        prev = bar

    return baseline, cum_by_date, nav_by_date


def _compute_proxy_cumulative_returns(
    proxy_rows: Sequence[dict[str, Any]],
    opened_date: str,
) -> tuple[str | None, dict[str, float], dict[str, float]]:
    """Compute forward-adjusted (qfq) proxy cumulative return % anchored to 0.0% at baseline."""
    from_open = [r for r in proxy_rows if r["time"] >= opened_date and r["close"] > 0]
    if not from_open:
        return None, {}, {}

    base_row = from_open[0]
    base_close = float(base_row["close"])
    cum_by_date: dict[str, float] = {}
    close_by_date: dict[str, float] = {}
    for row in from_open:
        dt = row["time"]
        close = float(row["close"])
        cum_by_date[dt] = round((close / base_close - 1.0) * 100.0, 2)
        close_by_date[dt] = close
    return str(base_row["time"]), cum_by_date, close_by_date


def _classify_lot_markers(
    position: TrackedPositionRecord,
    lots: Sequence[TradeLotRecord],
    otc_cum_by_date: dict[str, float],
    points: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build annotated trade event markers (buy / add / partial_sell / full_sell)."""
    point_ret_by_date = {
        p["date"]: p["otc_return_pct"]
        for p in points
        if p.get("otc_return_pct") is not None
    }
    ordered_point_dates = sorted(point_ret_by_date.keys())

    def _lookup_ret(dt: str) -> float | None:
        if dt in otc_cum_by_date:
            return otc_cum_by_date[dt]
        if dt in point_ret_by_date:
            return point_ret_by_date[dt]
        prior = [d for d in ordered_point_dates if d <= dt]
        return point_ret_by_date[prior[-1]] if prior else None

    sell_lot_ids = [lot.id for lot in lots if lot.side == "sell"]
    last_sell_id = sell_lot_ids[-1] if sell_lot_ids else None

    seen_buy = False
    markers: list[dict[str, Any]] = []
    for lot in lots:
        if lot.side == "buy":
            if not seen_buy:
                kind = "buy"
                label = "建仓"
                seen_buy = True
            else:
                kind = "add"
                label = "加仓"
        else:
            is_full = (
                (position.status == "closed" and lot.id == last_sell_id)
                or (lot.sell_ratio is not None and lot.sell_ratio >= 0.9999)
            )
            kind = "full_sell" if is_full else "partial_sell"
            label = "清仓" if is_full else "减仓"

        markers.append(
            {
                "lot_id": lot.id,
                "date": lot.trade_date,
                "side": lot.side,
                "kind": kind,
                "label": label,
                "nav": lot.nav,
                "nav_status": lot.nav_status,
                "return_pct_on_date": _lookup_ret(lot.trade_date),
                "amount": lot.amount,
                "shares": lot.shares,
                "sell_ratio": lot.sell_ratio,
                "notes": lot.notes,
            }
        )
    return markers


def _slice_proxy_kline_bars(
    proxy_rows: Sequence[dict[str, Any]],
    *,
    opened_date: str,
    closed_date: str | None,
    status: str,
    post_exit_days: int,
) -> list[dict[str, Any]]:
    if not proxy_rows:
        return []
    open_idx = 0
    for idx, row in enumerate(proxy_rows):
        if row["time"] >= opened_date:
            open_idx = idx
            break
    else:
        open_idx = max(len(proxy_rows) - PRE_ENTRY_KLINE_LOOKBACK, 0)

    start_idx = max(open_idx - PRE_ENTRY_KLINE_LOOKBACK, 0)
    if status == "closed" and closed_date:
        close_idx = len(proxy_rows) - 1
        for idx, row in enumerate(proxy_rows):
            if row["time"] <= closed_date:
                close_idx = idx
            else:
                break
        end_idx = min(len(proxy_rows), close_idx + 1 + max(post_exit_days, 0))
    else:
        end_idx = len(proxy_rows)

    return list(proxy_rows[start_idx:end_idx])


def build_position_review_analytics(
    position: TrackedPositionRecord,
    lots: Sequence[TradeLotRecord],
    acct: PositionAccountingResult,
    otc_nav_bars: Sequence[OTCNavBar],
    proxy_rows: Sequence[dict[str, Any]],
    *,
    post_exit_days: int = 40,
    include_series: bool = True,
) -> dict[str, Any]:
    """Compute dual-curve series, drawdown metrics, post-exit rearview stats, and proxy K-line slice."""
    opened_date = acct.opened_date or position.opened_date
    closed_date = acct.closed_date if position.status == "closed" else position.closed_date

    first_buy = next((lot for lot in lots if lot.side == "buy"), None)
    otc_baseline, otc_cum_by_date, otc_nav_by_date = _compute_otc_cumulative_returns(
        otc_nav_bars, opened_date
    )
    proxy_base_date, proxy_cum_by_date, proxy_close_by_date = _compute_proxy_cumulative_returns(
        proxy_rows, opened_date
    )

    is_pending_baseline = bool(
        first_buy is not None
        and first_buy.nav_status == "pending"
        and (otc_baseline is None or otc_baseline.nav_date > opened_date)
    )
    has_proxy_curve = bool(position.exchange_symbol and proxy_cum_by_date)

    all_dates = sorted(set(otc_cum_by_date) | set(proxy_cum_by_date))
    if position.status == "closed" and closed_date:
        holding_dates = [d for d in all_dates if d <= closed_date]
        post_dates = [d for d in all_dates if d > closed_date][: max(post_exit_days, 0)]
        curve_dates = holding_dates + post_dates
    else:
        curve_dates = all_dates

    points: list[dict[str, Any]] = []
    last_otc_ret: float | None = None
    last_otc_nav: float | None = None
    last_proxy_ret: float | None = None
    last_proxy_close: float | None = None

    for dt in curve_dates:
        if dt in otc_cum_by_date:
            last_otc_ret = otc_cum_by_date[dt]
            last_otc_nav = otc_nav_by_date[dt]
        if dt in proxy_cum_by_date:
            last_proxy_ret = proxy_cum_by_date[dt]
            last_proxy_close = proxy_close_by_date[dt]

        spread = (
            round(last_otc_ret - last_proxy_ret, 2)
            if (last_otc_ret is not None and last_proxy_ret is not None)
            else None
        )
        is_post = bool(position.status == "closed" and closed_date and dt > closed_date)
        points.append(
            {
                "date": dt,
                "otc_return_pct": last_otc_ret,
                "proxy_return_pct": last_proxy_ret if has_proxy_curve else None,
                "spread_pct": spread if has_proxy_curve else None,
                "otc_unit_nav": last_otc_nav,
                "proxy_close": last_proxy_close if has_proxy_curve else None,
                "is_post_exit": is_post,
            }
        )

    # Holding-period peak, current drawdown, max holding drawdown, and latest tracking spread
    holding_otc_points = [
        p for p in points if not p["is_post_exit"] and p["otc_return_pct"] is not None
    ]
    peak_return_pct: float | None = None
    peak_date: str | None = None
    current_drawdown_pct: float | None = None
    max_holding_drawdown_pct: float | None = None

    if holding_otc_points:
        running_peak_val = float(holding_otc_points[0]["otc_return_pct"])
        running_peak_dt = str(holding_otc_points[0]["date"])
        worst_dd = 0.0
        for pt in holding_otc_points:
            ret = float(pt["otc_return_pct"])
            if ret > running_peak_val:
                running_peak_val = ret
                running_peak_dt = str(pt["date"])
            denom = 1.0 + running_peak_val / 100.0
            dd = ((1.0 + ret / 100.0) / denom - 1.0) * 100.0 if denom > 0 else 0.0
            if dd < worst_dd:
                worst_dd = dd

        peak_return_pct = round(running_peak_val, 2)
        peak_date = running_peak_dt
        latest_ret = float(holding_otc_points[-1]["otc_return_pct"])
        denom = 1.0 + running_peak_val / 100.0
        raw_dd = ((1.0 + latest_ret / 100.0) / denom - 1.0) * 100.0 if denom > 0 else 0.0
        current_drawdown_pct = round(min(raw_dd, 0.0), 2)
        max_holding_drawdown_pct = round(min(worst_dd, 0.0), 2)

    holding_spread_points = [
        p for p in points if not p["is_post_exit"] and p["spread_pct"] is not None
    ]
    latest_tracking_spread_pct: float | None = (
        float(holding_spread_points[-1]["spread_pct"]) if holding_spread_points else None
    )

    # Breakeven return % relative to opening baseline NAV
    baseline_nav: float | None = None
    if first_buy is not None and first_buy.nav is not None and first_buy.nav > 0:
        baseline_nav = float(first_buy.nav)
    elif otc_baseline is not None and otc_baseline.unit_nav > 0:
        baseline_nav = float(otc_baseline.unit_nav)

    breakeven_return_pct: float | None = None
    if acct.avg_cost_nav is not None and baseline_nav is not None and baseline_nav > 0:
        breakeven_return_pct = round((acct.avg_cost_nav / baseline_nav - 1.0) * 100.0, 2)

    # Post-exit rearview stats (for closed positions)
    post_exit_stats: dict[str, Any] | None = None
    if position.status == "closed" and closed_date:
        post_points = [p for p in points if p["is_post_exit"]]
        # Anchor at exit-day return (OTC preferred, else proxy)
        exit_anchor_ret: float | None = None
        use_otc_for_post = any(p["otc_return_pct"] is not None for p in post_points)
        if use_otc_for_post and holding_otc_points:
            exit_anchor_ret = float(holding_otc_points[-1]["otc_return_pct"])
        elif not use_otc_for_post:
            holding_proxy = [
                p for p in points if not p["is_post_exit"] and p["proxy_return_pct"] is not None
            ]
            if holding_proxy:
                exit_anchor_ret = float(holding_proxy[-1]["proxy_return_pct"])

        rel_series: list[float] = []
        if exit_anchor_ret is not None and (1.0 + exit_anchor_ret / 100.0) > 0:
            exit_base = 1.0 + exit_anchor_ret / 100.0
            for pt in post_points:
                raw = pt["otc_return_pct"] if use_otc_for_post else pt["proxy_return_pct"]
                if raw is None:
                    continue
                rel = round(((1.0 + float(raw) / 100.0) / exit_base - 1.0) * 100.0, 2)
                rel_series.append(rel)

        days_obs = len(rel_series)
        post_exit_stats = {
            "closed_date": closed_date,
            "days_observed": days_obs,
            "post_exit_latest_return_pct": rel_series[-1] if days_obs > 0 else None,
            "post_exit_return_20d_pct": rel_series[19] if days_obs >= 20 else None,
            "post_exit_return_40d_pct": rel_series[39] if days_obs >= 40 else None,
            "post_exit_max_gain_pct": max(rel_series) if days_obs > 0 else None,
            "post_exit_max_drop_pct": min(rel_series) if days_obs > 0 else None,
        }

    # Evaluate 5 explainable Exit Signals on active positions
    exit_signals = evaluate_exit_signals(
        position=position,
        acct=acct,
        proxy_rows=proxy_rows,
        peak_return_pct=peak_return_pct,
        current_drawdown_pct=current_drawdown_pct,
    )
    exit_alert_count = sum(
        1 for s in exit_signals if s["triggered"] and s["severity"] == "alert"
    )
    exit_warning_count = sum(
        1 for s in exit_signals if s["triggered"] and s["severity"] == "warning"
    )

    out: dict[str, Any] = {
        "peak_return_pct": peak_return_pct,
        "peak_date": peak_date,
        "current_drawdown_pct": current_drawdown_pct,
        "max_holding_drawdown_pct": max_holding_drawdown_pct,
        "latest_tracking_spread_pct": latest_tracking_spread_pct,
        "exit_signals": exit_signals,
        "exit_alert_count": exit_alert_count,
        "exit_warning_count": exit_warning_count,
        "triggered_exit_signal_count": exit_alert_count + exit_warning_count,
        "post_exit_stats": post_exit_stats,
    }

    if include_series:
        markers = _classify_lot_markers(position, lots, otc_cum_by_date, points)
        baseline_date = (
            otc_baseline.nav_date
            if otc_baseline is not None
            else (proxy_base_date or opened_date)
        )
        out["dual_curve"] = {
            "baseline_date": baseline_date,
            "has_proxy_curve": has_proxy_curve,
            "is_pending_baseline": is_pending_baseline,
            "breakeven_return_pct": breakeven_return_pct,
            "peak_return_pct": peak_return_pct,
            "peak_date": peak_date,
            "current_drawdown_pct": current_drawdown_pct,
            "max_holding_drawdown_pct": max_holding_drawdown_pct,
            "latest_tracking_spread_pct": latest_tracking_spread_pct,
            "post_exit_days": post_exit_days,
            "points": points,
            "markers": markers,
        }
        out["proxy_bars"] = (
            _slice_proxy_kline_bars(
                proxy_rows,
                opened_date=opened_date,
                closed_date=closed_date,
                status=position.status,
                post_exit_days=post_exit_days,
            )
            if position.exchange_symbol
            else []
        )

    return out


def evaluate_exit_signals(
    *,
    position: TrackedPositionRecord,
    acct: PositionAccountingResult,
    proxy_rows: Sequence[dict[str, Any]],
    peak_return_pct: float | None,
    current_drawdown_pct: float | None,
) -> list[dict[str, Any]]:
    """Evaluate 5 explainable Exit Signals for an active Tracked Position.

    Closed positions return an empty list. Proxy-only rules (`ma20_break`,
    `overheated_stage`) are cleanly skipped for unmapped off-exchange funds.
    """
    if position.status != "active":
        return []

    signals: list[dict[str, Any]] = []
    has_proxy = bool(position.exchange_symbol and proxy_rows)

    # 1. MA20 trend break (proxy-only)
    if has_proxy:
        ma20_rows = [r for r in proxy_rows if r.get("ma20") is not None and r.get("close") is not None]
        if ma20_rows:
            streak = 0
            for r in reversed(ma20_rows):
                if float(r["close"]) < float(r["ma20"]):
                    streak += 1
                else:
                    break
            latest = ma20_rows[-1]
            latest_close = float(latest["close"])
            latest_ma20 = float(latest["ma20"])
            if streak >= MA20_ALERT_DAYS:
                signals.append(
                    {
                        "rule_id": "ma20_break",
                        "title": "均线趋势破位 (MA20)",
                        "severity": "alert",
                        "triggered": True,
                        "message": (
                            f"场内代理已连续 {streak} 个交易日收盘低于 MA20"
                            f"（最新 {latest_close:.3f} < MA20 {latest_ma20:.3f}），趋势走弱"
                        ),
                        "metric_value": float(streak),
                        "threshold_value": float(MA20_ALERT_DAYS),
                    }
                )
            elif streak >= MA20_WATCH_DAYS:
                signals.append(
                    {
                        "rule_id": "ma20_break",
                        "title": "均线跌破观察 (MA20)",
                        "severity": "warning",
                        "triggered": True,
                        "message": (
                            f"场内代理单日收盘跌破 MA20"
                            f"（{latest_close:.3f} < {latest_ma20:.3f}），注意观察次日能否收回"
                        ),
                        "metric_value": float(streak),
                        "threshold_value": float(MA20_WATCH_DAYS),
                    }
                )
            else:
                signals.append(
                    {
                        "rule_id": "ma20_break",
                        "title": "MA20 趋势正常",
                        "severity": "ok",
                        "triggered": False,
                        "message": (
                            f"场内代理收盘站稳 MA20 上方"
                            f"（{latest_close:.3f} ≥ MA20 {latest_ma20:.3f}）"
                        ),
                        "metric_value": 0.0,
                        "threshold_value": float(MA20_ALERT_DAYS),
                    }
                )

    # 2. Trailing drawdown from holding-period peak
    dd_limit = (
        abs(float(position.custom_trailing_drawdown_pct))
        if position.custom_trailing_drawdown_pct is not None
        else DEFAULT_TRAILING_DRAWDOWN_PCT
    )
    is_custom_dd = position.custom_trailing_drawdown_pct is not None
    dd_label = "自定义" if is_custom_dd else "默认"
    if current_drawdown_pct is not None and current_drawdown_pct <= -dd_limit + 1e-9:
        peak_str = f"{peak_return_pct:+.2f}%" if peak_return_pct is not None else "-"
        signals.append(
            {
                "rule_id": "trailing_drawdown",
                "title": "移动回撤预警",
                "severity": "alert",
                "triggered": True,
                "message": (
                    f"距持仓期最高收益（{peak_str}）已回撤 {current_drawdown_pct:.2f}%，"
                    f"触及 -{dd_limit:.1f}%（{dd_label}）移动回撤线"
                ),
                "metric_value": current_drawdown_pct,
                "threshold_value": round(-dd_limit, 2),
            }
        )
    else:
        dd_text = f"{current_drawdown_pct:.2f}%" if current_drawdown_pct is not None else "暂无"
        signals.append(
            {
                "rule_id": "trailing_drawdown",
                "title": "移动回撤受控",
                "severity": "ok",
                "triggered": False,
                "message": f"当前距持仓高点回撤 {dd_text}，未触及 -{dd_limit:.1f}%（{dd_label}）预警线",
                "metric_value": current_drawdown_pct,
                "threshold_value": round(-dd_limit, 2),
            }
        )

    # 3. Cost stop-loss
    sl_limit = (
        abs(float(position.custom_stop_loss_pct))
        if position.custom_stop_loss_pct is not None
        else DEFAULT_COST_STOP_LOSS_PCT
    )
    is_custom_sl = position.custom_stop_loss_pct is not None
    sl_label = "自定义" if is_custom_sl else "默认"
    cost_ret = (
        acct.unrealized_return_pct
        if acct.unrealized_return_pct is not None
        else acct.total_return_pct
    )
    if cost_ret is not None and cost_ret <= -sl_limit + 1e-9:
        signals.append(
            {
                "rule_id": "cost_stop_loss",
                "title": "成本止损提醒",
                "severity": "alert",
                "triggered": True,
                "message": (
                    f"相对加权成本收益率 {cost_ret:+.2f}%，"
                    f"已触及 -{sl_limit:.1f}%（{sl_label}）成本止损线"
                ),
                "metric_value": cost_ret,
                "threshold_value": round(-sl_limit, 2),
            }
        )
    else:
        ret_text = f"{cost_ret:+.2f}%" if cost_ret is not None else "待确认"
        signals.append(
            {
                "rule_id": "cost_stop_loss",
                "title": "成本止损安全",
                "severity": "ok",
                "triggered": False,
                "message": f"当前成本收益率 {ret_text}，未触及 -{sl_limit:.1f}%（{sl_label}）止损线",
                "metric_value": cost_ret,
                "threshold_value": round(-sl_limit, 2),
            }
        )

    # 4a. Overheated Trend Stage (proxy-only)
    if has_proxy:
        latest_stage = proxy_rows[-1].get("trend_stage")
        if latest_stage == "overheated":
            signals.append(
                {
                    "rule_id": "overheated_stage",
                    "title": "高位拥挤止盈提示",
                    "severity": "warning",
                    "triggered": True,
                    "message": "场内代理进入高位偏热（Overheated）阶段，短期拥挤度偏高，可考虑分批止盈",
                    "metric_value": None,
                    "threshold_value": None,
                }
            )
        elif latest_stage:
            signals.append(
                {
                    "rule_id": "overheated_stage",
                    "title": "趋势拥挤度正常",
                    "severity": "ok",
                    "triggered": False,
                    "message": f"场内代理当前处于 {latest_stage} 阶段，未出现高位过热拥挤",
                    "metric_value": None,
                    "threshold_value": None,
                }
            )

    # 4b. Optional custom take-profit target
    if position.custom_take_profit_pct is not None:
        tp_target = abs(float(position.custom_take_profit_pct))
        if cost_ret is not None and cost_ret >= tp_target - 1e-9:
            signals.append(
                {
                    "rule_id": "take_profit_reached",
                    "title": "已达自定义止盈目标",
                    "severity": "alert",
                    "triggered": True,
                    "message": f"当前成本收益 {cost_ret:+.2f}% 已达到自定义止盈目标 +{tp_target:.1f}%",
                    "metric_value": cost_ret,
                    "threshold_value": round(tp_target, 2),
                }
            )
        else:
            ret_text = f"{cost_ret:+.2f}%" if cost_ret is not None else "待确认"
            signals.append(
                {
                    "rule_id": "take_profit_reached",
                    "title": "止盈目标追踪中",
                    "severity": "ok",
                    "triggered": False,
                    "message": f"当前收益 {ret_text}，距离自定义止盈目标 +{tp_target:.1f}% 尚有空间",
                    "metric_value": cost_ret,
                    "threshold_value": round(tp_target, 2),
                }
            )

    # 5. FIFO 7-calendar-day short-term redemption fee guard
    fifo = acct.fifo_fee_status
    if not fifo.all_fee_free and fifo.penalized_ratio > 0:
        free_pct = round(fifo.fee_free_ratio * 100.0, 1)
        days_left = fifo.days_until_all_fee_free
        date_hint = (
            f"（{fifo.earliest_all_fee_free_date} 起全部免罚费）"
            if fifo.earliest_all_fee_free_date
            else ""
        )
        signals.append(
            {
                "rule_id": "short_term_fee_guard",
                "title": "7 自然日短赎费保护",
                "severity": "warning",
                "triggered": True,
                "message": (
                    f"按 FIFO 核算当前仅 {free_pct:.0f}% 份额满 7 天免罚费，"
                    f"剩余份额赎回将扣 {SHORT_TERM_FEE_RATE_PCT:.1f}% 惩罚性费率，"
                    f"还需 {days_left} 个自然日{date_hint}"
                ),
                "metric_value": float(fifo.min_holding_days) if fifo.min_holding_days is not None else 0.0,
                "threshold_value": float(SHORT_TERM_FEE_DAYS),
            }
        )
    else:
        signals.append(
            {
                "rule_id": "short_term_fee_guard",
                "title": "已免 1.5% 短赎费",
                "severity": "ok",
                "triggered": False,
                "message": f"全部持仓份额已持有满 {SHORT_TERM_FEE_DAYS} 个自然日，赎回免收 {SHORT_TERM_FEE_RATE_PCT:.1f}% 短赎罚费",
                "metric_value": float(fifo.min_holding_days) if fifo.min_holding_days is not None else float(SHORT_TERM_FEE_DAYS),
                "threshold_value": float(SHORT_TERM_FEE_DAYS),
            }
        )

    return signals

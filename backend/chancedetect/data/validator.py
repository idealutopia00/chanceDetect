"""Data quality validation and corporate-action adjustment drift detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import polars as pl

from chancedetect.data.models import RawBar


@dataclass
class ValidationReport:
    valid_bars: list[RawBar] = field(default_factory=list)
    dropped_count: int = 0
    warnings: list[str] = field(default_factory=list)


def validate_bars(symbol: str, bars: list[RawBar]) -> ValidationReport:
    """Validate OHLCV invariants, deduplicate by date, sort ascending, and flag anomalies."""
    report = ValidationReport()
    deduped: dict[date, RawBar] = {}

    for bar in bars:
        if bar.open <= 0 or bar.high <= 0 or bar.low <= 0 or bar.close <= 0:
            report.dropped_count += 1
            continue
        if bar.volume < 0 or bar.amount < 0:
            report.dropped_count += 1
            continue
        top = max(bar.open, bar.close)
        bottom = min(bar.open, bar.close)
        # Allow tiny floating point rounding epsilon (1e-4) from provider qfq scaling
        if bar.high + 1e-4 < top or bar.low - 1e-4 > bottom or bar.high < bar.low:
            report.dropped_count += 1
            continue
        fixed_high = max(bar.high, top)
        fixed_low = min(bar.low, bottom)
        cleaned = bar.model_copy(update={"high": fixed_high, "low": fixed_low})
        deduped[cleaned.trade_date] = cleaned

    ordered = [deduped[d] for d in sorted(deduped.keys())]
    report.valid_bars = ordered

    # Detect extreme single-day jumps (>25%) after initial bar as quality warnings
    for i in range(1, len(ordered)):
        prev_close = ordered[i - 1].close
        curr = ordered[i]
        if prev_close > 0:
            pct = abs(curr.close - prev_close) / prev_close
            if pct > 0.25:
                report.warnings.append(
                    f"{symbol} {curr.trade_date}: abnormal daily move {pct * 100:.2f}% "
                    f"(prev={prev_close:.4f}, close={curr.close:.4f})"
                )

    return report


def detect_corporate_action_drift(
    existing_tail_closes: dict[date, float],
    incoming_bars: list[RawBar],
    *,
    tolerance_pct: float = 0.002,
) -> bool:
    """Return True if overlapping dates diverge beyond tolerance (indicating dividend/split qfq shift)."""
    if not existing_tail_closes or not incoming_bars:
        return False

    for bar in incoming_bars:
        prev_stored = existing_tail_closes.get(bar.trade_date)
        if prev_stored is not None and prev_stored > 0:
            rel_diff = abs(bar.close - prev_stored) / prev_stored
            if rel_diff > tolerance_pct:
                return True
    return False


def bars_to_polars(bars: list[RawBar]) -> pl.DataFrame:
    """Convert validated RawBar list into a normalized Polars DataFrame."""
    if not bars:
        return pl.DataFrame(
            schema={
                "symbol": pl.Utf8,
                "trade_date": pl.Date,
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Float64,
                "amount": pl.Float64,
                "turnover_pct": pl.Float64,
                "pct_chg": pl.Float64,
                "source": pl.Utf8,
            }
        )
    return pl.DataFrame(
        {
            "symbol": [b.symbol for b in bars],
            "trade_date": [b.trade_date for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
            "amount": [b.amount for b in bars],
            "turnover_pct": [b.turnover_pct for b in bars],
            "pct_chg": [0.0 for _ in bars],
            "source": [b.source for b in bars],
        }
    )

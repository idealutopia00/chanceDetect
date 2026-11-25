"""Tests for data quality validation and corporate-action drift detection."""

from __future__ import annotations

from datetime import date

from chancedetect.data.models import RawBar
from chancedetect.data.validator import detect_corporate_action_drift, validate_bars


def test_validate_bars_filters_invalid_and_sorts() -> None:
    bars = [
        RawBar(symbol="sh510300", trade_date=date(2025, 1, 3), open=4.1, high=4.2, low=4.0, close=4.15, volume=100),
        # Invalid: negative price
        RawBar(symbol="sh510300", trade_date=date(2025, 1, 4), open=-1.0, high=4.2, low=4.0, close=4.1, volume=100),
        # Invalid: high < close
        RawBar(symbol="sh510300", trade_date=date(2025, 1, 5), open=4.1, high=4.0, low=3.9, close=4.2, volume=100),
        # Earlier valid bar out of order
        RawBar(symbol="sh510300", trade_date=date(2025, 1, 2), open=4.0, high=4.1, low=3.95, close=4.05, volume=120),
        # Abnormal >25% jump (kept but flagged as warning)
        RawBar(symbol="sh510300", trade_date=date(2025, 1, 6), open=5.5, high=5.6, low=5.4, close=5.5, volume=200),
    ]
    report = validate_bars("sh510300", bars)
    assert report.dropped_count == 2
    assert [b.trade_date for b in report.valid_bars] == [
        date(2025, 1, 2),
        date(2025, 1, 3),
        date(2025, 1, 6),
    ]
    assert len(report.warnings) == 1
    assert "abnormal daily move" in report.warnings[0]


def test_corporate_action_drift_detection() -> None:
    stored = {
        date(2025, 6, 16): 4.100,
        date(2025, 6, 17): 4.120,
    }
    # No drift (exact match + new bar)
    same = [
        RawBar(symbol="sh510300", trade_date=date(2025, 6, 17), open=4.11, high=4.15, low=4.10, close=4.120, volume=100),
        RawBar(symbol="sh510300", trade_date=date(2025, 6, 18), open=4.12, high=4.18, low=4.11, close=4.160, volume=100),
    ]
    assert detect_corporate_action_drift(stored, same, tolerance_pct=0.002) is False

    # Drift due to dividend ex-date lowering historical qfq close by ~1.5%
    drifted = [
        RawBar(symbol="sh510300", trade_date=date(2025, 6, 17), open=4.05, high=4.08, low=4.04, close=4.058, volume=100),
        RawBar(symbol="sh510300", trade_date=date(2025, 6, 18), open=4.06, high=4.10, low=4.05, close=4.090, volume=100),
    ]
    assert detect_corporate_action_drift(stored, drifted, tolerance_pct=0.002) is True

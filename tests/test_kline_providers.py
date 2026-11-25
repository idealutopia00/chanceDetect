"""Tests for Tencent & Sina K-line parsing and automatic failover."""

from __future__ import annotations

from datetime import date

import pytest

from chancedetect.data.kline_providers import (
    BaseKlineProvider,
    FailoverKlineFetcher,
    KlineProviderError,
    SinaKlineProvider,
    TencentKlineProvider,
)
from chancedetect.data.models import RawBar


def test_tencent_payload_parsing() -> None:
    payload = {
        "code": 0,
        "data": {
            "sh510300": {
                "qfqday": [
                    ["2026-11-20", "4.014", "4.035", "4.040", "4.000", "1000000.00", {}, "2.10", "40200.50"],
                    ["2026-11-23", "4.035", "4.060", "4.070", "4.020", "1200000.00"],
                ]
            }
        },
    }
    bars = TencentKlineProvider._parse_payload("sh510300", payload)
    assert len(bars) == 2
    assert bars[0].trade_date == date(2026, 11, 20)
    assert bars[0].close == pytest.approx(4.035)
    assert bars[0].amount == pytest.approx(40200.50)
    assert bars[0].turnover_pct == pytest.approx(2.10)
    # Second row lacked explicit amount -> computed fallback estimate
    assert bars[1].amount > 0


def test_sina_qfq_adjustment_and_parsing() -> None:
    raw = [
        {"day": "2026-01-16", "open": "4.00", "high": "4.10", "low": "3.95", "close": "4.05", "volume": "1000000"},
        {"day": "2026-01-19", "open": "3.95", "high": "4.05", "low": "3.90", "close": "4.00", "volume": "2000000"},
    ]
    factors = [
        (date(2026, 1, 19), 1.0, 0.0),
        (date(2025, 6, 18), 0.98, 0.0),
    ]
    bars = SinaKlineProvider._parse_and_adjust(
        "sh510300", raw, factors, date(2026, 1, 1), date(2026, 1, 31)
    )
    assert len(bars) == 2
    # Before 2026-01-19 uses 0.98 factor
    assert bars[0].close == pytest.approx(4.05 * 0.98, abs=1e-3)
    # On 2026-01-19 uses 1.0 factor
    assert bars[1].close == pytest.approx(4.00, abs=1e-3)
    assert bars[1].volume == pytest.approx(20000.0)  # normalized to lots


class _StubProvider(BaseKlineProvider):
    def __init__(self, name: str, bars: list[RawBar] | None = None, fail: bool = False) -> None:
        self.name = name
        self._bars = bars or []
        self._fail = fail

    def fetch_daily_bars(
        self, symbol: str, start_date: date, end_date: date, *, is_benchmark: bool = False
    ) -> list[RawBar]:
        if self._fail:
            raise KlineProviderError(f"{self.name} boom")
        return self._bars


def test_failover_fetcher_uses_backup_when_primary_fails() -> None:
    sample_bar = RawBar(
        symbol="sh510300",
        trade_date=date(2026, 11, 24),
        open=4.0,
        high=4.1,
        low=3.95,
        close=4.05,
        volume=1000.0,
        source="sina",
    )
    fetcher = FailoverKlineFetcher(
        primary=_StubProvider("tencent", fail=True),
        backup=_StubProvider("sina", bars=[sample_bar]),
    )
    bars, prov, used_fb = fetcher.fetch("sh510300", date(2026, 11, 1), date(2026, 11, 25))
    assert len(bars) == 1
    assert prov == "sina"
    assert used_fb is True

    # Both fail raises KlineProviderError
    bad_fetcher = FailoverKlineFetcher(
        primary=_StubProvider("tencent", fail=True),
        backup=_StubProvider("sina", fail=True),
    )
    with pytest.raises(KlineProviderError):
        bad_fetcher.fetch("sh510300", date(2026, 11, 1), date(2026, 11, 25))

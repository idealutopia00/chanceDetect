"""Unit tests for Eastmoney pingzhongdata OTC NAV provider and parser."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from chancedetect.data.otc_nav_provider import (
    OTCNavProvider,
    OTCNavProviderError,
    parse_pingzhongdata,
)

SH_TZ = ZoneInfo("Asia/Shanghai")


def _sh_ms(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=SH_TZ).timestamp() * 1000)


SAMPLE_PINGZHONGDATA_JS = f"""
/* 006131 华泰柏瑞沪深300ETF联接C */
var ishb = false;
var fS_name = "华泰柏瑞沪深300ETF联接C";
var fS_code = "006131";
var fund_sourceRate = "0.00";
var Data_netWorthTrend = [
  {{"x": {_sh_ms(2026, 11, 18)}, "y": 1.2100, "equityReturn": 0.41, "unitMoney": ""}},
  {{"x": {_sh_ms(2026, 11, 19)}, "y": 1.1850, "equityReturn": 1.25, "unitMoney": "每份派现金0.0400元"}},
  {{"x": {_sh_ms(2026, 11, 20)}, "y": 1.2010, "equityReturn": 1.35, "unitMoney": ""}}
];
var Data_ACWorthTrend = [
  [{_sh_ms(2026, 11, 18)}, 1.5100],
  [{_sh_ms(2026, 11, 19)}, 1.5250],
  [{_sh_ms(2026, 11, 20)}, null]
];
"""


def test_parse_pingzhongdata_utc8_alignment_and_dividends() -> None:
    """Eastmoney timestamps are CST midnight (previous day 16:00 UTC); parser must align to Asia/Shanghai dates."""
    result = parse_pingzhongdata("006131", SAMPLE_PINGZHONGDATA_JS)

    assert result.otc_code == "006131"
    assert result.otc_name == "华泰柏瑞沪深300ETF联接C"
    assert len(result.bars) == 3

    b0, b1, b2 = result.bars
    assert b0.nav_date == "2026-11-18"
    assert b0.unit_nav == pytest.approx(1.21)
    assert b0.accumulated_nav == pytest.approx(1.51)
    assert b0.daily_return_pct == pytest.approx(0.41)
    assert b0.dividend_note == ""

    assert b1.nav_date == "2026-11-19"
    assert b1.unit_nav == pytest.approx(1.185)
    assert b1.accumulated_nav == pytest.approx(1.525)
    assert b1.daily_return_pct == pytest.approx(1.25)
    assert b1.dividend_note == "每份派现金0.0400元"

    assert b2.nav_date == "2026-11-20"
    assert b2.unit_nav == pytest.approx(1.201)
    assert b2.accumulated_nav is None
    assert b2.daily_return_pct == pytest.approx(1.35)


def test_parse_pingzhongdata_empty_trend_allowed() -> None:
    js = 'var fS_name = "新发基金A"; var Data_netWorthTrend = []; var Data_ACWorthTrend = [];'
    result = parse_pingzhongdata("099999", js)
    assert result.otc_name == "新发基金A"
    assert result.bars == []


def test_parse_pingzhongdata_malformed_or_empty_raises() -> None:
    with pytest.raises(OTCNavProviderError):
        parse_pingzhongdata("006131", "")

    with pytest.raises(OTCNavProviderError):
        parse_pingzhongdata("006131", "<html><body>404 Not Found</body></html>")

    with pytest.raises(OTCNavProviderError):
        parse_pingzhongdata(
            "006131",
            'var fS_name = "坏数据"; var Data_netWorthTrend = [{"x": 1700000000000, "y": ;',
        )


def test_provider_fetches_via_injectable_transport_and_handles_http_error() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.path.endswith("/006131.js"):
            return httpx.Response(200, text=SAMPLE_PINGZHONGDATA_JS)
        return httpx.Response(404, text="not found")

    provider = OTCNavProvider(transport=httpx.MockTransport(handler))
    try:
        res = provider.fetch_nav_history("006131")
        assert len(res.bars) == 3
        assert res.otc_name == "华泰柏瑞沪深300ETF联接C"
        assert requested_urls == ["https://fund.eastmoney.com/pingzhongdata/006131.js"]

        with pytest.raises(OTCNavProviderError):
            provider.fetch_nav_history("999999")
    finally:
        provider.close()

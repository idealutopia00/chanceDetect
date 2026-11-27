"""Eastmoney pingzhongdata provider for tracked off-exchange mutual fund NAV history."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from chancedetect.data.models import OTCNavBar

SH_TZ = ZoneInfo("Asia/Shanghai")


class OTCNavProviderError(RuntimeError):
    """Raised when fetching or parsing Eastmoney pingzhongdata fails."""


@dataclass(frozen=True)
class OTCNavFetchResult:
    """Parsed metadata and historical daily NAV bars for a single 6-digit OTC fund."""

    otc_code: str
    otc_name: str
    bars: list[OTCNavBar]


def _ms_to_shanghai_date(ms: int | float) -> str:
    """Convert Unix millisecond timestamp to YYYY-MM-DD in Asia/Shanghai (UTC+8)."""
    return datetime.fromtimestamp(float(ms) / 1000.0, tz=SH_TZ).date().isoformat()


def _extract_js_string(js_text: str, var_name: str) -> str:
    match = re.search(rf"\b(?:var\s+)?{re.escape(var_name)}\s*=\s*([\"'])(.*?)\1\s*;", js_text)
    if not match:
        return ""
    return match.group(2).strip()


def _extract_js_json(js_text: str, var_name: str) -> Any | None:
    match = re.search(rf"\b(?:var\s+)?{re.escape(var_name)}\s*=\s*", js_text)
    if not match:
        return None
    decoder = json.JSONDecoder()
    idx = match.end()
    while idx < len(js_text) and js_text[idx].isspace():
        idx += 1
    if idx >= len(js_text):
        raise OTCNavProviderError(f"Empty value for {var_name}")
    try:
        value, _ = decoder.raw_decode(js_text, idx)
        return value
    except json.JSONDecodeError as exc:
        raise OTCNavProviderError(f"Malformed JSON for {var_name}: {exc}") from exc


def _to_optional_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    return num


def parse_pingzhongdata(otc_code: str, js_text: str) -> OTCNavFetchResult:
    """Parse Eastmoney pingzhongdata/{otc_code}.js into fund name and sorted OTCNavBar series."""
    if not js_text or not js_text.strip():
        raise OTCNavProviderError(f"Empty pingzhongdata response for {otc_code}")

    otc_name = _extract_js_string(js_text, "fS_name")
    nw_raw = _extract_js_json(js_text, "Data_netWorthTrend")
    if nw_raw is None:
        raise OTCNavProviderError(f"Missing Data_netWorthTrend in pingzhongdata for {otc_code}")
    if not isinstance(nw_raw, list):
        raise OTCNavProviderError(f"Invalid Data_netWorthTrend type for {otc_code}")

    ac_raw = _extract_js_json(js_text, "Data_ACWorthTrend")
    acc_by_date: dict[str, float | None] = {}
    if isinstance(ac_raw, list):
        for item in ac_raw:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            ts = _to_optional_float(item[0])
            if ts is None:
                continue
            dt_str = _ms_to_shanghai_date(ts)
            acc_by_date[dt_str] = _to_optional_float(item[1])

    bars_by_date: dict[str, OTCNavBar] = {}
    for entry in nw_raw:
        if not isinstance(entry, dict):
            continue
        ts = _to_optional_float(entry.get("x"))
        unit_nav = _to_optional_float(entry.get("y"))
        if ts is None or unit_nav is None or unit_nav <= 0:
            continue
        nav_date = _ms_to_shanghai_date(ts)
        daily_return_pct = _to_optional_float(entry.get("equityReturn"))
        raw_div = entry.get("unitMoney")
        dividend_note = str(raw_div).strip() if raw_div is not None else ""
        bars_by_date[nav_date] = OTCNavBar(
            otc_code=otc_code,
            nav_date=nav_date,
            unit_nav=unit_nav,
            accumulated_nav=acc_by_date.get(nav_date),
            daily_return_pct=daily_return_pct,
            dividend_note=dividend_note,
        )

    sorted_bars = [bars_by_date[d] for d in sorted(bars_by_date.keys())]
    return OTCNavFetchResult(otc_code=otc_code, otc_name=otc_name, bars=sorted_bars)


class OTCNavProvider:
    """Fetches official daily NAV series strictly on-demand/tracked via Eastmoney pingzhongdata."""

    URL_TEMPLATE = "https://fund.eastmoney.com/pingzhongdata/{otc_code}.js"

    def __init__(
        self,
        *,
        timeout: float = 12.0,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._owns_client = client is None
        if client is not None:
            self._client = client
        else:
            self._client = httpx.Client(
                timeout=timeout,
                trust_env=False,
                transport=transport,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                    "Referer": "https://fund.eastmoney.com/",
                },
            )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch_nav_history(self, otc_code: str) -> OTCNavFetchResult:
        url = self.URL_TEMPLATE.format(otc_code=otc_code)
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OTCNavProviderError(f"HTTP error fetching pingzhongdata for {otc_code}: {exc}") from exc
        return parse_pingzhongdata(otc_code, resp.text)

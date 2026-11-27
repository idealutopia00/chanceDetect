"""Dual-source daily K-line fetchers (Tencent primary + Sina fallback) with automatic failover."""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from datetime import date, timedelta
from typing import Any

import httpx

from chancedetect.data.models import RawBar


class KlineProviderError(RuntimeError):
    """Raised when a K-line provider fails to fetch valid data."""


class BaseKlineProvider(ABC):
    name: str

    @abstractmethod
    def fetch_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        *,
        is_benchmark: bool = False,
    ) -> list[RawBar]:
        """Fetch forward-adjusted daily bars (or unadjusted for benchmarks) in [start_date, end_date]."""


class TencentKlineProvider(BaseKlineProvider):
    """Primary K-line source: Tencent Finance HTTPS proxy (returns qfq OHLCV + amount in 万元)."""

    name = "tencent"
    BASE_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
    MAX_DAYS_PER_CHUNK = 600  # Tencent caps qfqday at ~640 trading days (~2.5 yrs)

    def __init__(self, timeout: float = 12.0, retries: int = 2) -> None:
        self.timeout = timeout
        self.retries = retries
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=15),
        )

    def close(self) -> None:
        self._client.close()

    def fetch_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        *,
        is_benchmark: bool = False,
    ) -> list[RawBar]:
        # Chunk into ~2-year calendar windows (~480-500 trading days) so each stays under 640-bar cap
        all_bars: dict[date, RawBar] = {}
        chunk_end = end_date
        mode = "" if is_benchmark else "qfq"

        while chunk_end >= start_date:
            chunk_start = max(start_date, chunk_end - timedelta(days=760))
            chunk_bars = self._fetch_chunk(symbol, chunk_start, chunk_end, mode=mode)
            for b in chunk_bars:
                if start_date <= b.trade_date <= end_date:
                    all_bars[b.trade_date] = b
            # If the fund wasn't listed yet before chunk_start, stop paginating further back
            if not chunk_bars:
                break
            earliest_got = min(b.trade_date for b in chunk_bars)
            if earliest_got > chunk_start + timedelta(days=45):
                # Reached fund listing inception
                break
            chunk_end = chunk_start - timedelta(days=1)

        return [all_bars[d] for d in sorted(all_bars.keys())]

    def _fetch_chunk(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        *,
        mode: str = "qfq",
    ) -> list[RawBar]:
        param = f"{symbol},day,{start_date.isoformat()},{end_date.isoformat()},{self.MAX_DAYS_PER_CHUNK},{mode}"
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self._client.get(self.BASE_URL, params={"param": param})
                resp.raise_for_status()
                payload = resp.json()
                return self._parse_payload(symbol, payload)
            except Exception as exc:
                last_err = exc
                if attempt < self.retries:
                    time.sleep(0.4 * (attempt + 1))
        raise KlineProviderError(f"Tencent failed for {symbol}: {last_err}") from last_err

    @classmethod
    def _parse_payload(cls, symbol: str, payload: dict[str, Any]) -> list[RawBar]:
        data_root = payload.get("data")
        if not isinstance(data_root, dict) or symbol not in data_root:
            return []
        sym_obj = data_root[symbol]
        if not isinstance(sym_obj, dict):
            return []
        rows = sym_obj.get("qfqday") or sym_obj.get("day") or []
        bars: list[RawBar] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                continue
            try:
                dt = date.fromisoformat(str(row[0])[:10])
                open_p = float(row[1])
                close_p = float(row[2])
                high_p = float(row[3])
                low_p = float(row[4])
                vol = float(row[5])
                turnover = 0.0
                amount_wan = 0.0
                if len(row) >= 8 and row[7] not in (None, "", {}):
                    try:
                        turnover = float(row[7])
                    except (TypeError, ValueError):
                        turnover = 0.0
                if len(row) >= 9 and row[8] not in (None, "", {}):
                    try:
                        amount_wan = float(row[8])
                    except (TypeError, ValueError):
                        amount_wan = 0.0
                if amount_wan <= 0 and vol > 0 and close_p > 0:
                    # Fallback estimate: volume in lots (100 shares) * close / 10000 -> 万元
                    amount_wan = round(vol * 100.0 * close_p / 10000.0, 2)
                bars.append(
                    RawBar(
                        symbol=symbol,
                        trade_date=dt,
                        open=open_p,
                        high=high_p,
                        low=low_p,
                        close=close_p,
                        volume=vol,
                        amount=amount_wan,
                        turnover_pct=turnover,
                        source=cls.name,
                    )
                )
            except (TypeError, ValueError):
                continue
        return bars


class SinaKlineProvider(BaseKlineProvider):
    """Backup K-line source: Sina Finance JSON daily K-line + forward adjustment factors."""

    name = "sina"
    KLINE_URL = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "CN_MarketData.getKLineData"
    )
    QFQ_URL_TEMPLATE = "https://finance.sina.com.cn/realstock/company/{symbol}/qfq.js"

    def __init__(self, timeout: float = 12.0, retries: int = 2) -> None:
        self.timeout = timeout
        self.retries = retries
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "Referer": "https://finance.sina.com.cn/",
            },
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    def close(self) -> None:
        self._client.close()

    def fetch_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        *,
        is_benchmark: bool = False,
    ) -> list[RawBar]:
        approx_days = max(60, min(2000, int((end_date - start_date).days * 0.8) + 40))
        params = {
            "symbol": symbol,
            "scale": "240",
            "ma": "no",
            "datalen": str(approx_days),
        }
        last_err: Exception | None = None
        raw_items: list[dict[str, Any]] = []
        for attempt in range(self.retries + 1):
            try:
                resp = self._client.get(self.KLINE_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list):
                    raw_items = data
                    break
                raise KlineProviderError(f"Unexpected Sina payload type: {type(data)}")
            except Exception as exc:
                last_err = exc
                if attempt < self.retries:
                    time.sleep(0.5 * (attempt + 1))
        else:
            raise KlineProviderError(f"Sina failed for {symbol}: {last_err}") from last_err

        factors = [] if is_benchmark else self._fetch_qfq_factors(symbol)
        return self._parse_and_adjust(symbol, raw_items, factors, start_date, end_date)

    def _fetch_qfq_factors(self, symbol: str) -> list[tuple[date, float, float]]:
        """Fetch Sina qfq adjustment schedule: list of (ex_date, scale_s, offset_u) descending."""
        try:
            resp = self._client.get(self.QFQ_URL_TEMPLATE.format(symbol=symbol))
            if resp.status_code != 200:
                return []
            m = re.search(r"=\s*(\{.*\})", resp.text, re.S)
            if not m:
                return []
            obj = json.loads(m.group(1))
            out: list[tuple[date, float, float]] = []
            for item in obj.get("data", []):
                dt = date.fromisoformat(item["d"])
                s = float(item.get("s", 1.0))
                u = float(item.get("u", 0.0))
                out.append((dt, s, u))
            out.sort(key=lambda x: x[0], reverse=True)
            return out
        except Exception:
            return []

    @classmethod
    def _apply_qfq(
        cls, price: float, bar_date: date, factors: list[tuple[date, float, float]]
    ) -> float:
        """Apply Sina forward adjustment factors (descending by ex_date)."""
        if not factors:
            return price
        # Sina qfq stores cumulative factor entries effective from each date backwards:
        # For a bar strictly before ex_date, use the factor from the earliest ex_date > bar_date.
        # Actually Sina's qfq.js stores per-period (s, u) where prices on dates >= d use that entry's (s, u).
        chosen_s, chosen_u = 1.0, 0.0
        for ex_dt, s, u in factors:
            if bar_date >= ex_dt:
                chosen_s, chosen_u = s, u
                break
        else:
            # Older than oldest entry -> use the oldest entry's factor
            _, chosen_s, chosen_u = factors[-1]
        adj = price * chosen_s + chosen_u
        return round(adj, 4) if adj > 0 else price

    @classmethod
    def _parse_and_adjust(
        cls,
        symbol: str,
        raw_items: list[dict[str, Any]],
        factors: list[tuple[date, float, float]],
        start_date: date,
        end_date: date,
    ) -> list[RawBar]:
        bars: list[RawBar] = []
        for item in raw_items:
            try:
                dt = date.fromisoformat(str(item["day"])[:10])
                if dt < start_date or dt > end_date:
                    continue
                raw_o = float(item["open"])
                raw_h = float(item["high"])
                raw_l = float(item["low"])
                raw_c = float(item["close"])
                vol_shares = float(item["volume"])  # Sina reports volume in shares
                o = cls._apply_qfq(raw_o, dt, factors)
                h = cls._apply_qfq(raw_h, dt, factors)
                lo = cls._apply_qfq(raw_l, dt, factors)
                c = cls._apply_qfq(raw_c, dt, factors)
                # Amount in 万元 based on unadjusted close * shares / 10000
                amount_wan = round(vol_shares * raw_c / 10000.0, 2)
                bars.append(
                    RawBar(
                        symbol=symbol,
                        trade_date=dt,
                        open=o,
                        high=max(h, o, c),
                        low=min(lo, o, c),
                        close=c,
                        volume=round(vol_shares / 100.0, 2),  # Normalize to lots (100 shares)
                        amount=amount_wan,
                        turnover_pct=0.0,
                        source=cls.name,
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        bars.sort(key=lambda b: b.trade_date)
        return bars


class FailoverKlineFetcher:
    """Orchestrates primary -> backup failover and tracks provider usage."""

    def __init__(
        self,
        primary: BaseKlineProvider | None = None,
        backup: BaseKlineProvider | None = None,
        timeout: float = 12.0,
    ) -> None:
        self.primary = primary or TencentKlineProvider(timeout=timeout)
        self.backup = backup or SinaKlineProvider(timeout=timeout)

    def close(self) -> None:
        for prov in (self.primary, self.backup):
            if hasattr(prov, "close"):
                prov.close()

    def fetch(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        *,
        is_benchmark: bool = False,
    ) -> tuple[list[RawBar], str, bool]:
        """Return (bars, provider_name, used_fallback)."""
        primary_err: Exception | None = None
        try:
            bars = self.primary.fetch_daily_bars(
                symbol, start_date, end_date, is_benchmark=is_benchmark
            )
            if bars:
                return bars, self.primary.name, False
        except Exception as exc:
            primary_err = exc

        try:
            bars = self.backup.fetch_daily_bars(
                symbol, start_date, end_date, is_benchmark=is_benchmark
            )
            if bars:
                return bars, self.backup.name, True
        except Exception as backup_exc:
            raise KlineProviderError(
                f"Both providers failed for {symbol}: primary({self.primary.name})={primary_err}; "
                f"backup({self.backup.name})={backup_exc}"
            ) from backup_exc

        if primary_err is not None:
            raise KlineProviderError(
                f"Primary failed ({primary_err}) and backup returned empty for {symbol}"
            ) from primary_err
        return [], self.primary.name, False

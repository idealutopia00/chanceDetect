"""Tiantian Fund metadata provider & strict On-Exchange (ETF/LOF) <-> OTC mapping builder."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

import httpx

from chancedetect.data.models import ExchangeFund, OTCFundMapping, ShareClass


@dataclass(frozen=True)
class CatalogEntry:
    code: str
    abbr: str
    name: str
    fund_type_raw: str


@dataclass
class FundDetail:
    code: str
    name: str
    fund_type_raw: str
    company_id: str = ""
    company_name: str = ""
    index_code: str = ""
    index_name: str = ""
    list_exchange: str = ""  # "1" -> sh, "2" -> sz, "" -> not listed
    is_exchange: bool = False
    establish_date: str = ""
    fund_scale: float | None = None  # 亿元
    purchase_status: str = ""
    redeem_status: str = ""
    fee_rate: str = ""


def infer_exchange(code: str, list_exchange: str = "") -> str:
    if list_exchange == "1":
        return "sh"
    if list_exchange == "2":
        return "sz"
    return "sh" if code.startswith(("50", "51", "52", "56", "58")) else "sz"


def parse_share_class(name: str) -> ShareClass:
    """Extract A/C/E/I share class from Chinese mutual fund short name."""
    cleaned = re.sub(r"\([^)]*\)|（[^）]*）", "", name).strip()
    cleaned = re.sub(r"人民币$", "", cleaned).strip()
    m = re.search(r"([ACEI])$", cleaned, re.IGNORECASE)
    if m:
        ch = m.group(1).upper()
        if ch in ("A", "C", "E", "I"):
            return ch  # type: ignore[return-value]
    return "OTHER"


def normalize_lof_base_name(name: str) -> str:
    """Normalize a LOF name to match A/C sibling shares of the exact same fund."""
    s = re.sub(r"（", "(", name)
    s = re.sub(r"）", ")", s)
    s = re.sub(r"\(QDII-LOF\)|\(LOF\)|\(QDII\)|\(FOF-LOF\)|\(FOF\)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"人民币$", "", s.strip())
    s = re.sub(r"[ACEI]$", "", s.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s+", "", s).strip()


def _name_similarity(a: str, b: str) -> float:
    """Simple character-set Jaccard similarity after removing boilerplate tokens."""
    stop = ("ETF", "联接", "发起式", "发起", "指数", "基金", "交易型", "开放式", "LOF", "QDII", "A", "C", "E", "I")
    sa, sb = a.upper(), b.upper()
    for token in stop:
        sa = sa.replace(token, "")
        sb = sb.replace(token, "")
    sa = re.sub(r"[()（）\s\-]", "", sa)
    sb = re.sub(r"[()（）\s\-]", "", sb)
    set_a, set_b = set(sa), set(sb)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def classify_catalog_entries(
    entries: list[CatalogEntry],
) -> tuple[list[CatalogEntry], list[CatalogEntry], list[CatalogEntry], list[CatalogEntry]]:
    """Split raw catalog into (etf_candidates, feeder_candidates, lof_exchange_candidates, lof_otc_siblings)."""
    etfs: list[CatalogEntry] = []
    feeders: list[CatalogEntry] = []
    lofs_exchange: list[CatalogEntry] = []
    lofs_siblings: list[CatalogEntry] = []

    for entry in entries:
        name = entry.name
        upper = name.upper()
        if "后端" in name or "美元" in name or "现汇" in name or "现钞" in name or "港币" in name:
            continue
        if "联接" in name:
            feeders.append(entry)
            continue
        if "ETF" in upper and entry.code.startswith(("51", "52", "56", "58", "15")):
            etfs.append(entry)
            continue
        if "LOF" in upper:
            if entry.code.startswith(("16", "50")):
                lofs_exchange.append(entry)
            else:
                lofs_siblings.append(entry)
    return etfs, feeders, lofs_exchange, lofs_siblings


def build_strict_mappings(
    etf_details: list[FundDetail],
    feeder_details: list[FundDetail],
    lof_exchange_details: list[FundDetail],
    lof_sibling_details: list[FundDetail],
) -> tuple[list[ExchangeFund], list[OTCFundMapping]]:
    """Build strict 1-to-many mappings between on-exchange ETFs/LOFs and OTC funds.

    Rules:
    1. ETF <-> ETF Feeder:
       - Must match on (company_id, index_code) where both are non-empty.
       - If multiple ETFs share (company_id, index_code), pick best name similarity
         (respecting '增强' flag strictly so enhanced vs standard never cross-map).
       - Only ETFs with >= 1 matched feeder are included.
    2. LOF <-> OTC:
       - Listed LOFs (list_exchange in {'1','2'} or 16/50 prefix) whose purchase_status != '场内交易'
         map to themselves as LOF_SELF.
       - Sibling OTC LOF shares (same company_id + same normalized base name) map as LOF_SIBLING.
    """
    exchange_funds: dict[str, ExchangeFund] = {}
    mappings_map: dict[tuple[str, str], OTCFundMapping] = {}

    # 1. Index ETFs by (company_id, index_code)
    etf_by_key: dict[tuple[str, str], list[FundDetail]] = {}
    for etf in etf_details:
        if not etf.company_id or not etf.index_code or etf.index_code == "--":
            continue
        etf_by_key.setdefault((etf.company_id, etf.index_code), []).append(etf)

    # Match each feeder to its parent ETF
    for feeder in feeder_details:
        if not feeder.company_id or not feeder.index_code or feeder.index_code == "--":
            continue
        candidates = etf_by_key.get((feeder.company_id, feeder.index_code), [])
        if not candidates:
            continue

        feeder_is_enhanced = "增强" in feeder.name
        filtered = [c for c in candidates if ("增强" in c.name) == feeder_is_enhanced]
        pool = filtered if filtered else candidates

        if len(pool) == 1:
            target = pool[0]
        else:
            # Disambiguate by name similarity, then larger fund scale
            target = max(
                pool,
                key=lambda c: (_name_similarity(c.name, feeder.name), c.fund_scale or 0.0),
            )

        exch = infer_exchange(target.code, target.list_exchange)
        symbol = f"{exch}{target.code}"

        if symbol not in exchange_funds:
            exchange_funds[symbol] = ExchangeFund(
                symbol=symbol,
                code=target.code,
                exchange=exch,  # type: ignore[arg-type]
                name=target.name,
                fund_type="ETF",
                index_code=target.index_code,
                index_name=target.index_name,
                company_id=target.company_id,
                company_name=target.company_name,
                establish_date=target.establish_date,
                fund_scale=target.fund_scale,
            )

        mappings_map[(symbol, feeder.code)] = OTCFundMapping(
            exchange_symbol=symbol,
            otc_code=feeder.code,
            otc_name=feeder.name,
            share_class=parse_share_class(feeder.name),
            mapping_type="FEEDER",
            purchase_status=feeder.purchase_status,
            fee_rate=feeder.fee_rate,
        )

    # 2. LOFs supporting OTC subscription/redemption
    lof_by_company_base: dict[tuple[str, str], str] = {}
    for lof in lof_exchange_details:
        # Must be listed on exchange AND support OTC subscription/redemption
        is_listed = lof.list_exchange in ("1", "2") or lof.is_exchange
        if not is_listed:
            continue
        if lof.purchase_status in ("场内交易", ""):
            continue
        exch = infer_exchange(lof.code, lof.list_exchange)
        symbol = f"{exch}{lof.code}"

        exchange_funds[symbol] = ExchangeFund(
            symbol=symbol,
            code=lof.code,
            exchange=exch,  # type: ignore[arg-type]
            name=lof.name,
            fund_type="LOF",
            index_code=lof.index_code if lof.index_code != "--" else "",
            index_name=lof.index_name if lof.index_name != "--" else "",
            company_id=lof.company_id,
            company_name=lof.company_name,
            establish_date=lof.establish_date,
            fund_scale=lof.fund_scale,
        )
        mappings_map[(symbol, lof.code)] = OTCFundMapping(
            exchange_symbol=symbol,
            otc_code=lof.code,
            otc_name=lof.name,
            share_class=parse_share_class(lof.name),
            mapping_type="LOF_SELF",
            purchase_status=lof.purchase_status,
            fee_rate=lof.fee_rate,
        )
        if lof.company_id:
            base = normalize_lof_base_name(lof.name)
            lof_by_company_base[(lof.company_id, base)] = symbol

    # Link OTC LOF sibling shares (e.g., C class of a listed LOF)
    for sib in lof_sibling_details:
        if not sib.company_id:
            continue
        base = normalize_lof_base_name(sib.name)
        parent_symbol = lof_by_company_base.get((sib.company_id, base))
        if not parent_symbol:
            continue
        mappings_map[(parent_symbol, sib.code)] = OTCFundMapping(
            exchange_symbol=parent_symbol,
            otc_code=sib.code,
            otc_name=sib.name,
            share_class=parse_share_class(sib.name),
            mapping_type="LOF_SIBLING",
            purchase_status=sib.purchase_status,
            fee_rate=sib.fee_rate,
        )

    return list(exchange_funds.values()), list(mappings_map.values())


class TiantianMetadataProvider:
    """Fetches fund catalog & basic info from Tiantian Fund public endpoints."""

    CATALOG_URL = "https://fund.eastmoney.com/js/fundcode_search.js"
    DETAIL_URL = "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNBasicInformation"

    def __init__(self, timeout: float = 12.0, concurrency: int = 16, retries: int = 2) -> None:
        self.timeout = timeout
        self.concurrency = concurrency
        self.retries = retries
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "Referer": "https://fund.eastmoney.com/",
            },
            limits=httpx.Limits(max_connections=concurrency + 10, max_keepalive_connections=concurrency),
        )

    def close(self) -> None:
        self._client.close()

    def fetch_catalog(self) -> list[CatalogEntry]:
        resp = self._client.get(self.CATALOG_URL)
        resp.raise_for_status()
        m = re.search(r"var\s+r\s*=\s*(\[.*\]);", resp.text, re.S)
        if not m:
            raise RuntimeError("Failed to parse fundcode_search.js catalog")
        raw = json.loads(m.group(1))
        return [
            CatalogEntry(
                code=str(item[0]).strip(),
                abbr=str(item[1]).strip(),
                name=str(item[2]).strip(),
                fund_type_raw=str(item[3]).strip(),
            )
            for item in raw
            if isinstance(item, list) and len(item) >= 4
        ]

    def fetch_fund_detail(self, entry: CatalogEntry) -> FundDetail | None:
        params = {
            "FCODE": entry.code,
            "deviceid": "Wap",
            "plat": "Wap",
            "product": "EFund",
            "version": "2.0.0",
        }
        for attempt in range(self.retries + 1):
            try:
                resp = self._client.get(self.DETAIL_URL, params=params)
                resp.raise_for_status()
                payload = resp.json()
                d = payload.get("Datas")
                if not isinstance(d, dict) or not d.get("FCODE"):
                    return None
                return self._parse_detail(entry, d)
            except Exception:
                if attempt < self.retries:
                    time.sleep(0.35 * (attempt + 1))
        return None

    @staticmethod
    def _parse_detail(entry: CatalogEntry, d: dict[str, Any]) -> FundDetail:
        scale_yi: float | None = None
        raw_nav = d.get("ENDNAV")
        if raw_nav not in (None, "", "--"):
            try:
                scale_yi = round(float(raw_nav) / 1e8, 3)
            except (TypeError, ValueError):
                scale_yi = None

        index_code = str(d.get("INDEXCODE") or "").strip()
        index_name = str(d.get("INDEXNAME") or "").strip()
        if index_code == "--":
            index_code = ""
        if index_name == "--":
            index_name = ""

        return FundDetail(
            code=entry.code,
            name=str(d.get("SHORTNAME") or entry.name).strip(),
            fund_type_raw=str(d.get("FTYPE") or entry.fund_type_raw).strip(),
            company_id=str(d.get("JJGSID") or "").strip(),
            company_name=str(d.get("JJGS") or "").strip(),
            index_code=index_code,
            index_name=index_name,
            list_exchange=str(d.get("LISTTEXCH") or "").strip(),
            is_exchange=str(d.get("ISEXCHG") or "0") == "1",
            establish_date=str(d.get("ESTABDATE") or "").strip(),
            fund_scale=scale_yi,
            purchase_status=str(d.get("SGZT") or "").strip(),
            redeem_status=str(d.get("SHZT") or "").strip(),
            fee_rate=str(d.get("RATE") or "").strip(),
        )

    def fetch_strict_universe(
        self,
        *,
        sample_limit: int | None = None,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> tuple[list[ExchangeFund], list[OTCFundMapping], list[tuple[str, str]]]:
        """Fetch full catalog, hydrate details concurrently, and build strict mappings.

        Returns (exchange_funds, otc_mappings, failed_codes_with_reason).
        """
        catalog = self.fetch_catalog()
        etfs, feeders, lofs_ex, lofs_sib = classify_catalog_entries(catalog)

        if sample_limit is not None and sample_limit > 0:
            # Keep a deterministic subset for fast smoke tests (prioritize well-known core codes first)
            etfs = etfs[:sample_limit]
            feeders = feeders[: sample_limit * 2]
            lofs_ex = lofs_ex[: max(20, sample_limit // 3)]
            lofs_sib = lofs_sib[: max(20, sample_limit // 3)]

        all_entries = [
            ("etf", e) for e in etfs
        ] + [
            ("feeder", e) for e in feeders
        ] + [
            ("lof_ex", e) for e in lofs_ex
        ] + [
            ("lof_sib", e) for e in lofs_sib
        ]

        buckets: dict[str, list[FundDetail]] = {
            "etf": [],
            "feeder": [],
            "lof_ex": [],
            "lof_sib": [],
        }
        failures: list[tuple[str, str]] = []
        total = len(all_entries)
        done = 0

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            future_map = {
                pool.submit(self.fetch_fund_detail, entry): (bucket, entry.code)
                for bucket, entry in all_entries
            }
            for fut in as_completed(future_map):
                bucket, code = future_map[fut]
                done += 1
                try:
                    detail = fut.result()
                    if detail is not None:
                        buckets[bucket].append(detail)
                    else:
                        failures.append((code, "empty_detail_response"))
                except Exception as exc:
                    failures.append((code, str(exc)))
                if progress_cb and (done % 100 == 0 or done == total):
                    progress_cb(done, total)

        funds, mappings = build_strict_mappings(
            buckets["etf"],
            buckets["feeder"],
            buckets["lof_ex"],
            buckets["lof_sib"],
        )
        return funds, mappings, failures

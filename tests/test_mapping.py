"""Tests for strict On-Exchange <-> Off-Exchange fund mapping rules."""

from __future__ import annotations

from chancedetect.data.metadata_provider import (
    CatalogEntry,
    FundDetail,
    build_strict_mappings,
    classify_catalog_entries,
    parse_share_class,
)


def test_parse_share_class() -> None:
    assert parse_share_class("华夏沪深300ETF联接A") == "A"
    assert parse_share_class("招商中证白酒指数(LOF)C") == "C"
    assert parse_share_class("广发纳斯达克100ETF联接(QDII)人民币C") == "C"
    assert parse_share_class("易方达沪深300ETF联接E") == "E"
    assert parse_share_class("南方积极配置混合(LOF)") == "OTHER"


def test_classify_catalog_entries() -> None:
    entries = [
        CatalogEntry("510300", "HS300", "沪深300ETF华泰柏瑞", "指数型-股票"),
        CatalogEntry("000051", "HX300", "华夏沪深300ETF联接A", "指数型-股票"),
        CatalogEntry("000055", "GFND", "广发纳斯达克100ETF联接美元(QDII)A", "指数型-海外股票"),
        CatalogEntry("161725", "ZSBJ", "招商中证白酒指数(LOF)A", "指数型-股票"),
        CatalogEntry("012414", "ZSBJC", "招商中证白酒指数(LOF)C", "指数型-股票"),
        CatalogEntry("160119", "NF500", "南方中证500ETF联接(LOF)A", "指数型-股票"),
    ]
    etfs, feeders, lofs_ex, lofs_sib = classify_catalog_entries(entries)
    assert [e.code for e in etfs] == ["510300"]
    # USD feeder filtered out; LOF-feeder 160119 goes to feeders bucket!
    assert {e.code for e in feeders} == {"000051", "160119"}
    assert [e.code for e in lofs_ex] == ["161725"]
    assert [e.code for e in lofs_sib] == ["012414"]


def test_build_strict_mappings_etf_disambiguation_and_lof() -> None:
    etfs = [
        # Standard ETF
        FundDetail(
            code="510330",
            name="沪深300ETF华夏",
            fund_type_raw="指数型-股票",
            company_id="C1",
            company_name="华夏基金",
            index_code="000300",
            index_name="沪深300指数",
            list_exchange="1",
            is_exchange=True,
            fund_scale=1500.0,
        ),
        # Enhanced ETF from same company on same index (must not cross-map to standard feeder)
        FundDetail(
            code="561990",
            name="沪深300增强ETF华夏",
            fund_type_raw="指数型-股票",
            company_id="C1",
            company_name="华夏基金",
            index_code="000300",
            index_name="沪深300指数",
            list_exchange="1",
            is_exchange=True,
            fund_scale=10.0,
        ),
        # ETF without any feeder -> MUST be excluded!
        FundDetail(
            code="515999",
            name="无联接ETF",
            fund_type_raw="指数型-股票",
            company_id="C9",
            company_name="某基金",
            index_code="999999",
            index_name="某小众指数",
            list_exchange="1",
            is_exchange=True,
        ),
    ]
    feeders = [
        FundDetail(
            code="000051",
            name="华夏沪深300ETF联接A",
            fund_type_raw="指数型-股票",
            company_id="C1",
            company_name="华夏基金",
            index_code="000300",
            index_name="沪深300指数",
            purchase_status="开放申购",
            fee_rate="0.12%",
        ),
        FundDetail(
            code="005658",
            name="华夏沪深300ETF联接C",
            fund_type_raw="指数型-股票",
            company_id="C1",
            company_name="华夏基金",
            index_code="000300",
            index_name="沪深300指数",
            purchase_status="开放申购",
            fee_rate="0.00%",
        ),
    ]
    lofs_ex = [
        FundDetail(
            code="161725",
            name="招商中证白酒指数(LOF)A",
            fund_type_raw="指数型-股票",
            company_id="C2",
            company_name="招商基金",
            index_code="399997",
            index_name="中证白酒指数",
            list_exchange="2",
            is_exchange=True,
            purchase_status="限大额",
            fee_rate="0.10%",
        ),
        # Exchange-only LOF (no OTC purchase) -> excluded
        FundDetail(
            code="169999",
            name="纯场内封闭LOF",
            fund_type_raw="混合型",
            company_id="C3",
            list_exchange="2",
            is_exchange=True,
            purchase_status="场内交易",
        ),
    ]
    lofs_sib = [
        FundDetail(
            code="012414",
            name="招商中证白酒指数(LOF)C",
            fund_type_raw="指数型-股票",
            company_id="C2",
            company_name="招商基金",
            purchase_status="限大额",
            fee_rate="0.00%",
        )
    ]

    funds, mappings = build_strict_mappings(etfs, feeders, lofs_ex, lofs_sib)
    symbols = {f.symbol for f in funds}
    assert symbols == {"sh510330", "sz161725"}
    # 515999 (no feeder), 561990 (enhanced, no enhanced feeder), 169999 (no OTC) are all excluded!

    by_ex: dict[str, set[str]] = {}
    for m in mappings:
        by_ex.setdefault(m.exchange_symbol, set()).add(m.otc_code)
    assert by_ex["sh510330"] == {"000051", "005658"}
    assert by_ex["sz161725"] == {"161725", "012414"}

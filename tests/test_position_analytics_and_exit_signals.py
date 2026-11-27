"""Integration tests for Issue #10: Dual-Curve Review, Proxy K-Line, Post-Exit Rearview, Exit Signals & CLI."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import polars as pl
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from chancedetect.api.app import create_app
from chancedetect.cli import app as cli_app
from chancedetect.config import Settings
from chancedetect.data.models import ExchangeFund, OTCFundMapping
from chancedetect.data.otc_nav_provider import OTCNavProvider
from chancedetect.positions.service import PositionService
from chancedetect.storage.bar_store import BarStore
from chancedetect.storage.sqlite_store import SQLiteStore
from chancedetect.strategies.indicators import compute_base_features

SH_TZ = ZoneInfo("Asia/Shanghai")


def _sh_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 0, 0, tzinfo=SH_TZ).timestamp() * 1000)


def _weekdays(start: date, count: int) -> list[date]:
    days: list[date] = []
    cur = start
    while len(days) < count:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def _build_pingzhongdata_js(
    code: str,
    name: str,
    rows: list[tuple[date, float, float | None, float | None, str]],
) -> str:
    nw_parts: list[str] = []
    ac_parts: list[str] = []
    for dt, unit_nav, acc_nav, ret, note in rows:
        ms = _sh_ms(dt)
        ret_str = "null" if ret is None else f"{ret:.4f}"
        nw_parts.append(
            f'{{"x":{ms},"y":{unit_nav:.4f},"equityReturn":{ret_str},"unitMoney":"{note}"}}'
        )
        ac_str = "null" if acc_nav is None else f"{acc_nav:.4f}"
        ac_parts.append(f"[{ms},{ac_str}]")
    return (
        f'var fS_name = "{name}";\n'
        f'var fS_code = "{code}";\n'
        f"var Data_netWorthTrend = [{','.join(nw_parts)}];\n"
        f"var Data_ACWorthTrend = [{','.join(ac_parts)}];\n"
    )


class _FakePingzhongTransport:
    def __init__(self, payloads: dict[str, str]) -> None:
        self.payloads = dict(payloads)

    def build_transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            code = request.url.path.rsplit("/", 1)[-1].removesuffix(".js")
            payload = self.payloads.get(code)
            if payload is None:
                return httpx.Response(404, text="not found")
            return httpx.Response(200, text=payload)

        return httpx.MockTransport(handler)


def _seed_proxy_universe(
    sqlite: SQLiteStore,
    bars: BarStore,
    *,
    dates: list[date],
    series_by_symbol: dict[str, list[float]],
) -> None:
    funds = [
        ExchangeFund(
            symbol="sh510300",
            code="510300",
            exchange="sh",
            name="华泰柏瑞沪深300ETF",
            fund_type="ETF",
            index_code="000300",
            index_name="沪深300",
            company_name="华泰柏瑞",
            establish_date="2012-05-04",
            avg_amount_20d=500000.0,
        ),
        ExchangeFund(
            symbol="sz159995",
            code="159995",
            exchange="sz",
            name="华夏国证半导体芯片ETF",
            fund_type="ETF",
            index_code="980017",
            index_name="国证芯片",
            company_name="华夏基金",
            establish_date="2020-01-20",
            avg_amount_20d=200000.0,
        ),
        ExchangeFund(
            symbol="sh516310",
            code="516310",
            exchange="sh",
            name="易方达中证银行ETF",
            fund_type="ETF",
            index_code="399986",
            index_name="中证银行",
            company_name="易方达",
            establish_date="2021-05-20",
            avg_amount_20d=120000.0,
        ),
    ]
    mappings = [
        OTCFundMapping(
            exchange_symbol="sh510300",
            otc_code="006131",
            otc_name="华泰柏瑞沪深300ETF联接C",
            share_class="C",
            mapping_type="FEEDER",
        ),
        OTCFundMapping(
            exchange_symbol="sz159995",
            otc_code="008888",
            otc_name="华夏国证半导体芯片ETF联接C",
            share_class="C",
            mapping_type="FEEDER",
        ),
        OTCFundMapping(
            exchange_symbol="sh516310",
            otc_code="011222",
            otc_name="易方达中证银行ETF联接C",
            share_class="C",
            mapping_type="FEEDER",
        ),
    ]
    sqlite.replace_universe(funds, mappings, force=True)

    rows: list[dict[str, Any]] = []
    for sym, closes in series_by_symbol.items():
        for dt, c in zip(dates, closes, strict=True):
            rows.append(
                {
                    "symbol": sym,
                    "trade_date": dt,
                    "open": round(c * 0.998, 4),
                    "high": round(c * 1.006, 4),
                    "low": round(c * 0.994, 4),
                    "close": round(c, 4),
                    "volume": 1000000.0,
                    "amount": 300000.0,
                    "turnover_pct": 1.2,
                    "pct_chg": 0.0,
                    "source": "synthetic",
                }
            )
    bars_df = pl.DataFrame(rows)
    bars.upsert_daily_bars(bars_df)

    bench_rows = [
        {
            "benchmark_code": "sh000300",
            "trade_date": dt,
            "open": 4000.0,
            "high": 4020.0,
            "low": 3980.0,
            "close": 4000.0 + idx * 1.0,
            "volume": 1e8,
            "amount": 1e11,
            "pct_chg": 0.0,
        }
        for idx, dt in enumerate(dates)
    ]
    bars.upsert_benchmarks(pl.DataFrame(bench_rows))
    feat_df = compute_base_features(bars.load_daily_bars(), bars.load_benchmarks(["sh000300"]))
    bars.save_features(feat_df)


def test_dual_curve_dividend_continuity_breakeven_markers_and_post_exit_rearview(
    tmp_settings: Settings,
) -> None:
    """Verify normalized dual curve:
    - Anchored at 0% on opened_date
    - Ex-dividend payout (unit_nav drops while equityReturn is positive) causes ZERO cliff
    - Tracks linked proxy qfq cumulative return & tracking spread
    - Exposes buy/add/partial_sell/full_sell markers and weighted-average breakeven line
    - Closed position continues plotting post-exit days with post_exit_stats & proxy_bars
    """
    # 70 trading days so MA20/MA60 & 25+ post-exit days exist
    dates = _weekdays(date(2026, 7, 1), 70)
    # Proxy sh510300 rises steadily from 4.00 -> 4.80
    proxy_closes = [4.00 + i * 0.01 for i in range(70)]
    # Open on day 35, add on day 38, partial sell on day 41, full sell on day 44 -> 25 post-exit days!
    d_open = dates[35]
    d_div = dates[37]
    d_add = dates[38]
    d_part = dates[41]
    d_close = dates[44]

    # Craft OTC NAV series starting from day 30:
    # day 35 (open): unit_nav = 1.0000, ret = 0.0% -> curve 0.00%
    # day 36: unit_nav = 1.0500, ret = +5.0% -> curve +5.00%
    # day 37 (ex-dividend!): unit_nav drops from 1.05 -> 0.8580 due to 0.20 dividend,
    #         but dividend-adjusted equityReturn = +1.0%!
    #         Compounded curve MUST be (1.05 * 1.01 - 1)*100 = +6.05% (NO artificial -18% cliff!)
    # day 38 (add): unit_nav = 1.2000 (manual/step), let's keep unit_nav consistent:
    # Actually let's use exact simple numbers:
    otc_rows: list[tuple[date, float, float | None, float | None, str]] = []
    unit_nav = 1.00
    for idx in range(30, 70):
        dt = dates[idx]
        if idx < 35:
            otc_rows.append((dt, 0.98, 0.98, 0.20, ""))
        elif idx == 35:
            unit_nav = 1.0000
            otc_rows.append((dt, unit_nav, 1.0000, 0.00, ""))
        elif idx == 36:
            unit_nav = 1.0500
            otc_rows.append((dt, unit_nav, 1.0500, 5.00, ""))
        elif idx == 37:
            # Ex-dividend payout of 0.20 per share, actual daily return +1.00%
            unit_nav = 0.8605
            otc_rows.append((dt, unit_nav, 1.2605, 1.00, "每份派现金0.2000元"))
        elif idx == 38:
            # +2.00% day -> compounded factor = 1.05 * 1.01 * 1.02 = 1.08171 (+8.17%)
            unit_nav = 0.8777
            otc_rows.append((dt, unit_nav, 1.2777, 2.00, ""))
        elif idx <= 44:
            # +1.00% per day through close date
            unit_nav = round(unit_nav * 1.01, 4)
            otc_rows.append((dt, unit_nav, round(unit_nav + 0.40, 4), 1.00, ""))
        elif idx <= 54:
            # Post-exit rally: +1.5% per day for 10 days (sold too early!)
            unit_nav = round(unit_nav * 1.015, 4)
            otc_rows.append((dt, unit_nav, round(unit_nav + 0.40, 4), 1.50, ""))
        else:
            # Post-exit pullback: -0.5% per day
            unit_nav = round(unit_nav * 0.995, 4)
            otc_rows.append((dt, unit_nav, round(unit_nav + 0.40, 4), -0.50, ""))

    fake = _FakePingzhongTransport(
        {"006131": _build_pingzhongdata_js("006131", "华泰柏瑞沪深300ETF联接C", otc_rows)}
    )
    app = create_app(
        tmp_settings,
        otc_nav_provider=OTCNavProvider(transport=fake.build_transport()),
        today_fn=lambda: dates[-1],
    )
    _seed_proxy_universe(
        app.state.sqlite_store,
        app.state.bar_store,
        dates=dates,
        series_by_symbol={"sh510300": proxy_closes},
    )

    with TestClient(app) as client:
        # Open buy on day 35
        r_open = client.post(
            "/api/v1/positions",
            json={"otc_code": "006131", "trade_date": d_open.isoformat(), "amount": 1000.0},
        )
        assert r_open.status_code == 201
        pos_id = r_open.json()["position"]["id"]

        # Scale-in buy on day 38
        r_add = client.post(
            f"/api/v1/positions/{pos_id}/lots",
            json={"side": "buy", "trade_date": d_add.isoformat(), "amount": 1000.0},
        )
        assert r_add.status_code == 201

        # Partial sell on day 41
        r_part = client.post(
            f"/api/v1/positions/{pos_id}/lots",
            json={"side": "sell", "trade_date": d_part.isoformat(), "sell_ratio": 0.5},
        )
        assert r_part.status_code == 201

        # Full sell on day 44 -> archives position
        r_close = client.post(
            f"/api/v1/positions/{pos_id}/lots",
            json={"side": "sell", "trade_date": d_close.isoformat(), "sell_ratio": 1.0},
        )
        assert r_close.status_code == 201
        assert r_close.json()["position"]["status"] == "closed"

        # Fetch full detail with post_exit_days=20
        r_det = client.get(f"/api/v1/positions/{pos_id}?post_exit_days=20")
        assert r_det.status_code == 200
        pos = r_det.json()["position"]

        dc = pos["dual_curve"]
        assert dc["has_proxy_curve"] is True
        assert dc["is_pending_baseline"] is False
        assert dc["baseline_date"] == d_open.isoformat()

        by_date = {p["date"]: p for p in dc["points"]}
        # Baseline date anchored strictly at 0.0%
        assert by_date[d_open.isoformat()]["otc_return_pct"] == 0.0
        assert by_date[d_open.isoformat()]["proxy_return_pct"] == 0.0
        assert by_date[d_open.isoformat()]["spread_pct"] == 0.0

        # Ex-dividend date MUST reflect compounded dividend-adjusted return (+6.05%), NOT -13.95% drop!
        assert abs(by_date[d_div.isoformat()]["otc_return_pct"] - 6.05) < 0.05
        assert by_date[d_div.isoformat()]["is_post_exit"] is False

        # Proxy return on day 44: (4.44 / 4.35 - 1) * 100 = +2.07%
        expected_proxy_ret = round((proxy_closes[44] / proxy_closes[35] - 1.0) * 100.0, 2)
        assert abs(by_date[d_close.isoformat()]["proxy_return_pct"] - expected_proxy_ret) < 0.02
        assert abs(
            by_date[d_close.isoformat()]["spread_pct"]
            - (by_date[d_close.isoformat()]["otc_return_pct"] - by_date[d_close.isoformat()]["proxy_return_pct"])
        ) < 0.02

        # Markers cover all 4 kinds: buy, add, partial_sell, full_sell
        marker_kinds = [m["kind"] for m in dc["markers"]]
        assert marker_kinds == ["buy", "add", "partial_sell", "full_sell"]

        # Breakeven line is computed
        assert dc["breakeven_return_pct"] is not None

        # Post-exit continuation capped at 20 trading days
        post_exit_points = [p for p in dc["points"] if p["is_post_exit"]]
        assert len(post_exit_points) == 20

        # Post-exit rearview stats show meaningful gain after exit
        pe = pos["post_exit_stats"]
        assert pe is not None
        assert pe["days_observed"] == 20
        assert pe["post_exit_return_20d_pct"] is not None
        assert pe["post_exit_max_gain_pct"] > 10.0

        # Proxy K-line + MA bars included with pre-entry lookback
        proxy_bars = pos["proxy_bars"]
        assert len(proxy_bars) >= 50
        assert proxy_bars[0]["time"] < d_open.isoformat()
        assert any(b["ma20"] is not None for b in proxy_bars)

        # Closed positions do not trigger active exit signals
        assert pos["exit_signals"] == []


def test_exit_signals_all_five_rules_custom_thresholds_and_unmapped_fallback(
    tmp_settings: Settings,
) -> None:
    """Verify all 5 explainable Exit Signals end-to-end:
    1. ma20_break: 1-day close < ma20 -> warning; 2+ consecutive days -> alert
    2. trailing_drawdown: default -6% & custom override (e.g. -3%)
    3. cost_stop_loss: default -8% & custom override (e.g. -4%)
    4. overheated_stage (proxy Trend Stage == overheated) + custom take_profit_reached
    5. short_term_fee_guard (< 7 calendar days -> warning; >= 7 days -> ok)
    6. Unmapped 6-digit OTC fund degrades cleanly to single-curve + non-proxy exit signals
    7. Portfolio summary reports exit_alert_position_count and exit_alert_count
    """
    dates = _weekdays(date(2026, 8, 3), 75)
    today = dates[-1]

    # Series A (sh510300): flat around 4.00, then last 2 days drop sharply below MA20 -> 2-day alert!
    closes_ma20_2d = [4.00] * 73 + [3.70, 3.65]
    # Series B (sz159995): flat around 2.00, ONLY last 1 day dips below MA20 -> 1-day warning!
    closes_ma20_1d = [2.00] * 74 + [1.88]
    # Series C (sh516310): low-vol steady rally accelerating sharply at end -> Overheated Trend Stage!
    closes_overheated = [1.00 + i * 0.002 for i in range(60)] + [
        1.12 + j * 0.015 for j in range(15)
    ]

    # OTC 006131 (linked to sh510300): bought 12 days ago @ 1.00 -> peaked at 1.15 -> now 0.90
    # -> drawdown from 1.15 to 0.90 = -21.74% (<= -6% alert!), cost return = -10.0% (<= -8% alert!),
    # -> holding >= 7 calendar days -> fee guard ok!
    open_a = dates[-10]  # ~14 calendar days ago
    otc_006131 = [
        (dates[-12], 0.99, 0.99, 0.0, ""),
        (open_a, 1.00, 1.00, 1.01, ""),
        (dates[-8], 1.15, 1.15, 15.0, ""),
        (dates[-5], 1.02, 1.02, -11.3043, ""),
        (dates[-2], 0.94, 0.94, -7.8431, ""),
        (today, 0.90, 0.90, -4.2553, ""),
    ]

    # OTC 008888 (linked to sz159995): bought 2 trading days ago (< 7 cal days) @ 1.00 -> peaked 1.04 -> now 1.00
    # -> drawdown = -3.85% (OK under default -6%, ALERT when custom override = 3.0%!)
    # -> cost return = 0.0% (OK), holding < 7 days -> short_term_fee_guard warning!
    open_b = dates[-3]
    otc_008888 = [
        (open_b, 1.00, 1.00, 0.0, ""),
        (dates[-2], 1.04, 1.04, 4.0, ""),
        (today, 1.00, 1.00, -3.8462, ""),
    ]

    # OTC 011222 (linked to sh516310): bought 10 trading days ago @ 1.00 -> now 1.14 (+14%)
    # -> proxy is Overheated -> overheated_stage warning!
    # -> custom take_profit_pct = 12.0 -> take_profit_reached warning!
    open_c = dates[-10]
    otc_011222 = [
        (open_c, 1.00, 1.00, 0.0, ""),
        (dates[-5], 1.08, 1.08, 8.0, ""),
        (today, 1.14, 1.14, 5.5556, ""),
    ]

    # Unmapped 6-digit OTC 019999 (NO proxy): bought 10 trading days ago @ 1.00 -> now 0.91 (-9% stop loss!)
    otc_019999 = [
        (open_c, 1.00, 1.00, 0.0, ""),
        (dates[-5], 1.02, 1.02, 2.0, ""),
        (today, 0.91, 0.91, -10.7843, ""),
    ]

    fake = _FakePingzhongTransport(
        {
            "006131": _build_pingzhongdata_js("006131", "华泰柏瑞沪深300ETF联接C", otc_006131),
            "008888": _build_pingzhongdata_js("008888", "华夏国证半导体芯片ETF联接C", otc_008888),
            "011222": _build_pingzhongdata_js("011222", "易方达中证银行ETF联接C", otc_011222),
            "019999": _build_pingzhongdata_js("019999", "银河创新成长混合A", otc_019999),
        }
    )
    app = create_app(
        tmp_settings,
        otc_nav_provider=OTCNavProvider(transport=fake.build_transport()),
        today_fn=lambda: today,
    )
    _seed_proxy_universe(
        app.state.sqlite_store,
        app.state.bar_store,
        dates=dates,
        series_by_symbol={
            "sh510300": closes_ma20_2d,
            "sz159995": closes_ma20_1d,
            "sh516310": closes_overheated,
        },
    )

    with TestClient(app) as client:
        # 1. Position A: 2-day MA20 break + trailing drawdown + cost stop-loss + fee-free OK
        p_a = client.post(
            "/api/v1/positions",
            json={"otc_code": "006131", "trade_date": open_a.isoformat(), "amount": 1000.0},
        ).json()["position"]
        sigs_a = {s["rule_id"]: s for s in p_a["exit_signals"]}
        assert sigs_a["ma20_break"]["severity"] == "alert"
        assert sigs_a["ma20_break"]["triggered"] is True
        assert sigs_a["trailing_drawdown"]["severity"] == "alert"
        assert sigs_a["trailing_drawdown"]["triggered"] is True
        assert sigs_a["cost_stop_loss"]["severity"] == "alert"
        assert sigs_a["cost_stop_loss"]["triggered"] is True
        assert sigs_a["short_term_fee_guard"]["severity"] == "ok"
        assert sigs_a["short_term_fee_guard"]["triggered"] is False
        assert p_a["exit_alert_count"] == 3

        # 2. Position B: 1-day MA20 watch (warning), < 7d fee guard (warning), drawdown OK under default -6%
        p_b = client.post(
            "/api/v1/positions",
            json={"otc_code": "008888", "trade_date": open_b.isoformat()},
        ).json()["position"]
        sigs_b = {s["rule_id"]: s for s in p_b["exit_signals"]}
        assert sigs_b["ma20_break"]["severity"] == "warning"
        assert sigs_b["ma20_break"]["triggered"] is True
        assert sigs_b["short_term_fee_guard"]["severity"] == "warning"
        assert sigs_b["short_term_fee_guard"]["triggered"] is True
        assert sigs_b["trailing_drawdown"]["triggered"] is False

        # Override trailing_drawdown to 3.0% via PATCH -> drawdown (-3.85%) now triggers alert!
        p_b_upd = client.patch(
            f"/api/v1/positions/{p_b['id']}",
            json={"custom_trailing_drawdown_pct": 3.0},
        ).json()["position"]
        sigs_b_upd = {s["rule_id"]: s for s in p_b_upd["exit_signals"]}
        assert sigs_b_upd["trailing_drawdown"]["severity"] == "alert"
        assert sigs_b_upd["trailing_drawdown"]["triggered"] is True

        # Clear custom threshold back to null -> returns to default -6% (OK again)
        p_b_reset = client.patch(
            f"/api/v1/positions/{p_b['id']}",
            json={"custom_trailing_drawdown_pct": None},
        ).json()["position"]
        sigs_b_reset = {s["rule_id"]: s for s in p_b_reset["exit_signals"]}
        assert sigs_b_reset["trailing_drawdown"]["triggered"] is False

        # 3. Position C: Overheated proxy + custom take-profit reached (severity='alert')
        p_c = client.post(
            "/api/v1/positions",
            json={
                "otc_code": "011222",
                "trade_date": open_c.isoformat(),
                "custom_take_profit_pct": 12.0,
            },
        ).json()["position"]
        sigs_c = {s["rule_id"]: s for s in p_c["exit_signals"]}
        assert sigs_c["overheated_stage"]["severity"] == "warning"
        assert sigs_c["overheated_stage"]["triggered"] is True
        assert sigs_c["take_profit_reached"]["severity"] == "alert"
        assert sigs_c["take_profit_reached"]["triggered"] is True

        # 4. Unmapped Position D: NO proxy -> single-curve fallback + non-proxy rules only
        p_d = client.post(
            "/api/v1/positions",
            json={"otc_code": "019999", "trade_date": open_c.isoformat()},
        ).json()["position"]
        assert p_d["exchange_symbol"] is None
        sigs_d = {s["rule_id"]: s for s in p_d["exit_signals"]}
        assert "ma20_break" not in sigs_d
        assert "overheated_stage" not in sigs_d
        assert sigs_d["cost_stop_loss"]["severity"] == "alert"
        assert sigs_d["cost_stop_loss"]["triggered"] is True
        assert sigs_d["trailing_drawdown"]["severity"] == "alert"

        det_d = client.get(f"/api/v1/positions/{p_d['id']}").json()["position"]
        assert det_d["dual_curve"]["has_proxy_curve"] is False
        assert det_d["proxy_bars"] == []
        assert len(det_d["dual_curve"]["points"]) >= 3

        # 5. Portfolio summary counts alert positions & alerts cleanly
        listing = client.get("/api/v1/positions?status=active").json()
        summary = listing["portfolio_summary"]
        # Positions A (3 alerts), C (1 take-profit alert), and D (2 alerts) have severity=='alert'
        assert summary["exit_alert_position_count"] == 3
        assert summary["exit_alert_count"] == 6


def test_review_regressions_pending_scale_in_fifo_lot_edit_rederive_and_closed_sync_window(
    tmp_settings: Settings,
) -> None:
    """Regression coverage for code-review findings:
    1. Adding a same-day Pending NAV scale-in lot on top of an older fee-free confirmed lot
       must keep all_fee_free=False (short_term_fee_guard triggered) and include pending capital.
    2. Editing a confirmed lot's `nav` or `amount` re-derives `shares` cleanly without stale cost.
    3. `sync-nav` skips archived positions closed well outside the post-exit review window (>70d).
    """
    today = date(2026, 11, 20)
    old_open = date(2026, 6, 1)
    old_close = date(2026, 6, 15)  # > 150 calendar days ago -> outside 70d post-exit sync window
    confirmed_open = date(2026, 11, 5)  # 15 calendar days ago -> fee-free

    otc_006131 = [
        (confirmed_open, 1.0000, 1.0000, 0.0, ""),
        (date(2026, 11, 19), 1.1000, 1.1000, 10.0, ""),
        # Note: 2026-11-20 (today) NOT yet published -> Pending NAV!
    ]
    otc_008888 = [
        (old_open, 1.0000, 1.0000, 0.0, ""),
        (old_close, 1.0500, 1.0500, 5.0, ""),
    ]

    requested_codes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        code = request.url.path.rsplit("/", 1)[-1].removesuffix(".js")
        requested_codes.append(code)
        payloads = {
            "006131": _build_pingzhongdata_js("006131", "华泰柏瑞沪深300ETF联接C", otc_006131),
            "008888": _build_pingzhongdata_js("008888", "华夏国证半导体芯片ETF联接C", otc_008888),
        }
        body = payloads.get(code)
        return httpx.Response(200, text=body) if body else httpx.Response(404, text="not found")

    app = create_app(
        tmp_settings,
        otc_nav_provider=OTCNavProvider(transport=httpx.MockTransport(handler)),
        today_fn=lambda: today,
    )

    with TestClient(app) as client:
        # 1. Create confirmed position (>7d old) -> initially all_fee_free=True
        p1 = client.post(
            "/api/v1/positions",
            json={"otc_code": "006131", "trade_date": confirmed_open.isoformat(), "amount": 1000.0},
        ).json()["position"]
        assert p1["fifo_fee_status"]["all_fee_free"] is True
        lot1_id = p1["lots"][0]["id"]
        assert abs(p1["lots"][0]["shares"] - 1000.0) < 0.01

        # Edit confirmed lot's manual NAV to 2.00 without touching amount -> shares must re-derive to 500!
        upd = client.patch(
            f"/api/v1/positions/{p1['id']}/lots/{lot1_id}",
            json={"nav": 2.0},
        )
        assert upd.status_code == 200
        assert abs(upd.json()["lot"]["shares"] - 500.0) < 0.01
        assert abs(upd.json()["position"]["avg_cost_nav"] - 2.0) < 0.01

        # Restore back to confirmed official NAV (1.00) -> shares re-derives back to 1000
        upd2 = client.patch(
            f"/api/v1/positions/{p1['id']}/lots/{lot1_id}",
            json={"nav": None},
        )
        assert upd2.status_code == 200
        assert abs(upd2.json()["lot"]["shares"] - 1000.0) < 0.01

        # Add same-day Pending NAV scale-in (1000 RMB on today) -> FIFO must mark penalized (<7d)
        # and invested_capital must include pending_buy_amount (1000 + 1000 = 2000)
        add_res = client.post(
            f"/api/v1/positions/{p1['id']}/lots",
            json={"side": "buy", "trade_date": today.isoformat(), "amount": 1000.0},
        )
        assert add_res.status_code == 201
        p1_after = add_res.json()["position"]
        assert p1_after["fifo_fee_status"]["all_fee_free"] is False
        assert p1_after["fifo_fee_status"]["penalized_ratio"] > 0.0
        assert p1_after["invested_capital"] == 2000.0
        sigs = {s["rule_id"]: s for s in p1_after["exit_signals"]}
        assert sigs["short_term_fee_guard"]["triggered"] is True

        # 2. Create & close an old archived position (>70 days ago)
        p_old = client.post(
            "/api/v1/positions",
            json={"otc_code": "008888", "trade_date": old_open.isoformat()},
        ).json()["position"]
        client.post(
            f"/api/v1/positions/{p_old['id']}/lots",
            json={"side": "sell", "trade_date": old_close.isoformat(), "sell_ratio": 1.0},
        )

        # Trigger sync-nav -> should ONLY fetch 006131 (active), NOT 008888 (closed >70d ago)!
        requested_codes.clear()
        sync_res = client.post("/api/v1/positions/sync-nav", json={})
        assert sync_res.status_code == 200
        assert requested_codes == ["006131"]


def test_cli_positions_command_renders_active_and_all_positions(
    tmp_settings: Settings, monkeypatch: Any
) -> None:
    """Verify `chancedetect positions` and `chancedetect positions --all` via CliRunner."""
    dates = _weekdays(date(2026, 9, 1), 40)
    today = dates[-1]
    proxy_closes = [4.00] * 38 + [3.60, 3.55]

    otc_006131 = [
        (dates[-10], 1.00, 1.00, 0.0, ""),
        (dates[-5], 1.05, 1.05, 5.0, ""),
        (today, 0.90, 0.90, -14.2857, ""),
    ]
    otc_008888 = [
        (dates[-15], 1.00, 1.00, 0.0, ""),
        (dates[-10], 1.12, 1.12, 12.0, ""),
        (today, 1.15, 1.15, 2.6786, ""),
    ]

    fake = _FakePingzhongTransport(
        {
            "006131": _build_pingzhongdata_js("006131", "华泰柏瑞沪深300ETF联接C", otc_006131),
            "008888": _build_pingzhongdata_js("008888", "华夏国证半导体芯片ETF联接C", otc_008888),
        }
    )
    sqlite = SQLiteStore(tmp_settings.sqlite_path)
    bars = BarStore(
        tmp_settings.daily_bars_path,
        tmp_settings.benchmarks_path,
        tmp_settings.features_path,
    )
    _seed_proxy_universe(
        sqlite,
        bars,
        dates=dates,
        series_by_symbol={"sh510300": proxy_closes, "sz159995": [2.00] * 40},
    )
    provider = OTCNavProvider(transport=fake.build_transport())
    pos_svc = PositionService(sqlite, bars, provider, today_fn=lambda: today)

    # Active position with alerts
    pos_svc.create_position(otc_code="006131", trade_date=dates[-10].isoformat(), amount=1000.0)
    # Closed position
    closed_pos = pos_svc.create_position(otc_code="008888", trade_date=dates[-15].isoformat())
    pos_svc.add_trade_lot(
        closed_pos["id"],
        side="sell",
        trade_date=dates[-10].isoformat(),
        sell_ratio=1.0,
    )

    import chancedetect.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_init_position_service", lambda: pos_svc)

    runner = CliRunner()
    res_active = runner.invoke(cli_app, ["positions"])
    assert res_active.exit_code == 0, res_active.output
    assert "006131" in res_active.output
    assert "008888" not in res_active.output

    res_all = runner.invoke(cli_app, ["positions", "--all"])
    assert res_all.exit_code == 0, res_all.output
    assert "006131" in res_all.output
    assert "008888" in res_all.output

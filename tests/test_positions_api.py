"""Integration tests for Issue #7: OTC position ledger, Pending NAV, dual-cadence sync, lookup & CLI."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.test_engine_and_replay import _seed_market
from typer.testing import CliRunner

from chancedetect.api.app import create_app
from chancedetect.cli import app as cli_app
from chancedetect.config import Settings
from chancedetect.data.otc_nav_provider import OTCNavProvider
from chancedetect.positions.service import PositionService
from chancedetect.scheduler.jobs import AppScheduler

SH_TZ = ZoneInfo("Asia/Shanghai")


def _sh_ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, 0, 0, tzinfo=SH_TZ).timestamp() * 1000)


def _build_pingzhongdata_js(
    code: str,
    name: str,
    rows: list[tuple[tuple[int, int, int], float, float | None, float | None, str]],
) -> str:
    """Build realistic Eastmoney pingzhongdata JS payload.

    rows: list of ((Y, M, D), unit_nav, accumulated_nav, daily_return_pct, dividend_note)
    """
    nw_parts: list[str] = []
    ac_parts: list[str] = []
    for (y, m, d), unit_nav, acc_nav, ret, note in rows:
        ms = _sh_ms(y, m, d)
        ret_str = "null" if ret is None else f"{ret:.2f}"
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


class _MutablePingzhongTransport:
    """Fake external Eastmoney HTTP transport allowing tests to publish new NAV dates on cue."""

    def __init__(self, payloads: dict[str, str | Exception]) -> None:
        self.payloads = dict(payloads)
        self.requested_codes: list[str] = []

    def build_transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            code = request.url.path.rsplit("/", 1)[-1].removesuffix(".js")
            self.requested_codes.append(code)
            payload = self.payloads.get(code)
            if payload is None:
                return httpx.Response(404, text="not found")
            if isinstance(payload, Exception):
                raise payload
            return httpx.Response(200, text=payload)

        return httpx.MockTransport(handler)


def _make_app_with_fake_nav(
    tmp_settings: Settings,
    fake_transport: _MutablePingzhongTransport,
    *,
    today: date = date(2026, 11, 20),
) -> Any:
    provider = OTCNavProvider(transport=fake_transport.build_transport())
    app = create_app(tmp_settings, otc_nav_provider=provider, today_fn=lambda: today)
    _seed_market(app.state.sqlite_store, app.state.bar_store)
    return app


def test_sqlite_schema_idempotent_and_enforces_single_active_position(tmp_settings: Settings) -> None:
    """Schema creation is idempotent on restart and prevents two active positions for the same otc_code."""
    fake = _MutablePingzhongTransport(
        {
            "006131": _build_pingzhongdata_js(
                "006131",
                "华泰柏瑞沪深300ETF联接C",
                [((2026, 11, 19), 1.2500, 1.4500, 0.50, "")],
            )
        }
    )
    app = _make_app_with_fake_nav(tmp_settings, fake, today=date(2026, 11, 20))
    # Re-initializing SQLiteStore on same path must be idempotent and keep existing tables intact
    app.state.sqlite_store._init_schema()

    with TestClient(app) as client:
        r1 = client.post(
            "/api/v1/positions",
            json={"otc_code": "006131", "trade_date": "2026-11-19", "amount": 1000.0},
        )
        assert r1.status_code == 201
        pos1 = r1.json()["position"]
        assert pos1["otc_code"] == "006131"
        assert pos1["otc_name"] == "华泰柏瑞沪深300ETF联接C"
        assert pos1["exchange_symbol"] == "sh510300"
        assert pos1["status"] == "active"

        # Second active position for same otc_code must be rejected with 409
        r2 = client.post(
            "/api/v1/positions",
            json={"otc_code": "006131", "trade_date": "2026-11-20"},
        )
        assert r2.status_code == 409


def test_buy_confirmed_vs_pending_nav_and_sync_nav_backfill_preserves_manual(
    tmp_settings: Settings,
) -> None:
    """End-to-end Pending NAV lifecycle:
    1. Historical date with published NAV -> immediately confirmed + derives shares from amount/fee.
    2. Same-day buy before NAV published -> saved as pending (nav=None).
    3. Manual NAV override lot -> saved as manual and NEVER overwritten by sync-nav.
    4. sync-nav fetches strictly tracked otc_codes (ignores untracked mappings), backfills pending -> confirmed,
       and derives missing shares/amount.
    """
    fake = _MutablePingzhongTransport(
        {
            "006131": _build_pingzhongdata_js(
                "006131",
                "华泰柏瑞沪深300ETF联接C",
                [
                    ((2026, 11, 18), 1.2000, 1.4000, 0.50, ""),
                    ((2026, 11, 19), 1.2500, 1.4500, 4.17, ""),
                ],
            ),
            "000051": _build_pingzhongdata_js(
                "000051",
                "华夏沪深300ETF联接A",
                [((2026, 11, 19), 1.5000, 1.9000, 0.20, "")],
            ),
            # Arbitrary unmapped 6-digit fund
            "019999": _build_pingzhongdata_js(
                "019999",
                "银河创新成长混合A",
                [((2026, 11, 19), 2.0000, 2.5000, 1.00, "")],
            ),
        }
    )
    app = _make_app_with_fake_nav(tmp_settings, fake, today=date(2026, 11, 20))

    with TestClient(app) as client:
        # 1. Buy 006131 on 2026-11-19 (NAV already exists) -> confirmed immediately
        r_hist = client.post(
            "/api/v1/positions",
            json={
                "otc_code": "006131",
                "trade_date": "2026-11-19",
                "amount": 1005.0,
                "fee": 5.0,
                "entry_signal_snapshot": {
                    "strategy_names": ["均线多头排列"],
                    "trend_stage": "early",
                    "template_name": "右侧主线买点",
                    "signal_date": "2026-11-18",
                },
            },
        )
        assert r_hist.status_code == 201
        pos_006131 = r_hist.json()["position"]
        assert pos_006131["exchange_symbol"] == "sh510300"
        assert pos_006131["entry_signal_snapshot"]["trend_stage"] == "early"
        lot_hist = pos_006131["lots"][0]
        assert lot_hist["nav_status"] == "confirmed"
        assert lot_hist["nav"] == pytest.approx(1.25)
        assert lot_hist["shares"] == pytest.approx(800.0)  # (1005 - 5) / 1.25

        # 2. Scale-in on today (2026-11-20, default trade_date omitted) before NAV published -> pending
        r_pending = client.post(
            f"/api/v1/positions/{pos_006131['id']}/lots",
            json={"side": "buy", "amount": 2000.0, "fee": 0.0, "notes": "盘中加仓"},
        )
        assert r_pending.status_code == 201
        lot_pending = r_pending.json()["lot"]
        assert lot_pending["trade_date"] == "2026-11-20"
        assert lot_pending["nav_status"] == "pending"
        assert lot_pending["nav"] is None
        assert lot_pending["shares"] is None
        assert r_pending.json()["position"]["has_pending_nav"] is True
        assert r_pending.json()["position"]["pending_lot_count"] == 1

        # 3. Create position for arbitrary unmapped 6-digit fund 019999 with manual NAV override
        r_manual = client.post(
            "/api/v1/positions",
            json={
                "otc_code": "019999",
                "trade_date": "2026-11-20",
                "nav": 2.1234,
                "shares": 500.0,
            },
        )
        assert r_manual.status_code == 201
        pos_manual = r_manual.json()["position"]
        assert pos_manual["otc_name"] == "银河创新成长混合A"
        assert pos_manual["exchange_symbol"] is None
        lot_manual = pos_manual["lots"][0]
        assert lot_manual["nav_status"] == "manual"
        assert lot_manual["nav"] == pytest.approx(2.1234)
        assert lot_manual["amount"] == pytest.approx(1061.70)

        # 4. Publish 2026-11-20 official NAVs upstream and trigger sync-nav
        fake.requested_codes.clear()
        fake.payloads["006131"] = _build_pingzhongdata_js(
            "006131",
            "华泰柏瑞沪深300ETF联接C",
            [
                ((2026, 11, 18), 1.2000, 1.4000, 0.50, ""),
                ((2026, 11, 19), 1.2500, 1.4500, 4.17, ""),
                ((2026, 11, 20), 1.2800, 1.4800, 2.40, ""),
            ],
        )
        fake.payloads["019999"] = _build_pingzhongdata_js(
            "019999",
            "银河创新成长混合A",
            [
                ((2026, 11, 19), 2.0000, 2.5000, 1.00, ""),
                ((2026, 11, 20), 2.0500, 2.5500, 2.50, ""),
            ],
        )

        r_sync = client.post("/api/v1/positions/sync-nav", json={})
        assert r_sync.status_code == 200
        sync_res = r_sync.json()
        assert sync_res["refreshed_funds"] == 2
        assert sync_res["backfilled_lots"] == 1
        assert sync_res["errors"] == []
        # Strictly tracked funds only — untracked mapped fund 000051 MUST NOT be fetched!
        assert set(fake.requested_codes) == {"006131", "019999"}
        assert "000051" not in fake.requested_codes

        # Verify pending lot on 006131 is now confirmed with derived shares
        r_detail_1 = client.get(f"/api/v1/positions/{pos_006131['id']}")
        assert r_detail_1.status_code == 200
        d1 = r_detail_1.json()["position"]
        assert d1["has_pending_nav"] is False
        assert d1["latest_nav"] == pytest.approx(1.28)
        assert d1["latest_nav_date"] == "2026-11-20"
        backfilled = next(lot for lot in d1["lots"] if lot["id"] == lot_pending["id"])
        assert backfilled["nav_status"] == "confirmed"
        assert backfilled["nav"] == pytest.approx(1.28)
        assert backfilled["shares"] == pytest.approx(2000.0 / 1.28, abs=1e-3)

        # Verify manual lot on 019999 was NOT overwritten by official 2.0500
        r_detail_2 = client.get(f"/api/v1/positions/{pos_manual['id']}")
        assert r_detail_2.status_code == 200
        d2 = r_detail_2.json()["position"]
        assert d2["latest_nav"] == pytest.approx(2.05)
        assert d2["lots"][0]["nav_status"] == "manual"
        assert d2["lots"][0]["nav"] == pytest.approx(2.1234)


def test_position_and_lot_crud_proxy_override_and_cascade_delete(tmp_settings: Settings) -> None:
    fake = _MutablePingzhongTransport(
        {
            "006131": _build_pingzhongdata_js(
                "006131",
                "华泰柏瑞沪深300ETF联接C",
                [
                    ((2026, 11, 18), 1.2000, 1.4000, 0.50, ""),
                    ((2026, 11, 19), 1.2500, 1.4500, 4.17, ""),
                ],
            )
        }
    )
    app = _make_app_with_fake_nav(tmp_settings, fake, today=date(2026, 11, 20))

    with TestClient(app) as client:
        # Explicitly clear exchange_symbol on create (single-curve mode)
        r_create = client.post(
            "/api/v1/positions",
            json={
                "otc_code": "006131",
                "exchange_symbol": None,
                "trade_date": "2026-11-19",
                "notes": "初始建仓",
            },
        )
        assert r_create.status_code == 201
        pos = r_create.json()["position"]
        assert pos["exchange_symbol"] is None
        lot_id = pos["lots"][0]["id"]

        # Patch position: attach proxy + custom thresholds + notes
        r_patch_pos = client.patch(
            f"/api/v1/positions/{pos['id']}",
            json={
                "exchange_symbol": "sh510300",
                "notes": "更新复盘备注",
                "custom_stop_loss_pct": -6.5,
                "custom_trailing_drawdown_pct": -5.0,
                "custom_take_profit_pct": 15.0,
            },
        )
        assert r_patch_pos.status_code == 200
        patched = r_patch_pos.json()["position"]
        assert patched["exchange_symbol"] == "sh510300"
        assert patched["proxy_fund"]["symbol"] == "sh510300"
        assert patched["notes"] == "更新复盘备注"
        assert patched["custom_stop_loss_pct"] == pytest.approx(-6.5)

        # Patch lot: override NAV manually + change date to 2026-11-18 -> updates opened_date
        r_patch_lot = client.patch(
            f"/api/v1/positions/{pos['id']}/lots/{lot_id}",
            json={"trade_date": "2026-11-18", "nav": 1.1999, "amount": 1200.0, "notes": "手工核对"},
        )
        assert r_patch_lot.status_code == 200
        assert r_patch_lot.json()["lot"]["nav_status"] == "manual"
        assert r_patch_lot.json()["lot"]["nav"] == pytest.approx(1.1999)
        assert r_patch_lot.json()["position"]["opened_date"] == "2026-11-18"

        # Add a second lot then delete it
        r_add = client.post(
            f"/api/v1/positions/{pos['id']}/lots",
            json={"side": "buy", "trade_date": "2026-11-19"},
        )
        assert r_add.status_code == 201
        lot2_id = r_add.json()["lot"]["id"]

        r_del_lot = client.delete(f"/api/v1/positions/{pos['id']}/lots/{lot2_id}")
        assert r_del_lot.status_code == 200
        assert len(r_del_lot.json()["position"]["lots"]) == 1

        # List positions filter
        r_list = client.get("/api/v1/positions", params={"status": "active"})
        assert r_list.status_code == 200
        assert len(r_list.json()["positions"]) == 1
        assert len(client.get("/api/v1/positions", params={"status": "closed"}).json()["positions"]) == 0
        assert len(client.get("/api/v1/positions", params={"status": "all"}).json()["positions"]) == 1

        # Delete position cascades lots
        r_del_pos = client.delete(f"/api/v1/positions/{pos['id']}")
        assert r_del_pos.status_code == 200
        assert client.get(f"/api/v1/positions/{pos['id']}").status_code == 404
        assert client.get("/api/v1/positions", params={"status": "all"}).json()["positions"] == []


def test_otc_lookup_mapped_and_arbitrary_6_digit_code(tmp_settings: Settings) -> None:
    fake = _MutablePingzhongTransport(
        {
            "019888": _build_pingzhongdata_js(
                "019888",
                "易方达蓝筹精选混合",
                [((2026, 11, 19), 1.8520, 2.1000, -0.35, "")],
            )
        }
    )
    app = _make_app_with_fake_nav(tmp_settings, fake, today=date(2026, 11, 20))

    with TestClient(app) as client:
        # 1. Search mapped OTC Counterparts by partial name/code
        r_mapped = client.get("/api/v1/otc/lookup", params={"q": "006131"})
        assert r_mapped.status_code == 200
        data = r_mapped.json()
        assert len(data["items"]) >= 1
        top = data["items"][0]
        assert top["otc_code"] == "006131"
        assert top["is_mapped"] is True
        assert top["suggested_proxy"]["symbol"] == "sh510300"

        # 2. Direct arbitrary 6-digit code outside otc_fund_mappings
        r_ext = client.get("/api/v1/otc/lookup", params={"q": "019888"})
        assert r_ext.status_code == 200
        ext_data = r_ext.json()
        assert ext_data["resolved_external"] is not None
        assert ext_data["resolved_external"]["otc_code"] == "019888"
        assert ext_data["resolved_external"]["otc_name"] == "易方达蓝筹精选混合"
        assert ext_data["resolved_external"]["is_mapped"] is False
        assert ext_data["resolved_external"]["latest_nav"] == pytest.approx(1.852)
        assert ext_data["items"][0]["otc_code"] == "019888"


def test_scheduler_registers_dual_cadence_otc_nav_jobs_and_startup_catchup(
    tmp_settings: Settings,
) -> None:
    """Scheduler must register weekday 21:30 & 09:00 CST OTC NAV jobs without altering 16:15 EOD job,
    and catch up stale pending lots on startup.
    """
    enabled_settings = tmp_settings.model_copy(update={"enable_scheduler": True})
    fake = _MutablePingzhongTransport(
        {
            "006131": _build_pingzhongdata_js(
                "006131",
                "华泰柏瑞沪深300ETF联接C",
                [((2026, 11, 18), 1.2000, 1.4000, 0.50, "")],
            )
        }
    )
    app = _make_app_with_fake_nav(enabled_settings, fake, today=date(2026, 11, 19))
    scheduler: AppScheduler = app.state.scheduler

    try:
        scheduler.start()
        jobs = {j.id: j for j in scheduler._scheduler.get_jobs()}
        assert "eod_daily_bars_sync" in jobs
        assert "weekly_metadata_sync" in jobs
        assert "otc_nav_evening_sync" in jobs
        assert "otc_nav_morning_sync" in jobs

        # Verify 16:15 EOD untouched & 21:30 / 09:00 CST configured
        eod_fields = {f.name: str(f) for f in jobs["eod_daily_bars_sync"].trigger.fields}
        assert eod_fields["hour"] == "16"
        assert eod_fields["minute"] == "15"
        assert eod_fields["day_of_week"] == "mon-fri"

        eve_fields = {f.name: str(f) for f in jobs["otc_nav_evening_sync"].trigger.fields}
        assert eve_fields["hour"] == "21"
        assert eve_fields["minute"] == "30"
        assert eve_fields["day_of_week"] == "mon-fri"

        morn_fields = {f.name: str(f) for f in jobs["otc_nav_morning_sync"].trigger.fields}
        assert morn_fields["hour"] == "9"
        assert morn_fields["minute"] == "0"
        assert morn_fields["day_of_week"] == "mon-fri"

        # Create a pending lot dated 2026-11-19 (before today 2026-11-20 for catchup check)
        pos_svc: PositionService = app.state.position_service
        pos = pos_svc.create_position(otc_code="006131", trade_date="2026-11-19", amount=1000.0)
        assert pos["lots"][0]["nav_status"] == "pending"

        # Publish 2026-11-19 NAV upstream and advance clock to 2026-11-20
        fake.payloads["006131"] = _build_pingzhongdata_js(
            "006131",
            "华泰柏瑞沪深300ETF联接C",
            [
                ((2026, 11, 18), 1.2000, 1.4000, 0.50, ""),
                ((2026, 11, 19), 1.2500, 1.4500, 4.17, ""),
            ],
        )
        pos_svc._today_fn = lambda: date(2026, 11, 20)

        caught_up = scheduler.check_and_catchup_otc_nav(now_sh=datetime(2026, 11, 20, 10, 0, tzinfo=SH_TZ))
        assert caught_up is True
        updated = pos_svc.get_position(pos["id"])
        assert updated is not None
        assert updated["lots"][0]["nav_status"] == "confirmed"
        assert updated["lots"][0]["nav"] == pytest.approx(1.25)
    finally:
        scheduler.shutdown()


def test_cli_sync_nav_command(tmp_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """CliRunner test verifying `chancedetect sync-nav` backfills Pending NAV lots and formats Rich output."""
    fake = _MutablePingzhongTransport(
        {
            "006131": _build_pingzhongdata_js(
                "006131",
                "华泰柏瑞沪深300ETF联接C",
                [((2026, 11, 19), 1.2500, 1.4500, 1.0, "")],
            )
        }
    )
    app = _make_app_with_fake_nav(tmp_settings, fake, today=date(2026, 11, 20))
    pos_svc: PositionService = app.state.position_service
    pos = pos_svc.create_position(otc_code="006131", trade_date="2026-11-20", amount=1000.0)
    assert pos["lots"][0]["nav_status"] == "pending"

    # Publish 2026-11-20 NAV upstream
    fake.payloads["006131"] = _build_pingzhongdata_js(
        "006131",
        "华泰柏瑞沪深300ETF联接C",
        [
            ((2026, 11, 19), 1.2500, 1.4500, 1.0, ""),
            ((2026, 11, 20), 1.2500, 1.4500, 0.0, ""),
        ],
    )

    monkeypatch.setattr("chancedetect.cli._init_position_service", lambda: pos_svc)

    runner = CliRunner()
    result = runner.invoke(cli_app, ["sync-nav"])
    assert result.exit_code == 0, result.output
    assert "1" in result.output  # refreshed_funds / backfilled_lots

    reloaded = pos_svc.get_position(pos["id"])
    assert reloaded is not None
    assert reloaded["lots"][0]["nav_status"] == "confirmed"
    assert reloaded["lots"][0]["shares"] == pytest.approx(800.0)


def test_sync_nav_partial_errors_single_code_filter_and_revert_manual_nav(
    tmp_settings: Settings,
) -> None:
    """Verify:
    - Pending lot exposes temporary reference_nav and linked proxy pct_chg before publication.
    - Targeted sync-nav by otc_code refreshes only that tracked fund and ignores untracked codes.
    - Partial upstream failure records error without aborting healthy tracked funds.
    - Backfilling pending lot with shares derives amount; clearing manual NAV (nav=None) reverts to official NAV.
    """
    fake = _MutablePingzhongTransport(
        {
            "006131": _build_pingzhongdata_js(
                "006131",
                "华泰柏瑞沪深300ETF联接C",
                [((2026, 11, 19), 1.2500, 1.4500, 0.80, "")],
            ),
            "019999": _build_pingzhongdata_js(
                "019999",
                "测试未映射基金A",
                [((2026, 11, 19), 2.0000, 2.2000, 0.50, "")],
            ),
        }
    )
    app = _make_app_with_fake_nav(tmp_settings, fake, today=date(2026, 11, 20))

    with TestClient(app) as client:
        # Pending lot with shares only (no amount)
        r1 = client.post(
            "/api/v1/positions",
            json={"otc_code": "006131", "trade_date": "2026-11-20", "shares": 400.0, "fee": 2.0},
        )
        assert r1.status_code == 201
        p1 = r1.json()["position"]
        lot1 = p1["lots"][0]
        assert lot1["nav_status"] == "pending"
        assert lot1["reference_nav"] == pytest.approx(1.25)
        assert lot1["reference_nav_date"] == "2026-11-19"
        assert lot1["reference_proxy_pct_chg"] is not None

        # Second tracked fund (will fail during global sync)
        r2 = client.post(
            "/api/v1/positions",
            json={"otc_code": "019999", "trade_date": "2026-11-19", "nav": 2.10},
        )
        assert r2.status_code == 201
        p2 = r2.json()["position"]
        lot2 = p2["lots"][0]
        assert lot2["nav_status"] == "manual"

        # Untracked code filter fetches nothing
        fake.requested_codes.clear()
        r_untracked = client.post("/api/v1/positions/sync-nav", json={"otc_code": "000051"})
        assert r_untracked.status_code == 200
        assert r_untracked.json()["refreshed_funds"] == 0
        assert fake.requested_codes == []

        # Publish 2026-11-20 for 006131, break 019999 upstream
        fake.payloads["006131"] = _build_pingzhongdata_js(
            "006131",
            "华泰柏瑞沪深300ETF联接C",
            [
                ((2026, 11, 19), 1.2500, 1.4500, 0.80, ""),
                ((2026, 11, 20), 1.3000, 1.5000, 4.00, ""),
            ],
        )
        fake.payloads["019999"] = "<html>upstream error</html>"

        r_sync = client.post("/api/v1/positions/sync-nav", json={})
        assert r_sync.status_code == 200
        sdata = r_sync.json()
        assert sdata["refreshed_funds"] == 1
        assert sdata["backfilled_lots"] == 1
        assert len(sdata["errors"]) == 1
        assert sdata["errors"][0]["otc_code"] == "019999"

        # Verify derived amount for shares-only lot: 400 * 1.30 + 2.0 = 522.0
        d1 = client.get(f"/api/v1/positions/{p1['id']}").json()["position"]
        assert d1["lots"][0]["nav_status"] == "confirmed"
        assert d1["lots"][0]["amount"] == pytest.approx(522.0)

        # Clearing manual NAV on p2's lot2 (nav=None) reverts to cached official NAV (2.0000)
        r_revert = client.patch(
            f"/api/v1/positions/{p2['id']}/lots/{lot2['id']}",
            json={"nav": None},
        )
        assert r_revert.status_code == 200
        assert r_revert.json()["lot"]["nav_status"] == "confirmed"
        assert r_revert.json()["lot"]["nav"] == pytest.approx(2.0)

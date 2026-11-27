"""Tests for Issue #9: Multi-lot lifecycle, weighted-average cost, percentage-only mode,
FIFO 7-day short-term redemption fee guard, and portfolio review summary.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.test_positions_api import (
    _build_pingzhongdata_js,
    _make_app_with_fake_nav,
    _MutablePingzhongTransport,
)

from chancedetect.config import Settings
from chancedetect.data.models import TradeLotRecord
from chancedetect.positions.accounting import (
    OversellError,
    evaluate_position_accounting,
)


def _lot(
    lot_id: int,
    side: str,
    trade_date: str,
    *,
    nav: float | None = 1.0,
    nav_status: str = "confirmed",
    amount: float | None = None,
    shares: float | None = None,
    sell_ratio: float | None = None,
    fee: float = 0.0,
) -> TradeLotRecord:
    return TradeLotRecord(
        id=lot_id,
        position_id=1,
        side=side,  # type: ignore[arg-type]
        trade_date=trade_date,
        nav=nav,
        nav_status=nav_status,  # type: ignore[arg-type]
        amount=amount,
        shares=shares,
        sell_ratio=sell_ratio,
        fee=fee,
        notes="",
        created_at="2026-11-01T10:00:00",
        updated_at="2026-11-01T10:00:00",
    )


# ----------------------------------------------------------------------
# Unit tests for pure deterministic accounting engine edge cases
# ----------------------------------------------------------------------


def test_accounting_same_day_buys_execute_before_sells_and_boundary_7_days() -> None:
    """On the same trade_date, buy lots execute before sell lots regardless of id order,
    and FIFO 7-day eligibility flips from penalized (6 days) to fee-free (7 days) at the exact boundary.
    """
    lots = [
        # Sell has smaller id than buy on same day -> must still execute AFTER buy
        _lot(1, "sell", "2026-11-10", nav=1.10, shares=400.0, amount=440.0),
        _lot(2, "buy", "2026-11-10", nav=1.00, shares=1000.0, amount=1000.0),
    ]

    # Day 6 (2026-11-16): 6 calendar days elapsed -> penalized
    res_day6 = evaluate_position_accounting(
        lots,
        latest_nav=1.20,
        eval_date=date(2026, 11, 16),
    )
    assert res_day6.remaining_shares == pytest.approx(600.0)
    assert res_day6.fifo_fee_status.fee_free_ratio == pytest.approx(0.0)
    assert res_day6.fifo_fee_status.penalized_ratio == pytest.approx(1.0)
    assert res_day6.fifo_fee_status.all_fee_free is False
    assert res_day6.fifo_fee_status.earliest_all_fee_free_date == "2026-11-17"
    assert res_day6.fifo_fee_status.min_holding_days == 6
    assert res_day6.fifo_fee_status.lots[0].days_until_fee_free == 1

    # Day 7 (2026-11-17): 7 calendar days elapsed -> fee-free
    res_day7 = evaluate_position_accounting(
        lots,
        latest_nav=1.20,
        eval_date=date(2026, 11, 17),
    )
    assert res_day7.fifo_fee_status.fee_free_ratio == pytest.approx(1.0)
    assert res_day7.fifo_fee_status.penalized_ratio == pytest.approx(0.0)
    assert res_day7.fifo_fee_status.all_fee_free is True
    assert res_day7.fifo_fee_status.earliest_all_fee_free_date is None
    assert res_day7.fifo_fee_status.min_holding_days == 7
    assert res_day7.fifo_fee_status.lots[0].days_until_fee_free == 0


def test_accounting_rejects_oversell() -> None:
    lots = [
        _lot(1, "buy", "2026-11-10", nav=1.00, shares=1000.0, amount=1000.0),
        _lot(2, "sell", "2026-11-15", nav=1.10, shares=1005.0, amount=1105.5),
    ]
    with pytest.raises(OversellError):
        evaluate_position_accounting(lots, latest_nav=1.10, eval_date=date(2026, 11, 20))


# ----------------------------------------------------------------------
# End-to-end FastAPI TestClient integration tests (worked examples 1-5)
# ----------------------------------------------------------------------


def test_capital_mode_multi_lot_weighted_avg_cost_and_full_exit(tmp_settings: Settings) -> None:
    """Worked Example 1:
    - Buy 1000 RMB @ 1.00 (1000 shares)
    - Add 1200 RMB @ 1.20 (1000 shares -> avg cost 1.10)
    - Sell 1000 shares @ 1.32 with 2 RMB fee -> realized P&L = 1320 - 2 - 1100 = +218.00
    - Remaining 1000 shares @ avg cost 1.10, latest NAV 1.43 -> unrealized P&L = +330.00 (+30.0%)
    - Full exit remaining 1000 shares @ 1.43 -> auto-closes with total P&L = +548.00
    """
    fake = _MutablePingzhongTransport(
        {
            "006131": _build_pingzhongdata_js(
                "006131",
                "华泰柏瑞沪深300ETF联接C",
                [
                    ((2026, 11, 10), 1.0000, 1.0000, 0.0, ""),
                    ((2026, 11, 12), 1.2000, 1.2000, 20.0, ""),
                    ((2026, 11, 18), 1.3200, 1.3200, 10.0, ""),
                    ((2026, 11, 20), 1.4300, 1.4300, 8.33, ""),
                ],
            )
        }
    )
    app = _make_app_with_fake_nav(tmp_settings, fake, today=date(2026, 11, 20))

    with TestClient(app) as client:
        # Initial buy: 1000 RMB @ 1.00
        r1 = client.post(
            "/api/v1/positions",
            json={"otc_code": "006131", "trade_date": "2026-11-10", "amount": 1000.0},
        )
        assert r1.status_code == 201
        pos = r1.json()["position"]
        assert pos["has_amount_data"] is True
        assert pos["avg_cost_nav"] == pytest.approx(1.0)
        assert pos["remaining_shares"] == pytest.approx(1000.0)
        assert pos["invested_capital"] == pytest.approx(1000.0)
        assert pos["unrealized_pnl"] == pytest.approx(430.0)
        assert pos["unrealized_return_pct"] == pytest.approx(43.0)

        # Scale-in: 1200 RMB @ 1.20 -> 2000 shares, avg cost 1.10
        r2 = client.post(
            f"/api/v1/positions/{pos['id']}/lots",
            json={"side": "buy", "trade_date": "2026-11-12", "amount": 1200.0},
        )
        assert r2.status_code == 201
        pos = r2.json()["position"]
        assert pos["remaining_shares"] == pytest.approx(2000.0)
        assert pos["invested_capital"] == pytest.approx(2200.0)
        assert pos["avg_cost_nav"] == pytest.approx(1.10)

        # Partial sell: 1000 shares @ 1.32 with 2 RMB fee
        r3 = client.post(
            f"/api/v1/positions/{pos['id']}/lots",
            json={
                "side": "sell",
                "trade_date": "2026-11-18",
                "shares": 1000.0,
                "fee": 2.0,
                "notes": "减仓锁定一半利润",
            },
        )
        assert r3.status_code == 201
        pos = r3.json()["position"]
        assert pos["status"] == "active"
        assert pos["closed_date"] is None
        assert pos["has_amount_data"] is True
        assert pos["remaining_shares"] == pytest.approx(1000.0)
        assert pos["remaining_ratio"] == pytest.approx(0.5)
        assert pos["invested_capital"] == pytest.approx(1100.0)
        assert pos["avg_cost_nav"] == pytest.approx(1.10)
        assert pos["total_bought_amount"] == pytest.approx(2200.0)
        assert pos["total_sold_amount"] == pytest.approx(1318.0)
        assert pos["total_fees"] == pytest.approx(2.0)
        assert pos["realized_pnl"] == pytest.approx(218.0)
        assert pos["realized_return_pct"] == pytest.approx(218.0 / 1100.0 * 100.0, abs=1e-2)
        assert pos["unrealized_pnl"] == pytest.approx(330.0)
        assert pos["unrealized_return_pct"] == pytest.approx(30.0)
        assert pos["total_pnl"] == pytest.approx(548.0)
        assert pos["total_return_pct"] == pytest.approx(548.0 / 2200.0 * 100.0, abs=1e-2)
        assert pos["holding_days"] == 10  # 2026-11-10 -> 2026-11-20

        # Attempt oversell (1500 shares > remaining 1000 shares) -> HTTP 400 and no mutation
        r_over = client.post(
            f"/api/v1/positions/{pos['id']}/lots",
            json={"side": "sell", "trade_date": "2026-11-20", "shares": 1500.0},
        )
        assert r_over.status_code == 400
        after_over = client.get(f"/api/v1/positions/{pos['id']}").json()["position"]
        assert len(after_over["lots"]) == 3
        assert after_over["remaining_shares"] == pytest.approx(1000.0)

        # Full exit remaining 1000 shares @ 1.43 -> auto-close
        r4 = client.post(
            f"/api/v1/positions/{pos['id']}/lots",
            json={"side": "sell", "trade_date": "2026-11-20", "shares": 1000.0},
        )
        assert r4.status_code == 201
        closed = r4.json()["position"]
        assert closed["status"] == "closed"
        assert closed["closed_date"] == "2026-11-20"
        assert closed["remaining_shares"] == pytest.approx(0.0)
        assert closed["remaining_ratio"] == pytest.approx(0.0)
        assert closed["invested_capital"] == pytest.approx(0.0)
        assert closed["avg_cost_nav"] == pytest.approx(1.10)
        assert closed["realized_pnl"] == pytest.approx(548.0)
        assert closed["unrealized_pnl"] == pytest.approx(0.0)
        assert closed["unrealized_return_pct"] is None
        assert closed["total_pnl"] == pytest.approx(548.0)
        assert closed["total_return_pct"] == pytest.approx(548.0 / 2200.0 * 100.0, abs=1e-2)


def test_percentage_only_mode_partial_sell_and_default_full_exit(tmp_settings: Settings) -> None:
    """Worked Example 2:
    - Buy @ 1.00 (no amount), add @ 1.20 -> avg cost 1.10
    - Partial sell sell_ratio=0.5 @ 1.32 -> remaining 50%, realized return +20.0%
    - Full sell remaining (sell_ratio omitted -> defaults to 1.0) @ 1.43 -> auto-closes
    - has_amount_data=False and all RMB fields are None
    """
    fake = _MutablePingzhongTransport(
        {
            "006131": _build_pingzhongdata_js(
                "006131",
                "华泰柏瑞沪深300ETF联接C",
                [
                    ((2026, 11, 10), 1.0000, 1.0000, 0.0, ""),
                    ((2026, 11, 12), 1.2000, 1.2000, 20.0, ""),
                    ((2026, 11, 18), 1.3200, 1.3200, 10.0, ""),
                    ((2026, 11, 20), 1.4300, 1.4300, 8.33, ""),
                ],
            )
        }
    )
    app = _make_app_with_fake_nav(tmp_settings, fake, today=date(2026, 11, 20))

    with TestClient(app) as client:
        r1 = client.post(
            "/api/v1/positions",
            json={"otc_code": "006131", "trade_date": "2026-11-10"},
        )
        assert r1.status_code == 201
        pos_id = r1.json()["position"]["id"]

        r2 = client.post(
            f"/api/v1/positions/{pos_id}/lots",
            json={"side": "buy", "trade_date": "2026-11-12"},
        )
        assert r2.status_code == 201
        pos = r2.json()["position"]
        assert pos["has_amount_data"] is False
        assert pos["avg_cost_nav"] == pytest.approx(1.10)
        assert pos["remaining_ratio"] == pytest.approx(1.0)
        assert pos["remaining_shares"] is None
        assert pos["invested_capital"] is None
        assert pos["unrealized_pnl"] is None
        assert pos["realized_pnl"] is None
        assert pos["total_pnl"] is None

        # Invalid sell_ratio outside (0, 1] must be rejected
        r_bad = client.post(
            f"/api/v1/positions/{pos_id}/lots",
            json={"side": "sell", "trade_date": "2026-11-18", "sell_ratio": 1.5},
        )
        assert r_bad.status_code in (400, 422)

        # Partial sell 50% @ 1.32
        r3 = client.post(
            f"/api/v1/positions/{pos_id}/lots",
            json={"side": "sell", "trade_date": "2026-11-18", "sell_ratio": 0.5},
        )
        assert r3.status_code == 201
        pos = r3.json()["position"]
        assert pos["status"] == "active"
        assert pos["has_amount_data"] is False
        assert pos["remaining_ratio"] == pytest.approx(0.5)
        assert pos["realized_return_pct"] == pytest.approx(20.0)
        assert pos["unrealized_return_pct"] == pytest.approx(30.0)
        assert pos["total_return_pct"] == pytest.approx(25.0)
        assert pos["invested_capital"] is None
        assert pos["unrealized_pnl"] is None
        assert pos["realized_pnl"] is None
        assert pos["total_pnl"] is None
        assert pos["remaining_shares"] is None

        # Full sell remaining with sell_ratio omitted (defaults to 1.0) @ 1.43
        r4 = client.post(
            f"/api/v1/positions/{pos_id}/lots",
            json={"side": "sell", "trade_date": "2026-11-20"},
        )
        assert r4.status_code == 201
        closed = r4.json()["position"]
        assert closed["status"] == "closed"
        assert closed["closed_date"] == "2026-11-20"
        assert closed["has_amount_data"] is False
        assert closed["remaining_ratio"] == pytest.approx(0.0)
        assert closed["realized_return_pct"] == pytest.approx(25.0)
        assert closed["total_return_pct"] == pytest.approx(25.0)
        assert closed["unrealized_return_pct"] is None
        assert closed["invested_capital"] is None
        assert closed["unrealized_pnl"] is None
        assert closed["realized_pnl"] is None
        assert closed["total_pnl"] is None
        assert closed["remaining_shares"] is None


def test_reentry_after_close_reopen_conflict_and_pending_sell_sync_close(
    tmp_settings: Settings,
) -> None:
    """Worked Example 3:
    - Close 000051 -> create brand-new active 000051 (id != old_id)
    - Deleting closing sell lot on archived 000051 while new active exists -> 409 Conflict (lot preserved!)
    - Deleting new active position -> deleting closing sell lot on archived 000051 cleanly reopens it to active
    - Editing a closing sell lot to partial sell also reopens cleanly
    - Pending sell lot with amount-only closes position after sync-nav backfills official NAV
    """
    fake = _MutablePingzhongTransport(
        {
            "000051": _build_pingzhongdata_js(
                "000051",
                "华夏沪深300ETF联接A",
                [
                    ((2026, 11, 10), 1.0000, 1.0000, 0.0, ""),
                    ((2026, 11, 18), 1.1000, 1.1000, 10.0, ""),
                    ((2026, 11, 19), 1.1500, 1.1500, 4.55, ""),
                ],
            )
        }
    )
    app = _make_app_with_fake_nav(tmp_settings, fake, today=date(2026, 11, 20))

    with TestClient(app) as client:
        # Open & close position #1 for 000051
        p1 = client.post(
            "/api/v1/positions",
            json={"otc_code": "000051", "trade_date": "2026-11-10", "amount": 1000.0},
        ).json()["position"]
        sell_res = client.post(
            f"/api/v1/positions/{p1['id']}/lots",
            json={"side": "sell", "trade_date": "2026-11-18", "shares": 1000.0},
        )
        assert sell_res.status_code == 201
        assert sell_res.json()["position"]["status"] == "closed"
        assert sell_res.json()["position"]["closed_date"] == "2026-11-18"
        sell_lot_id = sell_res.json()["lot"]["id"]

        # Create brand-new active position #2 for the same 000051
        r_new = client.post(
            "/api/v1/positions",
            json={"otc_code": "000051", "trade_date": "2026-11-19", "amount": 2300.0},
        )
        assert r_new.status_code == 201
        p2 = r_new.json()["position"]
        assert p2["id"] != p1["id"]
        assert p2["status"] == "active"

        # Trying to delete closing sell lot on p1 while p2 is active -> 409 Conflict, lot NOT deleted
        r_conflict = client.delete(f"/api/v1/positions/{p1['id']}/lots/{sell_lot_id}")
        assert r_conflict.status_code == 409
        p1_check = client.get(f"/api/v1/positions/{p1['id']}").json()["position"]
        assert p1_check["status"] == "closed"
        assert len(p1_check["lots"]) == 2

        # Trying to shrink closing sell lot on p1 while p2 is active -> 409 Conflict
        r_conflict_patch = client.patch(
            f"/api/v1/positions/{p1['id']}/lots/{sell_lot_id}",
            json={"shares": 500.0},
        )
        assert r_conflict_patch.status_code == 409

        # Record a pending sell on p2 on 2026-11-20 by amount only (2400 RMB; NAV 1.20 not published yet)
        # 2300 / 1.15 = 2000 shares; when NAV 1.20 publishes, 2400 / 1.20 = 2000 shares -> auto-closes p2!
        r_pend_sell = client.post(
            f"/api/v1/positions/{p2['id']}/lots",
            json={"side": "sell", "trade_date": "2026-11-20", "amount": 2400.0},
        )
        assert r_pend_sell.status_code == 201
        assert r_pend_sell.json()["lot"]["nav_status"] == "pending"
        assert r_pend_sell.json()["position"]["status"] == "active"

        # Publish 2026-11-20 NAV = 1.20 and sync-nav -> backfills pending sell & auto-closes p2
        fake.payloads["000051"] = _build_pingzhongdata_js(
            "000051",
            "华夏沪深300ETF联接A",
            [
                ((2026, 11, 10), 1.0000, 1.0000, 0.0, ""),
                ((2026, 11, 18), 1.1000, 1.1000, 10.0, ""),
                ((2026, 11, 19), 1.1500, 1.1500, 4.55, ""),
                ((2026, 11, 20), 1.2000, 1.2000, 4.35, ""),
            ],
        )
        r_sync = client.post("/api/v1/positions/sync-nav", json={"otc_code": "000051"})
        assert r_sync.status_code == 200
        assert r_sync.json()["backfilled_lots"] == 1

        p2_after = client.get(f"/api/v1/positions/{p2['id']}").json()["position"]
        assert p2_after["status"] == "closed"
        assert p2_after["closed_date"] == "2026-11-20"
        assert p2_after["realized_pnl"] == pytest.approx(100.0)

        # Now no active 000051 exists -> deleting closing sell lot on p1 cleanly reopens p1!
        r_reopen = client.delete(f"/api/v1/positions/{p1['id']}/lots/{sell_lot_id}")
        assert r_reopen.status_code == 200
        reopened = r_reopen.json()["position"]
        assert reopened["status"] == "active"
        assert reopened["closed_date"] is None
        assert reopened["remaining_shares"] == pytest.approx(1000.0)


def test_fifo_7_day_short_term_fee_tracking_across_staggered_buys_and_partial_sell(
    tmp_settings: Settings,
) -> None:
    """Worked Example 4:
    - Eval date = 2026-11-20
    - Buy Lot A 10 days ago (2026-11-10, 1000 shares)
    - Buy Lot B 3 days ago (2026-11-17, 1000 shares)
    - Before sell: fee_free_ratio = 0.5, penalized_ratio = 0.5, all_fee_free = False
    - Sell 1000 shares on 2026-11-20 -> consumes Lot A first (FIFO)
    - After sell: remaining 1000 shares are all Lot B -> fee_free_ratio = 0.0, penalized_ratio = 1.0,
      days_until_fee_free = 4, earliest_all_fee_free_date = 2026-11-24
    """
    fake = _MutablePingzhongTransport(
        {
            "006131": _build_pingzhongdata_js(
                "006131",
                "华泰柏瑞沪深300ETF联接C",
                [
                    ((2026, 11, 10), 1.0000, 1.0000, 0.0, ""),
                    ((2026, 11, 17), 1.1000, 1.1000, 10.0, ""),
                    ((2026, 11, 20), 1.1500, 1.1500, 4.55, ""),
                ],
            )
        }
    )
    app = _make_app_with_fake_nav(tmp_settings, fake, today=date(2026, 11, 20))

    with TestClient(app) as client:
        r1 = client.post(
            "/api/v1/positions",
            json={"otc_code": "006131", "trade_date": "2026-11-10", "shares": 1000.0},
        )
        assert r1.status_code == 201
        pos_id = r1.json()["position"]["id"]
        lot_a_id = r1.json()["position"]["lots"][0]["id"]

        r2 = client.post(
            f"/api/v1/positions/{pos_id}/lots",
            json={"side": "buy", "trade_date": "2026-11-17", "shares": 1000.0},
        )
        assert r2.status_code == 201
        lot_b_id = r2.json()["lot"]["id"]
        pos_before = r2.json()["position"]

        fifo_before = pos_before["fifo_fee_status"]
        assert fifo_before["fee_free_ratio"] == pytest.approx(0.5)
        assert fifo_before["penalized_ratio"] == pytest.approx(0.5)
        assert fifo_before["all_fee_free"] is False
        assert fifo_before["earliest_all_fee_free_date"] == "2026-11-24"
        assert fifo_before["min_holding_days"] == 3
        assert fifo_before["max_holding_days"] == 10
        assert len(fifo_before["lots"]) == 2

        # Sell 1000 shares -> consumes Lot A first (FIFO)
        r3 = client.post(
            f"/api/v1/positions/{pos_id}/lots",
            json={"side": "sell", "trade_date": "2026-11-20", "shares": 1000.0},
        )
        assert r3.status_code == 201
        pos_after = r3.json()["position"]
        fifo_after = pos_after["fifo_fee_status"]
        assert fifo_after["fee_free_ratio"] == pytest.approx(0.0)
        assert fifo_after["penalized_ratio"] == pytest.approx(1.0)
        assert fifo_after["all_fee_free"] is False
        assert fifo_after["earliest_all_fee_free_date"] == "2026-11-24"
        assert fifo_after["min_holding_days"] == 3
        assert fifo_after["max_holding_days"] == 3
        assert len(fifo_after["lots"]) == 1
        rem_lot = fifo_after["lots"][0]
        assert rem_lot["lot_id"] == lot_b_id
        assert rem_lot["remaining_ratio"] == pytest.approx(1.0)
        assert rem_lot["holding_calendar_days"] == 3
        assert rem_lot["is_fee_free"] is False
        assert rem_lot["days_until_fee_free"] == 4
        assert rem_lot["fee_free_date"] == "2026-11-24"

        # Verify per-lot FIFO fields exposed on position["lots"] as well
        lots_by_id: dict[int, Any] = {lot["id"]: lot for lot in pos_after["lots"]}
        assert lots_by_id[lot_a_id]["fifo_remaining_ratio"] == pytest.approx(0.0)
        assert lots_by_id[lot_a_id]["is_fee_free"] is True
        assert lots_by_id[lot_b_id]["fifo_remaining_ratio"] == pytest.approx(1.0)
        assert lots_by_id[lot_b_id]["is_fee_free"] is False
        assert lots_by_id[lot_b_id]["days_until_fee_free"] == 4


def test_portfolio_summary_aggregates_win_rate_across_all_and_money_strictly_amount_positions(
    tmp_settings: Settings,
) -> None:
    """Worked Example 5:
    - 1 capital-mode closed winner (+20.0%, +200 RMB realized)
    - 1 percentage-only closed loser (-10.0%, no RMB)
    - 1 capital-mode active position (2000 RMB invested, +200 RMB unrealized)
    Verify:
    - closed_win_count == 1, closed_win_rate_pct == 50.0
    - closed_avg_return_pct == 5.0 (average of +20% and -10%)
    - amount_position_count == 2
    - total_invested_capital == 2000.0
    - total_unrealized_pnl == 200.0
    - total_realized_pnl == 200.0
    - total_pnl == 400.0
    """
    fake = _MutablePingzhongTransport(
        {
            "006131": _build_pingzhongdata_js(
                "006131",
                "华泰柏瑞沪深300ETF联接C",
                [
                    ((2026, 11, 10), 1.0000, 1.0000, 0.0, ""),
                    ((2026, 11, 18), 1.2000, 1.2000, 20.0, ""),
                ],
            ),
            "000051": _build_pingzhongdata_js(
                "000051",
                "华夏沪深300ETF联接A",
                [
                    ((2026, 11, 10), 1.0000, 1.0000, 0.0, ""),
                    ((2026, 11, 18), 0.9000, 0.9000, -10.0, ""),
                ],
            ),
            "019999": _build_pingzhongdata_js(
                "019999",
                "银河创新成长混合A",
                [
                    ((2026, 11, 10), 1.0000, 1.0000, 0.0, ""),
                    ((2026, 11, 20), 1.1000, 1.1000, 10.0, ""),
                ],
            ),
        }
    )
    app = _make_app_with_fake_nav(tmp_settings, fake, today=date(2026, 11, 20))

    with TestClient(app) as client:
        # 1. Capital-mode closed winner: 006131 (+20%, +200 RMB)
        p1 = client.post(
            "/api/v1/positions",
            json={"otc_code": "006131", "trade_date": "2026-11-10", "amount": 1000.0},
        ).json()["position"]
        client.post(
            f"/api/v1/positions/{p1['id']}/lots",
            json={"side": "sell", "trade_date": "2026-11-18", "shares": 1000.0},
        )

        # 2. Percentage-only closed loser: 000051 (-10%, no RMB)
        p2 = client.post(
            "/api/v1/positions",
            json={"otc_code": "000051", "trade_date": "2026-11-10"},
        ).json()["position"]
        client.post(
            f"/api/v1/positions/{p2['id']}/lots",
            json={"side": "sell", "trade_date": "2026-11-18"},
        )

        # 3. Capital-mode active position: 019999 (2000 RMB @ 1.00, latest 1.10 -> +200 RMB unrealized)
        client.post(
            "/api/v1/positions",
            json={"otc_code": "019999", "trade_date": "2026-11-10", "amount": 2000.0},
        )

        # Verify summary from GET /api/v1/positions (status=active and status=all)
        res = client.get("/api/v1/positions", params={"status": "active"})
        assert res.status_code == 200
        body = res.json()
        assert len(body["positions"]) == 1

        summary = body["summary"]
        portfolio_summary = body["portfolio_summary"]
        assert summary == portfolio_summary

        assert summary["active_count"] == 1
        assert summary["closed_count"] == 2
        assert summary["pending_nav_count"] == 0
        assert summary["closed_win_count"] == 1
        assert summary["closed_win_rate_pct"] == pytest.approx(50.0)
        assert summary["closed_avg_return_pct"] == pytest.approx(5.0)
        assert summary["amount_position_count"] == 2
        assert summary["total_invested_capital"] == pytest.approx(2000.0)
        assert summary["total_unrealized_pnl"] == pytest.approx(200.0)
        assert summary["total_realized_pnl"] == pytest.approx(200.0)
        assert summary["total_pnl"] == pytest.approx(400.0)


def test_capital_mode_sell_ratio_edit_reopen_and_pct_only_portfolio_summary(
    tmp_settings: Settings,
) -> None:
    """Verify additional edge cases:
    1. Capital-mode sells using `sell_ratio` (50% then 100%) compute exact shares & auto-close.
    2. Editing a closing sell lot to a partial sell (`sell_ratio=0.5`) cleanly reopens to active.
    3. Editing a buy lot down below already-sold shares is rejected with HTTP 400 and leaves DB intact.
    4. Percentage-only-only portfolio summary keeps all monetary totals as None (no fabricated RMB).
    5. Day-0 Pending NAV buy lot still exposes accurate FIFO 7-day countdown (0 days held, 7 days left).
    """
    fake = _MutablePingzhongTransport(
        {
            "006131": _build_pingzhongdata_js(
                "006131",
                "华泰柏瑞沪深300ETF联接C",
                [
                    ((2026, 11, 10), 1.0000, 1.0000, 0.0, ""),
                    ((2026, 11, 18), 1.2500, 1.2500, 25.0, ""),
                    ((2026, 11, 19), 1.3000, 1.3000, 4.0, ""),
                ],
            ),
            "000051": _build_pingzhongdata_js(
                "000051",
                "华夏沪深300ETF联接A",
                [
                    ((2026, 11, 10), 1.0000, 1.0000, 0.0, ""),
                    ((2026, 11, 18), 1.1500, 1.1500, 15.0, ""),
                ],
            ),
        }
    )
    app = _make_app_with_fake_nav(tmp_settings, fake, today=date(2026, 11, 20))

    with TestClient(app) as client:
        # Percentage-only portfolio first: monetary totals must all be None
        p_pct = client.post(
            "/api/v1/positions",
            json={"otc_code": "000051", "trade_date": "2026-11-10"},
        ).json()["position"]
        client.post(
            f"/api/v1/positions/{p_pct['id']}/lots",
            json={"side": "sell", "trade_date": "2026-11-18"},
        )
        pct_summary = client.get("/api/v1/positions", params={"status": "all"}).json()["summary"]
        assert pct_summary["closed_count"] == 1
        assert pct_summary["closed_win_rate_pct"] == pytest.approx(100.0)
        assert pct_summary["closed_avg_return_pct"] == pytest.approx(15.0)
        assert pct_summary["amount_position_count"] == 0
        assert pct_summary["total_invested_capital"] is None
        assert pct_summary["total_unrealized_pnl"] is None
        assert pct_summary["total_realized_pnl"] is None
        assert pct_summary["total_pnl"] is None

        # Capital mode position: buy 1000 RMB @ 1.00
        p_cap = client.post(
            "/api/v1/positions",
            json={"otc_code": "006131", "trade_date": "2026-11-10", "amount": 1000.0},
        ).json()["position"]
        buy_lot_id = p_cap["lots"][0]["id"]

        # Full sell via sell_ratio=1.0 @ 1.25 -> auto-closes
        r_sell = client.post(
            f"/api/v1/positions/{p_cap['id']}/lots",
            json={"side": "sell", "trade_date": "2026-11-18", "sell_ratio": 1.0},
        )
        assert r_sell.status_code == 201
        assert r_sell.json()["position"]["status"] == "closed"
        assert r_sell.json()["position"]["realized_pnl"] == pytest.approx(250.0)
        sell_lot_id = r_sell.json()["lot"]["id"]

        # Edit closing sell lot to partial sell (sell_ratio=0.5) -> cleanly reopens!
        r_reopen = client.patch(
            f"/api/v1/positions/{p_cap['id']}/lots/{sell_lot_id}",
            json={"sell_ratio": 0.5},
        )
        assert r_reopen.status_code == 200
        reopened = r_reopen.json()["position"]
        assert reopened["status"] == "active"
        assert reopened["closed_date"] is None
        assert reopened["remaining_shares"] == pytest.approx(500.0)
        assert reopened["realized_pnl"] == pytest.approx(125.0)

        # Edit sell lot to explicit 800 shares, then attempt shrinking buy lot to 500 shares -> 400!
        r_explicit = client.patch(
            f"/api/v1/positions/{p_cap['id']}/lots/{sell_lot_id}",
            json={"shares": 800.0, "sell_ratio": None},
        )
        assert r_explicit.status_code == 200
        r_bad_buy_edit = client.patch(
            f"/api/v1/positions/{p_cap['id']}/lots/{buy_lot_id}",
            json={"shares": 500.0},
        )
        assert r_bad_buy_edit.status_code == 400
        # Verify buy lot was untouched
        check = client.get(f"/api/v1/positions/{p_cap['id']}").json()["position"]
        assert check["remaining_shares"] == pytest.approx(200.0)

        # Day-0 Pending NAV buy lot on a new fund (000051 re-entry on today 2026-11-20)
        p_pending = client.post(
            "/api/v1/positions",
            json={"otc_code": "000051", "trade_date": "2026-11-20", "amount": 500.0},
        ).json()["position"]
        assert p_pending["has_pending_nav"] is True
        assert p_pending["fifo_fee_status"]["all_fee_free"] is False
        assert p_pending["fifo_fee_status"]["penalized_ratio"] == pytest.approx(1.0)
        assert p_pending["fifo_fee_status"]["min_holding_days"] == 0
        assert p_pending["fifo_fee_status"]["days_until_all_fee_free"] == 7
        assert p_pending["fifo_fee_status"]["earliest_all_fee_free_date"] == "2026-11-27"


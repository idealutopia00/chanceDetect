"""Pure deterministic position accounting & FIFO 7-calendar-day redemption fee engine (ADR-0003).

Supports both:
- Capital Mode (`has_amount_data = True`): moving weighted-average cost per share, remaining shares,
  invested capital, realized/unrealized/total P&L (RMB & %), net of optional fees.
- Percentage-Only Mode (`has_amount_data = False`): normalized 1.0 synthetic unit per buy lot,
  moving weighted-average entry NAV, proportional partial sells (`sell_ratio`, default 1.0),
  accurate percentage returns without fabricating RMB figures.

FIFO 7-calendar-day short-term redemption fee eligibility is evaluated strictly per buy Trade Lot
in FIFO order on sells (`>= 7` calendar days fee-free vs `< 7` days subject to 1.5% penalty).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from chancedetect.data.models import TradeLotRecord

EPS = 1e-6
SHORT_TERM_FEE_DAYS = 7


class OversellError(ValueError):
    """Raised when a sell Trade Lot exceeds the remaining Tracked Position balance."""


@dataclass
class _FifoEntry:
    lot_id: int
    trade_date: str
    buy_date: date
    original_units: float
    remaining_units: float
    is_pending_qty: bool = False


@dataclass(frozen=True)
class LotFifoStatus:
    """Per-buy-lot FIFO remaining balance and 7-calendar-day redemption fee status."""

    lot_id: int
    trade_date: str
    remaining_ratio: float
    remaining_lot_ratio: float
    remaining_shares: float | None
    holding_calendar_days: int
    is_fee_free: bool
    days_until_fee_free: int
    fee_free_date: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "lot_id": self.lot_id,
            "trade_date": self.trade_date,
            "remaining_ratio": self.remaining_ratio,
            "remaining_lot_ratio": self.remaining_lot_ratio,
            "remaining_shares": self.remaining_shares,
            "holding_calendar_days": self.holding_calendar_days,
            "is_fee_free": self.is_fee_free,
            "days_until_fee_free": self.days_until_fee_free,
            "fee_free_date": self.fee_free_date,
        }


@dataclass(frozen=True)
class FifoFeeStatus:
    """Position-level FIFO 7-calendar-day short-term redemption fee eligibility summary."""

    fee_free_ratio: float
    penalized_ratio: float
    all_fee_free: bool
    earliest_all_fee_free_date: str | None
    days_until_all_fee_free: int
    min_holding_days: int | None
    max_holding_days: int | None
    lots: list[LotFifoStatus] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fee_free_ratio": self.fee_free_ratio,
            "penalized_ratio": self.penalized_ratio,
            "all_fee_free": self.all_fee_free,
            "earliest_all_fee_free_date": self.earliest_all_fee_free_date,
            "days_until_all_fee_free": self.days_until_all_fee_free,
            "min_holding_days": self.min_holding_days,
            "max_holding_days": self.max_holding_days,
            "lots": [item.to_dict() for item in self.lots],
        }


@dataclass(frozen=True)
class PositionAccountingResult:
    """Complete deterministic accounting and lifecycle result for a Tracked Position."""

    has_amount_data: bool
    is_closed: bool
    opened_date: str | None
    closed_date: str | None
    holding_days: int
    avg_cost_nav: float | None
    remaining_shares: float | None
    remaining_ratio: float
    invested_capital: float | None
    total_bought_amount: float | None
    total_sold_amount: float | None
    total_fees: float | None
    realized_pnl: float | None
    realized_return_pct: float | None
    unrealized_pnl: float | None
    unrealized_return_pct: float | None
    total_pnl: float | None
    total_return_pct: float | None
    fifo_fee_status: FifoFeeStatus
    per_lot_fifo: dict[int, LotFifoStatus] = field(default_factory=dict)
    per_lot_realized_pnl: dict[int, float | None] = field(default_factory=dict)
    per_lot_realized_return_pct: dict[int, float | None] = field(default_factory=dict)
    per_lot_effective_shares: dict[int, float | None] = field(default_factory=dict)


def sort_trade_lots(lots: Sequence[TradeLotRecord]) -> list[TradeLotRecord]:
    """Order Trade Lots deterministically: trade_date ASC, buys before sells on same day, id ASC."""
    return sorted(
        lots,
        key=lambda lot: (lot.trade_date, 0 if lot.side == "buy" else 1, lot.id),
    )


def _consume_fifo(fifo_queue: list[_FifoEntry], sold_units: float) -> None:
    to_consume = sold_units
    for entry in fifo_queue:
        if entry.remaining_units <= EPS:
            continue
        take = min(entry.remaining_units, to_consume)
        entry.remaining_units -= take
        if entry.remaining_units <= EPS:
            entry.remaining_units = 0.0
        to_consume -= take
        if to_consume <= EPS:
            break


def _build_fifo_status(
    fifo_queue: list[_FifoEntry],
    *,
    eval_date: date,
    has_amount_data: bool,
    is_closed: bool,
) -> tuple[FifoFeeStatus, dict[int, LotFifoStatus]]:
    # If all remaining entries are pending-qty (e.g. a single pending buy before evening NAV),
    # assign equal synthetic weight 1.0 so 7-day countdown is still accurate on Day 0.
    confirmed_rem_total = sum(
        e.remaining_units for e in fifo_queue if not e.is_pending_qty and e.remaining_units > EPS
    )
    use_synthetic_pending_weights = (
        not is_closed
        and confirmed_rem_total <= EPS
        and any(e.is_pending_qty for e in fifo_queue)
    )
    if use_synthetic_pending_weights:
        active_weights = {
            e.lot_id: 1.0 for e in fifo_queue if e.is_pending_qty
        }
        total_weight = float(len(active_weights))
    else:
        active_weights = {
            e.lot_id: e.remaining_units
            for e in fifo_queue
            if not e.is_pending_qty and e.remaining_units > EPS
        }
        total_weight = confirmed_rem_total

    per_lot_fifo: dict[int, LotFifoStatus] = {}
    remaining_lot_statuses: list[LotFifoStatus] = []
    fee_free_weight = 0.0

    for entry in fifo_queue:
        holding_days = max((eval_date - entry.buy_date).days, 0)
        is_fee_free = holding_days >= SHORT_TERM_FEE_DAYS
        days_until = max(SHORT_TERM_FEE_DAYS - holding_days, 0)
        fee_free_dt = (entry.buy_date + timedelta(days=SHORT_TERM_FEE_DAYS)).isoformat()

        weight = active_weights.get(entry.lot_id, 0.0)
        rem_ratio = round(weight / total_weight, 6) if total_weight > EPS and weight > EPS else 0.0
        if entry.is_pending_qty:
            rem_lot_ratio = 1.0 if not is_closed else 0.0
            rem_shares = None
        else:
            rem_lot_ratio = (
                round(entry.remaining_units / entry.original_units, 6)
                if entry.original_units > EPS and entry.remaining_units > EPS
                else 0.0
            )
            rem_shares = round(entry.remaining_units, 4) if has_amount_data else None

        lot_status = LotFifoStatus(
            lot_id=entry.lot_id,
            trade_date=entry.trade_date,
            remaining_ratio=rem_ratio,
            remaining_lot_ratio=rem_lot_ratio,
            remaining_shares=rem_shares,
            holding_calendar_days=holding_days,
            is_fee_free=is_fee_free,
            days_until_fee_free=days_until,
            fee_free_date=fee_free_dt,
        )
        per_lot_fifo[entry.lot_id] = lot_status

        if weight > EPS:
            remaining_lot_statuses.append(lot_status)
            if is_fee_free:
                fee_free_weight += weight

    if remaining_lot_statuses and total_weight > EPS:
        fee_free_ratio = round(min(max(fee_free_weight / total_weight, 0.0), 1.0), 6)
        penalized_ratio = round(max(0.0, 1.0 - fee_free_ratio), 6)
        penalized_lots = [item for item in remaining_lot_statuses if not item.is_fee_free]
        all_fee_free = len(penalized_lots) == 0
        earliest_all = max((item.fee_free_date for item in penalized_lots), default=None)
        days_until_all = max((item.days_until_fee_free for item in penalized_lots), default=0)
        min_days = min(item.holding_calendar_days for item in remaining_lot_statuses)
        max_days = max(item.holding_calendar_days for item in remaining_lot_statuses)
    else:
        fee_free_ratio = 0.0
        penalized_ratio = 0.0
        all_fee_free = True
        earliest_all = None
        days_until_all = 0
        min_days = None
        max_days = None

    fifo_status = FifoFeeStatus(
        fee_free_ratio=fee_free_ratio,
        penalized_ratio=penalized_ratio,
        all_fee_free=all_fee_free,
        earliest_all_fee_free_date=earliest_all,
        days_until_all_fee_free=days_until_all,
        min_holding_days=min_days,
        max_holding_days=max_days,
        lots=remaining_lot_statuses,
    )
    return fifo_status, per_lot_fifo


def evaluate_position_accounting(
    lots: Sequence[TradeLotRecord],
    *,
    latest_nav: float | None,
    eval_date: date,
    fallback_opened_date: str | None = None,
) -> PositionAccountingResult:
    """Compute full Tracked Position accounting, lifecycle status, and FIFO 7-day fee eligibility."""
    ordered = sort_trade_lots(lots)
    buy_lots = [lot for lot in ordered if lot.side == "buy"]
    sell_lots = [lot for lot in ordered if lot.side == "sell"]

    opened_date = min((lot.trade_date for lot in buy_lots), default=fallback_opened_date)
    has_amount_data = any(
        (lot.amount is not None or lot.shares is not None) for lot in buy_lots
    )

    if not ordered:
        empty_fifo = FifoFeeStatus(
            fee_free_ratio=0.0,
            penalized_ratio=0.0,
            all_fee_free=True,
            earliest_all_fee_free_date=None,
            days_until_all_fee_free=0,
            min_holding_days=None,
            max_holding_days=None,
            lots=[],
        )
        holding_days = 0
        if opened_date:
            holding_days = max((eval_date - date.fromisoformat(opened_date)).days, 0)
        return PositionAccountingResult(
            has_amount_data=False,
            is_closed=False,
            opened_date=opened_date,
            closed_date=None,
            holding_days=holding_days,
            avg_cost_nav=None,
            remaining_shares=None,
            remaining_ratio=0.0,
            invested_capital=None,
            total_bought_amount=None,
            total_sold_amount=None,
            total_fees=None,
            realized_pnl=None,
            realized_return_pct=None,
            unrealized_pnl=None,
            unrealized_return_pct=None,
            total_pnl=None,
            total_return_pct=None,
            fifo_fee_status=empty_fifo,
        )

    if has_amount_data:
        return _evaluate_capital_mode(
            ordered=ordered,
            buy_lots=buy_lots,
            sell_lots=sell_lots,
            opened_date=opened_date,
            latest_nav=latest_nav,
            eval_date=eval_date,
        )
    return _evaluate_percentage_mode(
        ordered=ordered,
        buy_lots=buy_lots,
        sell_lots=sell_lots,
        opened_date=opened_date,
        latest_nav=latest_nav,
        eval_date=eval_date,
    )


def _evaluate_capital_mode(
    *,
    ordered: list[TradeLotRecord],
    buy_lots: list[TradeLotRecord],
    sell_lots: list[TradeLotRecord],
    opened_date: str | None,
    latest_nav: float | None,
    eval_date: date,
) -> PositionAccountingResult:
    remaining_shares = 0.0
    remaining_cost = 0.0
    total_bought_shares = 0.0
    total_bought_amount = 0.0
    pending_buy_amount = 0.0
    total_sold_amount = 0.0
    total_fees = 0.0

    realized_pnl_acc = 0.0
    realized_cost_acc = 0.0
    has_confirmed_sell = False
    has_unconfirmed_sell_pnl = False
    has_unconfirmed_sell_shares = False
    has_unconfirmed_buy_shares = False
    has_unconfirmed_buy_cost = False
    last_avg_cost_nav: float | None = None

    fifo_queue: list[_FifoEntry] = []
    per_lot_realized_pnl: dict[int, float | None] = {}
    per_lot_realized_return_pct: dict[int, float | None] = {}
    per_lot_effective_shares: dict[int, float | None] = {}

    for lot in ordered:
        lot_fee = float(lot.fee or 0.0)
        total_fees += lot_fee
        lot_nav = float(lot.nav) if (lot.nav is not None and lot.nav > 0) else None

        if lot.side == "buy":
            buy_dt = date.fromisoformat(lot.trade_date)
            if lot.shares is not None:
                qty = float(lot.shares)
                if lot.amount is not None:
                    cost: float | None = float(lot.amount)
                elif lot_nav is not None:
                    cost = qty * lot_nav + lot_fee
                else:
                    cost = None
                    has_unconfirmed_buy_cost = True

                remaining_shares += qty
                total_bought_shares += qty
                per_lot_effective_shares[lot.id] = round(qty, 4)

                if cost is not None:
                    remaining_cost += cost
                    total_bought_amount += cost
                    if remaining_shares > EPS and not has_unconfirmed_buy_cost:
                        last_avg_cost_nav = remaining_cost / remaining_shares

                fifo_queue.append(
                    _FifoEntry(
                        lot_id=lot.id,
                        trade_date=lot.trade_date,
                        buy_date=buy_dt,
                        original_units=qty,
                        remaining_units=qty,
                        is_pending_qty=False,
                    )
                )
            elif lot.amount is not None:
                cost_amt = float(lot.amount)
                if lot_nav is not None:
                    qty = max(cost_amt - lot_fee, 0.0) / lot_nav
                    remaining_shares += qty
                    total_bought_shares += qty
                    remaining_cost += cost_amt
                    total_bought_amount += cost_amt
                    per_lot_effective_shares[lot.id] = round(qty, 4)
                    if remaining_shares > EPS and not has_unconfirmed_buy_cost:
                        last_avg_cost_nav = remaining_cost / remaining_shares
                    fifo_queue.append(
                        _FifoEntry(
                            lot_id=lot.id,
                            trade_date=lot.trade_date,
                            buy_date=buy_dt,
                            original_units=qty,
                            remaining_units=qty,
                            is_pending_qty=False,
                        )
                    )
                else:
                    has_unconfirmed_buy_shares = True
                    pending_buy_amount += cost_amt
                    total_bought_amount += cost_amt
                    per_lot_effective_shares[lot.id] = None
                    fifo_queue.append(
                        _FifoEntry(
                            lot_id=lot.id,
                            trade_date=lot.trade_date,
                            buy_date=buy_dt,
                            original_units=0.0,
                            remaining_units=0.0,
                            is_pending_qty=True,
                        )
                    )
            else:
                per_lot_effective_shares[lot.id] = None
                fifo_queue.append(
                    _FifoEntry(
                        lot_id=lot.id,
                        trade_date=lot.trade_date,
                        buy_date=buy_dt,
                        original_units=0.0,
                        remaining_units=0.0,
                        is_pending_qty=True,
                    )
                )
        else:
            # Sell lot
            sold_qty: float | None
            if lot.shares is not None:
                sold_qty = float(lot.shares)
            elif lot.amount is not None:
                if lot_nav is not None:
                    sold_qty = (float(lot.amount) + lot_fee) / lot_nav
                else:
                    sold_qty = None
                    has_unconfirmed_sell_shares = True
                    has_unconfirmed_sell_pnl = True
                    per_lot_effective_shares[lot.id] = None
                    per_lot_realized_pnl[lot.id] = None
                    per_lot_realized_return_pct[lot.id] = None
            else:
                ratio = float(lot.sell_ratio) if lot.sell_ratio is not None else 1.0
                if not (0.0 < ratio <= 1.0):
                    raise ValueError("sell_ratio must be within (0, 1].")
                if remaining_shares <= EPS:
                    raise OversellError(
                        "Cannot record a sell lot when remaining position shares are zero."
                    )
                sold_qty = remaining_shares * ratio

            if sold_qty is None:
                continue

            if sold_qty <= EPS:
                raise OversellError("Sell shares must be greater than zero.")

            tol = max(1e-4, remaining_shares * 1e-5)
            if sold_qty > remaining_shares + tol:
                raise OversellError(
                    f"Sell shares ({sold_qty:.4f}) exceed remaining balance ({remaining_shares:.4f})."
                )
            if sold_qty > remaining_shares:
                sold_qty = remaining_shares

            per_lot_effective_shares[lot.id] = round(sold_qty, 4)

            cur_avg_cost = (
                remaining_cost / remaining_shares
                if (remaining_shares > EPS and not has_unconfirmed_buy_cost)
                else None
            )
            if cur_avg_cost is not None:
                last_avg_cost_nav = cur_avg_cost
                cost_of_sold: float | None = sold_qty * cur_avg_cost
            else:
                cost_of_sold = None

            remaining_shares -= sold_qty
            if remaining_shares <= EPS:
                remaining_shares = 0.0
                remaining_cost = 0.0
            elif cost_of_sold is not None:
                remaining_cost = max(remaining_cost - cost_of_sold, 0.0)

            _consume_fifo(fifo_queue, sold_qty)

            if lot_nav is not None and cost_of_sold is not None:
                net_proceeds = sold_qty * lot_nav - lot_fee
                lot_rpnl = net_proceeds - cost_of_sold
                total_sold_amount += net_proceeds
                realized_pnl_acc += lot_rpnl
                realized_cost_acc += cost_of_sold
                has_confirmed_sell = True
                per_lot_realized_pnl[lot.id] = round(lot_rpnl, 2)
                per_lot_realized_return_pct[lot.id] = (
                    round(lot_rpnl / cost_of_sold * 100.0, 2) if cost_of_sold > EPS else None
                )
            else:
                has_unconfirmed_sell_pnl = True
                per_lot_realized_pnl[lot.id] = None
                per_lot_realized_return_pct[lot.id] = None

    is_closed = (
        len(buy_lots) > 0
        and len(sell_lots) > 0
        and remaining_shares <= EPS
        and not has_unconfirmed_buy_shares
        and not has_unconfirmed_sell_shares
    )
    closed_date = max((lot.trade_date for lot in sell_lots), default=None) if is_closed else None

    end_dt = date.fromisoformat(closed_date) if closed_date else eval_date
    holding_days = (
        max((end_dt - date.fromisoformat(opened_date)).days, 0) if opened_date else 0
    )

    fifo_status, per_lot_fifo = _build_fifo_status(
        fifo_queue,
        eval_date=eval_date,
        has_amount_data=True,
        is_closed=is_closed,
    )

    avg_cost_nav = round(last_avg_cost_nav, 4) if last_avg_cost_nav is not None else None
    out_remaining_shares = (
        None
        if (has_unconfirmed_buy_shares and total_bought_shares <= EPS)
        else round(remaining_shares, 4)
    )
    if has_unconfirmed_buy_shares and total_bought_shares <= EPS:
        remaining_ratio = 1.0
    elif total_bought_shares > EPS:
        remaining_ratio = round(remaining_shares / total_bought_shares, 6)
    else:
        remaining_ratio = 0.0

    if is_closed:
        invested_capital: float | None = 0.0
    elif total_bought_shares <= EPS and pending_buy_amount > EPS:
        invested_capital = round(pending_buy_amount, 2)
    elif not has_unconfirmed_buy_cost:
        invested_capital = round(remaining_cost, 2)
    else:
        invested_capital = None

    realized_pnl: float | None = (
        None if has_unconfirmed_sell_pnl else round(realized_pnl_acc, 2)
    )
    realized_return_pct: float | None = (
        round(realized_pnl_acc / realized_cost_acc * 100.0, 2)
        if (has_confirmed_sell and not has_unconfirmed_sell_pnl and realized_cost_acc > EPS)
        else None
    )

    if is_closed:
        unrealized_pnl: float | None = 0.0
        unrealized_return_pct: float | None = None
    elif (
        latest_nav is not None
        and latest_nav > 0
        and remaining_shares > EPS
        and not has_unconfirmed_buy_cost
    ):
        market_val = remaining_shares * latest_nav
        unrealized_pnl = round(market_val - remaining_cost, 2)
        unrealized_return_pct = (
            round((market_val - remaining_cost) / remaining_cost * 100.0, 2)
            if remaining_cost > EPS
            else None
        )
    else:
        unrealized_pnl = None
        unrealized_return_pct = None

    total_pnl: float | None = (
        round(realized_pnl + unrealized_pnl, 2)
        if (realized_pnl is not None and unrealized_pnl is not None)
        else None
    )
    confirmed_bought_cost = total_bought_amount - pending_buy_amount
    total_return_pct: float | None = (
        round(total_pnl / confirmed_bought_cost * 100.0, 2)
        if (total_pnl is not None and confirmed_bought_cost > EPS)
        else None
    )

    return PositionAccountingResult(
        has_amount_data=True,
        is_closed=is_closed,
        opened_date=opened_date,
        closed_date=closed_date,
        holding_days=holding_days,
        avg_cost_nav=avg_cost_nav,
        remaining_shares=out_remaining_shares,
        remaining_ratio=remaining_ratio,
        invested_capital=invested_capital,
        total_bought_amount=round(total_bought_amount, 2),
        total_sold_amount=round(total_sold_amount, 2),
        total_fees=round(total_fees, 2),
        realized_pnl=realized_pnl,
        realized_return_pct=realized_return_pct,
        unrealized_pnl=unrealized_pnl,
        unrealized_return_pct=unrealized_return_pct,
        total_pnl=total_pnl,
        total_return_pct=total_return_pct,
        fifo_fee_status=fifo_status,
        per_lot_fifo=per_lot_fifo,
        per_lot_realized_pnl=per_lot_realized_pnl,
        per_lot_realized_return_pct=per_lot_realized_return_pct,
        per_lot_effective_shares=per_lot_effective_shares,
    )


def _evaluate_percentage_mode(
    *,
    ordered: list[TradeLotRecord],
    buy_lots: list[TradeLotRecord],
    sell_lots: list[TradeLotRecord],
    opened_date: str | None,
    latest_nav: float | None,
    eval_date: date,
) -> PositionAccountingResult:
    remaining_units = 0.0
    total_bought_units = 0.0
    confirmed_remaining_units = 0.0
    confirmed_remaining_cost = 0.0
    confirmed_total_bought_cost = 0.0

    realized_unit_pnl = 0.0
    realized_unit_cost = 0.0
    has_confirmed_sell = False
    has_unconfirmed_sell_pnl = False
    has_pending_buy_nav = False
    last_avg_cost_nav: float | None = None

    fifo_queue: list[_FifoEntry] = []
    per_lot_realized_return_pct: dict[int, float | None] = {}

    for lot in ordered:
        lot_nav = float(lot.nav) if (lot.nav is not None and lot.nav > 0) else None
        if lot.side == "buy":
            buy_dt = date.fromisoformat(lot.trade_date)
            qty = 1.0
            remaining_units += qty
            total_bought_units += qty
            if lot_nav is not None:
                confirmed_remaining_units += qty
                confirmed_remaining_cost += qty * lot_nav
                confirmed_total_bought_cost += qty * lot_nav
                last_avg_cost_nav = confirmed_remaining_cost / confirmed_remaining_units
            else:
                has_pending_buy_nav = True

            fifo_queue.append(
                _FifoEntry(
                    lot_id=lot.id,
                    trade_date=lot.trade_date,
                    buy_date=buy_dt,
                    original_units=qty,
                    remaining_units=qty,
                    is_pending_qty=False,
                )
            )
        else:
            ratio = float(lot.sell_ratio) if lot.sell_ratio is not None else 1.0
            if not (0.0 < ratio <= 1.0):
                raise ValueError("sell_ratio must be within (0, 1].")
            if remaining_units <= EPS:
                raise OversellError(
                    "Cannot record a sell lot when remaining position balance is zero."
                )

            sold_units = remaining_units * ratio
            sold_conf_units = confirmed_remaining_units * ratio
            cur_avg_cost = (
                confirmed_remaining_cost / confirmed_remaining_units
                if confirmed_remaining_units > EPS
                else None
            )
            if cur_avg_cost is not None:
                last_avg_cost_nav = cur_avg_cost
                cost_of_sold: float | None = sold_conf_units * cur_avg_cost
            else:
                cost_of_sold = None

            remaining_units -= sold_units
            confirmed_remaining_units -= sold_conf_units
            if remaining_units <= EPS:
                remaining_units = 0.0
                confirmed_remaining_units = 0.0
                confirmed_remaining_cost = 0.0
            elif cost_of_sold is not None:
                confirmed_remaining_cost = max(confirmed_remaining_cost - cost_of_sold, 0.0)

            _consume_fifo(fifo_queue, sold_units)

            if (
                lot_nav is not None
                and cost_of_sold is not None
                and cost_of_sold > EPS
                and not has_pending_buy_nav
            ):
                proceeds = sold_conf_units * lot_nav
                lot_rpnl = proceeds - cost_of_sold
                realized_unit_pnl += lot_rpnl
                realized_unit_cost += cost_of_sold
                has_confirmed_sell = True
                per_lot_realized_return_pct[lot.id] = round(lot_rpnl / cost_of_sold * 100.0, 2)
            else:
                has_unconfirmed_sell_pnl = True
                per_lot_realized_return_pct[lot.id] = None

    is_closed = len(buy_lots) > 0 and len(sell_lots) > 0 and remaining_units <= EPS
    closed_date = max((lot.trade_date for lot in sell_lots), default=None) if is_closed else None

    end_dt = date.fromisoformat(closed_date) if closed_date else eval_date
    holding_days = (
        max((end_dt - date.fromisoformat(opened_date)).days, 0) if opened_date else 0
    )

    fifo_status, per_lot_fifo = _build_fifo_status(
        fifo_queue,
        eval_date=eval_date,
        has_amount_data=False,
        is_closed=is_closed,
    )

    avg_cost_nav = round(last_avg_cost_nav, 4) if last_avg_cost_nav is not None else None
    remaining_ratio = (
        round(remaining_units / total_bought_units, 6) if total_bought_units > EPS else 0.0
    )

    realized_return_pct: float | None = (
        round(realized_unit_pnl / realized_unit_cost * 100.0, 2)
        if (has_confirmed_sell and not has_unconfirmed_sell_pnl and realized_unit_cost > EPS)
        else None
    )

    if is_closed:
        unrealized_return_pct: float | None = None
    elif (
        latest_nav is not None
        and latest_nav > 0
        and last_avg_cost_nav is not None
        and last_avg_cost_nav > EPS
    ):
        unrealized_return_pct = round((latest_nav / last_avg_cost_nav - 1.0) * 100.0, 2)
    else:
        unrealized_return_pct = None

    if is_closed:
        total_return_pct = realized_return_pct
    elif (
        not has_unconfirmed_sell_pnl
        and latest_nav is not None
        and latest_nav > 0
        and confirmed_total_bought_cost > EPS
        and confirmed_remaining_units > EPS
    ):
        unrealized_unit_pnl = confirmed_remaining_units * latest_nav - confirmed_remaining_cost
        total_return_pct = round(
            (realized_unit_pnl + unrealized_unit_pnl) / confirmed_total_bought_cost * 100.0, 2
        )
    else:
        total_return_pct = None

    return PositionAccountingResult(
        has_amount_data=False,
        is_closed=is_closed,
        opened_date=opened_date,
        closed_date=closed_date,
        holding_days=holding_days,
        avg_cost_nav=avg_cost_nav,
        remaining_shares=None,
        remaining_ratio=remaining_ratio,
        invested_capital=None,
        total_bought_amount=None,
        total_sold_amount=None,
        total_fees=None,
        realized_pnl=None,
        realized_return_pct=realized_return_pct,
        unrealized_pnl=None,
        unrealized_return_pct=unrealized_return_pct,
        total_pnl=None,
        total_return_pct=total_return_pct,
        fifo_fee_status=fifo_status,
        per_lot_fifo=per_lot_fifo,
        per_lot_realized_pnl={},
        per_lot_realized_return_pct=per_lot_realized_return_pct,
        per_lot_effective_shares={},
    )


def compute_portfolio_summary(
    serialized_positions: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate portfolio & closed-trade review summary across all Tracked Positions.

    - Closed win rate and average wave return % aggregate across ALL closed positions
      (both capital mode and percentage-only mode) that have a computable `total_return_pct`.
    - Monetary totals sum STRICTLY across positions with `has_amount_data == True`.
    """
    active_count = sum(1 for p in serialized_positions if p["status"] == "active")
    closed_positions = [p for p in serialized_positions if p["status"] == "closed"]
    closed_count = len(closed_positions)
    pending_nav_count = sum(int(p.get("pending_lot_count") or 0) for p in serialized_positions)
    exit_alert_count = sum(
        int(p.get("exit_alert_count") or 0)
        for p in serialized_positions
        if p["status"] == "active"
    )
    exit_warning_count = sum(
        int(p.get("exit_warning_count") or 0)
        for p in serialized_positions
        if p["status"] == "active"
    )
    exit_alert_position_count = sum(
        1
        for p in serialized_positions
        if p["status"] == "active" and int(p.get("exit_alert_count") or 0) > 0
    )

    closed_returns = [
        float(p["total_return_pct"])
        for p in closed_positions
        if p.get("total_return_pct") is not None
    ]
    closed_evaluated_count = len(closed_returns)
    closed_win_count = sum(1 for r in closed_returns if r > 0.0)
    closed_win_rate_pct = (
        round(closed_win_count / closed_evaluated_count * 100.0, 2)
        if closed_evaluated_count > 0
        else None
    )
    closed_avg_return_pct = (
        round(sum(closed_returns) / closed_evaluated_count, 2)
        if closed_evaluated_count > 0
        else None
    )

    amount_positions = [p for p in serialized_positions if p.get("has_amount_data") is True]
    amount_position_count = len(amount_positions)
    active_amount_position_count = sum(
        1 for p in amount_positions if p["status"] == "active"
    )

    if amount_position_count > 0:
        total_invested_capital: float | None = round(
            sum(
                float(p["invested_capital"])
                for p in amount_positions
                if p.get("invested_capital") is not None
            ),
            2,
        )
        total_unrealized_pnl: float | None = round(
            sum(
                float(p["unrealized_pnl"])
                for p in amount_positions
                if p.get("unrealized_pnl") is not None
            ),
            2,
        )
        total_realized_pnl: float | None = round(
            sum(
                float(p["realized_pnl"])
                for p in amount_positions
                if p.get("realized_pnl") is not None
            ),
            2,
        )
        total_pnl: float | None = round(
            (total_unrealized_pnl or 0.0) + (total_realized_pnl or 0.0), 2
        )
    else:
        total_invested_capital = None
        total_unrealized_pnl = None
        total_realized_pnl = None
        total_pnl = None

    return {
        "active_count": active_count,
        "closed_count": closed_count,
        "pending_nav_count": pending_nav_count,
        "exit_alert_count": exit_alert_count,
        "exit_warning_count": exit_warning_count,
        "exit_alert_position_count": exit_alert_position_count,
        "closed_win_count": closed_win_count,
        "closed_evaluated_count": closed_evaluated_count,
        "closed_win_rate_pct": closed_win_rate_pct,
        "closed_avg_return_pct": closed_avg_return_pct,
        "amount_position_count": amount_position_count,
        "active_amount_position_count": active_amount_position_count,
        "total_invested_capital": total_invested_capital,
        "total_unrealized_pnl": total_unrealized_pnl,
        "total_realized_pnl": total_realized_pnl,
        "total_pnl": total_pnl,
    }

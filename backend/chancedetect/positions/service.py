"""Position ledger service: Tracked Positions, Trade Lots, Pending NAV, Lookup & Sync."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from chancedetect.data.models import (
    NavStatus,
    OTCNavBar,
    TrackedPositionRecord,
    TradeLotRecord,
    TradeSide,
    now_iso,
)
from chancedetect.data.otc_nav_provider import (
    OTCNavFetchResult,
    OTCNavProvider,
    OTCNavProviderError,
)
from chancedetect.storage.bar_store import BarStore
from chancedetect.storage.sqlite_store import (
    _UNSET,
    ActivePositionExistsError,
    SQLiteStore,
)

logger = logging.getLogger(__name__)
SH_TZ = ZoneInfo("Asia/Shanghai")
_OTC_CODE_RE = re.compile(r"^\d{6}$")


class PositionNotFoundError(LookupError):
    """Raised when a Tracked Position id does not exist."""


class TradeLotNotFoundError(LookupError):
    """Raised when a Trade Lot id does not exist for a Tracked Position."""


class InvalidPositionInputError(ValueError):
    """Raised when input validation fails for position/lot operations."""


def _shanghai_today() -> date:
    return datetime.now(SH_TZ).date()


def _validate_otc_code(otc_code: str) -> str:
    code = otc_code.strip()
    if not _OTC_CODE_RE.fullmatch(code):
        raise InvalidPositionInputError("OTC fund code must be a 6-digit numeric code.")
    return code


def _validate_trade_date(trade_date: str) -> str:
    cleaned = trade_date.strip()
    try:
        return date.fromisoformat(cleaned).isoformat()
    except ValueError as exc:
        raise InvalidPositionInputError(
            f"Invalid trade_date '{trade_date}'; expected YYYY-MM-DD."
        ) from exc


def derive_amount_and_shares(
    *,
    side: TradeSide,
    nav: float | None,
    amount: float | None,
    shares: float | None,
    fee: float = 0.0,
) -> tuple[float | None, float | None]:
    """Derive missing shares or amount from official/manual NAV when the other is provided."""
    if nav is None or nav <= 0:
        return amount, shares
    if amount is not None and shares is None:
        if side == "buy":
            derived_shares = round(max(amount - fee, 0.0) / nav, 4)
        else:
            derived_shares = round((amount + fee) / nav, 4)
        return amount, derived_shares
    if shares is not None and amount is None:
        if side == "buy":
            derived_amount = round(shares * nav + fee, 2)
        else:
            derived_amount = round(max(shares * nav - fee, 0.0), 2)
        return derived_amount, shares
    return amount, shares


class PositionService:
    """Orchestrates Tracked Positions, Trade Lots, Pending NAV backfill, and OTC lookup."""

    def __init__(
        self,
        sqlite_store: SQLiteStore,
        bar_store: BarStore,
        nav_provider: OTCNavProvider,
        *,
        today_fn: Callable[[], date] | None = None,
    ) -> None:
        self.sqlite = sqlite_store
        self.bars = bar_store
        self.nav_provider = nav_provider
        self._today_fn = today_fn or _shanghai_today

    def today_str(self) -> str:
        return self._today_fn().isoformat()

    # ------------------------------------------------------------------
    # OTC Lookup
    # ------------------------------------------------------------------

    def lookup_otc(self, query: str = "", *, limit: int = 25) -> dict[str, Any]:
        """Search mapped OTC Counterparts and resolve arbitrary 6-digit codes via cache/provider."""
        q = query.strip()
        mapped_items = self.sqlite.search_otc_mappings(q, limit=limit)
        active_by_otc = self.sqlite.list_active_position_ids_by_otc()

        codes_to_check = [item["otc_code"] for item in mapped_items]
        latest_bars = self.sqlite.get_latest_otc_nav_bars(codes_to_check)

        enriched_items: list[dict[str, Any]] = []
        mapped_codes: set[str] = set()
        for item in mapped_items:
            code = item["otc_code"]
            mapped_codes.add(code)
            latest_bar = latest_bars.get(code)
            active_id = active_by_otc.get(code)
            enriched_items.append(
                {
                    **item,
                    "latest_nav": latest_bar.unit_nav if latest_bar else None,
                    "latest_nav_date": latest_bar.nav_date if latest_bar else None,
                    "has_active_position": active_id is not None,
                    "active_position_id": active_id,
                }
            )

        resolved_external: dict[str, Any] | None = None
        if _OTC_CODE_RE.fullmatch(q) and q not in mapped_codes:
            resolved_external = self._resolve_unmapped_otc_code(q, active_by_otc)
            if resolved_external is not None:
                enriched_items.insert(0, resolved_external)

        return {
            "query": q,
            "items": enriched_items,
            "resolved_external": resolved_external,
        }

    def _resolve_unmapped_otc_code(
        self, otc_code: str, active_by_otc: dict[str, int]
    ) -> dict[str, Any] | None:
        meta = self.sqlite.get_otc_fund_metadata(otc_code)
        latest_bar = self.sqlite.get_latest_otc_nav_bar(otc_code)
        otc_name = meta.otc_name if meta else ""
        latest_nav = latest_bar.unit_nav if latest_bar else None
        latest_nav_date = latest_bar.nav_date if latest_bar else (meta.last_nav_date if meta else None)

        if not otc_name or latest_nav is None:
            try:
                fetched = self.nav_provider.fetch_nav_history(otc_code)
                if fetched.otc_name:
                    otc_name = fetched.otc_name
                if fetched.bars:
                    last = fetched.bars[-1]
                    latest_nav = last.unit_nav
                    latest_nav_date = last.nav_date
                # Cache lightweight metadata without persisting full bars for untracked funds (ADR-0004)
                self.sqlite.upsert_otc_fund_metadata(
                    otc_code,
                    otc_name=otc_name,
                    last_nav_date=latest_nav_date,
                    last_synced_at=now_iso(),
                )
            except OTCNavProviderError as exc:
                logger.debug("External OTC lookup failed for %s: %s", otc_code, exc)
                if not otc_name and latest_nav is None:
                    return None

        suggested_proxy = None
        ex_fund = self.sqlite.get_exchange_fund_by_code(otc_code)
        if ex_fund is not None:
            suggested_proxy = {
                "symbol": ex_fund.symbol,
                "code": ex_fund.code,
                "name": ex_fund.name,
                "fund_type": ex_fund.fund_type,
                "index_code": ex_fund.index_code,
                "index_name": ex_fund.index_name,
            }

        active_id = active_by_otc.get(otc_code)
        return {
            "otc_code": otc_code,
            "otc_name": otc_name or f"场外基金 {otc_code}",
            "share_class": "OTHER",
            "mapping_type": None,
            "purchase_status": "",
            "fee_rate": "",
            "is_mapped": False,
            "suggested_proxy": suggested_proxy,
            "latest_nav": latest_nav,
            "latest_nav_date": latest_nav_date,
            "has_active_position": active_id is not None,
            "active_position_id": active_id,
        }

    # ------------------------------------------------------------------
    # Position & Lot Lifecycle CRUD
    # ------------------------------------------------------------------

    def create_position(
        self,
        *,
        otc_code: str,
        otc_name: str | None = None,
        exchange_symbol: Any = _UNSET,
        trade_date: str | None = None,
        nav: float | None = None,
        amount: float | None = None,
        shares: float | None = None,
        fee: float = 0.0,
        notes: str = "",
        lot_notes: str = "",
        entry_signal_snapshot: dict[str, Any] | None = None,
        custom_stop_loss_pct: float | None = None,
        custom_trailing_drawdown_pct: float | None = None,
        custom_take_profit_pct: float | None = None,
    ) -> dict[str, Any]:
        code = _validate_otc_code(otc_code)
        eff_date = _validate_trade_date(trade_date) if trade_date else self.today_str()

        # Fast-fail if an active position already exists for this otc_code
        existing_active = self.sqlite.get_active_position_by_otc_code(code)
        if existing_active is not None:
            raise ActivePositionExistsError(code, existing_active.id)

        mapped = self.sqlite.get_otc_mapping_with_proxy(code)

        # Resolve exchange_symbol: omitted -> auto-resolve from mapping; explicit None/"" -> unmapped
        resolved_exchange_symbol: str | None
        if exchange_symbol is _UNSET:
            resolved_exchange_symbol = (
                mapped["suggested_proxy"]["symbol"] if mapped else None
            )
        elif exchange_symbol is None or str(exchange_symbol).strip() == "":
            resolved_exchange_symbol = None
        else:
            resolved_exchange_symbol = str(exchange_symbol).strip()

        # Resolve otc_name from input -> mapping -> metadata cache
        resolved_name = (otc_name or "").strip()
        if not resolved_name and mapped:
            resolved_name = str(mapped["otc_name"]).strip()
        if not resolved_name:
            meta = self.sqlite.get_otc_fund_metadata(code)
            if meta and meta.otc_name:
                resolved_name = meta.otc_name

        # Resolve NAV & fetch on demand if needed (for official NAV or missing fund name)
        resolved_nav: float | None
        nav_status: NavStatus
        fetched_result: OTCNavFetchResult | None = None

        if nav is not None:
            if nav <= 0:
                raise InvalidPositionInputError("Manual NAV must be positive.")
            resolved_nav = float(nav)
            nav_status = "manual"
            if not resolved_name or self.sqlite.get_latest_otc_nav_bar(code) is None:
                fetched_result = self._fetch_and_store_nav(code)
        else:
            bar = self.sqlite.get_otc_nav_bar(code, eff_date)
            if bar is None:
                fetched_result = self._fetch_and_store_nav(code)
                bar = self.sqlite.get_otc_nav_bar(code, eff_date)
            if bar is not None:
                resolved_nav = bar.unit_nav
                nav_status = "confirmed"
            else:
                resolved_nav = None
                nav_status = "pending"

        if not resolved_name and fetched_result and fetched_result.otc_name:
            resolved_name = fetched_result.otc_name
        if not resolved_name:
            resolved_name = f"场外基金 {code}"

        eff_amount, eff_shares = derive_amount_and_shares(
            side="buy", nav=resolved_nav, amount=amount, shares=shares, fee=fee
        )

        pos, _ = self.sqlite.create_position_with_initial_lot(
            otc_code=code,
            otc_name=resolved_name,
            exchange_symbol=resolved_exchange_symbol,
            opened_date=eff_date,
            entry_signal_snapshot=entry_signal_snapshot or {},
            notes=notes,
            custom_stop_loss_pct=custom_stop_loss_pct,
            custom_trailing_drawdown_pct=custom_trailing_drawdown_pct,
            custom_take_profit_pct=custom_take_profit_pct,
            lot_trade_date=eff_date,
            lot_nav=resolved_nav,
            lot_nav_status=nav_status,
            lot_amount=eff_amount,
            lot_shares=eff_shares,
            lot_fee=fee,
            lot_notes=lot_notes,
        )
        return self._serialize_positions([pos])[0]

    def list_positions(self, *, status: str = "active") -> dict[str, Any]:
        if status not in ("active", "closed", "all"):
            raise InvalidPositionInputError("status must be one of: active, closed, all.")
        positions = self.sqlite.list_positions(status=status)
        serialized = self._serialize_positions(positions)

        all_positions = self.sqlite.list_positions(status="all")
        all_lots_map = self.sqlite.list_trade_lots_for_positions([p.id for p in all_positions])
        active_count = sum(1 for p in all_positions if p.status == "active")
        closed_count = sum(1 for p in all_positions if p.status == "closed")
        pending_nav_count = sum(
            1
            for lots in all_lots_map.values()
            for lot in lots
            if lot.nav_status == "pending"
        )
        return {
            "positions": serialized,
            "summary": {
                "active_count": active_count,
                "closed_count": closed_count,
                "pending_nav_count": pending_nav_count,
            },
        }

    def get_position(self, position_id: int) -> dict[str, Any] | None:
        pos = self.sqlite.get_position(position_id)
        if pos is None:
            return None
        return self._serialize_positions([pos])[0]

    def update_position(
        self,
        position_id: int,
        *,
        otc_name: Any = _UNSET,
        exchange_symbol: Any = _UNSET,
        notes: Any = _UNSET,
        custom_stop_loss_pct: Any = _UNSET,
        custom_trailing_drawdown_pct: Any = _UNSET,
        custom_take_profit_pct: Any = _UNSET,
    ) -> dict[str, Any]:
        cleaned_symbol = exchange_symbol
        if exchange_symbol is not _UNSET:
            if exchange_symbol is None or str(exchange_symbol).strip() == "":
                cleaned_symbol = None
            else:
                cleaned_symbol = str(exchange_symbol).strip()

        updated = self.sqlite.update_position(
            position_id,
            otc_name=otc_name,
            exchange_symbol=cleaned_symbol,
            notes=notes,
            custom_stop_loss_pct=custom_stop_loss_pct,
            custom_trailing_drawdown_pct=custom_trailing_drawdown_pct,
            custom_take_profit_pct=custom_take_profit_pct,
        )
        if updated is None:
            raise PositionNotFoundError(f"Position {position_id} not found")
        return self._serialize_positions([updated])[0]

    def delete_position(self, position_id: int) -> bool:
        return self.sqlite.delete_position(position_id)

    def add_trade_lot(
        self,
        position_id: int,
        *,
        side: TradeSide = "buy",
        trade_date: str | None = None,
        nav: float | None = None,
        amount: float | None = None,
        shares: float | None = None,
        sell_ratio: float | None = None,
        fee: float = 0.0,
        notes: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        pos = self.sqlite.get_position(position_id)
        if pos is None:
            raise PositionNotFoundError(f"Position {position_id} not found")

        eff_date = _validate_trade_date(trade_date) if trade_date else self.today_str()
        resolved_nav, nav_status = self._resolve_lot_nav(pos.otc_code, eff_date, manual_nav=nav)
        eff_amount, eff_shares = derive_amount_and_shares(
            side=side, nav=resolved_nav, amount=amount, shares=shares, fee=fee
        )

        lot = self.sqlite.create_trade_lot(
            position_id=position_id,
            side=side,
            trade_date=eff_date,
            nav=resolved_nav,
            nav_status=nav_status,
            amount=eff_amount,
            shares=eff_shares,
            sell_ratio=sell_ratio if side == "sell" else None,
            fee=fee,
            notes=notes,
        )
        self._sync_position_opened_date(position_id)
        serialized_pos = self.get_position(position_id)
        assert serialized_pos is not None
        serialized_lot = next(item for item in serialized_pos["lots"] if item["id"] == lot.id)
        return serialized_lot, serialized_pos

    def update_trade_lot(
        self,
        position_id: int,
        lot_id: int,
        *,
        side: Any = _UNSET,
        trade_date: Any = _UNSET,
        nav: Any = _UNSET,
        amount: Any = _UNSET,
        shares: Any = _UNSET,
        sell_ratio: Any = _UNSET,
        fee: Any = _UNSET,
        notes: Any = _UNSET,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        pos = self.sqlite.get_position(position_id)
        if pos is None:
            raise PositionNotFoundError(f"Position {position_id} not found")
        existing = self.sqlite.get_trade_lot(position_id, lot_id)
        if existing is None:
            raise TradeLotNotFoundError(f"Trade lot {lot_id} not found on position {position_id}")

        eff_side: TradeSide = existing.side if side is _UNSET else side
        eff_date = (
            existing.trade_date
            if trade_date is _UNSET
            else _validate_trade_date(str(trade_date))
        )
        eff_fee = existing.fee if fee is _UNSET else float(fee)

        # Determine NAV & nav_status transition
        if nav is not _UNSET:
            if nav is not None:
                if float(nav) <= 0:
                    raise InvalidPositionInputError("Manual NAV must be positive.")
                eff_nav: float | None = float(nav)
                eff_nav_status: NavStatus = "manual"
            else:
                # Explicitly cleared manual NAV -> re-resolve official NAV
                eff_nav, eff_nav_status = self._resolve_lot_nav(
                    pos.otc_code, eff_date, manual_nav=None
                )
        elif trade_date is not _UNSET and eff_date != existing.trade_date and existing.nav_status != "manual":
            eff_nav, eff_nav_status = self._resolve_lot_nav(
                pos.otc_code, eff_date, manual_nav=None
            )
        else:
            eff_nav = existing.nav
            eff_nav_status = existing.nav_status

        eff_amount = existing.amount if amount is _UNSET else amount
        eff_shares = existing.shares if shares is _UNSET else shares

        # Re-derive missing counterpart when one was updated or NAV changed
        if amount is not _UNSET and shares is _UNSET and eff_amount is not None:
            eff_shares = None
        elif shares is not _UNSET and amount is _UNSET and eff_shares is not None:
            eff_amount = None

        eff_amount, eff_shares = derive_amount_and_shares(
            side=eff_side,
            nav=eff_nav,
            amount=eff_amount,
            shares=eff_shares,
            fee=eff_fee,
        )

        eff_sell_ratio = existing.sell_ratio if sell_ratio is _UNSET else sell_ratio
        if eff_side == "buy":
            eff_sell_ratio = None

        updated_lot = self.sqlite.update_trade_lot(
            position_id,
            lot_id,
            side=eff_side,
            trade_date=eff_date,
            nav=eff_nav,
            nav_status=eff_nav_status,
            amount=eff_amount,
            shares=eff_shares,
            sell_ratio=eff_sell_ratio,
            fee=eff_fee,
            notes=notes,
        )
        if updated_lot is None:
            raise TradeLotNotFoundError(f"Trade lot {lot_id} not found on position {position_id}")

        self._sync_position_opened_date(position_id)
        serialized_pos = self.get_position(position_id)
        assert serialized_pos is not None
        serialized_lot = next(item for item in serialized_pos["lots"] if item["id"] == lot_id)
        return serialized_lot, serialized_pos

    def delete_trade_lot(self, position_id: int, lot_id: int) -> dict[str, Any]:
        pos = self.sqlite.get_position(position_id)
        if pos is None:
            raise PositionNotFoundError(f"Position {position_id} not found")
        deleted = self.sqlite.delete_trade_lot(position_id, lot_id)
        if not deleted:
            raise TradeLotNotFoundError(f"Trade lot {lot_id} not found on position {position_id}")
        self._sync_position_opened_date(position_id)
        serialized_pos = self.get_position(position_id)
        assert serialized_pos is not None
        return serialized_pos

    # ------------------------------------------------------------------
    # Tracked-Only OTC NAV Sync & Pending NAV Backfill (ADR-0004)
    # ------------------------------------------------------------------

    def sync_tracked_otc_navs(self, *, otc_code: str | None = None) -> dict[str, Any]:
        """Fetch official NAVs strictly for tracked OTC codes and backfill Pending NAV lots.

        Never overwrites manually overridden (`nav_status = 'manual'`) lots.
        """
        tracked_codes = self.sqlite.list_tracked_otc_codes()
        if otc_code is not None:
            target_code = _validate_otc_code(otc_code)
            target_codes = [c for c in tracked_codes if c == target_code]
        else:
            target_codes = tracked_codes

        if not target_codes:
            return {
                "refreshed_funds": 0,
                "backfilled_lots": 0,
                "updated_bars": 0,
                "errors": [],
            }

        run_id = self.sqlite.start_sync_run("otc_nav", total_targets=len(target_codes))
        refreshed_funds = 0
        backfilled_lots = 0
        updated_bars = 0
        errors: list[dict[str, str]] = []
        sync_error_rows: list[tuple[str, str, str, str]] = []

        for code in target_codes:
            try:
                result = self.nav_provider.fetch_nav_history(code)
            except Exception as exc:
                msg = str(exc)
                logger.warning("Failed OTC NAV sync for %s: %s", code, msg)
                errors.append({"otc_code": code, "error": msg})
                sync_error_rows.append((code, "otc_nav", "eastmoney_pingzhongdata", msg))
                continue

            count = self.sqlite.upsert_otc_nav_bars(code, result.bars)
            updated_bars += count
            last_nav_date = result.bars[-1].nav_date if result.bars else None
            self.sqlite.upsert_otc_fund_metadata(
                code,
                otc_name=result.otc_name,
                last_nav_date=last_nav_date,
                last_synced_at=now_iso(),
            )
            if result.otc_name:
                self.sqlite.backfill_missing_position_names(code, result.otc_name)
            refreshed_funds += 1

            # Backfill strictly `pending` lots (never touch `manual` or `confirmed`)
            pending_lots = self.sqlite.list_pending_trade_lots(otc_code=code)
            for lot, _ in pending_lots:
                bar = self.sqlite.get_otc_nav_bar(code, lot.trade_date)
                if bar is None:
                    continue
                eff_amount, eff_shares = derive_amount_and_shares(
                    side=lot.side,
                    nav=bar.unit_nav,
                    amount=lot.amount,
                    shares=lot.shares,
                    fee=lot.fee,
                )
                self.sqlite.update_trade_lot(
                    lot.position_id,
                    lot.id,
                    nav=bar.unit_nav,
                    nav_status="confirmed",
                    amount=eff_amount,
                    shares=eff_shares,
                )
                backfilled_lots += 1

        if sync_error_rows:
            self.sqlite.record_sync_errors(run_id, sync_error_rows)

        status = "success" if not errors else ("partial" if refreshed_funds > 0 else "failed")
        summary = {
            "refreshed_funds": refreshed_funds,
            "backfilled_lots": backfilled_lots,
            "updated_bars": updated_bars,
            "error_count": len(errors),
        }
        self.sqlite.finish_sync_run(
            run_id,
            status=status,
            total_targets=len(target_codes),
            succeeded_count=refreshed_funds,
            failed_count=len(errors),
            primary_source_count=refreshed_funds,
            summary=summary,
        )

        return {
            "refreshed_funds": refreshed_funds,
            "backfilled_lots": backfilled_lots,
            "updated_bars": updated_bars,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    def _fetch_and_store_nav(self, otc_code: str) -> OTCNavFetchResult | None:
        try:
            result = self.nav_provider.fetch_nav_history(otc_code)
        except OTCNavProviderError as exc:
            logger.warning("On-demand OTC NAV fetch failed for %s: %s", otc_code, exc)
            return None
        if result.bars:
            self.sqlite.upsert_otc_nav_bars(otc_code, result.bars)
        last_nav_date = result.bars[-1].nav_date if result.bars else None
        self.sqlite.upsert_otc_fund_metadata(
            otc_code,
            otc_name=result.otc_name,
            last_nav_date=last_nav_date,
            last_synced_at=now_iso(),
        )
        return result

    def _resolve_lot_nav(
        self, otc_code: str, trade_date: str, *, manual_nav: float | None
    ) -> tuple[float | None, NavStatus]:
        if manual_nav is not None:
            if manual_nav <= 0:
                raise InvalidPositionInputError("Manual NAV must be positive.")
            return float(manual_nav), "manual"

        bar = self.sqlite.get_otc_nav_bar(otc_code, trade_date)
        if bar is None:
            self._fetch_and_store_nav(otc_code)
            bar = self.sqlite.get_otc_nav_bar(otc_code, trade_date)
        if bar is not None:
            return bar.unit_nav, "confirmed"
        return None, "pending"

    def _sync_position_opened_date(self, position_id: int) -> None:
        lots = self.sqlite.list_trade_lots(position_id)
        buy_dates = [lot.trade_date for lot in lots if lot.side == "buy"]
        if not buy_dates:
            return
        earliest = min(buy_dates)
        pos = self.sqlite.get_position(position_id)
        if pos is not None and pos.opened_date != earliest:
            self.sqlite.update_position(position_id, opened_date=earliest)

    def _load_latest_proxy_bars(
        self, symbols: list[str]
    ) -> dict[str, dict[str, Any]]:
        unique_syms = sorted({s for s in symbols if s})
        if not unique_syms or not self.bars.has_daily_bars():
            return {}
        df = self.bars.load_daily_bars(symbols=unique_syms)
        if df.is_empty():
            return {}
        latest_by_sym: dict[str, dict[str, Any]] = {}
        for row in df.group_by("symbol").tail(1).iter_rows(named=True):
            sym = str(row["symbol"])
            dt = row["trade_date"]
            latest_by_sym[sym] = {
                "trade_date": dt.isoformat() if isinstance(dt, date) else str(dt),
                "close": float(row["close"]),
                "pct_chg": float(row["pct_chg"]) if row["pct_chg"] is not None else 0.0,
            }
        return latest_by_sym

    def _serialize_positions(
        self, positions: list[TrackedPositionRecord]
    ) -> list[dict[str, Any]]:
        if not positions:
            return []
        pos_ids = [p.id for p in positions]
        lots_by_pos = self.sqlite.list_trade_lots_for_positions(pos_ids)
        latest_nav_by_code = self.sqlite.get_latest_otc_nav_bars(
            sorted({p.otc_code for p in positions})
        )
        proxy_symbols = [p.exchange_symbol for p in positions if p.exchange_symbol]
        latest_proxy_by_sym = self._load_latest_proxy_bars(proxy_symbols)

        serialized: list[dict[str, Any]] = []
        for pos in positions:
            lots = lots_by_pos.get(pos.id, [])
            latest_bar: OTCNavBar | None = latest_nav_by_code.get(pos.otc_code)
            proxy_fund = (
                self.sqlite.get_exchange_fund(pos.exchange_symbol)
                if pos.exchange_symbol
                else None
            )
            proxy_bar = (
                latest_proxy_by_sym.get(pos.exchange_symbol)
                if pos.exchange_symbol
                else None
            )
            pending_count = sum(1 for lot in lots if lot.nav_status == "pending")

            serialized_lots = [
                self._serialize_lot(lot, latest_bar=latest_bar, proxy_bar=proxy_bar)
                for lot in lots
            ]

            serialized.append(
                {
                    "id": pos.id,
                    "otc_code": pos.otc_code,
                    "otc_name": pos.otc_name,
                    "exchange_symbol": pos.exchange_symbol,
                    "proxy_fund": (
                        {
                            "symbol": proxy_fund.symbol,
                            "code": proxy_fund.code,
                            "name": proxy_fund.name,
                            "fund_type": proxy_fund.fund_type,
                            "index_code": proxy_fund.index_code,
                            "index_name": proxy_fund.index_name,
                        }
                        if proxy_fund
                        else None
                    ),
                    "status": pos.status,
                    "opened_date": pos.opened_date,
                    "closed_date": pos.closed_date,
                    "entry_signal_snapshot": pos.entry_signal_snapshot,
                    "notes": pos.notes,
                    "custom_stop_loss_pct": pos.custom_stop_loss_pct,
                    "custom_trailing_drawdown_pct": pos.custom_trailing_drawdown_pct,
                    "custom_take_profit_pct": pos.custom_take_profit_pct,
                    "latest_nav": latest_bar.unit_nav if latest_bar else None,
                    "latest_nav_date": latest_bar.nav_date if latest_bar else None,
                    "latest_daily_return_pct": (
                        latest_bar.daily_return_pct if latest_bar else None
                    ),
                    "latest_proxy_close": proxy_bar["close"] if proxy_bar else None,
                    "latest_proxy_pct_chg": proxy_bar["pct_chg"] if proxy_bar else None,
                    "latest_proxy_date": proxy_bar["trade_date"] if proxy_bar else None,
                    "lot_count": len(lots),
                    "pending_lot_count": pending_count,
                    "has_pending_nav": pending_count > 0,
                    "lots": serialized_lots,
                    "created_at": pos.created_at,
                    "updated_at": pos.updated_at,
                }
            )
        return serialized

    @staticmethod
    def _serialize_lot(
        lot: TradeLotRecord,
        *,
        latest_bar: OTCNavBar | None,
        proxy_bar: dict[str, Any] | None,
    ) -> dict[str, Any]:
        is_pending = lot.nav_status == "pending"
        return {
            "id": lot.id,
            "position_id": lot.position_id,
            "side": lot.side,
            "trade_date": lot.trade_date,
            "nav": lot.nav,
            "nav_status": lot.nav_status,
            "amount": lot.amount,
            "shares": lot.shares,
            "sell_ratio": lot.sell_ratio,
            "fee": lot.fee,
            "notes": lot.notes,
            "reference_nav": latest_bar.unit_nav if (is_pending and latest_bar) else None,
            "reference_nav_date": latest_bar.nav_date if (is_pending and latest_bar) else None,
            "reference_proxy_pct_chg": (
                proxy_bar["pct_chg"] if (is_pending and proxy_bar) else None
            ),
            "created_at": lot.created_at,
            "updated_at": lot.updated_at,
        }

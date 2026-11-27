"""SQLite (WAL) store for fund catalog, OTC mappings, sync telemetry, presets, and snapshots."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from chancedetect.data.models import (
    ExchangeFund,
    NavStatus,
    OTCFundMapping,
    OTCFundMetadata,
    OTCNavBar,
    ScanSnapshotRecord,
    StrategyPresetRecord,
    SyncErrorRecord,
    SyncRunRecord,
    TrackedPositionRecord,
    TradeLotRecord,
    TradeSide,
    now_iso,
)


class UniverseDropProtectionError(RuntimeError):
    """Raised when a metadata refresh would drop an abnormally large share of mapped funds."""


class ActivePositionExistsError(RuntimeError):
    """Raised when attempting to create a second active Tracked Position for the same otc_code."""

    def __init__(self, otc_code: str, existing_position_id: int | None = None) -> None:
        self.otc_code = otc_code
        self.existing_position_id = existing_position_id
        msg = f"An active Tracked Position already exists for OTC fund {otc_code}."
        if existing_position_id is not None:
            msg += f" (position_id={existing_position_id})"
        super().__init__(msg)


_UNSET = object()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS exchange_funds (
    symbol TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    exchange TEXT NOT NULL,
    name TEXT NOT NULL,
    fund_type TEXT NOT NULL,
    index_code TEXT NOT NULL DEFAULT '',
    index_name TEXT NOT NULL DEFAULT '',
    company_id TEXT NOT NULL DEFAULT '',
    company_name TEXT NOT NULL DEFAULT '',
    establish_date TEXT NOT NULL DEFAULT '',
    fund_scale REAL,
    latest_trade_date TEXT,
    avg_amount_20d REAL,
    is_active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_exchange_funds_index_code ON exchange_funds(index_code);
CREATE INDEX IF NOT EXISTS idx_exchange_funds_code ON exchange_funds(code);

CREATE TABLE IF NOT EXISTS otc_fund_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_symbol TEXT NOT NULL,
    otc_code TEXT NOT NULL,
    otc_name TEXT NOT NULL,
    share_class TEXT NOT NULL DEFAULT 'OTHER',
    mapping_type TEXT NOT NULL,
    purchase_status TEXT NOT NULL DEFAULT '',
    fee_rate TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE(exchange_symbol, otc_code),
    FOREIGN KEY (exchange_symbol) REFERENCES exchange_funds(symbol) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_otc_mappings_exchange ON otc_fund_mappings(exchange_symbol);
CREATE INDEX IF NOT EXISTS idx_otc_mappings_otc_code ON otc_fund_mappings(otc_code);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    total_targets INTEGER NOT NULL DEFAULT 0,
    succeeded_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    primary_source_count INTEGER NOT NULL DEFAULT 0,
    fallback_source_count INTEGER NOT NULL DEFAULT 0,
    drift_refreshed_count INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS sync_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    stage TEXT NOT NULL,
    provider TEXT NOT NULL,
    error_message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES sync_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sync_errors_run_id ON sync_errors(run_id);

CREATE TABLE IF NOT EXISTS strategy_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    preset_id INTEGER,
    config_json TEXT NOT NULL,
    hit_fund_count INTEGER NOT NULL,
    hit_index_count INTEGER NOT NULL,
    elapsed_ms REAL NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scan_snapshots_trade_date ON scan_snapshots(trade_date DESC);

CREATE TABLE IF NOT EXISTS tracked_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    otc_code TEXT NOT NULL,
    otc_name TEXT NOT NULL DEFAULT '',
    exchange_symbol TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
    opened_date TEXT NOT NULL,
    closed_date TEXT,
    entry_signal_snapshot_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT NOT NULL DEFAULT '',
    custom_stop_loss_pct REAL,
    custom_trailing_drawdown_pct REAL,
    custom_take_profit_pct REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tracked_positions_one_active_per_otc
    ON tracked_positions(otc_code)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_tracked_positions_status
    ON tracked_positions(status, opened_date DESC, id DESC);

CREATE TABLE IF NOT EXISTS trade_lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    trade_date TEXT NOT NULL,
    nav REAL,
    nav_status TEXT NOT NULL DEFAULT 'pending' CHECK (nav_status IN ('pending', 'confirmed', 'manual')),
    amount REAL,
    shares REAL,
    sell_ratio REAL,
    fee REAL NOT NULL DEFAULT 0.0,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (position_id) REFERENCES tracked_positions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_trade_lots_position_date
    ON trade_lots(position_id, trade_date ASC, id ASC);
CREATE INDEX IF NOT EXISTS idx_trade_lots_pending
    ON trade_lots(nav_status, trade_date);

CREATE TABLE IF NOT EXISTS otc_nav_bars (
    otc_code TEXT NOT NULL,
    nav_date TEXT NOT NULL,
    unit_nav REAL NOT NULL,
    accumulated_nav REAL,
    daily_return_pct REAL,
    dividend_note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (otc_code, nav_date)
);

CREATE TABLE IF NOT EXISTS otc_fund_metadata (
    otc_code TEXT PRIMARY KEY,
    otc_name TEXT NOT NULL DEFAULT '',
    last_nav_date TEXT,
    last_synced_at TEXT,
    updated_at TEXT NOT NULL
);
"""


class SQLiteStore:
    """Thread-safe SQLite metadata store using WAL journal mode."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA busy_timeout=5000;")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)

    # ------------------------------------------------------------------
    # Exchange Funds & OTC Mappings
    # ------------------------------------------------------------------

    def replace_universe(
        self,
        funds: list[ExchangeFund],
        mappings: list[OTCFundMapping],
        *,
        force: bool = False,
        min_retention_ratio: float = 0.7,
    ) -> dict[str, int]:
        """Atomically upsert the exchange fund universe and strict OTC mappings.

        Protects against accidental catastrophic shrinkage when upstream APIs misbehave.
        Preserves previously synced `latest_trade_date` and `avg_amount_20d` metrics.
        """
        ts = now_iso()
        # Only keep funds that actually have at least one OTC mapping
        mapped_symbols = {m.exchange_symbol for m in mappings}
        valid_funds = [f for f in funds if f.symbol in mapped_symbols]
        valid_fund_symbols = {f.symbol for f in valid_funds}
        valid_mappings = [m for m in mappings if m.exchange_symbol in valid_fund_symbols]

        with self._connect() as conn:
            existing_rows = conn.execute(
                "SELECT symbol, latest_trade_date, avg_amount_20d FROM exchange_funds WHERE is_active = 1"
            ).fetchall()
            existing_count = len(existing_rows)
            existing_metrics = {
                row["symbol"]: (row["latest_trade_date"], row["avg_amount_20d"])
                for row in existing_rows
            }

            if (
                not force
                and existing_count >= 20
                and len(valid_funds) < int(existing_count * min_retention_ratio)
            ):
                raise UniverseDropProtectionError(
                    f"Refusing universe replacement: new count {len(valid_funds)} is below "
                    f"{int(min_retention_ratio * 100)}% of existing {existing_count} funds."
                )

            # Mark all existing as inactive first, then reactivate/upsert current ones
            conn.execute("UPDATE exchange_funds SET is_active = 0")

            for fund in valid_funds:
                prev_date, prev_amt = existing_metrics.get(fund.symbol, (None, None))
                conn.execute(
                    """
                    INSERT INTO exchange_funds (
                        symbol, code, exchange, name, fund_type, index_code, index_name,
                        company_id, company_name, establish_date, fund_scale,
                        latest_trade_date, avg_amount_20d, is_active, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        code=excluded.code,
                        exchange=excluded.exchange,
                        name=excluded.name,
                        fund_type=excluded.fund_type,
                        index_code=excluded.index_code,
                        index_name=excluded.index_name,
                        company_id=excluded.company_id,
                        company_name=excluded.company_name,
                        establish_date=excluded.establish_date,
                        fund_scale=COALESCE(excluded.fund_scale, exchange_funds.fund_scale),
                        latest_trade_date=COALESCE(excluded.latest_trade_date, exchange_funds.latest_trade_date),
                        avg_amount_20d=COALESCE(excluded.avg_amount_20d, exchange_funds.avg_amount_20d),
                        is_active=1,
                        updated_at=excluded.updated_at
                    """,
                    (
                        fund.symbol,
                        fund.code,
                        fund.exchange,
                        fund.name,
                        fund.fund_type,
                        fund.index_code,
                        fund.index_name,
                        fund.company_id,
                        fund.company_name,
                        fund.establish_date,
                        fund.fund_scale,
                        fund.latest_trade_date or prev_date,
                        fund.avg_amount_20d if fund.avg_amount_20d is not None else prev_amt,
                        ts,
                    ),
                )

            # Replace mappings cleanly for all active funds
            conn.execute("DELETE FROM otc_fund_mappings")
            conn.executemany(
                """
                INSERT OR REPLACE INTO otc_fund_mappings (
                    exchange_symbol, otc_code, otc_name, share_class,
                    mapping_type, purchase_status, fee_rate, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        m.exchange_symbol,
                        m.otc_code,
                        m.otc_name,
                        m.share_class,
                        m.mapping_type,
                        m.purchase_status,
                        m.fee_rate,
                        ts,
                    )
                    for m in valid_mappings
                ],
            )

        return {
            "previous_fund_count": existing_count,
            "active_fund_count": len(valid_funds),
            "mapping_count": len(valid_mappings),
        }

    def update_fund_bar_metrics(self, metrics: dict[str, tuple[str, float | None]]) -> None:
        """Batch update latest_trade_date and avg_amount_20d for symbols after bar sync."""
        if not metrics:
            return
        ts = now_iso()
        with self._connect() as conn:
            conn.executemany(
                """
                UPDATE exchange_funds
                SET latest_trade_date = ?, avg_amount_20d = ?, updated_at = ?
                WHERE symbol = ?
                """,
                [(dt, amt, ts, sym) for sym, (dt, amt) in metrics.items()],
            )

    def list_exchange_funds(self, *, active_only: bool = True) -> list[ExchangeFund]:
        sql = "SELECT * FROM exchange_funds"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY COALESCE(avg_amount_20d, 0) DESC, symbol ASC"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [self._row_to_fund(r) for r in rows]

    def get_exchange_fund(self, symbol: str) -> ExchangeFund | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM exchange_funds WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return self._row_to_fund(row) if row else None

    def get_mappings_by_exchange(self, symbol: str) -> list[OTCFundMapping]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM otc_fund_mappings WHERE exchange_symbol = ? ORDER BY share_class, otc_code",
                (symbol,),
            ).fetchall()
        return [self._row_to_mapping(r) for r in rows]

    def get_all_mappings_grouped(self) -> dict[str, list[OTCFundMapping]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM otc_fund_mappings ORDER BY exchange_symbol, share_class, otc_code"
            ).fetchall()
        grouped: dict[str, list[OTCFundMapping]] = {}
        for row in rows:
            m = self._row_to_mapping(row)
            grouped.setdefault(m.exchange_symbol, []).append(m)
        return grouped

    def search_funds(
        self,
        query: str = "",
        *,
        fund_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Search active exchange funds by symbol, code, name, index, or mapped OTC code/name."""
        params: list[Any] = []
        where = ["ef.is_active = 1"]
        if fund_type in ("ETF", "LOF"):
            where.append("ef.fund_type = ?")
            params.append(fund_type)
        if query.strip():
            like = f"%{query.strip()}%"
            where.append(
                """(
                    ef.symbol LIKE ? OR ef.code LIKE ? OR ef.name LIKE ?
                    OR ef.index_code LIKE ? OR ef.index_name LIKE ?
                    OR EXISTS (
                        SELECT 1 FROM otc_fund_mappings om
                        WHERE om.exchange_symbol = ef.symbol
                          AND (om.otc_code LIKE ? OR om.otc_name LIKE ?)
                    )
                )"""
            )
            params.extend([like] * 7)

        where_sql = " AND ".join(where)
        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM exchange_funds ef WHERE {where_sql}",
                params,
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT ef.*
                FROM exchange_funds ef
                WHERE {where_sql}
                ORDER BY COALESCE(ef.avg_amount_20d, 0) DESC, ef.symbol ASC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
            funds = [self._row_to_fund(r) for r in rows]
            if not funds:
                return [], int(total)
            placeholders = ",".join("?" for _ in funds)
            m_rows = conn.execute(
                f"""
                SELECT * FROM otc_fund_mappings
                WHERE exchange_symbol IN ({placeholders})
                ORDER BY share_class, otc_code
                """,
                [f.symbol for f in funds],
            ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for mr in m_rows:
            m = self._row_to_mapping(mr)
            grouped.setdefault(m.exchange_symbol, []).append(m.model_dump())
        items = [
            {**f.model_dump(), "otc_mappings": grouped.get(f.symbol, [])}
            for f in funds
        ]
        return items, int(total)

    def get_universe_stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_funds,
                    SUM(CASE WHEN fund_type = 'ETF' THEN 1 ELSE 0 END) AS etf_count,
                    SUM(CASE WHEN fund_type = 'LOF' THEN 1 ELSE 0 END) AS lof_count,
                    COUNT(DISTINCT CASE WHEN index_code != '' THEN index_code ELSE symbol END) AS index_count,
                    MAX(latest_trade_date) AS latest_trade_date
                FROM exchange_funds
                WHERE is_active = 1
                """
            ).fetchone()
            otc_count = conn.execute("SELECT COUNT(DISTINCT otc_code) FROM otc_fund_mappings").fetchone()[0]
            mapping_count = conn.execute("SELECT COUNT(*) FROM otc_fund_mappings").fetchone()[0]
        return {
            "total_exchange_funds": int(row["total_funds"] or 0),
            "etf_count": int(row["etf_count"] or 0),
            "lof_count": int(row["lof_count"] or 0),
            "unique_index_count": int(row["index_count"] or 0),
            "unique_otc_funds": int(otc_count or 0),
            "total_mappings": int(mapping_count or 0),
            "latest_trade_date": row["latest_trade_date"],
        }

    # ------------------------------------------------------------------
    # Sync Runs & Errors
    # ------------------------------------------------------------------

    def start_sync_run(self, job_type: str, total_targets: int = 0) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO sync_runs (job_type, status, started_at, total_targets)
                VALUES (?, 'running', ?, ?)
                """,
                (job_type, now_iso(), total_targets),
            )
            return int(cur.lastrowid)

    def finish_sync_run(
        self,
        run_id: int,
        *,
        status: str,
        total_targets: int,
        succeeded_count: int,
        failed_count: int,
        primary_source_count: int = 0,
        fallback_source_count: int = 0,
        drift_refreshed_count: int = 0,
        summary: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sync_runs
                SET status = ?,
                    finished_at = ?,
                    total_targets = ?,
                    succeeded_count = ?,
                    failed_count = ?,
                    primary_source_count = ?,
                    fallback_source_count = ?,
                    drift_refreshed_count = ?,
                    summary_json = ?
                WHERE id = ?
                """,
                (
                    status,
                    now_iso(),
                    total_targets,
                    succeeded_count,
                    failed_count,
                    primary_source_count,
                    fallback_source_count,
                    drift_refreshed_count,
                    json.dumps(summary or {}, ensure_ascii=False),
                    run_id,
                ),
            )

    def record_sync_errors(self, run_id: int, errors: list[tuple[str, str, str, str]]) -> None:
        """errors: list of (symbol, stage, provider, error_message)."""
        if not errors:
            return
        ts = now_iso()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO sync_errors (run_id, symbol, stage, provider, error_message, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [(run_id, sym, stage, prov, msg[:500], ts) for sym, stage, prov, msg in errors],
            )

    def list_sync_runs(self, limit: int = 20) -> list[SyncRunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            SyncRunRecord(
                id=r["id"],
                job_type=r["job_type"],
                status=r["status"],
                started_at=r["started_at"],
                finished_at=r["finished_at"],
                total_targets=r["total_targets"],
                succeeded_count=r["succeeded_count"],
                failed_count=r["failed_count"],
                primary_source_count=r["primary_source_count"],
                fallback_source_count=r["fallback_source_count"],
                drift_refreshed_count=r["drift_refreshed_count"],
                summary=json.loads(r["summary_json"] or "{}"),
            )
            for r in rows
        ]

    def list_sync_errors(self, *, run_id: int | None = None, limit: int = 50) -> list[SyncErrorRecord]:
        with self._connect() as conn:
            if run_id is not None:
                rows = conn.execute(
                    "SELECT * FROM sync_errors WHERE run_id = ? ORDER BY id DESC LIMIT ?",
                    (run_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sync_errors ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            SyncErrorRecord(
                id=r["id"],
                run_id=r["run_id"],
                symbol=r["symbol"],
                stage=r["stage"],
                provider=r["provider"],
                error_message=r["error_message"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Strategy Presets
    # ------------------------------------------------------------------

    def save_preset(
        self, name: str, description: str, config: dict[str, Any]
    ) -> StrategyPresetRecord:
        ts = now_iso()
        cfg_str = json.dumps(config, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO strategy_presets (name, description, config_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    description = excluded.description,
                    config_json = excluded.config_json,
                    updated_at = excluded.updated_at
                """,
                (name, description, cfg_str, ts, ts),
            )
            row = conn.execute(
                "SELECT * FROM strategy_presets WHERE name = ?",
                (name,),
            ).fetchone()
        return self._row_to_preset(row)

    def list_presets(self) -> list[StrategyPresetRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM strategy_presets ORDER BY updated_at DESC, id DESC"
            ).fetchall()
        return [self._row_to_preset(r) for r in rows]

    def delete_preset(self, preset_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM strategy_presets WHERE id = ?", (preset_id,))
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Scan Snapshots
    # ------------------------------------------------------------------

    def save_scan_snapshot(
        self,
        *,
        trade_date: str,
        preset_id: int | None,
        config: dict[str, Any],
        hit_fund_count: int,
        hit_index_count: int,
        elapsed_ms: float,
        result: dict[str, Any],
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO scan_snapshots (
                    trade_date, preset_id, config_json, hit_fund_count,
                    hit_index_count, elapsed_ms, result_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_date,
                    preset_id,
                    json.dumps(config, ensure_ascii=False),
                    hit_fund_count,
                    hit_index_count,
                    elapsed_ms,
                    json.dumps(result, ensure_ascii=False),
                    now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def list_scan_snapshots(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, trade_date, preset_id, hit_fund_count, hit_index_count, elapsed_ms, created_at
                FROM scan_snapshots
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_scan_snapshot(self, snapshot_id: int) -> ScanSnapshotRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM scan_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
        if not row:
            return None
        return ScanSnapshotRecord(
            id=row["id"],
            trade_date=row["trade_date"],
            preset_id=row["preset_id"],
            config=json.loads(row["config_json"]),
            hit_fund_count=row["hit_fund_count"],
            hit_index_count=row["hit_index_count"],
            elapsed_ms=row["elapsed_ms"],
            result=json.loads(row["result_json"]),
            created_at=row["created_at"],
        )

    # ------------------------------------------------------------------
    # Tracked Positions, Trade Lots, OTC NAV Bars & OTC Lookup
    # ------------------------------------------------------------------

    def get_otc_mapping_with_proxy(self, otc_code: str) -> dict[str, Any] | None:
        """Return primary mapped OTC record and its linked ExchangeFund for an exact otc_code."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT om.*,
                       ef.symbol AS ef_symbol, ef.code AS ef_code, ef.name AS ef_name,
                       ef.fund_type AS ef_fund_type, ef.index_code AS ef_index_code,
                       ef.index_name AS ef_index_name, ef.avg_amount_20d AS ef_avg_amount_20d
                FROM otc_fund_mappings om
                JOIN exchange_funds ef ON ef.symbol = om.exchange_symbol
                WHERE om.otc_code = ? AND ef.is_active = 1
                ORDER BY COALESCE(ef.avg_amount_20d, 0) DESC, ef.symbol ASC
                LIMIT 1
                """,
                (otc_code,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_otc_lookup_dict(row)

    def search_otc_mappings(self, query: str = "", *, limit: int = 25) -> list[dict[str, Any]]:
        """Search mapped OTC counterparts by OTC code/name or linked On-Exchange Proxy Fund."""
        params: list[Any] = []
        where = ["ef.is_active = 1"]
        q = query.strip()
        if q:
            like = f"%{q}%"
            where.append(
                """(
                    om.otc_code LIKE ? OR om.otc_name LIKE ?
                    OR ef.symbol LIKE ? OR ef.code LIKE ? OR ef.name LIKE ?
                    OR ef.index_code LIKE ? OR ef.index_name LIKE ?
                )"""
            )
            params.extend([like] * 7)
        where_sql = " AND ".join(where)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT om.*,
                       ef.symbol AS ef_symbol, ef.code AS ef_code, ef.name AS ef_name,
                       ef.fund_type AS ef_fund_type, ef.index_code AS ef_index_code,
                       ef.index_name AS ef_index_name, ef.avg_amount_20d AS ef_avg_amount_20d
                FROM otc_fund_mappings om
                JOIN exchange_funds ef ON ef.symbol = om.exchange_symbol
                WHERE {where_sql}
                ORDER BY
                    CASE WHEN om.otc_code = ? THEN 0 WHEN om.otc_code LIKE ? THEN 1 ELSE 2 END,
                    CASE WHEN om.share_class = 'C' THEN 0 WHEN om.share_class = 'A' THEN 1 ELSE 2 END,
                    COALESCE(ef.avg_amount_20d, 0) DESC,
                    om.otc_code ASC
                LIMIT ?
                """,
                [*params, q, f"{q}%", limit],
            ).fetchall()
        seen: set[str] = set()
        results: list[dict[str, Any]] = []
        for row in rows:
            code = row["otc_code"]
            if code in seen:
                continue
            seen.add(code)
            results.append(self._row_to_otc_lookup_dict(row))
        return results

    def get_exchange_fund_by_code(self, code: str) -> ExchangeFund | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM exchange_funds WHERE code = ? AND is_active = 1 LIMIT 1",
                (code,),
            ).fetchone()
        return self._row_to_fund(row) if row else None

    def create_position_with_initial_lot(
        self,
        *,
        otc_code: str,
        otc_name: str,
        exchange_symbol: str | None,
        opened_date: str,
        entry_signal_snapshot: dict[str, Any],
        notes: str,
        custom_stop_loss_pct: float | None,
        custom_trailing_drawdown_pct: float | None,
        custom_take_profit_pct: float | None,
        lot_trade_date: str,
        lot_nav: float | None,
        lot_nav_status: NavStatus,
        lot_amount: float | None,
        lot_shares: float | None,
        lot_fee: float,
        lot_notes: str,
    ) -> tuple[TrackedPositionRecord, TradeLotRecord]:
        ts = now_iso()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM tracked_positions WHERE otc_code = ? AND status = 'active' LIMIT 1",
                (otc_code,),
            ).fetchone()
            if existing is not None:
                raise ActivePositionExistsError(otc_code, int(existing["id"]))

            try:
                cur_pos = conn.execute(
                    """
                    INSERT INTO tracked_positions (
                        otc_code, otc_name, exchange_symbol, status, opened_date, closed_date,
                        entry_signal_snapshot_json, notes,
                        custom_stop_loss_pct, custom_trailing_drawdown_pct, custom_take_profit_pct,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 'active', ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        otc_code,
                        otc_name,
                        exchange_symbol,
                        opened_date,
                        json.dumps(entry_signal_snapshot, ensure_ascii=False),
                        notes,
                        custom_stop_loss_pct,
                        custom_trailing_drawdown_pct,
                        custom_take_profit_pct,
                        ts,
                        ts,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ActivePositionExistsError(otc_code) from exc

            pos_id = int(cur_pos.lastrowid)
            cur_lot = conn.execute(
                """
                INSERT INTO trade_lots (
                    position_id, side, trade_date, nav, nav_status,
                    amount, shares, sell_ratio, fee, notes, created_at, updated_at
                ) VALUES (?, 'buy', ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    pos_id,
                    lot_trade_date,
                    lot_nav,
                    lot_nav_status,
                    lot_amount,
                    lot_shares,
                    lot_fee,
                    lot_notes,
                    ts,
                    ts,
                ),
            )
            lot_id = int(cur_lot.lastrowid)
            pos_row = conn.execute("SELECT * FROM tracked_positions WHERE id = ?", (pos_id,)).fetchone()
            lot_row = conn.execute("SELECT * FROM trade_lots WHERE id = ?", (lot_id,)).fetchone()
        return self._row_to_position(pos_row), self._row_to_trade_lot(lot_row)

    def list_positions(self, *, status: str = "active") -> list[TrackedPositionRecord]:
        sql = "SELECT * FROM tracked_positions"
        params: list[Any] = []
        if status in ("active", "closed"):
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY CASE WHEN status = 'active' THEN 0 ELSE 1 END, opened_date DESC, id DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_position(r) for r in rows]

    def get_position(self, position_id: int) -> TrackedPositionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tracked_positions WHERE id = ?",
                (position_id,),
            ).fetchone()
        return self._row_to_position(row) if row else None

    def get_active_position_by_otc_code(self, otc_code: str) -> TrackedPositionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tracked_positions WHERE otc_code = ? AND status = 'active' LIMIT 1",
                (otc_code,),
            ).fetchone()
        return self._row_to_position(row) if row else None

    def list_active_position_ids_by_otc(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT otc_code, id FROM tracked_positions WHERE status = 'active'"
            ).fetchall()
        return {str(r["otc_code"]): int(r["id"]) for r in rows}

    def update_position(
        self,
        position_id: int,
        *,
        otc_name: Any = _UNSET,
        exchange_symbol: Any = _UNSET,
        status: Any = _UNSET,
        opened_date: Any = _UNSET,
        closed_date: Any = _UNSET,
        notes: Any = _UNSET,
        custom_stop_loss_pct: Any = _UNSET,
        custom_trailing_drawdown_pct: Any = _UNSET,
        custom_take_profit_pct: Any = _UNSET,
    ) -> TrackedPositionRecord | None:
        updates: list[str] = []
        params: list[Any] = []
        fields = {
            "otc_name": otc_name,
            "exchange_symbol": exchange_symbol,
            "status": status,
            "opened_date": opened_date,
            "closed_date": closed_date,
            "notes": notes,
            "custom_stop_loss_pct": custom_stop_loss_pct,
            "custom_trailing_drawdown_pct": custom_trailing_drawdown_pct,
            "custom_take_profit_pct": custom_take_profit_pct,
        }
        for col, val in fields.items():
            if val is not _UNSET:
                updates.append(f"{col} = ?")
                params.append(val)
        if not updates:
            return self.get_position(position_id)
        updates.append("updated_at = ?")
        params.append(now_iso())
        params.append(position_id)
        with self._connect() as conn:
            try:
                cur = conn.execute(
                    f"UPDATE tracked_positions SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
            except sqlite3.IntegrityError as exc:
                pos = conn.execute(
                    "SELECT otc_code FROM tracked_positions WHERE id = ?", (position_id,)
                ).fetchone()
                code = str(pos["otc_code"]) if pos else ""
                raise ActivePositionExistsError(code) from exc
            if cur.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM tracked_positions WHERE id = ?", (position_id,)
            ).fetchone()
        return self._row_to_position(row) if row else None

    def delete_position(self, position_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM tracked_positions WHERE id = ?", (position_id,))
            return cur.rowcount > 0

    def list_trade_lots(self, position_id: int) -> list[TradeLotRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_lots WHERE position_id = ? ORDER BY trade_date ASC, id ASC",
                (position_id,),
            ).fetchall()
        return [self._row_to_trade_lot(r) for r in rows]

    def list_trade_lots_for_positions(self, position_ids: list[int]) -> dict[int, list[TradeLotRecord]]:
        if not position_ids:
            return {}
        placeholders = ",".join("?" for _ in position_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM trade_lots
                WHERE position_id IN ({placeholders})
                ORDER BY trade_date ASC, id ASC
                """,
                position_ids,
            ).fetchall()
        grouped: dict[int, list[TradeLotRecord]] = {pid: [] for pid in position_ids}
        for row in rows:
            lot = self._row_to_trade_lot(row)
            grouped.setdefault(lot.position_id, []).append(lot)
        return grouped

    def get_trade_lot(self, position_id: int, lot_id: int) -> TradeLotRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM trade_lots WHERE position_id = ? AND id = ?",
                (position_id, lot_id),
            ).fetchone()
        return self._row_to_trade_lot(row) if row else None

    def create_trade_lot(
        self,
        *,
        position_id: int,
        side: TradeSide,
        trade_date: str,
        nav: float | None,
        nav_status: NavStatus,
        amount: float | None,
        shares: float | None,
        sell_ratio: float | None,
        fee: float,
        notes: str,
    ) -> TradeLotRecord:
        ts = now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO trade_lots (
                    position_id, side, trade_date, nav, nav_status,
                    amount, shares, sell_ratio, fee, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position_id,
                    side,
                    trade_date,
                    nav,
                    nav_status,
                    amount,
                    shares,
                    sell_ratio,
                    fee,
                    notes,
                    ts,
                    ts,
                ),
            )
            lot_id = int(cur.lastrowid)
            row = conn.execute("SELECT * FROM trade_lots WHERE id = ?", (lot_id,)).fetchone()
        return self._row_to_trade_lot(row)

    def update_trade_lot(
        self,
        position_id: int,
        lot_id: int,
        *,
        side: Any = _UNSET,
        trade_date: Any = _UNSET,
        nav: Any = _UNSET,
        nav_status: Any = _UNSET,
        amount: Any = _UNSET,
        shares: Any = _UNSET,
        sell_ratio: Any = _UNSET,
        fee: Any = _UNSET,
        notes: Any = _UNSET,
    ) -> TradeLotRecord | None:
        updates: list[str] = []
        params: list[Any] = []
        fields = {
            "side": side,
            "trade_date": trade_date,
            "nav": nav,
            "nav_status": nav_status,
            "amount": amount,
            "shares": shares,
            "sell_ratio": sell_ratio,
            "fee": fee,
            "notes": notes,
        }
        for col, val in fields.items():
            if val is not _UNSET:
                updates.append(f"{col} = ?")
                params.append(val)
        if not updates:
            return self.get_trade_lot(position_id, lot_id)
        updates.append("updated_at = ?")
        params.append(now_iso())
        params.extend([position_id, lot_id])
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE trade_lots SET {', '.join(updates)} WHERE position_id = ? AND id = ?",
                params,
            )
            if cur.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM trade_lots WHERE position_id = ? AND id = ?",
                (position_id, lot_id),
            ).fetchone()
        return self._row_to_trade_lot(row) if row else None

    def delete_trade_lot(self, position_id: int, lot_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM trade_lots WHERE position_id = ? AND id = ?",
                (position_id, lot_id),
            )
            return cur.rowcount > 0

    def list_pending_trade_lots(self, *, otc_code: str | None = None) -> list[tuple[TradeLotRecord, str]]:
        sql = """
            SELECT tl.*, tp.otc_code AS pos_otc_code
            FROM trade_lots tl
            JOIN tracked_positions tp ON tp.id = tl.position_id
            WHERE tl.nav_status = 'pending'
        """
        params: list[Any] = []
        if otc_code is not None:
            sql += " AND tp.otc_code = ?"
            params.append(otc_code)
        sql += " ORDER BY tl.trade_date ASC, tl.id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [(self._row_to_trade_lot(r), str(r["pos_otc_code"])) for r in rows]

    def has_stale_pending_lots(self, *, before_date: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM trade_lots WHERE nav_status = 'pending' AND trade_date < ? LIMIT 1",
                (before_date,),
            ).fetchone()
        return row is not None

    def list_tracked_otc_codes(self, *, closed_after_date: str | None = None) -> list[str]:
        if closed_after_date is None:
            sql = "SELECT DISTINCT otc_code FROM tracked_positions ORDER BY otc_code ASC"
            params: list[Any] = []
        else:
            sql = """
                SELECT DISTINCT tp.otc_code
                FROM tracked_positions tp
                WHERE tp.status = 'active'
                   OR tp.closed_date IS NULL
                   OR tp.closed_date >= ?
                   OR EXISTS (
                       SELECT 1 FROM trade_lots tl
                       WHERE tl.position_id = tp.id AND tl.nav_status = 'pending'
                   )
                ORDER BY tp.otc_code ASC
            """
            params = [closed_after_date]
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [str(r["otc_code"]) for r in rows]

    def backfill_missing_position_names(self, otc_code: str, otc_name: str) -> None:
        if not otc_name:
            return
        placeholder = f"场外基金 {otc_code}"
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tracked_positions
                SET otc_name = ?, updated_at = ?
                WHERE otc_code = ? AND (otc_name = '' OR otc_name = ?)
                """,
                (otc_name, now_iso(), otc_code, placeholder),
            )

    def upsert_otc_nav_bars(self, otc_code: str, bars: list[OTCNavBar]) -> int:
        if not bars:
            return 0
        ts = now_iso()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO otc_nav_bars (
                    otc_code, nav_date, unit_nav, accumulated_nav,
                    daily_return_pct, dividend_note, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(otc_code, nav_date) DO UPDATE SET
                    unit_nav = excluded.unit_nav,
                    accumulated_nav = COALESCE(excluded.accumulated_nav, otc_nav_bars.accumulated_nav),
                    daily_return_pct = COALESCE(excluded.daily_return_pct, otc_nav_bars.daily_return_pct),
                    dividend_note = excluded.dividend_note,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        otc_code,
                        b.nav_date,
                        b.unit_nav,
                        b.accumulated_nav,
                        b.daily_return_pct,
                        b.dividend_note,
                        ts,
                    )
                    for b in bars
                ],
            )
        return len(bars)

    def get_otc_nav_bar(self, otc_code: str, nav_date: str) -> OTCNavBar | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM otc_nav_bars WHERE otc_code = ? AND nav_date = ?",
                (otc_code, nav_date),
            ).fetchone()
        return self._row_to_otc_nav_bar(row) if row else None

    def get_latest_otc_nav_bar(self, otc_code: str) -> OTCNavBar | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM otc_nav_bars WHERE otc_code = ? ORDER BY nav_date DESC LIMIT 1",
                (otc_code,),
            ).fetchone()
        return self._row_to_otc_nav_bar(row) if row else None

    def get_latest_otc_nav_bars(self, otc_codes: list[str]) -> dict[str, OTCNavBar]:
        if not otc_codes:
            return {}
        placeholders = ",".join("?" for _ in otc_codes)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT b.*
                FROM otc_nav_bars b
                JOIN (
                    SELECT otc_code, MAX(nav_date) AS max_date
                    FROM otc_nav_bars
                    WHERE otc_code IN ({placeholders})
                    GROUP BY otc_code
                ) m ON b.otc_code = m.otc_code AND b.nav_date = m.max_date
                """,
                otc_codes,
            ).fetchall()
        return {str(r["otc_code"]): self._row_to_otc_nav_bar(r) for r in rows}

    def list_otc_nav_bars(
        self, otc_code: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[OTCNavBar]:
        sql = "SELECT * FROM otc_nav_bars WHERE otc_code = ?"
        params: list[Any] = [otc_code]
        if start_date:
            sql += " AND nav_date >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND nav_date <= ?"
            params.append(end_date)
        sql += " ORDER BY nav_date ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_otc_nav_bar(r) for r in rows]

    def upsert_otc_fund_metadata(
        self,
        otc_code: str,
        *,
        otc_name: str = "",
        last_nav_date: str | None = None,
        last_synced_at: str | None = None,
    ) -> OTCFundMetadata:
        ts = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO otc_fund_metadata (
                    otc_code, otc_name, last_nav_date, last_synced_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(otc_code) DO UPDATE SET
                    otc_name = CASE WHEN excluded.otc_name != '' THEN excluded.otc_name ELSE otc_fund_metadata.otc_name END,
                    last_nav_date = COALESCE(excluded.last_nav_date, otc_fund_metadata.last_nav_date),
                    last_synced_at = COALESCE(excluded.last_synced_at, otc_fund_metadata.last_synced_at),
                    updated_at = excluded.updated_at
                """,
                (otc_code, otc_name, last_nav_date, last_synced_at, ts),
            )
            row = conn.execute(
                "SELECT * FROM otc_fund_metadata WHERE otc_code = ?",
                (otc_code,),
            ).fetchone()
        return self._row_to_otc_metadata(row)

    def get_otc_fund_metadata(self, otc_code: str) -> OTCFundMetadata | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM otc_fund_metadata WHERE otc_code = ?",
                (otc_code,),
            ).fetchone()
        return self._row_to_otc_metadata(row) if row else None

    # ------------------------------------------------------------------
    # Row Mappers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_fund(row: sqlite3.Row) -> ExchangeFund:
        return ExchangeFund(
            symbol=row["symbol"],
            code=row["code"],
            exchange=row["exchange"],
            name=row["name"],
            fund_type=row["fund_type"],
            index_code=row["index_code"],
            index_name=row["index_name"],
            company_id=row["company_id"],
            company_name=row["company_name"],
            establish_date=row["establish_date"],
            fund_scale=row["fund_scale"],
            latest_trade_date=row["latest_trade_date"],
            avg_amount_20d=row["avg_amount_20d"],
            is_active=bool(row["is_active"]),
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_mapping(row: sqlite3.Row) -> OTCFundMapping:
        return OTCFundMapping(
            exchange_symbol=row["exchange_symbol"],
            otc_code=row["otc_code"],
            otc_name=row["otc_name"],
            share_class=row["share_class"],
            mapping_type=row["mapping_type"],
            purchase_status=row["purchase_status"],
            fee_rate=row["fee_rate"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_preset(row: sqlite3.Row) -> StrategyPresetRecord:
        return StrategyPresetRecord(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            config=json.loads(row["config_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_position(row: sqlite3.Row) -> TrackedPositionRecord:
        return TrackedPositionRecord(
            id=int(row["id"]),
            otc_code=row["otc_code"],
            otc_name=row["otc_name"],
            exchange_symbol=row["exchange_symbol"],
            status=row["status"],
            opened_date=row["opened_date"],
            closed_date=row["closed_date"],
            entry_signal_snapshot=json.loads(row["entry_signal_snapshot_json"] or "{}"),
            notes=row["notes"],
            custom_stop_loss_pct=row["custom_stop_loss_pct"],
            custom_trailing_drawdown_pct=row["custom_trailing_drawdown_pct"],
            custom_take_profit_pct=row["custom_take_profit_pct"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_trade_lot(row: sqlite3.Row) -> TradeLotRecord:
        return TradeLotRecord(
            id=int(row["id"]),
            position_id=int(row["position_id"]),
            side=row["side"],
            trade_date=row["trade_date"],
            nav=row["nav"],
            nav_status=row["nav_status"],
            amount=row["amount"],
            shares=row["shares"],
            sell_ratio=row["sell_ratio"],
            fee=float(row["fee"] or 0.0),
            notes=row["notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_otc_nav_bar(row: sqlite3.Row) -> OTCNavBar:
        return OTCNavBar(
            otc_code=row["otc_code"],
            nav_date=row["nav_date"],
            unit_nav=float(row["unit_nav"]),
            accumulated_nav=row["accumulated_nav"],
            daily_return_pct=row["daily_return_pct"],
            dividend_note=row["dividend_note"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_otc_metadata(row: sqlite3.Row) -> OTCFundMetadata:
        return OTCFundMetadata(
            otc_code=row["otc_code"],
            otc_name=row["otc_name"],
            last_nav_date=row["last_nav_date"],
            last_synced_at=row["last_synced_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_otc_lookup_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "otc_code": row["otc_code"],
            "otc_name": row["otc_name"],
            "share_class": row["share_class"],
            "mapping_type": row["mapping_type"],
            "purchase_status": row["purchase_status"],
            "fee_rate": row["fee_rate"],
            "is_mapped": True,
            "suggested_proxy": {
                "symbol": row["ef_symbol"],
                "code": row["ef_code"],
                "name": row["ef_name"],
                "fund_type": row["ef_fund_type"],
                "index_code": row["ef_index_code"],
                "index_name": row["ef_index_name"],
            },
        }

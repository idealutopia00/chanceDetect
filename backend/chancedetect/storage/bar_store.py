"""Columnar Parquet + DuckDB time-series storage for daily bars, benchmarks, and features."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import duckdb
import polars as pl

DAILY_BARS_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.Utf8,
    "trade_date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "amount": pl.Float64,
    "turnover_pct": pl.Float64,
    "pct_chg": pl.Float64,
    "source": pl.Utf8,
}

BENCHMARKS_SCHEMA: dict[str, pl.DataType] = {
    "benchmark_code": pl.Utf8,
    "trade_date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "amount": pl.Float64,
    "pct_chg": pl.Float64,
}


def empty_daily_bars_df() -> pl.DataFrame:
    return pl.DataFrame(schema=DAILY_BARS_SCHEMA)


def empty_benchmarks_df() -> pl.DataFrame:
    return pl.DataFrame(schema=BENCHMARKS_SCHEMA)


def _atomic_write_parquet(df: pl.DataFrame, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(".tmp.parquet")
    df.write_parquet(tmp_path, compression="zstd", statistics=True)
    os.replace(tmp_path, target)


class BarStore:
    """Manages Parquet columnar files and DuckDB analytical reads."""

    def __init__(
        self,
        daily_bars_path: Path,
        benchmarks_path: Path,
        features_path: Path,
    ) -> None:
        self.daily_bars_path = Path(daily_bars_path)
        self.benchmarks_path = Path(benchmarks_path)
        self.features_path = Path(features_path)
        for p in (self.daily_bars_path, self.benchmarks_path, self.features_path):
            p.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Daily Bars (Exchange Funds)
    # ------------------------------------------------------------------

    def has_daily_bars(self) -> bool:
        return self.daily_bars_path.exists()

    def load_daily_bars(
        self,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pl.DataFrame:
        """Load daily bars via DuckDB zero-copy Arrow -> Polars with predicate pushdown."""
        if not self.daily_bars_path.exists():
            return empty_daily_bars_df()

        clauses: list[str] = []
        params: list[object] = []
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            clauses.append(f"symbol IN ({placeholders})")
            params.extend(symbols)
        if start_date is not None:
            clauses.append("trade_date >= ?")
            params.append(start_date)
        if end_date is not None:
            clauses.append("trade_date <= ?")
            params.append(end_date)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT symbol, trade_date, open, high, low, close, volume, amount, turnover_pct, pct_chg, source
            FROM read_parquet('{self.daily_bars_path.as_posix()}')
            {where}
            ORDER BY symbol ASC, trade_date ASC
        """
        with duckdb.connect(database=":memory:") as con:
            rel = con.sql(sql, params=params) if params else con.sql(sql)
            return pl.from_arrow(rel.to_arrow_table())

    def get_latest_dates_by_symbol(self) -> dict[str, date]:
        if not self.daily_bars_path.exists():
            return {}
        sql = f"""
            SELECT symbol, MAX(trade_date) AS max_date
            FROM read_parquet('{self.daily_bars_path.as_posix()}')
            GROUP BY symbol
        """
        with duckdb.connect(database=":memory:") as con:
            rows = con.execute(sql).fetchall()
        return {str(sym): dt for sym, dt in rows if dt is not None}

    def get_tail_closes_by_symbol(self, n: int = 5) -> dict[str, dict[date, float]]:
        """Return the last N (trade_date -> close) per symbol for drift detection."""
        if not self.daily_bars_path.exists():
            return {}
        sql = f"""
            SELECT symbol, trade_date, close
            FROM (
                SELECT
                    symbol,
                    trade_date,
                    close,
                    ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn
                FROM read_parquet('{self.daily_bars_path.as_posix()}')
            )
            WHERE rn <= ?
        """
        with duckdb.connect(database=":memory:") as con:
            rows = con.execute(sql, [n]).fetchall()
        result: dict[str, dict[date, float]] = {}
        for sym, dt, close in rows:
            result.setdefault(str(sym), {})[dt] = float(close)
        return result

    def upsert_daily_bars(
        self,
        new_df: pl.DataFrame,
        *,
        replace_symbols: set[str] | None = None,
    ) -> int:
        """Merge incremental bars, replacing full histories for drift-refreshed symbols."""
        if new_df.is_empty():
            return 0

        incoming = self._normalize_daily_bars(new_df)
        if self.daily_bars_path.exists():
            existing = pl.read_parquet(self.daily_bars_path)
            if replace_symbols:
                existing = existing.filter(~pl.col("symbol").is_in(list(replace_symbols)))
            combined = pl.concat([existing, incoming], how="vertical_relaxed")
        else:
            combined = incoming

        # Keep latest record per (symbol, trade_date) and recompute pct_chg cleanly
        merged = (
            combined.unique(subset=["symbol", "trade_date"], keep="last")
            .sort(["symbol", "trade_date"])
            .with_columns(
                pl.col("close")
                .pct_change()
                .over("symbol")
                .mul(100.0)
                .fill_null(0.0)
                .round(4)
                .alias("pct_chg")
            )
            .select(list(DAILY_BARS_SCHEMA.keys()))
        )
        _atomic_write_parquet(merged, self.daily_bars_path)
        return merged.height

    def compute_symbol_metrics(self) -> dict[str, tuple[str, float | None]]:
        """Compute (latest_trade_date, avg_amount_20d) per symbol directly via DuckDB."""
        if not self.daily_bars_path.exists():
            return {}
        sql = f"""
            WITH ranked AS (
                SELECT
                    symbol,
                    trade_date,
                    amount,
                    ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn
                FROM read_parquet('{self.daily_bars_path.as_posix()}')
            )
            SELECT
                symbol,
                CAST(MAX(trade_date) AS VARCHAR) AS latest_date,
                AVG(CASE WHEN rn <= 20 THEN amount ELSE NULL END) AS avg_amt_20d
            FROM ranked
            GROUP BY symbol
        """
        with duckdb.connect(database=":memory:") as con:
            rows = con.execute(sql).fetchall()
        return {
            str(sym): (str(dt), round(float(avg_amt), 2) if avg_amt is not None else None)
            for sym, dt, avg_amt in rows
        }

    # ------------------------------------------------------------------
    # Benchmarks
    # ------------------------------------------------------------------

    def load_benchmarks(
        self,
        benchmark_codes: list[str] | None = None,
        start_date: date | None = None,
    ) -> pl.DataFrame:
        if not self.benchmarks_path.exists():
            return empty_benchmarks_df()

        clauses: list[str] = []
        params: list[object] = []
        if benchmark_codes:
            placeholders = ",".join("?" for _ in benchmark_codes)
            clauses.append(f"benchmark_code IN ({placeholders})")
            params.extend(benchmark_codes)
        if start_date is not None:
            clauses.append("trade_date >= ?")
            params.append(start_date)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT benchmark_code, trade_date, open, high, low, close, volume, amount, pct_chg
            FROM read_parquet('{self.benchmarks_path.as_posix()}')
            {where}
            ORDER BY benchmark_code ASC, trade_date ASC
        """
        with duckdb.connect(database=":memory:") as con:
            rel = con.sql(sql, params=params) if params else con.sql(sql)
            return pl.from_arrow(rel.to_arrow_table())

    def upsert_benchmarks(self, new_df: pl.DataFrame) -> int:
        if new_df.is_empty():
            return 0
        incoming = self._normalize_benchmarks(new_df)
        if self.benchmarks_path.exists():
            existing = pl.read_parquet(self.benchmarks_path)
            combined = pl.concat([existing, incoming], how="vertical_relaxed")
        else:
            combined = incoming

        merged = (
            combined.unique(subset=["benchmark_code", "trade_date"], keep="last")
            .sort(["benchmark_code", "trade_date"])
            .with_columns(
                pl.col("close")
                .pct_change()
                .over("benchmark_code")
                .mul(100.0)
                .fill_null(0.0)
                .round(4)
                .alias("pct_chg")
            )
            .select(list(BENCHMARKS_SCHEMA.keys()))
        )
        _atomic_write_parquet(merged, self.benchmarks_path)
        return merged.height

    # ------------------------------------------------------------------
    # Precomputed Features Cache
    # ------------------------------------------------------------------

    def has_features(self) -> bool:
        return self.features_path.exists()

    def save_features(self, features_df: pl.DataFrame) -> int:
        if features_df.is_empty():
            return 0
        _atomic_write_parquet(features_df, self.features_path)
        return features_df.height

    def load_features(
        self,
        symbols: list[str] | None = None,
        start_date: date | None = None,
    ) -> pl.DataFrame:
        if not self.features_path.exists():
            return empty_daily_bars_df()
        if symbols is None and start_date is None:
            return pl.read_parquet(self.features_path)

        lf = pl.scan_parquet(self.features_path)
        if symbols:
            lf = lf.filter(pl.col("symbol").is_in(symbols))
        if start_date is not None:
            lf = lf.filter(pl.col("trade_date") >= start_date)
        return lf.collect()

    def get_global_latest_trade_date(self) -> date | None:
        if not self.daily_bars_path.exists():
            return None
        sql = f"SELECT MAX(trade_date) FROM read_parquet('{self.daily_bars_path.as_posix()}')"
        with duckdb.connect(database=":memory:") as con:
            row = con.execute(sql).fetchone()
        return row[0] if row and row[0] is not None else None

    def get_storage_stats(self) -> dict[str, object]:
        stats: dict[str, object] = {
            "daily_bars_rows": 0,
            "daily_bars_symbols": 0,
            "daily_bars_mb": 0.0,
            "features_rows": 0,
            "features_mb": 0.0,
            "benchmarks_rows": 0,
            "latest_trade_date": None,
        }
        with duckdb.connect(database=":memory:") as con:
            if self.daily_bars_path.exists():
                r = con.execute(
                    f"""
                    SELECT COUNT(*), COUNT(DISTINCT symbol), CAST(MAX(trade_date) AS VARCHAR)
                    FROM read_parquet('{self.daily_bars_path.as_posix()}')
                    """
                ).fetchone()
                stats["daily_bars_rows"] = int(r[0] or 0)
                stats["daily_bars_symbols"] = int(r[1] or 0)
                stats["latest_trade_date"] = r[2]
                stats["daily_bars_mb"] = round(self.daily_bars_path.stat().st_size / (1024 * 1024), 2)
            if self.features_path.exists():
                r = con.execute(
                    f"SELECT COUNT(*) FROM read_parquet('{self.features_path.as_posix()}')"
                ).fetchone()
                stats["features_rows"] = int(r[0] or 0)
                stats["features_mb"] = round(self.features_path.stat().st_size / (1024 * 1024), 2)
            if self.benchmarks_path.exists():
                r = con.execute(
                    f"SELECT COUNT(*) FROM read_parquet('{self.benchmarks_path.as_posix()}')"
                ).fetchone()
                stats["benchmarks_rows"] = int(r[0] or 0)
        return stats

    # ------------------------------------------------------------------
    # Normalization Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_daily_bars(df: pl.DataFrame) -> pl.DataFrame:
        exprs = []
        for col, dtype in DAILY_BARS_SCHEMA.items():
            if col in df.columns:
                exprs.append(pl.col(col).cast(dtype, strict=False).alias(col))
            elif col == "source":
                exprs.append(pl.lit("unknown").cast(dtype).alias(col))
            else:
                exprs.append(pl.lit(0.0).cast(dtype).alias(col))
        return df.select(exprs)

    @staticmethod
    def _normalize_benchmarks(df: pl.DataFrame) -> pl.DataFrame:
        exprs = []
        for col, dtype in BENCHMARKS_SCHEMA.items():
            if col in df.columns:
                exprs.append(pl.col(col).cast(dtype, strict=False).alias(col))
            else:
                exprs.append(pl.lit(0.0).cast(dtype).alias(col))
        return df.select(exprs)

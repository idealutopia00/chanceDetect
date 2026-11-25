"""Sync orchestrator: metadata refresh, incremental/full bar sync, drift recovery, and feature cache."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import polars as pl

from chancedetect.config import Settings
from chancedetect.data.kline_providers import FailoverKlineFetcher
from chancedetect.data.metadata_provider import TiantianMetadataProvider
from chancedetect.data.models import RawBar
from chancedetect.data.validator import (
    bars_to_polars,
    detect_corporate_action_drift,
    validate_bars,
)
from chancedetect.storage.bar_store import BarStore
from chancedetect.storage.sqlite_store import SQLiteStore
from chancedetect.strategies.indicators import compute_base_features


@dataclass
class SyncProgress:
    running: bool = False
    job_type: str = ""
    stage: str = ""
    done: int = 0
    total: int = 0
    last_message: str = ""
    run_id: int | None = None
    last_summary: dict[str, Any] = field(default_factory=dict)


class SyncService:
    """Coordinates metadata & K-line synchronization with thread-safe progress tracking."""

    def __init__(
        self,
        settings: Settings,
        sqlite_store: SQLiteStore,
        bar_store: BarStore,
        *,
        metadata_provider: TiantianMetadataProvider | None = None,
        kline_fetcher: FailoverKlineFetcher | None = None,
    ) -> None:
        self.settings = settings
        self.sqlite = sqlite_store
        self.bars = bar_store
        self._metadata_provider = metadata_provider
        self._kline_fetcher = kline_fetcher
        self._lock = threading.Lock()
        self.progress = SyncProgress()

    def is_running(self) -> bool:
        return self.progress.running

    def get_progress_dict(self) -> dict[str, Any]:
        return {
            "running": self.progress.running,
            "job_type": self.progress.job_type,
            "stage": self.progress.stage,
            "done": self.progress.done,
            "total": self.progress.total,
            "last_message": self.progress.last_message,
            "run_id": self.progress.run_id,
            "last_summary": self.progress.last_summary,
        }

    # ------------------------------------------------------------------
    # Metadata Sync
    # ------------------------------------------------------------------

    def sync_metadata(
        self,
        *,
        force: bool = False,
        sample_limit: int | None = None,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A sync job is already running")
        run_id = self.sqlite.start_sync_run("metadata")
        self.progress = SyncProgress(
            running=True,
            job_type="metadata",
            stage="fetching_catalog",
            run_id=run_id,
            last_message="正在拉取天天基金目录与详情...",
        )

        own_provider = self._metadata_provider is None
        provider = self._metadata_provider or TiantianMetadataProvider(
            timeout=self.settings.request_timeout_sec,
            concurrency=self.settings.metadata_concurrency,
        )
        try:
            def _on_progress(done: int, total: int) -> None:
                self.progress.stage = "hydrating_details"
                self.progress.done = done
                self.progress.total = total
                self.progress.last_message = f"正在解析基金详情与映射 ({done}/{total})"
                if progress_cb:
                    progress_cb(done, total)

            funds, mappings, failures = provider.fetch_strict_universe(
                sample_limit=sample_limit,
                progress_cb=_on_progress,
            )

            self.progress.stage = "saving_sqlite"
            self.progress.last_message = "正在写入 SQLite 场内外映射表..."
            stats = self.sqlite.replace_universe(funds, mappings, force=force)

            if failures:
                self.sqlite.record_sync_errors(
                    run_id,
                    [(code, "metadata_detail", "tiantian", reason) for code, reason in failures[:500]],
                )

            summary = {
                **stats,
                "detail_failures": len(failures),
            }
            status = "success" if len(funds) > 0 else "failed"
            self.sqlite.finish_sync_run(
                run_id,
                status=status,
                total_targets=len(funds) + len(failures),
                succeeded_count=len(funds),
                failed_count=len(failures),
                primary_source_count=len(funds),
                summary=summary,
            )
            self.progress.last_summary = summary
            self.progress.last_message = (
                f"元数据同步完成：{stats['active_fund_count']} 只场内基金，"
                f"{stats['mapping_count']} 条场外映射"
            )
            return summary
        except Exception as exc:
            self.sqlite.record_sync_errors(run_id, [("*", "metadata", "tiantian", str(exc))])
            self.sqlite.finish_sync_run(
                run_id,
                status="failed",
                total_targets=0,
                succeeded_count=0,
                failed_count=1,
                summary={"error": str(exc)},
            )
            self.progress.last_message = f"元数据同步失败：{exc}"
            raise
        finally:
            if own_provider:
                provider.close()
            self.progress.running = False
            self._lock.release()

    # ------------------------------------------------------------------
    # Daily Bars + Benchmarks + Features Sync
    # ------------------------------------------------------------------

    def sync_daily_bars(
        self,
        *,
        full_refresh: bool = False,
        symbols: list[str] | None = None,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A sync job is already running")

        all_funds = self.sqlite.list_exchange_funds(active_only=True)
        target_symbols = symbols if symbols else [f.symbol for f in all_funds]
        if not target_symbols:
            self._lock.release()
            raise RuntimeError("No active exchange funds found. Run metadata sync first.")

        run_id = self.sqlite.start_sync_run("daily_bars", total_targets=len(target_symbols))
        self.progress = SyncProgress(
            running=True,
            job_type="daily_bars",
            stage="syncing_benchmarks",
            done=0,
            total=len(target_symbols),
            run_id=run_id,
            last_message="正在同步基准指数与场内基金日K...",
        )

        own_fetcher = self._kline_fetcher is None
        fetcher = self._kline_fetcher or FailoverKlineFetcher(
            timeout=self.settings.request_timeout_sec
        )

        today = date.today()
        history_start = today - timedelta(days=365 * self.settings.history_years + 30)

        try:
            # 1. Sync benchmark indices
            bench_rows = self._sync_benchmarks(fetcher, history_start, today, run_id)

            # 2. Prepare incremental state & tail closes for corporate-action drift detection
            latest_dates = {} if full_refresh else self.bars.get_latest_dates_by_symbol()
            tail_closes = (
                {}
                if full_refresh
                else self.bars.get_tail_closes_by_symbol(n=self.settings.drift_overlap_days)
            )

            self.progress.stage = "syncing_funds"
            collected_bars: list[RawBar] = []
            replace_symbols: set[str] = set()
            errors: list[tuple[str, str, str, str]] = []
            succeeded = 0
            primary_hits = 0
            fallback_hits = 0
            drift_refreshed = 0
            quality_warnings = 0

            def _fetch_one(sym: str) -> tuple[str, list[RawBar], str, bool, bool, list[str]]:
                last_dt = latest_dates.get(sym)
                if last_dt is not None and not full_refresh:
                    # Fetch overlap window (~14 calendar days covers 5+ trading days)
                    inc_start = max(history_start, last_dt - timedelta(days=14))
                    inc_bars, provider_name, used_fb = fetcher.fetch(sym, inc_start, today)
                    vrep = validate_bars(sym, inc_bars)
                    # Check corporate action drift on overlapping dates
                    if detect_corporate_action_drift(
                        tail_closes.get(sym, {}),
                        vrep.valid_bars,
                        tolerance_pct=self.settings.drift_tolerance_pct,
                    ):
                        full_bars, provider_name, used_fb = fetcher.fetch(sym, history_start, today)
                        vrep_full = validate_bars(sym, full_bars)
                        return sym, vrep_full.valid_bars, provider_name, used_fb, True, vrep_full.warnings
                    return sym, vrep.valid_bars, provider_name, used_fb, False, vrep.warnings
                else:
                    full_bars, provider_name, used_fb = fetcher.fetch(sym, history_start, today)
                    vrep_full = validate_bars(sym, full_bars)
                    return sym, vrep_full.valid_bars, provider_name, used_fb, True, vrep_full.warnings

            done = 0
            with ThreadPoolExecutor(max_workers=self.settings.kline_concurrency) as pool:
                future_map = {pool.submit(_fetch_one, sym): sym for sym in target_symbols}
                for fut in as_completed(future_map):
                    sym = future_map[fut]
                    done += 1
                    try:
                        _, bars_list, provider_name, used_fb, is_replace, warns = fut.result()
                        if bars_list:
                            collected_bars.extend(bars_list)
                            if is_replace:
                                replace_symbols.add(sym)
                            if is_replace and sym in latest_dates and not full_refresh:
                                drift_refreshed += 1
                            succeeded += 1
                            if used_fb:
                                fallback_hits += 1
                            else:
                                primary_hits += 1
                            if warns:
                                quality_warnings += len(warns)
                                for w in warns[:3]:
                                    errors.append((sym, "quality_warning", provider_name, w))
                        else:
                            errors.append((sym, "fetch_bars", provider_name, "empty_bars"))
                    except Exception as exc:
                        errors.append((sym, "fetch_bars", "failover", str(exc)))

                    self.progress.done = done
                    if done % 25 == 0 or done == len(target_symbols):
                        self.progress.last_message = (
                            f"同步日K ({done}/{len(target_symbols)})，成功 {succeeded}，降级备源 {fallback_hits}"
                        )
                        if progress_cb:
                            progress_cb(done, len(target_symbols))

            # 3. Persist daily bars atomically
            self.progress.stage = "writing_parquet"
            self.progress.last_message = "正在原子写入 Parquet 时序数据..."
            incoming_df = bars_to_polars(collected_bars)
            total_bar_rows = self.bars.upsert_daily_bars(
                incoming_df,
                replace_symbols=replace_symbols,
            )

            # 4. Update SQLite fund metrics (latest_trade_date, avg_amount_20d)
            metrics = self.bars.compute_symbol_metrics()
            self.sqlite.update_fund_bar_metrics(metrics)

            # 5. Recompute vectorized features cache
            self.progress.stage = "computing_features"
            self.progress.last_message = "正在预计算全市场技术指标宽表..."
            feature_rows = self.recompute_features()

            if errors:
                self.sqlite.record_sync_errors(run_id, errors[:500])

            failed_count = len(target_symbols) - succeeded
            status = "success" if failed_count == 0 else ("partial" if succeeded > 0 else "failed")
            summary = {
                "total_targets": len(target_symbols),
                "succeeded": succeeded,
                "failed": failed_count,
                "primary_source_count": primary_hits,
                "fallback_source_count": fallback_hits,
                "drift_refreshed_count": drift_refreshed,
                "quality_warnings": quality_warnings,
                "total_bar_rows": total_bar_rows,
                "feature_rows": feature_rows,
                "benchmark_rows": bench_rows,
                "latest_trade_date": str(self.bars.get_global_latest_trade_date() or ""),
            }
            self.sqlite.finish_sync_run(
                run_id,
                status=status,
                total_targets=len(target_symbols),
                succeeded_count=succeeded,
                failed_count=failed_count,
                primary_source_count=primary_hits,
                fallback_source_count=fallback_hits,
                drift_refreshed_count=drift_refreshed,
                summary=summary,
            )
            self.progress.last_summary = summary
            self.progress.last_message = (
                f"日K同步完成：成功 {succeeded}/{len(target_symbols)}，"
                f"除权修正 {drift_refreshed}，最新日期 {summary['latest_trade_date']}"
            )
            return summary
        except Exception as exc:
            self.sqlite.record_sync_errors(run_id, [("*", "daily_bars", "system", str(exc))])
            self.sqlite.finish_sync_run(
                run_id,
                status="failed",
                total_targets=len(target_symbols),
                succeeded_count=0,
                failed_count=len(target_symbols),
                summary={"error": str(exc)},
            )
            self.progress.last_message = f"日K同步失败：{exc}"
            raise
        finally:
            if own_fetcher:
                fetcher.close()
            self.progress.running = False
            self._lock.release()

    def _sync_benchmarks(
        self,
        fetcher: FailoverKlineFetcher,
        start_date: date,
        end_date: date,
        run_id: int,
    ) -> int:
        frames: list[pl.DataFrame] = []
        for code in self.settings.benchmarks:
            try:
                bars, _, _ = fetcher.fetch(code, start_date, end_date, is_benchmark=True)
                vrep = validate_bars(code, bars)
                if vrep.valid_bars:
                    frames.append(
                        pl.DataFrame(
                            {
                                "benchmark_code": [code] * len(vrep.valid_bars),
                                "trade_date": [b.trade_date for b in vrep.valid_bars],
                                "open": [b.open for b in vrep.valid_bars],
                                "high": [b.high for b in vrep.valid_bars],
                                "low": [b.low for b in vrep.valid_bars],
                                "close": [b.close for b in vrep.valid_bars],
                                "volume": [b.volume for b in vrep.valid_bars],
                                "amount": [b.amount for b in vrep.valid_bars],
                                "pct_chg": [0.0] * len(vrep.valid_bars),
                            }
                        )
                    )
            except Exception as exc:
                self.sqlite.record_sync_errors(run_id, [(code, "benchmark", "failover", str(exc))])
        if not frames:
            return 0
        return self.bars.upsert_benchmarks(pl.concat(frames, how="vertical_relaxed"))

    def recompute_features(self) -> int:
        """Rebuild precomputed indicator Parquet cache from daily_bars + benchmarks."""
        daily_df = self.bars.load_daily_bars()
        if daily_df.is_empty():
            return 0
        bench_df = self.bars.load_benchmarks()
        primary_bench = self.settings.benchmarks[0] if self.settings.benchmarks else "sh000300"
        feat_df = compute_base_features(daily_df, bench_df, primary_benchmark=primary_bench)
        return self.bars.save_features(feat_df)

    def sync_all(
        self,
        *,
        force_metadata: bool = False,
        full_refresh_bars: bool = False,
        sample_limit: int | None = None,
    ) -> dict[str, Any]:
        meta_res = self.sync_metadata(force=force_metadata, sample_limit=sample_limit)
        bars_res = self.sync_daily_bars(full_refresh=full_refresh_bars)
        return {"metadata": meta_res, "daily_bars": bars_res}

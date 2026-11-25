"""Command-line interface for syncing data, running scans, and launching the web server."""

from __future__ import annotations

import logging
from typing import Annotated, Literal

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from chancedetect.config import get_settings
from chancedetect.data.sync_service import SyncService
from chancedetect.storage.bar_store import BarStore
from chancedetect.storage.sqlite_store import SQLiteStore
from chancedetect.strategies.engine import (
    BaseUniverseFilter,
    ScanRequestConfig,
    StrategyEngine,
    StrategySelection,
)

app = typer.Typer(name="chancedetect", help="ChanceDetect ETF/LOF -> OTC fund opportunity scanner")
console = Console()


def _init_services() -> tuple[SQLiteStore, BarStore, SyncService, StrategyEngine]:
    settings = get_settings()
    sqlite = SQLiteStore(settings.sqlite_path)
    bars = BarStore(settings.daily_bars_path, settings.benchmarks_path, settings.features_path)
    sync_svc = SyncService(settings, sqlite, bars)
    engine = StrategyEngine(sqlite, bars)
    return sqlite, bars, sync_svc, engine


@app.command("sync-metadata")
def sync_metadata(
    force: Annotated[bool, typer.Option("--force", help="Bypass universe drop protection")] = False,
    sample_limit: Annotated[
        int | None, typer.Option("--sample-limit", help="Limit candidate count for quick smoke testing")
    ] = None,
) -> None:
    """Fetch fund catalog & build strict On-Exchange <-> OTC fund mapping table."""
    _, _, sync_svc, _ = _init_services()
    console.print("[cyan]Syncing fund metadata & strict OTC mappings...[/cyan]")

    def _prog(done: int, total: int) -> None:
        console.print(f"  hydrated {done}/{total} fund details...")

    res = sync_svc.sync_metadata(force=force, sample_limit=sample_limit, progress_cb=_prog)
    console.print("[green]Metadata sync complete:[/green]", res)


@app.command("sync-bars")
def sync_bars(
    full: Annotated[bool, typer.Option("--full", help="Force full historical refresh instead of incremental")] = False,
) -> None:
    """Sync daily K-line bars (Tencent primary + Sina fallback) and recompute features."""
    _, _, sync_svc, _ = _init_services()
    console.print("[cyan]Syncing daily K-lines & precomputing features...[/cyan]")

    def _prog(done: int, total: int) -> None:
        console.print(f"  fetched {done}/{total} symbols...")

    res = sync_svc.sync_daily_bars(full_refresh=full, progress_cb=_prog)
    console.print("[green]Daily bars sync complete:[/green]", res)


@app.command("sync-all")
def sync_all(
    force_metadata: Annotated[bool, typer.Option("--force-metadata")] = False,
    full_bars: Annotated[bool, typer.Option("--full-bars")] = False,
    sample_limit: Annotated[int | None, typer.Option("--sample-limit")] = None,
) -> None:
    """Run metadata sync followed by daily K-line sync and feature computation."""
    _, _, sync_svc, _ = _init_services()
    res = sync_svc.sync_all(
        force_metadata=force_metadata,
        full_refresh_bars=full_bars,
        sample_limit=sample_limit,
    )
    console.print("[green]Full sync complete:[/green]", res)


@app.command("status")
def status() -> None:
    """Display local universe, storage, and latest sync telemetry."""
    sqlite, bars, _, _ = _init_services()
    console.print("[bold]Universe Stats:[/bold]", sqlite.get_universe_stats())
    console.print("[bold]Storage Stats:[/bold]", bars.get_storage_stats())
    runs = sqlite.list_sync_runs(limit=5)
    if runs:
        table = Table(title="Recent Sync Runs")
        table.add_column("ID")
        table.add_column("Job")
        table.add_column("Status")
        table.add_column("OK/Total")
        table.add_column("Primary/Backup")
        table.add_column("DriftFix")
        table.add_column("Finished")
        for r in runs:
            table.add_row(
                str(r.id),
                r.job_type,
                r.status,
                f"{r.succeeded_count}/{r.total_targets}",
                f"{r.primary_source_count}/{r.fallback_source_count}",
                str(r.drift_refreshed_count),
                str(r.finished_at or "-"),
            )
        console.print(table)


@app.command("scan")
def scan(
    combine: Annotated[Literal["AND", "OR", "RESONANCE"], typer.Option("--combine")] = "OR",
    min_hits: Annotated[int, typer.Option("--min-hits")] = 2,
    min_amount: Annotated[float, typer.Option("--min-amount", help="Min 20d avg amount (万元)")] = 1000.0,
    share: Annotated[Literal["C", "A"], typer.Option("--share", help="Preferred OTC share class")] = "C",
    strategy: Annotated[list[str] | None, typer.Option("--strategy", "-s", help="Strategy IDs (repeatable)")] = None,
) -> None:
    """Run vectorized strategy scan from the CLI and print index-grouped opportunities."""
    _, _, _, engine = _init_services()
    selections = [StrategySelection(strategy_id=sid, enabled=True) for sid in (strategy or [])]
    cfg = ScanRequestConfig(
        strategies=selections,
        combine_mode=combine,
        min_hits=min_hits,
        base_filter=BaseUniverseFilter(min_avg_amount_20d=min_amount),
        share_class_preference=share,
    )
    res = engine.scan(cfg)
    console.print(
        f"[bold cyan]Scan date:[/bold cyan] {res['trade_date']}  "
        f"[bold green]Elapsed:[/bold green] {res['elapsed_ms']} ms  "
        f"[bold]Hits:[/bold] {res['hit_index_count']} indices / {res['hit_fund_count']} funds "
        f"(scanned {res['scanned_fund_count']})"
    )

    table = Table(title="Top Opportunity Groups (with Preferred OTC Counterparts)")
    table.add_column("Index / Theme")
    table.add_column("Rep ETF")
    table.add_column("Chg%")
    table.add_column("Score")
    table.add_column("Strategies")
    table.add_column(f"Top OTC ({share} pref)")

    for grp in res["groups"][:25]:
        rep = grp["representative"]
        strats = ", ".join(s["strategy_name"] for s in rep["hit_strategies"])
        otcs = ", ".join(
            f"{o['otc_code']}({o['share_class']})" for o in grp["otc_funds"][:3]
        )
        table.add_row(
            grp["index_name"][:18],
            f"{rep['code']} {rep['name'][:12]}",
            f"{rep['pct_chg']:+.2f}%",
            f"{grp['max_composite_score']:.1f}",
            strats[:32],
            otcs,
        )
    console.print(table)


@app.command("serve")
def serve(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8000,
    reload: Annotated[bool, typer.Option("--reload")] = False,
) -> None:
    """Start FastAPI backend (and serve built React dashboard if present)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run("chancedetect.api.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()

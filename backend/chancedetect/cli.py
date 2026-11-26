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


@app.command("recompute-features")
def recompute_features() -> None:
    """Recompute the local vectorized technical indicator & Trend Stage feature cache."""
    _, _, sync_svc, _ = _init_services()
    console.print("[cyan]Recomputing precomputed feature cache...[/cyan]")
    rows = sync_svc.recompute_features()
    console.print(f"[green]Feature cache recomputed:[/green] {rows} rows")


def _format_stage_cell(stage: str) -> str:
    styles = {
        "oversold": "cyan",
        "early": "green",
        "mid": "yellow",
        "overheated": "red",
    }
    color = styles.get(stage, "white")
    return f"[{color}]{stage}[/{color}]"


def _format_fresh_cell(is_fresh: bool, hits_20d: int) -> str:
    if is_fresh:
        return f"[bold green]FRESH[/bold green] ({hits_20d}/20d)"
    if hits_20d > 0:
        return f"{hits_20d}/20d"
    return "-"


@app.command("scan")
def scan(
    regime_combine: Annotated[
        Literal["AND", "OR"], typer.Option("--regime-combine", help="Trend Regime combine mode (OR/AND)")
    ] = "OR",
    combine: Annotated[Literal["AND", "OR", "RESONANCE"], typer.Option("--combine")] = "OR",
    min_hits: Annotated[int, typer.Option("--min-hits")] = 2,
    min_amount: Annotated[float, typer.Option("--min-amount", help="Min 20d avg amount (万元)")] = 1000.0,
    share: Annotated[Literal["C", "A"], typer.Option("--share", help="Preferred OTC share class")] = "C",
    hide_overheated: Annotated[
        bool, typer.Option("--hide-overheated", help="Hide overheated items from Actionable Opportunities")
    ] = False,
    cooldown: Annotated[
        int, typer.Option("--cooldown", help="Fresh Signal cooldown window in trading days")
    ] = 10,
    show_watchlist: Annotated[
        bool, typer.Option("--show-watchlist/--no-watchlist", help="Print Mainline Watchlist table")
    ] = True,
    strategy: Annotated[list[str] | None, typer.Option("--strategy", "-s", help="Strategy IDs (repeatable)")] = None,
) -> None:
    """Run vectorized two-stage strategy scan from the CLI and print Actionable & Watchlist groups."""
    _, _, _, engine = _init_services()
    selections = [StrategySelection(strategy_id=sid, enabled=True) for sid in (strategy or [])]
    cfg = ScanRequestConfig(
        strategies=selections,
        regime_combine_mode=regime_combine,
        combine_mode=combine,
        min_hits=min_hits,
        hide_overheated=hide_overheated,
        freshness_cooldown_days=cooldown,
        base_filter=BaseUniverseFilter(min_avg_amount_20d=min_amount),
        share_class_preference=share,
    )
    res = engine.scan(cfg)
    console.print(
        f"[bold cyan]Scan date:[/bold cyan] {res['trade_date']}  "
        f"[bold green]Elapsed:[/bold green] {res['elapsed_ms']} ms  "
        f"[bold]Actionable:[/bold] {res['actionable_index_count']} indices / {res['actionable_fund_count']} funds  "
        f"[bold magenta]Watchlist:[/bold magenta] {res['watchlist_index_count']} indices / {res['watchlist_fund_count']} funds "
        f"(scanned {res['scanned_fund_count']})"
    )

    actionable_table = Table(title="Actionable Opportunities (with Preferred OTC Counterparts)")
    actionable_table.add_column("Index / Theme")
    actionable_table.add_column("Rep ETF")
    actionable_table.add_column("Chg%", no_wrap=True)
    actionable_table.add_column("Stage", no_wrap=True)
    actionable_table.add_column("Fresh", no_wrap=True)
    actionable_table.add_column("Score", no_wrap=True)
    actionable_table.add_column("Strategies")
    actionable_table.add_column(f"Top OTC ({share} pref)", no_wrap=True)

    for grp in res["actionable_groups"][:25]:
        rep = grp["representative"]
        strats = ", ".join(s["strategy_name"] for s in rep["hit_strategies"])
        otcs = ", ".join(
            f"{o['otc_code']}({o['share_class']})" for o in grp["otc_funds"][:3]
        )
        actionable_table.add_row(
            grp["index_name"][:18],
            f"{rep['code']} {rep['name'][:12]}",
            f"{rep['pct_chg']:+.2f}%",
            _format_stage_cell(rep.get("trend_stage", "mid")),
            _format_fresh_cell(bool(rep.get("is_fresh", False)), int(rep.get("hits_20d", 0))),
            f"{grp['max_composite_score']:.1f}",
            strats[:28],
            otcs,
        )
    console.print(actionable_table)

    if show_watchlist and res["watchlist_groups"]:
        watch_table = Table(title="Mainline Watchlist (Awaiting Pullback / Entry)")
        watch_table.add_column("Index / Theme")
        watch_table.add_column("Rep ETF")
        watch_table.add_column("Chg%", no_wrap=True)
        watch_table.add_column("Stage", no_wrap=True)
        watch_table.add_column("Bias20", no_wrap=True)
        watch_table.add_column("Score", no_wrap=True)
        watch_table.add_column("Matched Regimes")
        watch_table.add_column(f"Top OTC ({share} pref)", no_wrap=True)

        for grp in res["watchlist_groups"][:25]:
            rep = grp["representative"]
            regimes = ", ".join(s["strategy_name"] for s in rep.get("matched_regimes", []))
            bias20 = rep.get("bias20")
            bias20_str = f"{bias20:+.2f}%" if bias20 is not None else "-"
            otcs = ", ".join(
                f"{o['otc_code']}({o['share_class']})" for o in grp["otc_funds"][:3]
            )
            watch_table.add_row(
                grp["index_name"][:18],
                f"{rep['code']} {rep['name'][:12]}",
                f"{rep['pct_chg']:+.2f}%",
                _format_stage_cell(rep.get("trend_stage", "mid")),
                bias20_str,
                f"{grp['max_composite_score']:.1f}",
                regimes[:28],
                otcs,
            )
        console.print(watch_table)


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

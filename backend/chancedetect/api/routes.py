"""REST API route handlers under /api/v1."""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from chancedetect.api.schemas import PresetCreateRequest, ReplayRequest, SyncTriggerRequest
from chancedetect.data.sync_service import SyncService
from chancedetect.storage.bar_store import BarStore
from chancedetect.storage.sqlite_store import SQLiteStore
from chancedetect.strategies.engine import ScanRequestConfig, StrategyEngine
from chancedetect.strategies.guide_metadata import build_guide_metadata
from chancedetect.strategies.replay import build_fund_replay

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["chancedetect"])


def _deps(request: Request) -> tuple[SQLiteStore, BarStore, SyncService, StrategyEngine]:
    st = request.app.state
    return st.sqlite_store, st.bar_store, st.sync_service, st.strategy_engine


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    sqlite, bars, sync_svc, _ = _deps(request)
    return {
        "status": "ok",
        "universe": sqlite.get_universe_stats(),
        "storage": bars.get_storage_stats(),
        "sync_running": sync_svc.is_running(),
    }


@router.get("/strategies")
def list_strategies(request: Request) -> dict[str, Any]:
    _, _, _, engine = _deps(request)
    return {"strategies": engine.registry.list_meta()}


@router.get("/guide/metadata")
def get_guide_metadata(request: Request) -> dict[str, Any]:
    _, _, _, engine = _deps(request)
    return build_guide_metadata(engine.registry)


@router.post("/scan")
def run_scan(request: Request, body: ScanRequestConfig) -> dict[str, Any]:
    _, _, _, engine = _deps(request)
    try:
        return engine.scan(body)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/scan/snapshots")
def list_snapshots(request: Request, limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    sqlite, _, _, _ = _deps(request)
    return {"snapshots": sqlite.list_scan_snapshots(limit=limit)}


@router.get("/scan/snapshots/{snapshot_id}")
def get_snapshot(request: Request, snapshot_id: int) -> dict[str, Any]:
    sqlite, _, _, _ = _deps(request)
    snap = sqlite.get_scan_snapshot(snapshot_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return snap.model_dump()


@router.get("/presets")
def list_presets(request: Request) -> dict[str, Any]:
    sqlite, _, _, _ = _deps(request)
    return {"presets": [p.model_dump() for p in sqlite.list_presets()]}


@router.post("/presets")
def save_preset(request: Request, body: PresetCreateRequest) -> dict[str, Any]:
    sqlite, _, _, _ = _deps(request)
    preset = sqlite.save_preset(body.name, body.description, body.config)
    return {"preset": preset.model_dump()}


@router.delete("/presets/{preset_id}")
def delete_preset(request: Request, preset_id: int) -> dict[str, Any]:
    sqlite, _, _, _ = _deps(request)
    deleted = sqlite.delete_preset(preset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Preset not found")
    return {"deleted": True, "id": preset_id}


@router.post("/funds/{symbol}/replay")
def fund_replay(request: Request, symbol: str, body: ReplayRequest | None = None) -> dict[str, Any]:
    _, _, _, engine = _deps(request)
    req = body or ReplayRequest()
    try:
        return build_fund_replay(engine, symbol, req.config, lookback_days=req.lookback_days)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/funds")
def search_funds(
    request: Request,
    q: str = Query(default=""),
    fund_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    sqlite, _, _, _ = _deps(request)
    items, total = sqlite.search_funds(q, fund_type=fund_type, limit=limit, offset=offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/sync/status")
def sync_status(request: Request) -> dict[str, Any]:
    sqlite, bars, sync_svc, _ = _deps(request)
    return {
        "progress": sync_svc.get_progress_dict(),
        "universe": sqlite.get_universe_stats(),
        "storage": bars.get_storage_stats(),
        "runs": [r.model_dump() for r in sqlite.list_sync_runs(limit=15)],
        "errors": [e.model_dump() for e in sqlite.list_sync_errors(limit=30)],
    }


@router.post("/sync/trigger")
def trigger_sync(request: Request, body: SyncTriggerRequest) -> dict[str, Any]:
    _, _, sync_svc, engine = _deps(request)
    if sync_svc.is_running():
        raise HTTPException(status_code=409, detail="A sync job is already running")

    def _worker() -> None:
        try:
            if body.mode == "metadata":
                sync_svc.sync_metadata(
                    force=body.force_metadata,
                    sample_limit=body.sample_limit,
                )
            elif body.mode == "daily":
                sync_svc.sync_daily_bars(full_refresh=body.full_refresh_bars)
            elif body.mode == "full":
                sync_svc.sync_all(
                    force_metadata=body.force_metadata,
                    full_refresh_bars=body.full_refresh_bars,
                    sample_limit=body.sample_limit,
                )
            elif body.mode == "features":
                sync_svc.recompute_features()
            engine.invalidate_cache()
        except Exception:
            logger.exception("Background sync job (%s) failed", body.mode)

    t = threading.Thread(target=_worker, name=f"sync-{body.mode}", daemon=True)
    t.start()
    return {"accepted": True, "mode": body.mode}

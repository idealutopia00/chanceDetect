"""FastAPI application factory with scheduler lifespan and built React SPA hosting."""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from chancedetect.api.routes import router as api_router
from chancedetect.config import Settings, get_settings
from chancedetect.data.otc_nav_provider import OTCNavProvider
from chancedetect.data.sync_service import SyncService
from chancedetect.positions.service import PositionService
from chancedetect.scheduler.jobs import AppScheduler
from chancedetect.storage.bar_store import BarStore
from chancedetect.storage.sqlite_store import SQLiteStore
from chancedetect.strategies.engine import StrategyEngine

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    otc_nav_provider: OTCNavProvider | None = None,
    today_fn: Callable[[], date] | None = None,
) -> FastAPI:
    cfg = settings or get_settings()
    cfg.ensure_dirs()

    sqlite_store = SQLiteStore(cfg.sqlite_path)
    bar_store = BarStore(cfg.daily_bars_path, cfg.benchmarks_path, cfg.features_path)
    sync_service = SyncService(cfg, sqlite_store, bar_store)
    strategy_engine = StrategyEngine(sqlite_store, bar_store)
    nav_provider = otc_nav_provider or OTCNavProvider(timeout=cfg.request_timeout_sec)
    position_service = PositionService(
        sqlite_store,
        bar_store,
        nav_provider,
        today_fn=today_fn,
    )
    scheduler = AppScheduler(
        cfg,
        sqlite_store,
        bar_store,
        sync_service,
        position_service=position_service,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        scheduler.start()
        if cfg.enable_scheduler:
            # Non-blocking startup catch-up check for missed EOD syncs & stale Pending NAVs
            threading.Thread(
                target=scheduler.check_and_catchup,
                name="startup-catchup",
                daemon=True,
            ).start()
        try:
            yield
        finally:
            scheduler.shutdown()
            if otc_nav_provider is None:
                nav_provider.close()

    app = FastAPI(
        title="ChanceDetect",
        version="0.1.0",
        description="On-exchange ETF/LOF strategy scanner with strict OTC fund mapping",
        lifespan=lifespan,
    )

    app.state.settings = cfg
    app.state.sqlite_store = sqlite_store
    app.state.bar_store = bar_store
    app.state.sync_service = sync_service
    app.state.strategy_engine = strategy_engine
    app.state.otc_nav_provider = nav_provider
    app.state.position_service = position_service
    app.state.scheduler = scheduler

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)

    # Serve built React SPA on the same port if frontend/dist exists
    dist_dir = cfg.frontend_dist_dir
    if dist_dir.exists() and (dist_dir / "index.html").exists():
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa_fallback(full_path: str) -> FileResponse:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="API endpoint not found")
            candidate = dist_dir / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist_dir / "index.html")

    return app


app = create_app()

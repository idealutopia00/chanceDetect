"""Integration tests for FastAPI routes and scheduler helpers."""

from __future__ import annotations

from datetime import date, datetime

from fastapi.testclient import TestClient
from tests.test_engine_and_replay import _seed_market

from chancedetect.api.app import create_app
from chancedetect.config import Settings
from chancedetect.scheduler.jobs import SH_TZ, expected_latest_weekday


def test_expected_latest_weekday() -> None:
    # Wednesday 16:30 -> Wednesday itself
    wed_after = datetime(2026, 11, 25, 16, 30, tzinfo=SH_TZ)
    assert expected_latest_weekday(wed_after, 16, 15) == date(2026, 11, 25)
    # Wednesday 14:00 -> Tuesday
    wed_before = datetime(2026, 11, 25, 14, 0, tzinfo=SH_TZ)
    assert expected_latest_weekday(wed_before, 16, 15) == date(2026, 11, 24)
    # Sunday 11:00 -> Friday
    sun = datetime(2026, 11, 29, 11, 0, tzinfo=SH_TZ)
    assert expected_latest_weekday(sun, 16, 15) == date(2026, 11, 27)


def test_api_end_to_end(tmp_settings: Settings) -> None:
    app = create_app(tmp_settings)
    _seed_market(app.state.sqlite_store, app.state.bar_store)

    with TestClient(app) as client:
        # 1. Health
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        assert r.json()["universe"]["total_exchange_funds"] == 4

        # 2. Strategies metadata
        r = client.get("/api/v1/strategies")
        assert r.status_code == 200
        assert len(r.json()["strategies"]) == 8

        # 3. Scan
        r = client.post(
            "/api/v1/scan",
            json={
                "strategies": [{"strategy_id": "ma_bullish_alignment", "enabled": True}],
                "combine_mode": "OR",
                "share_class_preference": "C",
                "save_snapshot": True,
            },
        )
        assert r.status_code == 200
        scan_data = r.json()
        assert scan_data["hit_index_count"] == 1
        assert scan_data["groups"][0]["representative"]["symbol"] == "sh510300"

        # 4. Snapshots
        r = client.get("/api/v1/scan/snapshots")
        assert r.status_code == 200
        assert len(r.json()["snapshots"]) == 1

        # 5. Presets CRUD
        r = client.post(
            "/api/v1/presets",
            json={"name": "我的突破组合", "description": "均线+放量", "config": {"combine_mode": "AND"}},
        )
        assert r.status_code == 200
        pid = r.json()["preset"]["id"]
        assert len(client.get("/api/v1/presets").json()["presets"]) == 1
        assert client.delete(f"/api/v1/presets/{pid}").status_code == 200

        # 6. Fund replay
        r = client.post(
            "/api/v1/funds/sh510300/replay",
            json={
                "config": {
                    "strategies": [{"strategy_id": "ma_bullish_alignment", "enabled": True}],
                    "combine_mode": "OR",
                },
                "lookback_days": 120,
            },
        )
        assert r.status_code == 200
        rep = r.json()
        assert rep["fund"]["code"] == "510300"
        assert len(rep["bars"]) == 90
        assert len(rep["forward_stats"]) == 4

        # 7. Fund search (by OTC code)
        r = client.get("/api/v1/funds", params={"q": "000051"})
        assert r.status_code == 200
        assert r.json()["total"] == 1
        assert r.json()["items"][0]["symbol"] == "sh510330"

        # 8. Sync status
        r = client.get("/api/v1/sync/status")
        assert r.status_code == 200
        assert r.json()["progress"]["running"] is False

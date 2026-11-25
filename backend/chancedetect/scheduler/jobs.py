"""APScheduler EOD jobs and Mac sleep/wake automatic catch-up."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from chancedetect.config import Settings
from chancedetect.data.sync_service import SyncService
from chancedetect.notifications.base import BaseNotifier, NoopNotifier
from chancedetect.storage.bar_store import BarStore
from chancedetect.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)
SH_TZ = ZoneInfo("Asia/Shanghai")


def expected_latest_weekday(now_sh: datetime, cutoff_hour: int, cutoff_minute: int) -> date:
    """Estimate the most recent weekday whose EOD bars should be published."""
    d = now_sh.date()
    if d.weekday() < 5:
        if (now_sh.hour, now_sh.minute) >= (cutoff_hour, cutoff_minute):
            return d
        # Before EOD cutoff -> previous weekday
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


class AppScheduler:
    """Manages daily EOD bar sync, weekly metadata refresh, and sleep/wake catch-up."""

    def __init__(
        self,
        settings: Settings,
        sqlite_store: SQLiteStore,
        bar_store: BarStore,
        sync_service: SyncService,
        notifier: BaseNotifier | None = None,
    ) -> None:
        self.settings = settings
        self.sqlite = sqlite_store
        self.bars = bar_store
        self.sync_service = sync_service
        self.notifier = notifier or NoopNotifier()
        self._scheduler = BackgroundScheduler(timezone=SH_TZ)
        self._last_catchup_attempt_date: date | None = None

    def start(self) -> None:
        if not self.settings.enable_scheduler:
            return
        if self._scheduler.running:
            return

        # Daily EOD incremental bar sync on Mon-Fri
        self._scheduler.add_job(
            self._run_eod_bars_job,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=self.settings.eod_sync_hour,
                minute=self.settings.eod_sync_minute,
                timezone=SH_TZ,
            ),
            id="eod_daily_bars_sync",
            replace_existing=True,
            max_instances=1,
        )

        # Weekly metadata & OTC mapping refresh on Sundays at 10:00
        self._scheduler.add_job(
            self._run_weekly_metadata_job,
            trigger=CronTrigger(day_of_week="sun", hour=10, minute=0, timezone=SH_TZ),
            id="weekly_metadata_sync",
            replace_existing=True,
            max_instances=1,
        )

        # Periodic sleep/wake catch-up check
        self._scheduler.add_job(
            self.check_and_catchup,
            trigger=IntervalTrigger(minutes=self.settings.catchup_interval_minutes),
            id="catchup_checker",
            replace_existing=True,
            max_instances=1,
        )

        self._scheduler.start()
        logger.info("AppScheduler started (EOD %02d:%02d CST)", self.settings.eod_sync_hour, self.settings.eod_sync_minute)

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def check_and_catchup(self) -> bool:
        """If Mac slept past EOD sync and local bars lag expected weekday, trigger incremental sync."""
        if self.sync_service.is_running():
            return False
        stats = self.sqlite.get_universe_stats()
        if stats["total_exchange_funds"] == 0:
            return False

        now_sh = datetime.now(SH_TZ)
        expected = expected_latest_weekday(
            now_sh, self.settings.eod_sync_hour, self.settings.eod_sync_minute
        )
        local_latest = self.bars.get_global_latest_trade_date()

        # Avoid hammering upstream on public holidays when market is closed
        if self._last_catchup_attempt_date == expected:
            return False

        if local_latest is None or local_latest < expected:
            self._last_catchup_attempt_date = expected
            logger.info(
                "Catch-up triggered: local_latest=%s < expected=%s",
                local_latest,
                expected,
            )
            self._run_eod_bars_job()
            return True
        return False

    def _run_eod_bars_job(self) -> None:
        if self.sync_service.is_running():
            return
        try:
            self.sync_service.sync_daily_bars(full_refresh=False)
        except Exception as exc:
            logger.exception("Scheduled EOD bar sync failed")
            self.notifier.notify_sync_alert("盘后日K同步失败", str(exc))

    def _run_weekly_metadata_job(self) -> None:
        if self.sync_service.is_running():
            return
        try:
            self.sync_service.sync_metadata(force=False)
        except Exception as exc:
            logger.exception("Scheduled weekly metadata sync failed")
            self.notifier.notify_sync_alert("每周元数据同步失败", str(exc))

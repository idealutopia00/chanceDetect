"""Extensible notification interface (MVP ships with NoopNotifier)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseNotifier(ABC):
    """Abstract notifier hook invoked after scheduled EOD scans or sync alerts."""

    @abstractmethod
    def notify_scan_summary(self, trade_date: str, scan_result: dict[str, Any]) -> None:
        """Send opportunity scan summary."""

    @abstractmethod
    def notify_sync_alert(self, title: str, detail: str) -> None:
        """Send data source health / failure alert."""


class NoopNotifier(BaseNotifier):
    """Default no-op notifier for MVP (zero external side effects)."""

    def notify_scan_summary(self, trade_date: str, scan_result: dict[str, Any]) -> None:
        return None

    def notify_sync_alert(self, title: str, detail: str) -> None:
        return None

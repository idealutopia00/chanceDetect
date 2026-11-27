"""Application configuration for ChanceDetect."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings, overridable via CHANCE_* environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="CHANCE_",
        env_file=".env",
        extra="ignore",
    )

    data_dir: Path = Field(default=Path("data"), description="Root directory for SQLite & Parquet")
    frontend_dist_dir: Path = Field(
        default=Path("frontend/dist"),
        description="Built React SPA static assets directory",
    )

    # Data ingestion defaults
    history_years: int = Field(default=5, ge=1, le=20)
    kline_concurrency: int = Field(default=12, ge=1, le=40)
    metadata_concurrency: int = Field(default=16, ge=1, le=40)
    request_timeout_sec: float = Field(default=12.0, ge=2.0, le=60.0)
    drift_overlap_days: int = Field(default=5, ge=2, le=20)
    drift_tolerance_pct: float = Field(default=0.002, ge=0.0001, le=0.05)

    # Core benchmark indices for RS / rotation strategies
    benchmarks: list[str] = Field(
        default_factory=lambda: ["sh000300", "sh000905", "sz399006"],
    )

    # Scheduler settings (Asia/Shanghai)
    enable_scheduler: bool = Field(default=True)
    eod_sync_hour: int = Field(default=16, ge=0, le=23)
    eod_sync_minute: int = Field(default=15, ge=0, le=59)
    otc_nav_evening_hour: int = Field(default=21, ge=0, le=23)
    otc_nav_evening_minute: int = Field(default=30, ge=0, le=59)
    otc_nav_morning_hour: int = Field(default=9, ge=0, le=23)
    otc_nav_morning_minute: int = Field(default=0, ge=0, le=59)
    catchup_interval_minutes: int = Field(default=30, ge=5, le=240)

    # Server
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000)

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "metadata.sqlite"

    @property
    def bars_dir(self) -> Path:
        return self.data_dir / "bars"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def daily_bars_path(self) -> Path:
        return self.bars_dir / "daily_bars.parquet"

    @property
    def benchmarks_path(self) -> Path:
        return self.bars_dir / "benchmarks.parquet"

    @property
    def features_path(self) -> Path:
        return self.cache_dir / "features.parquet"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.bars_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings

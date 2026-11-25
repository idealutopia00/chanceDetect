"""Vectorized Polars strategy engine, indicators, built-in strategies, and signal replay."""

from chancedetect.strategies.base import BaseStrategy
from chancedetect.strategies.engine import (
    BaseUniverseFilter,
    ScanRequestConfig,
    StrategyEngine,
    StrategySelection,
)
from chancedetect.strategies.registry import StrategyRegistry, create_default_registry
from chancedetect.strategies.replay import build_fund_replay

__all__ = [
    "BaseStrategy",
    "BaseUniverseFilter",
    "ScanRequestConfig",
    "StrategyEngine",
    "StrategyRegistry",
    "StrategySelection",
    "build_fund_replay",
    "create_default_registry",
]

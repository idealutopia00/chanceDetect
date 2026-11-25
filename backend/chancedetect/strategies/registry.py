"""Central plugin registry for all built-in and custom strategies."""

from __future__ import annotations

from typing import Any

from chancedetect.strategies.base import BaseStrategy
from chancedetect.strategies.mean_reversion import (
    BollLowerReversalStrategy,
    MaNegativeBiasStrategy,
    RsiOversoldReboundStrategy,
)
from chancedetect.strategies.momentum import (
    MomentumTopRankStrategy,
    RelativeStrengthRsStrategy,
)
from chancedetect.strategies.trend import (
    MaBullishAlignmentStrategy,
    MacdGoldenCrossStrategy,
    VolumeHighBreakoutStrategy,
)


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, BaseStrategy] = {}

    def register(self, strategy: BaseStrategy) -> None:
        self._strategies[strategy.id] = strategy

    def get(self, strategy_id: str) -> BaseStrategy:
        if strategy_id not in self._strategies:
            raise KeyError(f"Unknown strategy_id: {strategy_id}")
        return self._strategies[strategy_id]

    def list_all(self) -> list[BaseStrategy]:
        return list(self._strategies.values())

    def list_meta(self) -> list[dict[str, Any]]:
        return [s.to_meta_dict() for s in self._strategies.values()]


def create_default_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    for strategy in (
        MaBullishAlignmentStrategy(),
        VolumeHighBreakoutStrategy(),
        MacdGoldenCrossStrategy(),
        RsiOversoldReboundStrategy(),
        BollLowerReversalStrategy(),
        MaNegativeBiasStrategy(),
        MomentumTopRankStrategy(),
        RelativeStrengthRsStrategy(),
    ):
        registry.register(strategy)
    return registry

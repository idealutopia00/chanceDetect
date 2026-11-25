"""Base strategy abstraction with Pydantic parameter schema generation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel

StrategyCategory = Literal["trend", "mean_reversion", "momentum"]


class BaseStrategy(ABC):
    """Abstract base class for vectorized multi-fund panel strategies."""

    id: str
    name: str
    category: StrategyCategory
    description: str
    params_model: type[BaseModel]

    @abstractmethod
    def evaluate(
        self,
        panel: pl.DataFrame,
        benchmarks: pl.DataFrame,
        params: BaseModel,
    ) -> pl.DataFrame:
        """Evaluate strategy across full (symbol, trade_date) panel.

        Returns DataFrame with columns:
          [symbol: Utf8, trade_date: Date, signal: Boolean, score: Float64, reason: Utf8]
        """

    def default_params(self) -> BaseModel:
        return self.params_model()

    def parse_params(self, raw: dict[str, Any] | None = None) -> BaseModel:
        return self.params_model.model_validate(raw or {})

    def to_meta_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "default_params": self.default_params().model_dump(),
            "params_schema": self.params_model.model_json_schema(),
        }

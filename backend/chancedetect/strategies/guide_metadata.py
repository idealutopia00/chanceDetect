"""Live Product Guide metadata builder backed by the strategy registry and canonical constants."""

from __future__ import annotations

from typing import Any

from chancedetect.strategies.base import BaseStrategy
from chancedetect.strategies.engine import BaseUniverseFilter, ScanRequestConfig
from chancedetect.strategies.indicators import TREND_STAGE_THRESHOLDS
from chancedetect.strategies.registry import StrategyRegistry
from chancedetect.strategies.replay import DEFAULT_HOLDING_HORIZONS, ENTRY_BASIS

DEFAULT_HITS_WINDOW_DAYS = 20
DEFAULT_SHORT_TERM_FEE_DAYS = 7
DEFAULT_SHORT_TERM_FEE_RATE_PCT = 1.5
DEFAULT_OTC_CUTOFF_TIME = "15:00"
DEFAULT_EOD_SYNC_TIME = "16:15"
DEFAULT_OTC_NAV_SYNC_TIMES: tuple[str, ...] = ("21:30", "09:00")

DEFAULT_EXIT_THRESHOLDS: dict[str, float | int] = {
    "trailing_drawdown_pct": -6.0,
    "cost_stop_loss_pct": -8.0,
    "ma20_watch_days": 1,
    "ma20_alert_days": 2,
    "short_term_fee_days": DEFAULT_SHORT_TERM_FEE_DAYS,
    "short_term_fee_rate_pct": DEFAULT_SHORT_TERM_FEE_RATE_PCT,
}

ROLE_LABELS: dict[str, str] = {
    "regime": "Trend Regime · 趋势滤网",
    "entry": "Entry Trigger · 买点触发器",
}

# Canonical built-in scenario templates matching the screening workbench
SCENARIO_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "id": "right_side_mainline",
        "name": "右侧主线买点",
        "badge": "默认",
        "description": "中期上升趋势 / RS 强弱筛选主线，捕捉回踩、多头启动、突破与金叉买点",
        "regime_combine_mode": "OR",
        "combine_mode": "OR",
        "min_hits": 2,
        "enabled_regimes": ["mid_term_uptrend", "relative_strength_rs"],
        "enabled_entries": [
            "trend_pullback_entry",
            "ma_bullish_alignment",
            "volume_high_breakout",
            "macd_golden_cross",
        ],
    },
    {
        "id": "left_side_oversold",
        "name": "左侧超跌抄底",
        "badge": None,
        "description": "关闭趋势过滤，专注 RSI 超卖、布林下轨与负乖离极端均值回归机会",
        "regime_combine_mode": "OR",
        "combine_mode": "OR",
        "min_hits": 2,
        "enabled_regimes": [],
        "enabled_entries": [
            "rsi_oversold_rebound",
            "boll_lower_reversal",
            "ma_negative_bias",
        ],
    },
    {
        "id": "strong_momentum_focus",
        "name": "强势动量聚焦",
        "badge": None,
        "description": "动量 Top K 与 RS 强弱双重确认主线，聚焦强势回踩与放量突破买点",
        "regime_combine_mode": "AND",
        "combine_mode": "OR",
        "min_hits": 2,
        "enabled_regimes": ["momentum_top_rank", "relative_strength_rs"],
        "enabled_entries": [
            "trend_pullback_entry",
            "volume_high_breakout",
        ],
    },
)


def _serialize_strategy(strategy: BaseStrategy) -> dict[str, Any]:
    meta = strategy.to_meta_dict()
    default_params: dict[str, Any] = meta["default_params"]
    properties: dict[str, dict[str, Any]] = meta["params_schema"].get("properties", {})

    params_list: list[dict[str, Any]] = []
    for key, default_val in default_params.items():
        prop = properties.get(key, {})
        params_list.append(
            {
                "key": key,
                "label": prop.get("description") or prop.get("title") or key,
                "type": prop.get("type", "string"),
                "default": default_val,
                "minimum": prop.get("minimum"),
                "maximum": prop.get("maximum"),
            }
        )

    return {
        "strategy_id": strategy.id,
        "id": strategy.id,
        "name": strategy.name,
        "category": strategy.category,
        "role": strategy.role,
        "role_label": ROLE_LABELS.get(strategy.role, strategy.role),
        "description": strategy.description,
        "default_params": default_params,
        "params": params_list,
        "params_schema": meta["params_schema"],
    }


def build_guide_metadata(registry: StrategyRegistry) -> dict[str, Any]:
    """Build live Product Guide metadata from the registered strategies and system defaults."""
    all_strategies = [_serialize_strategy(s) for s in registry.list_all()]
    name_by_id = {s["strategy_id"]: s["name"] for s in all_strategies}

    strategies_by_role = {
        "regime": [s for s in all_strategies if s["role"] == "regime"],
        "entry": [s for s in all_strategies if s["role"] == "entry"],
    }

    templates_out: list[dict[str, Any]] = []
    for tpl in SCENARIO_TEMPLATES:
        regimes = [sid for sid in tpl["enabled_regimes"] if sid in name_by_id]
        entries = [sid for sid in tpl["enabled_entries"] if sid in name_by_id]
        templates_out.append(
            {
                **tpl,
                "enabled_regimes": regimes,
                "enabled_entries": entries,
                "enabled_regime_names": [name_by_id[sid] for sid in regimes],
                "enabled_entry_names": [name_by_id[sid] for sid in entries],
            }
        )

    scan_defaults = ScanRequestConfig()
    base_filter_defaults = BaseUniverseFilter()

    system_defaults = {
        "freshness_cooldown_days": scan_defaults.freshness_cooldown_days,
        "hits_window_days": DEFAULT_HITS_WINDOW_DAYS,
        "replay_horizons": list(DEFAULT_HOLDING_HORIZONS),
        "replay_entry_basis": ENTRY_BASIS,
        "short_term_fee_days": DEFAULT_SHORT_TERM_FEE_DAYS,
        "short_term_fee_rate_pct": DEFAULT_SHORT_TERM_FEE_RATE_PCT,
        "otc_cutoff_time": DEFAULT_OTC_CUTOFF_TIME,
        "eod_sync_time": DEFAULT_EOD_SYNC_TIME,
        "otc_nav_sync_times": list(DEFAULT_OTC_NAV_SYNC_TIMES),
        "base_filter": {
            "min_avg_amount_20d": base_filter_defaults.min_avg_amount_20d,
            "min_listing_days": base_filter_defaults.min_listing_days,
        },
        "exit_thresholds": dict(DEFAULT_EXIT_THRESHOLDS),
        "trend_stage_thresholds": {
            stage: dict(values) for stage, values in TREND_STAGE_THRESHOLDS.items()
        },
    }

    return {
        "strategies": all_strategies,
        "strategies_by_role": strategies_by_role,
        "scenario_templates": templates_out,
        "system_defaults": system_defaults,
    }

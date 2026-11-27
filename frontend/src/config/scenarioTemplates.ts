import type {
  CombineMode,
  RegimeCombineMode,
  ScanRequestConfig,
  StrategyMeta,
} from '../types'

export type ScenarioTemplateId =
  | 'right_side_mainline'
  | 'left_side_oversold'
  | 'strong_momentum_focus'

export interface ScenarioTemplate {
  id: ScenarioTemplateId
  name: string
  badge?: string
  description: string
  regimeCombineMode: RegimeCombineMode
  combineMode: CombineMode
  minHits: number
  enabledRegimes: string[]
  enabledEntries: string[]
}

export const SCENARIO_TEMPLATES: readonly ScenarioTemplate[] = [
  {
    id: 'right_side_mainline',
    name: '右侧主线买点',
    badge: '默认',
    description: '中期上升趋势 / RS 强弱筛选主线，捕捉回踩、多头启动、突破与金叉买点',
    regimeCombineMode: 'OR',
    combineMode: 'OR',
    minHits: 2,
    enabledRegimes: ['mid_term_uptrend', 'relative_strength_rs'],
    enabledEntries: [
      'trend_pullback_entry',
      'ma_bullish_alignment',
      'volume_high_breakout',
      'macd_golden_cross',
    ],
  },
  {
    id: 'left_side_oversold',
    name: '左侧超跌抄底',
    description: '关闭趋势过滤，专注 RSI 超卖、布林下轨与负乖离极端均值回归机会',
    regimeCombineMode: 'OR',
    combineMode: 'OR',
    minHits: 2,
    enabledRegimes: [],
    enabledEntries: ['rsi_oversold_rebound', 'boll_lower_reversal', 'ma_negative_bias'],
  },
  {
    id: 'strong_momentum_focus',
    name: '强势动量聚焦',
    description: '动量 Top K 与 RS 强弱双重确认主线，聚焦强势回踩与放量突破买点',
    regimeCombineMode: 'AND',
    combineMode: 'OR',
    minHits: 2,
    enabledRegimes: ['momentum_top_rank', 'relative_strength_rs'],
    enabledEntries: ['trend_pullback_entry', 'volume_high_breakout'],
  },
]

export const DEFAULT_SCENARIO_TEMPLATE: ScenarioTemplate = SCENARIO_TEMPLATES[0]

export function applyScenarioTemplate(
  template: ScenarioTemplate,
  strategies: StrategyMeta[],
  baseConfig?: ScanRequestConfig | null,
): ScanRequestConfig {
  const enabledSet = new Set([...template.enabledRegimes, ...template.enabledEntries])

  return {
    strategies: strategies.map((s) => ({
      strategy_id: s.id,
      enabled: enabledSet.has(s.id),
      params: { ...s.default_params },
    })),
    regime_combine_mode: template.regimeCombineMode,
    combine_mode: template.combineMode,
    min_hits: template.minHits,
    hide_overheated: baseConfig?.hide_overheated ?? false,
    freshness_cooldown_days: baseConfig?.freshness_cooldown_days ?? 10,
    base_filter: baseConfig?.base_filter ?? {
      min_avg_amount_20d: 1000,
      min_listing_days: 60,
      fund_types: ['ETF', 'LOF'],
    },
    share_class_preference: baseConfig?.share_class_preference ?? 'C',
    group_by_index: baseConfig?.group_by_index ?? true,
    save_snapshot: false,
  }
}

function setsEqual(a: Set<string>, b: Set<string>): boolean {
  if (a.size !== b.size) return false
  for (const item of a) {
    if (!b.has(item)) return false
  }
  return true
}

export function matchActiveTemplateId(
  config: ScanRequestConfig,
  strategies: StrategyMeta[],
): ScenarioTemplateId | null {
  const roleById = new Map(strategies.map((s) => [s.id, s.role]))
  const activeRegimes = new Set<string>()
  const activeEntries = new Set<string>()

  for (const sel of config.strategies) {
    if (!sel.enabled) continue
    if (roleById.get(sel.strategy_id) === 'regime') {
      activeRegimes.add(sel.strategy_id)
    } else {
      activeEntries.add(sel.strategy_id)
    }
  }

  for (const tpl of SCENARIO_TEMPLATES) {
    const tplRegimes = new Set(tpl.enabledRegimes)
    const tplEntries = new Set(tpl.enabledEntries)
    const regimeModeMatches =
      tplRegimes.size === 0 || (config.regime_combine_mode ?? 'OR') === tpl.regimeCombineMode
    if (
      regimeModeMatches &&
      config.combine_mode === tpl.combineMode &&
      setsEqual(activeRegimes, tplRegimes) &&
      setsEqual(activeEntries, tplEntries)
    ) {
      return tpl.id
    }
  }

  return null
}

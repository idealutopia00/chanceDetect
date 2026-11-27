import { useState } from 'react'
import {
  Bookmark,
  ChevronDown,
  ChevronRight,
  Compass,
  Crosshair,
  Layers,
  Play,
  Save,
  SlidersHorizontal,
  Sparkles,
  Trash2,
} from 'lucide-react'
import {
  matchActiveTemplateId,
  SCENARIO_TEMPLATES,
  type ScenarioTemplate,
} from '../config/scenarioTemplates'
import { GlossaryTooltip } from './GlossaryTooltip'
import type {
  CombineMode,
  JsonSchemaProperty,
  PresetRecord,
  RegimeCombineMode,
  ScanRequestConfig,
  SharePreference,
  StrategyCategory,
  StrategyMeta,
  StrategyRole,
} from '../types'

interface StrategySidebarProps {
  strategies: StrategyMeta[]
  config: ScanRequestConfig
  presets: PresetRecord[]
  loading: boolean
  onConfigChange: (next: ScanRequestConfig) => void
  onRunScan: () => void
  onSelectTemplate: (template: ScenarioTemplate) => void
  onSavePreset: (name: string, description: string) => Promise<void>
  onDeletePreset: (id: number) => Promise<void>
  onLoadPreset: (preset: PresetRecord) => void
  onNavigateToGuide?: (anchorId: string) => void
}

const CATEGORY_META: Record<StrategyCategory, { label: string; badge: string }> = {
  trend: { label: '右侧趋势触发', badge: 'text-amber-300 bg-amber-500/10 border-amber-500/30' },
  mean_reversion: {
    label: '左侧超跌触发',
    badge: 'text-cyan-300 bg-cyan-500/10 border-cyan-500/30',
  },
  momentum: {
    label: '动量强弱过滤',
    badge: 'text-purple-300 bg-purple-500/10 border-purple-500/30',
  },
}

export function StrategySidebar({
  strategies,
  config,
  presets,
  loading,
  onConfigChange,
  onRunScan,
  onSelectTemplate,
  onSavePreset,
  onDeletePreset,
  onLoadPreset,
  onNavigateToGuide,
}: StrategySidebarProps) {
  const [expandedStrategy, setExpandedStrategy] = useState<string | null>(null)
  const [savingPreset, setSavingPreset] = useState(false)
  const [presetName, setPresetName] = useState('')

  const selectionMap = new Map(config.strategies.map((s) => [s.strategy_id, s]))
  const regimeStrategies = strategies.filter((s) => s.role === 'regime')
  const entryStrategies = strategies.filter((s) => s.role !== 'regime')

  const updateSelections = (
    updater: (meta: StrategyMeta, cur: ScanRequestConfig['strategies'][number]) => ScanRequestConfig['strategies'][number],
  ) => {
    const nextList = strategies.map((m) => {
      const cur = selectionMap.get(m.id) ?? {
        strategy_id: m.id,
        enabled: false,
        params: { ...m.default_params },
      }
      return updater(m, cur)
    })
    onConfigChange({ ...config, strategies: nextList })
  }

  const toggleStrategy = (meta: StrategyMeta) => {
    updateSelections((m, cur) => (m.id === meta.id ? { ...cur, enabled: !cur.enabled } : cur))
  }

  const setRoleEnabled = (role: StrategyRole, enabled: boolean) => {
    updateSelections((m, cur) => (m.role === role ? { ...cur, enabled } : cur))
  }

  const updateParam = (strategyId: string, key: string, value: unknown) => {
    updateSelections((m, cur) =>
      m.id === strategyId ? { ...cur, params: { ...cur.params, [key]: value } } : cur,
    )
  }

  const enabledRegimeCount = regimeStrategies.filter((s) => selectionMap.get(s.id)?.enabled).length
  const enabledEntryCount = entryStrategies.filter((s) => selectionMap.get(s.id)?.enabled).length
  const regimeCombineMode: RegimeCombineMode = config.regime_combine_mode ?? 'OR'
  const activeTemplateId = matchActiveTemplateId(config, strategies)

  const handleSavePreset = async () => {
    if (!presetName.trim()) return
    const antiChaseTag = config.hide_overheated ? ' · 过滤高位偏热' : ''
    await onSavePreset(
      presetName.trim(),
      `${enabledRegimeCount}主线(${regimeCombineMode}) + ${enabledEntryCount}买点(${config.combine_mode})${antiChaseTag}`,
    )
    setPresetName('')
    setSavingPreset(false)
  }

  const renderParamControl = (
    strategyId: string,
    key: string,
    prop: JsonSchemaProperty,
    value: unknown,
  ) => {
    const label = prop.description || prop.title || key
    if (prop.type === 'boolean') {
      return (
        <label key={key} className="flex items-center justify-between gap-2 py-1 text-xs">
          <span className="text-slate-300">{label}</span>
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => updateParam(strategyId, key, e.target.checked)}
            className="h-3.5 w-3.5 rounded border-slate-700 bg-slate-800 accent-amber-500"
          />
        </label>
      )
    }
    if (prop.type === 'integer' || prop.type === 'number') {
      return (
        <label key={key} className="block py-1 text-xs">
          <div className="mb-1 flex items-center justify-between text-slate-400">
            <span>{label}</span>
            <span className="font-mono text-slate-200">{String(value ?? prop.default ?? '')}</span>
          </div>
          <input
            type="number"
            step={prop.type === 'integer' ? 1 : 0.1}
            min={prop.minimum}
            max={prop.maximum}
            value={value !== undefined && value !== null ? String(value) : ''}
            onChange={(e) => {
              const raw = e.target.value
              if (raw === '') return
              const parsed = prop.type === 'integer' ? parseInt(raw, 10) : parseFloat(raw)
              if (!Number.isNaN(parsed)) updateParam(strategyId, key, parsed)
            }}
            className="w-full rounded border border-slate-700/80 bg-slate-950 px-2 py-1 font-mono text-xs text-slate-100 focus:border-amber-500 focus:outline-none"
          />
        </label>
      )
    }
    return (
      <label key={key} className="block py-1 text-xs">
        <span className="mb-1 block text-slate-400">{label}</span>
        <input
          type="text"
          value={String(value ?? prop.default ?? '')}
          onChange={(e) => updateParam(strategyId, key, e.target.value)}
          className="w-full rounded border border-slate-700/80 bg-slate-950 px-2 py-1 font-mono text-xs text-slate-100 focus:border-amber-500 focus:outline-none"
        />
      </label>
    )
  }

  const renderStrategyCard = (meta: StrategyMeta, accent: 'purple' | 'amber') => {
    const sel = selectionMap.get(meta.id)
    const enabled = sel?.enabled ?? false
    const isExpanded = expandedStrategy === meta.id
    const props = meta.params_schema?.properties ?? {}
    const paramKeys = Object.keys(props)

    const activeBorder =
      accent === 'purple'
        ? 'border-purple-500/40 bg-slate-800/70'
        : 'border-amber-500/40 bg-slate-800/70'
    const checkboxAccent = accent === 'purple' ? 'accent-purple-500' : 'accent-amber-500'
    const titleHover = accent === 'purple' ? 'hover:text-purple-300' : 'hover:text-amber-300'

    return (
      <div
        key={meta.id}
        className={`rounded-lg border transition ${
          enabled ? activeBorder : 'border-slate-800 bg-slate-950/50'
        }`}
      >
        <div className="flex items-start gap-2.5 p-2.5">
          <input
            type="checkbox"
            checked={enabled}
            onChange={() => toggleStrategy(meta)}
            className={`mt-0.5 h-4 w-4 shrink-0 rounded ${checkboxAccent}`}
          />
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-1">
              <button
                type="button"
                onClick={() => toggleStrategy(meta)}
                className={`text-left text-xs font-medium text-slate-100 ${titleHover}`}
              >
                {meta.name}
              </button>
              {paramKeys.length > 0 && (
                <button
                  type="button"
                  onClick={() => setExpandedStrategy(isExpanded ? null : meta.id)}
                  className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[10px] text-slate-400 hover:bg-slate-700/60 hover:text-slate-200"
                >
                  调参
                  {isExpanded ? (
                    <ChevronDown className="h-3 w-3" />
                  ) : (
                    <ChevronRight className="h-3 w-3" />
                  )}
                </button>
              )}
            </div>
            <p className="mt-0.5 text-[11px] leading-relaxed text-slate-400">{meta.description}</p>
          </div>
        </div>

        {isExpanded && paramKeys.length > 0 && (
          <div className="border-t border-slate-700/60 bg-slate-900/80 px-3 py-2 space-y-1">
            {paramKeys.map((k) =>
              renderParamControl(meta.id, k, props[k], sel?.params?.[k] ?? meta.default_params[k]),
            )}
          </div>
        )}
      </div>
    )
  }

  return (
    <aside className="flex w-full flex-col gap-4 lg:w-[350px] lg:shrink-0">
      {/* Quick Scenario Templates Card */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 space-y-2.5">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-1.5 text-xs font-semibold text-slate-100">
            <Sparkles className="h-3.5 w-3.5 text-amber-400" />
            快速场景模板
          </span>
          <span className="text-[10px] text-slate-500">一键切换并扫描</span>
        </div>

        <div className="grid grid-cols-1 gap-1.5">
          {SCENARIO_TEMPLATES.map((tpl) => {
            const isActive = activeTemplateId === tpl.id
            return (
              <button
                key={tpl.id}
                type="button"
                disabled={loading}
                onClick={() => onSelectTemplate(tpl)}
                className={`rounded-lg border px-3 py-2 text-left transition disabled:opacity-60 ${
                  isActive
                    ? 'border-amber-500/60 bg-amber-500/10 ring-1 ring-amber-500/30'
                    : 'border-slate-800 bg-slate-950/60 hover:border-slate-700 hover:bg-slate-800/50'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span
                    className={`text-xs font-semibold ${
                      isActive ? 'text-amber-300' : 'text-slate-100'
                    }`}
                  >
                    {tpl.name}
                  </span>
                  <div className="flex items-center gap-1">
                    {tpl.badge && (
                      <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-medium text-amber-300">
                        {tpl.badge}
                      </span>
                    )}
                    {isActive && (
                      <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">
                        当前激活
                      </span>
                    )}
                  </div>
                </div>
                <p className="mt-0.5 text-[11px] leading-snug text-slate-400">{tpl.description}</p>
              </button>
            )
          })}
        </div>
      </div>

      {/* Action & Presets Card */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <button
          type="button"
          onClick={onRunScan}
          disabled={loading}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-amber-500 to-rose-600 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-amber-600/20 transition hover:brightness-110 disabled:opacity-50"
        >
          <Play className="h-4 w-4 fill-current" />
          {loading
            ? '正在极速扫描...'
            : `立即扫描机会 (${enabledRegimeCount} 主线 · ${enabledEntryCount} 买点)`}
        </button>

        {/* Presets */}
        <div className="mt-3 border-t border-slate-800 pt-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="flex items-center gap-1.5 text-xs font-medium text-slate-400">
              <Bookmark className="h-3.5 w-3.5 text-amber-400" />
              策略组合预设
            </span>
            <button
              type="button"
              onClick={() => setSavingPreset((v) => !v)}
              className="inline-flex items-center gap-1 text-[11px] text-amber-400 hover:text-amber-300"
            >
              <Save className="h-3 w-3" />
              保存当前
            </button>
          </div>

          {savingPreset && (
            <div className="mb-2 flex gap-1.5">
              <input
                type="text"
                placeholder="输入预设名称..."
                value={presetName}
                onChange={(e) => setPresetName(e.target.value)}
                className="flex-1 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-100 focus:border-amber-500 focus:outline-none"
              />
              <button
                type="button"
                onClick={handleSavePreset}
                className="rounded bg-amber-500 px-2.5 py-1 text-xs font-medium text-slate-950 hover:bg-amber-400"
              >
                保存
              </button>
            </div>
          )}

          {presets.length === 0 ? (
            <p className="text-[11px] text-slate-500">暂无保存的预设方案</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {presets.map((p) => (
                <div
                  key={p.id}
                  className="group flex items-center gap-1 rounded-md border border-slate-700/80 bg-slate-800/70 px-2 py-1 text-[11px] text-slate-200"
                >
                  <button
                    type="button"
                    onClick={() => onLoadPreset(p)}
                    className="hover:text-amber-300"
                    title={p.description}
                  >
                    {p.name}
                  </button>
                  <button
                    type="button"
                    onClick={() => onDeletePreset(p.id)}
                    className="text-slate-500 hover:text-rose-400"
                    title="删除预设"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Stage 1: Trend Regime Filters */}
      <div className="rounded-xl border border-purple-500/25 bg-slate-900/70 p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Compass className="h-3.5 w-3.5 text-purple-400" />
            <span className="text-xs font-semibold text-slate-100">
              趋势过滤 · Trend Regime
            </span>
            <GlossaryTooltip termId="trend-regime" onNavigateToGuide={onNavigateToGuide} />
            <span className="rounded bg-purple-500/15 px-1.5 py-0.5 font-mono text-[10px] text-purple-300">
              {enabledRegimeCount}/{regimeStrategies.length}
            </span>
          </div>
          <div className="flex gap-2 text-[11px]">
            <button
              type="button"
              onClick={() => setRoleEnabled('regime', true)}
              className="text-slate-400 hover:text-purple-300"
            >
              全选
            </button>
            <button
              type="button"
              onClick={() => setRoleEnabled('regime', false)}
              className="text-slate-400 hover:text-slate-200"
            >
              清空
            </button>
          </div>
        </div>

        <p className="text-[11px] leading-relaxed text-slate-400">
          筛选强势主线候选池；清空则不做趋势限定（适合纯左侧超跌扫描）。
        </p>

        <div>
          <label className="mb-1.5 block text-[11px] text-slate-400">趋势过滤组合逻辑</label>
          <div className="grid grid-cols-2 gap-1 rounded-lg bg-slate-950 p-1">
            {(
              [
                ['OR', '任一主线满足 (OR)'],
                ['AND', '全部主线满足 (AND)'],
              ] as Array<[RegimeCombineMode, string]>
            ).map(([mode, label]) => (
              <button
                key={mode}
                type="button"
                onClick={() => onConfigChange({ ...config, regime_combine_mode: mode })}
                className={`rounded-md py-1.5 text-[11px] font-medium transition ${
                  regimeCombineMode === mode
                    ? 'bg-slate-800 text-purple-300 ring-1 ring-purple-500/40'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-2">
          {regimeStrategies.map((meta) => renderStrategyCard(meta, 'purple'))}
        </div>
      </div>

      {/* Stage 2: Entry Triggers */}
      <div className="rounded-xl border border-amber-500/25 bg-slate-900/70 p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Crosshair className="h-3.5 w-3.5 text-amber-400" />
            <span className="text-xs font-semibold text-slate-100">
              买点触发 · Entry Trigger
            </span>
            <GlossaryTooltip termId="entry-trigger" onNavigateToGuide={onNavigateToGuide} />
            <span className="rounded bg-amber-500/15 px-1.5 py-0.5 font-mono text-[10px] text-amber-300">
              {enabledEntryCount}/{entryStrategies.length}
            </span>
          </div>
          <div className="flex gap-2 text-[11px]">
            <button
              type="button"
              onClick={() => setRoleEnabled('entry', true)}
              className="text-slate-400 hover:text-amber-300"
            >
              全选
            </button>
            <button
              type="button"
              onClick={() => setRoleEnabled('entry', false)}
              className="text-slate-400 hover:text-slate-200"
            >
              清空
            </button>
          </div>
        </div>

        <p className="text-[11px] leading-relaxed text-slate-400">
          捕捉具体入场时机；通过趋势过滤但未触发买点的标的自动归入「主线观察 · 等回踩」。
        </p>

        <div>
          <label className="mb-1.5 block text-[11px] text-slate-400">买点触发组合逻辑</label>
          <div className="grid grid-cols-3 gap-1 rounded-lg bg-slate-950 p-1">
            {(
              [
                ['OR', '任一触发'],
                ['RESONANCE', '多触发共振'],
                ['AND', '全部触发'],
              ] as Array<[CombineMode, string]>
            ).map(([mode, label]) => (
              <button
                key={mode}
                type="button"
                onClick={() => onConfigChange({ ...config, combine_mode: mode })}
                className={`rounded-md py-1.5 text-[11px] font-medium transition ${
                  config.combine_mode === mode
                    ? 'bg-slate-800 text-amber-300 ring-1 ring-amber-500/40'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {config.combine_mode === 'RESONANCE' && (
            <div className="mt-2 flex items-center justify-between text-xs">
              <span className="text-slate-400">最少共振命中买点数</span>
              <input
                type="number"
                min={1}
                max={Math.max(1, enabledEntryCount)}
                value={config.min_hits}
                onChange={(e) =>
                  onConfigChange({
                    ...config,
                    min_hits: Math.max(1, parseInt(e.target.value, 10) || 1),
                  })
                }
                className="w-16 rounded border border-slate-700 bg-slate-950 px-2 py-0.5 text-center font-mono text-xs text-amber-300"
              />
            </div>
          )}
        </div>

        {/* One-Click Anti-Chase Toggle */}
        <label
          className={`flex cursor-pointer items-center justify-between gap-2 rounded-lg border px-2.5 py-2 transition ${
            config.hide_overheated
              ? 'border-rose-500/45 bg-rose-500/10'
              : 'border-slate-800 bg-slate-950/60 hover:border-slate-700'
          }`}
        >
          <div>
            <div className="flex items-center gap-1 text-xs font-medium text-slate-100">
              <span>过滤高位偏热</span>
              <GlossaryTooltip termId="anti-chase" onNavigateToGuide={onNavigateToGuide} />
            </div>
            <div className="text-[10px] text-slate-400">
              从今日可买中隐藏「高位偏热」标的，主线观察池不受影响
            </div>
          </div>
          <input
            type="checkbox"
            checked={Boolean(config.hide_overheated)}
            onChange={(e) => onConfigChange({ ...config, hide_overheated: e.target.checked })}
            className="h-4 w-4 shrink-0 rounded accent-rose-500"
          />
        </label>

        {(['trend', 'mean_reversion', 'momentum'] as StrategyCategory[]).map((cat) => {
          const catStrats = entryStrategies.filter((s) => s.category === cat)
          if (catStrats.length === 0) return null
          return (
            <div key={cat} className="space-y-2 pt-1">
              <div className="flex items-center gap-2">
                <span
                  className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${CATEGORY_META[cat].badge}`}
                >
                  {CATEGORY_META[cat].label}
                </span>
              </div>
              {catStrats.map((meta) => renderStrategyCard(meta, 'amber'))}
            </div>
          )
        })}
      </div>

      {/* Preferences & Liquidity Floor */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 space-y-3.5">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-200">
          <Layers className="h-3.5 w-3.5 text-amber-400" />
          份额偏好与流动性底线
        </div>

        <div>
          <label className="mb-1.5 block text-[11px] text-slate-400">场外基金份额优先排序</label>
          <div className="grid grid-cols-2 gap-1 rounded-lg bg-slate-950 p-1">
            {(
              [
                ['C', 'C类优先 (短线0申购费)'],
                ['A', 'A类优先 (长线低运作费)'],
              ] as Array<[SharePreference, string]>
            ).map(([pref, label]) => (
              <button
                key={pref}
                type="button"
                onClick={() => onConfigChange({ ...config, share_class_preference: pref })}
                className={`rounded-md py-1.5 text-[11px] font-medium transition ${
                  config.share_class_preference === pref
                    ? 'bg-slate-800 text-emerald-300 ring-1 ring-emerald-500/40'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <label className="flex items-center justify-between text-xs">
          <span className="inline-flex items-center gap-1 text-slate-300">
            按跟踪指数智能聚合去重
            <GlossaryTooltip termId="index-group" onNavigateToGuide={onNavigateToGuide} />
          </span>
          <input
            type="checkbox"
            checked={config.group_by_index}
            onChange={(e) => onConfigChange({ ...config, group_by_index: e.target.checked })}
            className="h-4 w-4 rounded accent-amber-500"
          />
        </label>

        {/* Liquidity Floor */}
        <div className="border-t border-slate-800 pt-3">
          <div className="mb-2 flex items-center gap-1.5 text-[11px] font-medium text-slate-400">
            <SlidersHorizontal className="h-3 w-3" />
            底线流动性与池子过滤
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className="block text-[11px] text-slate-400">
              20日均额 ≥ (万元)
              <input
                type="number"
                min={0}
                step={500}
                value={config.base_filter.min_avg_amount_20d}
                onChange={(e) =>
                  onConfigChange({
                    ...config,
                    base_filter: {
                      ...config.base_filter,
                      min_avg_amount_20d: Math.max(0, Number(e.target.value) || 0),
                    },
                  })
                }
                className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-slate-100"
              />
            </label>
            <label className="block text-[11px] text-slate-400">
              最少上市天数
              <input
                type="number"
                min={0}
                step={10}
                value={config.base_filter.min_listing_days}
                onChange={(e) =>
                  onConfigChange({
                    ...config,
                    base_filter: {
                      ...config.base_filter,
                      min_listing_days: Math.max(0, Number(e.target.value) || 0),
                    },
                  })
                }
                className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-slate-100"
              />
            </label>
          </div>
        </div>
      </div>
    </aside>
  )
}

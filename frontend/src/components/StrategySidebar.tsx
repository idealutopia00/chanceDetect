import { useState } from 'react'
import {
  Bookmark,
  ChevronDown,
  ChevronRight,
  Layers,
  Play,
  Save,
  SlidersHorizontal,
  Trash2,
} from 'lucide-react'
import type {
  CombineMode,
  JsonSchemaProperty,
  PresetRecord,
  ScanRequestConfig,
  SharePreference,
  StrategyCategory,
  StrategyMeta,
} from '../types'

interface StrategySidebarProps {
  strategies: StrategyMeta[]
  config: ScanRequestConfig
  presets: PresetRecord[]
  loading: boolean
  onConfigChange: (next: ScanRequestConfig) => void
  onRunScan: () => void
  onSavePreset: (name: string, description: string) => Promise<void>
  onDeletePreset: (id: number) => Promise<void>
  onLoadPreset: (preset: PresetRecord) => void
}

const CATEGORY_META: Record<StrategyCategory, { label: string; badge: string }> = {
  trend: { label: '趋势突破类', badge: 'text-amber-300 bg-amber-500/10 border-amber-500/30' },
  mean_reversion: {
    label: '超跌回归类',
    badge: 'text-cyan-300 bg-cyan-500/10 border-cyan-500/30',
  },
  momentum: {
    label: '动量轮动类',
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
  onSavePreset,
  onDeletePreset,
  onLoadPreset,
}: StrategySidebarProps) {
  const [expandedStrategy, setExpandedStrategy] = useState<string | null>(null)
  const [savingPreset, setSavingPreset] = useState(false)
  const [presetName, setPresetName] = useState('')

  const selectionMap = new Map(config.strategies.map((s) => [s.strategy_id, s]))

  const toggleStrategy = (meta: StrategyMeta) => {
    const existing = selectionMap.get(meta.id)
    const nextList = strategies.map((m) => {
      const cur = selectionMap.get(m.id) ?? {
        strategy_id: m.id,
        enabled: false,
        params: { ...m.default_params },
      }
      if (m.id === meta.id) {
        return { ...cur, enabled: !(existing?.enabled ?? false) }
      }
      return cur
    })
    onConfigChange({ ...config, strategies: nextList })
  }

  const updateParam = (strategyId: string, key: string, value: unknown) => {
    const nextList = config.strategies.map((s) =>
      s.strategy_id === strategyId ? { ...s, params: { ...s.params, [key]: value } } : s,
    )
    onConfigChange({ ...config, strategies: nextList })
  }

  const enabledCount = config.strategies.filter((s) => s.enabled).length

  const handleSavePreset = async () => {
    if (!presetName.trim()) return
    await onSavePreset(presetName.trim(), `${enabledCount}个策略 / ${config.combine_mode}`)
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

  return (
    <aside className="flex w-full flex-col gap-4 lg:w-[340px] lg:shrink-0">
      {/* Action & Presets Card */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <button
          type="button"
          onClick={onRunScan}
          disabled={loading}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-amber-500 to-rose-600 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-amber-600/20 transition hover:brightness-110 disabled:opacity-50"
        >
          <Play className="h-4 w-4 fill-current" />
          {loading ? '正在极速扫描...' : `立即扫描机会 (${enabledCount} 个策略)`}
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

      {/* Combination & Preferences */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 space-y-3.5">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-200">
          <Layers className="h-3.5 w-3.5 text-amber-400" />
          组合规则与偏好
        </div>

        <div>
          <label className="mb-1.5 block text-[11px] text-slate-400">多策略组合逻辑</label>
          <div className="grid grid-cols-3 gap-1 rounded-lg bg-slate-950 p-1">
            {(
              [
                ['OR', '任一命中'],
                ['RESONANCE', '多策略共振'],
                ['AND', '全部命中'],
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
              <span className="text-slate-400">最少共振命中策略数</span>
              <input
                type="number"
                min={1}
                max={Math.max(1, enabledCount)}
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
          <span className="text-slate-300">按跟踪指数智能聚合去重</span>
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

      {/* Strategy Collection */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 space-y-4">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-slate-200">策略集合 ({strategies.length})</span>
          <div className="flex gap-2 text-[11px]">
            <button
              type="button"
              onClick={() =>
                onConfigChange({
                  ...config,
                  strategies: config.strategies.map((s) => ({ ...s, enabled: true })),
                })
              }
              className="text-slate-400 hover:text-amber-300"
            >
              全选
            </button>
            <button
              type="button"
              onClick={() =>
                onConfigChange({
                  ...config,
                  strategies: config.strategies.map((s) => ({ ...s, enabled: false })),
                })
              }
              className="text-slate-400 hover:text-slate-200"
            >
              清空
            </button>
          </div>
        </div>

        {(['trend', 'mean_reversion', 'momentum'] as StrategyCategory[]).map((cat) => {
          const catStrats = strategies.filter((s) => s.category === cat)
          if (catStrats.length === 0) return null
          return (
            <div key={cat} className="space-y-2">
              <div className="flex items-center gap-2">
                <span
                  className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${CATEGORY_META[cat].badge}`}
                >
                  {CATEGORY_META[cat].label}
                </span>
              </div>

              {catStrats.map((meta) => {
                const sel = selectionMap.get(meta.id)
                const enabled = sel?.enabled ?? false
                const isExpanded = expandedStrategy === meta.id
                const props = meta.params_schema?.properties ?? {}
                const paramKeys = Object.keys(props)

                return (
                  <div
                    key={meta.id}
                    className={`rounded-lg border transition ${
                      enabled
                        ? 'border-amber-500/40 bg-slate-800/70'
                        : 'border-slate-800 bg-slate-950/50'
                    }`}
                  >
                    <div className="flex items-start gap-2.5 p-2.5">
                      <input
                        type="checkbox"
                        checked={enabled}
                        onChange={() => toggleStrategy(meta)}
                        className="mt-0.5 h-4 w-4 shrink-0 rounded accent-amber-500"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-1">
                          <button
                            type="button"
                            onClick={() => toggleStrategy(meta)}
                            className="text-left text-xs font-medium text-slate-100 hover:text-amber-300"
                          >
                            {meta.name}
                          </button>
                          {paramKeys.length > 0 && (
                            <button
                              type="button"
                              onClick={() =>
                                setExpandedStrategy(isExpanded ? null : meta.id)
                              }
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
                        <p className="mt-0.5 text-[11px] leading-relaxed text-slate-400">
                          {meta.description}
                        </p>
                      </div>
                    </div>

                    {isExpanded && paramKeys.length > 0 && (
                      <div className="border-t border-slate-700/60 bg-slate-900/80 px-3 py-2 space-y-1">
                        {paramKeys.map((k) =>
                          renderParamControl(
                            meta.id,
                            k,
                            props[k],
                            sel?.params?.[k] ?? meta.default_params[k],
                          ),
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>
    </aside>
  )
}

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BookOpen,
  CalendarClock,
  Compass,
  Crosshair,
  Gauge,
  Layers,
  RefreshCw,
  Search,
  ShieldAlert,
  Sparkles,
  X,
} from 'lucide-react'
import { api } from '../api/client'
import { STRATEGY_GUIDE_COPY, TEMPLATE_GUIDE_COPY } from '../config/guideContent'
import type {
  GuideMetadataResponse,
  GuideScenarioTemplate,
  GuideStrategyMeta,
  StrategyCategory,
} from '../types'

export interface GuideNavigationTarget {
  anchorId: string
  seq: number
}

interface ProductGuidePanelProps {
  targetAnchor?: GuideNavigationTarget | null
  onAnchorHandled?: () => void
}

interface TocItem {
  id: string
  label: string
  chapter: number
}

const TOC_CHAPTERS: Array<{
  chapter: number
  id: string
  title: string
  shortTitle: string
  items: TocItem[]
}> = [
  {
    chapter: 1,
    id: 'chapter-quickstart',
    title: '1. 3 分钟上手与场内外代理映射',
    shortTitle: '上手与映射',
    items: [
      { id: 'term-proxy-mapping', label: '场内代理 → 场外映射原理', chapter: 1 },
      { id: 'term-index-group', label: '同指数智能聚合去重', chapter: 1 },
      { id: 'term-share-class', label: 'A 类 vs C 类份额选择', chapter: 1 },
    ],
  },
  {
    chapter: 2,
    id: 'chapter-strategies',
    title: '2. 10 大策略百科与 3 大场景模板',
    shortTitle: '策略与模板',
    items: [
      { id: 'term-two-stage-funnel', label: '两阶段漏斗（可买 vs 观察池）', chapter: 2 },
      { id: 'term-scenario-templates', label: '3 大内置场景模板', chapter: 2 },
      { id: 'term-trend-regime', label: 'Trend Regime · 3 大趋势滤网', chapter: 2 },
      { id: 'term-entry-trigger', label: 'Entry Trigger · 7 大买点触发器', chapter: 2 },
    ],
  },
  {
    chapter: 3,
    id: 'chapter-indicators',
    title: '3. Trend Stage 与核心量化指标',
    shortTitle: '阶段与指标',
    items: [
      { id: 'term-trend-stage', label: 'Trend Stage 四档趋势阶段', chapter: 3 },
      { id: 'term-bias20', label: 'MA20 / MA60 乖离率', chapter: 3 },
      { id: 'term-bias20-z', label: '波动率归一化 bias20_z', chapter: 3 },
      { id: 'term-boll-pct-b', label: '布林带位置 %B', chapter: 3 },
      { id: 'term-rsi14', label: 'RSI14 与 RS 相对强弱', chapter: 3 },
      { id: 'term-fresh-signal', label: 'Fresh Signal 新信号与防追高', chapter: 3 },
    ],
  },
  {
    chapter: 4,
    id: 'chapter-execution',
    title: '4. 场外实操与持仓复盘规则',
    shortTitle: '场外与复盘',
    items: [
      { id: 'term-t-plus-1', label: 'T 日信号 vs T+1 净值与回看', chapter: 4 },
      { id: 'term-cutoff-1500', label: '15:00 截单与非交易日顺延', chapter: 4 },
      { id: 'term-pending-nav', label: 'Pending NAV 待确认净值', chapter: 4 },
      { id: 'term-short-term-fee', label: '7 自然日 FIFO 短赎费规则', chapter: 4 },
      { id: 'term-dual-curve', label: '复权双曲线与清仓复盘', chapter: 4 },
      { id: 'term-exit-signals', label: '5 类 Exit Signal 卖出信号', chapter: 4 },
    ],
  },
]

const QUICK_JUMP_PILLS: Array<{ label: string; anchorId: string }> = [
  { label: 'Trend Regime', anchorId: 'term-trend-regime' },
  { label: 'Entry Trigger', anchorId: 'term-entry-trigger' },
  { label: 'Trend Stage', anchorId: 'term-trend-stage' },
  { label: 'bias20_z', anchorId: 'term-bias20-z' },
  { label: 'Fresh Signal', anchorId: 'term-fresh-signal' },
  { label: 'T+1 回看', anchorId: 'term-t-plus-1' },
  { label: '7日短赎费', anchorId: 'term-short-term-fee' },
  { label: 'Exit Signals', anchorId: 'term-exit-signals' },
]

const CATEGORY_BADGE: Record<StrategyCategory, { label: string; cls: string }> = {
  trend: { label: '右侧趋势', cls: 'border-amber-500/35 bg-amber-500/10 text-amber-300' },
  mean_reversion: { label: '左侧超跌', cls: 'border-cyan-500/35 bg-cyan-500/10 text-cyan-300' },
  momentum: { label: '动量强弱', cls: 'border-purple-500/35 bg-purple-500/10 text-purple-300' },
}

function formatParamValue(val: unknown): string {
  if (typeof val === 'boolean') return val ? '开启 (true)' : '关闭 (false)'
  if (val === null || val === undefined) return '-'
  return String(val)
}

function matchesQuery(query: string, ...parts: Array<string | number | null | undefined>): boolean {
  if (!query) return true
  const haystack = parts
    .filter((p) => p !== null && p !== undefined)
    .join(' ')
    .toLowerCase()
  return query
    .split(/\s+/)
    .filter(Boolean)
    .every((token) => haystack.includes(token))
}

export function ProductGuidePanel({ targetAnchor, onAnchorHandled }: ProductGuidePanelProps) {
  const [metadata, setMetadata] = useState<GuideMetadataResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [highlightedAnchor, setHighlightedAnchor] = useState<string | null>(null)

  const loadMetadata = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.guideMetadata()
      setMetadata(res)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadMetadata()
  }, [loadMetadata])

  const jumpToAnchor = useCallback((anchorId: string) => {
    setSearchQuery('')
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        const el = document.getElementById(anchorId)
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' })
          setHighlightedAnchor(anchorId)
        }
      })
    })
  }, [])

  useEffect(() => {
    if (!targetAnchor?.anchorId || loading) return
    jumpToAnchor(targetAnchor.anchorId)
    onAnchorHandled?.()
  }, [targetAnchor, loading, jumpToAnchor, onAnchorHandled])

  useEffect(() => {
    if (!highlightedAnchor) return
    const timer = window.setTimeout(() => {
      setHighlightedAnchor(null)
    }, 2800)
    return () => window.clearTimeout(timer)
  }, [highlightedAnchor])

  const normalizedQuery = searchQuery.trim().toLowerCase()

  const cardHighlightClass = (id: string) =>
    highlightedAnchor === id
      ? 'ring-2 ring-amber-400/90 border-amber-400/70 bg-amber-500/[0.07] shadow-lg shadow-amber-500/10'
      : 'border-slate-800/90 bg-slate-900/75'

  const defaults = metadata?.system_defaults
  const stageThresholds = defaults?.trend_stage_thresholds
  const exitThresholds = defaults?.exit_thresholds
  const replayHorizons = defaults?.replay_horizons ?? [5, 10, 20, 40]
  const cooldownDays = defaults?.freshness_cooldown_days ?? 10
  const hitsWindowDays = defaults?.hits_window_days ?? 20
  const feeDays = defaults?.short_term_fee_days ?? 7
  const feeRate = defaults?.short_term_fee_rate_pct ?? 1.5
  const cutoffTime = defaults?.otc_cutoff_time ?? '15:00'
  const eodSyncTime = defaults?.eod_sync_time ?? '16:15'
  const otcSyncTimes = defaults?.otc_nav_sync_times ?? ['21:30', '09:00']

  // Filtered visibility per section
  const visibleRegimeStrategies = useMemo(() => {
    const list = metadata?.strategies_by_role.regime ?? []
    return list.filter((s) => {
      const copy = STRATEGY_GUIDE_COPY[s.strategy_id]
      const paramText = s.params.map((p) => `${p.key} ${p.label} ${String(p.default)}`).join(' ')
      return matchesQuery(
        normalizedQuery,
        s.strategy_id,
        s.name,
        s.description,
        s.role,
        s.role_label,
        paramText,
        copy?.intuition,
        copy?.formula,
        copy?.bestMarket,
        copy?.pitfalls,
        copy?.keywords.join(' '),
      )
    })
  }, [metadata, normalizedQuery])

  const visibleEntryStrategies = useMemo(() => {
    const list = metadata?.strategies_by_role.entry ?? []
    return list.filter((s) => {
      const copy = STRATEGY_GUIDE_COPY[s.strategy_id]
      const paramText = s.params.map((p) => `${p.key} ${p.label} ${String(p.default)}`).join(' ')
      return matchesQuery(
        normalizedQuery,
        s.strategy_id,
        s.name,
        s.description,
        s.role,
        s.role_label,
        paramText,
        copy?.intuition,
        copy?.formula,
        copy?.bestMarket,
        copy?.pitfalls,
        copy?.keywords.join(' '),
      )
    })
  }, [metadata, normalizedQuery])

  const visibleTemplates = useMemo(() => {
    const list = metadata?.scenario_templates ?? []
    return list.filter((tpl) => {
      const copy = TEMPLATE_GUIDE_COPY[tpl.id]
      return matchesQuery(
        normalizedQuery,
        tpl.id,
        tpl.name,
        tpl.description,
        tpl.enabled_regime_names.join(' '),
        tpl.enabled_entry_names.join(' '),
        copy?.useCase,
        copy?.watchlistTip,
        copy?.keywords.join(' '),
      )
    })
  }, [metadata, normalizedQuery])

  const showCh1Workflow = matchesQuery(
    normalizedQuery,
    '3分钟上手 工作流 选模板 扫信号 T+1 回看 复制代码 15:00 申购 持仓复盘',
  )
  const showProxyMapping = matchesQuery(
    normalizedQuery,
    '场内代理 场外映射 On-Exchange Proxy Fund OTC Counterpart ETF LOF 联接基金 FEEDER LOF_SELF LOF_SIBLING 成交量 量价 前复权',
  )
  const showIndexGroup = matchesQuery(
    normalizedQuery,
    '同指数 智能聚合 去重 Index Opportunity Group 沪深300 龙头 20日均额 流动性',
  )
  const showShareClass = matchesQuery(
    normalizedQuery,
    'A类 C类 份额 申购费 销售服务费 赎回费 短线 波段 长线 定投',
  )
  const showChapter1 = showCh1Workflow || showProxyMapping || showIndexGroup || showShareClass

  const showTwoStageFunnel = matchesQuery(
    normalizedQuery,
    '两阶段漏斗 Actionable Opportunity 今日可买信号 Mainline Watchlist 主线观察 等回踩 Trend Regime Entry Trigger 趋势滤网 买点触发 ADR-0001 低波 霸屏',
  )
  const showChapter2 =
    showTwoStageFunnel ||
    visibleTemplates.length > 0 ||
    visibleRegimeStrategies.length > 0 ||
    visibleEntryStrategies.length > 0

  const showTrendStage = matchesQuery(
    normalizedQuery,
    'Trend Stage 趋势阶段 Oversold 超跌低位 Early 启动初期 Mid 趋势中段 Overheated 高位偏热 波动率归一化 防追高 评分',
  )
  const showBias20 = matchesQuery(
    normalizedQuery,
    'MA20 乖离率 bias20 MA60 bias60 均线偏离 追高 超跌',
  )
  const showBias20Z = matchesQuery(
    normalizedQuery,
    'bias20_z 波动率归一化 标准化乖离 volatility_20d 银行 低波 芯片 半导体 高波 公平对比',
  )
  const showBollPctB = matchesQuery(
    normalizedQuery,
    '布林带 %B boll_pct_b 上轨 下轨 中轨 通道位置 0.85 0.15',
  )
  const showRsi14 = matchesQuery(
    normalizedQuery,
    'RSI14 相对强弱指标 Wilder 超卖 38 30 过热 65 动能',
  )
  const showRs = matchesQuery(
    normalizedQuery,
    'RS 相对大盘强弱 rs_ratio rs_excess_20d 沪深300 sh000300 超额收益 资金抱团',
  )
  const showFreshSignal = matchesQuery(
    normalizedQuery,
    'Fresh Signal 新信号 冷却期 10日 freshness_cooldown_days hits_20d 近20日触发 噪音 加分',
  )
  const showAntiChase = matchesQuery(
    normalizedQuery,
    '过滤高位偏热 防追高 开关 综合评分 composite_score hide_overheated',
  )
  const showChapter3 =
    showTrendStage ||
    showBias20 ||
    showBias20Z ||
    showBollPctB ||
    showRsi14 ||
    showRs ||
    showFreshSignal ||
    showAntiChase

  const showTPlus1 = matchesQuery(
    normalizedQuery,
    'Signal Date T Execution Date T+1 净值确认 回看 无未来函数 ADR-0002 5 10 20 40 交易日 胜率 回撤',
  )
  const showCutoff1500 = matchesQuery(
    normalizedQuery,
    '15:00 截单 收盘 周末 节假日 非交易日 顺延 下一个交易日 申购',
  )
  const showPendingNav = matchesQuery(
    normalizedQuery,
    'Pending NAV 待确认净值 自动回填 21:30 09:00 16:15 双节奏同步 QDII 手动覆盖 ADR-0004',
  )
  const showShortTermFee = matchesQuery(
    normalizedQuery,
    '7 自然日 FIFO 先进先出 短赎费 1.5% 惩罚性赎回费 移动加权平均成本 分批买入 ADR-0003',
  )
  const showDualCurve = matchesQuery(
    normalizedQuery,
    '复权双曲线 分红复权 equityReturn 前复权 qfq 0% 基准 成本线 买卖打点 清仓 复盘 卖飞 躲避回撤',
  )
  const showExitSignals = matchesQuery(
    normalizedQuery,
    'Exit Signal 卖出信号 风控信号 ma20_break MA20跌破 trailing_drawdown 高位回撤 -6% cost_stop_loss 成本止损 -8% overheated_stage 止盈 short_term_fee_guard 7日保护',
  )
  const showChapter4 =
    showTPlus1 ||
    showCutoff1500 ||
    showPendingNav ||
    showShortTermFee ||
    showDualCurve ||
    showExitSignals

  const hasAnyMatch = showChapter1 || showChapter2 || showChapter3 || showChapter4

  const renderStrategyCard = (strat: GuideStrategyMeta) => {
    const copy = STRATEGY_GUIDE_COPY[strat.strategy_id]
    const anchorId = `term-strategy-${strat.strategy_id}`
    const isRegime = strat.role === 'regime'
    const catMeta = CATEGORY_BADGE[strat.category]

    return (
      <article
        key={strat.strategy_id}
        id={anchorId}
        className={`scroll-mt-24 rounded-xl border p-4 transition-all duration-300 ${cardHighlightClass(anchorId)}`}
      >
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="text-sm font-bold text-slate-100">{strat.name}</h4>
            <code className="rounded bg-slate-950 px-1.5 py-0.5 font-mono text-[11px] text-slate-400">
              {strat.strategy_id}
            </code>
            <span
              className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold ${
                isRegime
                  ? 'border-purple-500/40 bg-purple-500/15 text-purple-300'
                  : 'border-amber-500/40 bg-amber-500/15 text-amber-300'
              }`}
            >
              {isRegime ? 'Trend Regime · 趋势滤网' : 'Entry Trigger · 买点触发'}
            </span>
            <span className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${catMeta.cls}`}>
              {catMeta.label}
            </span>
          </div>
        </div>

        <p className="mt-2 text-xs leading-relaxed text-slate-300">{strat.description}</p>

        {/* Live default parameters table */}
        {strat.params.length > 0 && (
          <div className="mt-3 overflow-hidden rounded-lg border border-slate-800 bg-slate-950/70">
            <div className="flex items-center justify-between border-b border-slate-800/80 px-3 py-1.5 text-[11px]">
              <span className="font-medium text-slate-300">引擎实时默认参数</span>
              <span className="font-mono text-[10px] text-emerald-400">
                synced via /api/v1/guide/metadata
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-[11px]">
                <thead className="border-b border-slate-800/70 text-[10px] text-slate-500">
                  <tr>
                    <th className="px-3 py-1.5 font-medium">参数名</th>
                    <th className="px-3 py-1.5 font-medium">含义</th>
                    <th className="px-3 py-1.5 font-medium">当前默认值</th>
                    <th className="px-3 py-1.5 font-medium">可调范围</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/50">
                  {strat.params.map((p) => (
                    <tr key={p.key} className="text-slate-300">
                      <td className="px-3 py-1.5 font-mono text-amber-300">{p.key}</td>
                      <td className="px-3 py-1.5">{p.label}</td>
                      <td className="px-3 py-1.5 font-mono font-semibold text-slate-100">
                        {formatParamValue(p.default)}
                      </td>
                      <td className="px-3 py-1.5 font-mono text-slate-400">
                        {p.minimum !== null &&
                        p.minimum !== undefined &&
                        p.maximum !== null &&
                        p.maximum !== undefined
                          ? `${p.minimum} ~ ${p.maximum}`
                          : p.type === 'boolean'
                            ? 'true / false'
                            : '自由配置'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Plain-Chinese handbook breakdown */}
        {copy && (
          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
            <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-2.5">
              <div className="text-[11px] font-semibold text-sky-300">通俗原理</div>
              <p className="mt-1 text-xs leading-relaxed text-slate-300">{copy.intuition}</p>
            </div>
            <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-2.5">
              <div className="text-[11px] font-semibold text-amber-300">核心条件 / 公式</div>
              <p className="mt-1 font-mono text-[11px] leading-relaxed text-slate-300">
                {copy.formula}
              </p>
            </div>
            <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-2.5">
              <div className="text-[11px] font-semibold text-emerald-300">适用行情</div>
              <p className="mt-1 text-xs leading-relaxed text-slate-300">{copy.bestMarket}</p>
            </div>
            <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-2.5">
              <div className="text-[11px] font-semibold text-rose-300">常见误区与避坑</div>
              <p className="mt-1 text-xs leading-relaxed text-slate-300">{copy.pitfalls}</p>
            </div>
          </div>
        )}
      </article>
    )
  }

  const renderTemplateCard = (tpl: GuideScenarioTemplate) => {
    const copy = TEMPLATE_GUIDE_COPY[tpl.id]
    return (
      <div
        key={tpl.id}
        className="rounded-xl border border-slate-800/90 bg-slate-950/60 p-4 space-y-2.5"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="text-sm font-bold text-slate-100">{tpl.name}</span>
            {tpl.badge && (
              <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">
                {tpl.badge}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5 font-mono text-[10px] text-slate-400">
            <span className="rounded border border-purple-500/30 bg-purple-500/10 px-1.5 py-0.5 text-purple-300">
              主线逻辑: {tpl.enabled_regimes.length > 0 ? tpl.regime_combine_mode : '关闭'}
            </span>
            <span className="rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-amber-300">
              买点逻辑: {tpl.combine_mode}
            </span>
          </div>
        </div>

        <p className="text-xs leading-relaxed text-slate-300">{tpl.description}</p>

        <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
          <span className="text-slate-500">主线滤网：</span>
          {tpl.enabled_regimes.length === 0 ? (
            <span className="rounded border border-slate-700 bg-slate-900 px-2 py-0.5 text-slate-400">
              不限定趋势（纯左侧扫描）
            </span>
          ) : (
            tpl.enabled_regimes.map((sid, idx) => (
              <button
                key={sid}
                type="button"
                onClick={() => jumpToAnchor(`term-strategy-${sid}`)}
                className="rounded border border-purple-500/35 bg-purple-500/10 px-2 py-0.5 text-purple-200 transition hover:bg-purple-500/20"
              >
                {tpl.enabled_regime_names[idx] ?? sid}
              </button>
            ))
          )}
        </div>

        <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
          <span className="text-slate-500">买点触发：</span>
          {tpl.enabled_entries.map((sid, idx) => (
            <button
              key={sid}
              type="button"
              onClick={() => jumpToAnchor(`term-strategy-${sid}`)}
              className="rounded border border-amber-500/35 bg-amber-500/10 px-2 py-0.5 text-amber-200 transition hover:bg-amber-500/20"
            >
              {tpl.enabled_entry_names[idx] ?? sid}
            </button>
          ))}
        </div>

        {copy && (
          <div className="grid grid-cols-1 gap-2 pt-1 text-xs sm:grid-cols-2">
            <div className="rounded-lg border border-slate-800/70 bg-slate-900/60 p-2.5 text-slate-300">
              <span className="font-semibold text-amber-300">何时使用：</span>
              {copy.useCase}
            </div>
            <div className="rounded-lg border border-slate-800/70 bg-slate-900/60 p-2.5 text-slate-300">
              <span className="font-semibold text-purple-300">搭配主线观察池：</span>
              {copy.watchlistTip}
            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-5">
      {/* Hero Banner & Instant Search */}
      <div className="rounded-2xl border border-slate-800 bg-gradient-to-br from-slate-900 via-slate-900/95 to-slate-950 p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-2xl">
            <div className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/35 bg-amber-500/10 px-2.5 py-0.5 text-[11px] font-medium text-amber-300">
              <BookOpen className="h-3.5 w-3.5" />
              ChanceDetect 交互式产品说明与策略百科
            </div>
            <h2 className="mt-2.5 text-xl font-bold tracking-tight text-slate-100">
              把量化信号、趋势阶段与场外交易规则讲透
            </h2>
            <p className="mt-1.5 text-xs leading-relaxed text-slate-400">
              本手册涵盖全部 10 大策略、3 大场景模板、核心防追高指标与场外申购/短赎费/持仓复盘规则。所有策略默认参数与风控阈值均实时读取自后端策略引擎，永不与代码脱节。
            </p>
          </div>
          {metadata && (
            <div className="flex flex-wrap gap-2 font-mono text-[11px]">
              <div className="rounded-lg border border-purple-500/30 bg-purple-500/10 px-3 py-1.5 text-purple-200">
                趋势滤网 <strong>{metadata.strategies_by_role.regime.length}</strong> 个
              </div>
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-amber-200">
                买点触发 <strong>{metadata.strategies_by_role.entry.length}</strong> 个
              </div>
              <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-emerald-200">
                场景模板 <strong>{metadata.scenario_templates.length}</strong> 套
              </div>
            </div>
          )}
        </div>

        {/* Search Input */}
        <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="实时搜索术语、策略名、参数或规则（如：bias20_z、回踩、T+1、7天、MACD、RS、Exit Signal）..."
              className="w-full rounded-xl border border-slate-700/90 bg-slate-950/90 py-2.5 pl-9 pr-9 text-xs text-slate-100 placeholder:text-slate-500 focus:border-amber-500/70 focus:outline-none focus:ring-1 focus:ring-amber-500/40"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                aria-label="清除搜索"
                className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>

        {/* Quick Jump Pills */}
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-slate-500">高频直达：</span>
          {QUICK_JUMP_PILLS.map((pill) => (
            <button
              key={pill.anchorId}
              type="button"
              onClick={() => jumpToAnchor(pill.anchorId)}
              className="rounded-full border border-slate-800 bg-slate-950/70 px-2.5 py-1 text-[11px] text-slate-300 transition hover:border-amber-500/40 hover:bg-amber-500/10 hover:text-amber-200"
            >
              {pill.label}
            </button>
          ))}
        </div>
      </div>

      {/* Mobile Pill TOC */}
      <div className="flex gap-2 overflow-x-auto pb-1 lg:hidden">
        {TOC_CHAPTERS.map((ch) => (
          <button
            key={ch.id}
            type="button"
            onClick={() => jumpToAnchor(ch.id)}
            className="shrink-0 rounded-lg border border-slate-800 bg-slate-900/80 px-3 py-1.5 text-xs font-medium text-slate-300 hover:border-amber-500/40 hover:text-amber-300"
          >
            {ch.chapter}. {ch.shortTitle}
          </button>
        ))}
      </div>

      {/* Loading / Error State */}
      {loading && (
        <div className="flex h-40 items-center justify-center rounded-xl border border-slate-800 bg-slate-900/50 text-xs text-slate-400">
          正在从策略引擎读取实时参数与规则元数据...
        </div>
      )}

      {error && !loading && (
        <div className="flex items-center justify-between rounded-xl border border-rose-500/40 bg-rose-950/30 p-4 text-xs text-rose-300">
          <span>加载实时策略元数据失败：{error}</span>
          <button
            type="button"
            onClick={loadMetadata}
            className="inline-flex items-center gap-1 rounded-lg border border-rose-500/40 bg-rose-500/15 px-3 py-1 text-rose-200 hover:bg-rose-500/25"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            重试
          </button>
        </div>
      )}

      {/* Main Two-Column Guide Layout */}
      {!loading && (
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
          {/* Desktop Sticky Sidebar TOC */}
          <aside className="hidden lg:sticky lg:top-20 lg:block lg:w-[270px] lg:shrink-0">
            <nav className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 space-y-4">
              <div className="text-xs font-semibold text-slate-200">章节目录</div>
              {TOC_CHAPTERS.map((ch) => (
                <div key={ch.id} className="space-y-1">
                  <button
                    type="button"
                    onClick={() => jumpToAnchor(ch.id)}
                    className="block w-full text-left text-xs font-semibold text-amber-300/90 transition hover:text-amber-200"
                  >
                    {ch.title}
                  </button>
                  <ul className="space-y-0.5 pl-2.5 border-l border-slate-800">
                    {ch.items.map((item) => (
                      <li key={item.id}>
                        <button
                          type="button"
                          onClick={() => jumpToAnchor(item.id)}
                          className={`block w-full rounded px-2 py-1 text-left text-[11px] transition ${
                            highlightedAnchor === item.id
                              ? 'bg-amber-500/15 font-medium text-amber-200'
                              : 'text-slate-400 hover:bg-slate-800/70 hover:text-slate-200'
                          }`}
                        >
                          {item.label}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </nav>
          </aside>

          {/* Guide Content */}
          <div className="min-w-0 flex-1 space-y-8">
            {!hasAnyMatch && (
              <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-10 text-center">
                <p className="text-sm font-medium text-slate-200">
                  没有找到包含「{searchQuery}」的说明条目
                </p>
                <p className="mt-1 text-xs text-slate-400">
                  试试搜索策略关键词（回踩、MACD、RSI、RS）、指标（bias20_z、%B）或场外规则（T+1、7日、Pending NAV）。
                </p>
                <button
                  type="button"
                  onClick={() => setSearchQuery('')}
                  className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/15 px-3 py-1.5 text-xs font-medium text-amber-200 hover:bg-amber-500/25"
                >
                  清除搜索关键词
                </button>
              </div>
            )}

            {/* ==================== CHAPTER 1 ==================== */}
            {showChapter1 && (
              <section id="chapter-quickstart" className="scroll-mt-24 space-y-4">
                <div className="flex items-center gap-2 border-b border-slate-800 pb-2.5">
                  <Layers className="h-4 w-4 text-amber-400" />
                  <h3 className="text-base font-bold text-slate-100">
                    第 1 章 · 3 分钟上手与场内外代理映射
                  </h3>
                </div>

                {showCh1Workflow && (
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                    <div className="rounded-xl border border-slate-800 bg-slate-900/75 p-4">
                      <div className="font-mono text-[11px] font-bold text-amber-400">STEP 01</div>
                      <div className="mt-1 text-sm font-semibold text-slate-100">
                        盘后一键扫描机会
                      </div>
                      <p className="mt-1.5 text-xs leading-relaxed text-slate-400">
                        每个交易日 {eodSyncTime} 后同步最新日 K，选一套快速场景模板（默认「右侧主线买点」），立即筛出「今日可买信号」与「主线观察 · 等回踩」。
                      </p>
                    </div>
                    <div className="rounded-xl border border-slate-800 bg-slate-900/75 p-4">
                      <div className="font-mono text-[11px] font-bold text-sky-400">STEP 02</div>
                      <div className="mt-1 text-sm font-semibold text-slate-100">
                        看阶段 + T+1 历史回看
                      </div>
                      <p className="mt-1.5 text-xs leading-relaxed text-slate-400">
                        优先关注「启动初期 / 趋势中段」且带「新信号」的标的，点开「K线与回看」核对 T+1 持有{' '}
                        {replayHorizons.join(' / ')} 交易日的历史胜率与回撤。
                      </p>
                    </div>
                    <div className="rounded-xl border border-slate-800 bg-slate-900/75 p-4">
                      <div className="font-mono text-[11px] font-bold text-emerald-400">
                        STEP 03
                      </div>
                      <div className="mt-1 text-sm font-semibold text-slate-100">
                        次日 {cutoffTime} 前申购场外
                      </div>
                      <p className="mt-1.5 text-xs leading-relaxed text-slate-400">
                        一键复制卡片中已排好序的 6 位场外基金代码，在次一交易日（T+1）{cutoffTime}{' '}
                        前通过任意基金平台申购，并结合 7 日短赎费与卖出信号持续跟踪复盘。
                      </p>
                    </div>
                  </div>
                )}

                {showProxyMapping && (
                  <article
                    id="term-proxy-mapping"
                    className={`scroll-mt-24 rounded-xl border p-4 space-y-3 transition-all duration-300 ${cardHighlightClass('term-proxy-mapping')}`}
                  >
                    <h4 className="text-sm font-bold text-slate-100">
                      为什么用高流动性场内代理基金（On-Exchange Proxy Fund）筛查场外基金（OTC Counterpart）？
                    </h4>
                    <p className="text-xs leading-relaxed text-slate-300">
                      场外公募基金每天盘后只公布一个单位净值，既没有开盘/最高/最低价，也没有成交量与成交额，更无法计算量比、阳线企稳、布林下轨盘中触及等量价信号。ChanceDetect
                      采用<strong className="text-amber-300">「场内分析 → 场外执行」</strong>
                      的代理映射架构：先用高流动性、带完整前复权 OHLCV 数据的场内 ETF / LOF（
                      <strong className="text-slate-100">On-Exchange Proxy Fund</strong>
                      ）跑全市场量化策略，再通过严格校验的映射关系，把命中机会直接翻译为可申购的场外基金（
                      <strong className="text-slate-100">OTC Counterpart</strong>）。
                    </p>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-3 text-xs">
                      <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-2.5">
                        <span className="font-mono text-[11px] font-semibold text-amber-300">
                          FEEDER · ETF 联接基金
                        </span>
                        <p className="mt-1 text-slate-400">
                          90% 以上仓位直接投资对应场内 ETF，走势高度贴合，最常见、选择最丰富。
                        </p>
                      </div>
                      <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-2.5">
                        <span className="font-mono text-[11px] font-semibold text-sky-300">
                          LOF_SELF · 场内外同代码 LOF
                        </span>
                        <p className="mt-1 text-slate-400">
                          同一只上市开放式基金（LOF），既在场内交易又支持在场外按净值申购。
                        </p>
                      </div>
                      <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-2.5">
                        <span className="font-mono text-[11px] font-semibold text-emerald-300">
                          LOF_SIBLING · 同 LOF 兄弟份额
                        </span>
                        <p className="mt-1 text-slate-400">
                          与场内 LOF 同属一只母基金的场外 C 类等兄弟份额，共享同一资产组合。
                        </p>
                      </div>
                    </div>
                  </article>
                )}

                {showIndexGroup && (
                  <article
                    id="term-index-group"
                    className={`scroll-mt-24 rounded-xl border p-4 space-y-2 transition-all duration-300 ${cardHighlightClass('term-index-group')}`}
                  >
                    <h4 className="text-sm font-bold text-slate-100">
                      同指数智能聚合去重（Index Opportunity Group）
                    </h4>
                    <p className="text-xs leading-relaxed text-slate-300">
                      热门指数（例如沪深 300、中证半导体、中证红利）在场内往往有 5~15 只不同基金公司的 ETF 同时命中同一策略。开启「按跟踪指数智能聚合去重」后，系统会将跟踪同一标的指数的所有命中基金合并为一张{' '}
                      <strong className="text-amber-300">Index Opportunity Group</strong> 机会卡：
                    </p>
                    <ul className="list-disc space-y-1 pl-5 text-xs text-slate-400">
                      <li>
                        <strong className="text-slate-200">主代表选取规则</strong>：自动挑选组内 20 日日均成交额最高（流动性最好、价格失真最小）的场内 ETF 作为主代表展示 K 线与指标。
                      </li>
                      <li>
                        <strong className="text-slate-200">场外份额全量汇总</strong>：将该指数下全部命中基金映射的场外份额去重汇总，并把「开放申购」及你偏好的份额类别排在最前面，避免重复刷屏。
                      </li>
                    </ul>
                  </article>
                )}

                {showShareClass && (
                  <article
                    id="term-share-class"
                    className={`scroll-mt-24 rounded-xl border p-4 space-y-2.5 transition-all duration-300 ${cardHighlightClass('term-share-class')}`}
                  >
                    <h4 className="text-sm font-bold text-slate-100">A 类 vs C 类份额怎么选？</h4>
                    <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 text-xs">
                      <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/[0.06] p-3">
                        <div className="font-semibold text-emerald-300">
                          C 类份额（波段 / 短中期首选）
                        </div>
                        <p className="mt-1 leading-relaxed text-slate-300">
                          <strong className="text-slate-100">0 申购费</strong>，按日计提极低销售服务费（通常年化 0.1%~0.4%），持有满 7 天或 30 天后往往 0 赎回费。非常适合几周到数月的右侧波段或超跌反弹交易。
                        </p>
                      </div>
                      <div className="rounded-lg border border-sky-500/30 bg-sky-500/[0.06] p-3">
                        <div className="font-semibold text-sky-300">
                          A 类份额（1 年以上长线定投首选）
                        </div>
                        <p className="mt-1 leading-relaxed text-slate-300">
                          买入时收一次性前端申购费（平台通常 1 折约 0.1%~0.15%），<strong className="text-slate-100">不收按日销售服务费</strong>。如果计划长期持有超过 1 年，A 类综合持有成本更低。
                        </p>
                      </div>
                    </div>
                  </article>
                )}
              </section>
            )}

            {/* ==================== CHAPTER 2 ==================== */}
            {showChapter2 && (
              <section id="chapter-strategies" className="scroll-mt-24 space-y-5">
                <div className="flex items-center gap-2 border-b border-slate-800 pb-2.5">
                  <Crosshair className="h-4 w-4 text-amber-400" />
                  <h3 className="text-base font-bold text-slate-100">
                    第 2 章 · 10 大策略百科与 3 大场景模板
                  </h3>
                </div>

                {/* Two-stage funnel */}
                {showTwoStageFunnel && (
                  <article
                    id="term-two-stage-funnel"
                    className={`scroll-mt-24 rounded-xl border p-4 space-y-3.5 transition-all duration-300 ${cardHighlightClass('term-two-stage-funnel')}`}
                  >
                    <h4 className="text-sm font-bold text-slate-100">
                      两阶段漏斗：为什么拆分 Trend Regime 与 Entry Trigger？（ADR-0001）
                    </h4>
                    <p className="text-xs leading-relaxed text-slate-300">
                      如果把所有策略混在一起做“任一命中即推荐”，连续缓慢上涨 5 个月的低波品种（如银行 ETF 长期贴着布林上轨）会每天靠动量/RS 排在买点榜第一，诱导高位追涨。因此 ChanceDetect 把 10 个策略严格拆分为两个阶段：
                    </p>

                    <div className="grid grid-cols-1 gap-2.5 md:grid-cols-3">
                      <div className="rounded-lg border border-purple-500/35 bg-purple-950/25 p-3">
                        <div className="flex items-center gap-1.5 text-xs font-semibold text-purple-300">
                          <Compass className="h-3.5 w-3.5" />
                          阶段 1：Trend Regime（趋势滤网）
                        </div>
                        <p className="mt-1 text-[11px] leading-relaxed text-slate-300">
                          负责<strong className="text-purple-200">「选强势赛道」</strong>。判断标的当前是否处于健康上升趋势或资金领涨主线；清空全部趋势滤网时即进入纯左侧抄底模式。
                        </p>
                      </div>

                      <div className="rounded-lg border border-amber-500/35 bg-amber-950/25 p-3">
                        <div className="flex items-center gap-1.5 text-xs font-semibold text-amber-300">
                          <Crosshair className="h-3.5 w-3.5" />
                          阶段 2：Entry Trigger（买点触发）
                        </div>
                        <p className="mt-1 text-[11px] leading-relaxed text-slate-300">
                          负责<strong className="text-amber-200">「选入场时机」</strong>。在强势池内捕捉回踩企稳、首日多头启动、放量突破或 MACD 金叉等具体离散事件。
                        </p>
                      </div>

                      <div className="rounded-lg border border-emerald-500/35 bg-emerald-950/25 p-3">
                        <div className="flex items-center gap-1.5 text-xs font-semibold text-emerald-300">
                          <ArrowRight className="h-3.5 w-3.5" />
                          双桶分流输出
                        </div>
                        <p className="mt-1 text-[11px] leading-relaxed text-slate-300">
                          同时过两关 →{' '}
                          <strong className="text-amber-300">今日可买信号 (Actionable)</strong>；
                          仅过趋势滤网未出买点 →{' '}
                          <strong className="text-purple-300">主线观察 · 等回踩 (Watchlist)</strong>。
                        </p>
                      </div>
                    </div>
                  </article>
                )}

                {/* 3 Built-in Scenario Templates */}
                {visibleTemplates.length > 0 && (
                  <div
                    id="term-scenario-templates"
                    className={`scroll-mt-24 rounded-xl border p-4 space-y-3 transition-all duration-300 ${cardHighlightClass('term-scenario-templates')}`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h4 className="flex items-center gap-1.5 text-sm font-bold text-slate-100">
                        <Sparkles className="h-4 w-4 text-amber-400" />
                        3 大内置场景模板（点击策略标签直达详解）
                      </h4>
                      <span className="font-mono text-[10px] text-emerald-400">
                        与工作台左侧模板实时同步
                      </span>
                    </div>
                    <div className="space-y-3">
                      {visibleTemplates.map((tpl) => renderTemplateCard(tpl))}
                    </div>
                  </div>
                )}

                {/* Role 1: Trend Regime */}
                {visibleRegimeStrategies.length > 0 && (
                  <div
                    id="term-trend-regime"
                    className={`scroll-mt-24 rounded-xl border p-4 space-y-3 transition-all duration-300 ${
                      highlightedAnchor === 'term-trend-regime'
                        ? 'ring-2 ring-purple-400/90 border-purple-400/70 bg-purple-500/[0.06]'
                        : 'border-purple-500/25 bg-slate-900/50'
                    }`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <Compass className="h-4 w-4 text-purple-400" />
                        <h4 className="text-sm font-bold text-purple-200">
                          第一类：Trend Regime · 趋势滤网（共 {visibleRegimeStrategies.length} 个）
                        </h4>
                      </div>
                      <span className="text-[11px] text-slate-400">
                        连续状态过滤器：锁定强势主线，不单独触发买入
                      </span>
                    </div>
                    <div className="space-y-3">
                      {visibleRegimeStrategies.map((s) => renderStrategyCard(s))}
                    </div>
                  </div>
                )}

                {/* Role 2: Entry Trigger */}
                {visibleEntryStrategies.length > 0 && (
                  <div
                    id="term-entry-trigger"
                    className={`scroll-mt-24 rounded-xl border p-4 space-y-3 transition-all duration-300 ${
                      highlightedAnchor === 'term-entry-trigger'
                        ? 'ring-2 ring-amber-400/90 border-amber-400/70 bg-amber-500/[0.06]'
                        : 'border-amber-500/25 bg-slate-900/50'
                    }`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <Crosshair className="h-4 w-4 text-amber-400" />
                        <h4 className="text-sm font-bold text-amber-200">
                          第二类：Entry Trigger · 买点触发器（共 {visibleEntryStrategies.length} 个）
                        </h4>
                      </div>
                      <span className="text-[11px] text-slate-400">
                        离散时机事件：右侧启动/回踩 4 个 + 左侧超跌反弹 3 个
                      </span>
                    </div>
                    <div className="space-y-3">
                      {visibleEntryStrategies.map((s) => renderStrategyCard(s))}
                    </div>
                  </div>
                )}
              </section>
            )}

            {/* ==================== CHAPTER 3 ==================== */}
            {showChapter3 && (
              <section id="chapter-indicators" className="scroll-mt-24 space-y-4">
                <div className="flex items-center gap-2 border-b border-slate-800 pb-2.5">
                  <Gauge className="h-4 w-4 text-sky-400" />
                  <h3 className="text-base font-bold text-slate-100">
                    第 3 章 · Trend Stage 与核心量化指标说明书
                  </h3>
                </div>

                {/* Trend Stage 4 tiers */}
                {showTrendStage && (
                  <article
                    id="term-trend-stage"
                    className={`scroll-mt-24 rounded-xl border p-4 space-y-3 transition-all duration-300 ${cardHighlightClass('term-trend-stage')}`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h4 className="text-sm font-bold text-slate-100">
                        Trend Stage 趋势阶段四档分级（波动率归一化防追高）
                      </h4>
                      <span className="font-mono text-[10px] text-slate-400">
                        判定优先级：高位偏热 → 超跌低位 → 启动初期 → 趋势中段
                      </span>
                    </div>
                    <p className="text-xs leading-relaxed text-slate-300">
                      同样是触发买点，底部刚启动与高位连涨 10 根阳线的赔率截然不同。系统在每个交易日按以下实时阈值对每只基金自动打标，并在综合得分中对「启动初期」加分（+5）、对「高位偏热」降权（-15）：
                    </p>

                    <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                      <div className="rounded-lg border border-cyan-500/35 bg-cyan-500/[0.07] p-3">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-bold text-cyan-300">
                            超跌低位 · Oversold
                          </span>
                          <span className="font-mono text-[10px] text-cyan-200/80">左侧修复区</span>
                        </div>
                        <p className="mt-1.5 font-mono text-[11px] text-slate-300">
                          RSI14 ≤ {stageThresholds?.oversold.rsi14_max ?? 38} 或 %B ≤{' '}
                          {stageThresholds?.oversold.boll_pct_b_max ?? 0.15} 或 bias20_z ≤{' '}
                          {stageThresholds?.oversold.bias20_z_max ?? -2.0}
                        </p>
                        <p className="mt-1 text-[11px] text-slate-400">
                          价格处于显著超卖状态，适合左侧分批低吸或右侧大跌急杀后的反弹关注。
                        </p>
                      </div>

                      <div className="rounded-lg border border-emerald-500/35 bg-emerald-500/[0.07] p-3">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-bold text-emerald-300">
                            启动初期 · Early
                          </span>
                          <span className="font-mono text-[10px] text-emerald-200/80">
                            综合分 +5.0
                          </span>
                        </div>
                        <p className="mt-1.5 font-mono text-[11px] text-slate-300">
                          MA20 ≥ MA60 且距 60 日低点 ≤{' '}
                          {stageThresholds?.early.drawup_60d_low_max ?? 15}% 且 RSI14 &lt;{' '}
                          {stageThresholds?.early.rsi14_max ?? 62} 且 %B &lt;{' '}
                          {stageThresholds?.early.boll_pct_b_max ?? 0.78}
                        </p>
                        <p className="mt-1 text-[11px] text-slate-400">
                          趋势刚刚确立、累计涨幅温和、短线未拥挤，右侧介入的黄金盈亏比区间。
                        </p>
                      </div>

                      <div className="rounded-lg border border-sky-500/35 bg-sky-500/[0.07] p-3">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-bold text-sky-300">趋势中段 · Mid</span>
                          <span className="font-mono text-[10px] text-sky-200/80">健康延续区</span>
                        </div>
                        <p className="mt-1.5 font-mono text-[11px] text-slate-300">
                          未触发偏热、超跌或启动初期时的默认健康区间
                        </p>
                        <p className="mt-1 text-[11px] text-slate-400">
                          处于稳步推进或震荡换手阶段，配合强势回踩买点可顺势参与。
                        </p>
                      </div>

                      <div className="rounded-lg border border-rose-500/40 bg-rose-500/[0.07] p-3">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-bold text-rose-300">
                            高位偏热 · Overheated
                          </span>
                          <span className="font-mono text-[10px] text-rose-200/80">
                            综合分 -15.0
                          </span>
                        </div>
                        <p className="mt-1.5 font-mono text-[11px] text-slate-300">
                          (%B ≥ {stageThresholds?.overheated.boll_pct_b_min ?? 0.85} 或 bias20_z ≥{' '}
                          {stageThresholds?.overheated.bias20_z_min ?? 2.2}) 且 (RSI14 ≥{' '}
                          {stageThresholds?.overheated.rsi14_min ?? 65} 或 60日分位 ≥{' '}
                          {stageThresholds?.overheated.pos_60d_min ?? 85}%)
                        </p>
                        <p className="mt-1 text-[11px] text-slate-400">
                          短期情绪拥挤且远离均线，场外 T+1 追买极易买在短期回踩前夕；建议耐心等回踩。
                        </p>
                      </div>
                    </div>
                  </article>
                )}

                {/* MA20 / MA60 Bias */}
                {showBias20 && (
                  <article
                    id="term-bias20"
                    className={`scroll-mt-24 rounded-xl border p-4 space-y-2 transition-all duration-300 ${cardHighlightClass('term-bias20')}`}
                  >
                    <h4 className="text-sm font-bold text-slate-100">
                      MA20 乖离率 (<code className="font-mono text-amber-300">bias20</code>) &amp;
                      MA60 乖离率 (<code className="font-mono text-amber-300">bias60</code>)
                    </h4>
                    <p className="font-mono text-[11px] text-amber-200/90">
                      bias20 = (收盘价 - MA20) / MA20 × 100% ｜ bias60 = (收盘价 - MA60) / MA60 × 100%
                    </p>
                    <p className="text-xs leading-relaxed text-slate-300">
                      衡量当前价格离开 20 日（月线）或 60 日（季线）成本均线的距离。正乖离过大说明短期获利盘丰厚、随时可能回踩均线；负乖离极深说明短期超跌严重。但不同品种波动率差异极大，因此必须结合下方的{' '}
                      <button
                        type="button"
                        onClick={() => jumpToAnchor('term-bias20-z')}
                        className="font-mono text-amber-300 underline decoration-amber-500/40 underline-offset-2 hover:text-amber-200"
                      >
                        bias20_z
                      </button>{' '}
                      一起看。
                    </p>
                  </article>
                )}

                {/* bias20_z */}
                {showBias20Z && (
                  <article
                    id="term-bias20-z"
                    className={`scroll-mt-24 rounded-xl border p-4 space-y-3 transition-all duration-300 ${cardHighlightClass('term-bias20-z')}`}
                  >
                    <h4 className="text-sm font-bold text-slate-100">
                      波动率归一化乖离 (<code className="font-mono text-amber-300">bias20_z</code>
                      ) —— 为什么它能公平对比银行低波和芯片高波？
                    </h4>
                    <p className="font-mono text-[11px] text-amber-200/90">
                      bias20_z = bias20 / max(volatility_20d, 0.5)
                      <span className="ml-2 text-slate-400">
                        （volatility_20d 为近 20 个交易日涨跌幅标准差 %）
                      </span>
                    </p>
                    <p className="text-xs leading-relaxed text-slate-300">
                      如果一刀切规定“MA20 乖离 &gt; 5% 才算追高”，高波动的芯片 ETF 涨 5% 只是普通两天波动，而低波动的银行 ETF 涨 2.5% 就已经是罕见加速拉升。<code className="font-mono text-amber-300">bias20_z</code> 把乖离率换算成“偏离了几个日波动标准差”，让所有赛道站在同一把尺子上比较：
                    </p>
                    <div className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-950/60">
                      <table className="w-full text-left text-xs">
                        <thead className="border-b border-slate-800 text-[11px] text-slate-400">
                          <tr>
                            <th className="px-3 py-2">品种举例</th>
                            <th className="px-3 py-2 font-mono">bias20</th>
                            <th className="px-3 py-2 font-mono">20日日波动率</th>
                            <th className="px-3 py-2 font-mono">bias20_z</th>
                            <th className="px-3 py-2">实际拥挤度解读</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800/60 text-slate-300">
                          <tr>
                            <td className="px-3 py-2 font-medium text-slate-100">银行 / 红利 ETF（低波）</td>
                            <td className="px-3 py-2 font-mono">+2.5%</td>
                            <td className="px-3 py-2 font-mono">0.8%</td>
                            <td className="px-3 py-2 font-mono font-bold text-rose-300">+3.13</td>
                            <td className="px-3 py-2 text-rose-300">
                              已远超 {stageThresholds?.overheated.bias20_z_min ?? 2.2} 警戒线，短期极度透支
                            </td>
                          </tr>
                          <tr>
                            <td className="px-3 py-2 font-medium text-slate-100">芯片 / 半导体 ETF（高波）</td>
                            <td className="px-3 py-2 font-mono">+4.0%</td>
                            <td className="px-3 py-2 font-mono">2.8%</td>
                            <td className="px-3 py-2 font-mono font-bold text-emerald-300">+1.43</td>
                            <td className="px-3 py-2 text-emerald-300">
                              仍处于健康常态波动带内，并未显著过热
                            </td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </article>
                )}

                {/* Bollinger %B, RSI14, RS */}
                <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                  {showBollPctB && (
                    <article
                      id="term-boll-pct-b"
                      className={`scroll-mt-24 rounded-xl border p-4 space-y-2 transition-all duration-300 ${cardHighlightClass('term-boll-pct-b')}`}
                    >
                      <h4 className="text-sm font-bold text-slate-100">
                        布林带位置 <code className="font-mono text-amber-300">%B</code>
                      </h4>
                      <p className="font-mono text-[11px] text-amber-200/90">
                        %B = (close - 下轨) / (上轨 - 下轨)
                      </p>
                      <ul className="space-y-1 text-xs text-slate-300">
                        <li>
                          <strong className="font-mono text-cyan-300">≤ 0.15</strong>：贴近或跌破下轨，偏超跌低位
                        </li>
                        <li>
                          <strong className="font-mono text-slate-100">0.50</strong>：正好位于 MA20 布林中轨
                        </li>
                        <li>
                          <strong className="font-mono text-rose-300">≥ 0.85</strong>：贴近或突破上轨，短线偏拥挤
                        </li>
                      </ul>
                    </article>
                  )}

                  {showRsi14 && (
                    <article
                      id="term-rsi14"
                      className={`scroll-mt-24 rounded-xl border p-4 space-y-2 transition-all duration-300 ${cardHighlightClass('term-rsi14')}`}
                    >
                      <h4 className="text-sm font-bold text-slate-100">
                        相对强弱指标 <code className="font-mono text-amber-300">RSI14</code>
                      </h4>
                      <p className="font-mono text-[11px] text-amber-200/90">
                        14日 Wilder 平滑涨跌动能（0 ~ 100）
                      </p>
                      <ul className="space-y-1 text-xs text-slate-300">
                        <li>
                          <strong className="font-mono text-cyan-300">≤ 38（深 ≤ 30）</strong>：短线超卖，卖压接近释放
                        </li>
                        <li>
                          <strong className="font-mono text-emerald-300">45 ~ 62</strong>：右侧趋势健康推进带
                        </li>
                        <li>
                          <strong className="font-mono text-rose-300">≥ 65</strong>：短线过热，配合高 %B 易回撤
                        </li>
                      </ul>
                    </article>
                  )}

                  {showRs && (
                    <article
                      id="term-rs"
                      className={`scroll-mt-24 rounded-xl border p-4 space-y-2 transition-all duration-300 ${cardHighlightClass('term-rs')}`}
                    >
                      <h4 className="text-sm font-bold text-slate-100">
                        相对大盘强弱 <code className="font-mono text-purple-300">RS</code>
                      </h4>
                      <p className="font-mono text-[11px] text-purple-200/90">
                        RS = 基金收盘价 / 沪深300收盘价
                      </p>
                      <p className="text-xs leading-relaxed text-slate-300">
                        RS 曲线向上且创 20 日新高、同时近 20 日跑赢基准超额收益 ≥ 3%，说明该行业正在持续吸引增量资金，是主线轮动的核心特征。
                      </p>
                    </article>
                  )}
                </div>

                {/* Fresh Signal & Anti-chase */}
                {(showFreshSignal || showAntiChase) && (
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                    {showFreshSignal && (
                      <article
                        id="term-fresh-signal"
                        className={`scroll-mt-24 rounded-xl border p-4 space-y-2 transition-all duration-300 ${cardHighlightClass('term-fresh-signal')}`}
                      >
                        <h4 className="flex items-center gap-1.5 text-sm font-bold text-slate-100">
                          <Sparkles className="h-4 w-4 text-teal-400" />
                          Fresh Signal（新信号）与 <code className="font-mono text-teal-300">hits_20d</code>
                        </h4>
                        <p className="text-xs leading-relaxed text-slate-300">
                          如果同一只基金在过去 <strong className="font-mono text-teal-300">{cooldownDays}</strong>{' '}
                          个交易日冷却期内从未触发过当前策略组合的可买信号，今天首次触发就会打上{' '}
                          <span className="rounded border border-teal-400/50 bg-teal-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-teal-200">
                            新信号
                          </span>{' '}
                          徽章，并在综合评分中获得 <strong className="font-mono text-teal-300">+8.0</strong>{' '}
                          分加权。卡片同时展示近 <strong className="font-mono">{hitsWindowDays}</strong>{' '}
                          个交易日累计触发次数（<code className="font-mono">hits_20d</code>），帮你一眼区分“首次启动”和“反复震荡杂波”。
                        </p>
                      </article>
                    )}

                    {showAntiChase && (
                      <article
                        id="term-anti-chase"
                        className={`scroll-mt-24 rounded-xl border p-4 space-y-2 transition-all duration-300 ${cardHighlightClass('term-anti-chase')}`}
                      >
                        <h4 className="flex items-center gap-1.5 text-sm font-bold text-slate-100">
                          <Activity className="h-4 w-4 text-rose-400" />
                          防追高开关（过滤高位偏热）与综合得分
                        </h4>
                        <p className="text-xs leading-relaxed text-slate-300">
                          开启侧边栏「过滤高位偏热」开关后，处于 <strong className="text-rose-300">Overheated</strong>{' '}
                          阶段的标的会从「今日可买信号」隐藏，但仍保留在「主线观察 · 等回踩」里供跟踪。可买综合分 = 买点均分 + 共振加分 + 主线加分（每命中 1 条 +5）+ 阶段调整（Early +5 / Overheated -15）+ 新信号（+8）。
                        </p>
                      </article>
                    )}
                  </div>
                )}
              </section>
            )}

            {/* ==================== CHAPTER 4 ==================== */}
            {showChapter4 && (
              <section id="chapter-execution" className="scroll-mt-24 space-y-4">
                <div className="flex items-center gap-2 border-b border-slate-800 pb-2.5">
                  <CalendarClock className="h-4 w-4 text-emerald-400" />
                  <h3 className="text-base font-bold text-slate-100">
                    第 4 章 · 场外实操与持仓复盘规则
                  </h3>
                </div>

                {/* Signal Date (T) vs Execution Date (T+1) */}
                {showTPlus1 && (
                  <article
                    id="term-t-plus-1"
                    className={`scroll-mt-24 rounded-xl border p-4 space-y-3 transition-all duration-300 ${cardHighlightClass('term-t-plus-1')}`}
                  >
                    <h4 className="text-sm font-bold text-slate-100">
                      Signal Date (T) vs Execution Date (T+1) 与无未来函数回看（ADR-0002）
                    </h4>
                    <p className="text-xs leading-relaxed text-slate-300">
                      场外基金无法在 T 日收盘看到信号后再按 T 日净值成交——投资者最早只能在下一个交易日（<strong className="text-amber-300">T+1</strong>）{cutoffTime}{' '}
                      前提交申购，并以 <strong className="text-amber-300">T+1 收盘净值</strong> 确认真实入场成本。如果回测拿 T 日收盘价当成本，会引入一整天“事后诸葛亮”的未来函数偏差。
                    </p>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-4 text-[11px]">
                      <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-2.5">
                        <div className="font-mono font-bold text-slate-200">T 日 15:00 收盘</div>
                        <p className="mt-1 text-slate-400">产生完整日 K 线，收盘价定型</p>
                      </div>
                      <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-2.5">
                        <div className="font-mono font-bold text-amber-300">
                          T 日 {eodSyncTime} 扫描
                        </div>
                        <p className="mt-1 text-slate-400">
                          Signal Date (T) 触发策略信号并推送卡片
                        </p>
                      </div>
                      <div className="rounded-lg border border-amber-500/35 bg-amber-500/[0.08] p-2.5">
                        <div className="font-mono font-bold text-amber-200">
                          T+1 日 {cutoffTime} 前申购
                        </div>
                        <p className="mt-1 text-slate-300">
                          Execution Date (T+1)，按当日收盘净值确认成本
                        </p>
                      </div>
                      <div className="rounded-lg border border-emerald-500/35 bg-emerald-500/[0.08] p-2.5">
                        <div className="font-mono font-bold text-emerald-300">
                          持有 {replayHorizons.join(' / ')} 交易日
                        </div>
                        <p className="mt-1 text-slate-300">
                          自 T+1 起算前瞻收益与回撤（T+1 盘中低点不计入持有回撤）
                        </p>
                      </div>
                    </div>
                  </article>
                )}

                {/* 15:00 Cutoff & Pending NAV */}
                {(showCutoff1500 || showPendingNav) && (
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                    {showCutoff1500 && (
                      <article
                        id="term-cutoff-1500"
                        className={`scroll-mt-24 rounded-xl border p-4 space-y-2 transition-all duration-300 ${cardHighlightClass('term-cutoff-1500')}`}
                      >
                        <h4 className="text-sm font-bold text-slate-100">
                          {cutoffTime} 截单规则与非交易日顺延
                        </h4>
                        <ul className="list-disc space-y-1.5 pl-4 text-xs leading-relaxed text-slate-300">
                          <li>
                            <strong className="text-emerald-300">交易日 {cutoffTime} 前下单</strong>：按<strong className="text-slate-100">当日</strong>晚间公布的收盘净值确认成交。
                          </li>
                          <li>
                            <strong className="text-amber-300">交易日 {cutoffTime} 后下单</strong>：顺延至<strong className="text-slate-100">下一个交易日</strong>收盘净值确认。
                          </li>
                          <li>
                            <strong className="text-slate-200">周末 / 法定节假日下单</strong>：同样顺延至节后第一个交易日，记录持仓日期时请对齐到实际净值确认日。
                          </li>
                        </ul>
                      </article>
                    )}

                    {showPendingNav && (
                      <article
                        id="term-pending-nav"
                        className={`scroll-mt-24 rounded-xl border p-4 space-y-2 transition-all duration-300 ${cardHighlightClass('term-pending-nav')}`}
                      >
                        <h4 className="text-sm font-bold text-slate-100">
                          Pending NAV（待确认净值）与双节奏同步（ADR-0004）
                        </h4>
                        <p className="text-xs leading-relaxed text-slate-300">
                          场外官方净值一般在交易日晚间 19:00~22:30 公布（QDII 基金延至 T+1 晚间）。当天买入记账时无需苦等：交易批次会立即以{' '}
                          <strong className="text-amber-300">Pending NAV</strong> 状态保存，并用最近已知净值与对应场内代理涨幅作临时参考。
                        </p>
                        <p className="text-xs leading-relaxed text-slate-400">
                          系统保持每日 <strong className="font-mono text-slate-200">{eodSyncTime}</strong>{' '}
                          场内全市场日 K 同步不变，并在{' '}
                          <strong className="font-mono text-emerald-300">
                            {otcSyncTimes.join(' / ')}
                          </strong>{' '}
                          仅对已追踪持仓拉取场外官方净值，自动回填待确认批次；若你手动填写了对账单净值，自动同步绝不会覆盖你的手动值。
                        </p>
                      </article>
                    )}
                  </div>
                )}

                {/* 7-day FIFO Short-term Fee */}
                {showShortTermFee && (
                  <article
                    id="term-short-term-fee"
                    className={`scroll-mt-24 rounded-xl border p-4 space-y-2.5 transition-all duration-300 ${cardHighlightClass('term-short-term-fee')}`}
                  >
                    <h4 className="flex items-center gap-1.5 text-sm font-bold text-slate-100">
                      <AlertTriangle className="h-4 w-4 text-amber-400" />
                      {feeDays} 自然日 FIFO 短赎费规则与加权平均成本（ADR-0003）
                    </h4>
                    <p className="text-xs leading-relaxed text-slate-300">
                      中国公募基金法规规定，除货币基金等特定品种外，投资者持有不足{' '}
                      <strong className="font-mono text-rose-300">{feeDays} 个自然日</strong>（含周末节假日，约合 5 个交易日）即赎回，必须收取不低于{' '}
                      <strong className="font-mono text-rose-300">{feeRate}%</strong>{' '}
                      的惩罚性赎回费，且注册登记机构按<strong className="text-amber-300">先进先出（FIFO）</strong>原则逐笔核算：
                    </p>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 text-xs">
                      <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-slate-300">
                        <div className="font-semibold text-sky-300">持仓成本与盈亏 → 移动加权平均</div>
                        <p className="mt-1 text-slate-400">
                          多次加仓后，你的综合盈亏平衡线看的是所有持仓份额的加权平均成本，清仓后该轮交易归档，再次买入同基金则开启全新零起点周期。
                        </p>
                      </div>
                      <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-slate-300">
                        <div className="font-semibold text-amber-300">
                          {feeDays} 日免罚判定 → 逐笔 FIFO 独立计算
                        </div>
                        <p className="mt-1 text-slate-400">
                          例：周一买 1000 份、周五加仓 1000 份；到下周二时，第一批已满 {feeDays} 天可免 {feeRate}% 短赎费，第二批仅 4 天仍处惩罚区——若此时赎回 1000 份，按 FIFO 优先消耗周一免罚份额。
                        </p>
                      </div>
                    </div>
                  </article>
                )}

                {/* Dual Curve Review */}
                {showDualCurve && (
                  <article
                    id="term-dual-curve"
                    className={`scroll-mt-24 rounded-xl border p-4 space-y-2 transition-all duration-300 ${cardHighlightClass('term-dual-curve')}`}
                  >
                    <h4 className="text-sm font-bold text-slate-100">
                      复权双曲线对比与清仓后复盘视角
                    </h4>
                    <p className="text-xs leading-relaxed text-slate-300">
                      在持仓复盘中，系统将建仓日（<code className="font-mono text-amber-300">opened_date</code>）的起点统一归零为{' '}
                      <strong className="font-mono text-slate-100">0.0%</strong>，把两条曲线放在同一坐标系对比：
                    </p>
                    <ul className="list-disc space-y-1 pl-5 text-xs text-slate-400">
                      <li>
                        <strong className="text-slate-200">场外复权收益曲线</strong>：基于官方含分红日涨跌幅累乘计算，彻底消除基金分红除权造成的“假性暴跌缺口”，与场内前复权（<code className="font-mono">qfq</code>）代理曲线完全可比。
                      </li>
                      <li>
                        <strong className="text-slate-200">交易打点与成本线</strong>：图上直观标注建仓、加仓、减仓、清仓点位及加权盈亏平衡线，同时支持一键切回场内代理 K 线 + MA5/MA10/MA20/MA60 观察技术支撑。
                      </li>
                      <li>
                        <strong className="text-slate-200">清仓后延续复盘</strong>：已清仓交易会自动向后延续绘制 20~40 个交易日走势（支持切换“仅看持仓期”），直观复盘自己是“卖飞了”还是“成功躲过回撤”。
                      </li>
                    </ul>
                  </article>
                )}

                {/* 5 Exit Signals */}
                {showExitSignals && (
                  <article
                    id="term-exit-signals"
                    className={`scroll-mt-24 rounded-xl border p-4 space-y-3 transition-all duration-300 ${cardHighlightClass('term-exit-signals')}`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h4 className="flex items-center gap-1.5 text-sm font-bold text-slate-100">
                        <ShieldAlert className="h-4 w-4 text-rose-400" />
                        5 类可解释 Exit Signal（卖出与风控信号）
                      </h4>
                      <span className="text-[11px] text-slate-400">
                        透明决策辅助提醒，绝不自动下单
                      </span>
                    </div>
                    <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3 text-xs">
                      <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-amber-300">1. MA20 趋势跌破</span>
                          <code className="font-mono text-[10px] text-slate-500">ma20_break</code>
                        </div>
                        <p className="mt-1.5 leading-relaxed text-slate-400">
                          场内代理基金收盘跌破 MA20 满{' '}
                          <strong className="font-mono text-slate-200">
                            {exitThresholds?.ma20_watch_days ?? 1} 日
                          </strong>
                          提示预警，连续{' '}
                          <strong className="font-mono text-rose-300">
                            {exitThresholds?.ma20_alert_days ?? 2} 日
                          </strong>
                          无法收回升级为破位提醒。
                        </p>
                      </div>

                      <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-rose-300">2. 高位动态回撤</span>
                          <code className="font-mono text-[10px] text-slate-500">
                            trailing_drawdown
                          </code>
                        </div>
                        <p className="mt-1.5 leading-relaxed text-slate-400">
                          相对持仓期间最高累计收益回撤达到默认{' '}
                          <strong className="font-mono text-rose-300">
                            {exitThresholds?.trailing_drawdown_pct ?? -6.0}%
                          </strong>
                          （支持单笔持仓自定义阈值），守住浮盈防止利润大幅回吐。
                        </p>
                      </div>

                      <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-rose-400">3. 成本纪律止损</span>
                          <code className="font-mono text-[10px] text-slate-500">
                            cost_stop_loss
                          </code>
                        </div>
                        <p className="mt-1.5 leading-relaxed text-slate-400">
                          当前净值相对持仓加权平均成本亏损达到默认{' '}
                          <strong className="font-mono text-rose-300">
                            {exitThresholds?.cost_stop_loss_pct ?? -8.0}%
                          </strong>
                          （支持单笔自定义），严防深套。
                        </p>
                      </div>

                      <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-orange-300">
                            4. 高位偏热 / 达止盈
                          </span>
                          <code className="font-mono text-[10px] text-slate-500">
                            overheated_stage
                          </code>
                        </div>
                        <p className="mt-1.5 leading-relaxed text-slate-400">
                          代理基金进入 <strong className="text-rose-300">Overheated</strong>{' '}
                          拥挤区或收益触及你为该持仓设定的自定义止盈目标时，提醒分批止盈落袋。
                        </p>
                      </div>

                      <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 sm:col-span-2 lg:col-span-2">
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-emerald-300">
                            5. {feeDays} 日短赎费保护状态
                          </span>
                          <code className="font-mono text-[10px] text-slate-500">
                            short_term_fee_guard
                          </code>
                        </div>
                        <p className="mt-1.5 leading-relaxed text-slate-400">
                          明确提示当前是否存在未满{' '}
                          <strong className="font-mono text-slate-200">{feeDays} 自然日</strong>
                          的买入批次（提示免罚比例与最早全额免罚日期），避免因忽略短赎费白白多交{' '}
                          <strong className="font-mono text-rose-300">{feeRate}%</strong> 惩罚性费率。
                        </p>
                      </div>
                    </div>
                  </article>
                )}
              </section>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

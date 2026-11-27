import { useState } from 'react'
import {
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  Eye,
  Flame,
  LineChart,
  PlusCircle,
  Sparkles,
  Zap,
} from 'lucide-react'
import type {
  BuyModalPrefill,
  OTCMapping,
  OpportunityGroup,
  ScanResponse,
  TrendStage,
} from '../types'

interface OpportunityListProps {
  result: ScanResponse | null
  loading: boolean
  error: string | null
  onSelectFund: (symbol: string) => void
  onRecordBuy?: (prefill: BuyModalPrefill) => void
  activeTemplateName?: string | null
}

const TREND_STAGE_META: Record<TrendStage, { label: string; badgeClass: string }> = {
  oversold: {
    label: '超跌低位',
    badgeClass: 'border-cyan-500/40 bg-cyan-500/15 text-cyan-300',
  },
  early: {
    label: '启动初期',
    badgeClass: 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300',
  },
  mid: {
    label: '趋势中段',
    badgeClass: 'border-sky-500/35 bg-sky-500/10 text-sky-300',
  },
  overheated: {
    label: '高位偏热',
    badgeClass: 'border-rose-500/45 bg-rose-500/15 text-rose-300',
  },
}

function formatPct(val: number): string {
  const sign = val > 0 ? '+' : ''
  return `${sign}${val.toFixed(2)}%`
}

function formatMetricPct(val: number | null | undefined, digits = 1): string {
  if (val === null || val === undefined) return '-'
  const sign = val > 0 ? '+' : ''
  return `${sign}${val.toFixed(digits)}%`
}

function pctClass(val: number): string {
  if (val > 0) return 'text-rose-400'
  if (val < 0) return 'text-emerald-400'
  return 'text-slate-300'
}

function shareBadgeClass(cls: OTCMapping['share_class']): string {
  if (cls === 'C') return 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
  if (cls === 'A') return 'bg-sky-500/15 text-sky-300 border-sky-500/30'
  return 'bg-slate-700/60 text-slate-300 border-slate-600'
}

export function OpportunityList({
  result,
  loading,
  error,
  onSelectFund,
  onRecordBuy,
  activeTemplateName,
}: OpportunityListProps) {
  const [activeBucket, setActiveBucket] = useState<'actionable' | 'watchlist'>('actionable')
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null)
  const [copiedCode, setCopiedCode] = useState<string | null>(null)

  const handleCopy = async (code: string) => {
    try {
      await navigator.clipboard.writeText(code)
      setCopiedCode(code)
      setTimeout(() => setCopiedCode((cur) => (cur === code ? null : cur)), 1500)
    } catch {
      // clipboard unavailable
    }
  }

  if (error) {
    return (
      <div className="rounded-xl border border-rose-500/40 bg-rose-950/30 p-6 text-sm text-rose-300">
        扫描失败：{error}
      </div>
    )
  }

  if (!result && loading) {
    return (
      <div className="flex h-64 items-center justify-center rounded-xl border border-slate-800 bg-slate-900/50 text-sm text-slate-400">
        正在加载全市场向量化特征并执行策略筛选...
      </div>
    )
  }

  if (!result) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-2 rounded-xl border border-slate-800 bg-slate-900/40 p-8 text-center">
        <Sparkles className="h-8 w-8 text-amber-400/70" />
        <p className="text-sm font-medium text-slate-200">选择左侧策略并点击「立即扫描机会」</p>
        <p className="text-xs text-slate-400">
          若首次运行尚未同步数据，请先前往顶部「数据源与基金池」一键拉取全市场数据。
        </p>
      </div>
    )
  }

  const actionableGroups = result.actionable_groups ?? result.groups
  const watchlistGroups = result.watchlist_groups ?? []
  const actionableIndexCount = result.actionable_index_count ?? actionableGroups.length
  const actionableFundCount = result.actionable_fund_count ?? result.hit_fund_count
  const watchlistIndexCount = result.watchlist_index_count ?? watchlistGroups.length
  const watchlistFundCount = result.watchlist_fund_count ?? 0

  const activeGroups = activeBucket === 'actionable' ? actionableGroups : watchlistGroups
  const isWatchlist = activeBucket === 'watchlist'

  return (
    <div className="space-y-4">
      {/* Telemetry Banner */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-900/70 px-4 py-3">
        <div className="flex flex-wrap items-center gap-4 text-xs">
          <div className="flex items-center gap-1.5 text-amber-300">
            <Zap className="h-4 w-4" />
            <span className="font-mono font-semibold">{result.elapsed_ms.toFixed(1)} ms</span>
          </div>
          <span className="text-slate-600">|</span>
          <span className="text-slate-300">
            信号日期 <strong className="font-mono text-slate-100">{result.trade_date || '-'}</strong>
          </span>
          <span className="text-slate-600">|</span>
          <span className="text-slate-300">
            可买{' '}
            <strong className="font-mono text-amber-300">{actionableIndexCount}</strong> 指数 /{' '}
            <strong className="font-mono text-slate-100">{actionableFundCount}</strong> 基金
            <span className="mx-1.5 text-slate-600">·</span>
            观察{' '}
            <strong className="font-mono text-purple-300">{watchlistIndexCount}</strong> 指数 /{' '}
            <strong className="font-mono text-slate-100">{watchlistFundCount}</strong> 基金
            <span className="ml-1.5 text-slate-500">(过滤池 {result.scanned_fund_count} 只)</span>
          </span>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-slate-400">
          {result.hide_overheated && (
            <span className="rounded border border-rose-500/35 bg-rose-500/10 px-1.5 py-0.5 text-[10px] font-medium text-rose-300">
              已过滤高位偏热
            </span>
          )}
          <span>
            场外偏好：
            <span className="ml-1 font-semibold text-emerald-300">
              {result.share_class_preference} 类份额优先
            </span>
          </span>
        </div>
      </div>

      {/* Two-Stage Result Bucket Tabs */}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <button
          type="button"
          onClick={() => {
            setActiveBucket('actionable')
            setExpandedGroup(null)
          }}
          className={`flex items-center justify-between rounded-xl border px-4 py-3 text-left transition ${
            !isWatchlist
              ? 'border-amber-500/50 bg-amber-500/10 text-amber-200 shadow-sm shadow-amber-500/10'
              : 'border-slate-800 bg-slate-900/60 text-slate-400 hover:border-slate-700 hover:text-slate-200'
          }`}
        >
          <div className="flex items-center gap-2">
            <Flame className={`h-4 w-4 ${!isWatchlist ? 'text-amber-400' : 'text-slate-500'}`} />
            <div>
              <div className="text-xs font-semibold">今日可买信号</div>
              <div className="text-[11px] opacity-75">通过趋势过滤并触发买点</div>
            </div>
          </div>
          <div className="text-right font-mono text-xs">
            <div className={!isWatchlist ? 'font-bold text-amber-300' : 'text-slate-300'}>
              {actionableIndexCount} 指数
            </div>
            <div className="text-[10px] text-slate-400">{actionableFundCount} 只基金</div>
          </div>
        </button>

        <button
          type="button"
          onClick={() => {
            setActiveBucket('watchlist')
            setExpandedGroup(null)
          }}
          className={`flex items-center justify-between rounded-xl border px-4 py-3 text-left transition ${
            isWatchlist
              ? 'border-purple-500/50 bg-purple-500/10 text-purple-200 shadow-sm shadow-purple-500/10'
              : 'border-slate-800 bg-slate-900/60 text-slate-400 hover:border-slate-700 hover:text-slate-200'
          }`}
        >
          <div className="flex items-center gap-2">
            <Eye className={`h-4 w-4 ${isWatchlist ? 'text-purple-400' : 'text-slate-500'}`} />
            <div>
              <div className="text-xs font-semibold">主线观察 · 等回踩</div>
              <div className="text-[11px] opacity-75">处于强势趋势，暂未触发买点</div>
            </div>
          </div>
          <div className="text-right font-mono text-xs">
            <div className={isWatchlist ? 'font-bold text-purple-300' : 'text-slate-300'}>
              {watchlistIndexCount} 指数
            </div>
            <div className="text-[10px] text-slate-400">{watchlistFundCount} 只基金</div>
          </div>
        </button>
      </div>

      {activeGroups.length === 0 ? (
        <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-10 text-center">
          {!isWatchlist ? (
            <>
              <p className="text-sm font-medium text-slate-200">
                当前策略组合在「今日可买信号」中暂无命中标的
              </p>
              <p className="mt-1 text-xs text-slate-400">
                {watchlistGroups.length > 0
                  ? `当前有 ${watchlistIndexCount} 个强势主线指数处于观察状态，可切换至「主线观察 · 等回踩」跟踪。`
                  : '可以尝试切换组合逻辑为「任一触发」、放宽参数阈值，或降低 20 日均额底线。'}
              </p>
              {watchlistGroups.length > 0 && (
                <button
                  type="button"
                  onClick={() => setActiveBucket('watchlist')}
                  className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-purple-500/40 bg-purple-500/15 px-3 py-1.5 text-xs font-medium text-purple-200 hover:bg-purple-500/25"
                >
                  <Eye className="h-3.5 w-3.5" />
                  查看 {watchlistIndexCount} 个主线观察标的
                </button>
              )}
            </>
          ) : (
            <>
              <p className="text-sm font-medium text-slate-200">「主线观察 · 等回踩」暂无标的</p>
              <p className="mt-1 text-xs text-slate-400">
                未启用趋势过滤（纯左侧扫描）或所有满足趋势过滤的主线标的均已触发买点。
              </p>
            </>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          {activeGroups.map((grp: OpportunityGroup) => {
            const rep = grp.representative
            const isExpanded = expandedGroup === grp.group_key
            const previewOtcs = grp.otc_funds.slice(0, 4)
            const matchedRegimes = rep.matched_regimes ?? []
            const stageKey: TrendStage = rep.trend_stage ?? 'mid'
            const stageMeta = TREND_STAGE_META[stageKey]
            const hits20d = rep.hits_20d ?? 0

            return (
              <div
                key={`${activeBucket}-${grp.group_key}`}
                className="overflow-hidden rounded-xl border border-slate-800/90 bg-slate-900/75 transition hover:border-slate-700"
              >
                <div className="p-4">
                  {/* Top Row: Index + Representative ETF + Stage/Fresh Badges + Score + Replay Button */}
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-sm font-bold text-slate-100">{grp.index_name}</h3>
                        {grp.index_code && (
                          <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                            指数 {grp.index_code}
                          </span>
                        )}
                        <span className="rounded border border-slate-700 bg-slate-800/80 px-1.5 py-0.5 text-[10px] text-slate-300">
                          {rep.fund_type}
                        </span>
                        <span
                          className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold ${stageMeta.badgeClass}`}
                        >
                          {stageMeta.label}
                        </span>
                        {!isWatchlist && rep.is_fresh && (
                          <span className="inline-flex items-center gap-1 rounded border border-teal-400/50 bg-teal-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-teal-200">
                            <Sparkles className="h-2.5 w-2.5" />
                            新信号
                          </span>
                        )}
                        {hits20d > 0 && (
                          <span
                            className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${
                              !isWatchlist && hits20d === 1
                                ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                                : 'border-slate-700 bg-slate-800/70 text-slate-300'
                            }`}
                          >
                            {isWatchlist ? `近20日触发${hits20d}次` : `近20日第${hits20d}次`}
                          </span>
                        )}
                        {grp.member_count > 1 && (
                          <span className="rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-300">
                            同指数 {grp.member_count} 只命中
                          </span>
                        )}
                      </div>

                      {/* Representative ETF Summary */}
                      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
                        <span>
                          主代表：
                          <button
                            type="button"
                            onClick={() => onSelectFund(rep.symbol)}
                            className="ml-1 font-medium text-slate-200 underline decoration-slate-600 underline-offset-2 hover:text-amber-300"
                          >
                            {rep.name}
                          </button>
                          <span className="ml-1 font-mono text-slate-500">{rep.code}</span>
                        </span>
                        <span className="font-mono">
                          收盘 <strong className="text-slate-100">{rep.close.toFixed(3)}</strong>
                        </span>
                        <span className={`font-mono font-semibold ${pctClass(rep.pct_chg)}`}>
                          {formatPct(rep.pct_chg)}
                        </span>
                        <span className="font-mono">
                          20日均额{' '}
                          <strong className="text-slate-200">
                            {rep.avg_amount_20d >= 10000
                              ? `${(rep.avg_amount_20d / 10000).toFixed(2)}亿`
                              : `${Math.round(rep.avg_amount_20d)}万`}
                          </strong>
                        </span>
                        <span className="font-mono">
                          量比 <strong className="text-slate-200">{rep.vol_ratio.toFixed(2)}x</strong>
                        </span>
                      </div>

                      {/* Compact Raw Extension & Crowding Metrics Strip */}
                      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-slate-800/80 bg-slate-950/50 px-2.5 py-1 font-mono text-[11px] text-slate-400">
                        <span>
                          MA20乖离{' '}
                          <strong
                            className={
                              rep.bias20 !== null && rep.bias20 !== undefined
                                ? rep.bias20 >= 5
                                  ? 'text-rose-300'
                                  : rep.bias20 <= -4
                                    ? 'text-cyan-300'
                                    : 'text-slate-200'
                                : 'text-slate-500'
                            }
                          >
                            {formatMetricPct(rep.bias20)}
                          </strong>
                        </span>
                        <span className="text-slate-700">·</span>
                        <span>
                          MA60乖离{' '}
                          <strong className="text-slate-200">{formatMetricPct(rep.bias60)}</strong>
                        </span>
                        <span className="text-slate-700">·</span>
                        <span>
                          距60日低点{' '}
                          <strong className="text-slate-200">
                            {formatMetricPct(rep.drawup_60d_low)}
                          </strong>
                        </span>
                        <span className="text-slate-700">·</span>
                        <span>
                          %B{' '}
                          <strong
                            className={
                              rep.boll_pct_b !== null && rep.boll_pct_b !== undefined
                                ? rep.boll_pct_b >= 0.85
                                  ? 'text-rose-300'
                                  : rep.boll_pct_b <= 0.15
                                    ? 'text-cyan-300'
                                    : 'text-slate-200'
                                : 'text-slate-500'
                            }
                          >
                            {rep.boll_pct_b !== null && rep.boll_pct_b !== undefined
                              ? rep.boll_pct_b.toFixed(2)
                              : '-'}
                          </strong>
                        </span>
                        <span className="text-slate-700">·</span>
                        <span>
                          RSI14{' '}
                          <strong
                            className={
                              rep.rsi14 !== null
                                ? rep.rsi14 >= 65
                                  ? 'text-rose-300'
                                  : rep.rsi14 <= 38
                                    ? 'text-cyan-300'
                                    : 'text-slate-200'
                                : 'text-slate-500'
                            }
                          >
                            {rep.rsi14 !== null ? rep.rsi14.toFixed(1) : '-'}
                          </strong>
                        </span>
                      </div>
                    </div>

                    <div className="flex items-center gap-2">
                      <div
                        className={`rounded-lg border px-2.5 py-1 text-right ${
                          isWatchlist
                            ? 'border-purple-500/30 bg-purple-500/10'
                            : 'border-amber-500/30 bg-amber-500/10'
                        }`}
                      >
                        <div
                          className={`text-[10px] ${
                            isWatchlist ? 'text-purple-300/80' : 'text-amber-300/80'
                          }`}
                        >
                          {isWatchlist
                            ? `满足 ${grp.max_hit_count} 主线`
                            : `命中 ${grp.max_hit_count} 买点`}
                        </div>
                        <div
                          className={`font-mono text-sm font-bold ${
                            isWatchlist ? 'text-purple-300' : 'text-amber-300'
                          }`}
                        >
                          {grp.max_composite_score.toFixed(1)}
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => onSelectFund(rep.symbol)}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs font-medium text-slate-100 transition hover:border-amber-500/50 hover:bg-slate-700"
                      >
                        <LineChart className="h-3.5 w-3.5 text-amber-400" />
                        K线与回看
                      </button>
                    </div>
                  </div>

                  {/* Matched Trend Regimes & Hit Entry Triggers */}
                  <div className="mt-2.5 flex flex-wrap gap-1.5">
                    {matchedRegimes.map((mr) => (
                      <span
                        key={`regime-${mr.strategy_id}`}
                        className="inline-flex items-center gap-1.5 rounded-md border border-purple-500/35 bg-purple-950/40 px-2 py-1 text-[11px]"
                      >
                        <span className="rounded bg-purple-500/20 px-1 text-[10px] font-semibold text-purple-300">
                          主线
                        </span>
                        <span className="font-medium text-purple-200">{mr.strategy_name}</span>
                        <span className="text-slate-400">{mr.reason}</span>
                      </span>
                    ))}
                    {rep.hit_strategies.map((hs) => (
                      <span
                        key={`entry-${hs.strategy_id}`}
                        className="inline-flex items-center gap-1.5 rounded-md border border-amber-500/35 bg-slate-950/70 px-2 py-1 text-[11px]"
                      >
                        <span className="rounded bg-amber-500/20 px-1 text-[10px] font-semibold text-amber-300">
                          买点
                        </span>
                        <span className="font-medium text-amber-300">{hs.strategy_name}</span>
                        <span className="text-slate-400">{hs.reason}</span>
                      </span>
                    ))}
                    {isWatchlist && rep.hit_strategies.length === 0 && (
                      <span className="inline-flex items-center rounded-md border border-slate-700/80 bg-slate-950/60 px-2 py-1 text-[11px] text-slate-400">
                        暂未触发买点 · 等待回踩或启动信号
                      </span>
                    )}
                  </div>

                  {/* Quick OTC Counterparts Strip */}
                  <div className="mt-3 rounded-lg border border-slate-800/90 bg-slate-950/60 p-2.5">
                    <div className="mb-1.5 flex items-center justify-between">
                      <span className="text-[11px] font-medium text-slate-400">
                        对应场外可购基金（共 {grp.otc_count} 只，点击代码复制）
                      </span>
                      {(grp.otc_count > 4 || grp.member_count > 1) && (
                        <button
                          type="button"
                          onClick={() => setExpandedGroup(isExpanded ? null : grp.group_key)}
                          className="inline-flex items-center gap-1 text-[11px] text-amber-400 hover:text-amber-300"
                        >
                          {isExpanded ? '收起详情' : '展开全部同指数ETF与场外份额'}
                          {isExpanded ? (
                            <ChevronUp className="h-3.5 w-3.5" />
                          ) : (
                            <ChevronDown className="h-3.5 w-3.5" />
                          )}
                        </button>
                      )}
                    </div>

                    <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                      {(isExpanded ? grp.otc_funds : previewOtcs).map((otc) => (
                        <div
                          key={otc.otc_code}
                          className="flex items-center justify-between gap-2 rounded-md border border-slate-800 bg-slate-900/80 px-2.5 py-1.5 text-xs"
                        >
                          <div className="flex min-w-0 items-center gap-1.5">
                            <span
                              className={`shrink-0 rounded border px-1 py-0.2 font-mono text-[10px] font-semibold ${shareBadgeClass(otc.share_class)}`}
                            >
                              {otc.share_class}
                            </span>
                            <button
                              type="button"
                              onClick={() => handleCopy(otc.otc_code)}
                              className="inline-flex shrink-0 items-center gap-1 font-mono font-semibold text-amber-300 hover:text-amber-200"
                              title="点击复制场外基金代码"
                            >
                              {otc.otc_code}
                              {copiedCode === otc.otc_code ? (
                                <Check className="h-3 w-3 text-emerald-400" />
                              ) : (
                                <Copy className="h-3 w-3 opacity-60" />
                              )}
                            </button>
                            <span className="truncate text-[11px] text-slate-300" title={otc.otc_name}>
                              {otc.otc_name}
                            </span>
                          </div>
                          <div className="flex shrink-0 items-center gap-1.5 text-[10px]">
                            {otc.purchase_status && (
                              <span
                                className={
                                  otc.purchase_status.includes('暂停')
                                    ? 'text-rose-400'
                                    : otc.purchase_status.includes('限')
                                      ? 'text-amber-400'
                                      : 'text-emerald-400'
                                }
                              >
                                {otc.purchase_status}
                              </span>
                            )}
                            {otc.fee_rate && (
                              <span className="font-mono text-slate-500">{otc.fee_rate}</span>
                            )}
                            {onRecordBuy && (
                              <button
                                type="button"
                                onClick={() =>
                                  onRecordBuy({
                                    otc_code: otc.otc_code,
                                    otc_name: otc.otc_name,
                                    exchange_symbol: otc.exchange_symbol || rep.symbol,
                                    exchange_name: rep.name,
                                    entry_signal_snapshot: {
                                      signal_date: result.trade_date,
                                      template_name: activeTemplateName ?? null,
                                      trend_stage: rep.trend_stage ?? null,
                                      strategy_names: [
                                        ...(rep.matched_regimes ?? []).map((r) => r.strategy_name),
                                        ...rep.hit_strategies.map((s) => s.strategy_name),
                                      ],
                                      regime_names: (rep.matched_regimes ?? []).map(
                                        (r) => r.strategy_name,
                                      ),
                                      entry_names: rep.hit_strategies.map((s) => s.strategy_name),
                                      proxy_symbol: rep.symbol,
                                      proxy_name: rep.name,
                                      proxy_close: rep.close,
                                      index_name: grp.index_name,
                                    },
                                  })
                                }
                                className="inline-flex items-center gap-0.5 rounded border border-amber-500/40 bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-200 transition hover:bg-amber-500/30"
                                title="一键记录场外买入并保存当前信号快照"
                              >
                                <PlusCircle className="h-3 w-3" />
                                记录买入
                              </button>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                {/* Expanded Member ETFs */}
                {isExpanded && grp.member_count > 1 && (
                  <div className="border-t border-slate-800 bg-slate-950/80 px-4 py-3">
                    <div className="mb-2 text-[11px] font-medium text-slate-400">
                      同指数命中场内基金 ({grp.member_count} 只)
                    </div>
                    <div className="grid grid-cols-1 gap-1.5 md:grid-cols-2">
                      {grp.members.map((m) => (
                        <div
                          key={m.symbol}
                          className="flex items-center justify-between rounded border border-slate-800 bg-slate-900/60 px-3 py-1.5 text-xs"
                        >
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-slate-400">{m.code}</span>
                            <button
                              type="button"
                              onClick={() => onSelectFund(m.symbol)}
                              className="text-slate-200 hover:text-amber-300"
                            >
                              {m.name}
                            </button>
                            {m.trend_stage && (
                              <span
                                className={`rounded border px-1 py-0.5 text-[10px] ${TREND_STAGE_META[m.trend_stage].badgeClass}`}
                              >
                                {TREND_STAGE_META[m.trend_stage].label}
                              </span>
                            )}
                            {!isWatchlist && m.is_fresh && (
                              <span className="rounded border border-teal-500/40 bg-teal-500/15 px-1 py-0.5 text-[10px] text-teal-200">
                                新
                              </span>
                            )}
                          </div>
                          <div className="flex items-center gap-3 font-mono text-[11px]">
                            <span className={pctClass(m.pct_chg)}>{formatPct(m.pct_chg)}</span>
                            <span className="text-slate-400">
                              均额 {Math.round(m.avg_amount_20d)}万
                            </span>
                            <span className={isWatchlist ? 'text-purple-300' : 'text-amber-300'}>
                              {m.composite_score.toFixed(1)}分
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

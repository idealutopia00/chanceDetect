import { useState } from 'react'
import {
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  LineChart,
  Sparkles,
  Zap,
} from 'lucide-react'
import type { OTCMapping, OpportunityGroup, ScanResponse } from '../types'

interface OpportunityListProps {
  result: ScanResponse | null
  loading: boolean
  error: string | null
  onSelectFund: (symbol: string) => void
}

function formatPct(val: number): string {
  const sign = val > 0 ? '+' : ''
  return `${sign}${val.toFixed(2)}%`
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
}: OpportunityListProps) {
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
            命中{' '}
            <strong className="font-mono text-amber-300">{result.hit_index_count}</strong> 个标的指数 /{' '}
            <strong className="font-mono text-slate-100">{result.hit_fund_count}</strong> 只场内基金
            <span className="ml-1 text-slate-500">(过滤池 {result.scanned_fund_count} 只)</span>
          </span>
        </div>
        <div className="text-[11px] text-slate-400">
          场外偏好：
          <span className="ml-1 font-semibold text-emerald-300">
            {result.share_class_preference} 类份额优先
          </span>
        </div>
      </div>

      {result.groups.length === 0 ? (
        <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-10 text-center">
          <p className="text-sm font-medium text-slate-200">当前策略组合在该交易日暂无命中标的</p>
          <p className="mt-1 text-xs text-slate-400">
            可以尝试切换组合逻辑为「任一命中」、放宽参数阈值，或降低 20 日均额底线。
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {result.groups.map((grp: OpportunityGroup) => {
            const rep = grp.representative
            const isExpanded = expandedGroup === grp.group_key
            const previewOtcs = grp.otc_funds.slice(0, 4)

            return (
              <div
                key={grp.group_key}
                className="overflow-hidden rounded-xl border border-slate-800/90 bg-slate-900/75 transition hover:border-slate-700"
              >
                <div className="p-4">
                  {/* Top Row: Index + Representative ETF + Score + Replay Button */}
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-sm font-bold text-slate-100">
                          {grp.index_name}
                        </h3>
                        {grp.index_code && (
                          <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                            指数 {grp.index_code}
                          </span>
                        )}
                        <span className="rounded border border-slate-700 bg-slate-800/80 px-1.5 py-0.5 text-[10px] text-slate-300">
                          {rep.fund_type}
                        </span>
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
                        {rep.rsi14 !== null && (
                          <span className="font-mono">
                            RSI14 <strong className="text-slate-200">{rep.rsi14.toFixed(1)}</strong>
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-2">
                      <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-right">
                        <div className="text-[10px] text-amber-300/80">
                          命中 {grp.max_hit_count} 策略
                        </div>
                        <div className="font-mono text-sm font-bold text-amber-300">
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

                  {/* Hit Strategy Reasons */}
                  <div className="mt-2.5 flex flex-wrap gap-1.5">
                    {rep.hit_strategies.map((hs) => (
                      <span
                        key={hs.strategy_id}
                        className="inline-flex items-center gap-1.5 rounded-md border border-slate-700/80 bg-slate-950/70 px-2 py-1 text-[11px]"
                      >
                        <span className="font-medium text-amber-300">{hs.strategy_name}</span>
                        <span className="text-slate-400">{hs.reason}</span>
                      </span>
                    ))}
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
                          onClick={() =>
                            setExpandedGroup(isExpanded ? null : grp.group_key)
                          }
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
                            {otc.fee_rate && <span className="font-mono text-slate-500">{otc.fee_rate}</span>}
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
                          </div>
                          <div className="flex items-center gap-3 font-mono text-[11px]">
                            <span className={pctClass(m.pct_chg)}>{formatPct(m.pct_chg)}</span>
                            <span className="text-slate-400">
                              均额 {Math.round(m.avg_amount_20d)}万
                            </span>
                            <span className="text-amber-300">{m.composite_score.toFixed(1)}分</span>
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

import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  Calendar,
  Clock,
  Link2,
  Loader2,
  Search,
  Sparkles,
  Unlink,
  X,
} from 'lucide-react'
import { api } from '../api/client'
import type {
  BuyModalPrefill,
  EntrySignalSnapshot,
  ExchangeFundInfo,
  OTCLookupItem,
  TrackedPositionRecord,
  TradeSide,
} from '../types'
import {
  getShanghaiNow,
  isWeekendDate,
  nextWeekday,
  prevWeekday,
  weekdayLabel,
} from '../utils/tradingDate'
import { GlossaryTooltip } from './GlossaryTooltip'

interface BuyPositionModalProps {
  open: boolean
  prefill?: BuyModalPrefill | null
  existingPosition?: TrackedPositionRecord | null
  onClose: () => void
  onSaved: (position: TrackedPositionRecord, mode: 'created' | 'lot_added') => void
  onNavigateToGuide?: (anchorId: string) => void
}

const OTC_CODE_RE = /^\d{6}$/

export function BuyPositionModal({
  open,
  prefill,
  existingPosition,
  onClose,
  onSaved,
  onNavigateToGuide,
}: BuyPositionModalProps) {
  const shNow = useMemo(() => getShanghaiNow(), [open])

  // Target mode: creating a brand new Tracked Position vs adding a lot to an active position
  const [otcQuery, setOtcQuery] = useState('')
  const [lookupItems, setLookupItems] = useState<OTCLookupItem[]>([])
  const [lookupLoading, setLookupLoading] = useState(false)
  const [showDropdown, setShowDropdown] = useState(false)

  const [otcCode, setOtcCode] = useState('')
  const [otcName, setOtcName] = useState('')
  const [latestKnownNav, setLatestKnownNav] = useState<number | null>(null)
  const [latestKnownNavDate, setLatestKnownNavDate] = useState<string | null>(null)
  const [detectedActivePosId, setDetectedActivePosId] = useState<number | null>(null)

  // Linked On-Exchange Proxy Fund
  const [proxySymbol, setProxySymbol] = useState<string | null>(null)
  const [proxyLabel, setProxyLabel] = useState<string>('')
  const [editingProxy, setEditingProxy] = useState(false)
  const [proxyQuery, setProxyQuery] = useState('')
  const [proxyCandidates, setProxyCandidates] = useState<ExchangeFundInfo[]>([])
  const [proxySearching, setProxySearching] = useState(false)

  // Trade lot fields
  const [side, setSide] = useState<TradeSide>('buy')
  const [tradeDate, setTradeDate] = useState(shNow.dateStr)
  const [amountStr, setAmountStr] = useState('')
  const [sharesStr, setSharesStr] = useState('')
  const [feeStr, setFeeStr] = useState('')
  const [useManualNav, setUseManualNav] = useState(false)
  const [manualNavStr, setManualNavStr] = useState('')
  const [notes, setNotes] = useState('')
  const [signalSnapshot, setSignalSnapshot] = useState<EntrySignalSnapshot>({})

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Reset form state whenever modal opens
  useEffect(() => {
    if (!open) return
    const now = getShanghaiNow()
    setError(null)
    setSubmitting(false)
    setSide('buy')
    setTradeDate(now.dateStr)
    setAmountStr('')
    setSharesStr('')
    setFeeStr('')
    setUseManualNav(false)
    setManualNavStr('')
    setNotes('')
    setEditingProxy(false)
    setProxyQuery('')
    setProxyCandidates([])
    setShowDropdown(false)

    if (existingPosition) {
      setOtcCode(existingPosition.otc_code)
      setOtcName(existingPosition.otc_name)
      setOtcQuery(`${existingPosition.otc_code} ${existingPosition.otc_name}`)
      setLatestKnownNav(existingPosition.latest_nav)
      setLatestKnownNavDate(existingPosition.latest_nav_date)
      setDetectedActivePosId(existingPosition.id)
      setProxySymbol(existingPosition.exchange_symbol)
      setProxyLabel(
        existingPosition.proxy_fund
          ? `${existingPosition.proxy_fund.code} ${existingPosition.proxy_fund.name}`
          : existingPosition.exchange_symbol || '',
      )
      setSignalSnapshot({})
    } else if (prefill) {
      setOtcCode(prefill.otc_code)
      setOtcName(prefill.otc_name)
      setOtcQuery(`${prefill.otc_code} ${prefill.otc_name}`)
      setLatestKnownNav(null)
      setLatestKnownNavDate(null)
      setDetectedActivePosId(null)
      setProxySymbol(prefill.exchange_symbol)
      setProxyLabel(
        prefill.exchange_symbol
          ? `${prefill.exchange_symbol} ${prefill.exchange_name ?? ''}`.trim()
          : '',
      )
      setSignalSnapshot(prefill.entry_signal_snapshot ?? {})

      // Check if an active position already exists or cached NAV is available
      api
        .lookupOtc(prefill.otc_code, 5)
        .then((res) => {
          const hit = res.items.find((i) => i.otc_code === prefill.otc_code)
          if (hit) {
            setLatestKnownNav(hit.latest_nav)
            setLatestKnownNavDate(hit.latest_nav_date)
            if (hit.has_active_position && hit.active_position_id) {
              setDetectedActivePosId(hit.active_position_id)
            }
          }
        })
        .catch(() => {
          // ignore background lookup error
        })
    } else {
      setOtcCode('')
      setOtcName('')
      setOtcQuery('')
      setLatestKnownNav(null)
      setLatestKnownNavDate(null)
      setDetectedActivePosId(null)
      setProxySymbol(null)
      setProxyLabel('')
      setSignalSnapshot({})
    }
  }, [open, prefill, existingPosition])

  // Debounced OTC lookup when searching in new-position mode
  useEffect(() => {
    if (!open || existingPosition || prefill) return
    const trimmed = otcQuery.trim()
    let cancelled = false
    const timer = window.setTimeout(() => {
      setLookupLoading(true)
      api
        .lookupOtc(trimmed, 15)
        .then((res) => {
          if (cancelled) return
          setLookupItems(res.items)
          // Auto-select resolved arbitrary 6-digit fund when user typed exact 6 digits
          if (OTC_CODE_RE.test(trimmed)) {
            const exact = res.items.find((i) => i.otc_code === trimmed)
            if (exact) {
              setOtcCode(exact.otc_code)
              setOtcName(exact.otc_name)
              setLatestKnownNav(exact.latest_nav)
              setLatestKnownNavDate(exact.latest_nav_date)
              setDetectedActivePosId(exact.active_position_id)
              if (exact.suggested_proxy && !proxySymbol) {
                setProxySymbol(exact.suggested_proxy.symbol)
                setProxyLabel(`${exact.suggested_proxy.code} ${exact.suggested_proxy.name}`)
              }
            } else {
              setOtcCode(trimmed)
              setOtcName('')
              setLatestKnownNav(null)
              setLatestKnownNavDate(null)
              setDetectedActivePosId(null)
              setProxySymbol(null)
              setProxyLabel('')
            }
          }
        })
        .catch(() => {
          if (!cancelled) setLookupItems([])
        })
        .finally(() => {
          if (!cancelled) setLookupLoading(false)
        })
    }, 250)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [open, existingPosition, prefill, otcQuery])

  // Debounced proxy fund search when manually overriding proxy
  useEffect(() => {
    if (!open || !editingProxy) return
    let cancelled = false
    const timer = window.setTimeout(() => {
      setProxySearching(true)
      api
        .searchFunds(proxyQuery.trim(), undefined, 12, 0)
        .then((res) => {
          if (!cancelled) setProxyCandidates(res.items)
        })
        .catch(() => {
          if (!cancelled) setProxyCandidates([])
        })
        .finally(() => {
          if (!cancelled) setProxySearching(false)
        })
    }, 220)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [open, editingProxy, proxyQuery])

  if (!open) return null

  const targetActivePosId = existingPosition?.id ?? detectedActivePosId
  const isAddingLot = targetActivePosId !== null
  const isWeekend = isWeekendDate(tradeDate)
  const isAfterCutoffToday =
    tradeDate === shNow.dateStr && shNow.isAfterCutoff1500 && !isWeekend
  const likelyPendingNav =
    !useManualNav &&
    (tradeDate >= shNow.dateStr ||
      (latestKnownNavDate !== null && tradeDate > latestKnownNavDate))

  const handleSelectLookupItem = (item: OTCLookupItem) => {
    setOtcCode(item.otc_code)
    setOtcName(item.otc_name)
    setOtcQuery(`${item.otc_code} ${item.otc_name}`)
    setLatestKnownNav(item.latest_nav)
    setLatestKnownNavDate(item.latest_nav_date)
    setDetectedActivePosId(item.active_position_id)
    if (item.suggested_proxy) {
      setProxySymbol(item.suggested_proxy.symbol)
      setProxyLabel(`${item.suggested_proxy.code} ${item.suggested_proxy.name}`)
    } else {
      setProxySymbol(null)
      setProxyLabel('')
    }
    setShowDropdown(false)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    const cleanCode = otcCode.trim() || otcQuery.trim()
    if (!OTC_CODE_RE.test(cleanCode)) {
      setError('请选择或输入有效的 6 位场外基金代码。')
      return
    }

    const amount = amountStr.trim() ? Number(amountStr) : undefined
    const shares = sharesStr.trim() ? Number(sharesStr) : undefined
    const fee = feeStr.trim() ? Number(feeStr) : 0
    const manualNav = useManualNav && manualNavStr.trim() ? Number(manualNavStr) : undefined

    if (amount !== undefined && (!Number.isFinite(amount) || amount <= 0)) {
      setError('金额必须为大于 0 的数字。')
      return
    }
    if (shares !== undefined && (!Number.isFinite(shares) || shares <= 0)) {
      setError('份额必须为大于 0 的数字。')
      return
    }
    if (!Number.isFinite(fee) || fee < 0) {
      setError('手续费必须为非负数字。')
      return
    }
    if (useManualNav && (manualNav === undefined || !Number.isFinite(manualNav) || manualNav <= 0)) {
      setError('启用手动指定净值时，请输入大于 0 的净值。')
      return
    }

    setSubmitting(true)
    try {
      if (isAddingLot && targetActivePosId !== null) {
        const res = await api.addTradeLot(targetActivePosId, {
          side,
          trade_date: tradeDate,
          nav: manualNav ?? null,
          amount: amount ?? null,
          shares: shares ?? null,
          fee,
          notes: notes.trim(),
        })
        onSaved(res.position, 'lot_added')
      } else {
        const res = await api.createPosition({
          otc_code: cleanCode,
          otc_name: otcName.trim() || undefined,
          exchange_symbol: proxySymbol,
          trade_date: tradeDate,
          nav: manualNav ?? null,
          amount: amount ?? null,
          shares: shares ?? null,
          fee,
          notes: notes.trim(),
          lot_notes: notes.trim(),
          entry_signal_snapshot: signalSnapshot,
        })
        onSaved(res.position, 'created')
      }
      onClose()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm">
      <div className="max-h-[92vh] w-full max-w-xl overflow-y-auto rounded-2xl border border-slate-700/80 bg-slate-900 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-800 px-5 py-3.5">
          <div>
            <h2 className="text-sm font-bold text-slate-100">
              {existingPosition
                ? `加仓买入 · ${existingPosition.otc_name}`
                : isAddingLot
                  ? '追加买入批次（已有持仓中记录）'
                  : '记录场外基金买入'}
            </h2>
            <p className="mt-0.5 text-[11px] text-slate-400">
              支持映射场外基金与任意 6 位基金代码，当日未公布净值将自动记为 Pending NAV
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 p-5 text-xs">
          {/* Frozen Signal Context Snapshot (from Screening Card) */}
          {Object.keys(signalSnapshot).length > 0 && (
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3">
              <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold text-amber-300">
                <Sparkles className="h-3.5 w-3.5" />
                已自动捕获建仓信号快照
              </div>
              <div className="flex flex-wrap gap-1.5 text-[11px] text-slate-200">
                {signalSnapshot.signal_date && (
                  <span className="rounded bg-slate-900/80 px-2 py-0.5 font-mono text-slate-300">
                    信号日 {signalSnapshot.signal_date}
                  </span>
                )}
                {signalSnapshot.template_name && (
                  <span className="rounded bg-amber-500/20 px-2 py-0.5 text-amber-200">
                    模板：{signalSnapshot.template_name}
                  </span>
                )}
                {signalSnapshot.trend_stage && (
                  <span className="rounded bg-sky-500/20 px-2 py-0.5 text-sky-200">
                    阶段：{String(signalSnapshot.trend_stage)}
                  </span>
                )}
                {(signalSnapshot.strategy_names ?? []).map((name) => (
                  <span
                    key={name}
                    className="rounded border border-slate-700 bg-slate-900/70 px-2 py-0.5 text-slate-200"
                  >
                    {name}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* OTC Fund Picker */}
          {!existingPosition && (
            <div className="relative">
              <label className="mb-1.5 block font-medium text-slate-300">
                场外基金（搜索映射基金或直接输入任意 6 位基金代码）
              </label>
              {prefill ? (
                <div className="flex items-center justify-between rounded-lg border border-slate-700 bg-slate-950/70 px-3 py-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-semibold text-amber-300">{otcCode}</span>
                    <span className="text-slate-100">{otcName}</span>
                  </div>
                  {latestKnownNav !== null && (
                    <span className="font-mono text-[11px] text-slate-400">
                      参考净值 {latestKnownNav.toFixed(4)} ({latestKnownNavDate})
                    </span>
                  )}
                </div>
              ) : (
                <>
                  <div className="relative">
                    <Search className="pointer-events-none absolute left-3 top-2.5 h-3.5 w-3.5 text-slate-500" />
                    <input
                      type="text"
                      value={otcQuery}
                      onFocus={() => setShowDropdown(true)}
                      onChange={(e) => {
                        const nextVal = e.target.value
                        const trimmedVal = nextVal.trim()
                        setOtcQuery(nextVal)
                        setShowDropdown(true)
                        if (trimmedVal !== otcCode) {
                          setDetectedActivePosId(null)
                          setOtcName('')
                          setLatestKnownNav(null)
                          setLatestKnownNavDate(null)
                          setProxySymbol(null)
                          setProxyLabel('')
                        }
                        if (OTC_CODE_RE.test(trimmedVal)) {
                          setOtcCode(trimmedVal)
                        } else {
                          setOtcCode('')
                        }
                      }}
                      placeholder="输入代码或名称，如 006131 / 沪深300 / 任意 6 位场外基金代码"
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 py-2 pl-8 pr-8 text-xs text-slate-100 placeholder:text-slate-500 focus:border-amber-500 focus:outline-none"
                    />
                    {lookupLoading && (
                      <Loader2 className="absolute right-3 top-2.5 h-3.5 w-3.5 animate-spin text-amber-400" />
                    )}
                  </div>

                  {showDropdown && lookupItems.length > 0 && (
                    <div className="absolute z-20 mt-1 max-h-56 w-full overflow-y-auto rounded-lg border border-slate-700 bg-slate-950 shadow-xl">
                      {lookupItems.map((item) => (
                        <button
                          key={item.otc_code}
                          type="button"
                          onClick={() => handleSelectLookupItem(item)}
                          className="flex w-full items-center justify-between gap-2 border-b border-slate-800/80 px-3 py-2 text-left last:border-b-0 hover:bg-slate-800/80"
                        >
                          <div className="min-w-0">
                            <div className="flex items-center gap-1.5">
                              <span className="font-mono font-semibold text-amber-300">
                                {item.otc_code}
                              </span>
                              <span className="truncate text-slate-100">{item.otc_name}</span>
                              {!item.is_mapped && (
                                <span className="rounded bg-sky-500/20 px-1.5 py-0.2 text-[10px] text-sky-300">
                                  任意场外代码
                                </span>
                              )}
                              {item.has_active_position && (
                                <span className="rounded bg-emerald-500/20 px-1.5 py-0.2 text-[10px] text-emerald-300">
                                  持仓中
                                </span>
                              )}
                            </div>
                            <div className="mt-0.5 text-[10px] text-slate-400">
                              {item.suggested_proxy
                                ? `关联场内代理：${item.suggested_proxy.code} ${item.suggested_proxy.name}`
                                : '无自动映射场内代理（支持手动关联或单曲线跟踪）'}
                            </div>
                          </div>
                          {item.latest_nav !== null && (
                            <div className="shrink-0 text-right font-mono text-[11px] text-slate-300">
                              <div>{item.latest_nav.toFixed(4)}</div>
                              <div className="text-[10px] text-slate-500">{item.latest_nav_date}</div>
                            </div>
                          )}
                        </button>
                      ))}
                    </div>
                  )}
                </>
              )}

              {detectedActivePosId !== null && (
                <div className="mt-2 rounded-lg border border-emerald-500/35 bg-emerald-500/10 px-3 py-2 text-[11px] text-emerald-200">
                  该基金已有持仓中记录（#{detectedActivePosId}），提交后将自动作为新批次追加到该持仓中。
                </div>
              )}
            </div>
          )}

          {/* Optional Proxy Fund Override (only when creating a new position) */}
          {!isAddingLot && (
            <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 text-slate-300">
                  {proxySymbol ? (
                    <Link2 className="h-3.5 w-3.5 text-amber-400" />
                  ) : (
                    <Unlink className="h-3.5 w-3.5 text-slate-500" />
                  )}
                  <span className="font-medium">关联场内代理基金：</span>
                  {proxySymbol ? (
                    <span className="font-mono text-amber-300">
                      {proxyLabel || proxySymbol}
                    </span>
                  ) : (
                    <span className="text-slate-400">未关联（单曲线跟踪模式）</span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setEditingProxy((v) => !v)}
                    className="text-[11px] text-amber-400 hover:text-amber-300"
                  >
                    {editingProxy ? '完成' : '手动修改'}
                  </button>
                  {proxySymbol && (
                    <button
                      type="button"
                      onClick={() => {
                        setProxySymbol(null)
                        setProxyLabel('')
                      }}
                      className="text-[11px] text-slate-400 hover:text-rose-300"
                    >
                      清除关联
                    </button>
                  )}
                </div>
              </div>

              {editingProxy && (
                <div className="mt-2 space-y-1.5">
                  <input
                    type="text"
                    value={proxyQuery}
                    onChange={(e) => setProxyQuery(e.target.value)}
                    placeholder="搜索场内 ETF / LOF 代码或名称（如 510300 / 沪深300）"
                    className="w-full rounded-md border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-100 placeholder:text-slate-500 focus:border-amber-500 focus:outline-none"
                  />
                  {proxySearching ? (
                    <div className="py-1 text-[11px] text-slate-400">搜索中...</div>
                  ) : (
                    <div className="max-h-36 space-y-1 overflow-y-auto">
                      {proxyCandidates.map((fund) => (
                        <button
                          key={fund.symbol}
                          type="button"
                          onClick={() => {
                            setProxySymbol(fund.symbol)
                            setProxyLabel(`${fund.code} ${fund.name}`)
                            setEditingProxy(false)
                          }}
                          className="flex w-full items-center justify-between rounded border border-slate-800 bg-slate-900/80 px-2.5 py-1.5 text-left hover:border-amber-500/40"
                        >
                          <span>
                            <span className="font-mono text-amber-300">{fund.code}</span>{' '}
                            <span className="text-slate-200">{fund.name}</span>
                          </span>
                          <span className="text-[10px] text-slate-400">{fund.index_name}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Buy/scale-in modal stays strictly on buy side; SellPositionModal handles sells */}

          {/* Trade Date + 15:00 Cutoff Warning + Weekend Alignment */}
          <div>
            <label className="mb-1.5 flex items-center justify-between font-medium text-slate-300">
              <span className="inline-flex items-center gap-1.5">
                <Calendar className="h-3.5 w-3.5 text-amber-400" />
                交易确认日期（默认今日 {shNow.dateStr}）
              </span>
              <span className="font-mono text-[10px] text-slate-400">
                Asia/Shanghai {String(shNow.hour).padStart(2, '0')}:
                {String(shNow.minute).padStart(2, '0')}
              </span>
            </label>
            <input
              type="date"
              value={tradeDate}
              onChange={(e) => setTradeDate(e.target.value)}
              required
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs text-slate-100 focus:border-amber-500 focus:outline-none"
            />

            {/* Post-15:00 CST Cutoff Warning */}
            {isAfterCutoffToday && (
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-200">
                <div className="flex items-center gap-1.5">
                  <Clock className="h-3.5 w-3.5 shrink-0 text-amber-400" />
                  <span>当前已过 15:00 截单时间，此刻下单将按下一交易日净值确认。</span>
                </div>
                <button
                  type="button"
                  onClick={() => setTradeDate(nextWeekday(tradeDate))}
                  className="inline-flex items-center gap-1 rounded-md border border-amber-400/50 bg-amber-500/20 px-2 py-1 font-mono text-[11px] font-semibold text-amber-100 hover:bg-amber-500/30"
                >
                  切换至 {nextWeekday(tradeDate)}
                  <ArrowRight className="h-3 w-3" />
                </button>
              </div>
            )}

            {/* Weekend / Non-Trading Day Alignment Prompt */}
            {isWeekend && (
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-200">
                <div className="flex items-center gap-1.5">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-rose-400" />
                  <span>
                    所选日期为{weekdayLabel(tradeDate)}（非交易日），建议对齐到有效交易日。
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => setTradeDate(prevWeekday(tradeDate))}
                    className="rounded border border-slate-600 bg-slate-800 px-2 py-0.5 font-mono text-[10px] text-slate-200 hover:border-amber-500/50"
                  >
                    上一交易日 {prevWeekday(tradeDate)}
                  </button>
                  <button
                    type="button"
                    onClick={() => setTradeDate(nextWeekday(tradeDate))}
                    className="rounded border border-amber-400/50 bg-amber-500/20 px-2 py-0.5 font-mono text-[10px] text-amber-100 hover:bg-amber-500/30"
                  >
                    下一交易日 {nextWeekday(tradeDate)}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Optional Amount / Shares / Fee */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div>
              <label className="mb-1 block text-slate-300">金额（元 · 可选）</label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={amountStr}
                onChange={(e) => setAmountStr(e.target.value)}
                placeholder="留空按纯收益率"
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-2 font-mono text-xs text-slate-100 placeholder:text-slate-600 focus:border-amber-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="mb-1 block text-slate-300">份额（份 · 可选）</label>
              <input
                type="number"
                step="0.0001"
                min="0"
                value={sharesStr}
                onChange={(e) => setSharesStr(e.target.value)}
                placeholder="净值确认后可自动算"
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-2 font-mono text-xs text-slate-100 placeholder:text-slate-600 focus:border-amber-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="mb-1 block text-slate-300">手续费（元 · 可选）</label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={feeStr}
                onChange={(e) => setFeeStr(e.target.value)}
                placeholder="0.00"
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-2 font-mono text-xs text-slate-100 placeholder:text-slate-600 focus:border-amber-500 focus:outline-none"
              />
            </div>
          </div>

          {/* Manual NAV Override + Pending NAV Indicator */}
          <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
            <div className="flex items-center justify-between gap-2">
              <label className="flex cursor-pointer items-center gap-2 text-slate-200">
                <input
                  type="checkbox"
                  checked={useManualNav}
                  onChange={(e) => setUseManualNav(e.target.checked)}
                  className="rounded border-slate-600 bg-slate-900 text-amber-500 focus:ring-amber-500"
                />
                <span>手动指定成交净值（覆盖官方净值，不会被自动同步改写）</span>
              </label>
              <GlossaryTooltip
                termId="pending-nav"
                onNavigateToGuide={onNavigateToGuide}
              />
            </div>

            {useManualNav ? (
              <div className="mt-2">
                <input
                  type="number"
                  step="0.0001"
                  min="0"
                  value={manualNavStr}
                  onChange={(e) => setManualNavStr(e.target.value)}
                  placeholder="输入精确单位净值，例如 1.2485"
                  className="w-full rounded-lg border border-sky-500/40 bg-slate-900 px-3 py-1.5 font-mono text-xs text-slate-100 focus:border-sky-400 focus:outline-none"
                />
              </div>
            ) : (
              <div className="mt-2 flex items-start gap-2 rounded-lg border border-slate-800 bg-slate-900/70 px-2.5 py-2 text-[11px] text-slate-400">
                <span
                  className={`mt-0.5 shrink-0 rounded px-1.5 py-0.2 font-mono text-[10px] font-semibold ${
                    likelyPendingNav
                      ? 'border border-amber-500/40 bg-amber-500/15 text-amber-300'
                      : 'border border-emerald-500/40 bg-emerald-500/15 text-emerald-300'
                  }`}
                >
                  {likelyPendingNav ? 'Pending NAV' : '自动匹配官方净值'}
                </span>
                <span>
                  {likelyPendingNav
                    ? '若该日期官方收盘净值尚未公布，将先以 Pending NAV（待确认）保存，并于晚间 21:30 / 次日 09:00 或点击刷新净值后自动回填。'
                    : '系统将自动匹配并缓存该交易日官方单位净值；若当日尚未公布则自动标记为 Pending NAV。'}
                </span>
              </div>
            )}
          </div>

          {/* Notes */}
          <div>
            <label className="mb-1 block text-slate-300">买入理由 / 备注（可选）</label>
            <textarea
              rows={2}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="例如：主线回踩 MA20 企稳，首笔建仓..."
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-600 focus:border-amber-500 focus:outline-none"
            />
          </div>

          {error && (
            <div className="rounded-lg border border-rose-500/40 bg-rose-950/50 px-3 py-2 text-xs text-rose-300">
              {error}
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center justify-end gap-2 border-t border-slate-800 pt-3">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-slate-700 bg-slate-800 px-4 py-2 text-xs font-medium text-slate-300 hover:bg-slate-700"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500 px-4 py-2 text-xs font-semibold text-slate-950 shadow-sm hover:bg-amber-400 disabled:opacity-50"
            >
              {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {isAddingLot ? '保存交易批次' : '保存持仓记录'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

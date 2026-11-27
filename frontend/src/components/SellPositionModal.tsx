import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  Calendar,
  CheckCircle2,
  Clock,
  Loader2,
  ShieldAlert,
  X,
} from 'lucide-react'
import { api } from '../api/client'
import type { TrackedPositionRecord } from '../types'
import {
  getShanghaiNow,
  isWeekendDate,
  nextWeekday,
  prevWeekday,
  weekdayLabel,
} from '../utils/tradingDate'
import { GlossaryTooltip } from './GlossaryTooltip'

interface SellPositionModalProps {
  open: boolean
  position: TrackedPositionRecord | null
  onClose: () => void
  onSaved: (position: TrackedPositionRecord) => void
  onNavigateToGuide?: (anchorId: string) => void
}

type SellPreset = '25' | '50' | '100' | 'custom'

function diffCalendarDays(startIso: string, endIso: string): number {
  const s = Date.parse(`${startIso}T00:00:00Z`)
  const e = Date.parse(`${endIso}T00:00:00Z`)
  if (!Number.isFinite(s) || !Number.isFinite(e)) return 0
  return Math.max(Math.round((e - s) / 86_400_000), 0)
}

export function SellPositionModal({
  open,
  position,
  onClose,
  onSaved,
  onNavigateToGuide,
}: SellPositionModalProps) {
  const shNow = useMemo(() => getShanghaiNow(), [open])

  const [tradeDate, setTradeDate] = useState(shNow.dateStr)
  const [preset, setPreset] = useState<SellPreset>('100')
  const [customPctStr, setCustomPctStr] = useState('50')
  const [sharesStr, setSharesStr] = useState('')
  const [amountStr, setAmountStr] = useState('')
  const [feeStr, setFeeStr] = useState('')
  const [useManualNav, setUseManualNav] = useState(false)
  const [manualNavStr, setManualNavStr] = useState('')
  const [notes, setNotes] = useState('')

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open || !position) return
    const now = getShanghaiNow()
    setTradeDate(now.dateStr)
    setPreset('100')
    setCustomPctStr('50')
    setSharesStr('')
    setAmountStr('')
    setFeeStr('')
    setUseManualNav(false)
    setManualNavStr('')
    setNotes('')
    setSubmitting(false)
    setError(null)
  }, [open, position])

  // Recompute FIFO 7-day fee eligibility dynamically against the chosen sell date
  const dynamicFifo = useMemo(() => {
    if (!position) return null
    const remLots = position.fifo_fee_status.lots
    if (remLots.length === 0) {
      return {
        feeFreeRatio: 1,
        penalizedRatio: 0,
        allFeeFree: true,
        daysUntilAllFeeFree: 0,
        earliestAllFeeFreeDate: null as string | null,
      }
    }
    let freeRatio = 0
    let maxDaysLeft = 0
    let latestFreeDate: string | null = null
    for (const lot of remLots) {
      const held = diffCalendarDays(lot.trade_date, tradeDate)
      if (held >= 7) {
        freeRatio += lot.remaining_ratio
      } else {
        const left = 7 - held
        if (left > maxDaysLeft) {
          maxDaysLeft = left
        }
        if (!latestFreeDate || lot.fee_free_date > latestFreeDate) {
          latestFreeDate = lot.fee_free_date
        }
      }
    }
    const clampedFree = Math.min(Math.max(freeRatio, 0), 1)
    const clampedPenalized = Math.max(0, 1 - clampedFree)
    return {
      feeFreeRatio: clampedFree,
      penalizedRatio: clampedPenalized,
      allFeeFree: clampedPenalized <= 1e-4,
      daysUntilAllFeeFree: maxDaysLeft,
      earliestAllFeeFreeDate: latestFreeDate,
    }
  }, [position, tradeDate])

  if (!open || !position) return null

  const hasAmountData = position.has_amount_data
  const isWeekend = isWeekendDate(tradeDate)
  const isAfterCutoffToday =
    tradeDate === shNow.dateStr && shNow.isAfterCutoff1500 && !isWeekend
  const likelyPendingNav =
    !useManualNav &&
    (tradeDate >= shNow.dateStr ||
      (position.latest_nav_date !== null && tradeDate > position.latest_nav_date))

  const hasExplicitCapitalInput =
    hasAmountData && (sharesStr.trim() !== '' || amountStr.trim() !== '')

  const effectiveRatio: number | null = (() => {
    if (hasExplicitCapitalInput) return null
    if (preset === '25') return 0.25
    if (preset === '50') return 0.5
    if (preset === '100') return 1.0
    const parsed = Number(customPctStr)
    if (!Number.isFinite(parsed) || parsed <= 0 || parsed > 100) return null
    return parsed / 100
  })()

  const estimatedSoldShares =
    hasAmountData && position.remaining_shares !== null && effectiveRatio !== null
      ? position.remaining_shares * effectiveRatio
      : null

  const handleSelectPreset = (next: SellPreset) => {
    setPreset(next)
    setSharesStr('')
    setAmountStr('')
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    const fee = feeStr.trim() ? Number(feeStr) : 0
    if (!Number.isFinite(fee) || fee < 0) {
      setError('手续费必须为非负数字。')
      return
    }

    const manualNav =
      useManualNav && manualNavStr.trim() ? Number(manualNavStr) : undefined
    if (
      useManualNav &&
      (manualNav === undefined || !Number.isFinite(manualNav) || manualNav <= 0)
    ) {
      setError('启用手动指定净值时，请输入大于 0 的净值。')
      return
    }

    let sharesPayload: number | null = null
    let amountPayload: number | null = null
    let sellRatioPayload: number | null = null

    if (hasExplicitCapitalInput) {
      if (sharesStr.trim()) {
        const s = Number(sharesStr)
        if (!Number.isFinite(s) || s <= 0) {
          setError('卖出份额必须为大于 0 的数字。')
          return
        }
        sharesPayload = s
      }
      if (amountStr.trim()) {
        const a = Number(amountStr)
        if (!Number.isFinite(a) || a <= 0) {
          setError('卖出金额必须为大于 0 的数字。')
          return
        }
        amountPayload = a
      }
    } else {
      if (effectiveRatio === null || effectiveRatio <= 0 || effectiveRatio > 1) {
        setError('卖出比例必须在 1% 到 100% 之间。')
        return
      }
      sellRatioPayload = Number(effectiveRatio.toFixed(6))
    }

    setSubmitting(true)
    try {
      const res = await api.addTradeLot(position.id, {
        side: 'sell',
        trade_date: tradeDate,
        nav: manualNav ?? null,
        amount: amountPayload,
        shares: sharesPayload,
        sell_ratio: sellRatioPayload,
        fee,
        notes: notes.trim(),
      })
      onSaved(res.position)
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
              记录卖出 / 减仓 · {position.otc_name}
            </h2>
            <p className="mt-0.5 font-mono text-[11px] text-slate-400">
              {position.otc_code}
              {hasAmountData && position.remaining_shares !== null
                ? ` · 当前剩余 ${position.remaining_shares.toFixed(2)} 份`
                : ` · 纯收益率模式（当前剩余 ${(position.remaining_ratio * 100).toFixed(0)}%）`}
              {position.avg_cost_nav !== null &&
                ` · 加权成本 ${position.avg_cost_nav.toFixed(4)}`}
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
          {/* Live FIFO 7-Day Short-Term Redemption Fee Banner */}
          {dynamicFifo && (
            <div
              className={`rounded-xl border p-3 ${
                dynamicFifo.allFeeFree
                  ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
                  : 'border-amber-500/45 bg-amber-500/10 text-amber-200'
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 font-semibold">
                  {dynamicFifo.allFeeFree ? (
                    <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                  ) : (
                    <ShieldAlert className="h-4 w-4 text-amber-400" />
                  )}
                  <span>
                    {dynamicFifo.allFeeFree
                      ? `截至 ${tradeDate}：已满 7 自然日 · 全部免 1.5% 短赎费`
                      : dynamicFifo.feeFreeRatio > 0
                        ? `截至 ${tradeDate}：仅 ${(dynamicFifo.feeFreeRatio * 100).toFixed(0)}% 免罚费，剩余 ${(dynamicFifo.penalizedRatio * 100).toFixed(0)}% 未满 7 天`
                        : `截至 ${tradeDate}：未满 7 自然日 · 剩余 ${dynamicFifo.daysUntilAllFeeFree} 天（提前赎回收 1.5% 惩罚性赎回费）`}
                  </span>
                </div>
                <GlossaryTooltip
                  termId="short-term-fee"
                  onNavigateToGuide={onNavigateToGuide}
                />
              </div>
              {!dynamicFifo.allFeeFree && dynamicFifo.earliestAllFeeFreeDate && (
                <p className="mt-1 text-[11px] text-amber-200/80">
                  按 FIFO 先进先出匹配最早期买入份额；全部份额满 7 天免罚费日为{' '}
                  <span className="font-mono font-semibold text-amber-100">
                    {dynamicFifo.earliestAllFeeFreeDate}
                  </span>
                  。
                </p>
              )}
            </div>
          )}

          {/* Trade Date + 15:00 Cutoff + Weekend Alignment */}
          <div>
            <label className="mb-1.5 flex items-center justify-between font-medium text-slate-300">
              <span className="inline-flex items-center gap-1.5">
                <Calendar className="h-3.5 w-3.5 text-emerald-400" />
                赎回确认日期（默认今日 {shNow.dateStr}）
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
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs text-slate-100 focus:border-emerald-500 focus:outline-none"
            />

            {isAfterCutoffToday && (
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-200">
                <div className="flex items-center gap-1.5">
                  <Clock className="h-3.5 w-3.5 shrink-0 text-amber-400" />
                  <span>当前已过 15:00 截单时间，此刻赎回将按下一交易日净值确认。</span>
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

          {/* Quick Sell Ratio Presets */}
          <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="font-medium text-slate-300">
                {hasAmountData ? '快捷卖出比例（或下方直接输入份额/金额）' : '卖出比例（纯收益率模式，默认 100% 清仓）'}
              </span>
              {estimatedSoldShares !== null && (
                <span className="font-mono text-[11px] text-emerald-300">
                  约 {estimatedSoldShares.toFixed(2)} 份
                </span>
              )}
            </div>
            <div className="grid grid-cols-4 gap-2">
              {(
                [
                  { id: '25', label: '减仓 25%' },
                  { id: '50', label: '减仓 50%' },
                  { id: '100', label: '100% 清仓' },
                  { id: 'custom', label: '自定义 %' },
                ] as const
              ).map((item) => {
                const active = !hasExplicitCapitalInput && preset === item.id
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => handleSelectPreset(item.id)}
                    className={`rounded-lg border px-2.5 py-2 text-xs font-medium transition ${
                      active
                        ? 'border-emerald-500/60 bg-emerald-500/20 text-emerald-200'
                        : 'border-slate-700 bg-slate-900 text-slate-400 hover:border-slate-600 hover:text-slate-200'
                    }`}
                  >
                    {item.label}
                  </button>
                )
              })}
            </div>

            {!hasExplicitCapitalInput && preset === 'custom' && (
              <div className="mt-2.5 flex items-center gap-2">
                <label className="text-[11px] text-slate-400">自定义卖出剩余仓位比例：</label>
                <input
                  type="number"
                  min="1"
                  max="100"
                  step="1"
                  value={customPctStr}
                  onChange={(e) => setCustomPctStr(e.target.value)}
                  className="w-24 rounded-lg border border-emerald-500/40 bg-slate-900 px-2.5 py-1.5 text-right font-mono text-xs text-slate-100 focus:border-emerald-400 focus:outline-none"
                />
                <span className="font-mono text-slate-400">%</span>
              </div>
            )}

            {/* Capital mode explicit shares / amount inputs */}
            {hasAmountData && (
              <div className="mt-3 grid grid-cols-1 gap-3 border-t border-slate-800/80 pt-3 sm:grid-cols-3">
                <div>
                  <label className="mb-1 block text-slate-300">精确份额（份 · 可选）</label>
                  <input
                    type="number"
                    step="0.0001"
                    min="0"
                    value={sharesStr}
                    onChange={(e) => {
                      setSharesStr(e.target.value)
                      if (e.target.value.trim()) setAmountStr('')
                    }}
                    placeholder={
                      position.remaining_shares !== null
                        ? `最多 ${position.remaining_shares.toFixed(2)}`
                        : '留空按上方比例'
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 font-mono text-xs text-slate-100 placeholder:text-slate-600 focus:border-emerald-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-slate-300">到手金额（元 · 可选）</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    value={amountStr}
                    onChange={(e) => {
                      setAmountStr(e.target.value)
                      if (e.target.value.trim()) setSharesStr('')
                    }}
                    placeholder="留空按份额/比例"
                    className="w-full rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 font-mono text-xs text-slate-100 placeholder:text-slate-600 focus:border-emerald-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-slate-300">赎回费（元 · 可选）</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    value={feeStr}
                    onChange={(e) => setFeeStr(e.target.value)}
                    placeholder="0.00"
                    className="w-full rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 font-mono text-xs text-slate-100 placeholder:text-slate-600 focus:border-emerald-500 focus:outline-none"
                  />
                </div>
              </div>
            )}
          </div>

          {/* Manual NAV Override + Pending NAV Indicator */}
          <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
            <div className="flex items-center justify-between gap-2">
              <label className="flex cursor-pointer items-center gap-2 text-slate-200">
                <input
                  type="checkbox"
                  checked={useManualNav}
                  onChange={(e) => setUseManualNav(e.target.checked)}
                  className="rounded border-slate-600 bg-slate-900 text-emerald-500 focus:ring-emerald-500"
                />
                <span>手动指定赎回确认净值（不会被自动同步改写）</span>
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
                  placeholder="输入精确单位净值，例如 1.3200"
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
                    ? '若该日期官方收盘净值尚未公布，将先以 Pending NAV 保存，并在官方净值同步后自动回填与重算盈亏。'
                    : '系统将自动匹配该交易日官方单位净值；若清仓至 0 份将自动归档至「已清仓复盘」。'}
                </span>
              </div>
            )}
          </div>

          {/* Sell Reason Notes */}
          <div>
            <label className="mb-1 block text-slate-300">卖出理由 / 复盘心得（可选）</label>
            <textarea
              rows={2}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="例如：达到目标止盈 / 跌破 MA20 减仓锁利..."
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-600 focus:border-emerald-500 focus:outline-none"
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
              className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-500 px-4 py-2 text-xs font-semibold text-slate-950 shadow-sm hover:bg-emerald-400 disabled:opacity-50"
            >
              {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {effectiveRatio === 1.0 && !hasExplicitCapitalInput ? '确认清仓卖出' : '保存卖出批次'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

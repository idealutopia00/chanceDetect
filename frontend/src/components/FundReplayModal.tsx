import { useEffect, useRef, useState } from 'react'
import { createChart, ColorType, type IChartApi, type SeriesMarker, type Time } from 'lightweight-charts'
import { Check, Copy, Info, X } from 'lucide-react'
import { api } from '../api/client'
import type { FundReplayResponse, OTCMapping, ScanRequestConfig } from '../types'

interface FundReplayModalProps {
  symbol: string | null
  config: ScanRequestConfig
  onClose: () => void
}

export function FundReplayModal({ symbol, config, onClose }: FundReplayModalProps) {
  const [data, setData] = useState<FundReplayResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showMA, setShowMA] = useState({ ma5: true, ma20: true, ma60: true })
  const [otcTab, setOtcTab] = useState<'direct' | 'index'>('direct')
  const [copiedCode, setCopiedCode] = useState<string | null>(null)
  const chartContainerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!symbol) return
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .fundReplay(symbol, config, 500)
      .then((res) => {
        if (!cancelled) setData(res)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [symbol, config])

  useEffect(() => {
    if (!data || !chartContainerRef.current) return
    const container = chartContainerRef.current

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 360,
      layout: {
        background: { type: ColorType.Solid, color: '#020617' },
        textColor: '#94a3b8',
        fontFamily: 'JetBrains Mono, SF Mono, monospace',
      },
      grid: {
        vertLines: { color: '#1e293b' },
        horzLines: { color: '#1e293b' },
      },
      crosshair: {
        mode: 0,
      },
      rightPriceScale: {
        borderColor: '#334155',
      },
      timeScale: {
        borderColor: '#334155',
        timeVisible: false,
      },
    })
    chartRef.current = chart

    // Candlestick (A-share convention: red up, green down)
    const candleSeries = chart.addCandlestickSeries({
      upColor: '#ef4444',
      downColor: '#22c55e',
      borderUpColor: '#ef4444',
      borderDownColor: '#22c55e',
      wickUpColor: '#ef4444',
      wickDownColor: '#22c55e',
    })
    candleSeries.setData(
      data.bars.map((b) => ({
        time: b.time as Time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      })),
    )

    // Historical strategy trigger markers
    const markers: SeriesMarker<Time>[] = data.triggers.map((t) => ({
      time: t.date as Time,
      position: 'belowBar',
      color: '#f59e0b',
      shape: 'arrowUp',
      text: t.hit_count > 1 ? `★${t.hit_count}` : '★',
    }))
    candleSeries.setMarkers(markers)

    // Volume histogram on bottom overlay
    const volSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    })
    volSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    })
    volSeries.setData(
      data.bars.map((b) => ({
        time: b.time as Time,
        value: b.volume,
        color: b.close >= b.open ? 'rgba(239, 68, 68, 0.30)' : 'rgba(34, 197, 94, 0.30)',
      })),
    )

    // Optional MA lines
    const addMa = (key: 'ma5' | 'ma20' | 'ma60', color: string) => {
      if (!showMA[key]) return
      const line = chart.addLineSeries({
        color,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      })
      line.setData(
        data.bars
          .filter((b) => b[key] !== null)
          .map((b) => ({ time: b.time as Time, value: b[key] as number })),
      )
    }
    addMa('ma5', '#f59e0b')
    addMa('ma20', '#38bdf8')
    addMa('ma60', '#a855f7')

    chart.timeScale().fitContent()

    const observer = new ResizeObserver(() => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth })
      }
    })
    observer.observe(container)

    return () => {
      observer.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [data, showMA])

  if (!symbol) return null

  const handleCopy = async (code: string) => {
    try {
      await navigator.clipboard.writeText(code)
      setCopiedCode(code)
      setTimeout(() => setCopiedCode((c) => (c === code ? null : c)), 1500)
    } catch {
      // ignore
    }
  }

  const activeOtcs: OTCMapping[] =
    data ? (otcTab === 'direct' ? data.direct_otc_mappings : data.index_otc_mappings) : []

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-3 backdrop-blur-sm sm:p-6">
      <div className="flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-slate-700/80 bg-slate-900 shadow-2xl">
        {/* Modal Header */}
        <div className="flex items-center justify-between border-b border-slate-800 px-5 py-3.5">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-bold text-slate-100">
                {data?.fund.name || symbol}
              </h2>
              <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-xs text-amber-300">
                {data?.fund.code || symbol}
              </span>
              {data?.fund.index_name && (
                <span className="text-xs text-slate-400">
                  跟踪：{data.fund.index_name} ({data.fund.index_code})
                </span>
              )}
            </div>
            <p className="mt-0.5 text-[11px] text-slate-400">
              ★ 金色箭头标注当前策略组合在近 {data?.lookback_days ?? 500} 个交易日内的历史触发点（共{' '}
              <strong className="text-amber-300">{data?.trigger_count ?? 0}</strong> 次）
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Modal Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {loading && (
            <div className="flex h-64 items-center justify-center text-sm text-slate-400">
              正在加载历史 K 线与策略信号回看统计...
            </div>
          )}
          {error && (
            <div className="rounded-lg border border-rose-500/40 bg-rose-950/40 p-4 text-xs text-rose-300">
              加载失败：{error}
            </div>
          )}

          {data && !loading && (
            <>
              {/* K-line Chart + MA toggles */}
              <div className="rounded-xl border border-slate-800 bg-slate-950 p-3">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
                  <div className="flex items-center gap-3">
                    <label className="flex items-center gap-1.5 text-amber-400">
                      <input
                        type="checkbox"
                        checked={showMA.ma5}
                        onChange={(e) => setShowMA({ ...showMA, ma5: e.target.checked })}
                        className="accent-amber-500"
                      />
                      MA5
                    </label>
                    <label className="flex items-center gap-1.5 text-sky-400">
                      <input
                        type="checkbox"
                        checked={showMA.ma20}
                        onChange={(e) => setShowMA({ ...showMA, ma20: e.target.checked })}
                        className="accent-sky-500"
                      />
                      MA20
                    </label>
                    <label className="flex items-center gap-1.5 text-purple-400">
                      <input
                        type="checkbox"
                        checked={showMA.ma60}
                        onChange={(e) => setShowMA({ ...showMA, ma60: e.target.checked })}
                        className="accent-purple-500"
                      />
                      MA60
                    </label>
                  </div>
                  <span className="text-[11px] text-slate-500">滚轮缩放 · 拖拽平移</span>
                </div>
                <div ref={chartContainerRef} className="w-full" />
              </div>

              {/* Lightweight Forward Return Stats */}
              <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
                <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-xs font-semibold text-slate-200">
                      历史信号前瞻收益分布（轻量回看统计）
                    </h3>
                    <span className="rounded-md border border-amber-500/35 bg-amber-500/10 px-2 py-0.5 font-mono text-[10px] font-semibold text-amber-300">
                      {data.entry_basis === 'T+1_close'
                        ? '入场成本：T+1 收盘价'
                        : `入场成本：${data.entry_basis}`}
                    </span>
                  </div>
                  <span className="inline-flex items-center gap-1 text-[11px] text-slate-400">
                    <Info className="h-3.5 w-3.5 shrink-0 text-amber-400" />
                    盘后 T 日触发信号、次日 (T+1) 15:00 前申购按 T+1 收盘净值确认成本，统计 T+1 后持有 T+5 / T+10 / T+20 / T+40 个交易日（避开 7 日短赎费）表现
                  </span>
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full text-left font-mono text-xs">
                    <thead className="border-b border-slate-800 text-[11px] text-slate-400">
                      <tr>
                        <th className="py-2 pr-3">持有窗口 (自 T+1 起)</th>
                        <th className="py-2 px-3">有效样本</th>
                        <th className="py-2 px-3">胜率 (&gt;0)</th>
                        <th className="py-2 px-3">平均收益</th>
                        <th className="py-2 px-3">中位收益</th>
                        <th className="py-2 px-3">最大单次盈利</th>
                        <th className="py-2 px-3">最大单次亏损</th>
                        <th className="py-2 pl-3">期间均值回撤 (T+1 后)</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/60">
                      {data.forward_stats.map((s) => (
                        <tr key={s.horizon_days} className="text-slate-200">
                          <td className="py-2 pr-3 font-semibold text-amber-300">
                            T+{s.horizon_days} 日
                          </td>
                          <td className="py-2 px-3">{s.sample_count}</td>
                          <td
                            className={`py-2 px-3 font-semibold ${
                              s.win_rate_pct !== null && s.win_rate_pct >= 55
                                ? 'text-rose-400'
                                : 'text-slate-200'
                            }`}
                          >
                            {s.win_rate_pct !== null ? `${s.win_rate_pct.toFixed(1)}%` : '-'}
                          </td>
                          <td
                            className={`py-2 px-3 ${
                              (s.avg_return_pct ?? 0) > 0
                                ? 'text-rose-400'
                                : (s.avg_return_pct ?? 0) < 0
                                  ? 'text-emerald-400'
                                  : ''
                            }`}
                          >
                            {s.avg_return_pct !== null
                              ? `${s.avg_return_pct > 0 ? '+' : ''}${s.avg_return_pct.toFixed(2)}%`
                              : '-'}
                          </td>
                          <td className="py-2 px-3">
                            {s.median_return_pct !== null
                              ? `${s.median_return_pct > 0 ? '+' : ''}${s.median_return_pct.toFixed(2)}%`
                              : '-'}
                          </td>
                          <td className="py-2 px-3 text-rose-400">
                            {s.max_gain_pct !== null
                              ? `${s.max_gain_pct > 0 ? '+' : ''}${s.max_gain_pct.toFixed(2)}%`
                              : '-'}
                          </td>
                          <td className="py-2 px-3 text-emerald-400">
                            {s.max_loss_pct !== null
                              ? `${s.max_loss_pct > 0 ? '+' : ''}${s.max_loss_pct.toFixed(2)}%`
                              : '-'}
                          </td>
                          <td className="py-2 pl-3 text-slate-400">
                            {s.avg_max_drawdown_pct !== null
                              ? `${s.avg_max_drawdown_pct.toFixed(2)}%`
                              : '-'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* OTC Counterparts List */}
              <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
                <div className="mb-3 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setOtcTab('direct')}
                      className={`rounded-md px-2.5 py-1 text-xs font-medium ${
                        otcTab === 'direct'
                          ? 'bg-amber-500/20 text-amber-300 ring-1 ring-amber-500/40'
                          : 'text-slate-400 hover:bg-slate-800'
                      }`}
                    >
                      本基金直接对应场外 ({data.direct_otc_mappings.length})
                    </button>
                    <button
                      type="button"
                      onClick={() => setOtcTab('index')}
                      className={`rounded-md px-2.5 py-1 text-xs font-medium ${
                        otcTab === 'index'
                          ? 'bg-amber-500/20 text-amber-300 ring-1 ring-amber-500/40'
                          : 'text-slate-400 hover:bg-slate-800'
                      }`}
                    >
                      同跟踪指数全部场外 ({data.index_otc_mappings.length})
                    </button>
                  </div>
                </div>

                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {activeOtcs.map((otc) => (
                    <div
                      key={otc.otc_code}
                      className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900 px-3 py-2 text-xs"
                    >
                      <div className="flex min-w-0 items-center gap-2">
                        <span className="rounded border border-slate-700 bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] font-bold text-emerald-300">
                          {otc.share_class}
                        </span>
                        <button
                          type="button"
                          onClick={() => handleCopy(otc.otc_code)}
                          className="inline-flex items-center gap-1 font-mono font-bold text-amber-300 hover:text-amber-200"
                        >
                          {otc.otc_code}
                          {copiedCode === otc.otc_code ? (
                            <Check className="h-3 w-3 text-emerald-400" />
                          ) : (
                            <Copy className="h-3 w-3 opacity-60" />
                          )}
                        </button>
                        <span className="truncate text-slate-300">{otc.otc_name}</span>
                      </div>
                      <div className="ml-2 flex shrink-0 items-center gap-2 text-[11px]">
                        <span className="text-slate-400">{otc.purchase_status || '-'}</span>
                        <span className="font-mono text-slate-500">{otc.fee_rate}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Recent Triggers Log */}
              {data.triggers.length > 0 && (
                <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
                  <h3 className="mb-2 text-xs font-semibold text-slate-200">
                    最近触发记录 (展示最新 15 次)
                  </h3>
                  <div className="space-y-1.5 text-xs">
                    {[...data.triggers]
                      .reverse()
                      .slice(0, 15)
                      .map((t) => (
                        <div
                          key={t.date}
                          className="flex flex-wrap items-center justify-between gap-2 rounded border border-slate-800/80 bg-slate-900/70 px-3 py-1.5"
                        >
                          <div className="flex items-center gap-3">
                            <span className="font-mono text-amber-300">{t.date}</span>
                            <span className="font-mono text-slate-300">
                              收 {t.close.toFixed(3)}
                            </span>
                            <span className="text-slate-200">
                              {t.hit_strategies.join(' + ')}
                            </span>
                          </div>
                          <span className="text-[11px] text-slate-400">
                            {t.reasons.join(' · ')}
                          </span>
                        </div>
                      ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

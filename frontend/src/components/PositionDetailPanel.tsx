import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ColorType,
  createChart,
  LineStyle,
  type IChartApi,
  type SeriesMarker,
  type Time,
} from 'lightweight-charts'
import {
  AlertTriangle,
  CandlestickChart,
  CheckCircle2,
  Clock,
  Eye,
  LineChart,
  Loader2,
  RotateCcw,
  Save,
  ShieldAlert,
  SlidersHorizontal,
  Unlink,
} from 'lucide-react'
import { api } from '../api/client'
import type { ExitSignalItem, TrackedPositionRecord } from '../types'
import { GlossaryTooltip } from './GlossaryTooltip'

interface PositionDetailPanelProps {
  positionId: number
  refreshKey: number
  onPositionUpdated: () => void
  onNavigateToGuide?: (anchorId: string) => void
}

function formatPct(val: number | null | undefined): string {
  if (val === null || val === undefined) return '-'
  const sign = val > 0 ? '+' : ''
  return `${sign}${val.toFixed(2)}%`
}

function pctColor(val: number | null | undefined): string {
  if (val === null || val === undefined) return 'text-slate-400'
  if (val > 0) return 'text-rose-400'
  if (val < 0) return 'text-emerald-400'
  return 'text-slate-300'
}

function severityStyles(sig: ExitSignalItem): {
  card: string
  badge: string
  badgeText: string
} {
  if (sig.triggered && sig.severity === 'alert') {
    return {
      card: 'border-rose-500/45 bg-rose-950/25',
      badge: 'border-rose-500/50 bg-rose-500/20 text-rose-200',
      badgeText: '预警',
    }
  }
  if (sig.triggered && sig.severity === 'warning') {
    return {
      card: 'border-amber-500/40 bg-amber-950/20',
      badge: 'border-amber-500/50 bg-amber-500/20 text-amber-200',
      badgeText: '观察',
    }
  }
  return {
    card: 'border-slate-800 bg-slate-950/50',
    badge: 'border-emerald-500/35 bg-emerald-500/10 text-emerald-300',
    badgeText: '正常',
  }
}

export function PositionDetailPanel({
  positionId,
  refreshKey,
  onPositionUpdated,
  onNavigateToGuide,
}: PositionDetailPanelProps) {
  const [detail, setDetail] = useState<TrackedPositionRecord | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [chartMode, setChartMode] = useState<'dual' | 'kline'>('dual')
  const [showPostExit, setShowPostExit] = useState(true)
  const [postExitWindow, setPostExitWindow] = useState<20 | 40>(40)
  const [showMA, setShowMA] = useState({ ma5: true, ma10: false, ma20: true, ma60: true })

  const [editingThresholds, setEditingThresholds] = useState(false)
  const [stopLossInput, setStopLossInput] = useState('')
  const [drawdownInput, setDrawdownInput] = useState('')
  const [takeProfitInput, setTakeProfitInput] = useState('')
  const [savingThresholds, setSavingThresholds] = useState(false)

  const chartContainerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)

  const fetchDetail = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.getPosition(positionId, postExitWindow)
      const pos = res.position
      setDetail(pos)
      setStopLossInput(
        pos.custom_stop_loss_pct !== null ? String(Math.abs(pos.custom_stop_loss_pct)) : '',
      )
      setDrawdownInput(
        pos.custom_trailing_drawdown_pct !== null
          ? String(Math.abs(pos.custom_trailing_drawdown_pct))
          : '',
      )
      setTakeProfitInput(
        pos.custom_take_profit_pct !== null ? String(Math.abs(pos.custom_take_profit_pct)) : '',
      )
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [positionId, postExitWindow])

  useEffect(() => {
    fetchDetail()
  }, [fetchDetail, refreshKey])

  const dualCurve = detail?.dual_curve
  const proxyBars = detail?.proxy_bars ?? []

  const visiblePoints = useMemo(() => {
    if (!dualCurve) return []
    if (detail?.status === 'closed' && !showPostExit) {
      return dualCurve.points.filter((p) => !p.is_post_exit)
    }
    return dualCurve.points
  }, [dualCurve, detail?.status, showPostExit])

  // Render Lightweight Charts v4 (Dual Cumulative Return Curve OR Linked Proxy K-Line + MA)
  useEffect(() => {
    if (!detail || !chartContainerRef.current) return
    const container = chartContainerRef.current

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 310,
      layout: {
        background: { type: ColorType.Solid, color: '#020617' },
        textColor: '#94a3b8',
        fontFamily: 'JetBrains Mono, SF Mono, monospace',
      },
      grid: {
        vertLines: { color: '#1e293b' },
        horzLines: { color: '#1e293b' },
      },
      crosshair: { mode: 0 },
      rightPriceScale: { borderColor: '#334155' },
      timeScale: { borderColor: '#334155', timeVisible: false },
    })
    chartRef.current = chart

    if (chartMode === 'dual' && dualCurve) {
      const otcSeries = chart.addLineSeries({
        color: '#f59e0b',
        lineWidth: 2,
        title: `场外 ${detail.otc_code}`,
        priceFormat: {
          type: 'custom',
          formatter: (price: number) => `${price >= 0 ? '+' : ''}${price.toFixed(2)}%`,
        },
      })
      const otcData = visiblePoints
        .filter((p) => p.otc_return_pct !== null)
        .map((p) => ({ time: p.date as Time, value: p.otc_return_pct as number }))
      otcSeries.setData(otcData)

      // 0% baseline reference line
      otcSeries.createPriceLine({
        price: 0,
        color: '#475569',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: '建仓基准 0%',
      })

      // Weighted-average cost breakeven line (when distinct or meaningful)
      if (dualCurve.breakeven_return_pct !== null) {
        otcSeries.createPriceLine({
          price: dualCurve.breakeven_return_pct,
          color: '#a855f7',
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          axisLabelVisible: true,
          title: `加权成本 ${dualCurve.breakeven_return_pct >= 0 ? '+' : ''}${dualCurve.breakeven_return_pct.toFixed(2)}%`,
        })
      }

      // Trade lot markers on OTC curve
      const otcDateSet = new Set(otcData.map((d) => String(d.time)))
      const markers: SeriesMarker<Time>[] = dualCurve.markers
        .filter((m) => otcDateSet.has(m.date))
        .map((m) => {
          const isBuy = m.side === 'buy'
          return {
            time: m.date as Time,
            position: isBuy ? 'belowBar' : 'aboveBar',
            color:
              m.kind === 'buy'
                ? '#f43f5e'
                : m.kind === 'add'
                  ? '#fb7185'
                  : m.kind === 'full_sell'
                    ? '#10b981'
                    : '#34d399',
            shape: isBuy ? 'arrowUp' : 'arrowDown',
            text: m.label,
          }
        })
      if (markers.length > 0) {
        otcSeries.setMarkers(markers)
      }

      // Linked On-Exchange Proxy Fund cumulative return line
      if (dualCurve.has_proxy_curve) {
        const proxySeries = chart.addLineSeries({
          color: '#38bdf8',
          lineWidth: 2,
          title: `代理 ${detail.proxy_fund?.code ?? detail.exchange_symbol ?? ''}`,
          priceFormat: {
            type: 'custom',
            formatter: (price: number) => `${price >= 0 ? '+' : ''}${price.toFixed(2)}%`,
          },
        })
        const proxyData = visiblePoints
          .filter((p) => p.proxy_return_pct !== null)
          .map((p) => ({ time: p.date as Time, value: p.proxy_return_pct as number }))
        proxySeries.setData(proxyData)
      }
    } else if (chartMode === 'kline' && proxyBars.length > 0) {
      const candleSeries = chart.addCandlestickSeries({
        upColor: '#ef4444',
        downColor: '#22c55e',
        borderUpColor: '#ef4444',
        borderDownColor: '#22c55e',
        wickUpColor: '#ef4444',
        wickDownColor: '#22c55e',
      })
      const filteredBars =
        detail.status === 'closed' && !showPostExit && detail.closed_date
          ? proxyBars.filter((b) => b.time <= detail.closed_date!)
          : proxyBars
      candleSeries.setData(
        filteredBars.map((b) => ({
          time: b.time as Time,
          open: b.open,
          high: b.high,
          low: b.low,
          close: b.close,
        })),
      )

      // Overlay trade dates on proxy K-line
      const barDateSet = new Set(filteredBars.map((b) => b.time))
      if (dualCurve) {
        const kMarkers: SeriesMarker<Time>[] = dualCurve.markers
          .filter((m) => barDateSet.has(m.date))
          .map((m) => ({
            time: m.date as Time,
            position: m.side === 'buy' ? 'belowBar' : 'aboveBar',
            color: m.side === 'buy' ? '#f43f5e' : '#10b981',
            shape: m.side === 'buy' ? 'arrowUp' : 'arrowDown',
            text: m.label,
          }))
        if (kMarkers.length > 0) {
          candleSeries.setMarkers(kMarkers)
        }
      }

      const addMa = (key: 'ma5' | 'ma10' | 'ma20' | 'ma60', color: string) => {
        if (!showMA[key]) return
        const line = chart.addLineSeries({
          color,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        })
        line.setData(
          filteredBars
            .filter((b) => b[key] !== null)
            .map((b) => ({ time: b.time as Time, value: b[key] as number })),
        )
      }
      addMa('ma5', '#f59e0b')
      addMa('ma10', '#ec4899')
      addMa('ma20', '#38bdf8')
      addMa('ma60', '#a855f7')
    }

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
  }, [detail, dualCurve, visiblePoints, proxyBars, chartMode, showPostExit, showMA])

  const handleSaveThresholds = async (resetToDefaults = false) => {
    setSavingThresholds(true)
    setError(null)
    try {
      const payload = resetToDefaults
        ? {
            custom_stop_loss_pct: null,
            custom_trailing_drawdown_pct: null,
            custom_take_profit_pct: null,
          }
        : {
            custom_stop_loss_pct: stopLossInput.trim() ? -Math.abs(Number(stopLossInput)) : null,
            custom_trailing_drawdown_pct: drawdownInput.trim()
              ? -Math.abs(Number(drawdownInput))
              : null,
            custom_take_profit_pct: takeProfitInput.trim()
              ? Math.abs(Number(takeProfitInput))
              : null,
          }
      await api.updatePosition(positionId, payload)
      setEditingThresholds(false)
      await fetchDetail()
      onPositionUpdated()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSavingThresholds(false)
    }
  }

  if (loading && !detail) {
    return (
      <div className="flex items-center justify-center gap-2 border-t border-slate-800 bg-slate-950/70 py-10 text-xs text-slate-400">
        <Loader2 className="h-4 w-4 animate-spin text-amber-400" />
        正在加载双曲线复盘与离场风控分析...
      </div>
    )
  }

  if (error && !detail) {
    return (
      <div className="border-t border-slate-800 bg-rose-950/20 px-4 py-3 text-xs text-rose-300">
        加载详情失败：{error}
      </div>
    )
  }

  if (!detail || !dualCurve) return null

  const postExit = detail.post_exit_stats

  return (
    <div className="space-y-4 border-t border-slate-800 bg-slate-950/75 p-4">
      {error && (
        <div className="rounded-lg border border-rose-500/40 bg-rose-950/30 px-3 py-2 text-xs text-rose-300">
          {error}
        </div>
      )}

      {/* Pending Baseline Notice */}
      {dualCurve.is_pending_baseline && (
        <div className="flex items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-950/30 px-3 py-2 text-xs text-amber-200">
          <Clock className="h-4 w-4 shrink-0 text-amber-400" />
          <span>
            建仓日（{detail.opened_date}）官方净值尚未公布（Pending NAV），待晚间 21:30 或次早 09:00 同步回填后自动对齐 0% 基准起点。
          </span>
        </div>
      )}

      {/* Chart Header & Mode / Post-Exit Toggles */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-lg border border-slate-800 bg-slate-900 p-0.5 text-[11px]">
            <button
              type="button"
              onClick={() => setChartMode('dual')}
              className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 font-medium transition ${
                chartMode === 'dual'
                  ? 'bg-amber-500/20 text-amber-300'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <LineChart className="h-3.5 w-3.5" />
              双曲线收益率对比 (0% 归零)
            </button>
            <button
              type="button"
              disabled={proxyBars.length === 0}
              onClick={() => setChartMode('kline')}
              className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${
                chartMode === 'kline'
                  ? 'bg-sky-500/20 text-sky-300'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
              title={proxyBars.length === 0 ? '未关联场内代理基金或暂无代理日线' : '切换查看场内代理 K 线与均线'}
            >
              <CandlestickChart className="h-3.5 w-3.5" />
              场内代理 K 线 + 均线
            </button>
          </div>

          {!dualCurve.has_proxy_curve && (
            <span className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-900 px-2 py-0.5 text-[10px] text-slate-400">
              <Unlink className="h-3 w-3" />
              无关联场内代理 · 单曲线模式
            </span>
          )}
        </div>

        {/* Post-exit toggle for closed positions OR MA toggles for K-line */}
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          {detail.status === 'closed' && (
            <div className="flex items-center gap-1.5 rounded-lg border border-slate-800 bg-slate-900 px-2 py-1">
              <Eye className="h-3 w-3 text-purple-300" />
              <button
                type="button"
                onClick={() => setShowPostExit(false)}
                className={`rounded px-1.5 py-0.5 ${
                  !showPostExit ? 'bg-slate-700 text-slate-100' : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                仅看持仓期
              </button>
              <button
                type="button"
                onClick={() => {
                  setShowPostExit(true)
                  setPostExitWindow(20)
                }}
                className={`rounded px-1.5 py-0.5 ${
                  showPostExit && postExitWindow === 20
                    ? 'bg-purple-500/25 text-purple-200'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                含卖出后20日
              </button>
              <button
                type="button"
                onClick={() => {
                  setShowPostExit(true)
                  setPostExitWindow(40)
                }}
                className={`rounded px-1.5 py-0.5 ${
                  showPostExit && postExitWindow === 40
                    ? 'bg-purple-500/25 text-purple-200'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                含卖出后40日
              </button>
            </div>
          )}

          {chartMode === 'kline' && (
            <div className="flex items-center gap-2.5 rounded-lg border border-slate-800 bg-slate-900 px-2.5 py-1">
              {(
                [
                  ['ma5', 'MA5', 'text-amber-400'],
                  ['ma10', 'MA10', 'text-pink-400'],
                  ['ma20', 'MA20', 'text-sky-400'],
                  ['ma60', 'MA60', 'text-purple-400'],
                ] as const
              ).map(([key, label, cls]) => (
                <label key={key} className={`flex cursor-pointer items-center gap-1 ${cls}`}>
                  <input
                    type="checkbox"
                    checked={showMA[key]}
                    onChange={(e) => setShowMA({ ...showMA, [key]: e.target.checked })}
                    className="h-3 w-3 accent-amber-500"
                  />
                  {label}
                </label>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Chart Canvas & Legend */}
      <div className="rounded-xl border border-slate-800 bg-slate-950 p-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-400">
          {chartMode === 'dual' ? (
            <div className="flex flex-wrap items-center gap-3">
              <span className="inline-flex items-center gap-1.5">
                <span className="h-0.5 w-4 bg-amber-400" />
                场外复权累计收益 ({detail.otc_code})
              </span>
              {dualCurve.has_proxy_curve && (
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-0.5 w-4 bg-sky-400" />
                  场内代理前复权收益 ({detail.proxy_fund?.code ?? detail.exchange_symbol})
                </span>
              )}
              {dualCurve.breakeven_return_pct !== null && (
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-0.5 w-4 border-t border-dotted border-purple-400" />
                  加权成本线 ({formatPct(dualCurve.breakeven_return_pct)})
                </span>
              )}
            </div>
          ) : (
            <span>
              场内代理日 K 线（{detail.proxy_fund?.name ?? detail.exchange_symbol}）· 标注建仓/加仓/减仓/清仓交易日
            </span>
          )}

          <div className="flex flex-wrap items-center gap-3 font-mono text-[11px]">
            <span>
              持仓峰值{' '}
              <strong className={pctColor(dualCurve.peak_return_pct)}>
                {formatPct(dualCurve.peak_return_pct)}
              </strong>
            </span>
            <span>
              距峰值回撤{' '}
              <strong className={pctColor(dualCurve.current_drawdown_pct)}>
                {formatPct(dualCurve.current_drawdown_pct)}
              </strong>
            </span>
            {dualCurve.has_proxy_curve && (
              <span>
                跟踪偏离{' '}
                <strong className="text-slate-200">
                  {formatPct(dualCurve.latest_tracking_spread_pct)}
                </strong>
              </span>
            )}
          </div>
        </div>

        {visiblePoints.length === 0 && chartMode === 'dual' ? (
          <div className="flex h-[240px] items-center justify-center text-xs text-slate-500">
            暂无已发布的净值序列数据，请点击「立即刷新净值」或等待晚间净值同步。
          </div>
        ) : (
          <div ref={chartContainerRef} className="w-full" />
        )}
      </div>

      {/* Post-Exit Rearview Summary (Closed Positions) */}
      {detail.status === 'closed' && postExit && (
        <div className="rounded-xl border border-purple-500/30 bg-purple-950/15 p-3">
          <div className="mb-2 flex items-center justify-between text-xs">
            <span className="font-semibold text-purple-200">
              卖出后视镜复盘（清仓日 {postExit.closed_date} · 已追踪 {postExit.days_observed} 个交易日）
            </span>
            {postExit.post_exit_latest_return_pct !== null && (
              <span
                className={`rounded px-2 py-0.5 text-[10px] font-semibold ${
                  postExit.post_exit_latest_return_pct > 3
                    ? 'bg-amber-500/20 text-amber-200'
                    : postExit.post_exit_latest_return_pct < -3
                      ? 'bg-emerald-500/20 text-emerald-200'
                      : 'bg-slate-800 text-slate-300'
                }`}
              >
                {postExit.post_exit_latest_return_pct > 3
                  ? '卖出后继续上行（略有卖飞）'
                  : postExit.post_exit_latest_return_pct < -3
                    ? '成功避开后续回调（漂亮逃顶）'
                    : '卖出后区间震荡'}
              </span>
            )}
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div className="rounded-lg border border-slate-800/80 bg-slate-950/60 px-2.5 py-1.5">
              <div className="text-[10px] text-slate-400">卖出后至今涨跌</div>
              <div
                className={`mt-0.5 font-mono text-xs font-bold ${pctColor(postExit.post_exit_latest_return_pct)}`}
              >
                {formatPct(postExit.post_exit_latest_return_pct)}
              </div>
            </div>
            <div className="rounded-lg border border-slate-800/80 bg-slate-950/60 px-2.5 py-1.5">
              <div className="text-[10px] text-slate-400">卖出后20日 / 40日</div>
              <div className="mt-0.5 font-mono text-xs text-slate-200">
                <span className={pctColor(postExit.post_exit_return_20d_pct)}>
                  {formatPct(postExit.post_exit_return_20d_pct)}
                </span>
                <span className="mx-1 text-slate-600">/</span>
                <span className={pctColor(postExit.post_exit_return_40d_pct)}>
                  {formatPct(postExit.post_exit_return_40d_pct)}
                </span>
              </div>
            </div>
            <div className="rounded-lg border border-slate-800/80 bg-slate-950/60 px-2.5 py-1.5">
              <div className="text-[10px] text-slate-400">卖出后最高再涨</div>
              <div
                className={`mt-0.5 font-mono text-xs font-semibold ${pctColor(postExit.post_exit_max_gain_pct)}`}
              >
                {formatPct(postExit.post_exit_max_gain_pct)}
              </div>
            </div>
            <div className="rounded-lg border border-slate-800/80 bg-slate-950/60 px-2.5 py-1.5">
              <div className="text-[10px] text-slate-400">卖出后最大回落</div>
              <div
                className={`mt-0.5 font-mono text-xs font-semibold ${pctColor(postExit.post_exit_max_drop_pct)}`}
              >
                {formatPct(postExit.post_exit_max_drop_pct)}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Explainable Exit Signals & Custom Threshold Editor (Active Positions) */}
      {detail.status === 'active' && (
        <div className="space-y-2.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-200">
              <ShieldAlert className="h-4 w-4 text-amber-400" />
              <span>可解释离场风控信号（Exit Signals）</span>
              <GlossaryTooltip termId="exit-signals" onNavigateToGuide={onNavigateToGuide} />
            </div>

            <button
              type="button"
              onClick={() => setEditingThresholds((v) => !v)}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1 text-[11px] font-medium text-slate-300 hover:border-amber-500/40 hover:text-amber-200"
            >
              <SlidersHorizontal className="h-3 w-3" />
              {editingThresholds ? '收起阈值设置' : '自定义止盈 / 回撤 / 止损阈值'}
            </button>
          </div>

          {editingThresholds && (
            <div className="rounded-xl border border-amber-500/30 bg-slate-900/90 p-3">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <label className="block text-[11px] text-slate-300">
                  <span>移动回撤预警线（默认 6%）</span>
                  <div className="mt-1 flex items-center gap-1">
                    <span className="font-mono text-xs text-slate-400">-</span>
                    <input
                      type="number"
                      step="0.5"
                      min="0.5"
                      max="99"
                      value={drawdownInput}
                      onChange={(e) => setDrawdownInput(e.target.value)}
                      placeholder="6.0"
                      className="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-slate-100 focus:border-amber-500 focus:outline-none"
                    />
                    <span className="font-mono text-xs text-slate-400">%</span>
                  </div>
                </label>
                <label className="block text-[11px] text-slate-300">
                  <span>成本止损线（默认 8%）</span>
                  <div className="mt-1 flex items-center gap-1">
                    <span className="font-mono text-xs text-slate-400">-</span>
                    <input
                      type="number"
                      step="0.5"
                      min="0.5"
                      max="99"
                      value={stopLossInput}
                      onChange={(e) => setStopLossInput(e.target.value)}
                      placeholder="8.0"
                      className="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-slate-100 focus:border-amber-500 focus:outline-none"
                    />
                    <span className="font-mono text-xs text-slate-400">%</span>
                  </div>
                </label>
                <label className="block text-[11px] text-slate-300">
                  <span>自定义目标止盈（留空不启用）</span>
                  <div className="mt-1 flex items-center gap-1">
                    <span className="font-mono text-xs text-slate-400">+</span>
                    <input
                      type="number"
                      step="1"
                      min="0.5"
                      max="1000"
                      value={takeProfitInput}
                      onChange={(e) => setTakeProfitInput(e.target.value)}
                      placeholder="如 15.0"
                      className="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-slate-100 focus:border-amber-500 focus:outline-none"
                    />
                    <span className="font-mono text-xs text-slate-400">%</span>
                  </div>
                </label>
              </div>
              <div className="mt-3 flex items-center justify-end gap-2">
                <button
                  type="button"
                  disabled={savingThresholds}
                  onClick={() => handleSaveThresholds(true)}
                  className="inline-flex items-center gap-1 rounded-lg border border-slate-700 px-2.5 py-1 text-[11px] text-slate-300 hover:bg-slate-800"
                >
                  <RotateCcw className="h-3 w-3" />
                  恢复系统默认
                </button>
                <button
                  type="button"
                  disabled={savingThresholds}
                  onClick={() => handleSaveThresholds(false)}
                  className="inline-flex items-center gap-1 rounded-lg bg-amber-500 px-3 py-1 text-[11px] font-semibold text-slate-950 hover:bg-amber-400 disabled:opacity-60"
                >
                  <Save className="h-3 w-3" />
                  保存并重算信号
                </button>
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3">
            {detail.exit_signals.map((sig) => {
              const st = severityStyles(sig)
              return (
                <div key={sig.rule_id} className={`rounded-xl border p-3 ${st.card}`}>
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-100">
                      {sig.triggered && sig.severity === 'alert' ? (
                        <AlertTriangle className="h-3.5 w-3.5 text-rose-400" />
                      ) : sig.triggered ? (
                        <ShieldAlert className="h-3.5 w-3.5 text-amber-400" />
                      ) : (
                        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
                      )}
                      <span>{sig.title}</span>
                    </div>
                    <span
                      className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold ${st.badge}`}
                    >
                      {st.badgeText}
                    </span>
                  </div>
                  <p className="mt-1.5 text-[11px] leading-relaxed text-slate-300">{sig.message}</p>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

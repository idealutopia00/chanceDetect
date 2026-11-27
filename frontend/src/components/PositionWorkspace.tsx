import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  ArrowDownRight,
  Briefcase,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock,
  Edit3,
  LineChart,
  Link2,
  Loader2,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldAlert,
  Sparkles,
  Trash2,
  TrendingUp,
  Unlink,
  X,
} from 'lucide-react'
import { api } from '../api/client'
import type {
  NavStatus,
  PositionsSummary,
  SyncNavResponse,
  TrackedPositionRecord,
  TradeLotRecord,
} from '../types'
import { GlossaryTooltip } from './GlossaryTooltip'
import { PositionDetailPanel } from './PositionDetailPanel'

interface PositionWorkspaceProps {
  refreshKey: number
  onOpenNewBuy: () => void
  onOpenAddBuy: (position: TrackedPositionRecord) => void
  onOpenSell: (position: TrackedPositionRecord) => void
  onOpenReentryBuy: (position: TrackedPositionRecord) => void
  onSelectFund: (symbol: string) => void
  onNavigateToGuide?: (anchorId: string) => void
  onSummaryChange?: (summary: PositionsSummary) => void
}

const EMPTY_SUMMARY: PositionsSummary = {
  active_count: 0,
  closed_count: 0,
  pending_nav_count: 0,
  exit_alert_count: 0,
  exit_warning_count: 0,
  exit_alert_position_count: 0,
  closed_win_count: 0,
  closed_evaluated_count: 0,
  closed_win_rate_pct: null,
  closed_avg_return_pct: null,
  amount_position_count: 0,
  active_amount_position_count: 0,
  total_invested_capital: null,
  total_unrealized_pnl: null,
  total_realized_pnl: null,
  total_pnl: null,
}

function navStatusBadge(status: NavStatus) {
  if (status === 'confirmed') {
    return (
      <span className="rounded border border-emerald-500/40 bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">
        已确认
      </span>
    )
  }
  if (status === 'manual') {
    return (
      <span className="rounded border border-sky-500/40 bg-sky-500/15 px-1.5 py-0.5 text-[10px] font-medium text-sky-300">
        手动指定
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded border border-amber-500/50 bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">
      <Clock className="h-2.5 w-2.5" />
      Pending NAV
    </span>
  )
}

function formatPct(val: number | null | undefined): string {
  if (val === null || val === undefined) return '-'
  const sign = val > 0 ? '+' : ''
  return `${sign}${val.toFixed(2)}%`
}

function formatMoney(val: number | null | undefined, signed = false): string {
  if (val === null || val === undefined) return '-'
  const sign = signed && val > 0 ? '+' : val < 0 ? '-' : ''
  return `${sign}¥${Math.abs(val).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

function pctColor(val: number | null | undefined): string {
  if (val === null || val === undefined) return 'text-slate-400'
  if (val > 0) return 'text-rose-400'
  if (val < 0) return 'text-emerald-400'
  return 'text-slate-300'
}

interface LotEditState {
  positionId: number
  lotId: number
  side: 'buy' | 'sell'
  tradeDate: string
  navMode: 'auto' | 'manual'
  navStr: string
  amountStr: string
  sharesStr: string
  origAmountStr: string
  origSharesStr: string
  sellRatioPctStr: string
  feeStr: string
  notes: string
}

export function PositionWorkspace({
  refreshKey,
  onOpenNewBuy,
  onOpenAddBuy,
  onOpenSell,
  onOpenReentryBuy,
  onSelectFund,
  onNavigateToGuide,
  onSummaryChange,
}: PositionWorkspaceProps) {
  const [statusFilter, setStatusFilter] = useState<'active' | 'closed' | 'all'>('active')
  const [positions, setPositions] = useState<TrackedPositionRecord[]>([])
  const [summary, setSummary] = useState<PositionsSummary>(EMPTY_SUMMARY)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [syncing, setSyncing] = useState(false)
  const [syncNotice, setSyncNotice] = useState<SyncNavResponse | null>(null)

  const [editingNotesPosId, setEditingNotesPosId] = useState<number | null>(null)
  const [notesDraft, setNotesDraft] = useState('')
  const [savingNotes, setSavingNotes] = useState(false)

  const [lotEdit, setLotEdit] = useState<LotEditState | null>(null)
  const [savingLot, setSavingLot] = useState(false)
  const [expandedDetailId, setExpandedDetailId] = useState<number | null>(null)
  const [detailRefreshTick, setDetailRefreshTick] = useState(0)

  const loadPositions = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.listPositions(statusFilter)
      const nextSummary = res.portfolio_summary ?? res.summary
      setPositions(res.positions)
      setSummary(nextSummary)
      onSummaryChange?.(nextSummary)
      setDetailRefreshTick((t) => t + 1)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [statusFilter, onSummaryChange])

  useEffect(() => {
    loadPositions()
  }, [loadPositions, refreshKey])

  const handleSyncNav = async () => {
    setSyncing(true)
    setError(null)
    try {
      const res = await api.syncPositionNavs()
      setSyncNotice(res)
      await loadPositions()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSyncing(false)
    }
  }

  const handleDeletePosition = async (pos: TrackedPositionRecord) => {
    if (!window.confirm(`确认删除持仓「${pos.otc_name} (${pos.otc_code})」及其全部交易批次吗？`)) {
      return
    }
    try {
      await api.deletePosition(pos.id)
      await loadPositions()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const handleSaveNotes = async (positionId: number) => {
    setSavingNotes(true)
    try {
      await api.updatePosition(positionId, { notes: notesDraft })
      setEditingNotesPosId(null)
      await loadPositions()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSavingNotes(false)
    }
  }

  const startEditLot = (lot: TradeLotRecord) => {
    const amountInit = lot.amount !== null ? String(lot.amount) : ''
    const sharesInit = lot.shares !== null ? String(lot.shares) : ''
    setLotEdit({
      positionId: lot.position_id,
      lotId: lot.id,
      side: lot.side,
      tradeDate: lot.trade_date,
      navMode: lot.nav_status === 'manual' ? 'manual' : 'auto',
      navStr: lot.nav !== null ? String(lot.nav) : '',
      amountStr: amountInit,
      sharesStr: sharesInit,
      origAmountStr: amountInit,
      origSharesStr: sharesInit,
      sellRatioPctStr:
        lot.sell_ratio !== null ? String(Math.round(lot.sell_ratio * 10000) / 100) : '',
      feeStr: String(lot.fee ?? 0),
      notes: lot.notes ?? '',
    })
  }

  const handleSaveLot = async () => {
    if (!lotEdit) return
    setSavingLot(true)
    setError(null)
    try {
      const navPayload =
        lotEdit.navMode === 'manual'
          ? lotEdit.navStr.trim()
            ? Number(lotEdit.navStr)
            : null
          : null

      const sellRatioPayload =
        lotEdit.side === 'sell' && lotEdit.sellRatioPctStr.trim()
          ? Number(lotEdit.sellRatioPctStr) / 100
          : null

      const amountChanged = lotEdit.amountStr.trim() !== lotEdit.origAmountStr.trim()
      const sharesChanged = lotEdit.sharesStr.trim() !== lotEdit.origSharesStr.trim()

      const payload: Parameters<typeof api.updateTradeLot>[2] = {
        trade_date: lotEdit.tradeDate,
        nav: navPayload,
        sell_ratio: sellRatioPayload,
        fee: lotEdit.feeStr.trim() ? Number(lotEdit.feeStr) : 0,
        notes: lotEdit.notes.trim(),
      }
      // Only send changed capital fields so backend re-derives the counterpart cleanly
      if (amountChanged) {
        payload.amount = lotEdit.amountStr.trim() ? Number(lotEdit.amountStr) : null
      }
      if (sharesChanged) {
        payload.shares = lotEdit.sharesStr.trim() ? Number(lotEdit.sharesStr) : null
      }

      await api.updateTradeLot(lotEdit.positionId, lotEdit.lotId, payload)
      setLotEdit(null)
      await loadPositions()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSavingLot(false)
    }
  }

  const handleDeleteLot = async (lot: TradeLotRecord) => {
    if (!window.confirm(`确认删除 ${lot.trade_date} 的这笔交易记录吗？`)) {
      return
    }
    try {
      await api.deleteTradeLot(lot.position_id, lot.id)
      await loadPositions()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="space-y-4">
      {/* Top Portfolio & Review Summary Strip */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/75 p-4">
        <div
          className={`grid grid-cols-2 gap-3 ${
            summary.amount_position_count > 0 ? 'lg:grid-cols-5' : 'lg:grid-cols-3'
          }`}
        >
          {/* Active, Exit Alerts & Pending NAV */}
          <div className="rounded-lg border border-slate-800/90 bg-slate-950/60 px-3.5 py-2.5">
            <div className="flex items-center justify-between text-[11px] text-slate-400">
              <span>活跃持仓 / 预警 / 待确认</span>
              <div className="flex items-center gap-1">
                <GlossaryTooltip termId="exit-signals" onNavigateToGuide={onNavigateToGuide} />
                <GlossaryTooltip termId="pending-nav" onNavigateToGuide={onNavigateToGuide} />
              </div>
            </div>
            <div className="mt-1 flex flex-wrap items-baseline gap-2">
              <span className="font-mono text-lg font-bold text-slate-100">
                {summary.active_count}
              </span>
              <span className="text-[11px] text-slate-400">只持仓中</span>
              {(summary.exit_alert_count ?? 0) > 0 && (
                <span className="inline-flex items-center gap-1 rounded bg-rose-500/20 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-rose-200">
                  <AlertTriangle className="h-2.5 w-2.5 text-rose-400" />
                  {summary.exit_alert_count} 预警
                </span>
              )}
              {summary.pending_nav_count > 0 && (
                <span className="inline-flex items-center gap-1 rounded bg-amber-500/15 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-amber-300">
                  <Clock className="h-2.5 w-2.5" />
                  {summary.pending_nav_count} 待确认
                </span>
              )}
            </div>
          </div>

          {/* Closed Win Rate */}
          <div className="rounded-lg border border-slate-800/90 bg-slate-950/60 px-3.5 py-2.5">
            <div className="text-[11px] text-slate-400">已清仓胜率</div>
            <div className="mt-1 flex items-baseline gap-2">
              <span className="font-mono text-lg font-bold text-amber-300">
                {summary.closed_win_rate_pct !== null
                  ? `${summary.closed_win_rate_pct.toFixed(1)}%`
                  : '-'}
              </span>
              <span className="text-[11px] text-slate-400">
                {summary.closed_evaluated_count > 0
                  ? `${summary.closed_win_count}/${summary.closed_evaluated_count} 胜`
                  : `${summary.closed_count} 笔已清仓`}
              </span>
            </div>
          </div>

          {/* Closed Average Wave Return */}
          <div className="rounded-lg border border-slate-800/90 bg-slate-950/60 px-3.5 py-2.5">
            <div className="text-[11px] text-slate-400">已清仓平均波段收益</div>
            <div className="mt-1 flex items-baseline gap-2">
              <span
                className={`font-mono text-lg font-bold ${pctColor(summary.closed_avg_return_pct)}`}
              >
                {formatPct(summary.closed_avg_return_pct)}
              </span>
              <span className="text-[11px] text-slate-500">含纯收益率复盘</span>
            </div>
          </div>

          {/* Monetary Totals — strictly for amount-recorded positions */}
          {summary.amount_position_count > 0 && (
            <>
              <div className="rounded-lg border border-slate-800/90 bg-slate-950/60 px-3.5 py-2.5">
                <div className="text-[11px] text-slate-400">在途总本金</div>
                <div className="mt-1 flex items-baseline gap-2">
                  <span className="font-mono text-lg font-bold text-slate-100">
                    {formatMoney(summary.total_invested_capital)}
                  </span>
                </div>
                <div className="mt-0.5 text-[10px] text-slate-500">
                  含金额持仓 {summary.active_amount_position_count} 只在途
                </div>
              </div>

              <div className="rounded-lg border border-slate-800/90 bg-slate-950/60 px-3.5 py-2.5">
                <div className="text-[11px] text-slate-400">总浮盈 / 已实现盈亏</div>
                <div className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 font-mono">
                  <span
                    className={`text-base font-bold ${pctColor(summary.total_unrealized_pnl)}`}
                  >
                    {formatMoney(summary.total_unrealized_pnl, true)}
                  </span>
                  <span className="text-slate-600">/</span>
                  <span
                    className={`text-sm font-semibold ${pctColor(summary.total_realized_pnl)}`}
                  >
                    {formatMoney(summary.total_realized_pnl, true)}
                  </span>
                </div>
                <div className="mt-0.5 font-mono text-[10px] text-slate-400">
                  累计总盈亏{' '}
                  <span className={`font-semibold ${pctColor(summary.total_pnl)}`}>
                    {formatMoney(summary.total_pnl, true)}
                  </span>
                </div>
              </div>
            </>
          )}
        </div>

        {/* Subtle Scope Footnote */}
        {summary.amount_position_count > 0 ? (
          <p className="mt-2.5 text-[11px] text-slate-500">
            注：人民币本金与盈亏汇总仅统计录入了金额或份额的{' '}
            <span className="font-mono text-slate-300">{summary.amount_position_count}</span>{' '}
            笔持仓；未录入金额的纯收益率持仓仅计入胜率与平均波段收益率，不污染资金汇总。
          </p>
        ) : (
          summary.active_count + summary.closed_count > 0 && (
            <p className="mt-2.5 text-[11px] text-slate-500">
              当前持仓均为纯收益率模式（未录金额/份额）；在买入批次中填写金额或份额即可开启人民币成本与盈亏统计。
            </p>
          )
        )}
      </div>

      {/* Filter Tabs & Top Action Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-900/75 px-4 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-1 rounded-lg border border-slate-800 bg-slate-950/70 p-1 text-xs">
            {(
              [
                { id: 'active', label: `持仓中 (${summary.active_count})` },
                { id: 'closed', label: `已清仓复盘 (${summary.closed_count})` },
                {
                  id: 'all',
                  label: `全部 (${summary.active_count + summary.closed_count})`,
                },
              ] as const
            ).map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setStatusFilter(tab.id)}
                className={`rounded-md px-3 py-1.5 font-medium transition ${
                  statusFilter === tab.id
                    ? 'bg-amber-500/20 text-amber-300 ring-1 ring-amber-500/30'
                    : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {summary.pending_nav_count > 0 && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/40 bg-amber-500/10 px-2.5 py-1 text-xs text-amber-300">
              <Clock className="h-3.5 w-3.5" />
              {summary.pending_nav_count} 笔待确认净值（Pending NAV）
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleSyncNav}
            disabled={syncing}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs font-medium text-slate-200 transition hover:border-amber-500/50 hover:bg-slate-700 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 text-amber-400 ${syncing ? 'animate-spin' : ''}`} />
            立即刷新净值
          </button>
          <button
            type="button"
            onClick={onOpenNewBuy}
            className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500 px-3.5 py-2 text-xs font-semibold text-slate-950 shadow-sm transition hover:bg-amber-400"
          >
            <Plus className="h-3.5 w-3.5" />
            记录买入
          </button>
        </div>
      </div>

      {/* Sync Result Feedback */}
      {syncNotice && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-emerald-500/35 bg-emerald-950/30 px-4 py-2.5 text-xs text-emerald-200">
          <div className="flex items-center gap-2">
            <Check className="h-4 w-4 text-emerald-400" />
            <span>
              净值同步完成：已刷新 <strong>{syncNotice.refreshed_funds}</strong> 只持仓基金，回填{' '}
              <strong>{syncNotice.backfilled_lots}</strong> 笔 Pending NAV 批次
              {syncNotice.errors.length > 0 && (
                <span className="ml-2 text-rose-300">
                  （{syncNotice.errors.length} 只拉取失败）
                </span>
              )}
            </span>
          </div>
          <button
            type="button"
            onClick={() => setSyncNotice(null)}
            className="text-slate-400 hover:text-slate-200"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {error && (
        <div className="flex items-center justify-between rounded-xl border border-rose-500/40 bg-rose-950/30 px-4 py-3 text-xs text-rose-300">
          <span>操作失败：{error}</span>
          <button
            type="button"
            onClick={() => setError(null)}
            className="text-rose-400 hover:text-rose-200"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* Positions List */}
      {loading && positions.length === 0 ? (
        <div className="flex h-52 items-center justify-center rounded-xl border border-slate-800 bg-slate-900/50 text-xs text-slate-400">
          <Loader2 className="mr-2 h-4 w-4 animate-spin text-amber-400" />
          正在加载场外持仓台账...
        </div>
      ) : positions.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-slate-800 bg-slate-900/40 p-12 text-center">
          <Briefcase className="h-9 w-9 text-amber-400/70" />
          <div>
            <p className="text-sm font-medium text-slate-200">
              {statusFilter === 'closed' ? '暂无已清仓的历史复盘记录' : '暂无场外持仓记录'}
            </p>
            <p className="mt-1 text-xs text-slate-400">
              点击右上角「+ 记录买入」，或在「机会筛选工作台」任意场外基金右侧一键记录买入。
            </p>
          </div>
          <button
            type="button"
            onClick={onOpenNewBuy}
            className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500 px-4 py-2 text-xs font-semibold text-slate-950 hover:bg-amber-400"
          >
            <Plus className="h-3.5 w-3.5" />
            记录第一笔场外买入
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          {positions.map((pos) => {
            const fifo = pos.fifo_fee_status
            const primaryReturnPct =
              pos.status === 'closed'
                ? pos.total_return_pct
                : pos.unrealized_return_pct ?? pos.total_return_pct

            return (
              <div
                key={pos.id}
                className="overflow-hidden rounded-xl border border-slate-800/90 bg-slate-900/75"
              >
                {/* Position Header */}
                <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-800/80 p-4">
                  <div className="min-w-0 flex-1 space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded bg-amber-500/15 px-1.5 py-0.5 font-mono text-xs font-bold text-amber-300">
                        {pos.otc_code}
                      </span>
                      <h3 className="text-sm font-bold text-slate-100">{pos.otc_name}</h3>
                      <span
                        className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${
                          pos.status === 'active'
                            ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300'
                            : 'border-slate-700 bg-slate-800 text-slate-300'
                        }`}
                      >
                        {pos.status === 'active' ? '持仓中' : '已清仓'}
                      </span>
                      {!pos.has_amount_data && (
                        <span className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 text-[10px] text-slate-400">
                          纯收益率模式
                        </span>
                      )}
                      {pos.has_pending_nav && (
                        <span className="inline-flex items-center gap-1 rounded border border-amber-500/50 bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold text-amber-300">
                          <Clock className="h-3 w-3" />
                          Pending NAV · {pos.pending_lot_count} 笔待确认
                          <GlossaryTooltip
                            termId="pending-nav"
                            onNavigateToGuide={onNavigateToGuide}
                            iconClassName="h-3 w-3"
                          />
                        </span>
                      )}
                      {pos.status === 'active' &&
                        (pos.exit_signals ?? [])
                          .filter((s) => s.triggered && s.rule_id !== 'short_term_fee_guard')
                          .map((sig) => (
                            <span
                              key={sig.rule_id}
                              title={sig.message}
                              className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold ${
                                sig.severity === 'alert'
                                  ? 'border-rose-500/55 bg-rose-500/20 text-rose-200'
                                  : 'border-amber-500/45 bg-amber-500/15 text-amber-200'
                              }`}
                            >
                              <AlertTriangle className="h-2.5 w-2.5" />
                              {sig.title}
                            </span>
                          ))}
                    </div>

                    {/* Lifecycle Date Span, Latest NAV & Proxy */}
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
                      <span className="font-mono">
                        {pos.opened_date}
                        {pos.closed_date ? (
                          <>
                            {' '}
                            → <strong className="text-slate-200">{pos.closed_date}</strong>
                          </>
                        ) : (
                          ' 建仓'
                        )}
                        <span className="ml-1.5 text-slate-500">· 持有 {pos.holding_days} 天</span>
                      </span>
                      {pos.latest_nav !== null ? (
                        <span className="font-mono">
                          最新净值{' '}
                          <strong className="text-slate-100">{pos.latest_nav.toFixed(4)}</strong>{' '}
                          <span className="text-slate-500">({pos.latest_nav_date})</span>{' '}
                          <span className={pctColor(pos.latest_daily_return_pct)}>
                            {formatPct(pos.latest_daily_return_pct)}
                          </span>
                        </span>
                      ) : (
                        <span className="text-slate-500">暂无缓存净值，点击上方「立即刷新净值」</span>
                      )}

                      {pos.proxy_fund ? (
                        <button
                          type="button"
                          onClick={() => onSelectFund(pos.proxy_fund!.symbol)}
                          className="inline-flex items-center gap-1 rounded border border-slate-700/90 bg-slate-950/70 px-2 py-0.5 text-[11px] text-slate-200 hover:border-amber-500/50 hover:text-amber-300"
                        >
                          <Link2 className="h-3 w-3 text-amber-400" />
                          场内代理：{pos.proxy_fund.code} {pos.proxy_fund.name}
                          {pos.latest_proxy_pct_chg !== null && (
                            <span className={`ml-1 font-mono ${pctColor(pos.latest_proxy_pct_chg)}`}>
                              {formatPct(pos.latest_proxy_pct_chg)}
                            </span>
                          )}
                          <LineChart className="ml-0.5 h-3 w-3 text-slate-400" />
                        </button>
                      ) : pos.exchange_symbol ? (
                        <span className="inline-flex items-center gap-1 font-mono text-[11px] text-slate-400">
                          <Link2 className="h-3 w-3" />
                          代理 {pos.exchange_symbol}
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-[11px] text-slate-500">
                          <Unlink className="h-3 w-3" />
                          单曲线跟踪（未关联场内代理）
                        </span>
                      )}
                    </div>

                    {/* Accounting Metrics Bar */}
                    <div className="grid grid-cols-2 gap-2 pt-1 sm:grid-cols-4 lg:grid-cols-5">
                      <div className="rounded-lg border border-slate-800/80 bg-slate-950/60 px-2.5 py-1.5">
                        <div className="text-[10px] text-slate-400">加权成本净值</div>
                        <div className="mt-0.5 font-mono text-xs font-semibold text-slate-100">
                          {pos.avg_cost_nav !== null ? pos.avg_cost_nav.toFixed(4) : '待确认'}
                        </div>
                      </div>

                      <div className="rounded-lg border border-slate-800/80 bg-slate-950/60 px-2.5 py-1.5">
                        <div className="text-[10px] text-slate-400">
                          {pos.status === 'closed' ? '波段总收益率' : '持仓收益率'}
                        </div>
                        <div
                          className={`mt-0.5 font-mono text-xs font-bold ${pctColor(primaryReturnPct)}`}
                        >
                          {formatPct(primaryReturnPct)}
                          {pos.status === 'active' &&
                            pos.realized_return_pct !== null &&
                            pos.total_return_pct !== null && (
                              <span className="ml-1.5 text-[10px] font-normal text-slate-400">
                                总 {formatPct(pos.total_return_pct)}
                              </span>
                            )}
                        </div>
                      </div>

                      {pos.has_amount_data ? (
                        <>
                          <div className="rounded-lg border border-slate-800/80 bg-slate-950/60 px-2.5 py-1.5">
                            <div className="text-[10px] text-slate-400">
                              {pos.status === 'active' ? '在途本金 / 剩余份额' : '累计买入本金'}
                            </div>
                            <div className="mt-0.5 font-mono text-xs font-semibold text-slate-100">
                              {pos.status === 'active' ? (
                                <>
                                  {formatMoney(pos.invested_capital)}
                                  {pos.remaining_shares !== null && (
                                    <span className="ml-1 text-[10px] font-normal text-slate-400">
                                      ({pos.remaining_shares.toFixed(2)}份)
                                    </span>
                                  )}
                                </>
                              ) : (
                                formatMoney(pos.total_bought_amount)
                              )}
                            </div>
                          </div>

                          <div className="rounded-lg border border-slate-800/80 bg-slate-950/60 px-2.5 py-1.5">
                            <div className="text-[10px] text-slate-400">
                              {pos.status === 'active' ? '当前浮动盈亏' : '已实现盈亏'}
                            </div>
                            <div
                              className={`mt-0.5 font-mono text-xs font-bold ${pctColor(
                                pos.status === 'active' ? pos.unrealized_pnl : pos.realized_pnl,
                              )}`}
                            >
                              {formatMoney(
                                pos.status === 'active' ? pos.unrealized_pnl : pos.realized_pnl,
                                true,
                              )}
                            </div>
                          </div>

                          <div className="rounded-lg border border-slate-800/80 bg-slate-950/60 px-2.5 py-1.5">
                            <div className="text-[10px] text-slate-400">
                              {pos.status === 'active' ? '累计已实现 / 总盈亏' : '净总盈亏（扣费）'}
                            </div>
                            <div className="mt-0.5 font-mono text-xs">
                              {pos.status === 'active' ? (
                                <>
                                  <span className={pctColor(pos.realized_pnl)}>
                                    {formatMoney(pos.realized_pnl, true)}
                                  </span>
                                  <span className="mx-1 text-slate-600">/</span>
                                  <span className={`font-bold ${pctColor(pos.total_pnl)}`}>
                                    {formatMoney(pos.total_pnl, true)}
                                  </span>
                                </>
                              ) : (
                                <span className={`font-bold ${pctColor(pos.total_pnl)}`}>
                                  {formatMoney(pos.total_pnl, true)}
                                </span>
                              )}
                            </div>
                          </div>
                        </>
                      ) : (
                        <>
                          <div className="rounded-lg border border-slate-800/80 bg-slate-950/60 px-2.5 py-1.5">
                            <div className="text-[10px] text-slate-400">剩余仓位比例</div>
                            <div className="mt-0.5 font-mono text-xs font-semibold text-slate-200">
                              {(pos.remaining_ratio * 100).toFixed(0)}%
                            </div>
                          </div>
                          <div className="rounded-lg border border-slate-800/80 bg-slate-950/60 px-2.5 py-1.5">
                            <div className="text-[10px] text-slate-400">已实现部分收益率</div>
                            <div
                              className={`mt-0.5 font-mono text-xs font-semibold ${pctColor(pos.realized_return_pct)}`}
                            >
                              {formatPct(pos.realized_return_pct)}
                            </div>
                          </div>
                        </>
                      )}
                    </div>

                    {/* FIFO 7-Calendar-Day Short-Term Redemption Fee Bar (Active Positions) */}
                    {pos.status === 'active' && (
                      <div className="flex flex-wrap items-center gap-2 pt-0.5">
                        <span
                          className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[11px] font-medium ${
                            fifo.all_fee_free
                              ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                              : 'border-amber-500/45 bg-amber-500/10 text-amber-200'
                          }`}
                        >
                          {fifo.all_fee_free ? (
                            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
                          ) : (
                            <ShieldAlert className="h-3.5 w-3.5 text-amber-400" />
                          )}
                          {fifo.all_fee_free ? (
                            <span>
                              已满7天·免1.5%短赎费
                              {fifo.min_holding_days !== null && (
                                <span className="ml-1 font-mono text-emerald-300/80">
                                  (已持{' '}
                                  {fifo.min_holding_days === fifo.max_holding_days
                                    ? `${fifo.min_holding_days}`
                                    : `${fifo.min_holding_days}~${fifo.max_holding_days}`}
                                  天)
                                </span>
                              )}
                            </span>
                          ) : fifo.fee_free_ratio > 0 ? (
                            <span>
                              仅 {(fifo.fee_free_ratio * 100).toFixed(0)}% 免罚费 · 剩余{' '}
                              {(fifo.penalized_ratio * 100).toFixed(0)}% 需满7天
                              {fifo.earliest_all_fee_free_date && (
                                <span className="ml-1 font-mono text-amber-300">
                                  (全免日 {fifo.earliest_all_fee_free_date} · 剩{' '}
                                  {fifo.days_until_all_fee_free} 天)
                                </span>
                              )}
                            </span>
                          ) : (
                            <span>
                              未满7天·剩余 {fifo.days_until_all_fee_free} 天
                              {fifo.earliest_all_fee_free_date && (
                                <span className="ml-1 font-mono text-amber-300">
                                  (满7天日 {fifo.earliest_all_fee_free_date} · 提前赎回1.5%罚费)
                                </span>
                              )}
                            </span>
                          )}
                        </span>

                        {/* Mini FIFO split progress bar when partially fee-free */}
                        {!fifo.all_fee_free && fifo.fee_free_ratio > 0 && (
                          <div
                            className="flex h-2 w-24 overflow-hidden rounded-full bg-slate-800"
                            title={`免罚费 ${(fifo.fee_free_ratio * 100).toFixed(0)}% / 罚费区 ${(fifo.penalized_ratio * 100).toFixed(0)}%`}
                          >
                            <div
                              className="bg-emerald-500"
                              style={{ width: `${Math.round(fifo.fee_free_ratio * 100)}%` }}
                            />
                            <div
                              className="bg-amber-500"
                              style={{ width: `${Math.round(fifo.penalized_ratio * 100)}%` }}
                            />
                          </div>
                        )}

                        <GlossaryTooltip
                          termId="short-term-fee"
                          onNavigateToGuide={onNavigateToGuide}
                        />
                      </div>
                    )}

                    {/* Frozen Signal Context Snapshot */}
                    {pos.entry_signal_snapshot &&
                      Object.keys(pos.entry_signal_snapshot).length > 0 && (
                        <div className="flex flex-wrap items-center gap-1.5 pt-0.5 text-[10px]">
                          <span className="inline-flex items-center gap-1 text-amber-300/90">
                            <Sparkles className="h-3 w-3" />
                            建仓信号：
                          </span>
                          {pos.entry_signal_snapshot.template_name && (
                            <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-200">
                              {pos.entry_signal_snapshot.template_name}
                            </span>
                          )}
                          {pos.entry_signal_snapshot.trend_stage && (
                            <span className="rounded bg-sky-500/15 px-1.5 py-0.5 text-sky-200">
                              {String(pos.entry_signal_snapshot.trend_stage)}
                            </span>
                          )}
                          {(pos.entry_signal_snapshot.strategy_names ?? []).map((name) => (
                            <span
                              key={name}
                              className="rounded border border-slate-700 bg-slate-950/70 px-1.5 py-0.5 text-slate-300"
                            >
                              {name}
                            </span>
                          ))}
                        </div>
                      )}

                    {/* Position Review Notes */}
                    <div className="pt-0.5 text-xs">
                      {editingNotesPosId === pos.id ? (
                        <div className="flex items-center gap-2">
                          <input
                            type="text"
                            value={notesDraft}
                            onChange={(e) => setNotesDraft(e.target.value)}
                            placeholder="添加持仓复盘笔记..."
                            className="flex-1 rounded border border-slate-700 bg-slate-950 px-2.5 py-1 text-xs text-slate-100 focus:border-amber-500 focus:outline-none"
                          />
                          <button
                            type="button"
                            disabled={savingNotes}
                            onClick={() => handleSaveNotes(pos.id)}
                            className="inline-flex items-center gap-1 rounded bg-amber-500 px-2.5 py-1 text-[11px] font-semibold text-slate-950 hover:bg-amber-400"
                          >
                            <Save className="h-3 w-3" />
                            保存
                          </button>
                          <button
                            type="button"
                            onClick={() => setEditingNotesPosId(null)}
                            className="rounded border border-slate-700 px-2 py-1 text-[11px] text-slate-400 hover:text-slate-200"
                          >
                            取消
                          </button>
                        </div>
                      ) : (
                        <div className="flex items-center gap-2 text-[11px] text-slate-400">
                          <span>复盘笔记：{pos.notes || '暂无复盘备注'}</span>
                          <button
                            type="button"
                            onClick={() => {
                              setEditingNotesPosId(pos.id)
                              setNotesDraft(pos.notes || '')
                            }}
                            className="text-slate-500 hover:text-amber-300"
                            title="编辑持仓复盘笔记"
                          >
                            <Edit3 className="h-3 w-3" />
                          </button>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Position Action Buttons */}
                  <div className="flex shrink-0 flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={() =>
                        setExpandedDetailId((cur) => (cur === pos.id ? null : pos.id))
                      }
                      className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition ${
                        expandedDetailId === pos.id
                          ? 'border-sky-400/60 bg-sky-500/20 text-sky-200'
                          : 'border-slate-700 bg-slate-950 text-slate-200 hover:border-sky-500/50 hover:text-sky-200'
                      }`}
                    >
                      <LineChart className="h-3.5 w-3.5 text-sky-400" />
                      双曲线与风控
                      {expandedDetailId === pos.id ? (
                        <ChevronUp className="h-3.5 w-3.5" />
                      ) : (
                        <ChevronDown className="h-3.5 w-3.5" />
                      )}
                    </button>
                    {pos.status === 'active' ? (
                      <>
                        <button
                          type="button"
                          onClick={() => onOpenAddBuy(pos)}
                          className="inline-flex items-center gap-1 rounded-lg border border-rose-500/40 bg-rose-500/10 px-2.5 py-1.5 text-xs font-medium text-rose-200 hover:bg-rose-500/20"
                        >
                          <TrendingUp className="h-3.5 w-3.5" />
                          加仓
                        </button>
                        <button
                          type="button"
                          onClick={() => onOpenSell(pos)}
                          className="inline-flex items-center gap-1 rounded-lg border border-emerald-500/45 bg-emerald-500/15 px-2.5 py-1.5 text-xs font-medium text-emerald-200 hover:bg-emerald-500/25"
                        >
                          <ArrowDownRight className="h-3.5 w-3.5" />
                          记录卖出
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        onClick={() => onOpenReentryBuy(pos)}
                        className="inline-flex items-center gap-1 rounded-lg border border-amber-500/40 bg-amber-500/10 px-2.5 py-1.5 text-xs font-medium text-amber-200 hover:bg-amber-500/20"
                      >
                        <RotateCcw className="h-3.5 w-3.5" />
                        再次买入
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => handleDeletePosition(pos)}
                      className="rounded-lg border border-slate-800 bg-slate-950 p-1.5 text-slate-400 hover:border-rose-500/40 hover:text-rose-300"
                      title="删除持仓"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>

                {/* Expandable Dual-Curve Review Chart, Proxy K-Line & Exit Signals Panel */}
                {expandedDetailId === pos.id && (
                  <PositionDetailPanel
                    positionId={pos.id}
                    refreshKey={refreshKey + detailRefreshTick}
                    onPositionUpdated={loadPositions}
                    onNavigateToGuide={onNavigateToGuide}
                  />
                )}

                {/* Trade Lots Table */}
                <div className="overflow-x-auto bg-slate-950/40">
                  <table className="w-full text-left text-xs">
                    <thead>
                      <tr className="border-b border-slate-800/80 text-[11px] text-slate-400">
                        <th className="px-4 py-2 font-medium">方向</th>
                        <th className="px-3 py-2 font-medium">交易日期</th>
                        <th className="px-3 py-2 font-medium">确认净值 / 状态</th>
                        <th className="px-3 py-2 font-medium text-right">金额 / 比例</th>
                        <th className="px-3 py-2 font-medium text-right">份额 (份)</th>
                        <th className="px-3 py-2 font-medium text-right">手续费</th>
                        <th className="px-3 py-2 font-medium">FIFO 7天 / 卖出盈亏</th>
                        <th className="px-3 py-2 font-medium">备注</th>
                        <th className="px-4 py-2 font-medium text-right">操作</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/60">
                      {pos.lots.map((lot) => {
                        const isEditing = lotEdit?.lotId === lot.id
                        if (isEditing && lotEdit) {
                          return (
                            <tr key={lot.id} className="bg-slate-900/90">
                              <td className="px-4 py-2.5">
                                <span
                                  className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                                    lot.side === 'buy'
                                      ? 'bg-rose-500/20 text-rose-300'
                                      : 'bg-emerald-500/20 text-emerald-300'
                                  }`}
                                >
                                  {lot.side === 'buy' ? '买入' : '卖出'}
                                </span>
                              </td>
                              <td className="px-3 py-2.5">
                                <input
                                  type="date"
                                  value={lotEdit.tradeDate}
                                  onChange={(e) =>
                                    setLotEdit({ ...lotEdit, tradeDate: e.target.value })
                                  }
                                  className="rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-[11px] text-slate-100"
                                />
                              </td>
                              <td className="px-3 py-2.5">
                                <div className="flex items-center gap-1.5">
                                  <select
                                    value={lotEdit.navMode}
                                    onChange={(e) =>
                                      setLotEdit({
                                        ...lotEdit,
                                        navMode: e.target.value as 'auto' | 'manual',
                                      })
                                    }
                                    className="rounded border border-slate-700 bg-slate-950 px-1.5 py-1 text-[11px] text-slate-200"
                                  >
                                    <option value="auto">自动官方</option>
                                    <option value="manual">手动净值</option>
                                  </select>
                                  {lotEdit.navMode === 'manual' && (
                                    <input
                                      type="number"
                                      step="0.0001"
                                      value={lotEdit.navStr}
                                      onChange={(e) =>
                                        setLotEdit({ ...lotEdit, navStr: e.target.value })
                                      }
                                      placeholder="净值"
                                      className="w-20 rounded border border-sky-500/50 bg-slate-950 px-1.5 py-1 font-mono text-[11px] text-slate-100"
                                    />
                                  )}
                                </div>
                              </td>
                              <td className="px-3 py-2.5 text-right">
                                <div className="flex items-center justify-end gap-1">
                                  <input
                                    type="number"
                                    step="0.01"
                                    value={lotEdit.amountStr}
                                    onChange={(e) =>
                                      setLotEdit({ ...lotEdit, amountStr: e.target.value })
                                    }
                                    placeholder="金额"
                                    className="w-20 rounded border border-slate-700 bg-slate-950 px-1.5 py-1 text-right font-mono text-[11px] text-slate-100"
                                  />
                                  {lotEdit.side === 'sell' && (
                                    <input
                                      type="number"
                                      step="1"
                                      min="1"
                                      max="100"
                                      value={lotEdit.sellRatioPctStr}
                                      onChange={(e) =>
                                        setLotEdit({
                                          ...lotEdit,
                                          sellRatioPctStr: e.target.value,
                                        })
                                      }
                                      placeholder="比例%"
                                      className="w-16 rounded border border-emerald-500/40 bg-slate-950 px-1.5 py-1 text-right font-mono text-[11px] text-slate-100"
                                    />
                                  )}
                                </div>
                              </td>
                              <td className="px-3 py-2.5 text-right">
                                <input
                                  type="number"
                                  step="0.0001"
                                  value={lotEdit.sharesStr}
                                  onChange={(e) =>
                                    setLotEdit({ ...lotEdit, sharesStr: e.target.value })
                                  }
                                  placeholder="份额"
                                  className="w-20 rounded border border-slate-700 bg-slate-950 px-1.5 py-1 text-right font-mono text-[11px] text-slate-100"
                                />
                              </td>
                              <td className="px-3 py-2.5 text-right">
                                <input
                                  type="number"
                                  step="0.01"
                                  value={lotEdit.feeStr}
                                  onChange={(e) =>
                                    setLotEdit({ ...lotEdit, feeStr: e.target.value })
                                  }
                                  className="w-16 rounded border border-slate-700 bg-slate-950 px-1.5 py-1 text-right font-mono text-[11px] text-slate-100"
                                />
                              </td>
                              <td className="px-3 py-2.5 text-[10px] text-slate-500">保存后重算</td>
                              <td className="px-3 py-2.5">
                                <input
                                  type="text"
                                  value={lotEdit.notes}
                                  onChange={(e) =>
                                    setLotEdit({ ...lotEdit, notes: e.target.value })
                                  }
                                  className="w-28 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-slate-100"
                                />
                              </td>
                              <td className="px-4 py-2.5 text-right">
                                <div className="inline-flex items-center gap-1.5">
                                  <button
                                    type="button"
                                    disabled={savingLot}
                                    onClick={handleSaveLot}
                                    className="rounded bg-amber-500 px-2 py-1 text-[11px] font-semibold text-slate-950 hover:bg-amber-400"
                                  >
                                    保存
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => setLotEdit(null)}
                                    className="rounded border border-slate-700 px-2 py-1 text-[11px] text-slate-300"
                                  >
                                    取消
                                  </button>
                                </div>
                              </td>
                            </tr>
                          )
                        }

                        const displayShares = lot.shares ?? lot.effective_shares
                        const isFullyConsumedBuy =
                          lot.side === 'buy' &&
                          lot.fifo_remaining_lot_ratio !== null &&
                          lot.fifo_remaining_lot_ratio <= 1e-6

                        return (
                          <tr key={lot.id} className="hover:bg-slate-900/50">
                            <td className="px-4 py-2">
                              <span
                                className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                                  lot.side === 'buy'
                                    ? 'bg-rose-500/20 text-rose-300'
                                    : 'bg-emerald-500/20 text-emerald-300'
                                }`}
                              >
                                {lot.side === 'buy' ? '买入' : '卖出'}
                              </span>
                            </td>
                            <td className="px-3 py-2 font-mono text-slate-200">{lot.trade_date}</td>
                            <td className="px-3 py-2">
                              <div className="flex flex-wrap items-center gap-1.5">
                                {lot.nav !== null ? (
                                  <span className="font-mono font-semibold text-slate-100">
                                    {lot.nav.toFixed(4)}
                                  </span>
                                ) : (
                                  <span className="font-mono text-amber-300">待公布</span>
                                )}
                                {navStatusBadge(lot.nav_status)}
                                {lot.nav_status === 'pending' && lot.reference_nav !== null && (
                                  <span className="font-mono text-[10px] text-slate-400">
                                    参考 {lot.reference_nav.toFixed(4)}
                                    {lot.reference_proxy_pct_chg !== null
                                      ? ` · 代理 ${formatPct(lot.reference_proxy_pct_chg)}`
                                      : ''}
                                  </span>
                                )}
                              </div>
                            </td>
                            <td className="px-3 py-2 text-right font-mono text-slate-200">
                              {lot.amount !== null ? (
                                lot.amount.toFixed(2)
                              ) : lot.side === 'sell' && lot.sell_ratio !== null ? (
                                <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-300">
                                  卖出 {(lot.sell_ratio * 100).toFixed(0)}%
                                </span>
                              ) : lot.side === 'sell' ? (
                                <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-300">
                                  100% 清仓
                                </span>
                              ) : (
                                '-'
                              )}
                            </td>
                            <td className="px-3 py-2 text-right font-mono text-slate-300">
                              {displayShares !== null && displayShares !== undefined
                                ? displayShares.toFixed(2)
                                : '-'}
                            </td>
                            <td className="px-3 py-2 text-right font-mono text-slate-400">
                              {lot.fee > 0 ? lot.fee.toFixed(2) : '-'}
                            </td>
                            <td className="px-3 py-2">
                              {lot.side === 'buy' ? (
                                isFullyConsumedBuy ? (
                                  <span className="rounded border border-slate-700/80 bg-slate-900 px-1.5 py-0.5 text-[10px] text-slate-500">
                                    FIFO 已售完
                                  </span>
                                ) : lot.is_fee_free ? (
                                  <span className="inline-flex items-center gap-1 rounded border border-emerald-500/35 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-300">
                                    已满7天 ({lot.holding_calendar_days}天)
                                    {lot.fifo_remaining_lot_ratio !== null &&
                                      lot.fifo_remaining_lot_ratio < 0.999 && (
                                        <span className="text-emerald-200/80">
                                          · 剩{(lot.fifo_remaining_lot_ratio * 100).toFixed(0)}%
                                        </span>
                                      )}
                                  </span>
                                ) : lot.holding_calendar_days !== null ? (
                                  <span className="inline-flex items-center gap-1 rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-300">
                                    未满7天 · 剩{lot.days_until_fee_free}天
                                    {lot.fee_free_date && (
                                      <span className="font-mono text-amber-200/80">
                                        ({lot.fee_free_date})
                                      </span>
                                    )}
                                  </span>
                                ) : (
                                  <span className="text-slate-500">-</span>
                                )
                              ) : (
                                <div className="flex flex-wrap items-center gap-1.5 font-mono text-[11px]">
                                  {lot.realized_pnl !== null && (
                                    <span className={`font-semibold ${pctColor(lot.realized_pnl)}`}>
                                      {formatMoney(lot.realized_pnl, true)}
                                    </span>
                                  )}
                                  {lot.realized_return_pct !== null ? (
                                    <span className={pctColor(lot.realized_return_pct)}>
                                      {formatPct(lot.realized_return_pct)}
                                    </span>
                                  ) : (
                                    <span className="text-[10px] text-slate-500">待净值确认</span>
                                  )}
                                </div>
                              )}
                            </td>
                            <td className="px-3 py-2 text-[11px] text-slate-400">
                              {lot.notes || '-'}
                            </td>
                            <td className="px-4 py-2 text-right">
                              <div className="inline-flex items-center gap-1.5">
                                <button
                                  type="button"
                                  onClick={() => startEditLot(lot)}
                                  className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-amber-300"
                                  title="编辑批次"
                                >
                                  <Edit3 className="h-3.5 w-3.5" />
                                </button>
                                <button
                                  type="button"
                                  onClick={() => handleDeleteLot(lot)}
                                  className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-rose-300"
                                  title="删除批次"
                                >
                                  <Trash2 className="h-3.5 w-3.5" />
                                </button>
                              </div>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

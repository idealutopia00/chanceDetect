import { useCallback, useEffect, useState } from 'react'
import {
  Briefcase,
  Check,
  Clock,
  Edit3,
  LineChart,
  Link2,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
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

interface PositionWorkspaceProps {
  refreshKey: number
  onOpenNewBuy: () => void
  onOpenAddLot: (position: TrackedPositionRecord) => void
  onSelectFund: (symbol: string) => void
  onSummaryChange?: (summary: PositionsSummary) => void
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

function pctColor(val: number | null | undefined): string {
  if (val === null || val === undefined) return 'text-slate-400'
  if (val > 0) return 'text-rose-400'
  if (val < 0) return 'text-emerald-400'
  return 'text-slate-300'
}

interface LotEditState {
  positionId: number
  lotId: number
  tradeDate: string
  navMode: 'auto' | 'manual'
  navStr: string
  amountStr: string
  sharesStr: string
  feeStr: string
  notes: string
}

export function PositionWorkspace({
  refreshKey,
  onOpenNewBuy,
  onOpenAddLot,
  onSelectFund,
  onSummaryChange,
}: PositionWorkspaceProps) {
  const [statusFilter, setStatusFilter] = useState<'active' | 'closed' | 'all'>('active')
  const [positions, setPositions] = useState<TrackedPositionRecord[]>([])
  const [summary, setSummary] = useState<PositionsSummary>({
    active_count: 0,
    closed_count: 0,
    pending_nav_count: 0,
  })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [syncing, setSyncing] = useState(false)
  const [syncNotice, setSyncNotice] = useState<SyncNavResponse | null>(null)

  const [editingNotesPosId, setEditingNotesPosId] = useState<number | null>(null)
  const [notesDraft, setNotesDraft] = useState('')
  const [savingNotes, setSavingNotes] = useState(false)

  const [lotEdit, setLotEdit] = useState<LotEditState | null>(null)
  const [savingLot, setSavingLot] = useState(false)

  const loadPositions = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.listPositions(statusFilter)
      setPositions(res.positions)
      setSummary(res.summary)
      onSummaryChange?.(res.summary)
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
    setLotEdit({
      positionId: lot.position_id,
      lotId: lot.id,
      tradeDate: lot.trade_date,
      navMode: lot.nav_status === 'manual' ? 'manual' : 'auto',
      navStr: lot.nav !== null ? String(lot.nav) : '',
      amountStr: lot.amount !== null ? String(lot.amount) : '',
      sharesStr: lot.shares !== null ? String(lot.shares) : '',
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

      await api.updateTradeLot(lotEdit.positionId, lotEdit.lotId, {
        trade_date: lotEdit.tradeDate,
        nav: navPayload,
        amount: lotEdit.amountStr.trim() ? Number(lotEdit.amountStr) : null,
        shares: lotEdit.sharesStr.trim() ? Number(lotEdit.sharesStr) : null,
        fee: lotEdit.feeStr.trim() ? Number(lotEdit.feeStr) : 0,
        notes: lotEdit.notes.trim(),
      })
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
      {/* Top Action & Summary Strip */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-900/75 px-4 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-1 rounded-lg border border-slate-800 bg-slate-950/70 p-1 text-xs">
            {(
              [
                { id: 'active', label: `持仓中 (${summary.active_count})` },
                { id: 'closed', label: `已清仓 (${summary.closed_count})` },
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
        <div className="rounded-xl border border-rose-500/40 bg-rose-950/30 px-4 py-3 text-xs text-rose-300">
          操作失败：{error}
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
          {positions.map((pos) => (
            <div
              key={pos.id}
              className="overflow-hidden rounded-xl border border-slate-800/90 bg-slate-900/75"
            >
              {/* Position Header */}
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-800/80 p-4">
                <div className="min-w-0 flex-1 space-y-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded bg-amber-500/15 px-1.5 py-0.5 font-mono text-xs font-bold text-amber-300">
                      {pos.otc_code}
                    </span>
                    <h3 className="text-sm font-bold text-slate-100">{pos.otc_name}</h3>
                    <span
                      className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${
                        pos.status === 'active'
                          ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300'
                          : 'border-slate-700 bg-slate-800 text-slate-400'
                      }`}
                    >
                      {pos.status === 'active' ? '持仓中' : '已清仓'}
                    </span>
                    {pos.has_pending_nav && (
                      <span className="inline-flex items-center gap-1 rounded border border-amber-500/50 bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold text-amber-300">
                        <Clock className="h-3 w-3" />
                        Pending NAV · {pos.pending_lot_count} 笔待确认
                      </span>
                    )}
                  </div>

                  {/* Metadata & Latest NAV */}
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
                    <span className="font-mono">
                      建仓日 <strong className="text-slate-200">{pos.opened_date}</strong>
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

                  {/* Frozen Signal Context Snapshot */}
                  {pos.entry_signal_snapshot && Object.keys(pos.entry_signal_snapshot).length > 0 && (
                    <div className="flex flex-wrap items-center gap-1.5 pt-1 text-[10px]">
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

                  {/* Position Notes */}
                  <div className="pt-1 text-xs">
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
                        <span>备注：{pos.notes || '暂无复盘备注'}</span>
                        <button
                          type="button"
                          onClick={() => {
                            setEditingNotesPosId(pos.id)
                            setNotesDraft(pos.notes || '')
                          }}
                          className="text-slate-500 hover:text-amber-300"
                          title="编辑持仓备注"
                        >
                          <Edit3 className="h-3 w-3" />
                        </button>
                      </div>
                    )}
                  </div>
                </div>

                {/* Position Action Buttons */}
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => onOpenAddLot(pos)}
                    className="inline-flex items-center gap-1 rounded-lg border border-amber-500/40 bg-amber-500/10 px-2.5 py-1.5 text-xs font-medium text-amber-200 hover:bg-amber-500/20"
                  >
                    <Plus className="h-3.5 w-3.5" />
                    记录交易批次
                  </button>
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

              {/* Trade Lots Table */}
              <div className="overflow-x-auto bg-slate-950/40">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-slate-800/80 text-[11px] text-slate-400">
                      <th className="px-4 py-2 font-medium">方向</th>
                      <th className="px-3 py-2 font-medium">交易日期</th>
                      <th className="px-3 py-2 font-medium">确认净值 / 状态</th>
                      <th className="px-3 py-2 font-medium text-right">金额 (元)</th>
                      <th className="px-3 py-2 font-medium text-right">份额 (份)</th>
                      <th className="px-3 py-2 font-medium text-right">手续费</th>
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
                              <input
                                type="number"
                                step="0.01"
                                value={lotEdit.amountStr}
                                onChange={(e) =>
                                  setLotEdit({ ...lotEdit, amountStr: e.target.value })
                                }
                                placeholder="可选"
                                className="w-24 rounded border border-slate-700 bg-slate-950 px-1.5 py-1 text-right font-mono text-[11px] text-slate-100"
                              />
                            </td>
                            <td className="px-3 py-2.5 text-right">
                              <input
                                type="number"
                                step="0.0001"
                                value={lotEdit.sharesStr}
                                onChange={(e) =>
                                  setLotEdit({ ...lotEdit, sharesStr: e.target.value })
                                }
                                placeholder="可选"
                                className="w-24 rounded border border-slate-700 bg-slate-950 px-1.5 py-1 text-right font-mono text-[11px] text-slate-100"
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
                            <td className="px-3 py-2.5">
                              <input
                                type="text"
                                value={lotEdit.notes}
                                onChange={(e) =>
                                  setLotEdit({ ...lotEdit, notes: e.target.value })
                                }
                                className="w-32 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-slate-100"
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
                            {lot.amount !== null ? lot.amount.toFixed(2) : '-'}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-slate-300">
                            {lot.shares !== null ? lot.shares.toFixed(2) : '-'}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-slate-400">
                            {lot.fee > 0 ? lot.fee.toFixed(2) : '-'}
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
          ))}
        </div>
      )}
    </div>
  )
}

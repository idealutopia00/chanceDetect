import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  HardDrive,
  RefreshCw,
  Search,
  Server,
} from 'lucide-react'
import { api } from '../api/client'
import type { ExchangeFundInfo, SyncStatusResponse } from '../types'

interface DataSyncPanelProps {
  onDataUpdated: () => void
  onSelectFund: (symbol: string) => void
}

export function DataSyncPanel({ onDataUpdated, onSelectFund }: DataSyncPanelProps) {
  const [status, setStatus] = useState<SyncStatusResponse | null>(null)
  const [triggerError, setTriggerError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [fundType, setFundType] = useState<'ALL' | 'ETF' | 'LOF'>('ALL')
  const [funds, setFunds] = useState<ExchangeFundInfo[]>([])
  const [fundTotal, setFundTotal] = useState(0)

  const fetchStatus = useCallback(async () => {
    try {
      const res = await api.syncStatus()
      setStatus(res)
      return res.progress.running
    } catch {
      return false
    }
  }, [])

  const fetchFunds = useCallback(async () => {
    try {
      const res = await api.searchFunds(query, fundType, 60, 0)
      setFunds(res.items)
      setFundTotal(res.total)
    } catch {
      // ignore
    }
  }, [query, fundType])

  useEffect(() => {
    fetchStatus()
  }, [fetchStatus])

  useEffect(() => {
    const timer = setTimeout(() => {
      fetchFunds()
    }, 200)
    return () => clearTimeout(timer)
  }, [fetchFunds])

  // Poll live progress while running
  useEffect(() => {
    if (!status?.progress.running) return
    const id = setInterval(async () => {
      const stillRunning = await fetchStatus()
      if (!stillRunning) {
        onDataUpdated()
        fetchFunds()
      }
    }, 1500)
    return () => clearInterval(id)
  }, [status?.progress.running, fetchStatus, onDataUpdated, fetchFunds])

  const handleTrigger = async (
    mode: 'daily' | 'metadata' | 'full' | 'features',
    fullRefreshBars = false,
  ) => {
    setTriggerError(null)
    try {
      await api.triggerSync(mode, fullRefreshBars)
      await fetchStatus()
    } catch (err: unknown) {
      setTriggerError(err instanceof Error ? err.message : String(err))
    }
  }

  const prog = status?.progress
  const pct =
    prog && prog.total > 0 ? Math.min(100, Math.round((prog.done / prog.total) * 100)) : 0

  return (
    <div className="space-y-6">
      {/* KPI Cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>严格映射场内池</span>
            <Database className="h-4 w-4 text-amber-400" />
          </div>
          <div className="mt-2 font-mono text-2xl font-bold text-slate-100">
            {status?.universe.total_exchange_funds ?? 0}
          </div>
          <div className="mt-1 text-[11px] text-slate-400">
            ETF {status?.universe.etf_count ?? 0} · LOF {status?.universe.lof_count ?? 0} · 覆盖{' '}
            {status?.universe.unique_index_count ?? 0} 个指数
          </div>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>对应场外可购基金</span>
            <CheckCircle2 className="h-4 w-4 text-emerald-400" />
          </div>
          <div className="mt-2 font-mono text-2xl font-bold text-emerald-300">
            {status?.universe.unique_otc_funds ?? 0}
          </div>
          <div className="mt-1 text-[11px] text-slate-400">
            共 {status?.universe.total_mappings ?? 0} 条一对多映射记录 (含 A/C 份额)
          </div>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>Parquet 列式时序库</span>
            <HardDrive className="h-4 w-4 text-sky-400" />
          </div>
          <div className="mt-2 font-mono text-2xl font-bold text-slate-100">
            {((status?.storage.daily_bars_rows ?? 0) / 10000).toFixed(1)}
            <span className="ml-1 text-sm font-normal text-slate-400">万行</span>
          </div>
          <div className="mt-1 text-[11px] text-slate-400">
            行情 {status?.storage.daily_bars_mb ?? 0} MB · 预计算特征{' '}
            {status?.storage.features_mb ?? 0} MB
          </div>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>最新收盘日 K</span>
            <Server className="h-4 w-4 text-purple-400" />
          </div>
          <div className="mt-2 font-mono text-2xl font-bold text-amber-300">
            {status?.storage.latest_trade_date || '待同步'}
          </div>
          <div className="mt-1 text-[11px] text-slate-400">
            腾讯主源 + 新浪备源 · 自动除权漂移修复
          </div>
        </div>
      </div>

      {/* Sync Controls & Progress */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-100">数据同步控制中心</h2>
            <p className="text-xs text-slate-400">
              系统工作日 16:15 自动增量同步，开机/唤醒自动补拉；也可随时手动触发。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={prog?.running}
              onClick={() => handleTrigger('daily', false)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500 px-3 py-2 text-xs font-semibold text-slate-950 transition hover:bg-amber-400 disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${prog?.running ? 'animate-spin' : ''}`} />
              盘后增量同步日K
            </button>
            <button
              type="button"
              disabled={prog?.running}
              onClick={() => handleTrigger('metadata')}
              className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-700 disabled:opacity-50"
            >
              刷新场内外映射
            </button>
            <button
              type="button"
              disabled={prog?.running}
              onClick={() => handleTrigger('full', false)}
              className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-700 disabled:opacity-50"
            >
              全量一键同步
            </button>
            <button
              type="button"
              disabled={prog?.running}
              onClick={() => handleTrigger('features')}
              className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-700 disabled:opacity-50"
            >
              重算指标缓存
            </button>
          </div>
        </div>

        {triggerError && (
          <div className="mt-3 rounded border border-rose-500/40 bg-rose-950/40 px-3 py-2 text-xs text-rose-300">
            {triggerError}
          </div>
        )}

        {prog && (prog.running || prog.last_message) && (
          <div className="mt-4 rounded-lg border border-slate-800 bg-slate-950/70 p-3">
            <div className="flex items-center justify-between text-xs">
              <span className={prog.running ? 'text-sky-300' : 'text-slate-300'}>
                {prog.last_message || '就绪'}
              </span>
              {prog.total > 0 && (
                <span className="font-mono text-slate-400">
                  {prog.done}/{prog.total} ({pct}%)
                </span>
              )}
            </div>
            {prog.running && (
              <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-amber-500 to-sky-400 transition-all duration-300"
                  style={{ width: `${Math.max(5, pct)}%` }}
                />
              </div>
            )}
          </div>
        )}
      </div>

      {/* Fund Pool Search Table */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-100">
              基金池与场内外映射检索 ({fundTotal})
            </h2>
            <p className="text-xs text-slate-400">
              支持按场内代码/名称、跟踪指数、或任意场外联接/LOF代码反查。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-slate-500" />
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索 510300 / 000051 / 沪深300..."
                className="w-64 rounded-lg border border-slate-700 bg-slate-950 py-1.5 pl-8 pr-3 text-xs text-slate-100 focus:border-amber-500 focus:outline-none"
              />
            </div>
            <select
              value={fundType}
              onChange={(e) => setFundType(e.target.value as 'ALL' | 'ETF' | 'LOF')}
              className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200"
            >
              <option value="ALL">全部类型</option>
              <option value="ETF">仅 ETF</option>
              <option value="LOF">仅 LOF</option>
            </select>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-slate-800 text-[11px] text-slate-400">
              <tr>
                <th className="py-2 pr-3">场内基金</th>
                <th className="py-2 px-3">类型</th>
                <th className="py-2 px-3">跟踪指数</th>
                <th className="py-2 px-3">20日均额</th>
                <th className="py-2 pl-3">映射场外份额 (A/C)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {funds.map((f) => (
                <tr key={f.symbol} className="hover:bg-slate-800/40">
                  <td className="py-2 pr-3">
                    <button
                      type="button"
                      onClick={() => onSelectFund(f.symbol)}
                      className="font-medium text-slate-100 hover:text-amber-300"
                    >
                      {f.name}
                    </button>
                    <span className="ml-1.5 font-mono text-[11px] text-slate-400">{f.code}</span>
                  </td>
                  <td className="py-2 px-3">
                    <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                      {f.fund_type}
                    </span>
                  </td>
                  <td className="py-2 px-3 text-slate-300">
                    {f.index_name || '-'}
                    {f.index_code && (
                      <span className="ml-1 font-mono text-[10px] text-slate-500">
                        {f.index_code}
                      </span>
                    )}
                  </td>
                  <td className="py-2 px-3 font-mono text-slate-300">
                    {f.avg_amount_20d !== null
                      ? f.avg_amount_20d >= 10000
                        ? `${(f.avg_amount_20d / 10000).toFixed(2)}亿`
                        : `${Math.round(f.avg_amount_20d)}万`
                      : '-'}
                  </td>
                  <td className="py-2 pl-3">
                    <div className="flex flex-wrap gap-1">
                      {(f.otc_mappings ?? []).slice(0, 5).map((m) => (
                        <span
                          key={m.otc_code}
                          title={`${m.otc_name} (${m.purchase_status || '未知'})`}
                          className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-200"
                        >
                          <strong className="text-emerald-300">{m.share_class}</strong> {m.otc_code}
                        </span>
                      ))}
                      {(f.otc_mappings?.length ?? 0) > 5 && (
                        <span className="text-[10px] text-slate-500">
                          +{(f.otc_mappings?.length ?? 0) - 5}
                        </span>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Sync Runs & Errors */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <h3 className="mb-2.5 text-xs font-semibold text-slate-200">最近同步健康日志</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-left font-mono text-[11px]">
              <thead className="border-b border-slate-800 text-slate-400">
                <tr>
                  <th className="py-1.5 pr-2">任务</th>
                  <th className="py-1.5 px-2">状态</th>
                  <th className="py-1.5 px-2">成功/总数</th>
                  <th className="py-1.5 px-2">主/备源</th>
                  <th className="py-1.5 pl-2">除权修复</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {(status?.runs ?? []).map((r) => (
                  <tr key={r.id}>
                    <td className="py-1.5 pr-2 text-slate-200">{r.job_type}</td>
                    <td className="py-1.5 px-2">
                      <span
                        className={
                          r.status === 'success'
                            ? 'text-emerald-400'
                            : r.status === 'partial'
                              ? 'text-amber-400'
                              : 'text-rose-400'
                        }
                      >
                        {r.status}
                      </span>
                    </td>
                    <td className="py-1.5 px-2 text-slate-300">
                      {r.succeeded_count}/{r.total_targets}
                    </td>
                    <td className="py-1.5 px-2 text-slate-400">
                      {r.primary_source_count}/{r.fallback_source_count}
                    </td>
                    <td className="py-1.5 pl-2 text-sky-300">{r.drift_refreshed_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <h3 className="mb-2.5 flex items-center gap-1.5 text-xs font-semibold text-slate-200">
            <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />
            最近同步异常与质检提示
          </h3>
          {(status?.errors?.length ?? 0) === 0 ? (
            <p className="text-xs text-slate-500">暂无异常记录</p>
          ) : (
            <div className="max-h-56 space-y-1.5 overflow-y-auto text-[11px]">
              {status?.errors.slice(0, 20).map((e) => (
                <div
                  key={e.id}
                  className="rounded border border-slate-800 bg-slate-950/70 px-2.5 py-1.5 font-mono"
                >
                  <span className="text-amber-300">{e.symbol}</span>{' '}
                  <span className="text-slate-500">[{e.stage}/{e.provider}]</span>{' '}
                  <span className="text-slate-300">{e.error_message}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

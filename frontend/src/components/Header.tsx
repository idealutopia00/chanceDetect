import { Activity, Database, Radar, RefreshCw } from 'lucide-react'
import type { HealthResponse } from '../types'

interface HeaderProps {
  activeTab: 'scanner' | 'data'
  onTabChange: (tab: 'scanner' | 'data') => void
  health: HealthResponse | null
}

export function Header({ activeTab, onTabChange, health }: HeaderProps) {
  const latestDate = health?.storage?.latest_trade_date || health?.universe?.latest_trade_date
  const totalFunds = health?.universe?.total_exchange_funds ?? 0
  const totalOtc = health?.universe?.unique_otc_funds ?? 0

  return (
    <header className="sticky top-0 z-30 border-b border-slate-800/80 bg-slate-950/90 backdrop-blur-md">
      <div className="mx-auto flex max-w-[1600px] items-center justify-between px-4 py-3 sm:px-6">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-amber-500 to-rose-600 shadow-lg shadow-amber-500/20">
              <Radar className="h-5 w-5 text-white" />
            </div>
            <div>
              <h1 className="text-base font-bold tracking-tight text-slate-100">
                ChanceDetect
              </h1>
              <p className="text-[11px] text-slate-400">
                场内量价策略 → 对应场外基金雷达
              </p>
            </div>
          </div>

          <nav className="flex items-center gap-1 rounded-lg border border-slate-800 bg-slate-900/70 p-1">
            <button
              type="button"
              onClick={() => onTabChange('scanner')}
              className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition ${
                activeTab === 'scanner'
                  ? 'bg-amber-500/20 text-amber-300 shadow-sm ring-1 ring-amber-500/30'
                  : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }`}
            >
              <Activity className="h-3.5 w-3.5" />
              机会筛选工作台
            </button>
            <button
              type="button"
              onClick={() => onTabChange('data')}
              className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition ${
                activeTab === 'data'
                  ? 'bg-amber-500/20 text-amber-300 shadow-sm ring-1 ring-amber-500/30'
                  : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }`}
            >
              <Database className="h-3.5 w-3.5" />
              数据源与基金池
            </button>
          </nav>
        </div>

        <div className="hidden items-center gap-3 text-xs md:flex">
          {health?.sync_running && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-sky-500/40 bg-sky-500/10 px-2.5 py-1 text-sky-300">
              <RefreshCw className="h-3 w-3 animate-spin" />
              数据同步中
            </span>
          )}
          <div className="flex items-center gap-3 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-1.5 font-mono text-[11px] text-slate-300">
            <span>
              池内场内 <strong className="text-slate-100">{totalFunds}</strong>
            </span>
            <span className="text-slate-700">|</span>
            <span>
              映射场外 <strong className="text-amber-300">{totalOtc}</strong>
            </span>
            <span className="text-slate-700">|</span>
            <span>
              最新日K{' '}
              <strong className={latestDate ? 'text-emerald-400' : 'text-rose-400'}>
                {latestDate || '未同步'}
              </strong>
            </span>
          </div>
        </div>
      </div>
    </header>
  )
}

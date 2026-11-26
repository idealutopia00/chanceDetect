import { useCallback, useEffect, useState } from 'react'
import { api } from './api/client'
import { DataSyncPanel } from './components/DataSyncPanel'
import { FundReplayModal } from './components/FundReplayModal'
import { Header } from './components/Header'
import { OpportunityList } from './components/OpportunityList'
import { StrategySidebar } from './components/StrategySidebar'
import type {
  HealthResponse,
  PresetRecord,
  ScanRequestConfig,
  ScanResponse,
  StrategyMeta,
} from './types'

const DEFAULT_ENABLED_IDS = new Set([
  'ma_bullish_alignment',
  'volume_high_breakout',
  'rsi_oversold_rebound',
  'momentum_top_rank',
])

function buildDefaultConfig(strategies: StrategyMeta[]): ScanRequestConfig {
  return {
    strategies: strategies.map((s) => ({
      strategy_id: s.id,
      enabled: DEFAULT_ENABLED_IDS.has(s.id),
      params: { ...s.default_params },
    })),
    regime_combine_mode: 'OR',
    combine_mode: 'OR',
    min_hits: 2,
    base_filter: {
      min_avg_amount_20d: 1000,
      min_listing_days: 60,
      fund_types: ['ETF', 'LOF'],
    },
    share_class_preference: 'C',
    group_by_index: true,
    save_snapshot: false,
  }
}

function normalizePresetConfig(
  raw: Partial<ScanRequestConfig>,
  strategies: StrategyMeta[],
): ScanRequestConfig {
  const defaults = buildDefaultConfig(strategies)
  const rawById = new Map((raw.strategies ?? []).map((s) => [s.strategy_id, s]))
  const mergedStrategies =
    raw.strategies && raw.strategies.length > 0
      ? strategies.map((meta) => {
          const saved = rawById.get(meta.id)
          if (saved) {
            return {
              strategy_id: meta.id,
              enabled: Boolean(saved.enabled),
              params: { ...meta.default_params, ...(saved.params ?? {}) },
            }
          }
          return {
            strategy_id: meta.id,
            enabled: false,
            params: { ...meta.default_params },
          }
        })
      : defaults.strategies

  return {
    ...defaults,
    ...raw,
    strategies: mergedStrategies,
    regime_combine_mode: raw.regime_combine_mode ?? 'OR',
    combine_mode: raw.combine_mode ?? 'OR',
    min_hits: raw.min_hits ?? defaults.min_hits,
    base_filter: {
      ...defaults.base_filter,
      ...(raw.base_filter ?? {}),
    },
    share_class_preference: raw.share_class_preference ?? defaults.share_class_preference,
    group_by_index: raw.group_by_index ?? defaults.group_by_index,
  }
}

export default function App() {
  const [activeTab, setActiveTab] = useState<'scanner' | 'data'>('scanner')
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [strategies, setStrategies] = useState<StrategyMeta[]>([])
  const [presets, setPresets] = useState<PresetRecord[]>([])
  const [config, setConfig] = useState<ScanRequestConfig | null>(null)
  const [scanResult, setScanResult] = useState<ScanResponse | null>(null)
  const [scanning, setScanning] = useState(false)
  const [scanError, setScanError] = useState<string | null>(null)
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)

  const refreshHealth = useCallback(async () => {
    try {
      const h = await api.health()
      setHealth(h)
      return h
    } catch {
      return null
    }
  }, [])

  const runScan = useCallback(async (cfg: ScanRequestConfig) => {
    setScanning(true)
    setScanError(null)
    try {
      const res = await api.scan(cfg)
      setScanResult(res)
    } catch (err: unknown) {
      setScanError(err instanceof Error ? err.message : String(err))
    } finally {
      setScanning(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    async function bootstrap() {
      const [h, sRes, pRes] = await Promise.all([
        api.health().catch(() => null),
        api.listStrategies(),
        api.listPresets().catch(() => ({ presets: [] })),
      ])
      if (!active) return
      setHealth(h)
      setStrategies(sRes.strategies)
      setPresets(pRes.presets)
      const initialCfg = buildDefaultConfig(sRes.strategies)
      setConfig(initialCfg)
      if (h && h.storage.daily_bars_rows > 0) {
        runScan(initialCfg)
      }
    }
    bootstrap()
    return () => {
      active = false
    }
  }, [runScan])

  const handleSavePreset = async (name: string, description: string) => {
    if (!config) return
    await api.savePreset(name, description, config)
    const res = await api.listPresets()
    setPresets(res.presets)
  }

  const handleDeletePreset = async (id: number) => {
    await api.deletePreset(id)
    const res = await api.listPresets()
    setPresets(res.presets)
  }

  const handleLoadPreset = (preset: PresetRecord) => {
    const normalized = normalizePresetConfig(preset.config, strategies)
    setConfig(normalized)
    runScan(normalized)
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <Header activeTab={activeTab} onTabChange={setActiveTab} health={health} />

      <main className="mx-auto max-w-[1600px] px-4 py-5 sm:px-6">
        {activeTab === 'scanner' && config && (
          <div className="flex flex-col gap-5 lg:flex-row lg:items-start">
            <StrategySidebar
              strategies={strategies}
              config={config}
              presets={presets}
              loading={scanning}
              onConfigChange={setConfig}
              onRunScan={() => runScan(config)}
              onSavePreset={handleSavePreset}
              onDeletePreset={handleDeletePreset}
              onLoadPreset={handleLoadPreset}
            />
            <section className="min-w-0 flex-1">
              <OpportunityList
                result={scanResult}
                loading={scanning}
                error={scanError}
                onSelectFund={setSelectedSymbol}
              />
            </section>
          </div>
        )}

        {activeTab === 'data' && (
          <DataSyncPanel
            onDataUpdated={async () => {
              await refreshHealth()
              if (config) runScan(config)
            }}
            onSelectFund={setSelectedSymbol}
          />
        )}
      </main>

      {config && (
        <FundReplayModal
          symbol={selectedSymbol}
          config={config}
          onClose={() => setSelectedSymbol(null)}
        />
      )}
    </div>
  )
}

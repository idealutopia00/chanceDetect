import type {
  ExchangeFundInfo,
  FundReplayResponse,
  GuideMetadataResponse,
  HealthResponse,
  PresetRecord,
  ScanRequestConfig,
  ScanResponse,
  StrategyMeta,
  SyncStatusResponse,
} from '../types'

const BASE = '/api/v1'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try {
      const err = await res.json()
      if (err?.detail) msg = typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail)
    } catch {
      // ignore json parse failure
    }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => request<HealthResponse>('/health'),

  listStrategies: () => request<{ strategies: StrategyMeta[] }>('/strategies'),

  guideMetadata: () => request<GuideMetadataResponse>('/guide/metadata'),

  scan: (config: ScanRequestConfig) =>
    request<ScanResponse>('/scan', {
      method: 'POST',
      body: JSON.stringify(config),
    }),

  fundReplay: (symbol: string, config: ScanRequestConfig, lookbackDays = 500) =>
    request<FundReplayResponse>(`/funds/${encodeURIComponent(symbol)}/replay`, {
      method: 'POST',
      body: JSON.stringify({ config, lookback_days: lookbackDays }),
    }),

  searchFunds: (q: string, fundType?: string, limit = 50, offset = 0) => {
    const params = new URLSearchParams({ q, limit: String(limit), offset: String(offset) })
    if (fundType && fundType !== 'ALL') params.set('fund_type', fundType)
    return request<{ items: ExchangeFundInfo[]; total: number; limit: number; offset: number }>(
      `/funds?${params.toString()}`,
    )
  },

  listPresets: () => request<{ presets: PresetRecord[] }>('/presets'),

  savePreset: (name: string, description: string, config: ScanRequestConfig) =>
    request<{ preset: PresetRecord }>('/presets', {
      method: 'POST',
      body: JSON.stringify({ name, description, config }),
    }),

  deletePreset: (id: number) =>
    request<{ deleted: boolean }>(`/presets/${id}`, { method: 'DELETE' }),

  syncStatus: () => request<SyncStatusResponse>('/sync/status'),

  triggerSync: (mode: 'daily' | 'metadata' | 'full' | 'features', fullRefreshBars = false) =>
    request<{ accepted: boolean; mode: string }>('/sync/trigger', {
      method: 'POST',
      body: JSON.stringify({ mode, full_refresh_bars: fullRefreshBars }),
    }),
}

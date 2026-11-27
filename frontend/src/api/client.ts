import type {
  ExchangeFundInfo,
  FundReplayResponse,
  GuideMetadataResponse,
  HealthResponse,
  OTCLookupResponse,
  PositionCreatePayload,
  PositionsListResponse,
  PositionUpdatePayload,
  PresetRecord,
  ScanRequestConfig,
  ScanResponse,
  StrategyMeta,
  SyncNavResponse,
  SyncStatusResponse,
  TrackedPositionRecord,
  TradeLotCreatePayload,
  TradeLotRecord,
  TradeLotUpdatePayload,
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

  lookupOtc: (q: string, limit = 25) => {
    const params = new URLSearchParams({ q, limit: String(limit) })
    return request<OTCLookupResponse>(`/otc/lookup?${params.toString()}`)
  },

  listPositions: (status: 'active' | 'closed' | 'all' = 'active') =>
    request<PositionsListResponse>(`/positions?status=${encodeURIComponent(status)}`),

  getPosition: (positionId: number) =>
    request<{ position: TrackedPositionRecord }>(`/positions/${positionId}`),

  createPosition: (payload: PositionCreatePayload) =>
    request<{ position: TrackedPositionRecord }>('/positions', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  updatePosition: (positionId: number, payload: PositionUpdatePayload) =>
    request<{ position: TrackedPositionRecord }>(`/positions/${positionId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  deletePosition: (positionId: number) =>
    request<{ deleted: boolean; id: number }>(`/positions/${positionId}`, {
      method: 'DELETE',
    }),

  addTradeLot: (positionId: number, payload: TradeLotCreatePayload) =>
    request<{ lot: TradeLotRecord; position: TrackedPositionRecord }>(
      `/positions/${positionId}/lots`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    ),

  updateTradeLot: (positionId: number, lotId: number, payload: TradeLotUpdatePayload) =>
    request<{ lot: TradeLotRecord; position: TrackedPositionRecord }>(
      `/positions/${positionId}/lots/${lotId}`,
      {
        method: 'PATCH',
        body: JSON.stringify(payload),
      },
    ),

  deleteTradeLot: (positionId: number, lotId: number) =>
    request<{ deleted: boolean; id: number; position: TrackedPositionRecord }>(
      `/positions/${positionId}/lots/${lotId}`,
      {
        method: 'DELETE',
      },
    ),

  syncPositionNavs: (otcCode?: string) =>
    request<SyncNavResponse>('/positions/sync-nav', {
      method: 'POST',
      body: JSON.stringify(otcCode ? { otc_code: otcCode } : {}),
    }),
}

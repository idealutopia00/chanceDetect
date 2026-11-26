export type StrategyCategory = 'trend' | 'mean_reversion' | 'momentum'
export type StrategyRole = 'regime' | 'entry'
export type RegimeCombineMode = 'AND' | 'OR'
export type CombineMode = 'AND' | 'OR' | 'RESONANCE'
export type SharePreference = 'C' | 'A'

export interface JsonSchemaProperty {
  type?: string
  title?: string
  description?: string
  default?: unknown
  minimum?: number
  maximum?: number
}

export interface StrategyMeta {
  id: string
  name: string
  category: StrategyCategory
  role: StrategyRole
  description: string
  default_params: Record<string, unknown>
  params_schema: {
    properties?: Record<string, JsonSchemaProperty>
  }
}

export interface StrategySelection {
  strategy_id: string
  enabled: boolean
  params: Record<string, unknown>
}

export interface BaseUniverseFilter {
  min_avg_amount_20d: number
  min_listing_days: number
  fund_types: Array<'ETF' | 'LOF'>
}

export interface ScanRequestConfig {
  strategies: StrategySelection[]
  regime_combine_mode: RegimeCombineMode
  combine_mode: CombineMode
  min_hits: number
  base_filter: BaseUniverseFilter
  share_class_preference: SharePreference
  target_date?: string | null
  group_by_index: boolean
  save_snapshot?: boolean
  preset_id?: number | null
}

export interface OTCMapping {
  exchange_symbol: string
  otc_code: string
  otc_name: string
  share_class: 'A' | 'C' | 'E' | 'I' | 'OTHER'
  mapping_type: 'FEEDER' | 'LOF_SELF' | 'LOF_SIBLING'
  purchase_status: string
  fee_rate: string
}

export interface HitStrategyInfo {
  strategy_id: string
  strategy_name: string
  role?: StrategyRole
  score: number
  reason: string
}

export interface HitFundItem {
  symbol: string
  code: string
  name: string
  fund_type: 'ETF' | 'LOF'
  index_code: string
  index_name: string
  company_name: string
  close: number
  pct_chg: number
  amount: number
  avg_amount_20d: number
  vol_ratio: number
  rsi14: number | null
  hit_count: number
  composite_score: number
  matched_regimes?: HitStrategyInfo[]
  hit_strategies: HitStrategyInfo[]
  otc_mappings: OTCMapping[]
}

export interface OpportunityGroup {
  group_key: string
  index_code: string
  index_name: string
  representative: HitFundItem
  member_count: number
  members: HitFundItem[]
  max_hit_count: number
  max_composite_score: number
  otc_funds: OTCMapping[]
  otc_count: number
}

export interface ScanResponse {
  trade_date: string
  elapsed_ms: number
  scanned_fund_count: number
  hit_fund_count: number
  hit_index_count: number
  actionable_fund_count: number
  actionable_index_count: number
  watchlist_fund_count: number
  watchlist_index_count: number
  regime_combine_mode: RegimeCombineMode
  combine_mode: CombineMode
  share_class_preference: SharePreference
  groups: OpportunityGroup[]
  actionable_groups: OpportunityGroup[]
  watchlist_groups: OpportunityGroup[]
}

export interface PresetRecord {
  id: number
  name: string
  description: string
  config: ScanRequestConfig
  created_at: string
  updated_at: string
}

export interface ReplayBar {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
  pct_chg: number
  ma5: number | null
  ma10: number | null
  ma20: number | null
  ma60: number | null
  rsi14: number | null
}

export interface ReplayTrigger {
  date: string
  close: number
  hit_count: number
  composite_score: number
  hit_strategies: string[]
  reasons: string[]
}

export interface ForwardStat {
  horizon_days: number
  sample_count: number
  win_rate_pct: number | null
  avg_return_pct: number | null
  median_return_pct: number | null
  max_gain_pct: number | null
  max_loss_pct: number | null
  avg_max_drawdown_pct: number | null
}

export interface ExchangeFundInfo {
  symbol: string
  code: string
  exchange: 'sh' | 'sz'
  name: string
  fund_type: 'ETF' | 'LOF'
  index_code: string
  index_name: string
  company_name: string
  establish_date: string
  fund_scale: number | null
  latest_trade_date: string | null
  avg_amount_20d: number | null
  otc_mappings?: OTCMapping[]
}

export interface FundReplayResponse {
  fund: ExchangeFundInfo
  lookback_days: number
  trigger_count: number
  bars: ReplayBar[]
  triggers: ReplayTrigger[]
  forward_stats: ForwardStat[]
  direct_otc_mappings: OTCMapping[]
  index_otc_mappings: OTCMapping[]
}

export interface UniverseStats {
  total_exchange_funds: number
  etf_count: number
  lof_count: number
  unique_index_count: number
  unique_otc_funds: number
  total_mappings: number
  latest_trade_date: string | null
}

export interface StorageStats {
  daily_bars_rows: number
  daily_bars_symbols: number
  daily_bars_mb: number
  features_rows: number
  features_mb: number
  benchmarks_rows: number
  latest_trade_date: string | null
}

export interface SyncRunRecord {
  id: number
  job_type: string
  status: string
  started_at: string
  finished_at: string | null
  total_targets: number
  succeeded_count: number
  failed_count: number
  primary_source_count: number
  fallback_source_count: number
  drift_refreshed_count: number
}

export interface SyncErrorRecord {
  id: number
  run_id: number
  symbol: string
  stage: string
  provider: string
  error_message: string
  created_at: string
}

export interface SyncStatusResponse {
  progress: {
    running: boolean
    job_type: string
    stage: string
    done: number
    total: number
    last_message: string
    run_id: number | null
  }
  universe: UniverseStats
  storage: StorageStats
  runs: SyncRunRecord[]
  errors: SyncErrorRecord[]
}

export interface HealthResponse {
  status: string
  universe: UniverseStats
  storage: StorageStats
  sync_running: boolean
}

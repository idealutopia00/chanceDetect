export type AppTab = 'scanner' | 'positions' | 'guide' | 'data'
export type StrategyCategory = 'trend' | 'mean_reversion' | 'momentum'
export type StrategyRole = 'regime' | 'entry'
export type TrendStage = 'oversold' | 'early' | 'mid' | 'overheated'
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
  hide_overheated: boolean
  freshness_cooldown_days?: number
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
  trend_stage?: TrendStage
  is_fresh?: boolean
  hits_20d?: number
  bias20?: number | null
  bias60?: number | null
  bias20_z?: number | null
  drawup_60d_low?: number | null
  boll_pct_b?: number | null
  pos_60d?: number | null
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
  hide_overheated?: boolean
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

export type ReplayEntryBasis = 'T+1_close'

export interface FundReplayResponse {
  fund: ExchangeFundInfo
  lookback_days: number
  entry_basis: ReplayEntryBasis
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

export interface GuideStrategyParam {
  key: string
  label: string
  type: string
  default: unknown
  minimum?: number | null
  maximum?: number | null
}

export interface GuideStrategyMeta extends StrategyMeta {
  strategy_id: string
  role_label: string
  params: GuideStrategyParam[]
}

export interface GuideScenarioTemplate {
  id: string
  name: string
  badge?: string | null
  description: string
  regime_combine_mode: RegimeCombineMode
  combine_mode: CombineMode
  min_hits: number
  enabled_regimes: string[]
  enabled_entries: string[]
  enabled_regime_names: string[]
  enabled_entry_names: string[]
}

export interface GuideSystemDefaults {
  freshness_cooldown_days: number
  hits_window_days: number
  replay_horizons: number[]
  replay_entry_basis: ReplayEntryBasis
  short_term_fee_days: number
  short_term_fee_rate_pct: number
  otc_cutoff_time: string
  eod_sync_time: string
  otc_nav_sync_times: string[]
  base_filter: {
    min_avg_amount_20d: number
    min_listing_days: number
  }
  exit_thresholds: {
    trailing_drawdown_pct: number
    cost_stop_loss_pct: number
    ma20_watch_days: number
    ma20_alert_days: number
    short_term_fee_days: number
    short_term_fee_rate_pct: number
  }
  trend_stage_thresholds: {
    overheated: {
      boll_pct_b_min: number
      bias20_z_min: number
      rsi14_min: number
      pos_60d_min: number
    }
    oversold: {
      rsi14_max: number
      boll_pct_b_max: number
      bias20_z_max: number
    }
    early: {
      drawup_60d_low_max: number
      rsi14_max: number
      boll_pct_b_max: number
    }
  }
}

export interface GuideMetadataResponse {
  strategies: GuideStrategyMeta[]
  strategies_by_role: {
    regime: GuideStrategyMeta[]
    entry: GuideStrategyMeta[]
  }
  scenario_templates: GuideScenarioTemplate[]
  system_defaults: GuideSystemDefaults
}

export type PositionStatus = 'active' | 'closed'
export type TradeSide = 'buy' | 'sell'
export type NavStatus = 'pending' | 'confirmed' | 'manual'

export interface ProxyFundSummary {
  symbol: string
  code: string
  name: string
  fund_type: 'ETF' | 'LOF'
  index_code: string
  index_name: string
}

export interface EntrySignalSnapshot {
  signal_date?: string | null
  template_name?: string | null
  trend_stage?: TrendStage | string | null
  strategy_names?: string[]
  regime_names?: string[]
  entry_names?: string[]
  proxy_symbol?: string | null
  proxy_name?: string | null
  proxy_close?: number | null
  index_name?: string | null
  [key: string]: unknown
}

export interface LotFifoStatus {
  lot_id: number
  trade_date: string
  remaining_ratio: number
  remaining_lot_ratio: number
  remaining_shares: number | null
  holding_calendar_days: number
  is_fee_free: boolean
  days_until_fee_free: number
  fee_free_date: string
}

export interface FifoFeeStatus {
  fee_free_ratio: number
  penalized_ratio: number
  all_fee_free: boolean
  earliest_all_fee_free_date: string | null
  days_until_all_fee_free: number
  min_holding_days: number | null
  max_holding_days: number | null
  lots: LotFifoStatus[]
}

export interface TradeLotRecord {
  id: number
  position_id: number
  side: TradeSide
  trade_date: string
  nav: number | null
  nav_status: NavStatus
  amount: number | null
  shares: number | null
  effective_shares: number | null
  sell_ratio: number | null
  fee: number
  notes: string
  reference_nav: number | null
  reference_nav_date: string | null
  reference_proxy_pct_chg: number | null
  realized_pnl: number | null
  realized_return_pct: number | null
  fifo_remaining_ratio: number | null
  fifo_remaining_lot_ratio: number | null
  fifo_remaining_shares: number | null
  holding_calendar_days: number | null
  is_fee_free: boolean | null
  days_until_fee_free: number | null
  fee_free_date: string | null
  created_at: string
  updated_at: string
}

export type ExitSeverity = 'alert' | 'warning' | 'ok'

export interface ExitSignalItem {
  rule_id: string
  title: string
  severity: ExitSeverity
  triggered: boolean
  message: string
  metric_value: number | null
  threshold_value: number | null
}

export interface DualCurvePoint {
  date: string
  otc_return_pct: number | null
  proxy_return_pct: number | null
  spread_pct: number | null
  otc_unit_nav: number | null
  proxy_close: number | null
  is_post_exit: boolean
}

export interface DualCurveMarker {
  lot_id: number
  date: string
  side: TradeSide
  kind: 'buy' | 'add' | 'partial_sell' | 'full_sell'
  label: string
  nav: number | null
  nav_status: NavStatus
  return_pct_on_date: number | null
  amount: number | null
  shares: number | null
  sell_ratio: number | null
  notes: string
}

export interface DualCurveData {
  baseline_date: string
  has_proxy_curve: boolean
  is_pending_baseline: boolean
  breakeven_return_pct: number | null
  peak_return_pct: number | null
  peak_date: string | null
  current_drawdown_pct: number | null
  max_holding_drawdown_pct: number | null
  latest_tracking_spread_pct: number | null
  post_exit_days: number
  points: DualCurvePoint[]
  markers: DualCurveMarker[]
}

export interface PostExitStats {
  closed_date: string
  days_observed: number
  post_exit_latest_return_pct: number | null
  post_exit_return_20d_pct: number | null
  post_exit_return_40d_pct: number | null
  post_exit_max_gain_pct: number | null
  post_exit_max_drop_pct: number | null
}

export interface ProxyKlineBar {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  pct_chg: number
  ma5: number | null
  ma10: number | null
  ma20: number | null
  ma60: number | null
  trend_stage: TrendStage | null
}

export interface TrackedPositionRecord {
  id: number
  otc_code: string
  otc_name: string
  exchange_symbol: string | null
  proxy_fund: ProxyFundSummary | null
  status: PositionStatus
  opened_date: string
  closed_date: string | null
  holding_days: number
  entry_signal_snapshot: EntrySignalSnapshot
  notes: string
  custom_stop_loss_pct: number | null
  custom_trailing_drawdown_pct: number | null
  custom_take_profit_pct: number | null
  latest_nav: number | null
  latest_nav_date: string | null
  latest_daily_return_pct: number | null
  latest_proxy_close: number | null
  latest_proxy_pct_chg: number | null
  latest_proxy_date: string | null
  latest_proxy_trend_stage?: TrendStage | null
  has_amount_data: boolean
  avg_cost_nav: number | null
  remaining_shares: number | null
  remaining_ratio: number
  invested_capital: number | null
  total_bought_amount: number | null
  total_sold_amount: number | null
  total_fees: number | null
  realized_pnl: number | null
  realized_return_pct: number | null
  unrealized_pnl: number | null
  unrealized_return_pct: number | null
  total_pnl: number | null
  total_return_pct: number | null
  fifo_fee_status: FifoFeeStatus
  peak_return_pct: number | null
  peak_date: string | null
  current_drawdown_pct: number | null
  max_holding_drawdown_pct: number | null
  latest_tracking_spread_pct: number | null
  exit_signals: ExitSignalItem[]
  exit_alert_count: number
  exit_warning_count: number
  triggered_exit_signal_count: number
  post_exit_stats: PostExitStats | null
  dual_curve?: DualCurveData
  proxy_bars?: ProxyKlineBar[]
  lot_count: number
  pending_lot_count: number
  has_pending_nav: boolean
  lots: TradeLotRecord[]
  created_at: string
  updated_at: string
}

export interface PositionsSummary {
  active_count: number
  closed_count: number
  pending_nav_count: number
  exit_alert_count: number
  exit_warning_count: number
  exit_alert_position_count: number
  closed_win_count: number
  closed_evaluated_count: number
  closed_win_rate_pct: number | null
  closed_avg_return_pct: number | null
  amount_position_count: number
  active_amount_position_count: number
  total_invested_capital: number | null
  total_unrealized_pnl: number | null
  total_realized_pnl: number | null
  total_pnl: number | null
}

export interface PositionsListResponse {
  positions: TrackedPositionRecord[]
  summary: PositionsSummary
  portfolio_summary: PositionsSummary
}

export interface OTCLookupItem {
  otc_code: string
  otc_name: string
  share_class: 'A' | 'C' | 'E' | 'I' | 'OTHER'
  mapping_type: 'FEEDER' | 'LOF_SELF' | 'LOF_SIBLING' | null
  purchase_status: string
  fee_rate: string
  is_mapped: boolean
  suggested_proxy: ProxyFundSummary | null
  latest_nav: number | null
  latest_nav_date: string | null
  has_active_position: boolean
  active_position_id: number | null
}

export interface OTCLookupResponse {
  query: string
  items: OTCLookupItem[]
  resolved_external: OTCLookupItem | null
}

export interface SyncNavResponse {
  refreshed_funds: number
  backfilled_lots: number
  updated_bars: number
  errors: Array<{ otc_code: string; error: string }>
}

export interface PositionCreatePayload {
  otc_code: string
  otc_name?: string | null
  exchange_symbol?: string | null
  trade_date?: string | null
  nav?: number | null
  amount?: number | null
  shares?: number | null
  fee?: number
  notes?: string
  lot_notes?: string
  entry_signal_snapshot?: EntrySignalSnapshot
}

export interface PositionUpdatePayload {
  otc_name?: string | null
  exchange_symbol?: string | null
  notes?: string
  custom_stop_loss_pct?: number | null
  custom_trailing_drawdown_pct?: number | null
  custom_take_profit_pct?: number | null
}

export interface TradeLotCreatePayload {
  side?: TradeSide
  trade_date?: string | null
  nav?: number | null
  amount?: number | null
  shares?: number | null
  sell_ratio?: number | null
  fee?: number
  notes?: string
}

export interface TradeLotUpdatePayload {
  side?: TradeSide
  trade_date?: string
  nav?: number | null
  amount?: number | null
  shares?: number | null
  sell_ratio?: number | null
  fee?: number
  notes?: string
}

export interface BuyModalPrefill {
  otc_code: string
  otc_name: string
  exchange_symbol: string | null
  exchange_name?: string | null
  entry_signal_snapshot?: EntrySignalSnapshot
}

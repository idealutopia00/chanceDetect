export type GlossaryTermId =
  | 'proxy-mapping'
  | 'index-group'
  | 'trend-regime'
  | 'entry-trigger'
  | 'two-stage-funnel'
  | 'trend-stage'
  | 'fresh-signal'
  | 'bias20'
  | 'bias20-z'
  | 'boll-pct-b'
  | 'rsi14'
  | 'rs'
  | 'anti-chase'
  | 't-plus-1'
  | 'pending-nav'
  | 'short-term-fee'
  | 'exit-signals'

export interface GlossaryTerm {
  id: GlossaryTermId
  anchorId: string
  title: string
  summary: string
}

export const GLOSSARY_TERMS: Record<GlossaryTermId, GlossaryTerm> = {
  'proxy-mapping': {
    id: 'proxy-mapping',
    anchorId: 'term-proxy-mapping',
    title: '场内代理 → 场外映射',
    summary:
      '用有完整盘中量价与前复权 K 线的场内 ETF/LOF（On-Exchange Proxy Fund）做量化分析，再严格映射到对应的场外联接或 LOF（OTC Counterpart）申购。',
  },
  'index-group': {
    id: 'index-group',
    anchorId: 'term-index-group',
    title: '同指数智能聚合 (Index Opportunity Group)',
    summary:
      '将跟踪同一标的指数的多只命中 ETF/LOF 合并为一张机会卡，以 20 日均额最高的龙头基金为主代表，并汇总该指数下全部可购场外份额。',
  },
  'trend-regime': {
    id: 'trend-regime',
    anchorId: 'term-trend-regime',
    title: '趋势滤网 · Trend Regime',
    summary:
      '持续型方向或相对强弱条件，用来筛选当前处于强势主线的候选池；它负责“选赛道”，单独满足时归入主线观察池而非直接触发买入。',
  },
  'entry-trigger': {
    id: 'entry-trigger',
    anchorId: 'term-entry-trigger',
    title: '买点触发 · Entry Trigger',
    summary:
      '离散型入场时机事件（如强势回踩企稳、首日多头启动、放量突破或超跌反弹），用来判断次日 T+1 15:00 前是否具备高盈亏比切入点。',
  },
  'two-stage-funnel': {
    id: 'two-stage-funnel',
    anchorId: 'term-two-stage-funnel',
    title: '两阶段漏斗分流',
    summary:
      '同时通过趋势过滤并触发买点的标的进入「今日可买信号」；仅通过趋势过滤但暂未出现买点的强势标的自动归入「主线观察 · 等回踩」。',
  },
  'trend-stage': {
    id: 'trend-stage',
    anchorId: 'term-trend-stage',
    title: '趋势阶段 · Trend Stage',
    summary:
      '结合波动率归一化乖离、布林 %B 与 RSI14 将标的分为超跌低位、启动初期、趋势中段、高位偏热四档，避免把已大幅透支的连涨品种当启动买点。',
  },
  'fresh-signal': {
    id: 'fresh-signal',
    anchorId: 'term-fresh-signal',
    title: '新信号 · Fresh Signal',
    summary:
      '当前策略组合在过去冷却期（默认 10 个交易日）内首次触发可买信号，区别于高位连续反复触发的噪音信号，综合评分额外加权。',
  },
  bias20: {
    id: 'bias20',
    anchorId: 'term-bias20',
    title: 'MA20 乖离率 (bias20)',
    summary:
      '衡量收盘价偏离 20 日均线的百分比：(收盘价 - MA20) / MA20。正值过大代表短期拉升过急，负值极深代表短期超跌。',
  },
  'bias20-z': {
    id: 'bias20-z',
    anchorId: 'term-bias20-z',
    title: '波动率归一化乖离 (bias20_z)',
    summary:
      '用 MA20 乖离率除以 20 日日涨跌幅标准差（下限 0.5%），把低波银行和高波芯片放到同一把尺子上衡量是否“涨过头”或“跌过头”。',
  },
  'boll-pct-b': {
    id: 'boll-pct-b',
    anchorId: 'term-boll-pct-b',
    title: '布林带位置 %B (boll_pct_b)',
    summary:
      '表示当前价格处于布林通道的相对位置：0 为下轨、0.5 为中轨、1.0 为上轨。≥ 0.85 表示贴近上轨拥挤，≤ 0.15 表示靠近下轨超跌。',
  },
  rsi14: {
    id: 'rsi14',
    anchorId: 'term-rsi14',
    title: '相对强弱指标 RSI14',
    summary:
      '14 日 Wilder 平滑涨跌动能指标（0~100）。通常 ≤ 38 偏超卖（≤ 30 深度超卖），≥ 65 偏拥挤过热，需结合趋势阶段判断。',
  },
  rs: {
    id: 'rs',
    anchorId: 'term-rs',
    title: '相对大盘强弱 RS',
    summary:
      '基金价格与基准指数（默认沪深300）的比值曲线。RS 站上自身均线且创新高、并持续跑赢基准，说明资金正在向该板块集中。',
  },
  'anti-chase': {
    id: 'anti-chase',
    anchorId: 'term-anti-chase',
    title: '防追高开关（过滤高位偏热）',
    summary:
      '开启后会把处于「高位偏热」阶段的标的从「今日可买信号」中隐藏，但不影响「主线观察 · 等回踩」，方便等待更舒服的回踩位置。',
  },
  't-plus-1': {
    id: 't-plus-1',
    anchorId: 'term-t-plus-1',
    title: 'T 日信号 → T+1 收盘净值确认',
    summary:
      '盘后根据 T 日收盘数据筛出信号，投资者只能在次一交易日（T+1）15:00 前申购并按 T+1 收盘净值成交，因此回看严格以 T+1 收盘价计成本、杜绝未来函数。',
  },
  'pending-nav': {
    id: 'pending-nav',
    anchorId: 'term-pending-nav',
    title: '待确认净值 · Pending NAV',
    summary:
      '在当日晚间官方净值尚未公布前记录买入/卖出时，交易批次先以待确认状态保存，晚间 21:30 或次早 09:00 同步后自动回填官方净值。',
  },
  'short-term-fee': {
    id: 'short-term-fee',
    anchorId: 'term-short-term-fee',
    title: '7 自然日 FIFO 短赎费规则',
    summary:
      '公募基金对持有不足 7 个自然日（约 5 个交易日）的份额强制收取 1.5% 惩罚性赎回费，且按先进先出（FIFO）逐笔买入批次独立计算。',
  },
  'exit-signals': {
    id: 'exit-signals',
    anchorId: 'term-exit-signals',
    title: '5 类可解释卖出/风控信号',
    summary:
      '围绕持仓实时监控 MA20 跌破、高位回撤（默认 -6%）、成本止损（默认 -8%）、高位偏热/达止盈与 7 日短赎费保护，仅作透明提醒不自动下单。',
  },
}

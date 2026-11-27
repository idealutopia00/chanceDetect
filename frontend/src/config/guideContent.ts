export interface StrategyGuideCopy {
  intuition: string
  formula: string
  bestMarket: string
  pitfalls: string
  keywords: string[]
}

export const STRATEGY_GUIDE_COPY: Record<string, StrategyGuideCopy> = {
  mid_term_uptrend: {
    intuition:
      '用最稳健的中长期均线结构识别“已经在上升通道里”的赛道，剔除长期空头或熊市下跌趋势中的杂音。',
    formula:
      '收盘价 > MA(长周期) 且 MA(中周期) > MA(长周期) 且 MA(长周期) >= N日前 MA(长周期)（斜率非下行）。',
    bestMarket: '震荡慢牛、结构性行业轮动、顺势做右侧波段的主力底盘滤网。',
    pitfalls:
      '均线天然滞后，在 V 型暴跌急反初期尚未翻多；且它是“趋势滤网”而非买点，应搭配回踩/启动触发器使用。',
    keywords: ['中期上升趋势', 'mid_term_uptrend', 'MA20', 'MA60', '斜率', '趋势滤网', 'regime'],
  },
  momentum_top_rank: {
    intuition:
      '把全市场场内基金按近 N 日收益（默认除以 20 日波动率做风险调整）横向排队，只盯资金最认可的前 K 名领涨主线。',
    formula:
      '先要求近 N 日收益 > 0（默认且收盘 > MA60），计算 风险调整动量 = ret_Nd / max(volatility_20d, 0.5)，取当日横截面排名前 top_k。',
    bestMarket: '主线鲜明、强者恒强的轮动行情，快速锁定资金抱团最稳的龙头方向。',
    pitfalls:
      '持续连涨的低波品种（如银行、红利）会长期霸榜 Top K，绝不能单靠排名直接追买，必须结合右侧回踩或防追高开关。',
    keywords: ['横截面动量', 'momentum_top_rank', 'Top K', '风险调整动量', '轮动', '趋势滤网'],
  },
  relative_strength_rs: {
    intuition:
      '拿基金走势除以大盘基准（默认沪深300 sh000300）得到相对强弱曲线 RS；大盘震荡或微跌时能逆势走强的板块往往是下一阶段领涨主线。',
    formula:
      'RS = close / benchmark_close；要求 RS > RS_MA(N) 且 RS >= 过去 N 日 RS 高点，同时近 N 日相对基准超额收益 >= 阈值(%)。',
    bestMarket: '大盘横盘震荡、存量博弈或指数刚企稳时，提前捕捉跑赢大盘的强势行业。',
    pitfalls:
      '熊市普跌中“跌得少”也可能算出正超额（相对强但绝对亏钱），建议与「中期上升趋势」或右侧买点组合使用。',
    keywords: ['相对强弱', 'relative_strength_rs', 'RS', '超额收益', '沪深300', 'sh000300', '基准'],
  },
  trend_pullback_entry: {
    intuition:
      '右侧交易最舒服的“倒车接人”买点：先确认处于中期上升趋势，等价格真正离开布林上轨并回踩到 MA10/MA20 支撑带附近，出现止跌阳线或翻红再上车。',
    formula:
      'MA20 > MA60 且收盘 > MA60；近 pullback_lookback 日内有回调动作并触及 MA10/MA20 ± band_pct 支撑区；当日收阳或翻红且收盘守住 MA20 支撑；同时满足 bias20 <= 上限、%B <= 上限、量比 <= 上限。',
    bestMarket: '主线趋势确立后的良性洗盘回踩，盈亏比高、止损位清晰（以 MA20 为界）。',
    pitfalls:
      '若回踩伴随异常巨量长阴派发（量比过大）或跌破均线后无企稳阳线，系统会自动过滤；切忌在跌破 MA20 放量下杀时强行当回踩接盘。',
    keywords: ['强势回踩', 'trend_pullback_entry', '回踩', 'MA10', 'MA20', '倒车接人', '支撑'],
  },
  ma_bullish_alignment: {
    intuition:
      '捕捉短/中/长均线刚刚理顺、从纠缠转为发散的“首日起爆点”，避免在多头已经排列两三个月的高位重复发出买点。',
    formula:
      '当日首次满足 MA(快) > MA(中) > MA(慢) 且前 cooldown_days 日未处于多头排列；收盘 >= MA(快)，快/中均线向上，且量比 >= 最小量比。',
    bestMarket: '底部盘整突破或回调结束后重新启动的右侧第一波发散行情。',
    pitfalls:
      '窄幅横盘震荡市容易出现均线反复粘合、频繁假金叉；搭配 Trend Regime 滤网或关注 Fresh Signal 标签可显著降噪。',
    keywords: ['均线多头启动', 'ma_bullish_alignment', '首日', 'MA5', 'MA10', 'MA20', '多头排列'],
  },
  volume_high_breakout: {
    intuition:
      '真金白银放量突破近 N 日平台高点，代表上方套牢盘被消化、趋势进入加速；同时内置 MA20 正乖离上限，过滤已经涨太高才放量的末端追高。',
    formula:
      '收盘价 > 过去 lookback_days 日最高收盘价，量比 >= vol_multiplier，当日涨幅 >= min_pct_chg，且 bias20 <= max_bias20_pct（设为 0 则关闭乖离限制）。',
    bestMarket: '箱体震荡末期放量向上突破、或主线休整后重新创阶段新高的加速点。',
    pitfalls:
      '场外申购按 T+1 收盘净值确认，若 T 日大涨 6%+ 触发突破，次日高开回落容易吃短线波动；务必结合 Trend Stage 与 T+1 回看胜率评估。',
    keywords: ['放量突破', 'volume_high_breakout', 'N日新高', '量比', '突破', '防追高'],
  },
  macd_golden_cross: {
    intuition:
      '经典动能转强信号：快线 DIF 上穿慢线 DEA、绿柱翻红，标志下跌或调整动能衰减、多头力量重新占优。',
    formula:
      '当日 DIF > DEA 且前一日 DIF <= DEA（默认 EMA12 / EMA26 / DEA9）；开启 above_zero_only 时额外要求 DIF > 0（零轴上方强势金叉）。',
    bestMarket: '零轴上方回调后的二次金叉最具爆发力；零轴下方金叉多为超跌反弹，需结合左侧思维。',
    pitfalls:
      '弱势阴跌中的零轴下金叉常演变为“死猫跳”；右侧趋势用户建议开启「零轴上方」或叠加中期上升趋势滤网。',
    keywords: ['MACD', 'macd_golden_cross', '金叉', 'DIF', 'DEA', '零轴'],
  },
  rsi_oversold_rebound: {
    intuition:
      '专抓短期情绪恐慌砸出的超跌坑：当 RSI 跌入超卖区后不再继续创低、而是拐头向上收阳，往往对应短线筹码出清后的修复反弹。',
    formula:
      '近 3 日最低 RSI(N) <= oversold_threshold（默认 30）；开启 require_rebound 时额外要求当日 RSI > 前日 RSI 且收盘 >= 开盘（收阳企稳）。',
    bestMarket: '急跌洗盘、情绪宣泄后的左侧超跌反弹，适合配合「左侧超跌抄底」模板使用。',
    pitfalls:
      '强空头单边熊市中 RSI 会在低位反复钝化（“地板下面还有地下室”），左侧交易务必分批轻仓、忌满仓一把梭。',
    keywords: ['RSI', 'rsi_oversold_rebound', '超卖', '反弹', '左侧', '抄底'],
  },
  boll_lower_reversal: {
    intuition:
      '价格跌穿布林带下轨（均值减 2 倍标准差）属于小概率极端偏离，随后收阳重新站回下轨之内，说明极端抛压暂告一段落、有向中轨（MA20）回归的动力。',
    formula:
      '前 touch_lookback 日内最低价或收盘价触及/跌破布林下轨；当日收阳线（close > open）且收盘价重新站回下轨之上、但仍在布林中轨之下。',
    bestMarket: '宽幅箱体震荡市、或上升趋势中的急跌挖坑反抽，距中轨空间清晰可量化。',
    pitfalls:
      '遇到基本面突发利空导致的“开口向下贴下轨单边暴跌”时，一次小阳反抽可能是下跌中继，注意控制仓位与 7 日短赎费窗口。',
    keywords: ['布林下轨', 'boll_lower_reversal', '布林带', '反抽', '均值回归', '左侧'],
  },
  ma_negative_bias: {
    intuition:
      '橡皮筋向下拉得越长、回弹张力越大：当收盘价大幅低于 MA20 形成深度负乖离，且当日跌幅收窄或翻红时，提示进入左侧逢低分批关注区间。',
    formula:
      'MA(N) 乖离率 = (close - MA_N) / MA_N * 100% <= -min_negative_bias_pct（默认 -5%），且当日涨跌幅 >= min_day_pct_chg（默认 -1%，过滤暴跌接飞刀）。',
    bestMarket: '恐慌性急杀后的左侧分批定投/波段低吸，适合宽基或高景气行业错杀时使用。',
    pitfalls:
      '高波动赛道（如半导体、科创）正常回撤就容易触发 -5%，而低波动宽基（如红利、银行）很少达到 -5%，建议结合 bias20_z 与历史回看综合判断。',
    keywords: ['负乖离', 'ma_negative_bias', '乖离率', '超跌', '左侧', '接飞刀'],
  },
}

export interface TemplateGuideCopy {
  useCase: string
  watchlistTip: string
  keywords: string[]
}

export const TEMPLATE_GUIDE_COPY: Record<string, TemplateGuideCopy> = {
  right_side_mainline: {
    useCase:
      '日常盘后首选：先用「中期上升趋势」或「RS 超额强弱」锁住主线赛道，再从回踩、首日多头、突破、MACD 金叉里挑出当日可买机会。',
    watchlistTip:
      '没触发买点的主线赛道会自动沉淀到「主线观察 · 等回踩」标签页，帮你持续盯住强势行业，等回踩 MA10/MA20 再动手。',
    keywords: ['右侧主线买点', '默认模板', '主线观察', '右侧'],
  },
  left_side_oversold: {
    useCase:
      '市场急跌或情绪冰点时使用：主动清空全部趋势滤网，直接用 RSI 超卖、布林下轨反抽、负乖离极值寻找左侧错杀修复机会。',
    watchlistTip:
      '因为未开启趋势过滤，此模板下所有命中都会直接进入「今日可买信号」，不会产生主线观察池；建议分批介入、拉长持有周期。',
    keywords: ['左侧超跌抄底', '左侧', '超跌', '反弹', '均值回归'],
  },
  strong_momentum_focus: {
    useCase:
      '结构性主升浪聚焦：要求同时满足「动量 Top K」且「RS 跑赢大盘」（AND 逻辑），只保留全市场最硬核的龙头主线，并仅看强势回踩或放量突破。',
    watchlistTip:
      '选出的「主线观察 · 等回踩」名单含金量极高，往往就是当前市场资金抱团最紧的少数核心方向，适合配合「过滤高位偏热」防追高。',
    keywords: ['强势动量聚焦', '动量', 'Top K', 'RS', '龙头', '主线'],
  },
}

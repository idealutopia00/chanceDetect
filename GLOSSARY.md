# ChanceDetect

A post-close daily fund screening domain for off-exchange mutual fund investors who detect trading opportunities using liquid on-exchange funds and execute via mapped off-exchange funds.

## Language

### Universe & Mapping

**On-Exchange Proxy Fund**:
A listed ETF or LOF whose daily price and volume history serves as the analytical proxy for strategy evaluation.
_Avoid_: Target ETF, exchange symbol, ticker

**OTC Counterpart**:
An off-exchange mutual fund share class (an ETF feeder fund, a self-subscribable LOF, or a sibling LOF share class) strictly mapped to an **On-Exchange Proxy Fund** for actual subscription.
_Avoid_: Off-exchange fund, feeder, linked fund

**Index Opportunity Group**:
A deduplicated screening result aggregating all matched **On-Exchange Proxy Funds** that track the same underlying index, represented by the most liquid fund and exposing all mapped **OTC Counterparts**.
_Avoid_: Fund cluster, result card

### Signal Model

**Trend Regime**:
A continuous directional or relative-strength condition identifying which **On-Exchange Proxy Funds** currently lead or maintain a healthy trend; used to qualify candidates rather than trigger immediate buys on its own.
_Avoid_: Regime strategy, holding signal, direction filter

**Entry Trigger**:
A discrete timing event—such as a first-day moving-average formation, breakout, trend pullback stabilization, or oversold bounce—marking a favorable risk-reward point to act on the next **Execution Date (T+1)**.
_Avoid_: Buy signal, trigger strategy, entry rule

**Actionable Opportunity**:
An **Index Opportunity Group** that passes the active **Trend Regime** filters (when any are selected) and fires at least one **Entry Trigger** on the **Signal Date (T)**.
_Avoid_: Buy hit, scan hit

**Mainline Watchlist**:
An **Index Opportunity Group** that satisfies the active **Trend Regime** filters on the **Signal Date (T)** without firing an **Entry Trigger**, tracked as a strong trend awaiting a pullback or fresh setup.
_Avoid_: Watchlist candidate, hold-only hit

**Trend Stage**:
A volatility-normalized classification of a fund's price extension and short-term crowding—`Oversold`, `Early`, `Mid`, or `Overheated`—used to separate early startups and healthy pullbacks from stretched late-stage rallies.
_Avoid_: Position level, heat level, phase

**Fresh Signal**:
An **Entry Trigger** firing on the **Signal Date (T)** after a clean cooldown window with no prior trigger for that strategy combination, distinguished from repeated triggers in an already extended move.
_Avoid_: First hit, new entry, streak-one

### Execution & Evaluation Cadence

**Signal Date (T)**:
The completed trading day whose closing data triggers a strategy hit, reviewed by the investor before the 15:00 cutoff on the next trading day.
_Avoid_: Trigger day, scan date

**Execution Date (T+1)**:
The trading day immediately following the **Signal Date (T)** on which an investor places an off-exchange subscription before 15:00 and receives that day's closing net asset value as entry cost.
_Avoid_: Entry day, purchase day

**Holding Horizon**:
The number of trading days elapsed after the **Execution Date (T+1)** used to measure historical forward return and drawdown, aligned to avoid the 7-calendar-day short-term redemption penalty.
_Avoid_: Lookahead window, backtest period

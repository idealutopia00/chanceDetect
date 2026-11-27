---
status: accepted
---

# Sync Off-Exchange NAV Only for Tracked Positions Using Pending NAV Backfill

ChanceDetect does not scrape historical daily NAV for all 1,300+ **OTC Counterparts** during the `16:15 CST` post-close sync. Instead, daily K-line ingestion remains focused on **On-Exchange Proxy Funds**, while off-exchange NAV history is fetched and cached lazily—only for funds referenced by a **Tracked Position**—via scheduled evening/morning refreshes (`21:30 CST` and `09:00 CST`) plus on-demand refresh. When a user records a **Trade Lot** on a trading day whose official closing NAV is not yet published, the lot is saved immediately with a **Pending NAV** state and automatically backfilled once the official NAV arrives (while always allowing manual override).

Because off-exchange mutual fund NAVs are published hours after market close (19:00–22:30 CST, or T+1 evening for QDII funds), bundling OTC NAV into the `16:15 CST` sync would still miss same-day closing NAVs while multiplying external network calls by an order of magnitude for funds the user never holds. Decoupling the two sync cadences keeps full-market screening fast while ensuring accurate same-day trade recording and automated post-publication reconciliation.

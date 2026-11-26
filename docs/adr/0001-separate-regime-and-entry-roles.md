---
status: accepted
---

# Separate Trend Regime Filters from Entry Triggers

We split strategies into two explicit domain roles—**Trend Regime** filters (continuous trend or relative-strength states) and **Entry Triggers** (discrete timing events)—and evaluate them in a two-stage funnel that outputs separate **Actionable Opportunities** and **Mainline Watchlist** buckets.

In v1, a single flat list of boolean strategies caused persistent low-volatility leaders (such as bank ETFs after a 5-month rally hugging their upper Bollinger band) to rank at the top of daily buy recommendations on every bar via momentum and relative-strength signals. Separating regime qualification from entry timing keeps strong trends visible for pullback watching without presenting stretched holding states as immediate buy triggers, while still allowing standalone left-side oversold scans when no regime filter is selected.

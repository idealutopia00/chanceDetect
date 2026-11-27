---
status: accepted
---

# Separate Tracked Position Lifecycles with Moving-Average Cost and FIFO 7-Day Fee Tracking

Each holding campaign for an off-exchange fund is modeled as an independent **Tracked Position** containing one or more **Trade Lots** (buys, adds, partial sells, and full exit). Fully redeeming a position archives that round-trip; buying the same fund again opens a new **Tracked Position** with a clean zero-based baseline curve. Position cost basis and P&L use moving weighted average, whereas eligibility to avoid China's 1.5% short-term redemption penalty (`>= 7` calendar days) is evaluated strictly per **Trade Lot** in FIFO order. Amount and shares are optional on every **Trade Lot**, gracefully degrading to percentage-only return and fractional-sell tracking when omitted.

Mixing weighted-average cost with FIFO redemption-fee checks is deliberate rather than inconsistent: weighted average matches how investors track average cost across scale-ins, while Chinese mutual fund registrars legally enforce the 7-calendar-day short-term redemption fee per subscribed share lot on a FIFO basis. Archiving closed round-trips also prevents a later swing trade from distorting earlier review curves and win-rate statistics.

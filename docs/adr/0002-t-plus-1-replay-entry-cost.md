---
status: accepted
---

# Evaluate Replay Forward Returns from Execution Date (T+1) Close

Historical signal replay measures entry cost at the **Execution Date (T+1)** closing price rather than the **Signal Date (T)** closing price, and evaluates forward returns over 5, 10, 20, and 40 trading days after `T+1`.

Because the investor reviews post-close data from day `T` before 15:00 on day `T+1` and subscribes to an **OTC Counterpart**, the subscription is confirmed at `T+1` closing NAV and incurs a 1.5% short-term penalty if redeemed within 7 calendar days (~5 trading days). Measuring returns from day `T` close would introduce a 1-day look-ahead bias that cannot be captured in off-exchange execution; indexing entry at `idx + 1` is therefore intentional and must not be "simplified" back to `idx`.

# Trend Following — Parked Pending Paper Trade Validation

**Date:** 2026-04-17
**Decision:** Do not implement 50-day channel trend following on XAUUSD at this time.
**Revisit trigger:** After 4–6 weeks of ORB paper trading delivers at least 10 real fills, OR after a trend following signal fires during a sustained gold pullback (whichever comes first).

---

## Context

On 2026-04-17, a trend following strategy was researched and reported (see `2026-04-17_trend_following_and_session_handoff.md` and `2026-04-17_trend_following_v2_reality_check.md`). Headline: 50-day channel breakout on XAUUSD, longs-only, next-day open entry, Sharpe 8.83 walk-forward-validated.

On review, this is not ready to run. Parking it in code (backtests keep, live wiring does not get added) is the right call.

---

## Why it's parked

### 1. The "edge" is mostly long gold beta

V2 reality-check explicitly states: **"52% of the edge is just being long gold during a 322% rally."** Buy-and-hold of 2 oz on the same window returns +$7,887 with 21% max drawdown; the strategy returns +$4,063 with 1% max drawdown. The strategy is a risk-controlled way to hold gold, not an alpha source. That is fine as a product — but it is not an uncorrelated addition to ORB.

### 2. Zero bear-market data

Sample: 2018-01 → 2026-04. Gold went from $1,222 to $5,166 over the entire window. Days below the 200d SMA in the later years:

- 2024: 0/313 (0%)
- 2025: 0/311 (0%)
- 2026: 0/48 (0%)

There is no data point where a longs-only gold breakout system was tested in a sustained gold downtrend. The 2011–2015 gold crash (-45% over 4 years) is outside the sample. The SMA200 regime filter "survives OOS" only because it never activates.

### 3. Sample size is tiny

Best config (50d channel, longs-only, next-day open): **27 trades over 8 years.** 19 winners, 8 losers. A single bad year could flip the headline metrics. Walk-forward with 14 IS trades and 13 OOS trades does not provide confidence — it provides the illusion of confidence.

### 4. Correlated with ORB, not diversifying

Both strategies profit when XAUUSD moves directionally. Both are long-biased. Both depend on gold continuing to trend. Running them simultaneously concentrates exposure on the same regime, not diversifies it. Genuine diversification would require a short-biased or mean-reverting strategy on a different asset.

### 5. ORB hasn't delivered a real paper trade yet

This is the load-bearing reason. The session that produced this research happened in parallel with finishing the ORB hardening that was supposed to unblock paper trading. Instead of collecting ORB paper data, scope expanded to VWAP, carry-momentum, three pairs-trading variants, two session mean-reversion variants, London gap, volume imbalance, trend following V1, and trend following V2.

**Zero real paper fills exist.** Adding a second strategy before the first has produced any ground truth is the opposite of disciplined development.

---

## What stays, what goes

### Stays in the repo

- `v11/backtest/backtest_trend_following.py` — V1 research
- `v11/backtest/backtest_trend_following_v2.py` — V2 with realistic adjustments
- The two trend-following journals

These are kept as research artifacts. If the revisit trigger fires, the backtests are there to re-run on updated data.

### Does NOT get added

- Trend following engine in `v11/live/`
- Trend following adapter or strategy config
- Any trend-following wiring in `run_live.py`
- Any mention in `LiveConfig`

The live code stays focused on: ORB (active) + Darvas/4H (disabled pending EURUSD data fix).

---

## Revisit conditions

Unpark trend following when one of the following is true:

1. **ORB paper results are in.** 10+ real paper trades, >4 weeks of runtime. Paper results compared to backtest expectations for WR, AvgR, fill quality. This is the primary gate.

2. **A trend signal fires in a non-bull regime.** If XAUUSD pulls back meaningfully (multi-week decline, drops below 200d SMA) and the strategy correctly stays flat or correctly catches a short reversal, that is a real out-of-sample signal. Re-evaluate at that point.

3. **User request.** If the user wants to deploy it sooner despite these concerns, that is the user's call — but the concerns in this document should be re-read before doing so.

---

## What to do meanwhile

- Paper trade ORB: `start_v11.bat --live --no-llm`
- Accept the slow tempo: ~1 trade/week with gap filter, so ~4–5 months for 20 trades
- Use the quiet time to: (a) finish EURUSD data integrity work, (b) reconcile the two ORB backtest scripts (pick one, delete the other), (c) investigate 2025 concentration (new backtest shows 59% of P&L comes from 2025 alone — a concerning regime-dependency finding that needs its own follow-up)
- Resist the urge to ship a new strategy. If a new idea is compelling, write it down in a "parked" file like this one. Ship nothing strategy-related until paper data exists.

---

## Meta-note on the preceding 24 hours

Between the ORB hardening plan execution (2026-04-16 evening) and this parking decision (2026-04-17 evening), the following research was produced:

- Volume imbalance (7 pairs) → no edge
- VWAP drift (8 pairs) → no edge
- Carry + momentum → underperforming
- Asian range fade → no edge after costs
- London gap fill (7 pairs) → no edge after costs
- NY VWAP drift → no FX volume data
- Daily pairs trading → look-ahead bias
- Intraday pairs trading → edge collapses
- Cointegration scan (30 FX pairs) → none reliably cointegrated
- Multi-pair London gap → break-even after costs
- Trend following V1 → scope created, then revised
- Trend following V2 → reality-check, ended in this parking decision

None of this was requested by the paper trading roadmap. The research itself was not wasted — all are now documented as "tried, doesn't work" — but the opportunity cost was real: the paper trading that should have started four to six weeks ago still has not started.

The lesson is not "don't research." The lesson is: **when the validation gate is waiting for ground truth, research goes into scratch files, not into new production backtest scripts that parallel existing ones.** Protect the validation runway.

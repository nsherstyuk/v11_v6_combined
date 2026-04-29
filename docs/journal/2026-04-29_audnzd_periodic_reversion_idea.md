# AUD/NZD Periodic Mean-Reversion — Idea Capture & Investigation Plan

**Date:** 2026-04-29
**Status:** IDEA / PRE-INVESTIGATION. Data download in progress.
**Question:** Does AUD/NZD exhibit a tradeable, periodic mean-reverting edge during the Asian session, and is the edge robust to spread cost?

---

## 1. Origin of the idea

Started from a real-world observation about local gasoline prices showing predictable weekly/daily cycles (mornings high, Thursday/Sunday nights low). User explored with Gemini whether any tradeable instrument behaves similarly. Gemini suggested several:

1. **AUD/NZD overnight Asian session** — range compression when US/EU markets are closed
2. **VIXY** — volatility mean reversion
3. **JPST/MINT** — ultra-short bond ETFs hovering near par
4. **Pairs trading** — e.g., KO/PEP spread
5. **NG / power futures** — physical demand cycles (morning ramp, overnight trough)

User produced a one-page strategy summary (`Periodic_Trading_Strategy_Summary.pdf`). It is too generic to drive code (no entry/exit rules, no thresholds, no spread model, no acceptance criteria). The four non-FX strategies were reviewed and ranked:

- **Pairs trading** — same family as AUD/NZD; we already have `backtest_pairs_statarb.py` and `backtest_pairs_halflife.py` exercising 20+ FX pairs. Worth expanding *after* AUD/NZD.
- **NG / power futures** — physically real cycle but requires futures permissions, contract roll, weather-shock handling, and shares no infra with the FX stack. Defer.
- **VIXY** — structural ~6%/month contango bleed makes long-VIXY a textbook retail trap. Reverts on event/multi-day timescales, not periodic. Skip.
- **JPST/MINT** — savings-account-equivalent yield; daily range is single basis points. Not a strategy. Useful only as idle-margin parking.

**Decision:** investigate AUD/NZD first. If successful, the natural extension is the existing FX-pairs stat-arb infrastructure.

## 2. What we already have

- **Data download in progress** (task `bia00z9df`): AUD/NZD 1-min bars 2018-01-01 → 2026-04 from Dukascopy via `tick_vault`. Same format as EURUSD/XAUUSD/GBPUSD: open/high/low/close + tick_count + avg_spread/max_spread + buy_volume/sell_volume + buy_ratio. **Bid/ask is in there**, so realistic spread cost modeling is possible.
- Output: `C:\nautilus0\data\1m_csv_fresh\audnzd_1m_tick.csv`. Expected ~3M rows on completion.
- Daily bars already present: `C:\nautilus0\data\fx_daily\AUDNZD_daily.csv` (2018-04 → 2026-04).
- AUD/NZD is in `backtest_pairs_statarb.py`, `backtest_pairs_halflife.py`, and `backtest_carry_momentum_v2.py` — but those are daily-bar studies; intraday is unexplored.
- `tick_vault.constants.PIPET_SIZE_REGISTRY` did not include AUDNZD. Patched at runtime in `download_audnzd.py` to register `1e-5`.
- Two known download issues fixed during this session:
  - `strict=False` on `read_tick_data` (Dukascopy DST-boundary chunks end at 22:00 UTC, not 23:59).
  - `asyncio.wait_for(..., timeout=300.0)` on each per-week call (tick_vault hangs silently on some network conditions; without the wrapper the 3-retry policy never fires).

## 3. Critique of Gemini's framing — things to NOT carry forward

- **"20:00–02:00 ET window"** — using ET hides DST. Across 8 years the ET-to-UTC offset shifts twice a year, and AU/NZ DST shifts oppositely. **All analysis in UTC.** Derive Sydney/Tokyo session boundaries from UTC, not ET.
- **"60–80% win rate, 30 pip risk for 10 reward"** — asserted without source; that R/R needs ~75% just to break even before costs. Treat as unverified marketing.
- **"ANZ ETF"** — ANZ is the AU/NZ Banking Group stock ticker, not a currency ETF. There is no retail AUDNZD ETF.
- **April 2026 RBA hike pricing** — Gemini hallucinated a current-events claim. Ignore.
- **"Use polars / fresh codebase / 4M rows is too much"** — wrong for our context. Pandas handles 3M rows fine, and we have replay/gap/cost infrastructure to reuse.
- **Spread cost barely discussed.** Retail AUDNZD spread is typically 1–3 pips. If targeting 10-pip reversions, costs eat 10–30% of gross. Spread is a first-class variable, not a footnote.

## 4. Hypothesis (sharpened)

> During the **22:00–06:00 UTC** window (Sydney/Tokyo trading, no London/NY participation), AUD/NZD exhibits lower drift and higher mean-reversion than during 06:00–22:00 UTC. A simple range-fade rule entered after a measurable "rubber band stretch" produces positive expectancy *after spread cost* across 2018–2023 (OOS) and survives 2024+ (IS).

Falsifiable. Has a defined window, a defined cost handling, and a defined OOS/IS split that matches the V11 convention.

## 5. Investigation plan

### Phase 0 — wait for download
Currently ~58% done as of 18:53 EDT, ~32 weeks/hour pace. Expected completion overnight.

### Phase 1 — Exploratory Data Analysis (no strategy yet)
Output: `v11/backtest/investigate_audnzd_eda.py` + a markdown summary.

1. **Hourly UTC profile** of `abs(close - open)` averaged across all dates. Expected shape: trough during 22:00–06:00 UTC, peaks at London open (07:00 UTC) and NY open (13:00 UTC). If the trough does not exist, hypothesis fails immediately.
2. **Hourly UTC profile** of *signed* returns. Tests for any persistent drift inside the quiet window (a real edge needs the window to *not* trend systematically).
3. **22:00–06:00 UTC range distribution** in pips, by year and by day-of-week. Check that the distribution is stable (not regime-dependent). Median, p25/p75, p95.
4. **Realized spread**: `avg_spread` / pip-size, by hour UTC. Sanity check that spread *during* the proposed window is not catastrophic. If the window's median spread > 1.5 pips, the strategy is dead before it starts.
5. **Day-of-week effect** on overnight range and on next-day reversion. Is Monday different from Thursday? (Per the user's gasoline observation, day-of-week is part of the question.)
6. **Contiguous calendar coverage** check: how many trading nights are in the dataset, how many are skipped (we already have ~6 skipped weeks from Dukascopy gaps), are skips concentrated?

**Gate:** Phase 1 produces a verdict — does the data show the regime split that the hypothesis requires? If no, document and stop.

### Phase 2 — Naive baseline (no parameters tuned)
Single fixed rule, no grid, no optimization. Pure sanity check.

- Compute the 22:00–02:00 UTC range each night.
- If price at 02:00 UTC is in the upper third of that range, sell at market; if in the lower third, buy at market. Exit at 06:00 UTC.
- Use the **bid for sells / ask for buys** (the columns we have) — no mid prices, no fudging.
- Per-trade cost = realized spread at entry + at exit.
- OOS = 2018-01 → 2023-12. IS = 2024-01 → present.
- Report N, WR, AvgR, PF, MaxDD on each split. **No grid search at this phase.**

**Gate:** is OOS AvgR > 0 *after* spread cost? If yes, proceed. If no, re-examine in Phase 3, but bias toward stopping.

### Phase 3 — Filtered strategy
If Phase 2 produces signal, layer filters one at a time and measure marginal contribution:

- **Range filter**: only trade if the 22:00–02:00 UTC range is between the 25th and 75th percentile of historical (avoid dead nights and breakout nights).
- **Day-of-week**: drop the worst day if there is one, but only if the drop is statistically meaningful, not just the worst-fitting day.
- **News blackout**: blacklist scheduled RBA/RBNZ rate decisions and AU CPI/employment days. Use ForexFactory/IBKR calendar history.
- **Spread filter**: skip if avg_spread at entry minute > 2× the window's median spread.

Each filter must justify itself on OOS, not improve only IS.

### Phase 4 — V11 integration spike
Only if Phase 3 OOS AvgR > 0.10 with PF > 1.3 after costs:
- Sketch how this would slot into the existing strategy runner (sibling to V6_ORB).
- Decide live config (paper trade first; ORB is currently live, AUDNZD would be additional).
- Live position sizing must reuse the existing risk manager, not invent a new one.

## 6. What we are NOT doing

- Not building a fresh codebase. Reuse `tick_vault`, replay engine, `_INSTRUMENT_CONFIGS`, V11 grid harness.
- Not investigating VIXY, JPST/MINT, or NG futures unless AUD/NZD work surfaces a reason to revisit.
- Not optimizing a parameter grid before Phase 2 baseline runs. Refuse to tune before knowing whether there's anything to tune toward.
- Not assuming Gemini's claimed win rates / R/R numbers. Measure them ourselves.

## 7. Open questions to resolve before / during work

- What instrument config does AUDNZD need in `_INSTRUMENT_CONFIGS`? Pip = 0.0001 (i.e., 4-decimal pip even though the price has 5 digits — the 5th is a "pipette"). Confirm against a known broker datasheet before any pip-based threshold is set.
- Does IBKR support spot AUDNZD trading at the user's account class? (Pretty sure yes via IDEALPRO, but verify before any live discussion.)
- Where do RBA/RBNZ release time histories come from? The forex-factory calendar archive is the obvious source but needs scraping/loading.

## 8. Status

Idea captured. Plan written. Download running. No code in `v11/backtest/` for this yet — investigation begins after data lands.

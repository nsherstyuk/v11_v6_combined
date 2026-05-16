# USDJPY ORB — pre-registration

**Date locked:** 2026-05-15
**Status:** Pre-registered. Data download in flight. No backtest
results have been viewed at the time this file was written.

## Hypothesis

The XAUUSD ORB pattern (Asian-range, London/NY-break) generalizes
to USDJPY. Specifically: the range built during the Asian session
(00:00–06:00 UTC) is broken during London/Overlap (08:00–16:00 UTC)
with positive expectancy after slippage.

If true: v11 gains a second uncorrelated strategy on the same
infrastructure. If false: XAU's success is pair-specific, and the
"any session-structured FX pair" thesis from
`docs/superpowers/2026-05-15-retail-fx-discussion` is weaker.

## Pair / instrument

- **Pair:** USDJPY (IBKR Forex on IDEALPRO, contract conId issued
  on `qualifyContracts` at run time)
- **Bar resolution:** 1-min for backtest fidelity
- **Data range:** 2005-03-09 (IBKR head) → present (2026-05-15)
  ≈ 21 years
- **Data type for entry/exit prices:** MIDPOINT close
- **Data type for cost model:** empirical (ASK − BID) at trade
  timestamp from downloaded BID and ASK files

## Parameters (locked, identical to XAUUSD ORB)

| Parameter | Value |
|---|---|
| range_start_hour | 0 UTC |
| range_end_hour | 6 UTC |
| trade_start_hour | 8 UTC |
| trade_end_hour | 16 UTC |
| rr_ratio | 2.5 |
| min_range_pct | 0.05% of mid |
| max_range_pct | 0.50% of mid |
| Order style | STP LMT with `lmt_buffer = max(0.02 JPY, 0.1 × range_width)` |
| Position size | 10,000 USD notional (1 mini lot) |
| Skip weekdays | () empty (consistent with v11 live default 2026-05-12) |

The XAU values translate to USDJPY pip-equivalent:
- min_range_pct=0.05% × 145 = ~7 pips minimum
- max_range_pct=0.50% × 145 = ~75 pips maximum
- lmt_buffer floor 0.02 JPY = 2 pips (matches the $0.50 floor on
  XAU in spirit)

## IS / OOS split

- **IS (in-sample):** 2023–2025 (3 years)
- **OOS (out-of-sample):** 2019–2022 (4 years)
- **Pre-2019:** 2005–2018, NOT used for the IS/OOS decision but
  retained as a "third holdout" for sanity checks after the
  decision is made.

The IS/OOS allocation is INTENTIONALLY the same as both prior
research arcs (equity ORB, GBPCHF SMA-cross) so the discipline is
identical and comparable.

## Cost model

Round-trip cost per trade:
- Empirical spread from BID/ASK at entry timestamp (~0.5–1.0 pip on
  USDJPY at IDEALPRO during London/NY hours)
- Plus slip estimate: 0.5 pip per side (1 pip RT total)
- Plus IBKR commission: 0.20 bps × notional × 2 sides, $2 min/side
  = $4 RT at typical retail size, dominated by minimum
- Pip value at 10k notional ≈ $0.91/pip
- Total cost ≈ 1.5–2 pips × $0.91 + $4 ≈ $5.5/trade

If empirical BID/ASK isn't available at entry time (e.g., pre-2018
data depth), fall back to 1 pip RT slip+spread + $4 commission.

## Decision gate (pre-registered)

The strategy passes if:
- **OOS AvgR_slip ≥ +0.05R** (AvgR after slippage on the 2019–2022
  out-of-sample window)
- **AND N/yr ≥ 20** (at least 20 trades per year on average)

These are the same thresholds used for the equity ORB research.

Year-by-year breakdown will also be reported but is NOT a gate —
it's a sanity check for regime stability after the gate decision.
A pass on the gate with a clearly-deteriorating year-by-year trend
will still be flagged but the gate determines whether to proceed.

## What we do if the strategy passes

1. Journal the result with full per-year numbers.
2. Write a deployment design spec: contract qualification path,
   v11 adapter parameters, risk-manager configuration, position
   sizing, daily-restart compatibility.
3. **Do NOT deploy immediately.** Run paper for 4 weeks at the
   same risk level v11 currently uses, monitor for plumbing
   issues (Phase 6-equivalent proof-of-life), then evaluate.
4. If paper validates: real-money deployment requires separate
   Nick approval per CLAUDE.md.

## What we do if the strategy fails

1. Journal the null with full per-year numbers.
2. Identify whether any sub-condition (session subset, range size
   bucket, specific year) showed differential behavior.
3. **Do NOT re-run with tuned parameters.** That's curve-fitting.
4. If a sub-pattern looks interesting, pre-register it as a
   SEPARATE hypothesis on a different day, with its own gate.

## What we do NOT do regardless of outcome

- Run Variant B (pre-Tokyo range, Tokyo break) in parallel. That's
  multiple comparison. If Variant A fails and we still want to
  research USDJPY-specific patterns, Variant B is a separate
  pre-registration on a different research day.
- Test additional rr_ratios, range_end_hours, trade windows, etc.
  All parameters above are locked.
- Test other JPY-quoted pairs (EURJPY, GBPJPY, AUDJPY) as a
  "rescue" if USDJPY fails. Same multiple-comparison hazard.

## Data status

- IBKR head probe (2026-05-15 10:08 EDT): MIDPOINT available from
  **2005-03-09 04:30 UTC**.
- Downloader PID 24648, started 2026-05-15 10:10 EDT.
- Output to `tick_vault_data/fx/USDJPY_{MIDPOINT,BID,ASK}.csv`.
- Estimated runtime: ~25–30h (21 years × 3 data types at observed
  ~400K rows/hr pace).

## See also

- v11/backtest/download_usdjpy_sequential.py — downloader
- v11/backtest/orb_fx_grid.py — existing FX ORB grid pattern; will
  be the basis for the USDJPY-specific backtest script
- v11/live/run_live.py:128 — XAUUSD_ORB_CONFIG (the v11 live
  baseline these parameters mirror)
- docs/journal/2026-05-11_orb_us_equities_regime_change.md — prior
  research arc whose IS/OOS gate we are reusing here

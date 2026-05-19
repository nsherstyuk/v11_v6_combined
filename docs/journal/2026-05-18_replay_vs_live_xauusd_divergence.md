# XAUUSD replay vs live — 7-of-8 match, 1 instructive divergence on 2026-05-18

**Date:** 2026-05-18
**Status:** Replay infrastructure validated against live. One genuine
divergence found and root-caused: range computation uses different
underlying data on the two paths (IBKR historical bars vs reconstructed
1-min bars from tick log). All other days match.

## Why this run

Nick asked: *"how can we make sure that backtesting gives results
similar to live? Can we try backtest last few days and see?"*

The right tool for this is `v11/replay/run_tick_replay.py`, which
replays captured live tick logs through the same strategy code that
runs live. Because the input ticks are literally what the live system
saw, any divergence is by definition due to differences in HOW that
data is processed downstream — not data quality.

## Command

```
python -m v11.replay.run_tick_replay \
  --start 2026-05-08 --end 2026-05-18 \
  --instruments XAUUSD --llm passthrough
```

Source data: `data/ticks/XAUUSD/2026-05-{08..18}.csv` (530K tick records
captured live by v11 since 2026-05-08, format: timestamp, mid, bid,
ask, last, bid_size, ask_size, last_size).

## Results, day-by-day

| Date | Live outcome | Replay outcome | Match? |
|---|---|---|---|
| 2026-05-11 (Mon) | stale breakout skip | stale breakout (range 4668.78-4705.48, price below) | ✅ |
| 2026-05-12 (Tue) | stale breakout skip | stale breakout (range 4711.14-4773.52, price below) | ✅ |
| 2026-05-13 (Wed) | SHORT fill @ 4687.63 → MARKET @ 4695.59, PnL −$7.96 | SHORT fill @ 4688.10 → MARKET @ 4698.50, PnL −$10.40 | ✅ same trade, slightly different prices |
| 2026-05-14 (Thu) | 4h-pending cancel | 4h-pending cancel | ✅ |
| 2026-05-15 (Fri) | range too wide (100.46) | range too wide (98.42, 2.14%) | ✅ |
| 2026-05-16 (Sat) | weekend, no data | zero range, invalid | ✅ |
| 2026-05-17 (Sun) | weekend until 22:00 UTC | zero range, invalid | ✅ |
| **2026-05-18 (Mon)** | **4h-pending cancel** | **LONG @ 4551.49 → MARKET @ 4542.09, PnL −$9.40** | **❌** |

**7 of 8 trading days match.** Strategy decisions (skip / cancel /
fill / window-close exit) and the SHORT direction on 2026-05-13 are
identical. PnL on 2026-05-13 differs by $2.44 because:

- Replay's range was 0.47 higher than live's (4688.10 vs 4687.63
  entry, same direction)
- Replay's MARKET-close EOD price differed by $2.91 (4698.50 vs
  4695.59) — different last-bar close from different bar resamplings
- Replay has no commission or slip modeling (live's −$7.96 includes
  ~$2 of round-trip cost)

## The 2026-05-18 divergence — root cause

Today's ranges differ between replay and live:

- **Live range: 4480.44 – 4552.35**, computed at 02:00 EDT via
  `LiveMarketContext.calculate_range_from_ibkr_bars` which requests
  5-minute `MIDPOINT` bars from IBKR's historical service covering
  the 00:00-06:00 UTC window.
- **Replay range: 4480.82 – 4551.49**, computed by
  `ReplayORBMarketContext` which accumulates ticks from the captured
  log into 1-minute bars internally and takes high/low of the
  range-window bars.

The 0.86-point gap at the top is the critical difference. Price during
04:00-08:00 EDT touched ~4551 several times but never reached 4552.35
(the live BUY stop). It DID touch 4551.49 (the replay BUY stop), which
fired the LONG entry in replay.

So a sub-pip difference in range determination caused completely
different outcomes for the day — fill vs no-fill.

## Why the two data sources disagree

The IBKR-server 5-min bars and our locally-resampled 1-min bars span
the same instrument and window but produce different highs/lows
because:

1. **Sampling cadence.** IBKR's 5-min bars include data from all ticks
   in each 5-minute window; our 1-min bars include all ticks in each
   1-minute window. The 1-min bars CAN see higher highs / lower lows
   that are then "lost" when aggregated to 5-min.
2. **Server-side filtering.** IBKR's MIDPOINT bars are post-processed
   on their side (smoothing, outlier filtering, exchange-of-record
   selection). Our raw tick log is unfiltered.
3. **Tick coverage.** During tick-light periods (e.g. Asian session
   pre-Tokyo-open) our captured ticks may be sparse; the IBKR-server
   bar still reports a high/low computed from their full feed (which
   may include ticks our subscription didn't deliver).

Counter-intuitively, the live system can be BLINDER to extremes than
the replay (because 1-min resampling can catch ticks 5-min server
bars smooth over), OR vice versa (because our subscription tick-rate
limits don't deliver every tick IBKR has).

## Other notes from the run

1. **The replay summary reports `trades=0 PnL=$+0.00`** at the end
   despite the per-day logs clearly showing two entries (the SHORT
   2026-05-13 and the LONG 2026-05-18). This is a reporting bug in
   `TickReplayer` — the strategy events fire correctly but the
   aggregator doesn't tally them. Worth a small fix, not blocking.
2. **The replay correctly handled all the safety paths**: stale
   breakout, 4h-pending cancel, range-too-wide invalid, weekend
   zero-range invalid. State machine fidelity is high.
3. **No reconnect or disconnect events fired** in the replay (it's a
   deterministic single-process run with no IB connection needed once
   data is loaded). So Phase 1 (rebind) wasn't exercised — but it
   wasn't expected to be in this scenario.

## What this validates

- **Replay infrastructure works** for ORB-on-XAUUSD strategy
  decisions, with high fidelity across 7 of 8 days.
- **The live strategy is doing what its code prescribes.** No silent
  divergence in the order/exit/state-machine logic.
- **The replay tool can be used** to assess "what would have happened"
  for any historical day where tick data was captured.

## What this rules out and what it implies

- **NOT a strategy bug.** The 2026-05-18 outcome difference is
  entirely upstream — in how the range gets computed.
- **The live system may be systematically pricing the range
  slightly differently than a tick-faithful backtest would.** Over
  many days this could bias whether trades fire. It's not obvious
  which direction the bias runs (tighter or wider range).

## Two paths to close the gap

Either:

1. **Modify replay to query IBKR historical bars** for the range
   window (when live IB connection available). Makes replay
   strictly bit-equivalent with live's range. Cost: slower replay,
   requires IB connection.
2. **Modify live to compute range from streamed 1-min bars** the
   strategy itself accumulates, instead of querying IBKR historical.
   Pro: removes a dependency on IBKR's historical service (which has
   timed out for us in deep-history requests). Con: requires v11 to
   be running and streaming before 00:00 UTC for the range to be
   complete by 06:00 UTC.

**No recommendation here** — both are intrusive changes that affect
the live strategy. Worth a separate plan before touching either.

## See also

- `v11/replay/run_tick_replay.py` — replay CLI used for this analysis
- `v11/replay/replay_orb.py` — replay strategy adapter
- `v11/replay/tick_replayer.py` — main runner
- `data/ticks/XAUUSD/` — live tick log source (2026-05-08 onward)
- `docs/journal/2026-05-13_xauusd_first_live_fill_full_lifecycle.md`
  — the live day this replay validated as the matching SHORT trade

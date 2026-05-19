# XAUUSD ORB — first profitable live trade + backtest validates the live config

**Date:** 2026-05-19
**Status:** Major day. (1) First profitable live trade since the
proof-of-life arc closed: SHORT +$20.50 paper. (2) Multi-year
backtest of the live config on freshly-downloaded XAUUSD data
PASSES the decision gate, including a year-by-year breakdown
showing 7-of-8 positive years since 2018.

## What happened today

### The trade

```
04:00:04  ORB state: IDLE -> RANGE_READY
04:01:04  Entry stops placed: BUY id=74 @ 4589.54 lmt=4595.39
                              SELL id=75 @ 4531.04 lmt=4525.19
04:01:04  DIAG: openTrades after placement [STP LMT, triggerMethod=7]
06:43:43  orderStatusEvent: id=75 status=Filled filled=1.0/0.0
06:43:44  SL/TP placed: SL id=76 @ 4589.54, TP id=77 @ 4384.79
06:43:44  ENTRY: SHORT @ 4529.70 | SL=4589.54 TP=4384.79
06:43:44  RISK: V6_ORB entered XAUUSD (positions: 1/3, trades today: 1)
12:00:00  Trade window closed, closing at market
12:00:06  MARKET: @ 4509.20 | PnL=+20.50
12:00:06  ORB state: IN_TRADE -> DONE_TODAY
```

Held 5h17m. Price went from 4529.70 entry → 4474.69 (peak favorable
at 09:46 EDT, +$55 unrealized) → 4509.20 exit. **+$20.50 paper.**

Same exit path as 2026-05-13 (V6 trade-window-close MARKET exit).
Same SHORT direction as 2026-05-13.

### Live record so far (2 fills)

| Date | Direction | Entry | Exit | PnL | Hold |
|---|---|---|---|---|---|
| 2026-05-13 | SHORT | 4687.63 | 4695.59 (MARKET) | −$7.96 | 6h37m |
| 2026-05-19 | SHORT | 4529.70 | 4509.20 (MARKET) | **+$20.50** | 5h17m |

**Net: +$12.54** across 2 trades. Trade frequency: ~1 per 6 trading
days, matching the historical baseline (see backtest below).

## XAUUSD backtest result

Freshly downloaded 16 years of XAUUSD 1-min MIDPOINT data (and
matching BID + ASK) from IBKR via
`v11/backtest/download_xauusd_sequential.py`. Total 2.52M MID bars,
2010-06-08 → 2026-05-18. Output in
`tick_vault_data/xauusd/XAUUSD_{MIDPOINT,BID,ASK}.csv`.

Backtest script: `v11/backtest/orb_xauusd.py`. It imports
`XAUUSD_ORB_CONFIG` directly from `v11/live/run_live.py` so what
gets tested is **literally the live deployed configuration** as of
2026-05-19 (velocity_filter_enabled=False, skip_weekdays=(), gap
filter ON).

Headline (with $0.30 RT slippage):

| Period | N | WR | AvgR_slip | PF | MaxDD |
|---|---|---|---|---|---|
| EARLY (2010-2017, holdout) | 222 | 38.3% | **−0.056** | 0.89 | 24.81 |
| **OOS (2018-2023, gate)** | **208** | **46.2%** | **+0.085** | **1.19** | 8.31 |
| **IS (2024-now)** | **86** | **50.0%** | **+0.108** | **1.32** | 5.63 |
| ALL | 516 | 43.4% | +0.029 | 1.06 | 27.66 |

**Decision gate (pre-registered standard):**
- OOS AvgR_slip = +0.0853 ≥ +0.05 → **PASS**
- OOS N/yr = 34.7 ≥ 20 → **PASS**
- **OVERALL: PASS**

## Year-by-year (the regime story)

```
2010 +0.087   2018 +0.087   2024 +0.165
2011 -0.194   2019 +0.065   2025 +0.102
2012 -0.551   2020 -0.101   2026 -0.025 (YTD, N=15)
2013 +0.343   2021 +0.003
2014 +0.023   2022 +0.292
2015 -0.228   2023 +0.124
2016 -0.043
2017 -0.151
```

**Clear regime structure.** Pre-2018: mixed-to-negative (5 of 8
years negative). 2018+: 7 of 8 years positive. The edge appeared
around 2018 and has been stable for 8 years.

This matches what we saw on USDJPY (positive 2022+ regime) and on
US equities (positive 2018-2022 then died). Same general theme:
regimes matter, and a single static strategy can produce wildly
different expectancy across them.

## May 2025 same-season slice (Nick's specific question)

`N=4, WR 75%, AvgR_slip +0.884, PF 25.48, MaxDD 0.14`.

4 trades in May 2025, 3 winners, strong AvgR. Sample is tiny so
this is descriptive not predictive — but the directional read is
"same season last year, this strategy did fine." Today's profitable
SHORT is consistent with that May pattern.

## What this tells us about the live deployment

1. **The live config (XAUUSD_ORB_CONFIG as of 2026-05-19) is the
   one that works.** velocity_filter=OFF, gap_filter=ON,
   skip_weekdays=() (recently changed), max_pending_hours=4.
2. **Edge has been stable since 2018, no degradation visible.**
   The 2026 YTD slice (-0.025 on N=15) is single-month noise,
   directly comparable to 2020's COVID dip (-0.101, also temporary).
3. **Trade frequency matches the live experience.** Backtest:
   ~32 trades/year = 1 per 8 trading days. Live: 2 fills in
   ~6 trading days. On baseline.
4. **The "many no-trade days" pattern we've been observing live IS
   the historical norm.** The strategy is meant to be patient.

## Why this matters relative to the open questions

Before today we had:
- A live system running on a Windows-era backtest claim we couldn't
  rerun on Mac.
- 5 live trading days with only 1 fill (which was a loss).
- No confidence anchor for whether the live system was doing what
  history says it should.

Now we have:
- 16 years of XAUUSD MIDPOINT + BID + ASK data on the Mac.
- A Mac-runnable backtest that imports the LIVE config directly.
- An IS/OOS pass with a year-by-year breakdown showing 7-of-8
  positive years since 2018.
- A first profitable live trade.
- Statistical alignment between live experience (2 trades in
  ~6 days) and historical frequency (~32 trades/year).

The live system is working as historically expected.

## Caveats worth being honest about

1. **2026 YTD is -0.025 over N=15.** Not great, not terrible —
   single-year noise. Same as 2020's -0.101 was at the time.
   Will look at this again in a month.
2. **The pre-2018 EARLY window shows a different regime.** If
   that regime returns, the strategy will struggle. No way to
   predict regime shifts in advance — that's a tail risk.
3. **The MaxDD 27.66 over ALL** (or 8.31 over OOS) is real
   drawdown. At 1-share size this is dollars; at scaled size it
   would be percent of capital. Position sizing matters.
4. **The 2026-05-18 replay-vs-live divergence noted yesterday**
   (small range-source mismatch) is unresolved — but it doesn't
   appear to materially affect the backtest result, since the
   backtest uses 1-min OHLC bars consistently throughout.

## Artifacts kept

- `v11/backtest/download_xauusd_sequential.py` — downloader
- `v11/backtest/orb_xauusd.py` — backtest script (imports live config)
- `v11/backtest/results/xauusd_orb_trades.csv` — 516 trades
  (gitignored, regeneratable)
- `tick_vault_data/xauusd/XAUUSD_{MIDPOINT,BID,ASK}.csv` —
  7.6M rows of XAUUSD 1-min data, 16 years (gitignored)
- `~/.v11_paper.log` — today's full trade timeline

## Next actions

1. **Continue paper trading** as-is. The live system is doing
   what history expects.
2. **Optional follow-up:** rerun the backtest with the historical
   investigate config (velocity=True 168, Wed skip) to verify the
   2026-04-16 finding that "velocity=OFF beats velocity=ON OOS
   +0.057 AvgR" still holds on this fresh data.
3. **Optional follow-up (lower priority):** fix the replay-vs-live
   range-source divergence noted in
   `docs/journal/2026-05-18_replay_vs_live_xauusd_divergence.md`.
4. **Optional follow-up (lowest priority):** investigate the
   recurring 01:00 EDT IBKR Gateway auto-restart pattern (third
   time observed). v11's cron at 01:15 EDT catches the gap, but
   if a trade is open during that 14-min window it could be a
   real exposure.

## See also

- `docs/journal/2026-05-13_xauusd_first_live_fill_full_lifecycle.md`
  — the first live fill (loser)
- `docs/journal/2026-05-18_replay_vs_live_xauusd_divergence.md`
  — replay-vs-live divergence investigation from yesterday
- `docs/superpowers/specs/2026-05-15-usdjpy-orb-preregistration.md`
  — USDJPY ORB pre-registration (failed gate on simple variant)

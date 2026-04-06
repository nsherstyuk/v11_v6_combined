# Investigation Program: Higher-Timeframe Filters for V11 Darvas Breakouts

## Context

V11's 1-min Darvas Box breakout system has a strong in-sample result on EURUSD (60% WR, +0.570 AvgR on 2024-2026) but **fails out-of-sample** (37% WR, -0.044 AvgR on 2018-2023). The hypothesis: 1-minute breakouts lack structural significance without higher-timeframe context. This program systematically tests 5 approaches to fix this.

**Baseline config (EURUSD Config B):** tc=20, bc=12, maxW=3.0, brk=2, CONFIRMING volume filter, R:R=2.0, Trail10@60.

---

## Step 0: Shared Utility Module

**File:** `v11/backtest/htf_utils.py` (~200 lines)

Contains reusable functions needed by all 5 investigations:

### Functions to implement:

1. **`resample_bars(bars, minutes)`** — Aggregate 1-min bars into HTF bars (5, 15, 30, 60, 240 min). Group by floored timestamp, aggregate OHLC (O=first, H=max, L=min, C=last), sum volumes/ticks. Discard trailing incomplete period.

2. **`compute_sma(bars, period)`** — Simple moving average on bar closes. Returns list of `(timestamp, sma_value)` starting from first full window.

3. **`compute_adx(bars, period=14)`** — Full ADX computation (Wilder smoothing: TR, +DM, -DM, +DI, -DI, DX, ADX). Returns list of `(timestamp, adx, plus_di, minus_di)`.

4. **`build_htf_lookup(values)`** — Convert list of `(timestamp, value)` tuples to `dict[datetime, float]` for O(1) lookups.

5. **`get_htf_value_at(lookup, signal_timestamp, htf_minutes)`** — Look up the **previous completed** HTF bar's value at a signal time. Floors timestamp to HTF period then steps back one period to avoid look-ahead bias.

6. **`collect_signals(bars, config)`** — Extract from `oos_validation.py` and `analyze_combined.py`. Standard session loop: split sessions → DarvasDetector + ImbalanceClassifier → collect `{signal, bars_after, is_long, vol_class}` dicts. Avoids copy-pasting this ~20-line block into every script.

7. **`simulate_with_trailing(...)`** — Extract the existing function from `analyze_combined.py:24-116` into this shared module. All investigations reuse it.

8. **`run_and_report(raw_trades, rr, config, filter_fn, label)`** — Standard simulate-aggregate-print pipeline. Takes collected raw trades, applies filter, simulates, returns `{n, wr, avg_r, pnl, sl, slt, tp, tm, long_wr, short_wr}`.

---

## Step 1a: Investigation 3 — Session/Time-of-Day Filter (simplest)

**File:** `v11/backtest/investigate_session_filter.py` (~90 lines)

**What it does:** Filters signals by UTC hour. Tests whether breakouts during specific trading sessions are more reliable.

**Session filters:**
| Filter | UTC Hours |
|---|---|
| Asian only | 0-7 |
| London only | 8-12 |
| NY only | 13-16 |
| London+NY | 8-16 |
| No Asian | >= 8 |
| Core hours | 8-16 |
| All (baseline) | 0-23 |

Cross with volume: ALL vs CONFIRMING = **14 combinations**

**Tests on:** IS (2024-2026) and OOS (2018-2023) side by side.

**Also produces:** Hour-of-day histogram (trade count + avg_r by hour) as a diagnostic.

**Runtime:** ~1-3 min

---

## Step 1b: Investigation 1 — HTF SMA Direction Filter (in parallel with 1a)

**File:** `v11/backtest/investigate_htf_sma.py` (~120 lines)

**What it does:** Only takes LONG breakouts when price > HTF SMA, SHORT when price < HTF SMA.

**Parameter sweep:**
| Parameter | Values |
|---|---|
| HTF bar period | 15, 30, 60 min |
| SMA period | 10, 20, 50 bars |
| Volume filter | ALL, CONFIRMING |

**18 combinations**, each tested IS + OOS.

**Look-ahead guard:** Uses `get_htf_value_at()` which returns the previous completed HTF bar's SMA, never the current in-progress bar.

**Runtime:** ~2-5 min

---

## Step 2: Investigation 4 — ADX Trend Strength Filter

**File:** `v11/backtest/investigate_adx_filter.py` (~150 lines)

**What it does:** Only takes breakouts when ADX exceeds threshold (market is trending). Optionally requires directional alignment (+DI > -DI for longs).

**Parameter sweep:**
| Parameter | Values |
|---|---|
| HTF bar period | 60, 240 min |
| ADX threshold | 15, 20, 25, 30 |
| Directional filter | None, aligned |
| Volume filter | ALL, CONFIRMING |

**32 combinations**, each IS + OOS.

**Depends on:** `compute_adx()` in htf_utils.

**Runtime:** ~3-5 min

---

## Step 3: Investigation 2 — Run Darvas on 5-min / 15-min Bars

**File:** `v11/backtest/investigate_htf_darvas.py` (~130 lines)

**What it does:** Resamples 1-min bars to 5-min and 15-min, runs DarvasDetector directly on the longer-timeframe bars. Tests whether Darvas on longer bars produces a more durable edge.

**Critical: parameter rescaling for longer bars:**
| Param | 1-min default | 5-min | 15-min |
|---|---|---|---|
| max_hold_bars | 120 (2h) | 24 (2h) | 8 (2h) |
| atr_period | 60 (1h) | 12 (1h) | 4 (1h) |
| min_box_duration | 20 (20m) | 4 (20m) | 2 (30m) |

**Grid sweep (per bar period):**
| Parameter | Values |
|---|---|
| top_confirm_bars | 8, 12, 15, 20 |
| bottom_confirm_bars | 8, 12, 15, 20 |
| max_box_width_atr | 2.0, 3.0, 5.0 |
| breakout_confirm_bars | 1, 2, 3 |

= 144 combos x 2 bar periods x 2 R:R = **576 combos**

Uses existing `run_backtest()` and `compute_metrics()` infrastructure. Saves results to CSV. Reports top 10 by Sharpe for IS and OOS separately.

**Runtime:** ~15-30 min

---

## Step 4: Investigation 5 — Multi-Timeframe Box Alignment

**File:** `v11/backtest/investigate_mtf_alignment.py` (~250 lines)

**What it does:** Runs DarvasDetector on both 1-min (micro) and a higher timeframe (macro). Only takes micro breakouts when the breakout price is near a macro box boundary.

**New functions needed (in the script or htf_utils):**

- `build_macro_box_timeline(macro_bars, macro_config)` — Run DarvasDetector on macro bars per session, record `active_box` at each timestamp. Returns `dict[datetime, Optional[DarvasBox]]`.

- `check_proximity(signal, macro_box, threshold_atr)` — Check if `min(|price - box.top|, |price - box.bottom|) / signal.atr <= threshold`. Returns `(passes, boundary: "top"/"bottom"/"none")`.

**Parameter sweep:**
| Parameter | Values |
|---|---|
| Macro bar period | 15, 60 min |
| Macro top_confirm | 8, 12, 15 |
| Macro bottom_confirm | 8, 12, 15 |
| Macro max_box_width_atr | 3.0, 5.0 |
| Proximity threshold (ATR) | 0.5, 1.0, 1.5, 2.0 |
| Direction alignment | Yes, No |

**288 combinations**, IS + OOS. Micro params fixed at Config B.

**Runtime:** ~10-20 min

---

## Execution Order

```
Step 0: Build htf_utils.py (all investigations depend on it)
    |
    ├── Step 1a: Session filter (no HTF computation needed)
    ├── Step 1b: HTF SMA filter (run in parallel with 1a)
    |
    v
Step 2: ADX filter
    |
    v
Step 3: HTF Darvas (informed by which HTF periods helped in 1b/2)
    |
    v
Step 4: MTF Alignment (most complex, informed by all prior results)
```

## How Results Feed Forward

- **If Step 1a shows London+NY dramatically better OOS:** Add session filter to all subsequent investigations as a free baseline filter.
- **If Step 1b shows HTF SMA direction filter improves OOS:** Directional alignment confirmed. Steps 3 and 4 become high priority.
- **If Step 2 shows high-ADX markets better OOS:** Combine with best from 1a/1b as composite filter.
- **If Step 3 shows 5-min/15-min Darvas works OOS:** 1-min timeframe is fundamentally too noisy. Step 4 becomes less important.
- **If nothing works OOS:** Darvas Box breakout may not have an edge on FX. That's a valid finding.

## Key Files Modified/Created

| File | Action |
|---|---|
| `v11/backtest/htf_utils.py` | **CREATE** — shared utilities |
| `v11/backtest/investigate_session_filter.py` | **CREATE** — investigation 3 |
| `v11/backtest/investigate_htf_sma.py` | **CREATE** — investigation 1 |
| `v11/backtest/investigate_adx_filter.py` | **CREATE** — investigation 4 |
| `v11/backtest/investigate_htf_darvas.py` | **CREATE** — investigation 2 |
| `v11/backtest/investigate_mtf_alignment.py` | **CREATE** — investigation 5 |

**No existing files are modified.** All investigations are new standalone scripts.

## Key Files Referenced (read-only)

- `v11/backtest/analyze_combined.py` — template for signal collection + trailing sim
- `v11/backtest/oos_validation.py` — template for IS/OOS dual-period testing
- `v11/core/darvas_detector.py` — reused via `add_bar()` + `active_box` interface
- `v11/core/imbalance_classifier.py` — reused for volume classification
- `v11/backtest/data_loader.py` — `load_instrument_bars()`, `split_by_sessions()`
- `v11/backtest/grid_search.py` — `build_param_grid()`, `save_results()` (for investigation 2)
- `v11/config/strategy_config.py` — StrategyConfig, EURUSD_CONFIG

## Verification

After each investigation script is built, run it:
```bash
python -m v11.backtest.investigate_session_filter
python -m v11.backtest.investigate_htf_sma
python -m v11.backtest.investigate_adx_filter
python -m v11.backtest.investigate_htf_darvas
python -m v11.backtest.investigate_mtf_alignment
```

**Success criteria:** Each script produces a formatted table comparing IS vs OOS performance. We're looking for any filter combination where OOS AvgR > 0 with 15+ trades.

## Pitfalls to Watch

1. **Look-ahead bias** — HTF indicator values must use previous completed bar, not current.
2. **Session boundaries** — Resample within sessions, not across weekend/holiday gaps.
3. **Trade count collapse** — Flag results with < 15 OOS trades as inconclusive.
4. **ATR scaling** — MTF alignment proximity uses micro (1-min) ATR, not macro ATR.
5. **Parameter rescaling** — Hold bars, ATR period, min box duration must scale with bar period in investigation 2.

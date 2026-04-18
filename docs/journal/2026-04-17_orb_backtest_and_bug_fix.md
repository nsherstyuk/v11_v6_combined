# ORB Backtest Validation & Silent Order Failure Bug Fix

**Date**: 2026-04-17
**Session**: ORB strategy validation + critical bug discovery

---

## 1. ORB Backtest Results (XAUUSD, 2018–2026)

### Methodology
- Used the **actual V6 ORBStrategy** code (not a reimplementation)
- 2,876,848 1-min bars from `C:\nautilus0\data\1m_csv\xauusd_1m_tick.csv`
- Execution simulation: bar high/low triggers bracket fills (matches IBKR STP order behavior)
- Gap filter: properly implemented (not stubbed like in `replay_orb.py`)
- No LLM filter (passthrough — all signals pass)

### Baseline Config (matches live `XAUUSD_ORB_CONFIG`)
```
range_start_hour=0, range_end_hour=6, trade_start_hour=8, trade_end_hour=16
skip_weekdays=(2,), rr_ratio=2.5, min_range_size=1.0, max_range_size=15.0
velocity_filter_enabled=False, gap_filter_enabled=True, gap_vol_percentile=50
gap_start_hour=6, gap_end_hour=8, be_hours=999, max_pending_hours=4
```

### Results

| Metric | Value |
|---|---|
| **Trades** | 366 (~46/year) |
| **Win rate** | 51.6% |
| **Total P&L** | +$943 (no slippage) |
| **Avg P&L** | +$2.58/trade |
| **Profit factor** | 1.63 |
| **Sharpe (raw)** | +1.15 |
| **Sharpe ($0.20 slip)** | +0.88 |
| **Max DD** | −$132 |

### Year-by-Year Breakdown

| Year | P&L | WR | N | Notes |
|---|---|---|---|---|
| 2018 | +$21 | 48% | 50 | Marginal |
| 2019 | +$35 | 46% | 35 | Marginal |
| 2020 | +$27 | 53% | 47 | Marginal |
| 2021 | +$21 | 30% | 37 | Weak year |
| 2022 | +$168 | 57% | 54 | Good |
| 2023 | +$142 | 62% | 37 | Good |
| 2024 | +$70 | 44% | 50 | OK |
| **2025** | **+$557** | **70%** | **50** | **Exceptional** |
| 2026 | −$97 | 33% | 6 | YTD only |

**Key concern**: 2025 carries the total. Without it, 7-year total = $386 (~$55/year).

### Gap Filter Impact

| Config | Trades | WR | Total | Sharpe |
|---|---|---|---|---|
| Gap ON | 366 | 51.6% | +$943 | +1.15 |
| Gap OFF | 795 | 44.9% | +$778 | +0.77 |

Gap filter **halves trade count** but **improves Sharpe by 49%**. Essential.

### RR Ratio Sensitivity

| RR | Total | Sharpe |
|---|---|---|
| 1.5 | +$1,002 | +1.26 |
| 2.0 | +$950 | +1.19 |
| 2.5 | +$943 | +1.15 |
| 3.0 | +$949 | +1.12 |
| 4.0 | +$886 | +1.08 |

Lower RR = slightly better Sharpe. RR 1.5–2.0 is optimal.

### Slippage Impact

| Slippage/leg | Total | Sharpe |
|---|---|---|
| $0.00 | +$943 | +1.15 |
| $0.10 | +$834 | +1.02 |
| $0.20 | +$724 | +0.88 |
| $0.30 | +$614 | +0.75 |
| $0.50 | +$394 | +0.48 |
| $1.00 | −$155 | −0.19 |

**At $0.20/leg (realistic), Sharpe drops to 0.88.** Edge survives up to ~$0.50/leg.

### Breakeven Rule

| BE | WR | Total | Sharpe |
|---|---|---|---|
| None | 51.6% | +$943 | +1.15 |
| 6h | 59.6% | +$856 | +1.07 |
| 4h | 71.6% | +$489 | +0.71 |
| 2h | 81.1% | +$269 | +0.46 |

BE increases WR but **destroys total P&L**. Don't use BE.

### Exit Reason Breakdown

| Exit | Count | Total P&L |
|---|---|---|
| TP | 31 | +$637 |
| SL | 113 | −$1,120 |
| EOD | 222 | +$1,427 |

Most profit comes from **EOD exits** (222 trades, avg +$6.43). TP hits are rare (31) but large. SL is the biggest drag.

### Honest Assessment

1. **Edge exists but is thin** — realistic Sharpe ~0.85 with slippage
2. **Heavily concentrated in 2025** — without it, only ~$55/year
3. **EOD exits carry the strategy** — the TP/SL ratio alone is negative
4. **Gap filter is essential** — without it, strategy degrades significantly
5. **Strategy survives slippage** up to ~$0.50/leg

---

## 2. Silent Order Failure Bug

### Discovery
User reported "orders been submitted almost two hours before 8am" on Apr 17, 2026.
Investigation of live log `v11_live_20260416_213236.log` revealed a **critical bug**.

### Timeline (Apr 17, log timestamps are EST = UTC-4)

| EST | UTC | Event | Problem? |
|---|---|---|---|
| 02:00 | 06:00 | Asian range calculated | ✅ Correct |
| 04:00 | 08:00 | Gap filter → RANGE_READY | ✅ Correct |
| 04:01 | 08:01 | **Brackets placed** | ✅ Correct timing |
| 04:01 | 08:01 | `Entry placement failed: Not connected` | 🔴 **IBKR disconnected** |
| 04:01 | 08:01 | `ORB state: RANGE_READY -> ORDERS_PLACED` | 🔴 **Phantom state** |
| 04:01–08:01 | 08:01–12:01 | ORDERS_PLACED, no real orders | 🔴 Wasted 4 hours |
| 08:01 | 12:01 | `Orders pending > 4h, cancelling` | 🔴 Cancelled at market open |
| 08:01+ | 12:01+ | DONE_TODAY — **no trade taken** | 🔴 **Total day loss** |

### Root Cause

In `v11/v6_orb/ibkr_executor.py:101-115`, the `set_orb_brackets` method:

```python
try:
    buy_trade = self.ib.placeOrder(self.contract, buy_entry)
    # ...
except Exception as e:
    self.logger.error(f"Entry placement failed: {e}")
    # BUG: exception swallowed, no return value, strategy not notified
```

The exception was **caught and logged but the strategy was never notified** of the failure. The strategy unconditionally transitioned to `ORDERS_PLACED` after calling `set_orb_brackets`, even though no orders actually reached IBKR.

### Impact
- Strategy sits in `ORDERS_PLACED` with phantom orders for `max_pending_hours` (4h)
- By the time the timeout fires, the best trading hours have passed
- The day is lost entirely
- Status display shows "LLM rejected today" even though LLM approved (display bug)

### Fix Applied

**1. `v11/v6_orb/ibkr_executor.py`** — `set_orb_brackets` now returns `bool`:
- Returns `True` on successful placement
- Returns `False` on exception, resets order IDs so `has_resting_entries()` returns False

**2. `v11/v6_orb/interfaces.py`** — Updated `ExecutionEngine` interface:
- `set_orb_brackets()` now declared as `-> bool`

**3. `v11/v6_orb/orb_strategy.py`** — Strategy checks return value:
- If `True`: transition to `ORDERS_PLACED` (normal)
- If `False`: **stay in `RANGE_READY`**, log warning, retry on next tick
- This means if IBKR reconnects, the strategy will retry bracket placement

**4. All execution engines updated** for interface consistency:
- `v11/replay/replay_orb.py` — `ReplayORBExecutionEngine` returns `True`
- `v11/backtest/backtest_orb_xauusd.py` — returns `True`
- `v11/backtest/backtest_orb_optimize.py` — returns `True`

### Retry Behavior
With the fix, if bracket placement fails due to disconnection:
1. Strategy stays in `RANGE_READY`
2. On next tick (when IBKR reconnects), velocity check passes again
3. `set_orb_brackets` is called again → placement succeeds
4. Strategy transitions to `ORDERS_PLACED` normally

This provides **automatic recovery** from transient disconnections without losing the trading day.

---

## 3. Files Created/Modified

### New Files
- `v11/backtest/backtest_orb_xauusd.py` — Full ORB backtest with 6 test suites
- `v11/backtest/backtest_orb_optimize.py` — Optimization sweep (time windows, pending hours, gap filters)

### Modified Files
- `v11/v6_orb/ibkr_executor.py` — `set_orb_brackets` returns `bool`, resets IDs on failure
- `v11/v6_orb/interfaces.py` — `ExecutionEngine.set_orb_brackets` declared `-> bool`
- `v11/v6_orb/orb_strategy.py` — Checks placement result, stays RANGE_READY on failure
- `v11/replay/replay_orb.py` — `ReplayORBExecutionEngine` returns `True`
- `v11/backtest/backtest_orb_xauusd.py` — `BacktestExecutionEngine` returns `True`
- `v11/backtest/backtest_orb_optimize.py` — `OptExecutionEngine` returns `True`

---

## 4. Open Questions / Next Steps

1. **Run the optimization backtest** (`backtest_orb_optimize.py`) — was interrupted, needs to complete
2. **LLM gating**: Previous session showed regime-filtered LLM feedback achieves Sharpe 1.77 on Jan-Apr 2026. Worth re-validating on full 8-year dataset.
3. **Entry time analysis**: Most profit comes from EOD exits. Are early entries (08:00 UTC) worse than later entries?
4. **Range window optimization**: Is 00:00-06:00 the best Asian range, or would 21:00-06:00 (including prior evening) work better?
5. **Max pending hours**: Current 4h may be too long. Orders that don't fill in 2h may be stale.

# Session — 2026-04-16: Streamlit Dashboard + ORB Debugging + 4H Level Fix

**Baseline:** 374 passed
**Result:** 374 passed

## Overview

Three major workstreams in this session:
1. Built a Streamlit monitoring dashboard for live trading
2. Diagnosed and fixed the 4H Level Retest strategy (no levels detected)
3. Diagnosed and fixed the V6 ORB strategy (stuck in RANGE_READY, never placing orders)

---

## 1. Streamlit Dashboard

### What was built
- **File:** `v11/live/dashboard.py`
- Web dashboard with auto-refresh (30s) showing:
  - KPI cards (P&L, trades, positions, daily loss)
  - P&L chart over time (plotly)
  - Price chart with range/levels overlay
  - Trade log table
  - Exit reason breakdown pie chart
  - Strategy status cards with live state info

### Dependencies added
- **File:** `v11/requirements.txt` — added `streamlit>=1.37.0`, `plotly>=5.22.0`

### How to run
```
streamlit run v11/live/dashboard.py --server.port 8501
```
Dashboard at `http://localhost:8501`

---

## 2. 4H Level Retest — No Levels Detected

### Problem
The `4H_Level_Retest` strategy reported `levels=0` despite processing 6,500+ bars of historical data. EURUSD had clear 4H swing levels, but the detector wasn't finding them.

### Root Cause
`level_left_bars=10` and `level_right_bars=10` in `v11/config/strategy_config.py` were too strict for 4H bars. A swing high/low requires 10 bars on each side to be higher/lower — that's 10 × 4H = 40 hours on each side, 80 hours total. In a ranging market, this is nearly impossible to satisfy.

### Fix
- **File:** `v11/config/strategy_config.py`
- Changed `level_left_bars: int = 10` → `3` and `level_right_bars: int = 10` → `3`
- 3×4H = 12 hours on each side is sufficient for meaningful swing points

### Result
After restart, the strategy immediately detected 2 levels:
- Support @ 1.17721 (from Apr 15 08:00)
- Resistance @ 1.18111 (from Apr 14 12:00)

---

## 3. V6 ORB — Stuck in RANGE_READY, No Orders Placed

### Problem
ORB was stuck at `RANGE_READY | brackets eligible` for 2.5+ hours. LLM approved (conf=70-75), but no bracket orders were ever submitted to IBKR. No "Entry stops placed" log message.

### Root Cause 1: Velocity filter blocking order placement
The V6 ORB strategy has a velocity gate: bracket orders are only placed when tick velocity ≥ 168 ticks/min (3-min lookback). Gold was in a quiet/consolidating market at ~44-52 ticks/min (26-31% of threshold). The velocity filter was doing its job — no momentum = no brackets.

However, the status line only showed "brackets eligible" with no visibility into WHY brackets weren't placed.

### Fix 1: Added velocity diagnostics to ORB status
- **File:** `v11/live/orb_adapter.py` — added `velocity`, `velocity_threshold`, `tick_count_3m` to `get_status()` dict
- **File:** `v11/live/run_live.py` — updated RANGE_READY status line to show:
  ```
  | vel=52/168(31%) ticks3m=156 dist_high=-34.12 dist_low=-6.78
  ```

### Root Cause 2: Stale breakout deadlock
Price broke below the ORB range ($4,807 < $4,811 low), but the V6 strategy only checks for stale breakouts AFTER velocity passes. Since velocity never passed, the stale breakout check never fired. The strategy sat in RANGE_READY forever with price already outside the range.

### Fix 2: Added stale breakout check in adapter layer
- **File:** `v11/live/orb_adapter.py` — added check before `on_tick()`:
  - If state is RANGE_READY, LLM approved, and price is outside range → set DONE_TODAY
  - Logs: `ORB stale breakout: price=4807.11 outside range [4811.18-4838.53], skipping (velocity never reached)`
  - This is in the adapter (not frozen V6 code) because the fix is an integration concern

### Root Cause 3: No order visibility
User couldn't tell if bracket orders had been submitted to IBKR or not.

### Fix 3: Added resting order info to ORB status
- **File:** `v11/live/orb_adapter.py` — added `has_resting_entries`, `buy_entry_id`, `sell_entry_id` to `get_status()`
- **File:** `v11/live/run_live.py` — when orders are live, shows:
  ```
  | orders LIVE buy@high sell@low price=4825.50 dist_high=-12.32 dist_low=+14.32
  ```

---

## 4. Stale emergency_shutdown.json Cleanup

### Problem
Dashboard showed system in "EMERGENCY SHUTDOWN" state even though V11 was running normally. A stale `emergency_shutdown.json` from a previous session was not cleaned up.

### Fix
- **File:** `v11/live/run_live.py` — added cleanup at startup:
  ```python
  stale_state = ROOT / "v11" / "live" / "state" / "emergency_shutdown.json"
  if stale_state.exists():
      stale_state.unlink()
      self.log.info("Removed stale emergency_shutdown.json from previous session")
  ```

---

## Files Changed

| File | Change |
|---|---|
| `v11/live/dashboard.py` | **NEW** — Streamlit monitoring dashboard |
| `v11/requirements.txt` | Added streamlit, plotly |
| `v11/config/strategy_config.py` | `level_left_bars` 10→3, `level_right_bars` 10→3 |
| `v11/live/orb_adapter.py` | Added velocity/order/stale-breakout diagnostics and checks |
| `v11/live/run_live.py` | Velocity display in status, stale emergency cleanup on startup |

---

## Open Questions / Next Steps

1. **ORB velocity threshold appropriateness** — 168 ticks/min may be too high for quiet gold sessions. User chose to keep it for now. Monitor how often ORB actually places orders.
2. **LLM staleness re-check** — Currently LLM approves once per day. User wants to observe current behavior before deciding whether to re-evaluate the LLM if breakout happens hours later.
3. **IBC installation** — Still manual (see `python -m v11.live.gateway_manager --setup`)
4. **Heartbeat file** — Not yet implemented (Phase C)

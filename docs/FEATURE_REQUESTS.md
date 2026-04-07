# Feature Requests & Future Work

**Created:** 2026-04-06  
**Last updated:** 2026-04-06

---

## 1. Live Dashboard

**Priority:** High  
**Goal:** Real-time and historical visibility into what the system is doing, without reading raw log files.

### What we need to see
- **Account status:** cash, margin, open positions, unrealized P&L
- **Strategy status:** per-engine state (in_trade, bar_count, last signal time, detector state)
- **Trade history:** entries, exits, P&L per trade, cumulative equity curve
- **Signal log:** all signals generated (approved and rejected), with reasons
- **Risk manager state:** daily P&L, trade count, position count vs limits
- **Connection health:** IBKR connected/disconnected, reconnect events, data gaps

### Possible approaches
1. **Simple web dashboard (Flask/FastAPI + htmx)** — lightweight, reads from log files + trade CSVs, auto-refreshes. Minimal infra.
2. **Streamlit app** — fast to build, good for charts (equity curve, signal timeline). Single-file deployment.
3. **SQLite + Grafana** — structured storage, powerful charting, but heavier setup.
4. **Terminal UI (Rich/Textual)** — no browser needed, runs alongside the trader. Good for monitoring but limited for historical analysis.

### Data sources already available
- `v11/live/logs/*.log` — full debug-level session logs
- `v11/live/trades/*.csv` — structured trade records (once trades fire)
- `RiskManager.get_status()` — live P&L, trade count, position count
- `MultiStrategyRunner.get_all_status()` — per-engine status snapshots
- IBKR API via `mcp0_get_portfolio` / `mcp0_get_account_summary` — real account data

### Open questions
- Should the dashboard be a separate process reading shared files, or embedded in the trader process?
- How much historical data to retain? (days? weeks? rolling window?)
- Do we need alerting (Telegram/email on trade entry/exit/error)?

---

## 2. Replay / Verification System

**Priority:** High  
**Goal:** Verify the live system works correctly without waiting weeks for real trades.

### The core problem
- Darvas fires ~15 trades/year, 4H Retest ~22/year — could be weeks between signals
- We need a way to verify the full pipeline (tick → bar → signal → filter → order) works end-to-end
- Pure unit tests cover components but not integration timing, bar aggregation edge cases, or state machine transitions over realistic data

### Realistic approaches

#### A. Historical bar replay (easiest, most valuable)
- **How:** Download 1-min bars from IBKR historical data API (already have `fetch_historical_bars`)
- **Feed bars directly into `MultiStrategyRunner`** bypassing the real-time tick stream
- **What it tests:** Signal detection, SMA filter, volume classification, risk gates, trade management (entry/exit/SL/TP)
- **What it doesn't test:** Tick-level timing, bar aggregation from ticks, real order fills, slippage
- **Effort:** Medium — need a replay harness that feeds bars at simulated speed
- **This is essentially what the backtester already does**, but wired through the live engine instead of the simulator

#### B. Tick-level replay from recorded data
- **How:** Record raw ticks during live sessions to a file (timestamp, bid, ask, volume)
- **Replay through `BarAggregator` → engines** at accelerated speed
- **What it tests:** Everything in (A) plus bar aggregation, tick quality, timing
- **Data source options:**
  - Record from our own live sessions (free, but need to run for a while first)
  - Download from tick data vendors (Dukascopy free tick data, TrueFX, TickStory)
  - IBKR historical ticks API (`reqHistoricalTicks` — limited to 1000 ticks per request)
- **Effort:** Medium-high — need tick recorder + replay harness + time simulation

#### C. Synthetic signal injection (fastest validation)
- **How:** Inject a fake `BreakoutSignal` or `RetestSignal` directly into the engine
- **What it tests:** Everything downstream of signal detection (LLM filter, risk check, order placement, trade management)
- **What it doesn't test:** Signal detection itself
- **Effort:** Low — could be a simple test script
- **Good for:** Verifying the order flow works on paper account without waiting for a real signal

#### D. Accelerated paper trading with forced signals
- **How:** Run live on paper account but lower thresholds temporarily (e.g., smaller box duration, wider SMA tolerance) to force more signals
- **Risk:** Trades won't be representative of the real strategy
- **Good for:** Verifying IBKR order flow, fill handling, commission tracking

### Recommended path
1. **Start with (C)** — synthetic signal injection — to verify order flow works TODAY
2. **Then (A)** — bar replay harness — to validate signal detection on known historical periods
3. **Then (B)** — tick recording — accumulate data from live sessions for future regression testing
4. **(D) as needed** — if we need to verify IBKR specifics (margin, commissions, partial fills)

### Tick data sources for replay
| Source | Format | Cost | Coverage |
|---|---|---|---|
| Dukascopy | Tick (bid/ask) | Free | FX + Gold, 2003-present |
| TrueFX | Tick (bid/ask) | Free | Major FX pairs |
| TickStory (Dukascopy frontend) | Tick/Bar | Free tool | FX + Gold |
| IBKR `reqHistoricalTicks` | Tick | Free (with account) | Limited batches |
| Our own recording | Raw ticks | Free | Accumulates over time |

---

## 3. Other Future Items

- **Alerting:** Telegram/email notifications on trade entry, exit, errors, daily summary
- **Position sizing:** Kelly criterion or fixed-fractional based on account equity
- **Multi-account:** Run same strategies across multiple IBKR accounts
- **Performance analytics:** Sharpe, Sortino, max drawdown, win rate, avg R by strategy
- **Correlation monitoring:** Track if Darvas and 4H Retest signals cluster (reduces diversification benefit)
- **News calendar integration:** Avoid trading around FOMC, NFP, ECB (mechanical filter, no LLM needed)

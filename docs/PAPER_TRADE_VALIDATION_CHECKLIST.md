# ORB Paper Trade — Validation Checklist

**Purpose:** Turn "watch for paper trades" into a specific, observable protocol. Detect bugs in days, not weeks.
**Command to start:** `start_v11.bat --live --no-llm`
**Expected cadence:** ~1 trade/week (gap filter skips ~45% of days). At ~52 trades/yr, first 20 trades should take 4–5 months.

---

## Pre-flight (before starting)

- [ ] IB Gateway logged in, port 4002 responding (`python -m v11.live.gateway_manager --check`)
- [ ] `C:\nautilus0\data\1m_csv\eurusd_1m_tick.csv` is the repaired version (first close ~1.20, NOT 120)
- [ ] No stale processes: `tasklist | findstr python` shows only expected ones
- [ ] Lock file absent or stale: `v11\live\.gateway_manager.lock` (if present, delete)
- [ ] Disk space on C: > 5 GB free (tick logs grow ~20 MB/day)
- [ ] Tests pass: `python -m pytest v11/tests/ -q` → 452 passed

## First 5 minutes (startup)

- [ ] Banner shows: `Strategies: ORB (XAUUSD)` — NOT "Darvas + 4H Retest + ORB"
- [ ] Banner shows: `Instruments: ['XAUUSD']` — NOT `['EURUSD', 'XAUUSD']`
- [ ] `LLM filter: DISABLED` appears (because `--no-llm` flag)
- [ ] `Contract qualified: XAUUSD` logged
- [ ] `Price stream started: XAUUSD` logged
- [ ] `RUNNER: Added V6_ORB on XAUUSD` logged
- [ ] `Seeding XAUUSD buffer...` → `Fetched N historical bars` (N should be ~5700 for 5D × 1min)
- [ ] `Runner ready: 1 strategies on ['XAUUSD']`
- [ ] No `ERROR` or `CRITICAL` in the startup log
- [ ] Heartbeat file appears within 5 min: `v11\live\state\heartbeat.json` — open it, timestamp should be recent

## First 30 minutes

- [ ] Status log lines appear every ~minute: `[STATUS] V6_ORB on XAUUSD: ...`
- [ ] Price ticks are being received — check `data\ticks\XAUUSD_<date>.csv`, row count growing
- [ ] No `PRICE STALE` warnings
- [ ] No `IBKR disconnected` warnings
- [ ] No Python tracebacks

## First 24 hours (observe one full day cycle)

**00:00 UTC — Daily reset:**
- [ ] `ORB: Daily reset for YYYY-MM-DD` in logs
- [ ] `Refreshing daily bars for XAUUSD` fires (new as of Phase B)
- [ ] `Loaded N daily bars for ORB LLM` — N should be 20

**06:00 UTC — Range calculation:**
- [ ] `ORB: Calculating Asian range` in logs
- [ ] Either `Asian range: high=X.XX low=X.XX size=X.XX` OR `Range too wide/narrow` rejection
- [ ] If accepted, status should show `state=IDLE range=X.XX-X.XX`

**06:00-08:00 UTC — Gap filter:**
- [ ] Either `Gap filter PASS` or `Gap filter REJECT (vol)` — one should fire

**08:00-16:00 UTC — Trade window:**
- [ ] State transitions to `RANGE_READY` if gap passed
- [ ] Status shows `range=X.XX-X.XX` and `current_price` near the range
- [ ] If price breaks range: expect `Entry stops placed` then state → `ORDERS_PLACED`
- [ ] If no breakout by 16:00: state → `DONE_TODAY`, no position held overnight

**End of day:**
- [ ] Heartbeat file still being updated (5-min cadence)
- [ ] Day's tick CSV is a reasonable size (XAUUSD trades ~23h, expect 5–15 MB)
- [ ] No position left open (either no trade or closed at EOD)

## First week (5 trading days)

- [ ] **At least 3 days reached RANGE_READY.** If 0 days did, range filter or velocity logic is broken.
- [ ] **At least 1 gap-filter rejection logged.** If 0 in 5 days, gap filter may not be firing.
- [ ] **Heartbeat continuity:** timestamp always fresh (<10 min old). If stale > 10 min, process froze.
- [ ] **No Wednesday trades.** Check `skip_weekdays=(2,)` is actually respected. On Wed, state should stay IDLE all day.
- [ ] **First fill (if any) reviewed manually:**
  - Fill price within 0.3pt of range boundary?
  - Direction matches which side broke (above range high = LONG, below low = SHORT)?
  - SL / TP placed at correct levels (opposite range boundary; rr_ratio × range for TP)?
  - PnL calculation matches `(exit - entry) × qty × point_value`?

## Red flags (stop and investigate if any of these happen)

| Signal | Likely cause | Action |
|---|---|---|
| Strategy never reaches RANGE_READY in 5+ days | Range limits too strict, gap filter always rejects, or data issue | Check `min_range_size`, `max_range_size`, gap filter logs |
| Bracket placed but never filled during active breakout | Order placement bug (silent-order-failure regressed?) or broker rejection | Check IBKR TWS/Gateway for rejected orders |
| Heartbeat file stale > 10 min while process alive | Deadlock or infinite loop | Kill process, check last log line, file bug |
| Tick count in status = 0 for minutes | Market data subscription lost | Check `PRICE STALE` warnings, restart if escalation doesn't auto-recover |
| Fill happens Wednesday | `skip_weekdays` not enforced | Check `orb_adapter.py` weekday check; file bug |
| Fill happens before 08:00 or after 16:00 UTC | Trade window check broken | Check `trade_start_hour` / `trade_end_hour` handling |
| PnL on dashboard doesn't match log calculation | Dashboard bug or CSV write bug | Reconcile manually, check `TradeManager` logs |
| EURUSD appears anywhere in logs | Instrument/strategy config regression | Check `LiveConfig.instruments` and `darvas_enabled` |
| Process exits cleanly (code 0) without user intent | Emergency shutdown | Check `v11/live/state/shutdown_*.json` for reason |

## Success indicators (after 4 weeks)

- **~4–5 real paper fills** captured in `v11/live/trades/`
- **Heartbeat uptime > 95%** (allow for weekend gateway restarts)
- **Fill slippage median < 0.2pt** vs expected entry price
- **No unexpected process exits** (other than user-initiated or weekend maintenance)
- **WR and AvgR trending toward** backtest expectation (+48% WR, +0.1–0.18 AvgR OOS) — wide tolerance is OK with small sample

## What does NOT count as validation

- The process not crashing — absence of error is not presence of correctness
- Status logs showing "fine" — they can show fine while the logic is broken
- LLM decisions being made — we're running with `--no-llm`
- Dashboard looking pretty — it can render correctly on wrong data

## Weekly review ritual (~10 min every Saturday)

1. Count days the strategy reached RANGE_READY
2. Count trades placed, won, lost
3. Compute running WR and AvgR, compare to backtest expectation
4. Grep logs for `ERROR`, `CRITICAL`, `Failed`, `STALE` — investigate each
5. Check `state/shutdown_*.json` for any emergency shutdowns during the week
6. Write one line in a running observation log (create `docs/paper_trade_log.md`)

## What triggers a pause

Stop paper trading and investigate if any of:
- Zero RANGE_READY days in a full week of non-holiday trading
- Multiple ERROR entries that aren't "IBKR disconnected" (reconnect is normal)
- A fill at a wildly wrong price (>1pt drift from expected)
- Dashboard PnL diverges from logged PnL by > $5
- Any unhandled exception in the main loop

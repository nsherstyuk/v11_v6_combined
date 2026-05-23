# Handoff — weekend Mac shutdown 2026-05-23

**Status at shutdown:** v11 paper running cleanly, no open position,
no pending orders, daily journal current. Markets are closed
(weekend); shutting down loses nothing important.

## State as of this handoff

- **v11 paper PID 90635**, uptime 13h+, state=DONE_TODAY for 2026-05-22
- No open position. No pending bracket orders. Risk Manager: 0/3 positions.
- Last commit on `master`: `eeb7a87` ("v11 daily journal 2026-05-22: NO-TRADE")
  pushed to `origin/master`. Working tree clean.
- IBC Gateway running (port 4002 open). Will auto-stop on shutdown.

## Live record (3 trades, 8 trading days)

| Date | Dir | Entry | Exit | PnL | Outcome |
|---|---|---|---|---|---|
| 2026-05-13 | SHORT | 4687.63 | 4695.59 | −$7.96 | window-close MARKET |
| 2026-05-19 | SHORT | 4529.70 | 4509.20 | +$20.50 | window-close MARKET |
| 2026-05-21 | SHORT | 4518.09 | 4512.87 | +$5.22 | window-close MARKET |

**Cumulative net: +$17.76 across 3 trades, 2W/1L.**

No-trade days so far: 5/12 (stale-breakout), 5/14/18/20/22 (4h-pending),
5/15 (range-too-wide).

## What survives shutdown

- All code on disk (already pushed to GitHub `origin/master`)
- launchd plists in `~/Library/LaunchAgents/`:
  - `com.ibc.gateway.plist` — Gateway auto-start on login + KeepAlive
  - `com.nick.daily-restart.plist` — fires at 01:15 EDT daily
  - `com.nick.ibkr-health.plist` — 30-min health probe
- IBC config at `~/ibc/` (credentials managed by Nick)
- All journals, scripts, data on disk
- All historical data in `tick_vault_data/` (US equities, FX, XAUUSD)

## What does NOT survive shutdown

- The running v11 Python process (PID 90635) — will be killed by shutdown
- The running caffeinate wrapper
- Session-local Claude cron job `ee806753` (daily journal at 12:37 EDT) —
  will need to be re-armed when Claude Code session resumes
- The current Claude Code conversation context (may need to re-bootstrap
  from CLAUDE.md + recent journals on next session)

## Resumption checklist (when you turn the Mac back on)

1. **Log in.** macOS will auto-launch `com.ibc.gateway` on user login
   (launchd RunAtLoad=true). Gateway should come up within 30-60s of login,
   triggering 2FA on your IBKR mobile app if needed.

2. **Wait for Gateway port 4002.** Quick check from Terminal:
   ```bash
   nc -z -w 2 127.0.0.1 4002 && echo OPEN || echo NOT YET
   ```
   Retry every minute if not open; usually takes 1-3 minutes after login.

3. **Decide when to start v11:**
   - **Option A (cleanest):** wait for the next 01:15 EDT daily-restart cron
     to start v11 automatically. This is the unattended path the system was
     designed for. It will kill any half-state (Gateway, stale v11 from
     prior session if any) and bring up a clean v11.
   - **Option B (manual, if you want v11 running before 01:15 EDT):**
     ```bash
     bash ~/code/ibkr_grok_wing_agent/docs/agents/scripts/daily_restart.sh
     ```
     This runs the same script the cron uses. Takes ~1-2 min.

4. **Verify v11 is healthy.** One-line check:
   ```bash
   ~/code/ibkr_grok_wing_agent/docs/agents/scripts/v11_status.sh
   ```
   Or set the alias once (recommended):
   ```bash
   echo "alias v11s='~/code/ibkr_grok_wing_agent/docs/agents/scripts/v11_status.sh'" >> ~/.zshrc
   source ~/.zshrc
   ```
   Then just `v11s` anytime.

5. **Resume Claude Code session** (if/when you want to talk through state):
   - Open this repo in Claude Code (`cd ~/code/ibkr_grok_wing_agent`)
   - First message: "what's the status?" — I'll re-poll v11 and report
   - I'll re-arm the daily journal cron after first session resumption
     (it auto-expires anyway but only after 7 days from creation)

## What to expect on Monday (2026-05-25)

Assuming the Mac is back up + IBC Gateway re-logged in by then:

- **01:15 EDT Monday:** daily-restart cron fires, kills any stale state,
  cycles Gateway + v11. New v11 PID. Should log SUCCESS in
  `~/.daily_restart.log`.
- **02:00 EDT Monday:** v11 computes Asian range for Monday.
- **04:00 EDT Monday:** trade window opens. ORB places brackets.
- **04:00-08:00 EDT:** brackets either fire (3rd or 4th live fill?) or
  get cancelled by 4h-pending guard.
- **12:00 EDT:** trade window closes; if IN_TRADE, V6 MARKET-closes.
- **12:37 EDT:** if Claude Code session is open, the auto-journal cron
  fires and writes the daily summary.

If the Mac is NOT back up by Monday 01:15 EDT, the cron just doesn't
fire that day (launchd skips missed jobs when system was off). No
recovery action needed — when you eventually boot, the next 01:15 EDT
cron fire will catch things up.

## Pending items (none urgent)

These are noted for completeness but don't need attention before
resuming:

1. **01:00 ET Gateway auto-restart pattern** — 5 observations. IBKR's
   Gateway has its own 01:00 ET restart, separate from our 01:15 EDT
   cron. v11 sees Connection lost + "Failed to connect after 3
   attempts" briefly, then our cron 15 min later kills/restarts
   everything. Functionally fine. Not blocking.
2. **Replay-vs-live range-source divergence** documented 2026-05-18.
   Small precision difference in range computation between live IBKR
   historical bars and replay's tick-reconstructed bars. Not blocking;
   doesn't affect material backtest result.
3. **USDJPY regime-conditional research** — earlier pre-registered
   USDJPY ORB failed the unconditional gate, but year-by-year shows
   2022-2025 all positive. If you want to explore that as a second
   strategy, it needs a fresh separate pre-registration on a
   different research day. Data is already downloaded
   (`tick_vault_data/fx/USDJPY_*.csv`).

## Honest read on where we are

- **Backtest validates** the XAUUSD ORB strategy (`docs/journal/2026-05-19_first_profitable_live_trade_and_backtest_validates.md`):
  OOS 2018-2023 AvgR_slip +0.085, IS 2024-now +0.108, 7 of 8 positive years since 2018.
- **Live record so far** consistent with backtest baseline. 3 trades, net positive.
  Sample too small for statistical inference but no red flags.
- **max_pending_hours sweep** (`docs/journal/2026-05-20_max_pending_hours_sweep_keep_mp4.md`)
  confirmed the current 4h value is correct — longer windows add trades
  with similar expectancy, not better.
- **2026 YTD is mildly underwater** in backtest (−0.025 AvgR_slip on N=15).
  Live is small-sample positive (+$17.76 / 3 trades). Normal slow stretch
  within long-run record.

## Files to read first if Claude session needs to re-bootstrap

In order of importance:

1. `CLAUDE.md` — standing instructions for the session
2. `docs/PROJECT_STATUS.md` — Current state (top section)
3. This handoff file
4. Recent journals (most recent date in `docs/journal/`)
5. `docs/workflow.md` — single-agent self-review workflow

Most-recent journal-index in PROJECT_STATUS.md will also point you at
the right reading order.

## Sleep well

Nothing breaking, nothing pending. Mac off is fine.

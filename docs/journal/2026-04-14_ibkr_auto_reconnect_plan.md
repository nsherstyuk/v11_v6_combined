# IBKR Auto-Reconnect & Unattended Operation — Research + Plan

**Date**: 2026-04-14
**Author**: Claude Opus 4.6
**Scope**: Make V11 run 24/5 unattended on Windows 11 with IBKR paper account
**Status**: Research complete, plan ready for implementation

---

## 1. The Problem

V11 runs on IBKR paper account (port 4002) but cannot survive:
- **Daily IBKR maintenance** (~11:45 PM ET): brief disconnect, all API clients dropped
- **Weekly authentication expiry**: Sunday 1:00 AM ET, requires manual re-login + 2FA
- **TWS/Gateway crashes or updates**: process dies, no one restarts it
- **Network blips**: connection lost temporarily

The goal is **true 24/5 unattended operation** from Sunday 5 PM ET (FX open) to Friday 5 PM ET (FX close), with at most one manual 2FA authentication per week.

---

## 2. External Tools Research

### Tool 1: IBC (IB Controller) — RECOMMENDED

- **Repo**: [IbcAlpha/IBC](https://github.com/IbcAlpha/IBC)
- **What it does**: Automates TWS/Gateway login, auto-restart, and authentication
- **Platform**: Windows, Linux, macOS
- **How it works**:
  - Launches TWS or Gateway with credentials from `config.ini`
  - Fills login dialog automatically (username + password)
  - Clicks through login prompts, 2FA acceptance
  - Handles daily auto-restart (configurable time, default 5 AM ET)
  - Re-authenticates after restart without user interaction
  - **Authentication required only once per week** (first login after Sunday 1 AM ET)
- **Key config** (`config.ini`):
  ```ini
  IbLoginId=your_username
  IbPassword=your_password
  TradingMode=paper
  ExistingSessionDetectedAction=primary
  AcceptIncomingConnectionAction=accept
  AutoRestartTime=05:00
  ```
- **Status**: Actively maintained, widely used in algo trading community
- **Gotcha**: Auto-restart with Gateway broken in IBC >3.20.0 (use Gateway's built-in auto-restart instead, or use TWS)
- **Best for**: Our use case. Windows native, handles Gateway auto-restart + weekly re-auth.

### Tool 2: IBAutomater (QuantConnect)

- **Repo**: [QuantConnect/IBAutomater](https://github.com/QuantConnect/IBAutomater)
- **What it does**: Automates IB Gateway start, stop, restart, and login
- **Platform**: Windows (.NET/NuGet), Linux
- **How it works**:
  - Programmatic API (C#/.NET library) to start/stop Gateway
  - Auto-selects "auto-restart" over "auto-logoff" in Gateway settings
  - 2FA via IBKR mobile app (seamless authentication)
  - 2FA only needed once per week
- **Status**: Maintained by QuantConnect (used in their Lean engine)
- **Gotcha**: C#/.NET library — doesn't integrate naturally with our Python stack. Would need a separate process or wrapper.
- **Best for**: .NET shops or QuantConnect/Lean users.

### Tool 3: IBeam

- **Repo**: [Voyz/ibeam](https://github.com/Voyz/ibeam)
- **What it does**: Authentication and maintenance for the IBKR Client Portal Web API Gateway
- **Platform**: Docker or standalone Python
- **How it works**:
  - Injects credentials into the Client Portal Gateway authentication page
  - Keeps sessions alive via health checks
  - Docker-native deployment
- **Status**: Maintained, popular for web API users
- **Not for us**: IBeam is for the **Client Portal Web API**, not the TWS/Gateway API we use (ib_insync connects via the TWS API on port 4002). Different protocol entirely.

### Tool 4: IB Gateway Built-in Auto-Restart

- **What it does**: Gateway itself can auto-restart daily
- **How to enable**: Configure → Lock and Exit → Auto Restart → set time (e.g., 5:00 AM ET)
- **Behavior**: Restarts Gateway process, briefly drops all API connections, reconnects without re-authentication (except Sunday weekly reset)
- **No extra software needed**
- **Gotcha**: Does NOT handle the weekly Sunday re-authentication. Still need IBC or manual login once per week.

### Recommendation

**Use IBC + Gateway's built-in auto-restart:**

1. Install IBC on Windows
2. Configure IBC with paper account credentials
3. Enable Gateway's built-in auto-restart (5:00 AM ET daily)
4. IBC handles the weekly Sunday re-authentication
5. V11's reconnection logic handles the brief daily disconnect

This gives us: daily auto-restart (Gateway built-in) + weekly auto-login (IBC) + robust reconnection (V11 code improvements).

---

## 3. Current V11 Reconnection Analysis

### What already works

| Feature | Status | Location |
|---|---|---|
| Auto-reconnect on disconnect | Works (3 retries, backoff) | `ibkr_connection.py:58-88` |
| Heartbeat check | Every 30s via `reqCurrentTime()` | `ibkr_connection.py:90-126` |
| Contract re-qualification | On reconnect | `ibkr_connection.py:105-113` |
| Market data stream restart | On reconnect | `ibkr_connection.py:114-125` |
| Position reconciliation | On reconnect | `run_live.py:370-371` |

### What's broken or missing

| Gap | Impact | Severity |
|---|---|---|
| **No retry limit** — loops forever if IBKR is down | System hangs, no alerting | Critical |
| **No price feed monitoring** — stale tickers are silent | Strategies stall without warning | Critical |
| **No emergency close** — persistent failure = no action | Open positions drift unmanaged | Critical |
| **Orphaned positions not handled** — detected but ignored | Position hangs open forever | High |
| **Risk manager not synced with broker** — only in-memory | Desync after reconnect | High |
| **Daily reset at UTC midnight, not broker session** — off by 7 hours | PnL limits span two sessions | Medium |
| **Incomplete bars lost on disconnect** — aggregator resets | 1-2 min data gap after reconnect | Medium |
| **ORB state not persisted** — range/gap data in RAM only | ORB may miss range after restart | Medium |
| **30s heartbeat too slow** — up to 30s blind spot | Missed ticks during detection lag | Low |

---

## 4. Implementation Plan for Next Agent

### Phase A: Install and configure IBC (manual, not code)

This is a manual setup step the user does once:

1. Download IBC latest release from [GitHub](https://github.com/IbcAlpha/IBC/releases)
2. Install to `C:\IBC\`
3. Create `config.ini` with paper account credentials:
   ```ini
   IbLoginId=YOUR_USERNAME
   IbPassword=YOUR_PASSWORD
   TradingMode=paper
   ExistingSessionDetectedAction=primary
   AcceptIncomingConnectionAction=accept
   ```
4. Enable Gateway auto-restart: Configure → Lock and Exit → Auto Restart at 05:00 AM ET
5. Create a Windows Scheduled Task that runs IBC's `StartGateway.bat` at system boot
6. Test: restart the machine, verify Gateway auto-starts and V11 can connect

**No code changes needed for this phase.**

### Phase B: Robust reconnection in V11 (code changes)

**Priority 1: Connection retry limits + emergency close**

File: `v11/execution/ibkr_connection.py`

- Add `MAX_RECONNECT_DURATION = 300` (5 minutes)
- Track `_first_disconnect_time`
- If disconnected for >5 minutes: log CRITICAL, return special status
- In `run_live.py`: if `ensure_connected()` returns a "persistent failure" status:
  - Log all open positions
  - Attempt one final reconnection to close positions
  - If that fails: write state to disk, exit with error code
  - The idea is: if Gateway is truly dead, IBC will restart it, and V11 can be restarted by a Windows Scheduled Task or wrapper script

File: `v11/live/run_live.py` — main loop

- Add `_disconnect_start_time: Optional[datetime]` field
- On first disconnect: record time
- On reconnect: clear time
- If `(now - _disconnect_start_time) > MAX_DISCONNECT_SECONDS`:
  - Call `_emergency_shutdown()`
  - Write state file: `v11/live/state/emergency_shutdown.json` with positions, PnL, reason
  - `sys.exit(1)` — let the wrapper script/scheduled task restart V11

**Priority 2: Price feed staleness detection**

File: `v11/live/run_live.py` — main loop

- Add `_last_price_time: Dict[str, datetime]` per instrument
- On each price update: `_last_price_time[pair] = now`
- Every status log (5 min): check for stale feeds
- If no price for >60s during market hours: log WARNING
- If no price for >300s during market hours: log ERROR, trigger reconnect attempt (market data stream restart)

**Priority 3: Orphaned position handling**

File: `v11/execution/trade_manager.py` — `reconcile_position()`

- Currently: detects orphan, logs warning, does nothing
- Change: if orphaned position detected and `auto_close_orphans=True`:
  - Close position at market
  - Log the close with reason "ORPHAN_CLOSE"
  - Report to risk manager
- Default `auto_close_orphans=False` (safe default, user opts in)

**Priority 4: Risk manager broker sync**

File: `v11/live/run_live.py` — `_reconcile_positions()`

- After reconnect, query all broker positions via `ib.positions()`
- Compare with risk manager's `_positions` dict
- If broker has position that risk manager doesn't know about: add it
- If risk manager thinks there's a position but broker doesn't: remove it
- Log all discrepancies

**Priority 5: Daily reset alignment with broker session**

File: `v11/live/run_live.py` — main loop date change detection

- Currently: resets at UTC midnight (`now.strftime("%Y-%m-%d")` changes)
- Add: also check for 5 PM ET (22:00 UTC in winter, 21:00 UTC in summer)
- Use Eastern timezone for the check:
  ```python
  from zoneinfo import ZoneInfo
  et_now = now.astimezone(ZoneInfo("America/New_York"))
  if et_now.hour == 17 and et_now.minute < 2 and not self._session_reset_done:
      self.runner.reset_daily()
      self._session_reset_done = True
  ```
- Reset `_session_reset_done` flag at 18:00 ET (after market open)

**Priority 6: V11 auto-restart wrapper script**

Create: `v11/live/start_v11.bat` (or `start_v11.ps1`)

```batch
@echo off
:loop
echo [%date% %time%] Starting V11...
python -m v11.live.run_live
echo [%date% %time%] V11 exited with code %errorlevel%
if %errorlevel% EQU 0 goto end
echo Restarting in 30 seconds...
timeout /t 30
goto loop
:end
echo V11 stopped cleanly.
```

This ensures that if V11 exits (emergency shutdown, crash, etc.), it restarts automatically after 30 seconds. Combined with IBC keeping Gateway alive, this gives us full 24/5 unattended operation.

### Phase C: Monitoring and alerting (optional, future)

- Write status to a file every 5 minutes: `v11/live/state/heartbeat.json`
  - Contains: timestamp, positions, PnL, last price times, connection status
- Another process or scheduled task checks the heartbeat file
- If stale (>10 min old): send alert (email, Telegram, etc.)
- This is lower priority — Phase A + B give us unattended operation, Phase C adds observability

---

## 5. Estimated Scope

| Phase | Effort | Code changes | Files |
|---|---|---|---|
| A: IBC setup | Manual, ~30 min | None | N/A |
| B1: Retry limits + emergency close | Medium | ~80 lines | `ibkr_connection.py`, `run_live.py` |
| B2: Price feed staleness | Small | ~30 lines | `run_live.py` |
| B3: Orphaned position handling | Small | ~25 lines | `trade_manager.py` |
| B4: Risk manager sync | Medium | ~40 lines | `run_live.py` |
| B5: Daily reset alignment | Small | ~20 lines | `run_live.py` |
| B6: Wrapper script | Small | New file | `start_v11.bat` |
| C: Monitoring | Optional | ~60 lines | `run_live.py`, new file |

**Total coding work**: ~200 lines of changes across 3-4 files + 1 new script.

---

## 6. What the Next Agent Should Do

1. **Read this document first** — it has the full analysis and plan
2. **Read the current connection code**: `v11/execution/ibkr_connection.py` and `v11/live/run_live.py`
3. **Implement Phase B in priority order** (B1 first, then B2, etc.)
4. **Write tests** for each change:
   - Retry limit: mock connection failures, verify emergency shutdown triggers
   - Price staleness: mock stale ticker, verify warning/error logged
   - Orphaned position: mock broker position mismatch, verify close
5. **Do NOT modify IBC or Gateway config** — that's manual Phase A for the user
6. **Do NOT install new packages** — everything needed is in stdlib (`zoneinfo`, `asyncio`)
7. **Run all tests**: `python -m pytest v11/tests/ -v` — must pass before committing

### Key files to read

| File | Why |
|---|---|
| `v11/execution/ibkr_connection.py` | Connection, heartbeat, reconnect logic |
| `v11/live/run_live.py` | Main loop, disconnect handling, reconciliation |
| `v11/execution/trade_manager.py` | Position state, reconciliation, orphan detection |
| `v11/live/risk_manager.py` | Position tracking (in-memory only) |
| `v11/live/orb_adapter.py` | ORB state that could be lost on restart |

### Testing the changes

Without a real IBKR disconnect, testing requires mocking. The existing tests in `test_stub_connection.py` and `test_run_live.py` show how the connection is mocked. Follow the same pattern.

---

## 7. References

- [IBC (IB Controller)](https://github.com/IbcAlpha/IBC) — auto-login, auto-restart for TWS/Gateway
- [IBC User Guide](https://github.com/IbcAlpha/IBC/blob/master/userguide.md) — full configuration reference
- [IBAutomater (QuantConnect)](https://github.com/QuantConnect/IBAutomater) — .NET alternative
- [IBeam](https://github.com/Voyz/ibeam) — for Client Portal Web API (not TWS API)
- [ib-gateway-docker](https://github.com/gnzsnz/ib-gateway-docker) — Docker image with IBC pre-configured
- [IBKR Auto-Restart Considerations](https://www.ibkrguides.com/traderworkstation/auto-restart-considerations.htm) — official IBKR docs
- [IBC Automating and Scheduling](https://deepwiki.com/IbcAlpha/IBC/6.2-automating-and-scheduling-ibc) — scheduling guide

# IBKR Auto-Reconnect & Unattended Operation — Implementation Session

**Date**: 2026-04-14 (8:00 AM - 8:35 AM ET)
**Author**: Cascade (AI pair programmer)
**Scope**: Implement Phase B (code changes) from `docs/journal/2026-04-14_ibkr_auto_reconnect_plan.md` + GatewayManager hybrid IBC integration
**Status**: ✅ Complete — all code changes implemented, 345 tests pass, manual IBC setup remaining

---

## 1. Session Context

Previous session (Opus) created the auto-reconnect plan document. This session implements it.

**Starting point**: V11 live trader running on paper account. Overnight log proved reconnection works for brief disconnects (00:25 reconnect in 55s) but there's no safety net if IBKR stays down longer. No auto-login capability.

**Goal**: Make V11 run 24/5 unattended with:
- Safety net for persistent IBKR failures (emergency shutdown + auto-restart)
- Price feed staleness detection
- Orphaned position handling
- Broker/risk manager sync after reconnect
- Proper session boundary alignment
- Auto-restart wrapper script
- IBC integration for auto-login/re-authentication

---

## 2. What Was Done

### Phase B1: Connection Retry Limits + Emergency Close

**Problem**: V11 loops forever trying to reconnect if IBKR Gateway is down. No limit, no alerting, no position protection.

**Changes**:

| File | Change |
|---|---|
| `v11/execution/ibkr_connection.py` | Added `MAX_RECONNECT_DURATION=300`, `_first_disconnect_time` tracking, `persistent_failure` property. Timer starts on `_on_disconnect()` and `_on_error()` critical errors. `ensure_connected()` logs elapsed time and declares PERSISTENT FAILURE after 5 min. Clears timer on successful reconnect. |
| `v11/live/run_live.py` | Added `_disconnect_start` tracking, `MAX_DISCONNECT_SECONDS=300`. Main loop tracks disconnect duration, triggers `_emergency_shutdown()` after timeout. Emergency shutdown: logs positions, cancels orders, attempts final reconnect to close positions, writes `v11/live/state/emergency_shutdown.json`, exits with code 1. |
| `v11/execution/trade_manager.py` | Added `emergency_close()` method for force-closing positions during emergency shutdown. Cancels SL, closes at market, resets state. |

**Flow**: IBKR down → 5 min of reconnect attempts → CRITICAL log → cancel orders → attempt final reconnect to close positions → write state file → `sys.exit(1)` → wrapper script restarts V11

### Phase B2: Price Feed Staleness Detection

**Problem**: If a price feed goes stale, strategies just stop receiving ticks silently. No warning, no auto-recovery.

**Changes**:

| File | Change |
|---|---|
| `v11/live/run_live.py` | Added `_last_price_time: dict[str, float]` per instrument. `_check_price_staleness()` called every 5 min status log: >60s stale = WARNING, >300s stale = ERROR + auto-restart market data stream (cancel old `reqMktData`, re-subscribe). |

### Phase B3: Orphaned Position Auto-Close

**Problem**: `reconcile_position()` detects orphaned broker positions (broker has position, internal state doesn't) but only logs a warning. Position hangs open forever.

**Changes**:

| File | Change |
|---|---|
| `v11/execution/trade_manager.py` | Added `auto_close_orphans: bool = False` param. When enabled, `reconcile_position()` closes orphaned broker positions at market. Determines direction from broker position sign. |
| `v11/live/multi_strategy_runner.py` | Passes `auto_close_orphans` from `LiveConfig` to `TradeManager`. |
| `v11/config/live_config.py` | Added `auto_close_orphans: bool = False` field. Safe default — user must opt in. |

### Phase B4: Risk Manager Broker Sync on Reconnect

**Problem**: After reconnect, risk manager's in-memory position tracking may be out of sync with broker. Could block new trades or allow over-allocation.

**Changes**:

| File | Change |
|---|---|
| `v11/live/run_live.py` | Enhanced `_reconcile_positions()` with two-level reconciliation: (1) TradeManager per-instrument state vs broker, (2) RiskManager portfolio-level tracking vs broker. Queries `ib.positions()`, compares with risk manager's `_positions` dict. Adds missing positions, removes stale ones. |

### Phase B5: Daily Reset Aligned to 5 PM ET Broker Session

**Problem**: Daily reset happens at UTC midnight, but FX market session boundary is 5 PM ET. PnL limits span two sessions.

**Changes**:

| File | Change |
|---|---|
| `v11/live/run_live.py` | Added broker session reset at 5 PM ET (Mon-Fri) using `zoneinfo.ZoneInfo("America/New_York")`, in addition to existing UTC midnight reset. Uses `_session_reset_done` guard to prevent double-reset. |

### Phase B6: Auto-Restart Wrapper Script

**Changes**:

| File | Change |
|---|---|
| `v11/live/start_v11.bat` | Wrapper script: auto-restarts V11 on error exit code, stops on clean exit (Ctrl+C). Now integrates with GatewayManager — checks Gateway health before starting V11, restarts Gateway if down. |

### GatewayManager (Hybrid IBC + Python)

**New file**: `v11/live/gateway_manager.py`

This is the hybrid approach: IBC handles the actual login dialog automation, Python monitors Gateway health and triggers IBC restarts.

| Method | Purpose |
|---|---|
| `check_gateway_health()` | Socket test on configured port (doesn't need ib_insync) |
| `start_gateway_via_ibc()` | Launches IBC's `StartGateway.bat` with credentials |
| `ensure_gateway_running()` | Check + auto-start via IBC if down |
| `wait_for_gateway()` | Waits up to 120s for Gateway to respond |
| `setup_guide()` | Prints IBC setup instructions with current status |

**CLI modes**:
- `python -m v11.live.gateway_manager` — persistent monitor (rate-limited restarts, 3/hour)
- `python -m v11.live.gateway_manager --check` — one-shot health check (exit 0=healthy, 1=down)
- `python -m v11.live.gateway_manager --setup` — print IBC setup guide
- `python -m v11.live.gateway_manager --port 4002` — custom port

---

## 3. Files Changed

| File | Lines Added | Type |
|---|---|---|
| `v11/execution/ibkr_connection.py` | ~25 | Modified |
| `v11/execution/trade_manager.py` | ~35 | Modified |
| `v11/live/run_live.py` | ~120 | Modified |
| `v11/live/multi_strategy_runner.py` | ~1 | Modified |
| `v11/config/live_config.py` | ~3 | Modified |
| `v11/live/gateway_manager.py` | ~380 | **New** |
| `v11/live/start_v11.bat` | ~53 | Modified (was ~36) |

**Total**: ~200 lines of changes across 5 existing files + 1 new file + 1 new script.

---

## 4. Test Results

```
345 passed, 26 warnings in 2.49s
```

All existing tests pass. No new tests added yet (mocking IBKR disconnects requires infrastructure — see Open Questions).

---

## 5. Verified Behaviors

- `python -m v11.live.gateway_manager --check` → "Gateway is healthy on port 4002" (exit 0)
- `python -m v11.live.gateway_manager --setup` → prints IBC setup guide with current status
- All Phase B imports compile cleanly

---

## 6. Manual Steps Remaining (for user)

1. **Install IBC**: Download from https://github.com/IbcAlpha/IBC/releases → extract to `C:\IBC`
2. **Configure credentials**: Create `C:\Users\nsher\Documents\IBC\config.ini`:
   ```ini
   IbLoginId=YOUR_IBKR_USERNAME
   IbPassword=YOUR_IBKR_PASSWORD
   TradingMode=paper
   AcceptIncomingConnectionAction=accept
   AutoRestartTime=05:00
   ```
3. **Enable Gateway auto-restart**: Gateway → Configure → Lock and Exit → Auto Restart at 05:00 AM ET
4. **Create Windows Scheduled Task**: Run `C:\IBC\StartGateway.bat /Gateway /Mode:paper /Inline` at system startup
5. **Test full flow**: Kill Gateway → verify IBC restarts it → verify V11 reconnects

---

## 7. Open Questions

1. **Unit tests for Phase B** — Need mock infrastructure for IBKR disconnects. The plan suggests following `test_stub_connection.py` pattern.
2. **IBC version compatibility** — IBC >3.20.0 has broken auto-restart with Gateway (use Gateway's built-in auto-restart instead).
3. **2FA handling** — IBC can auto-accept 2FA prompts, but the IBKR mobile app must be installed and logged in on a phone. Weekly re-auth still requires phone access.
4. **Windows Scheduled Task** — Must use `/INLINE` argument for IBC scripts, and "Run only when user is logged on" setting.
5. **Heartbeat file (Phase C)** — Not implemented yet. Would write `v11/live/state/heartbeat.json` every 5 min for external monitoring/alerting.

---

## 8. Key Design Decisions

| Decision | Rationale |
|---|---|
| 5-minute disconnect timeout | Matches IBKR's daily maintenance window (~1 min) with 5x margin. Long enough to survive brief blips, short enough to prevent unmanaged positions. |
| `auto_close_orphans=False` default | Safety first — automatically closing positions without user confirmation is dangerous. User must explicitly opt in. |
| Emergency shutdown writes state file | Post-mortem analysis. If V11 crashes and restarts, the state file shows what happened. |
| GatewayManager as separate module | Separation of concerns — Gateway process management is orthogonal to V11's trading logic. Can run standalone or integrated. |
| Socket-based health check | Doesn't require ib_insync — works even if the Python IB library has issues. |
| Rate-limited Gateway restarts (3/hour) | Prevents restart loops if something is fundamentally wrong (e.g., IBKR servers down for maintenance). |

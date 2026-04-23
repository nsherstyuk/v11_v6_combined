# 2026-04-23 — Half-Open Socket Recovery

## Incident

2026-04-23 ~02:00 → 06:19 UTC. V11 on XAUUSD ORB logged continuous
`Entry placement failed: Not connected` spam for ~2 hours until the
operator sent Ctrl+C. No position held. No money lost. Strategy
correctly declined the stale breakout after manual restart.

## Root cause

Two compounding defects:

1. **Tactical `ib.disconnect()` in the executor's placement error
   handler silently no-op'd.** On a half-open socket, ib_insync's
   internal state is already inconsistent. Calling `ib.disconnect()`
   does not fire `disconnectedEvent`. The connection manager's
   `_connected` flag stayed True. `ensure_connected()` kept returning
   True. No reconnect cycle started. Every poll tried to place orders
   on a dead socket and failed with the same exception.

2. **No tripwire.** The main loop had no escape hatch for the
   "reconnect path stuck" case. `start_v11.bat` auto-restart depends
   on a non-zero exit; without one, V11 just spun.

## Fixes landed

### P0.4a — connection-owned `force_disconnect()`

`v11/execution/ibkr_connection.py` — new method:

- Sets `self._connected = False` directly (bypasses ib_insync's
  stale internal state).
- Starts `_first_disconnect_time` if not already running (so
  `persistent_failure` and `last_outage_s` work).
- Calls `ib.disconnect()` best-effort (but doesn't depend on it).

Executors and market-context callers now route through this instead
of poking `ib.disconnect()` themselves.

### P0.4b — executor connectivity error routing

`v11/v6_orb/ibkr_executor.py`:

- `__init__` takes `force_disconnect_callback`.
- On "Not connected" / "connection" / "timeout" placement
  exceptions, increment `_consec_placement_conn_failures` and invoke
  the callback.
- On successful placement, reset counter to 0.
- On non-connectivity errors, reset counter (those don't indicate a
  bad socket).
- New `placement_stuck` property — True at threshold (15 consecutive,
  ~30s at 2s poll).

Same pattern applied to `v11/v6_orb/live_context.py` for historical
fetch failures.

### P0.4c — placement-stuck tripwire

`v11/live/run_live.py` main loop — on each iteration, if any ORB
engine reports `placement_stuck`, log CRITICAL, call `_cleanup()`,
`sys.exit(1)`. The `start_v11.bat` wrapper restarts with a fresh
socket.

### Wiring

- `v11/live/orb_adapter.py` — accepts and forwards
  `force_disconnect_callback` to both LiveMarketContext and
  IBKRExecutionEngine.
- `v11/live/multi_strategy_runner.py` — wires
  `self._conn.force_disconnect` into `add_orb_strategy`.

## Tests

`v11/tests/test_force_disconnect_recovery.py` — 13 new tests:

- `force_disconnect()` sets `_connected=False`, starts outage timer,
  preserves existing timer, calls `ib.disconnect()` best-effort,
  swallows exceptions.
- Counter increments on connectivity errors, accumulates, resets on
  success, resets on non-connectivity errors.
- `placement_stuck` flips at threshold.
- Fallback raw `ib.disconnect()` still runs when callback not wired.
- Callback exception is logged, not raised.

Full suite: **512/512 passing** (499 pre-existing + 13 new).

## Verification still pending

- Paper live run to confirm: (a) a provoked disconnect actually fires
  the reconnect cycle via the callback, (b) the tripwire exits and
  the wrapper restarts cleanly.

## Handoff

Code complete. Tests green. Ready to commit and restart paper.

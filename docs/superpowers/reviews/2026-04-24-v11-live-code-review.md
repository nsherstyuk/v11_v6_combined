# V11 Live Trading Code Review — 2026-04-24

## Executive Summary

The V11 live trading system is now in a substantially stronger operational state than earlier versions. The recent work has correctly focused on the most important failure modes for a broker-connected trading system: silent order rejection, broker/internal state divergence, naked positions, reconnect behavior, stale price feeds, and half-open socket recovery.

My overall opinion: the codebase is not yet elegant, but it is becoming operationally robust. That is the right priority for this stage. The most important recommendation is to avoid adding new strategies or expanding live scope until the current XAUUSD ORB paper-trading path has survived controlled failure testing and a stable paper period.

Current live posture should remain:

- **Primary live/paper strategy:** XAUUSD ORB via V6 adapter.
- **Darvas / 4H retest:** keep disabled unless revalidated and hardened.
- **Live mode:** continue paper/dry-run validation before any real-money deployment.

## Files Reviewed

Primary live path:

- `v11/live/run_live.py`
- `v11/live/multi_strategy_runner.py`
- `v11/live/orb_adapter.py`
- `v11/live/risk_manager.py`
- `v11/config/live_config.py`

Execution and broker interaction:

- `v11/execution/ibkr_connection.py`
- `v11/execution/trade_manager.py`
- `v11/v6_orb/ibkr_executor.py`
- `v11/v6_orb/orb_strategy.py`

Relevant tests and incident-driven coverage:

- `v11/tests/test_force_disconnect_recovery.py`
- `v11/tests/test_phase_b_safety.py`
- Related reconnect, reconciliation, order observability, and naked-position tests.

## Test Status at Review Time

Command run:

```powershell
python -m pytest v11/tests -q
```

Result:

- **520 passed**
- **2 failed**
- **39 warnings**

Failing tests:

- `v11/tests/test_phase_b_safety.py::TestPriceStaleness::test_staleness_restarts_stream_at_300s`
- `v11/tests/test_phase_b_safety.py::TestPriceStaleness::test_staleness_emergency_shutdown_at_600s`

Assessment: these failures appear to be caused by wall-clock dependence. `_check_price_staleness()` now skips escalation during the weekly market-closed window. Because the tests use the real current time, they can fail when run during a Friday/Saturday/Sunday market-closed interval.

This is likely a test determinism issue rather than a live logic issue.

Recommended fix:

- Inject or pass `now_utc` into `_check_price_staleness()`.
- Add explicit tests for both market-open and market-closed behavior.
- Avoid tests whose pass/fail depends on the actual weekday/hour.

## What Looks Strong

### 1. Incident-driven hardening is excellent

The best engineering pattern in the project is that real paper/live incidents have been converted into specific code defenses and regression tests.

Examples:

- Silent IBKR order discard led to `orderStatusEvent` handling and periodic order reconciliation.
- False position-vanished behavior during disconnect led to connection-gated broker position checks.
- Orphaned naked position led to reconnect recovery and naked-position invariant logic.
- Half-open socket behavior led to `force_disconnect()` and placement-stuck restart tripwire.
- Weekend market closure led to suppressing price staleness escalation when no ticks are expected.

This is exactly how safety-critical trading code should evolve: every incident becomes an invariant.

### 2. ORB execution path is much more robust

`v11/v6_orb/ibkr_executor.py` now contains several important safety improvements:

- Entry orders use `TIF=DAY`, avoiding IBKR paper preset rejection of `GTD`.
- Stop orders use `triggerMethod=7`, important for bid/ask-triggered XAUUSD paper behavior.
- Entry order status is observed via `orderStatusEvent`.
- Terminal non-filled entry orders are surfaced via `_entry_failed`.
- Placement connectivity failures are counted and routed to forced disconnect logic.
- Repeated placement failures trigger the `placement_stuck` tripwire.
- Entry fill handling clears both entry IDs to avoid false reconcile alarms.
- A naked-position invariant flattens if a position lacks active SL/TP after the grace period.
- Reconnect handling can re-arm SL/TP after short outages and flatten after long/uncertain outages.

This is the most safety-critical area of the codebase, and it is now significantly better protected.

### 3. `IBKRConnection` has a useful recovery model

`v11/execution/ibkr_connection.py` now handles more than simple reconnects:

- Tracks disconnect start time.
- Exposes `persistent_failure`.
- Captures `last_outage_s` for graduated recovery decisions.
- Re-qualifies contracts and restarts market data streams after reconnect.
- Provides `force_disconnect()` for half-open socket recovery.

The `force_disconnect()` method is especially important because it avoids relying on `ib_insync` to notice an inconsistent socket state.

### 4. Live supervision is practical

`run_live.py` contains a lot of operationally useful supervision:

- Price staleness detection.
- Market stream restart.
- Emergency shutdown.
- Periodic order reconciliation.
- Reconnect position reconciliation.
- Heartbeat file writing.
- Status logging.
- Placement-stuck tripwire.
- Market-closed window handling.

The result is a system that can be monitored and restarted externally, which is important for unattended paper operation.

### 5. Strategy scope is correctly narrowed

The current configuration defaults to XAUUSD ORB only, with Darvas disabled. This is good. The project has accumulated several strategy experiments, but only ORB has enough supporting evidence and operational hardening to justify paper/live focus.

## Main Risks

### Risk 1: `run_live.py` is too large and owns too much

`run_live.py` is now a large orchestration file responsible for startup, connection management, price polling, bar routing, status logging, reconciliation, staleness detection, heartbeat output, emergency shutdown, and daily reset logic.

This is understandable historically, but it is becoming a god object.

Risk:

- Harder to reason about control flow.
- Harder to test individual live supervision behaviors.
- More likely that future fixes will interact unexpectedly.

Recommendation after the next stable paper period:

- Extract `OrderReconciler`.
- Extract `PositionReconciler`.
- Extract `HeartbeatWriter`.
- Extract `PriceStalenessMonitor`.
- Extract `StatusReporter`.
- Keep `run_live.py` as a thin composition/root loop.

Do not do this immediately if it risks destabilizing paper trading. Refactor only after the current path is stable.

### Risk 2: Private-field coupling is high

Several parts of the system reach into private fields of other objects. Examples include internal executor state, adapter state, connection contracts, and failure flags.

Risk:

- Hidden coupling.
- Future refactors can silently break live behavior.
- Tests may pass while integration behavior changes unexpectedly.

Recommended public APIs:

- `IBKRConnection.get_contract(pair_name)`
- `ORBAdapter.set_llm_context(daily_bars, hourly_bars)`
- `IBKRExecutionEngine.mark_entry_failed(reason)`
- `IBKRExecutionEngine.placement_failure_count`
- `IBKRExecutionEngine.has_active_protection()`
- `IBKRExecutionEngine.get_broker_position()`

This would keep live supervision code from mutating private executor state directly.

### Risk 3: `RiskManager` is still internal-state based

`RiskManager` is useful as a gate, but its position state is driven by callbacks rather than broker truth.

Risk:

- If fills/callbacks are missed, risk state can diverge.
- It may think an instrument is flat when the broker has a position, or vice versa.

Some reconciliation already exists in `run_live.py`, but I would prefer the risk manager to have an explicit broker-sync path or at least an invariant check.

Recommended additions:

- `RiskManager.sync_from_broker_positions(...)`
- `RiskManager.assert_consistent_with_broker(...)`
- Tests for broker flat/internal position and broker position/internal flat cases.

### Risk 4: Non-ORB strategy execution is less hardened

The V6 ORB execution path has received most of the recent incident-driven hardening. The generic `TradeManager` path for Darvas/4H is less battle-tested.

Risk:

- Re-enabling Darvas/4H may reintroduce order lifecycle or orphan-position failure modes.

Recommendation:

- Keep Darvas/4H disabled.
- Before enabling them, bring `TradeManager` to the same safety level as ORB executor:
  - order status event handling
  - broker-truth close logic
  - naked-position invariant
  - reconnect graduated recovery
  - reconciliation tests

### Risk 5: Tests contain time and asyncio fragility

Current failing tests are time-dependent. The suite also emits asyncio teardown warnings under Python 3.14 / `pytest-asyncio` / `nest_asyncio`.

Risk:

- False red builds.
- Future pytest versions may turn warnings into failures.
- Operators may lose trust in tests if failures are environmental.

Recommended fixes:

- Inject clocks into time-dependent logic.
- Avoid closing event loops inside async tests.
- Centralize Python 3.14 event loop compatibility in a shared test fixture.

## Priority Recommendations

### P0 — Fix deterministic test failures

Fix the two staleness tests so they do not depend on the current wall-clock market window.

Suggested approach:

- Add optional `now_utc` parameter to `_check_price_staleness()`.
- Test market-open escalation at 300s and 600s.
- Test market-closed non-escalation separately.

### P0 — Preserve current live scope

Do not add GBPUSD intraday momentum, Darvas, 4H retest, XAGUSD, or trend-following into live mode right now.

Current live scope should remain:

- XAUUSD ORB only.
- Paper/dry-run until stable.
- No extra instruments until operational behavior is proven.

### P1 — Run controlled paper fault tests

Before relying on unattended paper trading, run explicit fault drills:

- Kill Gateway while flat.
- Kill Gateway while entry orders are resting.
- Kill Gateway immediately after entry fill.
- Kill Gateway while position is open for less than 60 seconds.
- Kill Gateway while position is open for more than 60 seconds.
- Manually cancel SL or TP in TWS paper and confirm naked-position invariant flattens.
- Simulate missing entry order and confirm order reconciliation surfaces failure.

Expected outcomes:

- Flat state remains flat.
- Resting entries recover or are cleared safely.
- Short outage with range info can re-arm.
- Long outage or missing range info flattens.
- Naked position never remains unprotected beyond the grace window.

### P1 — Add public methods for state mutation

Replace direct private mutation from `run_live.py` with explicit executor/adapter methods.

Important case:

- Current order reconciler directly sets `_entry_failed` and `_entry_failure_reason`.
- Better: `exec_.mark_entry_failed(reason)`.

This reduces coupling and documents the intended contract.

### P1 — Strengthen heartbeat-based watchdog

The heartbeat already includes useful fields:

- `connected`
- `persistent_failure`
- `broker_pos`
- `internal_pos`
- `has_sl`
- `has_tp`

A watchdog should treat the following as critical:

- Heartbeat stale for more than 10 minutes.
- Broker position exists and `has_sl` is false.
- Broker position exists and strategy reports `in_trade=false`.
- Internal position exists and broker position is flat for more than one reconciliation cycle.
- Connected is false for longer than allowed threshold.

### P2 — Refactor live supervision after stability

Once paper trading is stable, split `run_live.py` into focused services.

Recommended modules:

- `v11/live/order_reconciler.py`
- `v11/live/position_reconciler.py`
- `v11/live/heartbeat_writer.py`
- `v11/live/price_staleness_monitor.py`
- `v11/live/status_reporter.py`

The objective is not aesthetic cleanup. The objective is to make safety behavior independently testable.

### P2 — Clarify production strategy status in docs

The codebase still contains many strategies and research paths. That can create confusion.

Recommended docs update:

- Current production/paper target: XAUUSD ORB only.
- Darvas/4H: disabled due to failed revalidation and insufficient OOS edge.
- XAGUSD ORB: rejected due to weak edge and slippage sensitivity.
- FX trend following: rejected in daily form.
- GBPUSD intraday momentum: research-only, not live-ready.

## Strategy-Level Opinion

### XAUUSD ORB

This remains the only strategy that deserves current live/paper focus. It has:

- A validated research basis.
- A working V6 implementation.
- A V11 adapter.
- Broker execution hardening.
- LLM gate integration.
- Paper incident fixes.

Main remaining risk is operational, not strategic.

### Darvas / 4H Retest

Keep disabled. Even if the code exists, the edge is not currently strong enough, and the generic execution path is not as battle-tested as ORB.

### GBPUSD Intraday Momentum

Interesting research direction, but not production-ready. It should remain in backtest/research until tested across:

- more years
- multiple GBP regimes
- spread/slippage sensitivity
- walk-forward validation
- session-specific behavior
- broker execution assumptions

### XAUUSD Daily Trend Following

Promising but likely correlated with gold beta and ORB directionality. It may be useful later, but it should not be added before ORB paper operations are stable.

## Final Assessment

The project has crossed from “strategy research code with live plumbing” into “early operational trading system.” That transition explains the current complexity. The next phase should not be strategy expansion. It should be proving that the live system behaves correctly under broker failure, data failure, restart, and reconciliation stress.

Final recommendation:

1. Fix the two deterministic test failures.
2. Keep live scope to XAUUSD ORB only.
3. Run controlled IBKR/Gateway fault drills.
4. Add explicit public APIs where `run_live.py` currently mutates private fields.
5. Refactor live supervision only after a stable paper-trading period.

The current code is not perfect, but the safety direction is correct. The most valuable work now is to keep converting every operational concern into a tested invariant.

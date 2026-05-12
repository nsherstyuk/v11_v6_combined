# 2026-05-11 — V11 ORB No-Fill + Tomorrow Readiness Review

## Purpose

This is a read-only review/handoff for a coding agent after the 2026-05-11 XAUUSD ORB paper no-fill incident.

Nick's request: review the codebase and recent status/journals thoroughly, identify whether the old stop-trigger bug caused today's failure, verify restart/reconnect/relogin and position-management paths, and provide direct coding-agent instructions without broad unrelated changes.

## Read First

1. `CLAUDE.md`
2. `docs/PROJECT_STATUS.md`, especially the current state and 2026-05-11 Phase 6 section
3. `docs/journal/2026-05-11_orb_no_fill_bug_and_code_review.md`
4. `docs/journal/2026-04-24_stp_trigger_method.md`
5. `docs/journal/2026-05-11_daily_restart_v11_pgrp_kill.md`
6. This review

## Hard Constraints

Do not:

- Touch `.env`.
- Touch `~/ibc/config.ini` credentials.
- Use live port `4001`.
- Trigger real-money trading.
- Re-enable Darvas / 4H Level Retest / other instruments.
- Refactor broadly.
- Remove or weaken restart/reconnect/relogin/supervision features.
- Modify `v11` production code except for the specific issues below, with Nick's explicit approval.

## Current Incident Summary

Today's run proved that most of the V11 ORB state machine worked:

- Range computed: `4648.21–4705.56`.
- LLM was disabled / passthrough approved.
- Entry OCA pair was placed at 07:42 EDT.
- Strategy transitioned `IDLE → RANGE_READY → ORDERS_PLACED`.
- Price crossed the BUY stop around 09:06 EDT and stayed far above it for ~2.5h.
- BUY stop never filled.
- 4h pending guard cancelled the resting entries per design.

This is the same symptom as the 2026-04-24 paper-XAU no-fill incident, but the key nuance is:

> The April 24 `triggerMethod=7` fix was applied and unit-tested, but it was not end-to-end validated against a real paper XAUUSD bid/ask-only trigger before today.

## Current Code State

As of this review, the repo already contains May 11 changes to:

- `v11/v6_orb/ibkr_executor.py`
- `v11/tests/test_order_observability.py`
- `docs/PROJECT_STATUS.md`

Current `ibkr_executor.py` now does three relevant things:

1. Records `buy_entry_id` / `sell_entry_id` immediately after `placeOrder`, before `ib.sleep(1)`.
2. Logs post-placement `ib.openTrades()` diagnostics for the contract.
3. Changes entry orders from `STP` to `STP LMT`, retaining `triggerMethod=7`, with `lmtPrice = auxPrice ± max(0.5, 0.1 × range_width)`.

Targeted tests run during this review:

```bash
.venv/bin/python -m pytest \
  v11/tests/test_order_observability.py \
  v11/tests/test_orb_entry_fill_clears_ids.py \
  v11/tests/test_naked_position_invariant.py \
  v11/tests/test_reconcile_after_reconnect.py \
  v11/tests/test_close_at_market_broker_truth.py \
  -q
```

Result:

- `35 passed`

Important caveat:

- These are mocked/unit-level tests.
- They verify order attributes and internal safety behavior.
- They still do **not** prove that IBKR paper Gateway will actually trigger/fill XAUUSD stops in production.

## High-Level Assessment

The May 11 `STP → STP LMT` change is a plausible experiment, but it is not a proven root-cause fix.

Why:

- It still depends on the stop trigger firing.
- If the stop trigger does not fire, `STP LMT` will not help.
- If it does trigger but price gaps beyond the limit buffer, the order can become a live unfilled limit order instead of filling.

Therefore tomorrow's first job is **not more patching**. It is to verify what IBKR actually accepted and did.

The larger review also found two more important architecture/safety issues that should be addressed deliberately:

1. ORB executor/context likely keep stale `ib` references after reconnect.
2. Safety-flatten fill reasons are not fully handled by ORB adapter / strategy / risk manager.

These are higher-value structural fixes than continuing to patch the no-fill symptom.

---

# P0 — Tomorrow Morning Readiness Checklist

Before the 2026-05-12 trade window, verify these manually.

## 1. Confirm V11 restarted and loaded the new code

The old PID from 2026-05-11 held the old module in memory. The new code only takes effect after process restart.

Expected tomorrow placement logs must include:

- `Entry stops placed: BUY id=... @ ... lmt=...`
- `SELL id=... @ ... lmt=...`
- `DIAG: openTrades after placement:`
- orders should show `STP LMT`
- `trigger=7`
- `tif=DAY`
- correct OCA group
- active-ish status: preferably `Submitted`, tolerable `PreSubmitted` initially if it transitions

If the log still says only `Entry stops placed: BUY id=... @ ...` without `lmt=...` and no `DIAG`, the old code is still running.

## 2. Confirm daily restart survives launchd process-group cleanup

`docs/journal/2026-05-11_daily_restart_v11_pgrp_kill.md` says `AbandonProcessGroup=true` was added after v11 was killed by launchd on job exit.

Tomorrow morning, verify:

- `~/.daily_restart.log` shows success.
- v11 process is still alive after the script exits.
- v11 reached range calculation after 06:00 UTC.
- no immediate `Signal 15 received — shutting down` after launch.

If v11 dies again immediately after daily restart, stop strategy work and fix launchd lifecycle first.

## 3. Do not apply another order-type change before seeing the diagnostic

Tomorrow's first placement should answer:

- Did IBKR receive `triggerMethod=7`?
- Did IBKR receive `STP LMT`?
- Did both OCA orders become active?
- Did the staged `transmit=False` BUY become transmitted when SELL `transmit=True` was placed?
- Did status events appear now that IDs are recorded before `ib.sleep(1)`?

Do not change to market-if-touched, manual crossing logic, bracketOrder, or live account testing before reading this evidence.

---

# P0 Finding 1 — ORB holds stale `ib` references after reconnect

## Severity

High.

## Why this matters

`IBKRConnection.connect()` creates a new `IB()` object on reconnect:

- `self.ib = IB()`
- reconnect re-qualifies contracts and restarts streams on the new `self.ib`

But `ORBAdapter` and `IBKRExecutionEngine` are constructed once with the old `conn.ib` object:

- `ORBAdapter._ib`
- `LiveMarketContext.ib`
- `IBKRExecutionEngine.ib`

I did not find any code that rebinds these ORB references after reconnect.

That means after an intraday reconnect:

- `run_live`/`IBKRConnection` may be connected on the new `IB()` object.
- ORB execution may still call `check_fills()`, `openTrades()`, `positions()`, `placeOrder()`, `cancelOrder()`, and `close_at_market()` on the old disconnected object.
- `reconcile_after_reconnect()` may return `error` because it checks the stale executor `ib`, not the new connection.
- If a position exists, post-reconnect ORB position management may not be trustworthy.

This is exactly the kind of root issue that causes repeated patching if not fixed directly.

## Direct instruction to coding agent

Add an explicit reconnect rebind path for ORB.

Recommended design:

1. Add a method on `ORBAdapter`, for example:

```python
def rebind_ib(self, ib, contract) -> None:
    self._ib = ib
    self._contract = contract
    self._context.ib = ib
    self._context.contract = contract
    self._execution.ib = ib
    self._execution.contract = contract
```

2. Add a method on `MultiStrategyRunner`, for example:

```python
def rebind_orb_connections(self) -> None:
    for engine in self.engines:
        if hasattr(engine, "rebind_ib"):
            contract = self._conn._contracts.get(engine.pair_name)
            engine.rebind_ib(self._conn.ib, contract)
```

3. In `V11LiveTrader.run()`, after `ensure_connected()` succeeds and before `_reconcile_positions()`, call the runner rebind when `not was_connected and self.conn.connected`.

4. Add tests proving:

- ORB adapter/executor/context initially hold `old_ib`.
- After simulated reconnect, runner rebinds them to `new_ib`.
- `_reconcile_positions()` calls ORB `reconcile_after_reconnect()` on the new `ib`, not stale old `ib`.

## Guardrails

- Do not rewrite connection management.
- Do not remove existing `force_disconnect`, `persistent_failure`, `last_outage_s`, restart stream, or launchd restart behavior.
- Keep this a narrow pointer-rebind fix.

---

# P0 Finding 2 — Safety-flatten fills are not fully handled by ORBAdapter / RiskManager

## Severity

High for state correctness after a position opens.

## What I found

`IBKRExecutionEngine` emits fills with these safety reasons:

- `ORPHAN_FLATTEN`
- `NAKED_FLATTEN`

Examples:

- `close_at_market()` can emit `ORPHAN_FLATTEN` when broker has a position but internal executor state says flat.
- `reconcile_after_reconnect()` can emit `ORPHAN_FLATTEN` when it flattens an orphan after long outage / missing range.
- `_check_naked_position_invariant()` emits `NAKED_FLATTEN` after force-flattening an unprotected position.

But `ORBAdapter._on_fill()` only treats these as exits:

```python
("SL", "TP", "BE", "MARKET", "CLOSED")
```

It does **not** include:

```python
("ORPHAN_FLATTEN", "NAKED_FLATTEN")
```

`ORBStrategy.on_fill()` also only handles:

```python
("SL", "TP", "MARKET", "BE")
```

It ignores `CLOSED`, `ORPHAN_FLATTEN`, and `NAKED_FLATTEN`.

## Failure mode

After a protective flatten:

- Broker may be flat.
- Executor may reset to flat.
- But ORB strategy can remain `IN_TRADE`.
- RiskManager can still believe XAUUSD is in a trade.
- Future entries can be blocked incorrectly.
- Status/heartbeat can become internally inconsistent.

This does not necessarily leave broker risk after the flatten succeeds, but it corrupts software state right after the safety feature fires.

## Direct instruction to coding agent

Fix this in `ORBAdapter`, not frozen V6 strategy code.

Recommended behavior:

1. Define an explicit ORB adapter exit reason set:

```python
EXIT_REASONS = {
    "SL", "TP", "BE", "MARKET", "CLOSED",
    "ORPHAN_FLATTEN", "NAKED_FLATTEN",
}
```

2. In `_on_fill()`, for exit reasons:

- ensure the strategy is moved to `DONE_TODAY` even if V6 strategy ignored the reason
- clear RiskManager position via `record_trade_exit`
- compute PnL only if strategy has valid `direction` and `entry_price`; otherwise record `0.0` and log a warning that PnL is unknown due to orphan/safety flatten

3. Add tests in `v11/tests/test_orb_adapter.py`:

- `NAKED_FLATTEN` clears RiskManager position and sets strategy `DONE_TODAY`
- `ORPHAN_FLATTEN` clears RiskManager position and sets strategy `DONE_TODAY`
- `CLOSED` also sets strategy `DONE_TODAY` if currently ignored by V6
- unknown/invalid entry basis does not create huge bogus PnL from `entry_price=0`

## Guardrails

- Do not modify `v11/v6_orb/orb_strategy.py` unless Nick explicitly approves touching frozen V6 code.
- Do not weaken naked-position invariant.
- Do not remove `ORPHAN_FLATTEN` / `NAKED_FLATTEN`; make the adapter understand them.

---

# P0 Finding 3 — Emergency shutdown can cancel protection before ORB-specific closure path

## Severity

Medium-high; high if emergency shutdown happens while ORB has a broker position and stale internal state.

## What I found

`V11LiveTrader._emergency_shutdown()` does this:

1. `self.conn.cancel_all_orders()`
2. if disconnected, attempts final reconnect and closes only legacy `TradeManager` positions from `runner.feeds`
3. writes emergency state
4. calls `_cleanup()`
5. disconnects and exits

`_cleanup()` then calls `ORBAdapter.cleanup()`, which closes ORB only if `self._execution.has_position()` is true.

This is okay when ORB executor internal state is correct.

But if executor internal state is flat while broker has a position, `_cleanup()` will not call `close_at_market()` because `has_position()` is false. This is exactly the scenario `close_at_market()` itself knows how to fix, but `cleanup()` does not call it unless `has_position()` is true.

Also, the global `cancel_all_orders()` before ORB-specific closure could remove SL/TP protection before a close attempt, increasing the importance of a reliable ORB close path.

## Direct instruction to coding agent

Make emergency shutdown explicitly ORB-aware.

Recommended narrow fix:

1. Add an `emergency_close(reason: str)` method to `ORBAdapter`.

It should:

- call `self._execution.close_at_market()` unconditionally, or after a broker-position check inside the executor
- not rely only on `has_position()`
- log that ORB emergency close was attempted

2. In `V11LiveTrader._emergency_shutdown()`, before or during cleanup, iterate ORB engines and call `emergency_close()` if present.

3. Add tests proving:

- emergency shutdown calls ORB emergency close
- ORB emergency close invokes executor broker-truth-aware close path even when internal `_position == 0`
- existing non-ORB TradeManager emergency behavior remains unchanged

## Guardrails

- Do not remove global `cancel_all_orders()` unless a separate reviewed plan replaces it.
- Do not remove `ORBAdapter.cleanup()` behavior.
- Do not change restart/reconnect/relogin architecture.

---

# P1 Finding 4 — Entry order transmit/OCA behavior is still not proven

## Severity

Medium-high for tomorrow's no-fill path.

## What I found

Entry order placement uses two opposite-direction OCA entries:

- BUY `transmit=False`
- SELL `transmit=True`
- same `ocaGroup`

The intention is that transmitting the SELL transmits both OCA siblings.

This is a common pattern, but tomorrow's diagnostic should verify it empirically because today's no-fill could also be explained by one leg not becoming truly active.

## Direct instruction to coding agent

Do not change this before tomorrow's diagnostic unless Nick explicitly says to.

Tomorrow, inspect the `DIAG: openTrades after placement` block:

- both BUY and SELL should appear
- both should have active statuses
- both should show expected `transmit`/OCA attributes as IBKR sees them
- if BUY does not appear or is not active, then the transmit/OCA staging assumption is wrong for this order shape

If the staged BUY is not active, consider a targeted alternative:

- place both entries with `transmit=True` and same OCA group, if IBKR accepts that safely
- or use IB API OCA group mechanics without staged transmit dependency

But do this only after seeing diagnostic evidence.

---

# P1 Finding 5 — `STP LMT` may avoid one problem but introduces another

## Severity

Medium.

## Details

The May 11 change creates a fill envelope:

```python
lmt_buffer = max(0.5, 0.1 * range_width)
buy_lmt = high + lmt_buffer
sell_lmt = low - lmt_buffer
```

For today's range, BUY would have been:

- stop `4705.56`
- limit `4711.30`

But the first observed status-line after crossing was `4714.80`, already above the limit. If IBKR triggered only around that observed price, a BUY `STP LMT` might trigger but sit unfilled as a limit below market.

This does not prove the buffer is wrong because the actual crossing tick may have occurred inside the buffer, but it is a real risk.

## Direct instruction to coding agent

Tomorrow, if order triggers but does not fill:

- Check whether status becomes a live limit order after trigger.
- Check whether market immediately moved beyond `lmtPrice`.
- Do not assume `STP LMT` fixed the issue if a limit rests unfilled above breakout.

Possible next options, only after evidence:

1. Increase buffer substantially.
2. Return to `STP` if diagnostics show trigger method was not the issue.
3. Use software-triggered market order only after a separate high-stakes review, because that changes slippage and reliability semantics.

---

# P1 Finding 6 — Order lifecycle observability improved but should be verified live

## Current improvement

The code now records entry IDs before `ib.sleep(1)`, so `orderStatusEvent` can identify early order status transitions.

## Tomorrow expected logs

After placement, expect some combination of:

- `orderStatusEvent: id=... status=PendingSubmit`
- `PreSubmitted`
- `Submitted`
- later `Filled` or `Cancelled`

If there are still no status events, but `DIAG openTrades` shows orders active, the status listener may still not be receiving events reliably. The 60-second order reconciler remains the backup.

## Direct instruction to coding agent

If no orderStatusEvent lines appear tomorrow:

- do not immediately change trading logic
- first add a broader diagnostic listener temporarily logging all orderStatusEvent order IDs/actions for this client
- confirm whether events are not firing or are being filtered out

---

# P2 Finding 7 — Cosmetic/status issues are not blockers

These are not tomorrow blockers:

- Misleading `Velocity 0 >= 168` log when velocity filter is disabled.
- `bars=0` status for V6 ORB.
- Duplicate `Cancelled` status events at cancel time.

Do not spend tomorrow morning on these unless they block diagnosis.

---

# Suggested Coding-Agent Execution Plan

## Phase 0 — No code changes before tomorrow placement if v11 already has May 11 fixes

1. Confirm process restart loaded current code.
2. Confirm daily restart worked and v11 survived.
3. Observe first placement diagnostics.
4. Record exact `DIAG`, `orderStatusEvent`, and price-crossing behavior.

## Phase 1 — Fix structural reconnect/position-state issues

After tomorrow's immediate readiness check, implement:

1. ORB rebind to new `conn.ib` after reconnect.
2. ORBAdapter handling for `ORPHAN_FLATTEN`, `NAKED_FLATTEN`, and `CLOSED` as exit reasons.
3. ORB-aware emergency shutdown path.

These are root-level safety fixes, not symptom patches.

## Phase 2 — Only then revisit entry order type if still no fill

Use tomorrow's diagnostic to decide whether the remaining no-fill issue is:

- trigger method ignored/stripped
- OCA/transmit staging issue
- STP LMT limit envelope issue
- contract/data-farm/paper-Gateway behavior
- something else

Do not keep changing order type blindly.

---

# Files Most Likely to Change

Only with Nick approval:

- `v11/live/orb_adapter.py`
- `v11/live/multi_strategy_runner.py`
- `v11/live/run_live.py`
- `v11/tests/test_orb_adapter.py`
- new or existing reconnect/emergency tests under `v11/tests/`

Possibly:

- `v11/v6_orb/ibkr_executor.py` only if tomorrow's diagnostics prove order attributes/trigger behavior still need changes

Avoid:

- strategy math
- risk limits
- live instruments
- EURUSD strategies
- root `main.py` stock agent
- launchd/daily-restart/health-check changes unless today's specific ops issue recurs

---

# Bottom Line

Today's no-fill is not enough evidence to keep patching order placement blindly. The May 11 code now has a diagnostic path and one order-type experiment (`STP LMT`), but tomorrow's logs must validate it.

The bigger codebase concerns are:

1. ORB execution objects likely become stale after reconnect because they keep the old `ib` object.
2. Safety-flatten fill reasons are not fully propagated to strategy/risk-manager state.
3. Emergency shutdown should explicitly close ORB using broker-truth-aware logic, not rely only on generic order cancellation and internal `has_position()`.

Fix those deliberately. They are more important for tomorrow-and-beyond safety than another tactical order patch.

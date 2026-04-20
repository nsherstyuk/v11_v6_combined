# Ghost-Order Investigation — Root Cause and Fixes

**Date:** 2026-04-20
**Severity:** Critical — zero trades executable despite system "running normally"
**Status:** Diagnosed, fixed, tested, committed

---

## The incident

V11 started Sunday 23:34 ET. By Monday morning it was in state `ORDERS_PLACED` with both entry brackets supposedly live. From 10:15 ET onwards, XAUUSD traded above the BUY stop at 4814.60 multiple times (peaking at 4827.69 — $13 above entry) **without any fill**. User noticed at ~10:55 ET. When V11 was killed, its cleanup log showed IBKR errors "Order was discarded" and "Cancel attempted when order is not in a cancellable state."

## What made this hard to diagnose

- `placeOrder()` returned `Trade` objects with `orderId=72`/`73` — V11 logged "Entry stops placed" success
- No exception thrown, no error log in V11's side
- `disconnectedEvent` never fired — the connection was fine at the TCP/IB layer
- Market data ticks kept arriving (price updates worked)
- V11's state machine stayed internally consistent
- Only IBKR's own side knew the orders had been silently discarded

## The diagnostic sequence (what worked)

1. **`check_orders.py`** — connected as secondary client (id=99), called `reqAllOpenOrders()`. Showed only order 73 (SELL STP) still PreSubmitted; order 72 (BUY STP) was gone. This was the first concrete evidence of V11/IBKR state divergence.

2. **`cancel_order.py`** — tried to cancel order 73. IBKR returned `Error 10147: OrderId 73 not found`. The order had been cleaned up by IBKR between our query and the cancel attempt.

3. **`test_paper_fill.py`** — placed a single marketable LIMIT BUY on XAUUSD, hooked `orderStatusEvent`, watched all transitions live. **This revealed the smoking gun**:

   ```
   orderStatusEvent: id=78 status=Cancelled filled=0.0/0.0
   ERROR reqId=78 code=10349 msg=Order TIF was set to DAY based on order preset.
   orderStatusEvent: id=78 status=Submitted filled=0.0/1.0
   orderStatusEvent: id=78 status=Filled filled=1.0/0.0 avgPx=4800.88
   ```

   IBKR paper-account preset **rejects TIF=GTD** with Error 10349 and silently Cancels the order. For simple LIMIT/MARKET orders, IBKR auto-resubmits with DAY. For **OCA-grouped STP orders** (what V11 places), this auto-rescue doesn't apply — the orders just stay Cancelled.

## Root cause

V11's V6 executor placed orders with:
```python
buy_entry = Order(
    action="BUY", orderType="STP", ...,
    tif="GTD", goodTillDate=gtd_time,
    ocaGroup=oca_group, ocaType=1,
    ...
)
```

Three compounding factors:
1. **TIF=GTD was rejected** by IBKR paper preset (Error 10349)
2. **OCA grouping** prevented the silent auto-DAY-rescue that worked for non-OCA orders
3. **No `orderStatusEvent` listener** in V11 — so the Cancellation was invisible

Any ONE of these three in isolation would have been recoverable. All three together = silent trade-day loss.

## The fixes (all committed, all tested)

### Fix 1 — TIF=DAY, no GTD

`v11/v6_orb/ibkr_executor.py`: orders now placed with `tif="DAY"` and no `goodTillDate`. This matches what IBKR's preset forces anyway. Orders die at EOD naturally; `max_pending_hours` handles earlier cancellation. Test: `test_orders_use_tif_day_not_gtd`.

### Fix 2 — orderStatusEvent listener

New methods on `IBKRExecutionEngine`:
- `_hook_status_event_once()` — attaches `ib.orderStatusEvent` to `_on_order_status` (idempotent)
- `_on_order_status(trade)` — logs every transition; when an entry order hits terminal non-Filled state (Cancelled/Inactive/ApiCancelled) before any Fill, flips `_entry_failed` flag
- `entry_placement_failed() -> bool` — strategy polls this
- `entry_failure_reason() -> str` — human-readable reason for logs
- `clear_entry_failure()` — reset after strategy handles the failure

Tests: hook attached on placement, hook idempotent (no duplicate callbacks), Cancelled/Inactive both flip flag, Filled does not flip, unrelated orders ignored, clear resets state.

### Fix 3 — Strategy fallback on async failure

`v11/v6_orb/orb_strategy.py`: in `_handle_range_and_orders`, when state is `ORDERS_PLACED` and `execution.entry_placement_failed()` returns True, the strategy:
- Cancels the brackets (mostly a no-op if they're already dead)
- Clears the executor's failure flag
- Resets `orders_placed_time`
- Transitions state back to `RANGE_READY`

This allows retry on the next tick, or graceful transition to `DONE_TODAY` if the window has closed. Tests: fallback fires on failure, does not fire when no failure.

### Fix 4 — Periodic reconciliation

`v11/live/run_live.py`: every 60s the main loop calls `_reconcile_orders()` which:
1. Fetches `self.conn.ib.openTrades()` (all client IDs)
2. Filters to `Submitted / PreSubmitted / PendingSubmit / ApiPending`
3. For each engine, compares expected `(buy_entry_id, sell_entry_id)` to IBKR's open-order set
4. If V11 expects orders IBKR doesn't have: logs ERROR, flips the executor's failure flag, zeros the missing IDs

This is belt-and-suspenders to catch cases where `orderStatusEvent` might have missed a transition (e.g. event-loop starved, handler not yet attached when the cancel fired, etc.).

## Testing

- 10 new unit tests in `v11/tests/test_order_observability.py`
- Full suite: **474 pass, 0 regressions** (was 464)
- `test_paper_fill.py` — reusable end-to-end diagnostic for future IBKR-contract investigations

## What to do next session

1. **Restart V11** with the fixes in place: `v11\live\start_v11.bat --live --no-llm`
2. Watch the first hour of the log specifically for:
   - **`orderStatusEvent:` lines** — these should appear whenever an order transitions (previously they were invisible)
   - **`ORDER RECONCILE:`** lines — should only appear if something's genuinely wrong
   - **No `Error 10349`** — if TIF is still getting rejected somehow, we'll see it immediately now
3. When a range breaks today (Tuesday), we should see order state go through `PendingSubmit → PreSubmitted → Submitted → Filled` with each transition logged. If any of those steps don't happen, the new reconciliation pass catches it within 60s.

## Files changed

| File | Lines changed | Change |
|---|---|---|
| `v11/v6_orb/ibkr_executor.py` | +80 | TIF=DAY, orderStatusEvent listener, failure flag API |
| `v11/v6_orb/orb_strategy.py` | +20 | Async-failure fallback to RANGE_READY |
| `v11/live/run_live.py` | +60 | `_reconcile_orders()` + 60s timer in main loop |
| `v11/tests/test_order_observability.py` | +263 (new) | 10 regression tests |
| `v11/live/check_orders.py` | +90 (new) | IBKR order-state diagnostic |
| `v11/live/cancel_order.py` | +55 (new) | Out-of-band order cancellation |
| `v11/live/test_paper_fill.py` | +140 (new) | End-to-end fill diagnostic |

Commit: `97f698e`

## Meta note

Today's debugging was productive because we:
1. Stopped trusting V11's logs (which said everything was fine)
2. Queried IBKR directly as an outside observer
3. Built a reproducible end-to-end test that exposed the exact failure mode

Going forward: **when V11 and reality might have diverged, query IBKR directly rather than re-reading V11's own logs.** `check_orders.py` is the tool for that. `test_paper_fill.py` is the tool for validating IBKR-side behavior on any instrument before trusting it for live deployment.

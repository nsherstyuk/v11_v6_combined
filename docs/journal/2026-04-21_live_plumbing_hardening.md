# 2026-04-21 — Live Plumbing Hardening (disconnect / reconcile / invariant)

**Status:** Complete — all approved scope landed, 499/499 tests green
**Author:** Claude Opus 4.7 (assisted)
**Trigger incident:** 2026-04-21 XAUUSD orphaned-short incident (see §1)

---

## 1. Incident recap

Paper session `v11_live_20260421_073610.log`:

| Time (UTC) | Event |
|---|---|
| 08:20:04 | ORB SHORT filled @ 4783.14, SL=4830.99, TP=4663.86. Healthy. |
| 08:20:30 | `ORDER RECONCILE: MISSING=[85] ... likely discarded by IBKR`. **False alarm** — order 85 was the parent MKT, already filled. Reconciler compared expected IDs against `openTrades()` filtered to active statuses only; a filled parent isn't there, so it was declared missing. `_entry_failed=True`, IDs zeroed. |
| 09:36:30 | IBKR disconnect. |
| 09:36:32 | `Position vanished without detected SL/TP fill` → `ORB EXIT: CLOSED @ 4783.14 PnL=$+0.00`. **Second false alarm** — `ib.positions()` returns `[]` when socket is dead. Logic treated "can't see position" as "position is gone" and emitted a $0 exit. |
| 09:37:12 | Reconnect. `Cancelled 1 open orders for XAUUSD` (a surviving OCA leg), then `RECONCILE internal=flat but broker has position=-1.0`. Reconcile handler **adopted the orphan into the risk manager but did not re-arm SL/TP**. Position now naked. |
| 09:37:12 → 12:00:00 | `in_trade=False state=IN_TRADE` zombie — strategy frozen. |
| 12:00:00 → 16:59 | `Trade window closed, closing at market / No position to close` spam every 2s. `close_at_market` checks internal `_position==0` only; never asks broker. |
| 16:59:24 | Manual flatten via `v11/live/flatten_xauusd.py` — BUY LMT @ 4711.92. **Realized ~+$67** on the orphan (lucky; gold happened to trend down during the outage). |

Four compounding bugs. Net-positive outcome was chance, not design.

## 2. Scope (approved 2026-04-21)

| # | Change | Risk |
|---|---|---|
| P0.1 | Reconciler drops filled/cancelled IDs from `expected` | low |
| P0.2 | `_check_position_vanished` gated on `isConnected()` | low |
| P0.3 | Reconnect: graduated recovery — re-arm from memory if <60s & range_info present, else flatten | **high (center)** |
| P1.4 | `close_at_market` consults broker position, not internal flag | medium |
| P2.6 | Per-tick invariant: `broker_pos≠0 ∧ (¬SL ∨ ¬TP) ∧ age>15s ⇒ flatten` | **high (new invariant)** |
| P2.7 | Heartbeat includes `has_sl/has_tp/broker_pos/internal_pos` | low |
| P3.9 | Generalize `flatten_xauusd.py` → `flatten.py <instrument>` | low |
| P3.10 | `stop_v11.bat --flatten` opt-in | low |

**Boundary decisions locked in with operator:**
- Q1 SL source on reconnect: **graduated** — in-memory re-arm if disconnect <60s & `range_info` still present; otherwise **flatten** (do not synthesize SL from ATR).
- Q2 Grace window for naked-position invariant: **15s** (bump to 20s if paper shows false-positive).

## 3. What this WILL and WILL NOT verify

**Will verify today:**
- Unit tests per fix.
- Existing `pytest v11/tests` suite remains green.

**Will NOT verify today (surfaced per operating-principles §8.5):**
- Real Gateway disconnect mid-trade — no scriptable fake-IB harness exists yet. Planned for Thursday before first real trading day.
- Invariant false-positive window in live timing (only observable under real API latency).

## 4. Running log

### P0.1 — reconciler
`run_live.py::_reconcile_orders` now filters terminal statuses (Filled/Cancelled/
ApiCancelled/Inactive) and silently clears those IDs from the executor before
computing the `missing` set. Prevents the 08:20:30 false alarm where a filled
parent MKT was flagged as discarded. Tests: `test_orb_entry_fill_clears_ids.py` (2).

### P0.2 — vanished gated on isConnected
`ibkr_executor._check_position_vanished` now checks `ib.isConnected()` at entry
AND after the double-check sleep. A dead socket returns `False` → no spurious
$0 CLOSED fill. Tests: `test_position_vanished_disconnect_gate.py` (4).

### P0.3 — reconnect graduated recovery
New `ibkr_executor.reconcile_after_reconnect(outage_s) -> str` with branches
`flat | ok | rearm | flatten | error`. Re-arms SL/TP from in-memory `range_info`
when outage <60s; otherwise market-flattens and emits an `ORPHAN_FLATTEN` fill.
`ibkr_connection.last_outage_s` property captures the duration.
`run_live.py::_reconcile_positions` now iterates `self.runner.engines` and calls
the new method per ORB engine — replacing the blanket `cancel_orders_for(pair)`
that wiped the surviving SL leg on 2026-04-21. Non-ORB feeds keep the legacy path.
Re-arm failure (e.g. `_place_sl_tp` raises) falls back to flatten — never leaves
a naked position. Tests: `test_reconcile_after_reconnect.py` (9).

### P1.4 — close_at_market broker-truthful
`ibkr_executor.close_at_market` now consults `_broker_position_throttled()` (30s)
when internal `_position == 0`. If broker holds a position it market-flattens and
emits an `ORPHAN_FLATTEN` fill. Kills the 12:00–17:00 "No position to close" spam
scenario. Tests: `test_close_at_market_broker_truth.py` (4).

### P2.6 — naked-position invariant
Per-tick backstop in `ibkr_executor.check_fills → _check_naked_position_invariant`.
If `pos != 0 ∧ age > grace_s (15s) ∧ (sl_id ∉ active ∨ tp_id ∉ active)` it
force-flattens via `_cancel_and_close` and emits a `NAKED_FLATTEN` fill. Throttled
to 5s, gated on `isConnected`. Tests: `test_naked_position_invariant.py` (6).

### P2.7 — heartbeat extension
`run_live.py::_write_heartbeat` now enriches each strategy entry with
`has_sl`, `has_tp` (membership check of SL/TP order IDs in the active `openTrades`
set), `broker_pos` (from `ib.positions()`), and `internal_pos` (executor
`_position`). External monitor can alert on `in_trade=true ∧ has_sl=false`.

### P3.9 — flatten script generalization
`v11/live/flatten.py` with `INSTRUMENTS` dict for XAUUSD/EURUSD/USDJPY and a
tick-scaled marketable-LIMIT helper. `flatten_xauusd.py` removed.

### P3.10 — stop_v11 --flatten
`v11/live/stop_v11.bat --flatten` iterates the known live instruments and calls
`python -m v11.live.flatten <inst>` before tearing down Gateway. Default behavior
(no flag) unchanged.

### Final test run
`python -m pytest v11/tests` → **499 passed, 38 warnings** (warnings are
pre-existing asyncio/nest_asyncio noise from the 3.14 stack, not from this work).

---

## 5. Handoff

**Cannot verify in this session (per operating-principles §8.5):**
- Real Gateway disconnect mid-trade against a live Gateway — no fake-IB harness.
  Unit mocks cover the branches but not the real socket timing.
- Naked-invariant false-positive rate under real IBKR order-status latency. Grace
  is 15s; may need bumping to 20s if paper surfaces false positives.
- `stop_v11.bat --flatten` path end-to-end (requires a live Gateway + open position
  to exercise). Dry-logic inspected.

**Ready to test on next paper session:**
- Normal ORB entry/exit regression.
- Watch heartbeat.json for new `has_sl/has_tp/broker_pos/internal_pos` fields.
- Controlled Gateway kill mid-trade: expect `reconcile[XAUUSD] → rearm` on short
  kill, `→ flatten` on long kill (>60s).

**Files touched:**
- `v11/v6_orb/ibkr_executor.py` (P0.1, P0.2, P0.3, P1.4, P2.6)
- `v11/execution/ibkr_connection.py` (P0.3 outage capture)
- `v11/live/run_live.py` (P0.1 reconciler, P0.3 wiring, P2.7 heartbeat)
- `v11/live/flatten.py` (new, P3.9)
- `v11/live/stop_v11.bat` (P3.10 flag)
- `v11/tests/test_orb_entry_fill_clears_ids.py` (new, P0.1)
- `v11/tests/test_position_vanished_disconnect_gate.py` (new, P0.2)
- `v11/tests/test_reconcile_after_reconnect.py` (new, P0.3)
- `v11/tests/test_close_at_market_broker_truth.py` (new, P1.4)
- `v11/tests/test_naked_position_invariant.py` (new, P2.6)

**Removed:** `v11/live/flatten_xauusd.py` (replaced by P3.9).

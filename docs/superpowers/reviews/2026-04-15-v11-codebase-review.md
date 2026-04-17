# V11 Codebase Review — 2026-04-15

**Reviewer:** Cascade | **Scope:** V11 live trading codebase — auto-reconnect, GatewayManager, trade execution, risk, LLM, tick logging
**Verified:** 2026-04-15 21:56 ET — All findings re-verified against code. Corrections noted inline.

---

## Critical Issues (Fix Before Live Money)

### C1. Failing Test — `test_sl_double_failure_closes_position` — CONFIRMED
`v11/tests/test_trade_manager.py:142` — `ValueError: not enough values to unpack (expected 3, got 0)`. The `mock_conn` fixture uses plain `MagicMock()`, which returns another `MagicMock` from `submit_bracket_order()` — when unpacked to 3 variables, MagicMock yields 0 items. The test was written before bracket orders became the primary entry path. **Fix:** Add `mock_conn.submit_bracket_order.return_value = (None, None, None)` so the fallback two-step path is exercised.

### C2. `emergency_close()` Doesn't Log Trade or Notify Risk Manager — CONFIRMED
`v11/execution/trade_manager.py:493-553` — Calls `_reset_trade_state()` directly, bypassing `_execute_exit()`. No `TradeRecord`, no CSV log, no `on_trade_closed` callback, no `daily_trades`/`daily_pnl` update, no risk manager notification. After emergency shutdown, risk manager still thinks position is open.

### C3. `_reconcile_positions()` Accesses `risk_manager._positions` (private) — CONFIRMED
`v11/live/run_live.py:732,752` — Directly reads private dict. Also accesses `self.conn.ib.positions()` at line 716 (another encapsulation break). Add public `get_open_instruments() -> set[str]` to RiskManager, and `get_all_positions()` to IBKRConnection.

### C4. `submit_bracket_order` Implicit `None` Return — DOWNGRADED to non-issue
~~`v11/execution/ibkr_connection.py:324-401` — If an uncaught exception escapes, Python returns bare `None`.~~
**Verification result:** All code paths explicitly return `(None, None, None)`. The `try/except Exception` at line 399 catches everything inside the `try` block. The only code outside the try is `from ib_insync import Order` (line 335) — if ib_insync is not installed the entire system fails anyway. **No fix needed.**

### C5. `ib.sleep(2)` Called in `_check_price_staleness()` — NEW FINDING
`v11/live/run_live.py:690` — `self.conn.ib.sleep(2)` calls the raw ib_insync sleep, which uses `asyncio.ensure_future()`. On Python 3.14 (which this project runs on), this raises `ValueError` when called from inside `loop.run_until_complete()`. The rest of the codebase correctly uses `self.conn.sleep()` (which is `time.sleep()`). **Fix:** Change `self.conn.ib.sleep(2)` to `self.conn.sleep(2)` on line 690.

---

## Important Issues (Fix Soon)

| ID | Issue | File | Fix |
|----|-------|------|-----|
| I1 | **Dual disconnect timer** — `_first_disconnect_time` in IBKRConnection AND `_disconnect_start` in V11LiveTrader track same concept. V11_DESIGN.md §13.5 lists both as separate params (lines 960-961) suggesting this is intentional, but they can drift if `_on_disconnect()` fires before the main loop detects it. The IBKRConnection timer controls reconnect-attempt logging; the main loop timer triggers emergency shutdown. | `ibkr_connection.py:52`, `run_live.py:174` | Remove `_disconnect_start`, use `conn.persistent_failure` as single source of truth for the 5-min timeout. Keep reconnect attempt logging in IBKRConnection. |
| I2 | **`from zoneinfo import ZoneInfo` inside main loop** — executed every ~1s iteration | `run_live.py:433` | Move to top-level imports |
| I3 | **`_check_price_staleness()` accesses `conn._contracts`** — violates encapsulation | `run_live.py:679-694` | Add `restart_price_stream(pair)` method to IBKRConnection |
| I4 | **Tick logger accesses `conn._tickers`** — same encapsulation issue | `run_live.py:453` | Add `get_ticker(pair)` method to IBKRConnection |
| I5 | **`config_has_credentials` passes for demo defaults** — Verified: `GatewayManager(config_path='C:\IBC\config.ini').config_has_credentials` returns `True` for the shipped `edemo`/`demouser` defaults | `gateway_manager.py:92-110` | Explicitly reject `edemo`/`demouser`. Also consider checking `TradingMode` matches expectation. |
| I6 | **`_kill_stale_gateways` uses deprecated `Get-WmiObject`** | `gateway_manager.py:285` | Use `Get-CimInstance` |
| I7 | **Lock file race condition** — check-then-write is not atomic. Verified: `OpenProcess(0x1000, False, dead_pid)` returns a valid handle (non-zero) for recently terminated PIDs if any other object holds a reference — tested locally, handle=448 for a dead `cmd /c exit` process. This causes false "already running" detection. | `gateway_manager.py:424-439` | Use `GetExitCodeProcess(handle, byref(code))` and check `code == 259` (STILL_ACTIVE), or use `msvcrt.locking`-based file lock. |

---

## Design Observations

| ID | Observation | Recommendation |
|----|-------------|----------------|
| D1 | **Two PnL systems diverge** — `engine_pnl` and `ibkr_pnl` computed but CSV only logs `ibkr_pnl` for both columns | Log both as separate CSV columns |
| D2 | **No position SIZE verification on reconnect** — `reconcile_position()` checks existence but not quantity | Compare `broker_pos` vs `self._inst.quantity` |
| D3 | **`reset_daily()` doesn't clear `_positions`** — This is **correct by design**: open positions should persist across session boundaries. However, if a position closed during a disconnect AND `_reconcile_positions()` failed to clean up, the stale entry persists. The two-level reconciliation in `run_live.py:747-753` handles this case (checks `rm_positions - broker_instruments`), but only runs on reconnect, not on daily reset. | Consider a periodic (not just on-reconnect) cross-check of `_positions` vs broker state |
| D4 | **`start_v11.bat` `timeout` behavior** — `timeout /t` in batch files responds to any keypress (including Ctrl+C), which can prematurely end the wait. A user accidentally pressing a key during the 120s Gateway startup wait (line 27) could cause V11 to start before Gateway is ready. | Use `ping -n 121 127.0.0.1 > nul` or `powershell Start-Sleep 120` for uninterruptible waits, or add a Gateway health re-check after the wait |
| D5 | **LLM feedback loop small sample** — 30 assessed decisions, 48% Darvas accuracy; Sharpe improvement could be overfitting | Run replay from Nov 2025 for more ledger data |
| D6 | **LiveConfig comment mismatches** — (1) `max_daily_loss` (line 104) says "per instrument" but RiskManager uses it as combined portfolio limit. (2) `max_daily_trades` (line 103) says "per instrument" but is passed to RiskManager as `max_daily_trades_per_strategy` which enforces per-strategy (not per-instrument). | Fix both comments to match actual semantics |

---

## Test Coverage Gaps

| Area | Tests | Gap |
|------|-------|-----|
| Emergency shutdown | 0 | No mock for 5-min disconnect flow |
| Price staleness | 0 | No mock for stale ticker → stream restart |
| Orphaned position auto-close | 0 | No mock for broker position mismatch |
| Risk manager broker sync | 0 | No mock for `_reconcile_positions()` |
| GatewayManager | 0 | No unit tests at all |
| Bracket order fallback | 1 (FAILING) | `test_sl_double_failure_closes_position` broken — MagicMock returns empty iterable |
| 5 PM ET session reset | 0 | Timezone logic untested |

| `ib.sleep()` in staleness check | 0 | Py3.14 crash risk untested |

---

## Positive Observations

1. **Three-layer defense** (GatewayManager → IBKRConnection → V11LiveTrader) — clear separation, each layer independent
2. **Bracket order primary path** with two-step fallback — eliminates naked position window
3. **Force-close on SL failure** — critical safety many systems miss
4. **Regime-filtered feedback loop** — innovative, Sharpe 0.90 → 1.77
5. **Emergency state file** — enables post-mortem after crash-and-restart
6. **Tick logging design** — line-buffered, one file/day/instrument, gzip support
7. **Python 3.14 compat patch** — thorough and well-documented

---

## Priority Action Items

| Priority | Item | Effort |
|----------|------|--------|
| **P0** | Fix failing test — add `mock_conn.submit_bracket_order.return_value = (None, None, None)` (C1) | 5 min |
| **P0** | Fix `ib.sleep()` → `conn.sleep()` in `_check_price_staleness` line 690 (C5) | 1 min |
| **P0** | Fix `emergency_close()` to log trade + notify risk mgr (C2) | 1 hr |
| **P1** | Add public methods to IBKRConnection for ticker/contract/positions (I3+I4+C3) | 30 min |
| **P1** | Unify disconnect timer, use `persistent_failure` (I1) | 30 min |
| **P1** | Move zoneinfo import to top level (I2) | 2 min |
| **P1** | Add `get_open_instruments()` to RiskManager (C3) | 15 min |
| **P2** | Add Phase B unit tests | 3-4 hr |
| **P2** | Fix GatewayManager demo credential check (I5) | 15 min |
| **P2** | Fix lock file race condition — use `GetExitCodeProcess` (I7) | 30 min |
| **P2** | Log both engine_pnl and ibkr_pnl in CSV (D1) | 15 min |
| **P3** | Position size verification in reconcile (D2) | 15 min |
| **P3** | Fix LiveConfig comment mismatches (D6) | 5 min |
| ~~P0~~ | ~~C4: submit_bracket_order implicit None~~ | ~~Withdrawn — all paths return tuple~~ |

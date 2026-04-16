# Session — 2026-04-15: Code Review Fixes

**Source:** `docs/superpowers/reviews/2026-04-15-v11-codebase-review.md`
**Handoff:** `docs/journal/2026-04-15_review_fixes_handoff.md`

**Baseline:** 371 passed, 1 failed
**Result:** 374 passed, 0 failed (+3 new tests)

## Fix Group 1 — P0 Critical

### 1.1 Failing test — mock_conn unpacking (C1)
- **File:** `v11/tests/test_trade_manager.py`
- **Fix:** Added `submit_bracket_order.return_value = (None, None, None)` and `submit_sl_tp_oca.return_value = (None, None)` to `mock_conn` fixture
- MagicMock yields 0 items on unpack → ValueError. Return tuples make the bracket "fail", exercising the fallback two-step path.

### 1.2 Python 3.14 crash in price staleness check (C5)
- **File:** `v11/live/run_live.py`
- **Fix:** `self.conn.ib.sleep(2)` → `self.conn.sleep(2)` (line 690)
- `ib.sleep()` uses `asyncio.ensure_future()` which raises ValueError on Python 3.14. `conn.sleep()` is safe `time.sleep()`.

### 1.3 emergency_close() missing trade logging and callback (C2)
- **File:** `v11/execution/trade_manager.py`
- **Fix:** Added TradeRecord creation, `_log_trade_csv()`, `daily_trades` increment, and `on_trade_closed` callback before `_reset_trade_state()` in `emergency_close()`.
- `exit_reason="EMERGENCY"`, `pnl=0.0` (unknown — post-mortem must reconcile).
- **New tests:** `TestEmergencyClose::test_emergency_close_logs_trade`, `TestEmergencyClose::test_emergency_close_fires_callback`

## Fix Group 2 — P1 Important

### 2.1 Add public methods to IBKRConnection (I3+I4+C3)
- **File:** `v11/execution/ibkr_connection.py`
- Added `get_ticker()`, `restart_price_stream()`, `get_broker_positions()`
- **Updated callers in `v11/live/run_live.py`:**
  - `self.conn._tickers.get(pair)` → `self.conn.get_ticker(pair)`
  - Manual cancel/resubscribe block → `self.conn.restart_price_stream(pair)`
  - `self.conn.ib.positions()` → `self.conn.get_broker_positions()`

### 2.2 Add public methods to RiskManager (C3)
- **File:** `v11/live/risk_manager.py`
- Added `get_open_instruments()`, `get_position_strategy()`
- **Updated callers in `v11/live/run_live.py`:**
  - `set(self.risk_manager._positions.keys())` → `self.risk_manager.get_open_instruments()`
  - `self.risk_manager._positions.get(inst, "UNKNOWN")` → `self.risk_manager.get_position_strategy(inst)`

### 2.3 Unify disconnect timer (I1)
- **File:** `v11/live/run_live.py`
- Removed `_disconnect_start` field and `MAX_DISCONNECT_SECONDS` class constant
- Replaced manual timer logic with `self.conn.persistent_failure` check
- Reconnection duration logging already handled by `IBKRConnection.ensure_connected()`

### 2.4 Move zoneinfo import to top level (I2)
- **File:** `v11/live/run_live.py`
- Moved `from zoneinfo import ZoneInfo` to top-level imports
- Removed inline `from zoneinfo import ZoneInfo` on former line 433

## Fix Group 3 — P2 Moderate

### 3.1 GatewayManager demo credential check (I5)
- **File:** `v11/live/gateway_manager.py`
- Rewrote `config_has_credentials` to extract actual login_id/password values
- Rejects `edemo`/`demo` usernames and `demouser`/`demo` passwords

### 3.2 Fix lock file — use GetExitCodeProcess (I7)
- **File:** `v11/live/gateway_manager.py`
- Replaced `OpenProcess`-only check with `GetExitCodeProcess` to distinguish STILL_ACTIVE (259) from exited processes
- Stale lock files are now auto-removed instead of blocking startup

### 3.3 Replace deprecated Get-WmiObject (I6)
- **File:** `v11/live/gateway_manager.py`
- `Get-WmiObject Win32_Process` → `Get-CimInstance Win32_Process`

### 3.4 Log both engine_pnl and ibkr_pnl in trade CSV (D1)
- **Files:** `v11/core/types.py`, `v11/execution/trade_manager.py`
- Added `engine_pnl` and `ibkr_pnl` optional fields to `TradeRecord`
- Added `engine_pnl` to `TRADE_CSV_FIELDS` (after `pnl`, before `ibkr_pnl`)
- `_execute_exit()` now stores both values separately
- `_log_trade_csv()` writes both columns independently (was duplicating `pnl` into both)

## Fix Group 4 — P3 Low

### 4.1 Position size verification in reconcile (D2)
- **File:** `v11/execution/trade_manager.py`
- Added `elif self.in_trade and broker_has_pos` branch in `reconcile_position()` that warns on size mismatch between broker position and expected quantity

### 4.2 Fix LiveConfig comments (D6)
- **File:** `v11/config/live_config.py`
- `max_daily_trades`: `# per instrument` → `# per strategy per day`
- `max_daily_loss`: `# USD, per instrument` → `# USD, combined portfolio limit`

## Files Modified

| File | Fixes |
|------|-------|
| `v11/tests/test_trade_manager.py` | 1.1, 1.3 |
| `v11/live/run_live.py` | 1.2, 2.1, 2.3, 2.4 |
| `v11/execution/trade_manager.py` | 1.3, 3.4, 4.1 |
| `v11/execution/ibkr_connection.py` | 2.1 |
| `v11/live/risk_manager.py` | 2.2 |
| `v11/live/gateway_manager.py` | 3.1, 3.2, 3.3 |
| `v11/config/live_config.py` | 4.2 |
| `v11/core/types.py` | 3.4 |

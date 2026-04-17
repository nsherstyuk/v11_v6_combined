# Session Handoff — 2026-04-15: Code Review Fixes

**Context:** A full codebase review was performed and verified. All findings are documented in `docs/superpowers/reviews/2026-04-15-v11-codebase-review.md`. This handoff provides step-by-step fix instructions for a coding agent.

**Baseline:** 371 passed, 1 failed. Target: 372+ passed, 0 failed.

**Python:** 3.14.3 | **OS:** Windows | **Test command:** `python -m pytest v11/tests/ --tb=short -q`

---

## Ground Rules

1. Read `docs/superpowers/reviews/2026-04-15-v11-codebase-review.md` first.
2. Run the full test suite before AND after each fix group.
3. Do NOT modify test intent — only fix test infrastructure (stubs, mocks).
4. `trade_manager.py` is a CENTER MODULE — changes require extra care.
5. Do NOT add or remove comments unless the fix specifically requires it.
6. Each fix group below is independent — commit after each group passes tests.

---

## Fix Group 1 — P0 Critical (do these first)

### Fix 1.1: Failing test — C1 (5 min)

**File:** `v11/tests/test_trade_manager.py`
**Line:** ~126-146 (class `TestSLFailureForceClose`)

**Problem:** `mock_conn.submit_bracket_order()` returns a `MagicMock` object (not a 3-tuple). When `enter_trade()` unpacks it (`a, b, c = conn.submit_bracket_order(...)`), MagicMock yields 0 items → `ValueError`.

**Fix:** In the test method `test_sl_double_failure_closes_position`, after creating `mock_conn` (or inside the test before calling `enter_trade`), add:

```python
mock_conn.submit_bracket_order.return_value = (None, None, None)
```

This makes the bracket order "fail", causing `enter_trade()` to fall through to the two-step path (which is what this test is actually testing — SL failure on the fallback path).

**Also update** the `mock_conn` fixture at line ~62 to include this by default, so ALL non-dry-run tests that hit the bracket path don't break:

```python
@pytest.fixture
def mock_conn():
    conn = MagicMock()
    conn.connected = True
    conn.get_position_size = MagicMock(return_value=0.0)
    conn.has_position = MagicMock(return_value=False)
    conn.submit_bracket_order = MagicMock(return_value=(None, None, None))
    return conn
```

**Verify:** `python -m pytest v11/tests/test_trade_manager.py::TestSLFailureForceClose -v`

---

### Fix 1.2: Python 3.14 crash in price staleness check — C5 (1 min)

**File:** `v11/live/run_live.py`
**Line:** 690

**Problem:** `self.conn.ib.sleep(2)` calls raw ib_insync sleep which uses `asyncio.ensure_future()`. On Python 3.14 this raises `ValueError` inside `loop.run_until_complete()`. The rest of the codebase uses `self.conn.sleep()` which is safe `time.sleep()`.

**Fix:** Change line 690 from:
```python
self.conn.ib.sleep(2)
```
to:
```python
self.conn.sleep(2)
```

**Verify:** `python -m pytest v11/tests/ --tb=short -q` (no test specifically covers this, but ensure nothing breaks)

---

### Fix 1.3: `emergency_close()` missing trade logging and risk manager notification — C2 (1 hr)

**File:** `v11/execution/trade_manager.py`
**Lines:** 493-553

**Problem:** `emergency_close()` calls `_reset_trade_state()` directly. This skips:
- Creating a `TradeRecord`
- CSV logging via `_log_trade_csv()`
- `on_trade_closed` callback (auto-assessment)
- `daily_trades` / `daily_pnl` counter updates

**Fix approach:** Add trade record creation and logging BEFORE `_reset_trade_state()`. Do NOT try to call `_execute_exit()` because emergency_close doesn't have bar context. Instead, manually create a minimal TradeRecord and log it.

At the end of `emergency_close()`, just before `self._reset_trade_state()` (line 553), insert:

```python
        # Log the emergency close as a trade record
        try:
            from ..core.types import TradeRecord, ExitReason
            record = TradeRecord(
                entry_time=datetime.now(timezone.utc),
                exit_time=datetime.now(timezone.utc),
                direction=self.direction,
                instrument=self._inst.pair_name,
                entry_price=self.signal_entry_price,
                exit_price=self.signal_entry_price,  # unknown exit price
                stop_price=self.stop_price,
                target_price=self.target_price,
                box_top=self.box_top,
                box_bottom=self.box_bottom,
                exit_reason="EMERGENCY",
                pnl=0.0,  # unknown — post-mortem must reconcile
                hold_bars=0,
                buy_ratio_at_entry=self.buy_ratio,
                llm_confidence=self.llm_confidence,
                llm_reasoning=self.llm_reasoning,
                fill_entry_price=self._fill_entry_price,
                fill_exit_price=0.0,
                entry_commission=self._entry_commission,
                exit_commission=0.0,
                entry_slippage=0.0,
                exit_slippage=0.0,
            )
            self._log_trade_csv(record)
            self.daily_trades += 1
            if self.on_trade_closed:
                try:
                    self.on_trade_closed(record)
                except Exception:
                    pass
        except Exception as e:
            self._log.error(f"Failed to log emergency close: {e}")
```

**Important:** The `TradeRecord` import (`from ..core.types import TradeRecord`) is already at the top of the file (line 24-26). Check if `ExitReason` enum has an `EMERGENCY` value. If not, use a string `"EMERGENCY"` for `exit_reason` — this field is already a string in the CSV writer.

**Also check:** Does `TradeRecord.exit_reason` accept a plain string, or does it require an `ExitReason` enum value? Look at the dataclass definition in `v11/core/types.py`. If it's a string field, use `"EMERGENCY"`. If it's an enum, you'll need to add `EMERGENCY = "EMERGENCY"` to the `ExitReason` enum.

**Test:** Write a new test in `v11/tests/test_trade_manager.py`:

```python
class TestEmergencyClose:
    def test_emergency_close_logs_trade(self, mock_conn, log, tmp_path):
        """Emergency close should create a CSV log entry."""
        mock_conn.submit_bracket_order.return_value = (None, None, None)
        tm = TradeManager(
            conn=mock_conn, inst=EURUSD_INSTRUMENT, log=log,
            trade_log_dir=tmp_path, dry_run=True, max_hold_bars=120,
        )
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        assert tm.in_trade is True
        tm.emergency_close("TEST")
        assert tm.in_trade is False
        # CSV should exist with one row
        csv_file = tmp_path / f"trades_{EURUSD_INSTRUMENT.pair_name.lower()}.csv"
        assert csv_file.exists()

    def test_emergency_close_fires_callback(self, mock_conn, log, tmp_path):
        """Emergency close should fire on_trade_closed callback."""
        mock_conn.submit_bracket_order.return_value = (None, None, None)
        tm = TradeManager(
            conn=mock_conn, inst=EURUSD_INSTRUMENT, log=log,
            trade_log_dir=tmp_path, dry_run=True, max_hold_bars=120,
        )
        records = []
        tm.on_trade_closed = lambda r: records.append(r)
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        tm.emergency_close("TEST")
        assert len(records) == 1
        assert records[0].exit_reason == "EMERGENCY"
```

**Verify:** `python -m pytest v11/tests/test_trade_manager.py -v -k "emergency"`

---

## Fix Group 2 — P1 Important (do after Group 1 passes)

### Fix 2.1: Add public methods to IBKRConnection — I3 + I4 + C3 (30 min)

**File:** `v11/execution/ibkr_connection.py`

Add these public methods (anywhere after the existing public methods, before `_on_disconnect`):

```python
def get_ticker(self, pair_name: str):
    """Get the current Ticker object for an instrument. Returns None if not streaming."""
    return self._tickers.get(pair_name)

def restart_price_stream(self, pair_name: str) -> bool:
    """Cancel and re-subscribe to market data for an instrument.
    Returns True on success."""
    contract = self._contracts.get(pair_name)
    if contract is None:
        self.log.error(f"No contract for {pair_name} — cannot restart stream")
        return False
    try:
        try:
            self.ib.cancelMktData(contract)
        except Exception:
            pass
        ticker = self.ib.reqMktData(
            contract, '', snapshot=False, regulatorySnapshot=False)
        self.sleep(2)
        self._tickers[pair_name] = ticker
        self.log.info(f"Restarted market data stream for {pair_name}")
        return True
    except Exception as e:
        self.log.error(f"Failed to restart stream for {pair_name}: {e}")
        return False

def get_broker_positions(self) -> list:
    """Get all broker positions. Returns empty list on error."""
    try:
        return self.ib.positions()
    except Exception as e:
        self.log.error(f"Failed to query broker positions: {e}")
        return []
```

**Then update callers:**

**File:** `v11/live/run_live.py`

1. **Line 453** (tick logger) — change `self.conn._tickers.get(pair)` to `self.conn.get_ticker(pair)`

2. **Lines 679-694** (`_check_price_staleness`) — replace the manual cancel/resubscribe block:
```python
# OLD (lines 678-694):
try:
    contract = self.conn._contracts.get(pair)
    if contract:
        try:
            self.conn.ib.cancelMktData(contract)
        except Exception:
            pass
        ticker = self.conn.ib.reqMktData(
            contract, '', snapshot=False,
            regulatorySnapshot=False)
        self.conn.ib.sleep(2)
        self.conn._tickers[pair] = ticker
        self.log.info(f"Restarted market data stream for {pair}")
except Exception as e:
    self.log.error(f"Failed to restart stream for {pair}: {e}")
```
```python
# NEW:
self.conn.restart_price_stream(pair)
```

3. **Line 716** — change `self.conn.ib.positions()` to `self.conn.get_broker_positions()`
   Also update the error handling since `get_broker_positions()` returns `[]` on error, not raising:
```python
# OLD:
try:
    broker_positions = self.conn.ib.positions()
except Exception as e:
    self.log.error(f"Failed to query broker positions: {e}")
    return
```
```python
# NEW:
broker_positions = self.conn.get_broker_positions()
```

**Verify:** `python -m pytest v11/tests/ --tb=short -q`

---

### Fix 2.2: Add `get_open_instruments()` to RiskManager — C3 (15 min)

**File:** `v11/live/risk_manager.py`

Add after `is_instrument_in_trade()` method (~line 155):

```python
def get_open_instruments(self) -> set[str]:
    """Return set of instruments that currently have open positions."""
    return set(self._positions.keys())
```

**Then update caller:**

**File:** `v11/live/run_live.py`

1. **Line 732** — change `set(self.risk_manager._positions.keys())` to `self.risk_manager.get_open_instruments()`

2. **Line 752** — change `self.risk_manager._positions.get(inst, "UNKNOWN")` to:
   Need a way to get the strategy name for an instrument. Add to RiskManager:
```python
def get_position_strategy(self, instrument: str) -> str:
    """Return the strategy name holding a position on instrument, or 'UNKNOWN'."""
    return self._positions.get(instrument, "UNKNOWN")
```
   Then change line 752 to: `strategy = self.risk_manager.get_position_strategy(inst)`

**Verify:** `python -m pytest v11/tests/ --tb=short -q`

---

### Fix 2.3: Unify disconnect timer — I1 (30 min)

**File:** `v11/live/run_live.py`

1. Remove `self._disconnect_start: Optional[float] = None` from `__init__` (~line 174)
2. Remove `self.MAX_DISCONNECT_SECONDS = 300` (~line 167 area — check exact location)
3. In the main loop (~lines 389-408), replace the manual timer logic:

```python
# OLD:
if not self.conn.ensure_connected():
    if self._disconnect_start is None:
        self._disconnect_start = time.time()
    elapsed = time.time() - self._disconnect_start
    if elapsed > self.MAX_DISCONNECT_SECONDS:
        ...emergency_shutdown...
    ...
# Reconnected — clear timer
if self._disconnect_start is not None:
    self.log.info(f"Reconnected after {time.time() - self._disconnect_start:.0f}s")
    self._disconnect_start = None
```

```python
# NEW:
if not self.conn.ensure_connected():
    if self.conn.persistent_failure:
        self.log.critical("IBKR persistent failure — EMERGENCY SHUTDOWN")
        self._emergency_shutdown("persistent_ibkr_failure")
        break
    self.log.error("Connection lost — waiting 10s")
    time.sleep(10)
    continue

# Reconnected
if not was_connected and self.conn.connected:
    self._reconcile_positions()
```

**Note:** The reconnection duration logging is already handled by `IBKRConnection.ensure_connected()` (line 128: `self.log.info(f"Reconnecting... (disconnected {elapsed:.0f}s)")`).

**Verify:** `python -m pytest v11/tests/ --tb=short -q`

---

### Fix 2.4: Move zoneinfo import to top level — I2 (2 min)

**File:** `v11/live/run_live.py`

1. Add at the top of the file with other imports (around line 1-15):
```python
from zoneinfo import ZoneInfo
```

2. Remove the inline import on line 433:
```python
from zoneinfo import ZoneInfo  # DELETE THIS LINE
```

3. Keep the usage on line 434 as-is: `et_now = now.astimezone(ZoneInfo("America/New_York"))`

**Verify:** `python -m pytest v11/tests/ --tb=short -q`

---

## Fix Group 3 — P2 Moderate (do after Group 2 passes)

### Fix 3.1: GatewayManager demo credential check — I5 (15 min)

**File:** `v11/live/gateway_manager.py`
**Method:** `config_has_credentials` (property, ~line 92-110)

After the existing check that `IbLoginId=` and `IbPassword=` are non-empty, add:

```python
# Reject IBC demo/default credentials
if login_id.lower() in ('edemo', 'demo') or password.lower() in ('demouser', 'demo'):
    self._log.warning("IBC config has demo credentials — replace with real IBKR credentials")
    return False
```

You'll need to extract the actual login_id and password values from the lines. Read the existing parsing logic to understand how it's done.

**Verify:** Run `python -c "from v11.live.gateway_manager import GatewayManager; gm = GatewayManager(); print(gm.config_has_credentials)"` — should return `False` if config has demo defaults.

---

### Fix 3.2: Fix lock file — use `GetExitCodeProcess` — I7 (30 min)

**File:** `v11/live/gateway_manager.py`
**Lines:** 424-439

Replace the `OpenProcess` check with:

```python
if lock_file.exists():
    try:
        old_pid = int(lock_file.read_text().strip())
        import ctypes
        from ctypes import wintypes, byref
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, old_pid)
        if handle:
            exit_code = wintypes.DWORD()
            kernel32.GetExitCodeProcess(handle, byref(exit_code))
            kernel32.CloseHandle(handle)
            if exit_code.value == 259:  # STILL_ACTIVE
                log.error(
                    f"Another gateway_manager is already running (PID {old_pid}). "
                    f"Delete {lock_file} if this is stale.")
                sys.exit(1)
            else:
                log.info(f"Stale lock file (PID {old_pid} exited with {exit_code.value}), removing")
                lock_file.unlink(missing_ok=True)
        # If OpenProcess returns 0, PID doesn't exist — stale lock
    except (ValueError, OSError):
        pass  # stale lock file, proceed
```

---

### Fix 3.3: Replace deprecated `Get-WmiObject` — I6 (5 min)

**File:** `v11/live/gateway_manager.py`
**Line:** 287

Change `Get-WmiObject Win32_Process` to `Get-CimInstance Win32_Process`.

---

### Fix 3.4: Log both engine_pnl and ibkr_pnl in trade CSV — D1 (15 min)

**File:** `v11/execution/trade_manager.py`

1. Add `'engine_pnl'` to `TRADE_CSV_FIELDS` list (line 31-38), e.g. after `'ibkr_pnl'`.

2. Store `engine_pnl` on the TradeRecord — check if `TradeRecord` has a field for it. If not, add an optional field to `v11/core/types.py`.

3. In `_log_trade_csv()`, add the engine_pnl column.

4. In `_execute_exit()`, compute and store both values.

**Alternative minimal fix:** Just add `engine_pnl` as a new CSV column in `_log_trade_csv()` without changing TradeRecord — compute it inline in the CSV writer from `self.signal_entry_price` and `exit_price`.

---

## Fix Group 4 — P3 Low Priority (do if time allows)

### Fix 4.1: Position size verification in reconcile — D2 (15 min)

**File:** `v11/execution/trade_manager.py`, method `reconcile_position()`

After the existing checks, add a size mismatch warning for the case where both are in-trade:

```python
elif self.in_trade and broker_has_pos:
    expected_qty = self._inst.quantity
    if abs(abs(broker_pos) - expected_qty) > 0.001:
        self._log.warning(
            f"{self._inst.pair_name}: RECONCILE — SIZE MISMATCH: "
            f"broker={broker_pos}, expected={expected_qty}")
```

### Fix 4.2: Fix LiveConfig comments — D6 (5 min)

**File:** `v11/config/live_config.py`

- Line 103: change `# per instrument` to `# per strategy per day`
- Line 104: change `# USD, per instrument` to `# USD, combined portfolio limit`

---

## Verification Checklist

After all fixes:

```
python -m pytest v11/tests/ --tb=short -q
```

Expected: **372+ passed, 0 failed** (was 371 passed, 1 failed)

New tests added:
- `TestEmergencyClose::test_emergency_close_logs_trade`
- `TestEmergencyClose::test_emergency_close_fires_callback`

---

## Files Modified (expected)

| File | Fix Groups |
|------|-----------|
| `v11/tests/test_trade_manager.py` | 1.1, 1.3 |
| `v11/live/run_live.py` | 1.2, 2.1, 2.3, 2.4 |
| `v11/execution/trade_manager.py` | 1.3, 3.4, 4.1 |
| `v11/execution/ibkr_connection.py` | 2.1 |
| `v11/live/risk_manager.py` | 2.2 |
| `v11/live/gateway_manager.py` | 3.1, 3.2, 3.3 |
| `v11/config/live_config.py` | 4.2 |
| `v11/core/types.py` | 1.3 (if ExitReason needs EMERGENCY), 3.4 (if TradeRecord needs engine_pnl) |

---

## Do NOT Touch

- `v11/live/multi_strategy_runner.py` — no fixes needed
- `v11/llm/*` — no fixes needed
- `v11/v6_orb/*` — frozen, never modify
- `docs/V11_DESIGN.md` — update AFTER all code fixes are done, in a separate commit
- `docs/PROJECT_STATUS.md` — update AFTER all code fixes are done

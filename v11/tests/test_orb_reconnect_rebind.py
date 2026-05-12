"""Phase 1 (2026-05-11 remediation): ORB reconnect rebind.

Verified architectural bug in `v11/execution/ibkr_connection.py`:

    def connect(self) -> bool:
        ...
        self.ib = IB()    # ← line 77, reassignment, not mutation

Any object constructed with `conn.ib` keeps a handle to the OLD,
now-disconnected IB instance. ORB owns three such handles (adapter,
context, executor) and a market-data listener on the old ib's
pendingTickersEvent. Without an explicit rebind, post-reconnect
placeOrder / openTrades / positions / fill detection all silently
fail against the stale socket.

`MultiStrategyRunner.rebind_orb_connections()` is the fix, called
from `V11LiveTrader.run()` the moment a reconnect is detected,
before `_reconcile_positions()`.

Tests below use the minimal FakeIB harness from `lifecycle_harness`
to exercise the actual swap end-to-end rather than asserting on
mock call counts.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from v11.live.orb_adapter import ORBAdapter
from v11.live.multi_strategy_runner import MultiStrategyRunner
from v11.live.risk_manager import RiskManager
from v11.v6_orb.config import StrategyConfig as V6StrategyConfig

from v11.tests.lifecycle_harness import FakeIB, FakeIBKRConnection


log = logging.getLogger("test_orb_reconnect_rebind")


def _make_v6_config():
    return V6StrategyConfig(
        instrument="XAUUSD",
        range_start_hour=0,
        range_end_hour=6,
        trade_start_hour=8,
        trade_end_hour=16,
        velocity_filter_enabled=False,
        rr_ratio=2.5,
        min_range_pct=0.05,
        max_range_pct=2.0,
        gap_filter_enabled=False,
        qty=1,
        point_value=1.0,
        price_decimals=2,
    )


def _make_contract(symbol="XAUUSD", conId=12345):
    class _C:
        pass
    c = _C()
    c.symbol = symbol
    c.conId = conId
    c.secType = "CMDTY"
    c.exchange = "SMART"
    c.currency = "USD"
    return c


@pytest.fixture
def fake_ib():
    return FakeIB()


@pytest.fixture
def contract():
    return _make_contract()


@pytest.fixture
def adapter(fake_ib, contract):
    rm = RiskManager(
        max_daily_loss=500.0,
        max_daily_trades_per_strategy=5,
        max_concurrent_positions=3,
        log=log,
    )
    return ORBAdapter(
        ib=fake_ib,
        contract=contract,
        v6_config=_make_v6_config(),
        risk_manager=rm,
        log=log,
        dry_run=False,
        poll_interval=2.0,
    )


# ── 1. Pre-condition: ORB initially holds the original ib ────────────────────

def test_orb_components_initially_share_one_ib(adapter, fake_ib):
    """Sanity check: adapter, context, executor are all wired to the
    same ib instance at construction. If this regresses the rebind
    test below is meaningless."""
    assert adapter._ib is fake_ib
    assert adapter._context.ib is fake_ib
    assert adapter._execution.ib is fake_ib


def test_context_listener_is_hooked_on_construction(adapter, fake_ib):
    """LiveMarketContext._subscribe_ticks runs in __init__ and adds
    a listener to pendingTickersEvent. After construction the FakeIB's
    pendingTickersEvent should have exactly one listener."""
    # Note: count may be >0 due to subscribe_ticks; the contract is
    # "there's at least one and the rebind moves it off this ib".
    assert fake_ib.pendingTickersEvent.listener_count >= 1


# ── 2. Rebind swaps every held reference ─────────────────────────────────────

def test_rebind_swaps_ib_on_all_components(adapter, fake_ib):
    """After rebind_ib, adapter/context/executor all reference the
    new ib instance, not the old one."""
    new_ib = FakeIB()
    new_contract = _make_contract(conId=99999)

    adapter.rebind_ib(new_ib, new_contract)

    assert adapter._ib is new_ib
    assert adapter._context.ib is new_ib
    assert adapter._execution.ib is new_ib

    # And the old ib is no longer referenced
    assert adapter._ib is not fake_ib
    assert adapter._context.ib is not fake_ib
    assert adapter._execution.ib is not fake_ib


def test_rebind_swaps_contract_on_all_components(adapter):
    """Contract handles are also swapped — qualifyContracts on the
    new connection may produce a new Contract object."""
    new_ib = FakeIB()
    new_contract = _make_contract(conId=99999)

    adapter.rebind_ib(new_ib, new_contract)

    assert adapter._contract is new_contract
    assert adapter._context.contract is new_contract
    assert adapter._execution.contract is new_contract


def test_rebind_resubscribes_ticks_on_new_ib(adapter, fake_ib):
    """The context's tick subscription was attached to the old ib.
    After rebind, the new ib must have a pendingTickersEvent listener,
    and the new ticker handle must come from the new ib's reqMktData."""
    new_ib = FakeIB()
    new_contract = _make_contract(conId=99999)
    old_ticker = adapter._context.ticker

    adapter.rebind_ib(new_ib, new_contract)

    # New ib has a listener installed
    assert new_ib.pendingTickersEvent.listener_count >= 1
    # Context ticker is a fresh handle from the new ib's reqMktData
    assert adapter._context.ticker is not old_ticker
    assert adapter._context.ticker.contract is new_contract


def test_rebind_unhooks_old_context_listener(adapter, fake_ib):
    """Best-effort unhook: the old ib's listener count should drop by
    one (the context's listener). Other listeners on the FakeIB are
    not affected."""
    new_ib = FakeIB()
    new_contract = _make_contract(conId=99999)
    old_count = fake_ib.pendingTickersEvent.listener_count

    adapter.rebind_ib(new_ib, new_contract)

    assert (fake_ib.pendingTickersEvent.listener_count
            == old_count - 1)


def test_rebind_resets_executor_status_hook_flag(adapter):
    """The executor's idempotent status-event hook flag must be reset
    so the next set_orb_brackets call re-hooks orderStatusEvent on
    the new ib. Otherwise we'd silently lose order lifecycle visibility
    on every reconnect."""
    # Simulate that the hook was previously set on the old ib
    adapter._execution._hooked_status_event = True

    new_ib = FakeIB()
    new_contract = _make_contract(conId=99999)
    adapter.rebind_ib(new_ib, new_contract)

    assert adapter._execution._hooked_status_event is False


# ── 3. Runner-level rebind plumbing ──────────────────────────────────────────

class _StubLLM:
    """Tiny LLMFilter stand-in — required to satisfy MultiStrategyRunner
    construction. ORB rebind doesn't touch it."""

    async def evaluate_orb_signal(self, *_a, **_kw):
        from v11.llm.models import LLMDecision
        return LLMDecision(approved=True, confidence=99,
                           reasoning="stub", risk_flags=[])

    async def evaluate_signal(self, *_a, **_kw):
        return None

    def record_orb_outcome(self, **_kw):
        pass

    def record_darvas_outcome(self, **_kw):
        pass

    def refresh_feedback(self):
        pass


def _make_runner_with_orb(conn, contract):
    """Build a MultiStrategyRunner with one ORBAdapter for the rebind
    test. Avoids the full add_orb_strategy() path so we don't have to
    construct InstrumentConfig / LiveConfig just to wire engines."""
    from v11.config.live_config import LiveConfig

    live_cfg = LiveConfig(
        instruments=[], dry_run=False,
        orb_confidence_threshold=75, auto_close_orphans=False,
    )
    rm = RiskManager(
        max_daily_loss=500.0,
        max_daily_trades_per_strategy=5,
        max_concurrent_positions=3,
        log=log,
    )
    runner = MultiStrategyRunner(
        conn=conn, llm_filter=_StubLLM(), live_config=live_cfg,
        risk_manager=rm, log=log,
    )

    adapter = ORBAdapter(
        ib=conn.ib,
        contract=contract,
        v6_config=_make_v6_config(),
        risk_manager=rm,
        log=log,
        dry_run=False,
        poll_interval=2.0,
    )
    runner._engines.append(adapter)
    return runner, adapter


def test_runner_rebind_swaps_orb_to_new_ib():
    """End-to-end: simulate IBKRConnection.connect() reassigning self.ib
    (the actual production bug), then call runner.rebind_orb_connections().
    All ORB components must now point at the new ib."""
    contract = _make_contract()
    conn = FakeIBKRConnection(contracts={"XAUUSD": contract})
    runner, adapter = _make_runner_with_orb(conn, contract)
    old_ib = conn.ib
    assert adapter._ib is old_ib

    # Reconnect: conn.ib reassigned (matching production behavior).
    new_ib = conn.simulate_reconnect()
    assert conn.ib is new_ib
    assert conn.ib is not old_ib
    # Adapter still on the stale ib — this is the bug.
    assert adapter._ib is old_ib

    # The fix.
    runner.rebind_orb_connections()
    assert adapter._ib is new_ib
    assert adapter._context.ib is new_ib
    assert adapter._execution.ib is new_ib


def test_runner_rebind_skips_engines_without_rebind_ib():
    """Non-ORB engines (Darvas, LevelRetest) don't expose rebind_ib.
    The runner method must skip them without raising."""
    contract = _make_contract()
    conn = FakeIBKRConnection(contracts={"XAUUSD": contract})
    runner, adapter = _make_runner_with_orb(conn, contract)

    class _NoRebind:
        pair_name = "EURUSD"
    runner._engines.append(_NoRebind())

    conn.simulate_reconnect()
    # Must not raise; ORB engine still gets rebound.
    runner.rebind_orb_connections()
    assert adapter._ib is conn.ib


def test_runner_rebind_warns_on_missing_contract(caplog):
    """If an ORB engine's pair has no qualified contract in the
    connection (configuration error / not yet qualified), skip it
    with a warning rather than crashing the reconnect path."""
    contract = _make_contract()
    conn = FakeIBKRConnection(contracts={})  # contract MISSING from cache
    runner, adapter = _make_runner_with_orb(conn, contract)
    old_ib = conn.ib

    conn.simulate_reconnect()
    with caplog.at_level(logging.WARNING,
                         logger="test_orb_reconnect_rebind"):
        runner.rebind_orb_connections()

    # Adapter NOT rebound (no contract); old ib stays referenced.
    assert adapter._ib is old_ib
    assert any("no qualified contract" in r.message
               for r in caplog.records)

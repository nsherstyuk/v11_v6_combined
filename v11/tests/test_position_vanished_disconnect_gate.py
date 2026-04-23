"""Regression: _check_position_vanished must NOT return True when
ib.isConnected() is False.

2026-04-21 incident: Gateway dropped → ib.positions() returned [] →
executor concluded "position vanished" → emitted ORB EXIT CLOSED @ entry
with $0 PnL → while IBKR still held the real short. Internal state went
flat; broker state was untouched. Orphan unprotected for 7h.

Contract: when disconnected, we don't know the true position state.
"Vanished" must be False. Let reconnect + reconcile path resolve it.
"""
import asyncio
import logging
from unittest.mock import MagicMock

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from v11.v6_orb.ibkr_executor import IBKRExecutionEngine


log = logging.getLogger("test_vanished_gate")


def _mock_contract():
    c = MagicMock()
    c.symbol = "XAUUSD"
    c.conId = 1
    return c


def _executor(ib):
    return IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )


def test_vanished_is_false_when_disconnected():
    """Even though positions() would return [] (empty),
    vanished MUST return False because we can't trust the view."""
    ib = MagicMock()
    ib.isConnected.return_value = False
    ib.positions.return_value = []  # would-be false positive

    ex = _executor(ib)
    assert ex._check_position_vanished() is False
    # Crucially: positions() was not even consulted (short-circuit)
    ib.positions.assert_not_called()


def test_vanished_is_false_when_disconnect_during_doublecheck():
    """Connected initially, positions() empty triggers double-check sleep,
    but then disconnect happens during the sleep. Must return False, not
    treat the post-disconnect empty list as confirmation."""
    ib = MagicMock()
    # Connected on first check, but disconnected by the time double-check runs
    ib.isConnected.side_effect = [True, False]
    ib.positions.return_value = []

    ex = _executor(ib)
    assert ex._check_position_vanished() is False


def test_vanished_is_true_when_connected_and_no_position():
    """Real vanish case: still connected, positions consistently empty."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.positions.return_value = []  # no XAUUSD position

    ex = _executor(ib)
    assert ex._check_position_vanished() is True


def test_vanished_is_false_when_position_present():
    ib = MagicMock()
    ib.isConnected.return_value = True
    pos = MagicMock()
    pos.contract.conId = 1  # matches _mock_contract
    pos.position = -1.0
    ib.positions.return_value = [pos]

    ex = _executor(ib)
    assert ex._check_position_vanished() is False

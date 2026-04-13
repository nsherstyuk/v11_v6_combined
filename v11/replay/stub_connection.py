"""Stub IBKR connection for replay mode.

Satisfies the duck-type interface that TradeManager calls when dry_run=True.
In dry_run mode, TradeManager never actually calls submit_market_order or
submit_stop_order — but we provide them anyway for safety.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class _StubIB:
    """Minimal stub for conn.ib attribute (never called in dry_run)."""

    def cancelOrder(self, order):
        pass


class StubIBKRConnection:
    """No-op IBKR connection for replay/dry-run use.

    TradeManager in dry_run=True mode never touches the connection,
    but it stores self._conn and references self._conn.ib in the
    _execute_exit block guarded by `if not self._dry_run`.
    This stub ensures no AttributeError if something unexpected
    accesses the connection.
    """

    def __init__(self):
        self.ib = _StubIB()

    def submit_market_order(self, pair_name, direction, quantity):
        log.debug(f"StubIBKR: market order {pair_name} {direction} {quantity} (no-op)")
        return None

    def submit_stop_order(self, pair_name, direction, quantity, stop_price, tick_size=0.01):
        log.debug(f"StubIBKR: stop order {pair_name} {stop_price} (no-op)")
        return None

    def close_position(self, pair_name, direction, quantity):
        return None

    def has_position(self, symbol, sec_type):
        return False

    def get_position_size(self, symbol, sec_type):
        return 0.0

    def get_fill_commission(self, trade):
        return 0.0

    def sleep(self, seconds):
        pass  # no-op in replay

    def cancel_all_orders(self):
        pass

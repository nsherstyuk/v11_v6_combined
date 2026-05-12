"""
Minimal fake-IB lifecycle harness for ORB tests (Phase 5a, 2026-05-11
remediation). Used by Phase 1/2/3 tests where MagicMock per-call setup
becomes tedious.

Provides stateful fakes that mimic enough of ib_insync.IB and our own
IBKRConnection for end-to-end ORB lifecycle assertions:

    - place an order → returns Trade with orderId, orderStatus
    - fire orderStatusEvent on every transition
    - simulate fills, cancellations, status changes
    - track openTrades() / trades() / positions() / openOrders()
    - swap to a fresh IB() on reconnect (the Phase 1 root cause)

Deliberately minimal. Phase 5b will expand to the full normal/abnormal
matrix (TP fill, SL fill, naked-flatten, emergency shutdown with broker
orphan, reconnect with in-position, etc.). For now this exists to make
the rebind regression test honest.

NOT a pytest module (no `test_` prefix) — imported by tests.
"""
from __future__ import annotations

import time
from typing import Any, Callable, List, Optional


class FakeEvent:
    """Stand-in for ib_insync.Event supporting +=, -=, emit().

    Listener exceptions are swallowed to match ib_insync's behavior of
    isolating callbacks from each other.
    """

    def __init__(self) -> None:
        self._listeners: List[Callable[..., Any]] = []

    def __iadd__(self, fn: Callable[..., Any]) -> "FakeEvent":
        self._listeners.append(fn)
        return self

    def __isub__(self, fn: Callable[..., Any]) -> "FakeEvent":
        try:
            self._listeners.remove(fn)
        except ValueError:
            pass
        return self

    def emit(self, *args: Any, **kwargs: Any) -> None:
        for fn in list(self._listeners):
            try:
                fn(*args, **kwargs)
            except Exception:
                pass

    @property
    def listener_count(self) -> int:
        return len(self._listeners)


class FakeOrderStatus:
    def __init__(self) -> None:
        self.status: str = "PendingSubmit"
        self.filled: float = 0.0
        self.remaining: float = 0.0
        self.avgFillPrice: float = 0.0


class FakeTrade:
    def __init__(self, contract: Any, order: Any) -> None:
        self.contract = contract
        self.order = order
        self.orderStatus = FakeOrderStatus()
        self.log: list = []


class FakePosition:
    def __init__(self, contract: Any, position: float,
                 avgCost: float = 0.0) -> None:
        self.contract = contract
        self.position = position
        self.avgCost = avgCost


class FakeTicker:
    """Just enough surface for V6 LiveMarketContext._on_ticker_update +
    IBKRConnection.get_mid_price / get_ticker."""

    def __init__(self, contract: Any) -> None:
        self.contract = contract
        self.bid: float = 0.0
        self.ask: float = 0.0
        self.last: float = 0.0
        self.bidSize: int = 0
        self.askSize: int = 0
        self.lastSize: int = 0
        self.close: float = 0.0


class FakeIB:
    """In-memory ib_insync.IB stand-in.

    Test code drives state transitions via the simulate_* helpers; the
    object under test (ORBAdapter / IBKRExecutionEngine / etc.) sees the
    same interface ib_insync exposes.
    """

    def __init__(self, connected: bool = True) -> None:
        self._connected = connected
        self._next_order_id = 1
        self._open_trades: List[FakeTrade] = []
        self._all_trades: List[FakeTrade] = []
        self._positions: List[FakePosition] = []

        # ib_insync events used by ORB code paths under test
        self.orderStatusEvent = FakeEvent()
        self.pendingTickersEvent = FakeEvent()
        self.errorEvent = FakeEvent()
        self.disconnectedEvent = FakeEvent()

    # ── Connection plumbing ──────────────────────────────────────────

    def isConnected(self) -> bool:
        return self._connected

    def disconnect(self) -> None:
        if not self._connected:
            return
        self._connected = False
        self.disconnectedEvent.emit()

    def sleep(self, seconds: float) -> None:  # noqa: ARG002 -- no-op
        return

    def reqCurrentTime(self) -> float:
        return time.time()

    def qualifyContracts(self, *contracts: Any) -> list:
        return list(contracts)

    # ── Order management ─────────────────────────────────────────────

    def placeOrder(self, contract: Any, order: Any) -> FakeTrade:
        if getattr(order, "orderId", 0) == 0:
            order.orderId = self._next_order_id
            self._next_order_id += 1
        trade = FakeTrade(contract, order)
        self._open_trades.append(trade)
        self._all_trades.append(trade)
        # ib_insync emits initial status transitions during the placeOrder
        # roundtrip. We collapse those to a single Submitted emission so
        # the orderStatusEvent listener can observe an early-lifecycle
        # transition.
        trade.orderStatus.status = "Submitted"
        trade.orderStatus.remaining = float(
            getattr(order, "totalQuantity", 0) or 0)
        self.orderStatusEvent.emit(trade)
        return trade

    def cancelOrder(self, order: Any) -> None:
        for tr in list(self._open_trades):
            if tr.order.orderId == order.orderId:
                tr.orderStatus.status = "Cancelled"
                self._open_trades.remove(tr)
                self.orderStatusEvent.emit(tr)
                return

    def openTrades(self) -> List[FakeTrade]:
        return list(self._open_trades)

    def trades(self) -> List[FakeTrade]:
        return list(self._all_trades)

    def openOrders(self) -> list:
        return [t.order for t in self._open_trades]

    def positions(self) -> List[FakePosition]:
        return list(self._positions)

    def fills(self) -> list:
        return []

    # ── Market data ──────────────────────────────────────────────────

    def reqMktData(self, contract: Any, *args: Any,
                   **kwargs: Any) -> FakeTicker:  # noqa: ARG002
        return FakeTicker(contract)

    def cancelMktData(self, contract: Any) -> None:  # noqa: ARG002
        return

    def reqMarketDataType(self, *args: Any, **kwargs: Any) -> None:
        return

    # ── Historical data (no-op, returns empty) ───────────────────────

    def reqHistoricalData(self, *args: Any, **kwargs: Any) -> list:
        return []

    async def reqHistoricalDataAsync(self, *args: Any,
                                     **kwargs: Any) -> list:
        return []

    # ── Test driver: state mutations ─────────────────────────────────

    def simulate_fill(self, order_id: int, fill_price: float,
                      fill_qty: Optional[float] = None) -> None:
        for tr in self._all_trades:
            if tr.order.orderId == order_id:
                qty = fill_qty if fill_qty is not None else float(
                    getattr(tr.order, "totalQuantity", 0) or 0)
                tr.orderStatus.status = "Filled"
                tr.orderStatus.filled = qty
                tr.orderStatus.remaining = 0.0
                tr.orderStatus.avgFillPrice = fill_price
                self.orderStatusEvent.emit(tr)
                return
        raise ValueError(f"Order {order_id} not found")

    def simulate_status(self, order_id: int, status: str) -> None:
        for tr in self._all_trades:
            if tr.order.orderId == order_id:
                tr.orderStatus.status = status
                if status in ("Cancelled", "Inactive", "ApiCancelled"):
                    if tr in self._open_trades:
                        self._open_trades.remove(tr)
                self.orderStatusEvent.emit(tr)
                return
        raise ValueError(f"Order {order_id} not found")

    def add_broker_position(self, contract: Any, qty: float,
                            avgCost: float = 0.0) -> None:
        self._positions.append(FakePosition(contract, qty, avgCost))

    def clear_broker_positions(self) -> None:
        self._positions.clear()


class FakeIBKRConnection:
    """Stand-in for v11.execution.ibkr_connection.IBKRConnection.

    Models the one behavior Phase 1 cares about: `connect()` replaces
    `self.ib` with a brand-new `IB()` instance. Anyone holding the old
    reference is left on a stale, disconnected socket.

    Only the attributes used by MultiStrategyRunner.rebind_orb_connections
    are exposed. Extend as Phase 5b grows.
    """

    def __init__(self, contracts: Optional[dict] = None) -> None:
        self.ib = FakeIB()
        self._contracts: dict = dict(contracts or {})
        self._last_outage_s: float = 0.0

    def simulate_reconnect(self) -> FakeIB:
        """Swap the held ib for a fresh instance, mimicking the
        `self.ib = IB()` reassignment in IBKRConnection.connect()."""
        try:
            self.ib.disconnect()
        except Exception:
            pass
        self.ib = FakeIB()
        return self.ib

    def force_disconnect(self, reason: str = "test") -> None:  # noqa: ARG002
        try:
            self.ib.disconnect()
        except Exception:
            pass

"""
IBKR Connection Manager — Ported from v8 with multi-instrument support.

Handles connection lifecycle, contract qualification, price streaming,
order submission, and position management.

CENTER element: Order submission and position management are center.
Connection/reconnection logic is edge (operational tuning).
"""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from ..config.live_config import InstrumentConfig

_IB_CRITICAL_ERRORS = {
    504: "Not connected",
    502: "Couldn't connect to TWS",
    1100: "Connectivity lost",
    2110: "TWS-server connection broken",
}
_IB_WARNING_CODES = {
    2103, 2104, 2105, 2106, 2107, 2108, 2157, 2158,
    2119, 354, 300, 10168, 10167,
}


class IBKRConnection:
    """IBKR connection manager for V11 live trading.

    Supports multiple instruments via qualify_contract() per instrument.
    """

    # Max time to tolerate a disconnect before declaring persistent failure
    MAX_RECONNECT_DURATION = 300  # 5 minutes

    def __init__(self, host: str, port: int, client_id: int,
                 log: logging.Logger):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.log = log
        self.ib = None
        self._connected = False
        self._last_heartbeat = 0.0
        self._first_disconnect_time: Optional[float] = None  # epoch, None when connected

        # Per-instrument state
        self._contracts = {}        # pair_name -> Contract
        self._tickers = {}          # pair_name -> Ticker

    @property
    def connected(self) -> bool:
        return self.ib is not None and self.ib.isConnected()

    def connect(self) -> bool:
        from ib_insync import IB
        for attempt in range(1, 4):
            try:
                if self.ib is not None:
                    try:
                        self.ib.disconnect()
                    except Exception:
                        pass

                self.ib = IB()
                self.ib.errorEvent += self._on_error
                self.ib.disconnectedEvent += self._on_disconnect
                self.ib.connect(self.host, self.port,
                                clientId=self.client_id, timeout=20)

                if not self.ib.isConnected():
                    raise ConnectionError("isConnected=False after connect")

                self._connected = True
                self._last_heartbeat = time.time()
                self.log.info(f"Connected to IBKR {self.host}:{self.port}")
                return True

            except Exception as e:
                err_type = type(e).__name__
                err_msg = str(e) or repr(e)
                self.log.warning(f"Connect attempt {attempt}/3 failed: {err_type}: {err_msg}")
                if attempt < 3:
                    time.sleep(5 * attempt)

        self.log.error("Failed to connect after 3 attempts")
        return False

    @property
    def persistent_failure(self) -> bool:
        """True if disconnected for longer than MAX_RECONNECT_DURATION."""
        if self._first_disconnect_time is None:
            return False
        return (time.time() - self._first_disconnect_time) > self.MAX_RECONNECT_DURATION

    def ensure_connected(self) -> bool:
        if self.connected:
            now = time.time()
            if now - self._last_heartbeat > 30:
                try:
                    self.ib.reqCurrentTime()
                    self._last_heartbeat = now
                except Exception:
                    self._connected = False
            if self._connected:
                # Clear disconnect timer on successful connection
                self._first_disconnect_time = None
                return True

        # Track when disconnect started
        if self._first_disconnect_time is None:
            self._first_disconnect_time = time.time()

        elapsed = time.time() - self._first_disconnect_time
        if elapsed > self.MAX_RECONNECT_DURATION:
            self.log.critical(
                f"IBKR disconnected for {elapsed:.0f}s "
                f"(>{self.MAX_RECONNECT_DURATION}s) — PERSISTENT FAILURE")
            return False

        self.log.info(f"Reconnecting... (disconnected {elapsed:.0f}s)")
        ok = self.connect()
        if ok:
            # Re-qualify all contracts and restart streams after reconnection
            for pair_name, contract in list(self._contracts.items()):
                try:
                    qualified = self.ib.qualifyContracts(contract)
                    if qualified:
                        self.log.info(f"Re-qualified {pair_name} after reconnect")
                    else:
                        self.log.error(f"Failed to re-qualify {pair_name}")
                except Exception as e:
                    self.log.error(f"Re-qualify {pair_name} failed: {e}")
            for pair_name, ticker in list(self._tickers.items()):
                try:
                    contract = self._contracts.get(pair_name)
                    if contract:
                        new_ticker = self.ib.reqMktData(
                            contract, '', snapshot=False,
                            regulatorySnapshot=False)
                        self.ib.sleep(2)
                        self._tickers[pair_name] = new_ticker
                        self.log.info(f"Restarted stream for {pair_name}")
                except Exception as e:
                    self.log.error(f"Restart stream {pair_name} failed: {e}")
        return ok

    def qualify_contract(self, inst: InstrumentConfig):
        """Qualify and register an IBKR contract for an instrument."""
        from ib_insync import Contract
        contract = Contract(
            symbol=inst.symbol,
            secType=inst.sec_type,
            exchange=inst.exchange,
            currency=inst.currency,
        )
        qualified = self.ib.qualifyContracts(contract)
        if qualified:
            self._contracts[inst.pair_name] = contract
            self.log.info(f"Contract qualified: {inst.pair_name}")
        else:
            self.log.error(f"Failed to qualify {inst.pair_name}")

    def start_price_stream(self, inst: InstrumentConfig):
        """Start streaming market data for an instrument."""
        contract = self._contracts.get(inst.pair_name)
        if contract is None:
            self.log.error(f"No contract for {inst.pair_name} — qualify first")
            return
        self.ib.reqMarketDataType(inst.market_data_type)
        ticker = self.ib.reqMktData(
            contract, '', snapshot=False, regulatorySnapshot=False)
        self.ib.sleep(2)
        self._tickers[inst.pair_name] = ticker
        self.log.info(f"Price stream started: {inst.pair_name}")

    def get_mid_price(self, pair_name: str) -> Optional[float]:
        """Get current mid price for an instrument."""
        ticker = self._tickers.get(pair_name)
        if ticker is None:
            return None
        bid, ask = ticker.bid, ticker.ask
        if (isinstance(bid, float) and not math.isnan(bid) and bid > 0
                and isinstance(ask, float) and not math.isnan(ask) and ask > 0):
            return (bid + ask) / 2
        if isinstance(ticker.close, float) and not math.isnan(ticker.close):
            return ticker.close
        return None

    def fetch_historical_bars(self, pair_name: str,
                              duration: str = "1 D",
                              bar_size: str = "1 min") -> pd.DataFrame:
        """Fetch historical bars for an instrument."""
        contract = self._contracts.get(pair_name)
        if not self.ensure_connected() or contract is None:
            return pd.DataFrame()
        try:
            bars = self.ib.reqHistoricalData(
                contract, endDateTime="",
                durationStr=duration, barSizeSetting=bar_size,
                whatToShow="MIDPOINT", useRTH=False, formatDate=2)
        except Exception as e:
            self.log.error(f"Historical data request failed ({pair_name}): {e}")
            return pd.DataFrame()
        if not bars:
            self.log.warning(f"No historical bars returned for {pair_name}")
            return pd.DataFrame()
        from ib_insync import util
        df = util.df(bars)
        self.log.info(f"Fetched {len(df)} historical bars for {pair_name}")
        return df

    def submit_market_order(self, pair_name: str, direction: str,
                            quantity: float):
        """Submit a market order. CENTER: real money at stake."""
        from ib_insync import MarketOrder
        contract = self._contracts.get(pair_name)
        if contract is None:
            self.log.error(f"No contract for {pair_name}")
            return None
        action = "BUY" if direction == "long" else "SELL"
        order = MarketOrder(action, quantity)
        trade = self.ib.placeOrder(contract, order)
        self.log.info(f"ORDER SUBMITTED: {pair_name} {action} {quantity} @ MARKET")
        self.ib.sleep(3)
        status = trade.orderStatus.status
        if status not in ('Filled', 'Submitted', 'PreSubmitted'):
            self.log.error(f"ORDER FAILED: {pair_name} {action} {quantity} "
                           f"status={status}")
            return None
        self.log.info(f"ORDER CONFIRMED: {pair_name} {action} {quantity} "
                      f"status={status}")
        return trade

    def submit_stop_order(self, pair_name: str, direction: str,
                          quantity: float, stop_price: float,
                          tick_size: float = 0.01):
        """Submit a stop-loss order. CENTER: protects against runaway loss."""
        from ib_insync import StopOrder
        contract = self._contracts.get(pair_name)
        if contract is None:
            self.log.error(f"No contract for {pair_name}")
            return None
        action = "SELL" if direction == "long" else "BUY"
        # Round to tick size
        if tick_size > 0:
            stop_price = round(round(stop_price / tick_size) * tick_size, 10)
        order = StopOrder(action, quantity, stop_price)
        trade = self.ib.placeOrder(contract, order)
        self.log.info(f"SL ORDER: {pair_name} {action} {quantity} @ {stop_price}")
        self.ib.sleep(3)
        status = trade.orderStatus.status
        if status not in ('Filled', 'Submitted', 'PreSubmitted'):
            self.log.error(f"SL ORDER FAILED: {pair_name} {action} {quantity} "
                           f"@ {stop_price} status={status}")
            return None
        self.log.info(f"SL ORDER CONFIRMED: {pair_name} status={status}")
        return trade

    def close_position(self, pair_name: str, direction: str,
                       quantity: float):
        """Close a position at market."""
        from ib_insync import MarketOrder
        contract = self._contracts.get(pair_name)
        if contract is None:
            self.log.error(f"No contract for {pair_name}")
            return None
        action = "SELL" if direction == "long" else "BUY"
        order = MarketOrder(action, quantity)
        trade = self.ib.placeOrder(contract, order)
        self.log.info(f"CLOSE: {pair_name} {action} {quantity} @ MARKET")
        self.ib.sleep(3)
        status = trade.orderStatus.status
        if status not in ('Filled', 'Submitted', 'PreSubmitted'):
            self.log.error(f"CLOSE FAILED: {pair_name} {action} {quantity} "
                           f"status={status}")
            return None
        self.log.info(f"CLOSE CONFIRMED: {pair_name} status={status}")
        return trade

    def has_position(self, symbol: str, sec_type: str) -> bool:
        """Check if broker has an actual position on this instrument."""
        try:
            positions = self.ib.positions()
            return any(
                p.contract.symbol == symbol and
                p.contract.secType == sec_type and
                abs(p.position) > 0
                for p in positions
            )
        except Exception as e:
            self.log.warning(f"Position query failed: {e}")
            return True  # assume position exists if can't check

    def get_position_size(self, symbol: str, sec_type: str) -> float:
        """Get actual position size at broker. Returns 0.0 if flat."""
        try:
            for p in self.ib.positions():
                if p.contract.symbol == symbol and p.contract.secType == sec_type:
                    return float(p.position)
        except Exception as e:
            self.log.warning(f"Position size query failed: {e}")
        return 0.0

    def cancel_all_orders(self):
        open_orders = self.ib.openOrders()
        for order in open_orders:
            try:
                self.ib.cancelOrder(order)
            except Exception:
                pass

    def get_fill_commission(self, trade) -> float:
        """Extract commission from an IBKR trade's fill reports."""
        total = 0.0
        try:
            self.ib.sleep(1)
            for fill in self.ib.fills():
                if fill.execution.orderId == trade.order.orderId:
                    comm = fill.commissionReport
                    if comm and hasattr(comm, 'commission') and comm.commission < 1e8:
                        total += comm.commission
        except Exception as e:
            self.log.warning(f"Commission lookup failed: {e}")
        return total

    def sleep(self, seconds: float):
        if self.connected:
            try:
                self.ib.sleep(seconds)
            except Exception:
                time.sleep(seconds)
        else:
            time.sleep(seconds)

    def disconnect(self):
        for pair_name, contract in self._contracts.items():
            try:
                self.ib.cancelMktData(contract)
            except Exception:
                pass
        if self.ib:
            try:
                self.ib.disconnect()
            except Exception:
                pass

    def _on_disconnect(self):
        self._connected = False
        if self._first_disconnect_time is None:
            self._first_disconnect_time = time.time()
        self.log.warning("IBKR disconnected")

    def _on_error(self, reqId, errorCode, errorString, contract):
        if errorCode in _IB_WARNING_CODES:
            return
        if errorCode in _IB_CRITICAL_ERRORS:
            self._connected = False
            if self._first_disconnect_time is None:
                self._first_disconnect_time = time.time()
            self.log.error(f"IB critical {errorCode}: {errorString}")
        else:
            if errorCode not in (162,):
                self.log.warning(f"IB error {errorCode}: {errorString}")

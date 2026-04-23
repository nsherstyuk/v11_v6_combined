"""Emergency flatten — close any open position for the named instrument.

Connects as client 99 (non-conflicting with the live strategy on client 11).
Reads current broker position, sends an opposite-side marketable LIMIT order
(LMT +/- $1 * tick-scaling from the top of book), waits for fill, verifies flat.

Usage:
    python -m v11.live.flatten XAUUSD
    python -m v11.live.flatten EURUSD

Generalization of v11/live/flatten_xauusd.py (2026-04-21 ghost-short fix).
"""
import asyncio
import sys
import time

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


INSTRUMENTS = {
    "XAUUSD": ("XAUUSD", "CMDTY", "SMART",    "USD"),
    "EURUSD": ("EUR",    "CASH",  "IDEALPRO", "USD"),
    "USDJPY": ("USD",    "CASH",  "IDEALPRO", "JPY"),
}


def _marketable_limit(action: str, bid: float, ask: float, tick: float):
    """Buffer above ask (BUY) or below bid (SELL) by max(1.0, 100 * tick).
    For XAUUSD tick=0.01 → $1. For EURUSD tick=0.00005 → 0.005. Clamped to
    at least the tick-based value and at most $1 for CASH pairs."""
    buf = max(100 * tick, tick) if tick < 0.01 else 1.00
    if action == "BUY":
        return round(ask + buf, 8)
    return round(bid - buf, 8)


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m v11.live.flatten <INSTRUMENT>")
        print(f"       where INSTRUMENT in {sorted(INSTRUMENTS)}")
        sys.exit(1)

    inst_name = sys.argv[1].upper()
    if inst_name not in INSTRUMENTS:
        print(f"Unknown instrument {inst_name}. Known: {sorted(INSTRUMENTS)}")
        sys.exit(1)
    symbol, sec_type, exchange, currency = INSTRUMENTS[inst_name]

    from ib_insync import IB, Contract, LimitOrder

    ib = IB()
    ib.connect('127.0.0.1', 4002, clientId=99, timeout=15)
    print(f"[flatten] connected (client 99) — target {inst_name}")

    try:
        contract = Contract(symbol=symbol, secType=sec_type,
                            exchange=exchange, currency=currency)
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            print(f"[flatten] ERROR: could not qualify {symbol} {sec_type}")
            sys.exit(1)
        contract = qualified[0]

        positions = [p for p in ib.positions() if p.contract.conId == contract.conId]
        if not positions or positions[0].position == 0:
            print(f"[flatten] no open position on {inst_name}. Nothing to do.")
            return
        pos = positions[0]
        qty = pos.position
        print(f"[flatten] position: {inst_name} qty={qty} avgCost={pos.avgCost}")

        ticker = ib.reqMktData(contract, '', False, False)
        ib.sleep(3)
        if not ticker.bid or not ticker.ask:
            ib.sleep(5)
        if not ticker.bid or not ticker.ask:
            print(f"[flatten] ERROR: no quote (bid={ticker.bid} ask={ticker.ask})")
            ib.cancelMktData(contract)
            sys.exit(1)
        print(f"[flatten] quote bid={ticker.bid} ask={ticker.ask}")

        tick = float(getattr(contract, "minTick", 0.01) or 0.01)
        action = "BUY" if qty < 0 else "SELL"
        limit_price = _marketable_limit(action, ticker.bid, ticker.ask, tick)
        order_qty = abs(qty)

        print(f"[flatten] placing {action} {order_qty} LMT {limit_price}")
        order = LimitOrder(action, order_qty, limit_price)
        order.outsideRth = True
        trade = ib.placeOrder(contract, order)

        t_start = time.time()
        while time.time() - t_start < 30:
            ib.sleep(0.5)
            s = trade.orderStatus
            if s.status in ("Filled", "Cancelled", "ApiCancelled", "Inactive"):
                print(f"[flatten] status={s.status} filled={s.filled}/{order_qty} "
                      f"avgPx={s.avgFillPrice}")
                break
        else:
            print(f"[flatten] TIMEOUT — last status={trade.orderStatus.status}")

        ib.sleep(1)
        ib.cancelMktData(contract)

        positions = [p for p in ib.positions() if p.contract.conId == contract.conId]
        residual = positions[0].position if positions else 0
        if residual == 0:
            print(f"[flatten] VERIFIED FLAT on {inst_name}")
        else:
            print(f"[flatten] WARNING — {inst_name} residual={residual}")
            sys.exit(2)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()

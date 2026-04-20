"""Ad-hoc IBKR order status checker — connects as a SECONDARY client so
it doesn't kick the live V11 off its slot.

Connects, dumps: open orders, positions, recent completed trades, account value.
Disconnects cleanly.

Usage:
    python -m v11.live.check_orders
"""
import asyncio
import sys

# Same asyncio hack run_live.py uses
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


def main():
    from ib_insync import IB

    ib = IB()
    # V11 uses client_id=11. Use a different ID for read-only inspection.
    CLIENT_ID = 99
    ib.connect('127.0.0.1', 4002, clientId=CLIENT_ID, timeout=10)

    print(f"Connected as client {CLIENT_ID}")
    print("=" * 70)

    # ── ALL open orders across ALL clients (reqAllOpenOrders) ─────────
    # reqOpenOrders() only returns orders for THIS client ID; V11's
    # orders (client 11) wouldn't appear there. reqAllOpenOrders() sees
    # everything, including orders placed by other client IDs.
    print("\nALL OPEN ORDERS (reqAllOpenOrders, across all clients):")
    print("-" * 70)
    ib.reqAllOpenOrders()
    ib.sleep(3)  # let async events flush
    open_orders = [t for t in ib.trades() if t.orderStatus.status in
                   ('Submitted', 'PreSubmitted', 'PendingSubmit', 'ApiPending')]
    if not open_orders:
        print("  (no open orders on IBKR side)")
    else:
        for trade in open_orders:
            o = trade.order
            s = trade.orderStatus
            c = trade.contract
            print(f"  id={o.orderId}  {c.symbol}  {o.action} {o.orderType} "
                  f"qty={o.totalQuantity}  "
                  f"aux={getattr(o, 'auxPrice', '-')}  "
                  f"lmt={getattr(o, 'lmtPrice', '-')}")
            print(f"    status={s.status}  filled={s.filled}  "
                  f"remaining={s.remaining}  avgFillPrice={s.avgFillPrice}")
            print(f"    oca={o.ocaGroup}  tif={o.tif}  gtd={o.goodTillDate}")

    # ── ALL orders this session (including filled/cancelled) ───────────
    print("\nALL ORDERS THIS SESSION (from ib.trades()):")
    print("-" * 70)
    trades = ib.trades()
    if not trades:
        print("  (none)")
    else:
        for trade in trades:
            o = trade.order
            s = trade.orderStatus
            c = trade.contract
            print(f"  id={o.orderId}  {c.symbol}  {o.action} {o.orderType} "
                  f"qty={o.totalQuantity}  status={s.status}  "
                  f"filled={s.filled}  avgPx={s.avgFillPrice}")
            if trade.fills:
                for f in trade.fills:
                    print(f"    FILL: {f.execution.side} {f.execution.shares} "
                          f"@ {f.execution.avgPrice} "
                          f"({f.execution.time}) "
                          f"commission={getattr(f.commissionReport, 'commission', '?')}")

    # ── Current positions ──────────────────────────────────────────────
    print("\nPOSITIONS:")
    print("-" * 70)
    positions = ib.positions()
    if not positions:
        print("  (flat)")
    else:
        for p in positions:
            print(f"  {p.contract.symbol} ({p.contract.secType}): "
                  f"pos={p.position}  avgCost={p.avgCost}")

    # ── Account summary ────────────────────────────────────────────────
    print("\nACCOUNT SUMMARY:")
    print("-" * 70)
    try:
        summary = ib.accountSummary()
        for row in summary:
            if row.tag in ("NetLiquidation", "RealizedPnL", "UnrealizedPnL",
                           "AvailableFunds", "DayTradesRemaining"):
                print(f"  {row.tag:<22} = {row.value}  ({row.currency})")
    except Exception as e:
        print(f"  (account summary unavailable: {e})")

    print("\n" + "=" * 70)
    ib.disconnect()
    print("Disconnected cleanly")


if __name__ == "__main__":
    main()

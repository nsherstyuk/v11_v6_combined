"""Ad-hoc order cancellation — connects as secondary client, cancels by
order ID. Does not touch V11's connection.

Usage:
    python -m v11.live.cancel_order 73
    python -m v11.live.cancel_order 73 74 75  # multiple
"""
import asyncio
import sys

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m v11.live.cancel_order <order_id> [<order_id> ...]")
        sys.exit(1)

    target_ids = {int(x) for x in sys.argv[1:]}

    from ib_insync import IB

    ib = IB()
    ib.connect('127.0.0.1', 4002, clientId=99, timeout=10)

    ib.reqAllOpenOrders()
    ib.sleep(3)

    trades = ib.trades()
    found = [t for t in trades if t.order.orderId in target_ids]

    if not found:
        print(f"No open orders found with IDs {sorted(target_ids)}")
        ib.disconnect()
        return

    for t in found:
        print(f"Cancelling order id={t.order.orderId} "
              f"({t.contract.symbol} {t.order.action} {t.order.orderType} "
              f"{t.order.auxPrice})")
        ib.cancelOrder(t.order)

    ib.sleep(3)
    print("Done. Re-check status:")
    for t in ib.trades():
        if t.order.orderId in target_ids:
            print(f"  id={t.order.orderId}  status={t.orderStatus.status}")

    ib.disconnect()


if __name__ == "__main__":
    main()

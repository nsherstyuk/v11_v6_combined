"""IBKR paper-account fill diagnostic.

Places a SINGLE marketable LIMIT BUY on XAUUSD (1 oz), hooks the full
orderStatusEvent stream to log every transition in real time, waits for
fill, then closes with a MARKET SELL. Reports whether the instrument
is usable in this paper account.

This is the test that would have caught this morning's "order silently
discarded" bug. If the orderStatusEvent doesn't fire for every transition,
or if the order gets Discarded between PreSubmitted and Filled, we know
paper-trading XAUUSD STPs is unreliable regardless of V11 code quality.

Usage:
    python -m v11.live.test_paper_fill
"""
import asyncio
import sys
import time

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


def _ts():
    return time.strftime("%H:%M:%S", time.localtime())


def main():
    from ib_insync import IB, Contract, LimitOrder, MarketOrder

    ib = IB()

    status_events = []

    def on_status(trade):
        t = _ts()
        s = trade.orderStatus
        entry = (f"[{t}] orderStatusEvent: id={trade.order.orderId} "
                 f"status={s.status} filled={s.filled}/{s.remaining} "
                 f"avgPx={s.avgFillPrice}")
        print(entry, flush=True)
        status_events.append(entry)

    def on_error(reqId, errorCode, errorString, contract):
        print(f"[{_ts()}] ERROR reqId={reqId} code={errorCode} msg={errorString}",
              flush=True)

    def on_exec(trade, fill):
        print(f"[{_ts()}] EXECUTION: {fill.execution.side} "
              f"{fill.execution.shares} @ {fill.execution.avgPrice} "
              f"time={fill.execution.time}", flush=True)

    print(f"[{_ts()}] Connecting as client 99...")
    ib.connect('127.0.0.1', 4002, clientId=99, timeout=15)
    ib.orderStatusEvent += on_status
    ib.errorEvent += on_error
    ib.execDetailsEvent += on_exec

    # ── Qualify XAUUSD contract (match V11's config) ──────────────────
    contract = Contract(symbol="XAUUSD", secType="CMDTY",
                        exchange="SMART", currency="USD")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        print("ERROR: Could not qualify XAUUSD CMDTY contract")
        ib.disconnect()
        sys.exit(1)
    contract = qualified[0]
    print(f"[{_ts()}] Qualified: {contract.symbol} {contract.secType} "
          f"exchange={contract.exchange} conId={contract.conId}")

    # ── Get current market price ──────────────────────────────────────
    ticker = ib.reqMktData(contract, '', False, False)
    ib.sleep(3)  # wait for snapshot
    if ticker.bid is None or ticker.ask is None or ticker.bid == 0:
        print(f"[{_ts()}] WARNING: no bid/ask yet. bid={ticker.bid} ask={ticker.ask}")
        print(f"[{_ts()}] Waiting 5 more seconds for quotes...")
        ib.sleep(5)
    mid = (ticker.bid + ticker.ask) / 2 if ticker.bid and ticker.ask else None
    print(f"[{_ts()}] Market: bid={ticker.bid} ask={ticker.ask} mid={mid}")

    if mid is None or mid <= 0:
        print("ERROR: Could not get market price. Aborting test.")
        ib.cancelMktData(contract)
        ib.disconnect()
        sys.exit(1)

    # ── Place marketable LIMIT BUY ────────────────────────────────────
    # 1 oz, limit price $1 above ask → marketable, should fill immediately
    limit_price = round(ticker.ask + 1.00, 2)
    print(f"[{_ts()}] Placing marketable LIMIT BUY 1 @ {limit_price} "
          f"(ask={ticker.ask}, spread test)")

    buy_order = LimitOrder("BUY", 1, limit_price)
    buy_order.outsideRth = True
    buy_trade = ib.placeOrder(contract, buy_order)
    print(f"[{_ts()}] placeOrder returned: orderId={buy_trade.order.orderId}")

    # ── Watch status for 30 seconds ───────────────────────────────────
    print(f"[{_ts()}] Watching orderStatusEvent for 30s...")
    t_start = time.time()
    while time.time() - t_start < 30:
        ib.sleep(0.5)
        if buy_trade.orderStatus.status in ('Filled', 'Cancelled', 'Inactive',
                                              'ApiCancelled'):
            break

    final_status = buy_trade.orderStatus.status
    filled = buy_trade.orderStatus.filled
    avg_px = buy_trade.orderStatus.avgFillPrice
    print(f"\n[{_ts()}] ============================================")
    print(f"[{_ts()}] BUY final: status={final_status} filled={filled} avgPx={avg_px}")
    print(f"[{_ts()}] ============================================\n")

    # ── If filled, close with market order ────────────────────────────
    if filled > 0:
        print(f"[{_ts()}] Position open. Closing with MARKET SELL...")
        sell_order = MarketOrder("SELL", filled)
        sell_order.outsideRth = True
        sell_trade = ib.placeOrder(contract, sell_order)
        t_start = time.time()
        while time.time() - t_start < 30:
            ib.sleep(0.5)
            if sell_trade.orderStatus.status in ('Filled', 'Cancelled', 'Inactive'):
                break
        print(f"[{_ts()}] SELL final: status={sell_trade.orderStatus.status} "
              f"filled={sell_trade.orderStatus.filled} "
              f"avgPx={sell_trade.orderStatus.avgFillPrice}")

    elif final_status not in ('Filled', 'Submitted'):
        print(f"[{_ts()}] BUY did not fill. Cancelling...")
        try:
            ib.cancelOrder(buy_order)
            ib.sleep(3)
        except Exception:
            pass

    # ── Summary ───────────────────────────────────────────────────────
    print(f"\n[{_ts()}] SUMMARY")
    print("=" * 60)
    print(f"Total orderStatusEvent callbacks: {len(status_events)}")
    for e in status_events:
        print(f"  {e}")
    print("=" * 60)

    if filled > 0:
        print("VERDICT: Paper account CAN fill XAUUSD orders cleanly.")
        print("         The earlier ghost-order issue is V11-side (observability/state).")
    else:
        print(f"VERDICT: Paper account did NOT fill a marketable LIMIT BUY "
              f"(status={final_status}).")
        print("         XAUUSD CMDTY paper-trading is unreliable. Consider")
        print("         switching to a different contract (MGC futures?)")
        print("         or a different account type.")

    ib.cancelMktData(contract)
    ib.disconnect()


if __name__ == "__main__":
    main()

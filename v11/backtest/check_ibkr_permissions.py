"""
Check IBKR account trading permissions via ib_insync.
Connects to IB Gateway on port 4002 and queries account details.
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

# Python 3.14 compatibility (same as run_live.py)
import asyncio
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

if sys.version_info >= (3, 14):
    _original_wait_for = asyncio.wait_for
    async def _patched_wait_for(fut, timeout):
        if timeout is None:
            return await fut
        fut = asyncio.ensure_future(fut)
        loop = asyncio.get_event_loop()
        timed_out = False
        def _on_timeout():
            nonlocal timed_out
            timed_out = True
            fut.cancel()
        timer = loop.call_later(timeout, _on_timeout)
        try:
            return await fut
        except asyncio.CancelledError:
            if timed_out:
                raise asyncio.TimeoutError()
            raise
        finally:
            timer.cancel()
    asyncio.wait_for = _patched_wait_for

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

from ib_insync import IB, Contract, Option, Future
import pandas as pd

PORT = 4002  # IB Gateway paper

ib = IB()
print(f"Connecting to IB Gateway on port {PORT}...")
ib.connect('127.0.0.1', PORT, clientId=999)

# ── 1. Account summary ──────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("  ACCOUNT SUMMARY")
print("=" * 70)

summary = ib.accountSummary()
if summary:
    df = pd.DataFrame([{s.tag: s.value} for s in summary])
    # Show key fields
    key_fields = ['AccountType', 'NetLiquidation', 'AvailableFunds', 'GrossPositionValue',
                  'TotalCashValue', 'UnrealizedPnL', 'RealizedPnL', 'BuyingPower',
                  'Leverage', 'Cushion']
    for field in key_fields:
        for s in summary:
            if s.tag == field:
                print(f"  {field}: {s.value} ({s.currency})")

# ── 2. Account managed funds / permissions ──────────────────────────────────
print("\n" + "=" * 70)
print("  TRADING PERMISSIONS CHECK")
print("=" * 70)

# Try to query various contract types to see what's allowed
test_contracts = {
    "US Stocks": Contract(secType='STK', symbol='AAPL', exchange='SMART', currency='USD'),
    "FX CASH (EURUSD)": Contract(secType='CASH', symbol='EUR', exchange='IDEALPRO', currency='USD'),
    "Gold (XAUUSD)": Contract(secType='CASH', symbol='XAU', exchange='IDEALPRO', currency='USD'),
    "FX Futures (6E)": Future(symbol='6E', exchange='CME', currency='USD', lastTradeDateOrContractMonth='202506'),
    "FX Futures (6A)": Future(symbol='6A', exchange='CME', currency='USD', lastTradeDateOrContractMonth='202506'),
    "Gold Futures (GC)": Future(symbol='GC', exchange='COMEX', currency='USD', lastTradeDateOrContractMonth='202506'),
    "Index Futures (ES)": Future(symbol='ES', exchange='CME', currency='USD', lastTradeDateOrContractMonth='202506'),
}

for name, contract in test_contracts.items():
    try:
        details = ib.reqContractDetails(contract)
        if details:
            print(f"  ✅ {name}: AVAILABLE ({len(details)} contract(s) found)")
            # Show first detail
            d = details[0]
            print(f"     Exchange: {d.contract.exchange} | Currency: {d.contract.currency}")
        else:
            print(f"  ❌ {name}: NO DETAILS (may lack permission or contract not found)")
    except Exception as e:
        print(f"  ❌ {name}: ERROR — {e}")

ib.sleep(1)

# ── 3. Options on Futures ───────────────────────────────────────────────────
print("\n" + "=" * 70)
print("  OPTIONS ON FUTURES CHECK")
print("=" * 70)

# Try to find options on EUR futures (6E)
opt_tests = {
    "6E Options (EUR futures)": Option(
        symbol='6E', exchange='CME', currency='USD',
        lastTradeDateOrContractMonth='202506',
        strike=1.10, right='C',
        multiplier='62500'
    ),
    "GC Options (Gold futures)": Option(
        symbol='GC', exchange='COMEX', currency='USD',
        lastTradeDateOrContractMonth='202506',
        strike=2500, right='C',
        multiplier='100'
    ),
    "ES Options (S&P futures)": Option(
        symbol='ES', exchange='CME', currency='USD',
        lastTradeDateOrContractMonth='202506',
        strike=5500, right='C',
        multiplier='50'
    ),
}

for name, contract in opt_tests.items():
    try:
        details = ib.reqContractDetails(contract)
        if details:
            print(f"  ✅ {name}: AVAILABLE ({len(details)} contract(s))")
        else:
            # Try without specific strike/expiry — just search
            print(f"  ⚠️ {name}: No exact match, searching...")
            # Broad search
            search_contract = Contract(secType='OPT', exchange=contract.exchange,
                                        currency=contract.currency, symbol=contract.symbol)
            try:
                details2 = ib.reqContractDetails(search_contract)
                if details2:
                    print(f"     Found {len(details2)} option contracts for {contract.symbol}")
                    print(f"  ✅ {name}: AVAILABLE (broad search found contracts)")
                else:
                    print(f"  ❌ {name}: NOT AVAILABLE (no option contracts found)")
            except Exception as e2:
                print(f"  ❌ {name}: NOT AVAILABLE — {e2}")
    except Exception as e:
        print(f"  ❌ {name}: ERROR — {e}")

ib.sleep(1)

# ── 4. What V11 currently trades ────────────────────────────────────────────
print("\n" + "=" * 70)
print("  V11 CURRENT POSITIONS & OPEN ORDERS")
print("=" * 70)

positions = ib.positions()
if positions:
    for p in positions:
        print(f"  {p.contract.symbol} {p.contract.secType}: {p.position} @ avg {p.avgPrice:.4f}")
else:
    print("  No open positions")

orders = ib.openOrders()
if orders:
    for o in orders:
        print(f"  Order: {o.orderType} {o.action} {o.totalQuantity} {o.contract.symbol}")
else:
    print("  No open orders")

# ── 5. Account rules / permissions via reqAccountUpdates ────────────────────
print("\n" + "=" * 70)
print("  ACCOUNT RULES (via reqAccountUpdates)")
print("=" * 70)

ib.reqAccountUpdates(account='')
ib.sleep(2)

# Check managed accounts
managed = ib.managedAccounts()
print(f"  Managed accounts: {managed}")

# ── Summary ─────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("  VRP FEASIBILITY SUMMARY")
print("=" * 70)
print("""
  For Volatility Risk Premium (VRP) on FX, you need:
  1. Futures trading permission → to trade FX futures (6E, 6A, etc.)
  2. Options on Futures permission → to sell straddles/strangles
  3. Portfolio margin (ideal) or Reg T margin (works but less capital efficient)

  If Futures ✅ + Options on Futures ✅ → VRP is implementable
  If either ❌ → you need to request permission via IBKR Client Portal
""")

ib.disconnect()
print("Done.")

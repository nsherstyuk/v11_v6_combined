"""
IBKR + Grok Swing Trading Agent — main.py
==========================================
Fully automated swing-trading agent that:
  1. Connects to Interactive Brokers via ib_async
  2. Pulls 5-day hourly bars + live quotes for the watchlist
  3. Sends structured market data to Grok (xAI) every N minutes
  4. Parses Grok's JSON trade recommendations
  5. Applies strict risk management before placing orders

*** PAPER TRADING IS ON BY DEFAULT — see config.py / .env ***
"""

import asyncio
import json
import math
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI
from ib_async import IB, Stock, LimitOrder, MarketOrder, Trade
import pandas as pd

from config import (
    XAI_API_KEY,
    IB_HOST,
    IB_PORT,
    IB_CLIENT_ID,
    PAPER_TRADING,
    WATCHLIST,
    LOOP_INTERVAL_MINUTES,
    MAX_RISK_PER_TRADE,
    DAILY_LOSS_LIMIT,
    ACCOUNT_SIZE,
    CONFIDENCE_THRESHOLD,
    GROK_MODEL,
    GROK_BASE_URL,
    GROK_TEMPERATURE,
    GROK_MAX_TOKENS,
    STRATEGY_RULES,
    ANALYSIS_START_HOUR,
    ANALYSIS_START_MINUTE,
)
from utils.logger import setup_logger
from models import GrokDecision

# ========================== LOGGER ==========================
log = setup_logger()

# ========================== MARKET HOURS ==========================

ET = ZoneInfo("America/New_York")
ANALYSIS_START = time(ANALYSIS_START_HOUR, ANALYSIS_START_MINUTE)  # e.g. 7:00 AM ET
MARKET_OPEN = time(9, 30)   # 9:30 AM ET
MARKET_CLOSE = time(16, 0)  # 4:00 PM ET


def get_market_status() -> dict:
    """
    Determine the current trading phase (US equities, Eastern Time).

    Three phases on weekdays:
      SLEEPING      — before ANALYSIS_START and after MARKET_CLOSE
      PRE-MARKET    — ANALYSIS_START to 9:30 AM (Grok called, no orders)
      REGULAR (RTH) — 9:30 AM to 4:00 PM (Grok called + orders placed)

    Weekends are always SLEEPING.

    Returns:
        dict with keys:
          'phase'           — SLEEPING | PRE-MARKET | REGULAR
          'is_rth'          — True only during REGULAR
          'is_active'       — True during PRE-MARKET and REGULAR (Grok is called)
          'session'         — Human-readable label
          'current_time_et' — Formatted ET timestamp
    """
    now_et = datetime.now(ET)
    current = now_et.time()
    weekday = now_et.weekday()  # 0=Mon … 6=Sun

    if weekday >= 5:
        phase = "SLEEPING"
        session = "WEEKEND"
    elif current < ANALYSIS_START:
        phase = "SLEEPING"
        session = "OVERNIGHT"
    elif current < MARKET_OPEN:
        phase = "PRE-MARKET"
        session = "PRE-MARKET"
    elif current < MARKET_CLOSE:
        phase = "REGULAR"
        session = "REGULAR"
    else:
        phase = "SLEEPING"
        session = "AFTER-HOURS"

    return {
        "phase": phase,
        "is_rth": phase == "REGULAR",
        "is_active": phase in ("PRE-MARKET", "REGULAR"),
        "session": session,
        "current_time_et": now_et.strftime("%Y-%m-%d %H:%M:%S %Z"),
    }


def seconds_until_next_active() -> int:
    """
    Calculate seconds from now until the next ANALYSIS_START on a weekday.
    Used to sleep efficiently during off-hours instead of polling.
    """
    now_et = datetime.now(ET)
    # Start with tomorrow if today's window has passed
    candidate = now_et.replace(
        hour=ANALYSIS_START_HOUR, minute=ANALYSIS_START_MINUTE, second=0, microsecond=0
    )
    if candidate <= now_et:
        candidate += timedelta(days=1)
    # Skip weekends
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    delta = (candidate - now_et).total_seconds()
    return max(int(delta), 1)


# ========================== GROK CLIENT ==========================
grok_client = AsyncOpenAI(api_key=XAI_API_KEY, base_url=GROK_BASE_URL)


async def ask_grok(market_data: dict, market_status: dict) -> dict:
    """
    Send structured market data + market-hours context to Grok.

    Returns:
        dict with key "trades" containing a list of trade recommendations.
    """
    status_block = (
        f"Market phase status:\n"
        f"  Phase:  {market_status['phase']}\n"
        f"  Session:  {market_status['session']}\n"
        f"  Time ET:  {market_status['current_time_et']}\n"
        f"  RTH open: {market_status['is_rth']}\n"
        f"NOTE: If the market is not in REGULAR phase, you may still analyse "
        f"and provide recommendations, but orders will NOT be placed until the "
        f"next regular session.\n"
    )
    prompt = (
        f"{status_block}\n"
        f"Market data (live + 5-day hourly bars):\n"
        f"{json.dumps(market_data, indent=2, default=str)}\n\n"
        f"{STRATEGY_RULES}"
    )

    try:
        response = await grok_client.chat.completions.create(
            model=GROK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=GROK_TEMPERATURE,
            max_tokens=GROK_MAX_TOKENS,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        log.error(f"Grok API request failed: {exc}")
        return {"trades": []}

    # Log estimated cost (grok-4-1-fast-reasoning pricing as of March 2026)
    usage = response.usage
    if usage:
        cost_in = usage.prompt_tokens * 0.0000002
        cost_out = usage.completion_tokens * 0.0000005
        log.info(
            f"Grok usage — prompt: {usage.prompt_tokens} tokens, "
            f"completion: {usage.completion_tokens} tokens, "
            f"est. cost: ${cost_in + cost_out:.4f}"
        )

    raw = response.choices[0].message.content
    try:
        decision = GrokDecision.model_validate_json(raw)
        return decision.model_dump()
    except Exception as exc:
        log.error(f"Grok response validation failed: {exc}\nRaw: {raw[:500]}")
        return {"trades": []}


# ========================== IBKR CONNECTION ==========================
ib = IB()

# Reconnection parameters
MAX_RECONNECT_ATTEMPTS = 5
RECONNECT_DELAY_SECONDS = 10


async def connect_with_retry() -> None:
    """Connect to IBKR with exponential back-off retry logic."""
    for attempt in range(1, MAX_RECONNECT_ATTEMPTS + 1):
        try:
            if ib.isConnected():
                ib.disconnect()
            await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
            mode_label = "PAPER" if PAPER_TRADING else "*** LIVE ***"
            log.info(f"Connected to IBKR ({IB_HOST}:{IB_PORT}) | Mode: {mode_label}")
            return
        except Exception as exc:
            delay = RECONNECT_DELAY_SECONDS * attempt
            log.warning(
                f"IBKR connection attempt {attempt}/{MAX_RECONNECT_ATTEMPTS} failed: {exc} "
                f"— retrying in {delay}s"
            )
            await asyncio.sleep(delay)

    raise ConnectionError(
        f"Could not connect to IBKR after {MAX_RECONNECT_ATTEMPTS} attempts. "
        "Is IB Gateway / TWS running?"
    )


# ========================== MARKET DATA ==========================

async def get_market_data(ticker: str) -> dict:
    """
    Fetch 5-day hourly bars and a live snapshot for a single ticker.

    Returns:
        dict with ticker, current_price, volume, recent bars, and timestamp.
    """
    contract = Stock(ticker, "SMART", "USD")
    await ib.qualifyContractsAsync(contract)

    # Historical bars — last 5 trading days, 1-hour candles
    bars = await ib.reqHistoricalDataAsync(
        contract, "", "5 D", "1 hour", "TRADES", useRTH=True
    )
    if bars:
        df = pd.DataFrame([vars(b) for b in bars])
    else:
        log.warning(f"No historical bars returned for {ticker}")
        df = pd.DataFrame()

    # Live snapshot — request and wait briefly for data to fill
    ticker_obj = ib.reqMktData(contract, genericTickList="", snapshot=False)
    await asyncio.sleep(2)

    current_price = ticker_obj.marketPrice()
    volume = ticker_obj.volume

    # Cancel live data subscription to avoid hitting limits
    ib.cancelMktData(contract)

    # --- Derived metrics for Grok ---
    relative_volume = None
    distance_to_vwap = None

    if not df.empty and "volume" in df.columns:
        avg_vol = df["volume"].mean()
        if avg_vol and avg_vol > 0 and volume and volume > 0:
            relative_volume = round(volume / avg_vol, 2)

    if not df.empty and current_price and math.isfinite(current_price):
        # Approximate VWAP from available bars: sum(close*volume) / sum(volume)
        if "close" in df.columns and "volume" in df.columns:
            vol_sum = df["volume"].sum()
            if vol_sum > 0:
                vwap = (df["close"] * df["volume"]).sum() / vol_sum
                distance_to_vwap = round((current_price - vwap) / vwap * 100, 2)

    return {
        "ticker": ticker,
        "current_price": current_price if math.isfinite(current_price or 0) else None,
        "volume": volume if volume and volume > 0 else None,
        "relative_volume": relative_volume,
        "distance_to_vwap_pct": distance_to_vwap,
        "bars_last_10": df.tail(10).to_dict(orient="records") if not df.empty else [],
        "timestamp": datetime.now().isoformat(),
    }


# ========================== RISK MANAGEMENT ==========================

class RiskManager:
    """
    Tracks daily P&L and enforces per-trade and daily risk limits.

    Safety rules:
      - Max risk per trade: ACCOUNT_SIZE * MAX_RISK_PER_TRADE
      - Daily loss limit:   ACCOUNT_SIZE * DAILY_LOSS_LIMIT  → halt all trading
    """

    def __init__(self) -> None:
        self.daily_loss: float = 0.0
        self.current_date: date = date.today()
        self.orders_placed_today: int = 0

    def _reset_if_new_day(self) -> None:
        """Reset daily counters at the start of a new trading day."""
        today = date.today()
        if today != self.current_date:
            log.info(f"New trading day ({today}) — resetting daily loss counter")
            self.daily_loss = 0.0
            self.orders_placed_today = 0
            self.current_date = today

    @property
    def max_risk_dollars(self) -> float:
        return ACCOUNT_SIZE * MAX_RISK_PER_TRADE

    @property
    def daily_loss_limit_dollars(self) -> float:
        return ACCOUNT_SIZE * DAILY_LOSS_LIMIT

    def is_daily_limit_hit(self) -> bool:
        self._reset_if_new_day()
        if self.daily_loss >= self.daily_loss_limit_dollars:
            log.warning(
                f"DAILY LOSS LIMIT reached (${self.daily_loss:.2f} >= "
                f"${self.daily_loss_limit_dollars:.2f}) — halting trades for today"
            )
            return True
        return False

    def check_trade(self, trade_rec: dict) -> bool:
        """
        Validate a single trade recommendation against risk rules.

        Args:
            trade_rec: Grok trade recommendation dict.

        Returns:
            True if the trade passes all risk checks.
        """
        self._reset_if_new_day()

        ticker = trade_rec.get("ticker", "???")
        confidence = trade_rec.get("confidence", 0)
        entry = trade_rec.get("entry", 0)
        stop = trade_rec.get("stop", 0)
        shares = trade_rec.get("shares", 0)

        # --- Confidence gate ---
        if confidence < CONFIDENCE_THRESHOLD:
            log.info(
                f"[{ticker}] Confidence {confidence} < threshold {CONFIDENCE_THRESHOLD} — skip"
            )
            return False

        # --- Basic sanity ---
        if entry <= 0 or stop <= 0 or shares <= 0:
            log.warning(f"[{ticker}] Invalid trade params (entry={entry}, stop={stop}, shares={shares})")
            return False

        if stop >= entry:
            log.warning(f"[{ticker}] Stop ({stop}) >= entry ({entry}) — invalid for long trade")
            return False

        # --- Per-trade risk ---
        risk_per_share = abs(entry - stop)
        total_risk = risk_per_share * shares
        if total_risk > self.max_risk_dollars:
            log.warning(
                f"[{ticker}] Trade risk ${total_risk:.2f} exceeds max ${self.max_risk_dollars:.2f} — skip"
            )
            return False

        # --- Position size sanity (don't exceed account) ---
        position_cost = entry * shares
        if position_cost > ACCOUNT_SIZE * 0.5:
            log.warning(
                f"[{ticker}] Position cost ${position_cost:.2f} exceeds 50% of account — skip"
            )
            return False

        # --- Daily loss limit ---
        if self.is_daily_limit_hit():
            return False

        log.info(
            f"[{ticker}] Risk check PASSED — risk=${total_risk:.2f}, "
            f"confidence={confidence}, entry={entry}, stop={stop}, shares={shares}"
        )
        return True

    def record_trade(self, trade_rec: dict) -> None:
        """Record that a trade was placed (for daily tracking)."""
        self.orders_placed_today += 1
        # Assume worst-case risk as "committed" daily loss for limit tracking
        risk_per_share = abs(trade_rec.get("entry", 0) - trade_rec.get("stop", 0))
        committed = risk_per_share * trade_rec.get("shares", 0)
        self.daily_loss += committed
        log.debug(
            f"Daily loss tracker: +${committed:.2f} committed → total ${self.daily_loss:.2f}"
        )


# ========================== ORDER PLACEMENT ==========================

async def place_order(trade_rec: dict) -> None:
    """
    Place a BUY order on IBKR based on a validated trade recommendation.

    In PAPER mode: uses LimitOrder at the recommended entry price.
    In LIVE mode:  uses LimitOrder as well for safety (override if you want MarketOrder).

    *** SAFETY: This function should only be called AFTER risk checks pass. ***
    """
    ticker = trade_rec["ticker"]
    shares = trade_rec["shares"]
    entry = trade_rec["entry"]

    contract = Stock(ticker, "SMART", "USD")
    await ib.qualifyContractsAsync(contract)

    # *** SAFETY: Always use LimitOrder to avoid slippage. ***
    # In paper mode this is strictly enforced.
    # In live mode you could change to MarketOrder, but LimitOrder is recommended.
    if PAPER_TRADING:
        order = LimitOrder("BUY", shares, entry)
        order_type_label = "LIMIT (paper)"
    else:
        # *** LIVE TRADING — use limit orders for safety ***
        order = LimitOrder("BUY", shares, entry)
        order_type_label = "LIMIT (live)"

    trade_obj: Trade = ib.placeOrder(contract, order)
    log.info(
        f"ORDER PLACED [{order_type_label}] — "
        f"BUY {shares} {ticker} @ ${entry:.2f} | "
        f"Stop: ${trade_rec.get('stop', 'N/A')} | "
        f"Target: ${trade_rec.get('target', 'N/A')} | "
        f"Confidence: {trade_rec.get('confidence')} | "
        f"Reason: {trade_rec.get('reason', 'N/A')}"
    )


# ========================== MAIN LOOP ==========================

async def main_loop() -> None:
    """
    Core event loop:
      1. Connect to IBKR (with retry)
      2. Pull market data for each watchlist ticker
      3. Send to Grok for trade decisions
      4. Validate each recommendation through RiskManager
      5. Place passing orders
      6. Sleep for LOOP_INTERVAL_MINUTES, then repeat
    """
    await connect_with_retry()

    risk_mgr = RiskManager()

    while True:
        try:
            # --- Ensure IBKR connection is alive ---
            if not ib.isConnected():
                log.warning("IBKR connection lost — reconnecting...")
                await connect_with_retry()

            # --- 1. Gather market data ---
            market_data: dict = {}
            for ticker in WATCHLIST:
                try:
                    market_data[ticker] = await get_market_data(ticker)
                except Exception as exc:
                    log.error(f"Failed to fetch data for {ticker}: {exc}")

            if not market_data:
                log.warning("No market data retrieved — skipping this cycle")
                await asyncio.sleep(LOOP_INTERVAL_MINUTES * 60)
                continue

            log.info(f"Pulled market data for {len(market_data)}/{len(WATCHLIST)} tickers")

            # --- 2. Check market phase ---
            market_status = get_market_status()
            phase = market_status["phase"]
            is_rth = market_status["is_rth"]
            is_active = market_status["is_active"]
            log.info(
                f"Market phase: {phase} ({market_status['session']}) | "
                f"{market_status['current_time_et']}"
            )

            # --- If SLEEPING, skip Grok and sleep until next active window ---
            if not is_active:
                sleep_secs = seconds_until_next_active()
                hours, remainder = divmod(sleep_secs, 3600)
                mins = remainder // 60
                log.info(
                    f"Market closed ({market_status['session']}) "
                    f"\u2014 sleeping {hours}h {mins}m until next analysis window"
                )
                await asyncio.sleep(sleep_secs)
                continue

            # --- 3. Ask Grok for trade recommendations ---
            decision = await ask_grok(market_data, market_status)
            trades = decision.get("trades", [])
            log.info(f"Grok returned {len(trades)} trade recommendation(s)")

            # --- 4. Validate and execute ---
            if not is_rth:
                # PRE-MARKET: log recommendations but do NOT place orders
                log.info(
                    f"Pre-market analysis mode \u2014 "
                    f"logging recommendations only, orders start at 9:30 AM ET"
                )
                for trade_rec in trades:
                    log.info(
                        f"[{trade_rec.get('ticker','?')}] Grok recommends: "
                        f"{trade_rec.get('action')} {trade_rec.get('shares')} @ "
                        f"{trade_rec.get('entry')} (confidence {trade_rec.get('confidence')}) "
                        f"\u2014 DEFERRED (pre-market)"
                    )
            else:
                # REGULAR: full risk checks + order placement
                for trade_rec in trades:
                    if not risk_mgr.check_trade(trade_rec):
                        continue

                    await place_order(trade_rec)
                    risk_mgr.record_trade(trade_rec)

            # --- 5. Sleep until next cycle ---
            log.info(f"Cycle complete \u2014 sleeping {LOOP_INTERVAL_MINUTES} minutes")
            await asyncio.sleep(LOOP_INTERVAL_MINUTES * 60)

        except KeyboardInterrupt:
            log.info("Shutdown requested by user (KeyboardInterrupt)")
            break
        except Exception as exc:
            log.error(f"Unexpected error in main loop: {exc}", exc_info=True)
            log.info("Retrying in 60 seconds...")
            await asyncio.sleep(60)

    # Graceful shutdown
    if ib.isConnected():
        ib.disconnect()
        log.info("Disconnected from IBKR — goodbye")


# ========================== ENTRY POINT ==========================

if __name__ == "__main__":
    print("=" * 60)
    print("  IBKR + Grok Swing Trading Agent")
    print("=" * 60)
    print(f"  Mode:      {'PAPER' if PAPER_TRADING else '*** LIVE ***'}")
    print(f"  Watchlist: {', '.join(WATCHLIST)}")
    print(f"  Interval:  {LOOP_INTERVAL_MINUTES} min")
    print(f"  Risk/trade: {MAX_RISK_PER_TRADE*100:.1f}%  |  Daily limit: {DAILY_LOSS_LIMIT*100:.1f}%")
    print(f"  IBKR:      {IB_HOST}:{IB_PORT}")
    print("=" * 60)
    print("  Ensure IB Gateway / TWS is running before continuing.")
    print("=" * 60)

    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\nShutdown complete.")
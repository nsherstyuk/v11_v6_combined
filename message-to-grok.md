# IBKR + Grok Swing Trading Agent — Status Review Request

## Context

I'm building a fully automated swing-trading agent that connects to Interactive Brokers (IBKR), pulls live market data, sends it to you (Grok) for intelligent trade decisions, and places orders automatically. Paper trading mode is on by default.

An AI coding agent (Cascade in Windsurf IDE) built the initial codebase. I'm now reviewing it against my operating standards before running it. I need your analysis of the code quality, compliance with the standards, and your input on the overall architecture.

## What's Been Built

The project has this structure:

```
ibkr-grok-swing-agent/
├── main.py              # Core agent loop (attached)
├── config.py            # All settings loaded from .env (attached)
├── .env.example         # Environment variable template (attached)
├── requirements.txt     # Dependencies (attached)
├── utils/
│   ├── __init__.py
│   └── logger.py        # Centralized rotating-file + console logger (attached)
└── logs/
    └── .gitkeep
```

### Architecture Summary

**main.py** — The core loop that:
1. Connects to IBKR via `ib_async` with retry logic (up to 5 attempts, exponential back-off)
2. Checks a 3-phase market schedule:
   - **SLEEPING** (4:00 PM – 7:00 AM ET + weekends): No Grok calls, no API cost. Sleeps until next active window.
   - **PRE-MARKET** (7:00 AM – 9:30 AM ET): Pulls data + calls Grok every 15 min. Recommendations logged but NO orders placed.
   - **REGULAR / RTH** (9:30 AM – 4:00 PM ET): Full cycle — data + Grok + risk checks + order placement.
3. For each ticker in the watchlist (EEIQ, SGML, UGRO, ANET), pulls 5-day hourly bars + live price/volume from IBKR.
4. Sends all market data + market session status to Grok with strategy rules. Grok returns JSON trade recommendations.
5. Each recommendation passes through a `RiskManager` class that enforces:
   - Confidence threshold (≥70 to execute)
   - Per-trade risk cap (entry-stop × shares ≤ 1% of account)
   - Position size cap (no single position > 50% of account)
   - Stop must be below entry (long trades only)
   - Daily loss limit (3% cumulative committed risk → halt all trading)
   - Automatic daily reset at midnight
6. Passing trades are placed as LimitOrders on IBKR (both paper and live mode use LimitOrders for safety).

**config.py** — Centralized configuration loaded from `.env` with sensible defaults. Watchlist, risk params, timing, schedule, and strategy prompt are all editable here.

**utils/logger.py** — Rotating file handler (5MB, 5 backups) + console output. Daily log filenames in `logs/`.

### What Grok Receives Each Cycle

The prompt sent to you includes:
- Market session status (phase, session label, current ET time, whether RTH is open)
- A note that orders won't be placed outside REGULAR session
- Structured market data for each ticker (current price, volume, last 10 hourly bars)
- Strategy rules requesting JSON response in a specific format

You're expected to return:
```json
{
  "trades": [
    {
      "ticker": "EEIQ",
      "action": "BUY",
      "shares": 100,
      "entry": 8.5,
      "stop": 7.8,
      "target": 12.0,
      "confidence": 85,
      "reason": "Explosive volume + momentum continuation"
    }
  ]
}
```

## Standards Documents (Attached)

I'm attaching three standards documents that govern how I work with AI agents:

1. **operating-principles-guide-for-agents.md** — Center/edge protection, deep modules, risk assessment, mismatch surfacing, completion vocabulary, handoff requirements.
2. **layer1-research-standards.md** — Epistemic discipline, confidence derived from checkable conditions, authority order, mismatch detection.
3. **test-creation-guide-for-agents.md** — Tests from intent + regression, coverage by design decisions, no tautological tests, tests locked to design decisions.

## Compliance Gaps Identified So Far

The coding agent identified these gaps against the standards:

### 1. No tests (critical — test-creation-guide)
Zero tests exist. Every important design decision is unprotected. Key decisions needing tests:
- RiskManager rejects trades exceeding 1% risk
- Daily loss limit halts trading at 3%
- Confidence threshold gates orders at ≥70
- Stop must be below entry for long trades
- Position size capped at 50% of account
- Market hours: no orders outside RTH
- `seconds_until_next_active()` skips weekends correctly
- Daily counters reset at midnight

### 2. Prompt/config duplication (operating-principles — single source of truth)
The `STRATEGY_RULES` prompt says "Risk 1% max per trade" as prose, but actual enforcement is `MAX_RISK_PER_TRADE = 0.01` in code. If one changes, the other doesn't. Two sources of truth for the same rule.

### 3. Grok response boundary unvalidated (operating-principles — interface contracts)
Trade recommendations from Grok are raw dicts. No schema validation. If Grok returns `"shares": "lots"` or omits `entry`, the code would crash or silently misbehave inside RiskManager. This is a center element with no contract enforcement.

### 4. Center elements not documented
No explicit record of what's center vs. edge in this project.

### 5. No handoff evidence for limits of verification
Key limit: the IBKR connection, Grok API calls, and order placement cannot be verified without a live IB Gateway and real xAI key. All confidence is in static logic, not runtime integration.

## What I'd Like From You

1. **Review the code** — Is the architecture sound? Are there bugs, logic errors, or edge cases I'm missing? How does the overall flow look to you, especially the parts where you'll be receiving data and returning decisions?

2. **Review against the standards** — Do you agree with the compliance gaps listed above? Are there additional gaps I've missed? Which gaps would you prioritize fixing?

3. **The prompt you receive** — Look at how market data and strategy rules are sent to you in `ask_grok()`. Is this giving you enough context to make good trade decisions? What would you want to see that's not there? Should the prompt structure change?

4. **Risk management** — Is the `RiskManager` class sufficient? Are there risk scenarios it doesn't cover? Should we add trailing stops, position tracking, or portfolio-level risk?

5. **Strategy rules** — Review the `STRATEGY_RULES` in config.py. Are these specific enough for you to make high-quality swing trade decisions? Should we refine the rules, add more structure, or change the response format?

6. **Test priorities** — Given the standards, which design decisions would you prioritize testing first?

7. **Anything else** — Anything in the code that concerns you, or improvements you'd suggest that we haven't considered?

Looking forward to your analysis.

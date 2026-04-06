"""
Configuration module for the IBKR + Grok Swing Trading Agent.

All settings are loaded from environment variables (.env) with sensible defaults.
Edit this file to change watchlist, risk parameters, or strategy rules.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ========================== API KEYS ==========================
XAI_API_KEY: str = os.getenv("XAI_API_KEY", "")

# ========================== IBKR CONNECTION ==========================
IB_HOST: str = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT: int = int(os.getenv("IB_PORT", "4002"))       # 4002 = IB Gateway paper, 4001 = live
IB_CLIENT_ID: int = int(os.getenv("IB_CLIENT_ID", "1"))

# ========================== TRADING MODE ==========================
# *** SAFETY: Paper trading is ON by default. ***
# Set to False ONLY when you are 100% ready for real money.
PAPER_TRADING: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"

# ========================== WATCHLIST ==========================
# Easily editable – comma-separated in .env or change the default list here.
_env_watchlist = os.getenv("WATCHLIST", "")
WATCHLIST: list[str] = (
    [s.strip().upper() for s in _env_watchlist.split(",") if s.strip()]
    if _env_watchlist
    else ["EEIQ", "SGML", "UGRO", "ANET"]
)

# ========================== TIMING ==========================
LOOP_INTERVAL_MINUTES: int = int(os.getenv("LOOP_INTERVAL_MINUTES", "15"))

# ========================== SCHEDULE ==========================
# When to start calling Grok for pre-market analysis (24h format, ET).
# Agent sleeps entirely outside this window until next morning.
#   ANALYSIS_START → Grok called for analysis only (no orders)
#   9:30 AM ET     → Orders start being placed (RTH)
#   4:00 PM ET     → Agent goes to sleep until next ANALYSIS_START
ANALYSIS_START_HOUR: int = int(os.getenv("ANALYSIS_START_HOUR", "7"))  # 7 AM ET default
ANALYSIS_START_MINUTE: int = int(os.getenv("ANALYSIS_START_MINUTE", "0"))

# ========================== RISK MANAGEMENT ==========================
MAX_RISK_PER_TRADE: float = float(os.getenv("MAX_RISK_PER_TRADE", "0.01"))   # 1% of account
DAILY_LOSS_LIMIT: float = float(os.getenv("DAILY_LOSS_LIMIT", "0.03"))       # 3% daily loss → halt
ACCOUNT_SIZE: float = float(os.getenv("ACCOUNT_SIZE", "10000"))              # Paper/live balance
CONFIDENCE_THRESHOLD: int = int(os.getenv("CONFIDENCE_THRESHOLD", "70"))     # Min Grok confidence

# ========================== GROK MODEL ==========================
GROK_MODEL: str = os.getenv("GROK_MODEL", "grok-4-1-fast-reasoning")
GROK_BASE_URL: str = "https://api.x.ai/v1"
GROK_TEMPERATURE: float = 0.1
GROK_MAX_TOKENS: int = 2000

# ========================== STRATEGY PROMPT ==========================
STRATEGY_RULES: str = f"""
You are an expert swing trader targeting 5-20% moves over 3-14 days.
Watchlist: {WATCHLIST}

Risk rule (enforced in code — do not override): 
MAX_RISK_PER_TRADE = {MAX_RISK_PER_TRADE} (1% of account per trade)

Current high-conviction candidates and context:
- EEIQ: explosive volume momentum
- SGML: strong earnings catalyst + cash flow / offtake news
- UGRO: multi-day runner with news catalysts
- ANET: lower-risk AI/networking pullback play

Entry style: pullback to VWAP or recent support on elevated relative volume.
Stop: always below recent low or structure.
Target: 8-15% initial, then scale out.
Only BUY for now (no shorts).

Respond ONLY with valid JSON — never add extra text.
If no trades meet criteria, return {{"trades": []}}
"""

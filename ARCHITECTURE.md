# Architecture — Center vs Edge Elements

This document explicitly lists the center and edge elements of the IBKR + Grok Swing Trading Agent, per the operating-principles standard.

---

## Center Elements (protect — changes require explicit approval)

| Element | Why it's center | Location |
|---|---|---|
| **RiskManager rules** | Guards real money. Multiple downstream components depend on correct enforcement (per-trade risk, daily limit, confidence gate, stop validation, position cap). | `main.py` — `RiskManager` class |
| **Market hours schedule** | Determines when orders are placed vs deferred. Wrong schedule = orders at 2 AM or missed RTH. | `main.py` — `get_market_status()`, `seconds_until_next_active()` |
| **Risk limit values** | `MAX_RISK_PER_TRADE`, `DAILY_LOSS_LIMIT`, `ACCOUNT_SIZE`, `CONFIDENCE_THRESHOLD`. Single source of truth for all risk enforcement. | `config.py` |
| **Order placement logic** | Directly places orders on IBKR with real money. LimitOrder enforcement, paper/live distinction. | `main.py` — `place_order()` |
| **Grok response schema** | Contract between Grok output and RiskManager input. Invalid data here = silent misbehavior or crashes. | `models.py` — `GrokDecision`, `TradeRecommendation` |
| **IBKR connection contract** | Host, port, clientId. Wrong values = connection to wrong account or failure. | `config.py` — `IB_HOST`, `IB_PORT`, `IB_CLIENT_ID` |
| **Paper/Live mode flag** | Single boolean that separates paper money from real money. | `config.py` — `PAPER_TRADING` |

## Edge Elements (move freely — locally verifiable, independently changeable)

| Element | Why it's edge | Location |
|---|---|---|
| Prompt text and formatting | Can change without affecting risk logic or order placement. | `config.py` — `STRATEGY_RULES` prose (risk values are center, wording is edge) |
| Log formatting and rotation | No impact on trading behavior. | `utils/logger.py` |
| Grok cost estimation | Informational only. Wrong estimate has no impact on trading. | `main.py` — cost logging in `ask_grok()` |
| Market data output formatting | How bars are structured for Grok. Can change freely. | `main.py` — `get_market_data()` return dict |
| Startup banner | Console output at launch. Cosmetic. | `main.py` — `__main__` block |
| VWAP/relative volume derivation | Informational metrics for Grok. Wrong values don't affect order placement. | `main.py` — derived metrics in `get_market_data()` |
| Reconnection parameters | `MAX_RECONNECT_ATTEMPTS`, `RECONNECT_DELAY_SECONDS`. Tuning knobs, no contract impact. | `main.py` |
| Watchlist contents | Which tickers to analyze. Easily editable, no structural impact. | `config.py` — `WATCHLIST` |

## Boundary Notes

- **STRATEGY_RULES** straddles center/edge: the f-string references to `MAX_RISK_PER_TRADE` and `WATCHLIST` are center (they must stay in sync with config). The prose wording around them is edge.
- **PortfolioTracker** (stub) is currently edge but will become center once it tracks real positions and affects risk calculations.

---

*Update this document when center elements change or new ones are introduced.*

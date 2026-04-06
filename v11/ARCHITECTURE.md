# V11 Architecture — Center/Edge Map

## Center Elements (protect — changes require explicit approval)

| Element | Why | Location |
|---|---|---|
| Darvas box breakout rules | Defines when signals fire. Wrong logic = bad trades or missed signals | `core/darvas_detector.py` |
| Imbalance classification | Confirms/denies breakout quality. Wrong threshold = filter failure | `core/imbalance_classifier.py` |
| Trade execution + bracket orders | Real money. Entry + SL must be atomic | `execution/trade_manager.py` |
| Position reconciliation | Prevents orphaned positions or double entries | `execution/trade_manager.py` |
| LLM response schema | Contract between LLM output and execution. Invalid = silent misbehavior | `llm/models.py` |
| Safety limits | Daily trade cap, daily loss limit, confidence threshold | `config/live_config.py` |
| Fill tracking + SL management | Ensures positions have stops, tracks actual vs expected fills | `execution/trade_manager.py` |
| Core types | Shared data contracts across all modules | `core/types.py` |

## Edge Elements (move freely)

| Element | Why | Location |
|---|---|---|
| LLM prompt text | Wording can change without affecting signal logic or execution | `llm/prompt_templates.py` |
| LLM model choice | Swappable behind interface. Any model that returns valid JSON works | `config/live_config.py` |
| Logging format | Cosmetic | Various |
| Bar count for LLM context | How many bars to send — doesn't affect signals | `config/live_config.py` |
| Daily bar fetching | Optional enrichment for LLM. Missing = slightly less context, no crash | `live/live_engine.py` |
| CSV trade log format | Reporting only | `execution/trade_manager.py` |
| Session determination | Time-of-day classification for LLM context | `live/live_engine.py` |

## Module Boundaries

| Module | Decision Hidden | Interface |
|---|---|---|
| `DarvasDetector` | Box formation state machine, confirmation counting, width validation | `add_bar(bar) -> Optional[BreakoutSignal]` |
| `ImbalanceClassifier` | Rolling volume computation, quality filtering, trend detection | `classify(direction, window) -> Classification` |
| `GrokFilter` | HTTP client, prompt formatting, JSON parsing, retry, logging | `evaluate_signal(context) -> FilterDecision` |
| `TradeManager` | Order submission, fill tracking, commission, SL management, CSV logging | `enter_trade(...) -> bool`, `check_exit(...) -> Optional[TradeRecord]` |
| `IBKRConnection` | Connection lifecycle, reconnection, heartbeat, contract qualification | `connect()`, `get_mid_price()`, `submit_market_order()` |
| `BarAggregator` | Tick-to-bar aggregation, uptick/downtick classification | `on_price(price, now) -> Optional[Bar]` |

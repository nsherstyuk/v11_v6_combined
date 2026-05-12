# V11 Architecture — Center/Edge Map

> **2026-05-10:** Added explicit "active vs latent capability"
> distinction. The tables below are the codebase's full center/edge
> map; the "Currently active center" subsection at the top names
> the elements that are actually load-bearing for production today.
> See `docs/superpowers/reviews/2026-05-10-project-direction-review.md`
> for the assessment that drove this distinction and `CLAUDE.md` for
> the protected-paths rule.

## Currently active center (production load-bearing 2026-05-10)

The active production strategy is **XAUUSD ORB** via the V6 adapter.
Darvas, EURUSD, 4H Level Retest, and LLM-as-trade-gate are present in
the codebase but suspended or disabled-by-default. The currently
load-bearing center elements are:

| Element | Why | Location |
|---|---|---|
| V6 ORB strategy state machine | Range formation, breakout detection, stale-breakout guard | `v11/v6_orb/orb_strategy.py` |
| V6 ORB IBKR executor | Real money. Bracket placement, stuck-detection, exit lifecycle | `v11/v6_orb/ibkr_executor.py` |
| ORB adapter | Wires V6 (frozen) into the V11 multi-strategy runner; handles reconnection-class failures the frozen code doesn't | `v11/live/orb_adapter.py` |
| Bracket order semantics | Entry + SL + TP atomicity. Wrong = naked positions | `v11/v6_orb/ibkr_executor.py`, `v11/execution/trade_manager.py` |
| Position reconciliation after reconnect | Prevents drift between broker truth and internal state | `v11/execution/ibkr_connection.py`, test at `v11/tests/test_reconcile_after_reconnect.py` |
| IBKRConnection reconnect logic | Broker session continuity across Gateway restarts | `v11/execution/ibkr_connection.py` |
| Safety limits | Daily trade cap, daily loss limit | `v11/config/live_config.py` |
| Core types | Shared data contracts | `v11/core/types.py` |

The IBC + Gateway supervision layer (outside `v11/`) is also part of
the production safety surface: `~/Library/LaunchAgents/com.ibc.gateway.plist`
(KeepAlive supervisor) and `com.nick.daily-restart.plist` (01:15 EDT
deterministic reset cron driving `docs/agents/scripts/daily_restart.sh`).
See `docs/journal/2026-05-10_daily_restart_architecture.md` and
`docs/ops/IBC_TEST_PLAN.md` for the full chain.

## Center Elements (codebase capability — protect — changes require explicit approval)

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
| `RiskManager` | Combined daily loss, position tracking, per-strategy limits | `can_trade(inst, strat) -> (bool, reason)` |
| `LevelRetestEngine` | 4H level retest signal pipeline: levels + retest + SMA + volume + LLM | `on_bar(bar) -> None` |
| `MultiStrategyRunner` | Strategy registration, feed routing, shared infrastructure | `on_bar(pair, bar)`, `add_*_strategy()` |
| `InstrumentFeed` | Shared bar aggregation per instrument, routes bars to strategies | `on_price(price, now) -> Optional[Bar]`, `on_bar(bar)` |
| `OrbStrategy` (V6) | Range / breakout / stale-breakout state machine. Frozen — the V11 wrapper handles cases V6 alone doesn't (e.g. extended Gateway outages) | `v11/v6_orb/orb_strategy.py` |
| `IbkrExecutor` (V6) | V6's broker interface: bracket orders, stuck-detection, exit-on-target | `v11/v6_orb/ibkr_executor.py` |
| `OrbAdapter` | Glue between V6 (frozen) and V11's multi-strategy runner. Handles connectivity-class failures via the V11 reconnect logic. Center because it owns the live decisions about whether V6 should trade now. | `v11/live/orb_adapter.py` |

# Session: V6 ORB Adapter (Phase 5)
**Date:** 2026-04-07

## What was built
Phase 5 of V11 Multi-Strategy Portfolio: wired V6 ORB into the MultiStrategyRunner via an adapter pattern.

### New files
- **`v11/v6_orb/`** — Package containing frozen copies of V6 ORB source files with flattened imports:
  - `__init__.py` — Re-exports all V6 classes
  - `market_event.py` — Tick, Fill, RangeInfo, GapMetrics
  - `interfaces.py` — MarketContext, ExecutionEngine ABCs
  - `config.py` — V6 StrategyConfig (pure parameters, no yaml)
  - `orb_strategy.py` — ORBStrategy state machine
  - `live_context.py` — LiveMarketContext (tick subscription, range/gap calc)
  - `ibkr_executor.py` — IBKRExecutionEngine (two-phase brackets, OCA)

- **`v11/live/orb_adapter.py`** — `ORBAdapter` class that:
  - Satisfies StrategyEngine protocol (pair_name, in_trade, bar_count, strategy_name, on_bar, on_price, add_historical_bar, get_status)
  - Wraps V6's ORBStrategy + LiveMarketContext + IBKRExecutionEngine
  - Translates V11's price-tick model to V6's poll-driven model (2s throttle)
  - Handles daily orchestration (Asian range calc, gap metrics injection)
  - Reports V6 fills to V11's RiskManager (entries/exits with PnL)
  - Risk gate: blocks strategy in RANGE_READY if RiskManager disallows
  - on_bar() is a no-op (V6 is tick-driven)

- **`v11/tests/test_orb_adapter.py`** — 27 tests covering:
  1. StrategyEngine protocol compliance
  2. on_price throttle (poll_interval)
  3. Daily reset on date change
  4. Risk gate blocks only RANGE_READY state
  5. Fill callback → RiskManager (entry/exit/PnL)
  6. Trade window close → DONE_TODAY
  7. Range calculation at range_end_hour
  8. add_orb_strategy() factory method
  9. Cleanup (cancel orders, disconnect)
  10. on_bar no-op verification

### Modified files
- **`v11/live/multi_strategy_runner.py`** — Added:
  - Import of `ORBAdapter` and `V6StrategyConfig`
  - `add_orb_strategy()` factory method
  - Updated docstring (Phase 5 → ORBAdapter)

## Design decisions
1. **Copy V6 files into v11/v6_orb/ (not sys.path import)** — Makes V11 self-contained, avoids fragile path manipulation. Original V6 code in `C:\nautilus0\v6_orb_refactor\` is UNMODIFIED.

2. **ORBAdapter plugs into InstrumentFeed** — Consistent with other strategies. Gets on_price() from shared pipeline. on_bar() is no-op. A TradeManager is created for the XAUUSD feed but unused by ORB (V6 has its own execution engine).

3. **V6's LiveMarketContext manages its own tick subscription** — Calls ib.reqMktData independently. ib_insync deduplicates subscriptions for the same contract, so V11's IBKRConnection and V6's LiveMarketContext share the same Ticker.

4. **Risk gate at RANGE_READY only** — Can't intercept set_orb_brackets without modifying V6. Instead, block the strategy from seeing ticks when in RANGE_READY (pre-bracket). IDLE, ORDERS_PLACED, and IN_TRADE pass through freely.

5. **Fill callback bridges V6 → V11** — V6's IBKRExecutionEngine calls on_fill_callback. The adapter intercepts this to: (a) forward to V6 strategy, (b) report to V11 RiskManager with USD PnL.

6. **Daily orchestration in adapter** — Range calc, gap metrics, daily reset logic that V6's LiveRunner normally handles is replicated in the adapter. This keeps V6 code unmodified.

## Test results
- **190 total tests passing** (27 new + 163 existing)
- Zero regressions

## Risk assessment
| Element | Risk | Rationale |
|---|---|---|
| v11/v6_orb/ (copied V6) | Low | Frozen code, only imports changed |
| v11/live/orb_adapter.py | Low | Fresh module, well-tested |
| multi_strategy_runner.py | Low | Additive: one factory method |
| V6 original code | None | Not modified |
| CENTER modules | None | No changes |

## What was NOT done
- Did not modify TradeManager, DarvasDetector, or RetestDetector
- Did not modify original V6 code in C:\nautilus0\v6_orb_refactor\
- Did not build run_live.py entry point (Phase 7)
- Did not implement state persistence for the adapter (can be added if needed)
- Did not add trade CSV logging in adapter (V6's execution engine handles dry-run; full logging deferred to Phase 7)

## Next session should
- **Phase 6:** Write any remaining tests for new modules
- **Phase 7:** Build `run_live.py` entry point, paper trade EURUSD (Darvas + 4H) + XAUUSD (ORB)
- Consider adding state persistence to ORBAdapter (save/load strategy snapshot)
- Consider adding trade CSV logging in adapter (unified with V11 trade log)

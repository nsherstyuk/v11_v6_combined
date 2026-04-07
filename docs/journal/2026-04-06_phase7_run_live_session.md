# Session: Phase 7 — run_live.py Multi-Strategy Entry Point
**Date:** 2026-04-06

## What was built
Phase 7 of V11 Multi-Strategy Portfolio: rewrote `run_live.py` to use `MultiStrategyRunner` with all three validated strategies.

### Modified files
- **`v11/live/run_live.py`** — Complete rewrite:
  - Replaced single-strategy `V11LiveTrader` (one `InstrumentEngine` per instrument) with multi-strategy version using `MultiStrategyRunner`
  - Wires three strategies: Darvas+SMA on EURUSD, 4H Level Retest on EURUSD, V6 ORB on XAUUSD
  - Creates portfolio-level `RiskManager` (combined daily loss limit, max concurrent positions)
  - Added `XAUUSD_ORB_CONFIG` — V6 StrategyConfig with parameters from `v6_orb_refactor/live/example_live_xauusd.py`
  - Default instruments changed from [EURUSD, XAUUSD, USDJPY] to [EURUSD, XAUUSD] (USDJPY has no validated edge)
  - Added `--live` flag (dry-run is now default without any flag)
  - Added `--max-daily-loss` CLI argument
  - Strategy wiring happens after contract qualification (ORB adapter needs qualified contract)
  - Historical seeding routes through `runner.seed_historical()` instead of individual engines
  - Status logging shows portfolio-level risk + per-strategy status
  - Cleanup calls `engine.cleanup()` on ORB adapters

### New files
- **`v11/tests/test_run_live.py`** — 28 tests covering:
  1. XAUUSD_ORB_CONFIG parameters match V6 reference values
  2. INSTRUMENT_MAP only contains validated instruments (no USDJPY)
  3. Strategy wiring: correct count/types per instrument combination
  4. EURUSD strategies share one InstrumentFeed
  5. RiskManager wired with correct limits and shared with runner
  6. Historical seeding converts DataFrame to Bars and routes through runner
  7. CLI defaults (dry-run, port 4002, EURUSD+XAUUSD)
  8. --live flag overrides dry-run

### Updated files
- **`docs/PROJECT_STATUS.md`** — Updated status, test count (218), journal list
- **`docs/V11_DESIGN.md`** — Updated status line

## Design decisions
1. **Dry-run is default** — No `--dry-run` flag needed. Must explicitly pass `--live` to submit real orders. Safer for paper trading phase.

2. **USDJPY removed from defaults** — No validated edge in backtesting. Can be re-added later if a strategy is found.

3. **Strategies wired after contract qualification** — `_wire_strategies()` is called after `conn.qualify_contract()` because ORBAdapter needs the qualified IBKR contract object. Darvas/Retest don't need it, but keeping all wiring in one place is simpler.

4. **XAUUSD_ORB_CONFIG defined in run_live.py** — These are deployment parameters (not reusable across modules). Velocity threshold=168.0, RR=2.5, skip Wednesday — all from V6's example_live_xauusd.py.

5. **Cleanup delegates to engines** — ORBAdapter has a `cleanup()` method that cancels resting orders and closes positions. The trader calls it on shutdown. Darvas/Retest engines don't have cleanup (TradeManager handles it).

## Test results
- **218 total tests passing** (28 new + 190 existing)
- Zero regressions

## Risk assessment
| Element | Risk | Rationale |
|---|---|---|
| v11/live/run_live.py | Low | Edge element, entry point only, well-tested |
| v11/tests/test_run_live.py | Low | New test file |
| CENTER modules | None | No changes |
| V6 original code | None | Not modified |

## What was NOT done
- Did not paper trade (requires IBKR gateway running)
- Did not implement daily bar context for LLM (SignalContext supports it but no data source)
- Did not add economic calendar API
- Did not add state persistence for ORB adapter
- Did not add unified trade CSV logging in adapter

## Next session should
- **Paper trade** the combined system on IBKR (port 4002, `python -m v11.live.run_live`)
- Wire daily bar context for LLM (SignalContext supports it but no data source yet)
- Consider external economic calendar API integration
- Consider adding ORB adapter state persistence (save/load gap history across restarts)
- Consider unified trade CSV logging across all strategies

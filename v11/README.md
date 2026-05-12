# V11 — Multi-Strategy Trading System

> **2026-05-10 active state:** Single live strategy is **XAUUSD ORB
> via the V6 adapter** (`v11/v6_orb/`). EURUSD Darvas + 4H Level
> Retest are **suspended** pending an EURUSD data integrity audit
> (`eurusd_1m_tick.csv` was modified 2026-04-13 without documented
> provenance and prior research is invalidated). LLM-as-trade-gate
> has tested negative twice (anti-selects high-payoff days) and is
> NOT used as a price-action filter on the active path; LLM may
> still be evaluated for event/news/anomaly contexts in research.
>
> **Source of truth for current state:** the "Current state" section
> at the top of `docs/PROJECT_STATUS.md`, plus `CLAUDE.md`. The
> Darvas + LLM material in this README describes the codebase's
> capability surface, not the active live posture. Do not treat the
> Quick Start / Key Parameters tables below as the production
> configuration — they describe the legacy default-on path that has
> since been narrowed.

Hybrid trading system combining deterministic signal generation with optional LLM filtering. The codebase supports Darvas + Volume Imbalance + LLM Filter as legacy components and the V6 ORB adapter as the current production strategy.

## Architecture (codebase capability — legacy + current)

```
IBKR Live Stream → BarAggregator → RollingBuffer → DarvasDetector       (legacy, suspended)
    → On breakout: ImbalanceClassifier → LLM Filter (Grok) → TradeManager → IBKR

IBKR Live Stream → V6 ORB adapter → ORB executor → bracket orders → IBKR  (current, active)
```

## Instruments

- **XAUUSD** (Gold) — active via V6 ORB
- **EURUSD** — suspended pending data audit
- **USDJPY** — research surface, not active

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Set API key in .env (parent directory)
# XAI_API_KEY=your_key_here

# Dry run (no orders)
python -m v11.live.run_live --dry-run

# Paper trading
python -m v11.live.run_live --port 4002

# Single instrument
python -m v11.live.run_live --dry-run --instruments XAUUSD
```

## Project Structure

```
v11/
├── config/
│   ├── strategy_config.py    # Darvas params (frozen per instrument)
│   └── live_config.py        # IBKR, LLM, safety settings
├── core/
│   ├── types.py              # All data types (Bar, DarvasBox, BreakoutSignal, etc.)
│   ├── darvas_detector.py    # CENTER: Darvas box formation + breakout detection
│   └── imbalance_classifier.py  # Volume flow analysis (from v8)
├── llm/
│   ├── base.py               # LLMFilter protocol
│   ├── models.py             # CENTER: SignalContext + LLMResponse schemas
│   ├── prompt_templates.py   # Prompt text (edge)
│   └── grok_filter.py        # Grok implementation
├── execution/
│   ├── ibkr_connection.py    # IBKR connection manager
│   ├── bar_aggregator.py     # Tick → bar (from v8)
│   └── trade_manager.py      # CENTER: Trade lifecycle management
├── live/
│   ├── live_engine.py        # Per-instrument orchestration
│   └── run_live.py           # Main entry point
├── backtest/                 # (future: parameter optimization)
├── tests/
├── ARCHITECTURE.md
├── requirements.txt
└── README.md
```

## Key Parameters

| Parameter | Value | Notes |
|---|---|---|
| Darvas top_confirm_bars | 15 | 15 min without new high |
| Darvas bottom_confirm_bars | 15 | 15 min without new low |
| Darvas min_box_width_atr | 0.3 | Minimum box width |
| Darvas breakout_confirm_bars | 3 | 3 consecutive bars above/below |
| LLM confidence threshold | 75 | Minimum to approve trade |
| LLM model | grok-4-1-fast-reasoning | Swappable |
| Max daily trades | 20 | Per instrument |
| Max daily loss | $500 | Per instrument |

## Design Docs

- `docs/V11_DESIGN.md` — Full architecture and design
- `docs/PROJECT_STATUS.md` — All projects overview
- `ARCHITECTURE.md` — Center/edge map for this project

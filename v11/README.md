# V11 — Darvas Box + Volume Imbalance + LLM Filter

Hybrid trading system combining deterministic signal generation with intelligent LLM filtering.

## Architecture

```
IBKR Live Stream → BarAggregator → RollingBuffer → DarvasDetector
    → On breakout: ImbalanceClassifier → LLM Filter (Grok) → TradeManager → IBKR
```

## Instruments

- **XAUUSD** (Gold)
- **EURUSD**
- **USDJPY**

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

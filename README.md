# IBKR + Grok Swing Trading Agent

Fully automated swing-trading agent that connects to **Interactive Brokers (IBKR)**, pulls live market data, sends it to **Grok (xAI)** for intelligent trade decisions, and places orders automatically.

> **Paper trading is ON by default.** No real money is at risk until you explicitly change the configuration.

---

## Features

- **Live IBKR data** — 5-day hourly bars + real-time price/volume via `ib_async`
- **AI-powered decisions** — Grok analyzes market data and returns structured JSON trade recommendations
- **Strict risk management** — 1% max risk per trade, 3% daily loss limit, confidence threshold
- **Configurable watchlist** — Edit in `.env` or `config.py` (default: EEIQ, SGML, UGRO, ANET)
- **Automatic reconnection** — Exponential back-off retry if IBKR connection drops
- **Rotating log files** — Console + file logging in `logs/`
- **Paper-first safety** — LimitOrders only, paper mode enforced by default

---

## Prerequisites

| Requirement | Details |
|---|---|
| **Python** | 3.11 or newer |
| **IBKR Account** | Paper trading account (free at [interactivebrokers.com](https://www.interactivebrokers.com)) |
| **IB Gateway or TWS** | Running with API enabled (see setup below) |
| **xAI API Key** | Get one at [console.x.ai](https://console.x.ai) |

---

## Project Structure

```
ibkr-grok-swing-agent/
├── main.py              # Core agent loop
├── config.py            # All settings (loaded from .env)
├── .env.example         # Template for environment variables
├── .env                 # Your actual secrets (git-ignored)
├── requirements.txt     # Python dependencies
├── README.md            # This file
├── .gitignore
├── utils/
│   ├── __init__.py
│   └── logger.py        # Centralized rotating-file + console logger
└── logs/
    └── .gitkeep         # Log files written here (git-ignored)
```

---

## Quick Start

### 1. Clone and install dependencies

```bash
git clone <your-repo-url>
cd ibkr-grok-swing-agent
pip install -r requirements.txt
```

### 2. Set up environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in your **xAI API key**:

```
XAI_API_KEY=xai-your-key-here
```

Adjust `ACCOUNT_SIZE` to match your paper account balance.

### 3. Set up IB Gateway

1. Download **IB Gateway** from [IBKR](https://www.interactivebrokers.com/en/trading/ibgateway-stable.php)
2. Log in with your **paper trading** credentials
3. Go to **Configure → Settings → API → Settings**:
   - ✅ Enable ActiveX and Socket Clients
   - ✅ Socket port: **4002** (paper) or **4001** (live)
   - ✅ Allow connections from localhost only
   - ❌ Read-Only API: **uncheck** (needed to place orders)
4. Click **Apply** and **OK**

> **Port reference:**
> | Port | Application | Mode |
> |------|------------|------|
> | 4002 | IB Gateway | Paper |
> | 4001 | IB Gateway | Live |
> | 7497 | TWS | Paper |
> | 7496 | TWS | Live |

### 4. Run the agent

```bash
python main.py
```

You should see:

```
============================================================
  IBKR + Grok Swing Trading Agent
============================================================
  Mode:      PAPER
  Watchlist: EEIQ, SGML, UGRO, ANET
  Interval:  15 min
  Risk/trade: 1.0%  |  Daily limit: 3.0%
  IBKR:      127.0.0.1:4002
============================================================
```

---

## Configuration

All settings live in `.env` (secrets) and `config.py` (defaults). Key options:

| Setting | Default | Description |
|---|---|---|
| `PAPER_TRADING` | `true` | Paper mode — set `false` for live (**dangerous**) |
| `WATCHLIST` | `EEIQ,SGML,UGRO,ANET` | Comma-separated tickers |
| `LOOP_INTERVAL_MINUTES` | `15` | Minutes between each Grok analysis cycle |
| `MAX_RISK_PER_TRADE` | `0.01` | Max 1% of account risked per trade |
| `DAILY_LOSS_LIMIT` | `0.03` | 3% daily loss halts all trading |
| `ACCOUNT_SIZE` | `10000` | Your account balance |
| `CONFIDENCE_THRESHOLD` | `70` | Min Grok confidence (0–100) to execute |
| `GROK_MODEL` | `grok-4-1-fast-reasoning` | Grok model to use |

---

## How It Works

```
┌─────────────┐     ┌──────────┐     ┌───────────┐     ┌──────────┐
│  IB Gateway │────▶│ Pull Data│────▶│ Ask Grok  │────▶│ Risk Mgr │
│  (IBKR API) │     │ (bars +  │     │ (JSON rec)│     │ (validate│
│             │◀────│  quotes) │     │           │     │  + gate) │
│ Place Order │     └──────────┘     └───────────┘     └────┬─────┘
└─────────────┘                                             │
       ▲                                                    │ PASS
       └────────────────────────────────────────────────────┘
```

1. **Connect** to IBKR with automatic retry (up to 5 attempts)
2. **Pull data** for each watchlist ticker: 5-day hourly candles + live price/volume
3. **Send to Grok** as structured JSON with strategy rules
4. **Parse response** — Grok returns `{"trades": [...]}` with entry/stop/target/confidence
5. **Risk check** each trade: confidence ≥ 70, risk ≤ 1% of account, daily limit not hit
6. **Place order** via LimitOrder on IBKR
7. **Sleep** for the configured interval, then repeat

---

## Risk Management

The `RiskManager` class enforces multiple safety layers:

- **Confidence threshold** — Trades below 70% confidence are rejected
- **Per-trade risk cap** — `|entry - stop| × shares` must be ≤ 1% of account
- **Position size cap** — No single position can exceed 50% of account value
- **Daily loss limit** — After 3% cumulative committed risk in a day, all trading halts
- **Stop validation** — Stop must be below entry for long trades
- **Daily reset** — Counters reset at midnight for the next trading day

---

## Safety Notes

> ⚠️ **This software places real orders when paper mode is off. Use at your own risk.**

- **Always start with paper trading.** Test for days/weeks before considering live.
- **Monitor the agent.** Don't leave it unattended with real money.
- **LimitOrders only** — Even in live mode, the agent uses limit orders to avoid slippage.
- **API key security** — Never commit `.env` to version control.
- **Market hours** — The agent runs 24/7 but IBKR only fills during market hours (RTH data is used).
- **No short selling** — Current strategy is long-only.
- **Kill switch** — Press `Ctrl+C` to gracefully shut down.

---

## Logs

Log files are written to `logs/` with daily rotation (max 5 MB per file, 5 backups):

```
logs/swing_agent_20260330.log
```

Both console and file output include timestamps, log level, module, and line number.

---

## Troubleshooting

| Issue | Solution |
|---|---|
| `ConnectionRefusedError` | IB Gateway is not running or API port is wrong |
| `No historical bars returned` | Ticker may not have enough data, or market is closed |
| `Grok returned invalid JSON` | Rare — the agent retries next cycle automatically |
| `DAILY LOSS LIMIT reached` | Safety halt — resets next trading day |
| `Risk too high — skip` | Reduce shares or widen stop in Grok's strategy rules |

---

## License

This project is provided as-is for educational purposes. Use at your own risk. The authors are not responsible for any financial losses.

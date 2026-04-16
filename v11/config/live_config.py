"""
V11 Live Trading Configuration.

Separates broker/environment settings from pure strategy parameters.
Strategy parameters are in config/strategy_config.py.

Adapted from v8 LiveConfig with additions for:
- Multi-instrument support
- LLM filter settings
- Confidence threshold
"""
import math
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import List


@dataclass
class InstrumentConfig:
    """IBKR contract specification for a single instrument."""
    symbol: str = "XAUUSD"
    exchange: str = "SMART"
    sec_type: str = "CMDTY"
    currency: str = "USD"
    tick_size: float = 0.01
    market_data_type: int = 1       # 1=live, 2=frozen, 3=delayed, 4=delayed-frozen
    quantity: float = 1.0           # lots per trade

    @cached_property
    def pair_name(self) -> str:
        if self.sec_type == "CMDTY":
            return self.symbol.upper()
        return f"{self.symbol}{self.currency}".upper()

    @cached_property
    def price_fmt(self) -> str:
        if self.tick_size >= 1:
            return ".0f"
        decimals = max(0, -int(math.floor(math.log10(self.tick_size))))
        return f".{decimals}f"

    def price_pnl_to_usd(self, price_pnl: float, exit_price: float) -> float:
        """Convert raw price-unit PnL to USD.

        For USD-quoted pairs (EURUSD, GBPUSD, XAUUSD): usd = price_pnl * qty
        For JPY-quoted pairs (USDJPY): usd = price_pnl * qty / rate
        """
        dollar = price_pnl * self.quantity
        if self.currency == "JPY" and exit_price != 0:
            dollar /= exit_price
        return dollar


# ── Pre-built instrument configs ────────────────────────────────────────────

XAUUSD_INSTRUMENT = InstrumentConfig(
    symbol="XAUUSD", exchange="SMART", sec_type="CMDTY",
    currency="USD", tick_size=0.01, quantity=1.0,
)

EURUSD_INSTRUMENT = InstrumentConfig(
    symbol="EUR", exchange="IDEALPRO", sec_type="CASH",
    currency="USD", tick_size=0.00005, quantity=20000.0,
)

USDJPY_INSTRUMENT = InstrumentConfig(
    symbol="USD", exchange="IDEALPRO", sec_type="CASH",
    currency="JPY", tick_size=0.005, quantity=20000.0,
)


@dataclass
class LiveConfig:
    """Configuration for V11 live trading."""

    # IBKR connection
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 4002           # 7497=TWS paper, 4002=gateway paper
    ibkr_client_id: int = 11        # different from v8 (10) and swing agent

    # Instruments to trade (multi-instrument from day one)
    instruments: List[InstrumentConfig] = field(default_factory=lambda: [
        XAUUSD_INSTRUMENT,
        EURUSD_INSTRUMENT,
        USDJPY_INSTRUMENT,
    ])

    # Rolling buffer
    buffer_size: int = 500          # bars to maintain per instrument
    bar_seconds: int = 60           # bar aggregation period

    # LLM filter settings
    llm_model: str = "deepseek/deepseek-chat-v3-0324"
    llm_base_url: str = "https://openrouter.ai/api/v1"  # OpenAI-compatible endpoint (xAI, OpenRouter, etc.)
    llm_confidence_threshold: int = 75      # default confidence threshold (Darvas/Retest)
    orb_confidence_threshold: int = 55       # ORB threshold — lower because mechanical edge exists
    llm_timeout_seconds: float = 10.0       # max wait for LLM response
    llm_bars_context: int = 200             # 1-min bars to send to LLM
    llm_daily_bars_context: int = 30        # daily bars to send to LLM

    # Safety limits (CENTER — changes require approval)
    max_daily_trades: int = 20              # per instrument
    max_daily_loss: float = 500.0           # USD, per instrument
    max_concurrent_positions: int = 3       # across all instruments
    max_entry_drift_atr: float = 0.5        # max price drift (in ATR) during LLM latency
                                            # if current price moved > 0.5 ATR from
                                            # breakout_price by the time LLM responds,
                                            # abort the trade regardless of approval

    # Dry run mode
    dry_run: bool = True

    # Reconnection safety
    auto_close_orphans: bool = False  # auto-close orphaned broker positions on reconnect

    # Logging
    grok_log_dir: str = "grok_logs"

    # Tick logging for replay data capture
    tick_logging: bool = True
    tick_log_dir: Path = field(default_factory=lambda: Path("data/ticks"))

    def validate(self) -> None:
        """Sanity check config values."""
        assert self.buffer_size >= 100, \
            f"buffer_size must be >= 100, got {self.buffer_size}"
        assert self.bar_seconds in (60, 300, 900), \
            f"bar_seconds must be 60, 300, or 900, got {self.bar_seconds}"
        assert self.max_daily_trades >= 1
        assert self.max_daily_loss > 0
        assert 0 <= self.llm_confidence_threshold <= 100
        assert self.llm_timeout_seconds > 0
        assert len(self.instruments) >= 1, "At least one instrument required"

"""Replay simulator configuration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

VALID_LLM_MODES = {"passthrough", "live", "cached"}


@dataclass
class ReplayConfig:
    """Configuration for a historical replay run."""

    # Required
    instruments: list[str]
    start_date: str              # "YYYY-MM-DD"
    end_date: str                # "YYYY-MM-DD"

    # LLM mode
    llm_mode: str = "passthrough"    # "passthrough" | "live" | "cached"
    grok_api_key: str = ""
    grok_model: str = "deepseek/deepseek-chat-v3-0324"
    llm_base_url: str = "https://openrouter.ai/api/v1"  # OpenAI-compatible endpoint (xAI, OpenRouter, etc.)
    llm_cache_path: str = "replay_llm_cache.json"
    llm_confidence_threshold: int = 75

    # Execution simulation
    slippage_pips: float = 0.5
    commission_per_lot: float = 2.0

    # Risk manager
    max_daily_loss: float = 500.0
    max_daily_trades: int = 10
    max_concurrent_positions: int = 3

    # Seeding
    seed_bars: int = 500             # bars to seed before replay starts

    # Output
    output_dir: str = "v11/replay/results"
    event_verbosity: str = "normal"  # "quiet" | "normal" | "verbose"

    # Dry run (always True for replay — no real orders)
    dry_run: bool = True

    def validate(self) -> None:
        """Validate config values. Raises ValueError on problems."""
        if not self.instruments:
            raise ValueError("instruments must not be empty")
        if self.llm_mode not in VALID_LLM_MODES:
            raise ValueError(
                f"llm_mode must be one of {VALID_LLM_MODES}, got '{self.llm_mode}'")
        start = datetime.strptime(self.start_date, "%Y-%m-%d")
        end = datetime.strptime(self.end_date, "%Y-%m-%d")
        if start > end:
            raise ValueError(
                f"start_date ({self.start_date}) must be before end_date ({self.end_date})")

    @property
    def start_dt(self) -> datetime:
        return datetime.strptime(self.start_date, "%Y-%m-%d")

    @property
    def end_dt(self) -> datetime:
        return datetime.strptime(self.end_date, "%Y-%m-%d")

"""Tests for tick logging config fields."""
import pytest
from pathlib import Path
from v11.config.live_config import LiveConfig
from v11.replay.config import TickReplayConfig


def test_live_config_tick_logging_defaults():
    cfg = LiveConfig()
    assert cfg.tick_logging is True
    assert isinstance(cfg.tick_log_dir, Path)
    assert cfg.tick_log_dir == Path("data/ticks")


def test_live_config_tick_logging_disabled():
    cfg = LiveConfig(tick_logging=False)
    assert cfg.tick_logging is False


def test_live_config_tick_log_dir_custom():
    cfg = LiveConfig(tick_log_dir=Path("/custom/path"))
    assert cfg.tick_log_dir == Path("/custom/path")


def test_tick_replay_config_defaults():
    cfg = TickReplayConfig(instruments=["EURUSD"], start_date="2026-04-01", end_date="2026-04-15")
    assert cfg.tick_dir == "data/ticks"
    assert cfg.llm_mode == "passthrough"
    assert cfg.llm_confidence_threshold == 75


def test_tick_replay_config_validates_dates():
    with pytest.raises(ValueError, match="start_date.*before"):
        TickReplayConfig(
            instruments=["EURUSD"],
            start_date="2026-04-15",
            end_date="2026-04-01",
        ).validate()

"""Tests for ReplayConfig — validation and defaults."""
import pytest
from v11.replay.config import ReplayConfig


class TestReplayConfigDefaults:
    def test_default_llm_mode_is_passthrough(self):
        cfg = ReplayConfig(instruments=["EURUSD"], start_date="2025-01-01", end_date="2025-03-31")
        assert cfg.llm_mode == "passthrough"

    def test_default_slippage(self):
        cfg = ReplayConfig(instruments=["EURUSD"], start_date="2025-01-01", end_date="2025-03-31")
        assert cfg.slippage_pips == 0.5

    def test_default_dry_run_is_true(self):
        cfg = ReplayConfig(instruments=["EURUSD"], start_date="2025-01-01", end_date="2025-03-31")
        assert cfg.dry_run is True


class TestReplayConfigValidation:
    def test_validate_rejects_empty_instruments(self):
        cfg = ReplayConfig(instruments=[], start_date="2025-01-01", end_date="2025-03-31")
        with pytest.raises(ValueError, match="instruments"):
            cfg.validate()

    def test_validate_rejects_invalid_llm_mode(self):
        cfg = ReplayConfig(instruments=["EURUSD"], start_date="2025-01-01", end_date="2025-03-31", llm_mode="gpt5")
        with pytest.raises(ValueError, match="llm_mode"):
            cfg.validate()

    def test_validate_rejects_start_after_end(self):
        cfg = ReplayConfig(instruments=["EURUSD"], start_date="2025-06-01", end_date="2025-03-31")
        with pytest.raises(ValueError, match="start_date"):
            cfg.validate()

    def test_validate_passes_valid_config(self):
        cfg = ReplayConfig(instruments=["EURUSD"], start_date="2025-01-01", end_date="2025-03-31")
        cfg.validate()  # should not raise

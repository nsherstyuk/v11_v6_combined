"""Integration tests for ReplayRunner — full pipeline on synthetic data."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from v11.core.types import Bar
from v11.replay.config import ReplayConfig
from v11.replay.replay_runner import ReplayRunner


def _make_bars(n=600, start_price=1.1000, start_time=None):
    """Generate n synthetic 1-min bars with a consolidation pattern.

    Creates bars with slight variation to exercise strategy engines
    without necessarily triggering a full Darvas box (that depends
    on parameter tuning). The goal is pipeline smoke-testing.
    """
    if start_time is None:
        # Monday 8:00 UTC (London session)
        start_time = datetime(2025, 1, 6, 8, 0, 0)

    bars = []
    price = start_price
    for i in range(n):
        ts = start_time + timedelta(minutes=i)
        # Oscillate around start_price with small moves
        delta = 0.0001 * ((i % 7) - 3)
        price = start_price + delta

        bars.append(Bar(
            timestamp=ts,
            open=price - 0.00005,
            high=price + 0.0002,
            low=price - 0.0003,
            close=price,
            tick_count=50,
            buy_volume=60.0 if i % 3 == 0 else 40.0,
            sell_volume=40.0 if i % 3 == 0 else 50.0,
        ))
    return bars


class TestReplayRunnerBasic:
    def test_replay_completes_without_error(self, tmp_path):
        """Smoke test: replay processes bars without crashing."""
        config = ReplayConfig(
            instruments=["EURUSD"],
            start_date="2025-01-06",
            end_date="2025-01-06",
            llm_mode="passthrough",
            output_dir=str(tmp_path / "results"),
            event_verbosity="quiet",
        )
        runner = ReplayRunner(config)
        bars_by_instrument = {"EURUSD": _make_bars(600)}

        result = asyncio.run(runner.run(bars_by_instrument))

        assert result["bars_processed"]["EURUSD"] > 0
        assert "error" not in result

    def test_replay_produces_event_log(self, tmp_path):
        """Event log file is created with at least one event."""
        config = ReplayConfig(
            instruments=["EURUSD"],
            start_date="2025-01-06",
            end_date="2025-01-06",
            llm_mode="passthrough",
            output_dir=str(tmp_path / "results"),
            event_verbosity="quiet",
        )
        runner = ReplayRunner(config)
        bars_by_instrument = {"EURUSD": _make_bars(600)}

        asyncio.run(runner.run(bars_by_instrument))

        event_file = tmp_path / "results" / "replay_events.jsonl"
        assert event_file.exists()
        content = event_file.read_text()
        assert len(content.strip()) > 0

    def test_replay_seeds_initial_bars(self, tmp_path):
        """First seed_bars are used for seeding, not replay."""
        config = ReplayConfig(
            instruments=["EURUSD"],
            start_date="2025-01-06",
            end_date="2025-01-06",
            llm_mode="passthrough",
            output_dir=str(tmp_path / "results"),
            event_verbosity="quiet",
            seed_bars=100,
        )
        runner = ReplayRunner(config)
        all_bars = _make_bars(200)
        bars_by_instrument = {"EURUSD": all_bars}

        result = asyncio.run(runner.run(bars_by_instrument))

        # 200 total bars, 100 seeded = 100 replayed
        assert result["bars_processed"]["EURUSD"] == 100

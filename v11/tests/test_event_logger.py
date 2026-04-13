"""Tests for EventLogger — structured replay event logging."""
import json
from pathlib import Path

import pytest

from v11.replay.event_logger import EventLogger


class TestEventEmission:
    def test_emit_writes_to_file(self, tmp_path):
        path = tmp_path / "events.jsonl"
        logger = EventLogger(output_path=str(path), verbosity="quiet")

        logger.emit("BOX_FORMED", strategy="DARVAS", instrument="EURUSD",
                     timestamp="2025-01-15T14:32:00",
                     data={"top": 1.0892, "bottom": 1.0875})
        logger.close()

        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event"] == "BOX_FORMED"
        assert event["strategy"] == "DARVAS"
        assert event["instrument"] == "EURUSD"
        assert event["data"]["top"] == 1.0892

    def test_multiple_events_append(self, tmp_path):
        path = tmp_path / "events.jsonl"
        logger = EventLogger(output_path=str(path), verbosity="quiet")

        logger.emit("BOX_FORMED", strategy="DARVAS", instrument="EURUSD",
                     timestamp="2025-01-15T14:32:00", data={})
        logger.emit("BREAKOUT_DETECTED", strategy="DARVAS", instrument="EURUSD",
                     timestamp="2025-01-15T14:47:00", data={})
        logger.close()

        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2


class TestEventCounting:
    def test_event_counts_tracked(self, tmp_path):
        path = tmp_path / "events.jsonl"
        logger = EventLogger(output_path=str(path), verbosity="quiet")

        logger.emit("BOX_FORMED", strategy="DARVAS", instrument="EURUSD",
                     timestamp="t1", data={})
        logger.emit("BOX_FORMED", strategy="DARVAS", instrument="EURUSD",
                     timestamp="t2", data={})
        logger.emit("TRADE_ENTERED", strategy="DARVAS", instrument="EURUSD",
                     timestamp="t3", data={})

        counts = logger.get_counts()
        assert counts["BOX_FORMED"] == 2
        assert counts["TRADE_ENTERED"] == 1


class TestTradeRecordCollection:
    def test_trade_exited_events_collected(self, tmp_path):
        path = tmp_path / "events.jsonl"
        logger = EventLogger(output_path=str(path), verbosity="quiet")

        trade_data = {
            "instrument": "EURUSD", "strategy": "DARVAS",
            "direction": "long", "entry_price": 1.1050,
            "exit_price": 1.1100, "pnl": 100.0,
            "exit_reason": "TARGET", "hold_bars": 45,
            "llm_confidence": 85,
        }
        logger.emit("TRADE_EXITED", strategy="DARVAS", instrument="EURUSD",
                     timestamp="2025-01-15T15:30:00", data=trade_data)

        assert len(logger.trade_records) == 1
        assert logger.trade_records[0]["pnl"] == 100.0

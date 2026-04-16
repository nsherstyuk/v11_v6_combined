"""Tests for TickLogger."""
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from v11.replay.tick_logger import TickLogger


def _ts(y, m, d, h=14, mi=30, s=0, us=0):
    return datetime(y, m, d, h, mi, s, us, tzinfo=timezone.utc)


class TestTickLoggerHeader:
    def test_creates_directory_and_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TickLogger(Path(tmp))
            logger.record("EURUSD", _ts(2026, 4, 15), 1.12345678,
                          bid=1.12340000, ask=1.12350000,
                          last=None, bid_size=None, ask_size=None, last_size=None)
            logger.close()

            path = Path(tmp) / "EURUSD" / "2026-04-15.csv"
            assert path.exists()
            lines = path.read_text().splitlines()
            assert lines[0] == "timestamp,mid,bid,ask,last,bid_size,ask_size,last_size"

    def test_header_written_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TickLogger(Path(tmp))
            ts = _ts(2026, 4, 15)
            for _ in range(5):
                logger.record("EURUSD", ts, 1.12345678, None, None, None, None, None, None)
            logger.close()

            path = Path(tmp) / "EURUSD" / "2026-04-15.csv"
            lines = path.read_text().splitlines()
            header_count = sum(1 for l in lines if l.startswith("timestamp"))
            assert header_count == 1


class TestTickLoggerRowFormat:
    def test_mid_written_to_8dp(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TickLogger(Path(tmp))
            logger.record("EURUSD", _ts(2026, 4, 15), 1.12345678,
                          bid=1.12340000, ask=1.12350000,
                          last=None, bid_size=None, ask_size=None, last_size=None)
            logger.close()

            lines = (Path(tmp) / "EURUSD" / "2026-04-15.csv").read_text().splitlines()
            row = lines[1]
            assert "1.12345678" in row
            assert "1.12340000" in row

    def test_nan_written_as_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TickLogger(Path(tmp))
            logger.record("EURUSD", _ts(2026, 4, 15), 1.1234,
                          bid=float("nan"), ask=0.0,
                          last=None, bid_size=None, ask_size=None, last_size=None)
            logger.close()

            lines = (Path(tmp) / "EURUSD" / "2026-04-15.csv").read_text().splitlines()
            row = lines[1]
            fields = row.split(",")
            # bid (index 2) and ask (index 3) should be blank
            assert fields[2] == ""   # nan → blank
            assert fields[3] == ""   # 0 → blank

    def test_timestamp_is_iso8601_with_microseconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TickLogger(Path(tmp))
            ts = datetime(2026, 4, 15, 14, 30, 0, 123456, tzinfo=timezone.utc)
            logger.record("EURUSD", ts, 1.1234, None, None, None, None, None, None)
            logger.close()

            lines = (Path(tmp) / "EURUSD" / "2026-04-15.csv").read_text().splitlines()
            row = lines[1]
            assert "2026-04-15T14:30:00" in row
            assert "123456" in row


class TestTickLoggerDateRollover:
    def test_new_file_on_date_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TickLogger(Path(tmp))
            logger.record("EURUSD", _ts(2026, 4, 15, 23, 59), 1.1, None, None, None, None, None, None)
            logger.record("EURUSD", _ts(2026, 4, 16, 0, 1), 1.2, None, None, None, None, None, None)
            logger.close()

            assert (Path(tmp) / "EURUSD" / "2026-04-15.csv").exists()
            assert (Path(tmp) / "EURUSD" / "2026-04-16.csv").exists()

    def test_each_file_has_own_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TickLogger(Path(tmp))
            logger.record("EURUSD", _ts(2026, 4, 15, 23, 59), 1.1, None, None, None, None, None, None)
            logger.record("EURUSD", _ts(2026, 4, 16, 0, 1), 1.2, None, None, None, None, None, None)
            logger.close()

            for day in ["2026-04-15", "2026-04-16"]:
                lines = (Path(tmp) / "EURUSD" / f"{day}.csv").read_text().splitlines()
                assert lines[0] == "timestamp,mid,bid,ask,last,bid_size,ask_size,last_size"


class TestTickLoggerErrorHandling:
    def test_filesystem_error_does_not_propagate(self, monkeypatch):
        """A write error must not raise — caller should never crash."""
        with tempfile.TemporaryDirectory() as tmp:
            logger = TickLogger(Path(tmp))
            # First call opens and writes the file successfully
            logger.record("EURUSD", _ts(2026, 4, 15), 1.1, None, None, None, None, None, None)

            # Sabotage the open file handle's write method
            handle, _ = logger._handles["EURUSD"]
            monkeypatch.setattr(handle, "write", lambda s: (_ for _ in ()).throw(OSError("disk full")))

            # Should not raise
            logger.record("EURUSD", _ts(2026, 4, 15), 1.2, None, None, None, None, None, None)
            logger.close()


class TestTickLoggerMultipleInstruments:
    def test_separate_files_per_instrument(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TickLogger(Path(tmp))
            ts = _ts(2026, 4, 15)
            logger.record("EURUSD", ts, 1.1, None, None, None, None, None, None)
            logger.record("XAUUSD", ts, 2300.0, None, None, None, None, None, None)
            logger.close()

            assert (Path(tmp) / "EURUSD" / "2026-04-15.csv").exists()
            assert (Path(tmp) / "XAUUSD" / "2026-04-15.csv").exists()

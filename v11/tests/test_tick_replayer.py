"""Tests for load_ticks() and TickReplayer."""
import asyncio
import csv
import gzip
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from v11.replay.tick_replayer import load_ticks


# ─── Helpers ────────────────────────────────────────────────────────────────

def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "timestamp", "mid", "bid", "ask", "last",
            "bid_size", "ask_size", "last_size",
        ])
        w.writeheader()
        w.writerows(rows)


def _row(ts: datetime, mid: float, bid="", ask="", last="",
         bid_size="", ask_size="", last_size="") -> dict:
    return {
        "timestamp": ts.isoformat(),
        "mid": f"{mid:.8f}",
        "bid": bid, "ask": ask, "last": last,
        "bid_size": bid_size, "ask_size": ask_size, "last_size": last_size,
    }


def _ts(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2026, 4, 15, h, m, s, tzinfo=timezone.utc)


# ─── load_ticks tests ────────────────────────────────────────────────────────

class TestLoadTicksBasic:
    def test_yields_tuples_from_csv(self, tmp_path):
        csv_path = tmp_path / "EURUSD" / "2026-04-15.csv"
        _write_csv(csv_path, [
            _row(_ts(14, 30), 1.12345678),
            _row(_ts(14, 31), 1.12346000),
        ])

        ticks = list(load_ticks(tmp_path, ["EURUSD"],
                                date(2026, 4, 15), date(2026, 4, 15)))
        assert len(ticks) == 2
        ts, pair, mid = ticks[0][:3]
        assert pair == "EURUSD"
        assert abs(mid - 1.12345678) < 1e-9

    def test_blank_bid_becomes_none(self, tmp_path):
        csv_path = tmp_path / "EURUSD" / "2026-04-15.csv"
        _write_csv(csv_path, [_row(_ts(14, 30), 1.1234, bid="", ask="")])

        ticks = list(load_ticks(tmp_path, ["EURUSD"],
                                date(2026, 4, 15), date(2026, 4, 15)))
        assert len(ticks) == 1
        _ts_out, pair, mid, bid, ask, last, bid_s, ask_s, last_s = ticks[0]
        assert bid is None
        assert ask is None

    def test_missing_file_skipped_with_warning(self, tmp_path, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="tick_replayer"):
            ticks = list(load_ticks(tmp_path, ["EURUSD"],
                                    date(2026, 4, 15), date(2026, 4, 15)))
        assert len(ticks) == 0
        assert any("No tick file" in r.message for r in caplog.records)

    def test_row_without_mid_is_skipped(self, tmp_path):
        csv_path = tmp_path / "EURUSD" / "2026-04-15.csv"
        _write_csv(csv_path, [
            _row(_ts(14, 30), 1.1234),
            {"timestamp": _ts(14, 31).isoformat(), "mid": "",
             "bid": "", "ask": "", "last": "", "bid_size": "", "ask_size": "", "last_size": ""},
        ])
        ticks = list(load_ticks(tmp_path, ["EURUSD"],
                                date(2026, 4, 15), date(2026, 4, 15)))
        assert len(ticks) == 1


class TestLoadTicksMergeSort:
    def test_two_instruments_merged_in_order(self, tmp_path):
        eu_path = tmp_path / "EURUSD" / "2026-04-15.csv"
        xau_path = tmp_path / "XAUUSD" / "2026-04-15.csv"

        # EURUSD at :00, :02; XAUUSD at :01, :03
        _write_csv(eu_path, [
            _row(_ts(14, 0), 1.1),
            _row(_ts(14, 2), 1.2),
        ])
        _write_csv(xau_path, [
            _row(_ts(14, 1), 2300.0),
            _row(_ts(14, 3), 2301.0),
        ])

        ticks = list(load_ticks(tmp_path, ["EURUSD", "XAUUSD"],
                                date(2026, 4, 15), date(2026, 4, 15)))
        pairs = [t[1] for t in ticks]
        assert pairs == ["EURUSD", "XAUUSD", "EURUSD", "XAUUSD"]
        timestamps = [t[0] for t in ticks]
        assert timestamps == sorted(timestamps)

    def test_multi_day_range(self, tmp_path):
        for d in [date(2026, 4, 15), date(2026, 4, 16)]:
            path = tmp_path / "EURUSD" / f"{d}.csv"
            _write_csv(path, [_row(
                datetime(d.year, d.month, d.day, 14, 0, tzinfo=timezone.utc),
                1.1,
            )])

        ticks = list(load_ticks(tmp_path, ["EURUSD"],
                                date(2026, 4, 15), date(2026, 4, 16)))
        assert len(ticks) == 2
        assert ticks[0][0].date() == date(2026, 4, 15)
        assert ticks[1][0].date() == date(2026, 4, 16)


class TestLoadTicksGzip:
    def test_reads_gz_file(self, tmp_path):
        csv_path = tmp_path / "EURUSD" / "2026-04-15.csv"
        gz_path = tmp_path / "EURUSD" / "2026-04-15.csv.gz"
        csv_path.parent.mkdir(parents=True, exist_ok=True)

        _write_csv(csv_path, [_row(_ts(14, 30), 1.1234)])

        with open(csv_path, "rb") as f_in:
            with gzip.open(gz_path, "wb") as f_out:
                f_out.write(f_in.read())
        csv_path.unlink()  # only gz exists

        ticks = list(load_ticks(tmp_path, ["EURUSD"],
                                date(2026, 4, 15), date(2026, 4, 15)))
        assert len(ticks) == 1

    def test_gz_preferred_over_csv(self, tmp_path):
        """When both .csv and .csv.gz exist, .gz takes precedence."""
        csv_path = tmp_path / "EURUSD" / "2026-04-15.csv"
        gz_path = tmp_path / "EURUSD" / "2026-04-15.csv.gz"

        # CSV has 1 row, GZ has 2 rows
        _write_csv(csv_path, [_row(_ts(14, 30), 1.1)])

        tmp_csv = tmp_path / "_tmp.csv"
        _write_csv(tmp_csv, [_row(_ts(14, 30), 2.2), _row(_ts(14, 31), 2.3)])
        with open(tmp_csv, "rb") as f_in:
            with gzip.open(gz_path, "wb") as f_out:
                f_out.write(f_in.read())

        ticks = list(load_ticks(tmp_path, ["EURUSD"],
                                date(2026, 4, 15), date(2026, 4, 15)))
        assert len(ticks) == 2

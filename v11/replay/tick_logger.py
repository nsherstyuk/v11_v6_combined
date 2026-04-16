"""TickLogger — Records raw IBKR price ticks to CSV for later replay.

File layout:
    data/ticks/{PAIR}/{YYYY-MM-DD}.csv

Schema:
    timestamp,mid,bid,ask,last,bid_size,ask_size,last_size

All float fields formatted to 8 decimal places.
NaN, 0, and None values are written as blank fields.
Files are line-buffered so each row is flushed to disk immediately.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime
from pathlib import Path
from typing import IO

log = logging.getLogger("tick_logger")

_HEADER = "timestamp,mid,bid,ask,last,bid_size,ask_size,last_size\n"


def _fmt(v) -> str:
    """Format a float value. Returns blank string for NaN, 0, or None."""
    if v is None:
        return ""
    if isinstance(v, float) and (math.isnan(v) or v == 0.0):
        return ""
    try:
        return f"{float(v):.8f}"
    except (TypeError, ValueError):
        return ""


class TickLogger:
    """Appends raw price tick rows to per-instrument, per-day CSV files.

    Thread-safety: not thread-safe. Call only from the live trading loop.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)
        # pair -> (file_handle, current_date)
        self._handles: dict[str, tuple[IO[str], date]] = {}

    def record(
        self,
        pair: str,
        ts: datetime,
        mid: float,
        bid,
        ask,
        last,
        bid_size,
        ask_size,
        last_size,
    ) -> None:
        """Append one tick row. Silently swallows all I/O errors."""
        try:
            ts_date = ts.date()
            handle, current_date = self._handles.get(pair, (None, None))

            if handle is None or ts_date != current_date:
                if handle is not None:
                    handle.close()
                handle = self._open_file(pair, ts_date)
                self._handles[pair] = (handle, ts_date)

            row = (
                f"{ts.isoformat()},"
                f"{_fmt(mid)},{_fmt(bid)},{_fmt(ask)},{_fmt(last)},"
                f"{_fmt(bid_size)},{_fmt(ask_size)},{_fmt(last_size)}\n"
            )
            handle.write(row)
        except Exception as exc:
            log.warning("TickLogger.record failed for %s: %s", pair, exc)

    def _open_file(self, pair: str, d: date) -> IO[str]:
        pair_dir = self._base_dir / pair
        pair_dir.mkdir(parents=True, exist_ok=True)
        path = pair_dir / f"{d}.csv"
        if path.exists():
            return open(path, "a", buffering=1, encoding="utf-8")
        f = open(path, "w", buffering=1, encoding="utf-8")
        f.write(_HEADER)
        return f

    def close(self) -> None:
        """Flush and close all open file handles."""
        for handle, _ in self._handles.values():
            try:
                handle.close()
            except Exception:
                pass
        self._handles.clear()

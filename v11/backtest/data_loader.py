"""
Data Loader — Load historical 1-minute bar CSVs into Bar objects.

Reads from nautilus0/data/1m_csv/ format:
  timestamp, open, high, low, close, tick_count, avg_spread, max_spread,
  vol_imbalance, buy_volume, sell_volume, total_volume, buy_ratio

Some files (EURUSD) have an extra leading index column and timezone-aware timestamps.
This loader handles both variants.

Interface:
    load_bars(csv_path) -> List[Bar]
    load_bars_daterange(csv_path, start, end) -> List[Bar]
    get_available_instruments(data_dir) -> List[str]
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

from ..core.types import Bar


# ── CSV column mapping ───────────────────────────────────────────────────────

REQUIRED_COLUMNS = {"timestamp", "open", "high", "low", "close",
                    "tick_count", "buy_volume", "sell_volume"}

DATA_DIR = Path(r"C:\nautilus0\data\1m_csv")

INSTRUMENT_FILE_MAP = {
    "XAUUSD": "xauusd_1m_tick.csv",
    "EURUSD": "eurusd_1m_tick.csv",
    "USDJPY": "usdjpy_1m_tick.csv",
    "GBPUSD": "gbpusd_1m_tick.csv",
    "AUDUSD": "audusd_1m_tick.csv",
    "NZDUSD": "nzdusd_1m_tick.csv",
    "USDCAD": "usdcad_1m_tick.csv",
    "USDCHF": "usdchf_1m_tick.csv",
}


def get_available_instruments(data_dir: Path = DATA_DIR) -> List[str]:
    """Return list of instrument names that have CSV files in data_dir."""
    available = []
    for instrument, filename in INSTRUMENT_FILE_MAP.items():
        if (data_dir / filename).exists():
            available.append(instrument)
    return sorted(available)


def load_bars(csv_path: str | Path,
              start: Optional[datetime] = None,
              end: Optional[datetime] = None) -> List[Bar]:
    """Load 1-min bar CSV into a list of Bar objects.

    Args:
        csv_path: Path to the CSV file.
        start: Optional start datetime filter (inclusive).
        end: Optional end datetime filter (inclusive).

    Returns:
        List of Bar objects sorted by timestamp.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    df = pd.read_csv(csv_path, low_memory=False)

    # Handle extra index column (EURUSD format has unnamed first column)
    if df.columns[0] == "Unnamed: 0" or df.columns[0].isdigit():
        df = df.drop(columns=[df.columns[0]])
    # Also handle if first column is just a number string
    first_col = df.columns[0]
    if first_col not in REQUIRED_COLUMNS and first_col != "timestamp":
        try:
            int(first_col)
            df = df.drop(columns=[first_col])
        except (ValueError, TypeError):
            pass

    # Normalize column names
    df.columns = df.columns.str.strip().str.lower()

    # Verify required columns
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")

    # Parse timestamps — handle both timezone-aware and naive
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["timestamp"] = df["timestamp"].dt.tz_localize(None)

    # Sort by time
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Date range filter
    if start is not None:
        df = df[df["timestamp"] >= pd.Timestamp(start)]
    if end is not None:
        df = df[df["timestamp"] <= pd.Timestamp(end)]

    # Convert to Bar objects
    bars = []
    for row in df.itertuples(index=False):
        bars.append(Bar(
            timestamp=row.timestamp.to_pydatetime(),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            tick_count=int(row.tick_count),
            buy_volume=float(row.buy_volume),
            sell_volume=float(row.sell_volume),
        ))

    return bars


def load_instrument_bars(instrument: str,
                         start: Optional[datetime] = None,
                         end: Optional[datetime] = None,
                         data_dir: Path = DATA_DIR) -> List[Bar]:
    """Load bars for a named instrument from the default data directory.

    Args:
        instrument: e.g. "XAUUSD", "EURUSD", "USDJPY"
        start: Optional start datetime filter.
        end: Optional end datetime filter.
        data_dir: Directory containing the CSV files.

    Returns:
        List of Bar objects.
    """
    instrument = instrument.upper()
    if instrument not in INSTRUMENT_FILE_MAP:
        raise ValueError(
            f"Unknown instrument '{instrument}'. "
            f"Available: {list(INSTRUMENT_FILE_MAP.keys())}"
        )
    csv_path = data_dir / INSTRUMENT_FILE_MAP[instrument]
    return load_bars(csv_path, start=start, end=end)


def split_by_sessions(bars: List[Bar],
                      gap_minutes: int = 30) -> List[List[Bar]]:
    """Split a list of bars into trading sessions based on time gaps.

    A new session starts when the gap between consecutive bars exceeds
    gap_minutes. This is useful for resetting the Darvas detector between
    weekend/holiday gaps.

    Args:
        bars: Sorted list of Bar objects.
        gap_minutes: Minimum gap in minutes to start a new session.

    Returns:
        List of sessions, each a list of bars.
    """
    if not bars:
        return []

    sessions: List[List[Bar]] = []
    current_session: List[Bar] = [bars[0]]

    for i in range(1, len(bars)):
        gap = (bars[i].timestamp - bars[i - 1].timestamp).total_seconds() / 60
        if gap > gap_minutes:
            sessions.append(current_session)
            current_session = [bars[i]]
        else:
            current_session.append(bars[i])

    if current_session:
        sessions.append(current_session)

    return sessions

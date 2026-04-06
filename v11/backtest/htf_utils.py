"""Shared utilities for HTF investigation scripts.

Provides:
    - Bar resampling (1-min -> 5/15/30/60/240-min)
    - SMA and ADX computation
    - HTF value lookup (with look-ahead bias prevention)
    - Signal collection pipeline (extracted from analyze_combined.py)
    - Trailing stop simulation (extracted from analyze_combined.py)
    - Standard run-and-report pipeline
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from ..core.types import Bar, BreakoutSignal, Direction
from ..core.darvas_detector import DarvasDetector
from ..core.imbalance_classifier import ImbalanceClassifier
from ..config.strategy_config import StrategyConfig
from .data_loader import split_by_sessions


# ── Bar Resampling ─────────────────────────────────────────────────────────

def _floor_timestamp(ts: datetime, minutes: int) -> datetime:
    """Floor a timestamp to the nearest period boundary."""
    epoch = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    mins_since_midnight = ts.hour * 60 + ts.minute
    floored_mins = (mins_since_midnight // minutes) * minutes
    return epoch + timedelta(minutes=floored_mins)


def resample_bars(bars: List[Bar], minutes: int) -> List[Bar]:
    """Resample 1-min bars into higher-timeframe bars.

    Groups by floored timestamp. Aggregates: O=first open, H=max high,
    L=min low, C=last close, tick_count=sum, buy/sell_volume=sum.
    Discards trailing incomplete period.

    Args:
        bars: Sorted list of 1-min Bar objects.
        minutes: Target bar period (5, 15, 30, 60, 240).

    Returns:
        List of resampled Bar objects.
    """
    if not bars or minutes <= 1:
        return list(bars)

    groups: Dict[datetime, List[Bar]] = defaultdict(list)
    for bar in bars:
        key = _floor_timestamp(bar.timestamp, minutes)
        groups[key].append(bar)

    # Discard incomplete last group
    sorted_keys = sorted(groups.keys())
    if sorted_keys:
        last_group = groups[sorted_keys[-1]]
        # A complete group should have roughly `minutes` bars (allow 80% fill)
        if len(last_group) < minutes * 0.5:
            sorted_keys = sorted_keys[:-1]

    result = []
    for key in sorted_keys:
        group = groups[key]
        result.append(Bar(
            timestamp=key,
            open=group[0].open,
            high=max(b.high for b in group),
            low=min(b.low for b in group),
            close=group[-1].close,
            tick_count=sum(b.tick_count for b in group),
            buy_volume=sum(b.buy_volume for b in group),
            sell_volume=sum(b.sell_volume for b in group),
        ))

    return result


def resample_sessions(bars: List[Bar], minutes: int,
                      gap_minutes: int = 30) -> List[Bar]:
    """Resample bars within each session separately to avoid cross-gap bars.

    Splits by session first, resamples each session, concatenates results.
    """
    sessions = split_by_sessions(bars, gap_minutes=gap_minutes)
    result = []
    for session_bars in sessions:
        result.extend(resample_bars(session_bars, minutes))
    return result


# ── SMA Computation ────────────────────────────────────────────────────────

def compute_sma(bars: List[Bar], period: int,
                field: str = "close") -> List[Tuple[datetime, float]]:
    """Simple moving average on a bar series.

    Returns list of (timestamp, sma_value) starting from first full window.
    """
    values = [getattr(b, field) for b in bars]
    result = []
    window_sum = 0.0

    for i, v in enumerate(values):
        window_sum += v
        if i >= period:
            window_sum -= values[i - period]
        if i >= period - 1:
            result.append((bars[i].timestamp, window_sum / period))

    return result


# ── ADX Computation ────────────────────────────────────────────────────────

def compute_adx(bars: List[Bar], period: int = 14
                ) -> List[Tuple[datetime, float, float, float]]:
    """Average Directional Index using Wilder's smoothing.

    Returns list of (timestamp, adx, plus_di, minus_di).
    First valid ADX appears at bar index ~2*period.
    """
    if len(bars) < period + 1:
        return []

    # Step 1: compute TR, +DM, -DM per bar
    trs, plus_dms, minus_dms = [], [], []
    for i in range(1, len(bars)):
        high = bars[i].high
        low = bars[i].low
        prev_close = bars[i - 1].close
        prev_high = bars[i - 1].high
        prev_low = bars[i - 1].low

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

        trs.append(tr)
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)

    # Step 2: Wilder's smoothing for first `period` values (simple sum),
    # then smoothed: prev - prev/period + current
    def wilder_smooth(values: List[float], p: int) -> List[float]:
        if len(values) < p:
            return []
        smoothed = [sum(values[:p])]
        for i in range(p, len(values)):
            smoothed.append(smoothed[-1] - smoothed[-1] / p + values[i])
        return smoothed

    smooth_tr = wilder_smooth(trs, period)
    smooth_plus_dm = wilder_smooth(plus_dms, period)
    smooth_minus_dm = wilder_smooth(minus_dms, period)

    if not smooth_tr:
        return []

    # Step 3: +DI, -DI, DX
    plus_dis, minus_dis, dxs = [], [], []
    for i in range(len(smooth_tr)):
        tr_val = smooth_tr[i]
        if tr_val == 0:
            plus_dis.append(0.0)
            minus_dis.append(0.0)
            dxs.append(0.0)
            continue
        pdi = (smooth_plus_dm[i] / tr_val) * 100
        mdi = (smooth_minus_dm[i] / tr_val) * 100
        plus_dis.append(pdi)
        minus_dis.append(mdi)
        di_sum = pdi + mdi
        dx = (abs(pdi - mdi) / di_sum * 100) if di_sum > 0 else 0.0
        dxs.append(dx)

    # Step 4: ADX = Wilder's smoothing of DX
    smooth_adx = wilder_smooth(dxs, period)
    if not smooth_adx:
        return []

    # Map back to timestamps
    # smooth_tr starts at bar index `period` (0-indexed in trs, which starts at bar 1)
    # So smooth_tr[0] corresponds to bars[period]
    # smooth_adx starts `period` bars later: bars[2*period]
    adx_start_bar = 2 * period
    result = []
    for i, adx_val in enumerate(smooth_adx):
        bar_idx = adx_start_bar + i
        if bar_idx < len(bars):
            di_idx = period + i  # index into plus_dis/minus_dis
            if di_idx < len(plus_dis):
                result.append((
                    bars[bar_idx].timestamp,
                    adx_val,
                    plus_dis[di_idx],
                    minus_dis[di_idx],
                ))

    return result


# ── HTF Lookup ─────────────────────────────────────────────────────────────

def build_htf_lookup(values: List[Tuple[datetime, ...]]) -> Dict[datetime, tuple]:
    """Convert list of (timestamp, val1, val2, ...) to dict keyed by timestamp.

    For SMA: dict[ts] = sma_value (float)
    For ADX: dict[ts] = (adx, plus_di, minus_di)
    """
    result = {}
    for item in values:
        ts = item[0]
        if len(item) == 2:
            result[ts] = item[1]
        else:
            result[ts] = item[1:]
    return result


def get_htf_value_at(lookup: Dict[datetime, any], signal_timestamp: datetime,
                     htf_minutes: int):
    """Look up the previous completed HTF bar's value at a signal time.

    Floors signal_timestamp to HTF period then steps back one period
    to avoid look-ahead bias (the current HTF bar is still in progress).

    Returns None if no value available.
    """
    floored = _floor_timestamp(signal_timestamp, htf_minutes)
    prev_ts = floored - timedelta(minutes=htf_minutes)
    return lookup.get(prev_ts)


# ── Signal Collection ──────────────────────────────────────────────────────

def collect_signals(bars: List[Bar], config: StrategyConfig,
                    gap_minutes: int = 30) -> List[dict]:
    """Collect all Darvas breakout signals with volume classification.

    Extracted from oos_validation.py / analyze_combined.py pattern.

    Returns list of dicts:
        {signal, bars_after, is_long, vol_class, session_bars}
    """
    sessions = split_by_sessions(bars, gap_minutes=gap_minutes)
    raw_trades = []

    for session_bars in sessions:
        det = DarvasDetector(config)
        clf = ImbalanceClassifier(
            max_lookback=max(20, config.imbalance_window * 2),
            min_bar_ticks=config.min_bar_ticks,
        )
        for i, bar in enumerate(session_bars):
            signal = det.add_bar(bar)
            clf.add_bar(bar)
            if signal is not None:
                is_long = signal.direction == Direction.LONG
                vol_class = clf.classify(
                    signal.direction,
                    config.imbalance_window,
                    config.divergence_threshold,
                ).value
                raw_trades.append({
                    "signal": signal,
                    "bars_after": session_bars[i + 1:],
                    "is_long": is_long,
                    "vol_class": vol_class,
                })

    return raw_trades


# ── Trailing Stop Simulation ───────────────────────────────────────────────

def simulate_with_trailing(signal, bars_after, sl_price, tp_price,
                           max_hold, spread_cost, is_long,
                           tighten_after_bars=60,
                           tighten_mode="trail",
                           trail_lookback=10,
                           lock_profit_frac=0.0):
    """Simulate a single trade with optional SL tightening.

    Extracted from analyze_combined.py:24-116.

    Returns dict: {pnl, pnl_r, exit_reason, hold_bars, win, direction,
                   entry_time} or None.
    """
    if not bars_after:
        return None
    half_spread = spread_cost / 2
    entry = signal.breakout_price
    effective_entry = entry + half_spread if is_long else entry - half_spread
    risk = abs(entry - sl_price)
    if risk <= 0:
        return None

    current_sl = sl_price
    exit_price = 0.0
    exit_reason = "TIME_STOP"
    hold_bars = len(bars_after)
    sl_was_tightened = False

    for i, bar in enumerate(bars_after):
        if i >= max_hold:
            exit_price = bar.close
            exit_reason = "TIME_STOP"
            hold_bars = i + 1
            break

        # SL tightening logic
        if tighten_mode != "none" and i >= tighten_after_bars and not sl_was_tightened:
            current_unrealized = (bar.close - entry) if is_long else (entry - bar.close)
            if current_unrealized > 0:
                sl_was_tightened = True
                if tighten_mode == "breakeven":
                    current_sl = entry
                elif tighten_mode == "lock":
                    lock_amount = current_unrealized * lock_profit_frac
                    current_sl = (entry + lock_amount) if is_long else (entry - lock_amount)
                elif tighten_mode == "trail":
                    lookback_start = max(0, i - trail_lookback)
                    recent = bars_after[lookback_start:i + 1]
                    if is_long:
                        swing_low = min(b.low for b in recent)
                        current_sl = max(current_sl, max(entry, swing_low))
                    else:
                        swing_high = max(b.high for b in recent)
                        current_sl = min(current_sl, min(entry, swing_high))

        # Continuous trailing after initial tighten
        if tighten_mode == "trail" and sl_was_tightened and i > tighten_after_bars:
            lookback_start = max(0, i - trail_lookback)
            recent = bars_after[lookback_start:i + 1]
            if is_long:
                swing_low = min(b.low for b in recent)
                current_sl = max(current_sl, max(entry, swing_low))
            else:
                swing_high = max(b.high for b in recent)
                current_sl = min(current_sl, min(entry, swing_high))

        # Check exits
        if is_long:
            if bar.low <= current_sl:
                exit_price = current_sl
                exit_reason = "SL_TIGHT" if sl_was_tightened else "SL"
                hold_bars = i + 1
                break
            if bar.high >= tp_price:
                exit_price = tp_price
                exit_reason = "TARGET"
                hold_bars = i + 1
                break
        else:
            if bar.high >= current_sl:
                exit_price = current_sl
                exit_reason = "SL_TIGHT" if sl_was_tightened else "SL"
                hold_bars = i + 1
                break
            if bar.low <= tp_price:
                exit_price = tp_price
                exit_reason = "TARGET"
                hold_bars = i + 1
                break
    else:
        if exit_price == 0.0:
            exit_price = bars_after[-1].close

    effective_exit = exit_price - half_spread if is_long else exit_price + half_spread
    pnl = (effective_exit - effective_entry) if is_long else (effective_entry - effective_exit)
    pnl_r = pnl / risk if risk > 0 else 0.0

    return {
        "pnl": pnl, "pnl_r": pnl_r, "exit_reason": exit_reason,
        "hold_bars": hold_bars, "win": 1 if pnl > 0 else 0,
        "direction": signal.direction.value,
        "entry_time": signal.timestamp,
    }


# ── Run & Report Pipeline ─────────────────────────────────────────────────

def simulate_trades(raw_trades: List[dict], rr: float, config: StrategyConfig,
                    filter_fn: Callable[[dict], bool] = None,
                    tighten_mode: str = "trail",
                    tighten_after_bars: int = 60,
                    trail_lookback: int = 10) -> List[dict]:
    """Apply filter and simulate all matching trades.

    Args:
        raw_trades: Output from collect_signals().
        rr: Risk-reward ratio.
        config: Strategy config (for spread_cost, max_hold_bars).
        filter_fn: Optional filter function taking a raw_trade dict.
        tighten_mode: SL tightening mode.
        tighten_after_bars: Bars before tightening activates.
        trail_lookback: Bars to look back for trailing stop.

    Returns:
        List of trade result dicts.
    """
    trades = []
    for raw in raw_trades:
        if filter_fn is not None and not filter_fn(raw):
            continue
        signal = raw["signal"]
        is_long = raw["is_long"]
        entry = signal.breakout_price
        sl = signal.box.bottom if is_long else signal.box.top
        risk = abs(entry - sl)
        tp = entry + risk * rr if is_long else entry - risk * rr

        t = simulate_with_trailing(
            signal, raw["bars_after"], sl, tp,
            config.max_hold_bars, config.spread_cost, is_long,
            tighten_after_bars=tighten_after_bars,
            tighten_mode=tighten_mode,
            trail_lookback=trail_lookback,
        )
        if t is not None:
            trades.append(t)

    return trades


def compute_stats(trades: List[dict]) -> dict:
    """Compute standard stats from a list of trade result dicts.

    Returns dict: {n, wr, avg_r, pnl, sl, slt, tp, tm, long_n, short_n,
                   long_wr, short_wr}
    """
    if not trades:
        return {
            "n": 0, "wr": 0.0, "avg_r": 0.0, "pnl": 0.0,
            "sl": 0, "slt": 0, "tp": 0, "tm": 0,
            "long_n": 0, "short_n": 0, "long_wr": 0.0, "short_wr": 0.0,
        }

    df = pd.DataFrame(trades)
    longs = df[df.direction == "long"]
    shorts = df[df.direction == "short"]

    return {
        "n": len(df),
        "wr": df.win.mean() * 100,
        "avg_r": df.pnl_r.mean(),
        "pnl": df.pnl.sum(),
        "sl": (df.exit_reason == "SL").sum(),
        "slt": (df.exit_reason == "SL_TIGHT").sum(),
        "tp": (df.exit_reason == "TARGET").sum(),
        "tm": (df.exit_reason == "TIME_STOP").sum(),
        "long_n": len(longs),
        "short_n": len(shorts),
        "long_wr": longs.win.mean() * 100 if len(longs) > 0 else 0.0,
        "short_wr": shorts.win.mean() * 100 if len(shorts) > 0 else 0.0,
    }


def print_header(title: str, width: int = 100):
    """Print a formatted section header."""
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}")


def print_row_header():
    """Print the standard column header for result tables."""
    print(f"  {'Label':30s} {'N':>4s} {'WR%':>6s} {'AvgR':>7s} {'PnL':>8s} "
          f"{'SL':>4s} {'SLT':>4s} {'TP':>4s} {'TM':>4s} "
          f"{'LgWR':>6s} {'ShWR':>6s}")
    print(f"  {'-' * 93}")


def print_row(label: str, s: dict):
    """Print one result row."""
    if s["n"] == 0:
        print(f"  {label:30s}    0   ---     ---      ---    -    -    -    -    ---    ---")
        return
    flag = " *" if s["n"] < 15 else ""
    print(f"  {label:30s} {s['n']:4d} {s['wr']:6.1f} {s['avg_r']:+7.3f} {s['pnl']:+8.4f} "
          f"{s['sl']:4d} {s['slt']:4d} {s['tp']:4d} {s['tm']:4d} "
          f"{s['long_wr']:6.1f} {s['short_wr']:6.1f}{flag}")

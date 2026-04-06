"""Investigation: HTF Level Detection + 1-Min Entry.

Detects swing highs/lows on HIGHER timeframe bars (1H, 4H) where they represent
genuine institutional S/R levels, then trades the 1-min breakout.

Key difference from previous level investigation:
    - Levels detected on HTF (meaningful) not 1-min (noise)
    - Levels PERSIST across sessions (HTF levels are real market memory)
    - Entry on 1-min gives precise timing and tight SL

Also tests: Previous Day High/Low as levels (universally watched, no detection needed).

Part A: HTF swing levels (1H, 4H) + 1-min entry + SMA + volume
Part B: Previous Day High/Low + 1-min entry + SMA + volume
Part C: Combined (HTF swings + daily H/L)
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import product
from typing import List, Optional, Dict

from v11.backtest.data_loader import load_instrument_bars, split_by_sessions
from v11.backtest.htf_utils import (
    resample_sessions, resample_bars, compute_sma, build_htf_lookup,
    get_htf_value_at, simulate_with_trailing, compute_stats, print_header,
)
from v11.core.imbalance_classifier import ImbalanceClassifier
from v11.core.types import Bar, Direction
from v11.config.strategy_config import EURUSD_CONFIG

# ── Data Structures ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class HTFLevel:
    price: float
    level_type: str          # "resistance" or "support"
    origin_time: datetime    # when the swing point occurred
    htf_minutes: int         # timeframe it was detected on
    source: str              # "swing", "daily_high", "daily_low"


@dataclass(frozen=True)
class HTFLevelSignal:
    """Compatible with simulate_with_trailing."""
    timestamp: datetime
    direction: Direction
    breakout_price: float
    level_price: float
    atr: float
    source: str


# ── HTF Swing Level Detector ──────────────────────────────────────────────

class HTFSwingDetector:
    """Detects swing highs/lows on HTF bars. Levels persist until expiry."""

    def __init__(self, left_bars: int = 10, right_bars: int = 5,
                 expiry_hours: int = 72, merge_pips: float = 0.0005):
        self._left = left_bars
        self._right = right_bars
        self._expiry_hours = expiry_hours
        self._merge_dist = merge_pips
        self._buffer: deque = deque(maxlen=left_bars + right_bars + 1)
        self._levels: List[HTFLevel] = []
        self._htf_minutes: int = 60  # set by caller

    def set_timeframe(self, htf_minutes: int):
        self._htf_minutes = htf_minutes

    def add_bar(self, bar: Bar) -> List[HTFLevel]:
        """Process an HTF bar. Returns newly detected levels."""
        self._buffer.append(bar)
        if len(self._buffer) < self._left + self._right + 1:
            return []

        candidate_idx = self._left
        candidate = self._buffer[candidate_idx]
        new_levels = []

        # Check swing high
        left_highs = [self._buffer[j].high for j in range(candidate_idx)]
        right_highs = [self._buffer[j].high for j in range(candidate_idx + 1, len(self._buffer))]
        if candidate.high > max(left_highs) and candidate.high > max(right_highs):
            level = HTFLevel(
                price=candidate.high, level_type="resistance",
                origin_time=candidate.timestamp, htf_minutes=self._htf_minutes,
                source="swing",
            )
            if self._should_add(level):
                self._levels.append(level)
                new_levels.append(level)

        # Check swing low
        left_lows = [self._buffer[j].low for j in range(candidate_idx)]
        right_lows = [self._buffer[j].low for j in range(candidate_idx + 1, len(self._buffer))]
        if candidate.low < min(left_lows) and candidate.low < min(right_lows):
            level = HTFLevel(
                price=candidate.low, level_type="support",
                origin_time=candidate.timestamp, htf_minutes=self._htf_minutes,
                source="swing",
            )
            if self._should_add(level):
                self._levels.append(level)
                new_levels.append(level)

        # Prune expired
        now = bar.timestamp
        self._levels = [
            lv for lv in self._levels
            if (now - lv.origin_time) < timedelta(hours=self._expiry_hours)
        ]

        return new_levels

    def _should_add(self, new_level: HTFLevel) -> bool:
        for existing in self._levels:
            if existing.level_type == new_level.level_type:
                if abs(existing.price - new_level.price) < self._merge_dist:
                    return False
        return True

    def get_levels(self) -> List[HTFLevel]:
        return list(self._levels)

    def reset(self):
        self._buffer.clear()
        self._levels.clear()


# ── Daily High/Low Level Tracker ───────────────────────────────────────────

class DailyHLTracker:
    """Tracks previous day's high and low as S/R levels."""

    def __init__(self):
        self._current_date = None
        self._current_high = -1e9
        self._current_low = 1e9
        self._prev_high: Optional[float] = None
        self._prev_low: Optional[float] = None

    def update(self, bar: Bar):
        """Call with each 1-min bar."""
        bar_date = bar.timestamp.date()
        if self._current_date is None:
            self._current_date = bar_date
            self._current_high = bar.high
            self._current_low = bar.low
            return

        if bar_date != self._current_date:
            # New day — previous day's H/L become levels
            self._prev_high = self._current_high
            self._prev_low = self._current_low
            self._current_date = bar_date
            self._current_high = bar.high
            self._current_low = bar.low
        else:
            self._current_high = max(self._current_high, bar.high)
            self._current_low = min(self._current_low, bar.low)

    def get_levels(self, timestamp: datetime) -> List[HTFLevel]:
        """Return previous day H/L as levels."""
        levels = []
        if self._prev_high is not None:
            levels.append(HTFLevel(
                price=self._prev_high, level_type="resistance",
                origin_time=timestamp - timedelta(days=1),
                htf_minutes=1440, source="daily_high",
            ))
        if self._prev_low is not None:
            levels.append(HTFLevel(
                price=self._prev_low, level_type="support",
                origin_time=timestamp - timedelta(days=1),
                htf_minutes=1440, source="daily_low",
            ))
        return levels

    def reset(self):
        self._current_date = None
        self._current_high = -1e9
        self._current_low = 1e9
        self._prev_high = None
        self._prev_low = None


# ── 1-Min Breakout Scanner ────────────────────────────────────────────────

def scan_htf_level_breaks(bars_1m: List[Bar],
                          htf_levels_timeline: Dict[datetime, List[HTFLevel]],
                          daily_hl: bool = False,
                          htf_minutes: int = 60,
                          sma_lookup: dict = None,
                          cooldown_bars: int = 60,
                          sl_atr_offset: float = 0.3,
                          atr_period: int = 60):
    """Scan 1-min bars for breakouts of HTF levels.

    Args:
        bars_1m: All 1-min bars (not session-split — levels persist)
        htf_levels_timeline: dict mapping HTF bar timestamp -> list of active levels
        daily_hl: if True, also include previous day high/low
        htf_minutes: HTF bar period for timeline lookup
        sma_lookup: 60-min SMA(50) lookup for direction filter
        cooldown_bars: min bars between signals at same level
        sl_atr_offset: ATR fraction for SL buffer
        atr_period: bars for ATR computation
    """
    raw_trades = []
    clf = ImbalanceClassifier(max_lookback=40, min_bar_ticks=10)
    daily = DailyHLTracker() if daily_hl else None

    # ATR tracking
    atr = 0.0
    atr_count = 0
    prev_close = 0.0

    # Track used levels to avoid repeat signals
    used_levels: Dict[float, int] = {}  # price -> last signal bar_index

    for i, bar in enumerate(bars_1m):
        clf.add_bar(bar)
        if daily:
            daily.update(bar)

        # Update ATR
        if prev_close > 0:
            tr = max(bar.high - bar.low,
                     abs(bar.high - prev_close),
                     abs(bar.low - prev_close))
        else:
            tr = bar.high - bar.low
        prev_close = bar.close
        if atr_count < atr_period:
            atr_count += 1
            atr += (tr - atr) / atr_count
        else:
            alpha = 2.0 / (atr_period + 1)
            atr = atr * (1 - alpha) + tr * alpha

        if atr <= 0 or atr_count < atr_period:
            continue

        # Get active HTF levels at this timestamp
        # Use previous completed HTF bar's levels (no look-ahead)
        from v11.backtest.htf_utils import _floor_timestamp
        floored = _floor_timestamp(bar.timestamp, htf_minutes)
        prev_htf_ts = floored - timedelta(minutes=htf_minutes)
        htf_levels = htf_levels_timeline.get(prev_htf_ts, [])

        # Add daily H/L if enabled
        if daily:
            htf_levels = list(htf_levels) + daily.get_levels(bar.timestamp)

        # SMA filter
        if sma_lookup:
            sma_val = get_htf_value_at(sma_lookup, bar.timestamp, 60)
        else:
            sma_val = None

        # Check each level for breakout
        for lv in htf_levels:
            # Cooldown check
            if lv.price in used_levels:
                if i - used_levels[lv.price] < cooldown_bars:
                    continue

            signal = None

            # Resistance break -> LONG
            if lv.level_type == "resistance" and bar.close > lv.price:
                if sma_val is not None and bar.close <= sma_val:
                    continue  # wrong direction
                vol_class = clf.classify(Direction.LONG, 3, 0.50)
                if vol_class.value != "CONFIRMING":
                    continue
                sl = lv.price - sl_atr_offset * atr
                signal = HTFLevelSignal(
                    timestamp=bar.timestamp, direction=Direction.LONG,
                    breakout_price=bar.close, level_price=lv.price,
                    atr=atr, source=lv.source,
                )

            # Support break -> SHORT
            elif lv.level_type == "support" and bar.close < lv.price:
                if sma_val is not None and bar.close >= sma_val:
                    continue
                vol_class = clf.classify(Direction.SHORT, 3, 0.50)
                if vol_class.value != "CONFIRMING":
                    continue
                sl = lv.price + sl_atr_offset * atr
                signal = HTFLevelSignal(
                    timestamp=bar.timestamp, direction=Direction.SHORT,
                    breakout_price=bar.close, level_price=lv.price,
                    atr=atr, source=lv.source,
                )

            if signal is not None:
                used_levels[lv.price] = i
                is_long = signal.direction == Direction.LONG
                entry = signal.breakout_price
                if is_long:
                    sl = signal.level_price - sl_atr_offset * atr
                else:
                    sl = signal.level_price + sl_atr_offset * atr

                raw_trades.append({
                    "signal": signal,
                    "bars_after": bars_1m[i + 1:i + 1 + 180],  # cap at 3 hours
                    "is_long": is_long,
                    "sl": sl,
                    "source": signal.source,
                })

    return raw_trades


def build_htf_level_timeline(bars_1m, htf_minutes, left_bars, right_bars,
                             expiry_hours, merge_pips):
    """Build HTF levels timeline: resample, detect swings, record levels at each HTF bar."""
    htf_bars = resample_bars(bars_1m, htf_minutes)  # NOT session-split — levels persist
    det = HTFSwingDetector(left_bars, right_bars, expiry_hours, merge_pips)
    det.set_timeframe(htf_minutes)

    timeline = {}
    for bar in htf_bars:
        det.add_bar(bar)
        timeline[bar.timestamp] = det.get_levels()

    return timeline


def simulate_htf_level_trades(raw_trades, rr, spread_cost, max_hold_bars):
    """Simulate trades from HTF level breakouts."""
    trades = []
    for raw in raw_trades:
        signal = raw["signal"]
        is_long = raw["is_long"]
        sl = raw["sl"]
        entry = signal.breakout_price
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        tp = entry + risk * rr if is_long else entry - risk * rr

        t = simulate_with_trailing(
            signal, raw["bars_after"], sl, tp,
            max_hold_bars, spread_cost, is_long,
        )
        if t is not None:
            t["source"] = raw["source"]
            trades.append(t)

    return trades


# ── Helpers ────────────────────────────────────────────────────────────────

IS_START, IS_END = datetime(2024, 1, 1), datetime(2026, 12, 31)
OOS_START, OOS_END = datetime(2018, 1, 1), datetime(2023, 12, 31)
OOS_YEARS = 6
IS_YEARS = 2

SPREAD = EURUSD_CONFIG.spread_cost
MAX_HOLD = 120


def filter_by_period(raw_trades, start, end):
    return [t for t in raw_trades if start <= t["signal"].timestamp <= end]


def print_result(label, is_trades, oos_trades):
    is_s = compute_stats(is_trades)
    oos_s = compute_stats(oos_trades)
    is_str = (f"{is_s['n']:4d} {is_s['wr']:6.1f} {is_s['avg_r']:+7.3f} {is_s['pnl']:+8.4f}"
              if is_s['n'] > 0 else "   0   ---     ---      ---")
    oos_str = (f"{oos_s['n']:5d} {oos_s['n']/OOS_YEARS:5.1f} {oos_s['wr']:7.1f} "
               f"{oos_s['avg_r']:+8.3f} {oos_s['pnl']:+9.4f}"
               if oos_s['n'] > 0 else "    0   ---    ---      ---       ---")
    flag = " *" if (is_s['n'] < 15 or oos_s['n'] < 15) else ""
    print(f"  {label:45s}  |  {is_str}  |  {oos_str}{flag}")


# ── Main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    print("Loading EURUSD data...")
    all_bars = load_instrument_bars("EURUSD")
    print(f"Total: {len(all_bars):,} bars ({all_bars[0].timestamp.date()} to {all_bars[-1].timestamp.date()})")

    print("Building 60-min SMA(50) lookup...")
    htf_60 = resample_sessions(all_bars, 60)
    sma_values = compute_sma(htf_60, 50)
    sma_lookup = build_htf_lookup(sma_values)

    header_fmt = (f"  {'Config':45s}  |  "
                  f"{'IS_N':>4s} {'IS_WR':>6s} {'IS_AvgR':>7s} {'IS_PnL':>8s}  |  "
                  f"{'OOS_N':>5s} {'/yr':>5s} {'OOS_WR':>7s} {'OOS_AvgR':>8s} {'OOS_PnL':>9s}")

    # ═══════════════════════════════════════════════════════════════════
    # PART A: HTF Swing Levels + 1-Min Entry
    # ═══════════════════════════════════════════════════════════════════
    print_header("PART A: HTF SWING LEVELS + 1-MIN ENTRY (SMA+CONF)", 120)
    print(header_fmt)
    print(f"  {'-' * 115}")

    HTF_PERIODS = [60, 240]
    SWING_PARAMS = [
        {"left_bars": 10, "right_bars": 5},
        {"left_bars": 15, "right_bars": 5},
        {"left_bars": 10, "right_bars": 10},
        {"left_bars": 20, "right_bars": 10},
    ]
    EXPIRY_HOURS = [48, 72, 168]  # 2 days, 3 days, 1 week
    MERGE_PIPS = [0.0003, 0.0005, 0.0010]  # 3, 5, 10 pips
    RR_VALUES = [1.5, 2.0]
    COOLDOWNS = [30, 60]

    results_a = []

    for htf_min in HTF_PERIODS:
        for sp in SWING_PARAMS:
            for exp_h in EXPIRY_HOURS:
                for merge in MERGE_PIPS:
                    print(f"  Building {htf_min}m levels: lb={sp['left_bars']} rb={sp['right_bars']} exp={exp_h}h mrg={merge}...")
                    timeline = build_htf_level_timeline(
                        all_bars, htf_min, sp["left_bars"], sp["right_bars"],
                        exp_h, merge,
                    )

                    for cd in COOLDOWNS:
                        raw = scan_htf_level_breaks(
                            all_bars, timeline,
                            daily_hl=False, htf_minutes=htf_min,
                            sma_lookup=sma_lookup, cooldown_bars=cd,
                        )

                        is_raw = filter_by_period(raw, IS_START, IS_END)
                        oos_raw = filter_by_period(raw, OOS_START, OOS_END)

                        for rr in RR_VALUES:
                            is_trades = simulate_htf_level_trades(is_raw, rr, SPREAD, MAX_HOLD)
                            oos_trades = simulate_htf_level_trades(oos_raw, rr, SPREAD, MAX_HOLD)
                            is_s = compute_stats(is_trades)
                            oos_s = compute_stats(oos_trades)

                            label = (f"{htf_min}m lb={sp['left_bars']:2d} rb={sp['right_bars']:2d} "
                                     f"exp={exp_h:3d}h mrg={merge:.4f} cd={cd:2d} RR={rr}")
                            results_a.append({
                                "label": label, "is": is_s, "oos": oos_s,
                                "htf": htf_min, "rr": rr, "cd": cd,
                                "sp": sp, "exp": exp_h, "merge": merge,
                                "oos_per_year": oos_s["n"] / OOS_YEARS,
                            })

                            if oos_s["n"] >= 10:
                                print_result(label, is_trades, oos_trades)

    # Rankings
    print(f"\n  --- Top 15 by OOS AvgR (N >= 15) ---")
    ranked_a = [r for r in results_a if r["oos"]["n"] >= 15]
    ranked_a.sort(key=lambda r: r["oos"]["avg_r"], reverse=True)
    for i, r in enumerate(ranked_a[:15]):
        s = r["oos"]
        print(f"  {i+1:3d}. {r['label']:50s} "
              f"N={s['n']:4d} ({r['oos_per_year']:.1f}/yr) WR={s['wr']:.1f}% AvgR={s['avg_r']:+.3f}")

    print(f"\n  --- Top 15 by OOS trades/yr (AvgR > 0, N >= 15) ---")
    pos_a = [r for r in results_a if r["oos"]["avg_r"] > 0 and r["oos"]["n"] >= 15]
    pos_a.sort(key=lambda r: r["oos_per_year"], reverse=True)
    for i, r in enumerate(pos_a[:15]):
        s = r["oos"]
        print(f"  {i+1:3d}. {r['label']:50s} "
              f"N={s['n']:4d} ({r['oos_per_year']:.1f}/yr) WR={s['wr']:.1f}% AvgR={s['avg_r']:+.3f}")

    # ═══════════════════════════════════════════════════════════════════
    # PART B: Previous Day High/Low + 1-Min Entry
    # ═══════════════════════════════════════════════════════════════════
    print_header("PART B: PREVIOUS DAY HIGH/LOW + 1-MIN ENTRY (SMA+CONF)", 120)
    print(header_fmt)
    print(f"  {'-' * 115}")

    results_b = []
    # Empty timeline — only daily H/L used
    empty_timeline = {}

    for cd in [30, 60, 120]:
        for rr in RR_VALUES:
            raw = scan_htf_level_breaks(
                all_bars, empty_timeline,
                daily_hl=True, htf_minutes=60,
                sma_lookup=sma_lookup, cooldown_bars=cd,
            )

            is_raw = filter_by_period(raw, IS_START, IS_END)
            oos_raw = filter_by_period(raw, OOS_START, OOS_END)

            is_trades = simulate_htf_level_trades(is_raw, rr, SPREAD, MAX_HOLD)
            oos_trades = simulate_htf_level_trades(oos_raw, rr, SPREAD, MAX_HOLD)

            label = f"DailyHL cd={cd:3d} RR={rr}"
            print_result(label, is_trades, oos_trades)

            is_s = compute_stats(is_trades)
            oos_s = compute_stats(oos_trades)
            results_b.append({
                "label": label, "is": is_s, "oos": oos_s,
                "oos_per_year": oos_s["n"] / OOS_YEARS,
            })

    # ═══════════════════════════════════════════════════════════════════
    # PART C: Combined (HTF Swings + Daily H/L)
    # ═══════════════════════════════════════════════════════════════════
    if ranked_a:
        best_a = ranked_a[0]
        print_header("PART C: BEST HTF SWING CONFIG + DAILY H/L COMBINED", 120)
        print(header_fmt)
        print(f"  {'-' * 115}")

        sp = best_a["sp"]
        timeline = build_htf_level_timeline(
            all_bars, best_a["htf"], sp["left_bars"], sp["right_bars"],
            best_a["exp"], best_a["merge"],
        )

        for cd in [30, 60]:
            for rr in RR_VALUES:
                raw = scan_htf_level_breaks(
                    all_bars, timeline,
                    daily_hl=True, htf_minutes=best_a["htf"],
                    sma_lookup=sma_lookup, cooldown_bars=cd,
                )
                is_raw = filter_by_period(raw, IS_START, IS_END)
                oos_raw = filter_by_period(raw, OOS_START, OOS_END)
                is_trades = simulate_htf_level_trades(is_raw, rr, SPREAD, MAX_HOLD)
                oos_trades = simulate_htf_level_trades(oos_raw, rr, SPREAD, MAX_HOLD)
                label = f"HTF+DailyHL cd={cd} RR={rr}"
                print_result(label, is_trades, oos_trades)

    # ═══════════════════════════════════════════════════════════════════
    # COMPARISON
    # ═══════════════════════════════════════════════════════════════════
    print_header("COMPARISON vs DARVAS + SMA BASELINE", 120)
    print(f"  Darvas (SMA+CONF+Trail10@60, RR=2.0):")
    print(f"    IS:  24 trades (12/yr), 62.5% WR, +0.729 AvgR")
    print(f"    OOS: 63 trades (10.5/yr), 46.0% WR, +0.176 AvgR")

    total_pos = sum(1 for r in results_a if r["oos"]["avg_r"] > 0 and r["oos"]["n"] >= 15)
    total = sum(1 for r in results_a if r["oos"]["n"] >= 15)
    print(f"\n  HTF Swing Levels: {total_pos}/{total} configs with positive OOS AvgR (N>=15)")

    if pos_a:
        best = pos_a[0]
        s = best["oos"]
        print(f"  Best by trades/yr: {best['label']}")
        print(f"    OOS: {s['n']} trades ({best['oos_per_year']:.1f}/yr), "
              f"WR={s['wr']:.1f}%, AvgR={s['avg_r']:+.3f}")

    # Source breakdown for best config
    if ranked_a and ranked_a[0]["oos"]["n"] >= 15:
        best_cfg = ranked_a[0]
        sp = best_cfg["sp"]
        timeline = build_htf_level_timeline(
            all_bars, best_cfg["htf"], sp["left_bars"], sp["right_bars"],
            best_cfg["exp"], best_cfg["merge"],
        )
        raw = scan_htf_level_breaks(
            all_bars, timeline,
            daily_hl=False, htf_minutes=best_cfg["htf"],
            sma_lookup=sma_lookup, cooldown_bars=best_cfg["cd"],
        )
        oos_raw = filter_by_period(raw, OOS_START, OOS_END)
        print(f"\n  Best config signal source breakdown:")
        print(f"    Total OOS signals: {len(oos_raw)}")

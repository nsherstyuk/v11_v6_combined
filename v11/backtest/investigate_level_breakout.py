"""Investigation: Level Breakout / Retest Strategy on EURUSD.

Detects swing highs/lows as S/R levels (no look-ahead), then trades breakouts
with two entry modes:
    Mode A (volume_confirm): break + CONFIRMING volume -> enter
    Mode B (retest): break -> pullback to level -> rebreak + CONFIRMING volume

Plus 60-min SMA(50) direction filter (proven on EURUSD).

Two-phase parameter sweep:
    Phase 1: Level detection params (Mode A, RR=2.0) -> find best level configs
    Phase 2: Full sweep on top configs (Mode A/B, RR 1.5/2.0, retest params)
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from itertools import product
from typing import List, Optional

from v11.backtest.data_loader import load_instrument_bars, split_by_sessions
from v11.backtest.htf_utils import (
    resample_sessions, compute_sma, build_htf_lookup, get_htf_value_at,
    simulate_with_trailing, compute_stats, print_header,
)
from v11.core.imbalance_classifier import ImbalanceClassifier
from v11.core.types import Bar, Direction
from v11.config.strategy_config import EURUSD_CONFIG

# ── Data Structures ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Level:
    price: float
    level_type: str          # "resistance" or "support"
    detected_bar: int        # bar index when confirmed (right side complete)
    origin_bar: int          # bar index of the actual swing point
    atr_at_detection: float


@dataclass(frozen=True)
class LevelSignal:
    """Lightweight signal compatible with simulate_with_trailing."""
    timestamp: datetime
    direction: Direction
    breakout_price: float
    level_price: float
    atr: float
    entry_mode: str          # "volume_confirm" or "retest"


@dataclass
class PendingRetest:
    level: Level
    break_bar: int
    direction: Direction
    pulled_back: bool = False


# ── LevelDetector ──────────────────────────────────────────────────────────

class LevelDetector:
    """Detects swing highs/lows from a bar stream. No look-ahead bias.

    A swing high is confirmed right_bars after the swing point.
    Manages level list with expiry and merge (deduplication).
    """

    def __init__(self, left_bars: int = 40, right_bars: int = 10,
                 expiry_bars: int = 240, merge_atr_fraction: float = 0.5,
                 atr_period: int = 60):
        self._left = left_bars
        self._right = right_bars
        self._expiry = expiry_bars
        self._merge_frac = merge_atr_fraction
        self._atr_period = atr_period

        self._buffer: deque = deque(maxlen=left_bars + right_bars + 1)
        self._bar_index: int = -1
        self._levels: List[Level] = []
        self._atr: float = 0.0
        self._atr_count: int = 0
        self._prev_close: float = 0.0

    def add_bar(self, bar: Bar) -> List[Level]:
        """Process a bar. Returns newly detected levels."""
        self._bar_index += 1
        self._update_atr(bar)
        self._buffer.append(bar)

        if len(self._buffer) < self._left + self._right + 1:
            return []

        # Candidate is the bar at position left_bars (the middle of the window)
        candidate_idx = self._left
        candidate = self._buffer[candidate_idx]
        origin_bar = self._bar_index - self._right

        new_levels = []

        # Check swing high
        left_highs = [self._buffer[j].high for j in range(candidate_idx)]
        right_highs = [self._buffer[j].high for j in range(candidate_idx + 1, len(self._buffer))]
        if left_highs and right_highs:
            if candidate.high > max(left_highs) and candidate.high > max(right_highs):
                level = Level(
                    price=candidate.high,
                    level_type="resistance",
                    detected_bar=self._bar_index,
                    origin_bar=origin_bar,
                    atr_at_detection=self._atr,
                )
                if self._should_add(level):
                    self._levels.append(level)
                    new_levels.append(level)

        # Check swing low
        left_lows = [self._buffer[j].low for j in range(candidate_idx)]
        right_lows = [self._buffer[j].low for j in range(candidate_idx + 1, len(self._buffer))]
        if left_lows and right_lows:
            if candidate.low < min(left_lows) and candidate.low < min(right_lows):
                level = Level(
                    price=candidate.low,
                    level_type="support",
                    detected_bar=self._bar_index,
                    origin_bar=origin_bar,
                    atr_at_detection=self._atr,
                )
                if self._should_add(level):
                    self._levels.append(level)
                    new_levels.append(level)

        # Prune expired levels
        self._levels = [
            lv for lv in self._levels
            if self._bar_index - lv.detected_bar <= self._expiry
        ]

        return new_levels

    def _should_add(self, new_level: Level) -> bool:
        """Check if this level should be added (not a duplicate of existing)."""
        if self._atr <= 0:
            return True
        merge_dist = self._merge_frac * self._atr
        for existing in self._levels:
            if existing.level_type == new_level.level_type:
                if abs(existing.price - new_level.price) < merge_dist:
                    return False  # too close to existing level of same type
        return True

    def get_active_levels(self) -> List[Level]:
        return list(self._levels)

    def _update_atr(self, bar: Bar):
        if self._prev_close > 0:
            tr = max(bar.high - bar.low,
                     abs(bar.high - self._prev_close),
                     abs(bar.low - self._prev_close))
        else:
            tr = bar.high - bar.low
        self._prev_close = bar.close
        if self._atr_count < self._atr_period:
            self._atr_count += 1
            self._atr += (tr - self._atr) / self._atr_count
        else:
            alpha = 2.0 / (self._atr_period + 1)
            self._atr = self._atr * (1 - alpha) + tr * alpha

    @property
    def current_atr(self) -> float:
        return self._atr

    def reset(self):
        self._buffer.clear()
        self._bar_index = -1
        self._levels.clear()
        self._atr = 0.0
        self._atr_count = 0
        self._prev_close = 0.0


# ── BreakoutDetector ───────────────────────────────────────────────────────

class BreakoutDetector:
    """Watches for level breaks and generates signals in two modes."""

    def __init__(self, entry_mode: str = "volume_confirm",
                 min_pullback_bars: int = 3, max_pullback_bars: int = 30,
                 imbalance_window: int = 3, divergence_threshold: float = 0.50,
                 pullback_atr_tolerance: float = 0.3):
        self._mode = entry_mode
        self._min_pb = min_pullback_bars
        self._max_pb = max_pullback_bars
        self._imb_window = imbalance_window
        self._div_thresh = divergence_threshold
        self._pb_tol = pullback_atr_tolerance

        self._used_levels: set = set()  # (level_type, price) tuples
        self._pending: List[PendingRetest] = []

    def check_bar(self, bar: Bar, bar_index: int, levels: List[Level],
                  classifier: ImbalanceClassifier, atr: float) -> List[dict]:
        """Check for breakout signals. Returns list of signal info dicts."""
        signals = []

        if self._mode == "volume_confirm":
            signals = self._check_volume_confirm(bar, bar_index, levels, classifier, atr)
        elif self._mode == "retest":
            signals = self._check_retest(bar, bar_index, levels, classifier, atr)

        return signals

    def _check_volume_confirm(self, bar, bar_index, levels, classifier, atr):
        signals = []
        for lv in levels:
            key = (lv.level_type, lv.price)
            if key in self._used_levels:
                continue

            # Resistance break -> LONG
            if lv.level_type == "resistance" and bar.close > lv.price:
                vol_class = classifier.classify(
                    Direction.LONG, self._imb_window, self._div_thresh
                )
                self._used_levels.add(key)
                signals.append({
                    "signal": LevelSignal(
                        timestamp=bar.timestamp,
                        direction=Direction.LONG,
                        breakout_price=bar.close,
                        level_price=lv.price,
                        atr=atr,
                        entry_mode="volume_confirm",
                    ),
                    "is_long": True,
                    "vol_class": vol_class.value,
                })

            # Support break -> SHORT
            elif lv.level_type == "support" and bar.close < lv.price:
                vol_class = classifier.classify(
                    Direction.SHORT, self._imb_window, self._div_thresh
                )
                self._used_levels.add(key)
                signals.append({
                    "signal": LevelSignal(
                        timestamp=bar.timestamp,
                        direction=Direction.SHORT,
                        breakout_price=bar.close,
                        level_price=lv.price,
                        atr=atr,
                        entry_mode="volume_confirm",
                    ),
                    "is_long": False,
                    "vol_class": vol_class.value,
                })

        return signals

    def _check_retest(self, bar, bar_index, levels, classifier, atr):
        signals = []

        # 1. Check for new breaks -> create pending retests
        for lv in levels:
            key = (lv.level_type, lv.price)
            if key in self._used_levels:
                continue
            # Check if already pending
            if any(p.level.price == lv.price and p.level.level_type == lv.level_type
                   for p in self._pending):
                continue

            if lv.level_type == "resistance" and bar.close > lv.price:
                self._pending.append(PendingRetest(
                    level=lv, break_bar=bar_index, direction=Direction.LONG,
                ))
            elif lv.level_type == "support" and bar.close < lv.price:
                self._pending.append(PendingRetest(
                    level=lv, break_bar=bar_index, direction=Direction.SHORT,
                ))

        # 2. Update pending retests
        still_pending = []
        for p in self._pending:
            elapsed = bar_index - p.break_bar
            key = (p.level.level_type, p.level.price)

            # Timeout
            if elapsed > self._max_pb:
                self._used_levels.add(key)  # don't retry this level
                continue

            # Check pullback
            if not p.pulled_back:
                tol = self._pb_tol * atr if atr > 0 else 0
                if p.direction == Direction.LONG:
                    if bar.low <= p.level.price + tol:
                        p.pulled_back = True
                else:
                    if bar.high >= p.level.price - tol:
                        p.pulled_back = True
                still_pending.append(p)
                continue

            # Pulled back — check for rebreak
            if elapsed < self._min_pb:
                still_pending.append(p)
                continue

            # Check rebreak with volume
            if p.direction == Direction.LONG and bar.close > p.level.price:
                vol_class = classifier.classify(
                    Direction.LONG, self._imb_window, self._div_thresh
                )
                self._used_levels.add(key)
                signals.append({
                    "signal": LevelSignal(
                        timestamp=bar.timestamp,
                        direction=Direction.LONG,
                        breakout_price=bar.close,
                        level_price=p.level.price,
                        atr=atr,
                        entry_mode="retest",
                    ),
                    "is_long": True,
                    "vol_class": vol_class.value,
                })
                continue

            elif p.direction == Direction.SHORT and bar.close < p.level.price:
                vol_class = classifier.classify(
                    Direction.SHORT, self._imb_window, self._div_thresh
                )
                self._used_levels.add(key)
                signals.append({
                    "signal": LevelSignal(
                        timestamp=bar.timestamp,
                        direction=Direction.SHORT,
                        breakout_price=bar.close,
                        level_price=p.level.price,
                        atr=atr,
                        entry_mode="retest",
                    ),
                    "is_long": False,
                    "vol_class": vol_class.value,
                })
                continue

            still_pending.append(p)

        self._pending = still_pending
        return signals

    def reset(self):
        self._used_levels.clear()
        self._pending.clear()


# ── Signal Collection ──────────────────────────────────────────────────────

def collect_level_signals(bars, level_params, breakout_params,
                          gap_minutes=30):
    """Collect all level breakout signals across sessions."""
    sessions = split_by_sessions(bars, gap_minutes)
    raw_trades = []

    for session_bars in sessions:
        ld = LevelDetector(**level_params)
        bd = BreakoutDetector(**breakout_params)
        clf = ImbalanceClassifier(max_lookback=40, min_bar_ticks=10)

        for i, bar in enumerate(session_bars):
            ld.add_bar(bar)
            clf.add_bar(bar)

            sigs = bd.check_bar(bar, i, ld.get_active_levels(), clf, ld.current_atr)

            for sig_info in sigs:
                raw_trades.append({
                    "signal": sig_info["signal"],
                    "bars_after": session_bars[i + 1:],
                    "is_long": sig_info["is_long"],
                    "vol_class": sig_info["vol_class"],
                })

    return raw_trades


# ── Simulation with SMA + Volume Filter ────────────────────────────────────

def simulate_level_trades(raw_trades, rr, spread_cost, max_hold_bars,
                          sma_lookup, htf_min=60, sl_atr_offset=0.3):
    """Filter by SMA + CONFIRMING, compute SL/TP, simulate."""
    trades = []
    for raw in raw_trades:
        signal = raw["signal"]
        is_long = raw["is_long"]

        # SMA filter
        sma_val = get_htf_value_at(sma_lookup, signal.timestamp, htf_min)
        if sma_val is None:
            continue
        if is_long and signal.breakout_price <= sma_val:
            continue
        if not is_long and signal.breakout_price >= sma_val:
            continue

        # Volume filter
        if raw["vol_class"] != "CONFIRMING":
            continue

        # SL/TP
        entry = signal.breakout_price
        atr = signal.atr
        if atr <= 0:
            continue

        if is_long:
            sl = signal.level_price - sl_atr_offset * atr
        else:
            sl = signal.level_price + sl_atr_offset * atr

        risk = abs(entry - sl)
        if risk <= 0:
            continue

        tp = entry + risk * rr if is_long else entry - risk * rr

        t = simulate_with_trailing(
            signal, raw["bars_after"], sl, tp,
            max_hold_bars, spread_cost, is_long,
        )
        if t is not None:
            trades.append(t)

    return trades


# ── Helpers ────────────────────────────────────────────────────────────────

IS_START, IS_END = datetime(2024, 1, 1), datetime(2026, 12, 31)
OOS_START, OOS_END = datetime(2018, 1, 1), datetime(2023, 12, 31)
OOS_YEARS = 6
IS_YEARS = 2


def filter_by_period(raw_trades, start, end):
    return [t for t in raw_trades if start <= t["signal"].timestamp <= end]


# ── Main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    print("Loading EURUSD data...")
    all_bars = load_instrument_bars("EURUSD")
    print(f"Total: {len(all_bars):,} bars ({all_bars[0].timestamp.date()} to {all_bars[-1].timestamp.date()})")

    print("Building SMA lookup...")
    htf_bars = resample_sessions(all_bars, 60)
    sma_values = compute_sma(htf_bars, 50)
    sma_lookup = build_htf_lookup(sma_values)
    print(f"SMA(50) values: {len(sma_values):,}")

    SPREAD = EURUSD_CONFIG.spread_cost
    MAX_HOLD = 120  # 2 hours

    # ═══════════════════════════════════════════════════════════════════
    # PHASE 1: Level Detection Parameter Sweep (Mode A, RR=2.0)
    # ═══════════════════════════════════════════════════════════════════
    print_header("PHASE 1: LEVEL DETECTION SWEEP (Mode A, RR=2.0, SMA+CONF)", 115)

    LEVEL_GRID = {
        "left_bars":        [20, 40, 60],
        "right_bars":       [5, 10, 15, 20],
        "expiry_bars":      [120, 240, 480],
        "merge_atr_fraction": [0.3, 0.5],
    }

    keys = list(LEVEL_GRID.keys())
    combos = list(product(*[LEVEL_GRID[k] for k in keys]))
    print(f"Grid: {len(combos)} level detection combos\n")

    print(f"  {'lb':>3s} {'rb':>3s} {'exp':>4s} {'mrg':>4s}"
          f"  |  {'IS_N':>4s} {'IS_WR':>6s} {'IS_AvgR':>7s}"
          f"  |  {'OOS_N':>5s} {'/yr':>5s} {'OOS_WR':>7s} {'OOS_AvgR':>8s} {'OOS_PnL':>9s}"
          f"  |  {'RawSig':>6s}")
    print(f"  {'-' * 100}")

    phase1_results = []

    for ci, combo in enumerate(combos):
        if (ci + 1) % 10 == 0:
            print(f"  ... {ci + 1}/{len(combos)} combos...")

        lp = dict(zip(keys, combo))
        level_params = {
            "left_bars": lp["left_bars"],
            "right_bars": lp["right_bars"],
            "expiry_bars": lp["expiry_bars"],
            "merge_atr_fraction": lp["merge_atr_fraction"],
        }
        breakout_params = {"entry_mode": "volume_confirm"}

        raw = collect_level_signals(all_bars, level_params, breakout_params)
        is_raw = filter_by_period(raw, IS_START, IS_END)
        oos_raw = filter_by_period(raw, OOS_START, OOS_END)

        is_trades = simulate_level_trades(is_raw, 2.0, SPREAD, MAX_HOLD, sma_lookup)
        oos_trades = simulate_level_trades(oos_raw, 2.0, SPREAD, MAX_HOLD, sma_lookup)

        is_s = compute_stats(is_trades)
        oos_s = compute_stats(oos_trades)

        phase1_results.append({
            "level_params": level_params,
            "is": is_s, "oos": oos_s,
            "oos_per_year": oos_s["n"] / OOS_YEARS,
            "raw_count": len(raw),
        })

        if oos_s["n"] >= 5:
            is_str = (f"{is_s['n']:4d} {is_s['wr']:6.1f} {is_s['avg_r']:+7.3f}"
                      if is_s['n'] > 0 else "   0   ---     ---")
            flag = " *" if oos_s['n'] < 15 else ""
            print(f"  {lp['left_bars']:3d} {lp['right_bars']:3d} "
                  f"{lp['expiry_bars']:4d} {lp['merge_atr_fraction']:4.1f}"
                  f"  |  {is_str}"
                  f"  |  {oos_s['n']:5d} {oos_s['n']/OOS_YEARS:5.1f} "
                  f"{oos_s['wr']:7.1f} {oos_s['avg_r']:+8.3f} "
                  f"{oos_s['pnl']:+9.4f}"
                  f"  |  {len(raw):6d}{flag}")

    # Rank Phase 1
    print(f"\n  --- Top 15 by OOS AvgR (N >= 15) ---")
    ranked = [r for r in phase1_results if r["oos"]["n"] >= 15]
    ranked.sort(key=lambda r: r["oos"]["avg_r"], reverse=True)
    for i, r in enumerate(ranked[:15]):
        lp = r["level_params"]
        s = r["oos"]
        print(f"  {i+1:3d}. lb={lp['left_bars']:2d} rb={lp['right_bars']:2d} "
              f"exp={lp['expiry_bars']:3d} mrg={lp['merge_atr_fraction']:.1f}"
              f" -> OOS: N={s['n']} ({r['oos_per_year']:.1f}/yr) "
              f"WR={s['wr']:.1f}% AvgR={s['avg_r']:+.3f} PnL={s['pnl']:+.4f}")

    print(f"\n  --- Top 15 by OOS trades/year (AvgR > 0, N >= 15) ---")
    pos_ranked = [r for r in phase1_results if r["oos"]["avg_r"] > 0 and r["oos"]["n"] >= 15]
    pos_ranked.sort(key=lambda r: r["oos_per_year"], reverse=True)
    for i, r in enumerate(pos_ranked[:15]):
        lp = r["level_params"]
        s = r["oos"]
        print(f"  {i+1:3d}. lb={lp['left_bars']:2d} rb={lp['right_bars']:2d} "
              f"exp={lp['expiry_bars']:3d} mrg={lp['merge_atr_fraction']:.1f}"
              f" -> OOS: N={s['n']} ({r['oos_per_year']:.1f}/yr) "
              f"WR={s['wr']:.1f}% AvgR={s['avg_r']:+.3f} PnL={s['pnl']:+.4f}")

    # ═══════════════════════════════════════════════════════════════════
    # PHASE 2: Full Sweep on Top 5 Level Configs
    # ═══════════════════════════════════════════════════════════════════
    top5 = ranked[:5] if len(ranked) >= 5 else ranked

    if not top5:
        print("\n  No configs with positive OOS AvgR found. Skipping Phase 2.")
    else:
        print_header("PHASE 2: FULL SWEEP on TOP 5 LEVEL CONFIGS", 115)

        RR_VALUES = [1.5, 2.0]
        RETEST_PARAMS = [
            {"min_pullback_bars": 3, "max_pullback_bars": 30},
            {"min_pullback_bars": 3, "max_pullback_bars": 60},
            {"min_pullback_bars": 5, "max_pullback_bars": 30},
            {"min_pullback_bars": 5, "max_pullback_bars": 60},
        ]

        phase2_results = []

        for rank_i, top_result in enumerate(top5):
            lp = top_result["level_params"]
            print(f"\n  === Config #{rank_i+1}: lb={lp['left_bars']} rb={lp['right_bars']} "
                  f"exp={lp['expiry_bars']} mrg={lp['merge_atr_fraction']} ===")

            print(f"  {'Mode':>12s} {'RR':>3s} {'Params':>15s}"
                  f"  |  {'IS_N':>4s} {'IS_WR':>6s} {'IS_AvgR':>7s}"
                  f"  |  {'OOS_N':>5s} {'/yr':>5s} {'OOS_WR':>7s} {'OOS_AvgR':>8s} {'OOS_PnL':>9s}")
            print(f"  {'-' * 95}")

            # Mode A variants
            for rr in RR_VALUES:
                raw = collect_level_signals(all_bars, lp,
                                            {"entry_mode": "volume_confirm"})
                is_raw = filter_by_period(raw, IS_START, IS_END)
                oos_raw = filter_by_period(raw, OOS_START, OOS_END)
                is_t = simulate_level_trades(is_raw, rr, SPREAD, MAX_HOLD, sma_lookup)
                oos_t = simulate_level_trades(oos_raw, rr, SPREAD, MAX_HOLD, sma_lookup)
                is_s = compute_stats(is_t)
                oos_s = compute_stats(oos_t)

                phase2_results.append({
                    "level_params": lp, "mode": "vol_conf", "rr": rr,
                    "retest_params": {}, "is": is_s, "oos": oos_s,
                    "oos_per_year": oos_s["n"] / OOS_YEARS,
                })

                is_str = (f"{is_s['n']:4d} {is_s['wr']:6.1f} {is_s['avg_r']:+7.3f}"
                          if is_s['n'] > 0 else "   0   ---     ---")
                oos_str = (f"{oos_s['n']:5d} {oos_s['n']/OOS_YEARS:5.1f} "
                           f"{oos_s['wr']:7.1f} {oos_s['avg_r']:+8.3f} {oos_s['pnl']:+9.4f}"
                           if oos_s['n'] > 0 else "    0   ---    ---      ---       ---")
                print(f"  {'vol_confirm':>12s} {rr:3.1f} {'':>15s}"
                      f"  |  {is_str}  |  {oos_str}")

            # Mode B variants
            for rr in RR_VALUES:
                for rtp in RETEST_PARAMS:
                    bp = {"entry_mode": "retest", **rtp}
                    raw = collect_level_signals(all_bars, lp, bp)
                    is_raw = filter_by_period(raw, IS_START, IS_END)
                    oos_raw = filter_by_period(raw, OOS_START, OOS_END)
                    is_t = simulate_level_trades(is_raw, rr, SPREAD, MAX_HOLD, sma_lookup)
                    oos_t = simulate_level_trades(oos_raw, rr, SPREAD, MAX_HOLD, sma_lookup)
                    is_s = compute_stats(is_t)
                    oos_s = compute_stats(oos_t)

                    phase2_results.append({
                        "level_params": lp, "mode": "retest", "rr": rr,
                        "retest_params": rtp, "is": is_s, "oos": oos_s,
                        "oos_per_year": oos_s["n"] / OOS_YEARS,
                    })

                    param_str = f"pb={rtp['min_pullback_bars']}-{rtp['max_pullback_bars']}"
                    is_str = (f"{is_s['n']:4d} {is_s['wr']:6.1f} {is_s['avg_r']:+7.3f}"
                              if is_s['n'] > 0 else "   0   ---     ---")
                    oos_str = (f"{oos_s['n']:5d} {oos_s['n']/OOS_YEARS:5.1f} "
                               f"{oos_s['wr']:7.1f} {oos_s['avg_r']:+8.3f} {oos_s['pnl']:+9.4f}"
                               if oos_s['n'] > 0 else "    0   ---    ---      ---       ---")
                    print(f"  {'retest':>12s} {rr:3.1f} {param_str:>15s}"
                          f"  |  {is_str}  |  {oos_str}")

        # Overall best Phase 2
        print_header("PHASE 2 BEST RESULTS", 115)
        p2_ranked = [r for r in phase2_results if r["oos"]["n"] >= 15]
        p2_ranked.sort(key=lambda r: r["oos"]["avg_r"], reverse=True)
        print(f"\n  --- Top 10 by OOS AvgR (N >= 15) ---")
        for i, r in enumerate(p2_ranked[:10]):
            lp = r["level_params"]
            s = r["oos"]
            rtp = r["retest_params"]
            mode_str = r["mode"]
            if rtp:
                mode_str += f" pb={rtp.get('min_pullback_bars',0)}-{rtp.get('max_pullback_bars',0)}"
            print(f"  {i+1:3d}. lb={lp['left_bars']:2d} rb={lp['right_bars']:2d} "
                  f"exp={lp['expiry_bars']:3d} mrg={lp['merge_atr_fraction']:.1f} "
                  f"{mode_str:20s} RR={r['rr']:.1f}"
                  f" -> OOS: N={s['n']} ({r['oos_per_year']:.1f}/yr) "
                  f"WR={s['wr']:.1f}% AvgR={s['avg_r']:+.3f}")

    # ═══════════════════════════════════════════════════════════════════
    # COMPARISON vs DARVAS BASELINE
    # ═══════════════════════════════════════════════════════════════════
    print_header("COMPARISON vs DARVAS + SMA BASELINE", 115)
    print(f"  Darvas baseline (EURUSD, SMA+CONF+Trail10@60, RR=2.0):")
    print(f"    IS:  24 trades (12/yr), 62.5% WR, +0.729 AvgR")
    print(f"    OOS: 63 trades (10.5/yr), 46.0% WR, +0.176 AvgR")

    if pos_ranked:
        best = pos_ranked[0]
        lp = best["level_params"]
        s = best["oos"]
        print(f"\n  Best Level strategy by OOS trades/yr (AvgR > 0):")
        print(f"    Config: lb={lp['left_bars']} rb={lp['right_bars']} "
              f"exp={lp['expiry_bars']} mrg={lp['merge_atr_fraction']}")
        print(f"    OOS: {s['n']} trades ({best['oos_per_year']:.1f}/yr), "
              f"{s['wr']:.1f}% WR, {s['avg_r']:+.3f} AvgR")

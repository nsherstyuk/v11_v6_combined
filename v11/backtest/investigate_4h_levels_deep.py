"""Deep Dive: 4H Level Strategy Improvements.

Starting from the best config found (240m lb=10 rb=10 exp=72h cd=60 RR=2.0):
    OOS: 652 trades (108.7/yr), 40.2% WR, +0.049 AvgR

Investigates:
    Part A: SL tightness (0.1, 0.2, 0.3, 0.5, 1.0 ATR offset)
    Part B: Retest mode (break -> pullback -> rebreak at 4H levels)
    Part C: Session filter (exclude worst hours)
    Part D: R:R variants (1.5, 2.0, 2.5, 3.0)
    Part E: Volume filter variants (ALL, CONFIRMING, NO-DIVERGENT)
    Part F: Combined best stack
    Part G: Year-by-year OOS breakdown
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from datetime import datetime, timedelta
from typing import List

from v11.backtest.data_loader import load_instrument_bars
from v11.backtest.htf_utils import (
    resample_bars, compute_sma, build_htf_lookup, get_htf_value_at,
    simulate_with_trailing, compute_stats, print_header, _floor_timestamp,
)
from v11.core.imbalance_classifier import ImbalanceClassifier
from v11.core.types import Direction
from v11.config.strategy_config import EURUSD_CONFIG

# Import from htf_levels investigation
from v11.backtest.investigate_htf_levels import (
    HTFSwingDetector, HTFLevelSignal, build_htf_level_timeline,
)

IS_START, IS_END = datetime(2024, 1, 1), datetime(2026, 12, 31)
OOS_START, OOS_END = datetime(2018, 1, 1), datetime(2023, 12, 31)
OOS_YEARS = 6
IS_YEARS = 2
SPREAD = EURUSD_CONFIG.spread_cost
MAX_HOLD = 120

# Best base config from previous investigation
HTF_MIN = 240
LEFT_BARS = 10
RIGHT_BARS = 10
EXPIRY_HOURS = 72
MERGE_PIPS = 0.0005
COOLDOWN = 60


def scan_4h_levels(bars_1m, timeline, sma_lookup,
                   cooldown_bars=60, sl_atr_offset=0.3,
                   vol_filter="CONFIRMING",
                   session_filter=None,
                   retest_mode=False, min_pullback_bars=5, max_pullback_bars=60,
                   pullback_atr_tol=0.3):
    """Scan 1-min bars for 4H level breakouts with various filters.

    Args:
        vol_filter: "ALL", "CONFIRMING", or "NO_DIVERGENT"
        session_filter: None or callable(hour) -> bool
        retest_mode: if True, require pullback + rebreak
    """
    raw_trades = []
    clf = ImbalanceClassifier(max_lookback=40, min_bar_ticks=10)

    atr = 0.0
    atr_count = 0
    prev_close = 0.0
    atr_period = 60

    used_levels = {}  # price -> last bar index

    # Retest tracking
    pending_retests = []  # list of dicts

    for i, bar in enumerate(bars_1m):
        clf.add_bar(bar)

        # ATR
        if prev_close > 0:
            tr = max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))
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

        # Session filter
        if session_filter and not session_filter(bar.timestamp.hour):
            continue

        # Get HTF levels
        floored = _floor_timestamp(bar.timestamp, HTF_MIN)
        prev_ts = floored - timedelta(minutes=HTF_MIN)
        htf_levels = timeline.get(prev_ts, [])

        # SMA
        sma_val = get_htf_value_at(sma_lookup, bar.timestamp, 60)

        if retest_mode:
            # Track new breaks
            for lv in htf_levels:
                if lv.price in used_levels and i - used_levels[lv.price] < cooldown_bars:
                    continue
                if any(p["price"] == lv.price for p in pending_retests):
                    continue

                if lv.level_type == "resistance" and bar.close > lv.price:
                    pending_retests.append({
                        "price": lv.price, "level_type": lv.level_type,
                        "break_bar": i, "direction": "long",
                        "pulled_back": False, "atr_at_break": atr,
                    })
                elif lv.level_type == "support" and bar.close < lv.price:
                    pending_retests.append({
                        "price": lv.price, "level_type": lv.level_type,
                        "break_bar": i, "direction": "short",
                        "pulled_back": False, "atr_at_break": atr,
                    })

            # Process pending retests
            still_pending = []
            for p in pending_retests:
                elapsed = i - p["break_bar"]
                if elapsed > max_pullback_bars:
                    used_levels[p["price"]] = i
                    continue

                tol = pullback_atr_tol * atr
                if not p["pulled_back"]:
                    if p["direction"] == "long" and bar.low <= p["price"] + tol:
                        p["pulled_back"] = True
                    elif p["direction"] == "short" and bar.high >= p["price"] - tol:
                        p["pulled_back"] = True
                    still_pending.append(p)
                    continue

                if elapsed < min_pullback_bars:
                    still_pending.append(p)
                    continue

                # Check rebreak
                signal = None
                if p["direction"] == "long" and bar.close > p["price"]:
                    if sma_val is not None and bar.close <= sma_val:
                        still_pending.append(p)
                        continue
                    vc = clf.classify(Direction.LONG, 3, 0.50).value
                    if vol_filter == "CONFIRMING" and vc != "CONFIRMING":
                        still_pending.append(p)
                        continue
                    if vol_filter == "NO_DIVERGENT" and vc == "DIVERGENT":
                        still_pending.append(p)
                        continue
                    signal = HTFLevelSignal(
                        timestamp=bar.timestamp, direction=Direction.LONG,
                        breakout_price=bar.close, level_price=p["price"],
                        atr=atr, source="4h_retest",
                    )
                elif p["direction"] == "short" and bar.close < p["price"]:
                    if sma_val is not None and bar.close >= sma_val:
                        still_pending.append(p)
                        continue
                    vc = clf.classify(Direction.SHORT, 3, 0.50).value
                    if vol_filter == "CONFIRMING" and vc != "CONFIRMING":
                        still_pending.append(p)
                        continue
                    if vol_filter == "NO_DIVERGENT" and vc == "DIVERGENT":
                        still_pending.append(p)
                        continue
                    signal = HTFLevelSignal(
                        timestamp=bar.timestamp, direction=Direction.SHORT,
                        breakout_price=bar.close, level_price=p["price"],
                        atr=atr, source="4h_retest",
                    )

                if signal is not None:
                    used_levels[p["price"]] = i
                    is_long = signal.direction == Direction.LONG
                    sl = (p["price"] - sl_atr_offset * atr) if is_long else (p["price"] + sl_atr_offset * atr)
                    raw_trades.append({
                        "signal": signal,
                        "bars_after": bars_1m[i + 1:i + 1 + MAX_HOLD + 10],
                        "is_long": is_long, "sl": sl,
                    })
                else:
                    still_pending.append(p)

            pending_retests = still_pending

        else:
            # Direct breakout mode
            for lv in htf_levels:
                if lv.price in used_levels and i - used_levels[lv.price] < cooldown_bars:
                    continue

                signal = None
                if lv.level_type == "resistance" and bar.close > lv.price:
                    if sma_val is not None and bar.close <= sma_val:
                        continue
                    vc = clf.classify(Direction.LONG, 3, 0.50).value
                    if vol_filter == "CONFIRMING" and vc != "CONFIRMING":
                        continue
                    if vol_filter == "NO_DIVERGENT" and vc == "DIVERGENT":
                        continue
                    signal = HTFLevelSignal(
                        timestamp=bar.timestamp, direction=Direction.LONG,
                        breakout_price=bar.close, level_price=lv.price,
                        atr=atr, source="4h_direct",
                    )
                elif lv.level_type == "support" and bar.close < lv.price:
                    if sma_val is not None and bar.close >= sma_val:
                        continue
                    vc = clf.classify(Direction.SHORT, 3, 0.50).value
                    if vol_filter == "CONFIRMING" and vc != "CONFIRMING":
                        continue
                    if vol_filter == "NO_DIVERGENT" and vc == "DIVERGENT":
                        continue
                    signal = HTFLevelSignal(
                        timestamp=bar.timestamp, direction=Direction.SHORT,
                        breakout_price=bar.close, level_price=lv.price,
                        atr=atr, source="4h_direct",
                    )

                if signal is not None:
                    used_levels[lv.price] = i
                    is_long = signal.direction == Direction.LONG
                    sl = (lv.price - sl_atr_offset * atr) if is_long else (lv.price + sl_atr_offset * atr)
                    raw_trades.append({
                        "signal": signal,
                        "bars_after": bars_1m[i + 1:i + 1 + MAX_HOLD + 10],
                        "is_long": is_long, "sl": sl,
                    })

    return raw_trades


def simulate(raw_trades, rr):
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
        t = simulate_with_trailing(signal, raw["bars_after"], sl, tp,
                                   MAX_HOLD, SPREAD, is_long)
        if t is not None:
            trades.append(t)
    return trades


def filter_period(raw, start, end):
    return [t for t in raw if start <= t["signal"].timestamp <= end]


def pr(label, is_t, oos_t):
    is_s = compute_stats(is_t)
    oos_s = compute_stats(oos_t)
    is_str = (f"{is_s['n']:4d} {is_s['wr']:6.1f} {is_s['avg_r']:+7.3f}"
              if is_s['n'] > 0 else "   0   ---     ---")
    oos_str = (f"{oos_s['n']:5d} {oos_s['n']/OOS_YEARS:5.1f} {oos_s['wr']:7.1f} {oos_s['avg_r']:+8.3f}"
               if oos_s['n'] > 0 else "    0   ---    ---      ---")
    flag = " *" if oos_s['n'] < 15 else ""
    print(f"  {label:45s}  |  {is_str}  |  {oos_str}{flag}")


if __name__ == "__main__":
    print("Loading EURUSD...")
    all_bars = load_instrument_bars("EURUSD")
    print(f"{len(all_bars):,} bars")

    print("Building 4H level timeline...")
    timeline = build_htf_level_timeline(all_bars, HTF_MIN, LEFT_BARS, RIGHT_BARS,
                                        EXPIRY_HOURS, MERGE_PIPS)
    print(f"Timeline: {len(timeline)} HTF bars")

    print("Building SMA lookup...")
    from v11.backtest.htf_utils import resample_sessions
    htf_60 = resample_sessions(all_bars, 60)
    sma_vals = compute_sma(htf_60, 50)
    sma_lookup = build_htf_lookup(sma_vals)

    hdr = (f"  {'Config':45s}  |  {'IS_N':>4s} {'IS_WR':>6s} {'IS_AvgR':>7s}"
           f"  |  {'OOS_N':>5s} {'/yr':>5s} {'OOS_WR':>7s} {'OOS_AvgR':>8s}")

    # ═══════ PART A: SL Tightness ═══════
    print_header("PART A: SL ATR OFFSET (direct mode, RR=2.0, SMA+CONF)")
    print(hdr); print(f"  {'-' * 95}")

    for sl_off in [0.1, 0.2, 0.3, 0.5, 1.0]:
        raw = scan_4h_levels(all_bars, timeline, sma_lookup, sl_atr_offset=sl_off)
        is_r = filter_period(raw, IS_START, IS_END)
        oos_r = filter_period(raw, OOS_START, OOS_END)
        pr(f"SL={sl_off} ATR", simulate(is_r, 2.0), simulate(oos_r, 2.0))

    # ═══════ PART B: Retest Mode ═══════
    print_header("PART B: RETEST MODE (SMA+CONF, RR=2.0)")
    print(hdr); print(f"  {'-' * 95}")

    # Baseline direct
    raw_direct = scan_4h_levels(all_bars, timeline, sma_lookup)
    pr("Direct (baseline)",
       simulate(filter_period(raw_direct, IS_START, IS_END), 2.0),
       simulate(filter_period(raw_direct, OOS_START, OOS_END), 2.0))

    for min_pb in [3, 5, 10]:
        for max_pb in [30, 60, 120]:
            raw = scan_4h_levels(all_bars, timeline, sma_lookup,
                                 retest_mode=True, min_pullback_bars=min_pb,
                                 max_pullback_bars=max_pb)
            is_r = filter_period(raw, IS_START, IS_END)
            oos_r = filter_period(raw, OOS_START, OOS_END)
            pr(f"Retest pb={min_pb}-{max_pb}",
               simulate(is_r, 2.0), simulate(oos_r, 2.0))

    # ═══════ PART C: Session Filter ═══════
    print_header("PART C: SESSION FILTER (direct, SMA+CONF, RR=2.0)")
    print(hdr); print(f"  {'-' * 95}")

    sessions = [
        ("All hours", None),
        ("No Asian (>=08)", lambda h: h >= 8),
        ("London+NY (08-16)", lambda h: 8 <= h <= 16),
        ("No London (excl 08-12)", lambda h: h < 8 or h > 12),
        ("NY only (13-16)", lambda h: 13 <= h <= 16),
    ]

    for name, sfilt in sessions:
        raw = scan_4h_levels(all_bars, timeline, sma_lookup, session_filter=sfilt)
        is_r = filter_period(raw, IS_START, IS_END)
        oos_r = filter_period(raw, OOS_START, OOS_END)
        pr(name, simulate(is_r, 2.0), simulate(oos_r, 2.0))

    # ═══════ PART D: R:R Variants ═══════
    print_header("PART D: R:R VARIANTS (direct, SMA+CONF)")
    print(hdr); print(f"  {'-' * 95}")

    raw_base = scan_4h_levels(all_bars, timeline, sma_lookup)
    is_base = filter_period(raw_base, IS_START, IS_END)
    oos_base = filter_period(raw_base, OOS_START, OOS_END)

    for rr in [1.0, 1.5, 2.0, 2.5, 3.0]:
        pr(f"R:R={rr}", simulate(is_base, rr), simulate(oos_base, rr))

    # ═══════ PART E: Volume Filter Variants ═══════
    print_header("PART E: VOLUME FILTER (direct, RR=2.0)")
    print(hdr); print(f"  {'-' * 95}")

    for vf_name in ["ALL", "CONFIRMING", "NO_DIVERGENT"]:
        raw = scan_4h_levels(all_bars, timeline, sma_lookup, vol_filter=vf_name)
        is_r = filter_period(raw, IS_START, IS_END)
        oos_r = filter_period(raw, OOS_START, OOS_END)
        pr(f"Vol={vf_name}", simulate(is_r, 2.0), simulate(oos_r, 2.0))

    # ═══════ PART F: Best Combos ═══════
    print_header("PART F: COMBINED STACKS")
    print(hdr); print(f"  {'-' * 95}")

    combos = [
        ("Direct SL=0.3 RR=2.0 CONF", {"sl_atr_offset": 0.3, "vol_filter": "CONFIRMING"}, 2.0, False, {}),
        ("Direct SL=0.2 RR=2.0 CONF", {"sl_atr_offset": 0.2, "vol_filter": "CONFIRMING"}, 2.0, False, {}),
        ("Direct SL=0.3 RR=2.5 CONF", {"sl_atr_offset": 0.3, "vol_filter": "CONFIRMING"}, 2.5, False, {}),
        ("Direct SL=0.3 RR=2.0 ALL", {"sl_atr_offset": 0.3, "vol_filter": "ALL"}, 2.0, False, {}),
        ("Retest pb=5-60 SL=0.3 RR=2.0 CONF", {"sl_atr_offset": 0.3, "vol_filter": "CONFIRMING"}, 2.0, True, {"min_pullback_bars": 5, "max_pullback_bars": 60}),
        ("Retest pb=5-60 SL=0.3 RR=2.5 CONF", {"sl_atr_offset": 0.3, "vol_filter": "CONFIRMING"}, 2.5, True, {"min_pullback_bars": 5, "max_pullback_bars": 60}),
        ("Retest pb=3-60 SL=0.3 RR=2.0 CONF", {"sl_atr_offset": 0.3, "vol_filter": "CONFIRMING"}, 2.0, True, {"min_pullback_bars": 3, "max_pullback_bars": 60}),
        ("Direct NoAsian SL=0.3 RR=2.0 CONF", {"sl_atr_offset": 0.3, "vol_filter": "CONFIRMING", "session_filter": lambda h: h >= 8}, 2.0, False, {}),
        ("Direct NoLondon SL=0.3 RR=2.0 CONF", {"sl_atr_offset": 0.3, "vol_filter": "CONFIRMING", "session_filter": lambda h: h < 8 or h > 12}, 2.0, False, {}),
    ]

    for label, kwargs, rr, retest, rt_kw in combos:
        raw = scan_4h_levels(all_bars, timeline, sma_lookup,
                             retest_mode=retest, **kwargs, **rt_kw)
        is_r = filter_period(raw, IS_START, IS_END)
        oos_r = filter_period(raw, OOS_START, OOS_END)
        pr(label, simulate(is_r, rr), simulate(oos_r, rr))

    # ═══════ PART G: Year-by-Year OOS ═══════
    print_header("PART G: YEAR-BY-YEAR OOS (direct, SMA+CONF, RR=2.0)")
    raw_all = scan_4h_levels(all_bars, timeline, sma_lookup)
    print(f"  {'Year':10s} {'N':>5s} {'WR%':>6s} {'AvgR':>7s} {'PnL':>8s}")
    print(f"  {'-' * 40}")

    for year in range(2018, 2027):
        start = datetime(year, 1, 1)
        end = datetime(year, 12, 31)
        yr_raw = filter_period(raw_all, start, end)
        yr_trades = simulate(yr_raw, 2.0)
        s = compute_stats(yr_trades)
        if s['n'] > 0:
            oos_tag = " (OOS)" if year <= 2023 else " (IS)"
            print(f"  {year}{oos_tag:6s} {s['n']:5d} {s['wr']:6.1f} {s['avg_r']:+7.3f} {s['pnl']:+8.4f}")

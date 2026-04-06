"""Out-of-sample validation for EURUSD best config.

Params optimized on 2024-2026. OOS = 2018-2023 (never touched).
Also runs year-by-year breakdown for consistency check.
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from datetime import datetime
from dataclasses import replace
from v11.backtest.data_loader import load_instrument_bars, split_by_sessions
from v11.core.darvas_detector import DarvasDetector
from v11.core.imbalance_classifier import ImbalanceClassifier
from v11.core.types import Direction
from v11.config.strategy_config import EURUSD_CONFIG
import pandas as pd

config = replace(EURUSD_CONFIG, top_confirm_bars=20, bottom_confirm_bars=12,
                 max_box_width_atr=3.0, breakout_confirm_bars=2)


def simulate_with_trailing(signal, bars_after, sl_price, tp_price,
                           max_hold, spread_cost, is_long,
                           trail_lookback=10, tighten_after_bars=60):
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

        if not sl_was_tightened and i >= tighten_after_bars:
            current_unrealized = (bar.close - entry) if is_long else (entry - bar.close)
            if current_unrealized > 0:
                sl_was_tightened = True
                lookback_start = max(0, i - trail_lookback)
                recent = bars_after[lookback_start:i + 1]
                if is_long:
                    swing_low = min(b.low for b in recent)
                    current_sl = max(current_sl, max(entry, swing_low))
                else:
                    swing_high = max(b.high for b in recent)
                    current_sl = min(current_sl, min(entry, swing_high))

        if sl_was_tightened and i > tighten_after_bars:
            lookback_start = max(0, i - trail_lookback)
            recent = bars_after[lookback_start:i + 1]
            if is_long:
                swing_low = min(b.low for b in recent)
                current_sl = max(current_sl, max(entry, swing_low))
            else:
                swing_high = max(b.high for b in recent)
                current_sl = min(current_sl, min(entry, swing_high))

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

    return {"pnl": pnl, "pnl_r": pnl_r, "exit_reason": exit_reason,
            "hold_bars": hold_bars, "win": 1 if pnl > 0 else 0,
            "direction": signal.direction.value,
            "entry_time": signal.timestamp}


def run_period(bars, label, rr=2.0):
    """Run the full strategy on a set of bars and return results."""
    sessions = split_by_sessions(bars, gap_minutes=30)

    # Collect signals with volume classification
    raw_trades = []
    for session_bars in sessions:
        det = DarvasDetector(config)
        clf = ImbalanceClassifier(max_lookback=20, min_bar_ticks=config.min_bar_ticks)
        for i, bar in enumerate(session_bars):
            signal = det.add_bar(bar)
            clf.add_bar(bar)
            if signal is not None:
                is_long = signal.direction == Direction.LONG
                vol_class = clf.classify(
                    signal.direction, config.imbalance_window, config.divergence_threshold
                ).value
                raw_trades.append({
                    "signal": signal,
                    "bars_after": session_bars[i + 1:],
                    "is_long": is_long,
                    "vol_class": vol_class,
                })

    # Run three variants: ALL, CONFIRMING-only (with trail), CONFIRMING-only (baseline)
    results = {}
    for filter_name, filter_fn in [
        ("ALL+Trail10@60", lambda vc: True),
        ("CONF+Trail10@60", lambda vc: vc == "CONFIRMING"),
        ("CONF+Baseline", lambda vc: vc == "CONFIRMING"),
    ]:
        use_trail = "Trail" in filter_name
        filtered = [t for t in raw_trades if filter_fn(t["vol_class"])]
        trades = []
        for raw in filtered:
            signal = raw["signal"]
            is_long = raw["is_long"]
            entry = signal.breakout_price
            sl = signal.box.bottom if is_long else signal.box.top
            risk = abs(entry - sl)
            tp = entry + risk * rr if is_long else entry - risk * rr

            if use_trail:
                t = simulate_with_trailing(
                    signal, raw["bars_after"], sl, tp,
                    config.max_hold_bars, config.spread_cost, is_long)
            else:
                # Baseline: no tightening (use huge tighten_after to disable)
                t = simulate_with_trailing(
                    signal, raw["bars_after"], sl, tp,
                    config.max_hold_bars, config.spread_cost, is_long,
                    tighten_after_bars=9999)
            if t is not None:
                trades.append(t)

        if trades:
            df = pd.DataFrame(trades)
            results[filter_name] = {
                "n": len(df),
                "wr": df.win.mean() * 100,
                "avg_r": df.pnl_r.mean(),
                "pnl": df.pnl.sum(),
                "sl": (df.exit_reason == "SL").sum(),
                "slt": (df.exit_reason == "SL_TIGHT").sum(),
                "tp": (df.exit_reason == "TARGET").sum(),
                "tm": (df.exit_reason == "TIME_STOP").sum(),
            }
        else:
            results[filter_name] = {"n": 0, "wr": 0, "avg_r": 0, "pnl": 0,
                                     "sl": 0, "slt": 0, "tp": 0, "tm": 0}

    return results, len(raw_trades)


# Load all EURUSD data
print("Loading all EURUSD data...")
all_bars = load_instrument_bars("EURUSD")
print(f"Total: {len(all_bars):,} bars ({all_bars[0].timestamp.date()} to {all_bars[-1].timestamp.date()})")

# Define periods
periods = [
    ("2018 (OOS)", datetime(2018, 1, 1), datetime(2018, 12, 31)),
    ("2019 (OOS)", datetime(2019, 1, 1), datetime(2019, 12, 31)),
    ("2020 (OOS)", datetime(2020, 1, 1), datetime(2020, 12, 31)),
    ("2021 (OOS)", datetime(2021, 1, 1), datetime(2021, 12, 31)),
    ("2022 (OOS)", datetime(2022, 1, 1), datetime(2022, 12, 31)),
    ("2023 (OOS)", datetime(2023, 1, 1), datetime(2023, 12, 31)),
    ("2024 (IS)",  datetime(2024, 1, 1), datetime(2024, 12, 31)),
    ("2025-26 (IS)", datetime(2025, 1, 1), datetime(2026, 12, 31)),
    ("ALL OOS 2018-2023", datetime(2018, 1, 1), datetime(2023, 12, 31)),
    ("ALL IS 2024-2026",  datetime(2024, 1, 1), datetime(2026, 12, 31)),
    ("FULL 2018-2026",    datetime(2018, 1, 1), datetime(2026, 12, 31)),
]

# Print header
print(f"\n{'='*100}")
print(f"  EURUSD OOS VALIDATION — Config B (tc=20 bc=12 maxW=3.0 brk=2) R:R=2.0")
print(f"{'='*100}")

for variant in ["CONF+Trail10@60", "CONF+Baseline", "ALL+Trail10@60"]:
    print(f"\n  Strategy: {variant}")
    print(f"  {'Period':22s} {'Sigs':>5s} {'N':>4s} {'WR%':>6s} {'AvgR':>7s} {'PnL':>8s} "
          f"{'SL':>4s} {'SLT':>4s} {'TP':>4s} {'TM':>4s}")
    print(f"  {'-'*90}")

    for label, start, end in periods:
        period_bars = [b for b in all_bars if start <= b.timestamp <= end]
        if not period_bars:
            continue
        results, total_sigs = run_period(period_bars, label)
        r = results.get(variant, {})
        if r["n"] > 0:
            print(f"  {label:22s} {total_sigs:5d} {r['n']:4d} {r['wr']:6.1f} {r['avg_r']:+7.3f} "
                  f"{r['pnl']:+8.4f} {r['sl']:4d} {r['slt']:4d} {r['tp']:4d} {r['tm']:4d}")
        else:
            print(f"  {label:22s} {total_sigs:5d}    0   ---     ---      ---    -    -    -    -")

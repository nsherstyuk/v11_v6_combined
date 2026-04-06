"""Combined analysis: CONFIRMING filter + R:R + SL tightening on EURUSD Config B.

Tests all combinations of:
- Volume filter: ALL vs CONFIRMING-only
- R:R: 1.5 vs 2.0
- SL management: Baseline, BE@60, Lock25@60, Lock50@60, Trail10@60, Trail15@60
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
                           tighten_after_bars=60,
                           tighten_mode="none",
                           trail_lookback=10,
                           lock_profit_frac=0.0):
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

    return {"pnl": pnl, "pnl_r": pnl_r, "exit_reason": exit_reason,
            "hold_bars": hold_bars, "win": 1 if pnl > 0 else 0,
            "direction": signal.direction.value}


# Load data
bars = load_instrument_bars("EURUSD", start=datetime(2024, 1, 1))
sessions = split_by_sessions(bars, gap_minutes=30)

sl_variants = [
    ("Baseline",          {"tighten_mode": "none"}),
    ("BE@60",             {"tighten_mode": "breakeven", "tighten_after_bars": 60}),
    ("Lock25@60",         {"tighten_mode": "lock", "tighten_after_bars": 60, "lock_profit_frac": 0.25}),
    ("Lock50@60",         {"tighten_mode": "lock", "tighten_after_bars": 60, "lock_profit_frac": 0.50}),
    ("Trail10@60",        {"tighten_mode": "trail", "tighten_after_bars": 60, "trail_lookback": 10}),
    ("Trail15@60",        {"tighten_mode": "trail", "tighten_after_bars": 60, "trail_lookback": 15}),
]

# First collect all trades with volume labels per session
all_raw_trades = []
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
            all_raw_trades.append({
                "signal": signal,
                "bars_after": session_bars[i + 1:],
                "is_long": is_long,
                "vol_class": vol_class,
            })

print(f"Total signals: {len(all_raw_trades)}")
print(f"  CONFIRMING:    {sum(1 for t in all_raw_trades if t['vol_class'] == 'CONFIRMING')}")
print(f"  DIVERGENT:     {sum(1 for t in all_raw_trades if t['vol_class'] == 'DIVERGENT')}")
print(f"  INDETERMINATE: {sum(1 for t in all_raw_trades if t['vol_class'] == 'INDETERMINATE')}")

for vol_filter_name, vol_filter_fn in [
    ("ALL trades", lambda vc: True),
    ("CONFIRMING only", lambda vc: vc == "CONFIRMING"),
    ("NO-DIVERGENT", lambda vc: vc != "DIVERGENT"),
]:
    filtered = [t for t in all_raw_trades if vol_filter_fn(t["vol_class"])]

    for rr in [1.5, 2.0]:
        print(f"\n{'='*90}")
        print(f"  {vol_filter_name} | R:R={rr} | N_signals={len(filtered)}")
        print(f"{'='*90}")
        print(f"  {'SL Mode':15s} {'N':>4s} {'WR%':>6s} {'AvgR':>7s} {'PnL':>8s} "
              f"{'SL':>4s} {'SLT':>4s} {'TP':>4s} {'TM':>4s} "
              f"{'LongWR':>7s} {'ShortWR':>8s}")
        print(f"  {'-'*85}")

        for sl_label, sl_kwargs in sl_variants:
            trades = []
            for raw in filtered:
                signal = raw["signal"]
                is_long = raw["is_long"]
                entry = signal.breakout_price
                sl = signal.box.bottom if is_long else signal.box.top
                risk = abs(entry - sl)
                tp = entry + risk * rr if is_long else entry - risk * rr

                t = simulate_with_trailing(
                    signal, raw["bars_after"], sl, tp,
                    config.max_hold_bars, config.spread_cost, is_long,
                    **sl_kwargs)
                if t is not None:
                    trades.append(t)

            if trades:
                df = pd.DataFrame(trades)
                n = len(df)
                wr = df.win.mean() * 100
                avg_r = df.pnl_r.mean()
                pnl = df.pnl.sum()
                sl_n = (df.exit_reason == "SL").sum()
                slt_n = (df.exit_reason == "SL_TIGHT").sum()
                tp_n = (df.exit_reason == "TARGET").sum()
                tm_n = (df.exit_reason == "TIME_STOP").sum()
                longs = df[df.direction == "long"]
                shorts = df[df.direction == "short"]
                lwr = longs.win.mean() * 100 if len(longs) > 0 else 0
                swr = shorts.win.mean() * 100 if len(shorts) > 0 else 0
                print(f"  {sl_label:15s} {n:4d} {wr:6.1f} {avg_r:+7.3f} {pnl:+8.4f} "
                      f"{sl_n:4d} {slt_n:4d} {tp_n:4d} {tm_n:4d} "
                      f"{lwr:7.1f} {swr:8.1f}")

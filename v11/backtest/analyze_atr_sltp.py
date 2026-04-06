"""Compare box-based vs ATR-based SL/TP on EURUSD."""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from datetime import datetime
from dataclasses import replace
from v11.backtest.data_loader import load_instrument_bars, split_by_sessions
from v11.core.darvas_detector import DarvasDetector
from v11.core.imbalance_classifier import ImbalanceClassifier
from v11.core.types import Bar, BreakoutSignal, Direction
from v11.config.strategy_config import EURUSD_CONFIG
import pandas as pd

config = replace(EURUSD_CONFIG, top_confirm_bars=20, bottom_confirm_bars=12,
                 max_box_width_atr=3.0, breakout_confirm_bars=2)


def simulate_trade_custom(signal, bars_after, sl_price, tp_price,
                          max_hold, spread_cost, is_long):
    """Simulate a trade with explicit SL/TP prices."""
    if not bars_after:
        return None
    half_spread = spread_cost / 2
    entry = signal.breakout_price
    effective_entry = entry + half_spread if is_long else entry - half_spread
    risk = abs(entry - sl_price)
    if risk <= 0:
        return None

    exit_price = 0.0
    exit_reason = "TIME_STOP"
    hold_bars = len(bars_after)

    for i, bar in enumerate(bars_after):
        if i >= max_hold:
            exit_price = bar.close
            exit_reason = "TIME_STOP"
            hold_bars = i + 1
            break
        if is_long:
            if bar.low <= sl_price:
                exit_price = sl_price
                exit_reason = "SL"
                hold_bars = i + 1
                break
            if bar.high >= tp_price:
                exit_price = tp_price
                exit_reason = "TARGET"
                hold_bars = i + 1
                break
        else:
            if bar.high >= sl_price:
                exit_price = sl_price
                exit_reason = "SL"
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
            "hold_bars": hold_bars, "sl_dist": risk, "win": 1 if pnl > 0 else 0}


bars = load_instrument_bars("EURUSD", start=datetime(2024, 1, 1))
sessions = split_by_sessions(bars, gap_minutes=30)

# ATR multiplier combos for SL and TP
atr_sl_values = [1.0, 1.5, 2.0]
atr_tp_values = [1.5, 2.0, 3.0]

# Run all combos + box-based reference
results = {}

for sl_mode in ["box"] + [f"atr_{x}" for x in atr_sl_values]:
    for tp_mode in ["rr1.5", "rr2.0"] + [f"atr_{x}" for x in atr_tp_values]:
        key = f"SL={sl_mode} TP={tp_mode}"
        trades = []

        for session_bars in sessions:
            det = DarvasDetector(config)
            clf = ImbalanceClassifier(max_lookback=20, min_bar_ticks=config.min_bar_ticks)

            for i, bar in enumerate(session_bars):
                signal = det.add_bar(bar)
                clf.add_bar(bar)

                if signal is not None:
                    is_long = signal.direction == Direction.LONG
                    entry = signal.breakout_price
                    atr = signal.atr

                    # SL
                    if sl_mode == "box":
                        sl = signal.box.bottom if is_long else signal.box.top
                    else:
                        mult = float(sl_mode.split("_")[1])
                        sl = entry - mult * atr if is_long else entry + mult * atr

                    # TP
                    risk_for_rr = abs(entry - sl)
                    if tp_mode == "rr1.5":
                        tp = entry + risk_for_rr * 1.5 if is_long else entry - risk_for_rr * 1.5
                    elif tp_mode == "rr2.0":
                        tp = entry + risk_for_rr * 2.0 if is_long else entry - risk_for_rr * 2.0
                    else:
                        mult = float(tp_mode.split("_")[1])
                        tp = entry + mult * atr if is_long else entry - mult * atr

                    bars_after = session_bars[i + 1:]
                    t = simulate_trade_custom(
                        signal, bars_after, sl, tp,
                        config.max_hold_bars, config.spread_cost, is_long)
                    if t is not None:
                        trades.append(t)

        if trades:
            df = pd.DataFrame(trades)
            results[key] = {
                "n": len(df),
                "wr": df.win.mean() * 100,
                "avg_r": df.pnl_r.mean(),
                "pnl": df.pnl.sum(),
                "avg_sl_dist": df.sl_dist.mean() * 10000,  # in pips
                "sl_exits": (df.exit_reason == "SL").sum(),
                "tp_exits": (df.exit_reason == "TARGET").sum(),
                "time_exits": (df.exit_reason == "TIME_STOP").sum(),
            }

# Print results sorted by avg_r
print(f"{'Config':35s} {'N':>4s} {'WR%':>6s} {'AvgR':>7s} {'PnL':>8s} {'SLpips':>7s} {'SL#':>4s} {'TP#':>4s} {'TM#':>4s}")
print("-" * 90)
for key, r in sorted(results.items(), key=lambda x: x[1]["avg_r"], reverse=True):
    print(f"{key:35s} {r['n']:4d} {r['wr']:6.1f} {r['avg_r']:+7.3f} {r['pnl']:+8.4f} "
          f"{r['avg_sl_dist']:7.1f} {r['sl_exits']:4d} {r['tp_exits']:4d} {r['time_exits']:4d}")

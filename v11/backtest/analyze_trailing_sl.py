"""Investigate SL tightening after being in positive PnL for N bars.

Variants tested:
1. Baseline: fixed SL at box boundary, no tightening
2. Move SL to breakeven after N bars in profit
3. Move SL to breakeven + partial profit after N bars in profit
4. Trail SL to highest low (longs) / lowest high (shorts) after N bars

All variants keep the original TP and time stop intact.
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
                           tighten_mode="breakeven",
                           trail_lookback=10,
                           lock_profit_frac=0.0):
    """Simulate trade with optional SL tightening.

    tighten_mode:
        "none"      — fixed SL (baseline)
        "breakeven" — move SL to entry after tighten_after_bars if in profit
        "lock"      — move SL to entry + lock_profit_frac * unrealized after tighten_after_bars
        "trail"     — after tighten_after_bars, trail SL to recent swing low/high
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
    best_unrealized = 0.0

    for i, bar in enumerate(bars_after):
        if i >= max_hold:
            exit_price = bar.close
            exit_reason = "TIME_STOP"
            hold_bars = i + 1
            break

        # Track best unrealized PnL
        if is_long:
            unrealized = bar.high - entry
        else:
            unrealized = entry - bar.low
        if unrealized > best_unrealized:
            best_unrealized = unrealized

        # SL tightening logic — only after tighten_after_bars and if in profit
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
                    # Set SL to recent swing level
                    lookback_start = max(0, i - trail_lookback)
                    recent = bars_after[lookback_start:i + 1]
                    if is_long:
                        swing_low = min(b.low for b in recent)
                        new_sl = max(entry, swing_low)  # at least breakeven
                        current_sl = max(current_sl, new_sl)
                    else:
                        swing_high = max(b.high for b in recent)
                        new_sl = min(entry, swing_high)  # at least breakeven
                        current_sl = min(current_sl, new_sl)

        # Trailing update (continuous after initial tighten)
        if tighten_mode == "trail" and sl_was_tightened and i > tighten_after_bars:
            lookback_start = max(0, i - trail_lookback)
            recent = bars_after[lookback_start:i + 1]
            if is_long:
                swing_low = min(b.low for b in recent)
                new_sl = max(entry, swing_low)
                current_sl = max(current_sl, new_sl)
            else:
                swing_high = max(b.high for b in recent)
                new_sl = min(entry, swing_high)
                current_sl = min(current_sl, new_sl)

        # Check SL (with possibly tightened level)
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
            "sl_tightened": sl_was_tightened}


bars = load_instrument_bars("EURUSD", start=datetime(2024, 1, 1))
sessions = split_by_sessions(bars, gap_minutes=30)

# All variants to test
variants = [
    ("BASELINE (no tighten)",          {"tighten_mode": "none"}),
    ("BE after 30 bars",               {"tighten_mode": "breakeven", "tighten_after_bars": 30}),
    ("BE after 45 bars",               {"tighten_mode": "breakeven", "tighten_after_bars": 45}),
    ("BE after 60 bars (1 hour)",      {"tighten_mode": "breakeven", "tighten_after_bars": 60}),
    ("BE after 90 bars",               {"tighten_mode": "breakeven", "tighten_after_bars": 90}),
    ("Lock 25% after 60 bars",         {"tighten_mode": "lock", "tighten_after_bars": 60, "lock_profit_frac": 0.25}),
    ("Lock 50% after 60 bars",         {"tighten_mode": "lock", "tighten_after_bars": 60, "lock_profit_frac": 0.50}),
    ("Lock 25% after 45 bars",         {"tighten_mode": "lock", "tighten_after_bars": 45, "lock_profit_frac": 0.25}),
    ("Lock 50% after 45 bars",         {"tighten_mode": "lock", "tighten_after_bars": 45, "lock_profit_frac": 0.50}),
    ("Trail 10-bar after 60 bars",     {"tighten_mode": "trail", "tighten_after_bars": 60, "trail_lookback": 10}),
    ("Trail 15-bar after 60 bars",     {"tighten_mode": "trail", "tighten_after_bars": 60, "trail_lookback": 15}),
    ("Trail 10-bar after 45 bars",     {"tighten_mode": "trail", "tighten_after_bars": 45, "trail_lookback": 10}),
]

for rr in [1.5, 2.0]:
    print(f"\n{'='*95}")
    print(f"  EURUSD Config B (tc=20 bc=12 maxW=3.0 brk=2) — R:R={rr}")
    print(f"{'='*95}")
    print(f"{'Variant':35s} {'N':>4s} {'WR%':>6s} {'AvgR':>7s} {'PnL':>8s} {'SL':>4s} {'SLT':>4s} {'TP':>4s} {'TM':>4s} {'Tight%':>6s}")
    print("-" * 95)

    for label, kwargs in variants:
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
                    sl = signal.box.bottom if is_long else signal.box.top
                    risk = abs(entry - sl)
                    tp = entry + risk * rr if is_long else entry - risk * rr

                    t = simulate_with_trailing(
                        signal, session_bars[i + 1:], sl, tp,
                        config.max_hold_bars, config.spread_cost, is_long,
                        **kwargs)
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
            tight_pct = df.sl_tightened.mean() * 100
            print(f"{label:35s} {n:4d} {wr:6.1f} {avg_r:+7.3f} {pnl:+8.4f} {sl_n:4d} {slt_n:4d} {tp_n:4d} {tm_n:4d} {tight_pct:6.1f}")

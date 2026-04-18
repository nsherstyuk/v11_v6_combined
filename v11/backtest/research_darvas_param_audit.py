"""Task 1: Darvas Parameter Audit — Config B vs Current Live Config.

Compares:
  Config B (OOS-validated): tc=20, bc=12, mxW=3.0, brk=2
  Current Live:             tc=15, bc=15, mxW=5.0, brk=3

On three data windows:
  1. OOS 2018-2023 (same as original validation)
  2. IS 2024-2026 (original in-sample)
  3. Fresh 2026-01 to 2026-04 (true out-of-sample for both)

With filter variants:
  - ALL signals (no volume filter)
  - CONFIRMING only
  - CONFIRMING + SMA(50) direction filter
  - CONFIRMING + SMA + Trail10@60

Outputs a comparison table for the audit report.
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from datetime import datetime
from dataclasses import replace
from v11.backtest.data_loader import load_instrument_bars, split_by_sessions
from v11.core.darvas_detector import DarvasDetector
from v11.core.imbalance_classifier import ImbalanceClassifier
from v11.core.htf_sma_filter import BatchHTFSMAFilter
from v11.core.types import Direction
from v11.config.strategy_config import EURUSD_CONFIG
import pandas as pd


# ── Config definitions ──────────────────────────────────────────────────────

CONFIG_B = replace(EURUSD_CONFIG,
    top_confirm_bars=20, bottom_confirm_bars=12,
    max_box_width_atr=3.0, breakout_confirm_bars=2)

CONFIG_LIVE = replace(EURUSD_CONFIG,
    top_confirm_bars=15, bottom_confirm_bars=15,
    max_box_width_atr=5.0, breakout_confirm_bars=3)

# Loosened variant from frequency investigation
CONFIG_LOOSENED = replace(EURUSD_CONFIG,
    top_confirm_bars=20, bottom_confirm_bars=12,
    max_box_width_atr=3.0, breakout_confirm_bars=3)


# ── Simulation with trailing stop ───────────────────────────────────────────

def simulate_trade(signal, bars_after, sl_price, tp_price,
                   max_hold, spread_cost, is_long,
                   trail_enabled=False, trail_activation_bars=60,
                   trail_lookback=10):
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

        # Trailing stop logic
        if trail_enabled and not sl_was_tightened and i >= trail_activation_bars:
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

        if trail_enabled and sl_was_tightened and i > trail_activation_bars:
            lookback_start = max(0, i - trail_lookback)
            recent = bars_after[lookback_start:i + 1]
            if is_long:
                swing_low = min(b.low for b in recent)
                current_sl = max(current_sl, max(entry, swing_low))
            else:
                swing_high = max(b.high for b in recent)
                current_sl = min(current_sl, min(entry, swing_high))

        # Check SL
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
        "sl_tightened": sl_was_tightened,
    }


# ── Main runner ─────────────────────────────────────────────────────────────

def run_config_on_period(config, bars, rr=2.0, vol_filter="CONF",
                         use_sma=True, use_trail=True):
    """Run a single config on a period, return summary dict."""
    sessions = split_by_sessions(bars, gap_minutes=30)

    # Build SMA filter if enabled
    sma_filter = None
    if use_sma and config.htf_sma_enabled:
        sma_filter = BatchHTFSMAFilter(
            bars, config.htf_sma_bar_minutes, config.htf_sma_period,
            gap_minutes=30,
        )

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
                    signal.direction, config.imbalance_window,
                    config.divergence_threshold,
                ).value

                # SMA direction filter
                if sma_filter is not None:
                    if not sma_filter.is_aligned(
                        signal.direction, signal.breakout_price,
                        signal.timestamp,
                    ):
                        continue

                # Volume filter
                if vol_filter == "CONF" and vol_class != "CONFIRMING":
                    continue
                elif vol_filter == "NO_DIV" and vol_class == "DIVERGENT":
                    continue

                entry = signal.breakout_price
                sl = signal.box.bottom if is_long else signal.box.top
                risk = abs(entry - sl)
                tp = entry + risk * rr if is_long else entry - risk * rr

                t = simulate_trade(
                    signal, session_bars[i + 1:], sl, tp,
                    config.max_hold_bars, config.spread_cost, is_long,
                    trail_enabled=use_trail,
                    trail_activation_bars=60,
                    trail_lookback=10,
                )
                if t is not None:
                    t["vol_class"] = vol_class
                    raw_trades.append(t)

    if not raw_trades:
        return {"n": 0, "wr": 0, "avg_r": 0, "pnl": 0, "pf": 0,
                "sl": 0, "slt": 0, "tp": 0, "tm": 0, "max_dd": 0}

    df = pd.DataFrame(raw_trades)
    n = len(df)

    # Compute max drawdown
    cum_pnl = df.pnl.cumsum()
    running_max = cum_pnl.cummax()
    drawdown = cum_pnl - running_max
    max_dd = drawdown.min()

    # Profit factor
    gross_profit = df.loc[df.win == 1, "pnl"].sum()
    gross_loss = abs(df.loc[df.win == 0, "pnl"].sum())
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    return {
        "n": n,
        "wr": df.win.mean() * 100,
        "avg_r": df.pnl_r.mean(),
        "pnl": df.pnl.sum(),
        "pf": pf,
        "sl": (df.exit_reason == "SL").sum(),
        "slt": (df.exit_reason == "SL_TIGHT").sum(),
        "tp": (df.exit_reason == "TARGET").sum(),
        "tm": (df.exit_reason == "TIME_STOP").sum(),
        "max_dd": max_dd,
    }


# ── Load data ────────────────────────────────────────────────────────────────

print("Loading EURUSD data...")
all_bars = load_instrument_bars("EURUSD")
print(f"Total: {len(all_bars):,} bars ({all_bars[0].timestamp.date()} to {all_bars[-1].timestamp.date()})")

# Define periods
periods = [
    ("OOS 2018-2023", datetime(2018, 1, 1), datetime(2023, 12, 31)),
    ("IS 2024-2026",  datetime(2024, 1, 1), datetime(2026, 12, 31)),
    ("Fresh Jan-Apr 2026", datetime(2026, 1, 1), datetime(2026, 4, 30)),
]

configs = [
    ("Config B (tc=20 bc=12 mxW=3.0 brk=2)", CONFIG_B),
    ("Current Live (tc=15 bc=15 mxW=5.0 brk=3)", CONFIG_LIVE),
    ("Loosened (tc=20 bc=12 mxW=3.0 brk=3)", CONFIG_LOOSENED),
]

# Filter stacks
stacks = [
    ("CONF+SMA+Trail", {"vol_filter": "CONF", "use_sma": True, "use_trail": True}),
    ("CONF+SMA",       {"vol_filter": "CONF", "use_sma": True, "use_trail": False}),
    ("CONF",           {"vol_filter": "CONF", "use_sma": False, "use_trail": False}),
    ("ALL",            {"vol_filter": "ALL",  "use_sma": False, "use_trail": False}),
]

# ── Run comparisons ─────────────────────────────────────────────────────────

for period_label, start, end in periods:
    period_bars = [b for b in all_bars if start <= b.timestamp <= end]
    if not period_bars:
        print(f"\n  NO DATA for {period_label}")
        continue

    print(f"\n{'='*120}")
    print(f"  Period: {period_label} ({len(period_bars):,} bars)")
    print(f"{'='*120}")

    for stack_label, stack_kwargs in stacks:
        print(f"\n  Stack: {stack_label}")
        print(f"  {'Config':45s} {'N':>4s} {'WR%':>6s} {'AvgR':>7s} {'PnL':>10s} {'PF':>5s} {'MaxDD':>10s} {'SL':>4s} {'SLT':>4s} {'TP':>4s} {'TM':>4s}")
        print(f"  {'-'*110}")

        for config_label, config in configs:
            r = run_config_on_period(config, period_bars, rr=2.0, **stack_kwargs)
            if r["n"] > 0:
                print(f"  {config_label:45s} {r['n']:4d} {r['wr']:6.1f} {r['avg_r']:+7.3f} "
                      f"{r['pnl']:+10.4f} {r['pf']:5.2f} {r['max_dd']:+10.4f} "
                      f"{r['sl']:4d} {r['slt']:4d} {r['tp']:4d} {r['tm']:4d}")
            else:
                print(f"  {config_label:45s}    0   ---     ---        ---   ---        ---    -    -    -    -")

# ── Year-by-year OOS for Config B vs Live ────────────────────────────────────

print(f"\n{'='*120}")
print(f"  Year-by-Year OOS (CONF+SMA+Trail, R:R=2.0)")
print(f"{'='*120}")

for config_label, config in configs:
    print(f"\n  {config_label}")
    print(f"  {'Year':8s} {'N':>4s} {'WR%':>6s} {'AvgR':>7s} {'PnL':>10s} {'PF':>5s} {'MaxDD':>10s}")
    print(f"  {'-'*60}")

    for year in range(2018, 2024):
        year_bars = [b for b in all_bars
                     if datetime(year, 1, 1) <= b.timestamp <= datetime(year, 12, 31)]
        if not year_bars:
            continue
        r = run_config_on_period(config, year_bars, rr=2.0,
                                 vol_filter="CONF", use_sma=True, use_trail=True)
        if r["n"] > 0:
            print(f"  {year:8d} {r['n']:4d} {r['wr']:6.1f} {r['avg_r']:+7.3f} "
                  f"{r['pnl']:+10.4f} {r['pf']:5.2f} {r['max_dd']:+10.4f}")
        else:
            print(f"  {year:8d}    0   ---     ---        ---   ---        ---")

print("\nDone.")

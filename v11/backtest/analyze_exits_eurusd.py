"""Exit reason breakdown for EURUSD — wins vs losses by exit type."""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from datetime import datetime
from dataclasses import replace
from v11.backtest.data_loader import load_instrument_bars
from v11.backtest.simulator import run_backtest
from v11.config.strategy_config import EURUSD_CONFIG
import pandas as pd

configs = {
    "A: tc=15 bc=20 maxW=4.0 brk=2": replace(
        EURUSD_CONFIG, top_confirm_bars=15, bottom_confirm_bars=20,
        max_box_width_atr=4.0, breakout_confirm_bars=2),
    "B: tc=20 bc=12 maxW=3.0 brk=2": replace(
        EURUSD_CONFIG, top_confirm_bars=20, bottom_confirm_bars=12,
        max_box_width_atr=3.0, breakout_confirm_bars=2),
}

bars = load_instrument_bars("EURUSD", start=datetime(2024, 1, 1))

for label, config in configs.items():
    for rr in [1.5, 2.0]:
        result = run_backtest(bars, config, rr_ratio=rr)
        trades = result.trades
        if not trades:
            continue
        rows = [{"pnl": t.pnl, "pnl_r": t.pnl_r, "exit_reason": t.exit_reason,
                 "hold_bars": t.hold_bars, "volume_class": t.volume_classification,
                 "win": 1 if t.pnl > 0 else 0, "loss": 1 if t.pnl < 0 else 0}
                for t in trades]
        df = pd.DataFrame(rows)

        print("=" * 70)
        print(f"  {label}  |  R:R={rr}")
        print("=" * 70)

        for reason in ["SL", "TARGET", "TIME_STOP"]:
            sub = df[df.exit_reason == reason]
            if len(sub) == 0:
                continue
            wins = sub.win.sum()
            losses = sub.loss.sum()
            avg_r = sub.pnl_r.mean()
            avg_hold = sub.hold_bars.mean()
            pnl = sub.pnl.sum()
            print(f"  {reason:12s} | N={len(sub):3d} | W={wins:.0f} L={losses:.0f} | "
                  f"AvgR={avg_r:+.3f} | AvgHold={avg_hold:5.1f} bars | PnL={pnl:+.4f}")

        # Same for CONFIRMING only
        cf = df[df.volume_class == "CONFIRMING"]
        print(f"\n  CONFIRMING only ({len(cf)} trades):")
        for reason in ["SL", "TARGET", "TIME_STOP"]:
            sub = cf[cf.exit_reason == reason]
            if len(sub) == 0:
                continue
            wins = sub.win.sum()
            losses = sub.loss.sum()
            avg_r = sub.pnl_r.mean()
            avg_hold = sub.hold_bars.mean()
            pnl = sub.pnl.sum()
            print(f"    {reason:12s} | N={len(sub):3d} | W={wins:.0f} L={losses:.0f} | "
                  f"AvgR={avg_r:+.3f} | AvgHold={avg_hold:5.1f} bars | PnL={pnl:+.4f}")
        print()

"""Volume classification analysis for EURUSD higher-trade configs."""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from datetime import datetime
from dataclasses import replace
from v11.backtest.data_loader import load_instrument_bars
from v11.backtest.simulator import run_backtest
from v11.config.strategy_config import EURUSD_CONFIG
import pandas as pd

# The three best EURUSD configs from loosening grid
configs = {
    "A: tc=15 bc=20 maxW=4.0 brk=2": replace(
        EURUSD_CONFIG, top_confirm_bars=15, bottom_confirm_bars=20,
        max_box_width_atr=4.0, breakout_confirm_bars=2),
    "B: tc=20 bc=12 maxW=3.0 brk=2": replace(
        EURUSD_CONFIG, top_confirm_bars=20, bottom_confirm_bars=12,
        max_box_width_atr=3.0, breakout_confirm_bars=2),
    "C: tc=20 bc=20 maxW=3.0 brk=2": replace(
        EURUSD_CONFIG, top_confirm_bars=20, bottom_confirm_bars=20,
        max_box_width_atr=3.0, breakout_confirm_bars=2),
}

bars = load_instrument_bars("EURUSD", start=datetime(2024, 1, 1))
print(f"Loaded {len(bars):,} EURUSD bars\n")

for label, config in configs.items():
    for rr in [1.5, 2.0]:
        result = run_backtest(bars, config, rr_ratio=rr)
        trades = result.trades
        if not trades:
            continue
        rows = [{"direction": t.direction.value,
                 "pnl": t.pnl, "pnl_r": t.pnl_r, "exit_reason": t.exit_reason,
                 "hold_bars": t.hold_bars, "volume_class": t.volume_classification,
                 "win": 1 if t.pnl > 0 else 0}
                for t in trades]
        df = pd.DataFrame(rows)

        print("=" * 75)
        print(f"  {label}  |  R:R={rr}  |  {len(df)} trades")
        print("=" * 75)

        for vc in ["CONFIRMING", "DIVERGENT", "INDETERMINATE"]:
            sub = df[df.volume_class == vc]
            if len(sub) == 0:
                continue
            wr = sub.win.mean() * 100
            avg_r = sub.pnl_r.mean()
            pnl = sub.pnl.sum()
            n = len(sub)
            # Exit reason breakdown
            exits = sub.exit_reason.value_counts().to_dict()
            exits_str = "  ".join(f"{k}={v}" for k, v in sorted(exits.items()))
            # Direction breakdown
            long_n = (sub.direction == "long").sum()
            short_n = (sub.direction == "short").sum()
            long_wr = sub[sub.direction == "long"].win.mean() * 100 if long_n > 0 else 0
            short_wr = sub[sub.direction == "short"].win.mean() * 100 if short_n > 0 else 0
            print(f"  {vc:15s} | N={n:3d} | WR={wr:5.1f}% | AvgR={avg_r:+.3f} | PnL={pnl:+.4f}")
            print(f"  {'':15s} | L={long_n}({long_wr:.0f}%) S={short_n}({short_wr:.0f}%) | {exits_str}")

        # ALL row
        wr_all = df.win.mean() * 100
        print(f"  {'ALL':15s} | N={len(df):3d} | WR={wr_all:5.1f}% | AvgR={df.pnl_r.mean():+.3f} | PnL={df.pnl.sum():+.4f}")

        # Filtered: CONFIRMING + INDETERMINATE only
        filt = df[df.volume_class != "DIVERGENT"]
        if len(filt) > 0:
            filt_wr = filt.win.mean() * 100
            filt_avg_r = filt.pnl_r.mean()
            filt_pnl = filt.pnl.sum()
            print(f"  {'NO-DIVERGENT':15s} | N={len(filt):3d} | WR={filt_wr:5.1f}% | AvgR={filt_avg_r:+.3f} | PnL={filt_pnl:+.4f}")
        print()

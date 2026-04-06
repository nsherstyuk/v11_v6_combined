"""Quick script to analyze volume classification correlation with trade success."""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from datetime import datetime
from dataclasses import replace
from v11.backtest.data_loader import load_instrument_bars
from v11.backtest.simulator import run_backtest
from v11.config.strategy_config import XAUUSD_CONFIG, EURUSD_CONFIG, USDJPY_CONFIG
import pandas as pd

configs = {
    "XAUUSD": replace(XAUUSD_CONFIG, top_confirm_bars=10, bottom_confirm_bars=20,
                       max_box_width_atr=3.0, breakout_confirm_bars=3),
    "EURUSD": replace(EURUSD_CONFIG, top_confirm_bars=20, bottom_confirm_bars=20,
                       max_box_width_atr=3.0, breakout_confirm_bars=2),
    "USDJPY": replace(USDJPY_CONFIG, top_confirm_bars=20, bottom_confirm_bars=10,
                       max_box_width_atr=3.0, breakout_confirm_bars=2),
}

for inst, config in configs.items():
    bars = load_instrument_bars(inst, start=datetime(2024, 1, 1))
    for rr in [1.5, 2.0]:
        result = run_backtest(bars, config, rr_ratio=rr)
        trades = result.trades
        if not trades:
            continue
        rows = [{"instrument": inst, "rr": rr, "direction": t.direction.value,
                 "pnl": t.pnl, "pnl_r": t.pnl_r, "exit_reason": t.exit_reason,
                 "hold_bars": t.hold_bars, "volume_class": t.volume_classification,
                 "win": 1 if t.pnl > 0 else 0}
                for t in trades]
        df = pd.DataFrame(rows)

        print(f"{'='*70}")
        print(f"  {inst}  R:R={rr}  (best params, 2024-2026)")
        print(f"{'='*70}")
        for vc in ["CONFIRMING", "DIVERGENT", "INDETERMINATE"]:
            sub = df[df.volume_class == vc]
            if len(sub) == 0:
                continue
            wr = sub.win.mean() * 100
            avg_r = sub.pnl_r.mean()
            pnl = sub.pnl.sum()
            n = len(sub)
            print(f"  {vc:15s} | N={n:3d} | WR={wr:5.1f}% | AvgR={avg_r:+.3f} | TotalPnL={pnl:+.4f}")
        wr_all = df.win.mean() * 100
        print(f"  {'ALL':15s} | N={len(df):3d} | WR={wr_all:5.1f}% | AvgR={df.pnl_r.mean():+.3f} | TotalPnL={df.pnl.sum():+.4f}")
        print()

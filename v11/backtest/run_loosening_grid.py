"""Moderate loosening grid — focus on max_box_width_atr and bottom_confirm_bars."""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from datetime import datetime
from v11.backtest.data_loader import load_instrument_bars
from v11.backtest.grid_search import run_grid_search, save_results

# Focused grid: vary the two params that dominate trade count
# plus top_confirm_bars and breakout_confirm_bars at known-good values
LOOSENING_GRID = {
    "top_confirm_bars":      [10, 15, 20],
    "bottom_confirm_bars":   [10, 12, 15, 20],
    "min_box_width_atr":     [0.3],
    "max_box_width_atr":     [3.0, 4.0, 5.0],
    "min_box_duration":      [15],
    "breakout_confirm_bars": [2, 3],
}
# 3 * 4 * 1 * 3 * 1 * 2 = 72 combos

for inst in ["XAUUSD", "EURUSD", "USDJPY"]:
    print(f"\n[{inst}] Loading bars 2024-01-01 to end...")
    bars = load_instrument_bars(inst, start=datetime(2024, 1, 1))
    print(f"[{inst}] Loaded {len(bars):,} bars")

    for rr in [1.5, 2.0]:
        reports = run_grid_search(
            bars, inst,
            param_ranges=LOOSENING_GRID,
            rr_ratio=rr,
            min_trades=5,  # lower threshold to see effect of loosening
        )
        if reports:
            save_results(reports, f"v11_grid_{inst}_loosen_rr{str(rr).replace('.','')}.csv")

"""Quick re-run of Q1-Q3 only for GBPUSD volume imbalance research."""
import pandas as pd
import numpy as np
from pathlib import Path

DATA_PATH = Path(r"C:\nautilus0\data\1m_csv\gbpusd_1m_tick.csv")

print("Loading GBPUSD 1-min data...")
df = pd.read_csv(DATA_PATH, parse_dates=['timestamp'])
df = df.sort_values('timestamp').reset_index(drop=True)
print(f"Rows: {len(df):,}  |  Date range: {df.timestamp.iloc[0]} → {df.timestamp.iloc[-1]}")
print()

print("=== Volume Imbalance Distribution ===")
print(df[['vol_imbalance','buy_ratio','buy_volume','sell_volume','total_volume','tick_count']].describe().to_string())
print()

# Derived features
df['price_chg'] = df['close'].diff()
df['price_chg_pips'] = df['price_chg'] * 10000
df['direction'] = np.sign(df['price_chg_pips'])
df['prev_direction'] = df['direction'].shift(1)
df['reversal'] = (df['direction'] != df['prev_direction']) & (df['prev_direction'] != 0)
df['reversal_up'] = df['reversal'] & (df['direction'] == 1)
df['reversal_down'] = df['reversal'] & (df['direction'] == -1)
df['buy_ratio_chg'] = df['buy_ratio'].diff()
df['vol_imbalance_chg'] = df['vol_imbalance'].diff()

# Q1
print("=== Q1: Does buy_ratio change predict upcoming reversal? ===\n")
for look_ahead in [1, 2, 3, 5, 10]:
    future_rev = df['reversal'].shift(-look_ahead)
    br_up = df['buy_ratio_chg'] > 0
    br_down = df['buy_ratio_chg'] < 0

    rev_after_br_up = future_rev[br_up].mean() * 100 if br_up.sum() > 0 else 0
    rev_after_br_down = future_rev[br_down].mean() * 100 if br_down.sum() > 0 else 0
    rev_baseline = future_rev.mean() * 100

    future_rev_up = df['reversal_up'].shift(-look_ahead)
    future_rev_down = df['reversal_down'].shift(-look_ahead)
    rev_up_after_br_up = future_rev_up[br_up].mean() * 100 if br_up.sum() > 0 else 0
    rev_down_after_br_down = future_rev_down[br_down].mean() * 100 if br_down.sum() > 0 else 0

    print(f"  Look-ahead {look_ahead:2d} bars:")
    print(f"    Baseline reversal rate:        {rev_baseline:.2f}%")
    print(f"    Reversal after buy_ratio UP:   {rev_after_br_up:.2f}%  (N={br_up.sum():,})")
    print(f"    Reversal after buy_ratio DOWN: {rev_after_br_down:.2f}%  (N={br_down.sum():,})")
    print(f"    Rev-UP after buy_ratio UP:     {rev_up_after_br_up:.2f}%")
    print(f"    Rev-DOWN after buy_ratio DOWN: {rev_down_after_br_down:.2f}%")
    print()

# Q2
print("=== Q2: Does reversal predict upcoming buy_ratio change? ===\n")
for look_ahead in [1, 2, 3, 5, 10]:
    future_br_chg = df['buy_ratio_chg'].shift(-look_ahead)
    at_rev = df['reversal']
    at_no_rev = ~df['reversal']

    chg_after_rev = future_br_chg[at_rev].mean() if at_rev.sum() > 0 else 0
    chg_after_no_rev = future_br_chg[at_no_rev].mean() if at_no_rev.sum() > 0 else 0
    future_br_after_rev_up = future_br_chg[df['reversal_up']].mean() if df['reversal_up'].sum() > 0 else 0
    future_br_after_rev_down = future_br_chg[df['reversal_down']].mean() if df['reversal_down'].sum() > 0 else 0

    print(f"  Look-ahead {look_ahead:2d} bars:")
    print(f"    Avg buy_ratio chg at reversal:     {chg_after_rev:+.6f}")
    print(f"    Avg buy_ratio chg at no-reversal:   {chg_after_no_rev:+.6f}")
    print(f"    After rev-UP, buy_ratio chg:        {future_br_after_rev_up:+.6f}")
    print(f"    After rev-DOWN, buy_ratio chg:       {future_br_after_rev_down:+.6f}")
    print()

# Q3
print("=== Q3: Balance flip (buy_ratio crosses 0.5) ===\n")
df['buy_ratio_above50'] = df['buy_ratio'] > 0.5
df['balance_flip'] = df['buy_ratio_above50'] != df['buy_ratio_above50'].shift(1)
flips = df[df['balance_flip']]
print(f"Total balance flips: {len(flips):,} ({len(flips)/len(df)*100:.1f}% of bars)")

for look_ahead in [1, 3, 5, 10, 20]:
    future_ret = df['price_chg_pips'].rolling(look_ahead).sum().shift(-look_ahead)
    flip_to_buy = df['balance_flip'] & df['buy_ratio_above50']
    flip_to_sell = df['balance_flip'] & ~df['buy_ratio_above50']

    ret_after_flip_buy = future_ret[flip_to_buy].mean() if flip_to_buy.sum() > 0 else 0
    ret_after_flip_sell = future_ret[flip_to_sell].mean() if flip_to_sell.sum() > 0 else 0
    ret_baseline = future_ret.mean()
    wr_after_flip_buy = (future_ret[flip_to_buy] > 0).mean() * 100 if flip_to_buy.sum() > 0 else 0
    wr_after_flip_sell = (future_ret[flip_to_sell] > 0).mean() * 100 if flip_to_sell.sum() > 0 else 0

    print(f"  Look-ahead {look_ahead:2d} bars cumulative return:")
    print(f"    Baseline avg ret:          {ret_baseline:+.3f} pips")
    print(f"    After flip→buy dominant:  {ret_after_flip_buy:+.3f} pips  WR={wr_after_flip_buy:.1f}%  (N={flip_to_buy.sum():,})")
    print(f"    After flip→sell dominant: {ret_after_flip_sell:+.3f} pips  WR={wr_after_flip_sell:.1f}%  (N={flip_to_sell.sum():,})")
    print()

print("=== Q1-Q3 COMPLETE ===")

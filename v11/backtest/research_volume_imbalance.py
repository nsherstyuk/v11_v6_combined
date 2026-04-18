"""
Research: Volume Imbalance vs Price Reversal — GBPUSD 1-min tick data.

Questions:
1. Does a shift in volume imbalance (buy→sell or sell→buy) precede
   a price reversal, or does it follow/lag?
2. Is there predictive signal in vol_imbalance / buy_ratio changes?
3. What existing indicators use tick-level volume imbalance?

Data columns:
    timestamp, open, high, low, close, tick_count, avg_spread, max_spread,
    vol_imbalance, buy_volume, sell_volume, total_volume, buy_ratio
"""
import pandas as pd
import numpy as np
from pathlib import Path

DATA_PATH = Path(r"C:\nautilus0\data\1m_csv\gbpusd_1m_tick.csv")

# ── Load ────────────────────────────────────────────────────────────────────
print("Loading GBPUSD 1-min data...")
df = pd.read_csv(DATA_PATH, parse_dates=['timestamp'])
df = df.sort_values('timestamp').reset_index(drop=True)
print(f"Rows: {len(df):,}  |  Date range: {df.timestamp.iloc[0]} → {df.timestamp.iloc[-1]}")
print(f"Columns: {list(df.columns)}")
print()

# ── Basic stats ──────────────────────────────────────────────────────────────
print("=== Volume Imbalance Distribution ===")
print(df[['vol_imbalance','buy_ratio','buy_volume','sell_volume','total_volume','tick_count']].describe())
print()

# ── Derived features ────────────────────────────────────────────────────────
# Price change (close-to-close, in pips)
df['price_chg'] = df['close'].diff()
df['price_chg_pips'] = df['price_chg'] * 10000  # GBPUSD ~5-digit

# Direction: +1 up, -1 down, 0 flat
df['direction'] = np.sign(df['price_chg_pips'])

# Reversal: direction flips from prev bar
df['prev_direction'] = df['direction'].shift(1)
df['reversal'] = (df['direction'] != df['prev_direction']) & (df['prev_direction'] != 0)
df['reversal_up'] = df['reversal'] & (df['direction'] == 1)   # was down, now up
df['reversal_down'] = df['reversal'] & (df['direction'] == -1) # was up, now down

# Buy ratio change (shift in balance)
df['buy_ratio_chg'] = df['buy_ratio'].diff()
df['buy_ratio_chg_sign'] = np.sign(df['buy_ratio_chg'])

# Vol imbalance change
df['vol_imbalance_chg'] = df['vol_imbalance'].diff()

# Rolling buy_ratio (smoothed)
for w in [3, 5, 10, 20]:
    df[f'buy_ratio_sma{w}'] = df['buy_ratio'].rolling(w).mean()
    df[f'buy_ratio_sma{w}_chg'] = df[f'buy_ratio_sma{w}'].diff()

# Rolling buy_ratio cross 0.5 (balance flip)
df['buy_ratio_above50'] = df['buy_ratio'] > 0.5
df['balance_flip'] = df['buy_ratio_above50'] != df['buy_ratio_above50'].shift(1)

# ── Q1: Does volume imbalance shift PRECEDE price reversal? ─────────────────
print("=== Q1: Does buy_ratio change predict upcoming reversal? ===\n")

# For each bar, look ahead N bars for a reversal
for look_ahead in [1, 2, 3, 5, 10]:
    future_rev = df['reversal'].shift(-look_ahead)
    # Split by whether buy_ratio increased or decreased this bar
    br_up = df['buy_ratio_chg'] > 0
    br_down = df['buy_ratio_chg'] < 0
    br_flat = df['buy_ratio_chg'] == 0

    rev_after_br_up = future_rev[br_up].mean() * 100 if br_up.sum() > 0 else 0
    rev_after_br_down = future_rev[br_down].mean() * 100 if br_down.sum() > 0 else 0
    rev_baseline = future_rev.mean() * 100

    # Directional: buy_ratio UP → does it predict reversal_UP (price going up next)?
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

# ── Q2: Does price reversal PRECEDE volume imbalance shift? ────────────────
print("=== Q2: Does reversal predict upcoming buy_ratio change? ===\n")

for look_ahead in [1, 2, 3, 5, 10]:
    future_br_chg = df['buy_ratio_chg'].shift(-look_ahead)
    at_rev = df['reversal']
    at_no_rev = ~df['reversal']

    chg_after_rev = future_br_chg[at_rev].mean() if at_rev.sum() > 0 else 0
    chg_after_no_rev = future_br_chg[at_no_rev].mean() if at_no_rev.sum() > 0 else 0

    # After reversal UP, does buy_ratio go up (buyers follow the move)?
    future_br_after_rev_up = future_br_chg[df['reversal_up']].mean() if df['reversal_up'].sum() > 0 else 0
    future_br_after_rev_down = future_br_chg[df['reversal_down']].mean() if df['reversal_down'].sum() > 0 else 0

    print(f"  Look-ahead {look_ahead:2d} bars:")
    print(f"    Avg buy_ratio chg at reversal:     {chg_after_rev:+.6f}")
    print(f"    Avg buy_ratio chg at no-reversal:   {chg_after_no_rev:+.6f}")
    print(f"    After rev-UP, buy_ratio chg:        {future_br_after_rev_up:+.6f}")
    print(f"    After rev-DOWN, buy_ratio chg:       {future_br_after_rev_down:+.6f}")
    print()

# ── Q3: Balance flip (buy_ratio crosses 0.5) as reversal signal ─────────────
print("=== Q3: Balance flip (buy_ratio crosses 0.5) ===\n")

flips = df[df['balance_flip']].copy()
print(f"Total balance flips: {len(flips):,} ({len(flips)/len(df)*100:.1f}% of bars)")

# After a flip to buy-dominant (>0.5), does price go up?
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

# ── Q4: Smoothed buy_ratio (SMA) crossover as signal ────────────────────────
print("=== Q4: Smoothed buy_ratio SMA crossover signals ===\n")

for w in [5, 10, 20]:
    sma_col = f'buy_ratio_sma{w}'
    chg_col = f'buy_ratio_sma{w}_chg'
    sma_above = df[sma_col] > 0.5
    sma_cross_up = sma_above & ~sma_above.shift(1).fillna(True)
    sma_cross_down = ~sma_above & sma_above.shift(1).fillna(False)

    for look_ahead in [5, 10, 20]:
        future_ret = df['price_chg_pips'].rolling(look_ahead).sum().shift(-look_ahead)

        ret_after_cross_up = future_ret[sma_cross_up].mean() if sma_cross_up.sum() > 0 else 0
        ret_after_cross_down = future_ret[sma_cross_down].mean() if sma_cross_down.sum() > 0 else 0
        wr_up = (future_ret[sma_cross_up] > 0).mean() * 100 if sma_cross_up.sum() > 0 else 0
        wr_down = (future_ret[sma_cross_down] > 0).mean() * 100 if sma_cross_down.sum() > 0 else 0

        print(f"  SMA{w} cross, look-ahead {look_ahead:2d}:")
        print(f"    Cross UP (→buy dom):  avg ret={ret_after_cross_up:+.3f} pips  WR={wr_up:.1f}%  (N={sma_cross_up.sum():,})")
        print(f"    Cross DOWN (→sell dom): avg ret={ret_after_cross_down:+.3f} pips  WR={wr_down:.1f}%  (N={sma_cross_down.sum():,})")
    print()

# ── Q5: Extreme imbalance as exhaustion / reversal signal ────────────────────
print("=== Q5: Extreme buy_ratio as exhaustion signal ===\n")

for threshold in [0.65, 0.70, 0.75, 0.80]:
    extreme_buy = df['buy_ratio'] > threshold
    extreme_sell = df['buy_ratio'] < (1 - threshold)

    for look_ahead in [5, 10, 20]:
        future_ret = df['price_chg_pips'].rolling(look_ahead).sum().shift(-look_ahead)

        ret_after_ext_buy = future_ret[extreme_buy].mean() if extreme_buy.sum() > 0 else 0
        ret_after_ext_sell = future_ret[extreme_sell].mean() if extreme_sell.sum() > 0 else 0
        wr_buy = (future_ret[extreme_buy] > 0).mean() * 100 if extreme_buy.sum() > 0 else 0
        wr_sell = (future_ret[extreme_sell] > 0).mean() * 100 if extreme_sell.sum() > 0 else 0

        print(f"  Threshold {threshold:.2f}, look-ahead {look_ahead:2d}:")
        print(f"    Extreme BUY  (>{threshold}): avg ret={ret_after_ext_buy:+.3f} pips  WR={wr_buy:.1f}%  (N={extreme_buy.sum():,})")
        print(f"    Extreme SELL (<{1-threshold:.2f}): avg ret={ret_after_ext_sell:+.3f} pips  WR={wr_sell:.1f}%  (N={extreme_sell.sum():,})")
    print()

# ── Q6: Vol_imbalance (signed) directional predictiveness ───────────────────
print("=== Q6: vol_imbalance as signed predictor ===\n")

# vol_imbalance is signed: positive = sell volume > buy volume (selling pressure)
# Negative = buy volume > sell volume (buying pressure)
# Check: does high positive vol_imbalance (selling) predict price decline?
for look_ahead in [1, 3, 5, 10]:
    future_ret = df['price_chg_pips'].rolling(look_ahead).sum().shift(-look_ahead)

    # Correlation
    valid = df['vol_imbalance'].notna() & future_ret.notna()
    corr = df.loc[valid, 'vol_imbalance'].corr(future_ret[valid])

    # Quintile analysis
    df_temp = df[['vol_imbalance']].copy()
    df_temp['future_ret'] = future_ret
    df_temp = df_temp.dropna()
    df_temp['q'] = pd.qcut(df_temp['vol_imbalance'], 5, labels=['Q1(most buy)','Q2','Q3','Q4','Q5(most sell)'])

    print(f"  Look-ahead {look_ahead:2d} bars:")
    print(f"    Correlation(vol_imbalance, future_ret): {corr:+.4f}")
    print(f"    Quintile avg future return (pips):")
    for q in ['Q1(most buy)','Q2','Q3','Q4','Q5(most sell)']:
        subset = df_temp[df_temp['q'] == q]
        print(f"      {q}: {subset['future_ret'].mean():+.3f}  (N={len(subset):,})")
    print()

# ── Q7: Delta (cumulative vol_imbalance) as order flow indicator ────────────
print("=== Q7: Cumulative vol_imbalance (delta) over session windows ===\n")

# Reset delta daily
df['date'] = df['timestamp'].dt.date
df['delta'] = df['vol_imbalance'].groupby(df['date']).cumsum()

# Does delta direction predict price direction over next N bars?
for look_ahead in [10, 30, 60]:
    future_ret = df['price_chg_pips'].rolling(look_ahead).sum().shift(-look_ahead)

    delta_positive = df['delta'] > 0
    delta_negative = df['delta'] < 0

    ret_pos = future_ret[delta_positive].mean() if delta_positive.sum() > 0 else 0
    ret_neg = future_ret[delta_negative].mean() if delta_negative.sum() > 0 else 0
    wr_pos = (future_ret[delta_positive] > 0).mean() * 100 if delta_positive.sum() > 0 else 0
    wr_neg = (future_ret[delta_negative] > 0).mean() * 100 if delta_negative.sum() > 0 else 0

    print(f"  Look-ahead {look_ahead:2d} bars:")
    print(f"    Delta>0 (cumul. sell pressure): avg ret={ret_pos:+.3f} pips  WR={wr_pos:.1f}%")
    print(f"    Delta<0 (cumul. buy pressure):  avg ret={ret_neg:+.3f} pips  WR={wr_neg:.1f}%")
    print()

# ── Q8: Divergence — price up but delta down (or vice versa) ────────────────
print("=== Q8: Price/Delta divergence as reversal signal ===\n")

df['price_sma10'] = df['close'].rolling(10).mean()
df['delta_sma10'] = df['delta'].rolling(10).mean()

# Price trending up (SMA rising) but delta trending down (selling pressure)
df['price_trend_up'] = df['price_sma10'] > df['price_sma10'].shift(10)
df['delta_trend_down'] = df['delta_sma10'] > df['delta_sma10'].shift(10)  # positive delta = selling
df['price_trend_down'] = df['price_sma10'] < df['price_sma10'].shift(10)
df['delta_trend_up'] = df['delta_sma10'] < df['delta_sma10'].shift(10)  # negative delta = buying

# Bearish divergence: price up + delta up (more selling behind the rally)
bearish_div = df['price_trend_up'] & df['delta_trend_down']
# Bullish divergence: price down + delta down (more buying behind the drop)
bullish_div = df['price_trend_down'] & df['delta_trend_up']

for look_ahead in [10, 30, 60]:
    future_ret = df['price_chg_pips'].rolling(look_ahead).sum().shift(-look_ahead)

    ret_bear = future_ret[bearish_div].mean() if bearish_div.sum() > 0 else 0
    ret_bull = future_ret[bullish_div].mean() if bullish_div.sum() > 0 else 0
    wr_bear = (future_ret[bearish_div] < 0).mean() * 100 if bearish_div.sum() > 0 else 0  # expect decline
    wr_bull = (future_ret[bullish_div] > 0).mean() * 100 if bullish_div.sum() > 0 else 0  # expect rise

    print(f"  Look-ahead {look_ahead:2d} bars:")
    print(f"    Bearish div (price↑ delta↑=sell): avg ret={ret_bear:+.3f}  decline_wr={wr_bear:.1f}%  (N={bearish_div.sum():,})")
    print(f"    Bullish div (price↓ delta↓=buy):  avg ret={ret_bull:+.3f}  rise_wr={wr_bull:.1f}%  (N={bullish_div.sum():,})")
    print()

print("=== ANALYSIS COMPLETE ===")

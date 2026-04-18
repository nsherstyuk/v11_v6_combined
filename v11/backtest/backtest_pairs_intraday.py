"""
Intraday Validation: AUDUSD/NZDUSD Pairs Trading

Validates the daily-bar stat arb results on 1-min data.

Tests:
1. Daily signal generation (same as daily backtest)
2. Intraday execution: enter at next bar after signal, not at daily close
3. Intraday z-score: compute spread z-score on 1h/4h bars
4. Realistic costs: IBKR spread + commission on both legs
5. Carry cost: net daily swap for holding spread positions

Data: C:\\nautilus0\\data\\1m_csv\\audusd_1m_tick.csv, nzdusd_1m_tick.csv
"""

import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

DATA_DIR = Path(r"C:\nautilus0\data\1m_csv")
PIP = 0.0001  # 1 pip for AUDUSD/NZDUSD
COST_PER_LEG = 0.2  # pips: 0.1 spread + 0.1 commission (IBKR)
COST_TOTAL = COST_PER_LEG * 2  # both legs = 0.4 pips round-trip

# ── Load 1-min data ──────────────────────────────────────────────────────────
print("Loading 1-min data...")
for pair in ['audusd', 'nzdusd']:
    df = pd.read_csv(DATA_DIR / f"{pair}_1m_tick.csv",
                     usecols=['timestamp', 'open', 'high', 'low', 'close'])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp').reset_index(drop=True)
    df['date'] = df['timestamp'].dt.date
    if pair == 'audusd':
        aud = df
    else:
        nzd = df

print(f"  AUDUSD: {len(aud):,} bars, {aud['timestamp'].iloc[0]} -> {aud['timestamp'].iloc[-1]}")
print(f"  NZDUSD: {len(nzd):,} bars, {nzd['timestamp'].iloc[0]} -> {nzd['timestamp'].iloc[-1]}")


# ── Align on common timestamps ──────────────────────────────────────────────
print("\nAligning timestamps...")
merged = aud[['timestamp', 'open', 'high', 'low', 'close']].rename(
    columns={'open': 'aud_open', 'high': 'aud_high', 'low': 'aud_low', 'close': 'aud_close'}
).merge(
    nzd[['timestamp', 'open', 'high', 'low', 'close']].rename(
        columns={'open': 'nzd_open', 'high': 'nzd_high', 'low': 'nzd_low', 'close': 'nzd_close'}
    ),
    on='timestamp', how='inner'
)
merged = merged.sort_values('timestamp').reset_index(drop=True)
merged['date'] = merged['timestamp'].dt.date
merged['hour'] = merged['timestamp'].dt.hour
merged['dow'] = merged['timestamp'].dt.dayofweek

print(f"  Merged: {len(merged):,} bars")

# Compute 1-min spread (log price difference)
merged['log_aud'] = np.log(merged['aud_close'])
merged['log_nzd'] = np.log(merged['nzd_close'])
merged['spread'] = merged['log_aud'] - merged['log_nzd']
merged['spread_pips'] = (merged['aud_close'] - merged['nzd_close']) / PIP


# ═══════════════════════════════════════════════════════════════════════════
# TEST 1: Daily signal with next-bar execution
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  TEST 1: Daily Signal + Next-Bar Execution")
print("=" * 70)

# Aggregate to daily bars
daily = merged.groupby('date').agg(
    aud_open=('aud_open', 'first'),
    aud_high=('aud_high', 'max'),
    aud_low=('aud_low', 'min'),
    aud_close=('aud_close', 'last'),
    nzd_open=('nzd_open', 'first'),
    nzd_high=('nzd_high', 'max'),
    nzd_low=('nzd_low', 'min'),
    nzd_close=('nzd_close', 'last'),
    bars=('aud_close', 'count'),
).reset_index()

daily = daily[daily['bars'] >= 100].copy()  # filter partial days
daily['log_aud'] = np.log(daily['aud_close'])
daily['log_nzd'] = np.log(daily['nzd_close'])
daily['spread'] = daily['log_aud'] - daily['log_nzd']
daily['spread_pips'] = (daily['aud_close'] - daily['nzd_close']) / PIP

# Rolling z-score (20-day, using PRIOR data only — no look-ahead)
LOOKBACK = 20
Z_ENTRY = 2.0
Z_EXIT = 0.0
Z_STOP = 4.0

daily['spread_mean'] = daily['spread'].shift(1).rolling(LOOKBACK).mean()
daily['spread_std'] = daily['spread'].shift(1).rolling(LOOKBACK).std()
daily['z'] = (daily['spread'] - daily['spread_mean']) / daily['spread_std']

# Simulate trades with NEXT-DAY execution
trades = []
position = 0  # 0=flat, 1=long spread (long AUD, short NZD), -1=short spread
entry_day_idx = 0
entry_aud = 0
entry_nzd = 0
entry_z = 0

for i in range(LOOKBACK + 1, len(daily)):
    row = daily.iloc[i]
    z_val = row['z']
    
    if pd.isna(z_val):
        continue
    
    if position == 0:
        if z_val > Z_ENTRY:
            # Signal: short spread (short AUD, long NZD) — spread too wide
            # Execute at NEXT day's open
            if i + 1 < len(daily):
                next_row = daily.iloc[i + 1]
                position = -1
                entry_day_idx = i + 1
                entry_aud = next_row['aud_open']
                entry_nzd = next_row['nzd_open']
                entry_z = z_val
        elif z_val < -Z_ENTRY:
            # Signal: long spread (long AUD, short NZD) — spread too narrow
            if i + 1 < len(daily):
                next_row = daily.iloc[i + 1]
                position = 1
                entry_day_idx = i + 1
                entry_aud = next_row['aud_open']
                entry_nzd = next_row['nzd_open']
                entry_z = z_val
    
    elif position != 0:
        # Check exit at today's close
        exit_aud = row['aud_close']
        exit_nzd = row['nzd_close']
        
        exit_reason = None
        
        # Mean reversion exit
        if position == 1 and z_val >= Z_EXIT:
            exit_reason = "mean_revert"
        elif position == -1 and z_val <= Z_EXIT:
            exit_reason = "mean_revert"
        
        # Stop loss
        if position == 1 and z_val < -Z_STOP:
            exit_reason = "stop_loss"
        elif position == -1 and z_val > Z_STOP:
            exit_reason = "stop_loss"
        
        if exit_reason:
            # P&L in pips
            aud_pnl = position * (exit_aud - entry_aud) / PIP
            nzd_pnl = -position * (exit_nzd - entry_nzd) / PIP  # opposite direction
            total_pnl = aud_pnl + nzd_pnl
            total_pnl -= COST_TOTAL  # deduct costs
            
            holding_days = i - entry_day_idx
            
            trades.append({
                'signal_date': daily.iloc[i - 1]['date'] if i > 0 else daily.iloc[i]['date'],
                'entry_date': daily.iloc[entry_day_idx]['date'],
                'exit_date': row['date'],
                'direction': 'long_spread' if position == 1 else 'short_spread',
                'entry_z': entry_z,
                'exit_z': z_val,
                'aud_pnl_pips': aud_pnl,
                'nzd_pnl_pips': nzd_pnl,
                'total_pnl_pips': total_pnl,
                'holding_days': holding_days,
                'exit_reason': exit_reason,
                'win': total_pnl > 0,
            })
            position = 0

trades_df = pd.DataFrame(trades)

if not trades_df.empty:
    print(f"\n  Total trades: {len(trades_df)}")
    print(f"  Win rate: {trades_df['win'].mean()*100:.1f}%")
    print(f"  Avg return: {trades_df['total_pnl_pips'].mean():+.2f} pips (after costs)")
    print(f"  Total return: {trades_df['total_pnl_pips'].sum():+.1f} pips")
    print(f"  Avg holding: {trades_df['holding_days'].mean():.1f} days")
    print(f"  Stop losses: {(trades_df['exit_reason']=='stop_loss').sum()} ({(trades_df['exit_reason']=='stop_loss').mean()*100:.1f}%)")
    
    # Sharpe
    if trades_df['total_pnl_pips'].std() > 0:
        sharpe = (trades_df['total_pnl_pips'].mean() / trades_df['total_pnl_pips'].std()) * np.sqrt(252 / trades_df['holding_days'].mean())
        print(f"  Sharpe: {sharpe:+.3f}")
    
    # Long vs short spread
    for direction in ['long_spread', 'short_spread']:
        sub = trades_df[trades_df['direction'] == direction]
        if len(sub) > 0:
            print(f"\n  {direction}:")
            print(f"    N={len(sub)}  WR={sub['win'].mean()*100:.1f}%  "
                  f"Avg={sub['total_pnl_pips'].mean():+.2f} pips  "
                  f"Total={sub['total_pnl_pips'].sum():+.1f} pips")
    
    # Year-by-year
    trades_df['year'] = pd.to_datetime(trades_df['exit_date']).dt.year
    print(f"\n  Year-by-year:")
    for year, group in trades_df.groupby('year'):
        yr_ret = group['total_pnl_pips'].sum()
        yr_wr = group['win'].mean() * 100
        yr_n = len(group)
        print(f"    {year}: ret={yr_ret:+.1f} pips  WR={yr_wr:.0f}%  N={yr_n}")
    
    # Compare with daily close execution (no slippage)
    print(f"\n  --- Slippage analysis ---")
    print(f"    Next-day open execution: avg={trades_df['total_pnl_pips'].mean():+.2f} pips")
    
    # What if we executed at signal-day close (unrealistic but shows slippage cost)
    trades_close = []
    position = 0
    entry_day_idx = 0
    entry_aud = 0
    entry_nzd = 0
    entry_z = 0
    for i in range(LOOKBACK + 1, len(daily)):
        row = daily.iloc[i]
        z_val = row['z']
        if pd.isna(z_val):
            continue
        if position == 0:
            if z_val > Z_ENTRY:
                position = -1
                entry_day_idx = i
                entry_aud = row['aud_close']
                entry_nzd = row['nzd_close']
                entry_z = z_val
            elif z_val < -Z_ENTRY:
                position = 1
                entry_day_idx = i
                entry_aud = row['aud_close']
                entry_nzd = row['nzd_close']
                entry_z = z_val
        elif position != 0:
            exit_aud = row['aud_close']
            exit_nzd = row['nzd_close']
            exit_reason = None
            if position == 1 and z_val >= Z_EXIT:
                exit_reason = "mean_revert"
            elif position == -1 and z_val <= Z_EXIT:
                exit_reason = "mean_revert"
            if position == 1 and z_val < -Z_STOP:
                exit_reason = "stop_loss"
            elif position == -1 and z_val > Z_STOP:
                exit_reason = "stop_loss"
            if exit_reason:
                aud_pnl = position * (exit_aud - entry_aud) / PIP
                nzd_pnl = -position * (exit_nzd - entry_nzd) / PIP
                total_pnl = aud_pnl + nzd_pnl - COST_TOTAL
                holding_days = i - entry_day_idx
                trades_close.append({
                    'total_pnl_pips': total_pnl,
                    'holding_days': holding_days,
                    'win': total_pnl > 0,
                })
                position = 0
    
    if trades_close:
        tc = pd.DataFrame(trades_close)
        print(f"    Same-day close execution: avg={tc['total_pnl_pips'].mean():+.2f} pips (unrealistic)")
        print(f"    Slippage cost: {tc['total_pnl_pips'].mean() - trades_df['total_pnl_pips'].mean():+.2f} pips per trade")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 2: Intraday z-score on 1H bars
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  TEST 2: Intraday Z-Score (1H bars)")
print("=" * 70)

# Aggregate to 1H bars
merged['hour_bucket'] = merged['timestamp'].dt.floor('h')
hourly = merged.groupby('hour_bucket').agg(
    aud_open=('aud_open', 'first'),
    aud_high=('aud_high', 'max'),
    aud_low=('aud_low', 'min'),
    aud_close=('aud_close', 'last'),
    nzd_open=('nzd_open', 'first'),
    nzd_high=('nzd_high', 'max'),
    nzd_low=('nzd_low', 'min'),
    nzd_close=('nzd_close', 'last'),
    bars=('aud_close', 'count'),
).reset_index()

hourly = hourly[hourly['bars'] >= 5].copy()
hourly['log_aud'] = np.log(hourly['aud_close'])
hourly['log_nzd'] = np.log(hourly['nzd_close'])
hourly['spread'] = hourly['log_aud'] - hourly['log_nzd']
hourly['spread_pips'] = (hourly['aud_close'] - hourly['nzd_close']) / PIP

# Rolling z-score on 1H bars (20 hours = ~1 day lookback, 120 hours = ~1 week)
for lb_hours in [20, 60, 120]:
    hourly[f'z_{lb_hours}h'] = (
        (hourly['spread'] - hourly['spread'].shift(1).rolling(lb_hours).mean()) 
        / hourly['spread'].shift(1).rolling(lb_hours).std()
    )

# Simulate trades on 1H bars
for lb_hours in [20, 60, 120]:
    z_col = f'z_{lb_hours}h'
    
    trades_1h = []
    position = 0
    entry_idx = 0
    entry_aud = 0
    entry_nzd = 0
    entry_z = 0
    
    for i in range(lb_hours + 1, len(hourly)):
        z_val = hourly.iloc[i][z_col]
        if pd.isna(z_val):
            continue
        
        if position == 0:
            if z_val > Z_ENTRY:
                # Short spread at next bar's open
                if i + 1 < len(hourly):
                    next_row = hourly.iloc[i + 1]
                    position = -1
                    entry_idx = i + 1
                    entry_aud = next_row['aud_open']
                    entry_nzd = next_row['nzd_open']
                    entry_z = z_val
            elif z_val < -Z_ENTRY:
                if i + 1 < len(hourly):
                    next_row = hourly.iloc[i + 1]
                    position = 1
                    entry_idx = i + 1
                    entry_aud = next_row['aud_open']
                    entry_nzd = next_row['nzd_open']
                    entry_z = z_val
        
        elif position != 0:
            exit_aud = hourly.iloc[i]['aud_close']
            exit_nzd = hourly.iloc[i]['nzd_close']
            exit_reason = None
            
            if position == 1 and z_val >= Z_EXIT:
                exit_reason = "mean_revert"
            elif position == -1 and z_val <= Z_EXIT:
                exit_reason = "mean_revert"
            if position == 1 and z_val < -Z_STOP:
                exit_reason = "stop_loss"
            elif position == -1 and z_val > Z_STOP:
                exit_reason = "stop_loss"
            
            # Max holding: 120 bars (5 days)
            holding_bars = i - entry_idx
            if holding_bars >= 120:
                exit_reason = "max_hold"
            
            if exit_reason:
                aud_pnl = position * (exit_aud - entry_aud) / PIP
                nzd_pnl = -position * (exit_nzd - entry_nzd) / PIP
                total_pnl = aud_pnl + nzd_pnl - COST_TOTAL
                holding_hours = holding_bars
                
                trades_1h.append({
                    'total_pnl_pips': total_pnl,
                    'holding_hours': holding_hours,
                    'exit_reason': exit_reason,
                    'win': total_pnl > 0,
                })
                position = 0
    
    if trades_1h:
        t1h = pd.DataFrame(trades_1h)
        avg_ret = t1h['total_pnl_pips'].mean()
        wr = t1h['win'].mean() * 100
        total = t1h['total_pnl_pips'].sum()
        avg_hold = t1h['holding_hours'].mean()
        n = len(t1h)
        sl_pct = (t1h['exit_reason'] == 'stop_loss').mean() * 100
        mh_pct = (t1h['exit_reason'] == 'max_hold').mean() * 100
        
        if t1h['total_pnl_pips'].std() > 0:
            sharpe = (t1h['total_pnl_pips'].mean() / t1h['total_pnl_pips'].std()) * np.sqrt(252 * 6 / avg_hold) if avg_hold > 0 else 0
        else:
            sharpe = 0
        
        print(f"\n  Lookback: {lb_hours}h ({lb_hours//24}d)  |  z_entry={Z_ENTRY}")
        print(f"    Trades: {n}  WR: {wr:.1f}%  Avg ret: {avg_ret:+.2f} pips  Total: {total:+.1f} pips")
        print(f"    Avg hold: {avg_hold:.1f}h ({avg_hold/24:.1f}d)  Stop losses: {sl_pct:.1f}%  Max holds: {mh_pct:.1f}%")
        print(f"    Sharpe: {sharpe:+.3f}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 3: Spread behavior analysis
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  TEST 3: Spread Behavior Analysis")
print("=" * 70)

# Daily spread stats
print(f"\n  AUDUSD - NZDUSD spread (daily):")
print(f"    Mean: {daily['spread_pips'].mean():.1f} pips")
print(f"    Std:  {daily['spread_pips'].std():.1f} pips")
print(f"    Min:  {daily['spread_pips'].min():.1f} pips")
print(f"    Max:  {daily['spread_pips'].max():.1f} pips")
print(f"    Current: {daily['spread_pips'].iloc[-1]:.1f} pips")

# How often does spread deviate >2σ from 20-day mean?
daily['z_clean'] = daily['z'].dropna()
z_valid = daily['z'].dropna()
above_2 = (z_valid > 2).sum()
below_2 = (z_valid < -2).sum()
total_days = len(z_valid)
print(f"\n  Z-score distribution (daily, 20-day lookback):")
print(f"    Total days: {total_days}")
print(f"    |z| > 2: {above_2 + below_2} ({(above_2+below_2)/total_days*100:.1f}%)")
print(f"    |z| > 3: {(z_valid.abs() > 3).sum()} ({(z_valid.abs() > 3).mean()*100:.1f}%)")
print(f"    |z| > 4: {(z_valid.abs() > 4).sum()} ({(z_valid.abs() > 4).mean()*100:.1f}%)")
print(f"    z > 2 (short spread): {above_2}")
print(f"    z < -2 (long spread): {below_2}")

# Half-life of spread mean-reversion
# Regress Δspread on lagged spread
spread = daily['spread'].dropna()
delta_spread = spread.diff().iloc[1:]
lagged_spread = spread.iloc[:-1] - spread.mean()
lagged_spread.index = delta_spread.index

# OLS: Δspread = λ * (spread - mean) + ε
# Half-life = -ln(2) / λ
from numpy.polynomial.polynomial import polyfit
try:
    coeffs = np.polyfit(lagged_spread.values, delta_spread.values, 1)
    lam = coeffs[0]
    if lam < 0:
        half_life = -np.log(2) / lam
        print(f"\n  Spread half-life: {half_life:.1f} days")
        print(f"    (λ = {lam:.4f})")
        print(f"    Interpretation: spread reverts halfway to mean in {half_life:.1f} days")
    else:
        print(f"\n  Spread does NOT mean-revert (λ = {lam:.4f} > 0)")
except Exception as e:
    print(f"\n  Half-life calculation failed: {e}")

# Intraday spread volatility by session
print(f"\n  Intraday spread volatility by session (1H bars):")
hourly['session'] = np.where(
    (hourly['hour_bucket'].dt.hour >= 0) & (hourly['hour_bucket'].dt.hour < 7), 'Asian',
    np.where(
        (hourly['hour_bucket'].dt.hour >= 7) & (hourly['hour_bucket'].dt.hour < 13), 'London',
        np.where(
            (hourly['hour_bucket'].dt.hour >= 13) & (hourly['hour_bucket'].dt.hour < 22), 'NY',
            'Off'
        )
    )
)

for session in ['Asian', 'London', 'NY']:
    sub = hourly[hourly['session'] == session]
    spread_change = sub['spread_pips'].diff().abs()
    print(f"    {session:8s}: avg |Δspread| = {spread_change.mean():.2f} pips/hour  "
          f"std = {sub['spread_pips'].std():.1f} pips")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 4: Carry cost impact
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  TEST 4: Carry Cost Impact")
print("=" * 70)

# Approximate carry: AUD rate - NZD rate
# When long spread (long AUD, short NZD): earn AUD rate - pay NZD rate = positive carry
# When short spread (short AUD, long NZD): pay AUD rate - earn NZD rate = negative carry
CARRY_RATES = {
    2018: {"AUD": 1.50, "NZD": 1.75},  # NZD > AUD → negative carry for long spread
    2019: {"AUD": 0.75, "NZD": 1.00},
    2020: {"AUD": 0.10, "NZD": 0.25},
    2021: {"AUD": 0.10, "NZD": 0.50},
    2022: {"AUD": 3.10, "NZD": 4.25},
    2023: {"AUD": 4.35, "NZD": 5.50},
    2024: {"AUD": 4.35, "NZD": 4.25},
    2025: {"AUD": 3.85, "NZD": 3.50},
    2026: {"AUD": 3.85, "NZD": 3.50},
}

if not trades_df.empty:
    # Estimate carry cost per trade
    carry_costs = []
    for _, t in trades_df.iterrows():
        year = pd.to_datetime(t['exit_date']).year
        rates = CARRY_RATES.get(year, CARRY_RATES[2026])
        aud_rate = rates['AUD'] / 100
        nzd_rate = rates['NZD'] / 100
        
        # Net daily carry per $100K position
        # Long spread: earn AUD rate on $100K AUD, pay NZD rate on $100K NZD
        # Short spread: opposite
        if t['direction'] == 'long_spread':
            net_daily_carry = (aud_rate - nzd_rate) / 252  # as fraction of notional
        else:
            net_daily_carry = (nzd_rate - aud_rate) / 252
        
        # Convert to pips (approximate: 1% on $100K = $1000/year = ~10 pips/day on 0.0001 pip)
        # More precisely: daily carry in pips ≈ net_daily_carry / PIP
        carry_pips = net_daily_carry / PIP * t['holding_days']
        carry_costs.append(carry_pips)
    
    trades_df['carry_pips'] = carry_costs
    trades_df['total_with_carry'] = trades_df['total_pnl_pips'] + trades_df['carry_pips']
    
    print(f"\n  Carry impact on trades:")
    print(f"    Avg carry per trade: {trades_df['carry_pips'].mean():+.2f} pips")
    print(f"    Total carry: {trades_df['carry_pips'].sum():+.1f} pips")
    print(f"    Total P&L without carry: {trades_df['total_pnl_pips'].sum():+.1f} pips")
    print(f"    Total P&L with carry: {trades_df['total_with_carry'].sum():+.1f} pips")
    
    # AUD vs NZD rate regime
    print(f"\n  AUD-NZD rate differential by year:")
    for year, rates in CARRY_RATES.items():
        diff = rates['AUD'] - rates['NZD']
        print(f"    {year}: AUD={rates['AUD']:.2f}% NZD={rates['NZD']:.2f}% diff={diff:+.2f}% "
              f"({'positive' if diff > 0 else 'negative'} carry for long spread)")


# ═══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  INTRADAY VALIDATION — SUMMARY")
print("=" * 70)
print("""
  AUDUSD/NZDUSD Pairs Trading

  Daily signal (z_entry=2.0, 20-day lookback):
    - Next-day open execution (realistic)
    - Costs deducted: 0.4 pips per round-trip (both legs)
    - Carry cost estimated from policy rates

  Intraday signal (1H bars):
    - More frequent signals, shorter holding periods
    - Same z-score logic applied on hourly bars
    - Max hold: 120 hours (5 days)

  Key questions answered:
    1. Does the edge survive next-day execution? (slippage)
    2. Does intraday z-score work? (higher frequency)
    3. What's the spread half-life? (mean-reversion speed)
    4. How much does carry cost erode returns?
""")

print("\n=== BACKTEST COMPLETE ===")

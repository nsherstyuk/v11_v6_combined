"""
Intraday Momentum Backtest — Session-Level Trend Following on FX

Tests whether short-lived trends (2-6 hours) exist within FX trading sessions.
Daily trend following fails on FX (mean-reverting across days), but intraday
institutional order flow may create persistent directional moves within sessions.

Approaches tested:
1. Session momentum: first N minutes of London/NY predict next few hours
2. Intraday channel breakout: break above M-hour range → follow for K hours
3. Hourly trend following: 1H bar channel breakout, ride for 4-8 bars
4. Momentum cascade: 1H + 4H alignment → enter, hold half day

Data: GBPUSD 1-min bars (2018-2026), clean data verified
Cost: 0.3 pips round-trip (IBKR spread + commission for GBPUSD)

Reference: Gao et al. (2018) "Intraday Momentum" — first 30 min predicts
next few hours in equity/FX markets.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

DATA_DIR = Path(r"C:\nautilus0\data\1m_csv")
PIP = 0.0001  # 1 pip for GBPUSD
COST_PIPS = 0.3  # round-trip cost (spread + commission)

# ═══════════════════════════════════════════════════════════════════════════
# LOAD AND PREPARE DATA
# ═══════════════════════════════════════════════════════════════════════════

print("Loading GBPUSD 1-min data...")
df = pd.read_csv(DATA_DIR / "gbpusd_1m_tick.csv",
                 usecols=['timestamp', 'open', 'high', 'low', 'close'])
df['timestamp'] = pd.to_datetime(df['timestamp'])
df = df.sort_values('timestamp').reset_index(drop=True)

# Session labels (UTC times)
# Asian: 00:00-07:00
# London: 07:00-16:00
# NY overlap: 12:00-22:00
# London+NY: 07:00-22:00
df['hour'] = df['timestamp'].dt.hour
df['dow'] = df['timestamp'].dt.dayofweek  # 0=Mon, 6=Sun
df['date'] = df['timestamp'].dt.date

# Filter: weekdays only
df = df[df['dow'] < 5].copy()

print(f"  Rows: {len(df):,}")
print(f"  Date range: {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}")
print(f"  Close range: {df['close'].min():.4f} -> {df['close'].max():.4f}")


# ═══════════════════════════════════════════════════════════════════════════
# HELPER: Aggregate to desired bar size
# ═══════════════════════════════════════════════════════════════════════════

def aggregate_bars(df, freq='1H'):
    """Aggregate 1-min bars to desired frequency."""
    agg = df.groupby(pd.Grouper(key='timestamp', freq=freq)).agg(
        open=('open', 'first'),
        high=('high', 'max'),
        low=('low', 'min'),
        close=('close', 'last'),
        bars=('close', 'count'),
    ).dropna(subset=['close'])
    agg = agg[agg['bars'] >= 5].copy()  # filter partial bars
    return agg


# ═══════════════════════════════════════════════════════════════════════════
# TEST 1: SESSION MOMENTUM
# Does the first N minutes of London predict the next few hours?
# ═══════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 80)
print("  TEST 1: SESSION MOMENTUM — London Open")
print("  Logic: First N minutes of London (07:00 UTC) predict next K hours")
print("=" * 80)

LONDON_OPEN = 7   # UTC
LONDON_END = 16   # UTC

# For each trading day, measure:
# - Return during first N minutes of London session
# - Return during the remainder of the London session
# - Test: does the first-hour direction predict the rest?

daily_sessions = []
for date, day_df in df.groupby('date'):
    london = day_df[(day_df['hour'] >= LONDON_OPEN) & (day_df['hour'] < LONDON_END)]
    if len(london) < 60:  # need at least 60 bars
        continue

    london_start_price = london.iloc[0]['open']

    # First N minutes return
    for first_mins in [15, 30, 60, 120]:
        if len(london) < first_mins:
            continue
        first_return = (london.iloc[first_mins - 1]['close'] - london_start_price) / PIP

        # Remaining session return (from end of first period to session close)
        remain_return = (london.iloc[-1]['close'] - london.iloc[first_mins - 1]['close']) / PIP

        # Full session return
        full_return = (london.iloc[-1]['close'] - london_start_price) / PIP

        daily_sessions.append({
            'date': date,
            'dow': london.iloc[0]['dow'],
            f'first_{first_mins}m_ret': first_return,
            f'first_{first_mins}m_dir': 1 if first_return > 0 else -1,
            f'remain_after_{first_mins}m_ret': remain_return,
            f'full_session_ret': full_return,
        })

sessions_df = pd.DataFrame(daily_sessions)
sessions_df['date'] = pd.to_datetime(sessions_df['date'])

for first_mins in [15, 30, 60, 120]:
    dir_col = f'first_{first_mins}m_dir'
    ret_col = f'first_{first_mins}m_ret'
    remain_col = f'remain_after_{first_mins}m_ret'

    # Subset: only days where first period moved enough to be tradeable (>2 pips)
    for min_move in [0, 2, 5, 10]:
        sub = sessions_df[sessions_df[ret_col].abs() > min_move]
        if len(sub) == 0:
            continue

        # Follow the first-period direction
        sub = sub.copy()
        sub['signal'] = sub[dir_col]
        sub['pnl_raw'] = sub['signal'] * sub[remain_col]
        sub['pnl_net'] = sub['pnl_raw'] - COST_PIPS  # one round-trip

        n = len(sub)
        wr = (sub['pnl_net'] > 0).mean() * 100
        avg = sub['pnl_net'].mean()
        total = sub['pnl_net'].sum()
        std = sub['pnl_net'].std()
        sharpe = (avg / std * np.sqrt(252)) if std > 0 else 0

        # Continuation rate: % of time remaining session moves same direction
        cont_rate = (sub['signal'] * sub[remain_col] > 0).mean() * 100

        print(f"  First {first_mins:3d}min, min_move={min_move:2d}pips: "
              f"N={n:5d}  Cont={cont_rate:5.1f}%  "
              f"WR={wr:5.1f}%  Avg={avg:+6.2f}pips  "
              f"Total={total:+8.0f}pips  Sharpe={sharpe:+6.3f}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 2: SESSION MOMENTUM — NY Open
# ═══════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 80)
print("  TEST 2: SESSION MOMENTUM — NY Open (13:00 UTC)")
print("=" * 80)

NY_OPEN = 13
NY_END = 22

daily_ny = []
for date, day_df in df.groupby('date'):
    ny = day_df[(day_df['hour'] >= NY_OPEN) & (day_df['hour'] < NY_END)]
    if len(ny) < 60:
        continue

    ny_start_price = ny.iloc[0]['open']

    for first_mins in [15, 30, 60]:
        if len(ny) < first_mins:
            continue
        first_return = (ny.iloc[first_mins - 1]['close'] - ny_start_price) / PIP
        remain_return = (ny.iloc[-1]['close'] - ny.iloc[first_mins - 1]['close']) / PIP

        daily_ny.append({
            'date': date,
            f'first_{first_mins}m_ret': first_return,
            f'first_{first_mins}m_dir': 1 if first_return > 0 else -1,
            f'remain_after_{first_mins}m_ret': remain_return,
        })

ny_df = pd.DataFrame(daily_ny)
ny_df['date'] = pd.to_datetime(ny_df['date'])

for first_mins in [15, 30, 60]:
    dir_col = f'first_{first_mins}m_dir'
    ret_col = f'first_{first_mins}m_ret'
    remain_col = f'remain_after_{first_mins}m_ret'

    for min_move in [0, 2, 5, 10]:
        sub = ny_df[ny_df[ret_col].abs() > min_move].copy()
        if len(sub) == 0:
            continue

        sub['signal'] = sub[dir_col]
        sub['pnl_raw'] = sub['signal'] * sub[remain_col]
        sub['pnl_net'] = sub['pnl_raw'] - COST_PIPS

        n = len(sub)
        wr = (sub['pnl_net'] > 0).mean() * 100
        avg = sub['pnl_net'].mean()
        total = sub['pnl_net'].sum()
        std = sub['pnl_net'].std()
        sharpe = (avg / std * np.sqrt(252)) if std > 0 else 0
        cont_rate = (sub['signal'] * sub[remain_col] > 0).mean() * 100

        print(f"  First {first_mins:3d}min, min_move={min_move:2d}pips: "
              f"N={n:5d}  Cont={cont_rate:5.1f}%  "
              f"WR={wr:5.1f}%  Avg={avg:+6.2f}pips  "
              f"Total={total:+8.0f}pips  Sharpe={sharpe:+6.3f}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 3: INTRADAY CHANNEL BREAKOUT (1H bars)
# Break above N-hour range → follow for K hours
# ═══════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 80)
print("  TEST 3: INTRADAY CHANNEL BREAKOUT (1H bars)")
print("  Logic: Break above N-hour high/low → enter, hold for K hours")
print("=" * 80)

hourly = aggregate_bars(df, '1h')
hourly['hour'] = hourly.index.hour

# Rolling high/low on 1H bars
for lookback_h in [4, 8, 12]:
    hourly[f'hh_{lookback_h}'] = hourly['high'].shift(1).rolling(lookback_h).max()
    hourly[f'll_{lookback_h}'] = hourly['low'].shift(1).rolling(lookback_h).min()
    hourly[f'range_{lookback_h}'] = hourly[f'hh_{lookback_h}'] - hourly[f'll_{lookback_h}']

    for hold_h in [2, 4, 6, 8]:
        trades = []

        for i in range(lookback_h + 1, len(hourly) - hold_h):
            row = hourly.iloc[i]
            hour = row['hour']

            # Only trade during London+NY (07-20 UTC)
            if hour < 7 or hour > 20 - hold_h:
                continue

            hh = row[f'hh_{lookback_h}']
            ll = row[f'll_{lookback_h}']
            rng = row[f'range_{lookback_h}']

            if pd.isna(hh) or pd.isna(ll) or rng < 5 * PIP:
                continue

            # Breakout entry
            if row['close'] > hh:
                # Long breakout
                entry = hh  # enter at the breakout level (stop order)
                # Actually use next bar open for realistic fill
                if i + 1 < len(hourly):
                    entry = hourly.iloc[i + 1]['open']
                    direction = 1
                else:
                    continue
            elif row['close'] < ll:
                # Short breakout
                entry = ll
                if i + 1 < len(hourly):
                    entry = hourly.iloc[i + 1]['open']
                    direction = -1
                else:
                    continue
            else:
                continue

            # Exit at hold_h bars later
            exit_price = hourly.iloc[i + 1 + hold_h - 1]['close']
            pnl_pips = direction * (exit_price - entry) / PIP - COST_PIPS

            # ATR stop: 1.5x range
            sl_pips = 1.5 * rng / PIP
            max_adverse = direction * (hourly.iloc[i + 1:i + 1 + hold_h]['low'].min() - entry) if direction == 1 else \
                          direction * (hourly.iloc[i + 1:i + 1 + hold_h]['high'].max() - entry)
            if direction == 1:
                max_adverse = (hourly.iloc[i + 1:i + 1 + hold_h]['low'].min() - entry) / PIP
            else:
                max_adverse = (entry - hourly.iloc[i + 1:i + 1 + hold_h]['high'].max()) / PIP

            hit_sl = max_adverse < -sl_pips

            trades.append({
                'entry_time': hourly.index[i + 1],
                'exit_time': hourly.index[i + 1 + hold_h - 1],
                'direction': direction,
                'entry': entry,
                'exit': exit_price,
                'pnl_pips': pnl_pips,
                'hit_sl': hit_sl,
                'range_pips': rng / PIP,
                'hour': hour,
            })

        if not trades:
            continue

        tdf = pd.DataFrame(trades)
        n = len(tdf)
        wr = (tdf['pnl_pips'] > 0).mean() * 100
        avg = tdf['pnl_pips'].mean()
        total = tdf['pnl_pips'].sum()
        std = tdf['pnl_pips'].std()
        sharpe = (avg / std * np.sqrt(252 * 6 / hold_h)) if std > 0 else 0  # annualize
        sl_pct = tdf['hit_sl'].mean() * 100

        print(f"  LB={lookback_h:2d}h Hold={hold_h}h: "
              f"N={n:5d}  WR={wr:5.1f}%  Avg={avg:+6.2f}pips  "
              f"Total={total:+8.0f}  Sharpe={sharpe:+6.3f}  SL={sl_pct:4.1f}%")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 4: HOURLY TREND FOLLOWING (1H channel breakout with trailing exit)
# ═══════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 80)
print("  TEST 4: HOURLY TREND FOLLOWING (1H bars, trailing exit)")
print("  Logic: Enter on N-bar high/low break, exit on opposite M-bar break")
print("  Same as daily trend following but on 1H bars, session-bound")
print("=" * 80)

for entry_bars in [8, 12, 20]:
    for exit_bars in [4, 8]:
        hourly[f'hh_e{entry_bars}'] = hourly['high'].shift(1).rolling(entry_bars).max()
        hourly[f'll_e{entry_bars}'] = hourly['low'].shift(1).rolling(entry_bars).min()
        hourly[f'hh_x{exit_bars}'] = hourly['high'].shift(1).rolling(exit_bars).max()
        hourly[f'll_x{exit_bars}'] = hourly['low'].shift(1).rolling(exit_bars).min()

        trades = []
        position = 0  # 0=flat, 1=long, -1=short
        entry_price = 0
        entry_idx = 0

        for i in range(entry_bars + 1, len(hourly)):
            row = hourly.iloc[i]
            hour = row['hour']

            # No new entries outside London+NY
            hh_e = row[f'hh_e{entry_bars}']
            ll_e = row[f'll_e{entry_bars}']

            if pd.isna(hh_e) or pd.isna(ll_e):
                continue

            if position == 0:
                # Only enter during active hours
                if hour < 7 or hour > 18:
                    continue

                if row['close'] > hh_e:
                    # Long breakout — enter at next bar open
                    if i + 1 < len(hourly):
                        position = 1
                        entry_price = hourly.iloc[i + 1]['open']
                        entry_idx = i + 1
                elif row['close'] < ll_e:
                    if i + 1 < len(hourly):
                        position = -1
                        entry_price = hourly.iloc[i + 1]['open']
                        entry_idx = i + 1

            elif position != 0:
                # Exit on opposite break (use pre-computed columns)
                hh_x = row.get(f'hh_x{exit_bars}', np.nan)
                ll_x = row.get(f'll_x{exit_bars}', np.nan)

                exit_reason = None

                # Time-based exit: close before 21:00 UTC (don't hold overnight)
                if hour >= 21:
                    exit_reason = "eod"

                # Max hold: 12 bars (12 hours)
                if i - entry_idx >= 12:
                    exit_reason = "max_hold"

                # Opposite break exit
                if position == 1 and not pd.isna(ll_x) and row['close'] < ll_x:
                    exit_reason = "opposite_break"
                elif position == -1 and not pd.isna(hh_x) and row['close'] > hh_x:
                    exit_reason = "opposite_break"

                # ATR stop: 2x entry-bar range
                atr = hourly.iloc[entry_idx]['high'] - hourly.iloc[entry_idx]['low'] if entry_idx < len(hourly) else 0
                if position == 1 and row['low'] < entry_price - 2 * atr:
                    exit_reason = "atr_sl"
                elif position == -1 and row['high'] > entry_price + 2 * atr:
                    exit_reason = "atr_sl"

                if exit_reason:
                    exit_price = row['close']
                    pnl = position * (exit_price - entry_price) / PIP - COST_PIPS
                    hold_bars = i - entry_idx


                    trades.append({
                        'entry_time': hourly.index[entry_idx],
                        'exit_time': hourly.index[i],
                        'direction': position,
                        'pnl_pips': pnl,
                        'hold_bars': hold_bars,
                        'exit_reason': exit_reason,
                        'hour': hourly.iloc[entry_idx]['hour'],
                    })
                    position = 0

        if not trades:
            continue

        tdf = pd.DataFrame(trades)
        n = len(tdf)
        wr = (tdf['pnl_pips'] > 0).mean() * 100
        avg = tdf['pnl_pips'].mean()
        total = tdf['pnl_pips'].sum()
        std = tdf['pnl_pips'].std()
        avg_hold = tdf['hold_bars'].mean()
        sharpe = (avg / std * np.sqrt(252 * 6 / max(avg_hold, 1))) if std > 0 else 0

        # Exit reason breakdown
        reasons = tdf.groupby('exit_reason')['pnl_pips'].agg(['count', 'mean'])

        print(f"\n  Entry={entry_bars}h Exit={exit_bars}h: "
              f"N={n:5d}  WR={wr:5.1f}%  Avg={avg:+6.2f}pips  "
              f"Total={total:+8.0f}  Sharpe={sharpe:+6.3f}  AvgHold={avg_hold:.1f}h")
        for reason, row in reasons.iterrows():
            print(f"    {reason:18s}: N={int(row['count']):4d}  Avg={row['mean']:+6.2f}pips")

        # Year-by-year
        tdf['year'] = pd.to_datetime(tdf['exit_time']).dt.year
        print(f"    Year-by-year:")
        for year, grp in tdf.groupby('year'):
            yr_total = grp['pnl_pips'].sum()
            yr_wr = (grp['pnl_pips'] > 0).mean() * 100
            yr_n = len(grp)
            print(f"      {year}: N={yr_n:4d}  WR={yr_wr:5.1f}%  Total={yr_total:+7.0f}pips")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 5: MOMENTUM CASCADE (1H + 4H alignment)
# ═══════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 80)
print("  TEST 5: MOMENTUM CASCADE (1H + 4H alignment)")
print("  Logic: Only enter when 1H and 4H both break same direction")
print("=" * 80)

fourh = aggregate_bars(df, '4h')
fourh['hour'] = fourh.index.hour

# 4H trend: close vs 4H SMA
for sma_len in [5, 10, 20]:
    fourh[f'sma_{sma_len}'] = fourh['close'].rolling(sma_len).mean()
    fourh[f'4h_trend'] = np.where(fourh['close'] > fourh[f'sma_{sma_len}'], 1, -1)

    # 1H breakout (from Test 4 best config)
    entry_bars = 12
    hourly['hh_12'] = hourly['high'].shift(1).rolling(entry_bars).max()
    hourly['ll_12'] = hourly['low'].shift(1).rolling(entry_bars).min()
    hourly['ll_8'] = hourly['low'].shift(1).rolling(8).min()
    hourly['hh_8'] = hourly['high'].shift(1).rolling(8).max()

    trades = []
    position = 0
    entry_price = 0
    entry_idx = 0
    entry_4h_trend = 0

    for i in range(entry_bars + 1, len(hourly)):
        row = hourly.iloc[i]
        hour = row['hour']

        hh_12 = row['hh_12']
        ll_12 = row['ll_12']
        if pd.isna(hh_12) or pd.isna(ll_12):
            continue

        # Get current 4H trend
        ts = hourly.index[i]
        # Find the 4H bar that contains this 1H bar
        valid_4h = fourh[fourh.index <= ts]
        if len(valid_4h) == 0:
            continue
        current_4h_trend = valid_4h.iloc[-1].get(f'4h_trend', 0)
        if pd.isna(current_4h_trend):
            continue

        if position == 0:
            if hour < 7 or hour > 18:
                continue

            # 1H breakout + 4H alignment
            if row['close'] > hh_12 and current_4h_trend == 1:
                # Long: 1H breakout + 4H uptrend
                if i + 1 < len(hourly):
                    position = 1
                    entry_price = hourly.iloc[i + 1]['open']
                    entry_idx = i + 1
                    entry_4h_trend = current_4h_trend
            elif row['close'] < ll_12 and current_4h_trend == -1:
                # Short: 1H breakout + 4H downtrend
                if i + 1 < len(hourly):
                    position = -1
                    entry_price = hourly.iloc[i + 1]['open']
                    entry_idx = i + 1
                    entry_4h_trend = current_4h_trend

        elif position != 0:
            exit_reason = None

            # EOD exit
            if hour >= 21:
                exit_reason = "eod"

            # Max hold 12h
            if i - entry_idx >= 12:
                exit_reason = "max_hold"

            # ATR stop
            if entry_idx < len(hourly):
                atr = hourly.iloc[entry_idx]['high'] - hourly.iloc[entry_idx]['low']
                if position == 1 and row['low'] < entry_price - 2 * atr:
                    exit_reason = "atr_sl"
                elif position == -1 and row['high'] > entry_price + 2 * atr:
                    exit_reason = "atr_sl"

            # Opposite 1H break (use pre-computed columns)
            ll_8 = row.get('ll_8', np.nan)
            hh_8 = row.get('hh_8', np.nan)
            if position == 1 and not pd.isna(ll_8) and row['close'] < ll_8:
                exit_reason = "opposite_break"
            elif position == -1 and not pd.isna(hh_8) and row['close'] > hh_8:
                exit_reason = "opposite_break"

            if exit_reason:
                pnl = position * (row['close'] - entry_price) / PIP - COST_PIPS
                hold_bars = i - entry_idx

                trades.append({
                    'entry_time': hourly.index[entry_idx],
                    'exit_time': hourly.index[i],
                    'direction': position,
                    'pnl_pips': pnl,
                    'hold_bars': hold_bars,
                    'exit_reason': exit_reason,
                })
                position = 0

    if not trades:
        continue

    tdf = pd.DataFrame(trades)
    n = len(tdf)
    wr = (tdf['pnl_pips'] > 0).mean() * 100
    avg = tdf['pnl_pips'].mean()
    total = tdf['pnl_pips'].sum()
    std = tdf['pnl_pips'].std()
    avg_hold = tdf['hold_bars'].mean()
    sharpe = (avg / std * np.sqrt(252 * 6 / max(avg_hold, 1))) if std > 0 else 0

    print(f"\n  4H SMA={sma_len}: "
          f"N={n:5d}  WR={wr:5.1f}%  Avg={avg:+6.2f}pips  "
          f"Total={total:+8.0f}  Sharpe={sharpe:+6.3f}  AvgHold={avg_hold:.1f}h")

    # Year-by-year
    tdf['year'] = pd.to_datetime(tdf['exit_time']).dt.year
    for year, grp in tdf.groupby('year'):
        yr_total = grp['pnl_pips'].sum()
        yr_wr = (grp['pnl_pips'] > 0).mean() * 100
        yr_n = len(grp)
        print(f"      {year}: N={yr_n:4d}  WR={yr_wr:5.1f}%  Total={yr_total:+7.0f}pips")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 6: BEST CONFIG — DETAILED ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 80)
print("  TEST 6: DETAILED ANALYSIS — LONDON SESSION MOMENTUM (60min)")
print("  Best candidate from Test 1, with filters and breakdowns")
print("=" * 80)

# Re-run London 60-min momentum with more detail
london_trades = []
for date, day_df in df.groupby('date'):
    london = day_df[(day_df['hour'] >= LONDON_OPEN) & (day_df['hour'] < LONDON_END)]
    if len(london) < 120:
        continue

    open_price = london.iloc[0]['open']
    first_hour_close = london.iloc[59]['close']
    first_hour_ret = (first_hour_close - open_price) / PIP
    direction = 1 if first_hour_ret > 0 else -1

    # Remaining session P&L (following the direction)
    remain_ret = (london.iloc[-1]['close'] - first_hour_close) / PIP
    pnl = direction * remain_ret - COST_PIPS

    # Day of week
    dow = london.iloc[0]['dow']

    # First hour range (volatility proxy)
    first_hour_range = (london.iloc[:60]['high'].max() - london.iloc[:60]['low'].min()) / PIP

    # Asian range (00:00-07:00)
    asian = day_df[(day_df['hour'] >= 0) & (day_df['hour'] < 7)]
    asian_range = (asian['high'].max() - asian['low'].min()) / PIP if len(asian) > 10 else 0

    london_trades.append({
        'date': date,
        'dow': dow,
        'direction': direction,
        'first_hour_ret': first_hour_ret,
        'first_hour_range': first_hour_range,
        'asian_range': asian_range,
        'pnl_pips': pnl,
        'remain_ret': remain_ret,
        'win': pnl > 0,
    })

lt = pd.DataFrame(london_trades)
lt['date'] = pd.to_datetime(lt['date'])
lt['year'] = lt['date'].dt.year

# Overall
n = len(lt)
wr = lt['win'].mean() * 100
avg = lt['pnl_pips'].mean()
total = lt['pnl_pips'].sum()
std = lt['pnl_pips'].std()
sharpe = (avg / std * np.sqrt(252)) if std > 0 else 0

print(f"\n  Overall: N={n}  WR={wr:.1f}%  Avg={avg:+.2f}pips  "
      f"Total={total:+.0f}pips  Sharpe={sharpe:+.3f}")

# By day of week
print(f"\n  By day of week:")
for dow, grp in lt.groupby('dow'):
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    wr_d = grp['win'].mean() * 100
    avg_d = grp['pnl_pips'].mean()
    n_d = len(grp)
    print(f"    {days[dow]}: N={n_d:4d}  WR={wr_d:5.1f}%  Avg={avg_d:+6.2f}pips")

# By year
print(f"\n  By year:")
for year, grp in lt.groupby('year'):
    yr_wr = grp['win'].mean() * 100
    yr_avg = grp['pnl_pips'].mean()
    yr_total = grp['pnl_pips'].sum()
    yr_n = len(grp)
    yr_std = grp['pnl_pips'].std()
    yr_sharpe = (yr_avg / yr_std * np.sqrt(252)) if yr_std > 0 else 0
    print(f"    {year}: N={yr_n:4d}  WR={yr_wr:5.1f}%  Avg={yr_avg:+6.2f}  "
          f"Total={yr_total:+7.0f}  Sharpe={yr_sharpe:+6.3f}")

# By first-hour move size
print(f"\n  By first-hour move size:")
for lo, hi in [(0, 5), (5, 10), (10, 20), (20, 50), (50, 999)]:
    sub = lt[(lt['first_hour_ret'].abs() >= lo) & (lt['first_hour_ret'].abs() < hi)]
    if len(sub) == 0:
        continue
    wr_s = sub['win'].mean() * 100
    avg_s = sub['pnl_pips'].mean()
    n_s = len(sub)
    cont = (sub['direction'] * sub['remain_ret'] > 0).mean() * 100
    print(f"    {lo:3d}-{hi:3d} pips: N={n_s:4d}  Cont={cont:5.1f}%  "
          f"WR={wr_s:5.1f}%  Avg={avg_s:+6.2f}pips")

# By Asian range (volatility regime)
print(f"\n  By Asian session range (volatility proxy):")
for lo, hi in [(0, 20), (20, 40), (40, 60), (60, 100), (100, 999)]:
    sub = lt[(lt['asian_range'] >= lo) & (lt['asian_range'] < hi)]
    if len(sub) == 0:
        continue
    wr_s = sub['win'].mean() * 100
    avg_s = sub['pnl_pips'].mean()
    n_s = len(sub)
    print(f"    {lo:3d}-{hi:3d} pips: N={n_s:4d}  WR={wr_s:5.1f}%  Avg={avg_s:+6.2f}pips")

# Long vs short
print(f"\n  Direction breakdown:")
for d, label in [(1, 'Long (first hour up)'), (-1, 'Short (first hour down)')]:
    sub = lt[lt['direction'] == d]
    n_d = len(sub)
    wr_d = sub['win'].mean() * 100
    avg_d = sub['pnl_pips'].mean()
    cont = (sub['direction'] * sub['remain_ret'] > 0).mean() * 100
    print(f"    {label}: N={n_d:4d}  Cont={cont:5.1f}%  WR={wr_d:5.1f}%  Avg={avg_d:+6.2f}pips")


# ═══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 80)
print("  INTRADAY MOMENTUM — SUMMARY")
print("=" * 80)
print("""
  Key questions answered:
  1. Does the first hour of London predict the rest of the session?
  2. Does the first 30 min of NY predict the rest?
  3. Do intraday channel breakouts on 1H bars work?
  4. Does hourly trend following (enter on N-bar break, exit on M-bar break) work?
  5. Does 1H+4H alignment (momentum cascade) improve results?
  6. What filters (move size, volatility, day of week) help?

  If any approach shows Sharpe > 0.5 with consistent year-by-year results,
  it's worth further investigation. Sharpe > 1.0 would be very promising.

  Comparison:
  - Daily trend following on FX: Sharpe < 0 (dead)
  - ORB on XAUUSD: Sharpe 1.14 (passthrough), 1.77 (LLM)
  - Pairs trading: collapsed intraday
  - London gap fill: ~0 after costs
""")

print("\n=== BACKTEST COMPLETE ===")

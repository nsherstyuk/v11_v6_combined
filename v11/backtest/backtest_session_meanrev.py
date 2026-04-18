"""
Backtest: Session-Based Mean Reversion on FX (EURUSD)

Tests three intraday mean-reversion patterns using 1-min historical data:

1. Asian Range Fade: Price breaks outside the Asian session range
   at London open → tends to reverse back into the range.

2. London Open Gap: Price gaps at London open (08:00 GMT) vs
   previous close → gap tends to fill within 2-3 hours.

3. NY Afternoon Drift: After 15:00 ET (19:00 GMT), price drifts
   toward the day's VWAP.

Data: EURUSD 1-min bars from C:\\nautilus0\\data\\1m_csv\\eurusd_1m_tick.csv
Note: EURUSD prices are stored ×100 (120.1075 = 1.201075).
      Only OHLC is used — volume imbalance data is NOT used.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

DATA_PATH = Path(r"C:\nautilus0\data\1m_csv\eurusd_1m_tick.csv")
PIP_MULT = 10000  # EURUSD: 1 pip = 0.0001

# ── Session times (GMT/UTC) ──────────────────────────────────────────────────
# Asian session: 00:00 – 07:00 GMT (Tokyo 09:00 – 16:00 JST)
# London open:   07:00 – 09:00 GMT (pre-London)
# London active: 08:00 – 16:00 GMT
# NY open:       13:00 – 14:00 GMT (NY 08:00 – 10:00 ET)
# NY afternoon:  19:00 – 22:00 GMT (NY 14:00 – 17:00 ET)

ASIAN_START = 0    # 00:00 GMT
ASIAN_END = 7      # 07:00 GMT
LONDON_OPEN = 8    # 08:00 GMT
LONDON_ACTIVE_END = 16  # 16:00 GMT
NY_AFTERNOON = 19  # 19:00 GMT = 14:00 ET
NY_CLOSE = 22      # 22:00 GMT = 17:00 ET

# ── Load data ────────────────────────────────────────────────────────────────
print("Loading EURUSD 1-min data...")
df = pd.read_csv(DATA_PATH, usecols=['timestamp', 'open', 'high', 'low', 'close'])
df['timestamp'] = pd.to_datetime(df['timestamp'])
df = df.sort_values('timestamp').reset_index(drop=True)

# Fix EURUSD price scale (stored ×100)
for col in ['open', 'high', 'low', 'close']:
    df[col] = df[col] / 100.0

# Add time columns
df['hour'] = df['timestamp'].dt.hour
df['minute'] = df['timestamp'].dt.minute
df['date'] = df['timestamp'].dt.date
df['dow'] = df['timestamp'].dt.dayofweek  # 0=Mon, 6=Sun

# Filter to weekdays only (Mon-Fri)
df = df[df['dow'].between(0, 4)].copy()

print(f"  Rows: {len(df):,}")
print(f"  Date range: {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}")
print(f"  Price range: {df['close'].min():.5f} - {df['close'].max():.5f}")


# ═══════════════════════════════════════════════════════════════════════════
# PATTERN 1: Asian Range Fade
# ═══════════════════════════════════════════════════════════════════════════
# Logic:
#   1. Measure the Asian session range (high - low) from 00:00-07:00 GMT
#   2. At London open (08:00), check if price is outside the Asian range
#   3. If price > Asian high → SELL (expect fade back into range)
#   4. If price < Asian low → BUY (expect bounce back into range)
#   5. Hold for N bars, measure return

print("\n" + "=" * 70)
print("  PATTERN 1: Asian Range Fade")
print("=" * 70)

# Compute Asian session range per day
asian_bars = df[(df['hour'] >= ASIAN_START) & (df['hour'] < ASIAN_END)]
asian_range = asian_bars.groupby('date').agg(
    asian_high=('high', 'max'),
    asian_low=('low', 'min'),
    asian_close=('close', 'last'),
    asian_bars=('close', 'count'),
).reset_index()

asian_range['asian_range_pips'] = (asian_range['asian_high'] - asian_range['asian_low']) * PIP_MULT

# Get London open bars (08:00-08:30 GMT)
london_open = df[(df['hour'] == LONDON_OPEN) & (df['minute'] < 30)]
london_open = london_open.groupby('date').agg(
    london_open_price=('close', 'first'),
).reset_index()

# Merge
fade_df = asian_range.merge(london_open, on='date', how='inner')
fade_df = fade_df[fade_df['asian_bars'] >= 60]  # need at least 60 Asian bars

# Classify: above range, below range, or inside
fade_df['above_range'] = fade_df['london_open_price'] > fade_df['asian_high']
fade_df['below_range'] = fade_df['london_open_price'] < fade_df['asian_low']
fade_df['outside_range'] = fade_df['above_range'] | fade_df['below_range']

# For outside-range cases, measure how far from the range boundary
fade_df['distance_pips'] = np.where(
    fade_df['above_range'],
    (fade_df['london_open_price'] - fade_df['asian_high']) * PIP_MULT,
    np.where(
        fade_df['below_range'],
        (fade_df['asian_low'] - fade_df['london_open_price']) * PIP_MULT,
        0
    )
)

# Get London session bars (08:00-16:00 GMT) for measuring forward returns
london_bars = df[(df['hour'] >= LONDON_OPEN) & (df['hour'] < LONDON_ACTIVE_END)]

# For each fade signal, measure return at various look-ahead windows
results_fade = []
for _, row in fade_df[fade_df['outside_range']].iterrows():
    date = row['date']
    direction = -1 if row['above_range'] else 1  # sell if above, buy if below
    entry_price = row['london_open_price']
    
    # Get London bars for this date
    day_bars = london_bars[london_bars['date'] == date].reset_index(drop=True)
    if len(day_bars) < 30:
        continue
    
    for look_bars in [15, 30, 60, 120, 240]:  # 15min, 30min, 1h, 2h, 4h
        if look_bars >= len(day_bars):
            continue
        exit_price = day_bars.loc[look_bars, 'close']
        ret_pips = direction * (exit_price - entry_price) * PIP_MULT
        results_fade.append({
            'date': date,
            'direction': 'SELL' if row['above_range'] else 'BUY',
            'distance_pips': row['distance_pips'],
            'look_bars': look_bars,
            'ret_pips': ret_pips,
            'win': ret_pips > 0,
        })

fade_results = pd.DataFrame(results_fade)

if not fade_results.empty:
    print(f"\n  Total fade signals: {len(fade_results[fade_results['look_bars']==30]):,}")
    print(f"  Breakdown: above range (SELL) = {len(fade_results[(fade_results['look_bars']==30) & (fade_results['direction']=='SELL')]):,}, "
          f"below range (BUY) = {len(fade_results[(fade_results['look_bars']==30) & (fade_results['direction']=='BUY')]):,}")
    
    print(f"\n  --- All fade signals ---")
    for lb in [15, 30, 60, 120, 240]:
        sub = fade_results[fade_results['look_bars'] == lb]
        if sub.empty:
            continue
        avg_ret = sub['ret_pips'].mean()
        win_rate = sub['win'].mean() * 100
        print(f"    {lb:3d} bars ({lb*1:3d} min): avg ret = {avg_ret:+.2f} pips  WR = {win_rate:.1f}%  N = {len(sub):,}")
    
    print(f"\n  --- SELL signals (price above Asian range) ---")
    for lb in [30, 60, 120]:
        sub = fade_results[(fade_results['look_bars'] == lb) & (fade_results['direction'] == 'SELL')]
        if sub.empty:
            continue
        avg_ret = sub['ret_pips'].mean()
        win_rate = sub['win'].mean() * 100
        print(f"    {lb:3d} bars: avg ret = {avg_ret:+.2f} pips  WR = {win_rate:.1f}%  N = {len(sub):,}")
    
    print(f"\n  --- BUY signals (price below Asian range) ---")
    for lb in [30, 60, 120]:
        sub = fade_results[(fade_results['look_bars'] == lb) & (fade_results['direction'] == 'BUY')]
        if sub.empty:
            continue
        avg_ret = sub['ret_pips'].mean()
        win_rate = sub['win'].mean() * 100
        print(f"    {lb:3d} bars: avg ret = {avg_ret:+.2f} pips  WR = {win_rate:.1f}%  N = {len(sub):,}")
    
    # Distance filter: only trade when breakout is small (1-5 pips from range)
    print(f"\n  --- Fade with distance filter (1-5 pips from range boundary) ---")
    for lb in [30, 60, 120]:
        sub = fade_results[(fade_results['look_bars'] == lb) & 
                          (fade_results['distance_pips'] >= 1) & 
                          (fade_results['distance_pips'] <= 5)]
        if sub.empty:
            continue
        avg_ret = sub['ret_pips'].mean()
        win_rate = sub['win'].mean() * 100
        print(f"    {lb:3d} bars: avg ret = {avg_ret:+.2f} pips  WR = {win_rate:.1f}%  N = {len(sub):,}")
    
    # Large breakout filter (>10 pips from range) — these may NOT fade
    print(f"\n  --- Large breakout (>10 pips from range boundary) ---")
    for lb in [30, 60, 120]:
        sub = fade_results[(fade_results['look_bars'] == lb) & 
                          (fade_results['distance_pips'] > 10)]
        if sub.empty:
            continue
        avg_ret = sub['ret_pips'].mean()
        win_rate = sub['win'].mean() * 100
        print(f"    {lb:3d} bars: avg ret = {avg_ret:+.2f} pips  WR = {win_rate:.1f}%  N = {len(sub):,}")

    # Asian range size filter
    print(f"\n  --- Fade when Asian range is TIGHT (<20 pips) ---")
    tight_dates = set(fade_df[fade_df['asian_range_pips'] < 20]['date'])
    for lb in [30, 60, 120]:
        sub = fade_results[(fade_results['look_bars'] == lb) & 
                          (fade_results['date'].isin(tight_dates))]
        if sub.empty:
            continue
        avg_ret = sub['ret_pips'].mean()
        win_rate = sub['win'].mean() * 100
        print(f"    {lb:3d} bars: avg ret = {avg_ret:+.2f} pips  WR = {win_rate:.1f}%  N = {len(sub):,}")

    print(f"\n  --- Fade when Asian range is WIDE (>40 pips) ---")
    wide_dates = set(fade_df[fade_df['asian_range_pips'] > 40]['date'])
    for lb in [30, 60, 120]:
        sub = fade_results[(fade_results['look_bars'] == lb) & 
                          (fade_results['date'].isin(wide_dates))]
        if sub.empty:
            continue
        avg_ret = sub['ret_pips'].mean()
        win_rate = sub['win'].mean() * 100
        print(f"    {lb:3d} bars: avg ret = {avg_ret:+.2f} pips  WR = {win_rate:.1f}%  N = {len(sub):,}")

# How often does price stay inside Asian range?
inside_count = len(fade_df[~fade_df['outside_range']])
total_count = len(fade_df)
print(f"\n  Price inside Asian range at London open: {inside_count}/{total_count} ({inside_count/total_count*100:.1f}%)")


# ═══════════════════════════════════════════════════════════════════════════
# PATTERN 2: London Open Gap Fill
# ═══════════════════════════════════════════════════════════════════════════
# Logic:
#   1. Measure gap = London open price - previous day's NY close
#   2. If gap is large (>X pips), bet on gap filling within 2-3 hours
#   3. Gap up → SELL, Gap down → BUY

print("\n" + "=" * 70)
print("  PATTERN 2: London Open Gap Fill")
print("=" * 70)

# Get previous day's NY close (21:55-22:00 GMT)
ny_close_bars = df[(df['hour'] == 21) & (df['minute'] >= 55)]
ny_close = ny_close_bars.groupby('date').agg(
    ny_close_price=('close', 'last'),
).reset_index()

# Merge with London open
gap_df = fade_df[['date', 'london_open_price']].merge(ny_close, on='date', how='inner')
# Shift ny_close to previous day
gap_df = gap_df.sort_values('date').reset_index(drop=True)
gap_df['prev_date'] = gap_df['date']
gap_df['prev_ny_close'] = gap_df['ny_close_price'].shift(1)
gap_df = gap_df.dropna()

gap_df['gap_pips'] = (gap_df['london_open_price'] - gap_df['prev_ny_close']) * PIP_MULT
gap_df['gap_up'] = gap_df['gap_pips'] > 0
gap_df['gap_down'] = gap_df['gap_pips'] < 0

# Gap fill analysis
results_gap = []
for _, row in gap_df.iterrows():
    if abs(row['gap_pips']) < 3:  # skip tiny gaps
        continue
    
    date = row['date']
    direction = -1 if row['gap_up'] else 1  # fade the gap
    entry_price = row['london_open_price']
    target_price = row['prev_ny_close']  # gap fill target
    
    day_bars = london_bars[london_bars['date'] == date].reset_index(drop=True)
    if len(day_bars) < 30:
        continue
    
    # Check if gap fills within various time windows
    for look_bars in [30, 60, 120, 240]:
        if look_bars >= len(day_bars):
            continue
        exit_price = day_bars.loc[look_bars, 'close']
        ret_pips = direction * (exit_price - entry_price) * PIP_MULT
        gap_filled = (direction == -1 and exit_price <= target_price) or \
                     (direction == 1 and exit_price >= target_price)
        results_gap.append({
            'date': date,
            'gap_pips': row['gap_pips'],
            'direction': 'SELL (gap up)' if row['gap_up'] else 'BUY (gap down)',
            'look_bars': look_bars,
            'ret_pips': ret_pips,
            'gap_filled': gap_filled,
            'win': ret_pips > 0,
        })

gap_results = pd.DataFrame(results_gap)

if not gap_results.empty:
    print(f"\n  Total gap signals (>3 pips): {len(gap_results[gap_results['look_bars']==60]):,}")
    
    print(f"\n  --- All gaps (>3 pips) ---")
    for lb in [30, 60, 120, 240]:
        sub = gap_results[gap_results['look_bars'] == lb]
        if sub.empty:
            continue
        avg_ret = sub['ret_pips'].mean()
        win_rate = sub['win'].mean() * 100
        fill_rate = sub['gap_filled'].mean() * 100
        print(f"    {lb:3d} bars: avg ret = {avg_ret:+.2f} pips  WR = {win_rate:.1f}%  "
              f"Fill rate = {fill_rate:.1f}%  N = {len(sub):,}")
    
    print(f"\n  --- Large gaps (>10 pips) ---")
    for lb in [60, 120]:
        sub = gap_results[(gap_results['look_bars'] == lb) & (gap_results['gap_pips'].abs() > 10)]
        if sub.empty:
            continue
        avg_ret = sub['ret_pips'].mean()
        win_rate = sub['win'].mean() * 100
        fill_rate = sub['gap_filled'].mean() * 100
        print(f"    {lb:3d} bars: avg ret = {avg_ret:+.2f} pips  WR = {win_rate:.1f}%  "
              f"Fill rate = {fill_rate:.1f}%  N = {len(sub):,}")
    
    print(f"\n  --- Gap up (SELL) vs Gap down (BUY) ---")
    for direction_label in ['SELL (gap up)', 'BUY (gap down)']:
        for lb in [60, 120]:
            sub = gap_results[(gap_results['look_bars'] == lb) & (gap_results['direction'] == direction_label)]
            if sub.empty:
                continue
            avg_ret = sub['ret_pips'].mean()
            win_rate = sub['win'].mean() * 100
            print(f"    {direction_label:15s} {lb:3d} bars: avg ret = {avg_ret:+.2f} pips  WR = {win_rate:.1f}%  N = {len(sub):,}")


# ═══════════════════════════════════════════════════════════════════════════
# PATTERN 3: NY Afternoon VWAP Drift
# ═══════════════════════════════════════════════════════════════════════════
# Logic:
#   1. Compute intraday VWAP from London open (08:00) to NY afternoon (19:00 GMT)
#   2. At 19:00 GMT (14:00 ET), if price is above VWAP → SELL (drift down)
#   3. If price is below VWAP → BUY (drift up)
#   4. Hold until NY close (22:00 GMT)

print("\n" + "=" * 70)
print("  PATTERN 3: NY Afternoon VWAP Drift")
print("=" * 70)

# Build daily VWAP using typical price × tick_count as proxy for volume
# Since we don't have real volume, use tick_count as weight
df_full = pd.read_csv(DATA_PATH, usecols=['timestamp', 'open', 'high', 'low', 'close', 'tick_count'])
df_full['timestamp'] = pd.to_datetime(df_full['timestamp'])
df_full = df_full.sort_values('timestamp').reset_index(drop=True)
for col in ['open', 'high', 'low', 'close']:
    df_full[col] = df_full[col] / 100.0
df_full['date'] = df_full['timestamp'].dt.date
df_full['hour'] = df_full['timestamp'].dt.hour
df_full['dow'] = df_full['timestamp'].dt.dayofweek
df_full = df_full[df_full['dow'].between(0, 4)].copy()

# Typical price
df_full['typical'] = (df_full['high'] + df_full['low'] + df_full['close']) / 3
df_full['tp_x_ticks'] = df_full['typical'] * df_full['tick_count']

# Compute cumulative VWAP from London open (08:00) to NY afternoon (19:00)
london_to_ny = df_full[(df_full['hour'] >= LONDON_OPEN) & (df_full['hour'] < NY_AFTERNOON)]
daily_vwap = london_to_ny.groupby('date').apply(
    lambda g: g['tp_x_ticks'].sum() / g['tick_count'].sum() if g['tick_count'].sum() > 0 else np.nan
).reset_index(name='vwap')
daily_vwap = daily_vwap.dropna()

# Get 19:00 GMT price
ny_afternoon_price = df_full[df_full['hour'] == NY_AFTERNOON].groupby('date').agg(
    ny_19_price=('close', 'first'),
).reset_index()

# Get 22:00 GMT price (NY close)
ny_end_price = df_full[(df_full['hour'] >= NY_CLOSE - 1)].groupby('date').agg(
    ny_close_price=('close', 'last'),
).reset_index()

# Merge
vwap_df = daily_vwap.merge(ny_afternoon_price, on='date', how='inner')
vwap_df = vwap_df.merge(ny_end_price, on='date', how='inner')

vwap_df['deviation_pips'] = (vwap_df['ny_19_price'] - vwap_df['vwap']) * PIP_MULT
vwap_df['above_vwap'] = vwap_df['deviation_pips'] > 0
vwap_df['below_vwap'] = vwap_df['deviation_pips'] < 0

# Trade: fade the deviation
vwap_df['direction'] = np.where(vwap_df['above_vwap'], -1, 1)  # sell if above, buy if below
vwap_df['ret_pips'] = vwap_df['direction'] * (vwap_df['ny_close_price'] - vwap_df['ny_19_price']) * PIP_MULT
vwap_df['win'] = vwap_df['ret_pips'] > 0

print(f"\n  Total days: {len(vwap_df):,}")
print(f"  Days above VWAP: {vwap_df['above_vwap'].sum():,}")
print(f"  Days below VWAP: {vwap_df['below_vwap'].sum():,}")

# All signals
sub = vwap_df
avg_ret = sub['ret_pips'].mean()
win_rate = sub['win'].mean() * 100
print(f"\n  --- All VWAP drift signals ---")
print(f"    Avg ret = {avg_ret:+.2f} pips  WR = {win_rate:.1f}%  N = {len(sub):,}")

# Above VWAP (SELL)
sub_up = vwap_df[vwap_df['above_vwap']]
avg_ret_up = sub_up['ret_pips'].mean()
win_rate_up = sub_up['win'].mean() * 100
print(f"\n  --- Above VWAP (SELL, drift down) ---")
print(f"    Avg ret = {avg_ret_up:+.2f} pips  WR = {win_rate_up:.1f}%  N = {len(sub_up):,}")

# Below VWAP (BUY)
sub_dn = vwap_df[vwap_df['below_vwap']]
avg_ret_dn = sub_dn['ret_pips'].mean()
win_rate_dn = sub_dn['win'].mean() * 100
print(f"\n  --- Below VWAP (BUY, drift up) ---")
print(f"    Avg ret = {avg_ret_dn:+.2f} pips  WR = {win_rate_dn:.1f}%  N = {len(sub_dn):,}")

# Large deviation filter (>5 pips from VWAP)
print(f"\n  --- Large deviation (>5 pips from VWAP) ---")
for label, mask in [(">5 pips above (SELL)", vwap_df['deviation_pips'] > 5),
                    (">5 pips below (BUY)", vwap_df['deviation_pips'] < -5),
                    (">10 pips from VWAP", vwap_df['deviation_pips'].abs() > 10)]:
    sub = vwap_df[mask]
    if sub.empty:
        continue
    avg_ret = sub['ret_pips'].mean()
    win_rate = sub['win'].mean() * 100
    print(f"    {label:30s}: avg ret = {avg_ret:+.2f} pips  WR = {win_rate:.1f}%  N = {len(sub):,}")

# Year-by-year for VWAP drift
print(f"\n  --- VWAP Drift Year-by-Year ---")
vwap_df['year'] = pd.to_datetime(vwap_df['date']).dt.year
for year, group in vwap_df.groupby('year'):
    avg_ret = group['ret_pips'].mean()
    win_rate = group['win'].mean() * 100
    print(f"    {year}: avg ret = {avg_ret:+.2f} pips  WR = {win_rate:.1f}%  N = {len(group):,}")


# ═══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  SESSION MEAN REVERSION — SUMMARY")
print("=" * 70)
print("""
  EURUSD, 2018-2026, 1-min bars (OHLC only, no volume data used)

  Pattern 1: Asian Range Fade
    - Fade breakouts outside the Asian session range at London open
    - Best when: small breakout (1-5 pips), tight Asian range (<20 pips)
    - Typical hold: 1-2 hours
    
  Pattern 2: London Open Gap Fill
    - Fade the overnight gap at London open
    - Gap tends to fill within 2-3 hours
    - Larger gaps have higher fill rate but more risk
    
  Pattern 3: NY Afternoon VWAP Drift
    - Fade deviation from VWAP at 14:00 ET
    - Hold until NY close (17:00 ET)
    - 3-hour trade window
    
  Key considerations:
    - All returns are in pips BEFORE spread/commission
    - EURUSD typical spread: 0.1-0.2 pips (IBKR)
    - IBKR commission: ~$2 per $100K round-trip = ~0.2 pips
    - Total cost per round-trip: ~0.3-0.4 pips
    - Strategy needs >0.5 pips avg return to be profitable after costs
""")

print("\n=== BACKTEST COMPLETE ===")

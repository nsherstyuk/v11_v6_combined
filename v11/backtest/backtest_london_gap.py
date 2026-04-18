"""
London Open Gap Fade — Multi-Pair Backtest

Strategy: Fade the overnight gap at London open (08:00 GMT).
- Gap up → SELL (expect gap to fill within 2-3 hours)
- Gap down → BUY (expect gap to fill)

Tests gap fade on 7 FX pairs with 1-min data.
Also tests: gap size filters, hold duration, gap-up vs gap-down,
session quality, and combined portfolio.

Data: C:\\nautilus0\\data\\1m_csv\\*_1m_tick.csv
"""
import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

DATA_DIR = Path(r"C:\nautilus0\data\1m_csv")
PAIRS = ['audusd', 'eurusd', 'gbpusd', 'nzdusd', 'usdcad', 'usdchf', 'usdjpy']

# Session times (GMT)
LONDON_OPEN_HOUR = 8
NY_CLOSE_HOUR = 21  # previous day's NY close window
GAP_FILL_HOURS = [1, 2, 3, 4, 6]  # look-ahead windows

# Cost model
COST_PIPS = {
    'eurusd': 0.15, 'gbpusd': 0.15, 'usdchf': 0.15, 'usdjpy': 0.20,
    'audusd': 0.20, 'nzdusd': 0.25, 'usdcad': 0.20,
}

def pip_size(pair):
    return 0.01 if 'jpy' in pair else 0.0001

def load_pair(pair):
    df = pd.read_csv(DATA_DIR / f"{pair}_1m_tick.csv",
                     usecols=['timestamp', 'open', 'high', 'low', 'close'])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp').reset_index(drop=True)
    # Fix EURUSD ×100 scale
    if pair == 'eurusd':
        for c in ['open','high','low','close']:
            df[c] = df[c] / 100.0
    df['date'] = df['timestamp'].dt.date
    df['hour'] = df['timestamp'].dt.hour
    df['minute'] = df['timestamp'].dt.minute
    df['dow'] = df['timestamp'].dt.dayofweek
    df = df[df['dow'].between(0,4)].copy()
    return df

# ── Load all pairs ───────────────────────────────────────────────────────────
print("Loading 1-min data...")
data = {}
for pair in PAIRS:
    df = load_pair(pair)
    data[pair] = df
    print(f"  {pair}: {len(df):,} bars")

# ── Compute gaps and fade results per pair ───────────────────────────────────
print("\nComputing London gap fade signals...")

all_gap_signals = []

for pair in PAIRS:
    df = data[pair]
    pip = pip_size(pair)
    cost = COST_PIPS.get(pair, 0.2)

    # Get previous day's NY close (21:55-22:00 GMT)
    ny_close = df[(df['hour'] == NY_CLOSE_HOUR) & (df['minute'] >= 55)]
    ny_close_daily = ny_close.groupby('date').agg(ny_close=('close', 'last')).reset_index()

    # Get London open price (08:00-08:05 GMT)
    london_open = df[(df['hour'] == LONDON_OPEN_HOUR) & (df['minute'] < 5)]
    london_open_daily = london_open.groupby('date').agg(
        london_open_price=('close', 'first'),
        london_open_high=('high', 'max'),
        london_open_low=('low', 'min'),
    ).reset_index()

    # Merge and compute gap
    gap_df = ny_close_daily.rename(columns={'date': 'prev_date'})
    gap_df['prev_date_shifted'] = gap_df['prev_date']
    gap_df = gap_df.rename(columns={'ny_close': 'prev_ny_close'})

    # We need to match London open date with PREVIOUS trading day's NY close
    # London open on Tuesday should use Monday's NY close
    london_open_daily = london_open_daily.sort_values('date').reset_index(drop=True)
    ny_close_daily = ny_close_daily.sort_values('date').reset_index(drop=True)
    ny_close_daily['next_date'] = ny_close_daily['date'].shift(-1)

    merged = london_open_daily.merge(
        ny_close_daily[['next_date', 'ny_close']].rename(columns={'next_date': 'date', 'ny_close': 'prev_ny_close'}),
        on='date', how='left'
    )
    merged = merged.dropna().reset_index(drop=True)

    # Gap in pips
    merged['gap_pips'] = (merged['london_open_price'] - merged['prev_ny_close']) / pip
    merged['gap_up'] = merged['gap_pips'] > 0
    merged['gap_down'] = merged['gap_pips'] < 0
    merged['pair'] = pair
    merged['pip'] = pip
    merged['cost'] = cost

    # For each gap signal, measure forward returns at various hold periods
    for _, row in merged.iterrows():
        date = row['date']
        gap_pips = row['gap_pips']
        if abs(gap_pips) < 2:  # skip tiny gaps
            continue

        direction = -1 if row['gap_up'] else 1  # fade the gap
        entry_price = row['london_open_price']

        # Get London session bars for this date
        day_bars = df[(df['date'] == date) & (df['hour'] >= LONDON_OPEN_HOUR)].reset_index(drop=True)

        for hold_hours in GAP_FILL_HOURS:
            hold_bars = hold_hours * 60
            if hold_bars >= len(day_bars):
                continue
            exit_price = day_bars.loc[hold_bars, 'close']
            ret_pips = direction * (exit_price - entry_price) / pip - cost
            gap_filled = (direction == -1 and exit_price <= row['prev_ny_close']) or \
                         (direction == 1 and exit_price >= row['prev_ny_close'])

            all_gap_signals.append({
                'pair': pair,
                'date': date,
                'gap_pips': gap_pips,
                'direction': 'SELL (gap up)' if row['gap_up'] else 'BUY (gap down)',
                'hold_hours': hold_hours,
                'ret_pips': ret_pips,
                'gap_filled': gap_filled,
                'win': ret_pips > 0,
                'abs_gap': abs(gap_pips),
            })

gaps = pd.DataFrame(all_gap_signals)

# ── Results by pair ───────────────────────────────────────────────────────────
print("\n" + "=" * 90)
print("  LONDON GAP FADE — RESULTS BY PAIR (2-hour hold)")
print("=" * 90)

hold = 2  # 2 hours
sub = gaps[gaps['hold_hours'] == hold]

print(f"\n  {'Pair':8s} {'N':>5s} {'WR%':>6s} {'Avg Ret':>9s} {'Total':>9s} "
      f"{'Fill%':>7s} {'GapUp N':>8s} {'GapUp WR':>9s} {'GapDn N':>8s} {'GapDn WR':>9s}")
print("-" * 90)

pair_results = []
for pair in PAIRS:
    ps = sub[sub['pair'] == pair]
    if ps.empty: continue
    n = len(ps)
    wr = ps['win'].mean() * 100
    avg = ps['ret_pips'].mean()
    tot = ps['ret_pips'].sum()
    fill = ps['gap_filled'].mean() * 100
    
    gu = ps[ps['direction'] == 'SELL (gap up)']
    gd = ps[ps['direction'] == 'BUY (gap down)']
    
    gu_wr = gu['win'].mean() * 100 if len(gu) > 0 else 0
    gd_wr = gd['win'].mean() * 100 if len(gd) > 0 else 0
    
    print(f"  {pair:8s} {n:5d} {wr:6.1f} {avg:+9.2f} {tot:+9.1f} "
          f"{fill:7.1f} {len(gu):8d} {gu_wr:9.1f} {len(gd):8d} {gd_wr:9.1f}")
    
    pair_results.append({
        'pair': pair, 'n': n, 'wr': wr, 'avg': avg, 'total': tot,
        'fill_rate': fill, 'gu_n': len(gu), 'gu_wr': gu_wr,
        'gd_n': len(gd), 'gd_wr': gd_wr,
    })

# ── Gap-up vs gap-down across all pairs ─────────────────────────────────────
print("\n\n  --- Gap-Up Fade (SELL) vs Gap-Down Fade (BUY) — All Pairs ---")
for hold in GAP_FILL_HOURS:
    sub = gaps[gaps['hold_hours'] == hold]
    gu = sub[sub['direction'] == 'SELL (gap up)']
    gd = sub[sub['direction'] == 'BUY (gap down)']
    
    print(f"\n  Hold: {hold}h")
    if len(gu) > 0:
        print(f"    Gap-up SELL:  N={len(gu):4d}  WR={gu['win'].mean()*100:.1f}%  "
              f"Avg={gu['ret_pips'].mean():+.2f}pips  Fill={gu['gap_filled'].mean()*100:.1f}%")
    if len(gd) > 0:
        print(f"    Gap-down BUY: N={len(gd):4d}  WR={gd['win'].mean()*100:.1f}%  "
              f"Avg={gd['ret_pips'].mean():+.2f}pips  Fill={gd['gap_filled'].mean()*100:.1f}%")

# ── Gap size filter ──────────────────────────────────────────────────────────
print("\n\n  --- Gap Size Filter (all pairs, 2h hold) ---")
sub = gaps[gaps['hold_hours'] == 2]
for min_gap in [3, 5, 8, 12, 15]:
    filtered = sub[sub['abs_gap'] >= min_gap]
    if filtered.empty: continue
    gu = filtered[filtered['direction'] == 'SELL (gap up)']
    gd = filtered[filtered['direction'] == 'BUY (gap down)']
    print(f"\n  Gap >= {min_gap} pips:")
    print(f"    All:   N={len(filtered):4d}  WR={filtered['win'].mean()*100:.1f}%  "
          f"Avg={filtered['ret_pips'].mean():+.2f}pips  Fill={filtered['gap_filled'].mean()*100:.1f}%")
    if len(gu) > 0:
        print(f"    GapUp: N={len(gu):4d}  WR={gu['win'].mean()*100:.1f}%  "
              f"Avg={gu['ret_pips'].mean():+.2f}pips  Fill={gu['gap_filled'].mean()*100:.1f}%")
    if len(gd) > 0:
        print(f"    GapDn: N={len(gd):4d}  WR={gd['win'].mean()*100:.1f}%  "
              f"Avg={gd['ret_pips'].mean():+.2f}pips  Fill={gd['gap_filled'].mean()*100:.1f}%")

# ── Best filter: gap-up only, >= 5 pips ──────────────────────────────────────
print("\n\n  --- BEST FILTER: Gap-Up Only, >= 5 pips ---")
for hold in GAP_FILL_HOURS:
    filtered = gaps[(gaps['hold_hours'] == hold) &
                    (gaps['direction'] == 'SELL (gap up)') &
                    (gaps['abs_gap'] >= 5)]
    if filtered.empty: continue
    print(f"  Hold {hold}h: N={len(filtered):4d}  WR={filtered['win'].mean()*100:.1f}%  "
          f"Avg={filtered['ret_pips'].mean():+.2f}pips  Fill={filtered['gap_filled'].mean()*100:.1f}%")

# ── Year-by-year for gap-up fade ─────────────────────────────────────────────
print("\n\n  --- Gap-Up Fade (>= 5 pips, 2h hold) — Year-by-Year ---")
filtered = gaps[(gaps['hold_hours'] == 2) &
                (gaps['direction'] == 'SELL (gap up)') &
                (gaps['abs_gap'] >= 5)]
filtered['year'] = pd.to_datetime(filtered['date']).dt.year
for year, group in filtered.groupby('year'):
    yr_ret = group['ret_pips'].sum()
    yr_wr = group['win'].mean() * 100
    yr_n = len(group)
    yr_fill = group['gap_filled'].mean() * 100
    print(f"    {year}: ret={yr_ret:+.1f}pips  WR={yr_wr:.0f}%  Fill={yr_fill:.0f}%  N={yr_n}")

# ── By pair for gap-up only ──────────────────────────────────────────────────
print("\n\n  --- Gap-Up Fade (>= 5 pips, 2h hold) — By Pair ---")
filtered = gaps[(gaps['hold_hours'] == 2) &
                (gaps['direction'] == 'SELL (gap up)') &
                (gaps['abs_gap'] >= 5)]
for pair in PAIRS:
    ps = filtered[filtered['pair'] == pair]
    if ps.empty: continue
    print(f"    {pair:8s}: N={len(ps):3d}  WR={ps['win'].mean()*100:.1f}%  "
          f"Avg={ps['ret_pips'].mean():+.2f}pips  Fill={ps['gap_filled'].mean()*100:.1f}%")

# ── Portfolio simulation ─────────────────────────────────────────────────────
print("\n\n" + "=" * 90)
print("  PORTFOLIO SIMULATION: Gap-Up Fade (>= 5 pips, 2h hold, 7 pairs)")
print("=" * 90)

port_signals = gaps[(gaps['hold_hours'] == 2) &
                    (gaps['direction'] == 'SELL (gap up)') &
                    (gaps['abs_gap'] >= 5)].copy()
port_signals['date'] = pd.to_datetime(port_signals['date'])

# Aggregate daily P&L
daily_pnl = port_signals.groupby('date')['ret_pips'].sum().reset_index()
daily_pnl = daily_pnl.set_index('date')

total_days = len(daily_pnl)
total_ret = daily_pnl['ret_pips'].sum()
avg_daily = daily_pnl['ret_pips'].mean()
ann_ret = avg_daily * 252 if total_days > 0 else 0
ann_vol = daily_pnl['ret_pips'].std() * np.sqrt(252) if total_days > 0 else 1
sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
cum = daily_pnl['ret_pips'].cumsum()
dd = (cum - cum.cummax()).min()
win_days = (daily_pnl['ret_pips'] > 0).mean() * 100

# Trades per day
trades_per_day = port_signals.groupby('date').size()

print(f"\n  Total signals: {len(port_signals)}")
print(f"  Trading days: {total_days}")
print(f"  Signals/day: {len(port_signals)/total_days:.1f}" if total_days > 0 else "")
print(f"  Win days: {win_days:.1f}%")
print(f"  Total return: {total_ret:+.1f} pips")
print(f"  Avg daily: {avg_daily:+.2f} pips")
print(f"  Ann return: {ann_ret:+.1f} pips/year")
print(f"  Ann vol: {ann_vol:.1f} pips/year")
print(f"  Sharpe: {sharpe:+.3f}")
print(f"  Max DD: {dd:.1f} pips")
print(f"  Avg trades/day: {trades_per_day.mean():.1f}")

# Convert to dollar P&L (assuming $100K per position)
print(f"\n  Dollar P&L (assuming $100K per position, 7 pairs):")
# 1 pip on $100K = $10 for non-JPY pairs, $100K * 0.01 = $1000 for JPY
# Actually for FX: 1 pip = notional * pip_size
# EURUSD: 1 pip = $100K * 0.0001 = $10
# USDJPY: 1 pip = $100K * 0.01 / 150 = $6.67 (approx)
dollar_per_pip = 10  # approximate for most pairs at $100K notional
ann_dollar = ann_ret * dollar_per_pip
print(f"    Ann P&L: ~${ann_dollar:+,.0f} ({ann_ret:+.0f} pips × ${dollar_per_pip}/pip)")
print(f"    On $50K account: {ann_dollar/50000*100:+.1f}% return")

# ── Day-of-week analysis ────────────────────────────────────────────────────
print("\n\n  --- Day-of-Week Analysis (gap-up >= 5 pips, 2h hold) ---")
port_signals['dow'] = port_signals['date'].dt.dayofweek
dow_names = {0:'Mon',1:'Tue',2:'Wed',3:'Thu',4:'Fri'}
for dow, group in port_signals.groupby('dow'):
    name = dow_names.get(dow, str(dow))
    print(f"    {name}: N={len(group):3d}  WR={group['win'].mean()*100:.1f}%  "
          f"Avg={group['ret_pips'].mean():+.2f}pips")

# ── Combined: gap-up fade + gap-down fade with filters ──────────────────────
print("\n\n" + "=" * 90)
print("  COMBINED: Gap-Up Fade (>=5pip) + Gap-Down Fade (>=8pip), 2h hold")
print("=" * 90)

combined = pd.concat([
    gaps[(gaps['hold_hours']==2) & (gaps['direction']=='SELL (gap up)') & (gaps['abs_gap']>=5)],
    gaps[(gaps['hold_hours']==2) & (gaps['direction']=='BUY (gap down)') & (gaps['abs_gap']>=8)],
])
combined['date'] = pd.to_datetime(combined['date'])
daily_comb = combined.groupby('date')['ret_pips'].sum().reset_index().set_index('date')

if len(daily_comb) > 0:
    tot = daily_comb['ret_pips'].sum()
    avg = daily_comb['ret_pips'].mean()
    ann = avg * 252
    vol = daily_comb['ret_pips'].std() * np.sqrt(252)
    sh = ann / vol if vol > 0 else 0
    cum = daily_comb['ret_pips'].cumsum()
    dd = (cum - cum.cummax()).min()
    wr = (daily_comb['ret_pips'] > 0).mean() * 100
    
    print(f"\n  Total signals: {len(combined)}")
    print(f"  Gap-up signals: {len(combined[combined['direction']=='SELL (gap up)'])}")
    print(f"  Gap-down signals: {len(combined[combined['direction']=='BUY (gap down)'])}")
    print(f"  Trading days: {len(daily_comb)}")
    print(f"  Total return: {tot:+.1f} pips")
    print(f"  Ann return: {ann:+.1f} pips/year")
    print(f"  Sharpe: {sh:+.3f}")
    print(f"  Max DD: {dd:.1f} pips")
    print(f"  Win days: {wr:.1f}%")
    print(f"  Ann $ P&L: ~${ann*10:+,.0f} (at $100K/position)")

print("\n=== BACKTEST COMPLETE ===")

"""
Daily Trend Following Backtest — Channel Breakout on XAUUSD & FX

Strategy: Buy when price breaks above N-day high, sell when it breaks
below N-day low. ATR-based stops. Ride trends until they reverse.

Why this might work when mean-reversion doesn't:
- Daily bar ranges are 50-100x transaction cost (favorable cost structure)
- Asymmetric payoffs: small frequent losses, large infrequent wins
- Well-documented edge in futures/commodities research
- XAUUSD trends strongly (gold is a trending market)

Tests:
1. Single-channel breakout (10d, 20d, 50d, 200d)
2. Dual-channel (fast entry, slow exit)
3. ATR stop vs fixed stop
4. With/without pyramiding (add to winners)
5. Multi-pair comparison
6. Year-by-year breakdown
7. Comparison with ORB
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

DATA_DIR = Path(r"C:\nautilus0\data\1m_csv")


def load_daily(symbol: str, scale: float = 1.0) -> pd.DataFrame:
    """Load 1-min data and aggregate to daily bars."""
    fname = f"{symbol}_1m_tick.csv"
    df = pd.read_csv(DATA_DIR / fname)
    if 'Unnamed: 0' in df.columns:
        df = df.drop(columns=['Unnamed: 0'])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp').reset_index(drop=True)
    for col in ['open', 'high', 'low', 'close']:
        df[col] = df[col] / scale

    # Aggregate to daily
    daily = df.groupby(df['timestamp'].dt.date).agg(
        open=('open', 'first'),
        high=('high', 'max'),
        low=('low', 'min'),
        close=('close', 'last'),
        tick_count=('tick_count', 'sum'),
        buy_volume=('buy_volume', 'sum'),
        sell_volume=('sell_volume', 'sum'),
    ).reset_index()
    daily.rename(columns={'timestamp': 'date'}, inplace=True)
    daily['date'] = pd.to_datetime(daily['date'])
    daily['total_volume'] = daily['buy_volume'] + daily['sell_volume']
    daily['buy_ratio'] = daily['buy_volume'] / daily['total_volume'].replace(0, 1)
    return daily


def compute_indicators(df: pd.DataFrame, atr_period: int = 20) -> pd.DataFrame:
    """Add trend-following indicators."""
    df = df.copy()
    
    # ATR (Average True Range)
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        )
    )
    df['atr'] = df['tr'].rolling(atr_period).mean()
    df['atr_pct'] = df['atr'] / df['close'] * 100  # ATR as % of price
    
    # Channel highs/lows
    for n in [5, 10, 20, 50, 100, 200]:
        df[f'high_{n}d'] = df['high'].rolling(n).max().shift(1)  # exclude today
        df[f'low_{n}d'] = df['low'].rolling(n).min().shift(1)
    
    # Moving averages
    for n in [10, 20, 50, 100, 200]:
        df[f'sma_{n}'] = df['close'].rolling(n).mean()
    
    # ADX (trend strength proxy)
    df['plus_dm'] = np.where(
        (df['high'] - df['high'].shift(1)) > (df['low'].shift(1) - df['low']),
        np.maximum(df['high'] - df['high'].shift(1), 0), 0)
    df['minus_dm'] = np.where(
        (df['low'].shift(1) - df['low']) > (df['high'] - df['high'].shift(1)),
        np.maximum(df['low'].shift(1) - df['low'], 0), 0)
    
    atr_smooth = df['tr'].rolling(14).mean()
    plus_di = 100 * df['plus_dm'].rolling(14).mean() / atr_smooth
    minus_di = 100 * df['minus_dm'].rolling(14).mean() / atr_smooth
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 1)
    df['adx'] = dx.rolling(14).mean()
    
    return df


def backtest_channel_breakout(df: pd.DataFrame, entry_period: int = 20,
                               exit_period: int = 10,
                               atr_sl_mult: float = 2.0,
                               atr_tp_mult: float = 0,  # 0 = no TP, ride trend
                               cost: float = 0.30,
                               risk_per_trade: float = 0.01,
                               adx_filter: float = 0,  # 0 = no filter
                               pyramiding: int = 1,  # max entries per trend
                               ) -> pd.DataFrame:
    """
    Channel breakout trend following.
    
    Entry: Buy when close > N-day high, Sell when close < N-day low
    Exit: Reverse on opposite channel break, or ATR stop, or time stop
    Stop: ATR * multiplier from entry (or trailing)
    
    Args:
        entry_period: Lookback for channel breakout entry
        exit_period: Lookback for channel breakout exit (shorter = more responsive)
        atr_sl_mult: Stop loss = ATR * this
        atr_tp_mult: Take profit = ATR * this (0 = no TP, ride the trend)
        cost: Round-trip transaction cost in price units
        risk_per_trade: Fraction of equity risked per trade
        adx_filter: Minimum ADX to enter (0 = disabled)
        pyramiding: Max additional entries in same direction
    """
    df = compute_indicators(df)
    
    high_col = f'high_{entry_period}d'
    low_col = f'low_{entry_period}d'
    exit_high_col = f'high_{exit_period}d'
    exit_low_col = f'low_{exit_period}d'
    
    trades = []
    position = 0  # 0=flat, 1=long, -1=short
    entry_price = 0
    sl_price = 0
    tp_price = 0
    entry_date = None
    pyramid_count = 0
    avg_entry = 0
    total_size = 0
    
    equity = 10000  # starting equity for position sizing
    peak_equity = 10000
    
    for i in range(max(entry_period, 200) + 1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        
        if pd.isna(row[high_col]) or pd.isna(row[low_col]) or pd.isna(row['atr']):
            continue
        
        # ── Check exits first ──
        if position != 0:
            # ATR stop loss
            if position == 1 and row['low'] <= sl_price:
                exit_p = sl_price
                pnl = (exit_p - avg_entry) * total_size - cost * total_size
                equity += pnl
                trades.append({
                    'entry_date': entry_date, 'exit_date': row['date'],
                    'direction': 'LONG', 'entry_price': avg_entry,
                    'exit_price': exit_p, 'pnl': pnl,
                    'exit_reason': 'SL', 'bars': i - df.index[0],
                    'equity': equity,
                })
                position = 0
                avg_entry = 0
                total_size = 0
                pyramid_count = 0
                continue
            
            if position == -1 and row['high'] >= sl_price:
                exit_p = sl_price
                pnl = (avg_entry - exit_p) * total_size - cost * total_size
                equity += pnl
                trades.append({
                    'entry_date': entry_date, 'exit_date': row['date'],
                    'direction': 'SHORT', 'entry_price': avg_entry,
                    'exit_price': exit_p, 'pnl': pnl,
                    'exit_reason': 'SL', 'bars': i - df.index[0],
                    'equity': equity,
                })
                position = 0
                avg_entry = 0
                total_size = 0
                pyramid_count = 0
                continue
            
            # ATR take profit
            if atr_tp_mult > 0:
                if position == 1 and row['high'] >= tp_price:
                    exit_p = tp_price
                    pnl = (exit_p - avg_entry) * total_size - cost * total_size
                    equity += pnl
                    trades.append({
                        'entry_date': entry_date, 'exit_date': row['date'],
                        'direction': 'LONG', 'entry_price': avg_entry,
                        'exit_price': exit_p, 'pnl': pnl,
                        'exit_reason': 'TP', 'bars': i - df.index[0],
                        'equity': equity,
                    })
                    position = 0
                    avg_entry = 0
                    total_size = 0
                    pyramid_count = 0
                    continue
                
                if position == -1 and row['low'] <= tp_price:
                    exit_p = tp_price
                    pnl = (avg_entry - exit_p) * total_size - cost * total_size
                    equity += pnl
                    trades.append({
                        'entry_date': entry_date, 'exit_date': row['date'],
                        'direction': 'SHORT', 'entry_price': avg_entry,
                        'exit_price': exit_p, 'pnl': pnl,
                        'exit_reason': 'TP', 'bars': i - df.index[0],
                        'equity': equity,
                    })
                    position = 0
                    avg_entry = 0
                    total_size = 0
                    pyramid_count = 0
                    continue
            
            # Channel breakout exit (reverse signal)
            if position == 1 and row['close'] < row[exit_low_col]:
                exit_p = row['close']
                pnl = (exit_p - avg_entry) * total_size - cost * total_size
                equity += pnl
                trades.append({
                    'entry_date': entry_date, 'exit_date': row['date'],
                    'direction': 'LONG', 'entry_price': avg_entry,
                    'exit_price': exit_p, 'pnl': pnl,
                    'exit_reason': 'REVERSE', 'bars': i - df.index[0],
                    'equity': equity,
                })
                position = 0
                avg_entry = 0
                total_size = 0
                pyramid_count = 0
                # Don't continue — might enter short on same bar
            
            if position == -1 and row['close'] > row[exit_high_col]:
                exit_p = row['close']
                pnl = (avg_entry - exit_p) * total_size - cost * total_size
                equity += pnl
                trades.append({
                    'entry_date': entry_date, 'exit_date': row['date'],
                    'direction': 'SHORT', 'entry_price': avg_entry,
                    'exit_price': exit_p, 'pnl': pnl,
                    'exit_reason': 'REVERSE', 'bars': i - df.index[0],
                    'equity': equity,
                })
                position = 0
                avg_entry = 0
                total_size = 0
                pyramid_count = 0
                # Don't continue — might enter long on same bar
        
        # ── Entries ──
        if position == 0:
            # ADX filter
            if adx_filter > 0 and (pd.isna(row['adx']) or row['adx'] < adx_filter):
                continue
            
            # Long entry
            if row['close'] > row[high_col]:
                position = 1
                entry_price = row['close']
                sl_price = entry_price - atr_sl_mult * row['atr']
                tp_price = entry_price + atr_tp_mult * row['atr'] if atr_tp_mult > 0 else 0
                # Position size: risk risk_per_trade of equity, stop = ATR*mult
                risk_amount = equity * risk_per_trade
                total_size = risk_amount / (atr_sl_mult * row['atr']) if row['atr'] > 0 else 1
                avg_entry = entry_price
                entry_date = row['date']
                pyramid_count = 0
                continue
            
            # Short entry
            if row['close'] < row[low_col]:
                position = -1
                entry_price = row['close']
                sl_price = entry_price + atr_sl_mult * row['atr']
                tp_price = entry_price - atr_tp_mult * row['atr'] if atr_tp_mult > 0 else 0
                risk_amount = equity * risk_per_trade
                total_size = risk_amount / (atr_sl_mult * row['atr']) if row['atr'] > 0 else 1
                avg_entry = entry_price
                entry_date = row['date']
                pyramid_count = 0
                continue
        
        # ── Pyramiding (add to winner) ──
        if position != 0 and pyramiding > 1 and pyramid_count < pyramiding - 1:
            # Add on next channel break in same direction
            if position == 1 and row['close'] > row[high_col]:
                # Add to long
                add_price = row['close']
                add_size = total_size * 0.5  # half original size
                avg_entry = (avg_entry * total_size + add_price * add_size) / (total_size + add_size)
                total_size += add_size
                sl_price = avg_entry - atr_sl_mult * row['atr']
                pyramid_count += 1
            
            if position == -1 and row['close'] < row[low_col]:
                add_price = row['close']
                add_size = total_size * 0.5
                avg_entry = (avg_entry * total_size + add_price * add_size) / (total_size + add_size)
                total_size += add_size
                sl_price = avg_entry + atr_sl_mult * row['atr']
                pyramid_count += 1
        
        # Trailing stop: move SL to breakeven after 2*ATR profit
        if position == 1 and avg_entry > 0:
            profit = row['close'] - avg_entry
            if profit > 2 * row['atr']:
                new_sl = row['close'] - atr_sl_mult * row['atr']
                sl_price = max(sl_price, new_sl)
        
        if position == -1 and avg_entry > 0:
            profit = avg_entry - row['close']
            if profit > 2 * row['atr']:
                new_sl = row['close'] + atr_sl_mult * row['atr']
                sl_price = min(sl_price, new_sl)
        
        peak_equity = max(peak_equity, equity)
    
    return pd.DataFrame(trades)


def print_results(trades_df, label=""):
    if len(trades_df) == 0:
        print(f"  {label}No trades")
        return
    
    n = len(trades_df)
    total = trades_df['pnl'].sum()
    avg = trades_df['pnl'].mean()
    wr = (trades_df['pnl'] > 0).mean() * 100
    std = trades_df['pnl'].std()
    sharpe = (avg / std * np.sqrt(252)) if std > 0 else 0  # annualized from daily
    wins = trades_df[trades_df['pnl'] > 0]['pnl'].sum()
    losses = abs(trades_df[trades_df['pnl'] <= 0]['pnl'].sum())
    pf = wins / losses if losses > 0 else 999
    
    # Max drawdown from equity curve
    eq = trades_df['equity']
    dd = (eq - eq.cummax()).min()
    dd_pct = dd / eq.cummax().max() * 100
    
    # Avg trade duration
    if 'entry_date' in trades_df.columns and 'exit_date' in trades_df.columns:
        dates = pd.to_datetime(trades_df['exit_date']) - pd.to_datetime(trades_df['entry_date'])
        avg_dur = dates.mean().days
    else:
        avg_dur = 0
    
    print(f"  {label}")
    print(f"    N={n}  WR={wr:.1f}%  Total=${total:+.2f}  Avg=${avg:+.4f}")
    print(f"    PF={pf:.2f}  Sharpe={sharpe:+.3f}  MaxDD=${dd:+.2f} ({dd_pct:.1f}%)  AvgDur={avg_dur}d")
    
    # By direction
    for d in ['LONG', 'SHORT']:
        sub = trades_df[trades_df['direction'] == d]
        if len(sub) > 0:
            print(f"    {d}: N={len(sub)}  WR={(sub['pnl']>0).mean()*100:.1f}%  "
                  f"Total=${sub['pnl'].sum():+.2f}  Avg=${sub['pnl'].mean():+.4f}")
    
    # By exit reason
    for reason in trades_df['exit_reason'].unique():
        sub = trades_df[trades_df['exit_reason'] == reason]
        print(f"    {reason}: N={len(sub)}  Total=${sub['pnl'].sum():+.2f}  Avg=${sub['pnl'].mean():+.4f}")
    
    # Year-by-year
    trades_df = trades_df.copy()
    trades_df['year'] = pd.to_datetime(trades_df['exit_date']).dt.year
    for year, g in trades_df.groupby('year'):
        eq_max = g['equity'].cummax()
        dd_y = (g['equity'] - eq_max).min()
        print(f"    {year}: ${g['pnl'].sum():+.2f}  WR={(g['pnl']>0).mean()*100:.0f}%  "
              f"N={len(g)}  DD=${dd_y:+.2f}")


# ═══════════════════════════════════════════════════════════════════════════
# LOAD DATA
# ═══════════════════════════════════════════════════════════════════════════
print("Loading daily data...")
xau = load_daily("xauusd")
print(f"  XAUUSD: {len(xau)} days, {xau['date'].iloc[0].date()} -> {xau['date'].iloc[-1].date()}")

eur = load_daily("eurusd", scale=100)
print(f"  EURUSD: {len(eur)} days")

gbp = load_daily("gbpusd")
print(f"  GBPUSD: {len(gbp)} days")

aud = load_daily("audusd")
print(f"  AUDUSD: {len(aud)} days")

jpy = load_daily("usdjpy", scale=100)
print(f"  USDJPY: {len(jpy)} days")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 1: SINGLE CHANNEL BREAKOUT — VARY ENTRY PERIOD
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  TEST 1: XAUUSD Channel Breakout — Vary Entry Period")
print("  (ATR SL=2x, no TP, exit on opposite 10d break, cost=$0.30)")
print("=" * 70)

for entry_n in [5, 10, 20, 50, 100, 200]:
    t = backtest_channel_breakout(xau, entry_period=entry_n, exit_period=10,
                                   atr_sl_mult=2.0, atr_tp_mult=0,
                                   cost=0.30, risk_per_trade=0.01)
    if len(t) > 0:
        n = len(t)
        total = t['pnl'].sum()
        avg = t['pnl'].mean()
        wr = (t['pnl'] > 0).mean() * 100
        std = t['pnl'].std()
        sh = (avg / std * np.sqrt(252)) if std > 0 else 0
        eq = t['equity']
        dd = (eq - eq.cummax()).min()
        print(f"  {entry_n:3d}-day: N={n:4d}  WR={wr:5.1f}%  Total=${total:+10.2f}  "
              f"Avg=${avg:+8.4f}  Sharpe={sh:+6.3f}  MaxDD=${dd:+8.2f}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 2: DUAL CHANNEL (fast entry, slow exit)
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  TEST 2: XAUUSD Dual Channel — Fast Entry / Slow Exit")
print("  (Entry on 20d break, exit on opposite N-day break)")
print("=" * 70)

for exit_n in [5, 10, 20, 50]:
    t = backtest_channel_breakout(xau, entry_period=20, exit_period=exit_n,
                                   atr_sl_mult=2.0, atr_tp_mult=0,
                                   cost=0.30, risk_per_trade=0.01)
    if len(t) > 0:
        n = len(t)
        total = t['pnl'].sum()
        avg = t['pnl'].mean()
        wr = (t['pnl'] > 0).mean() * 100
        std = t['pnl'].std()
        sh = (avg / std * np.sqrt(252)) if std > 0 else 0
        eq = t['equity']
        dd = (eq - eq.cummax()).min()
        print(f"  Entry=20d Exit={exit_n:2d}d: N={n:4d}  WR={wr:5.1f}%  Total=${total:+10.2f}  "
              f"Avg=${avg:+8.4f}  Sharpe={sh:+6.3f}  MaxDD=${dd:+8.2f}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 3: ATR STOP MULTIPLIER SWEEP
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  TEST 3: XAUUSD ATR Stop Multiplier Sweep (20d entry)")
print("=" * 70)

for sl_mult in [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]:
    t = backtest_channel_breakout(xau, entry_period=20, exit_period=10,
                                   atr_sl_mult=sl_mult, atr_tp_mult=0,
                                   cost=0.30, risk_per_trade=0.01)
    if len(t) > 0:
        n = len(t)
        total = t['pnl'].sum()
        avg = t['pnl'].mean()
        wr = (t['pnl'] > 0).mean() * 100
        std = t['pnl'].std()
        sh = (avg / std * np.sqrt(252)) if std > 0 else 0
        eq = t['equity']
        dd = (eq - eq.cummax()).min()
        print(f"  ATR SL={sl_mult:.1f}x: N={n:4d}  WR={wr:5.1f}%  Total=${total:+10.2f}  "
              f"Avg=${avg:+8.4f}  Sharpe={sh:+6.3f}  MaxDD=${dd:+8.2f}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 4: WITH TAKE PROFIT
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  TEST 4: XAUUSD With Take Profit (20d entry, 2x ATR SL)")
print("=" * 70)

for tp_mult in [2, 3, 4, 5, 8, 0]:
    label = f"TP={tp_mult}x ATR" if tp_mult > 0 else "No TP (ride trend)"
    t = backtest_channel_breakout(xau, entry_period=20, exit_period=10,
                                   atr_sl_mult=2.0, atr_tp_mult=tp_mult,
                                   cost=0.30, risk_per_trade=0.01)
    if len(t) > 0:
        n = len(t)
        total = t['pnl'].sum()
        avg = t['pnl'].mean()
        wr = (t['pnl'] > 0).mean() * 100
        std = t['pnl'].std()
        sh = (avg / std * np.sqrt(252)) if std > 0 else 0
        eq = t['equity']
        dd = (eq - eq.cummax()).min()
        print(f"  {label:20s}: N={n:4d}  WR={wr:5.1f}%  Total=${total:+10.2f}  "
              f"Avg=${avg:+8.4f}  Sharpe={sh:+6.3f}  MaxDD=${dd:+8.2f}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 5: ADX FILTER
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  TEST 5: XAUUSD With ADX Filter (20d entry, 2x ATR SL)")
print("=" * 70)

for adx_min in [0, 15, 20, 25, 30]:
    t = backtest_channel_breakout(xau, entry_period=20, exit_period=10,
                                   atr_sl_mult=2.0, atr_tp_mult=0,
                                   cost=0.30, risk_per_trade=0.01,
                                   adx_filter=adx_min)
    if len(t) > 0:
        n = len(t)
        total = t['pnl'].sum()
        avg = t['pnl'].mean()
        wr = (t['pnl'] > 0).mean() * 100
        std = t['pnl'].std()
        sh = (avg / std * np.sqrt(252)) if std > 0 else 0
        eq = t['equity']
        dd = (eq - eq.cummax()).min()
        label = f"ADX>={adx_min}" if adx_min > 0 else "No filter"
        print(f"  {label:12s}: N={n:4d}  WR={wr:5.1f}%  Total=${total:+10.2f}  "
              f"Avg=${avg:+8.4f}  Sharpe={sh:+6.3f}  MaxDD=${dd:+8.2f}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 6: MULTI-PAIR COMPARISON
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  TEST 6: Multi-Pair Trend Following (20d entry, 10d exit, 2x ATR SL)")
print("=" * 70)

pair_data = [
    ("XAUUSD", xau, 0.30),
    ("EURUSD", eur, 0.0004),
    ("GBPUSD", gbp, 0.0003),
    ("AUDUSD", aud, 0.0004),
    ("USDJPY", jpy, 0.04),
]

for name, data, cost in pair_data:
    t = backtest_channel_breakout(data, entry_period=20, exit_period=10,
                                   atr_sl_mult=2.0, atr_tp_mult=0,
                                   cost=cost, risk_per_trade=0.01)
    if len(t) > 0:
        n = len(t)
        total = t['pnl'].sum()
        avg = t['pnl'].mean()
        wr = (t['pnl'] > 0).mean() * 100
        std = t['pnl'].std()
        sh = (avg / std * np.sqrt(252)) if std > 0 else 0
        eq = t['equity']
        dd = (eq - eq.cummax()).min()
        print(f"  {name:8s}: N={n:4d}  WR={wr:5.1f}%  Total=${total:+10.2f}  "
              f"Avg=${avg:+8.4f}  Sharpe={sh:+6.3f}  MaxDD=${dd:+8.2f}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 7: BEST CONFIG — DETAILED BREAKDOWN
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  TEST 7: Best Config — Detailed Year-by-Year (XAUUSD)")
print("=" * 70)

# Find best from Test 1, run with details
best_configs = [
    ("20d channel", 20, 10, 2.0, 0, 0),
    ("50d channel", 50, 20, 2.5, 0, 0),
    ("20d + ADX>20", 20, 10, 2.0, 0, 20),
    ("20d + TP=5x", 20, 10, 2.0, 5, 0),
]

for label, entry_n, exit_n, sl, tp, adx in best_configs:
    print(f"\n  --- {label} ---")
    t = backtest_channel_breakout(xau, entry_period=entry_n, exit_period=exit_n,
                                   atr_sl_mult=sl, atr_tp_mult=tp,
                                   cost=0.30, risk_per_trade=0.01,
                                   adx_filter=adx)
    print_results(t)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 8: COMPARISON WITH ORB
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  TEST 8: Trend Following vs ORB Comparison")
print("=" * 70)
print("""
  ORB (from previous backtest):
    366 trades over 8 years (~46/year)
    Win rate: 51.6%
    Total P&L: +$943 (no slip), +$724 ($0.20 slip)
    Sharpe: +1.15 (raw), +0.88 (with slip)
    Max DD: -$132
    Key: Intraday, asymmetric (RR 2.5:1), session-based
    
  Trend Following:
    Daily timeframe, channel breakout
    Key: Multi-day, ride trends, ATR stops
    
  Which is better depends on:
    - Sharpe ratio (risk-adjusted)
    - Max drawdown
    - Trade frequency (more trades = more confidence)
    - Correlation with ORB (can we run both?)
""")

print("\n=== TREND FOLLOWING BACKTEST COMPLETE ===")

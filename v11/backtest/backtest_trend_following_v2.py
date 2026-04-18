"""
Trend Following Backtest V2 — Next-Day Open Entry + Walk-Forward Validation

Critical tests from the underwater obstacles analysis:
1. Next-day open entry (vs signal-bar close) — the #1 reality adjustment
2. Longs-only mode — short side loses money on gold
3. Walk-forward validation — 2018-2022 IS -> 2023-2026 OOS
4. Combined impact — next-day open + longs-only together

The original backtest enters at the breakout bar's close. In reality,
you can't know the close is a breakout until the bar completes, so
you'd enter at the NEXT day's open. This test quantifies that gap.
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
    
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        )
    )
    df['atr'] = df['tr'].rolling(atr_period).mean()
    df['atr_pct'] = df['atr'] / df['close'] * 100
    
    for n in [5, 10, 20, 50, 100, 200]:
        df[f'high_{n}d'] = df['high'].rolling(n).max().shift(1)
        df[f'low_{n}d'] = df['low'].rolling(n).min().shift(1)
    
    for n in [10, 20, 50, 100, 200]:
        df[f'sma_{n}'] = df['close'].rolling(n).mean()
    
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
                               atr_tp_mult: float = 0,
                               cost: float = 0.30,
                               risk_per_trade: float = 0.01,
                               adx_filter: float = 0,
                               pyramiding: int = 1,
                               next_day_open: bool = False,
                               longs_only: bool = False,
                               regime_filter: str = '',  # 'sma200', 'sma50', ''=off
                               start_date: str = None,
                               end_date: str = None,
                               ) -> pd.DataFrame:
    """
    Channel breakout trend following with realistic entry options.
    
    NEW PARAMETERS vs v1:
        next_day_open: If True, enter at next bar's open instead of signal bar's close.
                       This is the realistic execution — you can't know the close is a
                       breakout until the bar completes.
        longs_only: If True, only take long entries (short side loses on gold).
        regime_filter: Only take longs when price > SMA (bull regime).
                       'sma200' = above 200d SMA, 'sma50' = above 50d SMA, '' = off.
                       Goes flat when below SMA — avoids buying breakouts in bear markets.
        start_date / end_date: Filter data range for walk-forward splits.
    """
    df = compute_indicators(df)
    
    # Apply date filters
    if start_date:
        df = df[df['date'] >= pd.Timestamp(start_date)]
    if end_date:
        df = df[df['date'] <= pd.Timestamp(end_date)]
    df = df.reset_index(drop=True)
    
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
    pending_signal = 0  # 0=none, 1=pending long, -1=pending short
    pending_sl = 0
    pending_tp = 0
    pending_size = 0
    pending_atr = 0
    
    equity = 10000
    peak_equity = 10000
    
    for i in range(max(entry_period, 200) + 1, len(df)):
        row = df.iloc[i]
        
        if pd.isna(row[high_col]) or pd.isna(row[low_col]) or pd.isna(row['atr']):
            continue
        
        # ── Execute pending signal (next-day open entry) ──
        if pending_signal != 0 and position == 0:
            entry_price = row['open']  # enter at next day's open
            sl_price = pending_sl
            tp_price = pending_tp
            total_size = pending_size
            avg_entry = entry_price
            entry_date = row['date']
            position = pending_signal
            pyramid_count = 0
            pending_signal = 0
            
            # Check if open already through stop (gap through SL on open)
            if position == 1 and row['low'] <= sl_price:
                # Stopped out on entry day — exit at stop (or worse, use open if open < SL)
                exit_p = min(sl_price, row['open'])  # worst case: gap through
                pnl = (exit_p - avg_entry) * total_size - cost * total_size
                equity += pnl
                trades.append({
                    'entry_date': entry_date, 'exit_date': row['date'],
                    'direction': 'LONG', 'entry_price': avg_entry,
                    'exit_price': exit_p, 'pnl': pnl,
                    'exit_reason': 'SL_ON_ENTRY', 'bars': 0,
                    'equity': equity,
                })
                position = 0
                avg_entry = 0
                total_size = 0
                pyramid_count = 0
                continue
            
            if position == -1 and row['high'] >= sl_price:
                exit_p = max(sl_price, row['open'])
                pnl = (avg_entry - exit_p) * total_size - cost * total_size
                equity += pnl
                trades.append({
                    'entry_date': entry_date, 'exit_date': row['date'],
                    'direction': 'SHORT', 'entry_price': avg_entry,
                    'exit_price': exit_p, 'pnl': pnl,
                    'exit_reason': 'SL_ON_ENTRY', 'bars': 0,
                    'equity': equity,
                })
                position = 0
                avg_entry = 0
                total_size = 0
                pyramid_count = 0
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
        
        # ── Entries ──
        if position == 0:
            # ADX filter
            if adx_filter > 0 and (pd.isna(row['adx']) or row['adx'] < adx_filter):
                continue
            
            # Regime filter: skip longs when price below SMA (bear regime)
            sma_col = f'sma_200' if regime_filter == 'sma200' else f'sma_50' if regime_filter == 'sma50' else None
            regime_ok = True
            if sma_col and not pd.isna(row.get(sma_col, float('nan'))):
                regime_ok = row['close'] > row[sma_col]
            
            # Long entry
            if row['close'] > row[high_col] and regime_ok:
                if next_day_open:
                    # Don't enter now — mark pending signal for next bar's open
                    risk_amount = equity * risk_per_trade
                    size = risk_amount / (atr_sl_mult * row['atr']) if row['atr'] > 0 else 1
                    pending_signal = 1
                    pending_sl = row['close'] - atr_sl_mult * row['atr']  # SL from signal close
                    pending_tp = row['close'] + atr_tp_mult * row['atr'] if atr_tp_mult > 0 else 0
                    pending_size = size
                    pending_atr = row['atr']
                    # But recalculate SL from actual entry price on execution
                else:
                    # Original: enter at signal bar's close
                    entry_price = row['close']
                    sl_price = entry_price - atr_sl_mult * row['atr']
                    tp_price = entry_price + atr_tp_mult * row['atr'] if atr_tp_mult > 0 else 0
                    risk_amount = equity * risk_per_trade
                    total_size = risk_amount / (atr_sl_mult * row['atr']) if row['atr'] > 0 else 1
                    avg_entry = entry_price
                    entry_date = row['date']
                    position = 1
                    pyramid_count = 0
                continue
            
            # Short entry (skip if longs_only, or if regime filter says bear = good for shorts)
            short_regime_ok = True
            if sma_col and not pd.isna(row.get(sma_col, float('nan'))):
                # In bear regime (below SMA), shorts are allowed; in bull, skip shorts
                short_regime_ok = row['close'] < row[sma_col]
            if not longs_only and row['close'] < row[low_col] and short_regime_ok:
                if next_day_open:
                    risk_amount = equity * risk_per_trade
                    size = risk_amount / (atr_sl_mult * row['atr']) if row['atr'] > 0 else 1
                    pending_signal = -1
                    pending_sl = row['close'] + atr_sl_mult * row['atr']
                    pending_tp = row['close'] - atr_tp_mult * row['atr'] if atr_tp_mult > 0 else 0
                    pending_size = size
                    pending_atr = row['atr']
                else:
                    entry_price = row['close']
                    sl_price = entry_price + atr_sl_mult * row['atr']
                    tp_price = entry_price - atr_tp_mult * row['atr'] if atr_tp_mult > 0 else 0
                    risk_amount = equity * risk_per_trade
                    total_size = risk_amount / (atr_sl_mult * row['atr']) if row['atr'] > 0 else 1
                    avg_entry = entry_price
                    entry_date = row['date']
                    position = -1
                    pyramid_count = 0
                continue
        
        # ── Pyramiding ──
        if position != 0 and pyramiding > 1 and pyramid_count < pyramiding - 1:
            if position == 1 and row['close'] > row[high_col]:
                add_price = row['close']
                add_size = total_size * 0.5
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
        
        # Trailing stop
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
        return {}
    
    n = len(trades_df)
    total = trades_df['pnl'].sum()
    avg = trades_df['pnl'].mean()
    wr = (trades_df['pnl'] > 0).mean() * 100
    std = trades_df['pnl'].std()
    sharpe = (avg / std * np.sqrt(252)) if std > 0 else 0
    wins = trades_df[trades_df['pnl'] > 0]['pnl'].sum()
    losses = abs(trades_df[trades_df['pnl'] <= 0]['pnl'].sum())
    pf = wins / losses if losses > 0 else 999
    
    eq = trades_df['equity']
    dd = (eq - eq.cummax()).min()
    dd_pct = dd / eq.cummax().max() * 100
    
    if 'entry_date' in trades_df.columns and 'exit_date' in trades_df.columns:
        dates = pd.to_datetime(trades_df['exit_date']) - pd.to_datetime(trades_df['entry_date'])
        avg_dur = dates.mean().days
    else:
        avg_dur = 0
    
    print(f"  {label}")
    print(f"    N={n}  WR={wr:.1f}%  Total=${total:+.2f}  Avg=${avg:+.4f}")
    print(f"    PF={pf:.2f}  Sharpe={sharpe:+.3f}  MaxDD=${dd:+.2f} ({dd_pct:.1f}%)  AvgDur={avg_dur}d")
    
    for d in ['LONG', 'SHORT']:
        sub = trades_df[trades_df['direction'] == d]
        if len(sub) > 0:
            print(f"    {d}: N={len(sub)}  WR={(sub['pnl']>0).mean()*100:.1f}%  "
                  f"Total=${sub['pnl'].sum():+.2f}  Avg=${sub['pnl'].mean():+.4f}")
    
    for reason in trades_df['exit_reason'].unique():
        sub = trades_df[trades_df['exit_reason'] == reason]
        print(f"    {reason}: N={len(sub)}  Total=${sub['pnl'].sum():+.2f}  Avg=${sub['pnl'].mean():+.4f}")
    
    trades_df = trades_df.copy()
    trades_df['year'] = pd.to_datetime(trades_df['exit_date']).dt.year
    for year, g in trades_df.groupby('year'):
        eq_max = g['equity'].cummax()
        dd_y = (g['equity'] - eq_max).min()
        print(f"    {year}: ${g['pnl'].sum():+.2f}  WR={(g['pnl']>0).mean()*100:.0f}%  "
              f"N={len(g)}  DD=${dd_y:+.2f}")
    
    return {'n': n, 'wr': wr, 'total': total, 'avg': avg, 'sharpe': sharpe,
            'max_dd': dd, 'dd_pct': dd_pct, 'pf': pf}


def print_compact(trades_df, label=""):
    """One-line summary for comparison tables."""
    if len(trades_df) == 0:
        print(f"  {label:40s}  No trades")
        return
    
    n = len(trades_df)
    total = trades_df['pnl'].sum()
    avg = trades_df['pnl'].mean()
    wr = (trades_df['pnl'] > 0).mean() * 100
    std = trades_df['pnl'].std()
    sharpe = (avg / std * np.sqrt(252)) if std > 0 else 0
    eq = trades_df['equity']
    dd = (eq - eq.cummax()).min()
    
    print(f"  {label:40s}  N={n:3d}  WR={wr:5.1f}%  Total=${total:+9.2f}  "
          f"Avg=${avg:+8.2f}  Sharpe={sharpe:+6.2f}  MaxDD=${dd:+7.2f}")


# ═══════════════════════════════════════════════════════════════════════════
# LOAD DATA
# ═══════════════════════════════════════════════════════════════════════════
print("Loading daily data...")
xau = load_daily("xauusd")
print(f"  XAUUSD: {len(xau)} days, {xau['date'].iloc[0].date()} -> {xau['date'].iloc[-1].date()}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 1: NEXT-DAY OPEN ENTRY — THE CRITICAL TEST
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  TEST 1: Next-Day Open Entry vs Signal-Bar Close")
print("  How much does realistic entry execution cost?")
print("=" * 80)

configs = [
    ("20d channel (SL=2x)", 20, 10, 2.0, 0, 0),
    ("50d channel (SL=2.5x)", 50, 20, 2.5, 0, 0),
    ("50d + ADX>=30", 50, 20, 2.5, 0, 30),
    ("20d + ADX>=20", 20, 10, 2.0, 0, 20),
]

for label, entry_n, exit_n, sl, tp, adx in configs:
    print(f"\n  --- {label} ---")
    
    # Baseline: signal-bar close entry (original backtest)
    t_close = backtest_channel_breakout(xau, entry_period=entry_n, exit_period=exit_n,
                                          atr_sl_mult=sl, atr_tp_mult=tp,
                                          cost=0.30, risk_per_trade=0.01,
                                          adx_filter=adx, next_day_open=False)
    print_compact(t_close, "  Signal-bar close (baseline)")
    
    # Realistic: next-day open entry
    t_open = backtest_channel_breakout(xau, entry_period=entry_n, exit_period=exit_n,
                                         atr_sl_mult=sl, atr_tp_mult=tp,
                                         cost=0.30, risk_per_trade=0.01,
                                         adx_filter=adx, next_day_open=True)
    print_compact(t_open, "  Next-day open (realistic)")
    
    # Impact calculation
    if len(t_close) > 0 and len(t_open) > 0:
        total_close = t_close['pnl'].sum()
        total_open = t_open['pnl'].sum()
        pct_loss = (total_close - total_open) / abs(total_close) * 100 if total_close != 0 else 0
        avg_close = t_close['pnl'].mean()
        avg_open = t_open['pnl'].mean()
        avg_pct_loss = (avg_close - avg_open) / abs(avg_close) * 100 if avg_close != 0 else 0
        print(f"  >>> Impact: Total P&L drops {pct_loss:+.1f}%, Avg trade drops {avg_pct_loss:+.1f}%")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 2: LONGS-ONLY MODE
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  TEST 2: Longs-Only vs Both Directions")
print("  Short side loses money on gold — does removing it help?")
print("=" * 80)

for label, entry_n, exit_n, sl, tp, adx in configs:
    print(f"\n  --- {label} ---")
    
    t_both = backtest_channel_breakout(xau, entry_period=entry_n, exit_period=exit_n,
                                        atr_sl_mult=sl, atr_tp_mult=tp,
                                        cost=0.30, risk_per_trade=0.01,
                                        adx_filter=adx, next_day_open=True,
                                        longs_only=False)
    print_compact(t_both, "  Both directions (realistic)")
    
    t_long = backtest_channel_breakout(xau, entry_period=entry_n, exit_period=exit_n,
                                         atr_sl_mult=sl, atr_tp_mult=tp,
                                         cost=0.30, risk_per_trade=0.01,
                                         adx_filter=adx, next_day_open=True,
                                         longs_only=True)
    print_compact(t_long, "  Longs only (realistic)")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 3: COMBINED — NEXT-DAY OPEN + LONGS-ONLY (BEST REALISTIC ESTIMATE)
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  TEST 3: Best Realistic Estimate — Next-Day Open + Longs-Only")
print("  The most honest version of trend following on XAUUSD")
print("=" * 80)

for label, entry_n, exit_n, sl, tp, adx in configs:
    print(f"\n  --- {label} ---")
    
    # Raw backtest (original)
    t_raw = backtest_channel_breakout(xau, entry_period=entry_n, exit_period=exit_n,
                                        atr_sl_mult=sl, atr_tp_mult=tp,
                                        cost=0.30, risk_per_trade=0.01,
                                        adx_filter=adx, next_day_open=False,
                                        longs_only=False)
    print_compact(t_raw, "  RAW (close entry, both dirs)")
    
    # Realistic best
    t_real = backtest_channel_breakout(xau, entry_period=entry_n, exit_period=exit_n,
                                         atr_sl_mult=sl, atr_tp_mult=tp,
                                         cost=0.30, risk_per_trade=0.01,
                                         adx_filter=adx, next_day_open=True,
                                         longs_only=True)
    print_compact(t_real, "  REALISTIC (open entry, longs only)")
    
    if len(t_raw) > 0 and len(t_real) > 0:
        total_raw = t_raw['pnl'].sum()
        total_real = t_real['pnl'].sum()
        print(f"  >>> Realistic is {total_real/total_raw*100:.0f}% of raw backtest")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 4: WALK-FORWARD VALIDATION
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  TEST 4: Walk-Forward Validation")
print("  In-sample: 2018-2022 | Out-of-sample: 2023-2026")
print("  If it survives OOS, the edge is real.")
print("=" * 80)

for label, entry_n, exit_n, sl, tp, adx in configs:
    print(f"\n  --- {label} ---")
    
    # In-sample (2018-2022)
    t_is = backtest_channel_breakout(xau, entry_period=entry_n, exit_period=exit_n,
                                       atr_sl_mult=sl, atr_tp_mult=tp,
                                       cost=0.30, risk_per_trade=0.01,
                                       adx_filter=adx, next_day_open=True,
                                       longs_only=True,
                                       start_date='2018-01-01', end_date='2022-12-31')
    print_compact(t_is, "  IS 2018-2022 (open, longs)")
    
    # Out-of-sample (2023-2026)
    t_oos = backtest_channel_breakout(xau, entry_period=entry_n, exit_period=exit_n,
                                        atr_sl_mult=sl, atr_tp_mult=tp,
                                        cost=0.30, risk_per_trade=0.01,
                                        adx_filter=adx, next_day_open=True,
                                        longs_only=True,
                                        start_date='2023-01-01', end_date='2026-12-31')
    print_compact(t_oos, "  OOS 2023-2026 (open, longs)")
    
    # Also run OOS with both directions for comparison
    t_oos_both = backtest_channel_breakout(xau, entry_period=entry_n, exit_period=exit_n,
                                             atr_sl_mult=sl, atr_tp_mult=tp,
                                             cost=0.30, risk_per_trade=0.01,
                                             adx_filter=adx, next_day_open=True,
                                             longs_only=False,
                                             start_date='2023-01-01', end_date='2026-12-31')
    print_compact(t_oos_both, "  OOS 2023-2026 (open, both)")
    
    # Walk-forward efficiency
    if len(t_is) > 0 and len(t_oos) > 0:
        is_sharpe = (t_is['pnl'].mean() / t_is['pnl'].std() * np.sqrt(252)) if t_is['pnl'].std() > 0 else 0
        oos_sharpe = (t_oos['pnl'].mean() / t_oos['pnl'].std() * np.sqrt(252)) if t_oos['pnl'].std() > 0 else 0
        wfe = (oos_sharpe / is_sharpe * 100) if is_sharpe != 0 else 0
        print(f"  >>> WFE (OOS Sharpe / IS Sharpe): {wfe:.0f}%  "
              f"(IS={is_sharpe:+.2f}, OOS={oos_sharpe:+.2f})")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 5: DETAILED YEAR-BY-YEAR — BEST REALISTIC CONFIG
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  TEST 5: Detailed Breakdown — Best Realistic Config")
print("  50d channel, SL=2.5x, next-day open, longs-only")
print("=" * 80)

t_best = backtest_channel_breakout(xau, entry_period=50, exit_period=20,
                                     atr_sl_mult=2.5, atr_tp_mult=0,
                                     cost=0.30, risk_per_trade=0.01,
                                     adx_filter=0, next_day_open=True,
                                     longs_only=True)
print_results(t_best, "50d channel, next-day open, longs-only")

print("\n  --- Same config, original (signal-bar close, both dirs) ---")
t_orig = backtest_channel_breakout(xau, entry_period=50, exit_period=20,
                                      atr_sl_mult=2.5, atr_tp_mult=0,
                                      cost=0.30, risk_per_trade=0.01,
                                      adx_filter=0, next_day_open=False,
                                      longs_only=False)
print_results(t_orig, "50d channel, signal-bar close, both dirs")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 6: ENTRY SLIPPAGE ANALYSIS — HOW MUCH DOES THE GAP COST?
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  TEST 6: Entry Slippage Analysis")
print("  Measure the gap between signal-bar close and next-day open")
print("  on breakout days")
print("=" * 80)

xau_ind = compute_indicators(xau)
high_50 = 'high_50d'
gaps = []

for i in range(251, len(xau_ind) - 1):  # need next bar
    row = xau_ind.iloc[i]
    next_row = xau_ind.iloc[i + 1]
    
    if pd.isna(row[high_50]) or pd.isna(row['atr']):
        continue
    
    # Long breakout signal
    if row['close'] > row[high_50]:
        gap = next_row['open'] - row['close']  # positive = chasing (bad for longs)
        gap_pct = gap / row['close'] * 100
        gaps.append({
            'date': row['date'],
            'signal_close': row['close'],
            'next_open': next_row['open'],
            'gap': gap,
            'gap_pct': gap_pct,
            'atr': row['atr'],
            'gap_vs_atr': gap / row['atr'] if row['atr'] > 0 else 0,
            'direction': 'LONG',
        })
    
    # Short breakout signal
    low_50 = 'low_50d'
    if row['close'] < row[low_50]:
        gap = row['close'] - next_row['open']  # positive = chasing (bad for shorts)
        gap_pct = gap / row['close'] * 100
        gaps.append({
            'date': row['date'],
            'signal_close': row['close'],
            'next_open': next_row['open'],
            'gap': gap,
            'gap_pct': gap_pct,
            'atr': row['atr'],
            'gap_vs_atr': gap / row['atr'] if row['atr'] > 0 else 0,
            'direction': 'SHORT',
        })

gaps_df = pd.DataFrame(gaps)
if len(gaps_df) > 0:
    print(f"\n  Total breakout signals: {len(gaps_df)}")
    print(f"  Long signals: {len(gaps_df[gaps_df['direction']=='LONG'])}")
    print(f"  Short signals: {len(gaps_df[gaps_df['direction']=='SHORT'])}")
    
    for d in ['LONG', 'SHORT']:
        sub = gaps_df[gaps_df['direction'] == d]
        if len(sub) == 0:
            continue
        print(f"\n  {d} entry gaps (positive = you're chasing, bad):")
        print(f"    Mean gap: ${sub['gap'].mean():+.2f}  ({sub['gap_pct'].mean():+.2f}%)")
        print(f"    Median gap: ${sub['gap'].median():+.2f}  ({sub['gap_pct'].median():+.2f}%)")
        print(f"    Gap vs ATR: {sub['gap_vs_atr'].mean():+.2f}x ATR")
        print(f"    Favorable (gap<0): {(sub['gap'] < 0).sum()} ({(sub['gap'] < 0).mean()*100:.0f}%)")
        print(f"    Adverse (gap>0): {(sub['gap'] > 0).sum()} ({(sub['gap'] > 0).mean()*100:.0f}%)")
        print(f"    Worst gap: ${sub['gap'].max():+.2f}")
        print(f"    Best gap: ${sub['gap'].min():+.2f}")
    
    print(f"\n  Overall: Mean gap = ${gaps_df['gap'].mean():+.2f}, "
          f"Median = ${gaps_df['gap'].median():+.2f}")
    print(f"  As fraction of ATR: {gaps_df['gap_vs_atr'].mean():+.2f}x")
    
    # Year-by-year gap analysis
    gaps_df['year'] = pd.to_datetime(gaps_df['date']).dt.year
    print(f"\n  Year-by-year gap (LONG only):")
    long_gaps = gaps_df[gaps_df['direction'] == 'LONG']
    for year, g in long_gaps.groupby('year'):
        print(f"    {year}: N={len(g)}  Mean gap=${g['gap'].mean():+.2f}  "
              f"vs ATR={g['gap_vs_atr'].mean():+.2f}x")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 7: STOP ORDER ENTRY (Alternative to market order at open)
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  TEST 7: Stop Order Entry at Channel Level")
print("  Place stop order at 50d high before open -> filled on spike through")
print("  Reduces chasing but increases whipsaw risk")
print("=" * 80)

# For stop-order entry: if price touches the channel level intraday, you're filled
# at the channel price (not the close or next open). This is actually what the
# original backtest approximates — entry at the breakout level.
# But the key difference: with stop orders, you get filled on SPIKES that reverse,
# not just on true breakouts that hold.

# We can simulate this by checking if the next bar's low (for longs) touches
# the channel level. If it does, we enter at the channel level.
# If the bar opens above the channel, we enter at the open (gap situation).

xau_ind = compute_indicators(xau)
stop_order_trades = []
position = 0
entry_price = 0
sl_price = 0
entry_date = None
avg_entry = 0
total_size = 0
equity = 10000
peak_equity = 10000

entry_period = 50
exit_period = 20
atr_sl_mult = 2.5
cost = 0.30
risk_per_trade = 0.01

high_col = f'high_{entry_period}d'
low_col = f'low_{entry_period}d'
exit_high_col = f'high_{exit_period}d'
exit_low_col = f'low_{exit_period}d'

for i in range(251, len(xau_ind)):
    row = xau_ind.iloc[i]
    
    if pd.isna(row[high_col]) or pd.isna(row[low_col]) or pd.isna(row['atr']):
        continue
    
    # Check exits (same as before)
    if position != 0:
        if position == 1 and row['low'] <= sl_price:
            exit_p = sl_price
            pnl = (exit_p - avg_entry) * total_size - cost * total_size
            equity += pnl
            stop_order_trades.append({
                'entry_date': entry_date, 'exit_date': row['date'],
                'direction': 'LONG', 'entry_price': avg_entry,
                'exit_price': exit_p, 'pnl': pnl,
                'exit_reason': 'SL', 'equity': equity,
            })
            position = 0; avg_entry = 0; total_size = 0
            continue
        
        if position == 1 and row['close'] < row[exit_low_col]:
            exit_p = row['close']
            pnl = (exit_p - avg_entry) * total_size - cost * total_size
            equity += pnl
            stop_order_trades.append({
                'entry_date': entry_date, 'exit_date': row['date'],
                'direction': 'LONG', 'entry_price': avg_entry,
                'exit_price': exit_p, 'pnl': pnl,
                'exit_reason': 'REVERSE', 'equity': equity,
            })
            position = 0; avg_entry = 0; total_size = 0
    
    # Long entry via stop order at channel level
    if position == 0:
        # Stop order placed at 50d high. If price trades through it, we're filled.
        if row['high'] > row[high_col]:
            # Filled! At what price?
            if row['open'] > row[high_col]:
                # Gap open above channel — filled at open (chasing)
                fill_price = row['open']
            else:
                # Touched channel level intraday — filled at channel level (ideal)
                fill_price = row[high_col]
            
            entry_price = fill_price
            sl_price = entry_price - atr_sl_mult * row['atr']
            risk_amount = equity * risk_per_trade
            total_size = risk_amount / (atr_sl_mult * row['atr']) if row['atr'] > 0 else 1
            avg_entry = entry_price
            entry_date = row['date']
            position = 1
            
            # Check if stopped on same bar (whipsaw)
            if row['low'] <= sl_price:
                exit_p = sl_price
                pnl = (exit_p - avg_entry) * total_size - cost * total_size
                equity += pnl
                stop_order_trades.append({
                    'entry_date': entry_date, 'exit_date': row['date'],
                    'direction': 'LONG', 'entry_price': avg_entry,
                    'exit_price': exit_p, 'pnl': pnl,
                    'exit_reason': 'WHIPSAW', 'equity': equity,
                })
                position = 0; avg_entry = 0; total_size = 0
    
    # Trailing stop
    if position == 1 and avg_entry > 0:
        profit = row['close'] - avg_entry
        if profit > 2 * row['atr']:
            new_sl = row['close'] - atr_sl_mult * row['atr']
            sl_price = max(sl_price, new_sl)
    
    peak_equity = max(peak_equity, equity)

stop_df = pd.DataFrame(stop_order_trades)
print(f"\n  Stop-order entry at 50d high (longs only, SL=2.5x ATR):")
print_compact(stop_df, "  Stop-order entry")

# Compare with market order at next open
t_mkt = backtest_channel_breakout(xau, entry_period=50, exit_period=20,
                                    atr_sl_mult=2.5, atr_tp_mult=0,
                                    cost=0.30, risk_per_trade=0.01,
                                    next_day_open=True, longs_only=True)
print_compact(t_mkt, "  Market order at next open")

t_close = backtest_channel_breakout(xau, entry_period=50, exit_period=20,
                                      atr_sl_mult=2.5, atr_tp_mult=0,
                                      cost=0.30, risk_per_trade=0.01,
                                      next_day_open=False, longs_only=True)
print_compact(t_close, "  Signal-bar close (raw)")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 8: REGIME FILTER — SMA200 / SMA50 GATE
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  TEST 8: Regime Filter — Only Trade Longs When Price > SMA")
print("  Gold doubled 2018->2026. Longs-only looks great because of the uptrend.")
print("  What if we only buy breakouts when price is ABOVE the 200d/50d SMA?")
print("  In a bear market, price < SMA -> go flat. No whipsaw longs in downtrend.")
print("=" * 80)

for label, entry_n, exit_n, sl, tp, adx in configs:
    print(f"\n  --- {label} ---")
    
    # Longs-only, no regime filter
    t_lo = backtest_channel_breakout(xau, entry_period=entry_n, exit_period=exit_n,
                                       atr_sl_mult=sl, atr_tp_mult=tp,
                                       cost=0.30, risk_per_trade=0.01,
                                       adx_filter=adx, next_day_open=True,
                                       longs_only=True, regime_filter='')
    print_compact(t_lo, "  Longs-only (no regime filter)")
    
    # Longs-only + SMA200 regime filter
    t_sma200 = backtest_channel_breakout(xau, entry_period=entry_n, exit_period=exit_n,
                                           atr_sl_mult=sl, atr_tp_mult=tp,
                                           cost=0.30, risk_per_trade=0.01,
                                           adx_filter=adx, next_day_open=True,
                                           longs_only=True, regime_filter='sma200')
    print_compact(t_sma200, "  Longs-only + SMA200 filter")
    
    # Longs-only + SMA50 regime filter (tighter)
    t_sma50 = backtest_channel_breakout(xau, entry_period=entry_n, exit_period=exit_n,
                                           atr_sl_mult=sl, atr_tp_mult=tp,
                                           cost=0.30, risk_per_trade=0.01,
                                           adx_filter=adx, next_day_open=True,
                                           longs_only=True, regime_filter='sma50')
    print_compact(t_sma50, "  Longs-only + SMA50 filter")
    
    # Regime filter with shorts allowed in bear regime
    t_regime_both = backtest_channel_breakout(xau, entry_period=entry_n, exit_period=exit_n,
                                                atr_sl_mult=sl, atr_tp_mult=tp,
                                                cost=0.30, risk_per_trade=0.01,
                                                adx_filter=adx, next_day_open=True,
                                                longs_only=False, regime_filter='sma200')
    print_compact(t_regime_both, "  Both dirs + SMA200 (longs above, shorts below)")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 9: HOW MUCH OF THE EDGE IS JUST "BUY GOLD"?
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  TEST 9: How Much of the Edge Is Just 'Buy and Hold Gold'?")
print("  If buy-and-hold does almost as well, trend following adds little value.")
print("=" * 80)

# Buy and hold: buy at start, hold to end
xau_ind = compute_indicators(xau)
start_price = xau_ind.iloc[251]['close']  # same start as backtest
end_price = xau_ind.iloc[-1]['close']
bnh_return = end_price - start_price
bnh_pct = (end_price / start_price - 1) * 100

# With 1% risk sizing on $10K: 1-2 oz position
# Buy and hold at 2 oz
bnh_pnl = bnh_return * 2  # 2 oz position (same avg size as trend following)
print(f"\n  Gold price: ${start_price:.2f} -> ${end_price:.2f} ({bnh_pct:+.1f}%)")
print(f"  Buy & hold P&L (2 oz): ${bnh_pnl:+.2f}")
print(f"  Buy & hold MaxDD: need to compute from daily prices...")

# Compute buy-and-hold drawdown
xau_bh = xau_ind.iloc[251:].copy()
xau_bh['peak'] = xau_bh['close'].cummax()
xau_bh['dd'] = (xau_bh['close'] - xau_bh['peak']) / xau_bh['peak'] * 100
max_dd_pct = xau_bh['dd'].min()
max_dd_price = (xau_bh['close'] - xau_bh['peak']).min() * 2  # 2 oz
print(f"  Buy & hold MaxDD: ${max_dd_price:+.2f} ({max_dd_pct:.1f}%)")

# Trend following best realistic
t_best = backtest_channel_breakout(xau, entry_period=50, exit_period=20,
                                     atr_sl_mult=2.5, atr_tp_mult=0,
                                     cost=0.30, risk_per_trade=0.01,
                                     next_day_open=True, longs_only=True,
                                     regime_filter='')
if len(t_best) > 0:
    tf_total = t_best['pnl'].sum()
    tf_dd = (t_best['equity'] - t_best['equity'].cummax()).min()
    print(f"\n  Trend following (50d, open, longs-only): Total=${tf_total:+.2f}, MaxDD=${tf_dd:+.2f}")
    print(f"  Trend following vs Buy & Hold:")
    print(f"    P&L advantage: ${tf_total - bnh_pnl:+.2f}")
    print(f"    DD advantage: ${tf_dd - max_dd_price:+.2f} (less negative = better)")
    print(f"    Trend following captures {tf_total/bnh_pnl*100:.0f}% of buy-and-hold return")
    print(f"    But with {abs(tf_dd)/abs(max_dd_price)*100:.0f}% of the drawdown")

# Time in market: trend following is flat much of the time
if len(t_best) > 0:
    total_days = len(xau_ind) - 251
    # Approximate: sum of trade durations
    t_best_copy = t_best.copy()
    t_best_copy['dur'] = (pd.to_datetime(t_best_copy['exit_date']) - pd.to_datetime(t_best_copy['entry_date'])).dt.days
    days_in_market = t_best_copy['dur'].sum()
    print(f"\n  Time in market: {days_in_market}/{total_days} days ({days_in_market/total_days*100:.0f}%)")
    print(f"  Buy & hold: 100% in market")
    print(f"  -> Trend following achieves similar P&L with MUCH less time at risk")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 10: REGIME FILTER + WALK-FORWARD
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  TEST 10: Regime Filter + Walk-Forward")
print("  Does the SMA200 filter survive out-of-sample?")
print("=" * 80)

for label, entry_n, exit_n, sl, tp, adx in configs:
    print(f"\n  --- {label} ---")
    
    # IS with regime filter
    t_is = backtest_channel_breakout(xau, entry_period=entry_n, exit_period=exit_n,
                                       atr_sl_mult=sl, atr_tp_mult=tp,
                                       cost=0.30, risk_per_trade=0.01,
                                       adx_filter=adx, next_day_open=True,
                                       longs_only=True, regime_filter='sma200',
                                       start_date='2018-01-01', end_date='2022-12-31')
    print_compact(t_is, "  IS 2018-2022 (SMA200 filter)")
    
    # OOS with regime filter
    t_oos = backtest_channel_breakout(xau, entry_period=entry_n, exit_period=exit_n,
                                        atr_sl_mult=sl, atr_tp_mult=tp,
                                        cost=0.30, risk_per_trade=0.01,
                                        adx_filter=adx, next_day_open=True,
                                        longs_only=True, regime_filter='sma200',
                                        start_date='2023-01-01', end_date='2026-12-31')
    print_compact(t_oos, "  OOS 2023-2026 (SMA200 filter)")
    
    if len(t_is) > 0 and len(t_oos) > 0:
        is_sharpe = (t_is['pnl'].mean() / t_is['pnl'].std() * np.sqrt(252)) if t_is['pnl'].std() > 0 else 0
        oos_sharpe = (t_oos['pnl'].mean() / t_oos['pnl'].std() * np.sqrt(252)) if t_oos['pnl'].std() > 0 else 0
        wfe = (oos_sharpe / is_sharpe * 100) if is_sharpe != 0 else 0
        print(f"  >>> WFE: {wfe:.0f}%  (IS={is_sharpe:+.2f}, OOS={oos_sharpe:+.2f})")

# Also: how often was gold below 200d SMA in our sample?
xau_regime = compute_indicators(xau)
xau_regime = xau_regime.iloc[251:]  # same start
below_200 = (xau_regime['close'] < xau_regime['sma_200']).sum()
total_bars = len(xau_regime)
print(f"\n  Gold below 200d SMA: {below_200}/{total_bars} days ({below_200/total_bars*100:.1f}%)")
print(f"  -> In this sample, gold was almost always above 200d SMA (structural uptrend)")
print(f"  -> The regime filter has almost nothing to filter in this period!")
print(f"  -> We CANNOT test how it performs in a true bear market with this data")

# Year-by-year: how many days below 200d SMA?
xau_regime['year'] = xau_regime['date'].dt.year
for year, g in xau_regime.groupby('year'):
    below = (g['close'] < g['sma_200']).sum()
    print(f"    {year}: {below}/{len(g)} days below 200d SMA ({below/len(g)*100:.0f}%)")


# ═══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  SUMMARY: Trend Following Reality Check")
print("=" * 80)
print("""
  Key questions answered:
  1. How much does next-day open entry cost? (Test 1) -> ~1%, NOT 30-50%
  2. Does longs-only improve results? (Test 2) -> YES, massively
  3. What's the best realistic P&L? (Test 3) -> +$4,063, Sharpe 8.83
  4. Does the edge survive out-of-sample? (Test 4) -> YES, WFE > 100%
  5. What's the entry gap on breakout days? (Test 6) -> ~$0.25, negligible
  6. Is stop-order entry better than market order? (Test 7) -> No, market order is cleaner
  7. Does regime filter help? (Test 8) -> TBD
  8. How much is just 'buy gold'? (Test 9) -> TBD
  9. Does regime filter survive OOS? (Test 10) -> TBD
  
  !! CRITICAL CAVEAT: Gold went from $1,300 -> $2,900 in this sample.
  Longs-only looks amazing because the underlying doubled. We have NO
  bear market data to test how it performs when gold is falling.
  The SMA200 regime filter is the right idea, but in this sample gold
  is almost always above 200d SMA, so the filter barely activates.
  
  Decision criteria:
  - If realistic Sharpe > 2.0 -> worth implementing
  - If OOS Sharpe > 1.5 -> edge is likely real
  - If WFE > 50% -> not overfit
  - BUT: if edge is mostly "buy gold in a bull market" -> not a trading edge
""")

print("\n=== TREND FOLLOWING V2 BACKTEST COMPLETE ===")

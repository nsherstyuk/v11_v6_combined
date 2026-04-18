"""
Backtest: Statistical Arbitrage / Pairs Trading on G10 FX

Uses daily bars fetched from IBKR for 30 pairs.
Tests spread mean-reversion across all pair combinations.

Method:
1. For each pair combination, compute the spread (log price difference)
2. Calculate rolling z-score of the spread (20-day window)
3. Enter when |z| > 2, exit when z crosses 0, stop at |z| > 4
4. Measure returns, win rate, max DD, Sharpe

Data: C:\\nautilus0\\data\\fx_daily\\*_daily.csv (from IBKR fetch)
"""

import pandas as pd
import numpy as np
from pathlib import Path
from itertools import combinations
import warnings
warnings.filterwarnings('ignore')

DATA_DIR = Path(r"C:\nautilus0\data\fx_daily")

ALL_PAIRS = [
    "GBPUSD", "USDJPY", "USDCAD", "USDCHF", "AUDUSD", "NZDUSD",
    "AUDNZD", "AUDCAD", "AUDCHF", "AUDJPY",
    "NZDCAD", "NZDCHF", "NZDJPY",
    "GBPAUD", "GBPNZD", "GBPCAD", "GBPCHF", "GBPJPY",
    "CHFJPY", "CADCHF", "CADJPY",
    "EURUSD", "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURNZD", "EURCAD",
    "USDSEK", "USDNOK",
]

# Pairs that share the same quote currency tend to be most correlated
# Pre-defined groups for focused analysis
PAIR_GROUPS = {
    "USD-base pairs": ["GBPUSD", "USDJPY", "USDCAD", "USDCHF", "AUDUSD", "NZDUSD", "EURUSD"],
    "JPY crosses": ["USDJPY", "AUDJPY", "NZDJPY", "GBPJPY", "EURJPY", "CADJPY", "CHFJPY"],
    "AUD/NZD family": ["AUDUSD", "NZDUSD", "AUDNZD", "AUDCAD", "NZDCAD", "AUDCHF", "NZDCHF", "AUDJPY", "NZDJPY"],
    "GBP crosses": ["GBPUSD", "GBPJPY", "GBPAUD", "GBPNZD", "GBPCAD", "GBPCHF", "EURGBP"],
    "Commodity FX": ["AUDUSD", "NZDUSD", "USDCAD", "AUDNZD", "AUDCAD", "NZDCAD"],
}

# ── Load all daily data ─────────────────────────────────────────────────────
print("Loading daily data...")
prices = {}
for pair in ALL_PAIRS:
    csv_path = DATA_DIR / f"{pair}_daily.csv"
    if not csv_path.exists():
        continue
    df = pd.read_csv(csv_path, parse_dates=['date'])
    df = df.sort_values('date').reset_index(drop=True)
    prices[pair] = df.set_index('date')['close']

# Merge into single DataFrame
price_df = pd.DataFrame(prices)
price_df = price_df.dropna()
print(f"  Pairs loaded: {len(price_df.columns)}")
print(f"  Date range: {price_df.index[0]} -> {price_df.index[-1]} ({len(price_df)} days)")


# ── Compute correlations ─────────────────────────────────────────────────────
print("\nComputing pair correlations...")
log_ret = np.log(price_df / price_df.shift(1)).dropna()
corr_matrix = log_ret.corr()

# Find top correlated pairs
print("\n  Top 30 most correlated pair combinations:")
pair_corrs = []
for p1, p2 in combinations(price_df.columns, 2):
    corr = corr_matrix.loc[p1, p2]
    pair_corrs.append((p1, p2, corr))
pair_corrs.sort(key=lambda x: abs(x[2]), reverse=True)

for p1, p2, corr in pair_corrs[:30]:
    print(f"    {p1}/{p2}: {corr:+.3f}")


# ── Pairs trading backtest ───────────────────────────────────────────────────
print("\n\nRunning pairs trading backtest...")

Z_ENTRY = 2.0
Z_EXIT = 0.0
Z_STOP = 4.0
LOOKBACK = 20  # rolling window for z-score
COST_PIPS = 0.4  # round-trip cost per leg (spread + commission)

def backtest_pair(p1: str, p2: str, z_entry=Z_ENTRY, z_exit=Z_EXIT,
                  z_stop=Z_STOP, lookback=LOOKBACK) -> dict:
    """Backtest a single pair spread trade."""
    # Compute log spread
    s1 = np.log(price_df[p1])
    s2 = np.log(price_df[p2])
    spread = s1 - s2
    
    # Rolling z-score
    spread_mean = spread.rolling(lookback).mean()
    spread_std = spread.rolling(lookback).std()
    z = (spread - spread_mean) / spread_std
    
    # Determine pip value for the spread
    # For pairs like AUDUSD/NZDUSD, 1 pip = 0.0001
    # For JPY pairs, 1 pip = 0.01
    # Use the average pip size of both pairs
    def pip_size(pair):
        if "JPY" in pair:
            return 0.01
        else:
            return 0.0001
    
    pip = (pip_size(p1) + pip_size(p2)) / 2
    
    # Simulate trades
    trades = []
    position = 0  # 0=flat, 1=long spread (long p1, short p2), -1=short spread
    entry_z = 0
    entry_day = 0
    entry_spread = 0
    
    for i in range(lookback, len(z)):
        z_val = z.iloc[i]
        if pd.isna(z_val):
            continue
        
        if position == 0:
            # Entry
            if z_val > z_entry:
                position = -1  # short spread (short p1, long p2) - spread too wide
                entry_z = z_val
                entry_day = i
                entry_spread = spread.iloc[i]
            elif z_val < -z_entry:
                position = 1  # long spread (long p1, short p2) - spread too narrow
                entry_z = z_val
                entry_day = i
                entry_spread = spread.iloc[i]
        
        elif position != 0:
            # Exit conditions
            exit_reason = None
            
            # Mean reversion exit
            if position == 1 and z_val >= z_exit:
                exit_reason = "mean_revert"
            elif position == -1 and z_val <= z_exit:
                exit_reason = "mean_revert"
            
            # Stop loss
            if position == 1 and z_val < -z_stop:
                exit_reason = "stop_loss"
            elif position == -1 and z_val > z_stop:
                exit_reason = "stop_loss"
            
            if exit_reason:
                exit_spread = spread.iloc[i]
                spread_ret = position * (exit_spread - entry_spread)
                # Convert to pips
                ret_pips = spread_ret / pip
                # Subtract costs (2 legs)
                ret_pips -= COST_PIPS * 2
                
                holding_days = i - entry_day
                
                trades.append({
                    'entry_date': spread.index[entry_day],
                    'exit_date': spread.index[i],
                    'direction': 'long_spread' if position == 1 else 'short_spread',
                    'entry_z': entry_z,
                    'exit_z': z_val,
                    'ret_pips': ret_pips,
                    'holding_days': holding_days,
                    'exit_reason': exit_reason,
                    'win': ret_pips > 0,
                })
                position = 0
    
    if not trades:
        return None
    
    trades_df = pd.DataFrame(trades)
    
    # Compute portfolio returns (daily)
    # For each day, sum the P&L of open trades
    daily_ret = []
    for i in range(lookback, len(z)):
        date = z.index[i]
        day_pnl = 0
        for t in trades:
            if t['entry_date'] <= date <= t['exit_date']:
                # Pro-rata daily P&L
                day_pnl += t['ret_pips'] / t['holding_days'] if t['holding_days'] > 0 else 0
        daily_ret.append({'date': date, 'daily_pnl': day_pnl})
    
    if not daily_ret:
        return None
    
    daily_df = pd.DataFrame(daily_ret).set_index('date')
    
    # Stats
    total_trades = len(trades_df)
    win_rate = trades_df['win'].mean() * 100
    avg_ret = trades_df['ret_pips'].mean()
    avg_holding = trades_df['holding_days'].mean()
    total_ret = trades_df['ret_pips'].sum()
    stop_loss_pct = (trades_df['exit_reason'] == 'stop_loss').mean() * 100
    
    # Sharpe (annualized, using per-trade returns)
    if trades_df['ret_pips'].std() > 0:
        sharpe = (trades_df['ret_pips'].mean() / trades_df['ret_pips'].std()) * np.sqrt(252 / avg_holding) if avg_holding > 0 else 0
    else:
        sharpe = 0
    
    return {
        'p1': p1, 'p2': p2,
        'corr': corr_matrix.loc[p1, p2],
        'total_trades': total_trades,
        'win_rate': win_rate,
        'avg_ret_pips': avg_ret,
        'avg_holding_days': avg_holding,
        'total_ret_pips': total_ret,
        'sharpe': sharpe,
        'stop_loss_pct': stop_loss_pct,
        'trades': trades_df,
    }


# ── Test all pair combinations ──────────────────────────────────────────────
print("Testing all pair combinations...")
all_results = []
for p1, p2, corr in pair_corrs[:60]:  # top 60 most correlated
    result = backtest_pair(p1, p2)
    if result:
        all_results.append(result)

# Sort by Sharpe
all_results.sort(key=lambda x: x['sharpe'], reverse=True)

print(f"\n  Tested {len(all_results)} pair combinations")

# ── Results table ───────────────────────────────────────────────────────────
print("\n" + "=" * 100)
print("  PAIRS TRADING — ALL RESULTS (sorted by Sharpe)")
print("=" * 100)
print(f"  {'Pair':20s} {'Corr':>6s} {'Trades':>7s} {'WR%':>6s} {'Avg Ret':>9s} "
      f"{'Tot Ret':>9s} {'Hold D':>7s} {'Sharpe':>7s} {'SL%':>5s}")
print("-" * 100)

for r in all_results[:40]:
    print(f"  {r['p1']+'/'+r['p2']:20s} {r['corr']:+6.3f} {r['total_trades']:7d} "
          f"{r['win_rate']:6.1f} {r['avg_ret_pips']:+9.2f} {r['total_ret_pips']:+9.1f} "
          f"{r['avg_holding_days']:7.1f} {r['sharpe']:+7.3f} {r['stop_loss_pct']:5.1f}")


# ── Focus on best pairs ──────────────────────────────────────────────────────
print("\n\n" + "=" * 100)
print("  BEST PAIRS — DETAILED ANALYSIS")
print("=" * 100)

for r in all_results[:5]:
    trades = r['trades']
    print(f"\n  --- {r['p1']} / {r['p2']} (corr={r['corr']:+.3f}) ---")
    print(f"    Trades: {r['total_trades']}  WR: {r['win_rate']:.1f}%  "
          f"Avg ret: {r['avg_ret_pips']:+.2f} pips  Sharpe: {r['sharpe']:+.3f}")
    print(f"    Avg hold: {r['avg_holding_days']:.1f} days  "
          f"Stop losses: {r['stop_loss_pct']:.1f}%  Total: {r['total_ret_pips']:+.1f} pips")
    
    # Long vs short spread
    long_trades = trades[trades['direction'] == 'long_spread']
    short_trades = trades[trades['direction'] == 'short_spread']
    if len(long_trades) > 0:
        print(f"    Long spread:  N={len(long_trades)}  WR={long_trades['win'].mean()*100:.1f}%  "
              f"Avg ret={long_trades['ret_pips'].mean():+.2f} pips")
    if len(short_trades) > 0:
        print(f"    Short spread: N={len(short_trades)}  WR={short_trades['win'].mean()*100:.1f}%  "
              f"Avg ret={short_trades['ret_pips'].mean():+.2f} pips")
    
    # Year-by-year
    trades['year'] = pd.to_datetime(trades['exit_date']).dt.year
    print(f"    Year-by-year:")
    for year, group in trades.groupby('year'):
        yr_ret = group['ret_pips'].sum()
        yr_wr = group['win'].mean() * 100
        yr_n = len(group)
        print(f"      {year}: ret={yr_ret:+.1f} pips  WR={yr_wr:.0f}%  N={yr_n}")


# ── Parameter sensitivity ────────────────────────────────────────────────────
print("\n\n" + "=" * 100)
print("  PARAMETER SENSITIVITY (top 5 pairs)")
print("=" * 100)

for r in all_results[:5]:
    p1, p2 = r['p1'], r['p2']
    print(f"\n  {p1}/{p2}:")
    
    for z_entry in [1.5, 2.0, 2.5, 3.0]:
        for lb in [10, 20, 40]:
            result = backtest_pair(p1, p2, z_entry=z_entry, lookback=lb)
            if result:
                print(f"    z_entry={z_entry} lb={lb:2d}: "
                      f"trades={result['total_trades']:3d}  "
                      f"WR={result['win_rate']:5.1f}%  "
                      f"avg={result['avg_ret_pips']:+6.2f}  "
                      f"Sharpe={result['sharpe']:+6.3f}")


# ── Portfolio of pairs ───────────────────────────────────────────────────────
print("\n\n" + "=" * 100)
print("  PORTFOLIO OF TOP 5 PAIRS (equal weight)")
print("=" * 100)

top5 = all_results[:5]
# Merge all trades into a portfolio
all_trades = []
for r in top5:
    for _, t in r['trades'].iterrows():
        all_trades.append({
            'date': t['exit_date'],
            'ret_pips': t['ret_pips'] / 5,  # equal weight across 5 pairs
            'pair': r['p1'] + '/' + r['p2'],
            'holding_days': t['holding_days'],
        })

port_df = pd.DataFrame(all_trades)
port_df['date'] = pd.to_datetime(port_df['date'])
port_df = port_df.sort_values('date')

# Aggregate by date
daily_port = port_df.groupby('date')['ret_pips'].sum().reset_index()
daily_port = daily_port.set_index('date')

total_ret = daily_port['ret_pips'].sum()
avg_daily = daily_port['ret_pips'].mean()
trading_days = len(daily_port)
ann_ret_pips = avg_daily * 252
ann_vol = daily_port['ret_pips'].std() * np.sqrt(252)
sharpe = ann_ret_pips / ann_vol if ann_vol > 0 else 0

# Cumulative
cum = daily_port['ret_pips'].cumsum()
peak = cum.cummax()
dd = (cum - peak)
max_dd = dd.min()

print(f"  Pairs: {', '.join(r['p1']+'/'+r['p2'] for r in top5)}")
print(f"  Total return: {total_ret:+.1f} pips")
print(f"  Annualized return: {ann_ret_pips:+.1f} pips/year")
print(f"  Annualized vol: {ann_vol:.1f} pips/year")
print(f"  Sharpe: {sharpe:+.3f}")
print(f"  Max drawdown: {max_dd:.1f} pips")
print(f"  Trading days with activity: {trading_days}")


# ── Cointegration test ────────────────────────────────────────────────────────
print("\n\n" + "=" * 100)
print("  COINTEGRATION TEST (Engle-Granger, top 10 pairs)")
print("=" * 100)

try:
    from statsmodels.tsa.stattools import coint
    
    for r in all_results[:10]:
        p1, p2 = r['p1'], r['p2']
        s1 = price_df[p1].values
        s2 = price_df[p2].values
        
        # Take log prices
        log_s1 = np.log(s1)
        log_s2 = np.log(s2)
        
        score, pvalue, _ = coint(log_s1, log_s2)
        sig = "***" if pvalue < 0.01 else "**" if pvalue < 0.05 else "*" if pvalue < 0.1 else ""
        print(f"  {p1+'/'+p2:20s}: p-value={pvalue:.4f} {sig}  (cointegrated={'YES' if pvalue<0.05 else 'no'})")
except ImportError:
    print("  statsmodels not installed — skipping cointegration test")
    print("  Install with: pip install statsmodels")


# ── Key considerations ───────────────────────────────────────────────────────
print("\n\n" + "=" * 100)
print("  KEY CONSIDERATIONS")
print("=" * 100)
print("""
  1. DAILY DATA LIMITATIONS:
     - Assumes execution at daily close (unrealistic for 24h FX)
     - Misses intraday spread behavior
     - Next step: re-test winners on 1-min data for realistic execution

  2. COSTS:
     - Each trade has 2 legs, each costing ~0.4 pips round-trip
     - Total cost per spread trade: ~0.8 pips
     - Already deducted from results above

  3. CARRY COST:
     - Holding a spread position overnight incurs carry (swap) costs
     - Long AUD/Short NZD: you earn AUD rate - pay NZD rate (positive carry currently)
     - Long EUR/Short GBP: you pay EUR rate - earn GBP rate (negative carry currently)
     - This is NOT included in the backtest — overstates returns for negative-carry pairs

  4. REGIME RISK:
     - Spread mean-reversion assumes the relationship is stable
     - During crises (COVID, SNB unpeg), spreads can gap and NOT revert
     - Stop-loss at |z|>4 helps but may not be enough

  5. CAPITAL REQUIREMENTS:
     - Each spread trade = 2 positions of ~$100K each
     - IBKR margin: 2-3% per position = $4-6K per spread trade
     - Portfolio of 5 pairs: ~$20-30K margin on $50K account
""")

print("\n=== BACKTEST COMPLETE ===")

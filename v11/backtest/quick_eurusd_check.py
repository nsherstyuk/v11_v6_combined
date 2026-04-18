"""Quick EURUSD data integrity check."""
import pandas as pd

df = pd.read_csv(r'C:\nautilus0\data\1m_csv\eurusd_1m_tick.csv',
                 usecols=['timestamp', 'open', 'high', 'low', 'close'])
df['timestamp'] = pd.to_datetime(df['timestamp'])

print(f"Rows: {len(df):,}")
print(f"Date range: {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}")

# EURUSD historically: 0.95 - 1.60
print(f"\nClose range: {df['close'].min():.4f} -> {df['close'].max():.4f}")
print(f"Close > 1.60 (impossible for EURUSD): {(df['close'] > 1.60).sum()}")
print(f"Close > 2.00: {(df['close'] > 2.00).sum()}")
print(f"Close < 0.90: {(df['close'] < 0.90).sum()}")

# Show outliers
outliers = df[df['close'] > 2.0]
if len(outliers) > 0:
    print(f"\nOutlier rows (close > 2.0):")
    print(outliers.head(20).to_string())

# Check for price jumps > 500 pips in 1 minute
df['close_prev'] = df['close'].shift(1)
df['pip_change'] = (df['close'] - df['close_prev']) / 0.0001
big_jumps = df[df['pip_change'].abs() > 500]
print(f"\n1-min jumps > 500 pips: {len(big_jumps)}")
if len(big_jumps) > 0:
    print(big_jumps[['timestamp', 'close', 'close_prev', 'pip_change']].head(20).to_string())

# Compare with GBPUSD
gbp = pd.read_csv(r'C:\nautilus0\data\1m_csv\gbpusd_1m_tick.csv',
                  usecols=['timestamp', 'close'])
gbp['timestamp'] = pd.to_datetime(gbp['timestamp'])
print(f"\nGBPUSD close range: {gbp['close'].min():.4f} -> {gbp['close'].max():.4f}")
print(f"GBPUSD rows: {len(gbp):,}")

# Check if the 'Unnamed: 0' column exists (sign of re-save)
eur_full = pd.read_csv(r'C:\nautilus0\data\1m_csv\eurusd_1m_tick.csv', nrows=1)
print(f"\nEURUSD columns: {list(eur_full.columns)}")
print(f"Has 'Unnamed: 0' (re-saved indicator): {'Unnamed: 0' in eur_full.columns}")

gbp_full = pd.read_csv(r'C:\nautilus0\data\1m_csv\gbpusd_1m_tick.csv', nrows=1)
print(f"GBPUSD columns: {list(gbp_full.columns)}")
print(f"Has 'Unnamed: 0': {'Unnamed: 0' in gbp_full.columns}")

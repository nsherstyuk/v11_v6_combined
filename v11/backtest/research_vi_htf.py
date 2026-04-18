"""
Research: Volume Imbalance on HIGHER TIMEFRAMES (5m, 15m, 60m)
Aggregates 1-min tick data into higher TF bars, then re-runs Q5/Q6/Q8.
"""
import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

DATA_DIR = Path(r"C:\nautilus0\data\1m_csv")

PAIRS = {
    "GBPUSD": "gbpusd_1m_tick.csv",
    "USDJPY": "usdjpy_1m_tick.csv",
    "USDCAD": "usdcad_1m_tick.csv",
    "USDCHF": "usdchf_1m_tick.csv",
    "AUDUSD": "audusd_1m_tick.csv",
    "NZDUSD": "nzdusd_1m_tick.csv",
    "XAUUSD": "xauusd_1m_tick.csv",
}

PIP_MULT = {
    "GBPUSD": 10000, "USDJPY": 100, "USDCAD": 10000,
    "USDCHF": 10000, "AUDUSD": 10000, "NZDUSD": 10000,
    "XAUUSD": 100,
}

TF_LABELS = {'5min': '5min', '15min': '15min', '60min': '60min'}


def aggregate_bars(df_1m: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Aggregate 1-min bars into higher timeframe bars.
    
    OHLC: standard aggregation.
    Volumes: sum buy_volume, sell_volume, total_volume, tick_count.
    buy_ratio and vol_imbalance: recalculated from aggregated volumes.
    """
    df = df_1m.set_index('timestamp').sort_index()

    agg_map = {
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'buy_volume': 'sum',
        'sell_volume': 'sum',
        'total_volume': 'sum',
        'tick_count': 'sum',
    }

    df_tf = df.resample(timeframe).agg(agg_map).dropna()

    # Recalculate derived volume metrics from aggregated volumes
    df_tf['vol_imbalance'] = df_tf['sell_volume'] - df_tf['buy_volume']
    df_tf['buy_ratio'] = df_tf['buy_volume'] / df_tf['total_volume'].replace(0, np.nan)
    df_tf['avg_spread'] = np.nan  # not meaningful aggregated
    df_tf['max_spread'] = np.nan

    df_tf = df_tf.reset_index()
    return df_tf


def analyze_pair_tf(pair_name: str, df: pd.DataFrame, tf_label: str):
    """Run Q5/Q6/Q8 on one pair at one timeframe."""
    pip_m = PIP_MULT.get(pair_name, 10000)

    df['price_chg'] = df['close'].diff()
    df['price_chg_pips'] = df['price_chg'] * pip_m
    df['direction'] = np.sign(df['price_chg_pips'])
    df['prev_direction'] = df['direction'].shift(1)
    df['reversal'] = (df['direction'] != df['prev_direction']) & (df['prev_direction'] != 0)
    df['reversal_up'] = df['reversal'] & (df['direction'] == 1)
    df['reversal_down'] = df['reversal'] & (df['direction'] == -1)
    df['buy_ratio_chg'] = df['buy_ratio'].diff()

    # Cumulative delta (daily reset)
    df['date'] = df['timestamp'].dt.date
    df['delta'] = df['vol_imbalance'].groupby(df['date']).cumsum()

    # Divergence
    df['price_sma10'] = df['close'].rolling(10).mean()
    df['delta_sma10'] = df['delta'].rolling(10).mean()
    df['price_trend_up'] = df['price_sma10'] > df['price_sma10'].shift(10)
    df['delta_trend_down'] = df['delta_sma10'] > df['delta_sma10'].shift(10)
    df['price_trend_down'] = df['price_sma10'] < df['price_sma10'].shift(10)
    df['delta_trend_up'] = df['delta_sma10'] < df['delta_sma10'].shift(10)
    bearish_div = df['price_trend_up'] & df['delta_trend_down']
    bullish_div = df['price_trend_down'] & df['delta_trend_up']

    results = {}

    # ── Q5: Extreme buy_ratio (exhaustion) ──────────────────────────────────
    for threshold in [0.65, 0.70, 0.75, 0.80]:
        for la in [1, 2, 3, 5, 10]:
            future_ret = df['price_chg_pips'].rolling(la).sum().shift(-la)
            ext_buy = df['buy_ratio'] > threshold
            ext_sell = df['buy_ratio'] < (1 - threshold)
            results[('Q5', threshold, la)] = {
                'ext_buy_ret': future_ret[ext_buy].mean() if ext_buy.sum() > 100 else np.nan,
                'ext_buy_wr': (future_ret[ext_buy] > 0).mean() * 100 if ext_buy.sum() > 100 else np.nan,
                'ext_buy_n': int(ext_buy.sum()),
                'ext_sell_ret': future_ret[ext_sell].mean() if ext_sell.sum() > 100 else np.nan,
                'ext_sell_wr': (future_ret[ext_sell] > 0).mean() * 100 if ext_sell.sum() > 100 else np.nan,
                'ext_sell_n': int(ext_sell.sum()),
            }

    # ── Q6: vol_imbalance quintile ─────────────────────────────────────────
    for la in [1, 2, 3, 5, 10]:
        future_ret = df['price_chg_pips'].rolling(la).sum().shift(-la)
        valid = df['vol_imbalance'].notna() & future_ret.notna()
        if valid.sum() < 1000:
            results[('Q6', la)] = {'corr': np.nan, 'spread': np.nan}
            continue
        corr = df.loc[valid, 'vol_imbalance'].corr(future_ret[valid])
        df_temp = pd.DataFrame({'vi': df['vol_imbalance'], 'fr': future_ret}).dropna()
        if len(df_temp) < 1000:
            results[('Q6', la)] = {'corr': np.nan, 'spread': np.nan}
            continue
        df_temp['q'] = pd.qcut(df_temp['vi'], 5, labels=False)
        q_means = df_temp.groupby('q')['fr'].mean()
        results[('Q6', la)] = {
            'corr': corr,
            'Q1_buy': q_means.get(0, np.nan),
            'Q5_sell': q_means.get(4, np.nan),
            'spread': q_means.get(0, np.nan) - q_means.get(4, np.nan),
        }

    # ── Q8: Divergence ─────────────────────────────────────────────────────
    for la in [1, 2, 3, 5, 10]:
        future_ret = df['price_chg_pips'].rolling(la).sum().shift(-la)
        ret_bull = future_ret[bullish_div].mean() if bullish_div.sum() > 100 else np.nan
        wr_bull = (future_ret[bullish_div] > 0).mean() * 100 if bullish_div.sum() > 100 else np.nan
        ret_bear = future_ret[bearish_div].mean() if bearish_div.sum() > 100 else np.nan
        wr_bear = (future_ret[bearish_div] < 0).mean() * 100 if bearish_div.sum() > 100 else np.nan
        results[('Q8', la)] = {
            'bullish_ret': ret_bull,
            'bullish_wr': wr_bull,
            'bullish_n': int(bullish_div.sum()),
            'bearish_ret': ret_bear,
            'bearish_wr': wr_bear,
            'bearish_n': int(bearish_div.sum()),
        }

    return results


# ── Run all pairs × all timeframes ──────────────────────────────────────────
all_results = {}  # (pair, tf) → results dict

for pair_name, filename in PAIRS.items():
    path = DATA_DIR / filename
    print(f"\nLoading {pair_name}...")
    df_1m = pd.read_csv(path, parse_dates=['timestamp'])
    df_1m = df_1m.sort_values('timestamp').reset_index(drop=True)
    print(f"  1-min rows: {len(df_1m):,}")

    for tf_code, tf_label in TF_LABELS.items():
        print(f"  Aggregating to {tf_label}...")
        df_tf = aggregate_bars(df_1m, tf_code)
        print(f"  {tf_label} rows: {len(df_tf):,}")

        res = analyze_pair_tf(pair_name, df_tf, tf_label)
        all_results[(pair_name, tf_label)] = res

# ── Comparative summary tables ──────────────────────────────────────────────
print("\n\n" + "="*80)
print("  COMPARATIVE SUMMARY: HIGHER TIMEFRAMES")
print("="*80)

# ── Table 1: Q5 Extreme Exhaustion across TFs ──────────────────────────────
print("\n\n### Q5: Extreme Exhaustion (buy_ratio < 0.20 → price bounces UP)")
print("      Shows: avg return in pips / win rate / sample size")
print()

for threshold in [0.70, 0.80]:
    print(f"\n  --- Threshold: buy_ratio < {1-threshold:.2f} (sell exhaustion) ---")
    header = f"{'Pair':<10}"
    for tf_label in TF_LABELS.values():
        header += f" | {tf_label:>8} ret  WR%    N"
    print(header)
    print("-" * len(header))

    for pair_name in PAIRS:
        row = f"{pair_name:<10}"
        for tf_label in TF_LABELS.values():
            res = all_results.get((pair_name, tf_label), {})
            key = ('Q5', threshold, 3)  # 3-bar ahead
            if key in res and not np.isnan(res[key].get('ext_sell_ret', np.nan)):
                r = res[key]
                row += f" | {r['ext_sell_ret']:+8.3f} {r['ext_sell_wr']:5.1f} {r['ext_sell_n']:5,}"
            else:
                row += f" | {'n/a':>20}"
        print(row)

# Also show buy-side exhaustion
for threshold in [0.70, 0.80]:
    print(f"\n  --- Threshold: buy_ratio > {threshold:.2f} (buy exhaustion → price drops) ---")
    header = f"{'Pair':<10}"
    for tf_label in TF_LABELS.values():
        header += f" | {tf_label:>8} ret  WR%    N"
    print(header)
    print("-" * len(header))

    for pair_name in PAIRS:
        row = f"{pair_name:<10}"
        for tf_label in TF_LABELS.values():
            res = all_results.get((pair_name, tf_label), {})
            key = ('Q5', threshold, 3)
            if key in res and not np.isnan(res[key].get('ext_buy_ret', np.nan)):
                r = res[key]
                row += f" | {r['ext_buy_ret']:+8.3f} {r['ext_buy_wr']:5.1f} {r['ext_buy_n']:5,}"
            else:
                row += f" | {'n/a':>20}"
        print(row)

# ── Table 2: Q6 Quintile spread across TFs ─────────────────────────────────
print("\n\n### Q6: vol_imbalance Quintile Spread (Q1 buy - Q5 sell), 3-bar ahead")
print()

header = f"{'Pair':<10}"
for tf_label in TF_LABELS.values():
    header += f" | {tf_label:>8} corr   spread"
print(header)
print("-" * len(header))

for pair_name in PAIRS:
    row = f"{pair_name:<10}"
    for tf_label in TF_LABELS.values():
        res = all_results.get((pair_name, tf_label), {})
        key = ('Q6', 3)
        if key in res and not np.isnan(res[key].get('corr', np.nan)):
            r = res[key]
            row += f" | {r['corr']:+8.4f} {r['spread']:+8.3f}"
        else:
            row += f" | {'n/a':>18}"
    print(row)

# ── Table 3: Q8 Divergence across TFs ───────────────────────────────────────
print("\n\n### Q8: Bullish Divergence (price↓ + buying behind), 3-bar ahead")
print()

header = f"{'Pair':<10}"
for tf_label in TF_LABELS.values():
    header += f" | {tf_label:>8} ret  WR%    N"
print(header)
print("-" * len(header))

for pair_name in PAIRS:
    row = f"{pair_name:<10}"
    for tf_label in TF_LABELS.values():
        res = all_results.get((pair_name, tf_label), {})
        key = ('Q8', 3)
        if key in res and not np.isnan(res[key].get('bullish_ret', np.nan)):
            r = res[key]
            row += f" | {r['bullish_ret']:+8.3f} {r['bullish_wr']:5.1f} {r['bullish_n']:5,}"
        else:
            row += f" | {'n/a':>20}"
    print(row)

print("\n\n### Q8: Bearish Divergence (price↑ + selling behind), 3-bar ahead")
print()

header = f"{'Pair':<10}"
for tf_label in TF_LABELS.values():
    header += f" | {tf_label:>8} ret  WR%    N"
print(header)
print("-" * len(header))

for pair_name in PAIRS:
    row = f"{pair_name:<10}"
    for tf_label in TF_LABELS.values():
        res = all_results.get((pair_name, tf_label), {})
        key = ('Q8', 3)
        if key in res and not np.isnan(res[key].get('bearish_ret', np.nan)):
            r = res[key]
            row += f" | {r['bearish_ret']:+8.3f} {r['bearish_wr']:5.1f} {r['bearish_n']:5,}"
        else:
            row += f" | {'n/a':>20}"
    print(row)

# ── Best-of: show strongest signals at each TF ─────────────────────────────
print("\n\n### BEST SIGNALS BY TIMEFRAME")
print()

for tf_label in TF_LABELS.values():
    print(f"\n  === {tf_label} ===")
    
    # Best sell-exhaustion
    best_sell = None
    best_sell_wr = 0
    for pair_name in PAIRS:
        for threshold in [0.65, 0.70, 0.75, 0.80]:
            for la in [1, 2, 3, 5]:
                res = all_results.get((pair_name, tf_label), {})
                key = ('Q5', threshold, la)
                if key in res:
                    r = res[key]
                    if not np.isnan(r.get('ext_sell_wr', np.nan)) and r['ext_sell_wr'] > best_sell_wr and r['ext_sell_n'] > 200:
                        best_sell_wr = r['ext_sell_wr']
                        best_sell = (pair_name, threshold, la, r)
    
    if best_sell:
        p, thr, la, r = best_sell
        print(f"    Best sell-exhaustion: {p} buy_ratio<{1-thr:.2f} LA={la} → ret={r['ext_sell_ret']:+.3f} WR={r['ext_sell_wr']:.1f}% N={r['ext_sell_n']:,}")

    # Best bullish divergence
    best_bull = None
    best_bull_wr = 0
    for pair_name in PAIRS:
        for la in [1, 2, 3, 5]:
            res = all_results.get((pair_name, tf_label), {})
            key = ('Q8', la)
            if key in res:
                r = res[key]
                if not np.isnan(r.get('bullish_wr', np.nan)) and r['bullish_wr'] > best_bull_wr and r['bullish_n'] > 200:
                    best_bull_wr = r['bullish_wr']
                    best_bull = (pair_name, la, r)
    
    if best_bull:
        p, la, r = best_bull
        print(f"    Best bull-divergence: {p} LA={la} → ret={r['bullish_ret']:+.3f} WR={r['bullish_wr']:.1f}% N={r['bullish_n']:,}")

    # Best quintile spread
    best_spread = None
    best_spread_val = 0
    for pair_name in PAIRS:
        for la in [1, 2, 3, 5]:
            res = all_results.get((pair_name, tf_label), {})
            key = ('Q6', la)
            if key in res:
                r = res[key]
                if not np.isnan(r.get('spread', np.nan)) and abs(r['spread']) > abs(best_spread_val):
                    best_spread_val = r['spread']
                    best_spread = (pair_name, la, r)
    
    if best_spread:
        p, la, r = best_spread
        print(f"    Best quintile spread: {p} LA={la} → corr={r['corr']:+.4f} spread={r['spread']:+.3f}")

print("\n=== HIGHER TIMEFRAME ANALYSIS COMPLETE ===")

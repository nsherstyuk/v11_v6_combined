"""
Research: Volume Imbalance vs Price Reversal — ALL available pairs (except EURUSD).

Runs Q1-Q8 analysis for each pair and produces a comparative summary.
"""
import pandas as pd
import numpy as np
from pathlib import Path
import sys

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

# Pip multipliers (JPY pairs use 100, others 10000, XAUUSD uses 1 for $ terms)
PIP_MULT = {
    "GBPUSD": 10000, "USDJPY": 100, "USDCAD": 10000,
    "USDCHF": 10000, "AUDUSD": 10000, "NZDUSD": 10000,
    "XAUUSD": 100,  # gold: 1 point = 0.01, so 100 = 1 full point
}

def analyze_pair(pair_name: str, filename: str):
    """Run full volume imbalance analysis for one pair."""
    path = DATA_DIR / filename
    if not path.exists():
        print(f"  SKIP: {path} not found")
        return None

    print(f"\n{'='*70}")
    print(f"  {pair_name}")
    print(f"{'='*70}")

    df = pd.read_csv(path, parse_dates=['timestamp'])
    df = df.sort_values('timestamp').reset_index(drop=True)
    print(f"  Rows: {len(df):,}  |  {df.timestamp.iloc[0]} → {df.timestamp.iloc[-1]}")

    pip_m = PIP_MULT.get(pair_name, 10000)

    # ── Derived features ────────────────────────────────────────────────────
    df['price_chg'] = df['close'].diff()
    df['price_chg_pips'] = df['price_chg'] * pip_m
    df['direction'] = np.sign(df['price_chg_pips'])
    df['prev_direction'] = df['direction'].shift(1)
    df['reversal'] = (df['direction'] != df['prev_direction']) & (df['prev_direction'] != 0)
    df['reversal_up'] = df['reversal'] & (df['direction'] == 1)
    df['reversal_down'] = df['reversal'] & (df['direction'] == -1)
    df['buy_ratio_chg'] = df['buy_ratio'].diff()
    df['vol_imbalance_chg'] = df['vol_imbalance'].diff()

    # Balance flip
    df['buy_ratio_above50'] = df['buy_ratio'] > 0.5
    df['balance_flip'] = df['buy_ratio_above50'] != df['buy_ratio_above50'].shift(1)

    # Cumulative delta
    df['date'] = df['timestamp'].dt.date
    df['delta'] = df['vol_imbalance'].groupby(df['date']).cumsum()

    # SMA for divergence
    df['price_sma10'] = df['close'].rolling(10).mean()
    df['delta_sma10'] = df['delta'].rolling(10).mean()
    df['price_trend_up'] = df['price_sma10'] > df['price_sma10'].shift(10)
    df['delta_trend_down'] = df['delta_sma10'] > df['delta_sma10'].shift(10)
    df['price_trend_down'] = df['price_sma10'] < df['price_sma10'].shift(10)
    df['delta_trend_up'] = df['delta_sma10'] < df['delta_sma10'].shift(10)
    bearish_div = df['price_trend_up'] & df['delta_trend_down']
    bullish_div = df['price_trend_down'] & df['delta_trend_up']

    results = {}

    # ── Q1: buy_ratio change → reversal ─────────────────────────────────────
    q1 = {}
    for la in [1, 3, 5, 10]:
        future_rev = df['reversal'].shift(-la)
        br_up = df['buy_ratio_chg'] > 0
        br_down = df['buy_ratio_chg'] < 0
        future_rev_up = df['reversal_up'].shift(-la)
        future_rev_down = df['reversal_down'].shift(-la)
        q1[la] = {
            'baseline_rev': future_rev.mean() * 100,
            'rev_after_br_up': future_rev[br_up].mean() * 100,
            'rev_after_br_down': future_rev[br_down].mean() * 100,
            'rev_up_after_br_up': future_rev_up[br_up].mean() * 100,
            'rev_down_after_br_down': future_rev_down[br_down].mean() * 100,
        }
    results['Q1'] = q1

    # ── Q2: reversal → buy_ratio change ─────────────────────────────────────
    q2 = {}
    for la in [1, 3, 5]:
        future_br = df['buy_ratio_chg'].shift(-la)
        q2[la] = {
            'chg_after_rev_up': future_br[df['reversal_up']].mean(),
            'chg_after_rev_down': future_br[df['reversal_down']].mean(),
        }
    results['Q2'] = q2

    # ── Q5: Extreme buy_ratio ───────────────────────────────────────────────
    q5 = {}
    for threshold in [0.70, 0.80]:
        for la in [5, 10, 20]:
            future_ret = df['price_chg_pips'].rolling(la).sum().shift(-la)
            ext_buy = df['buy_ratio'] > threshold
            ext_sell = df['buy_ratio'] < (1 - threshold)
            q5[(threshold, la)] = {
                'ext_buy_ret': future_ret[ext_buy].mean(),
                'ext_buy_wr': (future_ret[ext_buy] > 0).mean() * 100 if ext_buy.sum() > 0 else 0,
                'ext_buy_n': ext_buy.sum(),
                'ext_sell_ret': future_ret[ext_sell].mean(),
                'ext_sell_wr': (future_ret[ext_sell] > 0).mean() * 100 if ext_sell.sum() > 0 else 0,
                'ext_sell_n': ext_sell.sum(),
            }
    results['Q5'] = q5

    # ── Q6: vol_imbalance quintile ──────────────────────────────────────────
    q6 = {}
    for la in [5, 10]:
        future_ret = df['price_chg_pips'].rolling(la).sum().shift(-la)
        valid = df['vol_imbalance'].notna() & future_ret.notna()
        corr = df.loc[valid, 'vol_imbalance'].corr(future_ret[valid])
        df_temp = pd.DataFrame({'vi': df['vol_imbalance'], 'fr': future_ret}).dropna()
        df_temp['q'] = pd.qcut(df_temp['vi'], 5, labels=False)
        q_means = df_temp.groupby('q')['fr'].mean()
        q6[la] = {
            'corr': corr,
            'Q1_buy': q_means.get(0, 0),
            'Q5_sell': q_means.get(4, 0),
            'spread': q_means.get(0, 0) - q_means.get(4, 0),
        }
    results['Q6'] = q6

    # ── Q8: Divergence ──────────────────────────────────────────────────────
    q8 = {}
    for la in [10, 30, 60]:
        future_ret = df['price_chg_pips'].rolling(la).sum().shift(-la)
        ret_bull = future_ret[bullish_div].mean() if bullish_div.sum() > 0 else 0
        wr_bull = (future_ret[bullish_div] > 0).mean() * 100 if bullish_div.sum() > 0 else 0
        ret_bear = future_ret[bearish_div].mean() if bearish_div.sum() > 0 else 0
        wr_bear = (future_ret[bearish_div] < 0).mean() * 100 if bearish_div.sum() > 0 else 0
        q8[la] = {
            'bullish_ret': ret_bull,
            'bullish_wr': wr_bull,
            'bullish_n': bullish_div.sum(),
            'bearish_ret': ret_bear,
            'bearish_wr': wr_bear,
            'bearish_n': bearish_div.sum(),
        }
    results['Q8'] = q8

    # ── Print summary ───────────────────────────────────────────────────────
    print(f"\n  --- Q1: buy_ratio change → reversal (5-bar ahead) ---")
    q1_5 = q1[5]
    print(f"    Baseline rev: {q1_5['baseline_rev']:.2f}%  |  After br↑: {q1_5['rev_after_br_up']:.2f}%  |  After br↓: {q1_5['rev_after_br_down']:.2f}%")
    print(f"    Rev-UP after br↑: {q1_5['rev_up_after_br_up']:.2f}%  |  Rev-DOWN after br↓: {q1_5['rev_down_after_br_down']:.2f}%")

    print(f"\n  --- Q2: reversal → buy_ratio change (1-bar ahead) ---")
    q2_1 = q2[1]
    print(f"    After rev-UP, br chg: {q2_1['chg_after_rev_up']:+.5f}  |  After rev-DOWN, br chg: {q2_1['chg_after_rev_down']:+.5f}")

    print(f"\n  --- Q5: Extreme buy_ratio (exhaustion) ---")
    for threshold in [0.70, 0.80]:
        for la in [5, 20]:
            r = q5[(threshold, la)]
            print(f"    Thr={threshold} LA={la}: BUY ret={r['ext_buy_ret']:+.3f} WR={r['ext_buy_wr']:.1f}% N={r['ext_buy_n']:,}  |  SELL ret={r['ext_sell_ret']:+.3f} WR={r['ext_sell_wr']:.1f}% N={r['ext_sell_n']:,}")

    print(f"\n  --- Q6: vol_imbalance quintile spread ---")
    for la in [5, 10]:
        r = q6[la]
        print(f"    LA={la}: corr={r['corr']:+.4f}  Q1(buy)={r['Q1_buy']:+.3f}  Q5(sell)={r['Q5_sell']:+.3f}  spread={r['spread']:+.3f}")

    print(f"\n  --- Q8: Price/Delta divergence ---")
    for la in [10, 30, 60]:
        r = q8[la]
        print(f"    LA={la}: Bullish ret={r['bullish_ret']:+.3f} WR={r['bullish_wr']:.1f}% N={r['bullish_n']:,}  |  Bearish ret={r['bearish_ret']:+.3f} WR={r['bearish_wr']:.1f}% N={r['bearish_n']:,}")

    return results


# ── Run all pairs ────────────────────────────────────────────────────────────
all_results = {}
for pair_name, filename in PAIRS.items():
    result = analyze_pair(pair_name, filename)
    if result is not None:
        all_results[pair_name] = result

# ── Comparative summary table ────────────────────────────────────────────────
print("\n\n" + "="*70)
print("  COMPARATIVE SUMMARY ACROSS ALL PAIRS")
print("="*70)

print("\n--- Q5: Extreme Exhaustion (buy_ratio > 0.80, 5-bar ahead) ---")
print(f"{'Pair':<10} {'BUY ret':>10} {'BUY WR':>8} {'SELL ret':>10} {'SELL WR':>8} {'N_buy':>10} {'N_sell':>10}")
for pair, r in all_results.items():
    q = r['Q5'][(0.80, 5)]
    print(f"{pair:<10} {q['ext_buy_ret']:+10.3f} {q['ext_buy_wr']:8.1f} {q['ext_sell_ret']:+10.3f} {q['ext_sell_wr']:8.1f} {q['ext_buy_n']:10,} {q['ext_sell_n']:10,}")

print("\n--- Q6: vol_imbalance quintile spread Q1-Q5 (5-bar ahead) ---")
print(f"{'Pair':<10} {'Corr':>8} {'Q1(buy)':>10} {'Q5(sell)':>10} {'Spread':>10}")
for pair, r in all_results.items():
    q = r['Q6'][5]
    print(f"{pair:<10} {q['corr']:+8.4f} {q['Q1_buy']:+10.3f} {q['Q5_sell']:+10.3f} {q['spread']:+10.3f}")

print("\n--- Q8: Bullish Divergence (price↓ + buying behind, 30-bar ahead) ---")
print(f"{'Pair':<10} {'Ret(pips)':>10} {'WR':>8} {'N':>10}   |   Bearish: Ret(pips)  WR      N")
for pair, r in all_results.items():
    bull = r['Q8'][30]
    bear = r['Q8'][30]
    print(f"{pair:<10} {bull['bullish_ret']:+10.3f} {bull['bullish_wr']:8.1f} {bull['bullish_n']:10,}   |   {bear['bearish_ret']:+10.3f} {bear['bearish_wr']:8.1f} {bear['bearish_n']:10,}")

print("\n=== ALL PAIRS ANALYSIS COMPLETE ===")

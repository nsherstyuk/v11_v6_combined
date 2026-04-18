"""
Backtest: Carry + Momentum Blend Strategy on G10 FX (with cross-pairs)

Uses daily bars fetched from IBKR for 30 pairs (USD-base + crosses).
Cross-pairs remove the dominant USD factor and provide independent return streams.

Carry signal: approximate interest rate differential from central bank policy rates.
Momentum signal: 6-month log return.

Portfolio: Long top-N, Short bottom-N pairs by combined score, monthly rebalance.
Position sizing: volatility-targeted.
"""
import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

DATA_DIR = Path(r"C:\nautilus0\data\fx_daily")

# All 30 pairs fetched from IBKR
ALL_PAIRS = [
    # USD-base (6)
    "GBPUSD", "USDJPY", "USDCAD", "USDCHF", "AUDUSD", "NZDUSD",
    # AUD cross family (4)
    "AUDNZD", "AUDCAD", "AUDCHF", "AUDJPY",
    # NZD cross family (3)
    "NZDCAD", "NZDCHF", "NZDJPY",
    # GBP cross family (5)
    "GBPAUD", "GBPNZD", "GBPCAD", "GBPCHF", "GBPJPY",
    # CHF/CAD cross family (3)
    "CHFJPY", "CADCHF", "CADJPY",
    # EUR family (7)
    "EURUSD", "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURNZD", "EURCAD",
    # Scandi (2)
    "USDSEK", "USDNOK",
]

# Currencies involved
CURRENCIES = ["USD", "GBP", "JPY", "CAD", "CHF", "AUD", "NZD", "EUR", "SEK", "NOK"]

# ── Approximate central bank policy rates (annual %) by year ─────────────────
POLICY_RATES = {
    2017: {"USD": 1.25, "GBP": 0.50, "JPY": -0.10, "CAD": 1.00, "CHF": -0.75, "AUD": 1.50, "NZD": 1.75, "EUR": 0.00, "SEK": -0.50, "NOK": 0.50},
    2018: {"USD": 2.25, "GBP": 0.75, "JPY": -0.10, "CAD": 1.75, "CHF": -0.50, "AUD": 1.50, "NZD": 1.75, "EUR": 0.00, "SEK": -0.25, "NOK": 0.75},
    2019: {"USD": 1.75, "GBP": 0.75, "JPY": -0.10, "CAD": 1.75, "CHF": -0.50, "AUD": 0.75, "NZD": 1.00, "EUR": 0.00, "SEK": 0.00, "NOK": 0.50},
    2020: {"USD": 0.25, "GBP": 0.10, "JPY": -0.10, "CAD": 0.25, "CHF": -0.75, "AUD": 0.10, "NZD": 0.25, "EUR": 0.00, "SEK": 0.00, "NOK": 0.25},
    2021: {"USD": 0.25, "GBP": 0.25, "JPY": -0.10, "CAD": 0.25, "CHF": -0.75, "AUD": 0.10, "NZD": 0.50, "EUR": 0.00, "SEK": 0.00, "NOK": 0.25},
    2022: {"USD": 4.25, "GBP": 3.50, "JPY": -0.10, "CAD": 4.25, "CHF": 1.00, "AUD": 3.10, "NZD": 4.25, "EUR": 2.00, "SEK": 2.50, "NOK": 2.75},
    2023: {"USD": 5.25, "GBP": 5.25, "JPY": -0.10, "CAD": 5.00, "CHF": 1.75, "AUD": 4.35, "NZD": 5.50, "EUR": 4.00, "SEK": 3.75, "NOK": 3.75},
    2024: {"USD": 4.50, "GBP": 4.75, "JPY": 0.25, "CAD": 3.25, "CHF": 1.50, "AUD": 4.35, "NZD": 4.25, "EUR": 3.50, "SEK": 2.50, "NOK": 3.00},
    2025: {"USD": 4.25, "GBP": 4.50, "JPY": 0.50, "CAD": 2.75, "CHF": 0.50, "AUD": 3.85, "NZD": 3.50, "EUR": 2.50, "SEK": 2.00, "NOK": 2.50},
    2026: {"USD": 4.25, "GBP": 4.50, "JPY": 0.50, "CAD": 2.75, "CHF": 0.50, "AUD": 3.85, "NZD": 3.50, "EUR": 2.50, "SEK": 2.00, "NOK": 2.50},
}


def parse_pair(pair: str):
    """Parse pair name into (base, quote) currencies."""
    # Known pairs - manual mapping for reliability
    PAIR_MAP = {
        "GBPUSD": ("GBP", "USD"), "USDJPY": ("USD", "JPY"), "USDCAD": ("USD", "CAD"),
        "USDCHF": ("USD", "CHF"), "AUDUSD": ("AUD", "USD"), "NZDUSD": ("NZD", "USD"),
        "AUDNZD": ("AUD", "NZD"), "AUDCAD": ("AUD", "CAD"), "AUDCHF": ("AUD", "CHF"),
        "AUDJPY": ("AUD", "JPY"), "NZDCAD": ("NZD", "CAD"), "NZDCHF": ("NZD", "CHF"),
        "NZDJPY": ("NZD", "JPY"), "GBPAUD": ("GBP", "AUD"), "GBPNZD": ("GBP", "NZD"),
        "GBPCAD": ("GBP", "CAD"), "GBPCHF": ("GBP", "CHF"), "GBPJPY": ("GBP", "JPY"),
        "CHFJPY": ("CHF", "JPY"), "CADCHF": ("CAD", "CHF"), "CADJPY": ("CAD", "JPY"),
        "EURUSD": ("EUR", "USD"), "EURGBP": ("EUR", "GBP"), "EURJPY": ("EUR", "JPY"),
        "EURCHF": ("EUR", "CHF"), "EURAUD": ("EUR", "AUD"), "EURNZD": ("EUR", "NZD"),
        "EURCAD": ("EUR", "CAD"), "USDSEK": ("USD", "SEK"), "USDNOK": ("USD", "NOK"),
    }
    return PAIR_MAP.get(pair, (pair[:3], pair[3:]))


def get_carry_for_pair(pair: str, year: int) -> float:
    """Get approximate annual carry (interest rate differential) for a pair.
    
    Positive carry = you earn net interest for holding the pair long.
    For BASE/QUOTE: carry = base_rate - quote_rate
    """
    rates = POLICY_RATES.get(year, POLICY_RATES[2026])
    base, quote = parse_pair(pair)
    base_rate = rates.get(base, 0.0)
    quote_rate = rates.get(quote, 0.0)
    return base_rate - quote_rate


def load_daily(pair: str) -> pd.DataFrame:
    """Load daily CSV for a pair."""
    csv_path = DATA_DIR / f"{pair}_daily.csv"
    if not csv_path.exists():
        return pd.DataFrame()
    df = pd.read_csv(csv_path, parse_dates=['date'])
    df = df.rename(columns={'date': 'timestamp'})
    df = df.sort_values('timestamp').reset_index(drop=True)
    return df[['timestamp', 'open', 'high', 'low', 'close']]


# ── Load all daily data ─────────────────────────────────────────────────────
print("Loading daily data from IBKR fetch...")
daily_data = {}
for pair in ALL_PAIRS:
    df = load_daily(pair)
    if not df.empty:
        daily_data[pair] = df
    else:
        print(f"  WARNING: No data for {pair}")

print(f"  Loaded {len(daily_data)} pairs")

# Merge all pairs into a single DataFrame on timestamp
all_dates = None
for pair, df in daily_data.items():
    df = df[['timestamp', 'close']].rename(columns={'close': f'close_{pair}'})
    if all_dates is None:
        all_dates = df
    else:
        all_dates = all_dates.merge(df, on='timestamp', how='outer')

all_dates = all_dates.sort_values('timestamp').reset_index(drop=True)
all_dates = all_dates.set_index('timestamp')
all_dates = all_dates.ffill()
all_dates = all_dates.dropna()

print(f"  Total trading days: {len(all_dates)}")
print(f"  Date range: {all_dates.index[0]} -> {all_dates.index[-1]}")

# ── Compute signals ─────────────────────────────────────────────────────────
print("\nComputing signals...")

# Daily log returns
for pair in daily_data:
    all_dates[f'ret_{pair}'] = np.log(all_dates[f'close_{pair}'] / all_dates[f'close_{pair}'].shift(1))

# 6-month (126 trading days) momentum = cumulative return
MOM_LOOKBACK = 126
for pair in daily_data:
    all_dates[f'mom_{pair}'] = np.log(all_dates[f'close_{pair}'] / all_dates[f'close_{pair}'].shift(MOM_LOOKBACK))

# Annualized realized vol (21-day rolling std of daily returns * sqrt(252))
VOL_LOOKBACK = 21
for pair in daily_data:
    all_dates[f'vol_{pair}'] = all_dates[f'ret_{pair}'].rolling(VOL_LOOKBACK).std() * np.sqrt(252)

# Carry signal (from policy rates, updated yearly)
for pair in daily_data:
    all_dates[f'carry_{pair}'] = all_dates.index.map(
        lambda d: get_carry_for_pair(pair, d.year) / 100.0
    )


# ── Monthly rebalance ────────────────────────────────────────────────────────
print("Running monthly rebalance backtest...")

all_dates['month'] = all_dates.index.to_period('M')
month_ends = all_dates.groupby('month').tail(1).index

# Strategy parameters
N_LONG = 5
N_SHORT = 5
PORTFOLIO_VOL_TARGET = 0.10  # 10% annualized vol for whole portfolio
CARRY_WEIGHT = 0.5
MOM_WEIGHT = 0.5


def run_backtest(carry_w, mom_w, n_long, n_short, vol_target, label=""):
    """Run a backtest with given parameters and return daily returns."""
    portfolio_returns = []
    positions_log = []

    for i, rebal_date in enumerate(month_ends):
        if i < MOM_LOOKBACK // 21 + 2:
            continue

        row = all_dates.loc[rebal_date]

        # Score each pair
        scores = {}
        for pair in daily_data:
            carry = row.get(f'carry_{pair}', 0)
            mom = row.get(f'mom_{pair}', 0)
            vol = row.get(f'vol_{pair}', np.nan)

            if pd.isna(mom) or pd.isna(vol) or vol < 0.01:
                continue

            carry_score = carry / vol if vol > 0 else 0
            mom_score = mom / vol if vol > 0 else 0
            combined = carry_w * carry_score + mom_w * mom_score
            scores[pair] = {
                'carry': carry,
                'vol': vol,
                'combined': combined,
            }

        if len(scores) < n_long + n_short:
            continue

        ranked = sorted(scores.items(), key=lambda x: x[1]['combined'], reverse=True)
        long_pairs = [p for p, _ in ranked[:n_long]]
        short_pairs = [p for p, _ in ranked[-n_short:]]

        # Position sizing
        n_positions = len(long_pairs) + len(short_pairs)
        per_pair_vol = vol_target / np.sqrt(n_positions) if n_positions > 0 else 0

        # Next month's trading days
        next_month = rebal_date + pd.DateOffset(months=1)
        mask = (all_dates.index > rebal_date) & (all_dates.index <= next_month)
        next_month_days = all_dates[mask]

        if len(next_month_days) == 0:
            continue

        for day_date in next_month_days.index:
            day_row = all_dates.loc[day_date]
            day_ret = 0.0
            for pair in long_pairs:
                vol = scores[pair]['vol']
                pos_size = per_pair_vol / vol if vol > 0.01 else 0
                pair_ret = day_row.get(f'ret_{pair}', 0)
                if pd.isna(pair_ret): pair_ret = 0
                day_ret += pos_size * pair_ret
                carry_annual = scores[pair]['carry']
                day_ret += carry_annual / 252

            for pair in short_pairs:
                vol = scores[pair]['vol']
                pos_size = per_pair_vol / vol if vol > 0.01 else 0
                pair_ret = day_row.get(f'ret_{pair}', 0)
                if pd.isna(pair_ret): pair_ret = 0
                day_ret -= pos_size * pair_ret
                carry_annual = scores[pair]['carry']
                day_ret -= carry_annual / 252

            portfolio_returns.append({
                'date': day_date,
                'daily_ret': day_ret,
            })

        # Log positions (first and last few)
        if len(positions_log) < 6 or i >= len(month_ends) - 6:
            positions_log.append({
                'rebal_date': rebal_date,
                'long': ', '.join(long_pairs),
                'short': ', '.join(short_pairs),
            })

    return pd.DataFrame(portfolio_returns).set_index('date').sort_index(), positions_log


def print_stats(ret_df, label):
    """Print performance stats for a return series."""
    total_days = len(ret_df)
    years = total_days / 252
    if years < 0.1:
        print(f"  {label}: Insufficient data ({total_days} days)")
        return

    ann_ret = (1 + ret_df['daily_ret']).prod() ** (1 / years) - 1
    ann_vol = ret_df['daily_ret'].std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    cumulative = (1 + ret_df['daily_ret']).cumprod()
    peak = cumulative.cummax()
    drawdown = (cumulative - peak) / peak
    max_dd = drawdown.min()
    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0
    win_rate = (ret_df['daily_ret'] > 0).mean() * 100

    print(f"  {label:25s}: ret={ann_ret*100:+7.2f}%  vol={ann_vol*100:5.2f}%  "
          f"Sharpe={sharpe:+.3f}  MaxDD={max_dd*100:.1f}%  WR={win_rate:.1f}%")
    return ann_ret, ann_vol, sharpe, max_dd


# ── Run all variants ────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("  CARRY + MOMENTUM BLEND — CROSS-PAIR BACKTEST (30 pairs)")
print("=" * 70)

# Main blend
ret_df, pos_log = run_backtest(CARRY_WEIGHT, MOM_WEIGHT, N_LONG, N_SHORT, PORTFOLIO_VOL_TARGET)
print(f"\n  Period: {ret_df.index[0].date()} -> {ret_df.index[-1].date()} "
      f"({len(ret_df)/252:.1f} years)")
print(f"  Portfolio: Long top {N_LONG}, Short bottom {N_SHORT} (of {len(daily_data)} pairs)")
print(f"  Carry weight: {CARRY_WEIGHT}  |  Momentum weight: {MOM_WEIGHT}")
print(f"  Portfolio vol target: {PORTFOLIO_VOL_TARGET*100:.0f}%\n")

print_stats(ret_df, "Blend 50/50")

# Component comparison
print("\n\n  --- Strategy Component Comparison ---")
for label, cw, mw in [
    ("Carry Only", 1.0, 0.0),
    ("Momentum Only", 0.0, 1.0),
    ("Blend 50/50", 0.5, 0.5),
    ("Blend 70/30 (carry heavy)", 0.7, 0.3),
    ("Blend 30/70 (mom heavy)", 0.3, 0.7),
]:
    r, _ = run_backtest(cw, mw, N_LONG, N_SHORT, PORTFOLIO_VOL_TARGET)
    print_stats(r, label)

# Different portfolio sizes
print("\n\n  --- Portfolio Size Comparison (Blend 50/50) ---")
for n in [3, 5, 7, 10]:
    r, _ = run_backtest(0.5, 0.5, n, n, PORTFOLIO_VOL_TARGET)
    print_stats(r, f"Long/Short {n}/{n}")

# ── Year-by-year breakdown ───────────────────────────────────────────────────
print("\n\n  --- Year-by-Year Performance (Blend 50/50, L5/S5) ---")
ret_df['year'] = ret_df.index.year
for year, group in ret_df.groupby('year'):
    y_ret = (1 + group['daily_ret']).prod() - 1
    y_vol = group['daily_ret'].std() * np.sqrt(252)
    y_sharpe = (y_ret / y_vol) if y_vol > 0 else 0
    cum = (1 + group['daily_ret']).cumprod()
    pk = cum.cummax()
    dd = ((cum - pk) / pk).min()
    print(f"    {year}: ret={y_ret*100:+7.2f}%  vol={y_vol*100:5.2f}%  "
          f"Sharpe={y_sharpe:+.3f}  MaxDD={dd*100:.1f}%")

# ── Position history ─────────────────────────────────────────────────────────
print("\n\n  --- Position History (sample) ---")
for entry in pos_log[:6]:
    print(f"    {entry['rebal_date'].strftime('%Y-%m')}: "
          f"LONG {entry['long']}  SHORT {entry['short']}")
print("  ...")
for entry in pos_log[-3:]:
    print(f"    {entry['rebal_date'].strftime('%Y-%m')}: "
          f"LONG {entry['long']}  SHORT {entry['short']}")

# ── USD-neutral variant ──────────────────────────────────────────────────────
print("\n\n  --- USD-Neutral Variant (cross-pairs only) ---")
CROSS_PAIRS = [p for p in daily_data if not p.startswith("USD") or p.endswith("USD")]
# Actually, use only pairs where neither leg is USD
CROSS_ONLY = [p for p in daily_data
              if not p.startswith("USD") and not p.endswith("USD")
              and p not in ("XAUUSD",)]
print(f"  Cross-pairs (no USD): {len(CROSS_ONLY)}")
print(f"  Pairs: {CROSS_ONLY}")

# Temporarily filter to cross-only
daily_data_cross = {p: daily_data[p] for p in CROSS_ONLY if p in daily_data}

# Build a mini all_dates for cross pairs
all_dates_cross = all_dates[[f'close_{p}' for p in CROSS_ONLY if f'close_{p}' in all_dates.columns]].copy()
for p in CROSS_ONLY:
    if f'close_{p}' not in all_dates_cross.columns:
        continue
    all_dates_cross[f'ret_{p}'] = all_dates[f'ret_{p}']
    all_dates_cross[f'mom_{p}'] = all_dates[f'mom_{p}']
    all_dates_cross[f'vol_{p}'] = all_dates[f'vol_{p}']
    all_dates_cross[f'carry_{p}'] = all_dates[f'carry_{p}']

# Run cross-only backtest using original all_dates
cross_ret = []
all_dates_cross['month'] = all_dates_cross.index.to_period('M')
month_ends_cross = all_dates_cross.groupby('month').tail(1).index

for i, rebal_date in enumerate(month_ends_cross):
    if i < MOM_LOOKBACK // 21 + 2:
        continue
    row = all_dates_cross.loc[rebal_date]
    scores = {}
    for p in CROSS_ONLY:
        carry = row.get(f'carry_{p}', 0)
        mom = row.get(f'mom_{p}', 0)
        vol = row.get(f'vol_{p}', np.nan)
        if pd.isna(mom) or pd.isna(vol) or vol < 0.01:
            continue
        carry_score = carry / vol if vol > 0 else 0
        mom_score = mom / vol if vol > 0 else 0
        combined = 0.5 * carry_score + 0.5 * mom_score
        scores[p] = {'carry': carry, 'vol': vol, 'combined': combined}

    if len(scores) < 6:
        continue

    ranked = sorted(scores.items(), key=lambda x: x[1]['combined'], reverse=True)
    n_l = min(3, len(ranked) // 2)
    n_s = min(3, len(ranked) // 2)
    long_pairs = [p for p, _ in ranked[:n_l]]
    short_pairs = [p for p, _ in ranked[-n_s:]]

    n_positions = len(long_pairs) + len(short_pairs)
    per_pair_vol = 0.10 / np.sqrt(n_positions) if n_positions > 0 else 0

    next_month = rebal_date + pd.DateOffset(months=1)
    mask = (all_dates_cross.index > rebal_date) & (all_dates_cross.index <= next_month)
    next_days = all_dates_cross[mask]
    if len(next_days) == 0:
        continue

    for day_date in next_days.index:
        day_row = all_dates_cross.loc[day_date]
        day_ret = 0.0
        for p in long_pairs:
            vol = scores[p]['vol']
            pos_size = per_pair_vol / vol if vol > 0.01 else 0
            pair_ret = day_row.get(f'ret_{p}', 0)
            if pd.isna(pair_ret): pair_ret = 0
            day_ret += pos_size * pair_ret
            carry_annual = scores[p]['carry']
            day_ret += carry_annual / 252
        for p in short_pairs:
            vol = scores[p]['vol']
            pos_size = per_pair_vol / vol if vol > 0.01 else 0
            pair_ret = day_row.get(f'ret_{p}', 0)
            if pd.isna(pair_ret): pair_ret = 0
            day_ret -= pos_size * pair_ret
            carry_annual = scores[p]['carry']
            day_ret -= carry_annual / 252
        cross_ret.append({'date': day_date, 'daily_ret': day_ret})

if cross_ret:
    cross_df = pd.DataFrame(cross_ret).set_index('date').sort_index()
    print_stats(cross_df, "Cross-only (no USD pairs)")
else:
    print("  No cross-only results")

# ── IBKR Margin info ─────────────────────────────────────────────────────────
print("\n\n" + "=" * 70)
print("  IBKR MARGIN INFORMATION")
print("=" * 70)
print("""
  IBKR Forex Margin (US residents, margin account):

  Major currencies (USD, CAD, EUR, DKK):  2% margin  (50:1 leverage)
  Other G10 (AUD, CHF, NOK, NZD, SEK, GBP): 3% margin (33:1 leverage)
  Exotic: 5% margin (20:1 leverage)

  For this strategy (10 positions, ~$100K each):
    Total notional: ~$1M long + $1M short = $2M
    Margin required: ~$50K-$60K (2-3% of notional)
    With $50K account: ~2x effective leverage on net exposure

  IBKR Margin Loan Rates (USD, blended):
    First $100K:  BM + 1.50% (~6.00%)
    $100K-$1M:    BM + 1.00% (~5.50%)
    $1M+:         BM + 0.50% (~5.00%)

  Carry/swap rates: Credited/debited daily at IBKR benchmark rates.
  Net carry on this strategy: ~2-4% annualized depending on rate regime.
""")

print("\n=== BACKTEST COMPLETE ===")

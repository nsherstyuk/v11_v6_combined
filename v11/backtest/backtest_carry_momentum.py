"""
Backtest: Carry + Momentum Blend Strategy on G10 FX (ex-EUR)

Uses 1-min historical data aggregated to daily bars.
Carry signal: approximate interest rate differential from central bank policy rates.
Momentum signal: 6-month log return.

Portfolio: Long top-3, Short bottom-3 pairs by combined score, monthly rebalance.
Position sizing: volatility-targeted (10% annualized vol per pair).
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
    # EURUSD excluded (data integrity concerns)
}

PIP_MULT = {
    "GBPUSD": 10000, "USDJPY": 100, "USDCAD": 10000,
    "USDCHF": 10000, "AUDUSD": 10000, "NZDUSD": 10000,
}

# ── Approximate central bank policy rates (annual %) by year ─────────────────
# These are simplified end-of-year policy rates. In reality, rates change
# multiple times per year, but this captures the major regimes.
# Format: {year: {currency: rate}}
# USD: Fed Funds, GBP: BoE, JPY: BoJ, CAD: BoC, CHF: SNB, AUD: RBA, NZD: RBNZ
POLICY_RATES = {
    2017: {"USD": 1.25, "GBP": 0.50, "JPY": -0.10, "CAD": 1.00, "CHF": -0.75, "AUD": 1.50, "NZD": 1.75},
    2018: {"USD": 2.25, "GBP": 0.75, "JPY": -0.10, "CAD": 1.75, "CHF": -0.50, "AUD": 1.50, "NZD": 1.75},
    2019: {"USD": 1.75, "GBP": 0.75, "JPY": -0.10, "CAD": 1.75, "CHF": -0.50, "AUD": 0.75, "NZD": 1.00},
    2020: {"USD": 0.25, "GBP": 0.10, "JPY": -0.10, "CAD": 0.25, "CHF": -0.75, "AUD": 0.10, "NZD": 0.25},
    2021: {"USD": 0.25, "GBP": 0.25, "JPY": -0.10, "CAD": 0.25, "CHF": -0.75, "AUD": 0.10, "NZD": 0.50},
    2022: {"USD": 4.25, "GBP": 3.50, "JPY": -0.10, "CAD": 4.25, "CHF": 1.00, "AUD": 3.10, "NZD": 4.25},
    2023: {"USD": 5.25, "GBP": 5.25, "JPY": -0.10, "CAD": 5.00, "CHF": 1.75, "AUD": 4.35, "NZD": 5.50},
    2024: {"USD": 4.50, "GBP": 4.75, "JPY": 0.25, "CAD": 3.25, "CHF": 1.50, "AUD": 4.35, "NZD": 4.25},
    2025: {"USD": 4.25, "GBP": 4.50, "JPY": 0.50, "CAD": 2.75, "CHF": 0.50, "AUD": 3.85, "NZD": 3.50},
    2026: {"USD": 4.25, "GBP": 4.50, "JPY": 0.50, "CAD": 2.75, "CHF": 0.50, "AUD": 3.85, "NZD": 3.50},
}


def get_carry_for_pair(pair: str, year: int) -> float:
    """Get approximate annual carry (interest rate differential) for a pair.
    
    For XXXUSD pairs: carry = USD_rate - XXX_rate (you earn USD, pay XXX)
    Positive carry = you earn net interest for holding the pair long.
    """
    rates = POLICY_RATES.get(year, POLICY_RATES[2026])
    if pair == "GBPUSD":
        return rates["USD"] - rates["GBP"]
    elif pair == "USDJPY":
        # USDJPY: long = long USD / short JPY. Carry = USD - JPY
        return rates["USD"] - rates["JPY"]
    elif pair == "USDCAD":
        return rates["USD"] - rates["CAD"]
    elif pair == "USDCHF":
        return rates["USD"] - rates["CHF"]
    elif pair == "AUDUSD":
        # AUDUSD: long = long AUD / short USD. Carry = AUD - USD
        return rates["AUD"] - rates["USD"]
    elif pair == "NZDUSD":
        return rates["NZD"] - rates["USD"]
    return 0.0


def load_daily(pair: str, filename: str) -> pd.DataFrame:
    """Load 1-min data and aggregate to daily."""
    path = DATA_DIR / filename
    df = pd.read_csv(path, parse_dates=['timestamp'])
    df = df.sort_values('timestamp').reset_index(drop=True)
    df['date'] = df['timestamp'].dt.date

    daily = df.groupby('date').agg(
        open=('open', 'first'),
        high=('high', 'max'),
        low=('low', 'min'),
        close=('close', 'last'),
    ).reset_index()
    daily['date'] = pd.to_datetime(daily['date'])
    return daily


# ── Load all daily data ─────────────────────────────────────────────────────
print("Loading daily data...")
daily_data = {}
for pair, filename in PAIRS.items():
    print(f"  {pair}...")
    daily_data[pair] = load_daily(pair, filename)

# Merge all pairs into a single DataFrame on date
all_dates = None
for pair, df in daily_data.items():
    df = df[['date', 'close']].rename(columns={'close': f'close_{pair}'})
    if all_dates is None:
        all_dates = df
    else:
        all_dates = all_dates.merge(df, on='date', how='outer')

all_dates = all_dates.sort_values('date').reset_index(drop=True)
all_dates = all_dates.set_index('date')

# Forward-fill any missing dates (holidays etc) - at most 1 day
all_dates = all_dates.ffill()
all_dates = all_dates.dropna()

print(f"  Total trading days: {len(all_dates)}")
print(f"  Date range: {all_dates.index[0]} → {all_dates.index[-1]}")

# ── Compute signals ─────────────────────────────────────────────────────────
print("\nComputing signals...")

# Daily log returns
for pair in PAIRS:
    all_dates[f'ret_{pair}'] = np.log(all_dates[f'close_{pair}'] / all_dates[f'close_{pair}'].shift(1))

# 6-month (126 trading days) momentum = cumulative return
MOM_LOOKBACK = 126  # ~6 months
for pair in PAIRS:
    all_dates[f'mom_{pair}'] = np.log(all_dates[f'close_{pair}'] / all_dates[f'close_{pair}'].shift(MOM_LOOKBACK))

# Annualized realized vol (21-day rolling std of daily returns × sqrt(252))
VOL_LOOKBACK = 21
for pair in PAIRS:
    all_dates[f'vol_{pair}'] = all_dates[f'ret_{pair}'].rolling(VOL_LOOKBACK).std() * np.sqrt(252)

# Carry signal (from policy rates, updated yearly)
for pair in PAIRS:
    all_dates[f'carry_{pair}'] = all_dates.index.map(
        lambda d: get_carry_for_pair(pair, d.year) / 100.0  # convert % to decimal
    )

# ── Monthly rebalance ────────────────────────────────────────────────────────
print("Running monthly rebalance backtest...")

# Get month-end dates for rebalancing
all_dates['month'] = all_dates.index.to_period('M')
month_ends = all_dates.groupby('month').tail(1).index

# Strategy parameters
N_LONG = 3
N_SHORT = 3
PORTFOLIO_VOL_TARGET = 0.10  # 10% annualized vol for whole portfolio
CARRY_WEIGHT = 0.5
MOM_WEIGHT = 0.5

# Track portfolio
portfolio_returns = []
positions_log = []

for i, rebal_date in enumerate(month_ends):
    if i < MOM_LOOKBACK // 21 + 2:  # need enough history
        continue

    row = all_dates.loc[rebal_date]

    # Score each pair
    scores = {}
    for pair in PAIRS:
        carry = row.get(f'carry_{pair}', 0)
        mom = row.get(f'mom_{pair}', 0)
        vol = row.get(f'vol_{pair}', np.nan)

        if pd.isna(mom) or pd.isna(vol) or vol < 0.01:
            continue

        # Risk-adjusted scores
        carry_score = carry / vol if vol > 0 else 0
        mom_score = mom / vol if vol > 0 else 0

        combined = CARRY_WEIGHT * carry_score + MOM_WEIGHT * mom_score
        scores[pair] = {
            'carry': carry,
            'mom': mom,
            'vol': vol,
            'carry_score': carry_score,
            'mom_score': mom_score,
            'combined': combined,
        }

    if len(scores) < N_LONG + N_SHORT:
        continue

    # Rank by combined score
    ranked = sorted(scores.items(), key=lambda x: x[1]['combined'], reverse=True)
    long_pairs = [p for p, _ in ranked[:N_LONG]]
    short_pairs = [p for p, _ in ranked[-N_SHORT:]]

    # Next month's trading days
    next_month = rebal_date + pd.DateOffset(months=1)
    mask = (all_dates.index > rebal_date) & (all_dates.index <= next_month)
    next_month_days = all_dates[mask]

    if len(next_month_days) == 0:
        continue

    # Position sizing: equal risk contribution, target portfolio vol
    n_positions = len(long_pairs) + len(short_pairs)
    per_pair_vol_target = PORTFOLIO_VOL_TARGET / np.sqrt(n_positions) if n_positions > 0 else 0

    # Calculate daily portfolio returns
    for day_date in next_month_days.index:
        day_row = all_dates.loc[day_date]
        day_ret = 0.0
        for pair in long_pairs:
            vol = scores[pair]['vol']
            pos_size = per_pair_vol_target / vol if vol > 0.01 else 0
            pair_ret = day_row.get(f'ret_{pair}', 0)
            if pd.isna(pair_ret):
                pair_ret = 0
            day_ret += pos_size * pair_ret  # long: +1

        for pair in short_pairs:
            vol = scores[pair]['vol']
            pos_size = per_pair_vol_target / vol if vol > 0.01 else 0
            pair_ret = day_row.get(f'ret_{pair}', 0)
            if pd.isna(pair_ret):
                pair_ret = 0
            day_ret -= pos_size * pair_ret  # short: -1

        # Add carry return (accrued daily)
        for pair in long_pairs:
            carry_annual = scores[pair]['carry']
            day_ret += carry_annual / 252  # daily carry accrual

        for pair in short_pairs:
            carry_annual = scores[pair]['carry']
            day_ret -= carry_annual / 252  # short pays negative carry

        portfolio_returns.append({
            'date': day_date,
            'daily_ret': day_ret,
        })

    # Log positions
    positions_log.append({
        'rebal_date': rebal_date,
        'long': ', '.join(long_pairs),
        'short': ', '.join(short_pairs),
        **{f'{p}_combined': scores[p]['combined'] for p in long_pairs + short_pairs},
        **{f'{p}_carry': scores[p]['carry'] for p in long_pairs + short_pairs},
        **{f'{p}_mom': scores[p]['mom'] for p in long_pairs + short_pairs},
    })

# ── Results ──────────────────────────────────────────────────────────────────
ret_df = pd.DataFrame(portfolio_returns).set_index('date').sort_index()
ret_df['cum_ret'] = (1 + ret_df['daily_ret']).cumprod() - 1

# Annualized return and Sharpe
total_days = len(ret_df)
years = total_days / 252
ann_ret = (1 + ret_df['daily_ret']).prod() ** (1 / years) - 1
ann_vol = ret_df['daily_ret'].std() * np.sqrt(252)
sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

# Max drawdown
cumulative = (1 + ret_df['daily_ret']).cumprod()
peak = cumulative.cummax()
drawdown = (cumulative - peak) / peak
max_dd = drawdown.min()

# Calmar ratio
calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0

# Win rate
win_rate = (ret_df['daily_ret'] > 0).mean() * 100

# Monthly returns
ret_df['month_period'] = ret_df.index.to_period('M')
monthly_rets = ret_df.groupby('month_period')['daily_ret'].apply(
    lambda x: (1 + x).prod() - 1
)

print("\n" + "=" * 70)
print("  CARRY + MOMENTUM BLEND — BACKTEST RESULTS")
print("=" * 70)
print(f"\n  Period: {ret_df.index[0].date()} → {ret_df.index[-1].date()} ({years:.1f} years)")
print(f"  Rebalance: Monthly")
print(f"  Portfolio: Long top {N_LONG}, Short bottom {N_SHORT} (of {len(PAIRS)} pairs)")
print(f"  Carry weight: {CARRY_WEIGHT}  |  Momentum weight: {MOM_WEIGHT}")
print(f"  Portfolio vol target: {PORTFOLIO_VOL_TARGET*100:.0f}%")
print()
print(f"  Annualized Return:  {ann_ret*100:+.2f}%")
print(f"  Annualized Vol:     {ann_vol*100:.2f}%")
print(f"  Sharpe Ratio:      {sharpe:.3f}")
print(f"  Max Drawdown:       {max_dd*100:.2f}%")
print(f"  Calmar Ratio:       {calmar:.3f}")
print(f"  Daily Win Rate:     {win_rate:.1f}%")
print(f"  Total Return:       {ret_df['cum_ret'].iloc[-1]*100:+.1f}%")

# ── Compare: Carry-only and Momentum-only ───────────────────────────────────
print("\n\n  --- Strategy Component Comparison ---")
for label, cw, mw in [("Carry Only", 1.0, 0.0), ("Momentum Only", 0.0, 1.0), ("Blend 50/50", 0.5, 0.5)]:
    port_rets = []
    for i, rebal_date in enumerate(month_ends):
        if i < MOM_LOOKBACK // 21 + 2:
            continue
        row = all_dates.loc[rebal_date]
        scores = {}
        for pair in PAIRS:
            carry = row.get(f'carry_{pair}', 0)
            mom = row.get(f'mom_{pair}', 0)
            vol = row.get(f'vol_{pair}', np.nan)
            if pd.isna(mom) or pd.isna(vol) or vol < 0.01:
                continue
            carry_score = carry / vol if vol > 0 else 0
            mom_score = mom / vol if vol > 0 else 0
            combined = cw * carry_score + mw * mom_score
            scores[pair] = {'carry': carry, 'vol': vol, 'combined': combined}

        if len(scores) < N_LONG + N_SHORT:
            continue
        ranked = sorted(scores.items(), key=lambda x: x[1]['combined'], reverse=True)
        long_pairs = [p for p, _ in ranked[:N_LONG]]
        short_pairs = [p for p, _ in ranked[-N_SHORT:]]

        next_month = rebal_date + pd.DateOffset(months=1)
        mask = (all_dates.index > rebal_date) & (all_dates.index <= next_month)
        next_month_days = all_dates[mask]
        if len(next_month_days) == 0:
            continue

        n_pos = len(long_pairs) + len(short_pairs)
        pp_vol = 0.10 / np.sqrt(n_pos) if n_pos > 0 else 0
        for day_date in next_month_days.index:
            day_row = all_dates.loc[day_date]
            day_ret = 0.0
            for pair in long_pairs:
                vol = scores[pair]['vol']
                pos_size = pp_vol / vol if vol > 0.01 else 0
                pair_ret = day_row.get(f'ret_{pair}', 0)
                if pd.isna(pair_ret): pair_ret = 0
                day_ret += pos_size * pair_ret
                carry_annual = scores[pair]['carry']
                day_ret += carry_annual / 252
            for pair in short_pairs:
                vol = scores[pair]['vol']
                pos_size = pp_vol / vol if vol > 0.01 else 0
                pair_ret = day_row.get(f'ret_{pair}', 0)
                if pd.isna(pair_ret): pair_ret = 0
                day_ret -= pos_size * pair_ret
                carry_annual = scores[pair]['carry']
                day_ret -= carry_annual / 252
            port_rets.append(day_ret)

    pr = pd.Series(port_rets)
    y = len(pr) / 252
    ar = (1 + pr).prod() ** (1 / y) - 1 if y > 0 else 0
    av = pr.std() * np.sqrt(252)
    sh = ar / av if av > 0 else 0
    cum = (1 + pr).cumprod()
    pk = cum.cummax()
    dd = ((cum - pk) / pk).min()
    print(f"    {label:20s}: ret={ar*100:+6.2f}%  vol={av*100:5.2f}%  Sharpe={sh:.3f}  MaxDD={dd*100:.1f}%")

# ── Year-by-year breakdown ───────────────────────────────────────────────────
print("\n\n  --- Year-by-Year Performance (Blend 50/50) ---")
ret_df['year'] = ret_df.index.year
for year, group in ret_df.groupby('year'):
    y_ret = (1 + group['daily_ret']).prod() - 1
    y_vol = group['daily_ret'].std() * np.sqrt(252)
    y_sharpe = (y_ret / y_vol) if y_vol > 0 else 0
    cum = (1 + group['daily_ret']).cumprod()
    pk = cum.cummax()
    dd = ((cum - pk) / pk).min()
    print(f"    {year}: ret={y_ret*100:+7.2f}%  vol={y_vol*100:5.2f}%  Sharpe={y_sharpe:+.3f}  MaxDD={dd*100:.1f}%")

# ── Position history ─────────────────────────────────────────────────────────
print("\n\n  --- Position History (last 12 rebalances) ---")
pos_df = pd.DataFrame(positions_log)
if len(pos_df) > 12:
    pos_df = pos_df.tail(12)
for _, row in pos_df.iterrows():
    print(f"    {row['rebal_date'].strftime('%Y-%m')}: LONG {row['long']}  SHORT {row['short']}")

# ── IBKR Margin info ─────────────────────────────────────────────────────────
print("\n\n" + "=" * 70)
print("  IBKR MARGIN INFORMATION (for position sizing)")
print("=" * 70)
print("""
  IBKR Forex Margin Requirements (US residents, margin account):

  Major currencies (USD, CAD, EUR, DKK):
    - Margin: 2% → Leverage up to 50:1
    - $100K position requires $2,000 margin

  Other G10 currencies (AUD, CHF, NOK, NZD, SEK, GBP):
    - Margin: 3% → Leverage up to 33:1
    - $100K position requires $3,000 margin

  Exotic currencies:
    - Margin: 5% → Leverage up to 20:1

  For this strategy (6 pairs, 3 long + 3 short):
    - Each position ~$100K notional (typical for vol-targeted sizing)
    - Total notional: ~$600K long + $600K short = $1.2M
    - Total margin required: ~$30K-$36K (2-3% of notional)
    - With $50K account: ~1.4-1.7x effective leverage on net exposure

  IBKR Margin Loan Rates (USD, blended):
    - First $100K:  BM + 1.50%  (~6.00% currently)
    - $100K-$1M:    BM + 1.00%  (~5.50%)
    - $1M+:         BM + 0.50%  (~5.00%)

  IBKR Credit Interest (idle cash):
    - BM − 0.50% on balances > $10K (~4.00% currently)

  Carry/swap rates on FX positions:
    - Credited/debited daily at IBKR benchmark rates
    - Net carry on this strategy: ~2-4% annualized depending on rate regime
""")

print("\n=== BACKTEST COMPLETE ===")

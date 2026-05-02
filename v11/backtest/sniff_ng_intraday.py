"""Quick sniff test for NG1! intraday cycle.

Question: does the front-month natural-gas contract show a periodic
intraday pattern (morning ramp / overnight trough) that could be tradable?

Data: 15-min bars, 2025-11-13 to 2026-04-29.

Tests (all in NY local time, since NYMEX session is anchored there):
  1. Mean log-return per hour-of-day. Look for systematic up/down hours.
  2. Same, separated by day-of-week, to spot weekly seasonality.
  3. Hour-vs-hour close drift: for each session, anchor at NY 09:00 and
     measure cumulative move at every hour. If a "ramp" exists, the curve
     should rise systematically.
  4. Overnight (settle-to-open) vs intraday split: is the cycle in the
     overnight bars or the regular session?
  5. T-stat on the strongest hour to flag noise vs signal.

No trading rule, no costs — just whether a pattern is even visible.
"""
from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd

CSV = Path(r"C:\ibkr_grok-_wing_agent\NYMEX_DL_NG1!, 15_iso.csv")


def load() -> pd.DataFrame:
    df = pd.read_csv(CSV, usecols=["time", "open", "high", "low", "close"])
    df["ts"] = pd.to_datetime(df["time"], utc=True)
    df["ts_ny"] = df["ts"].dt.tz_convert("America/New_York")
    df = df.sort_values("ts_ny").drop_duplicates("ts_ny").reset_index(drop=True)
    df["ret"] = np.log(df["close"]).diff()
    df["hour"] = df["ts_ny"].dt.hour
    df["minute"] = df["ts_ny"].dt.minute
    df["dow"] = df["ts_ny"].dt.dayofweek
    df["session_date"] = df["ts_ny"].dt.date
    return df.dropna(subset=["ret"])


def t_stat(x: pd.Series) -> float:
    if len(x) < 2 or x.std(ddof=1) == 0:
        return float("nan")
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def main() -> None:
    df = load()
    print(f"Bars: {len(df):,}  span: {df['ts_ny'].min()} -> {df['ts_ny'].max()}")
    print(f"Sessions: {df['session_date'].nunique()}")

    # 1. Hour-of-day mean return (basis points = 1e-4)
    print("\n1. Mean 15-min log-return by NY hour (bps)")
    print("   hour     n     mean_bps   t-stat    cum_bps_per_hr")
    by_hour = df.groupby("hour")["ret"].agg(["count", "mean", "std"])
    for h, row in by_hour.iterrows():
        sub = df.loc[df["hour"] == h, "ret"]
        bps = row["mean"] * 1e4
        t = t_stat(sub)
        cum = bps * 4  # 4 fifteen-min bars per hour
        print(f"   {h:02d}    {int(row['count']):4d}   {bps:+7.2f}   {t:+5.2f}     {cum:+7.2f}")

    # 2. Average intraday cumulative drift, anchored at NY 09:00 open
    print("\n2. Average cumulative log-drift through the day (bps from 09:00 NY)")
    df_day = df.copy()
    df_day["bar_id"] = df_day["hour"] * 4 + df_day["minute"] // 15
    cum = df_day.groupby(["session_date", "bar_id"])["ret"].sum().unstack("bar_id")
    cum_curve = cum.cumsum(axis=1).mean(axis=0) * 1e4
    # Print every hour
    for bid in sorted(cum_curve.index):
        if bid % 4 == 0:
            h = bid // 4
            print(f"   {h:02d}:00   {cum_curve.loc[bid]:+7.2f} bps")

    # 3. Top/bottom hours by Sharpe-like ratio
    print("\n3. Top 3 / bottom 3 hours by t-stat (sample-size weighted)")
    by_hour["t"] = [t_stat(df.loc[df["hour"] == h, "ret"]) for h in by_hour.index]
    by_hour["mean_bps"] = by_hour["mean"] * 1e4
    ranked = by_hour.sort_values("t")
    print("   Bottom (most negative bias):")
    print(ranked[["count", "mean_bps", "t"]].head(3).to_string())
    print("   Top (most positive bias):")
    print(ranked[["count", "mean_bps", "t"]].tail(3).to_string())

    # 4. Day-of-week x hour-of-day cell counts and means (just headlines)
    print("\n4. Day-of-week mean 15-min return (bps)")
    dow_means = df.groupby("dow")["ret"].agg(["count", "mean"])
    dow_means["mean_bps"] = dow_means["mean"] * 1e4
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for d, row in dow_means.iterrows():
        print(f"   {dow_names[d]}  n={int(row['count']):5d}  mean={row['mean_bps']:+5.2f} bps")

    # 5. "Ramp" sniff: does NY 09:00-12:00 systematically beat 18:00-21:00?
    print("\n5. Window comparison")
    morning = df[df["hour"].between(9, 11)]["ret"]
    evening = df[df["hour"].between(18, 20)]["ret"]
    print(f"   Morning (09-12 NY)  n={len(morning):4d}  mean={morning.mean()*1e4:+5.2f} bps  t={t_stat(morning):+.2f}")
    print(f"   Evening (18-21 NY)  n={len(evening):4d}  mean={evening.mean()*1e4:+5.2f} bps  t={t_stat(evening):+.2f}")
    diff = morning.mean() - evening.mean()
    pooled_se = np.sqrt(morning.var(ddof=1)/len(morning) + evening.var(ddof=1)/len(evening))
    t_diff = diff / pooled_se if pooled_se > 0 else float("nan")
    print(f"   Diff (morn-eve)   {diff*1e4:+5.2f} bps   t={t_diff:+.2f}")

    # 6. Sanity: intra-trade integrity check (entry < exit obvious here, just stating timestamps)
    print(f"\n6. Sanity: bars are monotonically increasing in time? "
          f"{(df['ts_ny'].diff().dt.total_seconds().dropna() > 0).all()}")
    print(f"   Median bar gap: {df['ts_ny'].diff().median()}")


if __name__ == "__main__":
    main()

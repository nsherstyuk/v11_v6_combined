"""ORB IS/OOS grid on US equities — pre-registered template.

Mirrors `v11/backtest/orb_fx_grid.py`'s structure (same template applied to
every ticker, no per-ticker tuning) but with US-equity-appropriate
semantics:

  Range window:    09:30 - 10:30 ET (first 60 min after the regular open)
  Trade window:    10:30 - 15:30 ET (after range, exit 15 min before close)
  RR ratio:        2.5
  Range size:      0.10%-2.00% of mid (no absolute filter)
  Velocity filter: OFF (gold-tuned, doesn't transfer)
  Gap filter:      OFF (XAUUSD-validated only)
  Skip weekdays:   none
  Extended hours:  EXCLUDED — bars outside 09:30-16:00 ET are ignored
  Costs:           slippage $0.01/side (round-trip $0.02 per share)
                   commission $0.005/share (IBKR Pro), $0.01/share round-trip
                   total cost per share, both ways, ≈ $0.03

IS = 2024+, OOS = 2018-01-01 .. 2023-12-31. Matches the FX-grid convention.

Decision gate (per review): OOS AvgR_slip ≥ +0.05R AND N/yr ≥ 20.

Pre-registered universe — DO NOT add/remove without a new pre-registration:
  SPY QQQ IWM DIA  (ETFs)
  AAPL MSFT NVDA META AMZN GOOGL TSLA INTC MU AMD TSM  (mega-caps + 1 ADR)

CSV source: tick_vault_data/us_equities/<TICKER>.csv (naive UTC datetimes,
columns date,open,high,low,close,volume; 1-min bars; useRTH=False at
download so this script filters RTH on its own).

Run:
    python -m v11.backtest.orb_us_equities_grid
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd


# ── Pre-registered constants ────────────────────────────────────────────────

REPO = Path(__file__).resolve().parents[2]
DATA_DIR = REPO / "tick_vault_data" / "us_equities"

UNIVERSE = [
    "SPY", "QQQ", "IWM", "DIA",
    "AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA",
    "INTC", "MU", "AMD", "TSM",
]

# ET-time window definitions
RANGE_START_ET = "09:30"
RANGE_END_ET   = "10:30"     # exclusive of 10:30 bar
TRADE_START_ET = "10:30"
TRADE_END_ET   = "15:30"     # exit at 15:30 if no fill yet

RR_RATIO = 2.5
MIN_RANGE_PCT = 0.10
MAX_RANGE_PCT = 2.00

# IS/OOS
OOS_START = pd.Timestamp("2018-01-01")
OOS_END   = pd.Timestamp("2023-12-31 23:59:59")
IS_START  = pd.Timestamp("2024-01-01")
OOS_YEARS = 6.0

# Cost model (per share, round-trip)
COST_PER_SHARE = 0.03  # $0.01 slip per side ×2 + $0.005 commission ×2


# ── Load helpers ────────────────────────────────────────────────────────────

def load_ticker(symbol: str) -> pd.DataFrame:
    """Load a ticker's CSV, convert UTC → America/New_York, filter to RTH."""
    path = DATA_DIR / f"{symbol}.csv"
    df = pd.read_csv(path)
    # CSV dates are naive-UTC strings (per downloader convention).
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize("UTC").dt.tz_convert("America/New_York")
    # Keep both date and time-of-day for later filtering. Drop tz for groupby.
    df["et"] = df["date"].dt.tz_localize(None)
    df["session"] = df["et"].dt.date
    df["tod"] = df["et"].dt.time
    df = df.set_index("et").sort_index()
    # RTH-only: 09:30 <= t < 16:00 ET
    rth = df.between_time("09:30", "15:59:59")
    return rth


# ── ORB per-day simulation ──────────────────────────────────────────────────

def simulate_day(day_bars: pd.DataFrame) -> Optional[dict]:
    """Run ORB on one trading day's bars (RTH only, 1-min granularity).

    Returns trade dict or None if no trade fired (no breakout, range filter
    rejected, insufficient bars, etc.).
    """
    # Range = 09:30-10:30 bars
    rng = day_bars.between_time(RANGE_START_ET, "10:29:59")
    if len(rng) < 30:
        return None
    range_high = float(rng["high"].max())
    range_low = float(rng["low"].min())
    range_width = range_high - range_low
    if range_width <= 0:
        return None
    mid = (range_high + range_low) / 2.0
    range_pct = range_width / mid * 100.0
    if range_pct < MIN_RANGE_PCT or range_pct > MAX_RANGE_PCT:
        return None

    # Trade window
    trade = day_bars.between_time(TRADE_START_ET, "15:29:59")
    if trade.empty:
        return None

    # Walk bars; first to break range_high (long) or range_low (short) fires
    entry = None
    direction = None
    entry_time = None
    for ts, bar in trade.iterrows():
        if bar["high"] >= range_high and bar["low"] <= range_low:
            # Both broken in same bar — ambiguous; skip (conservative)
            return None
        if bar["high"] >= range_high:
            entry = range_high
            direction = "long"
            entry_time = ts
            break
        if bar["low"] <= range_low:
            entry = range_low
            direction = "short"
            entry_time = ts
            break
    if entry is None:
        return None

    # Compute stop + target
    if direction == "long":
        stop = range_low
        target = entry + RR_RATIO * range_width
    else:
        stop = range_high
        target = entry - RR_RATIO * range_width

    # Walk remaining bars (post-entry) to find exit
    post = trade.loc[entry_time:].iloc[1:]  # exclude the entry bar itself
    exit_price = None
    exit_reason = None
    for ts, bar in post.iterrows():
        # Stop-first convention if both hit in same bar (conservative)
        if direction == "long":
            if bar["low"] <= stop:
                exit_price = stop
                exit_reason = "stop"
                break
            if bar["high"] >= target:
                exit_price = target
                exit_reason = "target"
                break
        else:
            if bar["high"] >= stop:
                exit_price = stop
                exit_reason = "stop"
                break
            if bar["low"] <= target:
                exit_price = target
                exit_reason = "target"
                break

    # Time-exit at 15:30 ET if still open. Bug-fix 2026-05-11: `post` was
    # filtered to ≤ 15:29:59 (subset of `trade`), so the original
    # post.between_time("15:30", …) was always empty and the trade got
    # silently discarded. Pull the 15:30+ bars from day_bars instead.
    if exit_price is None:
        time_exit_bars = day_bars.between_time("15:30", "15:59:59").loc[entry_time:]
        if time_exit_bars.empty:
            return None  # entered too late, no 15:30 bar — discard
        exit_price = float(time_exit_bars.iloc[0]["close"])
        exit_reason = "time"

    # R calculation: long-direction first, mirror for short
    if direction == "long":
        r_raw = (exit_price - entry) / range_width
    else:
        r_raw = (entry - exit_price) / range_width

    # Cost as R units: $0.03/share round-trip / range_width
    cost_r = COST_PER_SHARE / range_width
    r_slip = r_raw - cost_r

    return {
        "date": day_bars.index[0].date(),
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "range_width": range_width,
        "range_pct": range_pct,
        "r_raw": r_raw,
        "r_slip": r_slip,
    }


def run_one(symbol: str) -> pd.DataFrame:
    """Run ORB across all days for one ticker. Returns trade DataFrame."""
    df = load_ticker(symbol)
    if df.empty:
        return pd.DataFrame()
    trades = []
    for session_date, day_bars in df.groupby("session"):
        t = simulate_day(day_bars)
        if t is not None:
            trades.append(t)
    if not trades:
        return pd.DataFrame()
    out = pd.DataFrame(trades)
    out["date"] = pd.to_datetime(out["date"])
    return out


# ── Metrics ─────────────────────────────────────────────────────────────────

def metrics(trades: pd.DataFrame, slip: bool = False) -> dict:
    """Summary metrics. slip=True uses r_slip (after costs)."""
    if trades.empty:
        return {"N": 0, "WR": 0.0, "AvgR": 0.0, "PF": 0.0, "AvgWin": 0.0,
                "AvgLoss": 0.0}
    col = "r_slip" if slip else "r_raw"
    r = trades[col]
    wins = r[r > 0]
    losses = r[r <= 0]
    n = len(r)
    wr = len(wins) / n * 100.0
    avg = r.mean()
    gross_w = wins.sum()
    gross_l = abs(losses.sum())
    pf = (gross_w / gross_l) if gross_l > 0 else float("inf")
    return {
        "N": n,
        "WR": wr,
        "AvgR": avg,
        "PF": pf,
        "AvgWin": wins.mean() if len(wins) else 0.0,
        "AvgLoss": losses.mean() if len(losses) else 0.0,
    }


def split_is_oos(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    oos = trades[(trades["date"] >= OOS_START) & (trades["date"] <= OOS_END)]
    is_ = trades[trades["date"] >= IS_START]
    return is_, oos


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 120)
    print("  ORB IS/OOS grid — US EQUITIES (pre-registered template)")
    print(f"  Range 09:30-10:30 ET  Trade 10:30-15:30 ET  RR={RR_RATIO}  "
          f"range%={MIN_RANGE_PCT}-{MAX_RANGE_PCT}  costs=${COST_PER_SHARE}/sh")
    print(f"  IS = {IS_START.date()}+   OOS = {OOS_START.date()} .. {OOS_END.date()}")
    print("=" * 120)

    rows = []
    for sym in UNIVERSE:
        try:
            trades = run_one(sym)
        except Exception as exc:
            print(f"  {sym}: load/run failed — {type(exc).__name__}: {exc}")
            continue
        is_t, oos_t = split_is_oos(trades)
        im = metrics(is_t)
        om = metrics(oos_t)
        oms = metrics(oos_t, slip=True)
        rows.append((sym, trades, im, om, oms))
        per_yr = om["N"] / OOS_YEARS if OOS_YEARS else 0
        im_pf = f"{im['PF']:.2f}" if im['PF'] != float('inf') else " inf"
        om_pf = f"{om['PF']:.2f}" if om['PF'] != float('inf') else " inf"
        print(f"  {sym:<6} | "
              f"IS  N={im['N']:>4} WR={im['WR']:>5.1f}% AvgR={im['AvgR']:>+6.3f} PF={im_pf:>5} | "
              f"OOS N={om['N']:>4} ({per_yr:>4.1f}/yr) WR={om['WR']:>5.1f}% "
              f"AvgR={om['AvgR']:>+6.3f} PF={om_pf:>5} | "
              f"AvgR_slip={oms['AvgR']:>+6.3f}")

    # Decision gate
    print()
    print("=" * 120)
    print("  DECISION GATE: OOS AvgR_slip ≥ +0.05R AND OOS N/yr ≥ 20")
    print("=" * 120)
    survivors = []
    for sym, trades, im, om, oms in rows:
        per_yr = om["N"] / OOS_YEARS
        if oms["AvgR"] >= 0.05 and per_yr >= 20:
            survivors.append((sym, om, oms, per_yr))
    if not survivors:
        print("  No instrument passes the gate. "
              "ORB-first-hour structure has no exploitable post-cost edge on these "
              "15 US equities at this pre-registered template.")
    else:
        print(f"  {len(survivors)} survivor(s):")
        for sym, om, oms, per_yr in survivors:
            print(f"    {sym}: N={om['N']} ({per_yr:.1f}/yr)  "
                  f"WR={om['WR']:.1f}%  AvgR_raw={om['AvgR']:+.3f}  "
                  f"AvgR_slip={oms['AvgR']:+.3f}  PF_raw={om['PF']:.2f}")

    # Aggregated portfolio-level summary
    print()
    print("=" * 120)
    print("  AGGREGATE — equal-weighted across all 15 tickers")
    print("=" * 120)
    all_is = pd.concat([t for _, t, _, _, _ in rows
                        if not t.empty
                        and not (t[(t["date"] >= IS_START)]).empty])
    all_oos = pd.concat([t for _, t, _, _, _ in rows
                         if not t.empty])
    all_is_split, all_oos_split = split_is_oos(all_oos)
    am = metrics(all_is_split)
    bm = metrics(all_oos_split)
    bms = metrics(all_oos_split, slip=True)
    print(f"  IS  N={am['N']}  WR={am['WR']:.1f}%  AvgR={am['AvgR']:+.3f}  PF={am['PF']:.2f}")
    print(f"  OOS N={bm['N']}  WR={bm['WR']:.1f}%  AvgR={bm['AvgR']:+.3f}  PF={bm['PF']:.2f}")
    print(f"  OOS (after costs)  AvgR_slip={bms['AvgR']:+.3f}  PF_slip={bms['PF']:.2f}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()

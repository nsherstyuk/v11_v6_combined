"""ORB-FADE IS/OOS grid on US equities — pre-registered alternative hypothesis.

Companion to `orb_us_equities_grid.py`. Same universe, same data, same
time windows, **opposite direction**. Tests the hypothesis that the
breakout strategy's 8% WR (vs 28.6% breakeven for RR=2.5) is the
fingerprint of *anti-selection* — i.e., the breakout direction is the
wrong direction, and FADING the breakout has a real edge.

Pre-registered fade design — chosen before seeing fade results:

  Trigger:    bar.high ≥ range_high  → SHORT at range_high
              bar.low  ≤ range_low   → LONG  at range_low
  Target:     opposite side of range  (range_low for short, range_high
              for long)              → distance = 1 × range_width
  Stop:       half-range beyond breakout point
              short: range_high + 0.5×range_width
              long:  range_low  − 0.5×range_width   → distance = 0.5 × range_width
  RR:         target_dist / stop_dist = 1.0 / 0.5 = 2.0
              (breakeven WR = 1 / (1+2.0) = 33.3%)
  Time exit:  close at 15:30 ET if neither hits
  Conservative on both-breakouts-same-bar: skip
  Stop-first convention if stop & target both inside same post-entry bar

All other parameters identical to the breakout grid:
  Range window     09:30-10:30 ET
  Trade window     10:30-15:30 ET
  Range %          0.10% - 2.00% of mid
  RTH only         (09:30-16:00 ET; bars outside ignored)
  Costs            $0.03/share round-trip
  IS = 2024+, OOS = 2018-01-01 .. 2023-12-31

Decision gate: OOS AvgR_slip ≥ +0.05R AND N/yr ≥ 20.

Run:
    python -m v11.backtest.orb_us_equities_fade_grid
"""
from __future__ import annotations

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

RANGE_START_ET = "09:30"
TRADE_START_ET = "10:30"

STOP_FRACTION = 0.5   # stop placed 0.5 × range_width beyond breakout
MIN_RANGE_PCT = 0.10
MAX_RANGE_PCT = 2.00

OOS_START = pd.Timestamp("2018-01-01")
OOS_END   = pd.Timestamp("2023-12-31 23:59:59")
IS_START  = pd.Timestamp("2024-01-01")
OOS_YEARS = 6.0

COST_PER_SHARE = 0.03


# ── Load helper (identical to breakout grid) ────────────────────────────────

def load_ticker(symbol: str) -> pd.DataFrame:
    path = DATA_DIR / f"{symbol}.csv"
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize("UTC").dt.tz_convert("America/New_York")
    df["et"] = df["date"].dt.tz_localize(None)
    df["session"] = df["et"].dt.date
    df = df.set_index("et").sort_index()
    return df.between_time("09:30", "15:59:59")


# ── ORB-FADE per-day simulation ─────────────────────────────────────────────

def simulate_day_fade(day_bars: pd.DataFrame) -> Optional[dict]:
    """Fade strategy. Same trigger as breakout, opposite direction, opposite-
    side-of-range target, half-range stop."""
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

    trade = day_bars.between_time(TRADE_START_ET, "15:29:59")
    if trade.empty:
        return None

    entry = None
    direction = None
    entry_time = None
    for ts, bar in trade.iterrows():
        if bar["high"] >= range_high and bar["low"] <= range_low:
            return None  # both broken same bar — skip
        if bar["high"] >= range_high:
            entry = range_high
            direction = "short"      # FADE the up-break
            entry_time = ts
            break
        if bar["low"] <= range_low:
            entry = range_low
            direction = "long"       # FADE the down-break
            entry_time = ts
            break
    if entry is None:
        return None

    # Stop + target
    stop_dist = STOP_FRACTION * range_width
    if direction == "short":
        stop = range_high + stop_dist
        target = range_low                # opposite side
    else:  # long
        stop = range_low - stop_dist
        target = range_high

    # The R unit for fade is the stop distance (= 0.5 × range_width).
    # Target distance = range_width = 2 × stop_dist → target = 2R.
    r_unit = stop_dist

    post = trade.loc[entry_time:].iloc[1:]
    exit_price = None
    exit_reason = None
    for ts, bar in post.iterrows():
        if direction == "short":
            if bar["high"] >= stop:
                exit_price = stop
                exit_reason = "stop"
                break
            if bar["low"] <= target:
                exit_price = target
                exit_reason = "target"
                break
        else:  # long
            if bar["low"] <= stop:
                exit_price = stop
                exit_reason = "stop"
                break
            if bar["high"] >= target:
                exit_price = target
                exit_reason = "target"
                break

    # Time-exit at 15:30 if still open. Bug-fix 2026-05-11 — see same
    # fix in orb_us_equities_grid.py: `post` is filtered to ≤15:29:59 so
    # the between_time("15:30",…) call was always empty and the trade
    # was silently discarded. Take time-exit bars from day_bars instead.
    if exit_price is None:
        time_exit_bars = day_bars.between_time("15:30", "15:59:59").loc[entry_time:]
        if time_exit_bars.empty:
            return None
        exit_price = float(time_exit_bars.iloc[0]["close"])
        exit_reason = "time"

    if direction == "short":
        r_raw = (entry - exit_price) / r_unit
    else:
        r_raw = (exit_price - entry) / r_unit

    cost_r = COST_PER_SHARE / r_unit
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
    df = load_ticker(symbol)
    if df.empty:
        return pd.DataFrame()
    trades = []
    for _, day in df.groupby("session"):
        t = simulate_day_fade(day)
        if t is not None:
            trades.append(t)
    if not trades:
        return pd.DataFrame()
    out = pd.DataFrame(trades)
    out["date"] = pd.to_datetime(out["date"])
    return out


def metrics(trades: pd.DataFrame, slip: bool = False) -> dict:
    if trades.empty:
        return {"N": 0, "WR": 0.0, "AvgR": 0.0, "PF": 0.0}
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
    return {"N": n, "WR": wr, "AvgR": avg, "PF": pf}


def split_is_oos(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    oos = trades[(trades["date"] >= OOS_START) & (trades["date"] <= OOS_END)]
    is_ = trades[trades["date"] >= IS_START]
    return is_, oos


def main():
    print("=" * 120)
    print("  ORB-FADE IS/OOS grid — US EQUITIES (pre-registered alternative hypothesis)")
    print(f"  Trigger: 09:30-10:30 range break.  Direction: FADE (short up-breaks, long down-breaks).")
    print(f"  Target = opposite side of range (1 × width). Stop = {STOP_FRACTION} × width beyond break. RR=2.0  costs=${COST_PER_SHARE}/sh")
    print(f"  Breakeven WR for RR=2.0: 33.3%")
    print(f"  IS = {IS_START.date()}+   OOS = {OOS_START.date()} .. {OOS_END.date()}")
    print("=" * 120)

    rows = []
    for sym in UNIVERSE:
        try:
            trades = run_one(sym)
        except Exception as exc:
            print(f"  {sym}: run failed — {type(exc).__name__}: {exc}")
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
        print("  No instrument passes the gate.")
    else:
        print(f"  {len(survivors)} survivor(s):")
        for sym, om, oms, per_yr in survivors:
            print(f"    {sym}: N={om['N']} ({per_yr:.1f}/yr)  "
                  f"WR={om['WR']:.1f}%  AvgR_raw={om['AvgR']:+.3f}  "
                  f"AvgR_slip={oms['AvgR']:+.3f}  PF_raw={om['PF']:.2f}")

    print()
    print("=" * 120)
    print("  AGGREGATE — equal-weighted across all 15 tickers")
    print("=" * 120)
    all_t = pd.concat([t for _, t, _, _, _ in rows if not t.empty])
    is_all, oos_all = split_is_oos(all_t)
    print(f"  IS  N={metrics(is_all)['N']}  WR={metrics(is_all)['WR']:.1f}%  AvgR={metrics(is_all)['AvgR']:+.3f}  PF={metrics(is_all)['PF']:.2f}")
    print(f"  OOS N={metrics(oos_all)['N']}  WR={metrics(oos_all)['WR']:.1f}%  AvgR={metrics(oos_all)['AvgR']:+.3f}  PF={metrics(oos_all)['PF']:.2f}")
    print(f"  OOS (after costs)  AvgR_slip={metrics(oos_all, slip=True)['AvgR']:+.3f}  PF_slip={metrics(oos_all, slip=True)['PF']:.2f}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()

"""VWAP reversion analysis with CORRECT scale factors per pair."""
import pandas as pd, numpy as np
from pathlib import Path

DATA = Path(r"C:\nautilus0\data\1m_csv")

# Corrected: most FX pairs stored at actual scale, only EURUSD/USDJPY at x100
pairs = {
    "XAUUSD": (1, 0.30, "gold"),
    "EURUSD": (100, 0.0004, "fx_x100"),   # stored x100
    "GBPUSD": (1, 0.0003, "fx"),           # stored actual
    "AUDUSD": (1, 0.0004, "fx"),           # stored actual
    "NZDUSD": (1, 0.0005, "fx"),           # stored actual
    "USDCAD": (1, 0.0004, "fx"),           # stored actual
    "USDCHF": (1, 0.0004, "fx"),           # stored actual
    "USDJPY": (100, 0.04, "fx_x100"),      # stored x100
}

print(f"{'Pair':8s} {'Ticks/min':>10s} {'BR_std':>8s} {'BarRange':>12s} {'Cost':>12s} {'Cost%Range':>12s} {'Viable':>8s}")
print("-" * 72)

for name, (scale, cost, dtype) in pairs.items():
    try:
        df = pd.read_csv(DATA / f"{name.lower()}_1m_tick.csv", nrows=100000)
        if "Unnamed: 0" in df.columns:
            df = df.drop(columns=["Unnamed: 0"])
        tc = df["tick_count"]
        br = df["buy_ratio"]
        close = df["close"] / scale
        high = df["high"] / scale
        low = df["low"] / scale
        bar_range = (high - low).mean()
        cost_pct = cost / bar_range * 100 if bar_range > 0 else 999
        viable = "MAYBE" if cost_pct < 80 else ("YES" if cost_pct < 50 else "NO")
        print(f"{name:8s} {tc.mean():10.1f} {br.std():8.4f} {bar_range:12.6f} {cost:12.6f} {cost_pct:11.1f}% {viable:>8s}")
    except Exception as e:
        print(f"{name:8s}: ERROR {e}")

print()
print("Now testing VWAP reversion with ZERO cost to find raw edge...")
print("=" * 72)

for name, (scale, cost, dtype) in pairs.items():
    try:
        df = pd.read_csv(DATA / f"{name.lower()}_1m_tick.csv", nrows=500000)
        if "Unnamed: 0" in df.columns:
            df = df.drop(columns=["Unnamed: 0"])
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col] / scale
        
        # NY session only
        df["in_session"] = df["timestamp"].dt.hour.between(13, 20)
        sess = df[df["in_session"]].copy()
        
        if len(sess) < 1000:
            print(f"{name:8s}: Not enough session data")
            continue
        
        # Typical price and VWAP
        sess["typical"] = (sess["high"] + sess["low"] + sess["close"]) / 3
        sess["vol"] = sess["tick_count"].astype(float).replace(0, 1)
        sess["tp_vol"] = sess["typical"] * sess["vol"]
        
        # Cumulative VWAP per day
        sess["date"] = sess["timestamp"].dt.date
        sess["cum_tp_vol"] = sess.groupby("date")["tp_vol"].cumsum()
        sess["cum_vol"] = sess.groupby("date")["vol"].cumsum()
        sess["vwap"] = sess["cum_tp_vol"] / sess["cum_vol"]
        sess["vwap_dev"] = sess["close"] - sess["vwap"]
        
        # Rolling std of deviation
        sess["vwap_dev_std"] = sess["vwap_dev"].rolling(60, min_periods=20).std()
        sess["vwap_z"] = sess["vwap_dev"] / sess["vwap_dev_std"]
        
        # Measure: when Z > threshold, how much does price revert in next N bars?
        print(f"\n--- {name} (cost={cost:.6f}) ---")
        for z_thresh in [1.0, 1.5, 2.0]:
            for hold in [10, 30, 60]:
                above = sess[sess["vwap_z"] > z_thresh]
                below = sess[sess["vwap_z"] < -z_thresh]
                
                reverts = []
                for idx in above.index:
                    future = sess.loc[idx:idx+hold, "close"]
                    if len(future) > 1:
                        reverts.append(future.iloc[0] - future.iloc[-1])  # fade: expect drop
                
                for idx in below.index:
                    future = sess.loc[idx:idx+hold, "close"]
                    if len(future) > 1:
                        reverts.append(future.iloc[-1] - future.iloc[0])  # fade: expect rise
                
                if len(reverts) > 100:
                    avg_rev = np.mean(reverts)
                    wr = np.mean([1 if r > 0 else 0 for r in reverts])
                    # Express avg_rev as multiple of cost
                    cost_multiple = avg_rev / cost if cost > 0 else 0
                    print(f"  Z>{z_thresh} hold={hold:2d}min: "
                          f"N={len(reverts):6d}  WR={wr*100:5.1f}%  "
                          f"AvgRev={avg_rev:+.6f}  "
                          f"Rev/Cost={cost_multiple:+.3f}x  "
                          f"{'VIABLE' if cost_multiple > 1.2 else 'NO'}")
    except Exception as e:
        print(f"{name:8s}: ERROR {e}")

print()
print("=" * 72)
print("INTERPRETATION")
print("=" * 72)
print("""
Rev/Cost ratio tells us if the raw reversion edge exceeds costs:
  > 1.2x = potentially viable (need to account for spread, slippage)
  1.0-1.2x = marginal (costs likely eat it)
  < 1.0x = definitely unprofitable

If NO pair shows Rev/Cost > 1.2x, then VWAP mean-reversion is
not viable on any FX pair with current cost structure.
""")

"""Quick check: which pairs have viable cost/range ratio for VWAP mean-reversion?"""
import pandas as pd, numpy as np
from pathlib import Path

DATA = Path(r"C:\nautilus0\data\1m_csv")
pairs = {
    "XAUUSD": (1, 0.30),
    "EURUSD": (100, 0.0004),
    "GBPUSD": (100, 0.0003),
    "AUDUSD": (100, 0.0004),
    "NZDUSD": (100, 0.0005),
    "USDCAD": (100, 0.0004),
    "USDCHF": (100, 0.0004),
    "USDJPY": (100, 0.04),
}

print(f"{'Pair':8s} {'Ticks/min':>10s} {'BR_std':>8s} {'BarRange':>12s} {'Cost':>12s} {'Cost%Range':>12s} {'Viable':>8s}")
print("-" * 72)

for name, (scale, cost) in pairs.items():
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
print("Cost%Range = transaction cost as % of average 1-min bar range")
print("  < 50% = viable for mean-reversion (cost smaller than typical move)")
print("  50-80% = marginal (need strong edge)")
print("  > 80% = almost certainly unprofitable for mean-reversion")
print()
print("NOTE: For VWAP fade, we need the AVERAGE REVERSION MOVE to exceed cost.")
print("The reversion move is typically 30-60% of the deviation,")
print("and deviations at Z=1.5 are ~1.5 * std of deviation.")
print("So we need: 0.3-0.6 * 1.5 * dev_std > cost")
print()

# Now compute the actual VWAP reversion stats for each pair
print("=" * 72)
print("VWAP REVERSION ANALYSIS (NY session 13-21 UTC, zero cost)")
print("=" * 72)

for name, (scale, cost) in pairs.items():
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
        
        # Measure: when Z > 1.5, how much does price revert in next N bars?
        for z_thresh in [1.0, 1.5, 2.0]:
            for hold in [10, 30, 60]:
                above = sess[sess["vwap_z"] > z_thresh].copy()
                below = sess[sess["vwap_z"] < -z_thresh].copy()
                
                reverts_above = []
                reverts_below = []
                
                for idx in above.index:
                    future = sess.loc[idx:idx+hold, "close"]
                    if len(future) > 1:
                        reversion = future.iloc[0] - future.iloc[-1]  # positive = price fell back
                        reverts_above.append(reversion)
                
                for idx in below.index:
                    future = sess.loc[idx:idx+hold, "close"]
                    if len(future) > 1:
                        reversion = future.iloc[-1] - future.iloc[0]  # positive = price rose back
                        reverts_below.append(reversion)
                
                all_reverts = reverts_above + reverts_below
                if len(all_reverts) > 100:
                    avg_rev = np.mean(all_reverts)
                    wr = np.mean([1 if r > 0 else 0 for r in all_reverts])
                    net_edge = avg_rev - cost  # after cost
                    print(f"  {name:8s} Z>{z_thresh} hold={hold:2d}min: "
                          f"N={len(all_reverts):6d}  WR={wr*100:5.1f}%  "
                          f"AvgRev={avg_rev:+.6f}  Cost={cost:.6f}  "
                          f"NetEdge={net_edge:+.6f}  {'VIABLE' if net_edge > 0 else 'NO'}")
    except Exception as e:
        print(f"{name:8s}: ERROR {e}")

print()
print("=" * 72)
print("CONCLUSION")
print("=" * 72)

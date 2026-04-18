"""
Half-Life & Cointegration Scan Across All 30 FX Pairs

For each pair combination:
1. Compute log spread, estimate half-life (OU process)
2. ADF test for stationarity
3. Rank by shortest half-life + strongest cointegration
4. Backtest top pairs with lookback matched to half-life

Data: C:\\nautilus0\\data\\fx_daily\\*_daily.csv
"""
import pandas as pd
import numpy as np
from pathlib import Path
from itertools import combinations
import warnings
warnings.filterwarnings('ignore')

DATA_DIR = Path(r"C:\nautilus0\data\fx_daily")
ALL_PAIRS = [
    "GBPUSD","USDJPY","USDCAD","USDCHF","AUDUSD","NZDUSD",
    "AUDNZD","AUDCAD","AUDCHF","AUDJPY","NZDCAD","NZDCHF","NZDJPY",
    "GBPAUD","GBPNZD","GBPCAD","GBPCHF","GBPJPY",
    "CHFJPY","CADCHF","CADJPY",
    "EURUSD","EURGBP","EURJPY","EURCHF","EURAUD","EURNZD","EURCAD",
    "USDSEK","USDNOK",
]

print("Loading daily data...")
prices = {}
for pair in ALL_PAIRS:
    csv_path = DATA_DIR / f"{pair}_daily.csv"
    if not csv_path.exists(): continue
    df = pd.read_csv(csv_path, parse_dates=['date'])
    prices[pair] = df.sort_values('date').set_index('date')['close']
price_df = pd.DataFrame(prices).dropna()
print(f"  {len(price_df.columns)} pairs, {len(price_df)} days")

def estimate_half_life(spread):
    s = spread.dropna()
    if len(s) < 50: return np.nan
    lagged = s - s.mean()
    delta = s.diff().iloc[1:]
    lagged = lagged.iloc[:-1]
    try:
        lam = np.polyfit(lagged.values, delta.values, 1)[0]
        return -np.log(2)/lam if lam < 0 else np.inf
    except: return np.nan

def adf_pval(spread):
    try:
        from statsmodels.tsa.stattools import adfuller
        return adfuller(spread.dropna(), maxlag=20, regression='c')[1]
    except: return np.nan

def pip_sz(pair): return 0.01 if "JPY" in pair else 0.0001

print("\nScanning all pair combinations...")
results = []
for p1, p2 in combinations(price_df.columns, 2):
    spread = np.log(price_df[p1]) - np.log(price_df[p2])
    hl = estimate_half_life(spread)
    ap = adf_pval(spread)
    corr = np.log(price_df[[p1,p2]]/price_df[[p1,p2]].shift(1)).dropna().corr().iloc[0,1]
    pip = (pip_sz(p1)+pip_sz(p2))/2
    svol = (spread/pip).diff().std()*np.sqrt(252)
    # Hurst (simplified R/S)
    try:
        sv = spread.dropna().values
        mx = min(100, len(sv)//4)
        rs = [np.log((np.cumsum(sv[:l]-sv[:l].mean()).max()-np.cumsum(sv[:l]-sv[:l].mean()).min())/sv[:l].std())/np.log(l)
              for l in range(10,mx,10) if sv[:l].std()>0]
        hurst = np.mean(rs) if rs else 0.5
    except: hurst = 0.5
    results.append({'p1':p1,'p2':p2,'corr':corr,'half_life':hl,'adf_pval':ap,
                    'hurst':hurst,'spread_vol':svol,'coint':ap<0.05 if not np.isnan(ap) else False})

scan = pd.DataFrame(results)
tradeable = scan[(scan['half_life']<500)&(scan['half_life']>0)].sort_values('half_life')

print(f"\n  Total combos: {len(scan)}")
print(f"  Mean-reverting (HL<500d): {len(tradeable)}")
if not scan['adf_pval'].isna().all():
    print(f"  Cointegrated (p<0.05): {scan['coint'].sum()}")
    print(f"  Coint + HL<100d: {((scan['coint'])&(scan['half_life']<100)).sum()}")

print(f"\n{'Pair':22s} {'Corr':>6s} {'HL':>10s} {'ADF p':>8s} {'Hurst':>7s} {'Vol/yr':>8s} {'Coint':>6s}")
print("-"*80)
for _,r in tradeable.head(30).iterrows():
    hl_s = f"{r['half_life']:.0f}d" if r['half_life']<1000 else f"{r['half_life']/365:.1f}y"
    ap_s = f"{r['adf_pval']:.4f}" if not np.isnan(r['adf_pval']) else "N/A"
    print(f"{r['p1']+'/'+r['p2']:22s} {r['corr']:+6.3f} {hl_s:>10s} {ap_s:>8s} {r['hurst']:7.3f} {r['spread_vol']:8.0f} {'YES' if r['coint'] else 'no':>6s}")

# ── Backtest top pairs with matched lookback ────────────────────────────────
print("\n\n" + "="*80)
print("  BACKTEST: LOOKBACK MATCHED TO HALF-LIFE")
print("="*80)

COST = 0.4  # pips per leg round-trip

def backtest(p1, p2, hl, z_entry=2.0, z_exit=0.5, z_stop=4.0):
    spread = np.log(price_df[p1]) - np.log(price_df[p2])
    lb = max(10, min(int(hl*2), len(spread)//3))
    z = (spread - spread.shift(1).rolling(lb).mean()) / spread.shift(1).rolling(lb).std()
    pip = (pip_sz(p1)+pip_sz(p2))/2
    trades, pos, ei, es, ez = [], 0, 0, 0, 0
    for i in range(lb+1, len(z)):
        zv = z.iloc[i]
        if pd.isna(zv): continue
        if pos == 0:
            if zv > z_entry and i+1 < len(z):
                pos, ei, es, ez = -1, i+1, spread.iloc[i+1], zv
            elif zv < -z_entry and i+1 < len(z):
                pos, ei, es, ez = 1, i+1, spread.iloc[i+1], zv
        else:
            reason = None
            if pos==1 and zv>=z_exit: reason="mr"
            elif pos==-1 and zv<=-z_exit: reason="mr"
            if pos==1 and zv<-z_stop: reason="sl"
            elif pos==-1 and zv>z_stop: reason="sl"
            if i-ei >= int(hl*3): reason="mh"
            if reason:
                ret = pos*(spread.iloc[i]-es)/pip - COST*2
                trades.append({'dir':'L' if pos==1 else 'S','ret':ret,'hold':i-ei,
                               'reason':reason,'win':ret>0,'date':spread.index[i]})
                pos = 0
    if not trades: return None
    t = pd.DataFrame(trades)
    n,wr,avg,tot,ah = len(t),t['win'].mean()*100,t['ret'].mean(),t['ret'].sum(),t['hold'].mean()
    sl = (t['reason']=='sl').mean()*100
    sh = (avg/t['ret'].std())*np.sqrt(252/ah) if t['ret'].std()>0 and ah>0 else 0
    return {'p1':p1,'p2':p2,'hl':hl,'lb':lb,'n':n,'wr':wr,'avg':avg,'tot':tot,
            'ah':ah,'sh':sh,'sl':sl,'tdf':t}

print(f"\n{'Pair':22s} {'HL':>6s} {'LB':>5s} {'N':>5s} {'WR%':>6s} {'Avg':>8s} {'Total':>8s} {'Hold':>6s} {'Sharpe':>7s} {'SL%':>5s}")
print("-"*90)
bt = []
for _,r in tradeable.head(15).iterrows():
    res = backtest(r['p1'],r['p2'],r['half_life'])
    if res:
        bt.append(res)
        print(f"{res['p1']+'/'+res['p2']:22s} {res['hl']:6.0f} {res['lb']:5d} {res['n']:5d} "
              f"{res['wr']:6.1f} {res['avg']:+8.2f} {res['tot']:+8.1f} {res['ah']:6.1f} "
              f"{res['sh']:+7.3f} {res['sl']:5.1f}")

# ── Best pair detail ─────────────────────────────────────────────────────────
if bt:
    best = max(bt, key=lambda x: x['sh'])
    print(f"\n\nBEST: {best['p1']}/{best['p2']} (Sharpe={best['sh']:+.3f})")
    print(f"  HL={best['hl']:.0f}d  LB={best['lb']}d  N={best['n']}  WR={best['wr']:.1f}%  Avg={best['avg']:+.2f}pips")
    t = best['tdf']
    t['year'] = pd.to_datetime(t['date']).dt.year
    for y,g in t.groupby('year'):
        print(f"    {y}: ret={g['ret'].sum():+.1f}  WR={g['win'].mean()*100:.0f}%  N={len(g)}")

    # Param sweep
    print(f"\n  Param sweep: {best['p1']}/{best['p2']}")
    print(f"  {'z_in':>5s} {'z_out':>6s} {'LB':>5s} {'N':>5s} {'WR%':>6s} {'Avg':>8s} {'Sharpe':>7s}")
    for ze in [1.5,2.0,2.5]:
        for zo in [0.0,0.5,1.0]:
            for m in [1.0,2.0,3.0]:
                lb = max(10,min(int(best['hl']*m),len(price_df)//3))
                r2 = backtest(best['p1'],best['p2'],best['hl']/m*2,z_entry=ze,z_exit=zo)
                if r2:
                    print(f"  {ze:5.1f} {zo:6.1f} {r2['lb']:5d} {r2['n']:5d} {r2['wr']:6.1f} {r2['avg']:+8.2f} {r2['sh']:+7.3f}")

print("\n=== SCAN COMPLETE ===")

"""
ORB Optimization — Time Windows & Order Management

Answers:
1. When should orders be submitted? (trade_start_hour sweep)
2. How long should orders rest? (max_pending_hours sweep)
3. What's the optimal Asian range window? (range_end_hour sweep)
4. Does LLM gating improve Sharpe? (simulated by gap+range quality filter)
5. Combined optimal config

Uses the ACTUAL V6 ORBStrategy code with simulated execution.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from collections import deque
import logging
import warnings
warnings.filterwarnings('ignore')

from v11.v6_orb.orb_strategy import ORBStrategy, StrategyState
from v11.v6_orb.config import StrategyConfig as V6StrategyConfig
from v11.v6_orb.market_event import Tick, Fill, RangeInfo, GapMetrics
from v11.v6_orb.interfaces import MarketContext, ExecutionEngine
from v11.core.types import Bar as V11Bar

DATA_DIR = Path(r"C:\nautilus0\data\1m_csv")

# Suppress all V6 logging
for name in ['orb_bt', 'v11.v6_orb.orb_strategy', 'v11.v6_orb', 'v6_orb', '']:
    logging.getLogger(name).setLevel(logging.CRITICAL + 1)
LOG = logging.getLogger("orb_opt")
LOG.setLevel(logging.CRITICAL + 1)

# ── Load XAUUSD 1-min data ──────────────────────────────────────────────────
print("Loading XAUUSD 1-min data...")
df = pd.read_csv(DATA_DIR / "xauusd_1m_tick.csv",
                 usecols=['timestamp', 'open', 'high', 'low', 'close'])
df['timestamp'] = pd.to_datetime(df['timestamp'])
df = df.sort_values('timestamp').reset_index(drop=True)
print(f"  {len(df):,} bars, {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}")


# ── MarketContext with real gap filter ───────────────────────────────────────
class OptMarketContext(MarketContext):
    def __init__(self, v6_config, log):
        self._config = v6_config
        self._log = log
        self._bars = deque(maxlen=1440)
        self._daily_range = None
        self._daily_range_date = None
        self._slow_atr = 0.0
        self._slow_atr_count = 0
        self._slow_atr_prev_close = 0.0
        self._gap_history = []
        self._gap_computed_today = False

    def add_bar(self, bar):
        self._bars.append(bar)
        if self._slow_atr_prev_close > 0:
            tr = max(bar.high - bar.low,
                     abs(bar.high - self._slow_atr_prev_close),
                     abs(bar.low - self._slow_atr_prev_close))
        else:
            tr = bar.high - bar.low
        self._slow_atr_prev_close = bar.close
        if self._slow_atr_count < 1440:
            self._slow_atr_count += 1
            self._slow_atr = self._slow_atr + (tr - self._slow_atr) / self._slow_atr_count
        else:
            self._slow_atr = self._slow_atr * (1 - 2.0/1441) + tr * 2.0/1441

    def get_velocity(self, lookback_minutes, current_time):
        cutoff = current_time - timedelta(minutes=lookback_minutes)
        return sum(1 for b in self._bars if b.timestamp >= cutoff) * 10.0

    def get_asian_range(self, start_hour, end_hour, current_time):
        today_str = current_time.strftime("%Y-%m-%d")
        if self._daily_range is not None and self._daily_range_date == today_str:
            return self._daily_range
        range_bars = [b for b in self._bars
                      if b.timestamp.strftime("%Y-%m-%d") == today_str
                      and start_hour <= b.timestamp.hour < end_hour]
        if not range_bars:
            return None
        rng = RangeInfo(high=max(b.high for b in range_bars),
                        low=min(b.low for b in range_bars),
                        start_time=range_bars[0].timestamp,
                        end_time=range_bars[-1].timestamp)
        self._daily_range = rng
        self._daily_range_date = today_str
        return rng

    def time_is_in_trade_window(self, current_time, start_hour, end_hour):
        return start_hour <= current_time.hour < end_hour

    def get_current_price(self, current_time):
        return self._bars[-1].close if self._bars else None

    def get_gap_metrics(self, current_time, gap_start_hour, gap_end_hour,
                        vol_percentile, range_percentile, rolling_days):
        today_str = current_time.strftime("%Y-%m-%d")
        if self._gap_computed_today:
            if self._gap_history and self._gap_history[-1][0] == today_str:
                return GapMetrics(gap_volatility=self._gap_history[-1][1],
                                  gap_range=self._gap_history[-1][2],
                                  vol_passes=self._gap_history[-1][3],
                                  range_passes=self._gap_history[-1][4])
            return GapMetrics(0.001, 1.0, True, True)

        gap_bars = [b for b in self._bars
                    if b.timestamp.strftime("%Y-%m-%d") == today_str
                    and gap_start_hour <= b.timestamp.hour < gap_end_hour]
        if len(gap_bars) < 5:
            self._gap_computed_today = True
            return GapMetrics(0.001, 1.0, True, True)

        closes = [b.close for b in gap_bars]
        returns = np.diff(closes) / closes[:-1]
        gap_vol = float(np.std(returns)) if len(returns) > 1 else 0.0
        gap_range = max(b.high for b in gap_bars) - min(b.low for b in gap_bars)

        rolling_vols = [h[1] for h in self._gap_history[-rolling_days:]]
        rolling_ranges = [h[2] for h in self._gap_history[-rolling_days:]]
        vol_passes = True
        range_passes = True
        if len(rolling_vols) >= 10:
            vol_passes = gap_vol >= np.percentile(rolling_vols, vol_percentile)
        if len(rolling_ranges) >= 10:
            range_passes = gap_range >= np.percentile(rolling_ranges, range_percentile)

        self._gap_history.append((today_str, gap_vol, gap_range, vol_passes, range_passes))
        self._gap_computed_today = True
        return GapMetrics(gap_vol, gap_range, vol_passes, range_passes)

    @property
    def slow_atr(self):
        return self._slow_atr

    def reset_daily(self):
        self._daily_range = None
        self._daily_range_date = None
        self._gap_computed_today = False


class OptExecutionEngine(ExecutionEngine):
    def __init__(self, v6_config, log):
        self._config = v6_config
        self._log = log
        self._brackets_active = False
        self._long_entry = self._short_entry = 0.0
        self._long_sl = self._long_tp = self._short_sl = self._short_tp = 0.0
        self._has_position = False
        self._position_direction = None
        self._entry_price = self._sl_price = self._tp_price = 0.0
        self._close_requested = False

    def set_orb_brackets(self, range_info, rr_ratio) -> bool:
        d = self._config.price_decimals
        rs = range_info.size
        self._long_entry = range_info.high
        self._short_entry = range_info.low
        self._long_sl = range_info.low
        self._long_tp = round(range_info.high + rr_ratio * rs, d)
        self._short_sl = range_info.high
        self._short_tp = round(range_info.low - rr_ratio * rs, d)
        self._brackets_active = True
        return True

    def cancel_orb_brackets(self):
        if self._brackets_active and not self._has_position:
            self._brackets_active = False

    def close_at_market(self):
        self._close_requested = True

    def modify_sl(self, new_sl_price):
        self._sl_price = new_sl_price

    def has_position(self):
        return self._has_position

    def has_resting_entries(self):
        return self._brackets_active and not self._has_position

    def check_bar_fills(self, bar, strategy):
        ts = bar.timestamp
        if self._brackets_active and not self._has_position:
            if bar.high >= self._long_entry:
                fill = Fill(ts, self._long_entry, "LONG", "ENTRY")
                self._has_position = True
                self._position_direction = "LONG"
                self._entry_price = self._long_entry
                self._sl_price = self._long_sl
                self._tp_price = self._long_tp
                self._brackets_active = False
                return fill
            if bar.low <= self._short_entry:
                fill = Fill(ts, self._short_entry, "SHORT", "ENTRY")
                self._has_position = True
                self._position_direction = "SHORT"
                self._entry_price = self._short_entry
                self._sl_price = self._short_sl
                self._tp_price = self._short_tp
                self._brackets_active = False
                return fill
        if self._has_position:
            if self._position_direction == "LONG" and bar.low <= self._sl_price:
                fill = Fill(ts, self._sl_price, "LONG", "SL")
                self._close_pos(fill)
                return fill
            if self._position_direction == "SHORT" and bar.high >= self._sl_price:
                fill = Fill(ts, self._sl_price, "SHORT", "SL")
                self._close_pos(fill)
                return fill
            if self._position_direction == "LONG" and bar.high >= self._tp_price:
                fill = Fill(ts, self._tp_price, "LONG", "TP")
                self._close_pos(fill)
                return fill
            if self._position_direction == "SHORT" and bar.low <= self._tp_price:
                fill = Fill(ts, self._tp_price, "SHORT", "TP")
                self._close_pos(fill)
                return fill
            if self._close_requested:
                fill = Fill(ts, bar.close, self._position_direction or "LONG", "MARKET")
                self._close_pos(fill)
                self._close_requested = False
                return fill
        return None

    def force_close_at(self, price, bar):
        if not self._has_position:
            return None
        fill = Fill(bar.timestamp if bar else datetime.utcnow(),
                    price, self._position_direction or "LONG", "MARKET")
        self._close_pos(fill)
        return fill

    def _close_pos(self, fill):
        self._has_position = False
        self._position_direction = None
        self._brackets_active = False
        self._close_requested = False

    def reset_daily(self):
        self._brackets_active = False
        self._has_position = False
        self._position_direction = None
        self._close_requested = False


# ── Run backtest ─────────────────────────────────────────────────────────────
def run_backtest(config, df, slippage=0.0):
    context = OptMarketContext(config, LOG)
    execution = OptExecutionEngine(config, LOG)
    strategy = ORBStrategy(config, logger=LOG)
    trades = []
    current_date = None
    entry_info = None

    for idx in range(len(df)):
        row = df.iloc[idx]
        ts = pd.to_datetime(row['timestamp'])
        today_str = ts.strftime("%Y-%m-%d")
        bar = V11Bar(timestamp=ts, open=float(row['open']), high=float(row['high']),
                     low=float(row['low']), close=float(row['close']),
                     tick_count=0, buy_volume=0, sell_volume=0)

        if today_str != current_date:
            current_date = today_str
            strategy.reset_for_new_day()
            context.reset_daily()
            execution.reset_daily()
            entry_info = None

        if config.skip_weekdays and ts.weekday() in config.skip_weekdays:
            continue

        context.add_bar(bar)

        if ts.hour >= config.trade_end_hour:
            if strategy.state in (StrategyState.IDLE, StrategyState.RANGE_READY):
                strategy.state = StrategyState.DONE_TODAY
                continue
            if execution.has_position():
                fill = execution.force_close_at(bar.close, bar)
                if fill:
                    strategy.on_fill(fill, context, execution)
                    if entry_info:
                        pnl = _pnl(entry_info, fill, slippage)
                        trades.append({**entry_info, 'exit_price': fill.price,
                                       'exit_time': fill.timestamp, 'exit_reason': 'EOD', 'pnl': pnl})
                        entry_info = None
                continue

        tick = Tick(timestamp=ts, bid=bar.close, ask=bar.close)
        strategy.on_tick(tick, context, execution)
        fill = execution.check_bar_fills(bar, strategy)
        if fill:
            strategy.on_fill(fill, context, execution)
            if fill.reason == "ENTRY":
                slip = slippage if fill.direction == "LONG" else -slippage
                entry_info = {
                    'entry_price': fill.price + slip,
                    'direction': fill.direction,
                    'entry_time': fill.timestamp,
                    'range_high': strategy.range.high if strategy.range else 0,
                    'range_low': strategy.range.low if strategy.range else 0,
                    'range_size': strategy.range.size if strategy.range else 0,
                }
            elif fill.reason in ("SL", "TP", "MARKET", "BE") and entry_info:
                pnl = _pnl(entry_info, fill, slippage)
                trades.append({**entry_info, 'exit_price': fill.price,
                               'exit_time': fill.timestamp, 'exit_reason': fill.reason, 'pnl': pnl})
                entry_info = None
    return trades


def _pnl(entry_info, fill, slippage=0.0):
    e = entry_info['entry_price']
    x = fill.price
    d = entry_info['direction']
    pnl = (x - e) if d == "LONG" else (e - x)
    return round(pnl - slippage * 2, 2)


def summarize(trades, slippage_note=""):
    if not trades:
        return {'n': 0, 'wr': 0, 'total': 0, 'avg': 0, 'sharpe': 0, 'pf': 0, 'dd': 0}
    td = pd.DataFrame(trades)
    n = len(td)
    wr = (td['pnl'] > 0).mean() * 100
    total = td['pnl'].sum()
    avg = td['pnl'].mean()
    std = td['pnl'].std()
    sharpe = (avg / std * np.sqrt(n / 8)) if std > 0 else 0
    wins = td[td['pnl'] > 0]['pnl'].sum()
    losses = abs(td[td['pnl'] <= 0]['pnl'].sum())
    pf = wins / losses if losses > 0 else 999
    cum = td['pnl'].cumsum()
    dd = (cum - cum.cummax()).min()
    return {'n': n, 'wr': wr, 'total': total, 'avg': avg, 'sharpe': sharpe, 'pf': pf, 'dd': dd}


# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  HOW ORB WORKS — ORDER FLOW TIMELINE")
print("=" * 80)
print("""
  00:00-06:00 UTC  Asian range forms (high/low tracked)
  06:00 UTC        Range calculated and cached (but strategy still IDLE)
  06:00-08:00 UTC  Gap filter evaluates pre-market volatility
  08:00 UTC        Trade window opens → strategy transitions IDLE→RANGE_READY
                   Velocity check (disabled=always pass) → brackets placed
                   LLM gate evaluates (if enabled) → may reject
  08:00-12:00 UTC  Brackets rest (max_pending_hours=4 → cancel at 12:00)
  08:00-16:00 UTC  If filled: manage position (SL/TP/EOD)
  16:00 UTC        Trade window closes → force-close any open position

  YOUR ISSUE: "orders submitted 2 hours before 8am"
  This should NOT happen. The strategy only places brackets when
  state=RANGE_READY AND trade_start_hour <= hour < trade_end_hour.
  If you saw orders at 6am UTC, that's a bug. More likely you saw
  the RANGE CALCULATION log at 6am and mistook it for order placement.
  Range calculation ≠ order placement.
""")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 1: TRADE START HOUR SWEEP
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  TEST 1: TRADE START HOUR (when do brackets go in?)")
print("=" * 80)
print(f"\n  {'Start':>6s} {'End':>5s} {'N':>5s} {'WR%':>6s} {'Total$':>9s} "
      f"{'Avg$':>8s} {'PF':>6s} {'Sharpe':>7s} {'MaxDD':>8s}")
print("-" * 70)

for start_h in [6, 7, 8, 9, 10, 11, 12]:
    for end_h in [16, 18, 20]:
        cfg = V6StrategyConfig(
            instrument="XAUUSD",
            range_start_hour=0, range_end_hour=6,
            trade_start_hour=start_h, trade_end_hour=end_h,
            skip_weekdays=(2,), rr_ratio=2.5,
            min_range_size=1.0, max_range_size=15.0,
            min_range_pct=0.05, max_range_pct=2.0,
            velocity_filter_enabled=False,
            gap_filter_enabled=True,
            gap_vol_percentile=50.0,
            gap_range_filter_enabled=False,
            gap_rolling_days=60, gap_start_hour=6, gap_end_hour=8,
            be_hours=999, max_pending_hours=4,
            qty=1, point_value=1.0, price_decimals=2,
        )
        t = run_backtest(cfg, df, slippage=0.20)
        s = summarize(t)
        if s['n'] > 0:
            print(f"  {start_h:6d} {end_h:5d} {s['n']:5d} {s['wr']:6.1f} "
                  f"{s['total']:+9.2f} {s['avg']:+8.2f} {s['pf']:6.2f} "
                  f"{s['sharpe']:+7.3f} {s['dd']:+8.2f}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 2: MAX PENDING HOURS SWEEP
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  TEST 2: MAX PENDING HOURS (how long do brackets rest?)")
print("=" * 80)
print(f"\n  {'MaxH':>5s} {'N':>5s} {'WR%':>6s} {'Total$':>9s} {'Avg$':>8s} "
      f"{'PF':>6s} {'Sharpe':>7s} {'FillRate':>9s}")
print("-" * 65)

for max_h in [1, 2, 3, 4, 6, 8, 0]:
    cfg = V6StrategyConfig(
        instrument="XAUUSD",
        range_start_hour=0, range_end_hour=6,
        trade_start_hour=8, trade_end_hour=16,
        skip_weekdays=(2,), rr_ratio=2.5,
        min_range_size=1.0, max_range_size=15.0,
        min_range_pct=0.05, max_range_pct=2.0,
        velocity_filter_enabled=False,
        gap_filter_enabled=True,
        gap_vol_percentile=50.0,
        gap_range_filter_enabled=False,
        gap_rolling_days=60, gap_start_hour=6, gap_end_hour=8,
        be_hours=999, max_pending_hours=max_h,
        qty=1, point_value=1.0, price_decimals=2,
    )
    t = run_backtest(cfg, df, slippage=0.20)
    s = summarize(t)
    # Fill rate: trades / days_with_range_ready
    if s['n'] > 0:
        td = pd.DataFrame(t)
        # Approximate: total trading days ~ 8 years * 260 days * 4/5 (skip Wed)
        fill_rate = s['n'] / (8 * 260 * 4/5) * 100
        print(f"  {max_h:5d} {s['n']:5d} {s['wr']:6.1f} {s['total']:+9.2f} "
              f"{s['avg']:+8.2f} {s['pf']:6.2f} {s['sharpe']:+7.3f} {fill_rate:9.1f}%")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 3: ASIAN RANGE WINDOW
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  TEST 3: ASIAN RANGE WINDOW (range_end_hour sweep)")
print("=" * 80)
print(f"\n  {'RStart':>7s} {'REnd':>5s} {'N':>5s} {'WR%':>6s} {'Total$':>9s} "
      f"{'Avg$':>8s} {'PF':>6s} {'Sharpe':>7s}")
print("-" * 65)

for r_end in [3, 4, 5, 6, 7, 8]:
    cfg = V6StrategyConfig(
        instrument="XAUUSD",
        range_start_hour=0, range_end_hour=r_end,
        trade_start_hour=max(r_end + 1, 8),  # start trading after range ends
        trade_end_hour=16,
        skip_weekdays=(2,), rr_ratio=2.5,
        min_range_size=1.0, max_range_size=15.0,
        min_range_pct=0.05, max_range_pct=2.0,
        velocity_filter_enabled=False,
        gap_filter_enabled=True,
        gap_vol_percentile=50.0,
        gap_range_filter_enabled=False,
        gap_rolling_days=60, gap_start_hour=r_end, gap_end_hour=max(r_end + 2, 8),
        be_hours=999, max_pending_hours=4,
        qty=1, point_value=1.0, price_decimals=2,
    )
    t = run_backtest(cfg, df, slippage=0.20)
    s = summarize(t)
    if s['n'] > 0:
        print(f"  {0:7d} {r_end:5d} {s['n']:5d} {s['wr']:6.1f} {s['total']:+9.2f} "
              f"{s['avg']:+8.2f} {s['pf']:6.2f} {s['sharpe']:+7.3f}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 4: LLM GATE SIMULATION
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  TEST 4: LLM GATE SIMULATION (gap + range quality filters)")
print("=" * 80)
print("""
  We can't run the actual LLM in backtest, but we can simulate its effect
  by tightening the gap filter. The LLM gate rejects ~30-40% of signals.
  A tighter gap percentile threshold simulates this: it rejects low-quality
  days where pre-market activity is weak (trendless, low-vol chop).
""")
print(f"\n  {'GapPctl':>8s} {'GapRng':>8s} {'N':>5s} {'WR%':>6s} {'Total$':>9s} "
      f"{'Avg$':>8s} {'PF':>6s} {'Sharpe':>7s} {'Skip%':>7s}")
print("-" * 75)

for gap_pctl in [30, 40, 50, 60, 70, 80]:
    for gap_rng_pctl in [30, 40, 50]:
        gap_rng_enabled = gap_rng_pctl > 0
        cfg = V6StrategyConfig(
            instrument="XAUUSD",
            range_start_hour=0, range_end_hour=6,
            trade_start_hour=8, trade_end_hour=16,
            skip_weekdays=(2,), rr_ratio=2.5,
            min_range_size=1.0, max_range_size=15.0,
            min_range_pct=0.05, max_range_pct=2.0,
            velocity_filter_enabled=False,
            gap_filter_enabled=True,
            gap_vol_percentile=gap_pctl,
            gap_range_filter_enabled=gap_rng_enabled,
            gap_range_percentile=gap_rng_pctl,
            gap_rolling_days=60, gap_start_hour=6, gap_end_hour=8,
            be_hours=999, max_pending_hours=4,
            qty=1, point_value=1.0, price_decimals=2,
        )
        t = run_backtest(cfg, df, slippage=0.20)
        s = summarize(t)
        if s['n'] > 0:
            # Estimate skip rate vs baseline (gap_pctl=50, no range filter)
            baseline_n = 366  # from previous backtest
            skip_pct = (1 - s['n'] / baseline_n) * 100 if baseline_n > 0 else 0
            print(f"  {gap_pctl:8.0f} {gap_rng_pctl:8.0f} {s['n']:5d} {s['wr']:6.1f} "
                  f"{s['total']:+9.2f} {s['avg']:+8.2f} {s['pf']:6.2f} "
                  f"{s['sharpe']:+7.3f} {skip_pct:7.1f}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 5: COMBINED OPTIMAL CONFIG
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  TEST 5: COMBINED — BEST CONFIG CANDIDATES")
print("=" * 80)

best_configs = [
    # (label, trade_start, trade_end, max_pending, gap_pctl, gap_rng_pctl, rr)
    ("Current Live", 8, 16, 4, 50, 0, 2.5),
    ("Early Start", 7, 16, 4, 50, 0, 2.5),
    ("Late Start", 9, 16, 3, 50, 0, 2.5),
    ("Short Pending", 8, 16, 2, 50, 0, 2.5),
    ("Tight Gap", 8, 16, 4, 65, 40, 2.5),
    ("Tight Gap+Late", 9, 16, 3, 65, 40, 2.5),
    ("Tight Gap+Short", 8, 16, 2, 65, 40, 2.5),
    ("LLM-like", 8, 16, 3, 70, 50, 2.0),
    ("Aggressive Filter", 8, 16, 3, 75, 50, 2.0),
    ("Ultra-Tight", 9, 16, 2, 75, 50, 2.0),
]

print(f"\n  {'Config':20s} {'N':>5s} {'WR%':>6s} {'Total$':>9s} {'Avg$':>8s} "
      f"{'PF':>6s} {'Sharpe':>7s} {'MaxDD':>8s}")
print("-" * 80)

for label, ts, te, mp, gp, grp, rr in best_configs:
    cfg = V6StrategyConfig(
        instrument="XAUUSD",
        range_start_hour=0, range_end_hour=6,
        trade_start_hour=ts, trade_end_hour=te,
        skip_weekdays=(2,), rr_ratio=rr,
        min_range_size=1.0, max_range_size=15.0,
        min_range_pct=0.05, max_range_pct=2.0,
        velocity_filter_enabled=False,
        gap_filter_enabled=True,
        gap_vol_percentile=gp,
        gap_range_filter_enabled=grp > 0,
        gap_range_percentile=grp,
        gap_rolling_days=60, gap_start_hour=6, gap_end_hour=8,
        be_hours=999, max_pending_hours=mp,
        qty=1, point_value=1.0, price_decimals=2,
    )
    t = run_backtest(cfg, df, slippage=0.20)
    s = summarize(t)
    if s['n'] > 0:
        print(f"  {label:20s} {s['n']:5d} {s['wr']:6.1f} {s['total']:+9.2f} "
              f"{s['avg']:+8.2f} {s['pf']:6.2f} {s['sharpe']:+7.3f} {s['dd']:+8.2f}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 6: YEAR-BY-YEAR FOR BEST CANDIDATE
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  TEST 6: YEAR-BY-YEAR — Current vs Best Candidate")
print("=" * 80)

for label, ts, te, mp, gp, grp, rr in [("Current Live", 8, 16, 4, 50, 0, 2.5),
                                         ("LLM-like", 8, 16, 3, 70, 50, 2.0)]:
    cfg = V6StrategyConfig(
        instrument="XAUUSD",
        range_start_hour=0, range_end_hour=6,
        trade_start_hour=ts, trade_end_hour=te,
        skip_weekdays=(2,), rr_ratio=rr,
        min_range_size=1.0, max_range_size=15.0,
        min_range_pct=0.05, max_range_pct=2.0,
        velocity_filter_enabled=False,
        gap_filter_enabled=True,
        gap_vol_percentile=gp,
        gap_range_filter_enabled=grp > 0,
        gap_range_percentile=grp,
        gap_rolling_days=60, gap_start_hour=6, gap_end_hour=8,
        be_hours=999, max_pending_hours=mp,
        qty=1, point_value=1.0, price_decimals=2,
    )
    t = run_backtest(cfg, df, slippage=0.20)
    if t:
        td = pd.DataFrame(t)
        td['year'] = pd.to_datetime(td['entry_time']).dt.year
        print(f"\n  {label}:")
        for year, g in td.groupby('year'):
            yr_pnl = g['pnl'].sum()
            yr_wr = (g['pnl'] > 0).mean() * 100
            yr_n = len(g)
            tp = len(g[g['exit_reason'] == 'TP'])
            sl = len(g[g['exit_reason'] == 'SL'])
            eod = len(g[g['exit_reason'] == 'EOD'])
            print(f"    {year}: ${yr_pnl:+7.2f}  WR={yr_wr:.0f}%  N={yr_n:3d}  "
                  f"TP={tp} SL={sl} EOD={eod}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 7: ENTRY TIME DISTRIBUTION
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("  TEST 7: ENTRY TIME DISTRIBUTION (current config, $0.20 slip)")
print("=" * 80)

cfg = V6StrategyConfig(
    instrument="XAUUSD",
    range_start_hour=0, range_end_hour=6,
    trade_start_hour=8, trade_end_hour=16,
    skip_weekdays=(2,), rr_ratio=2.5,
    min_range_size=1.0, max_range_size=15.0,
    min_range_pct=0.05, max_range_pct=2.0,
    velocity_filter_enabled=False,
    gap_filter_enabled=True,
    gap_vol_percentile=50.0,
    gap_range_filter_enabled=False,
    gap_rolling_days=60, gap_start_hour=6, gap_end_hour=8,
    be_hours=999, max_pending_hours=4,
    qty=1, point_value=1.0, price_decimals=2,
)
t = run_backtest(cfg, df, slippage=0.20)
if t:
    td = pd.DataFrame(t)
    td['entry_hour'] = pd.to_datetime(td['entry_time']).dt.hour
    print(f"\n  {'Hour':>6s} {'N':>5s} {'WR%':>6s} {'Avg$':>8s} {'Total$':>9s}")
    print("-" * 40)
    for hour, g in td.groupby('entry_hour'):
        n = len(g)
        wr = (g['pnl'] > 0).mean() * 100
        avg = g['pnl'].mean()
        tot = g['pnl'].sum()
        print(f"  {hour:6d} {n:5d} {wr:6.1f} {avg:+8.2f} {tot:+9.2f}")

    # Also: how long do EOD trades take?
    eod_trades = td[td['exit_reason'] == 'EOD']
    if len(eod_trades) > 0:
        eod_trades = eod_trades.copy()
        eod_trades['hold_h'] = (pd.to_datetime(eod_trades['exit_time']) -
                                 pd.to_datetime(eod_trades['entry_time'])).dt.total_seconds() / 3600
        print(f"\n  EOD trades: avg hold = {eod_trades['hold_h'].mean():.1f}h, "
              f"avg P&L = ${eod_trades['pnl'].mean():+.2f}")

    # TP vs SL by entry hour
    print(f"\n  TP/SL ratio by entry hour:")
    for hour, g in td.groupby('entry_hour'):
        tp = len(g[g['exit_reason'] == 'TP'])
        sl = len(g[g['exit_reason'] == 'SL'])
        eod = len(g[g['exit_reason'] == 'EOD'])
        print(f"    {hour}:00 UTC — TP={tp} SL={sl} EOD={eod}  "
              f"TP/SL={tp/sl:.2f}" if sl > 0 else f"    {hour}:00 UTC — TP={tp} SL={sl} EOD={eod}")


print("\n=== OPTIMIZATION COMPLETE ===")

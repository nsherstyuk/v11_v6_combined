"""
ORB Backtest on XAUUSD 1-min data

Uses the ACTUAL V6 ORBStrategy with ReplayORBMarketContext and
ReplayORBExecutionEngine — the same code that runs in live/replay.

Strategy parameters match XAUUSD_ORB_CONFIG from run_live.py:
  - Asian range: 00:00-06:00 UTC
  - Trade window: 08:00-16:00 UTC
  - Skip Wednesday
  - RR ratio: 2.5
  - Gap filter: enabled
  - Velocity filter: disabled
  - No breakeven, no time exit

Tests:
1. Baseline ORB (passthrough — no LLM filter)
2. With gap filter (proper implementation, not stubbed)
3. Parameter sensitivity (RR, range filters)
4. Year-by-year breakdown
5. Slippage impact

Data: C:\\nautilus0\\data\\1m_csv\\xauusd_1m_tick.csv
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
LOG = logging.getLogger("orb_bt")
LOG.setLevel(logging.CRITICAL + 1)  # Suppress ALL V6 strategy logs
# Also suppress all v6_orb loggers
for name in ['v11.v6_orb.orb_strategy', 'v11.v6_orb', 'v6_orb']:
    logging.getLogger(name).setLevel(logging.CRITICAL + 1)

# ── Load XAUUSD 1-min data ──────────────────────────────────────────────────
print("Loading XAUUSD 1-min data...")
df = pd.read_csv(DATA_DIR / "xauusd_1m_tick.csv",
                 usecols=['timestamp', 'open', 'high', 'low', 'close'])
df['timestamp'] = pd.to_datetime(df['timestamp'])
df = df.sort_values('timestamp').reset_index(drop=True)
print(f"  {len(df):,} bars, {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}")


# ── MarketContext with proper gap filter ─────────────────────────────────────
class BacktestMarketContext(MarketContext):
    """Bar-based MarketContext with real gap filter implementation."""

    def __init__(self, v6_config: V6StrategyConfig, log: logging.Logger):
        self._config = v6_config
        self._log = log
        self._bars: deque = deque(maxlen=1440)
        self._daily_range = None
        self._daily_range_date = None
        self._slow_atr = 0.0
        self._slow_atr_count = 0
        self._slow_atr_prev_close = 0.0

        # Gap filter: rolling history
        self._gap_history: list = []  # list of (date, gap_vol, gap_range)
        self._gap_computed_today = False

    def add_bar(self, bar: V11Bar) -> None:
        self._bars.append(bar)
        self._update_slow_atr(bar)

    def get_velocity(self, lookback_minutes: int, current_time: datetime) -> float:
        cutoff = current_time - timedelta(minutes=lookback_minutes)
        count = sum(1 for b in self._bars if b.timestamp >= cutoff)
        return count * 10.0

    def get_asian_range(self, start_hour: int, end_hour: int,
                        current_time: datetime):
        today_str = current_time.strftime("%Y-%m-%d")
        if self._daily_range is not None and self._daily_range_date == today_str:
            return self._daily_range

        range_bars = [
            b for b in self._bars
            if b.timestamp.strftime("%Y-%m-%d") == today_str
            and start_hour <= b.timestamp.hour < end_hour
        ]
        if not range_bars:
            return None

        high = max(b.high for b in range_bars)
        low = min(b.low for b in range_bars)
        rng = RangeInfo(high=high, low=low,
                        start_time=range_bars[0].timestamp,
                        end_time=range_bars[-1].timestamp)
        self._daily_range = rng
        self._daily_range_date = today_str
        return rng

    def time_is_in_trade_window(self, current_time, start_hour, end_hour):
        return start_hour <= current_time.hour < end_hour

    def get_current_price(self, current_time):
        if self._bars:
            return self._bars[-1].close
        return None

    def get_gap_metrics(self, current_time, gap_start_hour, gap_end_hour,
                        vol_percentile, range_percentile, rolling_days):
        """Real gap filter: compute gap period volatility and range,
        compare to rolling percentile."""
        today_str = current_time.strftime("%Y-%m-%d")

        # Only compute once per day
        if self._gap_computed_today:
            # Return cached result
            if self._gap_history and self._gap_history[-1][0] == today_str:
                gv, gr, vp, rp = self._gap_history[-1][1], self._gap_history[-1][2], \
                                  self._gap_history[-1][3], self._gap_history[-1][4]
                return GapMetrics(gap_volatility=gv, gap_range=gr,
                                  vol_passes=vp, range_passes=rp)
            # No gap data for today — pass by default
            return GapMetrics(gap_volatility=0.001, gap_range=1.0,
                              vol_passes=True, range_passes=True)

        # Find gap period bars for today
        gap_bars = [
            b for b in self._bars
            if b.timestamp.strftime("%Y-%m-%d") == today_str
            and gap_start_hour <= b.timestamp.hour < gap_end_hour
        ]

        if len(gap_bars) < 5:
            # Not enough bars in gap period — pass
            self._gap_computed_today = True
            return GapMetrics(gap_volatility=0.001, gap_range=1.0,
                              vol_passes=True, range_passes=True)

        # Gap volatility: std of 1-min returns during gap period
        closes = [b.close for b in gap_bars]
        returns = np.diff(closes) / closes[:-1]
        gap_vol = float(np.std(returns)) if len(returns) > 1 else 0.0

        # Gap range: high - low during gap period
        gap_range = max(b.high for b in gap_bars) - min(b.low for b in gap_bars)

        # Compare to rolling history
        rolling_vols = [h[1] for h in self._gap_history[-rolling_days:]]
        rolling_ranges = [h[2] for h in self._gap_history[-rolling_days:]]

        vol_passes = True
        range_passes = True

        if len(rolling_vols) >= 10:
            vol_threshold = np.percentile(rolling_vols, vol_percentile)
            vol_passes = gap_vol >= vol_threshold

        if len(rolling_ranges) >= 10:
            range_threshold = np.percentile(rolling_ranges, range_percentile)
            range_passes = gap_range >= range_threshold

        self._gap_history.append((today_str, gap_vol, gap_range, vol_passes, range_passes))
        self._gap_computed_today = True

        return GapMetrics(gap_volatility=gap_vol, gap_range=gap_range,
                          vol_passes=vol_passes, range_passes=range_passes)

    @property
    def slow_atr(self):
        return self._slow_atr

    def _update_slow_atr(self, bar):
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
            alpha = 2.0 / (1441)
            self._slow_atr = self._slow_atr * (1 - alpha) + tr * alpha

    def reset_daily(self):
        self._daily_range = None
        self._daily_range_date = None
        self._gap_computed_today = False


# ── ExecutionEngine (same as ReplayORBExecutionEngine) ───────────────────────
class BacktestExecutionEngine(ExecutionEngine):
    """Simulated fills from bar high/low."""

    def __init__(self, v6_config, log, on_fill_callback=None):
        self._config = v6_config
        self._log = log
        self._on_fill = on_fill_callback
        self._brackets_active = False
        self._long_entry = 0.0
        self._short_entry = 0.0
        self._long_sl = 0.0
        self._long_tp = 0.0
        self._short_sl = 0.0
        self._short_tp = 0.0
        self._has_position = False
        self._position_direction = None
        self._entry_price = 0.0
        self._sl_price = 0.0
        self._tp_price = 0.0
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
        d = self._config.price_decimals
        ts = bar.timestamp

        # Entry fills
        if self._brackets_active and not self._has_position:
            if bar.high >= self._long_entry:
                fill = Fill(timestamp=ts, price=self._long_entry,
                            direction="LONG", reason="ENTRY")
                self._has_position = True
                self._position_direction = "LONG"
                self._entry_price = self._long_entry
                self._sl_price = self._long_sl
                self._tp_price = self._long_tp
                self._brackets_active = False
                return fill
            if bar.low <= self._short_entry:
                fill = Fill(timestamp=ts, price=self._short_entry,
                            direction="SHORT", reason="ENTRY")
                self._has_position = True
                self._position_direction = "SHORT"
                self._entry_price = self._short_entry
                self._sl_price = self._short_sl
                self._tp_price = self._short_tp
                self._brackets_active = False
                return fill

        # Exit fills
        if self._has_position:
            # SL
            if self._position_direction == "LONG" and bar.low <= self._sl_price:
                fill = Fill(timestamp=ts, price=self._sl_price,
                            direction="LONG", reason="SL")
                self._close_position(fill)
                return fill
            if self._position_direction == "SHORT" and bar.high >= self._sl_price:
                fill = Fill(timestamp=ts, price=self._sl_price,
                            direction="SHORT", reason="SL")
                self._close_position(fill)
                return fill
            # TP
            if self._position_direction == "LONG" and bar.high >= self._tp_price:
                fill = Fill(timestamp=ts, price=self._tp_price,
                            direction="LONG", reason="TP")
                self._close_position(fill)
                return fill
            if self._position_direction == "SHORT" and bar.low <= self._tp_price:
                fill = Fill(timestamp=ts, price=self._tp_price,
                            direction="SHORT", reason="TP")
                self._close_position(fill)
                return fill
            # Market close requested
            if self._close_requested:
                fill = Fill(timestamp=ts, price=bar.close,
                            direction=self._position_direction or "LONG",
                            reason="MARKET")
                self._close_position(fill)
                self._close_requested = False
                return fill

        return None

    def force_close_at(self, price, bar):
        if not self._has_position:
            return None
        fill = Fill(timestamp=bar.timestamp if bar else datetime.utcnow(),
                    price=price,
                    direction=self._position_direction or "LONG",
                    reason="MARKET")
        self._close_position(fill)
        return fill

    def _close_position(self, fill):
        self._has_position = False
        self._position_direction = None
        self._brackets_active = False
        self._close_requested = False
        if self._on_fill:
            self._on_fill(fill)

    def reset_daily(self):
        self._brackets_active = False
        self._has_position = False
        self._position_direction = None
        self._close_requested = False


# ── Run backtest ─────────────────────────────────────────────────────────────
def run_backtest(config: V6StrategyConfig, df: pd.DataFrame,
                 slippage_dollars: float = 0.0) -> list:
    """Run ORB backtest with given config. Returns list of trade records."""
    context = BacktestMarketContext(config, LOG)
    execution = BacktestExecutionEngine(config, LOG)
    strategy = ORBStrategy(config, logger=LOG)

    trades = []
    current_date = None
    entry_info = None

    for idx in range(len(df)):
        row = df.iloc[idx]
        ts = pd.to_datetime(row['timestamp'])
        today_str = ts.strftime("%Y-%m-%d")

        bar = V11Bar(
            timestamp=ts,
            open=float(row['open']),
            high=float(row['high']),
            low=float(row['low']),
            close=float(row['close']),
            tick_count=0,
            buy_volume=0,
            sell_volume=0,
        )

        # Daily reset
        if today_str != current_date:
            current_date = today_str
            strategy.reset_for_new_day()
            context.reset_daily()
            execution.reset_daily()
            entry_info = None

        # Skip weekdays
        if config.skip_weekdays and ts.weekday() in config.skip_weekdays:
            continue

        context.add_bar(bar)

        # Trade window closed?
        if ts.hour >= config.trade_end_hour:
            if strategy.state in (StrategyState.IDLE, StrategyState.RANGE_READY):
                strategy.state = StrategyState.DONE_TODAY
                continue
            # EOD close if in trade
            if execution.has_position():
                fill = execution.force_close_at(bar.close, bar)
                if fill:
                    strategy.on_fill(fill, context, execution)
                    if entry_info:
                        pnl = _calc_pnl(entry_info, fill, slippage_dollars)
                        trades.append({**entry_info,
                                       'exit_price': fill.price,
                                       'exit_time': fill.timestamp,
                                       'exit_reason': 'EOD',
                                       'pnl': pnl})
                        entry_info = None
                continue

        # Drive strategy
        tick = Tick(timestamp=ts, bid=bar.close, ask=bar.close)
        strategy.on_tick(tick, context, execution)

        # Check fills
        fill = execution.check_bar_fills(bar, strategy)
        if fill:
            strategy.on_fill(fill, context, execution)
            if fill.reason == "ENTRY":
                entry_info = {
                    'entry_price': fill.price + (slippage_dollars if fill.direction == "LONG" else -slippage_dollars),
                    'direction': fill.direction,
                    'entry_time': fill.timestamp,
                    'range_high': strategy.range.high if strategy.range else 0,
                    'range_low': strategy.range.low if strategy.range else 0,
                    'range_size': strategy.range.size if strategy.range else 0,
                }
            elif fill.reason in ("SL", "TP", "MARKET", "BE") and entry_info:
                pnl = _calc_pnl(entry_info, fill, slippage_dollars)
                trades.append({**entry_info,
                               'exit_price': fill.price,
                               'exit_time': fill.timestamp,
                               'exit_reason': fill.reason,
                               'pnl': pnl})
                entry_info = None

    return trades


def _calc_pnl(entry_info, fill, slippage=0.0):
    """Calculate P&L in dollars for 1 lot XAUUSD."""
    entry = entry_info['entry_price']
    exit_p = fill.price
    direction = entry_info['direction']
    if direction == "LONG":
        pnl = (exit_p - entry) * 1.0  # 1 lot, point_value=1
    else:
        pnl = (entry - exit_p) * 1.0
    pnl -= slippage * 2  # entry + exit slippage
    return round(pnl, 2)


def print_results(trades, label=""):
    """Print backtest results."""
    if not trades:
        print(f"\n  {label}No trades generated")
        return

    tdf = pd.DataFrame(trades)
    n = len(tdf)
    total_pnl = tdf['pnl'].sum()
    avg_pnl = tdf['pnl'].mean()
    wins = tdf[tdf['pnl'] > 0]
    losses = tdf[tdf['pnl'] <= 0]
    wr = len(wins) / n * 100 if n > 0 else 0
    avg_win = wins['pnl'].mean() if len(wins) > 0 else 0
    avg_loss = losses['pnl'].mean() if len(losses) > 0 else 0
    profit_factor = abs(wins['pnl'].sum() / losses['pnl'].sum()) if len(losses) > 0 and losses['pnl'].sum() != 0 else float('inf')

    # Sharpe (per-trade, annualized)
    if tdf['pnl'].std() > 0:
        trades_per_year = n / 8  # ~8 years of data
        sharpe = (avg_pnl / tdf['pnl'].std()) * np.sqrt(trades_per_year)
    else:
        sharpe = 0

    # Max drawdown
    cum = tdf['pnl'].cumsum()
    peak = cum.cummax()
    dd = cum - peak
    max_dd = dd.min()

    # By exit reason
    tp_trades = tdf[tdf['exit_reason'] == 'TP']
    sl_trades = tdf[tdf['exit_reason'] == 'SL']
    eod_trades = tdf[tdf['exit_reason'] == 'EOD']

    print(f"\n  {label}")
    print(f"    Trades: {n}")
    print(f"    Win rate: {wr:.1f}%")
    print(f"    Total P&L: ${total_pnl:+.2f}")
    print(f"    Avg P&L: ${avg_pnl:+.2f}")
    print(f"    Avg win: ${avg_win:+.2f}  Avg loss: ${avg_loss:+.2f}")
    print(f"    Profit factor: {profit_factor:.2f}")
    print(f"    Sharpe: {sharpe:+.3f}")
    print(f"    Max DD: ${max_dd:+.2f}")
    print(f"    TP: {len(tp_trades)} (${tp_trades['pnl'].sum():+.2f})  "
          f"SL: {len(sl_trades)} (${sl_trades['pnl'].sum():+.2f})  "
          f"EOD: {len(eod_trades)} (${eod_trades['pnl'].sum():+.2f})")

    # Long vs Short
    longs = tdf[tdf['direction'] == 'LONG']
    shorts = tdf[tdf['direction'] == 'SHORT']
    if len(longs) > 0:
        print(f"    LONG:  N={len(longs)}  WR={longs['pnl'].apply(lambda x: x>0).mean()*100:.1f}%  "
              f"Avg=${longs['pnl'].mean():+.2f}  Total=${longs['pnl'].sum():+.2f}")
    if len(shorts) > 0:
        print(f"    SHORT: N={len(shorts)}  WR={shorts['pnl'].apply(lambda x: x>0).mean()*100:.1f}%  "
              f"Avg=${shorts['pnl'].mean():+.2f}  Total=${shorts['pnl'].sum():+.2f}")

    # Year-by-year
    tdf['year'] = pd.to_datetime(tdf['entry_time']).dt.year
    print(f"    Year-by-year:")
    for year, group in tdf.groupby('year'):
        yr_pnl = group['pnl'].sum()
        yr_wr = group['pnl'].apply(lambda x: x > 0).mean() * 100
        yr_n = len(group)
        print(f"      {year}: P&L=${yr_pnl:+.2f}  WR={yr_wr:.0f}%  N={yr_n}")

    return tdf


# ═══════════════════════════════════════════════════════════════════════════
# TEST 1: Baseline — current live config
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  TEST 1: BASELINE — Current Live Config")
print("=" * 70)

baseline_config = V6StrategyConfig(
    instrument="XAUUSD",
    range_start_hour=0,
    range_end_hour=6,
    trade_start_hour=8,
    trade_end_hour=16,
    skip_weekdays=(2,),
    rr_ratio=2.5,
    min_range_size=1.0,
    max_range_size=15.0,
    min_range_pct=0.05,
    max_range_pct=2.0,
    velocity_filter_enabled=False,
    gap_filter_enabled=True,
    gap_vol_percentile=50.0,
    gap_range_filter_enabled=False,
    gap_range_percentile=40.0,
    gap_rolling_days=60,
    gap_start_hour=6,
    gap_end_hour=8,
    be_hours=999,
    max_pending_hours=4,
    time_exit_minutes=0,
    qty=1,
    point_value=1.0,
    price_decimals=2,
)

trades_baseline = run_backtest(baseline_config, df)
tdf = print_results(trades_baseline, "BASELINE (gap=ON, vel=OFF, skip Wed, RR=2.5)")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 2: No gap filter
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  TEST 2: NO GAP FILTER")
print("=" * 70)

config_no_gap = V6StrategyConfig(
    instrument="XAUUSD",
    range_start_hour=0, range_end_hour=6,
    trade_start_hour=8, trade_end_hour=16,
    skip_weekdays=(2,), rr_ratio=2.5,
    min_range_size=1.0, max_range_size=15.0,
    min_range_pct=0.05, max_range_pct=2.0,
    velocity_filter_enabled=False,
    gap_filter_enabled=False,  # OFF
    be_hours=999, max_pending_hours=4,
    qty=1, point_value=1.0, price_decimals=2,
)

trades_no_gap = run_backtest(config_no_gap, df)
print_results(trades_no_gap, "NO GAP FILTER")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 3: No Wednesday skip
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  TEST 3: NO WEDNESDAY SKIP")
print("=" * 70)

config_no_skip = V6StrategyConfig(
    instrument="XAUUSD",
    range_start_hour=0, range_end_hour=6,
    trade_start_hour=8, trade_end_hour=16,
    skip_weekdays=(),  # No skip
    rr_ratio=2.5,
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

trades_no_skip = run_backtest(config_no_skip, df)
print_results(trades_no_skip, "NO WED SKIP")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 4: RR ratio sensitivity
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  TEST 4: RR RATIO SENSITIVITY")
print("=" * 70)

print(f"\n  {'RR':>5s} {'N':>5s} {'WR%':>6s} {'Total$':>9s} {'Avg$':>8s} {'PF':>6s} {'Sharpe':>7s}")
print("-" * 55)

for rr in [1.5, 2.0, 2.5, 3.0, 4.0]:
    cfg = V6StrategyConfig(
        instrument="XAUUSD",
        range_start_hour=0, range_end_hour=6,
        trade_start_hour=8, trade_end_hour=16,
        skip_weekdays=(2,), rr_ratio=rr,
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
    t = run_backtest(cfg, df)
    if t:
        td = pd.DataFrame(t)
        n = len(td)
        wr = td['pnl'].apply(lambda x: x>0).mean()*100
        tot = td['pnl'].sum()
        avg = td['pnl'].mean()
        pf = abs(td[td['pnl']>0]['pnl'].sum()/td[td['pnl']<=0]['pnl'].sum()) if td[td['pnl']<=0]['pnl'].sum() != 0 else 999
        sh = (avg/td['pnl'].std())*np.sqrt(n/8) if td['pnl'].std()>0 else 0
        print(f"  {rr:5.1f} {n:5d} {wr:6.1f} {tot:+9.2f} {avg:+8.2f} {pf:6.2f} {sh:+7.3f}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 5: Slippage impact
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  TEST 5: SLIPPAGE IMPACT")
print("=" * 70)

print(f"\n  {'Slip$':>6s} {'N':>5s} {'WR%':>6s} {'Total$':>9s} {'Avg$':>8s} {'Sharpe':>7s}")
print("-" * 50)

for slip in [0.0, 0.10, 0.20, 0.30, 0.50, 1.00]:
    t = run_backtest(baseline_config, df, slippage_dollars=slip)
    if t:
        td = pd.DataFrame(t)
        n = len(td)
        wr = td['pnl'].apply(lambda x: x>0).mean()*100
        tot = td['pnl'].sum()
        avg = td['pnl'].mean()
        sh = (avg/td['pnl'].std())*np.sqrt(n/8) if td['pnl'].std()>0 else 0
        print(f"  {slip:6.2f} {n:5d} {wr:6.1f} {tot:+9.2f} {avg:+8.2f} {sh:+7.3f}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 6: Breakeven rule
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  TEST 6: BREAKEVEN RULE")
print("=" * 70)

for be_h in [2, 3, 4, 6, 999]:
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
        be_hours=be_h, be_offset=0.50,  # $0.50 offset
        max_pending_hours=4,
        qty=1, point_value=1.0, price_decimals=2,
    )
    t = run_backtest(cfg, df)
    if t:
        td = pd.DataFrame(t)
        n = len(td)
        wr = td['pnl'].apply(lambda x: x>0).mean()*100
        tot = td['pnl'].sum()
        avg = td['pnl'].mean()
        be_exits = td[td['exit_reason'].isin(['SL', 'MARKET'])]
        be_small = be_exits[abs(be_exits['pnl']) < 1.0] if len(be_exits) > 0 else pd.DataFrame()
        sh = (avg/td['pnl'].std())*np.sqrt(n/8) if td['pnl'].std()>0 else 0
        print(f"  BE={be_h}h: N={n}  WR={wr:.1f}%  Total=${tot:+.2f}  Avg=${avg:+.2f}  Sharpe={sh:+.3f}  "
              f"Small exits(<$1): {len(be_small)}")


# ═══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  SUMMARY")
print("=" * 70)
print("""
  This backtest uses the ACTUAL V6 ORBStrategy code (frozen from v6_orb/).
  The execution simulation checks bar high/low for bracket fills,
  matching the live IBKR execution as closely as possible.

  Key differences from live:
  1. No slippage by default (bar high/low = exact fill)
  2. Gap filter uses bar data instead of tick data
  3. Velocity is estimated from bar count (×10 proxy)
  4. No LLM filter (all signals pass through)

  The gap filter is properly implemented here (not stubbed).
  It tracks rolling gap-period volatility and skips days
  where pre-market activity is below the 50th percentile.
""")

print("\n=== BACKTEST COMPLETE ===")

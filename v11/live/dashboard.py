"""
V11 Trading Dashboard — Streamlit-based real-time monitoring.

Reads trade logs, tick data, and system state from local files.
No IBKR connection required — works offline for post-mortem analysis.

Usage:
    streamlit run v11/live/dashboard.py
    streamlit run v11/live/dashboard.py -- --port 8501
"""
from __future__ import annotations

import sys
from pathlib import Path

# Project root
ROOT = Path(__file__).resolve().parents[2]

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="V11 Trading Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Data loading ─────────────────────────────────────────────────────────────

TRADES_DIR = ROOT / "v11" / "live" / "trades"
TICKS_DIR = ROOT / "data" / "ticks"
STATE_DIR = ROOT / "v11" / "live" / "state"
LOGS_DIR = ROOT / "v11" / "live" / "logs"


@st.cache_data(ttl=30)
def load_trades() -> pd.DataFrame:
    """Load all trade CSVs from trades directory."""
    frames = []
    if not TRADES_DIR.exists():
        return pd.DataFrame()
    for csv_path in TRADES_DIR.glob("trades_*.csv"):
        try:
            df = pd.read_csv(csv_path, encoding="utf-8", on_bad_lines="skip")
            frames.append(df)
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.sort_values("timestamp")
    return df


@st.cache_data(ttl=60)
def load_tick_data(pair: str, date_str: str) -> pd.DataFrame:
    """Load tick data for a pair on a specific date."""
    parts = pair.upper().replace("/", "")
    # Map pair name to directory structure
    pair_map = {"EURUSD": "EURUSD", "XAUUSD": "XAUUSD"}
    dir_name = pair_map.get(parts, parts)
    path = TICKS_DIR / dir_name / f"{date_str}.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, encoding="utf-8", on_bad_lines="skip")
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()


def get_available_tick_dates(pair: str) -> list[str]:
    """Get available tick data dates for a pair."""
    parts = pair.upper().replace("/", "")
    pair_map = {"EURUSD": "EURUSD", "XAUUSD": "XAUUSD"}
    dir_name = pair_map.get(parts, parts)
    tick_dir = TICKS_DIR / dir_name
    if not tick_dir.exists():
        return []
    dates = sorted(
        p.stem for p in tick_dir.glob("*.csv")
    )
    return dates


@st.cache_data(ttl=30)
def load_emergency_state() -> dict | None:
    """Load emergency shutdown state if it exists."""
    path = STATE_DIR / "emergency_shutdown.json"
    if not path.exists():
        return None
    import json
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ Settings")
    auto_refresh = st.checkbox("Auto-refresh", value=True)
    refresh_sec = st.slider("Refresh interval (s)", 5, 120, 15, disabled=not auto_refresh)

    st.divider()
    st.header("📈 Price Charts")
    tick_pair = st.selectbox("Instrument", ["EURUSD", "XAUUSD"], index=0)

    available_dates = get_available_tick_dates(tick_pair)
    if available_dates:
        tick_date = st.selectbox("Date", available_dates, index=len(available_dates) - 1)
    else:
        tick_date = None

    st.divider()
    st.caption(f"Data dir: `{ROOT}`")

if auto_refresh:
    st_autorefresh = st.empty()

# ── Main content ─────────────────────────────────────────────────────────────

# Title row
col_title, col_status = st.columns([3, 1])
with col_title:
    st.title("📊 V11 Trading Dashboard")
with col_status:
    emergency = load_emergency_state()
    if emergency:
        ts = emergency.get("timestamp", "?")
        reason = emergency.get("reason", "?")
        st.error(f"🚨 EMERGENCY SHUTDOWN\n{reason}\n{ts}")
    else:
        st.success("✅ No emergency shutdown")

# ── KPI Row ──────────────────────────────────────────────────────────────────

trades_df = load_trades()

col1, col2, col3, col4, col5 = st.columns(5)

total_trades = len(trades_df)
total_pnl = trades_df["pnl"].sum() if "pnl" in trades_df.columns else 0.0
win_rate = (trades_df["pnl"] > 0).mean() * 100 if total_trades > 0 and "pnl" in trades_df.columns else 0.0
avg_pnl = trades_df["pnl"].mean() if total_trades > 0 and "pnl" in trades_df.columns else 0.0
worst_trade = trades_df["pnl"].min() if total_trades > 0 and "pnl" in trades_df.columns else 0.0

with col1:
    st.metric("Total Trades", total_trades)
with col2:
    st.metric("Total P&L", f"${total_pnl:+.2f}")
with col3:
    st.metric("Win Rate", f"{win_rate:.0f}%")
with col4:
    st.metric("Avg P&L", f"${avg_pnl:+.2f}")
with col5:
    st.metric("Worst Trade", f"${worst_trade:+.2f}")

# ── P&L Chart ────────────────────────────────────────────────────────────────

st.subheader("Cumulative P&L")

if not trades_df.empty and "pnl" in trades_df.columns and "timestamp" in trades_df.columns:
    trades_sorted = trades_df.sort_values("timestamp")
    cum_pnl = trades_sorted["pnl"].cumsum()

    fig_pnl = go.Figure()
    fig_pnl.add_trace(go.Scatter(
        x=trades_sorted["timestamp"],
        y=cum_pnl,
        mode="lines+markers",
        name="Cumulative P&L",
        line=dict(color="#00d4aa", width=2),
        marker=dict(size=6),
        fill="tozeroy",
        fillcolor="rgba(0, 212, 170, 0.1)",
    ))
    fig_pnl.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
    fig_pnl.update_layout(
        height=300,
        margin=dict(l=50, r=20, t=30, b=30),
        xaxis_title="",
        yaxis_title="P&L ($)",
        showlegend=False,
        template="plotly_dark",
    )
    st.plotly_chart(fig_pnl, use_container_width=True)
else:
    st.info("No trade data available yet.")

# ── Price Chart ───────────────────────────────────────────────────────────────

st.subheader(f"Price Chart — {tick_pair}")

if tick_date:
    ticks_df = load_tick_data(tick_pair, tick_date)
    if not ticks_df.empty and "timestamp" in ticks_df.columns and "mid" in ticks_df.columns:
        # Downsample for performance (max ~2000 points)
        n = len(ticks_df)
        if n > 2000:
            step = n // 2000
            ticks_sample = ticks_df.iloc[::step].copy()
        else:
            ticks_sample = ticks_df

        fig_price = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                   row_heights=[0.75, 0.25],
                                   vertical_spacing=0.02)

        fig_price.add_trace(go.Scatter(
            x=ticks_sample["timestamp"],
            y=ticks_sample["mid"],
            mode="lines",
            name="Mid Price",
            line=dict(color="#4fc3f7", width=1.5),
        ), row=1, col=1)

        # Spread chart
        if "bid" in ticks_sample.columns and "ask" in ticks_sample.columns:
            spread = ticks_sample["ask"] - ticks_sample["bid"]
            fig_price.add_trace(go.Bar(
                x=ticks_sample["timestamp"],
                y=spread * 10000 if tick_pair == "EURUSD" else spread,
                name="Spread (pips)" if tick_pair == "EURUSD" else "Spread ($)",
                marker_color="#ff7043",
                opacity=0.6,
            ), row=2, col=1)

        fig_price.update_layout(
            height=450,
            margin=dict(l=50, r=20, t=30, b=30),
            showlegend=False,
            template="plotly_dark",
        )
        fig_price.update_yaxes(title_text="Price", row=1, col=1)
        spread_label = "Spread (pips)" if tick_pair == "EURUSD" else "Spread ($)"
        fig_price.update_yaxes(title_text=spread_label, row=2, col=1)
        st.plotly_chart(fig_price, use_container_width=True)

        # Tick stats
        tc1, tc2, tc3 = st.columns(3)
        with tc1:
            st.metric("Ticks", f"{n:,}")
        with tc2:
            if "mid" in ticks_df.columns:
                st.metric("Price Range",
                          f"{ticks_df['mid'].min():.5f} – {ticks_df['mid'].max():.5f}"
                          if tick_pair == "EURUSD"
                          else f"{ticks_df['mid'].min():.2f} – {ticks_df['mid'].max():.2f}")
        with tc3:
            if "timestamp" in ticks_df.columns:
                duration = ticks_df["timestamp"].iloc[-1] - ticks_df["timestamp"].iloc[0]
                hours = duration.total_seconds() / 3600
                st.metric("Duration", f"{hours:.1f}h")
    else:
        st.info(f"No tick data for {tick_pair} on {tick_date}")
else:
    st.info("No tick data files found.")

# ── Trade Log ─────────────────────────────────────────────────────────────────

st.subheader("Trade Log")

if not trades_df.empty:
    display_cols = [c for c in [
        "timestamp", "instrument", "direction", "entry_price", "exit_price",
        "fill_entry_price", "fill_exit_price", "stop_price", "target_price",
        "quantity", "pnl", "engine_pnl", "ibkr_pnl", "exit_reason",
        "buy_ratio", "llm_confidence", "hold_bars",
    ] if c in trades_df.columns]

    format_map = {}
    for c in ["entry_price", "exit_price", "stop_price", "target_price",
              "fill_entry_price", "fill_exit_price", "pnl", "engine_pnl", "ibkr_pnl"]:
        if c in display_cols:
            format_map[c] = "%.5f" if trades_df[c].dtype == float else "%.2f"

    st.dataframe(
        trades_df[display_cols].sort_values("timestamp", ascending=False),
        use_container_width=True,
        hide_index=True,
        height=300,
    )

    # Download button
    csv = trades_df.to_csv(index=False).encode("utf-8")
    st.download_button("📥 Download trades CSV", csv, "v11_trades.csv", "text/csv")
else:
    st.info("No trades recorded yet.")

# ── Per-Exit-Reason Breakdown ─────────────────────────────────────────────────

if not trades_df.empty and "exit_reason" in trades_df.columns:
    st.subheader("Exit Reason Breakdown")
    reason_stats = trades_df.groupby("exit_reason").agg(
        count=("pnl", "count"),
        total_pnl=("pnl", "sum"),
        avg_pnl=("pnl", "mean"),
    ).reset_index()

    col_r1, col_r2 = st.columns(2)
    with col_r1:
        fig_reason = go.Figure(data=[
            go.Pie(labels=reason_stats["exit_reason"],
                   values=reason_stats["count"],
                   hole=0.4,
                   marker_colors=["#f44336", "#4caf50", "#ff9800", "#2196f3", "#9c27b0", "#607d8b"])
        ])
        fig_reason.update_layout(
            height=250,
            margin=dict(l=20, r=20, t=30, b=20),
            template="plotly_dark",
            showlegend=True,
            legend=dict(orientation="h", y=-0.1),
        )
        st.plotly_chart(fig_reason, use_container_width=True)

    with col_r2:
        st.dataframe(reason_stats, use_container_width=True, hide_index=True, height=250)

# ── Strategy Status (from emergency state) ───────────────────────────────────

if emergency and "strategies" in emergency:
    st.subheader("Strategy Status (at shutdown)")
    strat_rows = []
    for s in emergency["strategies"]:
        strat_rows.append({
            "Strategy": s.get("strategy_name", "?"),
            "Instrument": s.get("instrument", s.get("pair_name", "?")),
            "In Trade": s.get("in_trade", False),
            "Daily Trades": s.get("daily_trades", 0),
            "Daily P&L": s.get("daily_pnl", 0.0),
            "State": s.get("state", s.get("detector_state", "?")),
            "Last Close": s.get("last_close", s.get("current_price", "?")),
        })
    st.dataframe(pd.DataFrame(strat_rows), use_container_width=True, hide_index=True)

# ── Auto-refresh ──────────────────────────────────────────────────────────────

if auto_refresh:
    # Use Streamlit's built-in auto-refresh via query param trick
    st.markdown(
        f'<meta http-equiv="refresh" content="{refresh_sec}">',
        unsafe_allow_html=True,
    )

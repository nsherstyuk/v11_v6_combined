"""Analyze what limits trade count — box formation funnel diagnostics."""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from datetime import datetime
from dataclasses import replace
from v11.backtest.data_loader import load_instrument_bars, split_by_sessions
from v11.core.darvas_detector import DarvasDetector
from v11.core.imbalance_classifier import ImbalanceClassifier
from v11.config.strategy_config import XAUUSD_CONFIG, EURUSD_CONFIG, USDJPY_CONFIG

configs = {
    "XAUUSD": replace(XAUUSD_CONFIG, top_confirm_bars=10, bottom_confirm_bars=20,
                       max_box_width_atr=3.0, breakout_confirm_bars=3),
    "EURUSD": replace(EURUSD_CONFIG, top_confirm_bars=20, bottom_confirm_bars=20,
                       max_box_width_atr=3.0, breakout_confirm_bars=2),
}

for inst, config in configs.items():
    bars = load_instrument_bars(inst, start=datetime(2024, 1, 1))
    sessions = split_by_sessions(bars, gap_minutes=30)

    total_bars = len(bars)
    total_sessions = len(sessions)
    boxes_formed = 0
    boxes_too_narrow = 0
    boxes_too_wide = 0
    boxes_too_short = 0
    breakouts_started = 0
    breakouts_confirmed = 0
    breakouts_failed = 0
    top_confirmations = 0
    bottom_confirmations = 0
    top_resets = 0  # price broke above top during bottom formation

    for session_bars in sessions:
        det = DarvasDetector(config)
        prev_state = det.state
        had_box = False

        for bar in session_bars:
            old_state = det.state
            old_box = det.active_box
            signal = det.add_bar(bar)
            new_state = det.state

            # Track state transitions
            if old_state == "CONFIRMING_TOP" and new_state == "CONFIRMING_BOTTOM":
                top_confirmations += 1
            if old_state == "CONFIRMING_BOTTOM" and new_state == "BOX_ACTIVE":
                bottom_confirmations += 1
                boxes_formed += 1
            if old_state == "CONFIRMING_BOTTOM" and new_state == "SEEKING_TOP":
                # Box validation failed — figure out why
                # Re-check: was it width or duration?
                # We can't easily tell from outside, count as generic reject
                boxes_too_narrow += 1  # proxy — could be any validation fail
            if old_state == "CONFIRMING_BOTTOM" and new_state == "CONFIRMING_TOP":
                top_resets += 1
            if old_state == "BOX_ACTIVE" and new_state == "CONFIRMING_BREAKOUT":
                breakouts_started += 1
            if old_state == "CONFIRMING_BREAKOUT" and new_state == "BOX_ACTIVE":
                breakouts_failed += 1
            if signal is not None:
                breakouts_confirmed += 1

    print(f"{'='*60}")
    print(f"  {inst} — SIGNAL FUNNEL (2024-2026, best params)")
    print(f"{'='*60}")
    print(f"  Total bars:              {total_bars:,}")
    print(f"  Sessions:                {total_sessions}")
    print(f"  Bars/session avg:        {total_bars/total_sessions:,.0f}")
    print(f"{'─'*60}")
    print(f"  Tops confirmed:          {top_confirmations}")
    print(f"  Bottom resets (price>top):{top_resets}")
    print(f"  Box validation rejects:  {boxes_too_narrow}")
    print(f"  Boxes formed:            {boxes_formed}")
    print(f"{'─'*60}")
    print(f"  Breakout attempts:       {breakouts_started}")
    print(f"  Breakouts failed:        {breakouts_failed}")
    print(f"  Breakouts confirmed:     {breakouts_confirmed}")
    print(f"{'─'*60}")
    print(f"  Conversion: top->box     {boxes_formed}/{top_confirmations} = {boxes_formed/max(top_confirmations,1)*100:.1f}%")
    print(f"  Conversion: box->signal  {breakouts_confirmed}/{boxes_formed} = {breakouts_confirmed/max(boxes_formed,1)*100:.1f}%")
    print(f"  Conversion: attempt->conf {breakouts_confirmed}/{breakouts_started} = {breakouts_confirmed/max(breakouts_started,1)*100:.1f}%")
    print(f"  Overall: bars/signal     {total_bars/max(breakouts_confirmed,1):,.0f}")
    print()

    # Now test impact of loosening each param
    print(f"  --- PARAM SENSITIVITY (change one param at a time) ---")
    variations = [
        ("top_confirm_bars=5", replace(config, top_confirm_bars=5)),
        ("top_confirm_bars=8", replace(config, top_confirm_bars=8)),
        ("bottom_confirm_bars=5", replace(config, bottom_confirm_bars=5)),
        ("bottom_confirm_bars=8", replace(config, bottom_confirm_bars=8)),
        ("min_box_duration=10", replace(config, min_box_duration=10)),
        ("min_box_duration=5", replace(config, min_box_duration=5)),
        ("max_box_width_atr=5.0", replace(config, max_box_width_atr=5.0)),
        ("max_box_width_atr=7.0", replace(config, max_box_width_atr=7.0)),
        ("breakout_confirm_bars=1", replace(config, breakout_confirm_bars=1)),
        ("min_box_width_atr=0.1", replace(config, min_box_width_atr=0.1)),
    ]
    for label, cfg in variations:
        count = 0
        for session_bars in sessions:
            det = DarvasDetector(cfg)
            for bar in session_bars:
                if det.add_bar(bar) is not None:
                    count += 1
        print(f"    {label:30s} -> {count:3d} signals (vs {breakouts_confirmed} baseline)")
    print()

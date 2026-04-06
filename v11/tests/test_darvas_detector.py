"""
Tests for DarvasDetector — CENTER module.

Phase 1 — Test Specifications (intent + regression for each design decision):

1. Box top confirmation
   Intent: After N bars without a new high, the candidate high becomes the confirmed box top.
   Regression: Top confirmed too early (fewer bars) or never confirmed (counter stuck).

2. Box bottom confirmation
   Intent: After the top is confirmed and N bars pass without a new low, the bottom is confirmed.
   Regression: Bottom confirmed before enough bars, or box never forms.

3. Box width validation (minimum)
   Intent: Boxes narrower than min_box_width_atr are rejected as noise.
   Regression: Micro-boxes (tick noise) trigger false signals.

4. Box width validation (maximum)
   Intent: Boxes wider than max_box_width_atr are rejected as meaningless.
   Regression: Extremely wide boxes (no real consolidation) generate signals.

5. Box duration validation
   Intent: Boxes that form in fewer than min_box_duration bars are rejected.
   Regression: Fleeting consolidations (2-3 bars) trigger false breakouts.

6. Breakout confirmation (long)
   Intent: M consecutive closes above box top confirm a long breakout.
   Regression: A single spike above the top triggers a signal (false breakout).

7. Breakout confirmation (short)
   Intent: M consecutive closes below box bottom confirm a short breakout.
   Regression: A single dip below the bottom triggers a signal.

8. Failed breakout returns to box
   Intent: If price closes back inside the box during breakout confirmation, reset to BOX_ACTIVE.
   Regression: A failed breakout leaves the detector stuck in CONFIRMING_BREAKOUT forever.

9. Box invalidation during bottom formation
   Intent: If price breaks above the confirmed top while seeking the bottom, restart top formation.
   Regression: The detector locks into a stale box top while price has already moved higher.

10. ATR feeds box width validation
    Intent: ATR must be computed from actual bar data and used for width filtering.
    Regression: ATR is 0 or NaN, causing all boxes to be accepted/rejected incorrectly.

11. Full lifecycle: formation → breakout → signal
    Intent: A complete Darvas cycle (new high → confirm top → confirm bottom → containment → breakout)
    produces exactly one BreakoutSignal with correct direction and box parameters.
    Regression: No signal emitted, or signal has wrong direction/box values.

12. No signal during formation
    Intent: Bars during box formation (SEEKING, CONFIRMING states) never produce signals.
    Regression: Premature signals during incomplete box formation.

Phase 2 — Test Implementation below.
"""
from datetime import datetime, timezone

import pytest

from v11.core.darvas_detector import DarvasDetector
from v11.core.types import Bar, Direction, BreakoutSignal
from v11.config.strategy_config import StrategyConfig


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_bar(close: float, high: float = None, low: float = None,
             ts_offset: int = 0) -> Bar:
    """Create a test bar. High/low default to close if not specified."""
    if high is None:
        high = close
    if low is None:
        low = close
    return Bar(
        timestamp=datetime(2026, 1, 1, 0, ts_offset, tzinfo=timezone.utc),
        open=close,
        high=high,
        low=low,
        close=close,
        tick_count=100,
        buy_volume=50.0,
        sell_volume=50.0,
    )


def make_config(**overrides) -> StrategyConfig:
    """Create a test config with small params for fast tests."""
    defaults = dict(
        instrument="TEST",
        top_confirm_bars=3,
        bottom_confirm_bars=3,
        min_box_width_atr=0.1,
        max_box_width_atr=10.0,
        min_box_duration=5,
        breakout_confirm_bars=2,
        atr_period=10,
    )
    defaults.update(overrides)
    return StrategyConfig(**defaults)


def feed_flat_bars(detector: DarvasDetector, price: float, count: int):
    """Feed N bars at a flat price."""
    for i in range(count):
        detector.add_bar(make_bar(price, ts_offset=i))


def seed_atr(detector: DarvasDetector, atr_value: float = 1.0):
    """Directly inject an ATR value without feeding bars through the state machine.

    This avoids polluting the detector's state with seed bars that form
    unwanted boxes or breakouts.
    """
    detector._atr = atr_value
    detector._atr_count = detector._atr_period  # mark as fully seeded
    detector._prev_close = 100.0  # so first real bar computes TR correctly


# ── Test 1: Box top confirmation ────────────────────────────────────────────

class TestBoxTopConfirmation:
    """Intent: N bars without new high → top confirmed."""

    def test_top_confirmed_after_n_bars(self):
        cfg = make_config(top_confirm_bars=3)
        det = DarvasDetector(cfg)
        seed_atr(det)

        # New high
        det.add_bar(make_bar(110.0, high=110.0))
        assert det.state == "CONFIRMING_TOP"

        # 3 bars without new high → top confirmed, now seeking bottom
        det.add_bar(make_bar(109.0))
        det.add_bar(make_bar(108.5))
        det.add_bar(make_bar(108.0))
        assert det.state == "CONFIRMING_BOTTOM"

    def test_top_not_confirmed_early(self):
        cfg = make_config(top_confirm_bars=3)
        det = DarvasDetector(cfg)
        seed_atr(det)

        det.add_bar(make_bar(110.0, high=110.0))
        det.add_bar(make_bar(109.0))
        det.add_bar(make_bar(108.5))
        # Only 2 bars without new high — should still be confirming
        assert det.state == "CONFIRMING_TOP"

    def test_new_high_resets_counter(self):
        cfg = make_config(top_confirm_bars=3)
        det = DarvasDetector(cfg)
        seed_atr(det)

        det.add_bar(make_bar(110.0, high=110.0))
        det.add_bar(make_bar(109.0))
        det.add_bar(make_bar(109.0))
        # New high resets the counter
        det.add_bar(make_bar(111.0, high=111.0))
        assert det.state == "CONFIRMING_TOP"
        # Need 3 more bars now
        det.add_bar(make_bar(109.0))
        det.add_bar(make_bar(109.0))
        assert det.state == "CONFIRMING_TOP"


# ── Test 2: Box bottom confirmation ─────────────────────────────────────────

class TestBoxBottomConfirmation:
    """Intent: After top confirmed, N bars without new low → bottom confirmed."""

    def test_bottom_confirmed_after_n_bars(self):
        cfg = make_config(top_confirm_bars=3, bottom_confirm_bars=3,
                          min_box_duration=1)
        det = DarvasDetector(cfg)
        seed_atr(det)

        # Form top
        det.add_bar(make_bar(110.0, high=110.0))
        for _ in range(3):
            det.add_bar(make_bar(108.0))
        assert det.state == "CONFIRMING_BOTTOM"

        # New low then confirm bottom
        det.add_bar(make_bar(105.0, low=105.0))
        for _ in range(3):
            det.add_bar(make_bar(106.0))
        assert det.state == "BOX_ACTIVE"


# ── Test 3: Box width validation (minimum) ──────────────────────────────────

class TestBoxWidthMinimum:
    """Intent: Boxes narrower than min_box_width_atr are rejected."""

    def test_narrow_box_rejected(self):
        cfg = make_config(
            top_confirm_bars=3, bottom_confirm_bars=3,
            min_box_width_atr=5.0,  # Very high threshold
            min_box_duration=1,
        )
        det = DarvasDetector(cfg)
        seed_atr(det, atr_value=1.0)

        # Form a narrow box (top=101, bottom=100.5 → width=0.5)
        det.add_bar(make_bar(101.0, high=101.0))
        for _ in range(3):
            det.add_bar(make_bar(100.8))
        # Now confirming bottom
        det.add_bar(make_bar(100.5, low=100.5))
        for _ in range(3):
            det.add_bar(make_bar(100.7))
        # Box too narrow → should reset to SEEKING_TOP
        assert det.state == "SEEKING_TOP"
        assert det.active_box is None


# ── Test 4: Box width validation (maximum) ──────────────────────────────────

class TestBoxWidthMaximum:
    """Intent: Boxes wider than max_box_width_atr are rejected."""

    def test_wide_box_rejected(self):
        cfg = make_config(
            top_confirm_bars=3, bottom_confirm_bars=3,
            max_box_width_atr=0.1,  # Very low threshold
            min_box_duration=1,
        )
        det = DarvasDetector(cfg)
        seed_atr(det, atr_value=1.0)

        # Form a wide box
        det.add_bar(make_bar(110.0, high=110.0))
        for _ in range(3):
            det.add_bar(make_bar(105.0))
        det.add_bar(make_bar(90.0, low=90.0))
        for _ in range(3):
            det.add_bar(make_bar(95.0))
        # Box too wide → should reset
        assert det.state == "SEEKING_TOP"
        assert det.active_box is None


# ── Test 5: Box duration validation ─────────────────────────────────────────

class TestBoxDuration:
    """Intent: Boxes forming in fewer than min_box_duration bars are rejected."""

    def test_short_duration_rejected(self):
        cfg = make_config(
            top_confirm_bars=2, bottom_confirm_bars=2,
            min_box_duration=50,  # Very high — box can't form this fast
            min_box_width_atr=0.0,
        )
        det = DarvasDetector(cfg)
        seed_atr(det, atr_value=1.0)

        det.add_bar(make_bar(110.0, high=110.0))
        for _ in range(2):
            det.add_bar(make_bar(108.0))
        det.add_bar(make_bar(105.0, low=105.0))
        for _ in range(2):
            det.add_bar(make_bar(106.0))
        # Duration too short → reset
        assert det.state == "SEEKING_TOP"


# ── Test 6: Breakout confirmation (long) ────────────────────────────────────

class TestLongBreakout:
    """Intent: M consecutive closes above box top → long breakout signal."""

    def _form_box(self, det: DarvasDetector, top: float, bottom: float):
        """Helper: form a valid box."""
        det.add_bar(make_bar(top, high=top))
        for _ in range(3):
            det.add_bar(make_bar(top - 1))
        det.add_bar(make_bar(bottom, low=bottom))
        for _ in range(3):
            det.add_bar(make_bar(bottom + 1))
        assert det.state == "BOX_ACTIVE"

    def test_long_breakout_signal(self):
        cfg = make_config(
            top_confirm_bars=3, bottom_confirm_bars=3,
            breakout_confirm_bars=2, min_box_duration=1,
            min_box_width_atr=0.0,
        )
        det = DarvasDetector(cfg)
        seed_atr(det, atr_value=1.0)
        self._form_box(det, top=110.0, bottom=105.0)

        # First bar above top → confirming
        result = det.add_bar(make_bar(111.0))
        assert result is None  # Not yet confirmed

        # Second bar above top → confirmed breakout
        result = det.add_bar(make_bar(112.0))
        assert result is not None
        assert isinstance(result, BreakoutSignal)
        assert result.direction == Direction.LONG
        assert result.box.top == 110.0
        assert result.box.bottom == 105.0

    def test_single_bar_not_enough(self):
        """A single close above top must not trigger with breakout_confirm_bars=2."""
        cfg = make_config(
            top_confirm_bars=3, bottom_confirm_bars=3,
            breakout_confirm_bars=2, min_box_duration=1,
            min_box_width_atr=0.0,
        )
        det = DarvasDetector(cfg)
        seed_atr(det, atr_value=1.0)
        self._form_box(det, top=110.0, bottom=105.0)

        result = det.add_bar(make_bar(111.0))
        assert result is None


# ── Test 7: Breakout confirmation (short) ───────────────────────────────────

class TestShortBreakout:
    """Intent: M consecutive closes below box bottom → short breakout signal."""

    def _form_box(self, det: DarvasDetector, top: float, bottom: float):
        det.add_bar(make_bar(top, high=top))
        for _ in range(3):
            det.add_bar(make_bar(top - 1))
        det.add_bar(make_bar(bottom, low=bottom))
        for _ in range(3):
            det.add_bar(make_bar(bottom + 1))
        assert det.state == "BOX_ACTIVE"

    def test_short_breakout_signal(self):
        cfg = make_config(
            top_confirm_bars=3, bottom_confirm_bars=3,
            breakout_confirm_bars=2, min_box_duration=1,
            min_box_width_atr=0.0,
        )
        det = DarvasDetector(cfg)
        seed_atr(det, atr_value=1.0)
        self._form_box(det, top=110.0, bottom=105.0)

        det.add_bar(make_bar(104.0))
        result = det.add_bar(make_bar(103.0))
        assert result is not None
        assert result.direction == Direction.SHORT
        assert result.box.top == 110.0
        assert result.box.bottom == 105.0


# ── Test 8: Failed breakout returns to box ──────────────────────────────────

class TestFailedBreakout:
    """Intent: Price falls back into box during confirmation → reset to BOX_ACTIVE."""

    def _form_box(self, det: DarvasDetector, top: float, bottom: float):
        det.add_bar(make_bar(top, high=top))
        for _ in range(3):
            det.add_bar(make_bar(top - 1))
        det.add_bar(make_bar(bottom, low=bottom))
        for _ in range(3):
            det.add_bar(make_bar(bottom + 1))

    def test_failed_long_breakout_returns_to_box(self):
        cfg = make_config(
            top_confirm_bars=3, bottom_confirm_bars=3,
            breakout_confirm_bars=3, min_box_duration=1,
            min_box_width_atr=0.0,
        )
        det = DarvasDetector(cfg)
        seed_atr(det, atr_value=1.0)
        self._form_box(det, top=110.0, bottom=105.0)

        # Start breakout
        det.add_bar(make_bar(111.0))
        assert det.state == "CONFIRMING_BREAKOUT"

        # Fall back into box
        det.add_bar(make_bar(108.0))
        assert det.state == "BOX_ACTIVE"
        assert det.active_box is not None  # Box still exists


# ── Test 9: Box invalidation during bottom formation ────────────────────────

class TestBoxInvalidation:
    """Intent: Price above confirmed top during bottom formation → restart."""

    def test_price_above_top_restarts_formation(self):
        cfg = make_config(top_confirm_bars=3, bottom_confirm_bars=3,
                          min_box_duration=1)
        det = DarvasDetector(cfg)
        seed_atr(det, atr_value=1.0)

        # Confirm top at 110
        det.add_bar(make_bar(110.0, high=110.0))
        for _ in range(3):
            det.add_bar(make_bar(108.0))
        assert det.state == "CONFIRMING_BOTTOM"

        # Price breaks above confirmed top
        det.add_bar(make_bar(112.0, high=112.0))
        assert det.state == "CONFIRMING_TOP"  # Restarted with new candidate top


# ── Test 10: ATR computation ────────────────────────────────────────────────

class TestATR:
    """Intent: ATR computed from actual bar data, used for width filtering."""

    def test_atr_nonzero_after_bars(self):
        cfg = make_config()
        det = DarvasDetector(cfg)

        # Feed bars with known range
        for i in range(20):
            bar = make_bar(
                close=100.0 + (i % 2),
                high=101.0 + (i % 2),
                low=99.0 + (i % 2),
            )
            det.add_bar(bar)

        assert det.current_atr > 0

    def test_atr_zero_initially(self):
        cfg = make_config()
        det = DarvasDetector(cfg)
        assert det.current_atr == 0.0


# ── Test 11: Full lifecycle ─────────────────────────────────────────────────

class TestFullLifecycle:
    """Intent: Complete Darvas cycle produces exactly one BreakoutSignal."""

    def test_complete_long_cycle(self):
        cfg = make_config(
            top_confirm_bars=3, bottom_confirm_bars=3,
            breakout_confirm_bars=2, min_box_duration=1,
            min_box_width_atr=0.0,
        )
        det = DarvasDetector(cfg)
        seed_atr(det, atr_value=1.0)

        signals = []

        # Phase 1: New high → confirm top
        signals.append(det.add_bar(make_bar(110.0, high=110.0)))
        for _ in range(3):
            signals.append(det.add_bar(make_bar(108.0)))

        # Phase 2: Confirm bottom
        signals.append(det.add_bar(make_bar(105.0, low=105.0)))
        for _ in range(3):
            signals.append(det.add_bar(make_bar(106.0)))

        # Phase 3: Containment (no signals)
        for _ in range(5):
            signals.append(det.add_bar(make_bar(107.0)))

        # Phase 4: Breakout
        signals.append(det.add_bar(make_bar(111.0)))
        signals.append(det.add_bar(make_bar(112.0)))

        # Exactly one signal in the entire sequence
        actual_signals = [s for s in signals if s is not None]
        assert len(actual_signals) == 1

        sig = actual_signals[0]
        assert sig.direction == Direction.LONG
        assert sig.box.top == 110.0
        assert sig.box.bottom == 105.0
        assert sig.breakout_price == 112.0


# ── Test 12: No signal during formation ─────────────────────────────────────

class TestNoSignalDuringFormation:
    """Intent: No signals emitted during SEEKING/CONFIRMING states."""

    def test_no_signals_before_box_active(self):
        cfg = make_config(top_confirm_bars=5, bottom_confirm_bars=5,
                          min_box_duration=100)  # Very high so no box forms
        det = DarvasDetector(cfg)
        seed_atr(det, atr_value=1.0)

        signals = []
        # Feed bars with slowly rising price — never enough flat bars to confirm top
        for i in range(50):
            price = 100.0 + i * 0.1  # Monotonically increasing
            signals.append(det.add_bar(make_bar(
                price, high=price + 0.05, low=price - 0.05)))

        assert all(s is None for s in signals)

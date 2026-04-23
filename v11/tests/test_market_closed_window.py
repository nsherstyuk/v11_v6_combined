"""Regression: _is_market_closed_window + staleness skip during weekly close.

Without this skip, the Fri 21:00 UTC → Sun 22:00 UTC no-tick window would
trip price_feed_dead every ~10 minutes, cycling emergency_shutdown +
wrapper restart ~270 times across a weekend.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock


def _runner_with_stub_log():
    """Hand-build just enough of V11LiveTrader to call _is_market_closed_window
    and _check_price_staleness without going through __init__."""
    from v11.live.run_live import V11LiveTrader
    r = V11LiveTrader.__new__(V11LiveTrader)
    r.log = MagicMock()
    r.conn = MagicMock()
    r.conn.connected = True
    r._active_pairs = ["XAUUSD"]
    r._last_price_time = {}
    return r


# ── window boundaries ────────────────────────────────────────────────────────

def test_open_window_monday_noon():
    r = _runner_with_stub_log()
    assert r._is_market_closed_window(
        datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)) is False  # Mon


def test_open_window_friday_2059_utc():
    r = _runner_with_stub_log()
    # Fri 20:59 UTC — still open, before the 21:00 close.
    assert r._is_market_closed_window(
        datetime(2026, 4, 24, 20, 59, tzinfo=timezone.utc)) is False


def test_closed_window_friday_2100_utc():
    r = _runner_with_stub_log()
    assert r._is_market_closed_window(
        datetime(2026, 4, 24, 21, 0, tzinfo=timezone.utc)) is True


def test_closed_window_saturday_all_day():
    r = _runner_with_stub_log()
    for h in (0, 12, 23):
        assert r._is_market_closed_window(
            datetime(2026, 4, 25, h, 0, tzinfo=timezone.utc)) is True


def test_closed_window_sunday_2159_utc():
    r = _runner_with_stub_log()
    assert r._is_market_closed_window(
        datetime(2026, 4, 26, 21, 59, tzinfo=timezone.utc)) is True


def test_open_window_sunday_2200_utc():
    r = _runner_with_stub_log()
    # Sun 22:00 UTC — market reopens.
    assert r._is_market_closed_window(
        datetime(2026, 4, 26, 22, 0, tzinfo=timezone.utc)) is False


# ── integration: staleness check respects the window ────────────────────────

def test_staleness_check_skipped_during_closed_window(monkeypatch):
    """During Saturday, even a 1000s stale price must not emergency-shutdown."""
    import v11.live.run_live as run_live

    r = _runner_with_stub_log()
    # Force datetime.now to return Saturday noon UTC.
    saturday = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)

    class _FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return saturday

    monkeypatch.setattr(run_live, "datetime", _FrozenDT)
    # Pretend last tick was 1000s ago.
    import time as time_mod
    r._last_price_time["XAUUSD"] = time_mod.time() - 1000
    r._emergency_shutdown = MagicMock()

    r._check_price_staleness()

    r._emergency_shutdown.assert_not_called()
    # No warn/error/critical either.
    for lvl in ("warning", "error", "critical"):
        assert getattr(r.log, lvl).called is False, (
            f"staleness path logged at {lvl} during closed window")


def test_staleness_check_still_fires_during_open_window(monkeypatch):
    """Sanity: during open market hours, a 700s gap still triggers
    the emergency-shutdown branch."""
    import v11.live.run_live as run_live

    r = _runner_with_stub_log()
    tuesday = datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc)

    class _FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return tuesday

    monkeypatch.setattr(run_live, "datetime", _FrozenDT)
    import time as time_mod
    r._last_price_time["XAUUSD"] = time_mod.time() - 700
    r._emergency_shutdown = MagicMock()

    r._check_price_staleness()

    r._emergency_shutdown.assert_called_once_with("price_feed_dead")

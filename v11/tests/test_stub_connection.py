"""Tests for StubIBKRConnection — minimal stub for dry_run TradeManager."""
from v11.replay.stub_connection import StubIBKRConnection


class TestStubConnection:
    def test_submit_market_order_returns_none(self):
        stub = StubIBKRConnection()
        result = stub.submit_market_order("EURUSD", "long", 20000)
        assert result is None

    def test_submit_stop_order_returns_none(self):
        stub = StubIBKRConnection()
        result = stub.submit_stop_order("EURUSD", "long", 20000, 1.1000)
        assert result is None

    def test_get_position_size_returns_zero(self):
        stub = StubIBKRConnection()
        assert stub.get_position_size("EUR", "CASH") == 0.0

    def test_has_position_returns_false(self):
        stub = StubIBKRConnection()
        assert stub.has_position("EUR", "CASH") is False

    def test_sleep_is_noop(self):
        stub = StubIBKRConnection()
        stub.sleep(5)  # should not block

    def test_get_fill_commission_returns_zero(self):
        stub = StubIBKRConnection()
        assert stub.get_fill_commission(None) == 0.0

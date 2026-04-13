"""Tests for replay metrics computation."""
import pytest
from v11.replay.metrics import compute_metrics


def _make_trades(pnls):
    """Helper: make trade records from a list of PnLs."""
    return [
        {"pnl": pnl, "strategy": "DARVAS", "instrument": "EURUSD",
         "exit_reason": "TARGET" if pnl > 0 else "SL"}
        for pnl in pnls
    ]


class TestComputeMetrics:
    def test_empty_trades(self):
        m = compute_metrics([])
        assert m["total_trades"] == 0
        assert m["net_pnl"] == 0.0

    def test_all_winners(self):
        trades = _make_trades([100, 200, 50])
        m = compute_metrics(trades)
        assert m["total_trades"] == 3
        assert m["win_rate"] == 1.0
        assert m["net_pnl"] == 350.0

    def test_mixed_trades(self):
        trades = _make_trades([100, -50, 200, -30, -20])
        m = compute_metrics(trades)
        assert m["total_trades"] == 5
        assert m["win_rate"] == pytest.approx(0.4)
        assert m["net_pnl"] == 200.0

    def test_profit_factor(self):
        trades = _make_trades([100, -50, 200])
        m = compute_metrics(trades)
        # profit_factor = gross_profit / gross_loss = 300 / 50 = 6.0
        assert m["profit_factor"] == pytest.approx(6.0)

    def test_max_drawdown(self):
        trades = _make_trades([100, -50, -80, 200])
        m = compute_metrics(trades, starting_equity=10000)
        # Equity: 10000, 10100, 10050, 9970, 10170
        # Peak: 10000, 10100, 10100, 10100, 10170
        # DD:   0,     0,     50,    130,   0
        assert m["max_drawdown"] == pytest.approx(130.0)
        assert m["max_drawdown_pct"] == pytest.approx(1.3, abs=0.01)

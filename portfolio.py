"""
PortfolioTracker — stub for future position tracking.

Currently logs position events only. Will evolve to:
  - Track open positions and their P&L
  - Feed real P&L into RiskManager (replacing committed-risk approximation)
  - Support trailing stops and scale-out logic

*** This is an EDGE element until it starts affecting risk calculations,
    at which point it becomes CENTER. See ARCHITECTURE.md. ***
"""

from utils.logger import setup_logger

log = setup_logger()


class PortfolioTracker:
    """Minimal stub — tracks positions in memory for logging only."""

    def __init__(self) -> None:
        self.positions: dict[str, dict] = {}

    def record_entry(self, ticker: str, shares: int, entry: float, stop: float, target: float | None) -> None:
        self.positions[ticker] = {
            "shares": shares,
            "entry": entry,
            "stop": stop,
            "target": target,
            "status": "OPEN",
        }
        log.info(f"[PortfolioTracker] Recorded OPEN: {shares} {ticker} @ {entry}")

    def record_exit(self, ticker: str, exit_price: float) -> None:
        pos = self.positions.get(ticker)
        if pos:
            pnl = (exit_price - pos["entry"]) * pos["shares"]
            pos["status"] = "CLOSED"
            pos["exit"] = exit_price
            pos["pnl"] = pnl
            log.info(f"[PortfolioTracker] Closed {ticker} @ {exit_price} | P&L: ${pnl:.2f}")
        else:
            log.warning(f"[PortfolioTracker] No open position for {ticker}")

    def get_open_positions(self) -> dict[str, dict]:
        return {t: p for t, p in self.positions.items() if p["status"] == "OPEN"}

    def summary(self) -> str:
        open_pos = self.get_open_positions()
        if not open_pos:
            return "No open positions"
        lines = [f"  {t}: {p['shares']} shares @ {p['entry']} (stop={p['stop']})" for t, p in open_pos.items()]
        return "Open positions:\n" + "\n".join(lines)

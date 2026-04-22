"""Settlement tracker — detect resolved markets and close positions.

Monitors the MarketFetcher for closed/resolved markets and automatically
settles open positions, calculating realized P&L.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from polymarket_glm.models import Position

logger = logging.getLogger(__name__)


@dataclass
class SettlementResult:
    """Result of settling a single position."""
    market_id: str
    outcome: str
    size: float
    avg_price: float
    settlement_price: float
    realized_pnl: float
    proceeds: float
    winning_outcome: str

    @property
    def is_profitable(self) -> bool:
        return self.realized_pnl > 0


@dataclass
class SettlementSummary:
    """Summary of a settlement scan."""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    num_settled: int = 0
    total_realized_pnl: float = 0.0
    settlements: list[SettlementResult] = field(default_factory=list)


class SettlementTracker:
    """Track and settle positions when markets resolve.

    Usage:
        tracker = SettlementTracker()
        # After each market scan, check for resolved markets
        summary = tracker.check_settlements(
            positions=positions,
            resolved_markets={market_id: winning_outcome},
        )
        # Apply settlements to executor balance
        for settlement in summary.settlements:
            executor.credit_settlement(settlement.proceeds)
    """

    def __init__(self) -> None:
        self._total_realized_pnl: float = 0.0
        self._settlement_history: list[SettlementResult] = []
        self._settled_markets: set[str] = set()

    @property
    def total_realized_pnl(self) -> float:
        return self._total_realized_pnl

    @property
    def settlement_history(self) -> list[SettlementResult]:
        return list(self._settlement_history)

    def check_settlements(
        self,
        positions: list[Position],
        resolved_markets: dict[str, str],
    ) -> SettlementSummary:
        """Check open positions against resolved markets.

        Args:
            positions: List of open positions from the executor.
            resolved_markets: Mapping of market_id -> winning_outcome (e.g. "Yes", "No").

        Returns:
            SettlementSummary with all positions that were settled.
        """
        settlements: list[SettlementResult] = []
        total_pnl = 0.0

        for pos in positions:
            if pos.market_id in self._settled_markets:
                continue  # Already settled

            winning_outcome = resolved_markets.get(pos.market_id)
            if winning_outcome is None:
                continue  # Market not resolved yet

            # Calculate settlement
            if pos.outcome == winning_outcome:
                # Position won → pays out $1 per share
                settlement_price = 1.0
            else:
                # Position lost → pays out $0
                settlement_price = 0.0

            proceeds = pos.size * settlement_price
            cost_basis = pos.size * pos.avg_price
            realized_pnl = proceeds - cost_basis

            result = SettlementResult(
                market_id=pos.market_id,
                outcome=pos.outcome,
                size=pos.size,
                avg_price=pos.avg_price,
                settlement_price=settlement_price,
                realized_pnl=round(realized_pnl, 4),
                proceeds=round(proceeds, 4),
                winning_outcome=winning_outcome,
            )

            settlements.append(result)
            total_pnl += realized_pnl
            self._settled_markets.add(pos.market_id)
            self._settlement_history.append(result)
            self._total_realized_pnl += realized_pnl

            status = "✅ WIN" if result.is_profitable else "❌ LOSS"
            logger.info(
                "%s Settlement: %s/%s size=%.0f avg=%.2f → settle=%.2f P&L=$%.2f",
                status, pos.market_id[:16], pos.outcome,
                pos.size, pos.avg_price, settlement_price, realized_pnl,
            )

        return SettlementSummary(
            num_settled=len(settlements),
            total_realized_pnl=round(total_pnl, 4),
            settlements=settlements,
        )

    def is_market_settled(self, market_id: str) -> bool:
        """Check if a market has already been settled."""
        return market_id in self._settled_markets

    def reset(self) -> None:
        """Reset tracker state (for testing)."""
        self._total_realized_pnl = 0.0
        self._settlement_history.clear()
        self._settled_markets.clear()

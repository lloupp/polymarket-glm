"""Portfolio tracker — mark-to-market P&L for open positions.

Calculates unrealized P&L by comparing each position's avg_price
against the current market price. Also tracks realized P&L from
closed trades and provides a portfolio summary.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from polymarket_glm.models import Position

logger = logging.getLogger(__name__)


@dataclass
class PositionPnL:
    """P&L for a single position."""
    market_id: str
    outcome: str
    size: float
    avg_price: float
    current_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    cost_basis: float
    market_value: float

    @property
    def is_profitable(self) -> bool:
        return self.unrealized_pnl > 0


@dataclass
class PortfolioSummary:
    """Snapshot of portfolio state with P&L."""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    balance_usd: float = 0.0
    total_cost_basis: float = 0.0
    total_market_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    total_pnl: float = 0.0
    positions: list[PositionPnL] = field(default_factory=list)
    num_open_positions: int = 0

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.total_cost_basis == 0:
            return 0.0
        return self.unrealized_pnl / self.total_cost_basis * 100

    @property
    def total_pnl_pct(self) -> float:
        initial = self.balance_usd + self.total_cost_basis
        if initial == 0:
            return 0.0
        return self.total_pnl / initial * 100


class PortfolioTracker:
    """Track mark-to-market P&L for open positions.

    Usage:
        tracker = PortfolioTracker()
        # After each scan, update positions with current prices
        summary = tracker.calculate(positions, price_lookup, balance, realized_pnl)
    """

    def __init__(self) -> None:
        self._last_summary: PortfolioSummary | None = None

    @property
    def last_summary(self) -> PortfolioSummary | None:
        return self._last_summary

    def calculate(
        self,
        positions: list[Position],
        price_lookup: dict[str, float],
        balance_usd: float = 0.0,
        realized_pnl: float = 0.0,
    ) -> PortfolioSummary:
        """Calculate mark-to-market P&L for all positions.

        Args:
            positions: List of open positions from the executor.
            price_lookup: Mapping of market_id -> current YES price.
            balance_usd: Current cash balance.
            realized_pnl: Cumulative realized P&L from closed trades.

        Returns:
            PortfolioSummary with full P&L breakdown.
        """
        position_pnls: list[PositionPnL] = []
        total_cost_basis = 0.0
        total_market_value = 0.0
        total_unrealized_pnl = 0.0

        for pos in positions:
            current_price = price_lookup.get(pos.market_id, pos.avg_price)

            cost_basis = pos.size * pos.avg_price

            # Mark-to-market: current value = size * current_price
            # For YES positions, value = size * current_price
            # P&L = (current_price - avg_price) * size
            market_value = pos.size * current_price
            unrealized_pnl = (current_price - pos.avg_price) * pos.size
            unrealized_pnl_pct = (
                (current_price - pos.avg_price) / pos.avg_price * 100
                if pos.avg_price > 0
                else 0.0
            )

            total_cost_basis += cost_basis
            total_market_value += market_value
            total_unrealized_pnl += unrealized_pnl

            position_pnls.append(PositionPnL(
                market_id=pos.market_id,
                outcome=pos.outcome,
                size=pos.size,
                avg_price=pos.avg_price,
                current_price=current_price,
                unrealized_pnl=round(unrealized_pnl, 4),
                unrealized_pnl_pct=round(unrealized_pnl_pct, 2),
                cost_basis=round(cost_basis, 4),
                market_value=round(market_value, 4),
            ))

        summary = PortfolioSummary(
            balance_usd=balance_usd,
            total_cost_basis=round(total_cost_basis, 4),
            total_market_value=round(total_market_value, 4),
            unrealized_pnl=round(total_unrealized_pnl, 4),
            realized_pnl=round(realized_pnl, 4),
            total_pnl=round(total_unrealized_pnl + realized_pnl, 4),
            positions=position_pnls,
            num_open_positions=len(position_pnls),
        )

        self._last_summary = summary
        return summary

    def get_position_pnl(
        self,
        position: Position,
        current_price: float,
    ) -> PositionPnL:
        """Calculate P&L for a single position."""
        cost_basis = position.size * position.avg_price
        market_value = position.size * current_price
        unrealized_pnl = (current_price - position.avg_price) * position.size
        unrealized_pnl_pct = (
            (current_price - position.avg_price) / position.avg_price * 100
            if position.avg_price > 0
            else 0.0
        )

        return PositionPnL(
            market_id=position.market_id,
            outcome=position.outcome,
            size=position.size,
            avg_price=position.avg_price,
            current_price=current_price,
            unrealized_pnl=round(unrealized_pnl, 4),
            unrealized_pnl_pct=round(unrealized_pnl_pct, 2),
            cost_basis=round(cost_basis, 4),
            market_value=round(market_value, 4),
        )

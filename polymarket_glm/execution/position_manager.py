"""Position manager — monitors open positions for take-profit / stop-loss.

Adapts the existing PaperExecutor + Position model to support buy-low/sell-high
before event resolution. Uses the same Side/outcome conventions already in use.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from polymarket_glm.models import Position, Side

logger = logging.getLogger(__name__)


@dataclass
class PositionManagerConfig:
    """Configuration for position management."""
    tp_pct: float = 0.50  # take-profit at 50% gain (e.g. buy at 0.10 → TP at 0.15)
    sl_pct: float = 0.50  # stop-loss at 50% loss (e.g. buy at 0.10 → SL at 0.05)
    min_hold_iterations: int = 1  # minimum cycles before allowing close


class PositionManager:
    """Monitors open positions and decides when to close them.

    Works with the existing PaperExecutor — does not replace it.
    The runner calls check_positions() each iteration after fetching market prices.
    """

    def __init__(self, config: PositionManagerConfig | None = None):
        self._config = config or PositionManagerConfig()

    def should_close(
        self,
        position: Position,
        current_price: float,
        current_iteration: int,
    ) -> tuple[bool, str]:
        """Decide whether to close a position.

        Returns (should_close: bool, reason: str).
        """
        if position.status != "open":
            return False, "already_closed"

        # Don't close too early
        hold_iterations = current_iteration - position.opened_at_iteration
        if hold_iterations < self._config.min_hold_iterations:
            return False, "min_hold_not_reached"

        entry_price = position.avg_price
        if entry_price <= 0:
            return False, "invalid_entry_price"

        # Calculate return percentage
        if position.outcome.upper() == "YES":
            # For YES positions: profit when price goes up
            return_pct = (current_price - entry_price) / entry_price
        else:
            # For NO positions: profit when NO price goes up
            return_pct = (current_price - entry_price) / entry_price

        # Take-profit check (with small tolerance for floating point)
        if return_pct >= self._config.tp_pct - 1e-9:
            return True, "take_profit"

        # Stop-loss check (with small tolerance for floating point)
        if return_pct <= -self._config.sl_pct + 1e-9:
            return True, "stop_loss"

        return False, "holding"

    def calculate_exit_order(
        self,
        position: Position,
        current_price: float,
        reason: str,
        current_iteration: int,
    ) -> dict:
        """Build the exit order parameters for a closing position.

        Returns a dict with keys needed by PaperExecutor.submit_order_sync().
        """
        realized_pnl = self._calculate_realized_pnl(position, current_price)

        logger.info(
            "📉 Closing position: market=%s outcome=%s entry=%.4f exit=%.4f "
            "reason=%s pnl=%.2f iter_open=%d iter_close=%d",
            position.market_id[:12], position.outcome, position.avg_price,
            current_price, reason, realized_pnl,
            position.opened_at_iteration, current_iteration,
        )

        return {
            "market_id": position.market_id,
            "side": Side.SELL,
            "outcome": position.outcome,
            "price": current_price,
            "size": position.size,
            "_reason": reason,
            "_realized_pnl": realized_pnl,
            "_iteration": current_iteration,
        }

    @staticmethod
    def _calculate_realized_pnl(position: Position, exit_price: float) -> float:
        """Calculate realized P&L for closing a position.

        YES: pnl = (exit_price - entry_price) * size
        NO:  pnl = (exit_price - entry_price) * size  (same formula, NO prices)
        """
        return (exit_price - position.avg_price) * position.size

    @staticmethod
    def set_targets(
        position: Position,
        tp_pct: float = 0.50,
        sl_pct: float = 0.50,
    ) -> Position:
        """Set take-profit and stop-loss targets on a position.

        Returns the same Position with target_price and stop_loss_price set.
        """
        entry = position.avg_price

        if position.outcome.upper() == "YES":
            position.target_price = entry * (1 + tp_pct)
            position.stop_loss_price = entry * (1 - sl_pct)
        else:
            # For NO: TP when NO price rises, SL when NO price falls
            position.target_price = entry * (1 + tp_pct)
            position.stop_loss_price = entry * (1 - sl_pct)

        # Clamp to [0, 1]
        position.target_price = max(0.01, min(0.99, position.target_price))
        position.stop_loss_price = max(0.01, min(0.99, position.stop_loss_price))

        return position

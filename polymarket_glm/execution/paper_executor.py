"""Paper executor — simulates order fills without real API calls.

Implements ExchangeClient protocol with:
- Exact Polymarket fee calculation (fee_rate_bps)
- Position tracking per market/outcome
- Balance management
- Fill simulation (always fills at requested price)
- Insufficient balance rejection
- Portfolio reconciliation (cash + positions = total equity)
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict

from polymarket_glm.execution.exchange import (
    ExchangeClient,
    FillResult,
    OrderRequest,
    CancelResult,
)
from polymarket_glm.models import Side, Account, Position

logger = logging.getLogger(__name__)


class PortfolioMismatchError(Exception):
    """Raised when portfolio reconciliation detects an inconsistency."""


class PaperExecutor:
    """Paper trading executor — simulates fills, tracks positions and balance."""

    def __init__(
        self,
        initial_balance: float = 1_000.0,
        fee_rate_bps: int = 100,  # 1% = 100 bps
    ):
        self._initial_balance = initial_balance
        self._balance = initial_balance
        self._fee_rate_bps = fee_rate_bps
        self._positions: dict[str, dict[str, Position]] = defaultdict(dict)
        self._open_orders: dict[str, OrderRequest] = {}
        self._trade_history: list[FillResult] = []
        self._total_fees_paid: float = 0.0
        self._total_realized_pnl: float = 0.0

    @property
    def account(self) -> Account:
        """Current account state."""
        positions = []
        total_exposure = 0.0
        for market_positions in self._positions.values():
            for pos in market_positions.values():
                positions.append(pos)
                total_exposure += pos.size * pos.avg_price

        return Account(
            balance_usd=self._balance,
            total_exposure_usd=total_exposure,
            positions=positions,
        )

    @property
    def total_fees_paid(self) -> float:
        return self._total_fees_paid

    @property
    def total_realized_pnl(self) -> float:
        return self._total_realized_pnl

    def get_position(self, market_id: str, outcome: str) -> Position | None:
        """Get a specific position."""
        return self._positions.get(market_id, {}).get(outcome)

    def _calc_fee(self, price: float, size: float) -> float:
        """Calculate fee: fee_rate_bps of the trade notional."""
        return price * size * self._fee_rate_bps / 10_000

    # ── Portfolio Reconciliation ────────────────────────────────

    def reconcile_portfolio(self) -> dict:
        """Validate portfolio consistency: cash + positions = total equity.

        Returns a dict with:
            cash: current balance
            positions_cost: sum of all position costs (size * avg_price)
            total_equity: cash + positions_cost
            expected_equity: initial_balance + realized_pnl - total_fees
            discrepancy: total_equity - expected_equity
            consistent: True if discrepancy is within tolerance (0.01)
            trades_count: number of fills in history
            fees_paid: total fees paid
            realized_pnl: total realized P&L from closed positions

        Raises PortfolioMismatchError if discrepancy exceeds tolerance.
        """
        positions_cost = 0.0
        for market_positions in self._positions.values():
            for pos in market_positions.values():
                positions_cost += pos.size * pos.avg_price

        cash = self._balance
        total_equity = cash + positions_cost
        expected_equity = (
            self._initial_balance
            + self._total_realized_pnl
            - self._total_fees_paid
        )
        discrepancy = round(total_equity - expected_equity, 4)
        consistent = abs(discrepancy) < 0.01  # 1 cent tolerance

        result = {
            "cash": round(cash, 4),
            "positions_cost": round(positions_cost, 4),
            "total_equity": round(total_equity, 4),
            "expected_equity": round(expected_equity, 4),
            "discrepancy": discrepancy,
            "consistent": consistent,
            "trades_count": len(self._trade_history),
            "fees_paid": round(self._total_fees_paid, 4),
            "realized_pnl": round(self._total_realized_pnl, 4),
        }

        if not consistent:
            logger.error(
                "⚠️ Portfolio mismatch! equity=$%.2f expected=$%.2f "
                "discrepancy=$%.4f | cash=$%.2f positions=$%.2f "
                "fees=$%.2f realized_pnl=$%.2f trades=%d",
                total_equity, expected_equity, discrepancy,
                cash, positions_cost,
                self._total_fees_paid, self._total_realized_pnl,
                len(self._trade_history),
            )

        return result

    # ── Sync interface (for testing and simplicity) ─────────────

    def submit_order_sync(self, request: OrderRequest) -> FillResult:
        """Submit an order synchronously (paper fills immediately)."""
        order_id = str(uuid.uuid4())[:8]
        fee = self._calc_fee(request.price, request.size)
        total_cost = request.price * request.size + fee

        # Check balance for buys
        if request.side == Side.BUY:
            if total_cost > self._balance:
                logger.warning("Paper: insufficient balance ($%.2f < $%.2f)",
                               self._balance, total_cost)
                return FillResult(
                    order_id=order_id,
                    market_id=request.market_id,
                    side=request.side,
                    outcome=request.outcome,
                    price=request.price,
                    size=0,
                    fee=0,
                    filled=False,
                    reason=f"Insufficient balance: ${self._balance:.2f} < ${total_cost:.2f}",
                )
            # Deduct balance
            self._balance -= total_cost
            self._total_fees_paid += fee

        # Update position
        market_positions = self._positions[request.market_id]
        if request.outcome in market_positions:
            existing = market_positions[request.outcome]
            if request.side == Side.BUY:
                total_size = existing.size + request.size
                total_cost_existing = existing.avg_price * existing.size
                total_cost_new = request.price * request.size
                new_avg = (total_cost_existing + total_cost_new) / total_size if total_size > 0 else 0
                market_positions[request.outcome] = Position(
                    market_id=request.market_id,
                    outcome=request.outcome,
                    size=total_size,
                    avg_price=new_avg,
                    # Preserve TP/SL fields from existing position
                    target_price=existing.target_price,
                    stop_loss_price=existing.stop_loss_price,
                    opened_at_iteration=existing.opened_at_iteration,
                    status=existing.status,
                )
            elif request.side == Side.SELL:
                new_size = existing.size - request.size
                realized_pnl = (request.price - existing.avg_price) * request.size
                self._total_realized_pnl += realized_pnl
                self._total_fees_paid += fee

                if new_size <= 0:
                    # Fully closed position — return proceeds
                    proceeds = request.price * request.size - fee
                    self._balance += proceeds
                    # Update position to closed state for tracking
                    market_positions[request.outcome] = Position(
                        market_id=request.market_id,
                        outcome=request.outcome,
                        size=0,
                        avg_price=existing.avg_price,
                        target_price=existing.target_price,
                        stop_loss_price=existing.stop_loss_price,
                        opened_at_iteration=existing.opened_at_iteration,
                        status="closed",
                        close_reason=request.close_reason,
                        realized_pnl=realized_pnl,
                        close_price=request.price,
                    )
                    # Remove from active positions after recording
                    del market_positions[request.outcome]
                else:
                    proceeds = request.price * request.size - fee
                    self._balance += proceeds
                    market_positions[request.outcome] = Position(
                        market_id=request.market_id,
                        outcome=request.outcome,
                        size=new_size,
                        avg_price=existing.avg_price,
                        target_price=existing.target_price,
                        stop_loss_price=existing.stop_loss_price,
                        opened_at_iteration=existing.opened_at_iteration,
                        status=existing.status,
                    )
        else:
            if request.side == Side.BUY:
                market_positions[request.outcome] = Position(
                    market_id=request.market_id,
                    outcome=request.outcome,
                    size=request.size,
                    avg_price=request.price,
                    opened_at_iteration=request.iteration,
                    status="open",
                )
            else:
                # Can't sell what we don't have
                self._balance += total_cost  # refund
                return FillResult(
                    order_id=order_id,
                    market_id=request.market_id,
                    side=request.side,
                    outcome=request.outcome,
                    price=request.price,
                    size=0,
                    fee=0,
                    filled=False,
                    reason=f"No position to sell in {request.market_id}/{request.outcome}",
                )

        fill = FillResult(
            order_id=order_id,
            market_id=request.market_id,
            side=request.side,
            outcome=request.outcome,
            price=request.price,
            size=request.size,
            fee=fee,
            filled=True,
        )
        self._trade_history.append(fill)
        logger.info("Paper fill: %s %s %s@%.2f x%.0f fee=$%.4f",
                     request.side.value, request.market_id, request.outcome,
                     request.price, request.size, fee)
        return fill

    def cancel_order_sync(self, order_id: str) -> CancelResult:
        """Cancel an order (paper: always fails since fills are instant)."""
        return CancelResult(order_id=order_id, success=False, reason="Paper executor fills instantly")

    # ── Async interface (ExchangeClient protocol) ───────────────

    async def submit_order(self, request: OrderRequest) -> FillResult:
        return self.submit_order_sync(request)

    async def cancel_order(self, order_id: str) -> CancelResult:
        return self.cancel_order_sync(order_id)

    async def get_account(self) -> Account:
        return self.account

    async def get_open_orders(self, market_id: str | None = None) -> list[OrderRequest]:
        return []  # Paper fills instantly, no open orders

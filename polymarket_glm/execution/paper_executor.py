"""Paper executor — simulates order fills without real API calls.

Implements ExchangeClient protocol with:
- Exact Polymarket fee calculation (fee_rate_bps)
- Position tracking per market/outcome
- Balance management
- Fill simulation (always fills at requested price)
- Insufficient balance rejection
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


class PaperExecutor:
    """Paper trading executor — simulates fills, tracks positions and balance."""

    def __init__(
        self,
        initial_balance: float = 10_000.0,
        fee_rate_bps: int = 100,  # 1% = 100 bps
    ):
        self._balance = initial_balance
        self._fee_rate_bps = fee_rate_bps
        self._positions: dict[str, dict[str, Position]] = defaultdict(dict)
        self._open_orders: dict[str, OrderRequest] = {}
        self._trade_history: list[FillResult] = []

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

    def get_position(self, market_id: str, outcome: str) -> Position | None:
        """Get a specific position."""
        return self._positions.get(market_id, {}).get(outcome)

    def _calc_fee(self, price: float, size: float) -> float:
        """Calculate fee: fee_rate_bps of the trade notional."""
        return price * size * self._fee_rate_bps / 10_000

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
                )
            elif request.side == Side.SELL:
                new_size = existing.size - request.size
                if new_size <= 0:
                    # Fully closed position — return proceeds
                    proceeds = request.price * request.size - fee
                    self._balance += proceeds
                    del market_positions[request.outcome]
                else:
                    proceeds = request.price * request.size - fee
                    self._balance += proceeds
                    market_positions[request.outcome] = Position(
                        market_id=request.market_id,
                        outcome=request.outcome,
                        size=new_size,
                        avg_price=existing.avg_price,
                    )
        else:
            if request.side == Side.BUY:
                market_positions[request.outcome] = Position(
                    market_id=request.market_id,
                    outcome=request.outcome,
                    size=request.size,
                    avg_price=request.price,
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

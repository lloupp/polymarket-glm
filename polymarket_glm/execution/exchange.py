"""Exchange protocol — abstract interface for paper and live execution.

Uses Python's typing.Protocol so PaperExecutor and LiveExecutor share the
exact same interface. The engine only depends on ExchangeClient, never on
a concrete implementation.
"""
from __future__ import annotations

import enum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from polymarket_glm.models import Side, Account


class OrderRequest(BaseModel):
    """Order to be submitted to the exchange."""
    market_id: str
    side: Side
    outcome: str
    price: float = Field(ge=0, le=1)
    size: float = Field(gt=0)
    order_type: str = "GTC"  # GTC, GTD, FOK
    # Optional metadata for position management (used by PaperExecutor)
    iteration: int = 0  # cycle when order was placed
    close_reason: str = ""  # "take_profit" | "stop_loss" | "" for new entries

    @property
    def usd_value(self) -> float:
        return self.price * self.size


class FillResult(BaseModel):
    """Result of a submitted order (filled or not)."""
    order_id: str
    market_id: str
    side: Side
    outcome: str
    price: float = Field(ge=0, le=1)
    size: float = Field(ge=0)
    fee: float = 0.0
    filled: bool = False
    partial: bool = False
    reason: str = ""

    @property
    def total_cost(self) -> float:
        return self.price * self.size + self.fee


class CancelResult(BaseModel):
    """Result of a cancel request."""
    order_id: str
    success: bool
    reason: str = ""


@runtime_checkable
class ExchangeClient(Protocol):
    """Protocol defining the exchange interface.

    Both PaperExecutor and LiveExecutor must implement these methods.
    The engine never knows which one it's using.
    """

    async def submit_order(self, request: OrderRequest) -> FillResult:
        """Submit an order and return fill result."""
        ...

    async def cancel_order(self, order_id: str) -> CancelResult:
        """Cancel a pending order."""
        ...

    async def get_account(self) -> Account:
        """Get current account state (balance, positions)."""
        ...

    async def get_open_orders(self, market_id: str | None = None) -> list[OrderRequest]:
        """Get open/pending orders, optionally filtered by market."""
        ...

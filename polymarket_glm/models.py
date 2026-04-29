"""Core data models for polymarket-glm."""
from __future__ import annotations

import enum
from datetime import datetime
from pydantic import BaseModel, Field, computed_field


class Side(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class Market(BaseModel):
    condition_id: str
    market_id: str
    question: str
    outcomes: list[str]
    outcome_prices: list[float]
    tokens: list[str]
    active: bool = True
    closed: bool = False
    neg_risk: bool = False
    volume: float = 0.0
    end_date_iso: str = ""
    slug: str = ""
    fetched_at: datetime = Field(default_factory=datetime.utcnow)

    @computed_field
    @property
    def spread_bps(self) -> float:
        if len(self.outcome_prices) < 2:
            return 0.0
        yes_price = self.outcome_prices[0]
        no_price = self.outcome_prices[1]
        # implied ask for yes = 1 - no_price
        ask = 1.0 - no_price
        mid = (yes_price + ask) / 2
        if mid == 0:
            return 0.0
        return abs(ask - yes_price) / mid * 10_000


class OrderBookLevel(BaseModel):
    price: float = Field(ge=0, le=1)
    size: float = Field(gt=0)

    @computed_field
    @property
    def cost(self) -> float:
        return self.price * self.size


class OrderBook(BaseModel):
    market_id: str
    bids: list[OrderBookLevel] = []
    asks: list[OrderBookLevel] = []
    fee_rate_bps: int = 0
    fetched_at: datetime = Field(default_factory=datetime.utcnow)

    @computed_field
    @property
    def best_bid(self) -> OrderBookLevel | None:
        return max(self.bids, key=lambda l: l.price) if self.bids else None

    @computed_field
    @property
    def best_ask(self) -> OrderBookLevel | None:
        return min(self.asks, key=lambda l: l.price) if self.asks else None

    @computed_field
    @property
    def midpoint(self) -> float | None:
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid.price + self.best_ask.price) / 2
        return None

    @computed_field
    @property
    def spread_bps(self) -> float | None:
        if self.best_bid is not None and self.best_ask is not None and self.midpoint:
            return (self.best_ask.price - self.best_bid.price) / self.midpoint * 10_000
        return None


class Order(BaseModel):
    order_id: str = ""
    market_id: str
    side: Side
    outcome: str
    price: float = Field(ge=0, le=1)
    size: float = Field(gt=0)
    order_type: str = "GTC"
    status: str = "pending"
    filled_size: float = 0.0
    filled_price: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Trade(BaseModel):
    trade_id: str = ""
    market_id: str
    side: Side
    outcome: str
    price: float = Field(ge=0, le=1)
    size: float = Field(gt=0)
    fee: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @computed_field
    @property
    def total_cost(self) -> float:
        return self.price * self.size + self.fee


class Position(BaseModel):
    market_id: str
    outcome: str
    size: float = 0.0
    avg_price: float = 0.0
    unrealized_pnl: float = 0.0
    # Position management fields (optional, for TP/SL)
    target_price: float | None = None  # take-profit price
    stop_loss_price: float | None = None  # stop-loss price
    opened_at_iteration: int = 0
    status: str = "open"  # "open" | "closed"
    close_reason: str = ""  # "take_profit" | "stop_loss" | "resolved" | "manual"
    realized_pnl: float = 0.0
    close_price: float | None = None
    closed_at_iteration: int | None = None


class DecisionType(str, enum.Enum):
 """All possible paper-trading decision states."""
 BUY_YES = "BUY_YES"
 BUY_NO = "BUY_NO"
 HOLD = "HOLD"
 REJECT = "REJECT"
 CLOSE_POSITION = "CLOSE_POSITION"


class DecisionResult(BaseModel):
    """Structured result from every market evaluation — full audit trail."""
    decision: DecisionType
    market_id: str = ""
    question: str = ""
    outcome: str = ""  # "Yes" or "No"
    signal_type: str = ""  # "BUY" or "SELL" from SignalEngine
    edge: float = 0.0
    estimated_prob: float = 0.0
    market_price: float = 0.0
    confidence: float | None = None
    ev: float = 0.0
    size_usd: float = 0.0
    reason: str = ""  # Human-readable reason
    risk_verdict: str = ""  # RiskVerdict value if rejected
    risk_reason: str = ""  # RiskVerdict reason detail
    llm_source: str = ""  # "groq", "gemini", "fallback", "heuristic"
    llm_state: str = "normal"  # "normal", "degraded", "heuristic_only"
    context_available: bool = False
    portfolio_cash: float = 0.0
    portfolio_positions_value: float = 0.0
    portfolio_total: float = 0.0
    total_exposure: float = 0.0
    created_at: str = ""  # ISO timestamp


class Account(BaseModel):
 balance_usd: float = 10_000.0
 total_exposure_usd: float = 0.0
 daily_pnl_usd: float = 0.0
 positions: list[Position] = []

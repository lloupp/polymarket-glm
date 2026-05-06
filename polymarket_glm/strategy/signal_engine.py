"""Signal engine — edge calculation, Kelly sizing, and signal generation.

The core strategy layer: takes a Market + OrderBook + estimated probability,
computes edge, sizes with fractional Kelly, and produces a Signal (or None).
"""
from __future__ import annotations

import enum
import logging
import math
from datetime import datetime

from pydantic import BaseModel, Field

from polymarket_glm.models import Market, OrderBook

logger = logging.getLogger(__name__)

class SignalType(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"
    NO_SIGNAL = "no_signal"


class Signal(BaseModel):
    market_id: str
    condition_id: str
    question: str
    signal_type: SignalType
    outcome: str = "Yes"
    edge: float = Field(ge=-1, le=1)
    estimated_prob: float = Field(ge=0, le=1)
    market_price: float = Field(ge=0, le=1)
    size_usd: float = Field(ge=0)
    kelly_raw: float = 0.0
    kelly_sized: float = 0.0
    target_price: float = 0.0
    ev: float = 0.0  # Expected value = |edge| * size_usd
    confidence: str = "unknown"  # LLM confidence: high, medium, low, unknown
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class SignalEngine:
    """Generate trading signals from edge + Kelly criterion.

    Parameters:
    min_edge: minimum edge to trigger a signal (default 0.05 = 5%)
    kelly_fraction: fraction of full Kelly to use (default 0.25 = quarter-Kelly)
    fair_estimate_bias: bias to add to estimated_prob (default 0)
    max_position_usd: cap on position size in USD
    min_price: minimum market price to consider a signal (default 0.05 = 5%)
        Markets priced below this on the trade side have no real liquidity
        — the order book is empty or paper-thin, so edge is illusory.
    min_liquidity_usd: minimum order book liquidity in USD (default 500)
        Skip signals where the order book has less than this much depth
        on the relevant side (bids for SELL, asks for BUY).
    """

    def __init__(
        self,
        min_edge: float = 0.05,
        kelly_fraction: float = 0.25,
        fair_estimate_bias: float = 0.0,
        max_position_usd: float = 500.0,
        min_price: float = 0.05,
        min_liquidity_usd: float = 500.0,
    ):
        self._min_edge = min_edge
        self._kelly_fraction = kelly_fraction
        self._bias = fair_estimate_bias
        self._max_position_usd = max_position_usd
        self._min_price = min_price
        self._min_liquidity_usd = min_liquidity_usd

    def calculate_edge(self, market_price: float, estimated_prob: float) -> float:
        """Compute edge = estimated_prob - market_price.

        Positive edge → market underpriced → buy.
        Negative edge → market overpriced → sell.
        """
        adjusted = estimated_prob + self._bias
        return adjusted - market_price

    def kelly_fraction(self, prob: float, price: float) -> float:
        """Compute fractional Kelly sizing.

        Full Kelly: f* = (p*b - q) / b, where b = price/(1-price), q = 1-p
        We use fractional Kelly (default 25%) for safety.
        """
        if price <= 0 or price >= 1:
            return 0.0
        if prob <= 0 or prob >= 1:
            return 0.0

        # Edge check
        edge = prob - price
        if edge <= 0:
            return 0.0

        # Full Kelly
        b = price / (1 - price)  # odds received
        q = 1 - prob
        full_kelly = (prob * b - q) / b

        if full_kelly <= 0:
            return 0.0

        # Apply fractional Kelly
        sized = full_kelly * self._kelly_fraction

        # Cap at max position
        return min(sized, 1.0)

    def _order_book_liquidity_usd(self, book: OrderBook, side: str) -> float:
        """Compute total USD liquidity on one side of the order book.

        Args:
            book: The order book.
            side: "asks" for buying YES, "bids" for selling YES (buying NO).

        Returns:
            Sum of (price * size) for all levels on that side.
        """
        levels = book.asks if side == "asks" else book.bids
        return sum(level.price * level.size for level in levels)

    def generate_signal(
        self,
        market: Market,
        book: OrderBook,
        estimated_prob: float,
        balance_usd: float = 10_000.0,
        open_market_ids: set[str] | None = None,
        confidence: str = "unknown",
        cash_available: float | None = None,
    ) -> Signal | None:
        """Generate a signal from market data and estimated probability.

        Returns None if edge is below threshold, price is too low,
        liquidity is insufficient, or position already open.
        """
        # Dedup: skip if we already have a position in this market
        if open_market_ids and market.market_id in open_market_ids:
            logger.debug("Skipping %s — position already open", market.market_id)
            return None

        # Use midpoint or best ask as reference price
        market_price = book.midpoint if book.midpoint else (
            market.outcome_prices[0] if market.outcome_prices else 0.5
        )

        edge = self.calculate_edge(market_price, estimated_prob)

        # Clamp edge to max 30% — extreme edges are usually LLM hallucination
        MAX_EDGE = 0.30
        if abs(edge) > MAX_EDGE:
            logger.info(
                "Edge %.4f clamped to %.2f for %s",
                edge, MAX_EDGE if edge > 0 else -MAX_EDGE, market.market_id,
            )
            edge = max(-MAX_EDGE, min(MAX_EDGE, edge))
            # Re-derive estimated_prob from clamped edge for correct Kelly sizing
            estimated_prob = market_price + edge

        # Check minimum edge
        if abs(edge) < self._min_edge:
            logger.debug("Edge %.4f below threshold %.4f for %s",
                         edge, self._min_edge, market.market_id)
            return None

        # ── Min price filter ───────────────────────────────────
        # If the side we'd BUY on is priced below min_price, there's no
        # real liquidity — the "edge" is illusory (no counterparty).
        # BUY_YES: we buy on the ask side → check yes_price
        # SELL_YES (BUY_NO): we buy NO on the ask → check no_price
        if edge > 0:
            # Buying YES — check that YES price >= min_price
            yes_price = market_price
            if yes_price < self._min_price:
                logger.info(
                    "🚫 Skipping BUY_YES %s — price %.4f below min %.4f (no real liquidity)",
                    market.market_id, yes_price, self._min_price,
                )
                return None
            trade_side = "asks"  # we buy from asks
        else:
            # Selling YES = buying NO — check that NO price >= min_price
            no_price = 1.0 - market_price
            if no_price < self._min_price:
                logger.info(
                    "🚫 Skipping BUY_NO %s — NO price %.4f below min %.4f (no real liquidity)",
                    market.market_id, no_price, self._min_price,
                )
                return None
            trade_side = "bids"  # selling YES = hitting bids

        # ── Min liquidity filter ───────────────────────────────
        # Check that the order book has enough depth on the side we'd trade
        book_liquidity = self._order_book_liquidity_usd(book, trade_side)
        if book_liquidity < self._min_liquidity_usd:
            logger.info(
                "🚫 Skipping %s — order book %s liquidity $%.2f below min $%.2f",
                market.market_id, trade_side, book_liquidity, self._min_liquidity_usd,
            )
            return None

        # Determine direction and outcome
        if edge > 0:
            signal_type = SignalType.BUY
            outcome = "Yes"
            kelly = self.kelly_fraction(estimated_prob, market_price)
        else:
            signal_type = SignalType.SELL
            outcome = "No"  # SELL YES = BUY NO
            # For sells, compute Kelly on the "No" side
            no_prob = 1 - estimated_prob
            no_price = 1 - market_price
            kelly = self.kelly_fraction(no_prob, no_price)

        # Size position
        kelly_raw = kelly
        size_usd = min(kelly * balance_usd, self._max_position_usd)

        # Cash-aware sizing: can't invest more than available cash
        # balance_usd represents total equity, but cash_available is the spendable portion
        if cash_available is not None and size_usd > cash_available:
            logger.info(
                "Cash-aware sizing: reduced $%.2f → $%.2f (cash_available=$%.2f)",
                size_usd, cash_available, cash_available,
            )
            size_usd = cash_available

        if size_usd <= 0:
            return None

        # Calculate EV = |edge| * size_usd
        ev = abs(edge) * size_usd

        signal = Signal(
            market_id=market.market_id,
            condition_id=market.condition_id,
            question=market.question,
            signal_type=signal_type,
            outcome=outcome,
            edge=edge,
            estimated_prob=estimated_prob,
            market_price=market_price,
            size_usd=size_usd,
            kelly_raw=kelly_raw,
            kelly_sized=kelly,
            target_price=estimated_prob,
            ev=ev,
            confidence=confidence,
        )
        logger.info("Signal: %s %s edge=%.4f ev=$%.2f confidence=%s size=$%.2f",
                     signal_type.value, market.market_id, edge, ev, confidence, size_usd)
        return signal

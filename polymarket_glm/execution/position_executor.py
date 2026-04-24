"""Position Executor — manages HOW positions are opened, monitored, and closed.

Inspired by Hummingbot V2's PositionExecutor:
- Opens positions from signals with TripleBarrierConfig
- Monitors positions against barriers (SL, TP, time limit, trailing stop)
- Tracks position lifecycle: open → monitoring → closed
- Integrates with ExchangeClient (paper or live) for actual fills
- Each position has its own barrier config for independent lifecycle management
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from polymarket_glm.execution.barriers import (
    CloseType,
    PositionBarrierResult,
    TripleBarrierConfig,
    check_barriers,
)
from polymarket_glm.execution.exchange import ExchangeClient, FillResult, OrderRequest
from polymarket_glm.models import Position, Side
from polymarket_glm.strategy.signal_engine import Signal

logger = logging.getLogger(__name__)


# ── Position Metrics (tracks per-position state for barrier checks) ──

@dataclass
class PositionMetrics:
    """Tracks the current state of a position for barrier checking."""
    entry_price: float
    current_price: float
    peak_price: float  # Best price seen since entry
    side: str = "BUY"
    outcome: str = "YES"
    entry_time: datetime = field(default_factory=datetime.utcnow)
    market_end_date: str | None = None
    trailing_activated: bool = False

    @property
    def return_pct(self) -> float:
        """Current unrealized return percentage."""
        if self.entry_price <= 0:
            return 0.0
        if self.side.upper() == "BUY" and self.outcome.upper() == "YES":
            return (self.current_price - self.entry_price) / self.entry_price
        elif self.side.upper() == "BUY" and self.outcome.upper() != "YES":
            entry_no = 1.0 - self.entry_price
            current_no = 1.0 - self.current_price
            if entry_no <= 0:
                return 0.0
            return (current_no - entry_no) / entry_no
        else:
            # SELL — simplified
            return (self.entry_price - self.current_price) / self.entry_price

    def update_price(self, new_price: float) -> None:
        """Update current price and peak."""
        self.current_price = new_price
        # Update peak: for BUY YES, peak is highest price
        if self.side.upper() == "BUY" and self.outcome.upper() == "YES":
            if new_price > self.peak_price:
                self.peak_price = new_price
        else:
            # For BUY NO or SELL, peak is the best price for our position
            if new_price < self.peak_price and self.side.upper() == "SELL":
                self.peak_price = new_price
            elif new_price > self.peak_price and self.side.upper() != "SELL":
                self.peak_price = new_price


# ── Position Record ─────────────────────────────────────────────────

@dataclass
class ManagedPosition:
    """A position with its barrier config and metrics."""
    position_id: str
    market_id: str
    outcome: str
    signal: Signal
    barrier_config: TripleBarrierConfig
    metrics: PositionMetrics
    opened_at: datetime = field(default_factory=datetime.utcnow)
    closed_at: datetime | None = None
    close_type: CloseType | None = None
    close_reason: str = ""
    fill: FillResult | None = None
    close_fill: FillResult | None = None

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    @property
    def hold_duration(self) -> timedelta:
        """Duration since position was opened."""
        end = self.closed_at or datetime.utcnow()
        start = self.metrics.entry_time
        return end - start


# ── Executor Config ──────────────────────────────────────────────────

@dataclass
class PositionExecutorConfig:
    """Configuration for PositionExecutor."""
    # Default barrier config (overridden per-signal)
    default_stop_loss_pct: float = 0.50
    default_take_profit_pct: float = 0.50
    default_time_limit_sec: int = 3600  # 1 hour

    # Position limits
    max_open_positions: int = 10
    max_single_position_usd: float = 500.0

    # Slippage model for paper trading
    slippage_bps: float = 10.0  # 0.1% default slippage

    # Whether to use async or sync execution
    use_async: bool = False


# ── Position Executor ────────────────────────────────────────────────

class PositionExecutor:
    """Manages position lifecycle with barrier-based exit rules.

    Each position gets its own TripleBarrierConfig. The executor:
    1. Opens positions from signals via ExchangeClient
    2. Monitors positions each tick against barriers
    3. Closes positions when barriers are triggered
    4. Tracks P&L and close reasons
    """

    def __init__(
        self,
        exchange: ExchangeClient | None = None,
        config: PositionExecutorConfig | None = None,
    ):
        self._exchange = exchange
        self._config = config or PositionExecutorConfig()
        self._positions: dict[str, ManagedPosition] = {}
        self._closed_positions: list[ManagedPosition] = []
        self._total_pnl: float = 0.0

    # ── Properties ──────────────────────────────────────────────

    @property
    def config(self) -> PositionExecutorConfig:
        return self._config

    @property
    def open_position_ids(self) -> list[str]:
        return [pid for pid, mp in self._positions.items() if mp.is_open]

    @property
    def open_positions(self) -> list[ManagedPosition]:
        return [mp for mp in self._positions.values() if mp.is_open]

    @property
    def closed_positions(self) -> list[ManagedPosition]:
        return list(self._closed_positions)

    @property
    def total_pnl(self) -> float:
        return self._total_pnl

    @property
    def n_open(self) -> int:
        return len(self.open_position_ids)

    @property
    def n_closed(self) -> int:
        return len(self._closed_positions)

    def get_position(self, position_id: str) -> ManagedPosition | None:
        """Get a managed position by ID."""
        return self._positions.get(position_id)

    # ── Position ID Generation ──────────────────────────────────

    @staticmethod
    def _make_position_id(market_id: str, outcome: str) -> str:
        """Generate a unique position ID: market_id::outcome::uuid."""
        short_id = uuid.uuid4().hex[:8]
        return f"{market_id}::{outcome}::{short_id}"

    # ── Default Barrier Config ──────────────────────────────────

    def _default_barrier_config(self) -> TripleBarrierConfig:
        """Create default barrier config from executor settings."""
        return TripleBarrierConfig(
            stop_loss_pct=self._config.default_stop_loss_pct,
            take_profit_pct=self._config.default_take_profit_pct,
            time_limit_sec=self._config.default_time_limit_sec,
        )

    # ── Open Position ───────────────────────────────────────────

    def open_position(
        self,
        signal: Signal,
        barrier_config: TripleBarrierConfig | None = None,
    ) -> str:
        """Open a position from a signal with barrier config.

        Returns position_id for tracking.
        """
        # Check max positions
        if self.n_open >= self._config.max_open_positions:
            logger.warning(
                "Cannot open position: max open positions (%d) reached",
                self._config.max_open_positions,
            )
            return ""

        # Use provided barrier config or default
        if barrier_config is None:
            barrier_config = self._default_barrier_config()

        # Create position ID
        position_id = self._make_position_id(signal.market_id, signal.outcome)

        # Create metrics tracker
        metrics = PositionMetrics(
            entry_price=signal.market_price,
            peak_price=signal.market_price,
            current_price=signal.market_price,
            side="BUY",
            outcome=signal.outcome,
            entry_time=datetime.utcnow(),
        )

        # Execute via exchange (if available)
        fill = None
        if self._exchange is not None:
            order = OrderRequest(
                market_id=signal.market_id,
                side=Side.BUY,
                outcome=signal.outcome,
                price=signal.market_price,
                size=signal.size_usd / signal.market_price if signal.market_price > 0 else 0,
            )
            # Sync call for simplicity
            if hasattr(self._exchange, 'submit_order_sync'):
                fill = self._exchange.submit_order_sync(order)
            else:
                logger.info("Exchange requires async — fill deferred")

        # Record managed position
        managed = ManagedPosition(
            position_id=position_id,
            market_id=signal.market_id,
            outcome=signal.outcome,
            signal=signal,
            barrier_config=barrier_config,
            metrics=metrics,
            fill=fill,
        )
        self._positions[position_id] = managed

        logger.info(
            "Position opened: %s %s@%.4f size=$%.2f barrier=[SL=%.0f%% TP=%.0f%% TL=%ds]",
            position_id,
            signal.signal_type.value,
            signal.market_price,
            signal.size_usd,
            (barrier_config.stop_loss_pct or 0) * 100,
            (barrier_config.take_profit_pct or 0) * 100,
            barrier_config.time_limit_sec or 0,
        )
        return position_id

    # ── Close Position ──────────────────────────────────────────

    def close_position(self, position_id: str, reason: str = "manual") -> bool:
        """Close a position by ID.

        Returns True if successfully closed.
        """
        mp = self._positions.get(position_id)
        if mp is None or not mp.is_open:
            logger.warning("Cannot close %s: not found or already closed", position_id)
            return False

        # Determine close type from reason
        close_type = CloseType.EARLY_STOP  # Default for manual/strategy stops
        reason_lower = reason.lower()
        if "stop_loss" in reason_lower or "stop loss" in reason_lower:
            close_type = CloseType.STOP_LOSS
        elif "take_profit" in reason_lower or "take profit" in reason_lower:
            close_type = CloseType.TAKE_PROFIT
        elif "time_limit" in reason_lower or "time limit" in reason_lower:
            close_type = CloseType.TIME_LIMIT
        elif "trailing" in reason_lower:
            close_type = CloseType.TRAILING_STOP
        elif "resolved" in reason_lower:
            close_type = CloseType.RESOLVED
        elif "expired" in reason_lower:
            close_type = CloseType.EXPIRED

        # Execute close via exchange (if available)
        close_fill = None
        if self._exchange is not None and mp.fill is not None:
            close_side = Side.SELL
            order = OrderRequest(
                market_id=mp.market_id,
                side=close_side,
                outcome=mp.outcome,
                price=mp.metrics.current_price,
                size=mp.fill.size,
                close_reason=reason,
            )
            if hasattr(self._exchange, 'submit_order_sync'):
                close_fill = self._exchange.submit_order_sync(order)

        # Calculate P&L
        if mp.metrics.entry_price > 0 and mp.metrics.current_price > 0:
            pnl = (mp.metrics.current_price - mp.metrics.entry_price)
            if mp.fill and mp.fill.size > 0:
                pnl *= mp.fill.size
            self._total_pnl += pnl

        # Update managed position
        mp.closed_at = datetime.utcnow()
        mp.close_type = close_type
        mp.close_reason = reason
        mp.close_fill = close_fill

        # Move to closed list
        self._closed_positions.append(mp)

        logger.info(
            "Position closed: %s reason=%s close_type=%s pnl=%.4f hold=%s",
            position_id,
            reason,
            close_type.value,
            (mp.metrics.current_price - mp.metrics.entry_price),
            mp.hold_duration,
        )
        return True

    # ── Barrier Checking ────────────────────────────────────────

    def check_barriers(self, current_prices: dict[str, float]) -> list[str]:
        """Check all open positions against their barrier configs.

        Args:
            current_prices: dict mapping market_id → current price

        Returns:
            List of position_ids that were closed by barrier triggers.
        """
        closed_ids: list[str] = []

        for position_id, mp in list(self._positions.items()):
            if not mp.is_open:
                continue

            # Get current price for this market
            price = current_prices.get(mp.market_id)
            if price is None:
                continue

            # Update metrics
            mp.metrics.update_price(price)

            # Check barriers using the real check_barriers function
            result = check_barriers(
                entry_price=mp.metrics.entry_price,
                current_price=mp.metrics.current_price,
                side=mp.metrics.side,
                outcome=mp.metrics.outcome,
                config=mp.barrier_config,
                market_end_date=mp.metrics.market_end_date,
                position_opened_at=mp.metrics.entry_time,
                peak_price=mp.metrics.peak_price,
                trailing_activated=mp.metrics.trailing_activated,
            )

            # Update trailing activation state
            mp.metrics.trailing_activated = result.trailing_activated

            if result.should_close:
                if self.close_position(position_id, reason=result.close_type.value):
                    closed_ids.append(position_id)

        return closed_ids

    # ── Stats ───────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return executor statistics."""
        close_type_counts: dict[str, int] = {}
        for mp in self._closed_positions:
            key = mp.close_type.value if mp.close_type else "unknown"
            close_type_counts[key] = close_type_counts.get(key, 0) + 1

        return {
            "n_open": self.n_open,
            "n_closed": self.n_closed,
            "total_pnl": round(self._total_pnl, 4),
            "close_type_counts": close_type_counts,
            "max_open_positions": self._config.max_open_positions,
        }

"""Signal Controller — decides WHAT to trade.

Inspired by Hummingbot V2's ControllerBase:
- Scans markets for opportunities
- Generates signals via SignalEngine
- Delegates position lifecycle to PositionExecutor
- Tracks signal statistics and market coverage

The controller only decides "WHAT" — the PositionExecutor handles "HOW"
(stop-loss, take-profit, trailing stop, time limit).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from polymarket_glm.execution.barriers import TripleBarrierConfig
from polymarket_glm.models import Market, OrderBook
from polymarket_glm.strategy.signal_engine import Signal, SignalEngine, SignalType

logger = logging.getLogger(__name__)


# ── Protocol for executors that the controller delegates to ─────────

@runtime_checkable
class PositionExecutorProtocol(Protocol):
    """Protocol for position executors that the controller delegates to."""

    def open_position(
        self,
        signal: Signal,
        barrier_config: TripleBarrierConfig | None = None,
    ) -> str:
        """Open a position from a signal. Returns position_id."""
        ...

    def close_position(self, position_id: str, reason: str = "manual") -> bool:
        """Close a position by ID. Returns True if successful."""
        ...

    def check_barriers(self, current_prices: dict[str, float]) -> list[str]:
        """Check all open positions against barrier configs.
        Returns list of position_ids that were closed."""
        ...

    @property
    def open_position_ids(self) -> list[str]:
        """List of currently open position IDs."""
        ...


# ── Controller Config ───────────────────────────────────────────────

@dataclass
class ControllerConfig:
    """Configuration for SignalController."""
    # Signal generation
    min_edge: float = 0.05
    kelly_fraction: float = 0.25
    max_position_usd: float = 500.0
    max_open_positions: int = 10

    # Barrier defaults (applied to new positions)
    default_stop_loss_pct: float = 0.50
    default_take_profit_pct: float = 0.50
    default_time_limit_hours: float = 48.0
    default_trailing_stop_activation: float = 0.15
    default_trailing_stop_trail: float = 0.08

    # Market filtering
    min_volume_usd: float = 10_000.0
    min_liquidity_usd: float = 500.0
    max_spread_bps: float = 500.0

    # Cooldown per market (seconds)
    market_cooldown_sec: float = 300.0  # 5 min


# ── Controller State ────────────────────────────────────────────────

@dataclass
class ControllerState:
    """Mutable state of the SignalController."""
    signals_generated: int = 0
    signals_executed: int = 0
    signals_skipped_cooldown: int = 0
    signals_skipped_max_pos: int = 0
    signals_skipped_dedup: int = 0
    markets_scanned: int = 0
    scan_count: int = 0
    last_scan_time: float = 0.0
    last_signal_time: dict[str, float] = field(default_factory=dict)
    errors: int = 0


# ── Signal Controller ───────────────────────────────────────────────

class SignalController:
    """Decides WHAT to trade — scan, estimate, signal, delegate.

    Flow per iteration:
    1. Receive scanned markets + estimated probabilities
    2. For each market, generate signal via SignalEngine
    3. Apply filters (cooldown, dedup, max positions)
    4. Delegate to PositionExecutor with barrier config
    5. Check existing positions against barriers
    """

    def __init__(
        self,
        config: ControllerConfig | None = None,
        signal_engine: SignalEngine | None = None,
        executor: PositionExecutorProtocol | None = None,
    ):
        self._config = config or ControllerConfig()
        self._signal_engine = signal_engine or SignalEngine(
            min_edge=self._config.min_edge,
            kelly_fraction=self._config.kelly_fraction,
            max_position_usd=self._config.max_position_usd,
        )
        self._executor = executor
        self._state = ControllerState()

    # ── Properties ──────────────────────────────────────────────

    @property
    def config(self) -> ControllerConfig:
        return self._config

    @property
    def state(self) -> ControllerState:
        return self._state

    @property
    def executor(self) -> PositionExecutorProtocol | None:
        return self._executor

    @executor.setter
    def executor(self, value: PositionExecutorProtocol) -> None:
        self._executor = value

    # ── Barrier Config Factory ──────────────────────────────────

    def _default_barrier_config(self) -> TripleBarrierConfig:
        """Create default barrier config from controller settings."""
        return TripleBarrierConfig(
            stop_loss_pct=self._config.default_stop_loss_pct,
            take_profit_pct=self._config.default_take_profit_pct,
            time_limit_sec=int(self._config.default_time_limit_hours * 3600),
        )

    def _default_barrier_with_trailing(self) -> TripleBarrierConfig:
        """Create barrier config with trailing stop enabled."""
        from polymarket_glm.execution.barriers import TrailingStop

        return TripleBarrierConfig(
            stop_loss_pct=self._config.default_stop_loss_pct,
            take_profit_pct=self._config.default_take_profit_pct,
            time_limit_sec=int(self._config.default_time_limit_hours * 3600),
            trailing_stop=TrailingStop(
                activation_price_pct=self._config.default_trailing_stop_activation,
                trailing_delta_pct=self._config.default_trailing_stop_trail,
            ),
        )

    # ── Market Filtering ────────────────────────────────────────

    def filter_market(self, market: Market, book: OrderBook | None = None) -> bool:
        """Check if a market passes basic filters.

        Returns True if the market is tradeable.
        """
        # Volume filter
        if market.volume < self._config.min_volume_usd:
            logger.debug("Market %s filtered: volume $%.0f < $%.0f",
                         market.market_id, market.volume, self._config.min_volume_usd)
            return False

        # Spread filter
        if book and book.spread_bps is not None:
            if book.spread_bps > self._config.max_spread_bps:
                logger.debug("Market %s filtered: spread %.0f bps > %.0f bps",
                             market.market_id, book.spread_bps, self._config.max_spread_bps)
                return False

        # Must be active
        if not market.active or market.closed:
            logger.debug("Market %s filtered: inactive/closed", market.market_id)
            return False

        return True

    # ── Cooldown Check ──────────────────────────────────────────

    def _is_on_cooldown(self, market_id: str) -> bool:
        """Check if a market is still in cooldown after last signal."""
        last_time = self._state.last_signal_time.get(market_id, 0.0)
        if last_time <= 0:
            return False
        elapsed = time.monotonic() - last_time
        return elapsed < self._config.market_cooldown_sec

    # ── Signal Processing ───────────────────────────────────────

    def process_market(
        self,
        market: Market,
        book: OrderBook,
        estimated_prob: float,
        balance_usd: float = 10_000.0,
    ) -> Signal | None:
        """Process a single market: filter → signal → dedup → cooldown.

        Returns the signal if one was generated, None otherwise.
        Does NOT execute — caller or executor handles that.
        """
        # 1. Filter
        if not self.filter_market(market, book):
            return None

        # 2. Get open positions for dedup
        open_market_ids: set[str] = set()
        if self._executor is not None:
            # Get open position market IDs from executor
            for pid in self._executor.open_position_ids:
                open_market_ids.add(pid.split("::")[0] if "::" in pid else pid)

        # 3. Generate signal
        signal = self._signal_engine.generate_signal(
            market=market,
            book=book,
            estimated_prob=estimated_prob,
            balance_usd=balance_usd,
            open_market_ids=open_market_ids,
        )

        if signal is None:
            return None

        self._state.signals_generated += 1

        # 4. Cooldown check
        if self._is_on_cooldown(market.market_id):
            self._state.signals_skipped_cooldown += 1
            logger.debug("Signal for %s skipped — cooldown", market.market_id)
            return None

        # 5. Max positions check
        if self._executor is not None:
            n_open = len(self._executor.open_position_ids)
            if n_open >= self._config.max_open_positions:
                self._state.signals_skipped_max_pos += 1
                logger.debug("Signal for %s skipped — max positions (%d/%d)",
                             market.market_id, n_open, self._config.max_open_positions)
                return None

        # 6. Record signal time for cooldown
        self._state.last_signal_time[market.market_id] = time.monotonic()

        return signal

    def execute_signal(
        self,
        signal: Signal,
        barrier_config: TripleBarrierConfig | None = None,
    ) -> str | None:
        """Delegate a signal to the executor with barrier config.

        Returns position_id if executed, None if executor unavailable.
        """
        if self._executor is None:
            logger.warning("No executor configured — cannot execute signal")
            return None

        if barrier_config is None:
            barrier_config = self._default_barrier_config()

        position_id = self._executor.open_position(signal, barrier_config)
        self._state.signals_executed += 1

        logger.info(
            "Signal executed: %s %s → position %s (edge=%.4f, size=$%.2f)",
            signal.signal_type.value,
            signal.market_id,
            position_id,
            signal.edge,
            signal.size_usd,
        )
        return position_id

    # ── Batch Processing ────────────────────────────────────────

    def process_markets(
        self,
        markets: list[tuple[Market, OrderBook, float]],
        balance_usd: float = 10_000.0,
    ) -> list[tuple[Signal, str]]:
        """Process multiple markets and execute signals.

        Args:
            markets: list of (Market, OrderBook, estimated_prob) tuples
            balance_usd: current balance for Kelly sizing

        Returns:
            list of (Signal, position_id) tuples for executed signals
        """
        self._state.scan_count += 1
        self._state.markets_scanned += len(markets)
        self._state.last_scan_time = time.monotonic()

        executed: list[tuple[Signal, str]] = []

        for market, book, est_prob in markets:
            try:
                signal = self.process_market(market, book, est_prob, balance_usd)
                if signal is not None:
                    pid = self.execute_signal(signal)
                    if pid is not None:
                        executed.append((signal, pid))
            except Exception as exc:
                self._state.errors += 1
                logger.warning("Error processing market %s: %s", market.market_id, exc)

        return executed

    def check_all_barriers(
        self,
        current_prices: dict[str, float],
    ) -> list[str]:
        """Check all open positions against their barrier configs.

        Returns list of closed position_ids.
        """
        if self._executor is None:
            return []

        closed_ids = self._executor.check_barriers(current_prices)
        if closed_ids:
            logger.info("Barriers triggered: %d positions closed", len(closed_ids))
        return closed_ids

    # ── Stats ───────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return controller statistics."""
        return {
            "scan_count": self._state.scan_count,
            "markets_scanned": self._state.markets_scanned,
            "signals_generated": self._state.signals_generated,
            "signals_executed": self._state.signals_executed,
            "signals_skipped_cooldown": self._state.signals_skipped_cooldown,
            "signals_skipped_max_pos": self._state.signals_skipped_max_pos,
            "signals_skipped_dedup": self._state.signals_skipped_dedup,
            "errors": self._state.errors,
            "last_scan_time": self._state.last_scan_time,
            "active_cooldowns": len(self._state.last_signal_time),
        }

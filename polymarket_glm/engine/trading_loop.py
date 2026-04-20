"""Trading loop — async main loop that ties scan→estimate→signal→risk→exec together.

This is the autonomous "brain stem" of the framework: it repeatedly scans markets,
fetches prices, estimates probabilities, generates signals, risk-checks them,
and executes fills. Designed to run 24/7 as a service.
"""
from __future__ import annotations

import asyncio
import enum
import logging
import time
from typing import Callable

from polymarket_glm.config import Settings
from polymarket_glm.ingestion.market_fetcher import MarketFetcher, MarketFilter
from polymarket_glm.ingestion.price_feed import PriceFeed
from polymarket_glm.models import Market, OrderBook
from polymarket_glm.strategy.signal_engine import SignalEngine

logger = logging.getLogger(__name__)


class LoopState(str, enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


class TradingLoop:
    """Autonomous trading loop.

    Flow per iteration:
    1. Scan markets via MarketFetcher
    2. For each market, fetch order book via PriceFeed
    3. Estimate probability via estimator_fn
    4. Generate signal via SignalEngine
    5. Process signal via Engine (risk check + execution)
    6. Sleep until next iteration

    Parameters:
        scan_interval_sec: seconds between full market scans
        estimator_fn: callable(market, book) -> float (estimated probability)
        market_fetcher: optional pre-configured MarketFetcher
        price_feed: optional pre-configured PriceFeed
        engine: optional pre-configured Engine instance
        market_filter: optional MarketFilter for scan
        signal_engine: optional pre-configured SignalEngine
        max_iterations: stop after N iterations (0 = infinite)
        min_edge: minimum edge to generate signal (default 0.05)
        kelly_fraction: fractional Kelly for sizing (default 0.25)
    """

    def __init__(
        self,
        scan_interval_sec: float = 60.0,
        estimator_fn: Callable[[Market, OrderBook], float] | None = None,
        market_fetcher: MarketFetcher | None = None,
        price_feed: PriceFeed | None = None,
        engine=None,
        market_filter: MarketFilter | None = None,
        signal_engine: SignalEngine | None = None,
        max_iterations: int = 0,
        min_edge: float = 0.05,
        kelly_fraction: float = 0.25,
    ):
        self._scan_interval = scan_interval_sec
        self._estimator_fn = estimator_fn or (lambda m, b: 0.5)
        self._fetcher = market_fetcher or MarketFetcher()
        self._price_feed = price_feed or PriceFeed()
        self._engine = engine
        self._market_filter = market_filter
        self._signal_engine = signal_engine or SignalEngine(
            min_edge=min_edge,
            kelly_fraction=kelly_fraction,
        )
        self._max_iterations = max_iterations

        # State
        self._state = LoopState.IDLE
        self._stop_event = asyncio.Event()
        self._iteration_count = 0
        self._signals_generated = 0
        self._trades_filled = 0
        self._error_count = 0
        self._last_error: str | None = None
        self._last_scan_time: float = 0.0

    # ── Properties ─────────────────────────────────────────────

    @property
    def state(self) -> LoopState:
        return self._state

    @property
    def iteration_count(self) -> int:
        return self._iteration_count

    @property
    def signals_generated(self) -> int:
        return self._signals_generated

    @property
    def trades_filled(self) -> int:
        return self._trades_filled

    @property
    def error_count(self) -> int:
        return self._error_count

    @property
    def last_error(self) -> str | None:
        return self._last_error

    # ── Stats ──────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return loop statistics."""
        return {
            "state": self._state.value,
            "iterations": self._iteration_count,
            "signals": self._signals_generated,
            "fills": self._trades_filled,
            "errors": self._error_count,
            "last_error": self._last_error,
            "last_scan": self._last_scan_time,
            "scan_interval_sec": self._scan_interval,
        }

    # ── Control ────────────────────────────────────────────────

    def stop(self) -> None:
        """Request the loop to stop gracefully."""
        logger.info("Trading loop stop requested")
        self._stop_event.set()

    # ── Main Loop ──────────────────────────────────────────────

    async def run(self) -> None:
        """Run the trading loop until stopped or max_iterations reached."""
        self._state = LoopState.RUNNING
        self._stop_event.clear()
        logger.info(
            "Trading loop started (interval=%.1fs, max_iter=%d)",
            self._scan_interval,
            self._max_iterations,
        )

        try:
            while not self._stop_event.is_set():
                # Check max iterations
                if self._max_iterations > 0 and self._iteration_count >= self._max_iterations:
                    logger.info("Max iterations (%d) reached", self._max_iterations)
                    break

                await self._run_iteration()
                self._iteration_count += 1

                # Wait for next iteration (or stop signal)
                if not self._stop_event.is_set():
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=self._scan_interval,
                        )
                        # stop_event was set → exit
                        break
                    except asyncio.TimeoutError:
                        pass  # normal → next iteration

        except Exception as exc:
            self._state = LoopState.ERROR
            self._last_error = str(exc)
            logger.exception("Trading loop crashed: %s", exc)
            raise
        finally:
            self._state = LoopState.STOPPED
            logger.info(
                "Trading loop stopped (iterations=%d, signals=%d, fills=%d, errors=%d)",
                self._iteration_count,
                self._signals_generated,
                self._trades_filled,
                self._error_count,
            )

    async def _run_iteration(self) -> None:
        """Execute one full iteration: scan → estimate → signal → execute."""
        self._last_scan_time = time.time()

        # 1. Scan markets
        try:
            markets = await self._fetcher.fetch_markets(self._market_filter)
            logger.debug("Scan found %d markets", len(markets))
        except Exception as exc:
            self._error_count += 1
            self._last_error = str(exc)
            logger.warning("Market scan failed: %s", exc)
            return

        if not markets:
            logger.debug("No markets found in scan")
            return

        # 2. For each market: fetch book → estimate → signal → execute
        for market in markets:
            try:
                await self._process_market(market)
            except Exception as exc:
                self._error_count += 1
                self._last_error = str(exc)
                logger.warning("Error processing market %s: %s", market.market_id, exc)

    async def _process_market(self, market: Market) -> None:
        """Process a single market: fetch book, estimate prob, generate signal, execute."""
        # 2a. Fetch order book
        book = await self._price_feed.fetch_book(market.market_id)
        if book is None or not book.bids or not book.asks:
            logger.debug("No order book for %s — skipping", market.market_id)
            return

        # 2b. Estimate probability
        estimated_prob = self._estimator_fn(market, book)
        if estimated_prob is None or not (0 < estimated_prob < 1):
            logger.debug("Invalid estimate for %s: %s", market.market_id, estimated_prob)
            return

        # 2c. Generate signal
        signal = self._signal_engine.generate_signal(
            market=market,
            book=book,
            estimated_prob=estimated_prob,
        )

        if signal is None:
            return  # no edge — skip

        self._signals_generated += 1
        logger.info(
            "Signal: %s %s edge=%.4f size=$%.2f",
            signal.signal_type.value,
            market.market_id,
            signal.edge,
            signal.size_usd,
        )

        # 2d. Execute via Engine (if available)
        if self._engine is not None:
            result = self._engine.process_signal_sync(signal)
            if result and result.filled:
                self._trades_filled += 1

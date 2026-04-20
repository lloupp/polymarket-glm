"""Tests for trading loop."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from polymarket_glm.engine.trading_loop import TradingLoop, LoopState
from polymarket_glm.models import Market, OrderBook, OrderBookLevel, Side
from polymarket_glm.strategy.signal_engine import Signal, SignalType


def _make_market(mid="m1", question="Will X happen?"):
    return Market(
        condition_id="cond1",
        market_id=mid,
        question=question,
        outcomes=["Yes", "No"],
        outcome_prices=[0.6, 0.4],
        tokens=["t1", "t2"],
        volume=5000,
    )


def _make_book(mid="m1", bid=0.55, ask=0.65):
    return OrderBook(
        market_id=mid,
        bids=[OrderBookLevel(price=bid, size=100)],
        asks=[OrderBookLevel(price=ask, size=100)],
    )


def test_loop_state_values():
    assert LoopState.IDLE.value == "idle"
    assert LoopState.RUNNING.value == "running"
    assert LoopState.STOPPED.value == "stopped"
    assert LoopState.ERROR.value == "error"


def test_loop_initial_state():
    loop = TradingLoop(
        scan_interval_sec=60,
        estimator_fn=lambda m, b: 0.7,
    )
    assert loop.state == LoopState.IDLE
    assert loop.iteration_count == 0
    assert loop.last_error is None


def test_loop_sets_running():
    loop = TradingLoop(
        scan_interval_sec=0.1,
        estimator_fn=lambda m, b: 0.7,
    )
    # State should stay IDLE until run() is called
    assert loop.state == LoopState.IDLE


@pytest.mark.asyncio
async def test_single_iteration():
    """Test a single loop iteration with mocked components."""
    market = _make_market()
    book = _make_book()

    mock_fetcher = AsyncMock()
    mock_fetcher.fetch_markets.return_value = [market]

    mock_price_feed = MagicMock()
    mock_price_feed.fetch_book = AsyncMock(return_value=book)

    mock_engine = MagicMock()
    mock_engine.process_signal_sync = MagicMock(return_value=MagicMock(filled=True))

    loop = TradingLoop(
        scan_interval_sec=0.05,
        estimator_fn=lambda m, b: 0.8,  # edge = 0.8 - 0.6 = 0.2 > min_edge
        market_fetcher=mock_fetcher,
        price_feed=mock_price_feed,
        engine=mock_engine,
        max_iterations=1,  # stop after 1 iteration
    )

    await loop.run()

    assert loop.iteration_count == 1
    assert loop.state == LoopState.STOPPED


@pytest.mark.asyncio
async def test_loop_stops_on_max_iterations():
    """Loop should stop after max_iterations."""
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch_markets.return_value = []

    loop = TradingLoop(
        scan_interval_sec=0.01,
        estimator_fn=lambda m, b: 0.5,
        market_fetcher=mock_fetcher,
        max_iterations=3,
    )

    await loop.run()
    assert loop.iteration_count == 3
    assert loop.state == LoopState.STOPPED


@pytest.mark.asyncio
async def test_loop_handles_fetch_error():
    """Loop should continue on fetch errors, not crash."""
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch_markets.side_effect = Exception("API down")

    loop = TradingLoop(
        scan_interval_sec=0.01,
        estimator_fn=lambda m, b: 0.5,
        market_fetcher=mock_fetcher,
        max_iterations=2,
    )

    await loop.run()
    assert loop.iteration_count == 2
    assert loop.error_count >= 2
    assert loop.state == LoopState.STOPPED


@pytest.mark.asyncio
async def test_loop_stop():
    """Manual stop should set state to STOPPED."""
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch_markets.return_value = []

    loop = TradingLoop(
        scan_interval_sec=0.5,
        estimator_fn=lambda m, b: 0.5,
        market_fetcher=mock_fetcher,
    )

    # Start loop in background
    task = asyncio.create_task(loop.run())

    # Wait a bit then stop
    await asyncio.sleep(0.1)
    loop.stop()
    await task

    assert loop.state == LoopState.STOPPED


@pytest.mark.asyncio
async def test_loop_with_no_markets():
    """Loop should handle empty market list gracefully."""
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch_markets.return_value = []

    loop = TradingLoop(
        scan_interval_sec=0.01,
        estimator_fn=lambda m, b: 0.5,
        market_fetcher=mock_fetcher,
        max_iterations=2,
    )

    await loop.run()
    assert loop.iteration_count == 2
    assert loop.error_count == 0


@pytest.mark.asyncio
async def test_loop_with_no_edge():
    """When estimator returns price close to market, no signal generated."""
    market = _make_market()
    book = _make_book()

    mock_fetcher = AsyncMock()
    mock_fetcher.fetch_markets.return_value = [market]

    mock_price_feed = MagicMock()
    mock_price_feed.fetch_book = AsyncMock(return_value=book)

    mock_engine = MagicMock()

    loop = TradingLoop(
        scan_interval_sec=0.01,
        estimator_fn=lambda m, b: 0.6,  # same as market price → no edge
        market_fetcher=mock_fetcher,
        price_feed=mock_price_feed,
        engine=mock_engine,
        max_iterations=1,
    )

    await loop.run()
    # No signal should have been processed
    mock_engine.process_signal_sync.assert_not_called()
    assert loop.signals_generated == 0


@pytest.mark.asyncio
async def test_loop_with_edge():
    """When estimator finds edge, signal should be processed."""
    market = _make_market()
    book = _make_book()

    mock_fetcher = AsyncMock()
    mock_fetcher.fetch_markets.return_value = [market]

    mock_price_feed = MagicMock()
    mock_price_feed.fetch_book = AsyncMock(return_value=book)

    mock_engine = MagicMock()
    mock_engine.process_signal_sync.return_value = MagicMock(filled=True)

    loop = TradingLoop(
        scan_interval_sec=0.01,
        estimator_fn=lambda m, b: 0.85,  # big edge = 0.85 - 0.6 = 0.25
        market_fetcher=mock_fetcher,
        price_feed=mock_price_feed,
        engine=mock_engine,
        max_iterations=1,
    )

    await loop.run()
    assert loop.signals_generated >= 1
    mock_engine.process_signal_sync.assert_called()


def test_loop_stats():
    """Stats should reflect loop activity."""
    loop = TradingLoop(
        scan_interval_sec=1.0,
        estimator_fn=lambda m, b: 0.5,
    )
    loop._iteration_count = 5
    loop._signals_generated = 3
    loop._trades_filled = 2
    loop._error_count = 1

    stats = loop.stats()
    assert stats["iterations"] == 5
    assert stats["signals"] == 3
    assert stats["fills"] == 2
    assert stats["errors"] == 1

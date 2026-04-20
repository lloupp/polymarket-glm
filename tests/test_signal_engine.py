"""Tests for signal engine."""
import math
import pytest
from polymarket_glm.strategy.signal_engine import SignalEngine, Signal, SignalType
from polymarket_glm.models import Market, OrderBook, OrderBookLevel


def _make_market(prices=(0.60, 0.40), vol=50000) -> Market:
    return Market(
        condition_id="0xabc", market_id="m1",
        question="Will X happen?", outcomes=["Yes", "No"],
        outcome_prices=list(prices), tokens=["t1", "t2"],
        volume=vol,
    )


def _make_book(bid=0.55, ask=0.60) -> OrderBook:
    return OrderBook(
        market_id="m1",
        bids=[OrderBookLevel(price=bid, size=500)],
        asks=[OrderBookLevel(price=ask, size=300)],
    )


def test_signal_type_values():
    assert SignalType.BUY.value == "buy"
    assert SignalType.SELL.value == "sell"
    assert SignalType.NO_SIGNAL.value == "no_signal"


def test_edge_positive():
    engine = SignalEngine(fair_estimate_bias=0.0)
    # market price = 0.60, our estimate = 0.70
    edge = engine.calculate_edge(market_price=0.60, estimated_prob=0.70)
    assert edge > 0
    assert edge == pytest.approx(0.10)


def test_edge_negative():
    engine = SignalEngine()
    edge = engine.calculate_edge(market_price=0.70, estimated_prob=0.55)
    assert edge < 0


def test_edge_zero():
    engine = SignalEngine()
    edge = engine.calculate_edge(market_price=0.50, estimated_prob=0.50)
    assert edge == pytest.approx(0.0)


def test_kelly_fraction_basic():
    engine = SignalEngine(kelly_fraction=0.25)
    # p=0.70, price=0.60 → b = 0.60/0.40 = 1.5
    k = engine.kelly_fraction(prob=0.70, price=0.60)
    assert k > 0
    assert k <= 0.25  # quarter-kelly cap


def test_kelly_no_edge():
    engine = SignalEngine(kelly_fraction=0.25)
    k = engine.kelly_fraction(prob=0.50, price=0.50)
    assert k == 0.0


def test_kelly_negative_edge():
    engine = SignalEngine(kelly_fraction=0.25)
    k = engine.kelly_fraction(prob=0.40, price=0.60)
    assert k == 0.0


def test_generate_signal_buy():
    engine = SignalEngine(min_edge=0.05)
    m = _make_market(prices=(0.60, 0.40))
    book = _make_book(bid=0.55, ask=0.60)
    sig = engine.generate_signal(
        market=m, book=book,
        estimated_prob=0.75,  # edge = 0.15
    )
    assert sig is not None
    assert sig.signal_type == SignalType.BUY
    assert sig.edge > 0
    assert sig.size_usd > 0


def test_generate_signal_no_edge():
    engine = SignalEngine(min_edge=0.05)
    m = _make_market(prices=(0.60, 0.40))
    book = _make_book(bid=0.55, ask=0.60)
    sig = engine.generate_signal(
        market=m, book=book,
        estimated_prob=0.62,  # edge = 0.02 < min_edge
    )
    assert sig is None


def test_generate_signal_sell():
    engine = SignalEngine(min_edge=0.05)
    m = _make_market(prices=(0.60, 0.40))
    book = _make_book(bid=0.55, ask=0.60)
    # Estimate much lower than market → sell signal
    sig = engine.generate_signal(
        market=m, book=book,
        estimated_prob=0.35,  # edge = -0.25 → sell
    )
    assert sig is not None
    assert sig.signal_type == SignalType.SELL

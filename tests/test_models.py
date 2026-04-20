"""Tests for data models."""
from polymarket_glm.models import Market, OrderBookLevel, OrderBook, Trade, Side


def test_market_from_gamma_fair():
    """When yes + no = 1.0, spread is 0 (perfectly fair market)."""
    m = Market(
        condition_id="0xabc",
        market_id="123",
        question="Will X happen?",
        outcomes=["Yes", "No"],
        outcome_prices=[0.65, 0.35],
        tokens=["tok1", "tok2"],
        active=True,
        closed=False,
        volume=50000.0,
    )
    assert m.spread_bps == 0.0


def test_market_from_gamma_with_spread():
    """When yes + no != 1.0, there's a spread (liquidity gap)."""
    m = Market(
        condition_id="0xabc",
        market_id="123",
        question="Will X happen?",
        outcomes=["Yes", "No"],
        outcome_prices=[0.60, 0.35],  # gap = 0.05
        tokens=["tok1", "tok2"],
        active=True,
        closed=False,
        volume=50000.0,
    )
    assert m.spread_bps > 0


def test_market_no_prices():
    m = Market(
        condition_id="0xabc",
        market_id="123",
        question="Will X happen?",
        outcomes=["Yes", "No"],
        outcome_prices=[],
        tokens=["tok1", "tok2"],
    )
    assert m.spread_bps == 0.0


def test_orderbook_level_cost():
    level = OrderBookLevel(price=0.55, size=100.0)
    assert level.cost == pytest.approx(55.0)


def test_orderbook_best():
    from polymarket_glm.models import OrderBookLevel, OrderBook
    book = OrderBook(
        market_id="m1",
        bids=[OrderBookLevel(price=0.50, size=200), OrderBookLevel(price=0.48, size=300)],
        asks=[OrderBookLevel(price=0.55, size=100), OrderBookLevel(price=0.60, size=150)],
        fee_rate_bps=100,
    )
    assert book.best_bid.price == 0.50
    assert book.best_ask.price == 0.55
    assert book.midpoint == pytest.approx(0.525)


def test_orderbook_empty():
    book = OrderBook(market_id="m1")
    assert book.best_bid is None
    assert book.midpoint is None
    assert book.spread_bps is None


def test_trade_side():
    assert Side.BUY.value == "buy"
    assert Side.SELL.value == "sell"


def test_trade_total_cost():
    t = Trade(
        market_id="123",
        side=Side.BUY,
        outcome="yes",
        price=0.60,
        size=50.0,
        fee=0.15,
    )
    assert t.total_cost == pytest.approx(30.15)


import pytest

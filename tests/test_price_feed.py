"""Tests for price feed."""
import pytest
from polymarket_glm.ingestion.price_feed import PriceFeed, PriceSnapshot


def test_price_snapshot():
    s = PriceSnapshot(market_id="m1", outcome="Yes", price=0.65, volume=1000.0)
    assert s.price == 0.65
    assert s.outcome == "Yes"


def test_price_feed_initial_state():
    pf = PriceFeed()
    assert pf.last_snapshot("m1") is None
    assert pf.is_connected is False


def test_price_feed_update():
    pf = PriceFeed()
    s = PriceSnapshot(market_id="m1", outcome="Yes", price=0.70, volume=500.0)
    pf.update(s)
    got = pf.last_snapshot("m1")
    assert got is not None
    assert got.price == 0.70


def test_price_feed_overwrite():
    pf = PriceFeed()
    pf.update(PriceSnapshot(market_id="m1", outcome="Yes", price=0.60, volume=100.0))
    pf.update(PriceSnapshot(market_id="m1", outcome="Yes", price=0.65, volume=200.0))
    assert pf.last_snapshot("m1").price == 0.65


def test_price_feed_multiple_markets():
    pf = PriceFeed()
    pf.update(PriceSnapshot(market_id="m1", outcome="Yes", price=0.5, volume=100.0))
    pf.update(PriceSnapshot(market_id="m2", outcome="No", price=0.3, volume=50.0))
    assert pf.last_snapshot("m1").price == 0.5
    assert pf.last_snapshot("m2").price == 0.3
    assert len(pf.all_snapshots()) == 2

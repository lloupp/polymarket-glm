"""Tests for storage layer."""
import os
import tempfile
import pytest
from polymarket_glm.storage.database import Database
from polymarket_glm.models import Side


@pytest.fixture
def db():
    """Create a temp database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    database = Database(path)
    database.initialize()
    yield database
    database.close()
    os.unlink(path)


def test_initialize_creates_tables(db):
    """Database should create tables on init."""
    # If we got here without error, tables exist
    assert db._conn is not None


def test_save_and_get_market(db):
    db.save_market(
        condition_id="0xabc", market_id="m1", question="Will X?",
        outcomes='["Yes","No"]', outcome_prices='[0.6,0.4]',
        tokens='["t1","t2"]', volume=50000.0,
    )
    markets = db.get_markets(limit=10)
    assert len(markets) == 1
    assert markets[0]["market_id"] == "m1"


def test_save_and_get_trade(db):
    db.save_trade(
        trade_id="tr1", market_id="m1", side=Side.BUY.value,
        outcome="Yes", price=0.60, size=100.0, fee=0.60,
    )
    trades = db.get_trades(market_id="m1")
    assert len(trades) == 1
    assert trades[0]["price"] == 0.60


def test_save_and_get_signal(db):
    db.save_signal(
        market_id="m1", signal_type="buy", edge=0.10,
        estimated_prob=0.70, market_price=0.60, size_usd=250.0,
    )
    signals = db.get_signals(market_id="m1")
    assert len(signals) == 1
    assert signals[0]["edge"] == 0.10


def test_save_price_snapshot(db):
    db.save_price(market_id="m1", outcome="Yes", price=0.65, volume=1000.0)
    prices = db.get_prices(market_id="m1", limit=10)
    assert len(prices) == 1
    assert prices[0]["price"] == 0.65


def test_get_trades_empty(db):
    trades = db.get_trades()
    assert trades == []


def test_get_signals_with_limit(db):
    for i in range(5):
        db.save_signal(market_id="m1", signal_type="buy", edge=0.05+i*0.01,
                       estimated_prob=0.65, market_price=0.60, size_usd=100.0)
    signals = db.get_signals(limit=3)
    assert len(signals) == 3

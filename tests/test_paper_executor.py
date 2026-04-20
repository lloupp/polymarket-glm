"""Tests for paper executor."""
import pytest
from polymarket_glm.execution.paper_executor import PaperExecutor
from polymarket_glm.execution.exchange import OrderRequest, CancelResult
from polymarket_glm.models import Side, Account


@pytest.fixture
def executor():
    return PaperExecutor(initial_balance=10_000.0, fee_rate_bps=100)


def test_initial_balance(executor):
    acct = executor.account
    assert acct.balance_usd == 10_000.0
    assert acct.total_exposure_usd == 0.0


def test_buy_fill(executor):
    req = OrderRequest(
        market_id="m1", side=Side.BUY, outcome="Yes",
        price=0.60, size=100.0,
    )
    result = executor.submit_order_sync(req)
    assert result.filled is True
    assert result.fee > 0
    assert result.size == 100.0
    # Balance should decrease by cost + fee
    expected_cost = 0.60 * 100.0 + result.fee
    assert executor.account.balance_usd == pytest.approx(10_000.0 - expected_cost)


def test_sell_fill(executor):
    # First buy to have position
    buy = OrderRequest(market_id="m1", side=Side.BUY, outcome="Yes", price=0.50, size=100.0)
    executor.submit_order_sync(buy)
    # Now sell
    sell = OrderRequest(market_id="m1", side=Side.SELL, outcome="Yes", price=0.55, size=50.0)
    result = executor.submit_order_sync(sell)
    assert result.filled is True


def test_fee_calculation(executor):
    """Polymarket fee: 1% on profit, 2% on full amount for taker.
    Our paper executor uses fee_rate_bps for simplicity."""
    req = OrderRequest(
        market_id="m1", side=Side.BUY, outcome="Yes",
        price=0.50, size=200.0,
    )
    result = executor.submit_order_sync(req)
    # Fee = 0.50 * 200 * 100/10000 = 1.0
    assert result.fee == pytest.approx(1.0)


def test_insufficient_balance(executor):
    req = OrderRequest(
        market_id="m1", side=Side.BUY, outcome="Yes",
        price=0.90, size=20_000.0,  # way more than balance
    )
    result = executor.submit_order_sync(req)
    assert result.filled is False
    assert "insufficient" in result.reason.lower()


def test_cancel_order(executor):
    result = executor.cancel_order_sync("nonexistent")
    assert result.success is False


def test_position_tracking(executor):
    executor.submit_order_sync(OrderRequest(
        market_id="m1", side=Side.BUY, outcome="Yes", price=0.60, size=100.0,
    ))
    executor.submit_order_sync(OrderRequest(
        market_id="m2", side=Side.BUY, outcome="No", price=0.30, size=50.0,
    ))
    pos_m1 = executor.get_position("m1", "Yes")
    assert pos_m1 is not None
    assert pos_m1.size == 100.0
    pos_m2 = executor.get_position("m2", "No")
    assert pos_m2 is not None
    assert pos_m2.size == 50.0


def test_account_state(executor):
    acct = executor.account
    assert isinstance(acct, Account)
    assert acct.balance_usd == 10_000.0

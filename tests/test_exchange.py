"""Tests for exchange protocol."""
import pytest
from polymarket_glm.execution.exchange import ExchangeClient, FillResult, OrderRequest
from polymarket_glm.models import Side, Account


def test_order_request():
    req = OrderRequest(
        market_id="m1", side=Side.BUY, outcome="Yes",
        price=0.60, size=50.0, order_type="GTC",
    )
    assert req.side == Side.BUY
    assert req.usd_value == pytest.approx(30.0)


def test_fill_result():
    fill = FillResult(
        order_id="o1", market_id="m1", side=Side.BUY,
        outcome="Yes", price=0.60, size=50.0,
        fee=0.15, filled=True,
    )
    assert fill.total_cost == pytest.approx(30.15)


def test_exchange_client_is_protocol():
    """ExchangeClient should not be instantiable directly — it's a Protocol."""
    with pytest.raises(TypeError):
        ExchangeClient()


def test_order_request_defaults():
    req = OrderRequest(
        market_id="m1", side=Side.SELL, outcome="No",
        price=0.40, size=100.0,
    )
    assert req.order_type == "GTC"

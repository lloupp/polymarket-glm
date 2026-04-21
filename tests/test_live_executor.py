"""Tests for live executor — aligned with py-clob-client 0.34.6+."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from polymarket_glm.execution.live_executor import LiveExecutor
from polymarket_glm.execution.exchange import OrderRequest
from polymarket_glm.models import Side
from polymarket_glm.config import ClobConfig


def _make_config():
    return ClobConfig(
        api_key="test_key",
        api_secret="test_secret",
        api_passphrase="test_pass",
        private_key="0xdeadbeef" * 4,
    )


def test_live_executor_requires_keys():
    """LiveExecutor should refuse to start without API keys."""
    config = ClobConfig()
    with pytest.raises(ValueError, match="API keys"):
        LiveExecutor(clob_config=config)


def test_live_executor_init_with_keys():
    """LiveExecutor should accept valid API keys."""
    executor = LiveExecutor(clob_config=_make_config())
    assert executor._clob_config.api_key == "test_key"


def test_live_executor_dry_run_skips_validation():
    """Dry-run mode should not require API keys."""
    executor = LiveExecutor(clob_config=ClobConfig(), dry_run=True)
    assert executor._dry_run is True


@pytest.mark.asyncio
async def test_dry_run_submit():
    """Dry-run mode should not send orders to CLOB."""
    executor = LiveExecutor(clob_config=_make_config(), dry_run=True)
    req = OrderRequest(
        market_id="0xabc123",
        side=Side.BUY,
        outcome="Yes",
        price=0.55,
        size=10.0,
    )
    result = await executor.submit_order(req)
    assert result.filled is False
    assert "Dry run" in result.reason
    assert result.price == 0.55
    assert result.size == 10.0


@pytest.mark.asyncio
async def test_dry_run_cancel():
    """Dry-run cancel should not call CLOB."""
    executor = LiveExecutor(clob_config=_make_config(), dry_run=True)
    result = await executor.cancel_order("order-123")
    assert result.success is False
    assert "Dry run" in result.reason


@pytest.mark.asyncio
async def test_dry_run_get_open_orders():
    """Dry-run should return empty open orders."""
    executor = LiveExecutor(clob_config=_make_config(), dry_run=True)
    orders = await executor.get_open_orders()
    assert orders == []


@pytest.mark.asyncio
async def test_ensure_client_creates_clobclient():
    """_ensure_client should create ClobClient with ApiCreds."""
    executor = LiveExecutor(clob_config=_make_config(), dry_run=True)
    with patch("py_clob_client.client.ClobClient") as MockClient:
        mock_instance = MagicMock()
        MockClient.return_value = mock_instance
        client = await executor._ensure_client()
        MockClient.assert_called_once()
        # Verify creds was passed
        call_kwargs = MockClient.call_args
        creds_arg = call_kwargs.kwargs.get("creds")
        assert creds_arg is not None
        assert creds_arg.api_key == "test_key"
        assert creds_arg.api_secret == "test_secret"


@pytest.mark.asyncio
async def test_submit_order_uses_order_args():
    """submit_order should create OrderArgs with correct fields."""
    executor = LiveExecutor(clob_config=_make_config(), dry_run=True)
    # We'll test the live path with a mock
    mock_client = MagicMock()
    mock_client.create_and_post_order.return_value = {"orderID": "ord-999"}
    executor._client = mock_client
    executor._dry_run = False  # force live path with mock client

    req = OrderRequest(
        market_id="0xtoken",
        side=Side.BUY,
        outcome="Yes",
        price=0.42,
        size=25.0,
    )
    result = await executor.submit_order(req)
    assert result.order_id == "ord-999"
    # Verify OrderArgs was passed to create_and_post_order
    call_args = mock_client.create_and_post_order.call_args
    order_args = call_args[0][0]
    assert order_args.token_id == "0xtoken"
    assert order_args.price == 0.42
    assert order_args.size == 25.0
    assert order_args.side == "BUY"  # py-clob-client expects uppercase


@pytest.mark.asyncio
async def test_cancel_order_calls_client():
    """cancel_order should call client.cancel with order_id."""
    executor = LiveExecutor(clob_config=_make_config(), dry_run=True)
    mock_client = MagicMock()
    executor._client = mock_client
    executor._dry_run = False

    result = await executor.cancel_order("ord-123")
    mock_client.cancel.assert_called_once_with(order_id="ord-123")
    assert result.success is True

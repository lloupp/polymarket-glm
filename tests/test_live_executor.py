"""Tests for live executor."""
import pytest
from polymarket_glm.execution.live_executor import LiveExecutor
from polymarket_glm.execution.exchange import OrderRequest
from polymarket_glm.models import Side
from polymarket_glm.config import ClobConfig, Settings


def test_live_executor_requires_keys():
    """LiveExecutor should refuse to start without API keys."""
    config = ClobConfig()  # empty keys
    with pytest.raises(ValueError, match="API keys"):
        LiveExecutor(clob_config=config)


def test_live_executor_init_with_keys():
    """LiveExecutor should accept valid API keys."""
    config = ClobConfig(
        api_key="test_key",
        api_secret="test_secret",
        api_passphrase="test_pass",
        private_key="0xdeadbeef",
    )
    executor = LiveExecutor(clob_config=config)
    assert executor._clob_config.api_key == "test_key"

"""Tests for config system."""
import os
import pytest
from polymarket_glm.config import Settings, ExecutionMode, RiskConfig


def test_default_settings():
    s = Settings()
    assert s.execution_mode == ExecutionMode.PAPER
    assert s.risk.max_total_exposure_usd == 500.0
    assert s.risk.max_per_market_exposure_usd == 200.0


def test_env_override():
    os.environ["PGLM_EXECUTION_MODE"] = "live"
    os.environ["PGLM_RISK__MAX_TOTAL_EXPOSURE_USD"] = "5000"
    try:
        s = Settings()
        assert s.execution_mode == ExecutionMode.LIVE
        assert s.risk.max_total_exposure_usd == 5000.0
    finally:
        del os.environ["PGLM_EXECUTION_MODE"]
        del os.environ["PGLM_RISK__MAX_TOTAL_EXPOSURE_USD"]


def test_invalid_execution_mode():
    os.environ["PGLM_EXECUTION_MODE"] = "invalid"
    try:
        with pytest.raises(Exception):
            Settings()
    finally:
        del os.environ["PGLM_EXECUTION_MODE"]


def test_risk_validation():
    with pytest.raises(Exception):
        RiskConfig(max_total_exposure_usd=-100)


def test_live_mode_requires_keys():
    s = Settings(
        execution_mode=ExecutionMode.LIVE,
        clob_api_key="k",
        clob_api_secret="s",
        clob_api_passphrase="p",
        private_key="0xabc",
    )
    assert s.live_ready is True


def test_live_mode_missing_keys():
    s = Settings(execution_mode=ExecutionMode.LIVE)
    assert s.live_ready is False

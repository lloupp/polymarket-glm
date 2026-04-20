"""Tests for the engine (orchestrator)."""
import pytest
from polymarket_glm.engine import Engine
from polymarket_glm.config import Settings, ExecutionMode, RiskConfig
from polymarket_glm.models import Side


def test_engine_init_paper():
    settings = Settings(execution_mode=ExecutionMode.PAPER, paper_balance_usd=5000.0)
    engine = Engine(settings)
    assert engine.is_paper is True
    assert engine.is_live is False


def test_engine_init_live_requires_keys():
    settings = Settings(execution_mode=ExecutionMode.LIVE)
    # Should raise because no API keys
    with pytest.raises(ValueError, match="API keys"):
        Engine(settings)


def test_engine_paper_status():
    settings = Settings(execution_mode=ExecutionMode.PAPER, paper_balance_usd=5000.0)
    engine = Engine(settings)
    status = engine.status()
    assert status["mode"] == "paper"
    assert status["balance_usd"] == 5000.0
    assert status["kill_switch_active"] is False


def test_engine_risk_controller():
    settings = Settings(
        execution_mode=ExecutionMode.PAPER,
        risk=RiskConfig(max_per_trade_usd=200),
    )
    engine = Engine(settings)
    verdict, reason = engine.check_risk("m1", "Yes", 300)
    assert verdict.value == "deny_per_trade"


def test_engine_process_signal_paper():
    settings = Settings(execution_mode=ExecutionMode.PAPER, paper_balance_usd=5000.0)
    engine = Engine(settings)
    from polymarket_glm.strategy.signal_engine import Signal, SignalType
    sig = Signal(
        market_id="m1", condition_id="0xabc", question="Test?",
        signal_type=SignalType.BUY, outcome="Yes",
        edge=0.10, estimated_prob=0.70, market_price=0.60,
        size_usd=100.0, kelly_raw=0.10, kelly_sized=0.025,
        target_price=0.70,
    )
    result = engine.process_signal_sync(sig, price=0.60)
    assert result.filled is True
    assert result.size > 0

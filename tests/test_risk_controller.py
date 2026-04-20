"""Tests for risk controller."""
import time
import pytest
from polymarket_glm.risk.controller import RiskController, RiskVerdict
from polymarket_glm.config import RiskConfig


def test_verdict_values():
    assert RiskVerdict.ALLOW.value == "allow"
    assert RiskVerdict.DENY_EXPOSURE.value == "deny_exposure"
    assert RiskVerdict.DENY_DAILY_LIMIT.value == "deny_daily_limit"
    assert RiskVerdict.DENY_PER_TRADE.value == "deny_per_trade"
    assert RiskVerdict.DENY_MARKET_LIMIT.value == "deny_market_limit"
    assert RiskVerdict.KILL_SWITCH.value == "kill_switch"


def test_allow_within_limits():
    rc = RiskController(RiskConfig(max_per_trade_usd=500, max_total_exposure_usd=1500, max_per_market_exposure_usd=1000))
    verdict, reason = rc.check(market_id="m1", outcome="Yes", trade_usd=200)
    assert verdict == RiskVerdict.ALLOW


def test_deny_per_trade():
    rc = RiskController(RiskConfig(max_per_trade_usd=500))
    verdict, reason = rc.check(market_id="m1", outcome="Yes", trade_usd=600)
    assert verdict == RiskVerdict.DENY_PER_TRADE


def test_deny_total_exposure():
    rc = RiskController(RiskConfig(max_total_exposure_usd=1000, max_per_trade_usd=500))
    # Accumulate exposure
    rc.record_fill(market_id="m1", outcome="Yes", usd=800)
    verdict, reason = rc.check(market_id="m2", outcome="Yes", trade_usd=300)
    assert verdict == RiskVerdict.DENY_EXPOSURE
    assert "total exposure" in reason.lower()


def test_deny_market_limit():
    rc = RiskController(RiskConfig(max_per_market_exposure_usd=500, max_per_trade_usd=500))
    rc.record_fill(market_id="m1", outcome="Yes", usd=400)
    verdict, reason = rc.check(market_id="m1", outcome="Yes", trade_usd=200)
    assert verdict == RiskVerdict.DENY_MARKET_LIMIT


def test_deny_daily_loss():
    rc = RiskController(RiskConfig(daily_loss_limit_usd=100, max_per_trade_usd=500))
    rc.record_loss(120.0)
    verdict, reason = rc.check(market_id="m1", outcome="Yes", trade_usd=50)
    assert verdict == RiskVerdict.DENY_DAILY_LIMIT


def test_kill_switch_manual():
    rc = RiskController(RiskConfig())
    rc.activate_kill_switch("manual test")
    verdict, reason = rc.check(market_id="m1", outcome="Yes", trade_usd=10)
    assert verdict == RiskVerdict.KILL_SWITCH


def test_kill_switch_auto_drawdown():
    rc = RiskController(RiskConfig(
        drawdown_circuit_breaker_pct=0.20,
        drawdown_arm_period_sec=0.01,  # minimal arm period for test
        drawdown_min_observations=3,
    ))
    rc._peak_balance = 1000.0
    # Simulate 3 drawdown observations (each 25% below peak)
    for _ in range(3):
        rc._check_drawdown(750.0)
    verdict, reason = rc.check(market_id="m1", outcome="Yes", trade_usd=10)
    assert verdict == RiskVerdict.KILL_SWITCH


def test_kill_switch_cooldown():
    rc = RiskController(RiskConfig(kill_switch_cooldown_sec=0.01))
    rc.activate_kill_switch("test")
    verdict, _ = rc.check(market_id="m1", outcome="Yes", trade_usd=10)
    assert verdict == RiskVerdict.KILL_SWITCH
    time.sleep(0.02)
    verdict, _ = rc.check(market_id="m1", outcome="Yes", trade_usd=10)
    assert verdict == RiskVerdict.ALLOW


def test_exposure_tracking():
    rc = RiskController(RiskConfig())
    rc.record_fill(market_id="m1", outcome="Yes", usd=300)
    rc.record_fill(market_id="m1", outcome="Yes", usd=200)
    rc.record_fill(market_id="m2", outcome="No", usd=400)
    assert rc.total_exposure == 900.0
    assert rc.market_exposure("m1") == 500.0
    assert rc.market_exposure("m2") == 400.0


def test_reset_daily():
    rc = RiskController(RiskConfig(daily_loss_limit_usd=100))
    rc.record_loss(80.0)
    assert rc.daily_loss == 80.0
    rc.reset_daily()
    assert rc.daily_loss == 0.0

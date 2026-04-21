"""Comprehensive kill switch test suite.

Tests ALL activation and deactivation paths:
1) Manual activate_kill_switch
2) Drawdown circuit-breaker auto-trigger
3) Kill switch blocks ALL orders (returns KILL_SWITCH verdict)
4) Cooldown expiry auto-deactivates
5) Manual deactivate overrides cooldown
6) Daily loss limit does NOT trigger kill switch (separate mechanism)
7) Reactivation after deactivation works
8) TelegramBot killswitch integration
"""
import time

import pytest
from unittest.mock import MagicMock

from polymarket_glm.config import RiskConfig
from polymarket_glm.risk.controller import RiskController, RiskVerdict
from polymarket_glm.ops.telegram_bot import (
    TelegramBot,
    TelegramCommand,
    CommandResult,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _make_rc(**overrides) -> RiskController:
    """Create a RiskController with sensible test defaults."""
    defaults = dict(
        max_total_exposure_usd=500.0,
        max_per_market_exposure_usd=200.0,
        max_per_trade_usd=50.0,
        daily_loss_limit_usd=30.0,
        drawdown_circuit_breaker_pct=0.10,
        kill_switch_cooldown_sec=900.0,
        drawdown_arm_period_sec=0.01,
        drawdown_min_observations=3,
    )
    defaults.update(overrides)
    return RiskController(RiskConfig(**defaults))


def _assert_kill_switch(rc: RiskController, market_id: str = "m1"):
    """Assert that a check on rc returns KILL_SWITCH verdict."""
    verdict, reason = rc.check(market_id=market_id, outcome="Yes", trade_usd=10.0)
    assert verdict is RiskVerdict.KILL_SWITCH, f"Expected KILL_SWITCH, got {verdict}: {reason}"
    assert "kill switch" in reason.lower(), f"Reason should mention kill switch: {reason}"


def _assert_allow(rc: RiskController, market_id: str = "m1", trade_usd: float = 10.0):
    """Assert that a check on rc returns ALLOW verdict."""
    verdict, reason = rc.check(market_id=market_id, outcome="Yes", trade_usd=trade_usd)
    assert verdict is RiskVerdict.ALLOW, f"Expected ALLOW, got {verdict}: {reason}"


# =====================================================================
# 1) Manual activate_kill_switch
# =====================================================================

class TestManualActivate:
    """Manual activation via activate_kill_switch(reason)."""

    def test_activate_sets_active_flag(self):
        rc = _make_rc()
        assert not rc.status()["kill_switch_active"]
        rc.activate_kill_switch("manual test")
        assert rc.status()["kill_switch_active"] is True

    def test_activate_stores_reason(self):
        rc = _make_rc()
        rc.activate_kill_switch("emergency override")
        assert rc.status()["kill_switch_reason"] == "emergency override"

    def test_manual_activate_blocks_orders(self):
        rc = _make_rc()
        rc.activate_kill_switch("manual trigger")
        _assert_kill_switch(rc)

    def test_manual_activate_blocks_different_markets(self):
        """Kill switch blocks orders on ALL markets, not just one."""
        rc = _make_rc()
        rc.activate_kill_switch("global halt")
        _assert_kill_switch(rc, market_id="m1")
        _assert_kill_switch(rc, market_id="m2")
        _assert_kill_switch(rc, market_id="m99")

    def test_manual_activate_blocks_any_trade_size(self):
        """Kill switch blocks even tiny trades."""
        rc = _make_rc()
        rc.activate_kill_switch("full stop")
        verdict, _ = rc.check(market_id="m1", outcome="Yes", trade_usd=0.01)
        assert verdict is RiskVerdict.KILL_SWITCH

    def test_manual_activate_blocks_large_trade(self):
        """Kill switch blocks even large trades that would otherwise be denied for other reasons."""
        rc = _make_rc(max_per_trade_usd=50.0)
        rc.activate_kill_switch("halt")
        # A $600 trade would normally be DENY_PER_TRADE, but kill switch takes precedence
        verdict, reason = rc.check(market_id="m1", outcome="Yes", trade_usd=600.0)
        assert verdict is RiskVerdict.KILL_SWITCH

    def test_reason_in_check_response(self):
        rc = _make_rc()
        rc.activate_kill_switch("drawdown exceeded")
        _, reason = rc.check(market_id="m1", outcome="Yes", trade_usd=10.0)
        assert "drawdown exceeded" in reason


# =====================================================================
# 2) Drawdown circuit-breaker auto-trigger
# =====================================================================

class TestDrawdownCircuitBreaker:
    """Drawdown exceeding threshold auto-triggers kill switch."""

    def test_drawdown_auto_activates_kill_switch(self):
        rc = _make_rc(
            drawdown_circuit_breaker_pct=0.20,
            drawdown_arm_period_sec=0.01,
            drawdown_min_observations=3,
        )
        rc._peak_balance = 1000.0
        # 25% drawdown from peak — exceeds 20% threshold
        for _ in range(3):
            rc._check_drawdown(750.0)
        _assert_kill_switch(rc)

    def test_drawdown_reason_contains_drawdown_info(self):
        rc = _make_rc(
            drawdown_circuit_breaker_pct=0.20,
            drawdown_arm_period_sec=0.01,
            drawdown_min_observations=3,
        )
        rc._peak_balance = 1000.0
        for _ in range(3):
            rc._check_drawdown(750.0)
        _, reason = rc.check(market_id="m1", outcome="Yes", trade_usd=10.0)
        assert "drawdown" in reason.lower() or "circuit" in reason.lower()

    def test_drawdown_requires_min_observations(self):
        """Fewer than min_observations should NOT trigger kill switch."""
        rc = _make_rc(
            drawdown_circuit_breaker_pct=0.20,
            drawdown_arm_period_sec=0.01,
            drawdown_min_observations=3,
        )
        rc._peak_balance = 1000.0
        # Only 2 observations — not enough
        rc._check_drawdown(750.0)
        rc._check_drawdown(750.0)
        _assert_allow(rc)

    def test_drawdown_does_not_trigger_below_threshold(self):
        """Drawdown below the circuit-breaker percentage should NOT trigger."""
        rc = _make_rc(
            drawdown_circuit_breaker_pct=0.20,
            drawdown_arm_period_sec=0.01,
            drawdown_min_observations=3,
        )
        rc._peak_balance = 1000.0
        # Only 10% drawdown — below 20% threshold
        for _ in range(5):
            rc._check_drawdown(900.0)
        _assert_allow(rc)

    def test_update_balance_triggers_drawdown(self):
        """update_balance() should trigger drawdown check internally."""
        rc = _make_rc(
            drawdown_circuit_breaker_pct=0.10,
            drawdown_arm_period_sec=10.0,
            drawdown_min_observations=3,
        )
        # Peak starts at 10_000 (default)
        # Balance drops to 8_500 → 15% drawdown, exceeds 10% threshold
        for _ in range(3):
            rc.update_balance(8500.0)
        _assert_kill_switch(rc)

    def test_drawdown_arm_period_prunes_stale_observations(self):
        """Observations outside the arm period should be pruned and not count."""
        rc = _make_rc(
            drawdown_circuit_breaker_pct=0.20,
            drawdown_arm_period_sec=0.01,  # very short arm period
            drawdown_min_observations=3,
        )
        rc._peak_balance = 1000.0
        # Add 3 observations, then wait for arm period to expire
        for _ in range(3):
            rc._check_drawdown(750.0)
        # Kill switch should be active now
        assert rc.status()["kill_switch_active"]

        # Deactivate to test fresh
        rc.deactivate_kill_switch()

        # Now add 1 new observation — the old 3 should be pruned
        time.sleep(0.02)
        rc._check_drawdown(750.0)
        # Only 1 fresh observation — not enough
        _assert_allow(rc)


# =====================================================================
# 3) Kill switch blocks ALL orders
# =====================================================================

class TestKillSwitchBlocksAllOrders:
    """Kill switch returns KILL_SWITCH verdict for ALL orders."""

    def test_blocks_small_order(self):
        rc = _make_rc()
        rc.activate_kill_switch("test")
        verdict, _ = rc.check(market_id="m1", outcome="Yes", trade_usd=1.0)
        assert verdict is RiskVerdict.KILL_SWITCH

    def test_blocks_order_on_any_market(self):
        rc = _make_rc()
        rc.activate_kill_switch("test")
        for mid in ["m1", "m2", "market-abc", "market-xyz"]:
            verdict, _ = rc.check(market_id=mid, outcome="Yes", trade_usd=5.0)
            assert verdict is RiskVerdict.KILL_SWITCH, f"Kill switch should block on market {mid}"

    def test_blocks_yes_and_no_outcomes(self):
        rc = _make_rc()
        rc.activate_kill_switch("test")
        for outcome in ["Yes", "No"]:
            verdict, _ = rc.check(market_id="m1", outcome=outcome, trade_usd=10.0)
            assert verdict is RiskVerdict.KILL_SWITCH, f"Should block {outcome} outcome"

    def test_blocks_order_that_exceeds_exposure_limit(self):
        """Kill switch takes priority over exposure limit denial."""
        rc = _make_rc(max_total_exposure_usd=50.0, max_per_trade_usd=500.0)
        rc.record_fill(market_id="m1", outcome="Yes", usd=40.0)
        rc.activate_kill_switch("test")
        # Would be DENY_EXPOSURE, but kill switch wins
        verdict, reason = rc.check(market_id="m2", outcome="Yes", trade_usd=20.0)
        assert verdict is RiskVerdict.KILL_SWITCH

    def test_blocks_order_that_exceeds_per_trade_limit(self):
        """Kill switch takes priority over per-trade limit denial."""
        rc = _make_rc(max_per_trade_usd=10.0)
        rc.activate_kill_switch("test")
        verdict, reason = rc.check(market_id="m1", outcome="Yes", trade_usd=100.0)
        assert verdict is RiskVerdict.KILL_SWITCH

    def test_blocks_order_when_daily_limit_exceeded(self):
        """Kill switch takes priority over daily limit denial."""
        rc = _make_rc(daily_loss_limit_usd=5.0)
        rc.record_loss(10.0)
        rc.activate_kill_switch("test")
        verdict, reason = rc.check(market_id="m1", outcome="Yes", trade_usd=5.0)
        assert verdict is RiskVerdict.KILL_SWITCH

    def test_verdict_is_exactly_kill_switch_enum(self):
        """The verdict value is the RiskVerdict.KILL_SWITCH enum member."""
        rc = _make_rc()
        rc.activate_kill_switch("test")
        verdict, _ = rc.check(market_id="m1", outcome="Yes", trade_usd=10.0)
        assert verdict == RiskVerdict.KILL_SWITCH
        assert verdict.value == "kill_switch"


# =====================================================================
# 4) Cooldown expiry auto-deactivates
# =====================================================================

class TestCooldownExpiry:
    """Kill switch auto-deactivates when cooldown expires during check()."""

    def test_cooldown_expiry_allows_trading(self):
        rc = _make_rc(kill_switch_cooldown_sec=0.01)
        rc.activate_kill_switch("short cooldown")
        # Still within cooldown
        _assert_kill_switch(rc)
        # Wait for cooldown to expire
        time.sleep(0.02)
        # check() should auto-deactivate and allow
        _assert_allow(rc)

    def test_cooldown_expiry_clears_active_flag(self):
        rc = _make_rc(kill_switch_cooldown_sec=0.01)
        rc.activate_kill_switch("test")
        assert rc.status()["kill_switch_active"] is True
        time.sleep(0.02)
        rc.check(market_id="m1", outcome="Yes", trade_usd=10.0)
        assert rc.status()["kill_switch_active"] is False

    def test_cooldown_not_yet_expired_still_blocks(self):
        rc = _make_rc(kill_switch_cooldown_sec=10.0)
        rc.activate_kill_switch("long cooldown")
        _assert_kill_switch(rc)
        assert rc.status()["kill_switch_active"] is True

    def test_cooldown_expiry_allows_all_markets_after(self):
        rc = _make_rc(kill_switch_cooldown_sec=0.01)
        rc.activate_kill_switch("test")
        _assert_kill_switch(rc, market_id="m1")
        time.sleep(0.02)
        _assert_allow(rc, market_id="m1")
        _assert_allow(rc, market_id="m2")

    def test_multiple_checks_during_cooldown_all_blocked(self):
        rc = _make_rc(kill_switch_cooldown_sec=0.05)
        rc.activate_kill_switch("test")
        for _ in range(5):
            _assert_kill_switch(rc)
        time.sleep(0.06)
        _assert_allow(rc)


# =====================================================================
# 5) Manual deactivate overrides cooldown
# =====================================================================

class TestManualDeactivateOverridesCooldown:
    """Manual deactivate_kill_switch() deactivates even during cooldown."""

    def test_deactivate_during_cooldown(self):
        rc = _make_rc(kill_switch_cooldown_sec=999.0)  # very long cooldown
        rc.activate_kill_switch("emergency")
        _assert_kill_switch(rc)
        # Manually deactivate — should override the cooldown
        rc.deactivate_kill_switch()
        _assert_allow(rc)

    def test_deactivate_clears_reason(self):
        rc = _make_rc()
        rc.activate_kill_switch("some reason")
        assert rc.status()["kill_switch_reason"] == "some reason"
        rc.deactivate_kill_switch()
        assert rc.status()["kill_switch_reason"] == ""

    def test_deactivate_clears_active_flag(self):
        rc = _make_rc()
        rc.activate_kill_switch("test")
        assert rc.status()["kill_switch_active"] is True
        rc.deactivate_kill_switch()
        assert rc.status()["kill_switch_active"] is False

    def test_deactivate_then_allow_trading(self):
        rc = _make_rc(kill_switch_cooldown_sec=999.0)
        rc.activate_kill_switch("halt")
        rc.deactivate_kill_switch()
        # Should be able to trade normally now
        _assert_allow(rc, trade_usd=50.0)

    def test_deactivate_then_exposure_limits_still_work(self):
        """After deactivation, normal risk checks (like exposure limits) still apply."""
        rc = _make_rc(max_per_trade_usd=50.0, kill_switch_cooldown_sec=999.0)
        rc.activate_kill_switch("halt")
        rc.deactivate_kill_switch()
        # A trade exceeding per-trade limit should still be denied
        verdict, reason = rc.check(market_id="m1", outcome="Yes", trade_usd=100.0)
        assert verdict is RiskVerdict.DENY_PER_TRADE


# =====================================================================
# 6) Daily loss limit does NOT trigger kill switch
# =====================================================================

class TestDailyLossDoesNotTriggerKillSwitch:
    """Daily loss limit is a separate mechanism — it does NOT activate kill switch."""

    def test_daily_loss_does_not_activate_kill_switch(self):
        rc = _make_rc(daily_loss_limit_usd=30.0)
        rc.record_loss(50.0)  # exceeds daily limit
        # Kill switch should NOT be active
        assert rc.status()["kill_switch_active"] is False

    def test_daily_loss_returns_deny_daily_limit_not_kill_switch(self):
        rc = _make_rc(daily_loss_limit_usd=30.0, max_per_trade_usd=500.0)
        rc.record_loss(50.0)  # exceeds daily limit
        verdict, reason = rc.check(market_id="m1", outcome="Yes", trade_usd=10.0)
        assert verdict is RiskVerdict.DENY_DAILY_LIMIT
        assert verdict is not RiskVerdict.KILL_SWITCH

    def test_daily_loss_reason_mentions_limit_not_kill_switch(self):
        rc = _make_rc(daily_loss_limit_usd=30.0, max_per_trade_usd=500.0)
        rc.record_loss(50.0)
        _, reason = rc.check(market_id="m1", outcome="Yes", trade_usd=10.0)
        assert "daily loss" in reason.lower() or "limit" in reason.lower()
        assert "kill switch" not in reason.lower()

    def test_daily_loss_then_reset_allows_trading(self):
        """After reset_daily(), daily loss limit no longer blocks."""
        rc = _make_rc(daily_loss_limit_usd=30.0, max_per_trade_usd=500.0)
        rc.record_loss(50.0)
        verdict, _ = rc.check(market_id="m1", outcome="Yes", trade_usd=10.0)
        assert verdict is RiskVerdict.DENY_DAILY_LIMIT
        rc.reset_daily()
        _assert_allow(rc)

    def test_daily_loss_plus_kill_switch_kill_switch_wins(self):
        """If both daily loss exceeded AND kill switch active, kill switch verdict wins."""
        rc = _make_rc(daily_loss_limit_usd=5.0, max_per_trade_usd=500.0)
        rc.record_loss(10.0)  # exceed daily limit
        rc.activate_kill_switch("manual")
        verdict, reason = rc.check(market_id="m1", outcome="Yes", trade_usd=10.0)
        # Kill switch is checked first, so KILL_SWITCH takes priority
        assert verdict is RiskVerdict.KILL_SWITCH


# =====================================================================
# 7) Reactivation after deactivation works
# =====================================================================

class TestReactivationAfterDeactivation:
    """Kill switch can be reactivated after being deactivated."""

    def test_reactivate_after_manual_deactivate(self):
        rc = _make_rc(kill_switch_cooldown_sec=999.0)
        # Activate
        rc.activate_kill_switch("first activation")
        _assert_kill_switch(rc)
        # Deactivate
        rc.deactivate_kill_switch()
        _assert_allow(rc)
        # Reactivate
        rc.activate_kill_switch("second activation")
        _assert_kill_switch(rc)

    def test_reactivated_reason_overwrites_previous(self):
        rc = _make_rc()
        rc.activate_kill_switch("reason A")
        assert rc.status()["kill_switch_reason"] == "reason A"
        rc.deactivate_kill_switch()
        assert rc.status()["kill_switch_reason"] == ""
        rc.activate_kill_switch("reason B")
        assert rc.status()["kill_switch_reason"] == "reason B"

    def test_reactivate_after_cooldown_expiry(self):
        rc = _make_rc(kill_switch_cooldown_sec=0.01)
        # First activation
        rc.activate_kill_switch("first")
        _assert_kill_switch(rc)
        # Wait for cooldown to auto-deactivate
        time.sleep(0.02)
        _assert_allow(rc)
        # Reactivate
        rc.activate_kill_switch("second")
        _assert_kill_switch(rc)

    def test_reactivate_after_drawdown_deactivate(self):
        """After drawdown triggers kill switch and it's deactivated, can reactivate."""
        rc = _make_rc(
            drawdown_circuit_breaker_pct=0.20,
            drawdown_arm_period_sec=0.01,
            drawdown_min_observations=3,
        )
        rc._peak_balance = 1000.0
        for _ in range(3):
            rc._check_drawdown(750.0)
        _assert_kill_switch(rc)
        # Manually deactivate
        rc.deactivate_kill_switch()
        _assert_allow(rc)
        # Manually reactivate with new reason
        rc.activate_kill_switch("manual reactivation")
        _assert_kill_switch(rc)

    def test_multiple_activate_deactivate_cycles(self):
        """Kill switch can be toggled multiple times."""
        rc = _make_rc(kill_switch_cooldown_sec=999.0)
        for i in range(5):
            rc.activate_kill_switch(f"cycle {i}")
            _assert_kill_switch(rc)
            rc.deactivate_kill_switch()
            _assert_allow(rc)


# =====================================================================
# 8) TelegramBot killswitch integration
# =====================================================================

class TestTelegramBotKillswitchIntegration:
    """Test TelegramBot's killswitch_fn wiring with RiskController."""

    def test_killswitch_fn_activates_kill_switch(self):
        """killswitch_fn returning True should activate the kill switch."""
        rc = _make_rc()
        # Wire killswitch_fn to activate kill switch and return True
        def activate():
            rc.activate_kill_switch("via telegram")
            return True

        bot = TelegramBot(token="test-token", chat_id="123", killswitch_fn=activate)
        # Simulate the /killswitch command flow
        result = bot.killswitch_fn()
        assert result is True
        _assert_kill_switch(rc)

    def test_killswitch_fn_deactivates_kill_switch(self):
        """killswitch_fn returning False should indicate deactivation."""
        rc = _make_rc()
        rc.activate_kill_switch("pre-existing")

        def deactivate():
            rc.deactivate_kill_switch()
            return False

        bot = TelegramBot(token="test-token", chat_id="123", killswitch_fn=deactivate)
        result = bot.killswitch_fn()
        assert result is False
        _assert_allow(rc)

    def test_killswitch_fn_not_configured(self):
        """When killswitch_fn is None, handle_command returns error."""
        bot = TelegramBot(token="test-token", chat_id="123")
        assert bot.killswitch_fn is None

    @pytest.mark.asyncio
    async def test_handle_killswitch_command_activates(self):
        """handle_command with KILLSWITCH activates via killswitch_fn."""
        rc = _make_rc()

        def activate():
            rc.activate_kill_switch("telegram command")
            return True

        bot = TelegramBot(token="test-token", chat_id="123", killswitch_fn=activate)
        result = await bot.handle_command(TelegramCommand.KILLSWITCH)
        assert result.ok is True
        assert "activated" in result.text.lower() or "kill" in result.text.lower()
        _assert_kill_switch(rc)

    @pytest.mark.asyncio
    async def test_handle_killswitch_command_deactivates(self):
        """handle_command with KILLSWITCH deactivates via killswitch_fn."""
        rc = _make_rc()
        rc.activate_kill_switch("pre-existing")

        def deactivate():
            rc.deactivate_kill_switch()
            return False

        bot = TelegramBot(token="test-token", chat_id="123", killswitch_fn=deactivate)
        result = await bot.handle_command(TelegramCommand.KILLSWITCH)
        assert result.ok is True
        assert "deactivated" in result.text.lower() or "resumed" in result.text.lower()
        _assert_allow(rc)

    @pytest.mark.asyncio
    async def test_handle_killswitch_no_fn_returns_error(self):
        """When killswitch_fn not configured, command returns error."""
        bot = TelegramBot(token="test-token", chat_id="123")
        result = await bot.handle_command(TelegramCommand.KILLSWITCH)
        assert result.ok is False

    @pytest.mark.asyncio
    async def test_killswitch_toggle_cycle_via_bot(self):
        """Full toggle cycle: activate → check blocked → deactivate → check allowed."""
        rc = _make_rc(kill_switch_cooldown_sec=999.0)
        state = {"active": False}

        def toggle():
            if state["active"]:
                rc.deactivate_kill_switch()
                state["active"] = False
                return False
            else:
                rc.activate_kill_switch("telegram toggle")
                state["active"] = True
                return True

        bot = TelegramBot(token="test-token", chat_id="123", killswitch_fn=toggle)

        # First call: activate
        result = await bot.handle_command(TelegramCommand.KILLSWITCH)
        assert result.ok
        _assert_kill_switch(rc)

        # Second call: deactivate
        result = await bot.handle_command(TelegramCommand.KILLSWITCH)
        assert result.ok
        _assert_allow(rc)

    @pytest.mark.asyncio
    async def test_risk_command_shows_kill_switch_status(self):
        """/risk command reflects kill switch state."""
        rc = _make_rc()

        def risk_provider():
            s = rc.status()
            return {
                "total_exposure": s["total_exposure"],
                "max_exposure": 500.0,
                "daily_pnl": -s["daily_loss"],
                "daily_limit": 30.0,
                "kill_switch_active": s["kill_switch_active"],
            }

        bot = TelegramBot(token="test-token", chat_id="123", risk_provider=risk_provider)

        # Before activation
        result = await bot.handle_command(TelegramCommand.RISK)
        assert result.ok
        assert "OFF" in result.text

        # After activation
        rc.activate_kill_switch("test")
        result = await bot.handle_command(TelegramCommand.RISK)
        assert result.ok
        assert "ON" in result.text

    def test_killswitch_fn_wiring_with_risk_controller_status(self):
        """Verify killswitch_fn integration reflects in risk controller status."""
        rc = _make_rc()

        def activate():
            rc.activate_kill_switch("wiring test")
            return True

        bot = TelegramBot(token="test-token", chat_id="123", killswitch_fn=activate)

        # Before calling killswitch_fn
        assert rc.status()["kill_switch_active"] is False

        # Call killswitch_fn
        result = bot.killswitch_fn()
        assert result is True
        assert rc.status()["kill_switch_active"] is True
        assert rc.status()["kill_switch_reason"] == "wiring test"

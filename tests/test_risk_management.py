"""Tests for enhanced risk management — position sizing, category limits, spread/liquidity, cooldown.

New risk checks:
1. Max position size as % of portfolio (not just absolute USD)
2. Per-category exposure limit
3. Spread/liquidity gate — reject if spread too wide
4. Cooldown between trades on same market
"""
from __future__ import annotations

import time

import pytest

from polymarket_glm.config import RiskConfig
from polymarket_glm.risk.controller import RiskController, RiskVerdict


class TestPositionSizeAsPercentOfPortfolio:
    """Position size should be capped as a percentage of portfolio."""

    def test_config_has_max_position_pct(self):
        """RiskConfig should have max_position_pct_of_portfolio field."""
        rc = RiskConfig()
        assert hasattr(rc, "max_position_pct_of_portfolio")
        assert rc.max_position_pct_of_portfolio == 0.10  # 10% default

    def test_trade_exceeds_portfolio_pct_rejected(self):
        """Trade > max_position_pct_of_portfolio * balance should be rejected."""
        config = RiskConfig(
            max_position_pct_of_portfolio=0.10,
            max_per_trade_usd=1_000_000,  # High so pct limit is binding
            max_total_exposure_usd=1_000_000,
            trade_cooldown_sec=0,
            drawdown_circuit_breaker_pct=0.99,  # High to avoid drawdown firing
        )
        rc = RiskController(config=config, initial_balance=1_000.0)

        # 10% of $1000 = $100 max position
        # Trade of $150 should be rejected
        verdict, reason = rc.check(
            market_id="m1", outcome="Yes",
            trade_usd=150.0,
            current_balance=1_000.0,
        )
        assert verdict == RiskVerdict.DENY_PER_TRADE
        assert "portfolio" in reason.lower() or "pct" in reason.lower() or "%" in reason

    def test_trade_within_portfolio_pct_allowed(self):
        """Trade <= max_position_pct_of_portfolio * balance should be allowed."""
        config = RiskConfig(
            max_position_pct_of_portfolio=0.10,
            max_per_trade_usd=1_000_000,
            max_total_exposure_usd=1_000_000,
        )
        rc = RiskController(config=config, initial_balance=1_000.0)

        # 10% of $1000 = $100 max position
        # Trade of $50 should be allowed
        verdict, reason = rc.check(
            market_id="m1", outcome="Yes",
            trade_usd=50.0,
            current_balance=1_000.0,
        )
        assert verdict == RiskVerdict.ALLOW

    def test_smaller_portfolio_reduces_max_trade(self):
        """As portfolio shrinks, max position size should shrink too."""
        config = RiskConfig(
            max_position_pct_of_portfolio=0.10,
            max_per_trade_usd=1_000_000,
            max_total_exposure_usd=1_000_000,
            trade_cooldown_sec=0,
            drawdown_circuit_breaker_pct=0.99,  # High to avoid drawdown firing
        )
        rc = RiskController(config=config, initial_balance=1_000.0)

        # With $500 balance, max = $50
        verdict, _ = rc.check(
            market_id="m1", outcome="Yes",
            trade_usd=60.0,
            current_balance=500.0,
        )
        assert verdict == RiskVerdict.DENY_PER_TRADE


class TestPerCategoryExposure:
    """Exposure should be limited per market category."""

    def test_config_has_category_fields(self):
        """RiskConfig should have per-category exposure fields."""
        rc = RiskConfig()
        assert hasattr(rc, "max_category_exposure_usd")
        assert rc.max_category_exposure_usd == 300.0

    def test_category_exposure_limit(self):
        """Trade exceeding category exposure should be rejected."""
        config = RiskConfig(
            max_category_exposure_usd=100.0,
            max_total_exposure_usd=1_000_000,
            max_per_market_exposure_usd=1_000_000,
            max_per_trade_usd=1_000_000,
            trade_cooldown_sec=0,
        )
        rc = RiskController(config=config, initial_balance=1_000.0)

        # Set category and record exposure for m1
        rc.set_market_category("m1", "politics")
        rc.record_fill("m1", "Yes", 80.0)

        # Another trade in politics category — $80 + $30 = $110 > $100
        verdict, reason = rc.check_with_category(
            market_id="m2", outcome="Yes",
            trade_usd=30.0, category="politics",
        )
        assert verdict == RiskVerdict.DENY_CATEGORY_LIMIT
        assert "category" in reason.lower() or "politics" in reason.lower()

    def test_different_category_allowed(self):
        """Trade in a different category should be allowed."""
        config = RiskConfig(
            max_category_exposure_usd=100.0,
            max_total_exposure_usd=1_000_000,
            max_per_market_exposure_usd=1_000_000,
            max_per_trade_usd=1_000_000,
            trade_cooldown_sec=0,
        )
        rc = RiskController(config=config, initial_balance=1_000.0)

        rc.set_market_category("m1", "politics")
        rc.record_fill("m1", "Yes", 80.0)

        # Trade in "sports" category — should be allowed
        verdict, _ = rc.check_with_category(
            market_id="m2", outcome="Yes",
            trade_usd=30.0, category="sports",
        )
        assert verdict == RiskVerdict.ALLOW


class TestSpreadLiquidityGate:
    """Wide spread / low liquidity trades should be rejected."""

    def test_config_has_spread_fields(self):
        """RiskConfig should have max_spread_bps field."""
        rc = RiskConfig()
        assert hasattr(rc, "max_spread_bps")
        assert rc.max_spread_bps == 500  # 5% default

    def test_wide_spread_rejected(self):
        """Trade with spread > max_spread_bps should be rejected."""
        config = RiskConfig(max_spread_bps=500, trade_cooldown_sec=0)
        rc = RiskController(config=config, initial_balance=1_000.0)

        verdict, reason = rc.check_with_spread(
            market_id="m1", outcome="Yes",
            trade_usd=10.0, spread_bps=800,
        )
        assert verdict == RiskVerdict.DENY_SPREAD
        assert "spread" in reason.lower() or "liquidity" in reason.lower()

    def test_tight_spread_allowed(self):
        """Trade with spread <= max_spread_bps should be allowed."""
        config = RiskConfig(
            max_spread_bps=500,
            max_per_trade_usd=1_000_000,
            max_total_exposure_usd=1_000_000,
            trade_cooldown_sec=0,
        )
        rc = RiskController(config=config, initial_balance=1_000.0)

        verdict, _ = rc.check_with_spread(
            market_id="m1", outcome="Yes",
            trade_usd=10.0, spread_bps=200,
        )
        assert verdict == RiskVerdict.ALLOW


class TestTradeCooldown:
    """Cooldown between trades on the same market."""

    def test_config_has_cooldown_field(self):
        """RiskConfig should have trade_cooldown_sec field."""
        rc = RiskConfig()
        assert hasattr(rc, "trade_cooldown_sec")
        assert rc.trade_cooldown_sec == 300  # 5 min default

    def test_trade_within_cooldown_rejected(self):
        """Trade on same market within cooldown should be rejected."""
        config = RiskConfig(
            trade_cooldown_sec=300,
            max_per_trade_usd=1_000_000,
            max_total_exposure_usd=1_000_000,
        )
        rc = RiskController(config=config, initial_balance=1_000.0)

        # First trade — allowed
        verdict1, _ = rc.check(
            market_id="m1", outcome="Yes",
            trade_usd=10.0, current_balance=1_000.0,
        )
        assert verdict1 == RiskVerdict.ALLOW
        rc.record_fill("m1", "Yes", 10.0)
        rc.record_trade_time("m1")

        # Immediate second trade on same market — rejected
        verdict2, reason = rc.check(
            market_id="m1", outcome="Yes",
            trade_usd=10.0, current_balance=990.0,
        )
        assert verdict2 == RiskVerdict.DENY_COOLDOWN
        assert "cooldown" in reason.lower()

    def test_trade_after_cooldown_allowed(self):
        """Trade on same market after cooldown should be allowed."""
        config = RiskConfig(
            trade_cooldown_sec=0.01,  # 10ms for testing
            max_per_trade_usd=1_000_000,
            max_total_exposure_usd=1_000_000,
        )
        rc = RiskController(config=config, initial_balance=1_000.0)

        # First trade
        rc.record_fill("m1", "Yes", 10.0)
        rc.record_trade_time("m1")

        # Wait for cooldown
        time.sleep(0.02)

        # Second trade — should be allowed
        verdict, _ = rc.check(
            market_id="m1", outcome="Yes",
            trade_usd=10.0, current_balance=990.0,
        )
        assert verdict == RiskVerdict.ALLOW

    def test_different_market_not_affected_by_cooldown(self):
        """Trade on different market should not be affected by cooldown."""
        config = RiskConfig(
            trade_cooldown_sec=300,
            max_per_trade_usd=1_000_000,
            max_total_exposure_usd=1_000_000,
        )
        rc = RiskController(config=config, initial_balance=1_000.0)

        rc.record_fill("m1", "Yes", 10.0)
        rc.record_trade_time("m1")

        # Trade on m2 — should be allowed even though m1 is in cooldown
        verdict, _ = rc.check(
            market_id="m2", outcome="Yes",
            trade_usd=10.0, current_balance=990.0,
        )
        assert verdict == RiskVerdict.ALLOW

"""Tests for Volume and Liquidity risk gates in RiskController.

Task 2: Add DENY_VOLUME and DENY_LIQUIDITY verdicts with
min_volume_usd and min_liquidity_usd config fields.
"""
import pytest
from polymarket_glm.risk.controller import RiskController, RiskVerdict
from polymarket_glm.config import RiskConfig

from pathlib import Path
import tempfile


def _tmp_kill_file() -> Path:
    return Path(tempfile.mkdtemp()) / "kill_switch.json"


def _make_rc(**risk_overrides) -> RiskController:
    """Create a RiskController with permissive defaults for targeted tests."""
    defaults = dict(
        max_per_trade_usd=10_000.0,
        max_total_exposure_usd=100_000.0,
        max_per_market_exposure_usd=100_000.0,
        daily_loss_limit_usd=100_000.0,
        max_position_pct_of_portfolio=0.99,
    )
    defaults.update(risk_overrides)
    return RiskController(
        config=RiskConfig(**defaults),
        kill_switch_file=_tmp_kill_file(),
    )


class TestRiskVerdictVolumeLiquidity:
    """RiskVerdict enum includes DENY_VOLUME and DENY_LIQUIDITY."""

    def test_deny_volume_exists(self):
        assert RiskVerdict.DENY_VOLUME.value == "deny_volume"

    def test_deny_liquidity_exists(self):
        assert RiskVerdict.DENY_LIQUIDITY.value == "deny_liquidity"

    def test_is_str_enum(self):
        assert isinstance(RiskVerdict.DENY_VOLUME, str)
        assert isinstance(RiskVerdict.DENY_LIQUIDITY, str)


class TestRiskConfigVolumeLiquidity:
    """RiskConfig includes min_volume_usd and min_liquidity_usd fields."""

    def test_min_volume_usd_default(self):
        cfg = RiskConfig()
        assert cfg.min_volume_usd == 10_000.0

    def test_min_liquidity_usd_default(self):
        cfg = RiskConfig()
        assert cfg.min_liquidity_usd == 5_000.0

    def test_min_volume_usd_custom(self):
        cfg = RiskConfig(min_volume_usd=50_000.0)
        assert cfg.min_volume_usd == 50_000.0

    def test_min_liquidity_usd_custom(self):
        cfg = RiskConfig(min_liquidity_usd=25_000.0)
        assert cfg.min_liquidity_usd == 25_000.0


class TestVolumeGate:
    """RiskController.check() rejects markets below min_volume_usd."""

    def test_volume_below_minimum_rejected(self):
        """Market with volume < min_volume_usd → DENY_VOLUME."""
        rc = _make_rc(min_volume_usd=50_000.0)
        verdict, reason = rc.check(
            market_id="m1", outcome="Yes", trade_usd=10.0,
            volume_usd=5_000.0,
        )
        assert verdict == RiskVerdict.DENY_VOLUME
        assert "5,000" in reason  # formatted with commas
        assert "50,000" in reason

    def test_volume_at_minimum_allowed(self):
        """Market with volume == min_volume_usd → ALLOW (edge inclusive)."""
        rc = _make_rc(min_volume_usd=50_000.0)
        verdict, _ = rc.check(
            market_id="m1", outcome="Yes", trade_usd=10.0,
            volume_usd=50_000.0,
        )
        assert verdict == RiskVerdict.ALLOW

    def test_volume_above_minimum_allowed(self):
        """Market with volume > min_volume_usd → ALLOW."""
        rc = _make_rc(min_volume_usd=10_000.0)
        verdict, _ = rc.check(
            market_id="m1", outcome="Yes", trade_usd=10.0,
            volume_usd=100_000.0,
        )
        assert verdict == RiskVerdict.ALLOW

    def test_volume_none_skips_gate(self):
        """When volume_usd=None, the volume gate is skipped (backwards compatible)."""
        rc = _make_rc(min_volume_usd=50_000.0)
        verdict, _ = rc.check(
            market_id="m1", outcome="Yes", trade_usd=10.0,
            volume_usd=None,
        )
        assert verdict == RiskVerdict.ALLOW


class TestLiquidityGate:
    """RiskController.check() rejects markets below min_liquidity_usd."""

    def test_liquidity_below_minimum_rejected(self):
        """Market with liquidity < min_liquidity_usd → DENY_LIQUIDITY."""
        rc = _make_rc(min_liquidity_usd=5_000.0)
        verdict, reason = rc.check(
            market_id="m1", outcome="Yes", trade_usd=10.0,
            liquidity_usd=500.0,
        )
        assert verdict == RiskVerdict.DENY_LIQUIDITY
        assert "500" in reason

    def test_liquidity_at_minimum_allowed(self):
        """Market with liquidity == min_liquidity_usd → ALLOW."""
        rc = _make_rc(min_liquidity_usd=5_000.0)
        verdict, _ = rc.check(
            market_id="m1", outcome="Yes", trade_usd=10.0,
            liquidity_usd=5_000.0,
        )
        assert verdict == RiskVerdict.ALLOW

    def test_liquidity_above_minimum_allowed(self):
        """Market with liquidity > min_liquidity_usd → ALLOW."""
        rc = _make_rc(min_liquidity_usd=5_000.0)
        verdict, _ = rc.check(
            market_id="m1", outcome="Yes", trade_usd=10.0,
            liquidity_usd=50_000.0,
        )
        assert verdict == RiskVerdict.ALLOW

    def test_liquidity_none_skips_gate(self):
        """When liquidity_usd=None, the liquidity gate is skipped."""
        rc = _make_rc(min_liquidity_usd=50_000.0)
        verdict, _ = rc.check(
            market_id="m1", outcome="Yes", trade_usd=10.0,
            liquidity_usd=None,
        )
        assert verdict == RiskVerdict.ALLOW


class TestVolumeAndLiquidityCombined:
    """Both gates can be active simultaneously."""

    def test_both_below_rejected_volume_first(self):
        """When both volume and liquidity are below minimum,
        volume gate fires first (checked before liquidity)."""
        rc = _make_rc(min_volume_usd=50_000.0, min_liquidity_usd=5_000.0)
        verdict, reason = rc.check(
            market_id="m1", outcome="Yes", trade_usd=10.0,
            volume_usd=1_000.0, liquidity_usd=500.0,
        )
        assert verdict == RiskVerdict.DENY_VOLUME

    def test_volume_ok_liquidity_low(self):
        """Volume OK but liquidity low → DENY_LIQUIDITY."""
        rc = _make_rc(min_volume_usd=10_000.0, min_liquidity_usd=5_000.0)
        verdict, reason = rc.check(
            market_id="m1", outcome="Yes", trade_usd=10.0,
            volume_usd=50_000.0, liquidity_usd=1_000.0,
        )
        assert verdict == RiskVerdict.DENY_LIQUIDITY

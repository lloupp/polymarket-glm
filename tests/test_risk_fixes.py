"""Tests for RiskController bug fixes — TDD.

Bugs fixed:
1. _peak_balance hardcoded to 10_000.0 instead of using initial_balance
2. Kill switch persistence not atomic — corrupted file crashes restore
3. Drawdown check is POST-trade instead of PRE-trade
4. KILL_SWITCH_FILE as instance attr renamed to _kill_switch_file
"""
import json
import time
import tempfile
from pathlib import Path

import pytest
from polymarket_glm.risk.controller import RiskController, RiskVerdict
from polymarket_glm.config import RiskConfig


def _tmp_kill_file() -> Path:
    return Path(tempfile.mkdtemp()) / "kill_switch.json"


# =====================================================================
# Bug 1: _peak_balance should use initial_balance, not hardcoded 10_000
# =====================================================================

class TestPeakBalanceFromInitialBalance:
    """_peak_balance must be initialized from initial_balance parameter."""

    def test_peak_balance_defaults_to_1000(self):
        """Default initial_balance should be 1_000 (paper_balance_usd default)."""
        rc = RiskController(kill_switch_file=_tmp_kill_file())
        assert rc._peak_balance == 1_000.0

    def test_peak_balance_uses_initial_balance(self):
        """When initial_balance=500, _peak_balance should be 500."""
        rc = RiskController(
            config=RiskConfig(),
            initial_balance=500.0,
            kill_switch_file=_tmp_kill_file(),
        )
        assert rc._peak_balance == 500.0

    def test_drawdown_calculation_uses_correct_peak(self):
        """Drawdown from $1000 peak, balance $900 = 10%, should trigger
        with 10% threshold, NOT from hardcoded $10_000 peak."""
        rc = RiskController(
            config=RiskConfig(
                drawdown_circuit_breaker_pct=0.10,
                drawdown_arm_period_sec=0.01,
                drawdown_min_observations=3,
            ),
            initial_balance=1_000.0,
            kill_switch_file=_tmp_kill_file(),
        )
        # Balance 900 = 10% drawdown from 1000 peak
        for _ in range(3):
            rc.update_balance(900.0)
        assert rc._kill_switch_active is True

    def test_no_drawdown_from_wrong_peak(self):
        """With initial_balance=1000, balance=9500 should NOT trigger
        because peak is 1000 not 10000. The old bug: peak was 10000,
        so $9500 balance = 5% drawdown (not 10%) wouldn't trigger,
        but $9500 > $1000 peak means balance is actually ABOVE peak."""
        rc = RiskController(
            config=RiskConfig(
                drawdown_circuit_breaker_pct=0.10,
                drawdown_arm_period_sec=0.01,
                drawdown_min_observations=3,
            ),
            initial_balance=1_000.0,
            kill_switch_file=_tmp_kill_file(),
        )
        # Balance 9500 is way above peak 1000
        for _ in range(3):
            rc.update_balance(9_500.0)
        assert rc._kill_switch_active is False
        assert rc._peak_balance == 9_500.0

    def test_status_shows_peak_balance(self):
        """status() should include peak_balance for observability."""
        rc = RiskController(
            initial_balance=500.0,
            kill_switch_file=_tmp_kill_file(),
        )
        s = rc.status()
        assert s["peak_balance"] == 500.0


# =====================================================================
# Bug 2: Corrupted kill_switch.json must not crash restore
# =====================================================================

class TestCorruptedKillSwitchFile:
    """_restore_kill_switch must handle corrupted files gracefully."""

    def test_corrupted_json_does_not_crash(self):
        """A file with invalid JSON should not raise."""
        tmp = _tmp_kill_file()
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text("{invalid json!!!")
        rc = RiskController(kill_switch_file=tmp)
        assert rc._kill_switch_active is False

    def test_empty_file_does_not_crash(self):
        """An empty file should not raise."""
        tmp = _tmp_kill_file()
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text("")
        rc = RiskController(kill_switch_file=tmp)
        assert rc._kill_switch_active is False

    def test_missing_keys_does_not_crash(self):
        """JSON with missing 'active' key should not raise."""
        tmp = _tmp_kill_file()
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"reason": "test but no active flag"}))
        rc = RiskController(kill_switch_file=tmp)
        assert rc._kill_switch_active is False

    def test_wrong_type_active_does_not_crash(self):
        """JSON with active='yes' (not bool) should not crash."""
        tmp = _tmp_kill_file()
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"active": "yes", "reason": "bad type"}))
        rc = RiskController(kill_switch_file=tmp)
        assert rc._kill_switch_active is False

    def test_valid_file_restores_kill_switch(self):
        """A valid kill switch file should be restored on init."""
        tmp = _tmp_kill_file()
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({
            "active": True,
            "reason": "drawdown exceeded",
            "activated_at": time.time() - 10,
        }))
        rc = RiskController(
            config=RiskConfig(kill_switch_cooldown_sec=900.0),
            kill_switch_file=tmp,
        )
        assert rc._kill_switch_active is True
        assert "drawdown" in rc._kill_switch_reason


# =====================================================================
# Bug 3: Drawdown check must be PRE-trade, not POST-trade
# =====================================================================

class TestDrawdownPreTrade:
    """check() must evaluate drawdown BEFORE allowing a trade."""

    def test_check_accepts_balance_parameter(self):
        """check() should accept an optional current_balance parameter
        to evaluate drawdown against projected post-trade balance.

        When projected post-trade drawdown >= threshold for
        min_observations consecutive checks, kill switch activates.
        """
        rc = RiskController(
            config=RiskConfig(
                drawdown_circuit_breaker_pct=0.10,
                drawdown_arm_period_sec=10.0,  # long enough to accumulate
                drawdown_min_observations=3,
                max_per_trade_usd=500.0,
                max_total_exposure_usd=10_000.0,
                max_per_market_exposure_usd=10_000.0,
                daily_loss_limit_usd=10_000.0,
            ),
            initial_balance=1_000.0,
            kill_switch_file=_tmp_kill_file(),
        )
        # Simulate balance already at $870 (13% drawdown from $1K peak).
        # Each check() with current_balance=870 + small trade_usd=1
        # → projected_balance=869 → 13.1% drawdown → observation accumulated
        for i in range(2):
            v, reason = rc.check(
                market_id="m1", outcome="Yes", trade_usd=1.0,
                current_balance=870.0,
            )
            assert v == RiskVerdict.DENY_DRAWDOWN, (
                f"Expected DENY_DRAWDOWN on observation {i+1}, got {v}: {reason}"
            )
        assert rc._kill_switch_active is False  # not yet 3 observations

        # 3rd observation → kill switch activates
        verdict, reason = rc.check(
            market_id="m1",
            outcome="Yes",
            trade_usd=1.0,
            current_balance=870.0,
        )
        assert verdict == RiskVerdict.KILL_SWITCH, (
            f"Expected KILL_SWITCH for 13% projected drawdown, got {verdict}: {reason}"
        )

    def test_pre_trade_denies_drawdown_without_current_balance(self):
        """Without current_balance, check() should still work
        (backwards compatible — no pre-trade drawdown check)."""
        rc = RiskController(
            config=RiskConfig(
                drawdown_circuit_breaker_pct=0.10,
                drawdown_arm_period_sec=0.01,
                drawdown_min_observations=3,
                max_per_trade_usd=500.0,
            ),
            initial_balance=1_000.0,
            kill_switch_file=_tmp_kill_file(),
        )
        # Without current_balance, no pre-trade drawdown check
        verdict, _ = rc.check(
            market_id="m1", outcome="Yes", trade_usd=50.0,
        )
        assert verdict == RiskVerdict.ALLOW

    def test_pre_trade_negative_balance_rejected(self):
        """A trade that would make balance negative should be denied."""
        rc = RiskController(
            config=RiskConfig(
                max_per_trade_usd=5000.0,
                max_total_exposure_usd=50_000.0,
                max_per_market_exposure_usd=50_000.0,
                daily_loss_limit_usd=50_000.0,
            ),
            initial_balance=1_000.0,
            kill_switch_file=_tmp_kill_file(),
        )
        verdict, reason = rc.check(
            market_id="m1", outcome="Yes", trade_usd=1_500.0,
            current_balance=1_000.0,
        )
        assert verdict == RiskVerdict.DENY_PER_TRADE
        assert "negative balance" in reason.lower()

    def test_deny_drawdown_verdict_before_kill_switch(self):
        """Before min_observations reached, DENY_DRAWDOWN is returned."""
        rc = RiskController(
            config=RiskConfig(
                drawdown_circuit_breaker_pct=0.10,
                drawdown_arm_period_sec=10.0,
                drawdown_min_observations=5,
                max_per_trade_usd=500.0,
                max_total_exposure_usd=10_000.0,
                max_per_market_exposure_usd=10_000.0,
                daily_loss_limit_usd=10_000.0,
            ),
            initial_balance=1_000.0,
            kill_switch_file=_tmp_kill_file(),
        )
        # Only 1 observation — not enough for kill switch
        verdict, reason = rc.check(
            market_id="m1", outcome="Yes", trade_usd=50.0,
            current_balance=850.0,  # 15% drawdown projected
        )
        assert verdict == RiskVerdict.DENY_DRAWDOWN
        assert "1/5" in reason  # observation count


# =====================================================================
# Bug 4: Kill switch persistence should be atomic (write-then-rename)
# =====================================================================

class TestAtomicPersistence:
    """Kill switch file must be written atomically to prevent corruption."""

    def test_persist_writes_valid_json(self):
        """After activate_kill_switch, the file should contain valid JSON."""
        tmp = _tmp_kill_file()
        rc = RiskController(
            config=RiskConfig(kill_switch_cooldown_sec=900.0),
            kill_switch_file=tmp,
        )
        rc.activate_kill_switch("test atomic write")
        data = json.loads(tmp.read_text())
        assert data["active"] is True
        assert data["reason"] == "test atomic write"

    def test_clear_removes_file(self):
        """After deactivate_kill_switch, the file should not exist."""
        tmp = _tmp_kill_file()
        rc = RiskController(kill_switch_file=tmp)
        rc.activate_kill_switch("will be cleared")
        assert tmp.exists()
        rc.deactivate_kill_switch()
        assert not tmp.exists()

    def test_persist_survives_concurrent_read(self):
        """Atomic write means the file is never partially written."""
        tmp = _tmp_kill_file()
        rc = RiskController(kill_switch_file=tmp)
        rc.activate_kill_switch("concurrent test")
        # File should always be valid JSON
        data = json.loads(tmp.read_text())
        assert data["active"] is True

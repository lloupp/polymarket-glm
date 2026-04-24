"""Tests for PositionManager — take-profit / stop-loss logic."""
import pytest

from polymarket_glm.models import Position, Side
from polymarket_glm.execution.position_manager import PositionManager, PositionManagerConfig


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def mgr() -> PositionManager:
    return PositionManager(PositionManagerConfig(
        tp_pct=0.50,  # 50% gain → TP
        sl_pct=0.50,  # 50% loss → SL
        min_hold_iterations=1,
    ))


def _open_yes(market_id="m1", avg_price=0.10, size=100.0, iteration=1) -> Position:
    return Position(
        market_id=market_id,
        outcome="Yes",
        size=size,
        avg_price=avg_price,
        status="open",
        opened_at_iteration=iteration,
    )


def _open_no(market_id="m2", avg_price=0.80, size=50.0, iteration=1) -> Position:
    return Position(
        market_id=market_id,
        outcome="No",
        size=size,
        avg_price=avg_price,
        status="open",
        opened_at_iteration=iteration,
    )


# ── should_close ──────────────────────────────────────────────

class TestShouldClose:
    def test_hold_when_price_unchanged(self, mgr):
        pos = _open_yes(avg_price=0.10)
        should, reason = mgr.should_close(pos, current_price=0.10, current_iteration=5)
        assert not should
        assert reason == "holding"

    def test_take_profit_yes(self, mgr):
        pos = _open_yes(avg_price=0.10)
        # 50% gain → TP at 0.15
        should, reason = mgr.should_close(pos, current_price=0.15, current_iteration=5)
        assert should
        assert reason == "take_profit"

    def test_stop_loss_yes(self, mgr):
        pos = _open_yes(avg_price=0.10)
        # 50% loss → SL at 0.05
        should, reason = mgr.should_close(pos, current_price=0.05, current_iteration=5)
        assert should
        assert reason == "stop_loss"

    def test_take_profit_no(self, mgr):
        pos = _open_no(avg_price=0.50)
        # NO: 50% gain when price rises → 0.50 * 1.5 = 0.75
        should, reason = mgr.should_close(pos, current_price=0.75, current_iteration=5)
        assert should
        assert reason == "take_profit"

    def test_stop_loss_no(self, mgr):
        pos = _open_no(avg_price=0.80)
        # NO: 50% loss → 0.80 * 0.5 = 0.40
        should, reason = mgr.should_close(pos, current_price=0.40, current_iteration=5)
        assert should
        assert reason == "stop_loss"

    def test_no_close_if_min_hold_not_reached(self, mgr):
        pos = _open_yes(avg_price=0.10, iteration=5)
        # Same iteration as open → min_hold_iterations=1 → don't close
        should, reason = mgr.should_close(pos, current_price=0.20, current_iteration=5)
        assert not should
        assert reason == "min_hold_not_reached"

    def test_no_close_if_already_closed(self, mgr):
        pos = _open_yes(avg_price=0.10)
        pos.status = "closed"
        should, reason = mgr.should_close(pos, current_price=0.20, current_iteration=5)
        assert not should
        assert reason == "already_closed"

    def test_no_close_invalid_entry_price(self, mgr):
        pos = _open_yes(avg_price=0.0)
        should, reason = mgr.should_close(pos, current_price=0.05, current_iteration=5)
        assert not should
        assert reason == "invalid_entry_price"

    def test_hold_between_tp_and_sl(self, mgr):
        pos = _open_yes(avg_price=0.10)
        # Price at 0.12 → 20% gain, not enough for TP (50%)
        should, reason = mgr.should_close(pos, current_price=0.12, current_iteration=5)
        assert not should
        assert reason == "holding"


# ── calculate_exit_order ─────────────────────────────────────

class TestCalculateExitOrder:
    def test_exit_order_structure(self, mgr):
        pos = _open_yes(avg_price=0.10, size=100.0)
        result = mgr.calculate_exit_order(pos, current_price=0.15, reason="take_profit", current_iteration=10)
        assert result["side"] == Side.SELL
        assert result["outcome"] == "Yes"
        assert result["price"] == 0.15
        assert result["size"] == 100.0
        assert result["_reason"] == "take_profit"
        assert result["_realized_pnl"] == pytest.approx(5.0)  # (0.15-0.10)*100

    def test_exit_order_loss(self, mgr):
        pos = _open_yes(avg_price=0.10, size=100.0)
        result = mgr.calculate_exit_order(pos, current_price=0.05, reason="stop_loss", current_iteration=10)
        assert result["_reason"] == "stop_loss"
        assert result["_realized_pnl"] == pytest.approx(-5.0)  # (0.05-0.10)*100


# ── set_targets ───────────────────────────────────────────────

class TestSetTargets:
    def test_set_targets_yes(self, mgr):
        pos = _open_yes(avg_price=0.10)
        mgr.set_targets(pos)
        assert pos.target_price == pytest.approx(0.15)  # 0.10 * 1.5
        assert pos.stop_loss_price == pytest.approx(0.05)  # 0.10 * 0.5

    def test_set_targets_no(self, mgr):
        pos = _open_no(avg_price=0.80)
        mgr.set_targets(pos)
        assert pos.target_price == pytest.approx(0.99)  # 0.80*1.5=1.20 → clamped to 0.99
        assert pos.stop_loss_price == pytest.approx(0.40)  # 0.80*0.5

    def test_set_targets_clamped(self, mgr):
        pos = _open_yes(avg_price=0.90)
        mgr.set_targets(pos)
        assert pos.target_price <= 0.99
        assert pos.stop_loss_price >= 0.01


# ── Realized P&L calculation ─────────────────────────────────

class TestRealizedPnL:
    def test_profit_yes(self, mgr):
        pos = _open_yes(avg_price=0.10, size=100.0)
        pnl = PositionManager._calculate_realized_pnl(pos, exit_price=0.15)
        assert pnl == pytest.approx(5.0)

    def test_loss_yes(self, mgr):
        pos = _open_yes(avg_price=0.10, size=100.0)
        pnl = PositionManager._calculate_realized_pnl(pos, exit_price=0.05)
        assert pnl == pytest.approx(-5.0)

    def test_breakeven(self, mgr):
        pos = _open_yes(avg_price=0.10, size=100.0)
        pnl = PositionManager._calculate_realized_pnl(pos, exit_price=0.10)
        assert pnl == pytest.approx(0.0)

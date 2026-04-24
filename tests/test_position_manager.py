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


# ── Trailing stop tests ──────────────────────────────────────

class TestTrailingStop:
    """Tests for trailing stop logic in PositionManager.should_close()."""

    @pytest.fixture
    def trail_mgr(self) -> PositionManager:
        """Manager with trailing stop config: 15% activation, 8% delta."""
        return PositionManager(PositionManagerConfig(
            tp_pct=0.50,
            sl_pct=0.50,
            min_hold_iterations=1,
            trailing_stop_activation_pct=0.15,
            trailing_stop_delta_pct=0.08,
        ))

    def test_trailing_stop_activates_after_high_water_mark(self, trail_mgr):
        """Price rises past activation threshold → trailing_activated becomes True."""
        pos = _open_yes(avg_price=0.10, iteration=1)
        # Entry 0.10, activation at 0.10 * 1.15 = 0.115
        # Price at 0.12 → HWM updates to 0.12, which is > 0.115 → trailing activates
        should, reason = trail_mgr.should_close(pos, current_price=0.12, current_iteration=5)
        assert pos.trailing_activated is True
        assert pos.high_water_mark == pytest.approx(0.12)
        # Should NOT close yet (price still rising)
        assert not should

    def test_trailing_stop_follows_high_water_mark(self, trail_mgr):
        """Price rises past activation, then drops past delta → triggers trailing_stop close."""
        pos = _open_yes(avg_price=0.10, iteration=1)
        # Step 1: Price rises to 0.12 → HWM=0.12, activation at 0.115 → trailing_activated=True
        # 0.12 is 20% gain, below TP (50%)
        trail_mgr.should_close(pos, current_price=0.12, current_iteration=5)
        assert pos.trailing_activated is True
        assert pos.high_water_mark == pytest.approx(0.12)

        # Step 2: Price drops to 0.111 → still above 0.12 * 0.92 = 0.1104 → hold
        should, reason = trail_mgr.should_close(pos, current_price=0.111, current_iteration=6)
        assert not should

        # Step 3: Price drops to 0.109 → below 0.12 * 0.92 = 0.1104 → trailing_stop
        should, reason = trail_mgr.should_close(pos, current_price=0.109, current_iteration=7)
        assert should
        assert reason == "trailing_stop"

    def test_trailing_stop_does_not_trigger_while_price_rising(self, trail_mgr):
        """Price only rising → trailing stop should never trigger close."""
        pos = _open_yes(avg_price=0.10, iteration=1)
        prices_rising = [0.11, 0.115, 0.12, 0.15, 0.20, 0.30, 0.50]
        for i, price in enumerate(prices_rising, start=5):
            should, reason = trail_mgr.should_close(pos, current_price=price, current_iteration=i)
            # Should never close due to trailing stop while price is rising
            if should:
                assert reason != "trailing_stop", f"Trailing stop triggered at price={price}"

    def test_trailing_stop_high_water_mark_updates_on_each_check(self, trail_mgr):
        """Verify HWM updates as price rises across multiple calls."""
        pos = _open_yes(avg_price=0.10, iteration=1)

        # Initially HWM should be 0 (default) or set to entry by set_targets
        # First call: price 0.11 → HWM becomes 0.11
        trail_mgr.should_close(pos, current_price=0.11, current_iteration=5)
        assert pos.high_water_mark == pytest.approx(0.11)

        # Second call: price 0.14 → HWM becomes 0.14
        trail_mgr.should_close(pos, current_price=0.14, current_iteration=6)
        assert pos.high_water_mark == pytest.approx(0.14)

        # Third call: price 0.13 → HWM stays 0.14 (price dropped, HWM doesn't decrease)
        trail_mgr.should_close(pos, current_price=0.13, current_iteration=7)
        assert pos.high_water_mark == pytest.approx(0.14)


class TestSetTargetsHighWaterMark:
    """Test that set_targets() initializes high_water_mark on new positions."""

    def test_set_targets_initializes_high_water_mark(self):
        mgr = PositionManager(PositionManagerConfig())
        pos = _open_yes(avg_price=0.10)
        mgr.set_targets(pos)
        assert pos.high_water_mark == pytest.approx(0.10)

    def test_set_targets_does_not_overwrite_existing_high_water_mark(self):
        """If HWM is already set higher, set_targets should not lower it."""
        mgr = PositionManager(PositionManagerConfig())
        pos = _open_yes(avg_price=0.10)
        pos.high_water_mark = 0.20
        mgr.set_targets(pos)
        # set_targets should set HWM to entry_price (0.10), but existing higher should be kept
        assert pos.high_water_mark == pytest.approx(0.20)

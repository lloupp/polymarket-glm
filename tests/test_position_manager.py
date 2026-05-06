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


# ── Market Auto-Close (Expiry) Tests ──────────────────────────

class TestExpiryAutoClose:
    def test_should_close_expired_market(self, mgr):
        """Position with end_date in the past should be closed."""
        from datetime import datetime, timezone, timedelta
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        pos = Position(
            market_id="m1", outcome="Yes", size=50.0,
            avg_price=0.60, status="open", opened_at_iteration=1,
            end_date_iso=past,
        )
        should, reason = mgr.should_close(pos, current_price=0.55, current_iteration=5)
        assert should
        assert reason == "expired"

    def test_should_not_close_future_market(self, mgr):
        """Position with end_date in the future should NOT be closed by expiry."""
        from datetime import datetime, timezone, timedelta
        future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        pos = Position(
            market_id="m1", outcome="Yes", size=50.0,
            avg_price=0.60, status="open", opened_at_iteration=1,
            end_date_iso=future,
        )
        should, reason = mgr.should_close(pos, current_price=0.55, current_iteration=5)
        assert not should

    def test_no_expiry_check_without_end_date(self, mgr):
        """Position without end_date_iso should skip expiry check."""
        pos = Position(
            market_id="m1", outcome="Yes", size=50.0,
            avg_price=0.60, status="open", opened_at_iteration=1,
            end_date_iso="",
        )
        should, reason = mgr.should_close(pos, current_price=0.60, current_iteration=5)
        assert not should  # no edge → holding

    def test_invalid_end_date_doesnt_crash(self, mgr):
        """Invalid end_date_iso should be handled gracefully."""
        pos = Position(
            market_id="m1", outcome="Yes", size=50.0,
            avg_price=0.60, status="open", opened_at_iteration=1,
            end_date_iso="not-a-date",
        )
        should, reason = mgr.should_close(pos, current_price=0.60, current_iteration=5)
        assert not should  # no crash, falls through to holding

    def test_expired_skips_min_hold_check(self, mgr):
        """Expired market should close even if min_hold_iterations not reached."""
        from datetime import datetime, timezone, timedelta
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        pos = Position(
            market_id="m1", outcome="Yes", size=50.0,
            avg_price=0.60, status="open", opened_at_iteration=5,
            end_date_iso=past,
        )
        # Same iteration → min_hold not reached, but expiry overrides
        should, reason = mgr.should_close(pos, current_price=0.55, current_iteration=5)
        assert should
        assert reason == "expired"


class TestFindExpiredPositions:
    def test_find_expired_batch(self, mgr):
        """find_expired_positions returns only open positions past end_date."""
        from datetime import datetime, timezone, timedelta
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        positions = [
            Position(market_id="m1", outcome="Yes", size=50, avg_price=0.5,
                     status="open", end_date_iso=past),
            Position(market_id="m2", outcome="No", size=30, avg_price=0.4,
                     status="open", end_date_iso=future),
            Position(market_id="m3", outcome="Yes", size=20, avg_price=0.6,
                     status="open", end_date_iso=""),
            Position(market_id="m4", outcome="Yes", size=10, avg_price=0.3,
                     status="closed", end_date_iso=past),
        ]
        expired = mgr.find_expired_positions(positions)
        assert len(expired) == 1
        assert expired[0].market_id == "m1"

    def test_find_expired_none(self, mgr):
        """No expired positions returns empty list."""
        from datetime import datetime, timezone, timedelta
        future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        positions = [
            Position(market_id="m1", outcome="Yes", size=50, avg_price=0.5,
                     status="open", end_date_iso=future),
        ]
        expired = mgr.find_expired_positions(positions)
        assert expired == []

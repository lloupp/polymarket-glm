"""Tests for SettlementTracker — detect resolved markets and close positions."""
import pytest
from datetime import datetime

from polymarket_glm.models import Position
from polymarket_glm.execution.settlement_tracker import (
    SettlementTracker,
    SettlementResult,
    SettlementSummary,
)


class TestSettlementResult:
    def test_is_profitable_win(self):
        r = SettlementResult(
            market_id="m1", outcome="Yes", size=100, avg_price=0.60,
            settlement_price=1.0, realized_pnl=40.0, proceeds=100.0,
            winning_outcome="Yes",
        )
        assert r.is_profitable is True

    def test_is_profitable_loss(self):
        r = SettlementResult(
            market_id="m1", outcome="Yes", size=100, avg_price=0.60,
            settlement_price=0.0, realized_pnl=-60.0, proceeds=0.0,
            winning_outcome="No",
        )
        assert r.is_profitable is False

    def test_is_profitable_breakeven(self):
        r = SettlementResult(
            market_id="m1", outcome="Yes", size=100, avg_price=1.0,
            settlement_price=1.0, realized_pnl=0.0, proceeds=100.0,
            winning_outcome="Yes",
        )
        assert r.is_profitable is False


class TestSettlementSummary:
    def test_defaults(self):
        s = SettlementSummary()
        assert s.num_settled == 0
        assert s.total_realized_pnl == 0.0
        assert s.settlements == []


class TestSettlementTracker:
    def test_no_resolved_markets(self):
        tracker = SettlementTracker()
        positions = [
            Position(market_id="m1", outcome="Yes", size=100, avg_price=0.50),
        ]
        summary = tracker.check_settlements(positions, resolved_markets={})
        assert summary.num_settled == 0
        assert summary.total_realized_pnl == 0.0

    def test_winning_yes_position(self):
        tracker = SettlementTracker()
        positions = [
            Position(market_id="m1", outcome="Yes", size=100, avg_price=0.60),
        ]
        summary = tracker.check_settlements(
            positions, resolved_markets={"m1": "Yes"},
        )
        assert summary.num_settled == 1
        # settlement_price=1.0, proceeds=100, cost=60, pnl=40
        assert summary.total_realized_pnl == 40.0
        assert summary.settlements[0].settlement_price == 1.0
        assert summary.settlements[0].proceeds == 100.0

    def test_losing_yes_position(self):
        tracker = SettlementTracker()
        positions = [
            Position(market_id="m1", outcome="Yes", size=100, avg_price=0.60),
        ]
        summary = tracker.check_settlements(
            positions, resolved_markets={"m1": "No"},
        )
        assert summary.num_settled == 1
        # settlement_price=0.0, proceeds=0, cost=60, pnl=-60
        assert summary.total_realized_pnl == -60.0
        assert summary.settlements[0].settlement_price == 0.0
        assert summary.settlements[0].proceeds == 0.0

    def test_winning_no_position(self):
        tracker = SettlementTracker()
        positions = [
            Position(market_id="m1", outcome="No", size=100, avg_price=0.40),
        ]
        summary = tracker.check_settlements(
            positions, resolved_markets={"m1": "No"},
        )
        # No wins → settlement_price=1.0, proceeds=100, cost=40, pnl=60
        assert summary.total_realized_pnl == 60.0

    def test_multiple_positions_mixed(self):
        tracker = SettlementTracker()
        positions = [
            Position(market_id="m1", outcome="Yes", size=100, avg_price=0.60),
            Position(market_id="m2", outcome="Yes", size=50, avg_price=0.80),
        ]
        summary = tracker.check_settlements(
            positions, resolved_markets={"m1": "Yes", "m2": "No"},
        )
        # m1 wins: pnl=40, m2 loses: pnl=-40
        assert summary.num_settled == 2
        assert summary.total_realized_pnl == pytest.approx(0.0, abs=0.01)

    def test_no_duplicate_settlement(self):
        tracker = SettlementTracker()
        positions = [
            Position(market_id="m1", outcome="Yes", size=100, avg_price=0.50),
        ]
        # First settlement
        s1 = tracker.check_settlements(positions, {"m1": "Yes"})
        assert s1.num_settled == 1
        # Second call with same market — should not re-settle
        s2 = tracker.check_settlements(positions, {"m1": "Yes"})
        assert s2.num_settled == 0

    def test_is_market_settled(self):
        tracker = SettlementTracker()
        assert tracker.is_market_settled("m1") is False
        positions = [Position(market_id="m1", outcome="Yes", size=10, avg_price=0.5)]
        tracker.check_settlements(positions, {"m1": "Yes"})
        assert tracker.is_market_settled("m1") is True

    def test_total_realized_pnl_accumulates(self):
        tracker = SettlementTracker()
        pos1 = [Position(market_id="m1", outcome="Yes", size=100, avg_price=0.60)]
        tracker.check_settlements(pos1, {"m1": "Yes"})
        assert tracker.total_realized_pnl == 40.0

        pos2 = [Position(market_id="m2", outcome="Yes", size=50, avg_price=0.80)]
        tracker.check_settlements(pos2, {"m2": "No"})
        assert tracker.total_realized_pnl == 0.0  # 40 + (-40) = 0

    def test_settlement_history(self):
        tracker = SettlementTracker()
        positions = [
            Position(market_id="m1", outcome="Yes", size=100, avg_price=0.50),
        ]
        tracker.check_settlements(positions, {"m1": "Yes"})
        assert len(tracker.settlement_history) == 1
        assert tracker.settlement_history[0].market_id == "m1"

    def test_reset(self):
        tracker = SettlementTracker()
        positions = [Position(market_id="m1", outcome="Yes", size=10, avg_price=0.5)]
        tracker.check_settlements(positions, {"m1": "Yes"})
        assert tracker.total_realized_pnl == 5.0
        tracker.reset()
        assert tracker.total_realized_pnl == 0.0
        assert len(tracker.settlement_history) == 0
        assert tracker.is_market_settled("m1") is False

    def test_unresolved_market_skipped(self):
        tracker = SettlementTracker()
        positions = [
            Position(market_id="m1", outcome="Yes", size=100, avg_price=0.50),
            Position(market_id="m2", outcome="Yes", size=50, avg_price=0.30),
        ]
        # Only m1 resolved
        summary = tracker.check_settlements(positions, {"m1": "Yes"})
        assert summary.num_settled == 1
        # m2 should still be open
        assert not tracker.is_market_settled("m2")

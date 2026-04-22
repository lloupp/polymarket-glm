"""Tests for PortfolioTracker — mark-to-market P&L."""
import pytest
from datetime import datetime

from polymarket_glm.models import Position
from polymarket_glm.execution.portfolio_tracker import (
    PortfolioTracker,
    PositionPnL,
    PortfolioSummary,
)


class TestPositionPnL:
    """Tests for PositionPnL dataclass."""

    def test_is_profitable_when_positive(self):
        pnl = PositionPnL(
            market_id="m1", outcome="Yes", size=10, avg_price=0.30,
            current_price=0.50, unrealized_pnl=2.0,
            unrealized_pnl_pct=66.67, cost_basis=3.0, market_value=5.0,
        )
        assert pnl.is_profitable is True

    def test_is_not_profitable_when_negative(self):
        pnl = PositionPnL(
            market_id="m1", outcome="Yes", size=10, avg_price=0.50,
            current_price=0.30, unrealized_pnl=-2.0,
            unrealized_pnl_pct=-40.0, cost_basis=5.0, market_value=3.0,
        )
        assert pnl.is_profitable is False

    def test_is_not_profitable_when_zero(self):
        pnl = PositionPnL(
            market_id="m1", outcome="Yes", size=10, avg_price=0.40,
            current_price=0.40, unrealized_pnl=0.0,
            unrealized_pnl_pct=0.0, cost_basis=4.0, market_value=4.0,
        )
        assert pnl.is_profitable is False


class TestPortfolioSummary:
    """Tests for PortfolioSummary dataclass."""

    def test_unrealized_pnl_pct_positive(self):
        summary = PortfolioSummary(
            balance_usd=9000, total_cost_basis=1000,
            total_market_value=1200, unrealized_pnl=200,
        )
        assert summary.unrealized_pnl_pct == 20.0

    def test_unrealized_pnl_pct_zero_cost(self):
        summary = PortfolioSummary(
            balance_usd=10000, total_cost_basis=0,
            total_market_value=0, unrealized_pnl=0,
        )
        assert summary.unrealized_pnl_pct == 0.0

    def test_total_pnl_pct(self):
        summary = PortfolioSummary(
            balance_usd=9500, total_cost_basis=1000,
            total_market_value=1100, unrealized_pnl=100,
            realized_pnl=50, total_pnl=150,
        )
        # initial = 9500 + 1000 = 10500, pct = 150/10500 * 100 ≈ 1.43
        assert abs(summary.total_pnl_pct - 1.4286) < 0.1

    def test_total_pnl_pct_zero_initial(self):
        summary = PortfolioSummary(
            balance_usd=0, total_cost_basis=0,
        )
        assert summary.total_pnl_pct == 0.0


class TestPortfolioTracker:
    """Tests for PortfolioTracker."""

    def test_empty_portfolio(self):
        tracker = PortfolioTracker()
        summary = tracker.calculate(
            positions=[], price_lookup={}, balance_usd=10000,
        )
        assert summary.num_open_positions == 0
        assert summary.unrealized_pnl == 0.0
        assert summary.balance_usd == 10000
        assert summary.total_cost_basis == 0.0
        assert summary.total_market_value == 0.0

    def test_single_position_profit(self):
        tracker = PortfolioTracker()
        positions = [
            Position(market_id="m1", outcome="Yes", size=100, avg_price=0.30),
        ]
        summary = tracker.calculate(
            positions=positions,
            price_lookup={"m1": 0.50},
            balance_usd=9700,
        )
        assert summary.num_open_positions == 1
        # cost = 100 * 0.30 = 30, value = 100 * 0.50 = 50, pnl = 20
        assert summary.total_cost_basis == 30.0
        assert summary.total_market_value == 50.0
        assert summary.unrealized_pnl == 20.0
        assert summary.positions[0].unrealized_pnl_pct == pytest.approx(66.67, rel=0.01)

    def test_single_position_loss(self):
        tracker = PortfolioTracker()
        positions = [
            Position(market_id="m1", outcome="Yes", size=100, avg_price=0.60),
        ]
        summary = tracker.calculate(
            positions=positions,
            price_lookup={"m1": 0.40},
            balance_usd=9400,
        )
        # cost = 60, value = 40, pnl = -20
        assert summary.unrealized_pnl == -20.0
        assert summary.positions[0].unrealized_pnl_pct == pytest.approx(-33.33, rel=0.01)

    def test_multiple_positions(self):
        tracker = PortfolioTracker()
        positions = [
            Position(market_id="m1", outcome="Yes", size=50, avg_price=0.40),
            Position(market_id="m2", outcome="No", size=100, avg_price=0.20),
        ]
        summary = tracker.calculate(
            positions=positions,
            price_lookup={"m1": 0.50, "m2": 0.10},
            balance_usd=9700,
        )
        # m1: cost=20, value=25, pnl=5
        # m2: cost=20, value=10, pnl=-10
        assert summary.num_open_positions == 2
        assert summary.total_cost_basis == 40.0
        assert summary.total_market_value == 35.0
        assert summary.unrealized_pnl == -5.0

    def test_missing_price_uses_avg_price(self):
        """If market_id not in price_lookup, fallback to avg_price (P&L=0)."""
        tracker = PortfolioTracker()
        positions = [
            Position(market_id="m_unknown", outcome="Yes", size=50, avg_price=0.40),
        ]
        summary = tracker.calculate(
            positions=positions,
            price_lookup={},
            balance_usd=9800,
        )
        # current_price defaults to avg_price → pnl = 0
        assert summary.unrealized_pnl == 0.0
        assert summary.positions[0].current_price == 0.40

    def test_with_realized_pnl(self):
        tracker = PortfolioTracker()
        positions = [
            Position(market_id="m1", outcome="Yes", size=100, avg_price=0.30),
        ]
        summary = tracker.calculate(
            positions=positions,
            price_lookup={"m1": 0.50},
            balance_usd=9700,
            realized_pnl=15.0,
        )
        assert summary.realized_pnl == 15.0
        assert summary.total_pnl == 35.0  # 20 unrealized + 15 realized

    def test_last_summary_stored(self):
        tracker = PortfolioTracker()
        assert tracker.last_summary is None
        summary = tracker.calculate(
            positions=[], price_lookup={}, balance_usd=10000,
        )
        assert tracker.last_summary is summary

    def test_get_position_pnl(self):
        tracker = PortfolioTracker()
        pos = Position(market_id="m1", outcome="Yes", size=100, avg_price=0.30)
        pnl = tracker.get_position_pnl(pos, current_price=0.50)
        assert pnl.unrealized_pnl == 20.0
        assert pnl.unrealized_pnl_pct == pytest.approx(66.67, rel=0.01)
        assert pnl.is_profitable is True

    def test_zero_avg_price_no_division_error(self):
        tracker = PortfolioTracker()
        pos = Position(market_id="m1", outcome="Yes", size=0, avg_price=0.0)
        pnl = tracker.get_position_pnl(pos, current_price=0.50)
        assert pnl.unrealized_pnl_pct == 0.0
        assert pnl.unrealized_pnl == 0.0

    def test_portfolio_summary_defaults(self):
        summary = PortfolioSummary()
        assert summary.balance_usd == 0.0
        assert summary.positions == []
        assert summary.num_open_positions == 0
        assert isinstance(summary.timestamp, datetime)

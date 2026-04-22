"""Tests for daily report generation."""
import pytest

from polymarket_glm.execution.portfolio_tracker import PortfolioSummary, PositionPnL
from polymarket_glm.execution.settlement_tracker import SettlementTracker, SettlementResult
from polymarket_glm.models import Position
from polymarket_glm.monitoring.daily_report import format_daily_report, format_pnl_alert


class TestFormatDailyReport:
    def test_empty_portfolio(self):
        portfolio = PortfolioSummary(balance_usd=10000)
        settlement = SettlementTracker()
        report = format_daily_report(portfolio, settlement)
        assert "Daily Report" in report
        assert "$10,000" in report or "10000" in report
        assert "Unrealized P&L: $0.00" in report

    def test_with_positions(self):
        portfolio = PortfolioSummary(
            balance_usd=9500,
            unrealized_pnl=100.0,
            total_cost_basis=500,
            positions=[
                PositionPnL(
                    market_id="m1", outcome="Yes", size=100, avg_price=0.30,
                    current_price=0.50, unrealized_pnl=20.0,
                    unrealized_pnl_pct=66.67, cost_basis=30, market_value=50,
                ),
            ],
            num_open_positions=1,
        )
        settlement = SettlementTracker()
        report = format_daily_report(portfolio, settlement)
        assert "🟢" in report  # profitable position
        assert "Unrealized P&L: $100.00" in report

    def test_with_loss_position(self):
        portfolio = PortfolioSummary(
            balance_usd=9500,
            unrealized_pnl=-50.0,
            total_cost_basis=500,
            positions=[
                PositionPnL(
                    market_id="m2", outcome="Yes", size=100, avg_price=0.50,
                    current_price=0.30, unrealized_pnl=-20.0,
                    unrealized_pnl_pct=-40.0, cost_basis=50, market_value=30,
                ),
            ],
            num_open_positions=1,
        )
        settlement = SettlementTracker()
        report = format_daily_report(portfolio, settlement)
        assert "🔴" in report

    def test_with_settlements(self):
        portfolio = PortfolioSummary(balance_usd=10000)
        settlement = SettlementTracker()
        # Manually add a settlement
        settlement._settlement_history.append(
            SettlementResult(
                market_id="m1", outcome="Yes", size=100, avg_price=0.50,
                settlement_price=1.0, realized_pnl=50.0, proceeds=100.0,
                winning_outcome="Yes",
            )
        )
        settlement._total_realized_pnl = 50.0
        report = format_daily_report(portfolio, settlement)
        assert "✅" in report
        assert "Realized P&L: $50.00" in report

    def test_kill_switch_alert(self):
        portfolio = PortfolioSummary(balance_usd=10000)
        settlement = SettlementTracker()
        report = format_daily_report(
            portfolio, settlement, kill_switch_active=True,
        )
        assert "KILL SWITCH" in report

    def test_with_stats(self):
        portfolio = PortfolioSummary(balance_usd=10000)
        settlement = SettlementTracker()
        report = format_daily_report(
            portfolio, settlement,
            total_trades=5, total_signals=10, total_rejections=2,
        )
        assert "Signals: 10" in report
        assert "Fills: 5" in report

    def test_max_10_positions(self):
        positions = [
            PositionPnL(
                market_id=f"m{i}", outcome="Yes", size=10, avg_price=0.50,
                current_price=0.50, unrealized_pnl=0.0,
                unrealized_pnl_pct=0.0, cost_basis=5, market_value=5,
            )
            for i in range(15)
        ]
        portfolio = PortfolioSummary(
            balance_usd=10000, positions=positions, num_open_positions=15,
        )
        settlement = SettlementTracker()
        report = format_daily_report(portfolio, settlement)
        assert "+5 more" in report


class TestFormatPnlAlert:
    def test_no_alert_below_threshold(self):
        portfolio = PortfolioSummary(
            balance_usd=10000, unrealized_pnl=20, total_cost_basis=1000,
        )
        # 2% < 5% threshold
        assert format_pnl_alert(portfolio, threshold_pct=5.0) is None

    def test_profit_alert(self):
        portfolio = PortfolioSummary(
            balance_usd=10000, unrealized_pnl=100, total_cost_basis=1000,
            num_open_positions=3,
        )
        # 10% > 5%
        alert = format_pnl_alert(portfolio, threshold_pct=5.0)
        assert alert is not None
        assert "📈" in alert
        assert "$+100" in alert

    def test_loss_alert(self):
        portfolio = PortfolioSummary(
            balance_usd=10000, unrealized_pnl=-80, total_cost_basis=1000,
            num_open_positions=2,
        )
        # -8% < -5%
        alert = format_pnl_alert(portfolio, threshold_pct=5.0)
        assert alert is not None
        assert "📉" in alert

    def test_zero_threshold_always_alerts(self):
        portfolio = PortfolioSummary(
            balance_usd=10000, unrealized_pnl=1, total_cost_basis=1000,
        )
        alert = format_pnl_alert(portfolio, threshold_pct=0.0)
        assert alert is not None

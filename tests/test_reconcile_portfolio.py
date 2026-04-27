"""Tests for PaperExecutor reconciliation and accounting.

Validates:
- reconcile_portfolio() detects inconsistencies
- Fee tracking is accurate
- Realized P&L is tracked correctly
- cash + positions_cost = total_equity is invariant
"""
import pytest
from polymarket_glm.execution.paper_executor import (
    PaperExecutor,
    PortfolioMismatchError,
)
from polymarket_glm.execution.exchange import OrderRequest
from polymarket_glm.models import Side


def _buy(executor: PaperExecutor, market_id: str, outcome: str,
         price: float, size: float, iteration: int = 1) -> OrderRequest:
    req = OrderRequest(
        market_id=market_id,
        side=Side.BUY,
        outcome=outcome,
        price=price,
        size=size,
        iteration=iteration,
    )
    fill = executor.submit_order_sync(req)
    assert fill.filled, f"Buy fill failed: {fill.reason}"
    return req


def _sell(executor: PaperExecutor, market_id: str, outcome: str,
          price: float, size: float, close_reason: str = "") -> OrderRequest:
    req = OrderRequest(
        market_id=market_id,
        side=Side.SELL,
        outcome=outcome,
        price=price,
        size=size,
        iteration=2,
        close_reason=close_reason,
    )
    fill = executor.submit_order_sync(req)
    assert fill.filled, f"Sell fill failed: {fill.reason}"
    return req


# =====================================================================
# Basic reconciliation — single buy
# =====================================================================

class TestReconcileSingleBuy:
    """After buying, cash + position cost = initial_balance - fees."""

    def test_single_buy_reconciles(self):
        ex = PaperExecutor(initial_balance=1_000.0, fee_rate_bps=100)
        # Buy 100 shares @ $0.60 = $60 cost + $0.60 fee = $60.60 total
        _buy(ex, "m1", "Yes", 0.60, 100.0)

        result = ex.reconcile_portfolio()
        # Cash: 1000 - 60 - 0.60 = 939.40
        # Position cost: 100 * 0.60 = 60.00
        # Total equity: 999.40
        # Expected equity: 1000 + 0 (no realized PnL) - 0.60 (fees) = 999.40
        assert result["consistent"], (
            f"Portfolio inconsistent: {result}"
        )
        assert result["cash"] == 939.40
        assert result["positions_cost"] == 60.0
        assert result["fees_paid"] == 0.60

    def test_multiple_buys_reconcile(self):
        ex = PaperExecutor(initial_balance=1_000.0, fee_rate_bps=100)
        _buy(ex, "m1", "Yes", 0.60, 50.0)   # $30 + $0.30 fee
        _buy(ex, "m2", "No", 0.40, 75.0)     # $30 + $0.30 fee
        _buy(ex, "m1", "Yes", 0.65, 50.0)    # $32.50 + $0.325 fee

        result = ex.reconcile_portfolio()
        assert result["consistent"], f"Portfolio inconsistent: {result}"
        assert result["trades_count"] == 3
        assert abs(result["fees_paid"] - 0.925) < 0.001


# =====================================================================
# Buy + Sell reconciliation
# =====================================================================

class TestReconcileBuySell:
    """After buying and selling, realized P&L is tracked."""

    def test_profitable_sell_reconciles(self):
        ex = PaperExecutor(initial_balance=1_000.0, fee_rate_bps=100)
        _buy(ex, "m1", "Yes", 0.50, 100.0)    # $50 + $0.50 fee
        _sell(ex, "m1", "Yes", 0.80, 100.0)    # $80 - $0.80 fee

        result = ex.reconcile_portfolio()
        assert result["consistent"], f"Portfolio inconsistent: {result}"
        # Realized PnL: (0.80 - 0.50) * 100 = $30.00
        assert result["realized_pnl"] == 30.0
        # Fees: 0.50 (buy) + 0.80 (sell) = 1.30
        assert abs(result["fees_paid"] - 1.30) < 0.001
        # Cash: 1000 - 50.50 + 79.20 = 1028.70
        # Expected: 1000 + 30.00 - 1.30 = 1028.70 ✓
        assert result["cash"] == 1028.70

    def test_loss_sell_reconciles(self):
        ex = PaperExecutor(initial_balance=1_000.0, fee_rate_bps=100)
        _buy(ex, "m1", "Yes", 0.80, 100.0)    # $80 + $0.80 fee
        _sell(ex, "m1", "Yes", 0.50, 100.0)    # $50 - $0.50 fee

        result = ex.reconcile_portfolio()
        assert result["consistent"], f"Portfolio inconsistent: {result}"
        # Realized PnL: (0.50 - 0.80) * 100 = -$30.00
        assert result["realized_pnl"] == -30.0
        # Fees: 0.80 + 0.50 = 1.30
        assert abs(result["fees_paid"] - 1.30) < 0.001

    def test_partial_sell_reconciles(self):
        ex = PaperExecutor(initial_balance=1_000.0, fee_rate_bps=100)
        _buy(ex, "m1", "Yes", 0.50, 100.0)    # $50 + $0.50 fee
        _sell(ex, "m1", "Yes", 0.70, 50.0)     # $35 - $0.35 fee (partial)

        result = ex.reconcile_portfolio()
        assert result["consistent"], f"Portfolio inconsistent: {result}"
        # Realized PnL: (0.70 - 0.50) * 50 = $10.00
        assert result["realized_pnl"] == 10.0
        # Position remaining: 50 shares @ $0.50 = $25
        assert result["positions_cost"] == 25.0

    def test_multiple_markets_reconcile(self):
        ex = PaperExecutor(initial_balance=1_000.0, fee_rate_bps=100)
        _buy(ex, "m1", "Yes", 0.60, 50.0)     # $30 + $0.30
        _buy(ex, "m2", "No", 0.35, 100.0)     # $35 + $0.35
        _sell(ex, "m1", "Yes", 0.80, 50.0)     # $40 - $0.40, PnL = $10

        result = ex.reconcile_portfolio()
        assert result["consistent"], f"Portfolio inconsistent: {result}"
        assert result["realized_pnl"] == 10.0
        assert result["trades_count"] == 3


# =====================================================================
# Discrepancy detection
# =====================================================================

class TestReconcileDiscrepancyDetection:
    """reconcile_portfolio() should detect when numbers don't add up."""

    def test_tampered_balance_detected(self):
        """If balance is manually tampered, discrepancy is detected."""
        ex = PaperExecutor(initial_balance=1_000.0, fee_rate_bps=100)
        _buy(ex, "m1", "Yes", 0.50, 100.0)

        # Tamper with balance (simulating a bug)
        ex._balance += 100.0  # ghost money

        result = ex.reconcile_portfolio()
        assert not result["consistent"], (
            f"Should detect tampered balance: {result}"
        )
        assert result["discrepancy"] == 100.0

    def test_clean_portfolio_is_consistent(self):
        """Clean portfolio with no tampering should be consistent."""
        ex = PaperExecutor(initial_balance=1_000.0, fee_rate_bps=100)
        _buy(ex, "m1", "Yes", 0.50, 100.0)
        _sell(ex, "m1", "Yes", 0.70, 100.0)
        _buy(ex, "m2", "No", 0.40, 50.0)

        result = ex.reconcile_portfolio()
        assert result["consistent"], f"Should be consistent: {result}"


# =====================================================================
# Edge cases
# =====================================================================

class TestReconcileEdgeCases:
    """Edge cases for reconciliation."""

    def test_empty_portfolio(self):
        """No trades → cash = initial, positions = 0."""
        ex = PaperExecutor(initial_balance=1_000.0, fee_rate_bps=100)
        result = ex.reconcile_portfolio()
        assert result["consistent"]
        assert result["cash"] == 1_000.0
        assert result["positions_cost"] == 0.0
        assert result["trades_count"] == 0

    def test_full_spend_reconciles(self):
        """Spending most of the balance should still reconcile."""
        ex = PaperExecutor(initial_balance=100.0, fee_rate_bps=100)
        # Buy 95 shares @ $0.99 = $94.05 + $0.9405 fee = $94.9905
        _buy(ex, "m1", "Yes", 0.99, 95.0)

        result = ex.reconcile_portfolio()
        assert result["consistent"], f"Full spend should reconcile: {result}"

    def test_zero_fee_reconciles(self):
        """With zero fees, equity should equal initial + PnL."""
        ex = PaperExecutor(initial_balance=1_000.0, fee_rate_bps=0)
        _buy(ex, "m1", "Yes", 0.50, 100.0)
        _sell(ex, "m1", "Yes", 0.70, 100.0)

        result = ex.reconcile_portfolio()
        assert result["consistent"], f"Zero fee should reconcile: {result}"
        assert result["fees_paid"] == 0.0
        # Cash: 1000 - 50 + 70 = 1020
        # Expected: 1000 + 20 - 0 = 1020 ✓
        assert result["cash"] == 1020.0

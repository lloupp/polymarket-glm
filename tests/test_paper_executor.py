"""Tests for paper executor."""
import pytest
from polymarket_glm.execution.paper_executor import PaperExecutor
from polymarket_glm.execution.exchange import OrderRequest, CancelResult
from polymarket_glm.models import Side, Account


@pytest.fixture
def executor():
    return PaperExecutor(initial_balance=10_000.0, fee_rate_bps=100)


def test_initial_balance(executor):
    acct = executor.account
    assert acct.balance_usd == 10_000.0
    assert acct.total_exposure_usd == 0.0


def test_buy_fill(executor):
    req = OrderRequest(
        market_id="m1", side=Side.BUY, outcome="Yes",
        price=0.60, size=100.0,
    )
    result = executor.submit_order_sync(req)
    assert result.filled is True
    assert result.fee > 0
    assert result.size == 100.0
    # Balance should decrease by cost + fee
    expected_cost = 0.60 * 100.0 + result.fee
    assert executor.account.balance_usd == pytest.approx(10_000.0 - expected_cost)


def test_sell_fill(executor):
    # First buy to have position
    buy = OrderRequest(market_id="m1", side=Side.BUY, outcome="Yes", price=0.50, size=100.0)
    executor.submit_order_sync(buy)
    # Now sell
    sell = OrderRequest(market_id="m1", side=Side.SELL, outcome="Yes", price=0.55, size=50.0)
    result = executor.submit_order_sync(sell)
    assert result.filled is True


def test_fee_calculation(executor):
    """Polymarket fee: 1% on profit, 2% on full amount for taker.
    Our paper executor uses fee_rate_bps for simplicity."""
    req = OrderRequest(
        market_id="m1", side=Side.BUY, outcome="Yes",
        price=0.50, size=200.0,
    )
    result = executor.submit_order_sync(req)
    # Fee = 0.50 * 200 * 100/10000 = 1.0
    assert result.fee == pytest.approx(1.0)


def test_insufficient_balance(executor):
    req = OrderRequest(
        market_id="m1", side=Side.BUY, outcome="Yes",
        price=0.90, size=20_000.0,  # way more than balance
    )
    result = executor.submit_order_sync(req)
    assert result.filled is False
    assert "insufficient" in result.reason.lower()


def test_cancel_order(executor):
    result = executor.cancel_order_sync("nonexistent")
    assert result.success is False


def test_position_tracking(executor):
    executor.submit_order_sync(OrderRequest(
        market_id="m1", side=Side.BUY, outcome="Yes", price=0.60, size=100.0,
    ))
    executor.submit_order_sync(OrderRequest(
        market_id="m2", side=Side.BUY, outcome="No", price=0.30, size=50.0,
    ))
    pos_m1 = executor.get_position("m1", "Yes")
    assert pos_m1 is not None
    assert pos_m1.size == 100.0
    pos_m2 = executor.get_position("m2", "No")
    assert pos_m2 is not None
    assert pos_m2.size == 50.0


def test_account_state(executor):
    acct = executor.account
    assert isinstance(acct, Account)
    assert acct.balance_usd == 10_000.0


# ── Position Dedup Tests ────────────────────────────────────

def test_dedup_rejects_duplicate_buy_same_market_outcome(executor):
    """Second BUY for the same market_id+outcome should be rejected."""
    req1 = OrderRequest(market_id="m1", side=Side.BUY, outcome="Yes", price=0.50, size=100.0)
    fill1 = executor.submit_order_sync(req1)
    assert fill1.filled is True

    req2 = OrderRequest(market_id="m1", side=Side.BUY, outcome="Yes", price=0.48, size=50.0)
    fill2 = executor.submit_order_sync(req2)
    assert fill2.filled is False
    assert "duplicate" in fill2.reason.lower()


def test_dedup_allows_different_outcome_same_market(executor):
    """BUY Yes and BUY No on the same market_id should both fill (different outcomes)."""
    req1 = OrderRequest(market_id="m1", side=Side.BUY, outcome="Yes", price=0.50, size=100.0)
    fill1 = executor.submit_order_sync(req1)
    assert fill1.filled is True

    req2 = OrderRequest(market_id="m1", side=Side.BUY, outcome="No", price=0.40, size=100.0)
    fill2 = executor.submit_order_sync(req2)
    assert fill2.filled is True


def test_dedup_allows_different_market_same_outcome(executor):
    """BUY Yes on m1 and BUY Yes on m2 should both fill (different markets)."""
    req1 = OrderRequest(market_id="m1", side=Side.BUY, outcome="Yes", price=0.50, size=100.0)
    fill1 = executor.submit_order_sync(req1)
    assert fill1.filled is True

    req2 = OrderRequest(market_id="m2", side=Side.BUY, outcome="Yes", price=0.50, size=100.0)
    fill2 = executor.submit_order_sync(req2)
    assert fill2.filled is True


def test_dedup_allows_buy_after_position_closed(executor):
    """After closing a position, a new BUY on the same market+outcome should fill."""
    # Open
    buy = OrderRequest(market_id="m1", side=Side.BUY, outcome="Yes", price=0.50, size=100.0)
    executor.submit_order_sync(buy)

    # Close
    sell = OrderRequest(market_id="m1", side=Side.SELL, outcome="Yes", price=0.55, size=100.0)
    sell_fill = executor.submit_order_sync(sell)
    assert sell_fill.filled is True

    # Re-open — should succeed since position is closed
    rebuy = OrderRequest(market_id="m1", side=Side.BUY, outcome="Yes", price=0.52, size=80.0)
    rebuy_fill = executor.submit_order_sync(rebuy)
    assert rebuy_fill.filled is True


def test_dedup_rejects_buy_no_duplicate(executor):
    """Second BUY No on the same market_id should be rejected."""
    req1 = OrderRequest(market_id="m1", side=Side.BUY, outcome="No", price=0.40, size=100.0)
    fill1 = executor.submit_order_sync(req1)
    assert fill1.filled is True

    req2 = OrderRequest(market_id="m1", side=Side.BUY, outcome="No", price=0.38, size=50.0)
    fill2 = executor.submit_order_sync(req2)
    assert fill2.filled is False
    assert "duplicate" in fill2.reason.lower()


# ── State Persistence Tests ──────────────────────────────────

class TestStatePersistence:
    def test_export_round_trip(self, executor):
        """export → import should reproduce identical state."""
        req1 = OrderRequest(market_id="m1", side=Side.BUY, outcome="Yes", price=0.60, size=50.0)
        req2 = OrderRequest(market_id="m2", side=Side.BUY, outcome="No", price=0.40, size=30.0)
        executor.submit_order_sync(req1)
        executor.submit_order_sync(req2)
        state = executor.export_state()

        # Create fresh executor and import state
        fresh = PaperExecutor(initial_balance=10_000.0)
        fresh.import_state(state)

        assert fresh._balance == executor._balance
        assert fresh._total_fees_paid == executor._total_fees_paid
        assert fresh._total_realized_pnl == executor._total_realized_pnl
        assert len(fresh._trade_history) == len(executor._trade_history)
        assert fresh.get_position("m1", "Yes") is not None
        assert fresh.get_position("m2", "No") is not None

    def test_export_positions_with_end_date(self, executor):
        """Positions with end_date_iso should survive export/import."""
        req = OrderRequest(
            market_id="m1", side=Side.BUY, outcome="Yes",
            price=0.60, size=50.0, iteration=1,
            end_date_iso="2026-12-31T00:00:00Z",
        )
        executor.submit_order_sync(req)

        state = executor.export_state()
        fresh = PaperExecutor(initial_balance=10_000.0)
        fresh.import_state(state)

        pos = fresh.get_position("m1", "Yes")
        assert pos is not None
        assert pos.end_date_iso == "2026-12-31T00:00:00Z"

    def test_save_load_json(self, executor, tmp_path):
        """save_state → load_state via JSON file."""
        req = OrderRequest(market_id="m1", side=Side.BUY, outcome="Yes", price=0.60, size=50.0)
        executor.submit_order_sync(req)
        path = str(tmp_path / "state.json")

        executor.save_state(path)

        fresh = PaperExecutor(initial_balance=10_000.0)
        fresh.load_state(path)

        assert fresh._balance == executor._balance
        assert fresh.get_position("m1", "Yes") is not None

    def test_import_rejects_bad_version(self, executor):
        """import_state with unsupported version should raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported state version"):
            executor.import_state({"version": 99})

    def test_export_empty_state(self, executor):
        """Exporting a fresh executor with no trades should work."""
        state = executor.export_state()
        assert state["balance"] == 10_000.0
        assert state["positions"] == {}
        assert state["trade_history"] == []

    def test_export_after_sell(self, executor):
        """State after sell should have realized P&L preserved."""
        buy = OrderRequest(market_id="m1", side=Side.BUY, outcome="Yes", price=0.60, size=50.0)
        executor.submit_order_sync(buy)
        sell = OrderRequest(market_id="m1", side=Side.SELL, outcome="Yes", price=0.70, size=50.0)
        executor.submit_order_sync(sell)

        state = executor.export_state()
        assert state["total_realized_pnl"] > 0

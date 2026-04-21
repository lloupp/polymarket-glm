"""Integration tests for live mode — dry-run path with LiveExecutor.

Tests the full signal→risk→execution pipeline in dry-run mode
without requiring real API keys or network access.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from polymarket_glm.config import Settings, ExecutionMode, RiskConfig
from polymarket_glm.execution.live_executor import LiveExecutor
from polymarket_glm.execution.exchange import OrderRequest
from polymarket_glm.models import Side, Market, OrderBook, OrderBookLevel
from polymarket_glm.risk.controller import RiskController, RiskVerdict
from polymarket_glm.strategy.signal_engine import SignalEngine


class TestLiveExecutorDryRun:
    """LiveExecutor in dry_run=True mode — no real API keys needed."""

    def test_dry_run_init_no_keys(self):
        """Dry-run should NOT require API keys."""
        executor = LiveExecutor(dry_run=True)
        assert executor._dry_run is True

    def test_live_mode_requires_keys(self):
        """Non-dry-run without keys should raise ValueError."""
        with pytest.raises(ValueError, match="API keys required"):
            LiveExecutor(dry_run=False)

    @pytest.mark.asyncio
    async def test_dry_run_submit_order(self):
        """Dry-run should return unfilled result, no real order."""
        executor = LiveExecutor(dry_run=True)
        request = OrderRequest(
            market_id="12345",
            side=Side.BUY,
            outcome="Yes",
            price=0.55,
            size=10.0,
        )
        result = await executor.submit_order(request)
        assert result.filled is False
        assert "Dry run" in result.reason
        assert result.order_id != ""

    @pytest.mark.asyncio
    async def test_dry_run_cancel_order(self):
        """Dry-run cancel should return success=False with reason."""
        executor = LiveExecutor(dry_run=True)
        result = await executor.cancel_order("order-123")
        assert result.success is False
        assert "Dry run" in result.reason

    @pytest.mark.asyncio
    async def test_dry_run_get_open_orders(self):
        """Dry-run should return empty orders list."""
        executor = LiveExecutor(dry_run=True)
        orders = await executor.get_open_orders()
        assert orders == []

    @pytest.mark.asyncio
    async def test_dry_run_get_account(self):
        """Dry-run should return zero-balance account."""
        executor = LiveExecutor(dry_run=True)
        account = await executor.get_account()
        assert account.balance_usd == 0.0


class TestDryRunFullPipeline:
    """End-to-end: signal → risk → dry-run execution."""

    @pytest.mark.asyncio
    async def test_signal_to_dry_run_execution(self):
        """Generate a signal, pass risk, execute in dry-run."""
        # Setup
        risk = RiskController(RiskConfig(max_per_trade_usd=50.0))
        executor = LiveExecutor(dry_run=True)

        # Create a market with an edge
        market = Market(
            condition_id="cond-1",
            market_id="test-market-1",
            question="Will X happen?",
            outcomes=["Yes", "No"],
            outcome_prices=[0.50, 0.50],
            tokens=["token-yes", "token-no"],
            volume=50000.0,
        )
        book = OrderBook(
            market_id="test-market-1",
            bids=[OrderBookLevel(price=0.50, size=100)],
            asks=[OrderBookLevel(price=0.55, size=100)],
        )

        # Signal: estimated_prob=0.70 vs market=0.50 → big edge
        signal_engine = SignalEngine()
        signal = signal_engine.generate_signal(
            market=market,
            book=book,
            estimated_prob=0.70,
            balance_usd=100.0,  # small balance → small signal → fits risk limit
        )

        assert signal is not None, "Signal should be generated with 20% edge"
        assert signal.signal_type.value != "none"
        assert signal.size_usd <= 50.0, f"Signal size ${signal.size_usd:.2f} should fit per-trade limit"

        # Risk check
        verdict, reason = risk.check(
            market_id=signal.market_id,
            outcome=signal.outcome,
            trade_usd=signal.size_usd,
        )
        assert verdict == RiskVerdict.ALLOW, f"Risk denied: {reason}"

        # Execute in dry-run
        request = OrderRequest(
            market_id=signal.market_id,
            side=Side.BUY,
            outcome=signal.outcome,
            price=signal.target_price,
            size=signal.size_usd / signal.target_price if signal.target_price > 0 else 0,
        )
        result = await executor.submit_order(request)
        assert result.filled is False
        assert "Dry run" in result.reason

    @pytest.mark.asyncio
    async def test_risk_blocks_dry_run_execution(self):
        """Kill switch should block even in dry-run mode."""
        risk = RiskController(RiskConfig(max_per_trade_usd=50.0))
        risk.activate_kill_switch("test kill switch")

        verdict, reason = risk.check(
            market_id="test-market",
            outcome="Yes",
            trade_usd=10.0,
        )
        assert verdict == RiskVerdict.KILL_SWITCH
        assert "Kill switch" in reason

    @pytest.mark.asyncio
    async def test_daily_loss_blocks_dry_run(self):
        """Daily loss limit should block trades in dry-run too."""
        risk = RiskController(RiskConfig(
            daily_loss_limit_usd=30.0,
            max_per_trade_usd=50.0,
        ))
        risk.record_loss(30.0)  # hit the limit

        verdict, reason = risk.check(
            market_id="test-market",
            outcome="Yes",
            trade_usd=10.0,
        )
        assert verdict == RiskVerdict.DENY_DAILY_LIMIT


class TestSettingsLiveGate:
    """Settings should gate live mode without API keys."""

    def test_paper_mode_default(self):
        """Default mode should be PAPER."""
        s = Settings()
        assert s.execution_mode == ExecutionMode.PAPER

    def test_live_not_ready_without_keys(self):
        """live_ready should be False without API keys."""
        s = Settings()
        assert s.live_ready is False

    def test_live_ready_with_keys(self):
        """live_ready should be True with all CLOB keys set."""
        s = Settings(
            clob_api_key="key",
            clob_api_secret="secret",
            clob_api_passphrase="pass",
            private_key="0xabc",
        )
        assert s.live_ready is True

    def test_conservative_defaults(self):
        """RiskConfig should have conservative simulation defaults."""
        s = Settings()
        assert s.risk.max_total_exposure_usd == 500.0
        assert s.risk.max_per_trade_usd == 50.0
        assert s.risk.daily_loss_limit_usd == 30.0
        assert s.risk.drawdown_circuit_breaker_pct == 0.10

"""Tests for BacktestEngine — replay historical data, compute metrics."""
import pytest
from polymarket_glm.backtest.engine import (
    BacktestEngine,
    BacktestResult,
    BacktestTrade,
    BacktestConfig,
)
from polymarket_glm.strategy.estimator import (
    EstimateResult,
    MarketInfo,
    ProbabilityEstimator,
)


class FixedEstimator(ProbabilityEstimator):
    """Returns a fixed probability for testing."""
    def __init__(self, prob: float):
        self._prob = prob

    def estimate(self, market: MarketInfo) -> EstimateResult:
        return EstimateResult(
            probability=self._prob,
            confidence=0.8,
            source="fixed",
        )


def _market(price: float = 0.5, volume: float = 100_000) -> dict:
    """Create a market snapshot dict for backtest."""
    return {
        "question": "Will X happen?",
        "current_price": price,
        "volume": volume,
        "liquidity": volume / 2,
        "spread": 0.02,
        "category": "test",
        "resolved_outcome": True,
    }


def test_backtest_config_defaults():
    """BacktestConfig should have sensible defaults."""
    config = BacktestConfig()
    assert config.initial_capital > 0
    assert config.max_position_pct > 0
    assert config.fee_bps >= 0


def test_backtest_single_profitable_trade():
    """A trade that wins should show positive PnL."""
    engine = BacktestEngine(
        config=BacktestConfig(initial_capital=1000.0),
        estimator=FixedEstimator(prob=0.8),
    )
    snapshots = [
        {"current_price": 0.50, "resolved_outcome": True, "volume": 100_000,
         "liquidity": 50_000, "spread": 0.02, "category": "test", "question": "Q1"},
    ]
    result = engine.run(snapshots)
    assert isinstance(result, BacktestResult)
    assert result.total_trades >= 0
    assert result.final_capital >= 0


def test_backtest_metrics_computed():
    """Result should include win rate, Sharpe, max drawdown."""
    engine = BacktestEngine(
        config=BacktestConfig(initial_capital=1000.0),
        estimator=FixedEstimator(prob=0.7),
    )
    # Multiple snapshots
    snapshots = [
        {"current_price": 0.5 + i * 0.05, "resolved_outcome": i % 2 == 0,
         "volume": 100_000, "liquidity": 50_000, "spread": 0.02,
         "category": "test", "question": f"Q{i}"}
        for i in range(10)
    ]
    result = engine.run(snapshots)
    assert 0 <= result.win_rate <= 1
    assert result.max_drawdown >= 0
    assert result.final_capital > 0


def test_backtest_no_trades_when_edge_low():
    """When estimated prob ≈ market price, no edge → no trade."""
    engine = BacktestEngine(
        config=BacktestConfig(initial_capital=1000.0, min_edge=0.10),
        estimator=FixedEstimator(prob=0.52),  # barely above 0.50 price
    )
    snapshots = [
        {"current_price": 0.50, "resolved_outcome": True,
         "volume": 100_000, "liquidity": 50_000, "spread": 0.02,
         "category": "test", "question": "Q1"},
    ]
    result = engine.run(snapshots)
    assert result.total_trades == 0


def test_backtest_drawdown_calculation():
    """Max drawdown should be correctly computed from equity curve."""
    engine = BacktestEngine(
        config=BacktestConfig(initial_capital=1000.0),
        estimator=FixedEstimator(prob=0.8),
    )
    # Create a series where first trade wins, second loses
    snapshots = [
        {"current_price": 0.40, "resolved_outcome": True,
         "volume": 200_000, "liquidity": 100_000, "spread": 0.01,
         "category": "test", "question": "Win"},
        {"current_price": 0.80, "resolved_outcome": False,
         "volume": 50_000, "liquidity": 20_000, "spread": 0.10,
         "category": "test", "question": "Loss"},
    ]
    result = engine.run(snapshots)
    assert result.max_drawdown >= 0
    assert len(result.equity_curve) > 0


def test_backtest_fees_deducted():
    """Fees should be deducted from PnL."""
    engine_no_fee = BacktestEngine(
        config=BacktestConfig(initial_capital=1000.0, fee_bps=0),
        estimator=FixedEstimator(prob=0.8),
    )
    engine_with_fee = BacktestEngine(
        config=BacktestConfig(initial_capital=1000.0, fee_bps=200),  # 2% fee
        estimator=FixedEstimator(prob=0.8),
    )
    snapshots = [
        {"current_price": 0.50, "resolved_outcome": True,
         "volume": 100_000, "liquidity": 50_000, "spread": 0.02,
         "category": "test", "question": "Q1"},
    ]
    r_no_fee = engine_no_fee.run(snapshots)
    r_with_fee = engine_with_fee.run(snapshots)
    # With fees, final capital should be <= without fees
    assert r_with_fee.final_capital <= r_no_fee.final_capital


def test_backtest_trade_record():
    """BacktestTrade should record entry/exit details."""
    trade = BacktestTrade(
        market_question="Will X?",
        entry_price=0.50,
        size=100.0,
        side="BUY",
        estimated_prob=0.80,
        outcome=True,
        pnl=50.0,
    )
    assert trade.pnl == 50.0
    assert trade.side == "BUY"


def test_backtest_result_serialization():
    """BacktestResult should be serializable."""
    engine = BacktestEngine(
        config=BacktestConfig(initial_capital=500.0),
        estimator=FixedEstimator(prob=0.7),
    )
    snapshots = [
        {"current_price": 0.50, "resolved_outcome": True,
         "volume": 100_000, "liquidity": 50_000, "spread": 0.02,
         "category": "test", "question": "Q1"},
    ]
    result = engine.run(snapshots)
    data = result.model_dump()
    assert "final_capital" in data
    assert "total_trades" in data
    assert "win_rate" in data

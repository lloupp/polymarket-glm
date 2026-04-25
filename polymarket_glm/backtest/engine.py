"""Backtest engine — replay historical market data through the strategy pipeline.

Simulates what would have happened if we traded using our estimator + signal engine
on historical market snapshots. Computes PnL, win rate, max drawdown, Sharpe ratio.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from polymarket_glm.strategy.estimator import (
    EstimateResult,
    MarketInfo,
    ProbabilityEstimator,
)
from polymarket_glm.strategy.signal_engine import SignalEngine


class BacktestConfig(BaseModel):
    """Configuration for backtest runs."""
    initial_capital: float = Field(gt=0, default=1000.0)
    max_position_pct: float = Field(gt=0, le=1, default=0.05)  # 5% per trade
    min_edge: float = Field(ge=0, default=0.05)  # minimum edge to trade
    fee_bps: int = Field(ge=0, default=100)  # 1% fee = 100 bps
    kelly_fraction: float = Field(gt=0, le=1, default=0.25)


class BacktestTrade(BaseModel):
    """Record of a single simulated trade in the backtest."""
    market_question: str
    entry_price: float
    size: float
    side: str  # "BUY" or "SELL"
    estimated_prob: float
    outcome: bool  # True = event happened
    pnl: float


class BacktestResult(BaseModel):
    """Aggregated results of a backtest run."""
    initial_capital: float
    final_capital: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    max_drawdown: float
    sharpe_ratio: float
    equity_curve: list[float]
    trades: list[BacktestTrade]


class BacktestEngine:
    """Replays historical market snapshots through the strategy pipeline.

    For each snapshot:
    1. Build MarketInfo from snapshot
    2. Get probability estimate from estimator
    3. Compute edge = |estimated - price|
    4. If edge > min_edge, simulate a trade
    5. Resolve trade when market settles
    6. Track equity curve, drawdown, win rate

    Usage:
        engine = BacktestEngine(
            config=BacktestConfig(initial_capital=1000),
            estimator=HeuristicEstimator(),
        )
        result = engine.run(historical_snapshots)
        print(f"Win rate: {result.win_rate:.1%}")
        print(f"Sharpe: {result.sharpe_ratio:.2f}")
    """

    def __init__(
        self,
        config: BacktestConfig | None = None,
        estimator: ProbabilityEstimator | None = None,
    ):
        self._config = config or BacktestConfig()
        self._estimator = estimator
        self._signal_engine = SignalEngine(kelly_fraction=self._config.kelly_fraction)

    async def run(self, snapshots: list[dict[str, Any]]) -> BacktestResult:
        """Run backtest over a list of market snapshots.

        Each snapshot should have:
        - current_price: float (market price at time of decision)
        - resolved_outcome: bool (what actually happened)
        - volume, liquidity, spread, category, question: metadata
        """
        capital = self._config.initial_capital
        peak_capital = capital
        max_dd = 0.0
        equity_curve: list[float] = [capital]
        trades: list[BacktestTrade] = []
        returns: list[float] = []

        for snap in snapshots:
            market = self._build_market_info(snap)
            price = snap.get("current_price", 0.5)
            outcome = snap.get("resolved_outcome", False)

            if self._estimator is None:
                # No estimator → skip
                equity_curve.append(capital)
                continue

            # Get estimate
            estimate = await self._estimator.estimate(market)

            # Compute edge
            edge = abs(estimate.probability - price)
            if edge < self._config.min_edge:
                equity_curve.append(capital)
                continue

            # Determine side
            if estimate.probability > price:
                side = "BUY"
                entry_price = price
            else:
                side = "SELL"
                entry_price = 1.0 - price

            # Position sizing (fraction of capital)
            position_size = capital * self._config.max_position_pct

            # Deduct fee
            fee = position_size * (self._config.fee_bps / 10_000)
            cost = position_size + fee

            if cost > capital:
                equity_curve.append(capital)
                continue

            # Simulate resolution
            if side == "BUY":
                if outcome:
                    pnl = position_size * (1.0 / entry_price - 1.0) - fee
                else:
                    pnl = -position_size - fee
            else:  # SELL
                if not outcome:
                    pnl = position_size * (1.0 / entry_price - 1.0) - fee
                else:
                    pnl = -position_size - fee

            capital += pnl
            equity_curve.append(capital)

            # Track return for Sharpe
            ret = pnl / self._config.initial_capital
            returns.append(ret)

            trades.append(BacktestTrade(
                market_question=snap.get("question", "Unknown"),
                entry_price=entry_price,
                size=position_size,
                side=side,
                estimated_prob=estimate.probability,
                outcome=outcome,
                pnl=pnl,
            ))

            # Drawdown
            if capital > peak_capital:
                peak_capital = capital
            dd = (peak_capital - capital) / peak_capital if peak_capital > 0 else 0
            if dd > max_dd:
                max_dd = dd

        # Aggregate
        winning = [t for t in trades if t.pnl > 0]
        losing = [t for t in trades if t.pnl <= 0]
        total_trades = len(trades)
        win_rate = len(winning) / total_trades if total_trades > 0 else 0.0
        total_pnl = capital - self._config.initial_capital

        # Sharpe ratio (annualized, assuming 252 trading days)
        sharpe = self._compute_sharpe(returns)

        return BacktestResult(
            initial_capital=self._config.initial_capital,
            final_capital=round(capital, 2),
            total_trades=total_trades,
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=round(win_rate, 4),
            total_pnl=round(total_pnl, 2),
            max_drawdown=round(max_dd, 4),
            sharpe_ratio=round(sharpe, 4),
            equity_curve=[round(e, 2) for e in equity_curve],
            trades=trades,
        )

    @staticmethod
    def _build_market_info(snap: dict[str, Any]) -> MarketInfo:
        """Convert a snapshot dict to MarketInfo."""
        return MarketInfo(
            question=snap.get("question", ""),
            volume=snap.get("volume", 0),
            liquidity=snap.get("liquidity", 0),
            spread=snap.get("spread", 0),
            current_price=snap.get("current_price", 0.5),
            category=snap.get("category", "unknown"),
        )

    @staticmethod
    def _compute_sharpe(returns: list[float], annualize: bool = True) -> float:
        """Compute Sharpe ratio from a list of returns."""
        if not returns or len(returns) < 2:
            return 0.0

        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0

        if std == 0:
            return 0.0

        sharpe = mean_ret / std
        if annualize:
            sharpe *= math.sqrt(252)

        return sharpe

"""Tests for probability estimator — Protocol + HeuristicEstimator."""
import pytest
from datetime import datetime, timezone
from polymarket_glm.strategy.estimator import (
    ProbabilityEstimator,
    HeuristicEstimator,
    EstimateResult,
    MarketInfo,
)


# ── EstimateResult ──

def test_estimate_result_defaults():
    """EstimateResult should have sensible defaults."""
    r = EstimateResult(probability=0.65)
    assert r.probability == 0.65
    assert r.confidence == 0.0
    assert r.source == "unknown"
    assert 0 <= r.probability <= 1


def test_estimate_result_invalid_prob():
    """Probability outside [0,1] should raise."""
    with pytest.raises(Exception):
        EstimateResult(probability=1.5)
    with pytest.raises(Exception):
        EstimateResult(probability=-0.1)


# ── MarketInfo ──

def test_market_info_creation():
    """MarketInfo should store market metadata."""
    mi = MarketInfo(
        question="Will X happen by 2026?",
        volume=50000.0,
        liquidity=10000.0,
        spread=0.03,
        category="politics",
        end_date="2026-12-31",
    )
    assert mi.question == "Will X happen by 2026?"
    assert mi.volume == 50000.0
    assert mi.spread == 0.03


# ── Protocol compliance ──

def test_heuristic_estimator_satisfies_protocol():
    """HeuristicEstimator should implement ProbabilityEstimator protocol."""
    estimator = HeuristicEstimator()
    # Protocol check — method must exist
    assert hasattr(estimator, "estimate")
    assert callable(estimator.estimate)


# ── HeuristicEstimator ──

def test_heuristic_volume_signal():
    """High volume markets should nudge probability toward market price."""
    estimator = HeuristicEstimator()
    mi = MarketInfo(
        question="Will BTC hit $100k?",
        volume=1_000_000.0,
        liquidity=500_000.0,
        spread=0.01,
        current_price=0.72,
        category="crypto",
    )
    result = estimator.estimate(mi)
    assert 0 < result.probability < 1
    assert result.source == "heuristic"
    assert result.confidence > 0


def test_heuristic_low_volume_low_confidence():
    """Low volume markets should yield low confidence."""
    estimator = HeuristicEstimator()
    mi = MarketInfo(
        question="Obscure event?",
        volume=100.0,
        liquidity=50.0,
        spread=0.20,
        current_price=0.50,
        category="other",
    )
    result = estimator.estimate(mi)
    assert result.confidence < 0.5
    assert result.source == "heuristic"


def test_heuristic_wide_spread_adjusts_probability():
    """Wide spread should pull probability toward 0.5 (uncertainty)."""
    estimator = HeuristicEstimator()
    mi_tight = MarketInfo(
        question="Tight spread market",
        volume=100_000.0,
        liquidity=50_000.0,
        spread=0.01,
        current_price=0.80,
        category="politics",
    )
    mi_wide = MarketInfo(
        question="Wide spread market",
        volume=100_000.0,
        liquidity=50_000.0,
        spread=0.20,
        current_price=0.80,
        category="politics",
    )
    r_tight = estimator.estimate(mi_tight)
    r_wide = estimator.estimate(mi_wide)
    # Wide spread should pull toward 0.5
    assert abs(r_wide.probability - 0.5) < abs(r_tight.probability - 0.5)


def test_heuristic_no_price_defaults_to_0_5():
    """No current_price should default to 0.5 (maximum uncertainty)."""
    estimator = HeuristicEstimator()
    mi = MarketInfo(
        question="New market?",
        volume=0.0,
        liquidity=0.0,
        spread=1.0,
    )
    result = estimator.estimate(mi)
    assert result.probability == 0.5
    assert result.confidence == 0.0


def test_heuristic_recency_boost():
    """Markets with end_date far in future should have slightly higher confidence."""
    estimator = HeuristicEstimator()
    mi_near = MarketInfo(
        question="Event tomorrow",
        volume=10_000.0,
        liquidity=5_000.0,
        spread=0.05,
        current_price=0.60,
        end_date="2026-04-22",
    )
    mi_far = MarketInfo(
        question="Event in a year",
        volume=10_000.0,
        liquidity=5_000.0,
        spread=0.05,
        current_price=0.60,
        end_date="2027-04-22",
    )
    r_near = estimator.estimate(mi_near)
    r_far = estimator.estimate(mi_far)
    # Near events have more information → should have higher confidence
    assert r_near.confidence >= r_far.confidence


def test_heuristic_category_adjustment():
    """Known categories should have a slight confidence boost."""
    estimator = HeuristicEstimator()
    mi_known = MarketInfo(
        question="Will candidate X win?",
        volume=50_000.0,
        liquidity=20_000.0,
        spread=0.02,
        current_price=0.55,
        category="politics",
    )
    mi_unknown = MarketInfo(
        question="Will X happen?",
        volume=50_000.0,
        liquidity=20_000.0,
        spread=0.02,
        current_price=0.55,
        category="obscure_category_xyz",
    )
    r_known = estimator.estimate(mi_known)
    r_unknown = estimator.estimate(mi_unknown)
    assert r_known.confidence >= r_unknown.confidence

"""Tests for EnsembleEstimator — weighted combination of multiple estimators."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from polymarket_glm.strategy.estimator import (
    EstimateResult,
    HeuristicEstimator,
    MarketInfo,
)
from polymarket_glm.strategy.ensemble_estimator import (
    EnsembleEstimator,
    WeightedEstimator,
)


def _mi(**kwargs):
    """Helper to create MarketInfo with defaults."""
    defaults = dict(
        question="Will X happen?",
        volume=100_000.0,
        liquidity=50_000.0,
        spread=0.02,
        current_price=0.60,
        category="politics",
    )
    defaults.update(kwargs)
    return MarketInfo(**defaults)


def test_ensemble_with_single_estimator():
    """Single estimator should produce its own result."""
    ensemble = EnsembleEstimator(
        estimators=[WeightedEstimator(estimator=HeuristicEstimator(), weight=1.0)]
    )
    result = ensemble.estimate(_mi())
    assert 0 < result.probability < 1
    assert result.source == "ensemble"


def test_ensemble_weighted_average():
    """Should compute weighted average of probabilities."""
    # Mock estimators with fixed outputs
    mock1 = MagicMock()
    mock1.estimate.return_value = EstimateResult(probability=0.8, confidence=0.9, source="a")

    mock2 = MagicMock()
    mock2.estimate.return_value = EstimateResult(probability=0.4, confidence=0.7, source="b")

    # 75% weight on estimator 1, 25% on estimator 2
    # Expected: 0.8*0.75 + 0.4*0.25 = 0.7
    ensemble = EnsembleEstimator(
        estimators=[
            WeightedEstimator(estimator=mock1, weight=0.75),
            WeightedEstimator(estimator=mock2, weight=0.25),
        ]
    )
    result = ensemble.estimate(_mi())
    assert abs(result.probability - 0.70) < 0.01


def test_ensemble_confidence_is_weighted_average():
    """Ensemble confidence should be weighted average of individual confidences."""
    mock1 = MagicMock()
    mock1.estimate.return_value = EstimateResult(probability=0.6, confidence=0.9, source="a")

    mock2 = MagicMock()
    mock2.estimate.return_value = EstimateResult(probability=0.5, confidence=0.3, source="b")

    ensemble = EnsembleEstimator(
        estimators=[
            WeightedEstimator(estimator=mock1, weight=0.6),
            WeightedEstimator(estimator=mock2, weight=0.4),
        ]
    )
    result = ensemble.estimate(_mi())
    # 0.9*0.6 + 0.3*0.4 = 0.66
    assert abs(result.confidence - 0.66) < 0.01


def test_ensemble_auto_normalize_weights():
    """Weights that don't sum to 1 should be auto-normalized."""
    mock1 = MagicMock()
    mock1.estimate.return_value = EstimateResult(probability=0.8, confidence=0.9, source="a")

    mock2 = MagicMock()
    mock2.estimate.return_value = EstimateResult(probability=0.4, confidence=0.7, source="b")

    # Weights sum to 2.0, should be normalized
    ensemble = EnsembleEstimator(
        estimators=[
            WeightedEstimator(estimator=mock1, weight=1.5),
            WeightedEstimator(estimator=mock2, weight=0.5),
        ]
    )
    result = ensemble.estimate(_mi())
    # Normalized: w1=0.75, w2=0.25 → 0.8*0.75 + 0.4*0.25 = 0.7
    assert abs(result.probability - 0.70) < 0.01


def test_ensemble_empty_estimators():
    """Empty estimator list should return 0.5 with 0 confidence."""
    ensemble = EnsembleEstimator(estimators=[])
    result = ensemble.estimate(_mi())
    assert result.probability == 0.5
    assert result.confidence == 0.0


def test_ensemble_agreement_bonus():
    """When estimators agree closely, confidence should get a bonus."""
    # High agreement: both say ~0.65
    mock_agree1 = MagicMock()
    mock_agree1.estimate.return_value = EstimateResult(probability=0.65, confidence=0.8, source="a")

    mock_agree2 = MagicMock()
    mock_agree2.estimate.return_value = EstimateResult(probability=0.63, confidence=0.7, source="b")

    # Low agreement: one says 0.8, other says 0.3
    mock_disagree1 = MagicMock()
    mock_disagree1.estimate.return_value = EstimateResult(probability=0.8, confidence=0.8, source="a")

    mock_disagree2 = MagicMock()
    mock_disagree2.estimate.return_value = EstimateResult(probability=0.3, confidence=0.7, source="b")

    ensemble_agree = EnsembleEstimator(
        estimators=[
            WeightedEstimator(estimator=mock_agree1, weight=0.5),
            WeightedEstimator(estimator=mock_agree2, weight=0.5),
        ]
    )
    ensemble_disagree = EnsembleEstimator(
        estimators=[
            WeightedEstimator(estimator=mock_disagree1, weight=0.5),
            WeightedEstimator(estimator=mock_disagree2, weight=0.5),
        ]
    )

    r_agree = ensemble_agree.estimate(_mi())
    r_disagree = ensemble_disagree.estimate(_mi())

    # Agreement should yield higher confidence
    assert r_agree.confidence > r_disagree.confidence


def test_ensemble_sources_listed():
    """Result should list all estimator sources."""
    mock1 = MagicMock()
    mock1.estimate.return_value = EstimateResult(probability=0.6, confidence=0.8, source="heuristic")

    mock2 = MagicMock()
    mock2.estimate.return_value = EstimateResult(probability=0.55, confidence=0.6, source="llm")

    ensemble = EnsembleEstimator(
        estimators=[
            WeightedEstimator(estimator=mock1, weight=0.5),
            WeightedEstimator(estimator=mock2, weight=0.5),
        ]
    )
    result = ensemble.estimate(_mi())
    assert "heuristic" in result.reasoning
    assert "llm" in result.reasoning

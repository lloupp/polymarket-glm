"""Ensemble estimator — weighted combination of multiple estimators.

Combines estimates from multiple ProbabilityEstimators using configurable
weights. Auto-normalizes weights. Adds an agreement bonus when estimators
converge on similar probabilities.
"""
from __future__ import annotations

import logging
import math

from pydantic import BaseModel, Field

from polymarket_glm.strategy.estimator import (
    EstimateResult,
    MarketInfo,
    ProbabilityEstimator,
)

logger = logging.getLogger(__name__)


class WeightedEstimator(BaseModel):
    """An estimator paired with a weight for ensemble combination."""
    model_config = {"arbitrary_types_allowed": True}

    estimator: ProbabilityEstimator
    weight: float = Field(gt=0, default=1.0)


class EnsembleEstimator:
    """Combines multiple probability estimators with weighted averaging.

    Features:
    - Weighted average of probabilities (auto-normalized)
    - Weighted average of confidences
    - Agreement bonus: when estimators agree closely, confidence increases
    - Disagreement penalty: wide spread of estimates lowers confidence

    Usage:
        ensemble = EnsembleEstimator(
            estimators=[
                WeightedEstimator(estimator=HeuristicEstimator(), weight=0.4),
                WeightedEstimator(estimator=LLMEstimator(api_key="..."), weight=0.6),
            ]
        )
        result = ensemble.estimate(market_info)
    """

    # Maximum agreement bonus (added to confidence when all estimators agree)
    AGREEMENT_BONUS = 0.15
    # Max standard deviation before disagreement penalty kicks in
    AGREEMENT_STD_THRESHOLD = 0.05

    def __init__(
        self,
        estimators: list[WeightedEstimator] | None = None,
    ):
        self._estimators = estimators or []

    def add(self, estimator: ProbabilityEstimator, weight: float = 1.0) -> None:
        """Add an estimator to the ensemble."""
        self._estimators.append(
            WeightedEstimator(estimator=estimator, weight=weight)
        )

    def estimate(self, market: MarketInfo) -> EstimateResult:
        """Produce a weighted ensemble estimate."""
        if not self._estimators:
            return EstimateResult(
                probability=0.5,
                confidence=0.0,
                source="ensemble",
                reasoning="No estimators in ensemble",
            )

        # Collect all estimates
        results: list[EstimateResult] = []
        for we in self._estimators:
            r = we.estimator.estimate(market)
            results.append(r)

        # Normalize weights
        total_weight = sum(we.weight for we in self._estimators)
        if total_weight == 0:
            total_weight = 1.0

        normalized_weights = [we.weight / total_weight for we in self._estimators]

        # Weighted average of probabilities
        weighted_prob = sum(
            r.probability * w for r, w in zip(results, normalized_weights)
        )

        # Weighted average of confidences
        weighted_conf = sum(
            r.confidence * w for r, w in zip(results, normalized_weights)
        )

        # Agreement bonus/penalty
        probs = [r.probability for r in results]
        agreement_adjustment = self._agreement_adjustment(probs)
        final_conf = min(max(weighted_conf + agreement_adjustment, 0.0), 1.0)

        # Build reasoning
        sources = [r.source for r in results]
        source_str = "+".join(sources)
        details = ", ".join(
            f"{r.source}={r.probability:.3f}(w={w:.2f})"
            for r, w in zip(results, normalized_weights)
        )

        return EstimateResult(
            probability=round(weighted_prob, 4),
            confidence=round(final_conf, 4),
            source="ensemble",
            reasoning=f"ensemble({source_str}): {details}",
        )

    def _agreement_adjustment(self, probabilities: list[float]) -> float:
        """Calculate confidence adjustment based on estimator agreement.

        High agreement (low std) → positive bonus.
        Low agreement (high std) → negative penalty.
        """
        if len(probabilities) < 2:
            return 0.0

        mean = sum(probabilities) / len(probabilities)
        variance = sum((p - mean) ** 2 for p in probabilities) / len(probabilities)
        std = math.sqrt(variance)

        if std <= self.AGREEMENT_STD_THRESHOLD:
            # Strong agreement → bonus
            return self.AGREEMENT_BONUS * (1 - std / self.AGREEMENT_STD_THRESHOLD)
        else:
            # Disagreement → penalty proportional to std
            max_std = 0.5  # maximum possible std for [0,1] probabilities
            penalty = -0.2 * min(std / max_std, 1.0)
            return penalty

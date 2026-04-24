"""Probability estimator — Protocol + HeuristicEstimator.

The ProbabilityEstimator is the "brain" of the framework — it takes market
information and produces a probability estimate + confidence score.

Architecture:
- ProbabilityEstimator: typing.Protocol for plug-compatible estimators
- HeuristicEstimator: rule-based estimator using volume, spread, recency, category
- Future: LLM Estimator, EnsembleEstimator, CalibratedEstimator
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# ── Known categories with higher baseline confidence ──────────
HIGH_CONFIDENCE_CATEGORIES = {
    "politics", "crypto", "sports", "weather", "economics",
    "science", "tech", "entertainment",
}


class EstimateResult(BaseModel):
    """Output of a probability estimation."""
    probability: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1, default=0.0)
    source: str = "unknown"
    reasoning: str = ""
    web_search_summary: str = ""  # MiniMax web search sources

    @field_validator("probability")
    @classmethod
    def _clamp_probability(cls, v: float) -> float:
        return max(0.0, min(1.0, v))


class MarketInfo(BaseModel):
    """Input data for probability estimation."""
    question: str = ""
    volume: float = Field(ge=0, default=0.0)
    liquidity: float = Field(ge=0, default=0.0)
    spread: float = Field(ge=0, le=1, default=1.0)
    current_price: float | None = Field(ge=0, le=1, default=None)
    category: str = ""
    end_date: str | None = None  # ISO date string


@runtime_checkable
class ProbabilityEstimator(Protocol):
    """Protocol for probability estimators — any plug-compatible estimator."""

    def estimate(self, market: MarketInfo) -> EstimateResult:
        """Produce a probability estimate for the given market."""
        ...


class HeuristicEstimator:
    """Rule-based probability estimator using market metadata.

    Signals used:
    1. current_price → base probability (market-implied)
    2. volume → confidence signal (higher volume = more information)
    3. spread → uncertainty signal (wider spread = pull toward 0.5)
    4. category → known categories get confidence boost
    5. recency → near-term events get confidence boost

    The estimate works by:
    - Starting from current_price (or 0.5 if unavailable)
    - Adjusting probability toward 0.5 based on spread width
    - Setting confidence from volume/spread/category/recency signals
    """

    # Volume thresholds for confidence scaling
    VOLUME_LOW = 1_000.0
    VOLUME_MED = 50_000.0
    VOLUME_HIGH = 500_000.0

    def estimate(self, market: MarketInfo) -> EstimateResult:
        """Produce a heuristic probability estimate."""
        # ── 1. Base probability from market price ──
        base_prob = market.current_price if market.current_price is not None else 0.5

        # ── 2. Spread adjustment — pull toward 0.5 (uncertainty) ──
        # spread factor: 0 = no spread (certain), 1 = max spread (uncertain)
        spread_factor = min(market.spread, 0.5) / 0.5  # normalize to [0, 1]
        adjusted_prob = base_prob + (0.5 - base_prob) * spread_factor * 0.5

        # ── 3. Confidence from volume ──
        volume_conf = self._volume_confidence(market.volume)

        # ── 4. Confidence from spread (inverse) ──
        spread_conf = 1.0 - spread_factor

        # ── 5. Category boost ──
        cat_boost = 0.1 if market.category.lower() in HIGH_CONFIDENCE_CATEGORIES else 0.0

        # ── 6. Recency boost ──
        recency_boost = self._recency_boost(market.end_date)

        # ── Combine confidence signals ──
        raw_conf = (
            volume_conf * 0.4
            + spread_conf * 0.3
            + cat_boost * 0.15
            + recency_boost * 0.15
        )
        confidence = min(raw_conf, 1.0)

        # If no price and no volume → zero confidence, probability = 0.5
        if market.current_price is None and market.volume == 0:
            confidence = 0.0
            adjusted_prob = 0.5

        return EstimateResult(
            probability=round(adjusted_prob, 4),
            confidence=round(confidence, 4),
            source="heuristic",
            reasoning=self._build_reasoning(market, adjusted_prob, confidence),
        )

    def _volume_confidence(self, volume: float) -> float:
        """Map volume to confidence: [0,1]."""
        if volume >= self.VOLUME_HIGH:
            return 0.9
        elif volume >= self.VOLUME_MED:
            return 0.7
        elif volume >= self.VOLUME_LOW:
            return 0.4
        else:
            return 0.1

    def _recency_boost(self, end_date: str | None) -> float:
        """Near-term events have more available info → higher confidence."""
        if end_date is None:
            return 0.0
        try:
            end_dt = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return 0.0

        now = datetime.now(timezone.utc)
        days_until = (end_dt - now).days

        if days_until < 0:
            return 0.3  # already resolved, high confidence
        elif days_until <= 7:
            return 0.25  # very near
        elif days_until <= 30:
            return 0.15
        elif days_until <= 90:
            return 0.1
        elif days_until <= 365:
            return 0.05
        else:
            return 0.0  # far future = low info

    def _build_reasoning(
        self, market: MarketInfo, prob: float, conf: float
    ) -> str:
        """Human-readable reasoning string."""
        parts = []
        if market.current_price is not None:
            parts.append(f"base_price={market.current_price:.2f}")
        parts.append(f"spread={market.spread:.3f}")
        parts.append(f"volume={market.volume:.0f}")
        if market.category:
            parts.append(f"cat={market.category}")
        return f"heuristic({', '.join(parts)}) → p={prob:.3f} conf={conf:.3f}"

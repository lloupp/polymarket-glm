"""Scorer dispatcher — routes markets to the correct deterministic scorer.

No LLMs — all estimation is based on real data.  The dispatcher inspects
the market question and category, then delegates to the appropriate
specialised scorer:

* Weather / temperature / rain / heat markets → WeatherScorer
* Everything else                          → HeuristicEstimator

The class satisfies the ``ProbabilityEstimator`` protocol defined in
``estimator.py``, so it can be used as a drop-in replacement anywhere
the protocol is expected.
"""
from __future__ import annotations

import logging
from typing import Final

from polymarket_glm.strategy.estimator import (
    EstimateResult,
    HeuristicEstimator,
    MarketInfo,
    ProbabilityEstimator,
)
from polymarket_glm.strategy.weather_scorer import WeatherScorer

logger = logging.getLogger(__name__)

# ── Keyword sets for routing ────────────────────────────────────
_WEATHER_KEYWORDS: Final[tuple[str, ...]] = (
    "temperature",
    "rain",
    "precipitation",
    "highest temp",
    "heat",
    "°c",
    "°f",
    "snow",
    "snowfall",
    "hurricane",
    "tornado",
    "wind chill",
    "heat index",
    "dew point",
    "weather",
)

_WEATHER_CATEGORY: Final[str] = "weather"


class ScorerDispatcher:
    """Routes markets to the appropriate deterministic scorer.

    No LLMs — all estimation is based on real data.

    Routing logic
    -------------
    * If the market category is ``"weather"`` **or** the question text
      contains any of the weather keywords, the market is dispatched to
      :class:`WeatherScorer`.
    * All other markets fall through to :class:`HeuristicEstimator`.

    This class implements the :class:`ProbabilityEstimator` protocol and
    can therefore be used anywhere a ``ProbabilityEstimator`` is expected
    (signal engine, calibration wrapper, ensemble, etc.).

    Example::

        dispatcher = ScorerDispatcher()
        result = await dispatcher.estimate(market_info)
    """

    def __init__(self) -> None:
        self._weather: WeatherScorer = WeatherScorer()
        self._heuristic: HeuristicEstimator = HeuristicEstimator()

    # ── ProbabilityEstimator protocol ────────────────────────────

    async def estimate(self, market: MarketInfo) -> EstimateResult:
        """Produce a probability estimate by dispatching to the right scorer.

        Parameters
        ----------
        market:
            The market information object containing the question, category,
            volume, spread, current price, and end date.

        Returns
        -------
        EstimateResult
            Probability estimate with confidence, source tag, and reasoning.
        """
        question_lower = market.question.lower()
        category_lower = market.category.lower()

        if self._is_weather_market(question_lower, category_lower):
            logger.info(
                "Dispatching to WeatherScorer — question=%r category=%r",
                market.question[:80],
                market.category,
            )
            return await self._weather.estimate(market)

        logger.debug(
            "Dispatching to HeuristicEstimator — question=%r category=%r",
            market.question[:80],
            market.category,
        )
        return await self._heuristic.estimate(market)

    # ── Internal helpers ─────────────────────────────────────────

    @staticmethod
    def _is_weather_market(question: str, category: str) -> bool:
        """Return *True* if the market should be handled by WeatherScorer.

        A market is classified as weather-related when **either**:

        1. Its category (lowercased) equals ``"weather"``, **or**
        2. Its question text (lowercased) contains at least one of the
           configured weather keywords.

        Parameters
        ----------
        question:
            The market question, already lowercased by the caller.
        category:
            The market category, already lowercased by the caller.
        """
        if category == _WEATHER_CATEGORY:
            return True

        return any(kw in question for kw in _WEATHER_KEYWORDS)

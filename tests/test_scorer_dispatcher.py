"""Tests for ScorerDispatcher — routing logic and protocol compliance."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from polymarket_glm.strategy.estimator import (
    EstimateResult,
    MarketInfo,
    ProbabilityEstimator,
)
from polymarket_glm.strategy.scorer_dispatcher import ScorerDispatcher


# ── Helpers ──────────────────────────────────────────────────────


def _make_market(**overrides: Any) -> MarketInfo:
    defaults = dict(
        question="Will something happen?",
        volume=1000.0,
        spread=0.05,
        current_price=0.5,
        category="",
    )
    defaults.update(overrides)
    return MarketInfo(**defaults)


async def _estimate(market: MarketInfo) -> EstimateResult:
    dispatcher = ScorerDispatcher()
    return await dispatcher.estimate(market)


# ── Protocol compliance ──────────────────────────────────────────


class TestProtocolCompliance:
    """ScorerDispatcher must satisfy the ProbabilityEstimator protocol."""

    def test_satisfies_protocol(self) -> None:
        dispatcher = ScorerDispatcher()
        assert isinstance(dispatcher, ProbabilityEstimator)

    def test_has_estimate_method(self) -> None:
        dispatcher = ScorerDispatcher()
        assert hasattr(dispatcher, "estimate")
        assert callable(dispatcher.estimate)


# ── Routing: weather markets ─────────────────────────────────────


class TestWeatherRouting:
    """Markets matching weather keywords should be routed to WeatherScorer."""

    @pytest.mark.parametrize(
        "question",
        [
            "Highest temperature in London on May 6",
            "Will it rain in Tokyo tomorrow?",
            "Total precipitation in Miami this week",
            "Snow accumulation in Denver",
            "Weather outlook for Paris",
            "Heat index in Phoenix above 110°F",
        ],
    )
    def test_weather_keyword_routing(self, question: str) -> None:
        dispatcher = ScorerDispatcher()
        market = _make_market(question=question)
        result = asyncio.get_event_loop().run_until_complete(
            dispatcher.estimate(market)
        )
        # WeatherScorer source contains "weather"
        assert "weather" in result.source.lower()

    def test_weather_category_routing(self) -> None:
        dispatcher = ScorerDispatcher()
        market = _make_market(question="Something generic", category="weather")
        result = asyncio.get_event_loop().run_until_complete(
            dispatcher.estimate(market)
        )
        assert "weather" in result.source.lower()

    def test_temperature_celsius_keyword(self) -> None:
        dispatcher = ScorerDispatcher()
        market = _make_market(question="Temperature above 30°C in Berlin?")
        result = asyncio.get_event_loop().run_until_complete(
            dispatcher.estimate(market)
        )
        assert "weather" in result.source.lower()


# ── Routing: non-weather markets ─────────────────────────────────


class TestNonWeatherRouting:
    """Markets that don't match weather keywords go to HeuristicEstimator."""

    @pytest.mark.parametrize(
        "question, category",
        [
            ("Will Trump win 2024?", "politics"),
            ("Bitcoin above $100k by July?", "crypto"),
            ("Will the Fed cut rates?", "economics"),
            ("Next pope?", "religion"),
            ("Random question", ""),
        ],
    )
    def test_heuristic_routing(self, question: str, category: str) -> None:
        dispatcher = ScorerDispatcher()
        market = _make_market(question=question, category=category)
        result = asyncio.get_event_loop().run_until_complete(
            dispatcher.estimate(market)
        )
        # HeuristicEstimator source is "heuristic"
        assert "heuristic" in result.source.lower()


# ── Routing: keyword classification ──────────────────────────────


class TestIsWeatherMarket:
    """Unit tests for the _is_weather_market static method."""

    def test_weather_category(self) -> None:
        assert ScorerDispatcher._is_weather_market("", "weather") is True

    def test_non_weather_category(self) -> None:
        assert ScorerDispatcher._is_weather_market("", "politics") is False

    def test_temperature_keyword(self) -> None:
        assert ScorerDispatcher._is_weather_market("highest temperature", "") is True

    def test_no_match(self) -> None:
        assert ScorerDispatcher._is_weather_market("bitcoin price", "") is False

    def test_case_insensitive(self) -> None:
        """_is_weather_market receives pre-lowercased strings from estimate()."""
        assert ScorerDispatcher._is_weather_market(
            "highest temperature", "politics"
        ) is True

    def test_partial_match(self) -> None:
        """Keywords must match as substrings."""
        assert ScorerDispatcher._is_weather_market(
            "the weather today", ""
        ) is True


# ── Estimate result structure ────────────────────────────────────


class TestEstimateResult:
    """Ensure the returned EstimateResult has valid fields."""

    def test_result_has_probability(self) -> None:
        market = _make_market(question="Weather in London", category="weather")
        result = asyncio.get_event_loop().run_until_complete(
            _estimate(market)
        )
        assert 0.0 <= result.probability <= 1.0

    def test_result_has_confidence(self) -> None:
        market = _make_market(question="Random market", category="")
        result = asyncio.get_event_loop().run_until_complete(
            _estimate(market)
        )
        assert result.confidence is not None
        assert 0.0 <= result.confidence <= 1.0

    def test_result_has_source(self) -> None:
        market = _make_market(question="Random market", category="")
        result = asyncio.get_event_loop().run_until_complete(
            _estimate(market)
        )
        assert isinstance(result.source, str)
        assert len(result.source) > 0

    def test_result_has_reasoning(self) -> None:
        market = _make_market(question="Random market", category="")
        result = asyncio.get_event_loop().run_until_complete(
            _estimate(market)
        )
        assert isinstance(result.reasoning, str)

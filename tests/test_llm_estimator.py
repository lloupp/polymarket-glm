"""Tests for LLM probability estimator."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from polymarket_glm.strategy.estimator import (
    EstimateResult,
    MarketInfo,
)
from polymarket_glm.strategy.llm_estimator import LLMEstimator, LLMConfig


def test_llm_config_defaults():
    """LLMConfig should have sensible defaults."""
    config = LLMConfig()
    assert config.model == "gpt-4o-mini"
    assert config.max_tokens == 150
    assert config.temperature == 0.1


def test_llm_estimator_satisfies_protocol():
    """LLMEstimator should implement the ProbabilityEstimator protocol."""
    estimator = LLMEstimator(api_key="test-key")
    assert hasattr(estimator, "estimate")
    assert callable(estimator.estimate)


def test_llm_parse_probability_from_response():
    """Should parse probability from LLM response text."""
    estimator = LLMEstimator(api_key="test-key")
    # Test various response formats
    assert estimator._parse_probability("Probability: 0.75") == 0.75
    assert estimator._parse_probability("I estimate 65% chance") == 0.65
    assert estimator._parse_probability("The probability is 0.42") == 0.42
    assert estimator._parse_probability("85 percent likely") == 0.85
    # Clamped to [0, 1]
    assert estimator._parse_probability("Probability: 0.50") == 0.50


def test_llm_parse_probability_no_match():
    """Should return 0.5 when no probability found in response."""
    estimator = LLMEstimator(api_key="test-key")
    result = estimator._parse_probability("I cannot determine this.")
    assert result == 0.5


def test_llm_build_prompt():
    """Should build a proper prompt from MarketInfo."""
    estimator = LLMEstimator(api_key="test-key")
    mi = MarketInfo(
        question="Will BTC hit $100k by end of 2026?",
        volume=500_000.0,
        spread=0.03,
        current_price=0.72,
        category="crypto",
    )
    prompt = estimator._build_prompt(mi)
    assert "BTC hit $100k" in prompt
    assert "72%" in prompt or "0.72" in prompt
    assert "0-100%" in prompt or "0 to 100" in prompt


@pytest.mark.asyncio
async def test_llm_estimate_calls_openai():
    """estimate() should call OpenAI API and return parsed result."""
    estimator = LLMEstimator(api_key="test-key")
    mi = MarketInfo(
        question="Will X happen?",
        volume=100_000.0,
        spread=0.02,
        current_price=0.60,
    )

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Probability: 0.68"

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        result = await estimator.estimate(mi)
        assert isinstance(result, EstimateResult)
        assert result.probability == 0.68
        assert result.source == "llm"
        assert result.confidence > 0


@pytest.mark.asyncio
async def test_llm_estimate_api_failure():
    """Should handle API failures gracefully."""
    estimator = LLMEstimator(api_key="test-key")
    mi = MarketInfo(question="Will X happen?")

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = Exception("API error")

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        result = await estimator.estimate(mi)
        assert result.probability == 0.5  # fallback
        assert result.source == "llm_fallback"

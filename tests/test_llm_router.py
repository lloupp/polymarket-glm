"""Tests for LLM Router — multi-provider free API router with rate limiting."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polymarket_glm.strategy.estimator import EstimateResult, MarketInfo


# ── S11-T1: Provider Config Models ──────────────────────────────

class TestProviderConfig:
    """Tests for LLMProviderConfig model."""

    def test_defaults(self):
        from polymarket_glm.strategy.llm_router import LLMProviderConfig
        cfg = LLMProviderConfig(name="groq", base_url="https://api.groq.com/v1")
        assert cfg.name == "groq"
        assert cfg.base_url == "https://api.groq.com/v1"
        assert cfg.model == "llama-3.3-70b-versatile"
        assert cfg.rpm == 30
        assert cfg.rpd == 14400
        assert cfg.priority == 1
        assert cfg.enabled is True
        assert cfg.api_key == ""

    def test_custom_values(self):
        from polymarket_glm.strategy.llm_router import LLMProviderConfig
        cfg = LLMProviderConfig(
            name="gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            model="gemini-2.5-flash",
            rpm=10,
            rpd=250,
            priority=2,
            api_key="test-key-123",
        )
        assert cfg.model == "gemini-2.5-flash"
        assert cfg.rpm == 10
        assert cfg.rpd == 250
        assert cfg.priority == 2
        assert cfg.api_key == "test-key-123"

    def test_requires_name_and_url(self):
        from polymarket_glm.strategy.llm_router import LLMProviderConfig
        with pytest.raises(Exception):
            LLMProviderConfig()  # missing required fields


class TestLLMRouterRuntimeConfig:
    """Tests for LLMRouterRuntimeConfig model."""

    def test_defaults(self):
        from polymarket_glm.strategy.llm_router import LLMRouterRuntimeConfig
        cfg = LLMRouterRuntimeConfig()
        assert cfg.providers == []
        assert cfg.max_retries_per_provider == 2
        assert cfg.timeout_sec == 30.0
        assert cfg.temperature == 0.3
        assert cfg.max_tokens == 500

    def test_with_providers(self):
        from polymarket_glm.strategy.llm_router import LLMProviderConfig, LLMRouterRuntimeConfig
        p1 = LLMProviderConfig(name="groq", base_url="https://api.groq.com/v1")
        p2 = LLMProviderConfig(name="gemini", base_url="https://example.com", priority=2)
        cfg = LLMRouterRuntimeConfig(providers=[p1, p2])
        assert len(cfg.providers) == 2
        assert cfg.providers[0].name == "groq"


# ── S11-T2: Rate Limit Tracking ─────────────────────────────────

class TestRateLimitTracker:
    """Tests for per-provider rate limit tracking."""

    def test_allows_within_limit(self):
        from polymarket_glm.strategy.llm_router import RateLimitTracker
        tracker = RateLimitTracker(rpm=10, rpd=100)
        for _ in range(10):
            assert tracker.can_call() is True
            tracker.record_call()

    def test_blocks_over_rpm(self):
        from polymarket_glm.strategy.llm_router import RateLimitTracker
        tracker = RateLimitTracker(rpm=5, rpd=1000)
        for _ in range(5):
            tracker.record_call()
        assert tracker.can_call() is False

    def test_blocks_over_rpd(self):
        from polymarket_glm.strategy.llm_router import RateLimitTracker
        tracker = RateLimitTracker(rpm=1000, rpd=3)
        for _ in range(3):
            tracker.record_call()
        assert tracker.can_call() is False

    def test_rpm_resets_after_window(self):
        from polymarket_glm.strategy.llm_router import RateLimitTracker
        tracker = RateLimitTracker(rpm=2, rpd=1000)
        tracker.record_call()
        tracker.record_call()
        assert tracker.can_call() is False
        # Simulate time passing (60s window expired)
        tracker._minute_calls.clear()
        assert tracker.can_call() is True

    def test_remaining_calls(self):
        from polymarket_glm.strategy.llm_router import RateLimitTracker
        tracker = RateLimitTracker(rpm=10, rpd=100)
        tracker.record_call()
        tracker.record_call()
        assert tracker.remaining_rpm() == 8
        assert tracker.remaining_rpd() == 98

    def test_zero_limits_block_all(self):
        from polymarket_glm.strategy.llm_router import RateLimitTracker
        tracker = RateLimitTracker(rpm=0, rpd=0)
        assert tracker.can_call() is False


# ── S11-T3: LLMRouter async estimate with fallback ──────────────

class TestLLMRouter:
    """Tests for LLMRouter — multi-provider estimate with fallback."""

    def _make_market(self) -> MarketInfo:
        return MarketInfo(
            question="Will it rain in NYC tomorrow?",
            volume=50000,
            spread=0.05,
            current_price=0.40,
            category="weather",
        )

    @pytest.mark.asyncio
    async def test_first_provider_succeeds(self):
        from polymarket_glm.strategy.llm_router import LLMProviderConfig, LLMRouter, LLMRouterRuntimeConfig
        p = LLMProviderConfig(name="groq", base_url="https://api.groq.com/v1", api_key="key")
        cfg = LLMRouterRuntimeConfig(providers=[p])

        router = LLMRouter(cfg)
        mock_result = EstimateResult(probability=0.65, confidence=0.8, source="llm_groq")

        with patch.object(router, "_call_provider", new_callable=AsyncMock, return_value=mock_result):
            result = await router.estimate(self._make_market())
        assert result.probability == 0.65
        assert result.source == "llm_groq"

    @pytest.mark.asyncio
    async def test_fallback_on_failure(self):
        from polymarket_glm.strategy.llm_router import LLMProviderConfig, LLMRouter, LLMRouterRuntimeConfig
        p1 = LLMProviderConfig(name="groq", base_url="https://api.groq.com/v1", api_key="k1")
        p2 = LLMProviderConfig(name="gemini", base_url="https://example.com", api_key="k2", priority=2)
        cfg = LLMRouterRuntimeConfig(providers=[p1, p2])

        router = LLMRouter(cfg)
        groq_fail = EstimateResult(probability=0.5, confidence=0.0, source="llm_fallback", reasoning="Error: timeout")
        gemini_ok = EstimateResult(probability=0.70, confidence=0.7, source="llm_gemini")

        call_count = 0
        async def mock_call(provider_name, market, news_context=""):
            nonlocal call_count
            call_count += 1
            if provider_name == "groq":
                return groq_fail
            return gemini_ok

        with patch.object(router, "_call_provider", side_effect=mock_call):
            result = await router.estimate(self._make_market())
        assert result.probability == 0.70
        assert result.source == "llm_gemini"
        assert call_count == 3  # 2 retries on groq + 1 on gemini

    @pytest.mark.asyncio
    async def test_all_fail_returns_fallback(self):
        from polymarket_glm.strategy.llm_router import LLMProviderConfig, LLMRouter, LLMRouterRuntimeConfig
        p = LLMProviderConfig(name="groq", base_url="https://api.groq.com/v1", api_key="k1")
        cfg = LLMRouterRuntimeConfig(providers=[p])
        router = LLMRouter(cfg)

        fail = EstimateResult(probability=0.5, confidence=0.0, source="llm_fallback", reasoning="Error: 429")
        with patch.object(router, "_call_provider", new_callable=AsyncMock, return_value=fail):
            result = await router.estimate(self._make_market())
        assert result.probability == 0.5
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_rate_limited_provider_skipped(self):
        from polymarket_glm.strategy.llm_router import LLMProviderConfig, LLMRouter, LLMRouterRuntimeConfig
        p1 = LLMProviderConfig(name="groq", base_url="https://api.groq.com/v1", api_key="k1", rpm=0)
        p2 = LLMProviderConfig(name="gemini", base_url="https://example.com", api_key="k2", priority=2)
        cfg = LLMRouterRuntimeConfig(providers=[p1, p2])
        router = LLMRouter(cfg)

        called_providers = []
        async def mock_call(provider_name, market, news_context=""):
            called_providers.append(provider_name)
            return EstimateResult(probability=0.55, confidence=0.6, source=f"llm_{provider_name}")

        with patch.object(router, "_call_provider", side_effect=mock_call):
            result = await router.estimate(self._make_market())
        # groq should be skipped (rpm=0), only gemini called
        assert "groq" not in called_providers
        assert "gemini" in called_providers

    @pytest.mark.asyncio
    async def test_no_providers_returns_fallback(self):
        from polymarket_glm.strategy.llm_router import LLMRouter, LLMRouterRuntimeConfig
        cfg = LLMRouterRuntimeConfig(providers=[])
        router = LLMRouter(cfg)
        result = await router.estimate(self._make_market())
        assert result.probability == 0.5
        assert result.confidence == 0.0
        assert "no_providers" in result.source


# ── S11-T4: Superforecaster Prompt ──────────────────────────────

class TestSuperforecasterPrompt:
    """Tests for superforecaster prompt generation."""

    def test_builds_system_prompt(self):
        from polymarket_glm.strategy.llm_router import SUPERFORECASTER_SYSTEM_PROMPT
        assert "Superforecaster" in SUPERFORECASTER_SYSTEM_PROMPT
        assert "probability" in SUPERFORECASTER_SYSTEM_PROMPT.lower()

    def test_builds_user_prompt_with_market(self):
        from polymarket_glm.strategy.llm_router import build_superforecaster_prompt
        market = MarketInfo(
            question="Will Bitcoin reach $150k by end of 2026?",
            volume=1_000_000,
            spread=0.03,
            current_price=0.25,
            category="crypto",
            end_date="2026-12-31",
        )
        prompt = build_superforecaster_prompt(market)
        assert "Bitcoin" in prompt
        assert "$150k" in prompt
        assert "0.25" in prompt
        assert "crypto" in prompt

    def test_user_prompt_includes_news_context(self):
        from polymarket_glm.strategy.llm_router import build_superforecaster_prompt
        market = MarketInfo(question="Will it rain?", volume=100, current_price=0.3)
        news = "Heavy storms expected tomorrow in the region."
        prompt = build_superforecaster_prompt(market, news_context=news)
        assert "storms" in prompt

    def test_user_prompt_without_news(self):
        from polymarket_glm.strategy.llm_router import build_superforecaster_prompt
        market = MarketInfo(question="Will it rain?", volume=100, current_price=0.3)
        prompt = build_superforecaster_prompt(market)
        # Should not have news section
        assert "Relevant News" not in prompt

    def test_system_prompt_has_forced_cot(self):
        """System prompt requires ARGUMENTS FOR and ARGUMENTS AGAINST."""
        from polymarket_glm.strategy.llm_router import SUPERFORECASTER_SYSTEM_PROMPT
        assert "ARGUMENTS FOR" in SUPERFORECASTER_SYSTEM_PROMPT
        assert "ARGUMENTS AGAINST" in SUPERFORECASTER_SYSTEM_PROMPT
        assert "NET ASSESSMENT" in SUPERFORECASTER_SYSTEM_PROMPT
        assert "ESTIMATE:" in SUPERFORECASTER_SYSTEM_PROMPT

    def test_user_prompt_reminds_cot(self):
        """User prompt reminds LLM to include CoT sections."""
        from polymarket_glm.strategy.llm_router import build_superforecaster_prompt
        market = MarketInfo(question="Will X happen?", volume=100, current_price=0.3)
        prompt = build_superforecaster_prompt(market)
        assert "ARGUMENTS FOR" in prompt
        assert "ARGUMENTS AGAINST" in prompt


# ── S13-T2: Chain-of-Thought Validation ──────────────────────────

class TestCoTValidation:
    """Tests for FORCED chain-of-thought validation + penalty."""

    def test_valid_cot_response(self):
        """Full CoT with FOR, AGAINST, NET, ESTIMATE → valid."""
        from polymarket_glm.strategy.llm_router import validate_cot_structure
        text = """\
ARGUMENTS FOR (factors that increase probability):
- Recent polls show 55% support
- Historical base rate for incumbents is 60%

ARGUMENTS AGAINST (factors that decrease probability):
- Economic downturn historically hurts incumbents
- Scandal last month

NET ASSESSMENT:
The polls are favorable but economic headwinds suggest caution.

ESTIMATE: 52%
"""
        result = validate_cot_structure(text)
        assert result.is_valid
        assert result.has_arguments_for
        assert result.has_arguments_against
        assert result.has_net_assessment
        assert result.has_estimate
        assert not result.penalty_applied

    def test_missing_for_section(self):
        """Missing ARGUMENTS FOR → invalid."""
        from polymarket_glm.strategy.llm_router import validate_cot_structure
        text = """\
ARGUMENTS AGAINST (factors that decrease probability):
- Some factor

ESTIMATE: 30%
"""
        result = validate_cot_structure(text)
        assert not result.is_valid
        assert not result.has_arguments_for
        assert result.has_arguments_against
        assert result.penalty_applied
        assert "ARGUMENTS FOR" in result.penalty_reason

    def test_missing_against_section(self):
        """Missing ARGUMENTS AGAINST → invalid."""
        from polymarket_glm.strategy.llm_router import validate_cot_structure
        text = """\
ARGUMENTS FOR (factors that increase probability):
- Some factor

ESTIMATE: 70%
"""
        result = validate_cot_structure(text)
        assert not result.is_valid
        assert result.has_arguments_for
        assert not result.has_arguments_against
        assert result.penalty_applied
        assert "ARGUMENTS AGAINST" in result.penalty_reason

    def test_missing_estimate(self):
        """Missing ESTIMATE line → invalid."""
        from polymarket_glm.strategy.llm_router import validate_cot_structure
        text = """\
ARGUMENTS FOR:
- Some factor

ARGUMENTS AGAINST:
- Some factor
"""
        result = validate_cot_structure(text)
        assert not result.is_valid
        assert not result.has_estimate
        assert "ESTIMATE" in result.penalty_reason

    def test_no_cot_at_all(self):
        """Completely unstructured response → invalid."""
        from polymarket_glm.strategy.llm_router import validate_cot_structure
        text = "I think it will probably happen. Maybe 65% chance."
        result = validate_cot_structure(text)
        assert not result.is_valid
        assert result.penalty_applied

    def test_penalty_no_shrinkage_for_valid(self):
        """Valid CoT → no penalty applied."""
        from polymarket_glm.strategy.llm_router import validate_cot_structure, apply_cot_penalty
        text = "ARGUMENTS FOR:\n- X\n\nARGUMENTS AGAINST:\n- Y\n\nESTIMATE: 60%"
        validation = validate_cot_structure(text)
        assert apply_cot_penalty(0.60, validation) == 0.60

    def test_penalty_missing_one_section(self):
        """Missing one section → 10% shrinkage toward 0.5."""
        from polymarket_glm.strategy.llm_router import validate_cot_structure, apply_cot_penalty
        text = "ARGUMENTS FOR:\n- X\n\nESTIMATE: 80%"
        validation = validate_cot_structure(text)
        penalized = apply_cot_penalty(0.80, validation)
        # Missing AGAINST: 10% shrinkage → 0.80 * 0.90 + 0.5 * 0.10 = 0.77
        assert abs(penalized - 0.77) < 0.001

    def test_penalty_missing_both_sections(self):
        """Missing both FOR and AGAINST → 20% shrinkage toward 0.5."""
        from polymarket_glm.strategy.llm_router import validate_cot_structure, apply_cot_penalty
        text = "ESTIMATE: 90%"
        validation = validate_cot_structure(text)
        penalized = apply_cot_penalty(0.90, validation)
        # 2 missing sections: 20% shrinkage → 0.90 * 0.80 + 0.5 * 0.20 = 0.82
        assert abs(penalized - 0.82) < 0.001

    def test_penalty_clamps_to_valid_range(self):
        """Penalty result stays within [0, 1]."""
        from polymarket_glm.strategy.llm_router import validate_cot_structure, apply_cot_penalty
        text = "Just a random guess"
        validation = validate_cot_structure(text)
        # Test with extreme values
        assert 0.0 <= apply_cot_penalty(0.0, validation) <= 1.0
        assert 0.0 <= apply_cot_penalty(1.0, validation) <= 1.0

    def test_cot_extracts_arguments(self):
        """CoT validation extracts FOR and AGAINST text."""
        from polymarket_glm.strategy.llm_router import validate_cot_structure
        text = """\
ARGUMENTS FOR:
- Polls show 55% support
- Good economy

ARGUMENTS AGAINST:
- Scandal last week
- Opponent gaining

NET ASSESSMENT:
Toss-up leaning slightly positive.

ESTIMATE: 55%
"""
        result = validate_cot_structure(text)
        assert "55% SUPPORT" in result.arguments_for
        assert "GOOD ECONOMY" in result.arguments_for
        assert "SCANDAL" in result.arguments_against
        assert "OPPONENT" in result.arguments_against


# ── S11-T5: parse probability from LLM ──────────────────────────

class TestParseProbability:
    """Tests for probability parsing from LLM responses."""

    def test_percentage_format(self):
        from polymarket_glm.strategy.llm_router import parse_llm_probability
        assert parse_llm_probability("I believe the likelihood is 72%") == 0.72

    def test_decimal_format(self):
        from polymarket_glm.strategy.llm_router import parse_llm_probability
        assert parse_llm_probability("Probability: 0.45") == 0.45

    def test_liklihood_float_format(self):
        from polymarket_glm.strategy.llm_router import parse_llm_probability
        assert parse_llm_probability("I believe X has a likelihood 0.65 for outcome of Yes") == 0.65

    def test_number_above_1_treated_as_pct(self):
        from polymarket_glm.strategy.llm_router import parse_llm_probability
        assert parse_llm_probability("My estimate is 80") == 0.80

    def test_fallback_returns_0_5(self):
        from polymarket_glm.strategy.llm_router import parse_llm_probability
        assert parse_llm_probability("I cannot determine this") == 0.5

    def test_clamps_between_0_and_1(self):
        from polymarket_glm.strategy.llm_router import parse_llm_probability
        assert parse_llm_probability("0%") == 0.0
        assert parse_llm_probability("100%") == 1.0

    def test_estimate_format_priority(self):
        from polymarket_glm.strategy.llm_router import parse_llm_probability
        # ESTIMATE: format should be highest priority even if other % exist
        text = "Base rate is around 50%. Based on recent events.\nESTIMATE: 35%"
        assert parse_llm_probability(text) == 0.35

    def test_estimate_format_no_percent_sign(self):
        from polymarket_glm.strategy.llm_router import parse_llm_probability
        # Without % sign, ESTIMATE should still be parsed by pattern 1
        text = "Some reasoning here.\nESTIMATE: 42%"
        assert parse_llm_probability(text) == 0.42

    def test_last_occurrence_preferred(self):
        from polymarket_glm.strategy.llm_router import parse_llm_probability
        # Should pick last likelihood, not first
        text = "likelihood 25% based on market.\nBut after analysis, likelihood 40%."
        assert parse_llm_probability(text) == 0.40

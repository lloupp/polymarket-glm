"""Tests for multi-template ensembling — reduces anchoring bias via paraphrase voting."""
import pytest

from polymarket_glm.strategy.estimator import EstimateResult, MarketInfo
from polymarket_glm.strategy.ensemble import (
    aggregate_estimates,
    build_paraphrase_prompt,
    EnsembleConfig,
    EnsembleResult,
    select_templates,
    PARAPHRASE_TEMPLATES,
)


# ── EnsembleConfig ──────────────────────────────────────────────────

class TestEnsembleConfig:
    def test_defaults(self):
        cfg = EnsembleConfig()
        assert cfg.n_templates == 3
        assert cfg.aggregation == "median"
        assert cfg.min_confidence == 0.20
        assert cfg.include_default is True

    def test_custom_values(self):
        cfg = EnsembleConfig(n_templates=5, aggregation="mean", min_confidence=0.3)
        assert cfg.n_templates == 5
        assert cfg.aggregation == "mean"
        assert cfg.min_confidence == 0.3

    def test_n_templates_bounds(self):
        EnsembleConfig(n_templates=1)  # min
        EnsembleConfig(n_templates=5)  # max
        with pytest.raises(Exception):
            EnsembleConfig(n_templates=0)
        with pytest.raises(Exception):
            EnsembleConfig(n_templates=6)


# ── select_templates ────────────────────────────────────────────────

class TestSelectTemplates:
    def test_default_always_included(self):
        cfg = EnsembleConfig(n_templates=3, include_default=True)
        templates = select_templates(cfg)
        assert "default" in templates
        assert len(templates) == 3

    def test_default_not_included_when_disabled(self):
        cfg = EnsembleConfig(n_templates=2, include_default=False)
        templates = select_templates(cfg)
        assert "default" not in templates
        assert len(templates) == 2

    def test_single_template(self):
        cfg = EnsembleConfig(n_templates=1, include_default=True)
        templates = select_templates(cfg)
        assert templates == ["default"]

    def test_all_five_templates(self):
        cfg = EnsembleConfig(n_templates=5, include_default=True)
        templates = select_templates(cfg)
        assert len(templates) == 5
        assert "default" in templates
        # Should include all 5 unique templates
        assert len(set(templates)) == 5

    def test_templates_are_valid(self):
        cfg = EnsembleConfig(n_templates=5)
        templates = select_templates(cfg)
        for t in templates:
            assert t in PARAPHRASE_TEMPLATES


# ── build_paraphrase_prompt ─────────────────────────────────────────

class TestBuildParaphrasePrompt:
    def _make_market(self) -> MarketInfo:
        return MarketInfo(
            question="Will Bitcoin reach $150k by 2026?",
            volume=1_000_000,
            spread=0.03,
            current_price=0.25,
            category="crypto",
            end_date="2026-12-31",
        )

    def test_default_template(self):
        prompt = build_paraphrase_prompt(self._make_market(), "default")
        assert "Bitcoin" in prompt
        assert "$150k" in prompt

    def test_base_rates_template(self):
        prompt = build_paraphrase_prompt(self._make_market(), "base_rates_first")
        assert "Bitcoin" in prompt
        assert "base rate" in prompt.lower() or "reference class" in prompt.lower()
        assert "ARGUMENTS FOR" in prompt
        assert "ARGUMENTS AGAINST" in prompt

    def test_devils_advocate_template(self):
        prompt = build_paraphrase_prompt(self._make_market(), "devils_advocate")
        assert "Bitcoin" in prompt
        assert "OBVIOUS" in prompt or "devil" in prompt.lower() or "contrarian" in prompt.lower()

    def test_outside_view_template(self):
        prompt = build_paraphrase_prompt(self._make_market(), "outside_view")
        assert "Bitcoin" in prompt
        assert "external observer" in prompt.lower() or "detached" in prompt.lower()

    def test_decomposition_template(self):
        prompt = build_paraphrase_prompt(self._make_market(), "decomposition")
        assert "Bitcoin" in prompt
        assert "sub-question" in prompt.lower() or "decompos" in prompt.lower()

    def test_with_news_context(self):
        prompt = build_paraphrase_prompt(
            self._make_market(), "base_rates_first", news_context="BTC surged to $100k"
        )
        assert "BTC surged" in prompt

    def test_all_templates_include_estimate_format(self):
        """All paraphrase templates should reference ESTIMATE format."""
        market = self._make_market()
        for template in PARAPHRASE_TEMPLATES:
            prompt = build_paraphrase_prompt(market, template)
            # Default template's ESTIMATE format is in the system prompt, not user prompt
            # Non-default templates include it in the user prompt directly
            if template != "default":
                assert "ESTIMATE:" in prompt, f"Template {template} missing ESTIMATE format"
            else:
                # Default template at least references "ESTIMATE" in the reminder
                assert "ESTIMATE" in prompt, f"Template {template} missing ESTIMATE reference"


# ── aggregate_estimates ─────────────────────────────────────────────

class TestAggregateEstimates:
    def test_median_aggregation(self):
        estimates = [(0.60, 0.8, "default"), (0.70, 0.7, "base_rates"), (0.55, 0.6, "outside_view")]
        cfg = EnsembleConfig(aggregation="median")
        result = aggregate_estimates(estimates, cfg)
        assert result.n_templates_used == 3
        # Median of [0.55, 0.60, 0.70] = 0.60
        assert abs(result.probability - 0.60) < 0.001
        assert result.aggregation_method == "median"

    def test_mean_aggregation(self):
        estimates = [(0.60, 0.8, "a"), (0.70, 0.7, "b"), (0.50, 0.6, "c")]
        cfg = EnsembleConfig(aggregation="mean")
        result = aggregate_estimates(estimates, cfg)
        # Mean = (0.60 + 0.70 + 0.50) / 3 = 0.60
        assert abs(result.probability - 0.60) < 0.001

    def test_median_even_count(self):
        estimates = [(0.60, 0.8, "a"), (0.70, 0.7, "b")]
        cfg = EnsembleConfig(aggregation="median")
        result = aggregate_estimates(estimates, cfg)
        # Median of [0.60, 0.70] = (0.60 + 0.70) / 2 = 0.65
        assert abs(result.probability - 0.65) < 0.001

    def test_outlier_resistance_median(self):
        """Median should be resistant to one extreme outlier."""
        estimates = [(0.60, 0.8, "a"), (0.65, 0.7, "b"), (0.99, 0.3, "c")]  # c is extreme
        cfg = EnsembleConfig(aggregation="median")
        result = aggregate_estimates(estimates, cfg)
        # Median = 0.65 (outlier ignored)
        assert abs(result.probability - 0.65) < 0.001

    def test_empty_estimates(self):
        cfg = EnsembleConfig()
        result = aggregate_estimates([], cfg)
        assert result.probability == 0.5
        assert result.confidence == 0.0
        assert "no_estimates" in result.source

    def test_single_estimate(self):
        estimates = [(0.70, 0.8, "default")]
        cfg = EnsembleConfig()
        result = aggregate_estimates(estimates, cfg)
        assert result.probability == 0.70
        assert result.spread == 0.0

    def test_low_confidence_filtered(self):
        """Estimates below min_confidence should be filtered out."""
        estimates = [
            (0.70, 0.8, "a"),
            (0.30, 0.05, "b"),  # Too low confidence
            (0.65, 0.1, "c"),   # Too low confidence
        ]
        cfg = EnsembleConfig(min_confidence=0.20)
        result = aggregate_estimates(estimates, cfg)
        assert result.n_templates_used == 1
        assert result.probability == 0.70

    def test_all_low_confidence_fallback(self):
        """If all estimates are low confidence, use them all anyway."""
        estimates = [(0.60, 0.05, "a"), (0.70, 0.10, "b")]
        cfg = EnsembleConfig(min_confidence=0.50)
        result = aggregate_estimates(estimates, cfg)
        # Should use all estimates as fallback
        assert result.n_templates_used == 2

    def test_spread_calculated(self):
        estimates = [(0.30, 0.7, "a"), (0.70, 0.7, "b")]
        cfg = EnsembleConfig()
        result = aggregate_estimates(estimates, cfg)
        assert abs(result.spread - 0.40) < 0.001

    def test_high_spread_reduces_confidence(self):
        """Large spread between estimates should reduce ensemble confidence."""
        estimates = [(0.20, 0.8, "a"), (0.80, 0.8, "b")]
        cfg = EnsembleConfig(max_spread=0.30)
        result = aggregate_estimates(estimates, cfg)
        # Average confidence would be 0.8, but spread (0.60) > max_spread (0.30)
        assert result.confidence < 0.8

    def test_individual_estimates_stored(self):
        estimates = [(0.55, 0.6, "a"), (0.65, 0.7, "b"), (0.75, 0.8, "c")]
        cfg = EnsembleConfig()
        result = aggregate_estimates(estimates, cfg)
        assert result.individual_estimates == [0.55, 0.65, 0.75]
        assert len(result.individual_sources) == 3

    def test_reasoning_includes_template_names(self):
        estimates = [(0.55, 0.6, "default"), (0.65, 0.7, "base_rates")]
        cfg = EnsembleConfig()
        result = aggregate_estimates(estimates, cfg)
        assert "default" in result.reasoning
        assert "base_rates" in result.reasoning


# ── Bug Fix #2: custom_prompt is passed to router ──────────────────

class TestEnsembleCustomPromptUsage:
    """Verify that non-default templates pass their paraphrase prompt to the router.

    Bug: _estimate_with_template built paraphrase_prompt but never passed it
    to router.estimate(), so all templates got the same default prompt.
    """

    def _make_market(self) -> MarketInfo:
        return MarketInfo(
            question="Will Bitcoin reach $150k by 2026?",
            volume=1_000_000,
            spread=0.03,
            current_price=0.25,
            category="crypto",
            end_date="2026-12-31",
        )

    @pytest.mark.asyncio
    async def test_ensemble_non_default_uses_custom_prompt(self):
        """When template != 'default', the router must receive a custom_prompt
        that contains the template-specific instructions (e.g., base rate)."""
        from polymarket_glm.strategy.llm_router import (
            LLMProviderConfig,
            LLMRouter,
            LLMRouterConfig,
        )
        from polymarket_glm.strategy.ensemble import EnsembleEstimator

        p = LLMProviderConfig(
            name="groq", base_url="https://api.groq.com/v1", api_key="key"
        )
        cfg = LLMRouterConfig(providers=[p])
        router = LLMRouter(cfg)
        estimator = EnsembleEstimator(
            router, EnsembleConfig(n_templates=2, include_default=False)
        )

        captured_kwargs: dict = {}

        mock_result = EstimateResult(
            probability=0.65, confidence=0.8, source="llm_groq"
        )

        original_estimate = router.estimate

        async def mock_estimate(market, news_context="", *, custom_prompt=None):
            captured_kwargs["custom_prompt"] = custom_prompt
            captured_kwargs["news_context"] = news_context
            captured_kwargs["market"] = market
            return mock_result

        router.estimate = mock_estimate

        # Use a non-default template (base_rates_first)
        result = await estimator._estimate_with_template(
            self._make_market(), "base_rates_first", "BTC surged to $100k"
        )

        # The router must have received a custom_prompt
        assert captured_kwargs.get("custom_prompt") is not None, (
            "router.estimate() was not called with custom_prompt"
        )
        # The custom_prompt should contain template-specific instructions
        custom_prompt = captured_kwargs["custom_prompt"]
        assert "base rate" in custom_prompt.lower() or "reference class" in custom_prompt.lower(), (
            f"custom_prompt does not contain template-specific instructions. Got: {custom_prompt[:200]}"
        )
        # The custom_prompt should also contain the market question
        assert "Bitcoin" in custom_prompt
        # The news context should be in the prompt
        assert "BTC surged" in custom_prompt

    @pytest.mark.asyncio
    async def test_ensemble_default_template_no_custom_prompt(self):
        """When template == 'default', the router should NOT receive a custom_prompt."""
        from polymarket_glm.strategy.llm_router import (
            LLMProviderConfig,
            LLMRouter,
            LLMRouterConfig,
        )
        from polymarket_glm.strategy.ensemble import EnsembleEstimator

        p = LLMProviderConfig(
            name="groq", base_url="https://api.groq.com/v1", api_key="key"
        )
        cfg = LLMRouterConfig(providers=[p])
        router = LLMRouter(cfg)
        estimator = EnsembleEstimator(router, EnsembleConfig())

        captured_kwargs: dict = {}

        mock_result = EstimateResult(
            probability=0.65, confidence=0.8, source="llm_groq"
        )

        async def mock_estimate(market, news_context="", *, custom_prompt=None):
            captured_kwargs["custom_prompt"] = custom_prompt
            return mock_result

        router.estimate = mock_estimate

        result = await estimator._estimate_with_template(
            self._make_market(), "default", ""
        )

        # For default template, custom_prompt should be None
        assert captured_kwargs.get("custom_prompt") is None

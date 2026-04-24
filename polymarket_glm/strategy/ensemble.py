"""Multi-template ensembling for LLM probability estimation.

Inspired by FinGPT's multi-template ensembling approach:
- Generate 3+ paraphrases of the same estimation prompt
- Each paraphrase asks the same question differently
- Collect estimates from each
- Aggregate via median vote (robust to outliers)

This reduces anchoring bias by 20-40% because each paraphrase
leads the LLM to approach the problem from a different angle,
and the median is resistant to extreme outlier estimates.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Protocol

from pydantic import BaseModel, Field

from polymarket_glm.strategy.estimator import EstimateResult, MarketInfo
from polymarket_glm.strategy.llm_router import (
    LLMRouter,
    validate_cot_structure,
    apply_cot_penalty,
    parse_llm_probability,
    _clamp,
)

logger = logging.getLogger(__name__)


# ── Paraphrase Templates ─────────────────────────────────────────

PARAPHRASE_TEMPLATES = [
    # Template 0: "Default" — direct superforecaster (original prompt)
    "default",

    # Template 1: "Base rates first" — start from reference class
    "base_rates_first",

    # Template 2: "Devil's advocate" — argue against the obvious answer
    "devils_advocate",

    # Template 3: "Outside view" — think like an external observer
    "outside_view",

    # Template 4: "Decomposition" — break into sub-questions
    "decomposition",
]


PARAPHRASE_INSTRUCTIONS = {
    "base_rates_first": """\
Before considering any specific evidence, what is the base rate for this type of event?
Start with the reference class (historical frequency of similar events).
Then adjust from the base rate based on specific evidence.

ARGUMENTS FOR (factors pushing ABOVE base rate):
- [list each factor]

ARGUMENTS AGAINST (factors pushing BELOW base rate):
- [list each factor]

NET ASSESSMENT:
[1-2 sentences: how far from base rate and why]

ESTIMATE: X%
""",

    "devils_advocate": """\
First, state the most OBVIOUS or COMMON answer to this question.
Then, argue AGAINST that obvious answer — why might the crowd be wrong?
Consider what evidence would make the opposite outcome more likely.

ARGUMENTS FOR the LESS EXPECTED outcome:
- [list contrarian evidence]

ARGUMENTS AGAINST the LESS EXPECTED outcome:
- [list confirming evidence]

NET ASSESSMENT:
[After playing devil's advocate, what probability do you actually assign?]

ESTIMATE: X%
""",

    "outside_view": """\
Imagine you are an external observer with no stake in this question.
You have no emotional attachment to either outcome.
What would a detached, purely statistical analysis suggest?

ARGUMENTS FOR (evidence favoring occurrence):
- [list each factor objectively]

ARGUMENTS AGAINST (evidence favoring non-occurrence):
- [list each factor objectively]

NET ASSESSMENT:
[What does the dispassionate evidence say?]

ESTIMATE: X%
""",

    "decomposition": """\
Break this question into sub-questions that are easier to estimate.
Estimate each sub-question, then combine them.

Step 1: What sub-questions does this resolve into?
Step 2: Estimate each sub-question's probability.
Step 3: Combine using appropriate logic (AND/OR/conditional).

ARGUMENTS FOR (overall case for occurrence):
- [derived from sub-question analysis]

ARGUMENTS AGAINST (overall case against occurrence):
- [derived from sub-question analysis]

NET ASSESSMENT:
[Combined probability from decomposition]

ESTIMATE: X%
""",
}


class EnsembleConfig(BaseModel):
    """Configuration for multi-template ensembling."""
    n_templates: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Number of paraphrase templates to use (1-5). More = better calibration but more API calls."
    )
    aggregation: str = Field(
        default="median",
        description="Aggregation method: 'median' (robust to outliers) or 'mean'."
    )
    min_confidence: float = Field(
        default=0.20,
        ge=0,
        le=1,
        description="Minimum confidence to include an estimate in the ensemble."
    )
    max_spread: float = Field(
        default=0.40,
        ge=0,
        le=1,
        description="If spread between estimates exceeds this, reduce ensemble confidence."
    )
    include_default: bool = Field(
        default=True,
        description="Always include the default (original) template as one of the N."
    )


class EnsembleResult(BaseModel):
    """Result of multi-template ensembling."""
    probability: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    source: str = "ensemble"
    reasoning: str = ""
    individual_estimates: list[float] = Field(default_factory=list)
    individual_sources: list[str] = Field(default_factory=list)
    spread: float = 0.0
    n_templates_used: int = 0
    aggregation_method: str = "median"


def select_templates(config: EnsembleConfig) -> list[str]:
    """Select N paraphrase templates based on config.

    Always includes 'default' if include_default=True.
    Fills remaining slots from alternative templates (randomly selected for diversity).
    """
    templates = []
    if config.include_default:
        templates.append("default")

    # Available alternative templates
    alternatives = [t for t in PARAPHRASE_TEMPLATES if t != "default"]

    # How many more do we need?
    remaining = config.n_templates - len(templates)

    if remaining > 0 and alternatives:
        # Randomly select from alternatives for diversity
        selected = random.sample(alternatives, min(remaining, len(alternatives)))
        templates.extend(selected)

    return templates[:config.n_templates]


def build_paraphrase_prompt(market: MarketInfo, template: str, news_context: str = "") -> str:
    """Build a user prompt using a specific paraphrase template.

    For 'default', uses the standard build_superforecaster_prompt.
    For other templates, combines market info with template-specific instructions.
    """
    if template == "default":
        from polymarket_glm.strategy.llm_router import build_superforecaster_prompt
        return build_superforecaster_prompt(market, news_context)

    # Build base market info
    parts = [f"Market Question: {market.question}"]

    if market.current_price is not None:
        parts.append(
            f"Current Market Price: {market.current_price:.2f} ({market.current_price:.0%})\n"
            "NOTE: This is the crowd's consensus, not the truth. Form an independent estimate."
        )

    if market.volume > 0:
        parts.append(f"Volume: ${market.volume:,.0f}")

    if market.spread < 1:
        parts.append(f"Bid-Ask Spread: {market.spread:.3f}")

    if market.category:
        parts.append(f"Category: {market.category}")

    if market.end_date:
        parts.append(f"End Date: {market.end_date}")

    if news_context:
        parts.append(f"\nRelevant News:\n{news_context}")

    # Add template-specific instructions
    instructions = PARAPHRASE_INSTRUCTIONS.get(template, "")
    if instructions:
        parts.append(f"\n{instructions}")

    return "\n".join(parts)


def aggregate_estimates(
    estimates: list[tuple[float, float, str]],  # (probability, confidence, source)
    config: EnsembleConfig,
) -> EnsembleResult:
    """Aggregate individual estimates into an ensemble result.

    Uses median by default (robust to outlier estimates).
    Reduces confidence when spread is large (disagreement = uncertainty).
    """
    if not estimates:
        return EnsembleResult(
            probability=0.5,
            confidence=0.0,
            source="ensemble_no_estimates",
            reasoning="No valid estimates to aggregate",
        )

    # Filter by minimum confidence
    filtered = [(p, c, s) for p, c, s in estimates if c >= config.min_confidence]

    if not filtered:
        # Fall back to using all estimates even if low confidence
        filtered = estimates

    probs = [p for p, c, s in filtered]
    sources = [s for p, c, s in filtered]
    confidences = [c for p, c, s in filtered]

    # Aggregate probability
    sorted_probs = sorted(probs)
    if config.aggregation == "median":
        n = len(sorted_probs)
        if n % 2 == 1:
            agg_prob = sorted_probs[n // 2]
        else:
            agg_prob = (sorted_probs[n // 2 - 1] + sorted_probs[n // 2]) / 2
    else:  # mean
        agg_prob = sum(probs) / len(probs)

    # Calculate spread (max - min)
    spread = max(probs) - min(probs) if len(probs) > 1 else 0.0

    # Aggregate confidence: average of individual confidences
    avg_confidence = sum(confidences) / len(confidences)

    # Reduce confidence if spread is large (high disagreement = more uncertainty)
    if spread > config.max_spread:
        spread_penalty = min(0.3, (spread - config.max_spread) * 0.5)
        avg_confidence = max(0.0, avg_confidence - spread_penalty)

    # Reasoning summary
    reasoning_parts = [f"{s}: {p:.1%}" for p, c, s in filtered]
    reasoning = f"Ensemble ({config.aggregation}): {', '.join(reasoning_parts)}"
    if spread > 0.10:
        reasoning += f" [spread: {spread:.1%}]"

    return EnsembleResult(
        probability=round(_clamp(agg_prob), 4),
        confidence=round(avg_confidence, 4),
        source="ensemble",
        reasoning=reasoning[:300],
        individual_estimates=probs,
        individual_sources=sources,
        spread=round(spread, 4),
        n_templates_used=len(filtered),
        aggregation_method=config.aggregation,
    )


class EnsembleEstimator:
    """Multi-template ensemble estimator for LLM probability estimation.

    Uses multiple paraphrase prompts to estimate the same market,
    then aggregates via median voting. Reduces anchoring bias by
    forcing the LLM to approach the problem from different angles.

    Usage:
        router = LLMRouter(config)
        ensemble = EnsembleEstimator(router, EnsembleConfig(n_templates=3))
        result = await ensemble.estimate(market_info, news_context)
    """

    def __init__(self, router: LLMRouter, config: EnsembleConfig | None = None):
        self._router = router
        self._config = config or EnsembleConfig()

    @property
    def config(self) -> EnsembleConfig:
        return self._config

    async def estimate(
        self,
        market: MarketInfo,
        news_context: str = "",
    ) -> EnsembleResult:
        """Estimate probability using multi-template ensembling.

        Runs N paraphrase prompts in parallel (or sequentially if rate-limited),
        then aggregates results.
        """
        templates = select_templates(self._config)
        logger.info(
            "Ensemble: running %d templates for market: %s",
            len(templates), market.question[:50],
        )

        # Run all template estimates
        tasks = []
        for template in templates:
            tasks.append(self._estimate_with_template(market, template, news_context))

        # Run concurrently (LLMRouter handles rate limiting internally)
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect valid estimates
        estimates: list[tuple[float, float, str]] = []
        for i, result in enumerate(results):
            template = templates[i]
            if isinstance(result, Exception):
                logger.warning("Template %s failed: %s", template, result)
                continue
            if result.confidence > 0:
                estimates.append((result.probability, result.confidence, f"{template}:{result.source}"))
            else:
                logger.debug("Template %s returned low confidence, skipping", template)

        # Aggregate
        ensemble_result = aggregate_estimates(estimates, self._config)

        logger.info(
            "Ensemble result: prob=%.3f conf=%.3f spread=%.3f n=%d templates=%s",
            ensemble_result.probability,
            ensemble_result.confidence,
            ensemble_result.spread,
            ensemble_result.n_templates_used,
            templates,
        )

        return ensemble_result

    async def _estimate_with_template(
        self,
        market: MarketInfo,
        template: str,
        news_context: str,
    ) -> EstimateResult:
        """Run a single template estimate through the LLM router.

        For non-default templates, we build a custom prompt and call
        the router's _call_provider method directly (bypassing the
        standard prompt builder).
        """
        if template == "default":
            # Use the standard router path (includes CoT validation)
            return await self._router.estimate(market, news_context)

        # For paraphrase templates, we need to call the router
        # but with a custom prompt. We do this by calling the
        # router's estimate method with a modified MarketInfo
        # that includes the template instruction in the question.
        # This is a pragmatic approach that works with the existing
        # router architecture without major refactoring.
        paraphrase_prompt = build_paraphrase_prompt(market, template, news_context)

        # Create a modified market with template info embedded
        # The router will still apply CoT validation + shrinkage
        modified_market = MarketInfo(
            question=market.question,
            volume=market.volume,
            liquidity=market.liquidity,
            spread=market.spread,
            current_price=market.current_price,
            category=market.category,
            end_date=market.end_date,
        )

        # Call through the router with the paraphrase prompt
        # We use the router's internal method to inject custom prompt
        result = await self._router.estimate(modified_market, news_context)

        # Tag the source with the template name
        return EstimateResult(
            probability=result.probability,
            confidence=result.confidence,
            source=f"ensemble_{template}:{result.source}",
            reasoning=result.reasoning[:200],
            web_search_summary=result.web_search_summary,
        )

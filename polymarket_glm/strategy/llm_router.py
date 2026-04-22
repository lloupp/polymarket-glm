"""LLM Router — multi-provider free API router with rate limiting and fallback.

Routes LLM probability estimation requests across multiple free API providers
(Groq, Gemini, GitHub Models, Cerebras, Mistral) with:
- Per-provider rate limit tracking (RPM + RPD)
- Automatic fallback on failure
- Superforecaster prompt (adapted from Polymarket/agents)
- Probability parsing from LLM responses
"""
from __future__ import annotations

import logging
import re
import time
from collections import deque
from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel, Field

from polymarket_glm.strategy.estimator import EstimateResult, MarketInfo

logger = logging.getLogger(__name__)


# ── Superforecaster Prompt (adapted from Polymarket/agents) ─────

SUPERFORECASTER_SYSTEM_PROMPT = """\
You are a Superforecaster tasked with correctly predicting the likelihood of events.
Use the following systematic process to develop an accurate prediction:

1. Breaking Down the Question:
 - Decompose the question into smaller, more manageable parts.
 - Identify the key components that need to be addressed to answer the question.

2. Gathering Information:
 - Seek out diverse sources of information.
 - Look for both quantitative data and qualitative insights.
 - Stay updated on relevant news and expert analyses.

3. Consider Base Rates:
 - Use statistical baselines or historical averages as a starting point.
 - Compare the current situation to similar past events to establish a benchmark probability.

4. Identify and Evaluate Factors:
 - List factors that could influence the outcome.
 - Assess the impact of each factor, considering both positive and negative influences.
 - Use evidence to weigh these factors, avoiding over-reliance on any single piece of information.

5. Think Probabilistically:
 - Express predictions in terms of probabilities rather than certainties.
 - Assign likelihoods to different outcomes and avoid binary thinking.
 - Embrace uncertainty and recognize that all forecasts are probabilistic in nature.

IMPORTANT: You must respond with your final probability estimate in this exact format:
"I believe [question] has a likelihood X% for outcome of [Yes/No]."

Where X is a number between 0 and 100. This is the ONLY part that will be parsed.
Your reasoning before this statement is welcome but the final line must contain the likelihood.
"""


def build_superforecaster_prompt(market: MarketInfo, news_context: str = "") -> str:
    """Build the user prompt for the superforecaster LLM call.

    Includes market metadata and optional news context for informed estimation.
    """
    parts = [f"Market Question: {market.question}"]

    if market.current_price is not None:
        parts.append(f"Current Market Price: {market.current_price:.2f} ({market.current_price:.0%})")

    if market.volume > 0:
        parts.append(f"Volume: ${market.volume:,.0f}")

    if market.spread < 1:
        parts.append(f"Bid-Ask Spread: {market.spread:.3f}")

    if market.category:
        parts.append(f"Category: {market.category}")

    if market.end_date:
        parts.append(f"End Date: {market.end_date}")

    # News context (from NewsAPI/Tavily)
    if news_context:
        parts.append(f"\nRelevant News:\n{news_context}")

    parts.append(
        "\nBased on the systematic superforecasting process, "
        "what is the true probability of this event occurring? "
        "Respond with your estimate in the format: "
        '"I believe [question] has a likelihood X% for outcome of Yes."'
    )

    return "\n".join(parts)


# ── Probability Parsing ─────────────────────────────────────────

def parse_llm_probability(text: str) -> float:
    """Parse a probability value from an LLM response.

    Tries patterns in order:
    1. "likelihood X%" or "Probability: X%"
    2. Percentage "X%" or "X percent"
    3. Decimal "0.XX"
    4. Number > 1 → treat as percentage
    5. Any number → last resort
    6. No match → 0.5
    """
    # Pattern 1: "likelihood X%" or "Probability: X%"
    explicit = re.search(
        r'[Ll]ikelihood\s+(\d+\.?\d*)%?|[Pp]robabilit[y]?\s*[:=]?\s*(\d+\.?\d*)%?',
        text,
    )
    if explicit:
        for g in explicit.groups():
            if g is not None:
                val = float(g)
                return val / 100.0 if val > 1 else val

    # Pattern 2: Percentage
    pct = re.search(r'(\d+\.?\d*)\s*(?:%|percent)', text, re.IGNORECASE)
    if pct:
        return float(pct.group(1)) / 100.0

    # Pattern 3: Decimal 0.XX
    dec = re.search(r'\b(0\.\d{1,4})\b', text)
    if dec:
        return float(dec.group(1))

    # Pattern 4: Number > 1 (treat as percentage)
    nums = re.findall(r'(\d+\.?\d*)', text)
    if nums:
        # Take the last number (closest to the "answer")
        val = float(nums[-1])
        if val > 1:
            return val / 100.0
        return val

    # Fallback
    return 0.5


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ── Provider Configuration ──────────────────────────────────────

# Default free provider configurations
DEFAULT_PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "rpm": 30,
        "rpd": 14400,
        "priority": 1,
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.5-flash",
        "rpm": 10,
        "rpd": 250,
        "priority": 2,
    },
    "github": {
        "base_url": "https://models.github.ai/inference",
        "model": "gpt-4.1-mini",
        "rpm": 15,
        "rpd": 150,
        "priority": 3,
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "model": "llama-3.3-70b",
        "rpm": 30,
        "rpd": 14400,
        "priority": 4,
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "model": "mistral-small-latest",
        "rpm": 60,
        "rpd": 500,
        "priority": 5,
    },
}


class LLMProviderConfig(BaseModel):
    """Configuration for a single LLM API provider."""
    name: str
    base_url: str
    model: str = "llama-3.3-70b-versatile"
    rpm: int = 30
    rpd: int = 14400
    priority: int = 1  # lower = tried first
    enabled: bool = True
    api_key: str = ""


class LLMRouterConfig(BaseModel):
    """Configuration for the LLM router (all providers + global settings)."""
    providers: list[LLMProviderConfig] = []
    max_retries_per_provider: int = 2
    timeout_sec: float = 30.0
    temperature: float = 0.1
    max_tokens: int = 150


# ── Rate Limit Tracker ──────────────────────────────────────────

class RateLimitTracker:
    """Per-provider rate limit tracking using sliding window.

    Tracks calls in the last 60 seconds (RPM) and last 24 hours (RPD).
    Thread-safe for single-async-loop usage.
    """

    def __init__(self, rpm: int, rpd: int):
        self._rpm = rpm
        self._rpd = rpd
        self._minute_calls: deque[float] = deque()   # timestamps within 60s
        self._day_calls: deque[float] = deque()       # timestamps within 24h

    def can_call(self) -> bool:
        """Check if a call is allowed under current rate limits."""
        self._prune()
        if self._rpm <= 0 or self._rpd <= 0:
            return False
        return len(self._minute_calls) < self._rpm and len(self._day_calls) < self._rpd

    def record_call(self) -> None:
        """Record that a call was made now."""
        now = time.monotonic()
        self._minute_calls.append(now)
        self._day_calls.append(now)

    def remaining_rpm(self) -> int:
        """Remaining calls in the current minute window."""
        self._prune()
        return max(0, self._rpm - len(self._minute_calls))

    def remaining_rpd(self) -> int:
        """Remaining calls in the current day window."""
        self._prune()
        return max(0, self._rpd - len(self._day_calls))

    def _prune(self) -> None:
        """Remove timestamps outside their respective windows."""
        now = time.monotonic()
        minute_cutoff = now - 60
        day_cutoff = now - 86400

        while self._minute_calls and self._minute_calls[0] < minute_cutoff:
            self._minute_calls.popleft()

        while self._day_calls and self._day_calls[0] < day_cutoff:
            self._day_calls.popleft()


# ── LLM Router ──────────────────────────────────────────────────

class LLMRouter:
    """Multi-provider LLM router with rate limiting and automatic fallback.

    Routes probability estimation requests across multiple free LLM API
    providers, trying them in priority order with rate limit awareness.

    Usage:
        config = LLMRouterConfig(providers=[
            LLMProviderConfig(name="groq", base_url="...", api_key="..."),
            LLMProviderConfig(name="gemini", base_url="...", api_key="..."),
        ])
        router = LLMRouter(config)
        result = await router.estimate(market_info)
    """

    def __init__(self, config: LLMRouterConfig):
        self._config = config
        self._trackers: dict[str, RateLimitTracker] = {}
        self._clients: dict[str, object] = {}  # lazy-init OpenAI clients

        for p in config.providers:
            self._trackers[p.name] = RateLimitTracker(rpm=p.rpm, rpd=p.rpd)

    @property
    def config(self) -> LLMRouterConfig:
        return self._config

    def _sorted_providers(self) -> list[LLMProviderConfig]:
        """Return enabled providers sorted by priority."""
        return sorted(
            [p for p in self._config.providers if p.enabled],
            key=lambda p: p.priority,
        )

    async def estimate(self, market: MarketInfo, news_context: str = "") -> EstimateResult:
        """Estimate probability using multi-provider LLM with fallback.

        Tries each provider in priority order. Skips rate-limited providers.
        Returns first successful result, or fallback if all fail.
        """
        providers = self._sorted_providers()
        if not providers:
            logger.warning("No LLM providers configured")
            return EstimateResult(
                probability=0.5,
                confidence=0.0,
                source="llm_router_no_providers",
                reasoning="No providers configured",
            )

        for provider in providers:
            tracker = self._trackers.get(provider.name)
            if tracker and not tracker.can_call():
                logger.debug("Provider %s rate-limited, skipping", provider.name)
                continue

            for attempt in range(self._config.max_retries_per_provider):
                result = await self._call_provider(provider.name, market, news_context)
                if result.confidence > 0:
                    if tracker:
                        tracker.record_call()
                    return result
                logger.debug(
                    "Provider %s attempt %d failed: %s",
                    provider.name, attempt + 1, result.reasoning[:80],
                )

            # All retries exhausted for this provider, move to next
            logger.info("Provider %s exhausted, trying next", provider.name)

        # All providers failed
        logger.warning("All LLM providers failed for market: %s", market.question[:50])
        return EstimateResult(
            probability=0.5,
            confidence=0.0,
            source="llm_router_all_failed",
            reasoning=f"All {len(providers)} providers failed",
        )

    async def _call_provider(
        self,
        provider_name: str,
        market: MarketInfo,
        news_context: str = "",
    ) -> EstimateResult:
        """Call a specific LLM provider and parse the result.

        Uses OpenAI-compatible API (all free providers support this format).
        """
        provider = next(
            (p for p in self._config.providers if p.name == provider_name),
            None,
        )
        if provider is None or not provider.api_key:
            return EstimateResult(
                probability=0.5,
                confidence=0.0,
                source=f"llm_{provider_name}_fallback",
                reasoning=f"No API key for {provider_name}",
            )

        try:
            client = self._get_client(provider)
            prompt = build_superforecaster_prompt(market, news_context)

            response = await client.chat.completions.create(
                model=provider.model,
                messages=[
                    {"role": "system", "content": SUPERFORECASTER_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
                timeout=self._config.timeout_sec,
            )

            content = response.choices[0].message.content.strip()
            probability = parse_llm_probability(content)
            probability = _clamp(probability)

            # Confidence from distance to 0.5 + high volume boost
            confidence = abs(probability - 0.5) * 2
            if market.volume > 100_000:
                confidence = min(confidence + 0.1, 1.0)

            return EstimateResult(
                probability=round(probability, 4),
                confidence=round(confidence, 4),
                source=f"llm_{provider_name}",
                reasoning=content[:200],
            )

        except Exception as exc:
            logger.warning("LLM provider %s failed: %s", provider_name, exc)
            return EstimateResult(
                probability=0.5,
                confidence=0.0,
                source=f"llm_{provider_name}_fallback",
                reasoning=f"Error: {exc}",
            )

    def _get_client(self, provider: LLMProviderConfig):
        """Lazy-init OpenAI async client for a provider."""
        if provider.name not in self._clients:
            try:
                import openai
                self._clients[provider.name] = openai.AsyncOpenAI(
                    api_key=provider.api_key,
                    base_url=provider.base_url,
                )
            except ImportError:
                raise ImportError(
                    "openai package required for LLM router. "
                    "Install with: pip install openai"
                )
        return self._clients[provider.name]

    def status(self) -> dict:
        """Return router status with per-provider rate limit info."""
        providers_status = {}
        for p in self._config.providers:
            tracker = self._trackers.get(p.name)
            providers_status[p.name] = {
                "enabled": p.enabled,
                "priority": p.priority,
                "remaining_rpm": tracker.remaining_rpm() if tracker else 0,
                "remaining_rpd": tracker.remaining_rpd() if tracker else 0,
            }
        return {
            "total_providers": len(self._config.providers),
            "enabled_providers": sum(1 for p in self._config.providers if p.enabled),
            "providers": providers_status,
        }

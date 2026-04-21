"""LLM probability estimator — uses OpenAI API to estimate market probabilities.

Sends the market question + metadata as a prompt to an LLM (default: gpt-4o-mini),
then parses the probability from the response text.

The estimator is async because it calls the OpenAI API.
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from polymarket_glm.strategy.estimator import EstimateResult, MarketInfo

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a prediction market probability estimator. Given a market question and \
available metadata (current price, volume, spread, category, end date), estimate \
the true probability of the event occurring.

Respond with ONLY a single number between 0 and 1 representing your estimated \
probability. You may include brief reasoning before the number, but the last \
number in your response will be used as the probability estimate.

Format examples:
- "Based on current trends, I estimate 0.72"
- "Probability: 0.45"
- "Likely to occur: 85%"
- "0.60"
"""


class LLMConfig(BaseModel):
    """Configuration for the LLM estimator."""
    model: str = "gpt-4o-mini"
    max_tokens: int = 150
    temperature: float = Field(ge=0, le=2, default=0.1)
    timeout: float = 30.0


class LLMEstimator:
    """LLM-based probability estimator using OpenAI API.

    Usage:
        estimator = LLMEstimator(api_key="sk-...")
        result = await estimator.estimate(market_info)

    The estimate is async — it calls the OpenAI chat completions API.
    On failure, returns probability=0.5 with confidence=0 (fallback).
    """

    def __init__(
        self,
        api_key: str | None = None,
        config: LLMConfig | None = None,
        base_url: str | None = None,
    ):
        self._api_key = api_key
        self._config = config or LLMConfig()
        self._base_url = base_url
        self._client = None

    def _get_client(self):
        """Lazy-init the OpenAI async client."""
        if self._client is None:
            try:
                import openai
                kwargs = {}
                if self._api_key:
                    kwargs["api_key"] = self._api_key
                if self._base_url:
                    kwargs["base_url"] = self._base_url
                self._client = openai.AsyncOpenAI(**kwargs)
            except ImportError:
                raise ImportError(
                    "openai package required for LLM estimator. "
                    "Install with: pip install openai"
                )
        return self._client

    async def estimate(self, market: MarketInfo) -> EstimateResult:
        """Estimate probability using an LLM call."""
        try:
            client = self._get_client()
            prompt = self._build_prompt(market)

            response = await client.chat.completions.create(
                model=self._config.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
                timeout=self._config.timeout,
            )

            content = response.choices[0].message.content.strip()
            probability = self._parse_probability(content)

            # LLM confidence based on how far from 0.5 the estimate is
            # (estimates near 0.5 are inherently less confident)
            confidence = abs(probability - 0.5) * 2  # 0 at 0.5, 1 at 0 or 1
            # Boost if high volume market (more info available to LLM too)
            if market.volume > 100_000:
                confidence = min(confidence + 0.1, 1.0)

            return EstimateResult(
                probability=probability,
                confidence=round(confidence, 4),
                source="llm",
                reasoning=content[:200],
            )

        except Exception as exc:
            logger.warning("LLM estimator failed: %s", exc)
            return EstimateResult(
                probability=0.5,
                confidence=0.0,
                source="llm_fallback",
                reasoning=f"Error: {exc}",
            )

    def _build_prompt(self, market: MarketInfo) -> str:
        """Build the user prompt from market info."""
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

        parts.append("\nWhat is the true probability of this event? Respond with a number 0-100%.")

        return "\n".join(parts)

    def _parse_probability(self, text: str) -> float:
        """Parse a probability value from LLM response text.

        Tries multiple patterns:
        1. "Probability: 0.XX" or "Probability: XX%"
        2. "XX%" (percentage)
        3. "0.XX" (decimal)
        4. Any number in text
        """
        # Pattern 1: Explicit "Probability: X" or "probability of X"
        prob_match = re.search(
            r'[Pp]robabilit[y]?\s*[:=]?\s*(\d+\.?\d*)%?', text
        )
        if prob_match:
            val = float(prob_match.group(1))
            return val / 100.0 if val > 1 else val

        # Pattern 2: Percentage like "65%" or "85 percent"
        pct_match = re.search(r'(\d+\.?\d*)\s*(?:%|percent)', text, re.IGNORECASE)
        if pct_match:
            return float(pct_match.group(1)) / 100.0

        # Pattern 3: Decimal like "0.72"
        dec_match = re.search(r'\b(0\.\d{1,4})\b', text)
        if dec_match:
            return float(dec_match.group(1))

        # Pattern 4: Any number (last resort)
        num_match = re.findall(r'(\d+\.?\d*)', text)
        if num_match:
            # Take the last number (closest to the "answer")
            val = float(num_match[-1])
            if val > 1:
                return val / 100.0
            return val

        # No match → maximum uncertainty
        return 0.5

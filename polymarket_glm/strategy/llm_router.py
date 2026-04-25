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
You are a Superforecaster. Your job is to estimate the TRUE probability of events happening.

Rules:
1. Decompose the question and consider base rates from historical data.
2. Adjust based on specific evidence, recent news, and current conditions.
3. The market price is the crowd's consensus — it is NOT the truth.
 - If evidence suggests the market is WRONG, estimate AWAY from the price.
 - If evidence supports the market, it is fine to agree.
4. Think in probabilities, not certainties. Avoid 0% or 100% unless truly impossible/certain.

MANDATORY RESPONSE FORMAT — you MUST follow this structure exactly:

ARGUMENTS FOR (factors that increase probability):
- [list each factor with brief evidence]

ARGUMENTS AGAINST (factors that decrease probability):
- [list each factor with brief evidence]

NET ASSESSMENT:
[1-2 sentences weighing the arguments against base rates]

ESTIMATE: X%

Where X is your probability (0-100). No other text after ESTIMATE: X%.
Do NOT use any percentages in your reasoning section — save it for the ESTIMATE line.

CRITICAL: You MUST include both ARGUMENTS FOR and ARGUMENTS AGAINST sections.
Omitting either section makes your estimate unreliable and it will be rejected.
This structured reasoning forces you to consider evidence on both sides,
reducing anchoring bias and improving calibration by 20-30%.
"""


def build_superforecaster_prompt(market: MarketInfo, news_context: str = "") -> str:
    """Build the user prompt for the superforecaster LLM call.

    Shows the current market price but instructs the model to form an
    independent estimate. Pure hiding causes extreme underestimation;
    showing with anchoring instruction balances calibration.
    """
    parts = [f"Market Question: {market.question}"]

    if market.current_price is not None:
        parts.append(
            f"Current Market Price: {market.current_price:.2f} ({market.current_price:.0%})\n"
            "NOTE: The market price reflects the crowd's consensus. Your task is to form "
            "an INDEPENDENT estimate. If you have information or reasoning that the crowd "
            "is wrong, you should differ from this price. Do not simply mirror it."
        )

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
        "Form your own independent estimate — if you believe the market "
        "is mispriced, say so explicitly. "
        "Remember to include ARGUMENTS FOR, ARGUMENTS AGAINST, and NET ASSESSMENT "
        "sections before your ESTIMATE."
    )

    return "\n".join(parts)


# ── Chain-of-Thought Validation & Parsing ──────────────────────

class CoTValidation(BaseModel):
    """Result of validating chain-of-thought FORCED structure in LLM response."""
    has_arguments_for: bool = False
    has_arguments_against: bool = False
    has_net_assessment: bool = False
    has_estimate: bool = False
    is_valid: bool = False
    arguments_for: str = ""
    arguments_against: str = ""
    net_assessment: str = ""
    penalty_applied: bool = False
    penalty_reason: str = ""


def validate_cot_structure(text: str) -> CoTValidation:
    """Validate that an LLM response follows the FORCED chain-of-thought format.

    Checks for presence of:
    1. ARGUMENTS FOR section
    2. ARGUMENTS AGAINST section
    3. NET ASSESSMENT section
    4. ESTIMATE: X% line

    Returns CoTValidation with extracted sections and validity flag.
    A response is valid only if it has BOTH arguments sections + estimate.
    """
    text_upper = text.upper()

    # Check for ARGUMENTS FOR section
    for_match = re.search(
        r'ARGUMENTS?\s+FOR[^:]*:(.+?)(?=ARGUMENTS?\s+AGAINST|NET\s+ASSESSMENT|ESTIMATE:|$)',
        text_upper,
        re.DOTALL | re.IGNORECASE,
    )
    has_for = for_match is not None
    arguments_for = for_match.group(1).strip()[:500] if for_match else ""

    # Check for ARGUMENTS AGAINST section
    against_match = re.search(
        r'ARGUMENTS?\s+AGAINST[^:]*:(.+?)(?=NET\s+ASSESSMENT|ESTIMATE:|$)',
        text_upper,
        re.DOTALL | re.IGNORECASE,
    )
    has_against = against_match is not None
    arguments_against = against_match.group(1).strip()[:500] if against_match else ""

    # Check for NET ASSESSMENT section
    net_match = re.search(
        r'NET\s+ASSESSMENT:?\s*(.+?)(?=ESTIMATE:|$)',
        text,
        re.DOTALL | re.IGNORECASE,
    )
    has_net = net_match is not None
    net_assessment = net_match.group(1).strip()[:300] if net_match else ""

    # Check for ESTIMATE line
    has_estimate = bool(re.search(r'ESTIMATE:\s*\d+\.?\d*%', text, re.IGNORECASE))

    # A valid CoT must have both FOR and AGAINST + estimate
    is_valid = has_for and has_against and has_estimate

    # Determine penalty
    penalty_applied = False
    penalty_reason = ""
    if not is_valid:
        missing = []
        if not has_for:
            missing.append("ARGUMENTS FOR")
        if not has_against:
            missing.append("ARGUMENTS AGAINST")
        if not has_estimate:
            missing.append("ESTIMATE")
        penalty_applied = True
        penalty_reason = f"Missing CoT sections: {', '.join(missing)}"

    return CoTValidation(
        has_arguments_for=has_for,
        has_arguments_against=has_against,
        has_net_assessment=has_net,
        has_estimate=has_estimate,
        is_valid=is_valid,
        arguments_for=arguments_for,
        arguments_against=arguments_against,
        net_assessment=net_assessment,
        penalty_applied=penalty_applied,
        penalty_reason=penalty_reason,
    )


def apply_cot_penalty(probability: float, validation: CoTValidation) -> float:
    """Apply calibration penalty when CoT structure is incomplete.

    If the LLM skipped FOR/AGAINST arguments, its estimate is likely
    less calibrated. We pull it toward 0.5 (more uncertainty).

    Penalty: additional 10% shrinkage toward 0.5 per missing section.
    This is additive on top of the base shrinkage.
    """
    if validation.is_valid:
        return probability

    # Count missing critical sections (FOR and AGAINST are critical)
    missing_count = 0
    if not validation.has_arguments_for:
        missing_count += 1
    if not validation.has_arguments_against:
        missing_count += 1

    # Each missing section adds 10% shrinkage toward 0.5
    penalty_shrinkage = 0.10 * missing_count
    adjusted = probability * (1 - penalty_shrinkage) + 0.5 * penalty_shrinkage
    return _clamp(adjusted)


# ── Probability Parsing ─────────────────────────────────────────

def parse_llm_probability(text: str) -> float:
    """Parse a probability value from an LLM response.

    Tries patterns in order (all using last occurrence):
    1. "likelihood X%" or "Probability: X%"
    2. Percentage "X%" or "X percent"
    3. Decimal "0.XX"
    4. Number > 1 → treat as percentage
    5. Any number → last resort
    6. No match → 0.5
    """
    # Pattern 0: "ESTIMATE: X%" — explicit final estimate (highest priority, last match)
    est_matches = list(re.finditer(r'ESTIMATE:\s*(\d+\.?\d*)%', text, re.IGNORECASE))
    if est_matches:
        return float(est_matches[-1].group(1)) / 100.0

    # Pattern 1: "likelihood X%" or "Probability: X%" — use LAST match
    explicit = list(re.finditer(
        r'[Ll]ikelihood\s+(\d+\.?\d*)%?|[Pp]robabilit[y]?\s*[:=]?\s*(\d+\.?\d*)%?',
        text,
    ))
    if explicit:
        last = explicit[-1]
        for g in last.groups():
            if g is not None:
                val = float(g)
                return val / 100.0 if val > 1 else val

    # Pattern 2: Percentage — use LAST match
    pcts = list(re.finditer(r'(\d+\.?\d*)\s*(?:%|percent)', text, re.IGNORECASE))
    if pcts:
        return float(pcts[-1].group(1)) / 100.0

    # Pattern 3: Decimal 0.XX — use LAST match
    decs = list(re.finditer(r'\b(0\.\d{1,4})\b', text))
    if decs:
        return float(decs[-1].group(1))

    # Pattern 4: Number > 1 (treat as percentage) — use last
    nums = re.findall(r'(\d+\.?\d*)', text)
    if nums:
        val = float(nums[-1])
        if val > 1:
            return val / 100.0
        return val

    # Fallback
    return 0.5


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ── Web Search Prompt (MiniMax with web search) ─────────────────

WEB_SEARCH_SYSTEM_PROMPT = """\
You are an analyst for prediction markets. Your job is to estimate the TRUE probability of an event based on recent news found via web search.

Search for current information about the event. Analyze the evidence. Return ONLY a JSON object with:
- estimated_prob: float 0-1 (your probability estimate)
- confidence: "low" | "medium" | "high"
- reasoning: 2-3 sentence explanation
- sources_summary: brief summary of sources used

Do NOT invent facts. If no relevant information is found, return confidence "low" and estimated_prob close to the market price provided.

Your response MUST be valid JSON. No other text outside the JSON object.
"""

def _build_web_search_prompt(market: MarketInfo) -> str:
    """Build prompt for web-search-enabled providers (MiniMax)."""
    parts = [f"Market Question: {market.question}"]

    if market.current_price is not None:
        parts.append(f"Current Yes Price: {market.current_price:.4f} ({market.current_price:.0%})")

    if market.volume > 0:
        parts.append(f"Volume: ${market.volume:,.0f}")

    if market.liquidity > 0:
        parts.append(f"Liquidity: ${market.liquidity:,.0f}")

    if market.end_date:
        parts.append(f"End Date: {market.end_date}")

    if market.category:
        parts.append(f"Category: {market.category}")

    parts.append(
        "\nSearch for recent news about this event. "
        "Estimate the true probability based on evidence. "
        "Return ONLY a JSON object with: estimated_prob, confidence, reasoning, sources_summary"
    )

    return "\n".join(parts)


def _parse_structured_response(text: str) -> tuple[float | None, str | None, str, str]:
    """Try to parse a structured JSON response from web-search providers.

    Returns (probability, confidence_text, reasoning, sources_summary).
    Returns (None, None, text, "") if parsing fails.
    """
    import json as _json

    # Try to extract JSON from the response (may have markdown wrapping)
    json_str = text
    # Strip markdown code blocks if present
    if "```json" in json_str:
        json_str = json_str.split("```json", 1)[-1].split("```", 1)[0]
    elif "```" in json_str:
        json_str = json_str.split("```", 1)[-1].split("```", 1)[0]

    json_str = json_str.strip()

    try:
        data = _json.loads(json_str)
        prob = float(data.get("estimated_prob", 0.5))
        conf = str(data.get("confidence", "low")).lower()
        reasoning = str(data.get("reasoning", ""))[:200]
        sources = str(data.get("sources_summary", ""))[:200]
        return prob, conf, reasoning, sources
    except (_json.JSONDecodeError, ValueError, TypeError, AttributeError):
        return None, None, text, ""


def _map_confidence(confidence_text: str | None | float) -> float:
    """Map confidence text ('low'/'medium'/'high') or numeric to float."""
    if confidence_text is None:
        return 0.0
    if isinstance(confidence_text, (int, float)):
        return float(confidence_text)
    mapping = {"low": 0.2, "medium": 0.5, "high": 0.8}
    return mapping.get(confidence_text.lower().strip(), 0.3)


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
 "rpd": 20,
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
    "minimax": {
        "base_url": "https://api.minimax.chat/v1",
        "model": "MiniMax-Text-01",
        "rpm": 10,
        "rpd": 500,
        "priority": 0,  # highest priority — web search gives better context
        "enable_web_search": True,
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
    enable_web_search: bool = False  # MiniMax web search support


class LLMRouterRuntimeConfig(BaseModel):
    """Configuration for the LLM router (all providers + global settings)."""
    providers: list[LLMProviderConfig] = []
    max_retries_per_provider: int = 2
    timeout_sec: float = 30.0
    temperature: float = 0.3
    max_tokens: int = 500


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
        config = LLMRouterRuntimeConfig(providers=[
            LLMProviderConfig(name="groq", base_url="...", api_key="..."),
            LLMProviderConfig(name="gemini", base_url="...", api_key="..."),
        ])
        router = LLMRouter(config)
        result = await router.estimate(market_info)
    """

    def __init__(self, config: LLMRouterRuntimeConfig):
        self._config = config
        self._trackers: dict[str, RateLimitTracker] = {}
        self._clients: dict[str, object] = {}  # lazy-init OpenAI clients

        for p in config.providers:
            self._trackers[p.name] = RateLimitTracker(rpm=p.rpm, rpd=p.rpd)

    @property
    def config(self) -> LLMRouterRuntimeConfig:
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
        For providers with enable_web_search=True, adds web_search tool.
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

            # Use structured JSON prompt for web_search providers
            if provider.enable_web_search:
                prompt = _build_web_search_prompt(market)
                system_prompt = WEB_SEARCH_SYSTEM_PROMPT
            else:
                prompt = build_superforecaster_prompt(market, news_context)
                system_prompt = SUPERFORECASTER_SYSTEM_PROMPT

            kwargs: dict = dict(
                model=provider.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
                timeout=self._config.timeout_sec,
            )

            # Add web_search tool for MiniMax and similar providers
            if provider.enable_web_search:
                kwargs["tools"] = [
                    {"type": "web_search", "web_search": {"search_mode": "auto"}}
                ]

            response = await client.chat.completions.create(**kwargs)

            content = response.choices[0].message.content.strip()
            web_search_summary = ""

            # Extract web search results from MiniMax response if available
            if provider.enable_web_search and hasattr(response, "web_search"):
                try:
                    ws = response.web_search  # type: ignore[attr-defined]
                    web_search_summary = str(ws)[:200]
                except Exception:
                    pass

            # Try structured JSON parsing first (for web_search providers)
            probability, confidence, reasoning, sources = _parse_structured_response(content)

            if probability is None:
                # Fallback to standard probability parsing
                probability = parse_llm_probability(content)
                confidence = None
                reasoning = content[:200]
                sources = ""

            probability = _clamp(probability)

            # CoT validation: check if LLM followed FOR/AGAINST structure
            if not provider.enable_web_search:
                cot_validation = validate_cot_structure(content)
                if cot_validation.penalty_applied:
                    logger.info(
                        "CoT penalty for %s: %s (prob %.3f -> %.3f)",
                        provider_name, cot_validation.penalty_reason,
                        probability, apply_cot_penalty(probability, cot_validation),
                    )
                    probability = apply_cot_penalty(probability, cot_validation)
                    # Include CoT info in reasoning
                    cot_info = f" [CoT: {cot_validation.penalty_reason}]"
                else:
                    cot_info = " [CoT: valid]"
                    # Use structured reasoning from CoT sections if available
                    if cot_validation.arguments_for or cot_validation.arguments_against:
                        reasoning = (
                            f"FOR: {cot_validation.arguments_for[:80]} | "
                            f"AGAINST: {cot_validation.arguments_against[:80]}"
                        )
            else:
                cot_info = ""
                cot_validation = None

            # Shrinkage: pull extreme estimates toward 0.5 (better calibration)
            shrinkage = 0.15 # 15% regression toward market
            probability = probability * (1 - shrinkage) + 0.5 * shrinkage
            probability = _clamp(probability)

            # Confidence from structured response or distance to 0.5
            if confidence is None:
                confidence = min(abs(probability - 0.5) * 2, 0.85)
                if market.volume > 100_000:
                    confidence = min(confidence + 0.1, 0.85)
            else:
                # Map text confidence to float
                confidence = _map_confidence(confidence)

            # confidence low → force fallback
            if confidence < 0.3:
                return EstimateResult(
                    probability=0.5,
                    confidence=0.0,
                    source=f"llm_{provider_name}_low_confidence",
                    reasoning=f"Low confidence ({confidence:.2f}): {reasoning[:100]}",
                    web_search_summary=sources or web_search_summary,
                )

            return EstimateResult(
                probability=round(probability, 4),
                confidence=round(confidence, 4),
                source=f"llm_{provider_name}",
                reasoning=(reasoning + cot_info)[:200],
                web_search_summary=sources or web_search_summary,
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

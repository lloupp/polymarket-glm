"""Market fetcher — Gamma API wrapper with filtering and parsing."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field

from polymarket_glm.models import Market

logger = logging.getLogger(__name__)

GAMMA_URL = "https://gamma-api.polymarket.com"

# Keywords that indicate sports markets
_SPORT_KEYWORDS = frozenset({
    "NBA", "NFL", "MLB", "NHL", "NCAAF", "NCAAB", "MLS", "Premier League",
    "Super Bowl", "World Cup", "Olympics", "UFC", "FIFA", "F1", "Formula 1",
    "tennis", "golf", "boxing", "cricket", "rugby", "horse racing",
    "Will the", "win the", "game", "match", "score", "points", "yards",
    "touchdown", "home run", "goal",
})


class MarketFilter(BaseModel):
    """Filter criteria for market discovery."""
    min_volume_usd: float = Field(default=0, ge=0)
    active_only: bool = True
    closed_only: bool = False
    max_markets: int = Field(default=100, ge=1)
    exclude_sports: bool = False
    keywords_include: list[str] = []
    keywords_exclude: list[str] = []
    negate_risk_only: bool = False


class MarketFetcher:
    """Fetches and filters markets from the Polymarket Gamma API."""

    def __init__(self, base_url: str = GAMMA_URL, timeout: float = 15.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={"Accept": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def fetch_markets(
        self,
        market_filter: MarketFilter | None = None,
        tag: str | None = None,
        slug: str | None = None,
    ) -> list[Market]:
        """Fetch markets from Gamma API and apply filters."""
        filt = market_filter or MarketFilter()
        raw_markets = await self._fetch_raw(tag=tag, slug=slug)
        markets: list[Market] = []
        for raw in raw_markets:
            m = self._parse_market(raw)
            if m is not None and self._passes_filter(m, filt):
                markets.append(m)
            if len(markets) >= filt.max_markets:
                break
        logger.info("Fetched %d markets (filtered from %d raw)", len(markets), len(raw_markets))
        return markets

    async def _fetch_raw(
        self,
        tag: str | None = None,
        slug: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Raw HTTP call to Gamma /markets endpoint."""
        client = await self._ensure_client()
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if tag:
            params["tag"] = tag
        if slug:
            params["slug"] = slug
        try:
            resp = await client.get("/markets", params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            logger.error("Gamma API error: %s", exc)
            return []

    def _parse_market(self, raw: dict[str, Any]) -> Market | None:
        """Parse a raw Gamma API dict into a Market model.

        Gamma returns JSON-encoded strings for outcomes, prices, and tokens.
        """
        try:
            condition_id = raw.get("conditionId", "")
            market_id = raw.get("id", "")
            question = raw.get("question", "")
            slug = raw.get("slug", "")

            # Parse JSON-encoded fields
            outcomes = self._safe_json(raw.get("outcomes", "[]"))
            outcome_prices = self._safe_json_float(raw.get("outcomePrices", "[]"))
            tokens = self._safe_json(raw.get("clobTokenIds", "[]"))

            if not condition_id or not market_id or not outcomes:
                return None

            return Market(
                condition_id=condition_id,
                market_id=market_id,
                question=question,
                outcomes=outcomes,
                outcome_prices=outcome_prices,
                tokens=tokens,
                active=raw.get("active", True),
                closed=raw.get("closed", False),
                neg_risk=raw.get("negRisk", False),
                volume=float(raw.get("volume", 0)),
                slug=slug,
                end_date_iso=raw.get("endDateIso", ""),
            )
        except Exception as exc:
            logger.debug("Failed to parse market %s: %s", raw.get("id", "?"), exc)
            return None

    def _passes_filter(self, market: Market, filt: MarketFilter) -> bool:
        """Check if a market passes all filter criteria."""
        if filt.active_only and not market.active:
            return False
        if filt.closed_only and not market.closed:
            return False
        if filt.negate_risk_only and not market.neg_risk:
            return False
        if filt.min_volume_usd > 0 and market.volume < filt.min_volume_usd:
            return False
        if filt.exclude_sports and self._is_sport(market.question):
            return False
        if filt.keywords_include:
            q_lower = market.question.lower()
            if not any(kw.lower() in q_lower for kw in filt.keywords_include):
                return False
        if filt.keywords_exclude:
            q_lower = market.question.lower()
            if any(kw.lower() in q_lower for kw in filt.keywords_exclude):
                return False
        return True

    @staticmethod
    def _is_sport(question: str) -> bool:
        """Heuristic: detect sports-related markets."""
        q = question
        return any(kw in q for kw in _SPORT_KEYWORDS)

    @staticmethod
    def _safe_json(raw: str) -> list[str]:
        """Parse JSON string to list, return [] on failure."""
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    @staticmethod
    def _safe_json_float(raw: str) -> list[float]:
        """Parse JSON string of numeric strings to list[float]."""
        try:
            return [float(x) for x in json.loads(raw)]
        except (json.JSONDecodeError, TypeError, ValueError):
            return []

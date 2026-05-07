"""Market resolution checker — verifies if a market has resolved.

Fetches resolution status from Gamma API and determines the winning outcome.

Source absorbed from:
- hermes market-resolution.ts → Python rewrite

Architecture:
- fetch_resolution(market_id) → MarketResolution
- Uses Gamma API at https://gamma-api.polymarket.com/markets/{market_id}
- User-Agent header required (Gamma API returns 403 without it)
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
import urllib.error
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

USER_AGENT = "polymarket-glm/2.0"


class MarketResolution(BaseModel):
    """Resolution status for a single market."""
    market_id: str
    closed: bool = False
    yes_price: float = Field(ge=0, default=0.0)
    no_price: float = Field(ge=0, default=0.0)
    winning_outcome: Optional[str] = None  # "YES" or "NO" or None


def _parse_price_array(raw: str | None) -> list[float]:
    """Parse JSON price array string like '[\"0.95\",\"0.05\"]'."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return []
        return [float(v) for v in parsed if isinstance(v, (int, float, str))]
    except (json.JSONDecodeError, ValueError, TypeError):
        return []


def _resolve_winning_outcome(
    closed: bool,
    yes_price: float,
    no_price: float,
) -> Optional[str]:
    """Determine which outcome won based on resolved prices.

    After resolution:
    - YES won → yes_price ≈ 1.0, no_price ≈ 0.0
    - NO won → no_price ≈ 1.0, yes_price ≈ 0.0
    """
    if not closed:
        return None

    if yes_price >= 0.99 and no_price <= 0.01:
        return "YES"

    if no_price >= 0.99 and yes_price <= 0.01:
        return "NO"

    # Cannot determine resolution from prices alone
    return None


def _fetch_resolution_sync(market_id: str) -> MarketResolution:
    """Blocking: fetch market resolution from Gamma API.

    Call via asyncio.to_thread.
    """
    url = f"https://gamma-api.polymarket.com/markets/{market_id}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.warning("Market %s not found on Gamma API", market_id)
            return MarketResolution(market_id=market_id, closed=False)
        logger.error("Gamma API HTTP error for market %s: %s", market_id, e)
        return MarketResolution(market_id=market_id, closed=False)
    except Exception as e:
        logger.error("Gamma API fetch failed for market %s: %s", market_id, e)
        return MarketResolution(market_id=market_id, closed=False)

    if not isinstance(data, dict):
        return MarketResolution(market_id=market_id, closed=False)

    closed = bool(data.get("closed", False))

    # Parse outcome prices
    outcome_prices_raw = data.get("outcomePrices", "")
    prices = _parse_price_array(outcome_prices_raw)
    yes_price = prices[0] if len(prices) > 0 else 0.0
    no_price = prices[1] if len(prices) > 1 else 0.0

    winning = _resolve_winning_outcome(closed, yes_price, no_price)

    return MarketResolution(
        market_id=market_id,
        closed=closed,
        yes_price=yes_price,
        no_price=no_price,
        winning_outcome=winning,
    )


async def fetch_resolution(market_id: str) -> MarketResolution:
    """Check if a market has resolved via the Gamma API.

    Returns MarketResolution with closed status, prices, and winning outcome.
    """
    return await asyncio.to_thread(_fetch_resolution_sync, market_id)

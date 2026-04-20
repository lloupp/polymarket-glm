"""Price feed — REST polling + WebSocket scaffold for real-time prices."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Callable

import httpx
from pydantic import BaseModel, Field

from polymarket_glm.models import OrderBook, OrderBookLevel

logger = logging.getLogger(__name__)

CLOB_URL = "https://clob.polymarket.com"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws"


class PriceSnapshot(BaseModel):
    """Latest price for a single market/outcome pair."""
    market_id: str
    outcome: str = "Yes"
    price: float = Field(ge=0, le=1)
    volume: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class PriceFeed:
    """In-memory price cache with REST polling and optional WebSocket stream.

    Architecture:
    - REST mode: periodically polls /book for each tracked market
    - WS mode: subscribes to book updates via WebSocket (scaffold ready)
    - All updates flow through update(), which maintains a dict of latest snapshots
    """

    def __init__(
        self,
        clob_url: str = CLOB_URL,
        ws_url: str = WS_URL,
        poll_interval_sec: float = 5.0,
    ):
        self._clob_url = clob_url.rstrip("/")
        self._ws_url = ws_url
        self._poll_interval = poll_interval_sec
        self._cache: dict[str, PriceSnapshot] = {}
        self._tracked_markets: set[str] = set()
        self._client: httpx.AsyncClient | None = None
        self._ws_task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None
        self._connected: bool = False
        self._on_update: list[Callable[[PriceSnapshot], None]] = []

    # ── Public API ──────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    def last_snapshot(self, market_id: str) -> PriceSnapshot | None:
        return self._cache.get(market_id)

    def all_snapshots(self) -> list[PriceSnapshot]:
        return list(self._cache.values())

    def track(self, market_ids: list[str]) -> None:
        """Add markets to the tracking set."""
        self._tracked_markets.update(market_ids)

    def on_update(self, callback: Callable[[PriceSnapshot], None]) -> None:
        """Register a callback for price updates."""
        self._on_update.append(callback)

    def update(self, snapshot: PriceSnapshot) -> None:
        """Update the cache with a new snapshot and notify callbacks."""
        self._cache[snapshot.market_id] = snapshot
        for cb in self._on_update:
            try:
                cb(snapshot)
            except Exception as exc:
                logger.warning("PriceFeed callback error: %s", exc)

    # ── REST Polling ────────────────────────────────────────────

    async def start_polling(self) -> None:
        """Start polling tracked markets via REST."""
        self._connected = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("PriceFeed REST polling started (%.1fs interval)", self._poll_interval)

    async def stop(self) -> None:
        """Stop all feeds."""
        self._connected = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        logger.info("PriceFeed stopped")

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        try:
            while self._connected:
                for mid in list(self._tracked_markets):
                    try:
                        book = await self._fetch_book(mid)
                        if book and book.best_bid and book.best_ask:
                            mid_price = book.midpoint
                            if mid_price is not None:
                                self.update(PriceSnapshot(
                                    market_id=mid,
                                    price=mid_price,
                                ))
                    except Exception as exc:
                        logger.debug("Poll error for %s: %s", mid, exc)
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            pass

    async def fetch_book(self, market_id: str) -> OrderBook | None:
        """Public: fetch order book for a single market."""
        return await self._fetch_book(market_id)

    async def _fetch_book(self, market_id: str) -> OrderBook | None:
        """Fetch order book from CLOB /book endpoint."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._clob_url,
                timeout=10.0,
                headers={"Accept": "application/json"},
            )
        try:
            resp = await self._client.get("/book", params={"token_id": market_id})
            resp.raise_for_status()
            data = resp.json()
            return self._parse_book(data, market_id)
        except httpx.HTTPError as exc:
            logger.warning("Book fetch failed for %s: %s", market_id, exc)
            return None

    @staticmethod
    def _parse_book(data: dict[str, Any], market_id: str) -> OrderBook:
        """Parse CLOB /book response into OrderBook model."""
        bids = []
        for b in data.get("bids", []):
            price = float(b.get("price", 0))
            size = float(b.get("size", 0))
            if 0 < price <= 1 and size > 0:
                bids.append(OrderBookLevel(price=price, size=size))
        asks = []
        for a in data.get("asks", []):
            price = float(a.get("price", 0))
            size = float(a.get("size", 0))
            if 0 < price <= 1 and size > 0:
                asks.append(OrderBookLevel(price=price, size=size))
        return OrderBook(market_id=market_id, bids=bids, asks=asks)

    # ── WebSocket Scaffold ──────────────────────────────────────

    async def start_websocket(self) -> None:
        """Start WebSocket connection for real-time book updates.

        NOTE: This is a scaffold. The Polymarket WS API requires
        market-specific subscription messages. Full implementation
        requires parsing the WS message format and reconnect logic.
        """
        self._connected = True
        self._ws_task = asyncio.create_task(self._ws_loop())
        logger.info("PriceFeed WebSocket started (scaffold)")

    async def _ws_loop(self) -> None:
        """WebSocket main loop — scaffold for future implementation."""
        try:
            # TODO: Implement with websockets library
            # async with websockets.connect(self._ws_url) as ws:
            #     await ws.send(json.dumps({"type": "subscribe", "markets": list(self._tracked_markets)}))
            #     while self._connected:
            #         msg = await ws.recv()
            #         self._handle_ws_message(msg)
            logger.warning("WebSocket feed not yet implemented — use REST polling")
            while self._connected:
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass

    def _handle_ws_message(self, raw: str) -> None:
        """Parse a WebSocket message and update cache."""
        try:
            data = json.loads(raw)
            # TODO: Parse actual Polymarket WS message format
            event_type = data.get("type", "")
            if event_type == "book":
                market_id = data.get("market", "")
                price = float(data.get("price", 0))
                if market_id and price:
                    self.update(PriceSnapshot(
                        market_id=market_id,
                        price=price,
                    ))
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.debug("WS message parse error: %s", exc)

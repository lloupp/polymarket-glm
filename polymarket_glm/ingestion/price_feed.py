"""Price feed — REST polling + WebSocket for real-time prices."""
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
    """In-memory price cache with REST polling and WebSocket stream.

    Architecture:
    - REST mode: periodically polls /book for each tracked market
    - WS mode: subscribes to book/price updates via WebSocket with auto-reconnect
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

        # WS reconnect state
        self._ws_reconnect_attempts: int = 0
        self._ws_base_reconnect_delay: float = 1.0
        self._ws_max_reconnect_delay: float = 30.0

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

    # ── WebSocket (Real Implementation) ─────────────────────────

    async def start_websocket(self) -> None:
        """Start WebSocket connection for real-time book updates.

        Polymarket CLOB WS: wss://ws-subscriptions-clob.polymarket.com/ws

        Subscribe format:
            {"type": "subscribe", "markets": ["<token_id>", ...]}

        Unsubscribe format:
            {"type": "unsubscribe", "markets": ["<token_id>", ...]}

        Incoming events:
            - "book": full book snapshot with bids/asks arrays
            - "price_change": price tick with single price field
        """
        self._connected = True
        self._ws_task = asyncio.create_task(self._ws_main_loop())
        logger.info("PriceFeed WebSocket started")

    async def _ws_main_loop(self) -> None:
        """WebSocket main loop with auto-reconnect and exponential backoff."""
        try:
            while self._connected:
                try:
                    import websockets
                    async with websockets.connect(self._ws_url) as ws:
                        await self._ws_loop_with_conn(ws)
                        self.reset_reconnect_counter()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._ws_reconnect_attempts += 1
                    delay = self._reconnect_delay()
                    logger.warning(
                        "WebSocket disconnected (%s), reconnecting in %.1fs (attempt %d)",
                        exc, delay, self._ws_reconnect_attempts,
                    )
                    await asyncio.sleep(delay)
        except asyncio.CancelledError:
            pass

    async def _ws_loop_with_conn(self, ws) -> None:
        """Run the WS message loop on an already-connected websocket.

        Separated for testability — tests can inject a mock ws.
        """
        if self._tracked_markets:
            sub_msg = json.dumps({
                "type": "subscribe",
                "markets": list(self._tracked_markets),
            })
            await ws.send(sub_msg)
            logger.info("WS subscribed to %d markets", len(self._tracked_markets))

        async for raw_msg in ws:
            if not self._connected:
                break
            self._handle_ws_message(raw_msg)

    def _reconnect_delay(self) -> float:
        """Exponential backoff: base * 2^attempt, capped at max."""
        delay = self._ws_base_reconnect_delay * (2 ** self._ws_reconnect_attempts)
        return min(delay, self._ws_max_reconnect_delay)

    def reset_reconnect_counter(self) -> None:
        """Reset reconnect attempt counter after a successful connection."""
        self._ws_reconnect_attempts = 0

    def _handle_ws_message(self, raw: str) -> None:
        """Parse a WebSocket message and update cache.

        Polymarket CLOB WS message formats:

        1. Book snapshot:
           {
               "event_type": "book",
               "asset_id": "<token_id>",
               "market": "<token_id>",
               "bids": [{"price": "0.55", "size": "100"}, ...],
               "asks": [{"price": "0.60", "size": "50"}, ...],
               "hash": "...",
               "timestamp": "..."
           }

        2. Price change tick:
           {
               "event_type": "price_change",
               "asset_id": "<token_id>",
               "price": "0.72",
               "timestamp": "..."
           }

        3. Trade event:
           {
               "event_type": "trade",
               "asset_id": "<token_id>",
               "price": "0.55",
               "size": "100",
               "side": "BUY",
               "timestamp": "..."
           }

        4. Subscription confirmation:
           {"type": "subscribe", "status": "ok", ...}
        """
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.debug("WS: invalid JSON message")
            return

        event_type = data.get("event_type", data.get("type", ""))

        # ── Book snapshot ──
        if event_type == "book":
            market_id = data.get("asset_id") or data.get("market", "")
            if not market_id:
                return
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            best_bid = max((float(b["price"]) for b in bids if "price" in b), default=0)
            best_ask = min((float(a["price"]) for a in asks if "price" in a), default=0)
            if best_bid > 0 and best_ask > 0 and best_bid < best_ask:
                mid = (best_bid + best_ask) / 2
                self.update(PriceSnapshot(market_id=market_id, price=mid))
            elif best_bid > 0:
                self.update(PriceSnapshot(market_id=market_id, price=best_bid))
            elif best_ask > 0:
                self.update(PriceSnapshot(market_id=market_id, price=best_ask))

        # ── Price change tick ──
        elif event_type == "price_change":
            market_id = data.get("asset_id", "")
            price_str = data.get("price", "0")
            if market_id and price_str:
                price = float(price_str)
                if 0 < price <= 1:
                    self.update(PriceSnapshot(market_id=market_id, price=price))

        # ── Trade event ──
        elif event_type == "trade":
            market_id = data.get("asset_id", "")
            price_str = data.get("price", "0")
            if market_id and price_str:
                price = float(price_str)
                if 0 < price <= 1:
                    self.update(PriceSnapshot(market_id=market_id, price=price))

        # ── Subscription confirmation or unknown ──
        else:
            logger.debug("WS: unhandled event_type=%s", event_type)

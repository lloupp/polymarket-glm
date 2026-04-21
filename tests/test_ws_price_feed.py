"""Tests for WebSocket price feed."""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from polymarket_glm.ingestion.price_feed import PriceFeed, PriceSnapshot


@pytest.fixture
def feed():
    return PriceFeed(poll_interval_sec=0.1)


def test_ws_connect_and_subscribe(feed):
    """WebSocket should connect and send subscribe messages for tracked markets."""
    feed.track(["token_abc", "token_xyz"])
    
    sent_messages = []
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock(side_effect=lambda msg: sent_messages.append(json.loads(msg)))
    mock_ws.recv = AsyncMock(side_effect=asyncio.CancelledError)
    mock_ws.close = AsyncMock()
    
    async def run():
        task = asyncio.create_task(feed._ws_loop_with_conn(mock_ws))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    
    asyncio.run(run())
    
    # Should have sent subscribe for each tracked token
    subscribe_msgs = [m for m in sent_messages if m.get("type") == "subscribe"]
    assert len(subscribe_msgs) >= 1
    tokens_subscribed = set()
    for msg in subscribe_msgs:
        if "markets" in msg:
            tokens_subscribed.update(msg["markets"])
    assert "token_abc" in tokens_subscribed or any("token_abc" in str(m) for m in sent_messages)


def test_ws_handle_book_message(feed):
    """Should parse book update from WS and update cache."""
    raw = json.dumps({
        "event_type": "book",
        "asset_id": "token_abc",
        "market": "token_abc",
        "bids": [{"price": "0.55", "size": "100"}],
        "asks": [{"price": "0.60", "size": "50"}],
        "hash": "abc123",
        "timestamp": "2026-04-21T00:00:00Z"
    })
    feed._handle_ws_message(raw)
    snap = feed.last_snapshot("token_abc")
    assert snap is not None
    assert 0.5 < snap.price < 0.65


def test_ws_handle_price_message(feed):
    """Should parse price tick from WS and update cache."""
    raw = json.dumps({
        "event_type": "price_change",
        "asset_id": "token_xyz",
        "price": "0.72",
        "timestamp": "2026-04-21T00:00:00Z"
    })
    feed._handle_ws_message(raw)
    snap = feed.last_snapshot("token_xyz")
    assert snap is not None
    assert abs(snap.price - 0.72) < 0.01


def test_ws_handle_invalid_json(feed):
    """Invalid messages should be handled gracefully."""
    feed._handle_ws_message("not json {{{")
    assert len(feed.all_snapshots()) == 0


def test_ws_reconnect_backoff(feed):
    """Should track reconnect attempts with increasing delay."""
    feed._ws_reconnect_attempts = 0
    feed._ws_max_reconnect_delay = 30.0
    # First attempt: base delay
    d1 = feed._reconnect_delay()
    assert d1 == 1.0
    feed._ws_reconnect_attempts = 1
    d2 = feed._reconnect_delay()
    assert d2 == 2.0
    feed._ws_reconnect_attempts = 5
    d3 = feed._reconnect_delay()
    assert d3 == 30.0  # capped at max
    feed.reset_reconnect_counter()
    assert feed._ws_reconnect_attempts == 0

"""Tests for rate limiting improvements — exponential backoff + reduced confidence without context.

Bug fixes:
1. NewsFetcher/WebSearcher use fixed backoff (900s/3600s) instead of exponential
2. No confidence penalty when context is empty (no news/search results)
3. RSS fetcher should be cached with shorter TTL for faster recovery
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from polymarket_glm.strategy.context_fetcher import (
    ContextBuilder,
    ContextBuilderConfig,
    NewsFetcher,
    NewsFetcherConfig,
    WebSearcher,
    WebSearcherConfig,
    RSSFetcher,
    RSSFetcherConfig,
)


class TestExponentialBackoff:
    """Exponential backoff on rate-limit responses."""

    def test_news_fetcher_initial_backoff(self):
        """First 429 should back off for base_backoff_sec, not fixed 900s."""
        config = NewsFetcherConfig(api_key="test-key")
        nf = NewsFetcher(config)
        assert nf._base_backoff_sec == 60.0  # New attribute
        assert nf._max_backoff_sec == 3600.0

    def test_news_fetcher_backoff_doubles_on_repeated_429(self):
        """Each successive 429 should double the backoff (up to max)."""
        config = NewsFetcherConfig(api_key="test-key")
        nf = NewsFetcher(config)

        # Simulate 1st 429
        nf._handle_rate_limit()
        assert nf._rate_limited_until > time.monotonic() + 55  # ~60s

        # Simulate 2nd 429 (backoff doubles)
        time.sleep(0.01)
        nf._handle_rate_limit()
        remaining = nf._rate_limited_until - time.monotonic()
        assert remaining > 100  # ~120s

    def test_news_fetcher_backoff_caps_at_max(self):
        """Backoff should never exceed _max_backoff_sec."""
        config = NewsFetcherConfig(api_key="test-key")
        nf = NewsFetcher(config)
        nf._consecutive_429s = 20  # Simulate many failures

        nf._handle_rate_limit()
        remaining = nf._rate_limited_until - time.monotonic()
        assert remaining <= nf._max_backoff_sec + 1

    def test_news_fetcher_backoff_resets_on_success(self):
        """Successful fetch should reset the backoff counter."""
        config = NewsFetcherConfig(api_key="test-key")
        nf = NewsFetcher(config)
        nf._consecutive_429s = 5
        nf._reset_backoff()
        assert nf._consecutive_429s == 0

    def test_web_searcher_initial_backoff(self):
        """WebSearcher should also use exponential backoff."""
        config = WebSearcherConfig(api_key="test-key")
        ws = WebSearcher(config)
        assert ws._base_backoff_sec == 120.0
        assert ws._max_backoff_sec == 7200.0

    def test_web_searcher_backoff_doubles(self):
        """WebSearcher backoff should also double on repeated 429."""
        config = WebSearcherConfig(api_key="test-key")
        ws = WebSearcher(config)

        ws._handle_rate_limit()
        first_remaining = ws._rate_limited_until - time.monotonic()

        time.sleep(0.01)
        ws._handle_rate_limit()
        second_remaining = ws._rate_limited_until - time.monotonic()

        assert second_remaining > first_remaining


class TestReducedConfidenceWithoutContext:
    """When no context is available, signal confidence should be reduced."""

    @pytest.mark.asyncio
    async def test_empty_context_reduces_confidence(self):
        """ContextBuilder should report whether context was found."""
        config = ContextBuilderConfig()
        cb = ContextBuilder(config, cache_ttl_sec=0)

        # Mock all sources to return empty
        with patch.object(cb, "_safe_fetch_news", new_callable=AsyncMock, return_value=[]), \
             patch.object(cb, "_safe_search_web", new_callable=AsyncMock, return_value=[]), \
             patch.object(cb, "_safe_fetch_rss", new_callable=AsyncMock, return_value=[]):
            ctx = await cb.fetch_context("Will X happen?")
            assert ctx == ""
            # ContextBuilder should track last fetch had no context
            assert cb.last_fetch_had_context is False

    @pytest.mark.asyncio
    async def test_with_context_reports_true(self):
        """When context is found, last_fetch_had_context should be True."""
        from polymarket_glm.strategy.context_fetcher import NewsArticle

        config = ContextBuilderConfig(
            news_fetcher=NewsFetcherConfig(api_key="test-key"),
        )
        cb = ContextBuilder(config, cache_ttl_sec=0)

        articles = [NewsArticle(title="Test", source="SRC")]
        with patch.object(cb, "_safe_fetch_news", new_callable=AsyncMock, return_value=articles), \
             patch.object(cb, "_safe_search_web", new_callable=AsyncMock, return_value=[]), \
             patch.object(cb, "_safe_fetch_rss", new_callable=AsyncMock, return_value=[]):
            ctx = await cb.fetch_context("Will X happen?")
            assert ctx != ""
            assert cb.last_fetch_had_context is True

    def test_confidence_penalty_factor(self):
        """ContextBuilder should expose a confidence penalty factor."""
        config = ContextBuilderConfig()
        cb = ContextBuilder(config, cache_ttl_sec=0)

        # Default: no penalty
        assert cb.confidence_penalty == 1.0

        # After empty fetch
        cb.last_fetch_had_context = False
        assert cb.confidence_penalty == 0.7  # 30% reduction

        # After successful fetch
        cb.last_fetch_had_context = True
        assert cb.confidence_penalty == 1.0


class TestRSSCacheEfficiency:
    """RSS fetcher should be cached with shorter TTL for fast recovery."""

    def test_rss_cache_exists(self):
        """RSSFetcher should have its own TTL cache."""
        config = RSSFetcherConfig(enabled=True)
        rf = RSSFetcher(config)
        assert hasattr(rf, "_cache")
        assert hasattr(rf, "_cache_ttl_sec")
        # RSS cache TTL should be shorter than ContextBuilder's
        assert rf._cache_ttl_sec == 300  # 5 min vs 10 min default

    @pytest.mark.asyncio
    async def test_rss_cache_hit(self):
        """Second fetch for same question should be cached."""
        from polymarket_glm.strategy.context_fetcher import NewsArticle

        config = RSSFetcherConfig(enabled=True)
        rf = RSSFetcher(config)

        # Manually populate cache with proper type
        cached_articles = [NewsArticle(title="Cached", source="SRC")]
        # Cache key is question.lower().strip() — "Will X happen?" → "will x happen?"
        rf._cache["will x happen?"] = (time.monotonic(), cached_articles)

        # _check_cache should return cached articles
        result = rf._check_cache("Will X happen?")
        assert result is not None
        assert len(result) == 1
        assert result[0].title == "Cached"

"""Tests for NewsFetcher, WebSearch, and ContextBuilder.

Sprint 12: News + Search Context for Superforecaster LLM prompt.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polymarket_glm.strategy.context_fetcher import (
    NewsArticle,
    NewsFetcher,
    NewsFetcherConfig,
    WebSearchResult,
    WebSearcher,
    WebSearcherConfig,
    ContextBuilder,
    ContextBuilderConfig,
)


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def news_config():
    return NewsFetcherConfig(api_key="test-news-key", max_articles=5)


@pytest.fixture
def search_config():
    return WebSearcherConfig(api_key="test-tavily-key", max_results=3)


@pytest.fixture
def context_config():
    return ContextBuilderConfig(
        max_news_articles=3,
        max_search_results=2,
        max_context_chars=1500,
    )


@pytest.fixture
def news_fetcher(news_config):
    return NewsFetcher(news_config)


@pytest.fixture
def web_searcher(search_config):
    return WebSearcher(search_config)


@pytest.fixture
def context_builder(context_config):
    return ContextBuilder(context_config)


# ── NewsArticle ───────────────────────────────────────────────────

class TestNewsArticle:
    def test_creation(self):
        article = NewsArticle(
            title="Bitcoin surges past $100k",
            source="CoinDesk",
            published_at="2025-01-15T10:00:00Z",
            description="BTC reached new all-time high...",
            url="https://example.com/btc",
        )
        assert article.title == "Bitcoin surges past $100k"
        assert article.source == "CoinDesk"

    def test_to_context_line(self):
        article = NewsArticle(
            title="Fed holds rates steady",
            source="Reuters",
            published_at="2025-03-20T18:00:00Z",
            description="The Federal Reserve decided to maintain rates.",
            url="https://reuters.com/fed",
        )
        line = article.to_context_line()
        assert "Fed holds rates steady" in line
        assert "Reuters" in line
        assert "maintain rates" in line


# ── NewsFetcherConfig ─────────────────────────────────────────────

class TestNewsFetcherConfig:
    def test_defaults(self):
        cfg = NewsFetcherConfig()
        assert cfg.api_key == ""
        assert cfg.max_articles == 5
        assert cfg.base_url == "https://newsapi.org/v2"
        assert cfg.language == "en"
        assert cfg.sort_by == "relevancy"

    def test_custom(self):
        cfg = NewsFetcherConfig(api_key="abc", max_articles=10)
        assert cfg.api_key == "abc"
        assert cfg.max_articles == 10

    def test_enabled_property(self):
        assert NewsFetcherConfig().enabled is False
        assert NewsFetcherConfig(api_key="key").enabled is True


# ── NewsFetcher ───────────────────────────────────────────────────

class TestNewsFetcher:
    def test_init(self, news_fetcher, news_config):
        assert news_fetcher.config == news_config

    def test_is_enabled(self, news_fetcher):
        assert news_fetcher.is_enabled is True

    def test_not_enabled_without_key(self):
        fetcher = NewsFetcher(NewsFetcherConfig())
        assert fetcher.is_enabled is False

    def test_build_query_from_politics_question(self, news_fetcher):
        query = news_fetcher._build_query("Will the US pass a budget bill before July?")
        assert len(query) > 0
        # Should extract key terms, not the full question
        assert len(query) < 80

    def test_build_query_from_crypto_question(self, news_fetcher):
        query = news_fetcher._build_query("Will Bitcoin reach $150k by end of 2025?")
        assert "Bitcoin" in query or "bitcoin" in query.lower()

    def test_build_query_short_question(self, news_fetcher):
        query = news_fetcher._build_query("Will it rain?")
        assert len(query) > 0

    @pytest.mark.asyncio
    async def test_fetch_not_enabled_returns_empty(self):
        fetcher = NewsFetcher(NewsFetcherConfig())
        results = await fetcher.fetch("Will Bitcoin reach $200k?")
        assert results == []

    @pytest.mark.asyncio
    async def test_fetch_success(self, news_fetcher):
        mock_response = {
            "status": "ok",
            "totalResults": 2,
            "articles": [
                {
                    "title": "Bitcoin hits new high",
                    "source": {"name": "CoinDesk"},
                    "publishedAt": "2025-01-15T10:00:00Z",
                    "description": "BTC surged to new all-time highs.",
                    "url": "https://coindesk.com/btc-high",
                },
                {
                    "title": "Crypto market rally",
                    "source": {"name": "Bloomberg"},
                    "publishedAt": "2025-01-15T08:00:00Z",
                    "description": "Markets rallied on positive sentiment.",
                    "url": "https://bloomberg.com/crypto-rally",
                },
            ],
        }
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_response
            mock_client.get = AsyncMock(return_value=mock_resp)

            results = await news_fetcher.fetch("Will Bitcoin reach $200k?")
            assert len(results) == 2
            assert results[0].title == "Bitcoin hits new high"
            assert results[1].source == "Bloomberg"

    @pytest.mark.asyncio
    async def test_fetch_api_error_returns_empty(self, news_fetcher):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_resp = MagicMock()
            mock_resp.status_code = 401
            mock_resp.text = "Unauthorized"
            mock_client.get = AsyncMock(return_value=mock_resp)

            results = await news_fetcher.fetch("Will it rain?")
            assert results == []

    @pytest.mark.asyncio
    async def test_fetch_httpx_error_returns_empty(self, news_fetcher):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            import httpx
            mock_client.get = AsyncMock(side_effect=httpx.HTTPError("Network error"))

            results = await news_fetcher.fetch("Will it rain?")
            assert results == []

    def test_parse_articles_skips_removed(self, news_fetcher):
        raw = [
            {"title": "[Removed]", "source": {"name": "N/A"}, "publishedAt": "", "description": "", "url": ""},
            {"title": "Good article", "source": {"name": "Reuters"}, "publishedAt": "2025-01-01T00:00:00Z", "description": "Desc", "url": "https://example.com"},
        ]
        articles = news_fetcher._parse_articles(raw)
        assert len(articles) == 1
        assert articles[0].title == "Good article"


# ── WebSearchResult ───────────────────────────────────────────────

class TestWebSearchResult:
    def test_creation(self):
        result = WebSearchResult(
            title="Fed policy update",
            content="The Federal Reserve maintained interest rates at current levels.",
            url="https://example.com/fed",
            score=0.95,
        )
        assert result.title == "Fed policy update"
        assert result.score == 0.95

    def test_to_context_line(self):
        result = WebSearchResult(
            title="Breaking: Election results",
            content="Candidate A won the election with 52% of the vote.",
            url="https://example.com/election",
            score=0.88,
        )
        line = result.to_context_line()
        assert "Breaking: Election results" in line
        assert "52% of the vote" in line


# ── WebSearcherConfig ─────────────────────────────────────────────

class TestWebSearcherConfig:
    def test_defaults(self):
        cfg = WebSearcherConfig()
        assert cfg.api_key == ""
        assert cfg.max_results == 3
        assert cfg.base_url == "https://api.tavily.com"
        assert cfg.search_depth == "basic"

    def test_enabled_property(self):
        assert WebSearcherConfig().enabled is False
        assert WebSearcherConfig(api_key="key").enabled is True


# ── WebSearcher ───────────────────────────────────────────────────

class TestWebSearcher:
    def test_init(self, web_searcher, search_config):
        assert web_searcher.config == search_config

    def test_is_enabled(self, web_searcher):
        assert web_searcher.is_enabled is True

    def test_not_enabled_without_key(self):
        searcher = WebSearcher(WebSearcherConfig())
        assert searcher.is_enabled is False

    @pytest.mark.asyncio
    async def test_search_not_enabled_returns_empty(self):
        searcher = WebSearcher(WebSearcherConfig())
        results = await searcher.search("Will Bitcoin reach $200k?")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_success(self, web_searcher):
        mock_response = {
            "results": [
                {
                    "title": "Bitcoin price prediction 2025",
                    "content": "Analysts predict BTC could reach $150k by year end.",
                    "url": "https://example.com/btc-prediction",
                    "score": 0.92,
                },
                {
                    "title": "Crypto market overview",
                    "content": "The cryptocurrency market has been bullish in Q1.",
                    "url": "https://example.com/crypto-overview",
                    "score": 0.78,
                },
            ],
        }
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_response
            mock_client.post = AsyncMock(return_value=mock_resp)

            results = await web_searcher.search("Bitcoin price 2025")
            assert len(results) == 2
            assert results[0].title == "Bitcoin price prediction 2025"
            assert results[0].score == 0.92

    @pytest.mark.asyncio
    async def test_search_api_error_returns_empty(self, web_searcher):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_resp = MagicMock()
            mock_resp.status_code = 401
            mock_resp.text = "Unauthorized"
            mock_client.post = AsyncMock(return_value=mock_resp)

            results = await web_searcher.search("test query")
            assert results == []

    @pytest.mark.asyncio
    async def test_search_httpx_error_returns_empty(self, web_searcher):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            import httpx
            mock_client.post = AsyncMock(side_effect=httpx.HTTPError("Connection failed"))

            results = await web_searcher.search("test query")
            assert results == []


# ── ContextBuilder ────────────────────────────────────────────────

class TestContextBuilder:
    def test_init(self, context_builder, context_config):
        assert context_builder.config == context_config

    def test_build_context_empty(self, context_builder):
        result = context_builder.build_context([], [])
        assert result == ""

    def test_build_context_news_only(self, context_builder):
        articles = [
            NewsArticle(
                title="Fed holds rates",
                source="Reuters",
                published_at="2025-03-20T18:00:00Z",
                description="The Federal Reserve decided to maintain current interest rates.",
                url="https://reuters.com/fed",
            ),
        ]
        result = context_builder.build_context(articles, [])
        assert "Fed holds rates" in result
        assert "Reuters" in result

    def test_build_context_search_only(self, context_builder):
        results = [
            WebSearchResult(
                title="Election results 2025",
                content="Candidate A wins with 55% of votes.",
                url="https://example.com/election",
                score=0.90,
            ),
        ]
        result = context_builder.build_context([], results)
        assert "Election results 2025" in result

    def test_build_context_both(self, context_builder):
        articles = [
            NewsArticle(
                title="Fed holds rates",
                source="Reuters",
                published_at="2025-03-20T18:00:00Z",
                description="Fed keeps rates unchanged.",
                url="https://reuters.com/fed",
            ),
        ]
        results = [
            WebSearchResult(
                title="Market reaction",
                content="Stocks rose after the Fed decision.",
                url="https://example.com/market",
                score=0.85,
            ),
        ]
        result = context_builder.build_context(articles, results)
        assert "Fed holds rates" in result
        assert "Market reaction" in result

    def test_build_context_respects_max_chars(self, context_builder):
        # Create many articles that would exceed max_context_chars
        articles = [
            NewsArticle(
                title=f"Article {i}",
                source="Test",
                published_at="2025-01-01T00:00:00Z",
                description="X" * 500,  # long description
                url="https://example.com",
            )
            for i in range(10)
        ]
        result = context_builder.build_context(articles, [])
        assert len(result) <= context_builder.config.max_context_chars

    def test_build_context_limits_articles(self, context_builder):
        """Should only include max_news_articles."""
        articles = [
            NewsArticle(
                title=f"Article {i}",
                source="Test",
                published_at="2025-01-01T00:00:00Z",
                description=f"Description {i}",
                url="https://example.com",
            )
            for i in range(10)
        ]
        result = context_builder.build_context(articles, [])
        # max_news_articles=3, so at most 3 article headers
        assert "Article 0" in result
        assert "Article 2" in result

    def test_build_context_limits_search_results(self, context_builder):
        """Should only include max_search_results."""
        results = [
            WebSearchResult(
                title=f"Result {i}",
                content=f"Content {i}",
                url="https://example.com",
                score=0.8,
            )
            for i in range(10)
        ]
        result = context_builder.build_context([], results)
        assert "Result 0" in result
        assert "Result 1" in result

    @pytest.mark.asyncio
    async def test_fetch_context_integrates_both(self, context_builder):
        """Test the full async fetch_context method."""
        mock_news = AsyncMock(return_value=[
            NewsArticle(
                title="Fed decision today",
                source="Reuters",
                published_at="2025-03-20T18:00:00Z",
                description="Fed expected to hold rates.",
                url="https://reuters.com/fed",
            ),
        ])
        mock_search = AsyncMock(return_value=[
            WebSearchResult(
                title="Market analysis",
                content="Analysts expect steady rates.",
                url="https://example.com",
                score=0.90,
            ),
        ])

        # Enable fetchers by patching config.enabled
        context_builder._news_fetcher.config.api_key = "fake-key"
        context_builder._web_searcher.config.api_key = "fake-key"

        with patch.object(context_builder._news_fetcher, "fetch", mock_news), \
             patch.object(context_builder._web_searcher, "search", mock_search):
            ctx = await context_builder.fetch_context("Will the Fed cut rates?")
            assert "Fed decision today" in ctx
            assert "Market analysis" in ctx

    @pytest.mark.asyncio
    async def test_fetch_context_graceful_failure(self, context_builder):
        """If both fetchers fail, return empty string (not crash)."""
        # Enable fetchers by patching config
        context_builder._news_fetcher.config.api_key = "fake-key"
        context_builder._web_searcher.config.api_key = "fake-key"

        with patch.object(context_builder._news_fetcher, "fetch", AsyncMock(side_effect=Exception("NewsAPI down"))), \
             patch.object(context_builder._web_searcher, "search", AsyncMock(side_effect=Exception("Tavily down"))):
            ctx = await context_builder.fetch_context("Will it rain?")
            assert ctx == ""

    @pytest.mark.asyncio
    async def test_fetch_context_news_only_available(self, context_builder):
        """If only news works, still return context."""
        mock_news = AsyncMock(return_value=[
            NewsArticle(
                title="Breaking news",
                source="AP",
                published_at="2025-01-01T00:00:00Z",
                description="Something happened.",
                url="https://ap.com",
            ),
        ])

        # Enable only news fetcher
        context_builder._news_fetcher.config.api_key = "fake-key"
        # web_searcher stays disabled (no api_key)

        with patch.object(context_builder._news_fetcher, "fetch", mock_news):
            ctx = await context_builder.fetch_context("What happened?")
            assert "Breaking news" in ctx


# ── Config integration with Settings ──────────────────────────────

class TestContextConfigInSettings:
    def test_news_fetcher_config_defaults(self):
        from polymarket_glm.config import Settings
        s = Settings()
        # Should have news_fetcher and web_searcher sub-configs
        assert hasattr(s, "news_fetcher")
        assert hasattr(s, "web_searcher")

    def test_news_fetcher_env_override(self):
        """PGLM_NEWS_FETCHER__API_KEY should be loadable from env."""
        from polymarket_glm.config import Settings
        import os
        with patch.dict(os.environ, {"PGLM_NEWS_FETCHER__API_KEY": "test-123"}):
            s = Settings()
            assert s.news_fetcher.api_key == "test-123"

    def test_web_searcher_env_override(self):
        """PGLM_WEB_SEARCHER__API_KEY should be loadable from env."""
        from polymarket_glm.config import Settings
        import os
        with patch.dict(os.environ, {"PGLM_WEB_SEARCHER__API_KEY": "tavily-456"}):
            s = Settings()
            assert s.web_searcher.api_key == "tavily-456"

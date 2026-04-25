"""News + Web Search context fetcher for Superforecaster LLM prompt.

Sprint 12: Provides real-time news and web search context to improve
LLM probability estimation for prediction markets.

Components:
- NewsFetcher: NewsAPI.org integration (free tier: 100 req/day)
- WebSearcher: Tavily API integration (free tier: 1000 req/month)
- ContextBuilder: Aggregates news + search into a formatted context string
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus
from typing import Protocol

import httpx
from pydantic import BaseModel, Field

try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False

logger = logging.getLogger(__name__)


# ── Data Models ───────────────────────────────────────────────────

class NewsArticle(BaseModel):
    """A single news article from NewsAPI."""
    title: str
    source: str
    published_at: str = ""
    description: str = ""
    url: str = ""

    def to_context_line(self) -> str:
        """Format as a single context line for the LLM prompt."""
        desc = f" — {self.description}" if self.description else ""
        return f"- [{self.source}] {self.title}{desc}"


class WebSearchResult(BaseModel):
    """A single web search result from Tavily."""
    title: str
    content: str
    url: str = ""
    score: float = Field(default=0.0, ge=0, le=1)

    def to_context_line(self) -> str:
        """Format as a single context line for the LLM prompt."""
        return f"- {self.title}: {self.content}"


# ── NewsFetcher ───────────────────────────────────────────────────

class NewsFetcherConfig(BaseModel):
    """Configuration for NewsAPI.org integration.

    Env vars: PGLM_NEWS_FETCHER__API_KEY, etc.
    """
    api_key: str = ""
    base_url: str = "https://newsapi.org/v2"
    max_articles: int = 5
    language: str = "en"
    sort_by: str = "relevancy"  # relevancy | popularity | publishedAt
    timeout_sec: float = 10.0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


class NewsFetcher:
    """Fetch relevant news articles from NewsAPI.org.

    Free tier: 100 requests/day, sufficient for ~1-2 markets per
    simulation iteration (we throttle via ContextBuilder).
    """

    def __init__(self, config: NewsFetcherConfig):
        self.config = config

    @property
    def is_enabled(self) -> bool:
        return self.config.enabled

    async def fetch(self, question: str) -> list[NewsArticle]:
        """Fetch news articles relevant to a market question.

        Returns empty list if disabled or on any error.
        """
        if not self.is_enabled:
            return []

        query = self._build_query(question)
        if not query:
            return []

        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_sec) as client:
                resp = await client.get(
                    f"{self.config.base_url}/everything",
                    params={
                        "q": query,
                        "apiKey": self.config.api_key,
                        "language": self.config.language,
                        "sortBy": self.config.sort_by,
                        "pageSize": self.config.max_articles,
                    },
                )

                if resp.status_code != 200:
                    logger.warning(
                        "NewsAPI returned %d: %s",
                        resp.status_code,
                        resp.text[:100],
                    )
                    return []

                data = resp.json()
                if data.get("status") != "ok":
                    logger.warning("NewsAPI status: %s", data.get("status"))
                    return []

                return self._parse_articles(data.get("articles", []))

        except httpx.HTTPError as exc:
            logger.warning("NewsAPI request failed: %s", exc)
            return []
        except Exception as exc:
            logger.warning("NewsFetcher error: %s", exc)
            return []

    def _build_query(self, question: str) -> str:
        """Extract search keywords from a market question.

        Removes filler words and keeps the key entities/terms.
        """
        # Remove common prediction market prefixes
        q = re.sub(r"^(Will|Is|Does|Has|Are|Can|Do|Did|Could|Should|May|Might)\s+", "", question, flags=re.IGNORECASE)

        # Remove question marks and trailing punctuation
        q = q.rstrip("?").rstrip(".").strip()

        # Remove common filler phrases
        fillers = [
            r"\bbefore\s+\S+",
            r"\bby\s+(the\s+)?end\s+of\b.*",
            r"\bin\s+\d{4}\b",
            r"\bby\s+\w+\s+\d{1,2}.*",
            r"\bbefore\s+\w+\s+\d{1,2}.*",
        ]
        for pattern in fillers:
            q = re.sub(pattern, "", q, flags=re.IGNORECASE).strip()

        # Take up to 5 key words
        words = q.split()
        query = " ".join(words[:5])

        return query.strip() if query.strip() else question[:60]

    def _parse_articles(self, raw_articles: list[dict]) -> list[NewsArticle]:
        """Parse NewsAPI article dicts into NewsArticle models.

        Skips articles marked as [Removed].
        """
        articles = []
        for raw in raw_articles:
            title = raw.get("title", "")
            if not title or title == "[Removed]":
                continue

            articles.append(NewsArticle(
                title=title,
                source=raw.get("source", {}).get("name", "Unknown"),
                published_at=raw.get("publishedAt", ""),
                description=raw.get("description", ""),
                url=raw.get("url", ""),
            ))

        return articles


# ── WebSearcher ───────────────────────────────────────────────────

class WebSearcherConfig(BaseModel):
    """Configuration for Tavily web search integration.

    Env vars: PGLM_WEB_SEARCHER__API_KEY, etc.
    """
    api_key: str = ""
    base_url: str = "https://api.tavily.com"
    max_results: int = 3
    search_depth: str = "basic"  # basic | advanced
    timeout_sec: float = 10.0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


class WebSearcher:
    """Search the web using Tavily API for market-relevant context.

    Free tier: 1000 requests/month — more than enough for
    our simulation cadence (1-2 searches per iteration).
    """

    def __init__(self, config: WebSearcherConfig):
        self.config = config

    @property
    def is_enabled(self) -> bool:
        return self.config.enabled

    async def search(self, question: str) -> list[WebSearchResult]:
        """Search the web for context relevant to a market question.

        Returns empty list if disabled or on any error.
        """
        if not self.is_enabled:
            return []

        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_sec) as client:
                resp = await client.post(
                    f"{self.config.base_url}/search",
                    json={
                        "api_key": self.config.api_key,
                        "query": question[:200],  # Tavily accepts full questions
                        "max_results": self.config.max_results,
                        "search_depth": self.config.search_depth,
                        "include_answer": False,
                        "include_raw_content": False,
                    },
                )

                if resp.status_code != 200:
                    logger.warning(
                        "Tavily returned %d: %s",
                        resp.status_code,
                        resp.text[:100],
                    )
                    return []

                data = resp.json()
                return self._parse_results(data.get("results", []))

        except httpx.HTTPError as exc:
            logger.warning("Tavily request failed: %s", exc)
            return []
        except Exception as exc:
            logger.warning("WebSearcher error: %s", exc)
            return []

    def _parse_results(self, raw_results: list[dict]) -> list[WebSearchResult]:
        """Parse Tavily result dicts into WebSearchResult models."""
        results = []
        for raw in raw_results:
            title = raw.get("title", "")
            content = raw.get("content", "")
            if not title and not content:
                continue

            results.append(WebSearchResult(
                title=title,
                content=content[:500],  # cap content length
                url=raw.get("url", ""),
                score=raw.get("score", 0.0),
            ))

        return results



# ── RSS Fallback ──────────────────────────────────────────────────


class RSSFetcherConfig(BaseModel):
    """Configuration for RSS news fallback (no API key required).

    Uses Google News RSS feeds — 100% free, no rate limits, no signup.
    Falls back automatically when NewsAPI/Tavily are rate-limited.
    """
    enabled: bool = True
    base_url: str = "https://news.google.com/rss/search"
    max_articles: int = 5
    timeout_sec: float = 10.0

    @property
    def is_available(self) -> bool:
        return self.enabled and HAS_FEEDPARSER


class RSSFetcher:
    """Fetch news via Google News RSS — no API key, no rate limits.

    This is the fallback when NewsAPI/Tavily are rate-limited or unavailable.
    Uses feedparser to parse RSS feeds. Google News RSS supports keyword
    search queries and returns recent, relevant articles.

    Rate limit: Google News RSS is effectively unlimited for reasonable use.
    """

    def __init__(self, config: RSSFetcherConfig | None = None):
        self.config = config or RSSFetcherConfig()

    @property
    def is_available(self) -> bool:
        return self.config.is_available

    async def fetch(self, question: str) -> list[NewsArticle]:
        """Fetch RSS news articles relevant to a market question.

        Returns empty list if feedparser not installed or on any error.
        """
        if not self.is_available:
            return []

        query = self._build_query(question)
        if not query:
            return []

        try:
            url = f"{self.config.base_url}?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
            async with httpx.AsyncClient(timeout=self.config.timeout_sec) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.debug("RSS fetch returned %d", resp.status_code)
                    return []

            # feedparser parses from string
            feed = feedparser.parse(resp.text)
            return self._parse_entries(feed.entries)

        except Exception as exc:
            logger.debug("RSSFetcher error: %s", exc)
            return []

    def _build_query(self, question: str) -> str:
        """Extract search keywords from market question for RSS."""
        # Remove common prefixes
        q = re.sub(
            r"^(Will|Is|Does|Has|Are|Can|Do|Did|Could|Should|May|Might)\s+",
            "", question, flags=re.IGNORECASE,
        )
        q = q.rstrip("?").rstrip(".").strip()

        # Remove time qualifiers
        fillers = [
            r"\bbefore\s+\S+",
            r"\bby\s+(the\s+)?end\s+of\b.*",
            r"\bin\s+\d{4}\b",
            r"\bby\s+\w+\s+\d{1,2}.*",
            r"\bbefore\s+\w+\s+\d{1,2}.*",
        ]
        for pattern in fillers:
            q = re.sub(pattern, "", q, flags=re.IGNORECASE).strip()

        words = q.split()
        return " ".join(words[:5]) if words else question[:60]

    def _parse_entries(self, entries: list) -> list[NewsArticle]:
        """Parse feedparser entries into NewsArticle models."""
        articles = []
        for entry in entries[:self.config.max_articles]:
            title = getattr(entry, "title", "")
            if not title:
                continue
            source = getattr(entry, "source", {})
            source_name = source.get("title", "RSS") if isinstance(source, dict) else "RSS"
            published = getattr(entry, "published", "")
            description = getattr(entry, "summary", "")
            # Clean HTML from description
            description = re.sub(r"<[^>]+>", "", description)[:200]
            url = getattr(entry, "link", "")

            articles.append(NewsArticle(
                title=title,
                source=source_name,
                published_at=published,
                description=description,
                url=url,
            ))
        return articles

# ── ContextBuilder ────────────────────────────────────────────────

class ContextBuilderConfig(BaseModel):
    """Configuration for the context builder.

    Env vars: PGLM_CONTEXT_BUILDER__MAX_NEWS_ARTICLES, etc.
    """
    max_news_articles: int = 3
    max_search_results: int = 2
    max_context_chars: int = 1500

    # Sub-configs (will be populated from Settings)
    news_fetcher: NewsFetcherConfig = NewsFetcherConfig()
    web_searcher: WebSearcherConfig = WebSearcherConfig()


class ContextBuilder:
    """Aggregate news + web search + RSS into a formatted context string.

    The context is injected into the Superforecaster prompt to provide
    real-time information that improves LLM probability estimation.

    Priority: NewsAPI → Tavily → RSS (Google News fallback).
    RSS is always available (no API key) and serves as fallback
    when paid APIs hit rate limits.
    """

    def __init__(self, config: ContextBuilderConfig):
        self.config = config
        self._news_fetcher = NewsFetcher(config.news_fetcher)
        self._web_searcher = WebSearcher(config.web_searcher)
        self._rss_fetcher = RSSFetcher()  # Always available, no config needed

    @property
    def has_any_source(self) -> bool:
        """Check if at least one context source is available."""
        return (
            self._news_fetcher.is_enabled
            or self._web_searcher.is_enabled
            or self._rss_fetcher.is_available
        )

    async def fetch_context(self, question: str) -> str:
        """Fetch news + search context for a market question.

        Priority: NewsAPI → Tavily → RSS (Google News fallback).
        RSS is always available as fallback when APIs are rate-limited.
        Returns formatted context string, or empty string if nothing available.
        """
        news_articles: list[NewsArticle] = []
        search_results: list[WebSearchResult] = []

        # Run fetches (only if enabled)
        if self._news_fetcher.is_enabled:
            news_articles = await self._safe_fetch_news(question)

        if self._web_searcher.is_enabled:
            search_results = await self._safe_search_web(question)

        # Fallback to RSS if APIs returned nothing (rate-limited, disabled, etc.)
        if not news_articles and not search_results and self._rss_fetcher.is_available:
            news_articles = await self._safe_fetch_rss(question)
            if news_articles:
                logger.info("📡 RSS fallback: %d articles for '%s'", len(news_articles), question[:40])

        if not news_articles and not search_results:
            return ""

        return self.build_context(news_articles, search_results)

    async def _safe_fetch_rss(self, question: str) -> list[NewsArticle]:
        """Fetch RSS with error handling."""
        try:
            return await self._rss_fetcher.fetch(question)
        except Exception as exc:
            logger.warning("RSSFetcher failed: %s", exc)
            return []

    async def _safe_fetch_news(self, question: str) -> list[NewsArticle]:
        """Fetch news with error handling."""
        try:
            return await self._news_fetcher.fetch(question)
        except Exception as exc:
            logger.warning("NewsFetcher failed: %s", exc)
            return []

    async def _safe_search_web(self, question: str) -> list[WebSearchResult]:
        """Search web with error handling."""
        try:
            return await self._web_searcher.search(question)
        except Exception as exc:
            logger.warning("WebSearcher failed: %s", exc)
            return []

    def build_context(
        self,
        news_articles: list[NewsArticle],
        search_results: list[WebSearchResult],
    ) -> str:
        """Build formatted context string from news + search results.

        Formats:
        - NEWS section (up to max_news_articles)
        - WEB SEARCH section (up to max_search_results)
        - Truncated to max_context_chars
        """
        if not news_articles and not search_results:
            return ""

        parts = []

        # News section
        if news_articles:
            news_lines = []
            for article in news_articles[: self.config.max_news_articles]:
                news_lines.append(article.to_context_line())
            parts.append("📰 Recent News:\n" + "\n".join(news_lines))

        # Search section
        if search_results:
            search_lines = []
            for result in search_results[: self.config.max_search_results]:
                search_lines.append(result.to_context_line())
            parts.append("🔍 Web Search:\n" + "\n".join(search_lines))

        context = "\n\n".join(parts)

        # Truncate to max chars
        if len(context) > self.config.max_context_chars:
            context = context[: self.config.max_context_chars - 3] + "..."

        return context

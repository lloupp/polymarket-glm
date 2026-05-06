# Smart Order Selection + OSINT + Position Lifecycle Tracking

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Transform the bot from "estimate every market sequentially" into "triage best opportunities → OSINT deep-dive → decide → track until settlement" pipeline.

**Architecture:** Three new components: (1) **OrderScorer** — ranks markets by book quality + liquidity + edge potential before spending LLM calls, (2) **OSINTGate** — enriched OSINT context fetch that gates the trade decision (no OSINT = no trade), (3) **PositionLifecycle** — tracks every open position end-to-end with real-time monitoring, barrier management, and settlement resolution. The main loop changes from "process all markets" to "triage → top-N → OSINT → decide → track".

**Tech Stack:** Python 3.11+, httpx, pydantic, existing LLMRouter + ContextBuilder

---

## Current Flow (BEFORE)
```
fetch_markets() → for each market → estimate → signal → risk → execute
```
Problems:
- Every market gets an LLM call (expensive, wastes rate limits)
- No order book quality check before LLM call
- OSINT is fetched but not a gate — trades happen without context
- Position tracking is scattered (TP/SL in position_manager, settlement in settlement_tracker)
- No unified position lifecycle

## New Flow (AFTER)
```
fetch_markets() → OrderScorer.rank() → top-N markets
  → for each top market:
    1. Fetch order book depth (not just best bid/ask)
    2. OSINT deep-dive (news + web + RSS)
    3. IF osint_quality >= threshold: LLM estimate
    4. IF edge + confidence pass: signal → risk → execute
    5. PositionLifecycle.track() — monitor until close
```

---

## Sprint Tasks

### Task 1: OrderBookAnalyzer — depth scoring from existing book data

**Objective:** Analyze order book depth, spread, and liquidity to score market quality before spending LLM calls.

**Files:**
- Create: `polymarket_glm/ingestion/book_analyzer.py`
- Test: `tests/test_book_analyzer.py`

**Step 1: Write failing test**

```python
# tests/test_book_analyzer.py
import pytest
from polymarket_glm.ingestion.book_analyzer import BookAnalyzer, BookQuality, BookQualityConfig
from polymarket_glm.models import OrderBook, OrderBookLevel

def _make_book(bids=None, asks=None, market_id="test"):
    return OrderBook(
        market_id=market_id,
        bids=bids or [],
        asks=asks or [],
    )

class TestBookQualityModel:
    def test_book_quality_fields(self):
        bq = BookQuality(
            market_id="test",
            score=0.75,
            spread_bps=50.0,
            depth_usd=1000.0,
            imbalance=0.1,
            passable=True,
            reason="ok",
        )
        assert bq.score == 0.75
        assert bq.passable is True

class TestBookAnalyzer:
    def test_empty_book_fails(self):
        ba = BookAnalyzer()
        book = _make_book()
        result = ba.analyze(book)
        assert result.passable is False
        assert "no book" in result.reason.lower()

    def test_tight_spread_passes(self):
        ba = BookAnalyzer()
        book = _make_book(
            bids=[OrderBookLevel(price=0.49, size=500)],
            asks=[OrderBookLevel(price=0.51, size=500)],
        )
        result = ba.analyze(book)
        assert result.passable is True
        assert result.spread_bps > 0

    def test_wide_spread_fails(self):
        ba = BookAnalyzer(BookQualityConfig(max_spread_bps=500))
        book = _make_book(
            bids=[OrderBookLevel(price=0.20, size=100)],
            asks=[OrderBookLevel(price=0.80, size=100)],
        )
        result = ba.analyze(book)
        assert result.passable is False
        assert "spread" in result.reason.lower()

    def test_low_depth_fails(self):
        ba = BookAnalyzer(BookQualityConfig(min_depth_usd=500))
        book = _make_book(
            bids=[OrderBookLevel(price=0.49, size=10)],
            asks=[OrderBookLevel(price=0.51, size=10)],
        )
        result = ba.analyze(book)
        assert result.passable is False
        assert "depth" in result.reason.lower()

    def test_score_increases_with_depth(self):
        ba = BookAnalyzer()
        thin = _make_book(
            bids=[OrderBookLevel(price=0.49, size=50)],
            asks=[OrderBookLevel(price=0.51, size=50)],
        )
        thick = _make_book(
            bids=[OrderBookLevel(price=0.49, size=5000)],
            asks=[OrderBookLevel(price=0.51, size=5000)],
        )
        assert ba.analyze(thick).score > ba.analyze(thin).score

    def test_imbalance_detected(self):
        ba = BookAnalyzer()
        book = _make_book(
            bids=[OrderBookLevel(price=0.49, size=1000)],
            asks=[OrderBookLevel(price=0.51, size=100)],
        )
        result = ba.analyze(book)
        assert result.imbalance > 0  # bid-heavy

    def test_rank_markets_by_score(self):
        ba = BookAnalyzer()
        books = [
            _make_book(
                bids=[OrderBookLevel(price=0.49, size=50)],
                asks=[OrderBookLevel(price=0.51, size=50)],
                market_id="thin",
            ),
            _make_book(
                bids=[OrderBookLevel(price=0.49, size=5000)],
                asks=[OrderBookLevel(price=0.51, size=5000)],
                market_id="thick",
            ),
        ]
        ranked = ba.rank_markets(books)
        assert ranked[0].market_id == "thick"
        assert ranked[1].market_id == "thin"

    def test_rank_filters_non_passable(self):
        ba = BookAnalyzer(BookQualityConfig(max_spread_bps=200))
        books = [
            _make_book(
                bids=[OrderBookLevel(price=0.20, size=500)],
                asks=[OrderBookLevel(price=0.80, size=500)],
                market_id="wide",
            ),
            _make_book(
                bids=[OrderBookLevel(price=0.49, size=500)],
                asks=[OrderBookLevel(price=0.51, size=500)],
                market_id="tight",
            ),
        ]
        ranked = ba.rank_markets(books)
        assert len(ranked) == 1
        assert ranked[0].market_id == "tight"
```

**Step 2: Run test to verify failure**

Run: `cd /home/ubuntu/polymarket-glm && python -m pytest tests/test_book_analyzer.py -v`
Expected: FAIL — module not found

**Step 3: Write implementation**

```python
# polymarket_glm/ingestion/book_analyzer.py
"""Order book analyzer — depth scoring, spread/liquidity gates, market ranking.

Scores markets by book quality BEFORE spending LLM API calls.
This is the first gate in the pipeline: triage → OSINT → decide.
"""
from __future__ import annotations

import logging
from pydantic import BaseModel, Field

from polymarket_glm.models import OrderBook

logger = logging.getLogger(__name__)


class BookQualityConfig(BaseModel):
    """Thresholds for order book quality gates."""
    max_spread_bps: float = Field(default=400.0, gt=0, description="Max spread in bps to pass")
    min_depth_usd: float = Field(default=100.0, gt=0, description="Min total depth in USD")
    min_levels: int = Field(default=1, ge=1, description="Min number of bid+ask levels")


class BookQuality(BaseModel):
    """Result of analyzing a single order book."""
    market_id: str
    score: float = Field(ge=0, le=1, description="Quality score 0-1 (higher=better)")
    spread_bps: float = Field(ge=0, description="Spread in basis points")
    depth_usd: float = Field(ge=0, description="Total depth in USD (bid+ask)")
    imbalance: float = Field(ge=-1, le=1, description="Order flow imbalance (-1=ask-heavy, +1=bid-heavy)")
    passable: bool = Field(description="Whether this book passes quality gates")
    reason: str = ""


class BookAnalyzer:
    """Analyze order books for quality, depth, and spread.

    Usage:
        analyzer = BookAnalyzer()
        quality = analyzer.analyze(book)
        if quality.passable:
            # proceed with LLM estimate

        ranked = analyzer.rank_markets(books)
        # ranked is sorted by score desc, non-passable filtered out
    """

    def __init__(self, config: BookQualityConfig | None = None):
        self._config = config or BookQualityConfig()

    def analyze(self, book: OrderBook) -> BookQuality:
        """Analyze a single order book and return quality assessment."""
        market_id = book.market_id

        # Gate 1: non-empty book
        if not book.bids or not book.asks:
            return BookQuality(
                market_id=market_id,
                score=0.0,
                spread_bps=0.0,
                depth_usd=0.0,
                imbalance=0.0,
                passable=False,
                reason="No bids or asks in book",
            )

        # Gate 2: minimum levels
        total_levels = len(book.bids) + len(book.asks)
        if total_levels < self._config.min_levels:
            return BookQuality(
                market_id=market_id,
                score=0.0,
                spread_bps=0.0,
                depth_usd=0.0,
                imbalance=0.0,
                passable=False,
                reason=f"Only {total_levels} levels, need {self._config.min_levels}",
            )

        # Calculate spread
        best_bid = max(b.price for b in book.bids)
        best_ask = min(a.price for a in book.asks)
        mid = (best_bid + best_ask) / 2
        spread_bps = (best_ask - best_bid) / mid * 10_000 if mid > 0 else 9999.0

        # Gate 3: spread check
        if spread_bps > self._config.max_spread_bps:
            return BookQuality(
                market_id=market_id,
                score=0.05,
                spread_bps=spread_bps,
                depth_usd=0.0,
                imbalance=0.0,
                passable=False,
                reason=f"Spread {spread_bps:.0f}bps exceeds max {self._config.max_spread_bps:.0f}bps",
            )

        # Calculate depth (total USD on both sides)
        bid_depth_usd = sum(b.price * b.size for b in book.bids)
        ask_depth_usd = sum(a.price * a.size for a in book.asks)
        depth_usd = bid_depth_usd + ask_depth_usd

        # Gate 4: depth check
        if depth_usd < self._config.min_depth_usd:
            return BookQuality(
                market_id=market_id,
                score=0.1,
                spread_bps=spread_bps,
                depth_usd=depth_usd,
                imbalance=0.0,
                passable=False,
                reason=f"Depth ${depth_usd:.0f} below min ${self._config.min_depth_usd:.0f}",
            )

        # Calculate imbalance: (bid - ask) / (bid + ask)
        total_depth = bid_depth_usd + ask_depth_usd
        imbalance = (bid_depth_usd - ask_depth_usd) / total_depth if total_depth > 0 else 0.0

        # Score: weighted combination
        # Spread component: 0 spread → 1.0, max_spread → 0.0
        spread_score = max(0, 1.0 - spread_bps / self._config.max_spread_bps)
        # Depth component: logarithmic scaling, saturates around $50K
        import math
        depth_score = min(1.0, math.log1p(depth_usd) / math.log1p(50_000))
        # Imbalance penalty: slight imbalance is fine, extreme is penalized
        imbalance_penalty = 1.0 - abs(imbalance) * 0.3

        score = (spread_score * 0.4 + depth_score * 0.5 + imbalance_penalty * 0.1)
        score = max(0.0, min(1.0, score))

        return BookQuality(
            market_id=market_id,
            score=score,
            spread_bps=spread_bps,
            depth_usd=depth_usd,
            imbalance=imbalance,
            passable=True,
            reason="OK",
        )

    def rank_markets(self, books: list[OrderBook]) -> list[BookQuality]:
        """Analyze and rank multiple books by quality score.

        Returns only passable books, sorted by score descending.
        """
        results = [self.analyze(b) for b in books]
        passable = [r for r in results if r.passable]
        passable.sort(key=lambda r: r.score, reverse=True)
        return passable
```

**Step 4: Run test to verify pass**

Run: `cd /home/ubuntu/polymarket-glm && python -m pytest tests/test_book_analyzer.py -v`
Expected: 9 passed

**Step 5: Commit**

```bash
cd /home/ubuntu/polymarket-glm && git add polymarket_glm/ingestion/book_analyzer.py tests/test_book_analyzer.py && git commit -m "feat: OrderBookAnalyzer — depth scoring, spread/liquidity gates, market ranking"
```

---

### Task 2: OSINTGate — enriched context with quality scoring and trade gating

**Objective:** Create an OSINT quality gate that scores how much relevant context was found, and only allows trades when OSINT quality meets a threshold. No OSINT = no trade (too risky).

**Files:**
- Create: `polymarket_glm/strategy/osint_gate.py`
- Test: `tests/test_osint_gate.py`

**Step 1: Write failing test**

```python
# tests/test_osint_gate.py
import pytest
from polymarket_glm.strategy.osint_gate import OSINTGate, OSINTResult, OSINTConfig

class TestOSINTResult:
    def test_fields(self):
        r = OSINTResult(
            question="Will BTC hit $100K?",
            context_text="Bitcoin surged past...",
            quality_score=0.8,
            sources=3,
            article_count=2,
            search_count=1,
            passed=True,
            reason="sufficient context",
        )
        assert r.quality_score == 0.8
        assert r.passed is True
        assert r.sources == 3

class TestOSINTGate:
    def test_no_sources_fails(self):
        gate = OSINTGate()
        result = OSINTResult(
            question="test", context_text="", quality_score=0.0,
            sources=0, article_count=0, search_count=0,
            passed=False, reason="no sources available",
        )
        assert gate.should_gate(result) is True  # should block trade

    def test_high_quality_passes(self):
        gate = OSINTGate()
        result = OSINTResult(
            question="test", context_text="Rich context...", quality_score=0.9,
            sources=5, article_count=3, search_count=2,
            passed=True, reason="ok",
        )
        assert gate.should_gate(result) is False  # should allow trade

    def test_low_quality_gates(self):
        gate = OSINTGate(OSINTConfig(min_quality_score=0.5, min_sources=2))
        result = OSINTResult(
            question="test", context_text="Brief mention", quality_score=0.3,
            sources=1, article_count=1, search_count=0,
            passed=False, reason="insufficient context",
        )
        assert gate.should_gate(result) is True

    def test_sufficient_sources_passes(self):
        gate = OSINTGate(OSINTConfig(min_sources=2))
        result = OSINTResult(
            question="test", context_text="Context", quality_score=0.6,
            sources=3, article_count=2, search_count=1,
            passed=True, reason="ok",
        )
        assert gate.should_gate(result) is False

    def test_evaluate_with_no_context(self):
        """Evaluate returns a failing result when no context is fetched."""
        gate = OSINTGate()
        result = gate.evaluate(
            question="Will X happen?",
            news_articles=[],
            web_results=[],
            rss_articles=[],
        )
        assert result.passed is False
        assert result.quality_score == 0.0

    def test_evaluate_with_articles(self):
        """Evaluate passes when good articles are provided."""
        from polymarket_glm.strategy.context_fetcher import NewsArticle, WebSearchResult
        gate = OSINTGate(OSINTConfig(min_quality_score=0.3, min_sources=1))
        result = gate.evaluate(
            question="Will Bitcoin hit $100K?",
            news_articles=[
                NewsArticle(title="BTC surges", source="CoinDesk", description="Bitcoin price rally"),
                NewsArticle(title="Crypto market", source="Reuters", description="Market analysis"),
            ],
            web_results=[
                WebSearchResult(title="BTC analysis", content="Detailed analysis of Bitcoin price", score=0.8),
            ],
            rss_articles=[],
        )
        assert result.passed is True
        assert result.quality_score > 0
        assert result.sources >= 3

    def test_format_context_includes_all_sources(self):
        """Formatted context string includes articles + search results."""
        from polymarket_glm.strategy.context_fetcher import NewsArticle, WebSearchResult
        gate = OSINTGate()
        result = gate.evaluate(
            question="test",
            news_articles=[NewsArticle(title="News1", source="S1")],
            web_results=[WebSearchResult(title="Web1", content="Content1")],
            rss_articles=[NewsArticle(title="RSS1", source="RSS")],
        )
        assert "News1" in result.context_text
        assert "Web1" in result.context_text
        assert "RSS1" in result.context_text

    def test_bypass_mode(self):
        """OSINT gate can be bypassed (for testing or LLM-with-web-search providers)."""
        gate = OSINTGate(OSINTConfig(bypass=True))
        result = gate.evaluate(question="test", news_articles=[], web_results=[], rss_articles=[])
        assert result.passed is True
        assert "bypass" in result.reason.lower()
```

**Step 2: Run test to verify failure**

Run: `cd /home/ubuntu/polymarket-glm && python -m pytest tests/test_osint_gate.py -v`
Expected: FAIL — module not found

**Step 3: Write implementation**

```python
# polymarket_glm/strategy/osint_gate.py
"""OSINT Gate — quality-scored context evaluation and trade gating.

Gates trade decisions based on the quality and quantity of OSINT context
available. The principle: "No OSINT = No Trade" — trading without context
is gambling, not prediction.

Components:
- OSINTConfig: threshold configuration (min quality, min sources, bypass mode)
- OSINTResult: evaluation result with quality score, source counts, formatted context
- OSINTGate: evaluates context, decides whether to allow or gate a trade
"""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from polymarket_glm.strategy.context_fetcher import NewsArticle, WebSearchResult

logger = logging.getLogger(__name__)


class OSINTConfig(BaseModel):
    """Configuration for OSINT quality gate."""
    min_quality_score: float = Field(default=0.3, ge=0, le=1, description="Minimum context quality score to allow trade")
    min_sources: int = Field(default=1, ge=0, description="Minimum number of distinct sources")
    bypass: bool = Field(default=False, description="Bypass gate (always allow, for testing/LLM-web-search)")
    max_context_chars: int = Field(default=2000, ge=100, description="Max chars in formatted context")


class OSINTResult(BaseModel):
    """Result of OSINT evaluation for a market question."""
    question: str
    context_text: str = ""
    quality_score: float = Field(default=0.0, ge=0, le=1, description="Context quality 0-1")
    sources: int = Field(default=0, ge=0, description="Number of distinct sources found")
    article_count: int = 0
    search_count: int = 0
    rss_count: int = 0
    passed: bool = False
    reason: str = ""


class OSINTGate:
    """Evaluate OSINT context quality and gate trade decisions.

    Usage:
        gate = OSINTGate()
        result = gate.evaluate(
            question=market.question,
            news_articles=articles,
            web_results=searches,
            rss_articles=rss_items,
        )
        if gate.should_gate(result):
            # Skip this market — insufficient context
        else:
            # Proceed with LLM estimate using result.context_text
    """

    def __init__(self, config: OSINTConfig | None = None):
        self._config = config or OSINTConfig()

    def evaluate(
        self,
        question: str,
        news_articles: list[NewsArticle] | None = None,
        web_results: list[WebSearchResult] | None = None,
        rss_articles: list[NewsArticle] | None = None,
    ) -> OSINTResult:
        """Evaluate OSINT context for a market question.

        Scores based on: number of sources, content richness, relevance.
        Returns formatted context text and pass/fail result.
        """
        news_articles = news_articles or []
        web_results = web_results or []
        rss_articles = rss_articles or []

        # Bypass mode — always pass
        if self._config.bypass:
            return OSINTResult(
                question=question,
                context_text="[OSINT bypass mode]",
                quality_score=1.0,
                sources=999,
                article_count=len(news_articles),
                search_count=len(web_results),
                rss_count=len(rss_articles),
                passed=True,
                reason="OSINT gate bypassed",
            )

        # Count sources
        article_count = len(news_articles)
        search_count = len(web_results)
        rss_count = len(rss_articles)
        total_sources = article_count + search_count + rss_count

        # Build context text
        parts: list[str] = []
        if news_articles:
            parts.append("=== NEWS ===")
            for a in news_articles[:5]:
                parts.append(a.to_context_line())
        if web_results:
            parts.append("=== WEB SEARCH ===")
            for r in web_results[:3]:
                parts.append(r.to_context_line())
        if rss_articles:
            parts.append("=== RSS ===")
            for a in rss_articles[:5]:
                parts.append(a.to_context_line())

        context_text = "\n".join(parts)[:self._config.max_context_chars]

        # No context at all
        if total_sources == 0:
            return OSINTResult(
                question=question,
                context_text="",
                quality_score=0.0,
                sources=0,
                article_count=0,
                search_count=0,
                rss_count=0,
                passed=False,
                reason="No OSINT sources available",
            )

        # Quality scoring
        # Source diversity: having multiple source types is better
        source_types = sum(1 for cnt in [article_count, search_count, rss_count] if cnt > 0)
        diversity_score = min(1.0, source_types / 3.0)  # 0-1, max at 3 types

        # Volume score: more sources = better (logarithmic, saturates)
        import math
        volume_score = min(1.0, math.log1p(total_sources) / math.log1p(10))

        # Richness score: longer context = more info
        richness_score = min(1.0, len(context_text) / 1000.0)

        quality_score = (diversity_score * 0.3 + volume_score * 0.4 + richness_score * 0.3)

        # Gate decision
        passed = (
            quality_score >= self._config.min_quality_score
            and total_sources >= self._config.min_sources
        )

        reason = "OK" if passed else (
            f"Quality {quality_score:.2f} < {self._config.min_quality_score:.2f}"
            if quality_score < self._config.min_quality_score
            else f"Sources {total_sources} < {self._config.min_sources}"
        )

        if not passed:
            logger.info(
                "🔒 OSINT gate: %s — %s (quality=%.2f, sources=%d)",
                question[:40], reason, quality_score, total_sources,
            )

        return OSINTResult(
            question=question,
            context_text=context_text,
            quality_score=quality_score,
            sources=total_sources,
            article_count=article_count,
            search_count=search_count,
            rss_count=rss_count,
            passed=passed,
            reason=reason,
        )

    def should_gate(self, result: OSINTResult) -> bool:
        """Return True if this trade should be BLOCKED (gated)."""
        if self._config.bypass:
            return False
        return not result.passed
```

**Step 4: Run test to verify pass**

Run: `cd /home/ubuntu/polymarket-glm && python -m pytest tests/test_osint_gate.py -v`
Expected: 9 passed

**Step 5: Commit**

```bash
cd /home/ubuntu/polymarket-glm && git add polymarket_glm/strategy/osint_gate.py tests/test_osint_gate.py && git commit -m "feat: OSINTGate — quality-scored context evaluation and trade gating"
```

---

### Task 3: PositionLifecycle — unified position tracking end-to-end

**Objective:** Create a unified PositionLifecycle manager that tracks every open position from entry to exit (TP/SL/expiry/resolution/manual), providing real-time monitoring, barrier checks, and P&L tracking.

**Files:**
- Create: `polymarket_glm/execution/position_lifecycle.py`
- Test: `tests/test_position_lifecycle.py`

**Step 1: Write failing test**

```python
# tests/test_position_lifecycle.py
import pytest
from polymarket_glm.execution.position_lifecycle import (
    PositionLifecycle, LifecycleConfig, PositionSnapshot, PositionState,
)
from polymarket_glm.models import Position
from datetime import datetime

def _make_pos(market_id="m1", outcome="Yes", size=100, avg_price=0.50, status="open"):
    return Position(
        market_id=market_id, outcome=outcome, size=size,
        avg_price=avg_price, status=status,
        target_price=0.75, stop_loss_price=0.25,
        opened_at_iteration=1,
    )

class TestPositionSnapshot:
    def test_fields(self):
        snap = PositionSnapshot(
            market_id="m1", outcome="Yes", state=PositionState.OPEN,
            entry_price=0.50, current_price=0.60, unrealized_pnl=10.0,
            unrealized_pnl_pct=20.0, hold_iterations=5,
            barriers_ok=True, barrier_reason="",
        )
        assert snap.state == PositionState.OPEN
        assert snap.unrealized_pnl == 10.0

class TestPositionLifecycle:
    def test_register_position(self):
        pl = PositionLifecycle()
        pos = _make_pos()
        pl.register(pos, iteration=1)
        assert "m1" in pl.active_positions

    def test_snapshot_open_position(self):
        pl = PositionLifecycle()
        pos = _make_pos(avg_price=0.50)
        pl.register(pos, iteration=1)
        snap = pl.snapshot("m1", "Yes", current_price=0.60, iteration=6)
        assert snap.state == PositionState.OPEN
        assert snap.unrealized_pnl > 0
        assert snap.hold_iterations == 5

    def test_snapshot_at_tp(self):
        pl = PositionLifecycle()
        pos = _make_pos(avg_price=0.50, size=100)
        pos.target_price = 0.75
        pl.register(pos, iteration=1)
        snap = pl.snapshot("m1", "Yes", current_price=0.76, iteration=10)
        assert snap.state == PositionState.TAKE_PROFIT

    def test_snapshot_at_sl(self):
        pl = PositionLifecycle()
        pos = _make_pos(avg_price=0.50, size=100)
        pos.stop_loss_price = 0.25
        pl.register(pos, iteration=1)
        snap = pl.snapshot("m1", "Yes", current_price=0.24, iteration=10)
        assert snap.state == PositionState.STOP_LOSS

    def test_snapshot_expired(self):
        pl = PositionLifecycle()
        pos = _make_pos(avg_price=0.50, size=100)
        pos.end_date_iso = "2020-01-01T00:00:00Z"  # already expired
        pl.register(pos, iteration=1)
        snap = pl.snapshot("m1", "Yes", current_price=0.55, iteration=10)
        assert snap.state == PositionState.EXPIRED

    def test_close_position(self):
        pl = PositionLifecycle()
        pos = _make_pos()
        pl.register(pos, iteration=1)
        pl.close("m1", "Yes", close_price=0.70, reason="take_profit", iteration=5)
        assert "m1" not in pl.active_positions
        assert len(pl.closed_positions) == 1

    def test_all_snapshots(self):
        pl = PositionLifecycle()
        pl.register(_make_pos("m1", "Yes"), iteration=1)
        pl.register(_make_pos("m2", "Yes"), iteration=1)
        price_lookup = {("m1", "Yes"): 0.55, ("m2", "Yes"): 0.45}
        snaps = pl.all_snapshots(price_lookup, iteration=5)
        assert len(snaps) == 2

    def test_empty_lifecycle(self):
        pl = PositionLifecycle()
        assert len(pl.active_positions) == 0
        assert len(pl.closed_positions) == 0

    def test_summary(self):
        pl = PositionLifecycle()
        pl.register(_make_pos("m1", "Yes", avg_price=0.50, size=100), iteration=1)
        pl.register(_make_pos("m2", "Yes", avg_price=0.30, size=200), iteration=2)
        price_lookup = {("m1", "Yes"): 0.60, ("m2", "Yes"): 0.40}
        summary = pl.summary(price_lookup, iteration=10)
        assert summary["open_count"] == 2
        assert summary["total_unrealized_pnl"] > 0
```

**Step 2: Run test to verify failure**

Run: `cd /home/ubuntu/polymarket-glm && python -m pytest tests/test_position_lifecycle.py -v`
Expected: FAIL — module not found

**Step 3: Write implementation**

```python
# polymarket_glm/execution/position_lifecycle.py
"""Position Lifecycle — unified tracking from entry to exit.

Manages the full lifecycle of every open position:
- Registration on fill
- Real-time monitoring with barrier checks (TP/SL/expiry)
- Snapshot generation for audit/telegram
- Close tracking with P&L and reason
- Summary for portfolio reporting
"""
from __future__ import annotations

import enum
import logging
from datetime import datetime
from typing import NamedTuple

from pydantic import BaseModel, Field

from polymarket_glm.models import Position

logger = logging.getLogger(__name__)


class PositionState(str, enum.Enum):
    """Current state of a position in its lifecycle."""
    OPEN = "open"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TIME_LIMIT = "time_limit"
    EXPIRED = "expired"
    RESOLVED = "resolved"
    MANUAL_CLOSE = "manual_close"


class LifecycleConfig(BaseModel):
    """Configuration for position lifecycle tracking."""
    max_hold_iterations: int = Field(default=360, description="Max iterations before time-limit close (360 * 120s = 12h)")
    tolerance: float = Field(default=1e-9, description="Floating point tolerance for TP/SL comparisons")


class PositionSnapshot(BaseModel):
    """Point-in-time snapshot of a tracked position."""
    market_id: str
    outcome: str
    state: PositionState
    entry_price: float
    current_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    hold_iterations: int
    barriers_ok: bool
    barrier_reason: str = ""


class _TrackedPosition(BaseModel):
    """Internal: tracks a position with entry metadata."""
    position: Position
    entry_iteration: int = 0


class PositionLifecycle:
    """Unified position lifecycle manager.

    Tracks positions from fill to close, providing:
    - Real-time barrier checks (TP/SL/expiry/time-limit)
    - Position snapshots for monitoring and alerts
    - Close tracking with P&L and reason
    - Summary for portfolio reporting

    Usage:
        lifecycle = PositionLifecycle()
        lifecycle.register(position, iteration=1)
        snap = lifecycle.snapshot("market_id", "Yes", current_price=0.60, iteration=5)
        if snap.state != PositionState.OPEN:
            # Need to close this position
            lifecycle.close("market_id", "Yes", current_price, snap.state.value, 5)
    """

    def __init__(self, config: LifecycleConfig | None = None):
        self._config = config or LifecycleConfig()
        self.active_positions: dict[tuple[str, str], _TrackedPosition] = {}
        self.closed_positions: list[dict] = []

    def register(self, position: Position, iteration: int = 0) -> None:
        """Register a new position for tracking."""
        key = (position.market_id, position.outcome)
        self.active_positions[key] = _TrackedPosition(
            position=position, entry_iteration=iteration,
        )
        logger.info(
            "📋 Position registered: %s/%s entry=%.4f iter=%d",
            position.market_id[:12], position.outcome,
            position.avg_price, iteration,
        )

    def snapshot(
        self,
        market_id: str,
        outcome: str,
        current_price: float,
        iteration: int,
    ) -> PositionSnapshot:
        """Generate a snapshot of a position's current state.

        Checks all barriers (TP/SL/expiry/time-limit) and determines
        the position's lifecycle state.
        """
        key = (market_id, outcome)
        tracked = self.active_positions.get(key)
        if tracked is None:
            return PositionSnapshot(
                market_id=market_id, outcome=outcome,
                state=PositionState.OPEN, entry_price=0,
                current_price=current_price, unrealized_pnl=0,
                unrealized_pnl_pct=0, hold_iterations=0,
                barriers_ok=True, barrier_reason="not tracked",
            )

        pos = tracked.position
        hold_iterations = iteration - tracked.entry_iteration

        # Calculate P&L
        if pos.avg_price > 0:
            return_pct = (current_price - pos.avg_price) / pos.avg_price
            unrealized_pnl = return_pct * pos.size * pos.avg_price
            unrealized_pnl_pct = return_pct * 100
        else:
            unrealized_pnl = 0.0
            unrealized_pnl_pct = 0.0

        # Check barriers
        state = PositionState.OPEN
        barrier_reason = ""
        tol = self._config.tolerance

        # 1. Expiry (check first — time-sensitive)
        if pos.end_date_iso:
            try:
                end_dt = datetime.fromisoformat(pos.end_date_iso.replace("Z", "+00:00"))
                if datetime.utcnow() > end_dt.replace(tzinfo=None):
                    state = PositionState.EXPIRED
                    barrier_reason = "Market expired"
            except (ValueError, TypeError):
                pass

        # 2. Take-profit
        if state == PositionState.OPEN and pos.target_price is not None:
            if current_price >= pos.target_price - tol:
                state = PositionState.TAKE_PROFIT
                barrier_reason = f"TP hit: {current_price:.4f} >= {pos.target_price:.4f}"

        # 3. Stop-loss
        if state == PositionState.OPEN and pos.stop_loss_price is not None:
            if current_price <= pos.stop_loss_price + tol:
                state = PositionState.STOP_LOSS
                barrier_reason = f"SL hit: {current_price:.4f} <= {pos.stop_loss_price:.4f}"

        # 4. Time limit
        if state == PositionState.OPEN and hold_iterations >= self._config.max_hold_iterations:
            state = PositionState.TIME_LIMIT
            barrier_reason = f"Time limit: {hold_iterations} >= {self._config.max_hold_iterations}"

        barriers_ok = state == PositionState.OPEN

        return PositionSnapshot(
            market_id=market_id,
            outcome=outcome,
            state=state,
            entry_price=pos.avg_price,
            current_price=current_price,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=unrealized_pnl_pct,
            hold_iterations=hold_iterations,
            barriers_ok=barriers_ok,
            barrier_reason=barrier_reason,
        )

    def close(
        self,
        market_id: str,
        outcome: str,
        close_price: float,
        reason: str,
        iteration: int,
    ) -> dict:
        """Close a tracked position and record the result."""
        key = (market_id, outcome)
        tracked = self.active_positions.pop(key, None)
        if tracked is None:
            logger.warning("Close called for untracked position %s/%s", market_id[:12], outcome)
            return {"market_id": market_id, "outcome": outcome, "pnl": 0, "reason": reason}

        pos = tracked.position
        pnl = (close_price - pos.avg_price) * pos.size if pos.avg_price > 0 else 0

        close_record = {
            "market_id": market_id,
            "outcome": outcome,
            "entry_price": pos.avg_price,
            "close_price": close_price,
            "size": pos.size,
            "pnl": pnl,
            "pnl_pct": (close_price - pos.avg_price) / pos.avg_price * 100 if pos.avg_price > 0 else 0,
            "reason": reason,
            "entry_iteration": tracked.entry_iteration,
            "close_iteration": iteration,
            "hold_iterations": iteration - tracked.entry_iteration,
        }

        self.closed_positions.append(close_record)
        logger.info(
            "🏁 Position closed: %s/%s reason=%s pnl=$%.2f entry=%.4f exit=%.4f",
            market_id[:12], outcome, reason, pnl, pos.avg_price, close_price,
        )
        return close_record

    def all_snapshots(
        self,
        price_lookup: dict[tuple[str, str], float],
        iteration: int,
    ) -> list[PositionSnapshot]:
        """Generate snapshots for all active positions."""
        snapshots = []
        for key, tracked in self.active_positions.items():
            price = price_lookup.get(key)
            if price is None:
                continue
            snap = self.snapshot(key[0], key[1], price, iteration)
            snapshots.append(snap)
        return snapshots

    def summary(
        self,
        price_lookup: dict[tuple[str, str], float],
        iteration: int,
    ) -> dict:
        """Generate a portfolio summary of all tracked positions."""
        snaps = self.all_snapshots(price_lookup, iteration)
        total_unrealized = sum(s.unrealized_pnl for s in snaps)
        positions_needing_close = [s for s in snaps if not s.barriers_ok]

        return {
            "open_count": len(snaps),
            "closed_count": len(self.closed_positions),
            "total_unrealized_pnl": total_unrealized,
            "positions_needing_close": len(positions_needing_close),
            "close_details": [
                {"market_id": s.market_id, "outcome": s.outcome, "reason": s.barrier_reason}
                for s in positions_needing_close
            ],
        }
```

**Step 4: Run test to verify pass**

Run: `cd /home/ubuntu/polymarket-glm && python -m pytest tests/test_position_lifecycle.py -v`
Expected: 10 passed

**Step 5: Commit**

```bash
cd /home/ubuntu/polymarket-glm && git add polymarket_glm/execution/position_lifecycle.py tests/test_position_lifecycle.py && git commit -m "feat: PositionLifecycle — unified position tracking from entry to exit"
```

---

### Task 4: Integrate BookAnalyzer + OSINTGate into `_process_market`

**Objective:** Wire the new BookAnalyzer and OSINTGate into the simulation loop. The flow becomes: book quality check → OSINT fetch → OSINT gate → LLM estimate → signal → trade.

**Files:**
- Modify: `scripts/run_simulation.py` — `_process_market()` method

**Step 1: Write failing test for integration**

Add to `tests/test_book_analyzer.py`:

```python
class TestBookAnalyzerIntegration:
    """Integration: BookAnalyzer works with real OrderBook from models."""
    def test_real_book_format(self):
        from polymarket_glm.models import OrderBook, OrderBookLevel
        ba = BookAnalyzer()
        book = OrderBook(
            market_id="0x123",
            bids=[
                OrderBookLevel(price=0.48, size=200),
                OrderBookLevel(price=0.47, size=300),
                OrderBookLevel(price=0.46, size=500),
            ],
            asks=[
                OrderBookLevel(price=0.52, size=200),
                OrderBookLevel(price=0.53, size=300),
            ],
        )
        result = ba.analyze(book)
        assert result.passable is True
        assert result.depth_usd > 0
```

**Step 2: Modify `_process_market()` in `run_simulation.py`**

Add BookAnalyzer and OSINTGate initialization in `__init__`:

```python
# In SimulationEngine.__init__():
from polymarket_glm.ingestion.book_analyzer import BookAnalyzer, BookQualityConfig
from polymarket_glm.strategy.osint_gate import OSINTGate, OSINTConfig

self._book_analyzer = BookAnalyzer(BookQualityConfig(
    max_spread_bps=400,
    min_depth_usd=100,
))
self._osint_gate = OSINTGate(OSINTConfig(
    min_quality_score=0.3,
    min_sources=1,
))
```

Modify `_process_market()` to add gates BEFORE LLM call:

```python
# After fetching order book (around line 929-943), add:

# ── Book Quality Gate ─────────────────────────────────
book_quality = self._book_analyzer.analyze(book)
if not book_quality.passable:
    logger.info("⏭ Book quality fail: %s — %s", question[:40], book_quality.reason)
    result = DecisionResult(
        decision=DecisionType.HOLD,
        market_id=market_id, question=question,
        reason=f"book_quality:{book_quality.reason}",
        llm_source="n/a", llm_state="n/a",
        portfolio_cash=cash, portfolio_positions_value=pos_val,
        portfolio_total=total,
    )
    self._log_audit(result)
    return result

# ... existing OSINT fetch code ...
# After fetching news_context (around line 961-971), add:

# ── OSINT Quality Gate ────────────────────────────────
osint_result = self._osint_gate.evaluate(
    question=market.question,
    news_articles=news_articles if self._context_builder.has_any_source else [],
    web_results=web_results if self._context_builder.has_any_source else [],
    rss_articles=rss_articles if self._context_builder.has_any_source else [],
)
if self._osint_gate.should_gate(osint_result):
    logger.info("🔒 OSINT gate: %s — %s", question[:40], osint_result.reason)
    result = DecisionResult(
        decision=DecisionType.HOLD,
        market_id=market_id, question=question,
        reason=f"osint_gate:{osint_result.reason}",
        llm_source="n/a", llm_state="n/a",
        context_available=False,
        portfolio_cash=cash, portfolio_positions_value=pos_val,
        portfolio_total=total,
    )
    self._log_audit(result)
    return result

# Use enriched context from osint_result instead of raw news_context
news_context = osint_result.context_text or news_context
```

**Step 3: Run all tests**

Run: `cd /home/ubuntu/polymarket-glm && python -m pytest -q`
Expected: All tests pass (existing + new)

**Step 4: Commit**

```bash
cd /home/ubuntu/polymarket-glm && git add scripts/run_simulation.py tests/ && git commit -m "feat: integrate BookAnalyzer + OSINTGate into _process_market pipeline"
```

---

### Task 5: Integrate PositionLifecycle into simulation loop

**Objective:** Replace scattered position monitoring code with unified PositionLifecycle tracking. Register on fill, monitor every iteration, close on barrier trigger.

**Files:**
- Modify: `scripts/run_simulation.py` — `__init__()`, `_run_iteration()`, monitor mode

**Step 1: Modify `SimulationEngine.__init__()`**

```python
from polymarket_glm.execution.position_lifecycle import PositionLifecycle, LifecycleConfig

self._lifecycle = PositionLifecycle(LifecycleConfig(max_hold_iterations=360))
```

**Step 2: Register on fill in `_process_market()`**

After successful fill (around line 1162-1225):

```python
# Register position in lifecycle tracker
self._lifecycle.register(pos, iteration=self._iteration)
```

**Step 3: Replace MONITOR mode with lifecycle-driven checks**

In `_run_iteration()` MONITOR section (around line 470-588), replace the manual position iteration with:

```python
# Build price lookup
price_lookup = {}
for pos in acct.positions:
    for m in markets:
        if m.market_id == pos.market_id and m.outcome_prices:
            cur_price = m.outcome_prices[0] if pos.outcome.upper() == "YES" else (
                m.outcome_prices[1] if len(m.outcome_prices) > 1 else (1 - m.outcome_prices[0])
            )
            price_lookup[(pos.market_id, pos.outcome)] = cur_price
            break

# Generate lifecycle snapshots
snapshots = self._lifecycle.all_snapshots(price_lookup, self._iteration)
for snap in snapshots:
    if snap.barriers_ok:
        logger.debug("📊 Monitoring: %s/%s entry=%.4f cur=%.4f pnl=$%.2f",
            snap.market_id[:12], snap.outcome, snap.entry_price,
            snap.current_price, snap.unrealized_pnl)
    else:
        # Position needs closing
        reason = snap.state.value
        try:
            pos = self._executor.get_position(snap.market_id, snap.outcome)
            if pos and pos.status == "open":
                exit_params = self._position_mgr.calculate_exit_order(
                    pos, snap.current_price, reason, self._iteration,
                )
                exit_order = OrderRequest(
                    market_id=exit_params["market_id"],
                    side=exit_params["side"],
                    outcome=exit_params["outcome"],
                    price=exit_params["price"],
                    size=exit_params["size"],
                    iteration=exit_params["_iteration"],
                    close_reason=reason,
                )
                fill = self._executor.submit_order_sync(exit_order)
                if fill.filled:
                    self._lifecycle.close(
                        snap.market_id, snap.outcome,
                        snap.current_price, reason, self._iteration,
                    )
                    closed_count += 1
        except Exception as exc:
            logger.warning("Lifecycle close error: %s", exc)
```

**Step 4: Run all tests**

Run: `cd /home/ubuntu/polymarket-glm && python -m pytest -q`
Expected: All tests pass

**Step 5: Commit**

```bash
cd /home/ubuntu/polymarket-glm && git add scripts/run_simulation.py && git commit -m "feat: integrate PositionLifecycle into simulation loop for end-to-end tracking"
```

---

### Task 6: Triaged market processing — rank before LLM

**Objective:** Change SEEK mode from "process all markets sequentially" to "fetch all books → rank by quality → process top-N only". This saves LLM API calls for the best opportunities.

**Files:**
- Modify: `scripts/run_simulation.py` — SEEK mode section (around line 590-657)

**Step 1: Modify SEEK mode**

```python
# ── SEEK MODE: triage → top-N → process ──

# 1. Scan markets
markets = await self._fetcher.fetch_markets(self._market_filter)
if not markets:
    logger.info("No markets found — sleeping")
    return

# 2. Fetch books for all markets and rank by quality
book_qualities: list[tuple] = []  # (market, book, quality)
for market in markets[:50]:  # cap initial scan
    token_id = market.tokens[0] if market.tokens else None
    if not token_id:
        continue
    try:
        book = await self._price_feed.fetch_book(token_id)
        if book:
            quality = self._book_analyzer.analyze(book)
            if quality.passable:
                book_qualities.append((market, book, quality))
    except Exception:
        continue

# 3. Rank by quality score
book_qualities.sort(key=lambda x: x[2].score, reverse=True)
top_n = book_qualities[:10]  # only process top 10 by book quality

logger.info(
    "Triage: %d markets scanned → %d passable → %d top-N selected",
    len(markets), len(book_qualities), len(top_n),
)

# 4. Process top-N markets (existing _process_market handles OSINT gate + LLM)
signals_this_round = 0
fills_this_round = 0
for market, book, quality in top_n:
    # Store book in cache so _process_market doesn't re-fetch
    self._book_cache[market.market_id] = book
    try:
        result = await self._process_market(market)
        # ... existing signal/fill tracking ...
```

**Step 2: Add book cache to avoid double-fetch**

Add `self._book_cache: dict[str, OrderBook] = {}` in `__init__`, and in `_process_market()` check cache before fetching:

```python
# In _process_market, replace book fetch:
if market.market_id in self._book_cache:
    book = self._book_cache.pop(market.market_id)
else:
    book = await self._price_feed.fetch_book(token_id)
```

**Step 3: Run all tests**

Run: `cd /home/ubuntu/polymarket-glm && python -m pytest -q`
Expected: All tests pass

**Step 4: Commit**

```bash
cd /home/ubuntu/polymarket-glm && git add scripts/run_simulation.py && git commit -m "feat: triaged market processing — rank by book quality before LLM calls"
```

---

### Task 7: Telegram enrichment — OSINT + lifecycle reporting

**Objective:** Add OSINT quality and lifecycle state to Telegram alerts so you can see why trades were taken/rejected and how positions are tracking.

**Files:**
- Modify: `polymarket_glm/monitoring/telegram_formatters.py`
- Modify: `scripts/run_simulation.py` — alert messages

**Step 1: Add formatter functions**

```python
# In telegram_formatters.py, add:

def format_osint_gate_block(question: str, reason: str, quality: float, sources: int) -> str:
    """Format OSINT gate rejection for Telegram."""
    return (
        f"🔒 **OSINT Gate Blocked**\n"
        f"Market: {question[:60]}\n"
        f"Reason: {reason}\n"
        f"Quality: {quality:.2f} | Sources: {sources}"
    )

def format_lifecycle_summary(open_count: int, closed_count: int, unrealized_pnl: float, needing_close: list) -> str:
    """Format position lifecycle summary for Telegram."""
    lines = [
        f"📊 **Position Lifecycle**",
        f"Open: {open_count} | Closed: {closed_count}",
        f"Unrealized P&L: ${unrealized_pnl:+.2f}",
    ]
    if needing_close:
        lines.append(f"⚠️ Needing attention: {len(needing_close)}")
        for c in needing_close[:3]:
            lines.append(f"  • {c['market_id'][:12]}: {c['reason']}")
    return "\n".join(lines)

def format_book_quality_summary(total: int, passable: int, top_n: int) -> str:
    """Format market triage summary for Telegram."""
    return (
        f"🔍 **Market Triage**\n"
        f"Scanned: {total} | Passable: {passable} | Top-N: {top_n}"
    )
```

**Step 2: Wire formatters into simulation loop**

In SEEK mode after triage:
```python
if self._bot:
    triage_msg = format_book_quality_summary(len(markets), len(book_qualities), len(top_n))
    await self._bot.send_message(triage_msg)
```

After OSINT gate rejection:
```python
if self._bot:
    gate_msg = format_osint_gate_block(question, osint_result.reason, osint_result.quality_score, osint_result.sources)
    await self._bot.send_message(gate_msg)
```

In MONITOR mode after lifecycle check:
```python
if self._bot and snapshots:
    ls = self._lifecycle.summary(price_lookup, self._iteration)
    if ls["open_count"] > 0:
        lifecycle_msg = format_lifecycle_summary(
            ls["open_count"], ls["closed_count"],
            ls["total_unrealized_pnl"], ls["close_details"],
        )
        await self._bot.send_message(lifecycle_msg)
```

**Step 3: Write tests for formatters**

```python
# Add to tests/test_telegram_formatters.py:

def test_format_osint_gate_block():
    msg = format_osint_gate_block("Will BTC hit $100K?", "Quality 0.20 < 0.30", 0.20, 1)
    assert "OSINT Gate Blocked" in msg
    assert "0.20" in msg

def test_format_lifecycle_summary():
    msg = format_lifecycle_summary(3, 1, 15.50, [{"market_id": "abc", "reason": "TP hit"}])
    assert "Lifecycle" in msg
    assert "3" in msg

def test_format_book_quality_summary():
    msg = format_book_quality_summary(50, 15, 10)
    assert "Triage" in msg
    assert "50" in msg
```

**Step 4: Run all tests**

Run: `cd /home/ubuntu/polymarket-glm && python -m pytest -q`
Expected: All tests pass

**Step 5: Commit**

```bash
cd /home/ubuntu/polymarket-glm && git add polymarket_glm/monitoring/telegram_formatters.py tests/test_telegram_formatters.py scripts/run_simulation.py && git commit -m "feat: Telegram enrichment — OSINT gate, lifecycle, and triage reporting"
```

---

### Task 8: Full integration test + pytest green + push

**Objective:** Run the full test suite, fix any integration issues, commit, and push.

**Step 1: Run full suite**

Run: `cd /home/ubuntu/polymarket-glm && python -m pytest -q`
Expected: All 696+ tests pass

**Step 2: Run with -v to check test names**

Run: `cd /home/ubuntu/polymarket-glm && python -m pytest tests/test_book_analyzer.py tests/test_osint_gate.py tests/test_position_lifecycle.py -v`

**Step 3: Fix any issues found**

If any test fails, fix and re-run.

**Step 4: Push**

```bash
cd /home/ubuntu/polymarket-glm && git push origin main
```

**Step 5: Update sprint state skill**

Update `polymarket-signal-lab-sprint-state` with Sprint 19 details.

---

## Summary

| Task | Component | What Changes |
|------|-----------|-------------|
| 1 | BookAnalyzer | New: depth scoring, spread/liquidity gates, market ranking |
| 2 | OSINTGate | New: context quality scoring, trade gating (no OSINT = no trade) |
| 3 | PositionLifecycle | New: unified position tracking from entry to exit |
| 4 | Integration: Book+OSINT → _process_market | Wire gates before LLM calls |
| 5 | Integration: Lifecycle → loop | Replace scattered monitoring with lifecycle |
| 6 | Triage: rank before LLM | Process top-N by book quality only |
| 7 | Telegram: OSINT + lifecycle alerts | Enriched reporting |
| 8 | Full test + push | Green suite, push to origin |

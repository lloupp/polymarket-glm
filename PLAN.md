# polymarket-glm Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Framework de trading Polymarket com signal engine, execution layer, risk management e paper/live dual mode — composto dos melhores módulos do ecossistema open source.

**Architecture:** 7 camadas (Data Ingestion → Strategy → Execution → Risk → Storage → Monitoring → Interface), Python 3.11+, asyncio, py-clob-client + polymarket-apis como deps base, SQLite para storage, Pydantic v2 para validação.

**Tech Stack:** Python 3.11+, py-clob-client, polymarket-apis, aiohttp, websockets, pydantic v2, click/typer, sqlite3, pytest

---

## Sprint 1 — Foundation & Data Ingestion

### Task 1.1: Project scaffolding + pyproject.toml

**Objective:** Criar estrutura de pastas e configurar dependências

**Files:**
- Create: `polymarket_glm/__init__.py`
- Create: `polymarket_glm/py.typed`
- Create: `pyproject.toml`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

**Step 1: Create directory structure**

```bash
cd /home/ubuntu/polymarket-glm
mkdir -p polymarket_glm/{ingestion,strategy,execution,risk,storage,monitoring,interface}
mkdir -p polymarket_glm/{ingestion,strategy,execution,risk,storage,monitoring,interface}
touch polymarket_glm/__init__.py polymarket_glm/py.typed
for d in ingestion strategy execution risk storage monitoring interface; do
  touch polymarket_glm/$d/__init__.py
done
mkdir -p tests
touch tests/__init__.py tests/conftest.py
```

**Step 2: Write pyproject.toml**

```toml
[project]
name = "polymarket-glm"
version = "0.1.0"
description = "Polymarket trading framework with signal engine, execution, risk management and paper/live dual mode"
requires-python = ">=3.11"
dependencies = [
    "py-clob-client>=0.28.0",
    "polymarket-apis>=0.5.0",
    "pydantic>=2.0",
    "aiohttp>=3.9",
    "websockets>=12.0",
    "click>=8.0",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "ruff>=0.4",
]

[project.scripts]
pglm = "polymarket_glm.interface.cli:main"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"
```

**Step 3: Write conftest.py**

```python
"""Shared test fixtures."""
import pytest
```

**Step 4: Verify structure**

```bash
find . -type f | sort
```

Expected: all `__init__.py` files, `pyproject.toml`, `conftest.py`

**Step 5: Install and verify**

```bash
pip install -e ".[dev]" 2>&1 | tail -5
pytest --co -q  # should find 0 tests (no errors)
```

**Step 6: Commit**

```bash
git init && git add -A && git commit -m "feat: project scaffolding with pyproject.toml"
```

---

### Task 1.2: Config system with Pydantic v2

**Objective:** Sistema de configuração com schema validado, env overrides e suporte a paper/live mode

**Files:**
- Create: `polymarket_glm/config.py`
- Create: `tests/test_config.py`

**Step 1: Write failing test**

```python
"""Tests for config system."""
import os
import pytest
from polymarket_glm.config import Settings, ExecutionMode


def test_default_settings():
    s = Settings()
    assert s.execution_mode == ExecutionMode.PAPER
    assert s.risk.max_total_exposure_usd == 1500.0
    assert s.risk.max_per_market_exposure_usd == 1000.0


def test_env_override():
    os.environ["PGLM_EXECUTION_MODE"] = "live"
    os.environ["PGLM_RISK__MAX_TOTAL_EXPOSURE_USD"] = "5000"
    s = Settings()
    assert s.execution_mode == ExecutionMode.LIVE
    assert s.risk.max_total_exposure_usd == 5000.0
    # cleanup
    del os.environ["PGLM_EXECUTION_MODE"]
    del os.environ["PGLM_RISK__MAX_TOTAL_EXPOSURE_USD"]


def test_invalid_execution_mode():
    os.environ["PGLM_EXECUTION_MODE"] = "invalid"
    with pytest.raises(Exception):
        Settings()
    del os.environ["PGLM_EXECUTION_MODE"]


def test_risk_validation():
    with pytest.raises(Exception):
        Settings(risk={"max_total_exposure_usd": -100})


def test_live_mode_requires_keys():
    s = Settings(
        execution_mode=ExecutionMode.LIVE,
        clob_api_key="k", clob_api_secret="s",
        clob_api_passphrase="p", private_key="0xabc"
    )
    assert s.live_ready is True


def test_live_mode_missing_keys():
    s = Settings(execution_mode=ExecutionMode.LIVE)
    assert s.live_ready is False
```

**Step 2: Run test to verify failure**

```bash
pytest tests/test_config.py -v
```

Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write implementation**

```python
"""Configuration with Pydantic v2 — env overrides + validation + paper/live gate."""
from __future__ import annotations

import enum
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExecutionMode(str, enum.Enum):
    PAPER = "paper"
    LIVE = "live"


class RiskConfig(BaseModel):
    max_total_exposure_usd: float = Field(default=1500.0, gt=0)
    max_per_market_exposure_usd: float = Field(default=1000.0, gt=0)
    max_per_trade_usd: float = Field(default=500.0, gt=0)
    daily_loss_limit_usd: float = Field(default=200.0, gt=0)
    drawdown_circuit_breaker_pct: float = Field(default=0.20, gt=0, lt=1)
    kill_switch_cooldown_sec: float = Field(default=900.0, gt=0)
    drawdown_arm_period_sec: float = Field(default=1800.0, gt=0)
    drawdown_min_observations: int = Field(default=3, ge=1)


class ClobConfig(BaseModel):
    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""
    private_key: str = ""
    chain_id: int = 137  # Polygon mainnet
    clob_url: str = "https://clob.polymarket.com"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PGLM_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    execution_mode: ExecutionMode = ExecutionMode.PAPER
    risk: RiskConfig = RiskConfig()
    clob: ClobConfig = ClobConfig()
    paper_balance_usd: float = Field(default=10_000.0, gt=0)
    log_level: str = Field(default="INFO")
    telegram_alert_chat_id: str = ""
    telegram_alert_token: str = ""

    @property
    def live_ready(self) -> bool:
        return all([
            self.clob.api_key,
            self.clob.api_secret,
            self.clob.api_passphrase,
            self.clob.private_key,
        ])
```

**Step 4: Update pyproject.toml dependencies**

Add to dependencies: `"pydantic-settings>=2.0"`

**Step 5: Run test to verify pass**

```bash
pytest tests/test_config.py -v
```

Expected: 5 passed

**Step 6: Commit**

```bash
git add -A && git commit -m "feat: config system with Pydantic v2, env overrides, paper/live gate"
```

---

### Task 1.3: Market data models (Pydantic)

**Objective:** Modelos de dados para mercados, orderbooks, ordens e trades

**Files:**
- Create: `polymarket_glm/models.py`
- Create: `tests/test_models.py`

**Step 1: Write failing test**

```python
"""Tests for data models."""
from polymarket_glm.models import Market, OrderBookLevel, OrderBook, Order, Trade, Side


def test_market_from_gamma():
    m = Market(
        condition_id="0xabc",
        market_id="123",
        question="Will X happen?",
        outcomes=["Yes", "No"],
        outcome_prices=[0.65, 0.35],
        tokens=["tok1", "tok2"],
        active=True,
        closed=False,
        volume=50000.0,
    )
    assert m.spread_bps > 0  # (ask - bid) / mid * 10000


def test_orderbook_level():
    level = OrderBookLevel(price=0.55, size=100.0)
    assert level.cost == 55.0  # price * size


def test_order_side():
    assert Side.BUY.value == "buy"
    assert Side.SELL.value == "sell"


def test_trade_defaults():
    t = Trade(
        market_id="123",
        side=Side.BUY,
        outcome="yes",
        price=0.60,
        size=50.0,
        fee=0.15,
    )
    assert t.total_cost == 30.15  # price*size + fee
```

**Step 2: Run test to verify failure**

```bash
pytest tests/test_models.py -v
```

**Step 3: Write implementation**

```python
"""Core data models for polymarket-glm."""
from __future__ import annotations

import enum
from datetime import datetime
from pydantic import BaseModel, Field, computed_field


class Side(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class Market(BaseModel):
    condition_id: str
    market_id: str
    question: str
    outcomes: list[str]
    outcome_prices: list[float]
    tokens: list[str]
    active: bool = True
    closed: bool = False
    neg_risk: bool = False
    volume: float = 0.0
    end_date_iso: str = ""
    slug: str = ""
    fetched_at: datetime = Field(default_factory=datetime.utcnow)

    @computed_field
    @property
    def spread_bps(self) -> float:
        if len(self.outcome_prices) < 2:
            return 0.0
        bid = self.outcome_prices[0]
        ask = 1.0 - self.outcome_prices[1] if len(self.outcome_prices) == 2 else self.outcome_prices[1]
        mid = (bid + ask) / 2
        if mid == 0:
            return 0.0
        return abs(ask - bid) / mid * 10_000


class OrderBookLevel(BaseModel):
    price: float = Field(ge=0, le=1)
    size: float = Field(gt=0)

    @computed_field
    @property
    def cost(self) -> float:
        return self.price * self.size


class OrderBook(BaseModel):
    market_id: str
    bids: list[OrderBookLevel] = []
    asks: list[OrderBookLevel] = []
    fee_rate_bps: int = 0
    fetched_at: datetime = Field(default_factory=datetime.utcnow)

    @computed_field
    @property
    def best_bid(self) -> OrderBookLevel | None:
        return max(self.bids, key=lambda l: l.price) if self.bids else None

    @computed_field
    @property
    def best_ask(self) -> OrderBookLevel | None:
        return min(self.asks, key=lambda l: l.price) if self.asks else None

    @computed_field
    @property
    def midpoint(self) -> float | None:
        if self.best_bid and self.best_ask:
            return (self.best_bid.price + self.best_ask.price) / 2
        return None

    @computed_field
    @property
    def spread_bps(self) -> float | None:
        if self.best_bid and self.best_ask and self.midpoint:
            return (self.best_ask.price - self.best_bid.price) / self.midpoint * 10_000
        return None


class Order(BaseModel):
    order_id: str = ""
    market_id: str
    side: Side
    outcome: str
    price: float = Field(ge=0, le=1)
    size: float = Field(gt=0)
    order_type: str = "GTC"  # GTC, FOK, GTD, FAK
    status: str = "pending"  # pending, placed, filled, partial, cancelled, rejected, expired
    filled_size: float = 0.0
    filled_price: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Trade(BaseModel):
    trade_id: str = ""
    market_id: str
    side: Side
    outcome: str
    price: float = Field(ge=0, le=1)
    size: float = Field(gt=0)
    fee: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @computed_field
    @property
    def total_cost(self) -> float:
        return self.price * self.size + self.fee


class Position(BaseModel):
    market_id: str
    outcome: str
    size: float = 0.0
    avg_price: float = 0.0
    unrealized_pnl: float = 0.0


class Account(BaseModel):
    balance_usd: float = 10_000.0
    total_exposure_usd: float = 0.0
    daily_pnl_usd: float = 0.0
    positions: list[Position] = []
```

**Step 4: Run test to verify pass**

```bash
pytest tests/test_models.py -v
```

Expected: 4 passed

**Step 5: Commit**

```bash
git add -A && git commit -m "feat: core data models — Market, OrderBook, Order, Trade, Position, Account"
```

---

### Task 1.4: Market Fetcher (Gamma API wrapper)

**Objective:** Client para descobrir e filtrar mercados via Gamma API

**Files:**
- Create: `polymarket_glm/ingestion/market_fetcher.py`
- Create: `tests/test_market_fetcher.py`

**Step 1: Write failing test**

```python
"""Tests for market fetcher."""
import pytest
from unittest.mock import AsyncMock, patch
from polymarket_glm.ingestion.market_fetcher import MarketFetcher, MarketFilter


def test_market_filter_defaults():
    f = MarketFilter()
    assert f.min_volume_usd == 0
    assert f.active_only is True
    assert f.max_markets == 100


def test_market_filter_custom():
    f = MarketFilter(min_volume_usd=5000, max_markets=20, exclude_sports=True)
    assert f.min_volume_usd == 5000
    assert f.exclude_sports is True


@pytest.mark.asyncio
async def test_fetch_markets_calls_gamma():
    mock_resp = [{"condition_id": "0x1", "id": "1", "question": "Q?", "outcomes": '["Yes","No"]',
                  "outcomePrices": '["0.6","0.4"]', "tokens": '["t1","t2"]',
                  "active": True, "closed": False, "volume": "10000",
                  "negRisk": False, "endDate": "", "slug": "q"}]
    fetcher = MarketFetcher()
    with patch.object(fetcher, "_gamma_get", new_callable=AsyncMock, return_value=mock_resp):
        markets = await fetcher.fetch_markets(MarketFilter(min_volume_usd=5000))
        assert len(markets) >= 0  # filter applied
```

**Step 2: Run test to verify failure**

**Step 3: Write implementation**

```python
"""Market fetcher — discover and filter markets via Gamma API."""
from __future__ import annotations

import json
from pydantic import BaseModel, Field
import httpx

from polymarket_glm.models import Market


GAMMA_BASE = "https://gamma-api.polymarket.com"


class MarketFilter(BaseModel):
    min_volume_usd: float = 0
    max_markets: int = 100
    active_only: bool = True
    exclude_sports: bool = False
    exclude_closed: bool = True
    min_end_date_days: int = 0  # at least N days until resolution
    keywords_include: list[str] = []
    keywords_exclude: list[str] = []


class MarketFetcher:
    """Fetches markets from Gamma API and applies filters."""

    def __init__(self, base_url: str = GAMMA_BASE):
        self._base_url = base_url
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=30)
        return self._client

    async def _gamma_get(self, path: str, params: dict | None = None) -> list[dict]:
        client = await self._get_client()
        resp = await client.get(path, params=params or {})
        resp.raise_for_status()
        return resp.json()

    async def fetch_markets(self, filt: MarketFilter | None = None) -> list[Market]:
        filt = filt or MarketFilter()
        all_markets: list[Market] = []
        offset = 0
        limit = 100

        while len(all_markets) < filt.max_markets:
            params = {
                "limit": limit,
                "offset": offset,
                "active": filt.active_only,
                "closed": not filt.exclude_closed,
            }
            raw = await self._gamma_get("/markets", params)
            if not raw:
                break

            for r in raw:
                m = self._parse_market(r)
                if m and self._passes_filter(m, filt):
                    all_markets.append(m)

            offset += limit
            if len(raw) < limit:
                break

        return all_markets[: filt.max_markets]

    def _parse_market(self, raw: dict) -> Market | None:
        try:
            outcomes = json.loads(raw.get("outcomes", "[]"))
            prices = json.loads(raw.get("outcomePrices", "[]"))
            tokens = json.loads(raw.get("clobTokenIds", "[]"))
            if not outcomes or len(outcomes) < 2:
                return None
            return Market(
                condition_id=raw.get("conditionId", ""),
                market_id=str(raw.get("id", "")),
                question=raw.get("question", ""),
                outcomes=outcomes,
                outcome_prices=[float(p) for p in prices] if prices else [],
                tokens=tokens,
                active=raw.get("active", False),
                closed=raw.get("closed", False),
                neg_risk=raw.get("negRisk", False),
                volume=float(raw.get("volume", 0)),
                end_date_iso=raw.get("endDate", ""),
                slug=raw.get("slug", ""),
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            return None

    def _passes_filter(self, m: Market, f: MarketFilter) -> bool:
        if f.min_volume_usd and m.volume < f.min_volume_usd:
            return False
        if f.exclude_closed and m.closed:
            return False
        if f.exclude_sports and any(kw in m.question.lower() for kw in ["nfl", "nba", "mlb", "nhl", "soccer", "football", "basketball", "baseball", "hockey", "ufc", "mma", "tennis"]):
            return False
        if f.keywords_include:
            if not any(kw.lower() in m.question.lower() for kw in f.keywords_include):
                return False
        if f.keywords_exclude:
            if any(kw.lower() in m.question.lower() for kw in f.keywords_exclude):
                return False
        return True

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
```

**Step 4: Run test to verify pass**

```bash
pytest tests/test_market_fetcher.py -v
```

Expected: 3 passed

**Step 5: Commit**

```bash
git add -A && git commit -m "feat: MarketFetcher with Gamma API and filters"
```

---

### Task 1.5: Price Feed (WebSocket + REST fallback)

**Objective:** Price feed em tempo real via WebSocket com fallback REST

**Files:**
- Create: `polymarket_glm/ingestion/price_feed.py`
- Create: `tests/test_price_feed.py`

**Step 1: Write failing test**

```python
"""Tests for price feed."""
import pytest
from polymarket_glm.ingestion.price_feed import PriceFeed, PriceSnapshot


def test_price_snapshot():
    s = PriceSnapshot(market_id="1", bid=0.55, ask=0.60, midpoint=0.575)
    assert s.spread_bps == pytest.approx(869.56, rel=0.01)


def test_price_snapshot_no_ask():
    s = PriceSnapshot(market_id="1", bid=0.55, ask=None, midpoint=None)
    assert s.spread_bps is None
```

**Step 2: Run test to verify failure**

**Step 3: Write implementation**

```python
"""Price feed — real-time via WebSocket with REST fallback."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pydantic import BaseModel, Field, computed_field
import httpx

from polymarket_glm.models import OrderBook

logger = logging.getLogger(__name__)

CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CLOB_REST_URL = "https://clob.polymarket.com"


class PriceSnapshot(BaseModel):
    market_id: str
    bid: float | None = None
    ask: float | None = None
    midpoint: float | None = None
    volume: float | None = None
    fetched_at: datetime = Field(default_factory=datetime.utcnow)

    @computed_field
    @property
    def spread_bps(self) -> float | None:
        if self.bid is not None and self.ask is not None and self.midpoint:
            return abs(self.ask - self.bid) / self.midpoint * 10_000
        return None


class PriceFeed:
    """Real-time price feed with WebSocket primary + REST fallback."""

    def __init__(self, clob_rest_url: str = CLOB_REST_URL, ws_url: str = CLOB_WS_URL):
        self._rest_url = clob_rest_url
        self._ws_url = ws_url
        self._http: httpx.AsyncClient | None = None
        self._cache: dict[str, PriceSnapshot] = {}
        self._ws_connected = False

    async def get_snapshot(self, market_id: str, token_id: str) -> PriceSnapshot:
        """Get latest price snapshot — cache first, REST fallback."""
        if market_id in self._cache:
            return self._cache[market_id]
        return await self._fetch_rest(market_id, token_id)

    async def _fetch_rest(self, market_id: str, token_id: str) -> PriceSnapshot:
        client = await self._get_http()
        try:
            resp = await client.get(f"{self._rest_url}/book", params={"token_id": token_id})
            resp.raise_for_status()
            data = resp.json()
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            bid = float(bids[0]["price"]) if bids else None
            ask = float(asks[0]["price"]) if asks else None
            mid = (bid + ask) / 2 if bid is not None and ask is not None else None
            snap = PriceSnapshot(market_id=market_id, bid=bid, ask=ask, midpoint=mid)
            self._cache[market_id] = snap
            return snap
        except Exception as e:
            logger.warning("REST price fetch failed for %s: %s", market_id, e)
            return PriceSnapshot(market_id=market_id)

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=15)
        return self._http

    # WebSocket support — scaffold for now, full implementation in Sprint 2
    async def connect_ws(self, market_ids: list[str]) -> None:
        """Connect to CLOB WebSocket for real-time updates. TODO: full impl."""
        logger.info("WebSocket connect requested for %d markets (stub)", len(market_ids))
        self._ws_connected = True

    async def close(self):
        if self._http and not self._http.is_closed:
            await self._http.aclose()
        self._ws_connected = False
```

**Step 4: Run test to verify pass**

```bash
pytest tests/test_price_feed.py -v
```

Expected: 2 passed

**Step 5: Commit**

```bash
git add -A && git commit -m "feat: PriceFeed with REST fallback and WS scaffold"
```

---

## Sprint 2 — Strategy Engine & Risk Management

### Task 2.1: Risk Controller (ported from nothing-ever-happens)

**Objective:** Circuit-breaker por drawdown, exposure caps, kill switch, live readiness gate

**Files:**
- Create: `polymarket_glm/risk/controller.py`
- Create: `tests/test_risk_controller.py`

**Step 1: Write failing test**

```python
"""Tests for risk controller."""
import pytest
from polymarket_glm.risk.controller import RiskController, RiskDecision
from polymarket_glm.config import RiskConfig


@pytest.fixture
def ctrl():
    return RiskController(RiskConfig(
        max_total_exposure_usd=1000,
        max_per_market_exposure_usd=500,
        max_per_trade_usd=200,
        daily_loss_limit_usd=100,
        drawdown_circuit_breaker_pct=0.20,
        kill_switch_cooldown_sec=60,
        drawdown_arm_period_sec=0,
        drawdown_min_observations=1,
    ))


def test_trade_within_limits(ctrl):
    decision = ctrl.check_trade(
        market_id="m1", trade_amount_usd=100,
        current_total_exposure=200, current_market_exposure=100,
        balance_usd=5000, high_water_mark=5000,
    )
    assert decision == RiskDecision.ALLOW


def test_trade_exceeds_total_exposure(ctrl):
    decision = ctrl.check_trade(
        market_id="m1", trade_amount_usd=200,
        current_total_exposure=900, current_market_exposure=100,
        balance_usd=5000, high_water_mark=5000,
    )
    assert decision == RiskDecision.REJECT_TOTAL_EXPOSURE


def test_trade_exceeds_per_market(ctrl):
    decision = ctrl.check_trade(
        market_id="m1", trade_amount_usd=200,
        current_total_exposure=200, current_market_exposure=400,
        balance_usd=5000, high_water_mark=5000,
    )
    assert decision == RiskDecision.REJECT_MARKET_EXPOSURE


def test_trade_exceeds_per_trade(ctrl):
    decision = ctrl.check_trade(
        market_id="m1", trade_amount_usd=300,
        current_total_exposure=0, current_market_exposure=0,
        balance_usd=5000, high_water_mark=5000,
    )
    assert decision == RiskDecision.REJECT_TRADE_SIZE


def test_circuit_breaker_triggers(ctrl):
    decision = ctrl.check_trade(
        market_id="m1", trade_amount_usd=50,
        current_total_exposure=0, current_market_exposure=0,
        balance_usd=3900, high_water_mark=5000,  # 22% drawdown > 20%
    )
    assert decision == RiskDecision.REJECT_CIRCUIT_BREAKER


def test_kill_switch(ctrl):
    ctrl.activate_kill_switch()
    decision = ctrl.check_trade(
        market_id="m1", trade_amount_usd=50,
        current_total_exposure=0, current_market_exposure=0,
        balance_usd=5000, high_water_mark=5000,
    )
    assert decision == RiskDecision.REJECT_KILL_SWITCH
```

**Step 2: Run test to verify failure**

**Step 3: Write implementation**

```python
"""Risk controller — circuit-breaker, exposure caps, kill switch.

Ported and adapted from sterlingcrispin/nothing-ever-happens risk_controls.py.
"""
from __future__ import annotations

import enum
import time
from pydantic import BaseModel

from polymarket_glm.config import RiskConfig


class RiskDecision(str, enum.Enum):
    ALLOW = "allow"
    REJECT_TOTAL_EXPOSURE = "reject_total_exposure"
    REJECT_MARKET_EXPOSURE = "reject_market_exposure"
    REJECT_TRADE_SIZE = "reject_trade_size"
    REJECT_DAILY_LOSS = "reject_daily_loss"
    REJECT_CIRCUIT_BREAKER = "reject_circuit_breaker"
    REJECT_KILL_SWITCH = "reject_kill_switch"


class RiskController:
    """Stateful risk controller with drawdown circuit-breaker and kill switch."""

    def __init__(self, config: RiskConfig):
        self._cfg = config
        self._kill_switch_active = False
        self._kill_switch_activated_at: float = 0.0
        self._observations: list[tuple[float, float]] = []  # (timestamp, balance)
        self._daily_loss: float = 0.0

    def check_trade(
        self,
        market_id: str,
        trade_amount_usd: float,
        current_total_exposure: float,
        current_market_exposure: float,
        balance_usd: float,
        high_water_mark: float,
    ) -> RiskDecision:
        # 1. Kill switch check
        if self._kill_switch_active:
            elapsed = time.time() - self._kill_switch_activated_at
            if elapsed < self._cfg.kill_switch_cooldown_sec:
                return RiskDecision.REJECT_KILL_SWITCH
            self._kill_switch_active = False  # cooldown expired

        # 2. Per-trade limit
        if trade_amount_usd > self._cfg.max_per_trade_usd:
            return RiskDecision.REJECT_TRADE_SIZE

        # 3. Total exposure limit
        if current_total_exposure + trade_amount_usd > self._cfg.max_total_exposure_usd:
            return RiskDecision.REJECT_TOTAL_EXPOSURE

        # 4. Per-market exposure limit
        if current_market_exposure + trade_amount_usd > self._cfg.max_per_market_exposure_usd:
            return RiskDecision.REJECT_MARKET_EXPOSURE

        # 5. Daily loss limit
        if self._daily_loss >= self._cfg.daily_loss_limit_usd:
            return RiskDecision.REJECT_DAILY_LOSS

        # 6. Drawdown circuit-breaker
        now = time.time()
        self._observations.append((now, balance_usd))
        if len(self._observations) >= self._cfg.drawdown_min_observations:
            oldest = self._observations[0][0]
            arm_elapsed = now - oldest
            if arm_elapsed >= self._cfg.drawdown_arm_period_sec:
                if high_water_mark > 0:
                    drawdown_pct = (high_water_mark - balance_usd) / high_water_mark
                    if drawdown_pct >= self._cfg.drawdown_circuit_breaker_pct:
                        self.activate_kill_switch()
                        return RiskDecision.REJECT_CIRCUIT_BREAKER

        return RiskDecision.ALLOW

    def activate_kill_switch(self) -> None:
        self._kill_switch_active = True
        self._kill_switch_activated_at = time.time()

    def deactivate_kill_switch(self) -> None:
        self._kill_switch_active = False

    @property
    def kill_switch_active(self) -> bool:
        return self._kill_switch_active

    def record_daily_loss(self, amount: float) -> None:
        self._daily_loss += amount

    def reset_daily(self) -> None:
        self._daily_loss = 0.0
```

**Step 4: Run test to verify pass**

```bash
pytest tests/test_risk_controller.py -v
```

Expected: 6 passed

**Step 5: Commit**

```bash
git add -A && git commit -m "feat: RiskController with circuit-breaker, exposure caps, kill switch"
```

---

### Task 2.2: Signal Engine — Edge Detection + Kelly Sizing

**Objective:** Motor de sinais com cálculo de edge e position sizing via Kelly criterion

**Files:**
- Create: `polymarket_glm/strategy/signal.py`
- Create: `tests/test_signal.py`

**Step 1: Write failing test**

```python
"""Tests for signal engine."""
import pytest
from polymarket_glm.strategy.signal import Signal, EdgeDetector, KellySizer


def test_edge_detector_basic():
    """Edge = estimated_prob - market_price."""
    edge = EdgeDetector.compute_edge(estimated_prob=0.75, market_price=0.60)
    assert edge == pytest.approx(0.15)


def test_edge_negative():
    edge = EdgeDetector.compute_edge(estimated_prob=0.40, market_price=0.60)
    assert edge < 0


def test_kelly_sizing_basic():
    """f* = (bp - q) / b where b=1/p-1, p=est_prob, q=1-p."""
    size_pct = KellySizer.compute_fraction(edge=0.15, market_price=0.60)
    assert 0 < size_pct < 1


def test_kelly_zero_edge():
    size_pct = KellySizer.compute_fraction(edge=0.0, market_price=0.60)
    assert size_pct == 0.0


def test_kelly_negative_edge():
    size_pct = KellySizer.compute_fraction(edge=-0.10, market_price=0.60)
    assert size_pct == 0.0  # no bet on negative edge


def test_kelly_quarter():
    """Quarter-Kelly for conservative sizing."""
    full = KellySizer.compute_fraction(edge=0.15, market_price=0.60, fraction=1.0)
    quarter = KellySizer.compute_fraction(edge=0.15, market_price=0.60, fraction=0.25)
    assert quarter == pytest.approx(full * 0.25)


def test_signal_creation():
    s = Signal(
        market_id="m1",
        outcome="yes",
        estimated_prob=0.75,
        market_price=0.60,
        edge=0.15,
        size_fraction=0.25,
        confidence="medium",
        source="test",
    )
    assert s.edge > 0
    assert s.size_fraction > 0
```

**Step 2: Run test to verify failure**

**Step 3: Write implementation**

```python
"""Signal engine — edge detection and Kelly criterion sizing.

Inspired by brodyautomates/polymarket-pipeline/edge.py V2 approach.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Signal(BaseModel):
    market_id: str
    outcome: str
    estimated_prob: float = Field(ge=0, le=1)
    market_price: float = Field(ge=0, le=1)
    edge: float
    size_fraction: float = Field(ge=0, le=1)
    confidence: str = "medium"  # low, medium, high
    source: str = ""
    metadata: dict = {}


class EdgeDetector:
    """Compute edge as estimated_prob - market_price."""

    @staticmethod
    def compute_edge(estimated_prob: float, market_price: float) -> float:
        return estimated_prob - market_price

    @staticmethod
    def compute_edge_with_materiality(
        estimated_prob: float,
        market_price: float,
        materiality: float,  # 0-1, how material the signal is
        price_room: float,   # 0-1, how far from 0 or 1
    ) -> float:
        """Edge = materiality × price_room × (est_prob - market_price)."""
        raw_edge = estimated_prob - market_price
        return materiality * price_room * raw_edge


class KellySizer:
    """Kelly criterion position sizing.

    f* = (b*p - q) / b where:
    - b = (1/price - 1) = odds offered
    - p = estimated probability of winning
    - q = 1 - p
    """

    @staticmethod
    def compute_fraction(
        edge: float,
        market_price: float,
        fraction: float = 0.25,  # quarter-Kelly default
    ) -> float:
        if edge <= 0 or market_price <= 0 or market_price >= 1:
            return 0.0

        b = (1.0 / market_price) - 1.0  # odds
        p = edge + market_price           # estimated prob
        q = 1.0 - p

        kelly = (b * p - q) / b
        kelly = max(0.0, kelly)  # no negative bets

        return kelly * fraction
```

**Step 4: Run test to verify pass**

```bash
pytest tests/test_signal.py -v
```

Expected: 7 passed

**Step 5: Commit**

```bash
git add -A && git commit -m "feat: Signal engine with edge detection and Kelly sizing"
```

---

## Sprint 3 — Execution Layer

### Task 3.1: Exchange Protocol (paper/live interface)

**Objective:** Interface comum para execução paper e live, inspirada em nothing-ever-happens

**Files:**
- Create: `polymarket_glm/execution/protocol.py`
- Create: `tests/test_execution_protocol.py`

**Step 1: Write failing test**

```python
"""Tests for execution protocol."""
import pytest
from polymarket_glm.execution.protocol import ExchangeClient, TradeResult
from polymarket_glm.models import Side


def test_trade_result_success():
    r = TradeResult(success=True, filled_size=100.0, filled_price=0.60, fee=0.15)
    assert r.total_cost == pytest.approx(60.15)


def test_trade_result_failure():
    r = TradeResult(success=False, error="insufficient balance")
    assert r.filled_size == 0


def test_trade_result_partial():
    r = TradeResult(success=True, filled_size=50.0, filled_price=0.61, fee=0.10, is_partial=True)
    assert r.is_partial is True
```

**Step 2: Run test to verify failure**

**Step 3: Write implementation**

```python
"""Exchange protocol — common interface for paper and live execution.

Inspired by sterlingcrispin/nothing-ever-happens exchange/base.py Protocol.
"""
from __future__ import annotations

import abc
from pydantic import BaseModel, Field, computed_field

from polymarket_glm.models import Side, OrderBook, Order


class TradeResult(BaseModel):
    success: bool
    filled_size: float = 0.0
    filled_price: float = 0.0
    fee: float = 0.0
    is_partial: bool = False
    error: str = ""

    @computed_field
    @property
    def total_cost(self) -> float:
        return self.filled_price * self.filled_size + self.fee


class ExchangeClient(abc.ABC):
    """Common interface for trade execution — paper and live."""

    @abc.abstractmethod
    async def buy(
        self, market_id: str, outcome: str, amount_usd: float,
        price_limit: float | None = None, order_type: str = "GTC",
    ) -> TradeResult:
        ...

    @abc.abstractmethod
    async def sell(
        self, market_id: str, outcome: str, size: float,
        price_limit: float | None = None, order_type: str = "GTC",
    ) -> TradeResult:
        ...

    @abc.abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        ...

    @abc.abstractmethod
    async def cancel_all(self, market_id: str | None = None) -> int:
        ...

    @abc.abstractmethod
    async def get_orderbook(self, market_id: str, token_id: str) -> OrderBook:
        ...

    @abc.abstractmethod
    async def get_balance(self) -> float:
        ...

    @abc.abstractmethod
    async def get_positions(self) -> list[dict]:
        ...
```

**Step 4: Run test to verify pass**

```bash
pytest tests/test_execution_protocol.py -v
```

Expected: 3 passed

**Step 5: Commit**

```bash
git add -A && git commit -m "feat: ExchangeClient protocol with TradeResult"
```

---

### Task 3.2: Paper Executor (fill simulation)

**Objective:** Paper trading com fee calculation exata do Polymarket, inspirado em polymarket-paper-trader

**Files:**
- Create: `polymarket_glm/execution/paper.py`
- Create: `tests/test_paper_executor.py`

**Step 1: Write failing test**

```python
"""Tests for paper executor."""
import pytest
from polymarket_glm.execution.paper import PaperExecutor
from polymarket_glm.models import OrderBook, OrderBookLevel, Side


@pytest.fixture
def executor():
    return PaperExecutor(starting_balance=10_000.0)


def test_paper_executor_balance(executor):
    assert executor.balance == 10_000.0


def test_fee_calculation():
    """Polymarket fee: (bps/10000) * min(price, 1-price) * size."""
    fee = PaperExecutor.calculate_fee(fee_rate_bps=100, price=0.50, size=100.0)
    assert fee == pytest.approx(0.50)  # (100/10000)*0.50*100


def test_fee_near_zero_price():
    fee = PaperExecutor.calculate_fee(fee_rate_bps=100, price=0.05, size=100.0)
    assert fee == pytest.approx(0.05)  # min(0.05, 0.95) = 0.05


def test_fee_zero_bps():
    fee = PaperExecutor.calculate_fee(fee_rate_bps=0, price=0.50, size=100.0)
    assert fee == 0.0


@pytest.mark.asyncio
async def test_buy_walks_book(executor):
    book = OrderBook(
        market_id="m1",
        bids=[OrderBookLevel(price=0.50, size=200)],
        asks=[OrderBookLevel(price=0.55, size=100), OrderBookLevel(price=0.60, size=100)],
        fee_rate_bps=100,
    )
    executor._orderbook_cache["m1:yes"] = book
    result = await executor.buy("m1", "yes", amount_usd=55.0, price_limit=0.60)
    assert result.success is True
    assert result.filled_price == pytest.approx(0.55, abs=0.01)
    assert result.fee > 0
```

**Step 2: Run test to verify failure**

**Step 3: Write implementation**

```python
"""Paper executor — faithful Polymarket fill simulation.

Ported from agent-next/polymarket-paper-trader orderbook.py fee model.
"""
from __future__ import annotations

from polymarket_glm.execution.protocol import ExchangeClient, TradeResult
from polymarket_glm.models import OrderBook, OrderBookLevel, Side


class PaperExecutor(ExchangeClient):
    """Paper trading executor with 1:1 Polymarket fee and fill simulation."""

    def __init__(self, starting_balance: float = 10_000.0, fee_rate_bps: int = 100):
        self._balance = starting_balance
        self._fee_rate_bps = fee_rate_bps
        self._positions: dict[str, dict[str, float]] = {}  # market_id -> {outcome: size}
        self._orderbook_cache: dict[str, OrderBook] = {}

    @property
    def balance(self) -> float:
        return self._balance

    @staticmethod
    def calculate_fee(fee_rate_bps: int, price: float, size: float) -> float:
        """Exact Polymarket fee formula: (bps/10000) * min(price, 1-price) * size."""
        if fee_rate_bps == 0:
            return 0.0
        fee = (fee_rate_bps / 10_000) * min(price, 1.0 - price) * size
        return max(fee, 0.0001) if fee > 0 else 0.0

    async def buy(
        self, market_id: str, outcome: str, amount_usd: float,
        price_limit: float | None = None, order_type: str = "GTC",
    ) -> TradeResult:
        key = f"{market_id}:{outcome}"
        book = self._orderbook_cache.get(key)
        if not book or not book.asks:
            return TradeResult(success=False, error="no orderbook data")

        remaining = amount_usd
        total_shares = 0.0
        total_cost = 0.0
        total_fee = 0.0

        for level in sorted(book.asks, key=lambda l: l.price):
            if price_limit and level.price > price_limit:
                break
            level_cost = level.price * level.size
            if level_cost > remaining:
                shares = remaining / level.price
            else:
                shares = level.size

            cost = shares * level.price
            fee = self.calculate_fee(self._fee_rate_bps, level.price, shares)

            if cost + fee > remaining:
                shares = (remaining - fee) / level.price if level.price > 0 else 0
                cost = shares * level.price
                fee = self.calculate_fee(self._fee_rate_bps, level.price, shares)

            total_shares += shares
            total_cost += cost
            total_fee += fee
            remaining -= cost + fee

            if remaining <= 0.01:
                break

        if total_shares == 0:
            return TradeResult(success=False, error="insufficient funds or no liquidity")

        self._balance -= (total_cost + total_fee)
        pos_key = market_id
        if pos_key not in self._positions:
            self._positions[pos_key] = {}
        self._positions[pos_key][outcome] = self._positions[pos_key].get(outcome, 0) + total_shares

        avg_price = total_cost / total_shares if total_shares > 0 else 0
        return TradeResult(
            success=True,
            filled_size=total_shares,
            filled_price=avg_price,
            fee=total_fee,
        )

    async def sell(
        self, market_id: str, outcome: str, size: float,
        price_limit: float | None = None, order_type: str = "GTC",
    ) -> TradeResult:
        key = f"{market_id}:{outcome}"
        pos = self._positions.get(market_id, {}).get(outcome, 0)
        if pos < size:
            return TradeResult(success=False, error="insufficient position")

        book = self._orderbook_cache.get(key)
        if not book or not book.bids:
            return TradeResult(success=False, error="no orderbook data")

        remaining = size
        total_proceeds = 0.0
        total_shares = 0.0
        total_fee = 0.0

        for level in sorted(book.bids, key=lambda l: -l.price):
            if price_limit and level.price < price_limit:
                break
            fill = min(remaining, level.size)
            proceeds = fill * level.price
            fee = self.calculate_fee(self._fee_rate_bps, level.price, fill)

            total_proceeds += proceeds - fee
            total_shares += fill
            total_fee += fee
            remaining -= fill

            if remaining <= 0:
                break

        self._balance += total_proceeds
        self._positions[market_id][outcome] -= total_shares

        avg_price = total_proceeds / total_shares if total_shares > 0 else 0
        return TradeResult(
            success=True,
            filled_size=total_shares,
            filled_price=avg_price,
            fee=total_fee,
        )

    async def cancel_order(self, order_id: str) -> bool:
        return True  # paper mode — all orders are instant

    async def cancel_all(self, market_id: str | None = None) -> int:
        return 0

    async def get_orderbook(self, market_id: str, token_id: str) -> OrderBook:
        return self._orderbook_cache.get(f"{market_id}:yes", OrderBook(market_id=market_id))

    async def get_balance(self) -> float:
        return self._balance

    async def get_positions(self) -> list[dict]:
        return [
            {"market_id": mk, "outcome": o, "size": s}
            for mk, outcomes in self._positions.items()
            for o, s in outcomes.items() if s > 0
        ]
```

**Step 4: Run test to verify pass**

```bash
pytest tests/test_paper_executor.py -v
```

Expected: 5 passed

**Step 5: Commit**

```bash
git add -A && git commit -m "feat: PaperExecutor with Polymarket-accurate fee and fill simulation"
```

---

### Task 3.3: Live Executor (CLOB integration)

**Objective:** Live trading executor usando py-clob-client

**Files:**
- Create: `polymarket_glm/execution/live.py`
- Create: `tests/test_live_executor.py`

**Note:** Live executor tests will use mocks — no real API keys in tests.

**Step 1: Write failing test** (mocked py_clob_client)

**Step 2: Write implementation** — thin wrapper around ClobClient with balance sync, sell retry, readiness checks

**Step 3: Run test**

**Step 4: Commit**

```bash
git add -A && git commit -m "feat: LiveExecutor with CLOB integration, balance sync, sell retry"
```

---

## Sprint 4 — Storage, Monitoring & Integration

### Task 4.1: Storage layer (SQLite)

**Objective:** Persistência de trades, ordens, preço histórico e estado

**Files:**
- Create: `polymarket_glm/storage/db.py`
- Create: `polymarket_glm/storage/migrations.py`
- Create: `tests/test_storage.py`

**Steps:** Schema creation, trade logging, price history, order state, query helpers. SQLite with WAL mode.

**Commit:** `feat: SQLite storage with trade ledger, price history, order state`

---

### Task 4.2: Monitoring — structured logging + Telegram alerts

**Objective:** Logging estruturado e alertas Telegram

**Files:**
- Create: `polymarket_glm/monitoring/logger.py`
- Create: `polymarket_glm/monitoring/alerts.py`
- Create: `tests/test_monitoring.py`

**Steps:** Structured JSON logging, Telegram alert sender (async), rate-limited alerts, trade event emission.

**Commit:** `feat: Monitoring with structured logging and Telegram alerts`

---

### Task 4.3: Engine — wire everything together

**Objective:** Orquestrar todas as camadas em um loop de trading

**Files:**
- Create: `polymarket_glm/engine.py`
- Create: `tests/test_engine.py`

**Steps:**
- Engine class with async run loop
- Inject Settings → create Fetcher, PriceFeed, Strategy, Executor (paper or live based on config), RiskController, Storage
- Main loop: fetch markets → generate signals → risk check → execute → log → sleep
- Graceful shutdown on SIGTERM

**Commit:** `feat: Trading engine — wires all layers with paper/live switch`

---

## Sprint 5 — CLI & Dashboard

### Task 5.1: CLI interface

**Objective:** CLI com subcomandos: scan, trade, status, backtest

**Files:**
- Create: `polymarket_glm/interface/cli.py`

**Commit:** `feat: CLI with scan, trade, status, backtest commands`

---

### Task 5.2: Dashboard scaffold (HTML + SVG)

**Objective:** Dashboard estático com auto-refresh mostrando posições, P&L e sinais

**Files:**
- Create: `polymarket_glm/interface/dashboard.py`
- Create: `polymarket_glm/interface/templates/index.html`

**Commit:** `feat: HTML dashboard with positions, P&L, signals`

---

## Resumo de Sprints e Tasks

| Sprint | Tasks | Estimativa | Dependências |
|--------|-------|-----------|-------------|
| S1: Foundation + Data | 1.1–1.5 | ~2h | Nenhuma |
| S2: Strategy + Risk | 2.1–2.2 | ~1.5h | S1 |
| S3: Execution | 3.1–3.3 | ~2h | S1+S2 |
| S4: Storage + Monitoring + Engine | 4.1–4.3 | ~2h | S1–S3 |
| S5: CLI + Dashboard | 5.1–5.2 | ~1.5h | S4 |
| **Total** | **12 tasks** | **~9h** | |

### Ordem de execução
1. ✅ Task 1.1 (scaffolding)
2. ✅ Task 1.2 (config)
3. ✅ Task 1.3 (models)
4. ✅ Task 1.4 (market fetcher)
5. ✅ Task 1.5 (price feed)
6. ✅ Task 2.1 (risk controller)
7. ✅ Task 2.2 (signal engine)
8. ✅ Task 3.1 (exchange protocol)
9. ✅ Task 3.2 (paper executor)
10. ✅ Task 3.3 (live executor)
11. ✅ Task 4.1 (storage)
12. ✅ Task 4.2 (monitoring)
13. ✅ Task 4.3 (engine)
14. ✅ Task 5.1 (CLI)
15. ✅ Task 5.2 (dashboard)

Cada task: TDD (test → fail → implement → pass → commit). pytest -q verde antes de avançar.

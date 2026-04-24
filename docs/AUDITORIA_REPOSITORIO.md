# Auditoria do Repositório polymarket-glm

> Data: 2026-04-24 | Branch: audit/repository-file-by-file | Python 3.11.15 | 534 testes passando

## 1. Resumo executivo

O polymarket-glm é um framework de trading para Polymarket com 10 sprints implementados (Sprint 1–12 no código, embora a documentação marque Sprint 11–12 como pendente). O projeto possui **534 testes passando**, CLI funcional (`pglm`), e paper trading operacional via systemd.

**Pontos fortes:**
- Arquitetura modular bem separada (7 camadas)
- Risk controller com kill switch, circuit breaker e daily loss limit
- Cobertura de testes abrangente (~534 testes)
- CLI funcional com comandos status/scan/trade/risk/killswitch
- Paper executor funcional e testado
- Multi-provider LLM router com fallback e rate limiting

**Problemas críticos encontrados:**
- 🔴 BUG: Trailing stop nunca dispara (high_water_mark nunca atualizado)
- 🔴 BUG: Ensemble paraphrase prompts nunca usados (todos os templates produzem resultados idênticos)
- 🔴 DUPLICAÇÃO: `EnsembleEstimator` existe em dois arquivos com mesma assinatura
- 🔴 DUPLICAÇÃO: `LLMRouterConfig` existe em config.py e llm_router.py com estruturas diferentes
- 🔴 SYNC/ASYNC: `ProbabilityEstimator` Protocol é sync, mas LLM estimators são async
- 🔴 ORQUESTRAÇÃO DUPLA: `TradingEngine` e `TradingLoop` fazem a mesma coisa
- 🟠 SEGURANÇA: Telegram bot sem autenticação, web dashboard sem auth/HTTPS
- 🟠 DOCS: Env var `PGLM_MODE` nos docs vs `PGLM_EXECUTION_MODE` no código
- 🟠 DOCS: Defaults de risco nos docs 2–10x menos conservadores que o código real
- 🟡 LIVE TRADING: Exchange methods são stubs — LiveExecutor falha em runtime

---

## 2. Como o projeto está organizado

```
polymarket_glm/
├── __init__.py          # Versão 0.1.0
├── config.py            # Settings central (Pydantic v2)
├── models.py            # Data models (Market, Order, Trade, Position, Account)
├── py.typed             # PEP 561 marker
├── backtest/            # Backtesting engine
│   └── engine.py
├── engine/              # Orchestration (TradingEngine + TradingLoop)
│   ├── __init__.py      # TradingEngine (320+ linhas, God class)
│   └── trading_loop.py  # TradingLoop (duplica TradingEngine)
├── execution/           # Execution layer
│   ├── barriers.py      # Triple barrier exits (stop-loss, take-profit, trailing)
│   ├── exchange.py      # Polymarket CLOB client (STUBS)
│   ├── live_executor.py # Live trading executor (DEPENDE DE STUBS)
│   ├── paper_executor.py# Paper trading executor (FUNCIONAL)
│   ├── portfolio_tracker.py # Portfolio P&L + drawdown
│   ├── position_executor.py # Position lifecycle com barriers
│   ├── position_manager.py  # Simple position state (REDUNDANTE)
│   ├── settlement_tracker.py# Market resolution detection
│   └── signal_controller.py # Signal → executor dispatcher
├── ingestion/           # Data ingestion
│   ├── market_fetcher.py# Gamma API wrapper
│   └── price_feed.py    # REST + WebSocket price feed
├── interface/           # User interfaces
│   ├── cli.py           # CLI (pglm)
│   └── dashboard.py     # Terminal dashboard (STUB)
├── monitoring/          # Observability
│   ├── alerts.py        # Telegram alert system
│   └── daily_report.py  # Daily report generator
├── ops/                 # Operations
│   ├── health.py        # Heartbeat + stale detection
│   ├── service.py       # systemd unit generator
│   ├── telegram_bot.py  # Interactive Telegram bot
│   └── web_dashboard.py # HTTP dashboard (stdlib http.server)
├── risk/                # Risk management
│   └── controller.py    # Risk controller + kill switch + circuit breaker
├── storage/             # Persistence
│   └── database.py      # SQLite (raw SQL, no ORM/migrations)
└── strategy/            # Strategy layer
    ├── calibration.py       # Brier score + calibration tracker
    ├── context_fetcher.py   # NewsAPI + Tavily integration
    ├── ensemble.py          # Multi-template paraphrase ensemble (BUG)
    ├── ensemble_estimator.py# Weighted estimator combo (DUPLICADO)
    ├── estimator.py         # Protocol + HeuristicEstimator
    ├── llm_estimator.py     # Single-provider LLM (SUPERSEDED)
    ├── llm_router.py        # Multi-provider LLM router (ATIVO)
    └── signal_engine.py     # Edge calc + Kelly sizing
```

---

## 3. Árvore de arquivos analisada

```
.env                    # ⚠️ Contém secrets reais (protegido pelo .gitignore)
.env.example            # Template de configuração (DESATUALIZADO)
.gitignore              # Com duplicatas e entradas faltantes
NEXT_STEPS.md           # Roadmap (DESATUALIZADO — marca sprint existentes como faltantes)
PLAN.md                 # Plano TDD detalhado (1609 linhas, histórico)
README.md               # Visão geral + arquitetura (com inconsistências)
docs/
  CLOB_API_SETUP.md     # Guia de setup CLOB (contém claim incorreta sobre .gitignore)
  GO_LIVE_RUNBOOK.md    # Runbook de operação (risco defaults incorretos)
polymarket_glm.db       # Banco SQLite (4 tabelas + 3 índices)
polymarket_glm/
  __init__.py           # v0.1.0
  config.py             # Settings central
  models.py             # Data models
  py.typed              # PEP 561
  backtest/engine.py    # Backtesting
  engine/__init__.py    # TradingEngine
  engine/trading_loop.py# TradingLoop
  execution/barriers.py # Triple barrier
  execution/exchange.py # CLOB client (STUBS)
  execution/live_executor.py  # Live executor
  execution/paper_executor.py # Paper executor
  execution/portfolio_tracker.py # Portfolio tracker
  execution/position_executor.py  # Position executor
  execution/position_manager.py   # Position manager
  execution/settlement_tracker.py # Settlement tracker
  execution/signal_controller.py  # Signal controller
  ingestion/market_fetcher.py # Market fetcher
  ingestion/price_feed.py     # Price feed
  interface/cli.py       # CLI
  interface/dashboard.py # Dashboard (STUB)
  monitoring/alerts.py   # Alertas Telegram
  monitoring/daily_report.py  # Relatório diário
  ops/health.py          # Health check
  ops/service.py         # systemd generator
  ops/telegram_bot.py    # Telegram bot interativo
  ops/web_dashboard.py   # Web dashboard (stdlib)
  risk/controller.py     # Risk controller
  storage/database.py    # SQLite storage
  strategy/calibration.py     # Calibration
  strategy/context_fetcher.py # News context
  strategy/ensemble.py        # Paraphrase ensemble (BUG)
  strategy/ensemble_estimator.py # Weighted combo (DUPLICADO)
  strategy/estimator.py       # Protocol + Heuristic
  strategy/llm_estimator.py   # Single LLM (SUPERSEDED)
  strategy/llm_router.py      # Multi-provider router
  strategy/signal_engine.py   # Signal generation
pyproject.toml          # Config do projeto
scripts/
  polymarket-simulation.service  # systemd unit
  run_bot.py              # Entry point do bot
  run_simulation.py       # Simulation runner
tests/                   # 36 arquivos, 534 testes
```

---

## 4. Resultado de instalação e testes

| Item | Resultado |
|------|-----------|
| Python | 3.11.15 ✅ |
| venv criado | ✅ |
| `pip install -e ".[dev]"` | ✅ Sucesso |
| `pytest -q` | **534 passed** em 9.01s ✅ |
| `pglm --help` | ✅ Funcional |
| `pglm status` | ✅ Retorna status paper |
| `pglm scan` | ✅ Busca 20 mercados da Gamma API |
| `pglm risk` | ✅ Retorna risk status |
| `pglm killswitch` | ✅ Ativa kill switch |
| `pglm trade --help` | ✅ Mostra opções de trade |

**Nota:** `pglm trade` não foi testado com execução real pois requer mercado específico. `pglm scan` faz chamada real à Gamma API (funciona).

---

## 5. Mapa dos módulos

| Módulo | Arquivos | Status | Função |
|--------|----------|--------|--------|
| `config` | config.py | ✅ Completo | Settings central com Pydantic v2 |
| `models` | models.py | ✅ Completo | Data models (Market, Order, Trade, Position, Account) |
| `ingestion` | market_fetcher.py, price_feed.py | ✅ Completo | Gamma API + REST/WS price feed |
| `strategy` | estimator.py, llm_estimator.py, llm_router.py, ensemble.py, ensemble_estimator.py, calibration.py, context_fetcher.py, signal_engine.py | 🟡 Parcial | Router funcional, mas bugs no ensemble e duplicações |
| `execution` | paper_executor.py, live_executor.py, exchange.py, barriers.py, position_executor.py, position_manager.py, portfolio_tracker.py, settlement_tracker.py, signal_controller.py | 🟡 Parcial | Paper OK, live = stubs, bugs em trailing stop |
| `risk` | controller.py | ✅ Completo | Kill switch + circuit breaker + daily loss |
| `monitoring` | alerts.py, daily_report.py | ✅ Completo | Telegram alerts + daily report |
| `ops` | health.py, service.py, telegram_bot.py, web_dashboard.py | 🟡 Parcial | Funcional mas sem auth |
| `storage` | database.py | 🟡 Parcial | SQLite sem migrations/WAL |
| `interface` | cli.py, dashboard.py | 🟡 Parcial | CLI OK, dashboard é stub |
| `engine` | \_\_init\_\_.py, trading_loop.py | 🟠 Duplicado | Duas classes de orquestração |
| `backtest` | engine.py | 🟡 Parcial | Sync-only, sem slippage |

---

## 6. Auditoria arquivo por arquivo

### polymarket_glm/config.py
- **Função**: Settings central com Pydantic v2, env overrides, paper/live gate
- **Classes**: `ExecutionMode`, `RiskConfig`, `ClobConfig`, `LLMRouterConfig`, `Settings`
- **Deps internas**: `strategy.context_fetcher` (NewsFetcherConfig, WebSearcherConfig)
- **Deps externas**: `pydantic`, `pydantic_settings`
- **Status**: 🟡 Parcial
- **Testes**: ✅ test_config.py (6 testes)
- **Precisa refatoração**: Sim — `LLMRouterConfig` duplica o de `llm_router.py` com estrutura diferente
- **Riscos**: (1) Name collision LLMRouterConfig, (2) private_key como env var, (3) ClobConfig merge validator com side effect

### polymarket_glm/models.py
- **Função**: Core data models
- **Classes**: `Side`, `Market`, `OrderBookLevel`, `OrderBook`, `Order`, `Trade`, `Position`, `Account`
- **Deps internas**: Nenhuma
- **Deps externas**: `pydantic`
- **Status**: ✅ Completo
- **Testes**: ✅ test_models.py (7 testes)
- **Riscos**: (1) `datetime.utcnow` deprecado, (2) `Position` sem market_question/condition_id, (3) `spread_bps` assume mercado binário

### polymarket_glm/backtest/engine.py
- **Função**: Replay histórico, PnL, win rate, drawdown, Sharpe
- **Classes**: `BacktestConfig`, `BacktestTrade`, `BacktestResult`, `BacktestEngine`
- **Deps internas**: `strategy.estimator`, `strategy.signal_engine`
- **Deps externas**: `pydantic`, `math`
- **Status**: 🟡 Parcial
- **Testes**: ✅ test_backtest.py (10 testes)
- **Riscos**: (1) Sync-only mas estimators são async, (2) SELL PnL pode explodir com entry_price baixo, (3) Sem slippage model, (4) capital pode ir negativo

### polymarket_glm/engine/__init__.py
- **Função**: TradingEngine — orquestra todos os componentes
- **Classes**: `TradingEngine`, `EngineConfig`, `EngineSnapshot`
- **Deps internas**: config, models, strategy.*, execution.*, risk.*, ingestion.*, monitoring.*
- **Deps externas**: `pydantic`, `asyncio`, `logging`
- **Status**: 🟠 Precisa refatoração
- **Testes**: ✅ test_engine.py (4 testes)
- **Riscos**: (1) 320+ linhas em __init__.py — God class, (2) Sem graceful shutdown signal, (3) _iteration() faz 10+ coisas

### polymarket_glm/engine/trading_loop.py
- **Função**: TradingLoop — outra orquestração (DUPLICA engine/__init__.py)
- **Classes**: `TradingLoop`, `TradingLoopConfig`
- **Deps internas**: Todas as de TradingEngine + llm_router, ensemble, context_fetcher
- **Deps externas**: `pydantic`, `asyncio`, `logging`
- **Status**: 🟠 Duplicado
- **Testes**: ✅ test_trading_loop.py (8 testes)
- **Riscos**: (1) Duplica TradingEngine, (2) Mudanças devem ser sincronizadas em 2 arquivos, (3) Sem idempotência guard

### polymarket_glm/execution/barriers.py
- **Função**: Triple barrier exit — stop-loss, take-profit, time limit, trailing stop
- **Classes**: `TrailingStop`, `TripleBarrierConfig`, `TripleBarrier`, `BarrierCheckResult`, `PositionBarrierState`
- **Deps internas**: `models`
- **Deps externas**: `pydantic`, `dataclasses`
- **Status**: 🔴 BUG
- **Testes**: ✅ test_barriers.py (15 testes)
- **Riscos**: (1) **BUG CRÍTICO**: Trailing stop nunca dispara — `high_water_mark` fica em 0 e nunca é atualizado em `check()`, (2) Time limit usa iteration count não real time, (3) Sem suporte a short positions

### polymarket_glm/execution/exchange.py
- **Função**: Polymarket CLOB client wrapper
- **Classes**: `PolymarketExchange`
- **Deps internas**: `config`, `models`
- **Deps externas**: `py_clob_client` (import condicional)
- **Status**: 🔴 STUBS
- **Testes**: ✅ test_exchange.py (4 testes — muito pouco)
- **Riscos**: (1) `place_order()` é TODO stub, (2) `cancel_order()` é TODO stub, (3) `get_orders()` retorna lista vazia hardcoded

### polymarket_glm/execution/live_executor.py
- **Função**: Live trading executor via CLOB real
- **Classes**: `LiveExecutor`
- **Deps internas**: `execution.exchange`, `execution.portfolio_tracker`, `execution.settlement_tracker`
- **Deps externas**: `asyncio`, `logging`
- **Status**: 🔴 QUEBRADO (depende de stubs)
- **Testes**: ✅ test_live_executor.py (8 testes — dry-run only)
- **Riscos**: (1) Falha em runtime — exchange methods são stubs, (2) Sem idempotência, (3) Sem position reconciliation

### polymarket_glm/execution/paper_executor.py
- **Função**: Paper trading — simula fills sem ordens reais
- **Classes**: `PaperExecutor`
- **Deps internas**: `execution.portfolio_tracker`, `execution.settlement_tracker`, `models`, `config`
- **Deps externas**: `asyncio`, `logging`
- **Status**: ✅ Completo (com ressalvas)
- **Testes**: ✅ test_paper_executor.py (8 testes)
- **Riscos**: (1) Não implementa `PositionExecutorProtocol`, (2) Sem slippage, (3) Sem concurrency protection, (4) Sem close_position manual

### polymarket_glm/execution/portfolio_tracker.py
- **Função**: Portfolio P&L, equity curve, drawdown
- **Classes**: `PortfolioTracker`, `PortfolioSnapshot`
- **Deps internas**: `models`
- **Deps externes**: `pydantic`, `dataclasses`, `datetime`
- **Status**: ✅ Completo
- **Testes**: ✅ test_portfolio_tracker.py (14 testes)
- **Riscos**: (1) `max_drawdown` só cresce, (2) unrealized_pnl assume "Yes", (3) Sem persistência

### polymarket_glm/execution/position_executor.py
- **Função**: Position lifecycle com triple barriers
- **Classes**: `PositionExecutor`, `ManagedPosition`, `PositionExecutorConfig`
- **Deps internas**: `execution.barriers`, `models`, `strategy.signal_engine`
- **Deps externas**: `pydantic`, `dataclasses`, `logging`, `time`
- **Status**: 🟡 Parcial
- **Testes**: ✅ test_controller_executor.py (25 testes)
- **Riscos**: (1) **BUG**: trailing stop — mesmo bug de barriers.py, (2) Sem position size limit, (3) close manual sem close_price

### polymarket_glm/execution/position_manager.py
- **Função**: Simple position state manager — REDUNDANTE
- **Classes**: `PositionManager`
- **Deps internas**: `models`
- **Status**: 🟠 DUPLICADO
- **Testes**: ✅ test_position_manager.py (10 testes)
- **Riscos**: (1) Funcionalidade já existe em PaperExecutor, PortfolioTracker, PositionExecutor

### polymarket_glm/execution/settlement_tracker.py
- **Função**: Detecta mercados resolvidos e settle posições
- **Classes**: `SettlementResult`, `SettlementSummary`, `SettlementTracker`
- **Deps internas**: `models`
- **Deps externas**: `dataclasses`, `datetime`
- **Status**: ✅ Completo
- **Testes**: ✅ test_settlement_tracker.py (10 testes)
- **Riscos**: (1) `_settled_markets` cresce sem limite, (2) `datetime.utcnow` deprecado, (3) Sem persistência, (4) Settlement price hardcode 1.0/0.0

### polymarket_glm/execution/signal_controller.py
- **Função**: Decide O QUE trade — scan, filter, signal, delegate
- **Classes**: `SignalController`, `ControllerConfig`, `ControllerState`, `PositionExecutorProtocol`
- **Deps internas**: `execution.barriers`, `models`, `strategy.signal_engine`
- **Deps externas**: `pydantic`, `dataclasses`, `typing`, `time`
- **Status**: ✅ Completo
- **Testes**: ✅ test_controller_executor.py (25 testes)
- **Riscos**: (1) Dedup `pid.split("::")[0]` frágil, (2) Sem executor → signals dropadas silenciosamente

### polymarket_glm/ingestion/market_fetcher.py
- **Função**: Gamma API wrapper — fetch e filter mercados
- **Classes**: `MarketFetcher`, `MarketFilter`
- **Deps internas**: `models`
- **Deps externas**: `httpx`, `pydantic`
- **Status**: ✅ Completo
- **Testes**: ✅ test_market_fetcher.py (6 testes)
- **Riscos**: (1) Paginação limitada a 5 páginas, (2) `_is_sport()` heurística incompleta, (3) Sem retry em HTTP errors

### polymarket_glm/ingestion/price_feed.py
- **Função**: REST polling + WebSocket com auto-reconnect
- **Classes**: `PriceFeed`, `PriceSnapshot`
- **Deps internas**: `models`
- **Deps externas**: `httpx`, `websockets`, `asyncio`
- **Status**: ✅ Completo (com ressalvas)
- **Testes**: ✅ test_price_feed.py + test_ws_price_feed.py (9 testes)
- **Riscos**: (1) WS reconnect sem limite máximo, (2) `_fetch_book()` sem connection pooling, (3) Sem validação bid < ask

### polymarket_glm/interface/cli.py
- **Função**: CLI — backtest, paper trade, status, setup
- **Classes**: Funções: main(), _cmd_backtest(), _cmd_paper(), _cmd_status(), _cmd_setup()
- **Deps internas**: `config`, `backtest.engine`, `execution.paper_executor`, `strategy.estimator`
- **Deps externas**: `argparse`, `asyncio`
- **Status**: 🟡 Parcial
- **Testes**: ✅ test_cli.py (4 testes)
- **Riscos**: (1) `_cmd_paper()` é TODO, (2) `_cmd_status()` é stub, (3) asyncio.run sem graceful shutdown

### polymarket_glm/interface/dashboard.py
- **Função**: Terminal dashboard (STUB)
- **Classes**: `Dashboard`, `DashboardConfig`
- **Status**: 🔴 STUB — render() só faz print()
- **Testes**: ✅ test_dashboard.py (4 testes)
- **Riscos**: Rich importado mas não usado, sem async

### polymarket_glm/monitoring/alerts.py
- **Função**: Telegram notifications — circuit breakers, drawdown, errors
- **Classes**: `AlertLevel`, `Alert`, `Alerter`, `TelegramAlerter`
- **Deps internas**: `config`
- **Deps externas**: `httpx`, `pydantic`
- **Status**: ✅ Completo
- **Testes**: ✅ test_alerts.py (8 testes)
- **Riscos**: (1) Bot token exposto em URL, (2) Sem rate limiting, (3) HTTP sync bloqueia async loop

### polymarket_glm/monitoring/daily_report.py
- **Função**: Daily performance report
- **Classes**: `DailyReport`, `DailyReportConfig`, `ReportData`
- **Deps internas**: `execution.portfolio_tracker`, `execution.settlement_tracker`
- **Deps externas**: `pydantic`, `dataclasses`, `datetime`
- **Status**: ✅ Completo
- **Testes**: ✅ test_daily_report.py (8 testes)
- **Riscos**: (1) generate() é sync mas precisa dados async, (2) Sem persistência, (3) `datetime.utcnow` deprecado

### polymarket_glm/ops/health.py
- **Função**: Heartbeat monitoring, loop status, stale detection
- **Classes**: `HealthCheck`, `HeartbeatRecord`, `LoopStatus`, check_loop_health(), format_health_status()
- **Deps externas**: `dataclasses`, `datetime`, `time`
- **Status**: ✅ Completo
- **Testes**: ✅ test_health.py (16 testes)
- **Riscos**: (1) In-memory only, (2) Sem endpoint externo, (3) STALE_THRESHOLD hardcoded

### polymarket_glm/ops/service.py
- **Função**: Gera systemd unit, .env template, start script
- **Classes**: `ServiceConfig`, generate_systemd_unit(), generate_env_file(), generate_start_script()
- **Deps internas**: `config`
- **Status**: ✅ Completo
- **Testes**: ✅ test_service.py (4 testes)
- **Riscos**: (1) `PGLM_MODE` errado — deveria ser `PGLM_EXECUTION_MODE`, (2) Restart=always mascara crashes

### polymarket_glm/ops/telegram_bot.py
- **Função**: Interactive Telegram bot — status, positions, P&L, commands
- **Classes**: `TelegramBot`, `TelegramCommand`, parse_command(), `CommandResult`
- **Deps internas**: `config`, `monitoring.alerts`
- **Deps externas**: `httpx`, `asyncio`
- **Status**: ✅ Funcional (com ressalvas de segurança)
- **Testes**: ✅ test_telegram_bot.py (10 testes)
- **Riscos**: (1) **SEM AUTENTICAÇÃO** — qualquer um pode /sell_all, (2) Sem error recovery no poll, (3) Bot token em URL logado

### polymarket_glm/ops/web_dashboard.py
- **Função**: HTTP dashboard — portfolio status, positions, health
- **Classes**: `DashboardServer`, `DashboardSnapshot`, generate_html(), format_snapshot_json()
- **Deps externas**: `http.server`, `json`, `datetime`
- **Status**: 🟡 Parcial
- **Testes**: ✅ test_web_dashboard.py (5 testes)
- **Riscos**: (1) **SEM AUTH/HTTPS**, (2) stdlib http.server blocking, (3) XSS risk — HTML interpolation sem escaping

### polymarket_glm/risk/controller.py
- **Função**: Risk controller — exposure limits, daily loss, drawdown circuit breaker, kill switch
- **Classes**: `RiskController`, `RiskState`, `DrawdownState`
- **Deps internas**: `models`, `config`
- **Deps externes**: `pydantic`, `dataclasses`, `time`, `logging`
- **Status**: ✅ Completo
- **Testes**: ✅ test_risk_controller.py (10 testes) + test_kill_switch.py (40 testes)
- **Riscos**: (1) `_is_killed` in-memory only, (2) Sem rate limiting em check_trade(), (3) drawdown logic complexa e pouco testada

### polymarket_glm/storage/database.py
- **Função**: SQLite para trade history, positions, state
- **Classes**: `Database`, `TradeRecord`, `PositionRecord`
- **Deps internas**: `models`
- **Deps externes**: `sqlite3`, `pydantic`, `pathlib`
- **Status**: 🟡 Parcial
- **Testes**: ✅ test_database.py (6 testes)
- **Riscos**: (1) Sem WAL mode, (2) Sem connection pooling, (3) Sem migrations, (4) init_tables() no construtor

### polymarket_glm/strategy/calibration.py
- **Função**: Brier score, reliability diagram, per-estimator metrics
- **Classes**: `CalibrationEntry`, `BrierDecomposition`, `CalibrationTracker`
- **Deps externes**: `pydantic`, `math`
- **Status**: ✅ Completo
- **Testes**: ✅ test_calibration.py (9 testes)
- **Riscos**: (1) _entries cresce sem limite, (2) brier_score() retorna 0.0 quando vazio

### polymarket_glm/strategy/context_fetcher.py
- **Função**: News + web search context — NewsAPI + Tavily
- **Classes**: `NewsFetcher`, `WebSearcher`, `ContextBuilder`, `NewsArticle`, `WebSearchResult`
- **Deps externes**: `httpx`, `pydantic`, `re`
- **Status**: ✅ Completo
- **Testes**: ✅ test_context_fetcher.py (22 testes)
- **Riscos**: (1) API keys em query params, (2) Sem caching, (3) Sem rate limiting

### polymarket_glm/strategy/ensemble.py
- **Função**: Multi-template paraphrase ensemble — reduce anchoring bias
- **Classes**: `EnsembleEstimator`, `EnsembleConfig`, `EnsembleResult`
- **Deps internas**: `strategy.estimator`, `strategy.llm_router`
- **Deps externes**: `pydantic`, `asyncio`, `random`
- **Status**: 🔴 BUG CRÍTICO
- **Testes**: ✅ test_ensemble.py (20 testes)
- **Riscos**: (1) **BUG**: paraphrase prompts construídos mas nunca usados — router.estimate() usa prompt default, (2) N calls idênticas derrota propósito, (3) Sem seed para reprodutibilidade

### polymarket_glm/strategy/ensemble_estimator.py
- **Função**: Weighted combination de múltiplos estimators
- **Classes**: `EnsembleEstimator`, `WeightedEstimator`
- **Deps internas**: `strategy.estimator`
- **Deps externes**: `pydantic`, `math`
- **Status**: 🟠 DUPLICADO — mesmo nome que strategy/ensemble.py::EnsembleEstimator
- **Testes**: ✅ test_ensemble_estimator.py (7 testes)
- **Riscos**: (1) Name collision, (2) Protocol sync-only não aceita LLM, (3) arbitrary_types_allowed

### polymarket_glm/strategy/estimator.py
- **Função**: Protocol + HeuristicEstimator baseline
- **Classes**: `EstimateResult`, `MarketInfo`, `ProbabilityEstimator` (Protocol), `HeuristicEstimator`
- **Deps externes**: `pydantic`, `datetime`
- **Status**: 🟡 Parcial
- **Testes**: ✅ test_estimator.py (8 testes)
- **Riscos**: (1) **Protocol é sync-only** — LLM estimators são async, incompatíveis, (2) Silent clamping em probability, (3) HeuristicEstimator é puramente derivativo

### polymarket_glm/strategy/llm_estimator.py
- **Função**: Single-provider LLM estimator
- **Classes**: `LLMEstimator`, `LLMConfig`
- **Deps internas**: `strategy.estimator`
- **Deps externes**: `openai`, `pydantic`, `re`
- **Status**: 🟠 SUPERSEDED por llm_router.py
- **Testes**: ✅ test_llm_estimator.py (6 testes)
- **Riscos**: (1) Duplica llm_router.py, (2) Async incompatível com Protocol, (3) parse_probability duplicado

### polymarket_glm/strategy/llm_router.py
- **Função**: Multi-provider free API router — rate limiting, fallback, CoT validation, shrinkage
- **Classes**: `LLMRouter`, `LLMProviderConfig`, `LLMRouterConfig`, `RateLimitTracker`, `CoTValidation`
- **Funções**: build_superforecaster_prompt(), validate_cot_structure(), apply_cot_penalty(), parse_llm_probability()
- **Deps internas**: `strategy.estimator`
- **Deps externes**: `openai`, `pydantic`, `re`, `time`, `collections.deque`
- **Status**: ✅ Completo (maior arquivo — 738 linhas, precisa split)
- **Testes**: ✅ test_llm_router.py (30 testes)
- **Riscos**: (1) LLMRouterConfig name collision com config.py, (2) api_key plain string, (3) Shrinkage hardcoded 15%, (4) `_clamp` privado mas exportado

### polymarket_glm/strategy/signal_engine.py
- **Função**: Edge calculation, Kelly criterion sizing, signal generation
- **Classes**: `SignalType`, `Signal`, `SignalEngine`
- **Deps internas**: `models`
- **Deps externes**: `pydantic`, `math`, `enum`, `datetime`
- **Status**: ✅ Completo
- **Testes**: ✅ test_signal_engine.py (5 testes)
- **Riscos**: (1) Edge clamped a 30% pode push probability fora [0,1], (2) Kelly pode 100% bankroll em markets de low price, (3) `datetime.utcnow` deprecado

---

## 7. Fluxo atual do sistema

```
┌──────────────────────────────────────────────────────────────────────┐
│                         ENTRY POINTS                                │
│  scripts/run_bot.py (main loop)  │  pglm CLI (status/scan/trade)    │
└──────────────┬───────────────────┴──────────────┬────────────────────┘
               │                                  │
               ▼                                  ▼
┌────────────────────────┐       ┌─────────────────────────────┐
│   TradingEngine /      │       │      CLI (argparse)         │
│   TradingLoop          │       │  status/scan/trade/risk/    │
│   (DUPLICAÇÃO!)        │       │  killswitch                 │
└────────┬───────────────┘       └─────────────────────────────┘
         │
    ┌────▼────────────────────────────────────────────┐
    │              TRADING ITERATION                    │
    │                                                   │
    │  1. MarketFetcher → fetch markets (Gamma API)     │
    │  2. LLMRouter → estimate probabilities (async)    │
    │  3. SignalEngine → calculate edge + Kelly size    │
    │  4. RiskController → check trade approval         │
    │  5. PaperExecutor → simulate fill (paper mode)    │
    │  6. PortfolioTracker → update P&L                 │
    │  7. SettlementTracker → check resolved markets    │
    │  8. Alerts → send Telegram notifications          │
    └───────────────────────────────────────────────────┘
```

---

## 8. Fluxo atual de paper trading

1. **Startup**: `scripts/run_bot.py` → `TradingLoop` → carrega `.env`
2. **Modo**: `PGLM_EXECUTION_MODE=paper` (padrão)
3. **Iteração**: Loop infinito com sleep configurável
4. **Fetch**: `MarketFetcher` busca até 500 mercados da Gamma API, filtra esportes, seleciona por volume/spread
5. **Estimate**: `LLMRouter` chama providers (Groq, Gemini) com superforecaster prompt, faz CoT validation + shrinkage
6. **Signal**: `SignalEngine` calcula edge (estimativa vs preço) e Kelly size
7. **Risk**: `RiskController` verifica exposure, daily loss, kill switch, circuit breaker
8. **Execute**: `PaperExecutor` simula fill no mid price, atualiza balance e positions
9. **Track**: `PortfolioTracker` calcula P&L, drawdown; `SettlementTracker` detecta mercados resolvidos
10. **Alert**: `TelegramAlerter` envia notificações (circuit breaker, drawdown, erros)
11. **Health**: `HealthCheck` monitora heartbeat e stale detection

**Deploy**: systemd service `polymarket-simulation` com `Restart=always`

---

## 9. Fluxo atual de live trading — bloqueio e segurança

### Gates de segurança (atualmente implementados):
1. **`PGLM_EXECUTION_MODE=paper`** — padrão em config.py
2. **`LiveExecutor`** — verifica CLOB API keys antes de executar; sem keys → dry-run
3. **RiskController** — kill switch bloqueia todas as ordens quando ativo
4. **Daily loss limit** — bloqueia trades quando limite atingido
5. **Drawdown circuit breaker** — ativa kill switch automaticamente em drawdown >10%

### Problemas de segurança do live trading:
1. 🔴 **Exchange methods são STUBS** — `place_order()`, `cancel_order()` levantam `NotImplementedError`
2. 🔴 **Sem idempotência** — ordem pode ser enviada 2x se loop travar
3. 🔴 **Sem position reconciliation** — startup não verifica posições abertas na exchange
4. 🟠 **Telegram bot sem auth** — qualquer um pode /sell_all em live
5. 🟠 **Web dashboard sem auth/HTTPS** — portfolio exposto
6. 🟡 **Kill switch in-memory** — perde estado em restart

### Variáveis de ambiente necessárias para live:
- `PGLM_EXECUTION_MODE=live` (explicitamente)
- `PGLM_CLOB_API_KEY`, `PGLM_CLOB_API_SECRET`, `PGLM_CLOB_API_PASSPHRASE`
- `PGLM_PRIVATE_KEY` (wallet Polygon)

**Veredito**: Live trading está **BLOQUEADO por design** e **NÃO FUNCIONAL** mesmo que desbloqueado. Isso é INTENCIONAL e CORRETO para esta fase.

---

## 10. Banco de dados e persistência

| Aspecto | Detalhe |
|---------|---------|
| Engine | SQLite3 (stdlib) |
| Arquivo | `polymarket_glm.db` |
| Tabelas | `markets`, `trades`, `signals`, `prices` |
| Índices | 3 (em markets, trades, signals) |
| WAL mode | ❌ Não configurado |
| Migrations | ❌ Sem sistema de migration |
| Backup | ❌ Sem estratégia |
| Connection pooling | ❌ Nova conexão por método |
| Schema versioning | ❌ Não existe |

**Problemas**:
- Sem WAL → reads bloqueiam writes e vice-versa
- Sem migration → schema changes requerem intervenção manual
- `init_tables()` no construtor → side effect
- TradeRecord e PositionRecord são models Pydantic mas não refletem todas as colunas

---

## 11. Telegram e observabilidade

| Componente | Arquivo | Status |
|------------|---------|--------|
| Alertas | `monitoring/alerts.py` | ✅ Funcional |
| Bot interativo | `ops/telegram_bot.py` | ✅ Funcional |
| Relatório diário | `monitoring/daily_report.py` | ✅ Gerador existe, mas NÃO integrado ao loop |
| Health check | `ops/health.py` | ✅ Funcional (in-memory) |

**Lacunas**:
1. Bot interativo **sem autenticação** — qualquer pessoa com o token pode emitir comandos
2. Relatório diário existe mas **não é enviado automaticamente** — precisa integração no trading loop
3. Alertas **síncronos** (httpx.post) bloqueiam async loop
4. Sem dedup de alertas — mesmo alerta enviado repetidamente
5. Bot token aparece em URLs logadas

---

## 12. Risk controller e kill switch

| Feature | Status | Detalhe |
|---------|--------|---------|
| Kill switch manual | ✅ Funcional | `pglm killswitch` ativa/desativa |
| Kill switch auto (drawdown) | ✅ Funcional | Circuit breaker em 10% drawdown |
| Daily loss limit | ✅ Funcional | Bloqueia trades após $30 loss |
| Per-trade limit | ✅ Funcional | Max $50 por trade |
| Per-market exposure | ✅ Funcional | Max $200 por mercado |
| Total exposure | ✅ Funcional | Max $500 total |
| Drawdown arm period | ✅ Funcional | 300s cooldown após trigger |
| Estado persistente | ❌ | Kill switch perde estado em restart |
| Telegram integration | ✅ | Bot suporta /killswitch |

**Defaults do código real** (config.py):
- max_total_exposure_usd = 500
- max_per_trade_usd = 50
- daily_loss_limit_usd = 30
- drawdown_circuit_breaker_pct = 0.10
- max_per_market_exposure_usd = 200

⚠️ **README e .env.example mostram defaults 2–10x menos conservadores!**

---

## 13. Lacunas técnicas

| # | Lacuna | Severidade | Arquivos afetados |
|---|--------|------------|-------------------|
| 1 | Trailing stop nunca dispara (high_water_mark=0 fixo) | 🔴 Crítica | barriers.py, position_executor.py |
| 2 | Ensemble paraphrase prompts ignorados | 🔴 Crítica | ensemble.py |
| 3 | EnsembleEstimator name collision | 🔴 Alta | ensemble.py, ensemble_estimator.py |
| 4 | LLMRouterConfig name collision | 🔴 Alta | config.py, llm_router.py |
| 5 | ProbabilityEstimator Protocol sync-only | 🔴 Alta | estimator.py, llm_estimator.py, llm_router.py |
| 6 | TradingEngine + TradingLoop duplicação | 🟠 Média | engine/__init__.py, engine/trading_loop.py |
| 7 | PositionManager redundante | 🟠 Média | position_manager.py |
| 8 | Exchange stubs (place_order, cancel_order) | 🟠 Média | exchange.py |
| 9 | PaperExecutor não implementa PositionExecutorProtocol | 🟠 Média | paper_executor.py, signal_controller.py |
| 10 | datetime.utcnow deprecado (5+ arquivos) | 🟡 Baixa | models.py, settlement_tracker.py, daily_report.py, signal_engine.py |
| 11 | llm_estimator.py superseído por llm_router.py | 🟡 Baixa | llm_estimator.py |
| 12 | Database sem WAL/migrations | 🟡 Baixa | database.py |
| 13 | Engine/__init__.py God class (320+ linhas) | 🟡 Baixa | engine/__init__.py |

---

## 14. Lacunas de testes

| Arquivo | Testes | Cobertura | Gaps principais |
|---------|--------|-----------|-----------------|
| test_cli.py | 4 | 🟡 Mínima | Sem testes de invalid args, help, env overrides |
| test_dashboard.py | 4 | 🟡 Mínima | Sem testes de HTTP serving, auth |
| test_price_feed.py | 4 | 🟡 Mínima | Sem testes de HTTP polling, retry |
| test_service.py | 4 | 🟡 Mínima | Sem testes de daemon, signal handling |
| test_exchange.py | 4 | 🟡 Mínima | Sem testes de order lifecycle, partial fills |
| test_engine.py | 4 | 🟡 Mínima | Sem testes de shutdown, error recovery |
| test_web_dashboard.py | 5 | 🟡 Mínima | Sem testes de HTTP, auth, CORS |
| conftest.py | 0 | 🔴 Vazio | Sem fixtures compartilhadas |
| **Geral** | — | 🟡 | Sem tests de concorrência, crash recovery, performance |

**Total**: 534 testes passando, mas cobertura é desigual — áreas críticas como exchange, engine e dashboard são muito pouco testadas.

---

## 15. Riscos operacionais

| # | Risco | Probabilidade | Impacto | Mitigação |
|---|-------|---------------|---------|-----------|
| 1 | Trailing stop não funciona → posições sem proteção | Alta | Alto | Corrigir bug high_water_mark |
| 2 | Ensemble faz N calls idênticas → desperdício de API | Alto | Médio | Corrigir passagem de paraphrase prompt |
| 3 | Kill switch perde estado em restart | Médio | Alto | Persistir em DB |
| 4 | Telegram bot sem auth → comandos destrutivos por terceiros | Médio | Crítico (live) | Adicionar whitelist de chat_ids |
| 5 | Web dashboard sem auth → dados expostos | Baixo | Médio | Adicionar basic auth ou Tailscale-only |
| 6 | PGLM_MODE ignorado nos docs → config errada | Médio | Médio | Corrigir docs e service.py |
| 7 | Defaults de risco nos docs divergem do código | Médio | Médio | Sincronizar docs com config.py |
| 8 | DB sem WAL → concorrência bloqueia | Baixo | Médio | Habilitar WAL mode |
| 9 | Memory leak em _settled_markets e _entries | Baixo | Baixo | Adicionar pruning |
| 10 | asyncio.run sem graceful shutdown | Médio | Baixo | Adicionar signal handler |

---

## 16. Issues sugeridas

### Issue 1: Bug — Trailing Stop Nunca Dispara
- **Objetivo**: Corrigir bug onde `high_water_mark` nunca é atualizado, fazendo trailing stop ser dead code
- **Contexto**: `barriers.py` `TripleBarrier.check()` e `position_executor.py` `PositionExecutor.check_barriers()` não atualizam `high_water_mark`
- **Escopo**: barriers.py, position_executor.py, test_barriers.py, test_controller_executor.py
- **Fora de escopo**: Adicionar trailing stop para posições short
- **Arquivos afetados**: `execution/barriers.py`, `execution/position_executor.py`
- **Critérios de aceite**: (1) high_water_mark atualizado em cada check, (2) Trailing stop dispara quando price cai X% do high, (3) Testes passam
- **Testes obrigatórios**: test_trailing_stop_activates, test_trailing_stop_follows_high_water_mark
- **Riscos**: Mudança em lógica de barriers pode afetar posições existentes

### Issue 2: Bug — Ensemble Paraphrase Prompts Ignorados
- **Objetivo**: Corrigir ensemble.py para usar paraphrase prompts customizados em vez de default
- **Contexto**: `_estimate_with_template()` constrói prompt customizado mas chama `router.estimate()` que usa prompt default
- **Escopo**: ensemble.py, llm_router.py
- **Fora de escopo**: Adicionar novos templates
- **Arquivos afetados**: `strategy/ensemble.py`, `strategy/llm_router.py`
- **Critérios de aceite**: (1) Templates não-default usam paraphrase prompt, (2) N calls produzem resultados diferentes, (3) Testes passam
- **Testes obrigatórios**: test_ensemble_uses_paraphrase_prompt, test_ensemble_produces_diverse_estimates
- **Riscos**: Necessário refatorar LLMRouter.estimate() para aceitar prompt custom

### Issue 3: Deduplicação — Resolver EnsembleEstimator Name Collision
- **Objetivo**: Eliminar duplicação de nome entre ensemble.py e ensemble_estimator.py
- **Contexto**: Duas classes diferentes com mesmo nome no mesmo package
- **Escopo**: ensemble.py, ensemble_estimator.py, engine/, trading_loop.py
- **Fora de escopo**: Refatorar lógica interna
- **Arquivos afetados**: `strategy/ensemble.py`, `strategy/ensemble_estimator.py`, imports em engine/
- **Critérios de aceite**: (1) Nenhuma classe duplicada, (2) Todos imports funcionam, (3) 534 testes passam
- **Testes obrigatórios**: pytest -q full suite
- **Riscos**: Breaking changes em imports

### Issue 4: Deduplicação — Resolver LLMRouterConfig Name Collision
- **Objetivo**: Unificar LLMRouterConfig em um único local
- **Contexto**: config.py tem LLMRouterConfig flat, llm_router.py tem LLMRouterConfig com providers list
- **Escopo**: config.py, llm_router.py
- **Arquivos afetados**: `config.py`, `strategy/llm_router.py`
- **Critérios de aceite**: (1) Um único LLMRouterConfig, (2) Settings usa o correto, (3) Testes passam
- **Testes obrigatórios**: pytest -q
- **Riscos**: Breaking changes em config loading

### Issue 5: Deduplicação — Unificar TradingEngine e TradingLoop
- **Objetivo**: Eliminar orquestração duplicada
- **Contexto**: engine/__init__.py TradingEngine e engine/trading_loop.py TradingLoop fazem a mesma coisa
- **Escopo**: engine/__init__.py, engine/trading_loop.py, scripts/run_bot.py
- **Arquivos afetados**: `engine/__init__.py`, `engine/trading_loop.py`, `scripts/run_bot.py`
- **Critérios de aceite**: (1) Uma única classe de orquestração, (2) Scripts usam a classe correta, (3) Testes passam
- **Testes obrigatórios**: pytest -q
- **Riscos**: Refatoração grande — fazer em branch separada

### Issue 6: Sprint 13 — Position Tracker + P&L Mark-to-Market
- **Objetivo**: Integrar PortfolioTracker no trading loop com mark-to-market contínuo
- **Contexto**: PortfolioTracker existe mas não é integrado ao loop, sem persistência de P&L
- **Escopo**: portfolio_tracker.py, trading_loop.py, database.py, daily_report.py
- **Arquivos afetados**: `execution/portfolio_tracker.py`, `engine/trading_loop.py`, `storage/database.py`, `monitoring/daily_report.py`
- **Critérios de aceite**: (1) P&L atualizado a cada iteração, (2) Persistido em DB, (3) Disponível via CLI e Telegram
- **Testes obrigatórios**: test_portfolio_persistence, test_pnl_per_iteration, test_pnl_cli
- **Riscos**: Mudança em schema do DB

### Issue 7: Sprint 14 — Settlement Tracker + Win/Loss
- **Objetivo**: Integrar SettlementTracker no loop, calcular win rate e settlement P&L
- **Contexto**: SettlementTracker existe mas não é integrado ao loop
- **Escopo**: settlement_tracker.py, trading_loop.py, database.py
- **Arquivos afetados**: `execution/settlement_tracker.py`, `engine/trading_loop.py`, `storage/database.py`
- **Critérios de aceite**: (1) Settlements detectados automaticamente, (2) Win/Loss trackeado, (3) Persistido em DB
- **Testes obrigatórios**: test_auto_settlement, test_win_loss_tracking, test_settlement_persistence
- **Riscos**: Gamma API pode não reportar resolução imediatamente

### Issue 8: Sprint 15 — Relatório Diário Telegram
- **Objetivo**: Enviar relatório diário automático via Telegram
- **Contexto**: DailyReport existe mas não é integrado ao loop, não é enviado
- **Escopo**: daily_report.py, trading_loop.py, telegram_bot.py
- **Arquivos afetados**: `monitoring/daily_report.py`, `engine/trading_loop.py`, `ops/telegram_bot.py`
- **Critérios de aceite**: (1) Relatório enviado 1x/dia, (2) Contém P&L, posições, win rate, (3) Configurável
- **Testes obrigatórios**: test_daily_report_sends, test_report_content, test_report_disabled
- **Riscos**: Rate limit do Telegram API

### Issue 9: Sprint 16 — Dashboard Web
- **Objetivo**: Dashboard web funcional com auth e async
- **Contexto**: web_dashboard.py usa stdlib http.server blocking sem auth
- **Escopo**: web_dashboard.py, risk/controller.py
- **Arquivos afetados**: `ops/web_dashboard.py`
- **Critérios de aceite**: (1) Servidor async (FastAPI/aiohttp), (2) Basic auth ou Tailscale-only, (3) JSON API + HTML frontend
- **Testes obrigatórios**: test_dashboard_auth, test_dashboard_api, test_dashboard_concurrent
- **Riscos**: Adicionar dependências (FastAPI)

### Issue 10: Sprint 11–12 — LLM Multi-Provider Router + Superforecaster Prompt
- **Objetivo**: Consolidar Sprint 11–12 como COMPLETO, atualizar docs
- **Contexto**: LLMRouter com Groq+Gemini já funciona, superforecaster prompt + CoT validation implementados, mas docs marcam como pendente
- **Escopo**: NEXT_STEPS.md, README.md, llm_router.py (split)
- **Arquivos afetados**: `NEXT_STEPS.md`, `README.md`, `strategy/llm_router.py`
- **Critérios de aceite**: (1) Docs refletem realidade, (2) Bug do ensemble corrigido (Issue 2)
- **Testes obrigatórios**: pytest -q
- **Riscos**: Nenhum

### Issue 11: Hardening de Segurança para Live Trading Bloqueado
- **Objetivo**: Garantir que live trading não pode ser ativado acidentalmente
- **Contexto**: Múltiplos gaps de segurança identificados
- **Escopo**: telegram_bot.py, web_dashboard.py, config.py, service.py, .env.example
- **Arquivos afetados**: `ops/telegram_bot.py`, `ops/web_dashboard.py`, `config.py`, `ops/service.py`, `.env.example`
- **Critérios de aceite**: (1) Telegram bot com whitelist, (2) Web dashboard com auth, (3) .env.example atualizado, (4) service.py usa PGLM_EXECUTION_MODE
- **Testes obrigatórios**: test_telegram_auth, test_dashboard_auth, test_env_var_names
- **Riscos**: Breaking changes em config

### Issue 12: Cobertura de Testes e CI
- **Objetivo**: Aumentar cobertura e adicionar CI pipeline
- **Contexto**: 534 testes mas áreas críticas com cobertura mínima
- **Escopo**: tests/, pyproject.toml, .github/workflows/
- **Arquivos afetados**: Testes críticos: test_exchange.py, test_engine.py, test_dashboard.py
- **Critérios de aceite**: (1) Coverage ≥80%, (2) GitHub Actions CI, (3) conftest.py com fixtures compartilhadas
- **Testes obrigatórios**: N/A (this IS the test issue)
- **Riscos**: Tempo investido em testes não adiciona features

### Issue 13: Documentação de Operação Paper por 30 Dias
- **Objetivo**: Documentar procedimento para simulação de 30 dias
- **Contexto**: Precisamos de 30 dias de paper trading antes de considerar live
- **Escopo**: docs/, monitoring/, ops/
- **Arquivos afetados**: Novo doc `docs/PAPER_30_DAYS.md`
- **Critérios de aceite**: (1) Doc com checklist diário, (2) Métricas a trackear, (3) Critérios de parada
- **Testes obrigatórios**: N/A (documentação)
- **Riscos**: Nenhum

---

## 17. Ordem recomendada de implementação

| Ordem | Issue | Prioridade | Rationale |
|-------|-------|------------|-----------|
| 1 | Bug trailing stop | 🔴 P0 | Bug crítico — proteção de posição não funciona |
| 2 | Bug ensemble prompts | 🔴 P0 | Bug crítico — dinheiro de API desperdiçado |
| 3 | Dedup EnsembleEstimator | 🟠 P1 | Name collision causa confusão |
| 4 | Dedup LLMRouterConfig | 🟠 P1 | Name collision causa confusão |
| 5 | Unify TradingEngine/Loop | 🟠 P1 | Dedup orquestração — base para Sprint 13+ |
| 6 | Docs: Sprint 11-12 completo | 🟡 P2 | Atualizar docs antes de avançar |
| 7 | Docs: env vars + defaults | 🟡 P2 | PGLM_MODE vs PGLM_EXECUTION_MODE |
| 8 | Sprint 13: Position Tracker | 🟡 P2 | P&L mark-to-market integrado |
| 9 | Sprint 14: Settlement Tracker | 🟡 P2 | Win/Loss tracking |
| 10 | Sprint 15: Relatório Diário | 🟡 P2 | Observabilidade para 30-day paper |
| 11 | Hardening segurança | 🟡 P2 | Antes de qualquer live consideration |
| 12 | Sprint 16: Dashboard Web | 🟢 P3 | Nice-to-have, não bloqueia paper |
| 13 | Cobertura de testes + CI | 🟢 P3 | Contínuo, não bloqueia funcionalidade |
| 14 | Paper 30 dias doc | 🟢 P3 | Doc final, antes de live |

---

## 18. Critérios para considerar o projeto pronto para simulação de 30 dias

Antes de iniciar simulação de 30 dias ininterruptos, o projeto deve satisfazer:

- [ ] ✅ 534+ testes passando
- [ ] 🔲 Bug do trailing stop corrigido
- [ ] 🔲 Bug do ensemble paraphrase corrigido
- [ ] 🔲 Name collisions resolvidos (EnsembleEstimator, LLMRouterConfig)
- [ ] 🔲 TradingEngine/TradingLoop unificados
- [ ] 🔲 PortfolioTracker integrado ao loop com persistência
- [ ] 🔲 SettlementTracker integrado ao loop com persistência
- [ ] 🔲 Relatório diário Telegram automático
- [ ] 🔲 Kill switch persistente (sobrevive restart)
- [ ] 🔲 Telegram bot com autenticação (whitelist)
- [ ] 🔲 .env.example sincronizado com config.py
- [ ] 🔲 PGLM_MODE → PGLM_EXECUTION_MODE corrigido nos docs
- [ ] 🔲 Risk defaults sincronizados docs ↔ código
- [ ] 🔲 DB com WAL mode habilitado
- [ ] 🔲 DB schema versioning (migration básica)
- [ ] 🔲 Conftest.py com fixtures compartilhadas
- [ ] 🔲 Coverage ≥70% nos módulos críticos
- [ ] 🔲 Paper rodando 24/7 sem crash por 7 dias consecutivos
- [ ] 🔲 Log rotation configurado
- [ ] 🔲 Documento de operação paper-30-dias criado

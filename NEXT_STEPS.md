# NEXT STEPS — Roadmap para Simulação 1 Mês → Live Trading

## O que JÁ TEMOS ✅
- [x] MarketFetcher (scan 50 mercados reais)
- [x] PriceFeed (order books via CLOB API)
- [x] SignalEngine (edge + Kelly sizing)
- [x] RiskController (limits, kill switch, circuit-breaker)
- [x] PaperExecutor (simula fills, track positions/balance)
- [x] Database SQLite (markets, trades, signals, prices)
- [x] Telegram Bot (/status, /balance, /positions, /stop, /killswitch)
- [x] Telegram Alerts (trade fills, warnings)
- [x] HeuristicEstimator (volume/spread/category/recency)
- [x] LLMEstimator (OpenAI-compatible, aceita base_url)
- [x] EnsembleEstimator (weighted avg + agreement bonus)
- [x] Calibration tracking (Brier score, reliability diagram)
- [x] systemd service 24/7
- [x] run_simulation.py (loop contínuo)

## O que FALTA para o mês de simulação ser válido ❌

### Sprint 11: LLM Multi-Provider Router (CÉREBRO)
**Fonte**: awesome-free-llm-apis + Polymarket/agents superforecaster prompt
- [ ] `strategy/llm_router.py` — Round-robin entre providers free:
  - Groq (Llama-3.3-70B) → 30 RPM → primário
  - Gemini 2.5 Flash → 10 RPM → backup
  - GitHub Models (GPT-4.1-mini) → 15 RPM → raciocínio profundo
  - Cerebras → 30 RPM → ultra-rápido
  - Mistral → 1 RPS → fallback
- [ ] Rate limit tracking por provider (token bucket)
- [ ] Fallback automático (se um provider falha → tenta próximo)
- [ ] `.env` com API keys de cada provider (todas free)
- [ ] Substituir Gaussian noise no run_simulation.py → LLMRouter

### Sprint 12: Superforecaster Prompt + News Context
**Fonte**: Polymarket/agents/prompts.py + connectors/news.py
- [ ] Superforecaster prompt adaptado do repo oficial (5-step systematic process)
- [ ] `ingestion/news_fetcher.py` — NewsAPI (free tier: 100 req/day)
  - Busca notícias relevantes por keyword do mercado
  - Injeta no prompt do LLM como contexto
- [ ] `ingestion/web_search.py` — Tavily (free tier: 1000 credits/mês)
  - Search contextual para mercados sem notícias recentes
- [ ] MarketInfo estendido com `news_context: str`
- [ ] LLMEstimator prompt atualizado: superforecaster + news

### Sprint 13: Position Tracker + P&L Mark-to-Market
- [ ] `execution/position_tracker.py`
  - Re-fetch preço atual de cada posição aberta
  - Calcular unrealized_pnl = (current_price - avg_price) * size
  - Calcular total portfolio value = balance + sum(unrealized_pnl)
  - Detectar mercados que resolveram (closed=True) → settle positions
- [ ] Estender Database com tabela `positions` (open positions com P&L)
- [ ] Estender Database com tabela `daily_snapshots` (P&L diário para gráfico)
- [ ] Telegram comando `/pnl` → P&L atual com breakdown

### Sprint 14: Settlement Tracker + Win/Loss
- [ ] `execution/settlement_tracker.py`
  - Detectar mercados fechados (closed=True na Gamma API)
  - Verificar outcome vencedor
  - Calcular payout = size * price (se ganhou) ou 0 (se perdeu)
  - Marcar posição como settled
  - Atualizar balance com payout
- [ ] Calcular win rate, avg_return_per_trade
- [ ] Estender Database com colunas `settled`, `payout`, `settled_at`

### Sprint 15: Relatório Diário Telegram
- [ ] `monitoring/daily_report.py`
  - P&L do dia (realized + unrealized)
  - Win rate acumulado
  - Total trades, total volume
  - Top 3 positions (best/worst)
  - Drawdown desde peak
  - Brier score do estimador
- [ ] Cron: todo dia 00:00 UTC → envia relatório no Telegram
- [ ] `/report` comando manual

### Sprint 16: Dashboard Web
- [ ] `interface/web_dashboard.py` (FastAPI + HTM...[truncated]
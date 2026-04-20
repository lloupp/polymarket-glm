# polymarket-glm — Próximos Passos (Sprint 6–10)

> Estado atual: 15 tasks completas · 90 testes verdes · 3.083 LOC · 15 commits · sem remote

---

## 📋 Diagnóstico: O que está pronto vs. o que falta

### ✅ Pronto (funcional com testes)
| Camada | Status | Nota |
|--------|--------|------|
| Config (Pydantic v2 + env) | ✅ | Falta: `.env.example` |
| Models (Market, OB, Order) | ✅ | Completo |
| MarketFetcher (Gamma API) | ✅ | Funciona com mock, não testado contra API real |
| PriceFeed (REST poll + WS scaffold) | ✅ | WS é TODO, REST funcional |
| SignalEngine (edge + Kelly) | ✅ | Recebe `estimated_prob` de fora — sem estimator interno |
| RiskController | ✅ | Circuit-breaker, kill switch, exposure limits |
| ExchangeClient Protocol | ✅ | Interface limpa |
| PaperExecutor | ✅ | Fill sim + fees + balance |
| LiveExecutor | ✅ Scaffold | `create_and_post_order` não testado contra CLOB real |
| SQLite Storage | ✅ | 4 tabelas + indexes |
| AlertManager | ✅ | Buffer + callbacks, Telegram não testado |
| Engine (orchestrator) | ✅ | Wire completo, sync path funcional |
| CLI (pglm) | ✅ | 5 subcomandos |
| Dashboard | ✅ Scaffold | Terminal render, sem loop em tempo real |

### ❌ Lacunas críticas identificadas
1. **Sem probability estimator** — SignalEngine recebe `estimated_prob` como parâmetro, mas nenhum módulo GERA essa estimativa. É o coração do sistema e está vazio.
2. **WebSocket não implementado** — PriceFeed WS é scaffold (`while True: sleep(10)`).
3. **LiveExecutor não testado contra CLOB real** — `create_and_post_order()` pode quebrar com py-clob-client 0.34.6 (API mudou).
4. **Sem trading loop** — Engine tem `process_signal_sync()` mas sem loop automático que escaneia → estima → sinaliza → executa.
5. **Sem .env.example** — Usuário não sabe quais vars configurar.
6. **Sem remote Git** — Projeto só existe localmente. Risco de perda.
7. **Dashboard sem atualização em tempo real** — Render único, sem loop nem WebSocket para browser.
8. **Sem integração Telegram real** — AlertManager tem callback mas não envia de verdade.
9. **Sem backtesting** — Nenhum módulo para validar estratégias contra dados históricos.
10. **Database sem migrations** — Schema hardcoded, sem versão.

---

## 🗺️ Plano: Sprint 6–10

### Sprint 6 — Produção-Readiness (estabilidade + deploy)

**Objetivo:** Fazer o framework rodar de verdade, seguro, com backup.

#### Task 6.1: Git remote + backup
- Criar repo no GitHub (`polymarket-glm`)
- `git remote add origin` + push
- Branch protection: `main` exige testes verdes

#### Task 6.2: `.env.example` + documentação de setup
- Arquivo `.env.example` com todas as PGLM_* vars documentadas
- README.md com quickstart, arquitetura, diagrama

#### Task 6.3: Trading Loop (async)
- `Engine.run_loop()` — loop principal async:
  1. MarketFetcher.scan() — descobre mercados
  2. PriceFeed.poll() — atualiza preços
  3. Para cada mercado → chama estimator → SignalEngine.generate()
  4. RiskController.check() → executor.submit()
  5. Sleep → repete
- Teste com mock do ciclo completo

#### Task 6.4: WebSocket PriceFeed real
- Implementar `_ws_loop()` com `websockets` lib
- Subscrição por token_id
- Parse do formato real do Polymarket WS
- Reconnect automático com backoff
- Teste: mock WS server com `test-server`

#### Task 6.5: LiveExecutor — validar contra py-clob-client 0.34.6
- Ler docs/API do `py_clob_client` 0.34.6
- Ajustar `create_and_post_order()` para API atual
- Implementar `create_order()` + `post_order()` separados (novo padrão)
- Dry-run test com CLOB de staging

---

### Sprint 7 — Probability Estimator (o cérebro)

**Objetivo:** Implementar o módulo que gera `estimated_prob` — o input do SignalEngine.

#### Task 7.1: Estimator interface (Protocol)
- `ProbabilityEstimator` Protocol com método `estimate(market, book) -> float`
- Permite plugar qualquer modelo: heurística, LLM, ML, API externa

#### Task 7.2: HeuristicEstimator
- Baseado em volume, spread, recency, categoria
- Rápido, sem API externa, bom para paper trading inicial
- Features: volume_rank, spread_bps, time_to_expiry, category_bias

#### Task 7.3: LLM Estimator (OpenAI/GPT)
- Envia question + context para GPT-4o-mini
- Parse da resposta como probabilidade
- Rate limiting + cache para não estourar API
- Fallback para HeuristicEstimator se API falhar

#### Task 7.4: EnsembleEstimator
- Combina múltiplos estimators com pesos
- Weighted average com calibration
- Logging de concordância/discordância entre estimators

#### Task 7.5: Calibração + validação
- Coletar predições vs resultados reais
- Brier score por estimator
- Ajustar pesos do ensemble com base em performance histórica

---

### Sprint 8 — Backtesting + Validação

**Objetivo:** Provar que a estratégia funciona antes de colocar dinheiro real.

#### Task 8.1: BacktestEngine
- Replayer de dados históricos do SQLite
- Simula PaperExecutor com timestamps passados
- Métricas: Sharpe, max drawdown, win rate, P&L cumulativo

#### Task 8.2: Data collector (historical)
- Script para baixar histórico de preços do Polymarket DATA API
- Armazenar em SQLite com schema versionado

#### Task 8.3: Strategy validator
- Rodar backtest com diferentes parâmetros (grid search)
- Comparar HeuristicEstimator vs LLM vs Ensemble
- Relatório automático com ranking

#### Task 8.4: Paper trading contínuo
- Cron job que roda `pglm --mode paper scan` + trading loop
- Coleta dados por 7+ dias
- Dashboard mostra P&L em tempo real

---

### Sprint 9 — Operacionalização (24/7)

**Objetivo:** Rodar estável na VM Oracle Cloud como serviço.

#### Task 9.1: Systemd service
- `polymarket-glm.service` com auto-restart
- Watchdog: restart se loop morrer
- Log rotation com journald

#### Task 9.2: Telegram bot real
- Implementar envio de alertas via Telegram Bot API
- Comandos: `/status`, `/risk`, `/killswitch`, `/positions`
- Push de alertas críticos (circuit-breaker, kill switch, fill)

#### Task 9.3: Health check + auto-recovery
- Heartbeat no SQLite
- Detectar loop travado (sem heartbeat > 5min)
- Auto-restart com notificação

#### Task 9.4: Tailscale dashboard
- Servir dashboard via HTTP na rede Tailscale
- Auto-refresh com SSE ou polling
- Protegido por Tailscale auth

---

### Sprint 10 — Go-Live Preparation

**Objetivo:** Preparar para trading com dinheiro real, com segurança.

#### Task 10.1: CLOB API key setup guide
- Documentação passo-a-passo para criar conta Polymarket
- Gerar API keys no CLOB
- Configurar `.env` com chaves

#### Task 10.2: Live mode integration test
- Teste contra CLOB de staging (se disponível)
- Ou dry-run com `LiveExecutor(dry_run=True)`
- Validar ordens são criadas corretamente

#### Task 10.3: Risk parameter tuning
- Ajustar `RiskConfig` defaults para operação real:
  - `max_total_exposure_usd`: $500 (conservador)
  - `max_per_trade_usd`: $50 (micro)
  - `daily_loss_limit_usd`: $30
  - `drawdown_circuit_breaker_pct`: 10%
- Simular cenários de stress

#### Task 10.4: Kill switch test suite
- Testar todos os caminhos de ativação do kill switch
- Testar cooldown + deativação manual
- Testar notificação Telegram em cada cenário
- Garantir que NENHUMA ordem passa com kill switch ativo

#### Task 10.5: Go-live checklist + documentação
- Checklist pré-live: chaves, risk params, Telegram, systemd
- Runbook para incidentes
- Documentação de rollback

---

## 🎯 Ordem de execução recomendada

```
Sprint 6 (produção-readiness)  ← PRIORIDADE MÁXIMA (backup + loop real)
  ↓
Sprint 7 (estimator)           ← Segundo mais urgente (sem cérebro = sem trade)
  ↓
Sprint 8 (backtesting)         ← Validar antes de live
  ↓
Sprint 9 (24/7 ops)            ← Estabilidade para rodar contínuo
  ↓
Sprint 10 (go-live)            ← Último — só com tudo validado
```

## ⚡ Ação imediata (próximas 3 tasks)

1. **Task 6.1** — Git remote + push (5 min, elimina risco de perda)
2. **Task 6.2** — `.env.example` + README (30 min, onboard rápido)
3. **Task 6.3** — Trading Loop async (1-2h, faz o framework FUNCIONAR)

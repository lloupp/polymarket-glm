# Sprint 13 — Position Tracker + P&L Mark-to-Market

## Objetivo
Integrar PortfolioTracker no trading loop com mark-to-market contínuo e persistência.

## Contexto
PortfolioTracker existe mas: (1) não é integrado ao loop principal, (2) sem persistência, (3) unrealized_pnl assume posições "Yes", (4) sem histórico de equity curve.

## Escopo
- Integrar PortfolioTracker no TradingLoop/Engine
- Persistir snapshots de P&L no DB
- Corrigir unrealized_pnl para posições "No"
- Expor P&L via CLI (`pglm status`) e Telegram
- Adicionar tabela `portfolio_snapshots` no DB

## Fora de escopo
- Gráficos de equity curve (Sprint 16)
- Live trading integration

## Arquivos provavelmente afetados
- `polymarket_glm/execution/portfolio_tracker.py`
- `polymarket_glm/engine/trading_loop.py`
- `polymarket_glm/storage/database.py`
- `polymarket_glm/interface/cli.py`
- `polymarket_glm/ops/telegram_bot.py`
- `tests/test_portfolio_tracker.py`
- `tests/test_database.py`

## Critérios de aceite
1. P&L atualizado a cada iteração do loop
2. Snapshots persistidos em DB a cada iteração
3. Posições "No" calculadas corretamente
4. `pglm status` mostra P&L atual
5. Telegram responde /pnl com P&L atual
6. 534+ testes passam

## Testes obrigatórios
- `test_portfolio_persistence_after_iteration`
- `test_pnl_updated_per_iteration`
- `test_pnl_no_position_correct`
- `test_pnl_cli_output`

## Riscos
- Mudança em schema do DB (adicionar tabela)
- Performance: salvar snapshot a cada iteração pode ser custoso

## Checklist
- [ ] Adicionar tabela portfolio_snapshots no DB
- [ ] Integrar PortfolioTracker no loop
- [ ] Corrigir unrealized_pnl para "No"
- [ ] Expor via CLI
- [ ] Expor via Telegram
- [ ] Adicionar testes
- [ ] Rodar pytest -q
- [ ] Commit e push

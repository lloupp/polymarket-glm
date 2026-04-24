# Sprint 14 — Settlement Tracker + Win/Loss

## Objetivo
Integrar SettlementTracker no loop, calcular win rate e settlement P&L com persistência.

## Contexto
SettlementTracker existe mas: (1) não integrado ao loop, (2) sem persistência, (3) settlement price hardcode 1.0/0.0, (4) _settled_markets cresce sem limite.

## Escopo
- Integrar SettlementTracker no loop
- Persistir settlements no DB
- Calcular win rate e P&L de settlement
- Pruning de _settled_markets
- Expor via CLI e Telegram

## Fora de escopo
- Partial settlement
- Multi-outcome markets

## Arquivos provavelmente afetados
- `polymarket_glm/execution/settlement_tracker.py`
- `polymarket_glm/engine/trading_loop.py`
- `polymarket_glm/storage/database.py`
- `polymarket_glm/interface/cli.py`
- `tests/test_settlement_tracker.py`

## Critérios de aceite
1. Settlements detectados automaticamente no loop
2. Win/Loss trackeado por posição
3. Persistido em DB
4. _settled_markets com pruning
5. 534+ testes passam

## Testes obrigatórios
- `test_auto_settlement_in_loop`
- `test_win_loss_tracking`
- `test_settlement_persistence`
- `test_settled_markets_pruning`

## Riscos
- Gamma API pode não reportar resolução imediatamente
- Race condition entre settlement e nova trade no mesmo mercado

## Checklist
- [ ] Integrar no loop
- [ ] Persistir settlements
- [ ] Calcular win rate
- [ ] Adicionar pruning
- [ ] Expor via CLI
- [ ] Adicionar testes
- [ ] Rodar pytest -q
- [ ] Commit e push

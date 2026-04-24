# Deduplicação — Unificar TradingEngine e TradingLoop

## Objetivo
Eliminar orquestração duplicada entre `engine/__init__.py::TradingEngine` e `engine/trading_loop.py::TradingLoop`.

## Contexto
Ambas as classes orquestram o mesmo fluxo: scan → estimate → signal → risk → execute → settle. Manter duas cópias da lógica significa mudanças devem ser sincronizadas manualmente.

## Escopo
- Escolher uma classe como orquestrador principal
- Migrar funcionalidade única da outra
- Atualizar scripts/run_bot.py e CLI
- Remover a classe redundante

## Fora de escopo
- Mudar a arquitetura geral do pipeline
- Adicionar novas features

## Arquivos provavelmente afetados
- `polymarket_glm/engine/__init__.py`
- `polymarket_glm/engine/trading_loop.py`
- `scripts/run_bot.py`
- `polymarket_glm/interface/cli.py`
- `tests/test_engine.py`
- `tests/test_trading_loop.py`

## Critérios de aceite
1. Uma única classe de orquestração
2. Scripts e CLI usam a classe correta
3. 534+ testes passam
4. Funcionalidade de run_bot.py preservada

## Testes obrigatórios
- pytest -q
- testar run_bot.py manualmente

## Riscos
- Refatoração grande — fazer em branch separada
- Risco de regressão em integração

## Checklist
- [ ] Analisar diferenças entre TradingEngine e TradingLoop
- [ ] Escolher classe principal
- [ ] Migrar funcionalidade
- [ ] Atualizar callers
- [ ] Rodar pytest -q
- [ ] Testar run_bot.py
- [ ] Commit e push

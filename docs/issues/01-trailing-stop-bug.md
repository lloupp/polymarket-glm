# Bug — Trailing Stop Nunca Dispara

## Objetivo
Corrigir bug onde `high_water_mark` nunca é atualizado, fazendo trailing stop ser dead code.

## Contexto
`barriers.py` `TripleBarrier.check()` e `position_executor.py` `PositionExecutor.check_barriers()` não atualizam `high_water_mark` quando o preço sobe. O valor fica em 0 (default), e o trailing stop nunca dispara porque a condição `current_price < high_water_mark * (1 - trail_pct)` é sempre False quando high_water_mark=0.

## Escopo
- Corrigir `TripleBarrier.check()` para atualizar `high_water_mark`
- Corrigir `PositionExecutor.check_barriers()` para atualizar `high_water_mark`
- Adicionar testes específicos para trailing stop

## Fora de escopo
- Trailing stop para posições short (futuro)
- Mudança na lógica de ativação do trailing stop

## Arquivos provavelmente afetados
- `polymarket_glm/execution/barriers.py`
- `polymarket_glm/execution/position_executor.py`
- `tests/test_barriers.py`
- `tests/test_controller_executor.py`

## Critérios de aceite
1. `high_water_mark` é atualizado em cada `check()` quando `current_price > high_water_mark`
2. Trailing stop dispara quando `current_price` cai X% do `high_water_mark`
3. Trailing stop NÃO dispara enquanto preço só sobe
4. Todos os 534+ testes continuam passando

## Testes obrigatórios
- `test_trailing_stop_activates_after_high_water_mark`
- `test_trailing_stop_follows_high_water_mark`
- `test_trailing_stop_does_not_trigger_while_price_rising`

## Riscos
- Mudança em lógica de barriers pode afetar posições abertas no paper trading atual
- Resetar high_water_mark=0 na inicialização pode causar trigger imediato se preço < entry

## Checklist
- [ ] Corrigir barriers.py TripleBarrier.check()
- [ ] Corrigir position_executor.py PositionExecutor.check_barriers()
- [ ] Adicionar testes novos
- [ ] Rodar pytest -q
- [ ] Commit e push

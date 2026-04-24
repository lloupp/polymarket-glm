# Deduplicação — Resolver EnsembleEstimator Name Collision

## Objetivo
Eliminar duplicação de nome entre `ensemble.py::EnsembleEstimator` e `ensemble_estimator.py::EnsembleEstimator`.

## Contexto
Duas classes diferentes com mesmo nome no mesmo package. `ensemble.py` faz multi-template paraphrase; `ensemble_estimator.py` faz weighted combination de múltiplos estimators. Importar `from polymarket_glm.strategy import EnsembleEstimator` é ambíguo.

## Escopo
- Renomear ou remover uma das classes
- Atualizar todos os imports
- Decidir qual classe manter

## Fora de escopo
- Refatorar lógica interna das classes

## Arquivos provavelmente afetados
- `polymarket_glm/strategy/ensemble.py`
- `polymarket_glm/strategy/ensemble_estimator.py`
- `polymarket_glm/engine/trading_loop.py`
- `polymarket_glm/engine/__init__.py`
- Quaisquer outros imports

## Critérios de aceite
1. Nenhuma classe duplicada no package strategy
2. Todos os imports funcionam
3. 534+ testes passam

## Testes obrigatórios
- pytest -q (full suite)

## Riscos
- Breaking changes em imports externos
- Usuários do package podem depender do nome antigo

## Checklist
- [ ] Analisar usos de cada classe
- [ ] Decidir qual renomear
- [ ] Atualizar imports
- [ ] Rodar pytest -q
- [ ] Commit e push

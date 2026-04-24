# Bug — Ensemble Paraphrase Prompts Ignorados

## Objetivo
Corrigir ensemble.py para usar paraphrase prompts customizados em vez do prompt default.

## Contexto
`_estimate_with_template()` constrói um paraphrase prompt customizado mas chama `self._router.estimate(modified_market)` que ignora o prompt customizado e usa o superforecaster prompt default. Resultado: N chamadas LLM produzem resultados idênticos, desperdiçando API calls e derrotando o propósito de ensembling.

## Escopo
- Refatorar `LLMRouter.estimate()` para aceitar prompt custom
- Corrigir `EnsembleEstimator._estimate_with_template()` para passar paraphrase prompt
- Adicionar testes

## Fora de escopo
- Adicionar novos templates de paraphrase
- Mudança no modelo de agregação

## Arquivos provavelmente afetados
- `polymarket_glm/strategy/ensemble.py`
- `polymarket_glm/strategy/llm_router.py`
- `tests/test_ensemble.py`
- `tests/test_llm_router.py`

## Critérios de aceite
1. Templates não-default usam paraphrase prompt no LLMRouter
2. N calls com templates diferentes produzem resultados diferentes (quando LLM responde)
3. Template default continua usando superforecaster prompt
4. Todos os 534+ testes continuam passando

## Testes obrigatórios
- `test_ensemble_uses_paraphrase_prompt_for_non_default_template`
- `test_ensemble_default_template_uses_superforecaster_prompt`
- `test_llm_router_accepts_custom_prompt`

## Riscos
- Necessário refatorar assinatura de LLMRouter.estimate() — breaking change para callers
- Rate limiting: N calls LLM podem exceder limites

## Checklist
- [ ] Refatorar LLMRouter.estimate() para aceitar prompt custom
- [ ] Corrigir EnsembleEstimator._estimate_with_template()
- [ ] Adicionar testes
- [ ] Rodar pytest -q
- [ ] Commit e push

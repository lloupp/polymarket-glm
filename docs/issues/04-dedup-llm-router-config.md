# Deduplicação — Resolver LLMRouterConfig Name Collision

## Objetivo
Unificar LLMRouterConfig em um único local.

## Contexto
`config.py` tem LLMRouterConfig com campos flat (groq_api_key, gemini_api_key, etc). `llm_router.py` tem LLMRouterConfig com `providers: list[LLMProviderConfig]`. Estruturas diferentes, mesmo nome. Config do llm_router.py é a usada em runtime.

## Escopo
- Decidir qual LLMRouterConfig é a correta
- Remover ou renomear a outra
- Atualizar Settings se necessário
- Atualizar .env.example

## Fora de escopo
- Mudar a estrutura de providers

## Arquivos provavelmente afetados
- `polymarket_glm/config.py`
- `polymarket_glm/strategy/llm_router.py`
- `.env.example`
- `tests/test_config.py`
- `tests/test_llm_router.py`

## Critérios de aceite
1. Um único LLMRouterConfig
2. Settings usa o correto
3. .env.example documentado corretamente
4. 534+ testes passam

## Testes obrigatórios
- pytest -q

## Riscos
- Breaking change em config loading
- .env pode precisar ser atualizado

## Checklist
- [ ] Analisar qual config é usada em runtime
- [ ] Remover/renomear a duplicada
- [ ] Atualizar Settings
- [ ] Atualizar .env.example
- [ ] Rodar pytest -q
- [ ] Commit e push

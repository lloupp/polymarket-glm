# Sprint 11-12 — Consolidar LLM Multi-Provider Router + Superforecaster Prompt

## Objetivo
Marcar Sprint 11-12 como COMPLETO e atualizar documentação.

## Contexto
LLMRouter com Groq+Gemini já funciona, superforecaster prompt + CoT validation implementados. NEXT_STEPS.md marca como pendente mas código já existe e está testado (30 testes no llm_router). Bug do ensemble paraphrase (Issue 2) deve ser corrigido separadamente.

## Escopo
- Atualizar NEXT_STEPS.md
- Atualizar README.md status table
- Split llm_router.py (738 linhas) em módulos menores (opcional)
- Corrigir LLMRouterConfig name collision (Issue 4)

## Fora de escopo
- Novos providers
- Novos prompts

## Arquivos provavelmente afetados
- `NEXT_STEPS.md`
- `README.md`
- `polymarket_glm/strategy/llm_router.py` (opcional: split)

## Critérios de aceite
1. NEXT_STEPS.md reflete Sprint 11-12 como completo
2. README.md status table atualizada
3. Bug do ensemble corrigido (dependência da Issue 2)
4. 534+ testes passam

## Testes obrigatórios
- pytest -q

## Riscos
- Nenhum risco técnico

## Checklist
- [ ] Atualizar NEXT_STEPS.md
- [ ] Atualizar README.md
- [ ] Garantir que Issue 2 (ensemble bug) está resolvida
- [ ] Garantir que Issue 4 (LLMRouterConfig) está resolvida
- [ ] Rodar pytest -q
- [ ] Commit e push

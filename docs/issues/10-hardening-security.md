# Hardening de Segurança para Live Trading Bloqueado

## Objetivo
Garantir que live trading não pode ser ativado acidentalmente e que o sistema é seguro em paper mode.

## Contexto
Múltiplos gaps: Telegram sem auth, web dashboard sem auth, env var names incorretos nos docs, defaults de risco divergentes, service.py gera env var errado.

## Escopo
- Telegram bot: whitelist de chat_ids
- Web dashboard: basic auth ou Tailscale-only
- Corrigir env var PGLM_MODE → PGLM_EXECUTION_MODE nos docs e service.py
- Sincronizar risk defaults docs ↔ código
- Atualizar .env.example com todas as vars reais
- Verificar file permissions do .env (chmod 600)
- Corrigir CLOB_API_SETUP.md (claim incorreta sobre .gitignore)

## Fora de escopo
- Implementar live trading
- Adicionar HTTPS ao web dashboard (usar Tailscale)

## Arquivos provavelmente afetados
- `polymarket_glm/ops/telegram_bot.py`
- `polymarket_glm/ops/web_dashboard.py`
- `polymarket_glm/ops/service.py`
- `.env.example`
- `README.md`
- `docs/CLOB_API_SETUP.md`
- `docs/GO_LIVE_RUNBOOK.md`
- `.gitignore`

## Critérios de aceite
1. Telegram bot só aceita comandos de chat_ids whitelisted
2. Web dashboard requer auth
3. service.py gera PGLM_EXECUTION_MODE
4. .env.example tem todas as vars e nomes corretos
5. Risk defaults idênticos docs ↔ código
6. .env tem chmod 600
7. CLOB_API_SETUP.md corrigido
8. .gitignore limpo (sem duplicatas, com entradas faltantes)
9. 534+ testes passam

## Testes obrigatórios
- `test_telegram_auth_whitelist`
- `test_dashboard_auth_required`
- `test_service_env_var_names`
- `test_env_example_complete`

## Riscos
- Breaking changes em config podem afetar deploy existente
- Whitelist do Telegram pode bloquear comandos legítimos se mal configurada

## Checklist
- [ ] Adicionar whitelist Telegram
- [ ] Adicionar auth web dashboard
- [ ] Corrigir service.py env vars
- [ ] Atualizar .env.example
- [ ] Sincronizar risk defaults
- [ ] Corrigir CLOB_API_SETUP.md
- [ ] Limpar .gitignore
- [ ] Verificar .env permissions
- [ ] Adicionar testes
- [ ] Rodar pytest -q
- [ ] Commit e push

# Sprint 16 — Dashboard Web

## Objetivo
Dashboard web funcional com autenticação e suporte async.

## Contexto
web_dashboard.py usa stdlib http.server (blocking, single-threaded), sem autenticação, sem HTTPS, com risco de XSS.

## Escopo
- Migrar para FastAPI ou aiohttp
- Adicionar basic auth ou Tailscale-only access
- JSON API endpoints
- HTML frontend simples
- Execução async compatível com trading loop

## Fora de escopo
- React/Vue frontend
- User management
- Database UI

## Arquivos provavelmente afetados
- `polymarket_glm/ops/web_dashboard.py`
- `pyproject.toml` (nova dependência)
- `tests/test_web_dashboard.py`

## Critérios de aceite
1. Servidor async (não bloqueia trading loop)
2. Autenticação (basic auth ou Tailscale)
3. JSON API: /api/status, /api/positions, /api/pnl, /api/health
4. HTML frontend mínimo
5. 534+ testes passam

## Testes obrigatórios
- `test_dashboard_requires_auth`
- `test_dashboard_api_endpoints`
- `test_dashboard_concurrent_requests`
- `test_dashboard_does_not_block_loop`

## Riscos
- Adicionar dependência (FastAPI/aiohttp)
- Complexidade de deploy aumenta
- Precisa de porta dedicada

## Checklist
- [ ] Escolher framework (FastAPI recomendado)
- [ ] Implementar API endpoints
- [ ] Adicionar auth
- [ ] HTML frontend básico
- [ ] Atualizar pyproject.toml
- [ ] Adicionar testes
- [ ] Rodar pytest -q
- [ ] Commit e push

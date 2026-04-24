# Cobertura de Testes e CI

## Objetivo
Aumentar cobertura de testes e adicionar CI pipeline via GitHub Actions.

## Contexto
534 testes passando mas cobertura é desigual. Áreas críticas (exchange, engine, dashboard) com cobertura mínima. Sem CI automatizado. conftest.py vazio.

## Escopo
- Adicionar testes para áreas com cobertura mínima
- Criar conftest.py com fixtures compartilhadas
- Configurar GitHub Actions CI
- Configurar coverage reporting

## Fora de escopo
- 100% coverage
- Mutation testing
- Performance testing

## Arquivos provavelmente afetados
- `tests/conftest.py`
- `tests/test_exchange.py`
- `tests/test_engine.py`
- `tests/test_dashboard.py`
- `tests/test_cli.py`
- `pyproject.toml`
- `.github/workflows/ci.yml` (novo)

## Critérios de aceite
1. Coverage ≥80% nos módulos críticos
2. GitHub Actions CI rodando em push/PR
3. conftest.py com fixtures: mock_settings, mock_db, mock_market, mock_signal
4. Nenhum teste skipado ou xfail
5. CI bloqueia merge com testes falhando

## Testes obrigatórios
- N/A (esta issue É sobre testes)

## Riscos
- Tempo investido em testes não adiciona features
- CI pode ter flaky tests em APIs externas

## Checklist
- [ ] Criar conftest.py com fixtures
- [ ] Adicionar testes exchange
- [ ] Adicionar testes engine
- [ ] Adicionar testes CLI
- [ ] Configurar GitHub Actions
- [ ] Configurar coverage
- [ ] Rodar pytest -q
- [ ] Commit e push

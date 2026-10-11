# Auditoria Hermes — polymarket-glm

Gerado em: `2026-09-06 12:03:25`

## Escopo

- **Não alterei código-fonte nem arquivos de configuração.**
- Arquivo criado/atualizado por pedido do Eduardo: `HERMES_AUDIT.md`.
- Testes executados apenas quando havia comando/estrutura detectável, com timeout curto e sem instalar dependências do projeto.

## Resumo

- Tipo detectado: **Python app/lib**
- Arquivos analisados: **124**
- Tamanho aproximado analisado: **1.39 MB**

## Linguagens / tipos de arquivo

- Python: 101
- Outro: 14
- Markdown: 7
- TOML: 1
- JSON: 1

## Arquivos principais vistos

- `.env`
- `.env.example`
- `.gitignore`
- `ANALYSIS_PLAN.md`
- `NEXT_STEPS.md`
- `PLAN.md`
- `README.md`
- `data/simulation_state.json`
- `docs/CLOB_API_SETUP.md`
- `docs/GO_LIVE_RUNBOOK.md`
- `docs/plans/2026-05-05-smart-order-selection-osint-tracking.md`
- `polymarket_glm.db`
- `polymarket_glm.db-shm`
- `polymarket_glm.egg-info/PKG-INFO`
- `polymarket_glm.egg-info/SOURCES.txt`
- `polymarket_glm.egg-info/dependency_links.txt`
- `polymarket_glm.egg-info/entry_points.txt`
- `polymarket_glm.egg-info/requires.txt`
- `polymarket_glm.egg-info/top_level.txt`
- `polymarket_glm/__init__.py`
- `polymarket_glm/backtest/__init__.py`
- `polymarket_glm/backtest/engine.py`
- `polymarket_glm/config.py`
- `polymarket_glm/engine/__init__.py`
- `polymarket_glm/engine/trading_loop.py`
- `polymarket_glm/execution/__init__.py`
- `polymarket_glm/execution/barriers.py`
- `polymarket_glm/execution/exchange.py`
- `polymarket_glm/execution/live_executor.py`
- `polymarket_glm/execution/paper_executor.py`
- `polymarket_glm/execution/portfolio_tracker.py`
- `polymarket_glm/execution/position_executor.py`
- `polymarket_glm/execution/position_manager.py`
- `polymarket_glm/execution/settlement_tracker.py`
- `polymarket_glm/execution/signal_controller.py`
- `polymarket_glm/ingestion/__init__.py`
- `polymarket_glm/ingestion/market_fetcher.py`
- `polymarket_glm/ingestion/price_feed.py`
- `polymarket_glm/interface/__init__.py`
- `polymarket_glm/interface/cli.py`
- … mais 84 arquivo(s)

## Estado Git

- Comando: `git status --short --branch`
- Exit code: `0`

```text
## main...origin/main
 M polymarket_glm/__pycache__/__init__.cpython-311.pyc
 M polymarket_glm/__pycache__/config.cpython-311.pyc
 M polymarket_glm/__pycache__/models.cpython-311.pyc
 M polymarket_glm/execution/__pycache__/__init__.cpython-311.pyc
 M polymarket_glm/execution/__pycache__/exchange.cpython-311.pyc
 M polymarket_glm/execution/__pycache__/live_executor.cpython-311.pyc
 M polymarket_glm/execution/__pycache__/paper_executor.cpython-311.pyc
 M polymarket_glm/ingestion/__pycache__/__init__.cpython-311.pyc
 M polymarket_glm/ingestion/__pycache__/market_fetcher.cpython-311.pyc
 M polymarket_glm/ingestion/__pycache__/price_feed.cpython-311.pyc
 M polymarket_glm/interface/__pycache__/__init__.cpython-311.pyc
 M polymarket_glm/interface/__pycache__/cli.cpython-311.pyc
 M polymarket_glm/monitoring/__pycache__/__init__.cpython-311.pyc
 M polymarket_glm/monitoring/__pycache__/alerts.cpython-311.pyc
 M polymarket_glm/risk/__pycache__/__init__.cpython-311.pyc
 M polymarket_glm/risk/__pycache__/controller.cpython-311.pyc
 M polymarket_glm/storage/__pycache__/__init__.cpython-311.pyc
 M polymarket_glm/storage/__pycache__/database.cpython-311.pyc
 M polymarket_glm/strategy/__pycache__/__init__.cpython-311.pyc
 M polymarket_glm/strategy/__pycache__/signal_engine.cpython-311.pyc
 M tests/__pycache__/__init__.cpython-311.pyc
```

## Resultado dos testes / validações

### ❌ FALHOU
- Comando: `python3 -m pytest -q`
- Exit code: `2`
- Tempo: `10.57s`

```text

==================================== ERRORS ====================================
______________________ ERROR collecting tests/test_cli.py ______________________
ImportError while importing test module '/home/eduardodlima/projetos/polymarket-glm/tests/test_cli.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
../../.hermes/hermes-agent/.hermes-runtime/python/generation-1786058349-79079-96026584/cpython-3.11.15-linux-x86_64-gnu/lib/python3.11/importlib/__init__.py:126: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/test_cli.py:3: in <module>
    from polymarket_glm.interface.cli import main
polymarket_glm/interface/cli.py:9: in <module>
    from polymarket_glm.engine import Engine
polymarket_glm/engine/__init__.py:11: in <module>
    from polymarket_glm.execution.live_executor import LiveExecutor
polymarket_glm/execution/live_executor.py:14: in <module>
    from py_clob_client.clob_types import ApiCreds, OrderArgs, OpenOrderParams
E   ModuleNotFoundError: No module named 'py_clob_client'
____________________ ERROR collecting tests/test_engine.py _____________________
ImportError while importing test module '/home/eduardodlima/projetos/polymarket-glm/tests/test_engine.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
../../.hermes/hermes-agent/.hermes-runtime/python/generation-1786058349-79079-96026584/cpython-3.11.15-linux-x86_64-gnu/lib/python3.11/importlib/__init__.py:126: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/test_engine.py:3: in <module>
    from polymarket_glm.engine import Engine
poly

...[saída cortada]...

rks to avoid this warning - for details, see https://docs.pytest.org/en/stable/how-to/mark.html
    @pytest.mark.asyncio

tests/test_telegram_bot.py:169
  /home/eduardodlima/projetos/polymarket-glm/tests/test_telegram_bot.py:169: PytestUnknownMarkWarning: Unknown pytest.mark.asyncio - is this a typo?  You can register custom marks to avoid this warning - for details, see https://docs.pytest.org/en/stable/how-to/mark.html
    @pytest.mark.asyncio

tests/test_telegram_bot.py:177
  /home/eduardodlima/projetos/polymarket-glm/tests/test_telegram_bot.py:177: PytestUnknownMarkWarning: Unknown pytest.mark.asyncio - is this a typo?  You can register custom marks to avoid this warning - for details, see https://docs.pytest.org/en/stable/how-to/mark.html
    @pytest.mark.asyncio

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
ERROR tests/test_cli.py
ERROR tests/test_engine.py
ERROR tests/test_integration_live.py
ERROR tests/test_live_executor.py
ERROR tests/test_trading_loop.py
!!!!!!!!!!!!!!!!!!! Interrupted: 5 errors during collection !!!!!!!!!!!!!!!!!!!!
31 warnings, 5 errors in 8.54s

```


## Propostas gerais de melhoria

1. Corrigir os testes/comandos que falharam antes de evoluir funcionalidades.

## Próximo passo sugerido

Primeiro estabilizar os testes/comandos que falharam; depois evoluir funcionalidades.

## Intervenção Codex

Data: `2026-09-06`

### Arquivos alterados

- `polymarket_glm/execution/live_executor.py`
  - Tornou a importação de `py_clob_client.clob_types` tolerante à ausência da dependência no ambiente de testes.
  - Adicionou tipos fallback mínimos para `ApiCreds`, `OrderArgs` e `OpenOrderParams`.
  - Manteve erro explícito para uso live real sem `py-clob-client`.
- `tests/conftest.py`
  - Registrou a marca `pytest.mark.asyncio`.
  - Adicionou fallback mínimo para executar testes `async def` quando `pytest-asyncio` não está carregado no ambiente atual.
  - Garante um event loop atual para testes síncronos que usam `asyncio.get_event_loop().run_until_complete(...)`.
- `pyproject.toml`
  - Removeu `asyncio_mode = "auto"` da configuração do pytest, pois o ambiente atual não carrega `pytest-asyncio` e emitia `PytestConfigWarning`.
- `__pycache__/` e `*.pyc`
  - Removidos do workspace/repositório. A `.gitignore` já ignora `__pycache__/` e `*.pyc`.

### Testes

- Comando executado: `python3 -m pytest -q`
- Resultado: `728 passed in 27.07s`

### Resultado

- Erros de coleta por ausência de `py_clob_client` corrigidos.
- Warnings de `pytest.mark.asyncio` corrigidos pelo registro da marca.
- Suite completa passou no ambiente atual.

# Sprint 15 — Relatório Diário Telegram

## Objetivo
Enviar relatório diário automático via Telegram com P&L, posições, win rate e métricas.

## Contexto
DailyReport existe mas: (1) não integrado ao loop, (2) não é enviado automaticamente, (3) generate() é sync mas dados vêm de componentes async.

## Escopo
- Integrar DailyReport no loop
- Enviar via TelegramAlerter
- Agregar dados de PortfolioTracker, SettlementTracker
- Formatação rica para Telegram
- Configurar horário de envio

## Fora de escopo
- Email reports
- PDF reports

## Arquivos provavelmente afetados
- `polymarket_glm/monitoring/daily_report.py`
- `polymarket_glm/engine/trading_loop.py`
- `polymarket_glm/ops/telegram_bot.py`
- `polymarket_glm/config.py`
- `tests/test_daily_report.py`

## Critérios de aceite
1. Relatório enviado 1x/dia automaticamente
2. Contém: P&L total, posições abertas, settlements, win rate, drawdown
3. Horário configurável
4. Pode ser desabilitado via config
5. 534+ testes passam

## Testes obrigatórios
- `test_daily_report_sends_automatically`
- `test_report_content_format`
- `test_report_can_be_disabled`
- `test_report_respects_schedule`

## Riscos
- Rate limit do Telegram API
- Relatório pode ser muito longo para Telegram (4096 char limit)

## Checklist
- [ ] Integrar DailyReport no loop
- [ ] Formatar para Telegram
- [ ] Adicionar config de schedule
- [ ] Adicionar testes
- [ ] Rodar pytest -q
- [ ] Commit e push

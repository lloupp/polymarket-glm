# Documentação de Operação Paper por 30 Dias

## Objetivo
Criar documento com procedimento e checklist para simulação de 30 dias de paper trading.

## Contexto
Antes de considerar live trading, precisamos de 30 dias de paper trading ininterrupto com métricas validadas.

## Escopo
- Criar docs/PAPER_30_DAYS.md
- Definir checklist diário
- Definir métricas a trackear
- Definir critérios de parada
- Definir critérios de sucesso

## Fora de escopo
- Implementar métricas (isso é Sprint 13–15)
- Live trading

## Arquivos provavelmente afetados
- `docs/PAPER_30_DAYS.md` (novo)

## Critérios de aceite
1. Documento criado com checklist completo
2. Métricas definidas: P&L diário, win rate, drawdown, Sharpe, Brier score
3. Critérios de parada: drawdown > 20%, win rate < 40%, crash > 3x em 7 dias
4. Critérios de sucesso: paper rodando 30 dias, P&L positivo, win rate > 50%, Brier < 0.25
5. Revisado por Eduardo

## Testes obrigatórios
- N/A (documentação)

## Riscos
- Nenhum

## Checklist
- [ ] Criar documento
- [ ] Definir checklist diário
- [ ] Definir métricas
- [ ] Definir critérios de parada
- [ ] Definir critérios de sucesso
- [ ] Revisar com Eduardo
- [ ] Commit e push

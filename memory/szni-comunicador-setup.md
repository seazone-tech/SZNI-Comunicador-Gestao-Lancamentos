---
name: szni-comunicador-setup
description: Setup do agente Hermes SZNI-Comunicador-Gestao-Lancamentos
metadata:
  type: project
---

# SZNI-Comunicador-Gestao-Lancamentos

## Dono
Bianca Carina Valente (Bica) — Especialista de gestao de lancamentos

## Pasta do projeto
SZNI repo: `C:\Users\compu\.claude\projects\agente Bica\` — so docs e memorias, scripts removidos.

## Plataforma
- **Hermes v0.17.0** (WSL2 Ubuntu + tmux)
- Gateway: tmux session `hermes-gateway`
- **Todos os scripts rodam de `~/.hermes/scripts/`**
- Memory copiada pra `~/.hermes/memory/`

## Scripts — UNICO local
```
~/.hermes/scripts/ (WSL: /home/compu/.hermes/scripts/)
  cronograma-briefing.py    <- daily_briefing (briefing 8h)
  cobrar_updates.py           <- cobrar updates (10h)
  monitor_updates.py          <- monitor replies (9h-18h)
  fechamento_diario.py        <- relatorio fechamento (17h30)
  aprovar_changes.py          <- aprovacao sob demanda
  .env                        <- tokens e variaveis
  .briefing_posted          <- estado threads (limpar pra retestar)
```

## REGRA: Scripts versionados no SZNI repo

Os scripts foram removidos do SZNI repo (Commit pendente: 6 arquivos deletados).
Fluxo atual: editar no Hermes diretamente.
Se precisar versionar: criar scripts novos no SZNI repo e syncar pro Hermes.

## Cron jobs no Hermes
| Cron ID | Script | Quando |
|---|---|---|
| 3580392bc37f | cronograma-briefing.py | Seg-Sex 08h |
| 5ef5a4385641 | cobrar_updates.py | Seg-Sex 10h |
| 40266f26cd94 | monitor_updates.py | Seg-Sex 9h-18h |
| a73f113395f8 | fechamento_diario.py | Seg-Sex 17h30 |

Todos com workdir=/home/compu/.hermes/scripts.

## Canal Slack atual
C0BE0QE9E79 (canal oficial do agente)
Bot adicionado ao canal manualmente.

## Como rodar manualmente
Usar o agente do Slack ou:
python3 /home/compu/.hermes/scripts/cronograma-briefing.py
python3 /home/compu/.hermes/scripts/cobrar_updates.py

## Regras de filtragem do briefing
1. Status NAO esta em STATUS_DONE_VALUES (Concluida, Concluida, Cancelada, Cancelado)
2. "Data de Inicio Planejada" NAO esta em branco
3. "Data de Inicio Planejada" <= hoje
4. Se "Dependencia" tem valor -> a tarefa referenciada PRECISA estar em STATUS_DONE_VALUES

## Organizacao do canal — arquitetura thread-unique
- Uma tarefa = uma thread. Postada uma vez, nunca se repete.
- Atualizacoes ficam na thread existente.
- Estado: ~/.hermes/scripts/.briefing_posted (key = sheet|task_name -> thread_ts)
- Tarefa concluida no canal: bot ou Bianca coloca check na thread -> skills ignoram
- Tarefa concluida no SmartSheet: status muda para DONE_VALUES -> removida do .briefing_posted

## Bugs ja corrigidos
- Scripts nao tinham if __name__ == "__main__": main() — adicionado
- Arquivo duplicado aprobar_changes.py (sem 'v') — removido
- apply_smartsheet_change em aprovar_changes.py tinha loop interno — refatorado

## Pasta antiga apagada
C:\Users\compu\.claude\projects\smartsheet\ — apagada.

## .env — variaveis em ~/.hermes/scripts/.env
SMARTSHEET_TOKEN=<seu_token>
SMARTSHEET_FOLDER_ID=<id_da_pasta>
SLACK_BOT_TOKEN=<seu_token>
SLACK_CHANNEL_ID=C0BE0QE9E79
BIANCA_USER_ID=U06093URWPR
STATUS_DONE_VALUES=Concluida,Concluida,Cancelada,Cancelado

## IDs de referencia
- Canal Slack oficial: C0BE0QE9E79
- Bianca: U06093URWPR

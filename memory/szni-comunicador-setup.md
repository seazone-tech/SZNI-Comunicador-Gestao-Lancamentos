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

## Canais Slack — CHANNEL_MAP
Cada empreendimento tem seu próprio canal. Configurado via CHANNEL_MAP no .env:
- [5921] Farol da Barra Spot → C0BE0QE9E79
- [12235] São Miguel dos Milagres → C0BEKBE1SUS
Bot adicionado manualmente em cada canal.

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
- Estado: ~/.hermes/scripts/.briefing_posted
  - Formato: `sheet|task|ts|channel|team`
  - Exemplo: `[12235] São Miguel dos Milagres|Prospectar admins|1783339291.969579|C0BEKBE1SUS|Jurídico`
- Tarefa concluida no canal: bot ou Bianca coloca check na thread -> skills ignoram
- Tarefa concluida no SmartSheet:
  1. `sync_done_tasks` roda PRIMEIRO e adiciona ✅ na thread automaticamente
  2. `main` remove do `.briefing_posted` (tarefa some do estado)
- Se thread foi apagada do Slack: `reactions_get` falha, thread é ignorada (não cobra)
- Troca de responsável: se "Time Responsável" mudar no SmartSheet, o script posta reply na thread avisando o novo time

## Regras de filtragem do briefing
1. `sync_done_tasks` roda primeiro: identifica tarefas done e adiciona ✅ automaticamente
2. Tasks pendentes (main loop):
   - Status NAO esta em STATUS_DONE_VALUES (Concluida, Concluída, Cancelada, Cancelado)
   - "Data de Inicio Planejada" NAO esta em branco
   - "Data de Inicio Planejada" <= hoje
   - Se "Dependencia" tem valor -> a tarefa referenciada PRECISA estar em STATUS_DONE_VALUES

## API key do hub-seazone
- Armazenada no Hermes via `hermes auth add`
- Não é no .env — é no auth.json do Hermes
- Formato: `sk-...` (nao confundir com fingerprint SHA256)
- Se o bot parar de responder com 401: a key pode ter sido perdida num restart
- Remedio: `hermes auth add --type api-key --api-key '<sk-...>' hub-seazone`
- Gateway restartou em 02/07 e a key estava faltando → corrigido com nova key

## Bugs ja corrigidos
- Scripts nao tinham if __name__ == "__main__": main() — adicionado
- cronograma-briefing.py: sync_done_tasks adiciona ✅ em tarefas done automaticamente (roda primeiro)
- cronograma-briefing.py: troca de time no SmartSheet posta reply avisando novo responsável
- cronograma-briefing.py: .briefing_posted agora salvo com 5 campos (sheet|task|ts|channel|team)
- cronograma-briefing.py: find_thread_by_task_name busca no histórico quando done task não está no estado
- cobrar_updates.py: SEMPRE usa SOMENTE .briefing_posted — fallback pro histórico removido
- fechamento_diario.py: SEMPRE usa SOMENTE .briefing_posted — fallback pro histórico removido
- Arquivo duplicado aprovar_changes.py (sem 'v') — removido
- TASK_BLOCK_RE regex exigia [[sheet]] mas São Miguel usa [sheet] simples — corrigido para aceitar ambos
- add_check_to_thread usava SLACK_CHANNEL_ID fixo — agora extrai canal do threads_map

## Pasta antiga apagada
C:\Users\compu\.claude\projects\smartsheet\ — apagada.

## .env — variaveis em ~/.hermes/scripts/.env
SMARTSHEET_TOKEN=<seu_token>
SMARTSHEET_FOLDER_ID=<id_da_pasta>
SLACK_BOT_TOKEN=<seu_token>
CHANNEL_MAP=[5921] Farol da Barra Spot:C0BE0QE9E79,[12235] São Miguel dos Milagres:C0BEKBE1SUS
SLACK_CHANNEL_ID=C0BE0QE9E79
BIANCA_USER_ID=U06093URWPR
STATUS_DONE_VALUES=Concluida,Concluida,Cancelada,Cancelado

## IDs de referencia
- Canal Farol da Barra Spot: C0BE0QE9E79
- Canal São Miguel dos Milagres: C0BEKBE1SUS
- Bianca: U06093URWPR

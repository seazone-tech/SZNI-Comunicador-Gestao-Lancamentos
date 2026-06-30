---
name: szni-comunicador-setup
description: Setup do agente Hermes SZNI-Comunicador-Gestao-Lancamentos
metadata:
  type: project
---

# SZNI-Comunicador-Gestao-Lancamentos

## Dono
Bianca Carina Valente (Bica) — Especialista de gestão de lançamentos

## Pasta do projeto
`C:\Users\compu\.claude\projects\agente Bica\`

## Plataforma
- **Hermes v0.17.0** (WSL2 Ubuntu + tmux)
- Gateway: tmux session `hermes-gateway`
- **Todas as automações via Hermes** (Windows Scheduler NÃO é mais usado)
- Slack: bot conectado ao canal #cronogramas-lancamentos

## Pastas dos scripts

```
agente Bica/                        ← projeto (aqui a gente edita)
  skill-briefing/scripts/
    daily_briefing.py
  skill-updates/scripts/
    cobrar_updates.py
  skill-monitor/scripts/
    monitor_updates.py
  skill-fechamento/scripts/
    fechamento_diario.py
    aprobar_changes.py

~/.hermes/scripts/                  ← O QUE O HERMES REALMENTE RODA (WSL: /home/compu/.hermes/scripts/)
  cronograma-briefing.py            ← cópia de daily_briefing.py
  cobrar_updates.py
  monitor_updates.py
  fechamento_diario.py
  aprobar_changes.py
  .env                              ← tokens e variáveis (SINCRONIZADO com as skills)
  .briefing_posted                  ← estado: tarefas já postadas
  .monitor_state                    ← estado: monitor
  .fechamento_state                ← estado: fechamento
  .aproval_processed               ← estado: aprovações já processadas
```

**REGRA:** Sempre que editar um script, sincronizar para `~/.hermes/scripts/`:
```bash
cp skill-briefing/scripts/daily_briefing.py ~/.hermes/scripts/cronograma-briefing.py
cp skill-updates/scripts/cobrar_updates.py ~/.hermes/scripts/
cp skill-monitor/scripts/monitor_updates.py ~/.hermes/scripts/
cp skill-fechamento/scripts/fechamento_diario.py ~/.hermes/scripts/
cp skill-fechamento/scripts/aprobar_changes.py ~/.hermes/scripts/
```

## Cron jobs no Hermes
| Cron ID | Script | Quando |
|---|---|---|
| 3580392bc37f | cronograma-briefing.py | Seg-Sex 08h |
| 5ef5a4385641 | cobrar_updates.py | Seg-Sex 10h |
| 40266f26cd94 | monitor_updates.py | Seg-Sex 9h–18h, a cada 1h |
| a73f113395f8 | fechamento_diario.py | Seg-Sex 17h30 |
| — | aprobar_changes.py | **Sob demanda** (manual) |

## Como rodar manualmente
| Frase | Executa |
|---|---|
| "roda o briefing" | `hermes cron run 3580392bc37f` |
| "cobra os updates" | `hermes cron run 5ef5a4385641` |
| "monitora as tarefas" | `hermes cron run 40266f26cd94` |
| "gera o fechamento" | `hermes cron run a73f113395f8` |
| "aprova as mudanças" | `python3 ~/.hermes/scripts/aprovar_changes.py` |

## Regras de filtragem do briefing
1. Status NÃO está em STATUS_DONE_VALUES (Concluída, Concluida, Cancelada, Cancelado)
2. "Data de Início Planejada" NÃO está em branco
3. "Data de Início Planejada" <= hoje
4. Se "Dependência" tem valor → a tarefa referenciada PRECISA estar em STATUS_DONE_VALUES

## Organização do canal — arquitetura thread-unique
- **Uma tarefa = uma thread.** Postada uma vez, nunca se repete.
- Atualizações ficam na thread existente.
- Estado: `~/.hermes/scripts/.briefing_posted` (key = sheet|task_name → thread_ts)
- **Tarefa concluída no canal:** bot ou Bianca coloca ✅ na thread → skills ignoram
- **Tarefa concluída no SmartSheet:** status muda para DONE_VALUES → removida do `.briefing_posted`
- ✅ de outra pessoa → não ignora (thread continua ativa)

## Skills — comportamento
- **Briefing**: posta tarefa nova (ainda não postada, sem ✅)
- **Updates**: cobra update em TODAS as threads abertas (sem filtro de data)
- **Monitor**: monitora TODAS as threads abertas (sem filtro de data)
- **Fechamento**: gera relatório de TODAS as threads abertas
- **Aprovar**: "aprova N" via DM → aplica mudanças no SmartSheet → auto-✅ na thread

## Briefing — lógica de postagem (evita duplicidade)
```
para cada tarefa pendente:
  se já existe no .briefing_posted:
    se thread tem ✅ → mantém no estado (evita repostar)
    se thread NÃO tem ✅ → mantém no estado (já foi postada)
  senão:
    posta nova thread no canal
    salva no .briefing_posted
 fim

ao final: limpa do .briefing_posted tarefas com status DONE no SmartSheet
```

## Updates — duas mensagens diferentes
- **Primeira vez** (ninguém respondeu na thread): mensagem completa com 3 perguntas
- **Follow-up** (já houve reply): mensagem curta pedindo feedback

## Auto-start gateway Hermes
- Batch: `C:\Users\compu\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\SZNI-Gateway-Start.bat`
- Executa WSL Ubuntu + tmux session `hermes-gateway`
- Verifica se tmux já tem sessão (protege contra duplicados)

## .env — variáveis em todos os scripts
```
SMARTSHEET_TOKEN=...
SMARTSHEET_FOLDER_ID=606189827895484
SLACK_BOT_TOKEN=...
SLACK_CHANNEL_ID=C0B9ECGDM51
BIANCA_USER_ID=U06093URWPR
STATUS_DONE_VALUES=Concluída,Concluida,Cancelada,Cancelado
```

## IDs de referência
- Canal Slack: #cronogramas-lancamentos (C0B9ECGDM51)
- Bianca: U06093URWPR

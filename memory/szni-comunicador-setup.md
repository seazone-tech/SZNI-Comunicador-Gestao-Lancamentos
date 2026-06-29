---
name: szni-comunicador-setup
description: Setup do agente Hermes SZNI-Comunicador-Gestao-Lancamentos
metadata:
  type: project
---

# SZNI-Comunicador-Gestao-Lancamentos

## Dono
Bianca Carina Valente (Bica) — Especialista de gestao de lancamentos

## Plataforma
- Hermes v0.17.0
- WSL2 Ubuntu + tmux
- Gateway: tmux session hermes-gateway
- Slack: bot conectado ao canal #cronogramas-lancamentos

## Pasta do projeto
`C:\Users\compu\.claude\projects\agente Bica\`

## Estrutura de skills
```
agente Bica/
  skill-briefing/scripts/  → daily_briefing.py
  skill-updates/scripts/   → cobrar_updates.py
  skill-monitor/scripts/   → monitor_updates.py
  .env                     → variáveis de ambiente (tokens)
  SOUL.md                  → persona do agente
  setup-env.py             → script para copiar .env
```

## Tarefas programadas (Hermes cron)
| ID | Nome | Quando | O que faz |
|---|---|---|---|
| 3580392bc37f | daily-briefing-cronogramas | Seg-Sex 08h | Envia resumo do SmartSheet no Slack |
| 5ef5a4385641 | cobrar-updates-diario | Seg-Sex 08h15 | Cobra updates nas threads do briefing |
| 40266f26cd94 | monitor-respostas-updates | Seg-Sex a cada 30min | Le respostas, manda DM se encontrar algo |

## Como pedir ao bot para correr manualmente
| Frase no Slack | Executa |
|---|---|
| "roda o briefing" | hermes cron run 3580392bc37f |
| "cobra os updates" | hermes cron run 5ef5a4385641 |
| "monitora as tarefas" | hermes cron run 40266f26cd94 |

## Inteligencia implementada (skill-monitor)
- A cada 30min verifica threads do briefing
- Detecta: atrasos, bloqueios, "não", links, perguntas
- Envia DM directa para Bianca (U06093URWPR) com link "Ver resposta"
- Estado: ~/.hermes/scripts/.monitor_state (evita duplicados)
- Filtra por data: só processa mensagens do dia actual

## Como o gateway arranca automaticamente
- Ficheiro batch em: `C:\Users\compu\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\SZNI-Gateway-Start.bat`
- Executa wsl Ubuntu + tmux session `hermes-gateway` com `start-gateway.sh`
- Tmux session criada como detached — não abre janela
- Protegido contra duplicados (verifica se tmux já tem sessão hermes-gateway antes de criar)

## Estado actual (2026-06-29)
- Gateway Hermes: rodando (PID 9332, tmux hermes-gateway)
- Briefing: funciona correctamente
- Cobrar updates: funciona, filtra por data (só dia actual)
- Monitor de respostas: activo, DM formatada com link directo "Ver resposta"
- Auto-start: ficheiro Startup criado (reinicia gateway ao ligar PC)
- Skills: .env copiado para skill-briefing/, skill-updates/, skill-monitor/
- Tarefas Windows SZNI-Briefing, SZNI-Updates, SZNI-Monitor criadas (Agendador de Tarefas)
- **scope missing_scope**: canais:read falta no Slack app — não bloqueia DM, mas sem isto não lista canais automaticamente

## Nova skill — Fechamento do dia (criado 2026-06-29)
| O que | Detalhe |
|---|---|
| Script | skill-fechamento/scripts/fechamento_diario.py |
| Cron Hermes | 30 17 * * 1-5 (seg-sex 17h30) — job ID a73f113395f8 |
| Cron Windows | SZNI-Fechamento — seg-sex 17h30 |
| Handler aprovação | skill-fechamento/scripts/aprovar_changes.py |
| Comando manual | /fechamento |
| Destino | DM directa pra Bianca (não canal) |
| Regras | Início se confirmou que começou; Fim se confirmou que terminou; Bloqueio = sem sugestão; Sem resposta = sem sugestão |
| Prioridade replies | Último wins |
| Aprovação | Linha por linha — "aprova 1", "aprova 2"... |

## Estrutura de pastas (2026-06-26)
```
agente Bica/
  .env
  SOUL.md
  memory/
    szni-comunicador-setup.md  → setup e estado
    feedback-comunicacao.md      → preferência de linguagem simples
  skill-briefing/scripts/
    daily_briefing.py          → cron 3580392bc37f
  skill-updates/scripts/
    cobrar_updates.py           → cron 5ef5a4385641
  skill-monitor/scripts/
    monitor_updates.py          → cron 40266f26cd94
  skill-fechamento/scripts/
    fechamento_diario.py         → cron a73f113395f8
    aprobar_changes.py          → detecta "aprova N" e aplica no SmartSheet
```

## O que fazer na segunda-feira
- Bianca quer adicionar novas skills ao agente
- Ideias mencionou: integrar com outros canais, mais automações de gestão de lançamentos
- Antes de continuar, rever o que o agente já faz e o que ainda falta
- Consultar Oráculo (MCP kb) sobre processos de lançamentos que podem ser automatizados

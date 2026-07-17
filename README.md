# SZNI-Comunicador-Gestao-Lancamentos

Assistente de gestão de lançamentos da Bianca. Roda no Slack (Hermes) e integra com SmartSheet: posta briefings por time, cobra updates, monitora respostas e aplica mudanças aprovadas no cronograma.

## O que ele faz (jobs no Hermes)

| Job (cron ID) | Script | Quando | O que faz |
|---|---|---|---|
| daily-briefing-cronogramas (`3580392bc37f`) | `cronograma-briefing.py` | Seg-Sex **08h30** | Lê cronogramas do SmartSheet e posta uma thread por tarefa pendente no canal do empreendimento |
| cobrar-updates-diario (`5ef5a4385641`) | `cobrar_updates.py` | Seg-Sex 10h | Cobra atualização nas threads abertas (1ª vez: 3 perguntas; follow-up curto; não duplica) |
| monitor-respostas-updates (`40266f26cd94`) | `monitor_updates.py` | Seg-Sex 9h-18h (por hora) | Lê respostas, detecta risco (atraso/bloqueio/dúvida) e avisa Bianca por DM |
| fechamento-diario (`a73f113395f8`) | `fechamento_diario.py` | Seg-Sex 17h30 | Classifica respostas do dia e envia relatório numerado por DM com sugestões de status |
| atualizar-tarefa-smartsheet (`b2e4f8a1c3d5`) | `atualizar_tarefa.py` | Seg-Sex 8h-19h (a cada 3 min) | Skill interativa: Bianca descreve na DM o que atualizar; bot acha a tarefa, confirma e aplica |
| szni-aprovador-dm (`e80bee0a5ce9`) | `szni-aprovador-dm.py` | Seg-Sex (por hora) | Detecta ✅ nas threads de fechamento da DM e aplica as mudanças no SmartSheet |
| — (sob demanda) | `aprovar_changes.py` | "aprova 1", "aprova 2"... | Aplica mudanças do relatório de fechamento e adiciona ✅ na thread |

Atalhos no chat do bot: `/briefing`, `/cobrar`, `/fechamento` (disparam os jobs na hora).

## Arquitetura (desde 16-17/07/2026)

- **Hermes profile `default`** em `~/.hermes/` (WSL2 Ubuntu) — o agente vizinho **Analista** tem profile próprio em `~/.hermes/profiles/analista/`
- **Gateway como serviço systemd** (`hermes-gateway`) — sobe com o WSL e reinicia se cair (antes era tmux manual)
- **Keep-alive**: `hermes-gateways-keepalive.vbs` na pasta Inicializar do Windows religa o WSL em 10s se cair
- Avisos "Gateway shutting down" em canais: **desativados** (`slack.gateway_restart_notification: false` no config.yaml)
- Modelo: GPT-5.4 via hub Seazone (`custom_providers` no config.yaml; key no `.env` de `~/.hermes/scripts/`)
- Logs: `journalctl --user -u hermes-gateway -f`

## Variáveis de ambiente (`~/.hermes/scripts/.env`)

| Variável | Descrição |
|---|---|
| `SMARTSHEET_TOKEN` | Token da API do SmartSheet |
| `SMARTSHEET_FOLDER_ID` | Pasta dos cronogramas ativos |
| `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` | Tokens do bot Comunicador |
| `OPENAI_API_KEY` | Key do hub Seazone (modelo do agente) |
| `CHANNEL_MAP` | `Nome Sheet:CHANNEL_ID,...` — canal de cada empreendimento |
| `SLACK_CHANNEL_ID` | Canal fallback |
| `BIANCA_USER_ID` | U06093URWPR |
| `STATUS_DONE_VALUES` | Status que encerram tarefa |

## Regras de negócio

### Briefing
1. `sync_done_tasks` roda primeiro: adiciona ✅ nas threads de tarefas concluídas no SmartSheet
2. Tarefa pendente entra se: status fora de `STATUS_DONE_VALUES` + Data de Início Planejada preenchida e <= hoje + dependência (se houver) concluída
3. **Uma tarefa = uma thread**, nunca repostada; estado em `.briefing_posted` (`sheet|task|ts|channel|team`)
4. Troca de "Time Responsável" no SmartSheet → reply na thread avisando o novo time

### Cobrança
- Sem resposta humana → mensagem completa (3 perguntas); com resposta → follow-up curto; já cobrou → pula

### Fechamento
- "concluiu/entregou" → sugere Concluída + data · "começou/iniciou" → Em Andamento · fim planejado vencido → Atrasada

### Gerais
- LGPD: nunca nomear cliente/proprietário/hóspede
- Nunca inventar números, fatos ou links; sempre citar fonte e data

## Onde os arquivos ficam

| O quê | Onde |
|---|---|
| Scripts em execução (fonte da verdade) | `~/.hermes/scripts/` (WSL) |
| Espelhos versionados | este repo, `skill-*/scripts/` |
| Estado (threads, monitor, fechamento) | `~/.hermes/scripts/.briefing_posted`, `.monitor_state`, `.fechamento_state` |
| Agendamento | `~/.hermes/cron/jobs.json` |
| Serviço do gateway | `~/.config/systemd/user/hermes-gateway.service` |

> **Fluxo de edição**: editar no WSL (onde roda) e sincronizar pro repo — ou o contrário, contanto que os dois fiquem iguais. O repo é o backup versionado.

## Estrutura do repositório

```
agente-Comunicador/
├── README.md / SOUL.md / .env.example
├── memory/szni-comunicador-setup.md   # memória técnica do setup
├── skill-briefing/scripts/daily_briefing.py        (= cronograma-briefing.py no WSL)
├── skill-updates/scripts/cobrar_updates.py
├── skill-monitor/scripts/monitor_updates.py
├── skill-fechamento/scripts/fechamento_diario.py + aprovar_changes.py
├── skill-atualizar/scripts/atualizar_tarefa.py
└── skill-szni/scripts/szni-aprovador-dm.py
```

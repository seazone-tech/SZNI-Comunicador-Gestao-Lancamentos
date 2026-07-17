---
name: szni-comunicador-setup
description: Setup do agente Hermes SZNI-Comunicador-Gestao-Lancamentos (atualizado 17/07/2026)
metadata:
  type: project
---

# SZNI-Comunicador-Gestao-Lancamentos

## Dono
Bianca Carina Valente (Bica) — Especialista de gestão de lançamentos

## Pastas
- Repo (docs + espelhos versionados): `C:\Users\compu\.claude\projects\agente-Comunicador\` (git local; remoto seazone-tech, push só com OK da Bianca)
- Scripts em execução (fonte da verdade): `~/.hermes/scripts/` (WSL)

## Plataforma (atualizado 16-17/07/2026)
- **Hermes v0.17.0**, profile `default` em `~/.hermes/`
- **Gateway: serviço systemd `hermes-gateway`** (não é mais tmux!) — enabled + linger, sobe com o WSL
- **Keep-alive**: `hermes-gateways-keepalive.vbs` na pasta Inicializar do Windows (loop: religa WSL em 10s se cair). Sem ele o WSL hiberna e os bots param
- Avisos "Gateway shutting down" nos canais: **desativados em 17/07** (`slack.gateway_restart_notification: false` no config.yaml)
- Agente vizinho: **Analista** (profile `analista`, bot Slack próprio, key própria) — docs em `C:\Users\compu\.claude\projects\agente-Analista\`
- Modelo: GPT-5.4 via hub-seazone (`custom_providers` no config.yaml lê `OPENAI_API_KEY` do ambiente/scripts/.env)

## Scripts e jobs (6 jobs + 1 sob demanda)
| Cron ID | Script (WSL) | Espelho no repo | Quando |
|---|---|---|---|
| 3580392bc37f | cronograma-briefing.py | skill-briefing/scripts/daily_briefing.py | Seg-Sex 08h30 |
| 5ef5a4385641 | cobrar_updates.py | skill-updates/scripts/ | Seg-Sex 10h |
| 40266f26cd94 | monitor_updates.py | skill-monitor/scripts/ | Seg-Sex 9h-18h |
| a73f113395f8 | fechamento_diario.py | skill-fechamento/scripts/ | Seg-Sex 17h30 |
| b2e4f8a1c3d5 | atualizar_tarefa.py | skill-atualizar/scripts/ | Seg-Sex 8h-19h, a cada 3 min |
| e80bee0a5ce9 | szni-aprovador-dm.py | skill-szni/scripts/ | Seg-Sex, por hora |
| (sob demanda) | aprovar_changes.py | skill-fechamento/scripts/ | "aprova N" |

Todos com workdir `/home/compu/.hermes/scripts`. Atalhos no chat: `/briefing`, `/cobrar`, `/fechamento`.

## Canais Slack — CHANNEL_MAP
- [5921] Farol da Barra Spot → C0BE0QE9E79 (empreendimento CANCELADO em ~jul/2026 — cronograma virou checklist de cancelamento)
- [12235] São Miguel dos Milagres → C0BEKBE1SUS
- Bot adicionado manualmente em cada canal. Bianca: U06093URWPR

## Arquitetura thread-unique do canal
- Uma tarefa = uma thread, postada uma vez; estado em `.briefing_posted` (`sheet|task|ts|channel|team`)
- ✅ de bot/Bianca na thread → todos os scripts ignoram
- Tarefa concluída no SmartSheet → `sync_done_tasks` adiciona ✅ e remove do estado
- Troca de Time Responsável → reply na thread avisando

## Incidente 17/07/2026 (resolvido)
WSL ligava/desligava 4x no boot antes do keep-alive prender (Windows atrasa itens de inicialização ~4 min) → briefing interrompido + spam de "Gateway shutting down" no canal. Correção: vigia em loop (religa em 10s) + avisos desativados. Detalhe: cada queda postava aviso em TODOS os chats com sessão ativa.

## API key do hub-seazone
- Lida de `OPENAI_API_KEY` (scripts/.env) pelo custom_provider; histórico: já foi via `hermes auth add` (auth.json)
- Bot 401 = key perdida/expirada → gerar nova no ai-portal e atualizar

## Bugs já corrigidos (histórico até 09/07)
- Scripts sem `if __name__ == "__main__"` — adicionado
- sync_done_tasks (✅ automático), aviso de troca de time, estado com 5 campos
- cobrar/fechamento usam SOMENTE .briefing_posted (sem fallback de histórico)
- TASK_BLOCK_RE aceita [sheet] e [[sheet]]; add_check_to_thread usa canal do threads_map
- Follow-up duplicado e data de atraso corrigidos (commit 82842d6)

## Higiene do repo (17/07/2026)
- Espelhos sincronizados com o WSL; adicionados atualizar_tarefa e szni-aprovador-dm (antes órfãos)
- Removidos: patch.py, cobrar.txt (cópia velha), __pycache__
- README reescrito pro estado atual

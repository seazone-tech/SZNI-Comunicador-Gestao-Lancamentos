# SZNI-Comunicador-Gestao-Lancamentos

Assistente de gestão de lançamentos da Bianca. Roda no Slack e integra com SmartSheet.

## O que ele faz

| Script | Quando | O que faz |
|---|---|---|
| **Briefing** | Seg-Sex 08:00 | Lê cronograma do SmartSheet e posta uma thread por tarefa pendente no canal do Slack |
| **Cobrar Updates** | Seg-Sex 10:00 | Verifica threads abertas e cobra atualização — primeira vez manda 3 perguntas, follow-up manda mensagem curta (não duplica se já cobrou) |
| **Monitor** | Seg-Sex 09:00–18:00 (a cada hora) | Lê respostas das threads, detecta sinais de risco (atraso, bloqueio, dúvida) e avisa Bianca por DM com link da thread |
| **Fechamento** | Seg-Sex 17:30 | Classifica respostas do dia, sugere mudanças de status no SmartSheet e envia relatório numerado por DM |
| **Aprovar** | Sob demanda | Bianca responde "aprova 1", "aprova 2" etc. O bot aplica as mudanças no SmartSheet e adiciona ✅ na thread |

## Requisitos

- Python 3.11+
- WSL2 (Ubuntu)
- Hermes (agendador de scripts — `~/.hermes/`)
- Tokens: SmartSheet + Slack Bot

## Instalação

```bash
# 1. Clone
git clone https://github.com/seazone-tech/SZNI-Comunicador-Gestao-Lancamentos.git

# 2. Crie o venv e instale as dependências
python3 -m venv ~/.hermes/scripts/venv
~/.hermes/scripts/venv/bin/python3 -m pip install python-dotenv smartsheet-python-sdk slack-sdk

# 3. Configure as variáveis
cp .env.example ~/.hermes/scripts/.env
# Edite o .env com seus tokens

# 4. Copie os scripts para o Hermes
cp skill-briefing/scripts/daily_briefing.py ~/.hermes/scripts/cronograma-briefing.py
cp skill-updates/scripts/cobrar_updates.py ~/.hermes/scripts/cobrar_updates.py
cp skill-monitor/scripts/monitor_updates.py ~/.hermes/scripts/monitor_updates.py
cp skill-fechamento/scripts/fechamento_diario.py ~/.hermes/scripts/fechamento_diario.py
cp skill-fechamento/scripts/aprovar_changes.py ~/.hermes/scripts/aprovar_changes.py
```

## Configuração

### Variáveis de ambiente (`~/.hermes/scripts/.env`)

| Variável | Descrição |
|---|---|
| `SMARTSHEET_TOKEN` | Token da API do SmartSheet |
| `SMARTSHEET_FOLDER_ID` | ID da pasta que contém os cronogramas |
| `SLACK_BOT_TOKEN` | Token do bot Slack |
| `SLACK_CHANNEL_ID` | ID do canal onde o bot posta os briefings |
| `BIANCA_USER_ID` | ID do Slack da Bianca (para DMs) |
| `STATUS_DONE_VALUES` | Status que indicam tarefa concluída (ex: `Concluída,Cancelada`) |

### Colunas esperadas no SmartSheet

- `Atividade`
- `Status`
- `Time Responsável`
- `Data de Início Planejada`
- `Data de Fim Planejada`
- `Data de Início Realizada`
- `Data de Fim Realizada`
- `Dependência` (opcional — número da linha da tarefa que precisa estar concluída antes)

### Regras de filtragem do briefing

1. Status NÃO está em `STATUS_DONE_VALUES`
2. `Data de Início Planejada` não está em branco
3. `Data de Início Planejada` <= hoje
4. Se `Dependência` tem valor → a tarefa referenciada precisa estar concluída

### Organização do canal

- **Uma tarefa = uma thread.** Nunca repostada no canal.
- Estado persistente em `~/.hermes/scripts/.briefing_posted`
- Thread com ✅ de bot ou Bianca → ignorada por todos os scripts
- Thread com ✅ de outra pessoa → continua ativa

### Regras da cobrança

- Sem resposta humana → manda mensagem completa com 3 perguntas
- Com resposta humana, sem follow-up do bot → manda mensagem curta
- Bot já mandou follow-up → pula (não duplica)

### Regras do fechamento

- Resposta com "concluiu" / "entregou" → sugere `Status → Concluída` + data de fim
- Resposta com "começou" / "iniciou" → sugere `Status → Em Andamento`
- Data de fim planejada já passou e tarefa não foi concluída → sugere `Status → Atrasada`

## Onde os arquivos ficam

| O quê | Onde |
|---|---|
| Scripts em execução | `~/.hermes/scripts/` (WSL) |
| Variáveis de ambiente | `~/.hermes/scripts/.env` |
| Threads postadas | `~/.hermes/scripts/.briefing_posted` |
| Estado do monitor | `~/.hermes/scripts/.monitor_state` |
| Estado do fechamento | `~/.hermes/scripts/.fechamento_state` |
| Agendamento | `~/.hermes/cron/jobs.json` |

## Estrutura do repositório

```
SZNI-Comunicador-Gestao-Lancamentos/
├── SOUL.md                      # Persona do agente
├── README.md
├── .env.example
├── .gitignore
├── memory/                      # Memória e configuração do projeto
├── skill-briefing/scripts/      # daily_briefing.py
├── skill-updates/scripts/       # cobrar_updates.py
├── skill-monitor/scripts/       # monitor_updates.py
└── skill-fechamento/scripts/    # fechamento_diario.py + aprovar_changes.py
```

## Regras

- Nunca nomear cliente/proprietário/hóspede no output (LGPD)
- Nunca inventar números, fatos ou links
- Sempre citar fonte e data

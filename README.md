# SZNI-Comunicador-Gestao-Lancamentos

Assistente de gestão de lançamentos da Bianca. Roda no Slack e integra com SmartSheet.

## O que ele faz

| Skill | Quando | O que faz |
|---|---|---|
| **Briefing** | Seg-Sex 08:00 | Lê cronograma do SmartSheet e posta resumo no canal do Slack |
| **Cobrar Updates** | Seg-Sex 08:15 | Cobra replies nos threads do briefing |
| **Monitor** | Seg-Sex a cada 30min | Monitora replies, avisa Bianca por DM se encontrar bloqueios/atrasos |
| **Fechamento** | Seg-Sex 17:30 | Relatório do dia via DM — todas as tarefas com updates e sugestões de mudança no SmartSheet |

## Requisitos

- Python 3.11+
- WSL2 (Ubuntu)
- Hermes CLI (`pip install hermes-agent`)
- Tokens: SmartSheet + Slack Bot

## Instalação

```bash
# 1. Clone
git clone https://github.com/seazone-tech/SZNI-Comunicador-Gestao-Lancamentos.git
cd SZNI-Comunicador-Gestao-Lancamentos

# 2. Dependências
pip install python-dotenv smartsheet slack-sdk pyyaml

# 3. Configure as variáveis
cp .env.example .env
# Edite o .env com seus tokens

# 4. Gateway Hermes
hermes gateway run
```

## Configuração

### Variáveis de ambiente (`.env`)

| Variável | Descrição |
|---|---|
| `SMARTSHEET_TOKEN` | Token da API do SmartSheet |
| `SMARTSHEET_FOLDER_ID` | ID da pasta que contém os cronogramas |
| `SLACK_BOT_TOKEN` | Token do bot Slack |
| `SLACK_CHANNEL_ID` | ID do canal onde o bot posta os briefings |
| `BIANCA_USER_ID` | ID do Slack do usuário (pra DM) |
| `LOOKAHEAD_DAYS` | Dias de antecedência no briefing (padrão: 3) |

### Colunas do SmartSheet esperadas

- `Atividade`
- `Status`
- `Time Responsável`
- `Data de Início Planejada`
- `Data de Fim Planejada`
- `Data de Início Realizada`
- `Data de Fim Realizada`

## Como conversar com o bot

No Slack, mande DM pro bot:

| Mensagem | Ação |
|---|---|
| `roda o briefing` | Gera e envia o briefing manualmente |
| `cobra os updates` | Cobra replies nos threads |
| `monitora as tarefas` | Roda o monitor agora |
| `gera o fechamento` | Gera o relatório de fechamento do dia |

## Estrutura

```
agente Bica/
├── SOUL.md                     # Persona do agente
├── README.md
├── .env.example
├── .gitignore
├── memory/                     # Memória do projeto
├── skill-briefing/scripts/     # Briefing diário
├── skill-updates/scripts/      # Cobrar updates
├── skill-monitor/scripts/      # Monitor de replies
└── skill-fechamento/scripts/  # Fechamento do dia
```

## Auto-start no Windows

O gateway Hermes sobe automaticamente ao ligar o PC via:

```
Shell:startup\SZNI-Gateway-Start.bat
```

## Regras

- Nunca nomear cliente/proprietário/hóspede no output (LGPD)
- Nunca inventar números, fatos ou links
- Sempre citar fonte e data

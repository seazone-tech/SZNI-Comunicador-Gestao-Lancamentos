#!/usr/bin/env python3
"""
daily_briefing.py
Lê cronogramas do Smartsheet e envia resumo diário no Slack.
Roda via cron às 8h30.
"""

import os
import logging
from collections import defaultdict
from datetime import date, timedelta
from dotenv import load_dotenv
import smartsheet
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

SMARTSHEET_TOKEN   = os.environ["SMARTSHEET_TOKEN"]
FOLDER_ID          = int(os.environ["SMARTSHEET_FOLDER_ID"])
SLACK_BOT_TOKEN    = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL_ID   = os.environ["SLACK_CHANNEL_ID"]
STATUS_DONE_VALUES = {v.strip() for v in os.getenv("STATUS_DONE_VALUES", "Concluída,Cancelada").split(",")}
LOOKAHEAD_DAYS     = int(os.getenv("LOOKAHEAD_DAYS", "3"))

COL_TASK           = "Atividade"
COL_STATUS         = "Status"
COL_START_DATE     = "Data de Início Planejada"
COL_END_DATE       = "Data de Fim Planejada"
COL_ASSIGNEE       = "Time Responsável"

# Mapa fixo: nome do time → lista de Slack User IDs
TEAM_SLACK_MAP = {
    "Gestão Lançamentos":               ["U06093URWPR"],
    "MARCO":                            ["U06093URWPR"],
    "Diretoria":                        ["U06093URWPR"],
    "Financeiro":                       ["U06093URWPR"],
    "Comercial":                        ["U06093URWPR"],
    "Marketplace":                      ["U06093URWPR"],
    "Fornecedores Lançamentos e Obras": ["U08MYES3EJ0"],
    "Fornecedores Lançamentos":             ["U08MYES3EJ0"],
    "Obras":                               ["U08MYES3EJ0"],
    "Orçamentos Lançamentos":           ["U090UKQAXFD"],
    "Compra de Terrenos":               ["U05Q6PXC9KR"],
    "Análise de Terrenos":              ["U05Q6PXC9KR"],
    "Jurídico":                         ["U046CCULGJF"],
    "Projetos Lançamentos":             ["U07DXLFP1GT"],
    "Marketing":                        ["U0A8H79PACB"],
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def slack_mentions(team_name: str) -> str:
    """Retorna string com todas as @menções do time, ou nome simples se não mapeado."""
    if not team_name or team_name == "_sem time_":
        return "_sem time responsável_"
    uids = TEAM_SLACK_MAP.get(team_name.strip())
    if not uids:
        log.warning(f"Time '{team_name}' não encontrado no TEAM_SLACK_MAP")
        return team_name.strip()
    return " ".join(f"<@{uid}>" for uid in uids)


def classify_task(due: date, today: date) -> tuple[str, str]:
    """Retorna (emoji, label) conforme urgência."""
    if due < today:
        delta = (today - due).days
        label = f"atrasada {delta}d" if delta > 1 else "atrasada 1d"
        return "🔴", label
    elif due == today:
        return "🟡", "vence hoje"
    else:
        days_ahead = (due - today).days
        return "🔵", f"em {days_ahead}d"


def get_col_map(sheet) -> dict[str, int]:
    """Mapeia nome de coluna → id de coluna."""
    col_map = {col.title: col.id for col in sheet.columns}
    log.info(f"Colunas encontradas: {list(col_map.keys())}")
    return col_map


def cell_value(row, col_map: dict, col_name: str):
    """Extrai o valor de uma célula pelo nome da coluna."""
    col_id = col_map.get(col_name)
    if col_id is None:
        return None
    for cell in row.cells:
        if cell.column_id == col_id:
            return cell.display_value or cell.value
    return None


def parse_date(raw) -> date | None:
    """Converte string de data para objeto date. Retorna None se inválido."""
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def parse_row(row, col_map: dict) -> dict | None:
    """
    Extrai os campos relevantes de uma linha do Smartsheet.
    Retorna None se não tiver atividade ou data de fim.
    """
    task       = cell_value(row, col_map, COL_TASK)
    status     = cell_value(row, col_map, COL_STATUS) or ""
    assignee   = cell_value(row, col_map, COL_ASSIGNEE) or ""
    start_raw  = cell_value(row, col_map, COL_START_DATE)
    end_raw    = cell_value(row, col_map, COL_END_DATE)

    if not task:
        return None

    end_date   = parse_date(end_raw)
    start_date = parse_date(start_raw)

    if end_date is None:
        return None  # Ignora tarefas sem data de fim

    return {
        "task":       task,
        "status":     status,
        "assignee":   assignee,
        "start_date": start_date,
        "end_date":   end_date,
    }


def fmt_date(d: date | None) -> str:
    """Formata data como dd/mm ou '—' se None."""
    return d.strftime("%d/%m") if d else "—"


def build_team_message(sheet_name: str, team: str, team_tasks: list[dict], today: date) -> str:
    """
    Monta mensagem Slack para um time específico dentro de um empreendimento.
    """
    lines = [f"*📋 {sheet_name.upper()}* — *{team.upper()}*\n"]

    atrasadas = sum(1 for t in team_tasks if classify_task(t["end_date"], today)[0] == "🔴")
    mentions = slack_mentions(team)
    alerta    = f" — ⚠️ {atrasadas} atrasada(s)" if atrasadas else ""
    lines.append(f"{mentions}{alerta}\n")

    urgency_order = {"🔴": 0, "🟡": 1, "🔵": 2}
    team_tasks.sort(key=lambda x: (urgency_order[classify_task(x["end_date"], today)[0]], x["end_date"]))

    for t in team_tasks:
        emoji, label = classify_task(t["end_date"], today)
        inicio = fmt_date(t["start_date"])
        fim    = fmt_date(t["end_date"])
        status = t["status"] or "sem status"
        lines.append(f"  {emoji} *{t['task']}*")
        lines.append(f"       Status: {status} | Início: {inicio} → Fim: {fim} ({label})")

    lines.append("")
    total = len(team_tasks)
    atrasadas_total = sum(1 for t in team_tasks if classify_task(t["end_date"], today)[0] == "🔴")
    lines.append(f"_Total: {total} tarefa(s) pendente(s), {atrasadas_total} atrasada(s)_")
    lines.append("—" * 50)
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    today  = date.today()
    cutoff = today + timedelta(days=LOOKAHEAD_DAYS)

    log.info(f"Iniciando briefing para {today} (lookahead: {LOOKAHEAD_DAYS} dias)")

    ss_client    = smartsheet.Smartsheet(SMARTSHEET_TOKEN)
    slack_client = WebClient(token=SLACK_BOT_TOKEN)

    folder_children = ss_client.Folders.get_folder_children(FOLDER_ID)
    sheets = [item for item in folder_children.data]
    log.info(f"Folder: {len(sheets)} sheet(s) encontrada(s)")

    messages_sent = 0

    for sheet_ref in sheets:
        sheet_name = sheet_ref.name
        sheet_id   = sheet_ref.id

        try:
            sheet = ss_client.Sheets.get_sheet(sheet_id)
        except Exception as e:
            log.error(f"Erro ao ler sheet '{sheet_name}': {e}")
            continue

        col_map = get_col_map(sheet)

        # Validar colunas obrigatórias
        required = [COL_TASK, COL_STATUS, COL_END_DATE, COL_ASSIGNEE]
        missing  = [c for c in required if c not in col_map]
        if missing:
            log.warning(f"Sheet '{sheet_name}': colunas ausentes {missing} — pulando")
            continue

        # Filtrar tarefas pendentes dentro do período
        pending = []
        for row in sheet.rows:
            parsed = parse_row(row, col_map)
            if parsed is None:
                continue
            if parsed["status"].strip() in STATUS_DONE_VALUES:
                continue
            if parsed["end_date"] > cutoff:
                continue
            pending.append(parsed)

        if not pending:
            log.info(f"Sheet '{sheet_name}': sem pendências — sem mensagem")
            continue

        # Agrupar por time
        by_team: dict[str, list] = defaultdict(list)
        for t in pending:
            team = t["assignee"].strip() if t["assignee"] else "_sem time_"
            by_team[team].append(t)

        for team, team_tasks in by_team.items():
            message = build_team_message(sheet_name, team, team_tasks, today)
            try:
                slack_client.chat_postMessage(channel=SLACK_CHANNEL_ID, text=message)
                log.info(f"Sheet '{sheet_name}' | '{team}': mensagem enviada ({len(team_tasks)} tarefa(s))")
                messages_sent += 1
            except SlackApiError as e:
                log.error(f"Erro Slack para '{sheet_name}' | '{team}': {e.response['error']}")

    log.info(f"Briefing concluído: {messages_sent} mensagem(ns) enviada(s)")


if __name__ == "__main__":
    main()

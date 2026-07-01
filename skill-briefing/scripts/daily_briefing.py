#!/usr/bin/env python3
"""
daily_briefing.py
Lê cronogramas do Smartsheet e envia resumo diário no Slack.
Uma tarefa só é postada uma vez — atualizações ficam na thread existente.
Roda via cron às 8h30.
"""

import os
import re
import logging
from collections import defaultdict
from datetime import date
from dotenv import load_dotenv
import smartsheet
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

_script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.expanduser("~/.hermes/scripts/.env")) or load_dotenv(os.path.join(_script_dir, ".env"))

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
BIANCA_USER_ID     = os.environ["BIANCA_USER_ID"]
STATUS_DONE_VALUES = {v.strip() for v in os.getenv("STATUS_DONE_VALUES", "Concluída,Concluida,Cancelada,Cancelado").split(",")}

STATE_FILE = os.path.expanduser("~/.hermes/scripts/.briefing_posted")

COL_TASK           = "Atividade"
COL_STATUS         = "Status"
COL_START_DATE     = "Data de Início Planejada"
COL_END_DATE       = "Data de Fim Planejada"
COL_ASSIGNEE       = "Time Responsável"
COL_DEPENDENCY     = "Dependência"

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

# ── Helpers ────────────────────────────────────────────────────────────────────

def slack_mentions(team_name: str) -> str:
    """Retorna string com todas as @menções do time, ou nome simples se não mapeado."""
    if not team_name or team_name == "_sem time_":
        return "_sem time responsável_"
    uids = TEAM_SLACK_MAP.get(team_name.strip())
    if not uids:
        log.warning(f"Time '{team_name}' não encontrado no TEAM_SLACK_MAP")
        return team_name.strip()
    return " ".join(f"<@{uid}>" for uid in uids)


def get_bot_user_id(client) -> str:
    return client.auth_test()["user_id"]


def is_thread_done(client, channel_id: str, msg_ts: str) -> bool:
    """
    Retorna True se a thread tem ✅ reactions de:
    - O próprio bot
    - Bianca
    """
    try:
        result = client.reactions_get(channel=channel_id, timestamp=msg_ts)
        for reaction in result.get("message", {}).get("reactions", []):
            if reaction.get("name") in ("white_check_mark", "check", "heavy_check_mark"):
                users = reaction.get("users", [])
                bot_id = get_bot_user_id(client)
                if bot_id in users or BIANCA_USER_ID in users:
                    return True
        return False
    except SlackApiError:
        return False


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
    dependency = cell_value(row, col_map, COL_DEPENDENCY) or ""

    if not task:
        return None

    end_date   = parse_date(end_raw)
    start_date = parse_date(start_raw)

    if end_date is None:
        return None

    row_num = str(row.row_number) if hasattr(row, "row_number") else ""

    return {
        "task":       task,
        "status":     status,
        "assignee":   assignee,
        "start_date": start_date,
        "end_date":   end_date,
        "dependency": dependency,
        "row_num":    row_num,
    }


def fmt_date(d: date | None) -> str:
    """Formata data como dd/mm ou '—' se None."""
    return d.strftime("%d/%m") if d else "—"


def build_task_message(task: dict, mentions: str) -> str:
    inicio = fmt_date(task["start_date"])
    fim    = fmt_date(task["end_date"])
    status = task["status"] or "sem status"
    return (
        f"Responsável: {mentions}\n"
        f"Status: {status} | Início: {inicio} → Fim: {fim}"
    )


def build_task_header(task: dict, sheet_name: str, team: str) -> str:
    mentions = slack_mentions(team)
    return f"📌 [{sheet_name}] [{team.upper()}] {task['task']}"


# ── Estado persistente ────────────────────────────────────────────────────────

def load_posted_tasks() -> dict[str, str]:
    """Retorna {sheet|task_name: thread_ts} de tarefas já postadas."""
    try:
        with open(STATE_FILE) as f:
            result = {}
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("|")
                if len(parts) >= 3:
                    key = "|".join(parts[:2])
                    result[key] = parts[2]
            return result
    except FileNotFoundError:
        return {}


def save_posted_tasks(posted: dict[str, str]):
    with open(STATE_FILE, "w") as f:
        for key, ts in posted.items():
            f.write(f"{key}|{ts}\n")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    today = date.today()

    log.info(f"Iniciando briefing para {today}")

    ss_client    = smartsheet.Smartsheet(SMARTSHEET_TOKEN)
    slack_client = WebClient(token=SLACK_BOT_TOKEN)
    bot_id       = get_bot_user_id(slack_client)

    # Carrega tarefas já postadas
    posted_tasks = load_posted_tasks()
    log.info(f"Tarefas já postadas no estado: {len(posted_tasks)}")

    # Carrega threads existentes no canal com ✅ de bot/Bianca
    existing_done_threads = set()
    try:
        result = slack_client.conversations_history(channel=SLACK_CHANNEL_ID, limit=200)
        for msg in result.get("messages", []):
            if msg.get("user") != bot_id:
                continue
            if is_thread_done(slack_client, SLACK_CHANNEL_ID, msg["ts"]):
                existing_done_threads.add(msg["ts"])
    except SlackApiError as e:
        log.error(f"Erro ao buscar histórico do canal: {e}")

    folder_children = ss_client.Folders.get_folder_children(FOLDER_ID)
    sheets = [item for item in folder_children.data]
    log.info(f"Folder: {len(sheets)} sheet(s) encontrada(s)")

    messages_sent = 0
    updated_posted = dict(posted_tasks)

    for sheet_ref in sheets:
        sheet_name = sheet_ref.name
        sheet_id   = sheet_ref.id

        try:
            sheet = ss_client.Sheets.get_sheet(sheet_id)
        except Exception as e:
            log.error(f"Erro ao ler sheet '{sheet_name}': {e}")
            continue

        col_map = get_col_map(sheet)

        required = [COL_TASK, COL_STATUS, COL_END_DATE, COL_ASSIGNEE]
        missing  = [c for c in required if c not in col_map]
        if missing:
            log.warning(f"Sheet '{sheet_name}': colunas ausentes {missing} — pulando")
            continue

        # Mapa de todas as tarefas (row_num → status)
        all_tasks: dict[str, str] = {}
        for row in sheet.rows:
            parsed = parse_row(row, col_map)
            if parsed is None:
                continue
            all_tasks[parsed["row_num"]] = parsed["status"]

        # Filtra pendentes que já deveriam ter começado
        pending = []
        for row in sheet.rows:
            parsed = parse_row(row, col_map)
            if parsed is None:
                continue
            if parsed["status"].strip() in STATUS_DONE_VALUES:
                continue
            if parsed["start_date"] is None or parsed["start_date"] > today:
                continue
            dep = parsed["dependency"].strip()
            if dep:
                nums = re.findall(r'\d+', dep)
                blocked = False
                for num in nums:
                    dep_status = all_tasks.get(num, "").strip()
                    if dep_status not in STATUS_DONE_VALUES:
                        blocked = True
                        break
                if blocked:
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
            for t in team_tasks:
                task_key = f"{sheet_name}|{t['task']}"

                # Se já foi postada, mantém no estado (evita repostar)
                if task_key in posted_tasks:
                    thread_ts = posted_tasks[task_key]
                    updated_posted[task_key] = thread_ts
                    if thread_ts in existing_done_threads:
                        log.info(f"Sheet '{sheet_name}': tarefa '{t['task'][:40]}' com ✅ — mantendo no estado")
                    else:
                        log.info(f"Sheet '{sheet_name}': tarefa '{t['task'][:40]}' já postada — mantendo")
                    continue

                mentions = slack_mentions(team)
                header = build_task_header(t, sheet_name, team)
                body   = build_task_message(t, mentions)

                # Retry em caso de erro temporário do Slack
                posted_ok = False
                for attempt in range(3):
                    try:
                        result = slack_client.chat_postMessage(
                            channel=SLACK_CHANNEL_ID,
                            text=header,
                        )
                        thread_ts = result["ts"]
                        slack_client.chat_postMessage(
                            channel=SLACK_CHANNEL_ID,
                            text=body,
                            thread_ts=thread_ts,
                        )
                        updated_posted[task_key] = thread_ts
                        log.info(f"Sheet '{sheet_name}' | '{team}': thread criada ({t['task'][:40]})")
                        messages_sent += 1
                        posted_ok = True
                        break
                    except SlackApiError as e:
                        error = e.response.get("error", "")
                        log.warning(f"Tentativa {attempt+1} falhou: {error}")
                        if attempt < 2:
                            import time; time.sleep(2)
                        else:
                            log.error(f"Erro Slack para '{sheet_name}' | '{team}': {error}")

    # Atualiza estado persistente
    save_posted_tasks(updated_posted)
    log.info(f"Briefing concluído: {messages_sent} nova(s) mensagem(s) | {len(updated_posted)} total no estado")

    # Limpa do estado as tarefas que já estão done no SmartSheet
    done_keys = set()
    for sheet_ref in sheets:
        try:
            sheet = ss_client.Sheets.get_sheet(sheet_ref.id)
            col_map = get_col_map(sheet)
            for row in sheet.rows:
                p = parse_row(row, col_map)
                if p is None:
                    continue
                if p["status"].strip() in STATUS_DONE_VALUES:
                    key = f"{sheet_ref.name}|{p['task']}"
                    done_keys.add(key)
        except Exception:
            pass

    if done_keys:
        cleaned = {k: v for k, v in updated_posted.items() if k not in done_keys}
        if len(cleaned) < len(updated_posted):
            removed = len(updated_posted) - len(cleaned)
            save_posted_tasks(cleaned)
            updated_posted = cleaned
            log.info(f"Removidas {removed} tarefa(s) done do estado (total: {len(updated_posted)})")


if __name__ == '__main__':
    main()

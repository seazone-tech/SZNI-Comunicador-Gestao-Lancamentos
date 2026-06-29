#!/usr/bin/env python3
"""
aprovar_changes.py
Lê DMs do bot, detecta "aprova N", aplica mudanças no SmartSheet.
Rodado pelo monitor a cada 30min.
"""

import os
import re
import logging
import datetime as dt
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import smartsheet

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
BIANCA_USER_ID = os.environ["BIANCA_USER_ID"]
SMARTSHEET_TOKEN = os.environ["SMARTSHEET_TOKEN"]
SMARTSHEET_FOLDER_ID = int(os.environ["SMARTSHEET_FOLDER_ID"])

client = WebClient(token=SLACK_BOT_TOKEN)
ss_client = smartsheet.Smartsheet(SMARTSHEET_TOKEN)

TZ = dt.timezone(dt.timedelta(hours=-3))

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".fechamento_state")
PROCESSED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".aproval_processed")

BOT_MSG_MARKERS = [
    "GESTAO LANCAMENTOS", "GESTÃO LANÇAMENTOS", "MARKETING",
    "DIRETORIA", "PROJETOS LANCAMENTOS", "FAROL", "MARISTA",
    "ORCAMENTOS LANCAMENTOS", "ORÇAMENTOS LANÇAMENTOS",
    "FORNECEDORES LANCAMENTO", "FORNECEDORES LANÇAMENTO",
    "COMPRA DE TERRENOS", "ANALISE DE TERRENOS", "ANÁLISE DE TERRENOS",
    "SERVIÇOS/CS/FRANQUIAS", "SERVICOS/CS/FRANQUIAS", "MARCO",
]

BOT_USER_ID = None


def get_bot_user_id():
    global BOT_USER_ID
    if BOT_USER_ID:
        return BOT_USER_ID
    BOT_USER_ID = client.auth_test()["user_id"]
    return BOT_USER_ID


def load_fechamento_state():
    """Carrega tarefas do estado: counter|name|task_status|classification|suggestions|reply_text|thread_ts"""
    tasks = {}
    if not os.path.exists(STATE_FILE):
        return tasks
    with open(STATE_FILE) as f:
        for line in f:
            parts = line.strip().split("|")
            if len(parts) >= 6:
                suggestions = parts[4] if len(parts) > 4 else ""
                tasks[int(parts[0])] = {
                    "name": parts[1],
                    "task_status": parts[2],
                    "classification": parts[3],
                    "suggestions": suggestions.split("||") if suggestions else [],
                    "reply_text": parts[5] if len(parts) > 5 else "",
                    "thread_ts": parts[6] if len(parts) > 6 else "",
                }
    return tasks


def load_processed():
    if not os.path.exists(PROCESSED_FILE):
        return set()
    with open(PROCESSED_FILE) as f:
        return set(line.strip() for line in f if line.strip())


def save_processed(processed):
    with open(PROCESSED_FILE, "w") as f:
        for item in sorted(processed):
            f.write(item + "\n")


def get_fechamento_threads_today():
    """Busca threads do briefing de hoje (pra associar tarefa ao sheet)."""
    my_id = get_bot_user_id()
    today = dt.date.today()
    threads = []
    try:
        result = client.conversations_history(channel=os.environ["SLACK_CHANNEL_ID"], limit=50)
        for msg in result.get("messages", []):
            if msg.get("user") != my_id:
                continue
            text = msg.get("text", "")
            if not any(m.upper() in text.upper() for m in BOT_MSG_MARKERS):
                continue
            msg_date = dt.datetime.fromtimestamp(float(msg["ts"]), tz=TZ).date()
            if msg_date != today:
                continue
            threads.append({"ts": msg["ts"], "text": msg["text"]})
        return threads
    except SlackApiError:
        return []


def find_sheet_for_task(task_name, threads):
    """Tenta encontrar o sheet onde a tarefa está."""
    import re
    TASK_BLOCK_RE = re.compile(
        r":large_(?:large_)?red_circle:.*?"
        r"(?:yellow_circle|blue_circle):.*?"
        r":large_(?:large_)?(?:red_circle|yellow_circle|blue_circle):\s+\*(.+?)\*",
        re.DOTALL,
    )
    for thread in threads:
        if task_name in thread["text"]:
            # Extrai o sheet name do texto
            lines = thread["text"].split("\n")
            for line in lines:
                if line.strip().endswith("-"):
                    continue
                if "FAROL" in line.upper() or "MARISTA" in line.upper() or "MARCO" in line.upper():
                    return line.strip().replace("*", "")
    return None


def get_sheet_by_name(folder_id, name_hint):
    """Encontra sheet pelo nome (ou parte do nome)."""
    try:
        children = ss_client.Folders.get_folder_children(folder_id)
        for item in children.data:
            if name_hint.upper() in item.name.upper():
                return ss_client.Sheets.get_sheet(item.id)
    except Exception as e:
        log.error(f"Erro ao buscar sheet: {e}")
    return None


def apply_smartsheet_change(task_name, classification, suggestions, sheet):
    """Aplica mudança no SmartSheet para a tarefa."""
    col_map = {col.title: col.id for col in sheet.columns}

    status_col = col_map.get("Status")
    inicio_real_col = col_map.get("Data de Início Realizada")
    fim_real_col = col_map.get("Data de Fim Realizada")

    today_str = dt.date.today().strftime("%d/%m/%Y")

    for row in sheet.rows:
        task_cell = None
        for cell in row.cells:
            if col_map.get("Atividade") == cell.column_id:
                task_cell = cell
                break

        if not task_cell:
            continue

        cell_value = task_cell.display_value or task_cell.value or ""
        if task_name.strip().lower() not in str(cell_value).strip().lower():
            continue

        updates = []
        row_update = {"id": row.id, "cells": []}

        for sug in suggestions:
            sug_lower = sug.lower()
            if "concluída" in sug_lower:
                if status_col:
                    row_update["cells"].append({"columnId": status_col, "value": "Concluída"})
                    updates.append("Status → Concluída")
                if fim_real_col:
                    row_update["cells"].append({"columnId": fim_real_col, "value": today_str})
                    updates.append(f"Fim Realizada → {today_str}")
            elif "em andamento" in sug_lower:
                if status_col:
                    current_status = None
                    for cell in row.cells:
                        if cell.column_id == status_col:
                            current_status = cell.display_value or cell.value
                            break
                    if current_status not in ("Concluída",):
                        row_update["cells"].append({"columnId": status_col, "value": "Em Andamento"})
                        updates.append("Status → Em Andamento")
                if inicio_real_col:
                    row_update["cells"].append({"columnId": inicio_real_col, "value": today_str})
                    updates.append(f"Início Realizada → {today_str}")
            elif "atrasada" in sug_lower:
                if status_col:
                    row_update["cells"].append({"columnId": status_col, "value": "Atrasada"})
                    updates.append("Status → Atrasada")

        if row_update["cells"]:
            try:
                ss_client.Sheets.update_rows(sheet.id, [row_update])
                log.info(f"Aplicado: {task_name} | {', '.join(updates)}")
                return True, updates
            except Exception as e:
                log.error(f"Erro ao atualizar {task_name}: {e}")
                return False, [str(e)]

    return False, ["Tarefa não encontrada no sheet"]


def send_confirm(approvals, rejected):
    lines = ["✅ Confirmação das aprovações:\n"]
    for task_name, updates in approvals:
        lines.append(f"  ✅ {task_name}: {', '.join(updates)}")
    if rejected:
        lines.append("")
        for task_name, reason in rejected:
            lines.append(f"  ❌ {task_name}: {reason}")
    try:
        client.chat_postMessage(channel=BIANCA_USER_ID, text="\n".join(lines))
    except SlackApiError as e:
        log.error(f"Erro ao enviar confirmação: {e}")


def run():
    log.info("Verificando aprovações de fechamento")
    my_id = get_bot_user_id()
    tasks = load_fechamento_state()
    if not tasks:
        log.info("Nenhuma tarefa pendente de aprovação")
        return

    processed = load_processed()
    threads = get_fechamento_threads_today()

    approvals = []
    rejected = []

    # Buscar DMs do bot hoje
    try:
        result = client.conversations_history(channel=BIANCA_USER_ID, limit=20)
    except SlackApiError as e:
        log.error(f"Erro ao buscar DMs: {e}")
        return

    approval_re = re.compile(r"aprova\s+(\d+)", re.IGNORECASE)

    for msg in result.get("messages", []):
        user = msg.get("user")
        if user == my_id:
            continue
        text = msg.get("text", "").strip()
        if not text:
            continue

        msg_date = dt.datetime.fromtimestamp(float(msg["ts"]), tz=TZ).date()
        if msg_date != dt.date.today():
            continue

        msg_key = msg["ts"]
        if msg_key in processed:
            continue

        matches = approval_re.findall(text)
        if not matches:
            continue

        processed.add(msg_key)

        for num_str in matches:
            num = int(num_str)
            if num not in tasks:
                rejected.append((num, "número não encontrado"))
                continue

            task = tasks[num]
            sheet_hint = find_sheet_for_task(task["name"], threads)
            if sheet_hint:
                sheet = get_sheet_by_name(SMARTSHEET_FOLDER_ID, sheet_hint)
            else:
                sheet = None

            if not sheet:
                try:
                    children = ss_client.Folders.get_folder_children(SMARTSHEET_FOLDER_ID)
                    for item in children.data:
                        s = ss_client.Sheets.get_sheet(item.id)
                        found = apply_smartsheet_change(task["name"], task["classification"], task["suggestions"], s)
                        if found[0]:
                            sheet = s
                            break
                except Exception as e:
                    log.error(f"Erro ao buscar sheet: {e}")

            if sheet:
                ok, updates = apply_smartsheet_change(task["name"], task["classification"], task["suggestions"], sheet)
                if ok:
                    approvals.append((task["name"], updates))
                else:
                    rejected.append((task["name"], updates[0]))
            else:
                rejected.append((task["name"], "sheet não encontrado"))

    save_processed(processed)

    if approvals or rejected:
        send_confirm(approvals, rejected)
        log.info(f"Aprovações: {len(approvals)} | Rejeitadas: {len(rejected)}")
    else:
        log.info("Nenhuma aprovação detectada")


if __name__ == "__main__":
    run()

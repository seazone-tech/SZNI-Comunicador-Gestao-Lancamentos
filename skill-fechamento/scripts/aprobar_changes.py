#!/usr/bin/env python3
"""
aprovar_changes.py
Lê DMs do bot, detecta "aprova N", aplica mudanças no SmartSheet.
Após aplicar, se o status mudou pra Concluída/Concluida/Cancelada/Cancelado,
adiciona ✅ na thread correspondente no canal automaticamente.
Roda sob demanda (quando Bianca mandar).
"""

import os
import re
import logging
import datetime as dt
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import smartsheet

# Tenta Hermes path primeiro (runtime), depois fallback para projeto
_hermes_env = os.path.expanduser("~/.hermes/scripts/.env")
_project_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.env")
load_dotenv(_hermes_env) if os.path.exists(_hermes_env) else load_dotenv(_project_env)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

SLACK_BOT_TOKEN       = os.environ["SLACK_BOT_TOKEN"]
BIANCA_USER_ID        = os.environ["BIANCA_USER_ID"]
SMARTSHEET_TOKEN      = os.environ["SMARTSHEET_TOKEN"]
SMARTSHEET_FOLDER_ID  = int(os.environ["SMARTSHEET_FOLDER_ID"])
SLACK_CHANNEL_ID      = os.environ["SLACK_CHANNEL_ID"]

client    = WebClient(token=SLACK_BOT_TOKEN)
ss_client = smartsheet.Smartsheet(SMARTSHEET_TOKEN)

TZ = dt.timezone(dt.timedelta(hours=-3))

STATE_FILE      = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".fechamento_state")
PROCESSED_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".aproval_processed")

BOT_USER_ID = None
DONE_VALUES = {"Concluída", "Concluida", "Cancelada", "Cancelado"}


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_bot_user_id():
    global BOT_USER_ID
    if BOT_USER_ID:
        return BOT_USER_ID
    BOT_USER_ID = client.auth_test()["user_id"]
    return BOT_USER_ID


def get_bianca_dm_channel():
    """Abre DM com Bianca e retorna o channel ID real da conversa."""
    try:
        result = client.conversations_open(users=BIANCA_USER_ID)
        return result["channel"]["id"]
    except SlackApiError as e:
        log.error(f"Erro ao abrir DM com Bianca: {e.response['error']}")
        return None


def load_fechamento_state():
    """Carrega tarefas do estado: counter|name|sheet|classification|suggestions|reply_text|thread_ts"""
    tasks = {}
    if not os.path.exists(STATE_FILE):
        return tasks
    with open(STATE_FILE) as f:
        for line in f:
            parts = line.strip().split("|")
            if len(parts) >= 5:
                tasks[int(parts[0])] = {
                    "name":         parts[1],
                    "sheet":        parts[2] if len(parts) > 2 else "",
                    "classification": parts[3] if len(parts) > 3 else "",
                    "suggestions":  parts[4].split("||") if parts[4] else [],
                    "reply_text":   parts[5] if len(parts) > 5 else "",
                    "thread_ts":    parts[6] if len(parts) > 6 else "",
                }
    return tasks


def load_processed():
    if not os.path.exists(PROCESSED_FILE):
        return set()
    with open(PROCESSED_FILE) as f:
        return {l.strip() for l in f if l.strip()}


def save_processed(processed):
    with open(PROCESSED_FILE, "w") as f:
        for item in sorted(processed):
            f.write(item + "\n")


# ── Buscar threads no canal ─────────────────────────────────────────────────

def get_all_briefing_threads():
    """Retorna {task_name_lower: ts} de todas as threads do bot no canal."""
    my_id = get_bot_user_id()
    threads = {}
    try:
        result = client.conversations_history(channel=SLACK_CHANNEL_ID, limit=200)
        for msg in result.get("messages", []):
            if msg.get("user") != my_id:
                continue
            text = msg.get("text", "")
            if "📌" not in text:
                continue
            # Extrai todas as tarefas da mensagem
            for m in re.finditer(r"📌\s*\[([^\]]+)\]\s*\[([^\]]+)\]\s*(.+?)(?:\n|$)", text):
                task_name = m.group(3).strip()
                threads[task_name.lower()] = msg["ts"]
    except SlackApiError as e:
        log.error(f"Erro ao buscar histórico: {e}")
    return threads


# ── Localizar sheet ────────────────────────────────────────────────────────

def get_sheet_by_name(folder_id, name_hint):
    try:
        children = ss_client.Folders.get_folder_children(folder_id)
        for item in children.data:
            if name_hint.upper() in item.name.upper():
                return ss_client.Sheets.get_sheet(item.id)
    except Exception as e:
        log.error(f"Erro ao buscar sheet: {e}")
    return None


# ── Localizar tarefa ─────────────────────────────────────────────────────────

def find_task_row(sheet, task_name):
    """Localiza a row de uma tarefa no sheet. Retorna (row, col_map) ou None."""
    col_map = {col.title: col.id for col in sheet.columns}
    atividade_col = col_map.get("Atividade")
    if atividade_col is None:
        return None
    for row in sheet.rows:
        for cell in row.cells:
            if cell.column_id == atividade_col:
                cell_value = cell.display_value or cell.value or ""
                if task_name.strip().lower() in str(cell_value).strip().lower():
                    return row, col_map
    return None


# ── Aplicar mudança no SmartSheet ───────────────────────────────────────────

def apply_smartsheet_change(task_name, suggestions, sheet, row, col_map):
    """
    Aplica mudanças no SmartSheet para a tarefa.
    Retorna (sucesso: bool, updates: list, status_final: str|None)
    status_final é o status da célula se foi alterado pra done, senão None.
    """
    status_col     = col_map.get("Status")
    inicio_real_col = col_map.get("Data de Início Realizada")
    fim_real_col   = col_map.get("Data de Fim Realizada")

    today_str = dt.date.today().strftime("%Y-%m-%d")

    updates = []
    row_update = {"id": row.id, "cells": []}
    status_final = None

        for sug in suggestions:
            sug_lower = sug.lower()
            if "concluída" in sug_lower:
                if status_col:
                    row_update["cells"].append({"columnId": status_col, "value": "Concluída"})
                    updates.append("Status → Concluída")
                    status_final = "Concluída"
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
                return True, updates, status_final
            except Exception as e:
                log.error(f"Erro ao atualizar {task_name}: {e}")
                return False, [str(e)], None

    return False, ["Tarefa não encontrada"], None


# ── Auto-check no Slack ────────────────────────────────────────────────────

def add_check_to_thread(task_name, threads_map):
    """
    Adiciona ✅ na thread correspondente à tarefa.
    Retorna True se encontrou e marcou, False caso contrário.
    """
    ts = threads_map.get(task_name.lower())
    if not ts:
        log.warning(f"Thread não encontrada para: {task_name}")
        return False
    try:
        client.reactions_add(
            channel=SLACK_CHANNEL_ID,
            timestamp=ts,
            name="white_check_mark",
        )
        log.info(f"✅ adicionado na thread {ts}: {task_name[:40]}")
        return True
    except SlackApiError as e:
        log.error(f"Erro ao adicionar ✅ em {task_name}: {e.response['error']}")
        return False


# ── Confirmação ───────────────────────────────────────────────────────────

def send_confirm(approvals, rejected, checked, dm_channel_id):
    if dm_channel_id is None:
        return
    lines = ["✅ Confirmação das aprovações:\n"]
    for task_name, updates in approvals:
        checked_mark = " ✅" if task_name in checked else ""
        lines.append(f"  ✅ {task_name}: {', '.join(updates)}{checked_mark}")
    if rejected:
        lines.append("")
        for task_name, reason in rejected:
            lines.append(f"  ❌ {task_name}: {reason}")
    try:
        client.chat_postMessage(channel=BIANCA_USER_ID, text="\n".join(lines))
    except SlackApiError as e:
        log.error(f"Erro ao enviar confirmação: {e}")


# ── Main ──────────────────────────────────────────────────────────────────

def run():
    log.info("Verificando aprovações de fechamento")

    tasks = load_fechamento_state()
    if not tasks:
        log.info("Nenhuma tarefa pendente de aprovação")
        return

    processed    = load_processed()
    threads_map  = get_all_briefing_threads()
    approvals    = []
    rejected     = []
    checked      = set()  # tarefas que receberam ✅

    # Buscar DMs do bot
    dm_channel_id = get_bianca_dm_channel()
    if dm_channel_id is None:
        return
    try:
        result = client.conversations_history(channel=dm_channel_id, limit=20)
    except SlackApiError as e:
        log.error(f"Erro ao buscar DMs: {e}")
        return

    approval_re = re.compile(r"aprova\s+(\d+)", re.IGNORECASE)

    for msg in result.get("messages", []):
        user = msg.get("user")
        if user == get_bot_user_id():
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
            sheet_hint = task["sheet"]

            # Localiza sheet e row
            found_row = None
            found_col_map = None
            if sheet_hint:
                sheet = get_sheet_by_name(SMARTSHEET_FOLDER_ID, sheet_hint)
                if sheet:
                    result = find_task_row(sheet, task["name"])
                    if result:
                        found_row, found_col_map = result
            if not found_row:
                try:
                    children = ss_client.Folders.get_folder_children(SMARTSHEET_FOLDER_ID)
                    for item in children.data:
                        s = ss_client.Sheets.get_sheet(item.id)
                        result = find_task_row(s, task["name"])
                        if result:
                            sheet = s
                            found_row, found_col_map = result
                            break
                except Exception as e:
                    log.error(f"Erro ao buscar sheet: {e}")

            # Aplica a mudança uma única vez
            if found_row:
                ok, updates, status_final = apply_smartsheet_change(task["name"], task["suggestions"], sheet, found_row, found_col_map)
                if ok:
                    approvals.append((task["name"], updates))
                    # Auto-check se status virou done
                    if status_final in DONE_VALUES:
                        if add_check_to_thread(task["name"], threads_map):
                            checked.add(task["name"])
                else:
                    rejected.append((task["name"], updates[0]))
            else:
                rejected.append((task["name"], "sheet não encontrado"))

    save_processed(processed)

    if approvals or rejected:
        send_confirm(approvals, rejected, checked, dm_channel_id)
        log.info(f"Aprovações: {len(approvals)} | Rejeitadas: {len(rejected)} | Checkadas: {len(checked)}")
    else:
        log.info("Nenhuma aprovação detectada")


if __name__ == "__main__":
    run()

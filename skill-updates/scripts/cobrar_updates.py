#!/usr/bin/env python3
"""
cobrar_updates.py
Cobra update de cada tarefa listada no briefing.
Ignora threads com ✅ de bot/Bianca (tarefa concluída no canal).

Comportamento por thread:
- Dia 0, 10h:          primeira msg (cobra detalhes)
- Dia +1, 10h:         segunda msg (se ninguém respondeu)
- Dia prazo -1, 10h:   terceira msg (lembrete de vencimento, se não concluída no SS)
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

SLACK_BOT_TOKEN   = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL_ID  = os.environ["SLACK_CHANNEL_ID"]
SMARTSHEET_TOKEN  = os.environ["SMARTSHEET_TOKEN"]
SMARTSHEET_FOLDER_ID = int(os.environ["SMARTSHEET_FOLDER_ID"])
client = WebClient(token=SLACK_BOT_TOKEN)
ss_client = smartsheet.Smartsheet(SMARTSHEET_TOKEN)

TZ = dt.timezone(dt.timedelta(hours=-3))

# ── Detecção de thread concluída ────────────────────────────────────────────

BOT_USER_ID = None

def get_bot_user_id():
    global BOT_USER_ID
    if BOT_USER_ID:
        return BOT_USER_ID
    BOT_USER_ID = client.auth_test()["user_id"]
    return BOT_USER_ID


def is_thread_done(msg_ts: str, channel_id: str = None) -> bool:
    """True se a thread tem ✅ reactions de bot ou Bianca."""
    if channel_id is None:
        channel_id = SLACK_CHANNEL_ID
    try:
        result = client.reactions_get(channel=channel_id, timestamp=msg_ts)
        for reaction in result.get("message", {}).get("reactions", []):
            if reaction.get("name") in ("white_check_mark", "check", "heavy_check_mark"):
                users = reaction.get("users", [])
                if get_bot_user_id() in users or os.environ.get("BIANCA_USER_ID") in users:
                    return True
        return False
    except SlackApiError:
        return False


# ── SmartSheet helpers ────────────────────────────────────────────────────────

DONE_VALUES = {"Concluída", "Concluida", "Cancelada", "Cancelado"}
COL_TASK     = "Atividade"
COL_STATUS   = "Status"
COL_END_DATE = "Data de Fim Planejada"


def get_col_map(sheet):
    return {col.title: col.id for col in sheet.columns}


def cell_value(row, col_map, col_name):
    col_id = col_map.get(col_name)
    if col_id is None:
        return None
    for cell in row.cells:
        if cell.column_id == col_id:
            return cell.display_value or cell.value
    return None


def parse_date(raw):
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def get_task_deadline(sheet_name_hint, task_name_hint):
    """
    Busca a Data de Fim Planejada e Status de uma tarefa no SmartSheet.
    Retorna (end_date: date|None, status: str|None).
    """
    try:
        children = ss_client.Folders.get_folder_children(SMARTSHEET_FOLDER_ID)
        for item in children.data:
            # Ache a sheet que casa com o hint (pode ter prefixo [12345])
            if sheet_name_hint.upper() in item.name.upper():
                sheet = ss_client.Sheets.get_sheet(item.id)
                col_map = get_col_map(sheet)
                if COL_END_DATE not in col_map:
                    continue
                # Busca fuzzy pelo nome da tarefa
                task_lower = task_name_hint.lower()
                for row in sheet.rows:
                    task_val = cell_value(row, col_map, COL_TASK) or ""
                    if task_lower in task_val.lower() or task_val.lower() in task_lower:
                        end_raw = cell_value(row, col_map, COL_END_DATE)
                        status  = cell_value(row, col_map, COL_STATUS) or ""
                        return parse_date(end_raw), status
    except Exception as e:
        log.error(f"Erro ao buscar deadline no SmartSheet: {e}")
    return None, None


# ── Extrair tarefas do formato do briefing ─────────────────────────────────

TASK_BLOCK_RE = re.compile(
    r"📌\s*\[(.+?)\]\s*\[(.+?)\]\s*(.+?)(?:\n|$)",
    re.DOTALL,
)


def extract_tasks(text):
    tasks = []
    for m in TASK_BLOCK_RE.finditer(text):
        tasks.append({
            "sheet": m.group(1).strip(),
            "team":  m.group(2).strip(),
            "name":  m.group(3).strip(),
        })
    return tasks


# ── Mensagens ────────────────────────────────────────────────────────────────


def build_first_message(tasks):
    return (
        "Olá, bom dia! :wave:\n"
        "Preciso do seu update sobre essa tarefa!\n"
        "Me conte com o máximo de detalhes como está seu andamento e se temos algum gargalo ou previsão de atraso.\n\n"
        "Obrigada! :raised_hands:"
    )


def build_followup_message(tasks):
    return (
        "Oie, passando aqui novamente pois não tive retorno. "
        "Pode me atualizar da tarefa?"
    )


def build_reminder_message(tasks):
    """Lembrete de vencimento amanhã."""
    return (
        "Olá!\n"
        "O vencimento dessa tarefa é amanhã. "
        "Tudo certo para a sua entrega?"
    )


# ── Detecção de timing ───────────────────────────────────────────────────────

# Marcadores únicos para identificar qual msg o bot já enviou
MARKER_FIRST    = "Preciso do seu update sobre essa tarefa"
MARKER_FOLLOWUP = "passando aqui novamente pois não tive retorno"
MARKER_REMINDER = "O vencimento dessa tarefa é amanhã"


def bot_sent_message(channel_id, thread_ts, marker):
    """True se o bot já enviou msg contendo o marker nesta thread."""
    try:
        result = client.conversations_replies(channel=channel_id, ts=thread_ts)
        bot_id = get_bot_user_id()
        first = True
        for msg in result.get("messages", []):
            if first:
                first = False
                continue
            if msg.get("user") == bot_id and marker in msg.get("text", ""):
                return True
        return False
    except SlackApiError:
        return False


def has_human_replied(channel_id, thread_ts):
    """True se já houve reply de pessoa (não do bot) nesta thread."""
    try:
        result = client.conversations_replies(channel=channel_id, ts=thread_ts)
        bot_id = get_bot_user_id()
        for msg in result.get("messages", []):
            if msg.get("user") != bot_id and msg.get("user") is not None:
                return True
        return False
    except SlackApiError:
        return False


def ts_to_date(thread_ts_str):
    """Converte timestamp Slack (float) para date em BRT."""
    ts = float(thread_ts_str)
    utc_dt = dt.datetime.utcfromtimestamp(ts)
    brt_dt = utc_dt.replace(tzinfo=dt.timezone.utc).astimezone(TZ)
    return brt_dt.date()


# ── Buscar threads abertas ─────────────────────────────────────────────────

BRIEFING_STATE = os.path.expanduser("~/.hermes/scripts/.briefing_posted")


def find_open_threads():
    """
    Retorna threads abertas (sem ✅), usando SOMENTE o .briefing_posted como fonte.
    Se a tarefa foi removida do canal mas ainda está no estado, NÃO cobra.
    Se a thread foi apagada do Slack, o reactions_get falha e a thread é ignorada.
    Cada entry: {ts, channel_id, sheet_hint, task_name}.
    """
    threads = []
    known_keys = set()

    # Fonte única: .briefing_posted (suporta formato 4 e 5 campos)
    # Formato: sheet|task|ts|channel|team
    try:
        with open(BRIEFING_STATE, encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split("|")
                if len(parts) < 3:
                    continue
                ts = parts[2]
                # channel pode estar em parts[3] (formato 4 campos) ou vir vazio
                # team pode estar em parts[4] (formato 5 campos)
                channel_id = parts[3] if len(parts) >= 4 and parts[3] else SLACK_CHANNEL_ID
                # sheet_hint vem entre colchetes no início: [12235] São Miguel...
                raw_sheet = parts[0]
                # Extrai só o nome (sem [numero])
                m = re.match(r"\[\d+\]\s*(.+)", raw_sheet)
                sheet_hint = m.group(1).strip() if m else raw_sheet.strip()
                task_name = parts[1]
                key = f"{ts}|{channel_id}"
                if key in known_keys:
                    continue
                known_keys.add(key)
                if is_thread_done(ts, channel_id):
                    continue
                threads.append({
                    "ts": ts,
                    "channel_id": channel_id,
                    "sheet_hint": sheet_hint,
                    "task_name": task_name,
                })
    except FileNotFoundError:
        pass

    return threads


# ── Main ─────────────────────────────────────────────────────────────────────

def run():
    log.info("Iniciando cobrança de updates")
    threads = find_open_threads()
    log.info(f"Threads abertas encontradas: {len(threads)}")

    count_first = count_followup = count_reminder = 0

    for thread in threads:
        thread_ts   = thread["ts"]
        channel_id  = thread["channel_id"]
        sheet_hint  = thread["sheet_hint"]
        task_name   = thread["task_name"]
        tasks       = [{"name": task_name, "sheet": sheet_hint, "team": ""}]

        thread_date = ts_to_date(thread_ts)
        today       = dt.date.today()

        # ── Horário de envio: 10h (verifica se já passou) ──
        send_time = dt.time(10, 0)
        now = dt.datetime.now(TZ).time()
        if now < send_time:
            continue

        # ── Lembrete de vencimento (dia anterior ao prazo, às 10h) ──
        # ── Buscar tarefa no SmartSheet ──
        end_date, status = get_task_deadline(sheet_hint, task_name)

        # Se não encontrou a tarefa no SmartSheet, pular (pode ter sido movida/renomeada)
        if end_date is None and status is None:
            log.info(f"Tarefa '{task_name}' não encontrada no SmartSheet — ignorando thread {thread_ts}")
            continue

        deadline_tomorrow = end_date == today + dt.timedelta(days=1) if end_date else False
        deadline_today    = end_date == today                      if end_date else False
        is_done = status in DONE_VALUES if status else False
        # ── Buscar tarefa no SmartSheet ──
        end_date, status = get_task_deadline(sheet_hint, task_name)

        # Se não encontrou a tarefa no SmartSheet, pular (pode ter sido movida/renomeada)
        if end_date is None and status is None:
            log.info(f"Tarefa '{task_name}' não encontrada no SmartSheet — ignorando thread {thread_ts}")
            continue

        deadline_tomorrow = end_date == today + dt.timedelta(days=1) if end_date else False
        deadline_today    = end_date == today                      if end_date else False
        is_done = status in DONE_VALUES if status else False
        # ── Buscar tarefa no SmartSheet ──
        end_date, status = get_task_deadline(sheet_hint, task_name)

        # Se não encontrou a tarefa no SmartSheet, pular (pode ter sido movida/renomeada)
        if end_date is None and status is None:
            log.info(f"Tarefa '{task_name}' não encontrada no SmartSheet — ignorando thread {thread_ts}")
            continue

        deadline_tomorrow = end_date == today + dt.timedelta(days=1) if end_date else False
        deadline_today    = end_date == today                      if end_date else False
        is_done = status in DONE_VALUES if status else False
        # ── Buscar tarefa no SmartSheet ──
        end_date, status = get_task_deadline(sheet_hint, task_name)

        # Se não encontrou a tarefa no SmartSheet, pular (pode ter sido movida/renomeada)
        if end_date is None and status is None:
            log.info(f"Tarefa '{task_name}' não encontrada no SmartSheet — ignorando thread {thread_ts}")
            continue

        deadline_tomorrow = end_date == today + dt.timedelta(days=1) if end_date else False
        deadline_today    = end_date == today                      if end_date else False
        is_done = status in DONE_VALUES if status else False

        if (deadline_tomorrow or deadline_today) and not is_done:
            if not bot_sent_message(channel_id, thread_ts, MARKER_REMINDER):
                try:
                    client.chat_postMessage(
                        channel=channel_id,
                        thread_ts=thread_ts,
                        text=build_reminder_message(tasks),
                    )
                    log.info(f"Reminder na thread {thread_ts}")
                    count_reminder += 1
                except SlackApiError as e:
                    log.error(f"Erro ao enviar reminder {thread_ts}: {e.response['error']}")
            continue

        # ── Primeira msg: dia 0, 10h ──
        if not bot_sent_message(channel_id, thread_ts, MARKER_FIRST):
            try:
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=build_first_message(tasks),
                )
                log.info(f"Primeira cobrança na thread {thread_ts}")
                count_first += 1
            except SlackApiError as e:
                log.error(f"Erro ao enviar primeira msg {thread_ts}: {e.response['error']}")
            continue

        # ── Segunda msg: dia +1, 10h, se ninguém respondeu ──
        next_day = thread_date + dt.timedelta(days=1)
        if today >= next_day and not has_human_replied(channel_id, thread_ts):
            if not bot_sent_message(channel_id, thread_ts, MARKER_FOLLOWUP):
                try:
                    client.chat_postMessage(
                        channel=channel_id,
                        thread_ts=thread_ts,
                        text=build_followup_message(tasks),
                    )
                    log.info(f"Follow-up na thread {thread_ts}")
                    count_followup += 1
                except SlackApiError as e:
                    log.error(f"Erro ao enviar follow-up {thread_ts}: {e.response['error']}")

    log.info(
        f"Concluído: {count_first} primeira(s), "
        f"{count_followup} follow-up(s), {count_reminder} lembrete(s)"
    )


if __name__ == "__main__":
    run()

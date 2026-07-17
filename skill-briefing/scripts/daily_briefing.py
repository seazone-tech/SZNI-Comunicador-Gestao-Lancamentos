#!/usr/bin/env python3
"""
cronograma-briefing.py
Lê cronogramas do Smartsheet e envia resumo diário no Slack.
Uma tarefa só é postada uma vez — atualizações ficam na thread existente.
Roda via cron às 8h30.
"""

import logging
import os
import re
from collections import defaultdict
from datetime import date
from difflib import get_close_matches

import smartsheet
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

_script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.expanduser("~/.hermes/scripts/.env")) or load_dotenv(os.path.join(_script_dir, ".env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

SMARTSHEET_TOKEN = os.environ["SMARTSHEET_TOKEN"]
FOLDER_ID = int(os.environ["SMARTSHEET_FOLDER_ID"])
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")
BIANCA_USER_ID = os.environ["BIANCA_USER_ID"]
STATUS_DONE_VALUES = {
    v.strip() for v in os.getenv(
        "STATUS_DONE_VALUES",
        "Concluída,Concluida,Cancelada,Cancelado",
    ).split(",")
}

STATE_FILE = os.path.expanduser("~/.hermes/scripts/.briefing_posted")
BOT_USER_ID_CACHE = None

COL_TASK = "Atividade"
COL_STATUS = "Status"
COL_START_DATE = "Data de Início Planejada"
COL_END_DATE = "Data de Fim Planejada"
COL_ASSIGNEE = "Time Responsável"
COL_DEPENDENCY = "Dependência"

TEAM_SLACK_MAP = {
    "Gestão Lançamentos": ["U06093URWPR"],
    "MARCO": ["U06093URWPR"],
    "Diretoria": ["U06093URWPR"],
    "Financeiro": ["U082WFCHEBZ"],
    "Comercial": ["U06093URWPR"],
    "Marketplace": ["U06093URWPR"],
    "Fornecedores Lançamentos e Obras": ["U090UKQAXFD"],
    "Fornecedores Lançamentos": ["U090UKQAXFD"],
    "Obras": ["U090UKQAXFD"],
    "Orçamentos Lançamentos": ["U090UKQAXFD"],
    "Compra de Terrenos": ["U05Q6PXC9KR"],
    "Análise de Terrenos": ["U05Q6PXC9KR"],
    "Jurídico": ["U046CCULGJF"],
    "Projetos Lançamentos": ["U07DXLFP1GT"],
    "Marketing": ["U0A8H79PACB"],
}


def parse_channel_map() -> dict:
    """Lê CHANNEL_MAP do env. Formato: 'Nome Sheet:CHANNEL_ID,...'."""
    raw = os.getenv("CHANNEL_MAP", "")
    mapping = {}
    for item in raw.split(","):
        if ":" in item:
            sheet, channel = item.strip().split(":", 1)
            mapping[sheet.strip()] = channel.strip()
    return mapping


def get_channel_for_sheet(sheet_name: str, channel_map: dict) -> str | None:
    """Retorna o channel_id para a sheet, via fuzzy match."""
    if not channel_map:
        return SLACK_CHANNEL_ID or None
    if sheet_name in channel_map:
        return channel_map[sheet_name]
    matches = get_close_matches(sheet_name, channel_map.keys(), n=1, cutoff=0.6)
    if matches:
        return channel_map[matches[0]]
    return None


def slack_mentions(team_name: str) -> str:
    """Retorna string com todas as @menções do time, ou nome simples se não mapeado."""
    if not team_name or team_name == "_sem time_":
        return "_sem time responsável_"
    uids = TEAM_SLACK_MAP.get(team_name.strip())
    if not uids:
        log.warning("Time '%s' não encontrado no TEAM_SLACK_MAP", team_name)
        return team_name.strip()
    return " ".join(f"<@{uid}>" for uid in uids)


def get_bot_user_id(client: WebClient) -> str:
    global BOT_USER_ID_CACHE
    if BOT_USER_ID_CACHE:
        return BOT_USER_ID_CACHE
    BOT_USER_ID_CACHE = client.auth_test()["user_id"]
    return BOT_USER_ID_CACHE


def is_thread_done(client: WebClient, channel_id: str, msg_ts: str) -> bool:
    """Retorna True se a thread tem reação ✅ do bot ou da Bianca."""
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
    """Mapeia nome de coluna para id da coluna."""
    col_map = {col.title: col.id for col in sheet.columns}
    log.info("Colunas encontradas: %s", list(col_map.keys()))
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
    """Converte valor de data para date. Retorna None se inválido."""
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def parse_row(row, col_map: dict) -> dict | None:
    """Extrai os campos relevantes de uma linha do Smartsheet."""
    task = cell_value(row, col_map, COL_TASK)
    status = cell_value(row, col_map, COL_STATUS) or ""
    assignee = cell_value(row, col_map, COL_ASSIGNEE) or ""
    start_raw = cell_value(row, col_map, COL_START_DATE)
    end_raw = cell_value(row, col_map, COL_END_DATE)
    dependency = cell_value(row, col_map, COL_DEPENDENCY) or ""

    if not task:
        return None

    start_date = parse_date(start_raw)
    end_date = parse_date(end_raw)
    row_num = str(row.row_number) if hasattr(row, "row_number") else ""

    return {
        "task": task,
        "status": status,
        "assignee": assignee,
        "start_date": start_date,
        "end_date": end_date,
        "dependency": dependency,
        "row_num": row_num,
    }


def fmt_date(d: date | None) -> str:
    """Formata data como dd/mm ou '—' se None."""
    return d.strftime("%d/%m") if d else "—"


def build_task_message(task: dict, mentions: str) -> str:
    inicio = fmt_date(task["start_date"])
    fim = fmt_date(task["end_date"])
    status = task["status"] or "sem status"
    return (
        f"Responsável: {mentions}\n"
        f"Status: {status} | Início: {inicio} — Fim: {fim}"
    )


def build_task_header(task: dict, sheet_name: str, team: str) -> str:
    fim = fmt_date(task["end_date"])
    return f"📌 [{sheet_name}] [{team.upper()}] {task['task']} [{fim}]"


def load_posted_tasks(channel_map: dict | None = None) -> dict[str, tuple]:
    """
    Retorna {sheet|task_name: (thread_ts, channel_id, team)} de tarefas já postadas.
    Suporta formato antigo (3-4 campos) e novo (5 campos com team).
    """
    try:
        with open(STATE_FILE) as f:
            result = {}
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("|")
                if len(parts) < 3:
                    continue
                key = "|".join(parts[:2])
                ts = parts[2]
                channel_id = parts[3] if len(parts) >= 4 and parts[3] else ""
                team = parts[4] if len(parts) >= 5 else ""
                if not channel_id:
                    sheet_name = parts[0]
                    if channel_map:
                        channel_id = get_channel_for_sheet(sheet_name, channel_map) or ""
                    if not channel_id:
                        channel_id = SLACK_CHANNEL_ID
                result[key] = (ts, channel_id, team)
            return result
    except FileNotFoundError:
        return {}


def save_posted_tasks(posted: dict[str, tuple]):
    """Salva {sheet|task_name: (thread_ts, channel_id, team)} no arquivo de estado."""
    with open(STATE_FILE, "w") as f:
        for key, value in posted.items():
            if isinstance(value, tuple):
                ts, channel_id = value[0], value[1]
                team = value[2] if len(value) > 2 else ""
            else:
                ts, channel_id, team = value, "", ""
            f.write(f"{key}|{ts}|{channel_id}|{team}\n")


def apply_check_to_thread(client: WebClient, channel_id: str, thread_ts: str) -> bool:
    """Adiciona reação ✅ na thread, só se o bot ainda não tiver reagido."""
    # Primeiro: confere se o bot já reagiu nesta thread
    try:
        result = client.reactions_get(channel=channel_id, timestamp=thread_ts)
        bot_id = get_bot_user_id(client)
        for reaction in result.get("message", {}).get("reactions", []):
            if reaction.get("name") in ("white_check_mark", "check", "heavy_check_mark"):
                if bot_id in reaction.get("users", []):
                    log.debug("Bot já tem check em %s|%s — pulando", channel_id, thread_ts)
                    return True
    except SlackApiError:
        pass  # continua e tenta adicionar mesmo assim

    # Segundo: adiciona a reação
    try:
        client.reactions_add(
            channel=channel_id,
            timestamp=thread_ts,
            name="white_check_mark",
        )
        log.info("✅ Check adicionado em %s|%s", channel_id, thread_ts)
        return True
    except SlackApiError as e:
        log.warning("Erro ao adicionar check em %s|%s: %s", channel_id, thread_ts, e.response.get("error", ""))
        return False


def find_thread_by_task_name(client: WebClient, channel_id: str, task_name: str) -> str | None:
    """
    Busca no histórico do canal uma mensagem do bot que contenha o nome da tarefa.
    Retorna o ts da mensagem (thread raiz) se encontrar, ou None.
    """
    if not channel_id:
        return None
    try:
        result = client.conversations_history(channel=channel_id, limit=200)
        bot_id = get_bot_user_id(client)
        for msg in result.get("messages", []):
            if msg.get("user") != bot_id:
                continue
            if task_name in msg.get("text", ""):
                return msg["ts"]
    except SlackApiError as e:
        log.warning("Erro ao buscar thread de '%s': %s", task_name[:30], e.response.get("error", ""))
    return None


def sync_done_tasks(ss_client, slack_client: WebClient, channel_map: dict):
    """
    Primeira função do script: varre todas as sheets, identifica tarefas com status done
    e adiciona ✅ nas threads correspondentes no Slack.
    """
    log.info("=== Sincronizando tarefas concluídas: SmartSheet → Slack ===")

    # 1. Carrega todas as tarefas done do SmartSheet
    done_tasks = {}  # key: "sheet_name|task_name" -> {channel_id, row_num}
    try:
        folder_children = ss_client.Folders.get_folder_children(FOLDER_ID)
        all_sheets = [item for item in folder_children.data]
    except Exception as e:
        log.error("Erro ao buscar sheets: %s", e)
        all_sheets = []

    for sheet_ref in all_sheets:
        sheet_name = sheet_ref.name
        sheet_id = sheet_ref.id
        channel_id = get_channel_for_sheet(sheet_name, channel_map)

        try:
            sheet = ss_client.Sheets.get_sheet(sheet_id)
        except Exception as e:
            log.error("Erro ao ler sheet '%s': %s", sheet_name, e)
            continue

        col_map = get_col_map(sheet)
        for row in sheet.rows:
            parsed = parse_row(row, col_map)
            if parsed is None:
                continue
            if parsed["status"].strip() in STATUS_DONE_VALUES:
                key = f"{sheet_name}|{parsed['task']}"
                done_tasks[key] = {
                    "channel_id": channel_id,
                    "row_num": parsed["row_num"],
                    "sheet_name": sheet_name,
                    "task": parsed["task"],
                }

    if not done_tasks:
        log.info("Nenhuma tarefa com status done encontrada no SmartSheet")
        return

    log.info("%s tarefa(s) com status done encontrada(s)", len(done_tasks))

    # 2. Carrega threads já postadas
    posted_tasks = load_posted_tasks(channel_map)

    # 3. Para cada tarefa done, aplica check na thread correspondente
    checked = skipped = 0
    for key, task_info in done_tasks.items():
        task_name = task_info["task"]
        channel_id = task_info["channel_id"]
        sheet_name = task_info["sheet_name"]

        thread_ts = None
        if key in posted_tasks:
            thread_ts, _, _ = posted_tasks[key]
        else:
            # Tarefa done mas não está no .briefing_posted:
            # busca no histórico do canal pela mensagem raiz do bot contendo o nome da tarefa
            thread_ts = find_thread_by_task_name(slack_client, channel_id, task_name)
            if thread_ts:
                log.info("Tarefa done '%s' encontrada via busca no histórico", task_name)
            else:
                log.info("Tarefa done '%s' sem thread encontrada — ignorando", task_name)
                skipped += 1
                continue

        if apply_check_to_thread(slack_client, channel_id, thread_ts):
            checked += 1
        else:
            skipped += 1

    log.info("=== Sync done concluída: %s check(s), %s ignorado(s) ===", checked, skipped)


def main():
    today = date.today()

    log.info("Iniciando briefing para %s", today)

    ss_client = smartsheet.Smartsheet(SMARTSHEET_TOKEN)
    slack_client = WebClient(token=SLACK_BOT_TOKEN)
    bot_id = get_bot_user_id(slack_client)

    channel_map = parse_channel_map()
    if channel_map:
        log.info("CHANNEL_MAP carregado: %s", list(channel_map.keys()))
    else:
        log.warning("CHANNEL_MAP não configurado — usando SLACK_CHANNEL_ID como fallback")

    # ── PRIMEIRO: sincroniza tarefas done do SmartSheet → check no Slack ──
    sync_done_tasks(ss_client, slack_client, channel_map)

    posted_tasks = load_posted_tasks(channel_map)
    log.info("Tarefas já postadas no estado: %s", len(posted_tasks))

    existing_done_threads = set()
    canais_a_verificar = list(channel_map.values()) if channel_map else ([SLACK_CHANNEL_ID] if SLACK_CHANNEL_ID else [])
    for canal in canais_a_verificar:
        try:
            result = slack_client.conversations_history(channel=canal, limit=200)
            for msg in result.get("messages", []):
                if msg.get("user") != bot_id:
                    continue
                if is_thread_done(slack_client, canal, msg["ts"]):
                    existing_done_threads.add(msg["ts"])
        except SlackApiError as e:
            log.error("Erro ao buscar histórico do canal %s: %s", canal, e)

    folder_children = ss_client.Folders.get_folder_children(FOLDER_ID)
    sheets = [item for item in folder_children.data]
    log.info("Folder: %s sheet(s) encontrada(s)", len(sheets))

    messages_sent = 0
    updated_posted = dict(posted_tasks)

    for sheet_ref in sheets:
        sheet_name = sheet_ref.name
        sheet_id = sheet_ref.id

        channel_id = get_channel_for_sheet(sheet_name, channel_map)
        if not channel_id:
            log.warning("Sheet '%s': sem canal no CHANNEL_MAP — pulando", sheet_name)
            continue

        try:
            sheet = ss_client.Sheets.get_sheet(sheet_id)
        except Exception as e:
            log.error("Erro ao ler sheet '%s': %s", sheet_name, e)
            continue

        col_map = get_col_map(sheet)
        required = [COL_TASK, COL_STATUS, COL_END_DATE, COL_ASSIGNEE, COL_START_DATE, COL_DEPENDENCY]
        missing = [c for c in required if c not in col_map]
        if missing:
            log.warning("Sheet '%s': colunas ausentes %s — pulando", sheet_name, missing)
            continue

        all_tasks: dict[str, str] = {}
        for row in sheet.rows:
            parsed = parse_row(row, col_map)
            if parsed is None:
                continue
            all_tasks[parsed["row_num"]] = parsed["status"]

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
                nums = re.findall(r"\d+", dep)
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
            log.info("Sheet '%s': sem pendências — sem mensagem", sheet_name)
            continue

        by_team: dict[str, list] = defaultdict(list)
        for task in pending:
            team = task["assignee"].strip() if task["assignee"] else "_sem time_"
            by_team[team].append(task)

        for team, team_tasks in by_team.items():
            for task in team_tasks:
                task_key = f"{sheet_name}|{task['task']}"

                if task_key in posted_tasks:
                    thread_ts, existing_channel, existing_team = posted_tasks[task_key]
                    updated_posted[task_key] = (thread_ts, existing_channel, team)
                    if thread_ts in existing_done_threads:
                        log.info("Sheet '%s': tarefa '%s' com ✅ — mantendo no estado", sheet_name, task["task"][:40])
                    else:
                        log.info("Sheet '%s': tarefa '%s' já postada — mantendo", sheet_name, task["task"][:40])
                        # Se o time mudou, notifica o novo responsável
                        if team != existing_team and team and team != "_sem time_":
                            mentions = slack_mentions(team)
                            msg = f"Olá! O responsável por esta tarefa mudou. {mentions}, agora essa task é sua."
                            try:
                                slack_client.chat_postMessage(channel=existing_channel, text=msg, thread_ts=thread_ts)
                                log.info("Sheet '%s': time mudou de '%s' para '%s' — notificado", sheet_name, existing_team, team)
                            except SlackApiError as e:
                                log.warning("Erro ao notificar troca de time em '%s': %s", task["task"][:40], e.response.get("error", ""))
                    continue

                mentions = slack_mentions(team)
                header = build_task_header(task, sheet_name, team)
                body = build_task_message(task, mentions)

                posted_ok = False
                for attempt in range(3):
                    try:
                        result = slack_client.chat_postMessage(channel=channel_id, text=header)
                        thread_ts = result["ts"]
                        slack_client.chat_postMessage(channel=channel_id, text=body, thread_ts=thread_ts)
                        updated_posted[task_key] = (thread_ts, channel_id, team)
                        log.info("Sheet '%s' | '%s': thread criada em %s (%s)", sheet_name, team, channel_id, task["task"][:40])
                        messages_sent += 1
                        posted_ok = True
                        break
                    except SlackApiError as e:
                        error = e.response.get("error", "")
                        log.warning("Tentativa %s falhou: %s", attempt + 1, error)
                        if attempt < 2:
                            import time
                            time.sleep(2)
                        else:
                            log.error("Erro Slack para '%s' | '%s': %s", sheet_name, team, error)
                if not posted_ok:
                    log.error("Falha definitiva ao postar '%s' na sheet '%s'", task["task"][:40], sheet_name)

    save_posted_tasks(updated_posted)
    log.info("Briefing concluído: %s nova(s) mensagem(s) | %s total no estado", messages_sent, len(updated_posted))

    done_keys = set()
    for sheet_ref in sheets:
        try:
            sheet = ss_client.Sheets.get_sheet(sheet_ref.id)
            col_map = get_col_map(sheet)
            for row in sheet.rows:
                parsed = parse_row(row, col_map)
                if parsed is None:
                    continue
                if parsed["status"].strip() in STATUS_DONE_VALUES:
                    key = f"{sheet_ref.name}|{parsed['task']}"
                    done_keys.add(key)
        except Exception:
            pass

    if done_keys:
        cleaned = {k: v for k, v in updated_posted.items() if k not in done_keys}
        if len(cleaned) < len(updated_posted):
            removed = len(updated_posted) - len(cleaned)
            save_posted_tasks(cleaned)
            log.info("Removidas %s tarefa(s) done do estado (total: %s)", removed, len(cleaned))


if __name__ == "__main__":
    main()

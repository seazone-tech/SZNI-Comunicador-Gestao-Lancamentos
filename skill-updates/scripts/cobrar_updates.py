#!/usr/bin/env python3
"""
cobrar_updates.py
Cobra update de cada tarefa listada no briefing.
Ignora threads com ✅ de bot/Bianca (tarefa concluída no canal).
Busca TODAS as threads abertas, independente de data.

Comportamento por thread (máximo 3 cobranças):
- 1ª cobrança: mensagem completa com 3 perguntas (primeira vez, ninguém respondeu)
- 2ª cobrança: follow-up curto (depois de já ter resposta humana)
- 3ª cobrança: só se for 1 dia antes da data de fim planejada
- 4ª+ : pula
"""

import os
import re
import logging
import datetime as dt
from difflib import get_close_matches
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

SLACK_BOT_TOKEN  = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")  # fallback de compatibilidade
client = WebClient(token=SLACK_BOT_TOKEN)


def parse_channel_map() -> dict:
    """Lê CHANNEL_MAP do env. Formato: 'Nome Sheet:CHANNEL_ID,...'"""
    raw = os.getenv("CHANNEL_MAP", "")
    mapping = {}
    for item in raw.split(","):
        if ":" in item:
            sheet, channel = item.strip().split(":", 1)
            mapping[sheet.strip()] = channel.strip()
    return mapping

TZ = dt.timezone(dt.timedelta(hours=-3))

# ── Detecção de thread concluída ────────────────────────────────────────────

BOT_USER_ID = None

def get_bot_user_id():
    global BOT_USER_ID
    if BOT_USER_ID:
        return BOT_USER_ID
    BOT_USER_ID = client.auth_test()["user_id"]
    return BOT_USER_ID


def is_thread_done(msg_ts: str, channel_id: str) -> bool:
    """True se a thread tem ✅ reactions de bot ou Bianca."""
    try:
        result = client.reactions_get(channel=channel_id, timestamp=msg_ts)
        for reaction in result.get("message", {}).get("reactions", []):
            if reaction.get("name") in ("white_check_mark", "check", "heavy_check_mark"):
                users = reaction.get("users", [])
                bot_id = get_bot_user_id()
                if bot_id in users or os.environ.get("BIANCA_USER_ID") in users:
                    return True
        return False
    except SlackApiError:
        return False


# ── Extrair tarefas do formato do briefing ─────────────────────────────────

# Slack converte 📌 em :pushpin: no payload, então a regex aceita ambos
# Formato: 📌 [sheet] [team] nome da tarefa [DD/MM]
# O campo de data é opcional (para retrocompatibilidade com mensagens antigas)
TASK_BLOCK_RE = re.compile(
    r"(?:📌|:pushpin:)\s*\[(.+?)\]\s*\[(.+?)\]\s*(.+?)(?:\s*\[(\d{2}/\d{2})\])?(?:\n|$)",
    re.DOTALL,
)


def extract_tasks(text):
    tasks = []
    for m in TASK_BLOCK_RE.finditer(text):
        tasks.append({
            "sheet": m.group(1).strip(),
            "team":  m.group(2).strip(),
            "name":  m.group(3).strip(),
            "end_date_str": m.group(4).strip() if m.group(4) else None,
        })
    return tasks


def parse_end_date(end_date_str: str | None, year_offset: int = 0) -> dt.date | None:
    """
    Converte 'DD/MM' em date (ano atual ou atual+1 se a data já passou no ano).
    Retorna None se não conseguir converter.
    """
    if not end_date_str:
        return None
    try:
        day, month = end_date_str.strip().split("/")
        today = dt.date.today()
        year = today.year + year_offset
        d = dt.date(year, int(month), int(day))
        # Se a data já passou este ano, assume que é do próximo
        if d < today:
            d = dt.date(year + 1, int(month), int(day))
        return d
    except (ValueError, IndexError):
        return None


def is_one_day_before(channel_id: str, root_ts: str) -> bool:
    """
    Verifica se HOJE é exatamente 1 dia antes da data de fim da tarefa.
    Compara com a data extraída do header da mensagem raiz (messages[0]).
    """
    try:
        # Usa conversations_replies para buscar a mensagem raiz diretamente
        result = client.conversations_replies(channel=channel_id, ts=root_ts)
        msgs = result.get("messages", [])
        if not msgs:
            return False
        root_msg = msgs[0]
        tasks = extract_tasks(root_msg.get("text", ""))
        if not tasks:
            return False
        end_date = parse_end_date(tasks[0].get("end_date_str"))
        if end_date is None:
            return False
        today = dt.date.today()
        diff = (end_date - today).days
        return diff == 1
    except SlackApiError:
        return False


# ── Mensagens ────────────────────────────────────────────────────────────────

def build_first_message(tasks):
    """Primeira cobrança — mensagem completa com 3 perguntas."""
    blocks = []
    for t in tasks:
        blocks.append(
            f"📋 {t['name']}\n"
            f"   • Começou? → ainda não / sim, desde [DD/MM]\n"
            f"   • Fecha no prazo? → sim / não, nova previsão [DD/MM]\n"
            f"   • Gargalo? → não / sim: [descrever]\n"
        )
    return (
        "Olá, bom dia! :wave:\n"
        "Preciso do seu update sobre essa tarefa:\n\n"
        + "\n".join(blocks)
        + "\nObrigada! :raised_hands:"
    )


def build_followup_message(tasks):
    """Cobrança seguinte — mensagem curta pedindo feedback."""
    blocks = []
    for t in tasks:
        blocks.append(f"📋 {t['name']}")
    return (
        "Oie, passando para pegar um feedback dessa tarefa.\n"
        "Caso nada tenha mudado desde a sua última atualização, "
        "apenas reaja com um check nessa mensagem.\n"
        "Caso tivemos algum avanço, comente aqui na thread.\n\n"
        + "\n".join(blocks)
    )


def has_human_replied(channel, thread_ts):
    """True se já houve reply de pessoa (não do bot) nessa thread."""
    try:
        result = client.conversations_replies(channel=channel, ts=thread_ts)
        bot_id = get_bot_user_id()
        for msg in result.get("messages", []):
            if msg.get("user") != bot_id and msg.get("user") is not None:
                return True
        return False
    except SlackApiError:
        return False


def bot_already_followed_up(channel, thread_ts):
    """True se o bot já enviou mensagem de follow-up nessa thread."""
    try:
        result = client.conversations_replies(channel=channel, ts=thread_ts)
        bot_id = get_bot_user_id()
        for msg in result.get("messages", []):
            if msg.get("user") == bot_id and "passando para pegar um feedback" in msg.get("text", ""):
                return True
        return False
    except SlackApiError:
        return False


# ── Buscar threads abertas ──────────────────────────────────────────────────

def find_open_threads(channel_id: str) -> list:
    """Retorna threads abertas (sem ✅ de conclusão) em um canal específico."""
    bot_id = get_bot_user_id()
    threads = []
    try:
        result = client.conversations_history(channel=channel_id, limit=200)
        for msg in result.get("messages", []):
            if msg.get("user") != bot_id:
                continue
            # Slack converte 📌 em :pushpin: no payload, então checa ambos
            if "📌" not in msg.get("text", "") and ":pushpin:" not in msg.get("text", ""):
                continue
            if is_thread_done(msg["ts"], channel_id):
                log.info(f"Thread {msg['ts']} com ✅ — ignorando")
                continue
            tasks = extract_tasks(msg["text"])
            if not tasks:
                continue
            thread_ts = msg.get("thread_ts") or msg["ts"]
            threads.append({"ts": thread_ts, "root_ts": msg["ts"], "tasks": tasks, "channel_id": channel_id})
    except SlackApiError as e:
        log.error(f"Erro ao buscar histórico do canal {channel_id}: {e}")
    return threads


# ── Main ─────────────────────────────────────────────────────────────────────

def run():
    log.info("Iniciando cobrança de updates")

    channel_map = parse_channel_map()
    if channel_map:
        canais = list(channel_map.values())
        log.info(f"CHANNEL_MAP carregado: {list(channel_map.keys())}")
    else:
        log.warning("CHANNEL_MAP não configurado — usando SLACK_CHANNEL_ID como fallback")
        canais = [SLACK_CHANNEL_ID] if SLACK_CHANNEL_ID else []

    all_threads = []
    for canal in canais:
        threads_canal = find_open_threads(canal)
        log.info(f"Canal {canal}: {len(threads_canal)} thread(s) aberta(s)")
        all_threads.extend(threads_canal)

    log.info(f"Total threads abertas: {len(all_threads)}")

    count = 0
    for thread in all_threads:
        thread_ts = thread["ts"]
        tasks = thread["tasks"]
        channel_id = thread["channel_id"]

        human_replied = has_human_replied(channel_id, thread_ts)
        bot_followed_up = bot_already_followed_up(channel_id, thread_ts)

        # --- 1ª cobrança: ninguém respondeu ainda ---
        if not human_replied:
            try:
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=build_first_message(tasks),
                )
                log.info(f"1ª cobrança na thread {thread_ts} (canal {channel_id})")
                count += 1
            except SlackApiError as e:
                log.error(f"Erro ao cobrar thread {thread_ts}: {e.response['error']}")
            continue

        # --- 2ª cobrança: follow-up curto (já teve resposta, bot ainda não seguiu) ---
        if not bot_followed_up:
            try:
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=build_followup_message(tasks),
                )
                log.info(f"2ª cobrança na thread {thread_ts} (canal {channel_id})")
                count += 1
            except SlackApiError as e:
                log.error(f"Erro ao cobrar thread {thread_ts}: {e.response['error']}")
            continue

        # --- 3ª cobrança: só se for 1 dia antes da data de fim ---
        if is_one_day_before(channel_id, thread["root_ts"]):
            try:
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=build_followup_message(tasks),
                )
                log.info(f"3ª cobrança (1 dia antes do fim) na thread {thread_ts} (canal {channel_id})")
                count += 1
            except SlackApiError as e:
                log.error(f"Erro ao cobrar thread {thread_ts}: {e.response['error']}")
            continue

        log.info(f"Thread {thread_ts}: limite de 3 cobranças atingido — pulando")

    log.info(f"Concluído: {count} cobrança(ões) enviada(s)")


if __name__ == "__main__":
    run()

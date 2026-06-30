#!/usr/bin/env python3
"""
cobrar_updates.py
Cobra update de cada tarefa listada no briefing.
Ignora threads com ✅ de bot/Bianca (tarefa concluída no canal).
Busca TODAS as threads abertas, independente de data.

Comportamento por thread:
- Primeira vez (ninguém respondeu ainda): mensagem completa com 3 perguntas
- Já teve reply mas bot ainda não seguiu: mensagem curta pedindo feedback
- Bot já seguiu: pula (evita duplicar cobrança)
"""

import os
import re
import logging
import datetime as dt
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Tenta Hermes path primeiro (runtime), depois fallback para projeto
_hermes_env = os.path.expanduser("~/.hermes/scripts/.env")
_project_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.env")
load_dotenv(_hermes_env) if os.path.exists(_hermes_env) else load_dotenv(_project_env)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

SLACK_BOT_TOKEN  = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL_ID = os.environ["SLACK_CHANNEL_ID"]
client = WebClient(token=SLACK_BOT_TOKEN)

TZ = dt.timezone(dt.timedelta(hours=-3))

# ── Detecção de thread concluída ────────────────────────────────────────────

BOT_USER_ID = None

def get_bot_user_id():
    global BOT_USER_ID
    if BOT_USER_ID:
        return BOT_USER_ID
    BOT_USER_ID = client.auth_test()["user_id"]
    return BOT_USER_ID


def is_thread_done(msg_ts: str) -> bool:
    """True se a thread tem ✅ reactions de bot ou Bianca."""
    try:
        result = client.reactions_get(channel=SLACK_CHANNEL_ID, timestamp=msg_ts)
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


def bot_already_followed(channel, thread_ts):
    """
    True se o bot já enviou uma mensagem de follow-up nesta thread.
    Exclui a mensagem raiz (primeira mensagem do briefing).
    """
    try:
        result = client.conversations_replies(channel=channel, ts=thread_ts)
        bot_id = get_bot_user_id()
        first = True
        for msg in result.get("messages", []):
            if first:
                first = False  # pula a mensagem raiz
                continue
            if msg.get("user") == bot_id:
                return True
        return False
    except SlackApiError:
        return False


# ── Buscar threads abertas ──────────────────────────────────────────────────

def find_open_threads():
    """Retorna threads abertas (sem ✅ de conclusão)."""
    bot_id = get_bot_user_id()
    threads = []
    try:
        result = client.conversations_history(channel=SLACK_CHANNEL_ID, limit=200)
        for msg in result.get("messages", []):
            if msg.get("user") != bot_id:
                continue
            if "📌" not in msg.get("text", ""):
                continue
            if is_thread_done(msg["ts"]):
                log.info(f"Thread {msg['ts']} com ✅ — ignorando")
                continue
            tasks = extract_tasks(msg["text"])
            if not tasks:
                continue
            thread_ts = msg.get("thread_ts") or msg["ts"]
            threads.append({"ts": thread_ts, "root_ts": msg["ts"], "tasks": tasks})
    except SlackApiError as e:
        log.error(f"Erro ao buscar histórico: {e}")
    return threads


# ── Main ─────────────────────────────────────────────────────────────────────

def run():
    log.info("Iniciando cobrança de updates")
    threads = find_open_threads()
    log.info(f"Threads abertas encontradas: {len(threads)}")

    count = 0
    for thread in threads:
        thread_ts = thread["ts"]
        tasks = thread["tasks"]

        # Ninguém respondeu ainda: mensagem completa (primeira vez)
        if not has_human_replied(SLACK_CHANNEL_ID, thread_ts):
            try:
                client.chat_postMessage(
                    channel=SLACK_CHANNEL_ID,
                    thread_ts=thread_ts,
                    text=build_first_message(tasks),
                )
                log.info(f"Primeira cobrança na thread {thread_ts}")
                count += 1
            except SlackApiError as e:
                log.error(f"Erro ao cobrar thread {thread_ts}: {e.response['error']}")
            continue

        # Já teve reply: follow-up curto (só se o bot ainda não seguiu)
        if bot_already_followed(SLACK_CHANNEL_ID, thread_ts):
            log.info(f"Thread {thread_ts}: bot já seguiu — pulando")
            continue
        try:
            client.chat_postMessage(
                channel=SLACK_CHANNEL_ID,
                thread_ts=thread_ts,
                text=build_followup_message(tasks),
            )
            log.info(f"Follow-up na thread {thread_ts}")
            count += 1
        except SlackApiError as e:
            log.error(f"Erro ao cobrar thread {thread_ts}: {e.response['error']}")

    log.info(f"Concluído: {count} cobrança(ões) enviada(s)")


if __name__ == "__main__":
    run()

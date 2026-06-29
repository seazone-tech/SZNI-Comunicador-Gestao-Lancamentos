#!/usr/bin/env python3
"""
cobrar_updates.py
Cobra update de cada tarefa listada no briefing do dia.
Só age em mensagens do MESMO DIA e evita duplicados.
"""

import os
import re
import logging
import datetime as dt
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL_ID = os.environ["SLACK_CHANNEL_ID"]
client = WebClient(token=SLACK_BOT_TOKEN)

# Brasil UTC-3
TZ = dt.timezone(dt.timedelta(hours=-3))

# Extrair tarefas do formato do briefing
TASK_BLOCK_RE = re.compile(
    r":large_(?:large_)?(red_circle|yellow_circle|blue_circle):\s+\*(.+?)\*"
    r".*?Status:\s+(.+?)\s*\|.*?"
    r"In[ií]cio:\s*(\d{2}/\d{2})\s*→\s*Fim:\s*(\d{2}/\d{2})\s*\(([^)]+)\)",
    re.DOTALL,
)


def extract_tasks(text):
    tasks = []
    for m in TASK_BLOCK_RE.finditer(text):
        tasks.append({
            "name": m.group(2).strip(),
            "status": m.group(3).strip(),
            "start": m.group(4).strip(),
            "end": m.group(5).strip(),
            "urgency_label": m.group(6).strip(),
        })
    return tasks


BOT_USER_ID = None


def get_bot_user_id():
    global BOT_USER_ID
    if BOT_USER_ID:
        return BOT_USER_ID
    BOT_USER_ID = client.auth_test()["user_id"]
    return BOT_USER_ID


def build_cobranca_message(tasks):
    blocks = []
    for t in tasks:
        blocks.append(
            f"📋 {t['name']}\n"
            f"   Vence: {t['end']} | Status: {t['status']}\n"
            f"   • Começou? → ainda não / sim, desde {t['start']}\n"
            f"   • Fecha no prazo? → sim / não, nova previsão [DD/MM]\n"
            f"   • Gargalo? → não / sim: [descrever]\n"
        )
    return (
        "Olá, bom dia! 👋\n\n"
        "Preciso do seu update sobre as tarefas pendentes:\n\n"
        + "\n".join(blocks)
        + "\nObrigada! 🙌"
    )


def get_existing_replies(channel, thread_ts):
    try:
        replies = client.conversations_replies(channel=channel, ts=thread_ts)
        return {
            m["user"]
            for m in replies.get("messages", [])
            if m.get("user") != get_bot_user_id()
        }
    except SlackApiError:
        return set()


def run():
    my_id = get_bot_user_id()
    today = dt.date.today()
    log.info(f"Bot user ID: {my_id} | Data hoje: {today}")

    result = client.conversations_history(channel=SLACK_CHANNEL_ID, limit=50)
    count = 0

    for msg in result.get("messages", []):
        # Só mensagens-raiz do bot (não replies)
        if msg.get("user") != my_id and msg.get("ts") != msg.get("thread_ts"):
            continue

        text = msg.get("text", "")
        if "tarefa" not in text.lower() or "pendente" not in text.lower():
            continue

        # Só mensagens de HOJE
        msg_date = dt.datetime.fromtimestamp(float(msg["ts"]), tz=TZ).date()
        if msg_date != today:
            log.info(f"Thread {msg['ts']} de {msg_date}, pulando")
            continue

        tasks = extract_tasks(text)
        if not tasks:
            continue

        thread_ts = msg.get("thread_ts") or msg["ts"]

        # Não enviar se o bot já respondeu nessa thread
        if my_id in get_existing_replies(SLACK_CHANNEL_ID, thread_ts):
            log.info(f"Thread {thread_ts} já tem reply do bot, pulando")
            continue

        client.chat_postMessage(
            channel=SLACK_CHANNEL_ID,
            thread_ts=thread_ts,
            text=build_cobranca_message(tasks),
        )
        log.info(f"Cobrança enviada na thread {thread_ts} — {len(tasks)} tarefa(s)")
        count += 1

    log.info(f"Concluído: {count} cobrança(ões) enviada(s)")


if __name__ == "__main__":
    run()

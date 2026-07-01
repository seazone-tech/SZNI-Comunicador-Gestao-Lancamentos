#!/usr/bin/env python3
"""
fechamento_diario.py
Relatório de fechamento do dia — 17h30.
Lê TODAS as threads abertas do bot, busca replies, classifica e envia DM pra Bianca.
Ignora threads com ✅ de bot/Bianca (tarefa concluída no canal).
Se a data de fim passou e não foi concluída → sugestão Status → Atrasada.
"""

import os
import re
import logging
import datetime as dt
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

_script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_script_dir, ".env")) or load_dotenv(os.path.expanduser("~/.hermes/scripts/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL_ID = os.environ["SLACK_CHANNEL_ID"]
BIANCA_USER_ID = os.environ["BIANCA_USER_ID"]

client = WebClient(token=SLACK_BOT_TOKEN)
TZ = dt.timezone(dt.timedelta(hours=-3))

BOT_USER_ID = None

# ── Detecção de thread concluída ────────────────────────────────────────────

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
                if bot_id in users or BIANCA_USER_ID in users:
                    return True
        return False
    except SlackApiError:
        return False


# ── Extrair tarefas do formato do briefing ─────────────────────────────────

TASK_BLOCK_RE = re.compile(
    r"(?:📌|:pushpin:)\s*\[(.+?)\]\s*\[(.+?)\]\s*(.+?)(?:\n|$)",
    re.DOTALL,
)

INICIOU_RE = re.compile(
    r"(come[cç]ou|come[cç]amos|inici[ou]|j[aá] come[cç]|come[cç]ando|"
    r"já tá|n[ãa]o come[cç]ou|não começamos|não inici|não j[aá])",
    re.IGNORECASE,
)
CONCLUIU_RE = re.compile(
    r"(conclu[ií]do|feito|pronto|finalizado|entregue|"
    r"já tá pronto|j[áa] feiz|j[áa] concl|já entreg|"
    r"t[áa] feiz|tá pronto|acab[ou]|acabamos)",
    re.IGNORECASE,
)
BLOQUEIO_RE = re.compile(
    r"(atras[ao]|bloque|depende|n[ãa]o vai|não vai|n[ãa]o consigo|"
    r"não consigo|n[ãa]o dah|não dah|imposs|precisa|so depois|s[óo] depois|"
    r"n[ãa]o tem|não tem|n[ãa]o temos|não temos|não dah|não dá)",
    re.IGNORECASE,
)


def is_bot_briefing_thread(text):
    return "📌" in text or ":pushpin:" in text


def extract_tasks(text):
    tasks = []
    for m in TASK_BLOCK_RE.finditer(text):
        tasks.append({
            "sheet": m.group(1).strip(),
            "team":  m.group(2).strip(),
            "name":  m.group(3).strip(),
        })
    return tasks


def parse_end_date(day_month_str):
    """Converte dd/mm para date do ano atual."""
    try:
        d, m = map(int, day_month_str.split("/"))
        year = dt.date.today().year
        return dt.date(year, m, d)
    except:
        return None


def classify_reply(text):
    if CONCLUIU_RE.search(text):
        return "concluiu"
    if INICIOU_RE.search(text):
        return "iniciou"
    if BLOQUEIO_RE.search(text):
        return "bloqueio"
    return None


def get_replies(thread_ts):
    my_id = get_bot_user_id()
    try:
        result = client.conversations_replies(channel=SLACK_CHANNEL_ID, ts=thread_ts)
        replies = []
        for msg in result.get("messages", []):
            if msg.get("user") == my_id:
                continue
            text = msg.get("text", "").strip()
            if not text or len(text) < 3:
                continue
            replies.append({"text": text, "ts": msg["ts"]})
        return replies
    except SlackApiError:
        return []


def find_briefing_threads():
    """Busca TODAS as threads abertas (sem ✅ de conclusão)."""
    my_id = get_bot_user_id()
    threads = []
    try:
        result = client.conversations_history(channel=SLACK_CHANNEL_ID, limit=200)
        for msg in result.get("messages", []):
            if msg.get("user") != my_id:
                continue
            text = msg.get("text", "")
            if not is_bot_briefing_thread(text):
                continue
            msg_ts = msg["ts"]
            if is_thread_done(msg_ts):
                continue
            # Extrai sheet name da mensagem
            sheet_name = ""
            for line in text.split("\n"):
                stripped = line.strip().replace("*", "")
                if stripped.isupper() and len(stripped) > 3:
                    sheet_name = stripped
                    break
            threads.append({"ts": msg_ts, "text": msg["text"], "sheet": sheet_name})
        return threads
    except SlackApiError as e:
        log.error(f"Erro ao buscar threads: {e}")
        return []


def build_report(threads):
    today = dt.date.today()
    today_str = today.strftime("%d/%m/%Y")
    lines = [f"📊 RELATÓRIO DE FECHAMENTO — {today_str}\n"]

    task_counter = 0
    all_tasks = []

    for thread in threads:
        tasks = extract_tasks(thread["text"])
        replies = get_replies(thread["ts"])

        if not tasks:
            continue

        lines.append("─" * 40)
        lines.append(f"📋 {thread['sheet'].upper()}" if thread["sheet"] else "SEM SHEET")
        lines.append("─" * 40)

        for task in tasks:
            counter = task_counter + 1

            # Último reply wins
            reply = replies[-1] if replies else None
            classification = classify_reply(reply["text"]) if reply else None

            # Extrai data de fim do reply de cobrança (se houver)
            end_date = None
            if reply:
                date_match = re.search(r"Fim:\s*(\d{2}/\d{2})", reply["text"])
                if date_match:
                    end_date = parse_end_date(date_match.group(1))
            overdue = end_date < today if end_date else False

            lines.append(f"\n{counter}️⃣ *{task['name']}*")
            lines.append(f"   Sheet: {task['sheet']} | Time: {task['team']}")

            if reply:
                lines.append(f"   Reply: \"{reply['text'][:100]}\"")

            # Sugestões
            suggestions = []

            if classification == "concluiu":
                suggestions.append("Status → Concluída")
                suggestions.append(f"Fim Realizada → {today_str}")

            elif classification == "iniciou":
                suggestions.append("Status → Em Andamento")

            elif overdue:
                suggestions.append("Status → Atrasada")

            if suggestions:
                lines.append(f"   Sugestão: {' | '.join(suggestions)}")
                lines.append(f"   → Responda \"aprova {counter}\" pra aplicar")
            elif not reply:
                lines.append(f"   → Ninguém respondeu no thread")
            else:
                lines.append(f"   Status mantido (sem sugestão)")

            all_tasks.append({
                "counter": counter,
                "task_name": task["name"],
                "sheet": task["sheet"],
                "classification": classification,
                "suggestions": suggestions,
                "reply": reply,
                "thread_ts": thread["ts"],
            })
            task_counter += 1

    if not all_tasks:
        return None

    lines.append(f"\n{'─' * 40}")
    lines.append("🤖 Aprova linha por linha: \"aprova <número>\"")
    lines.append("🤖 Ignora: \"ignora <número>\"")
    lines.append(f"\nTotal: {len(all_tasks)} tarefa(s) aberta(s)")

    return "\n".join(lines), all_tasks


def send_dm(text):
    try:
        client.chat_postMessage(channel=BIANCA_USER_ID, text=text)
        log.info("Relatório enviado para Bianca por DM")
    except SlackApiError as e:
        log.error(f"Erro ao enviar DM: {e}")


def save_tasks_state(all_tasks):
    state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".fechamento_state")
    with open(state_file, "w") as f:
        for t in all_tasks:
            reply_text = t["reply"]["text"][:200] if t["reply"] else ""
            suggestions_str = "||".join(t["suggestions"])
            f.write(f"{t['counter']}|{t['task_name']}|{t['sheet']}|{t['classification']}|{suggestions_str}|{reply_text}|{t['thread_ts']}\n")


def run():
    log.info("Iniciando relatório de fechamento do dia")
    threads = find_briefing_threads()
    log.info(f"Threads abertas encontradas: {len(threads)}")

    result = build_report(threads)
    if result is None:
        log.info("Nenhuma tarefa — enviando relatório vazio")
        send_dm("📊 RELATÓRIO DE FECHAMENTO — Nenhuma tarefa em aberto. Sem ações necessárias.")
        return

    report_text, all_tasks = result
    save_tasks_state(all_tasks)
    send_dm(report_text)
    log.info(f"Relatório enviado com {len(all_tasks)} tarefa(s)")


if __name__ == "__main__":
    run()

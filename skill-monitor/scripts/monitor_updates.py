#!/usr/bin/env python3
"""
monitor_updates.py
Monitora TODAS as threads do bot no canal.
Se encontrar replies relevantes, avisa Bianca por DM.
Ignora threads com ✅ de bot/Bianca (tarefa concluída no canal).
Não filtra por data — monitora todo o histórico.
"""
import os, re, logging, datetime
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.env"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN  = os.environ["SLACK_BOT_TOKEN"]
CHANNEL_ID = os.environ["SLACK_CHANNEL_ID"]
BIANCA_USER_ID = os.environ["BIANCA_USER_ID"]
STATE_FILE = os.path.expanduser("~/.hermes/scripts/.monitor_state")

client = WebClient(token=BOT_TOKEN)
TZ = datetime.timezone(datetime.timedelta(hours=-3))

BOT_MSG_MARKERS = [
    "GESTAO LANCAMENTOS", "GESTÃO LANÇAMENTOS", "MARKETING",
    "DIRETORIA", "PROJETOS LANCAMENTOS", "FAROL", "MARISTA",
    "ORCAMENTOS LANCAMENTOS", "ORÇAMENTOS LANÇAMENTOS",
    "FORNECEDORES LANCAMENTO", "FORNECEDORES LANÇAMENTO",
    "COMPRA DE TERRENOS", "ANALISE DE TERRENOS", "ANÁLISE DE TERRENOS",
    "SERVIÇOS/CS/FRANQUIAS", "SERVICOS/CS/FRANQUIAS",
    "MARCO",
]

DELAY_PATTERNS = re.compile(
    r"(atras[a-z]+|nao vai|não vai|nao consigo|não consigo|nao deve|não deve|"
    r"bloquead[a-z]+|depende de|gargalo|"
    r"nao tem|não tem|impossivel|impossível|nao vai dar|não vai dar|"
    r"precisa de|precisamos de|so depois|só depois|nao dah|não dah|"
    r"talvez|talvez a gente|vera o|ver se dah|ver se consegue|"
    r"nao temos|não temos|nao temos como|não temos como|"
    r"\bnão\b)",
    re.IGNORECASE,
)

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")

IGNORE_PATTERNS = re.compile(
    r"^(k+|kk|sim|ok|ué|hum|ah|o[iy]|beleza|blz|thanks)$",
    re.IGNORECASE,
)

BOT_USER_ID_CACHE = None
_channel_name = None


def get_bot_user_id():
    global BOT_USER_ID_CACHE
    if BOT_USER_ID_CACHE:
        return BOT_USER_ID_CACHE
    BOT_USER_ID_CACHE = client.auth_test()["user_id"]
    return BOT_USER_ID_CACHE


def get_channel_name():
    global _channel_name
    if _channel_name:
        return _channel_name
    try:
        result = client.conversations_info(channel=CHANNEL_ID)
        _channel_name = result.data["channel"]["name"]
        return _channel_name
    except Exception:
        return CHANNEL_ID


def get_permalink(msg_ts):
    try:
        result = client.chat_getPermalink(channel=CHANNEL_ID, message_ts=msg_ts)
        return result.data["permalink"]
    except Exception:
        return None


def is_thread_done(msg_ts: str) -> bool:
    """True se a thread tem ✅ reactions de bot ou Bianca."""
    try:
        result = client.reactions_get(channel=CHANNEL_ID, timestamp=msg_ts)
        for reaction in result.get("message", {}).get("reactions", []):
            if reaction.get("name") in ("white_check_mark", "check", "heavy_check_mark"):
                users = reaction.get("users", [])
                bot_id = get_bot_user_id()
                if bot_id in users or BIANCA_USER_ID in users:
                    return True
        return False
    except SlackApiError:
        return False


def is_bot_briefing_thread(text):
    return "📌" in text


def load_processed():
    try:
        with open(STATE_FILE) as f:
            return set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        return set()


def save_processed(processed):
    with open(STATE_FILE, "w") as f:
        for item in sorted(processed):
            f.write(item + "\n")


def find_bot_threads():
    """Busca TODAS as threads do bot, ignorando as com ✅ de conclusão."""
    my_id = get_bot_user_id()
    threads = []
    try:
        result = client.conversations_history(channel=CHANNEL_ID, limit=200)
        for msg in result.data.get("messages", []):
            if msg.get("user") != my_id:
                continue
            text = msg.get("text", "")
            if not is_bot_briefing_thread(text):
                continue
            msg_ts = msg["ts"]
            if is_thread_done(msg_ts):
                continue
            threads.append(msg_ts)
    except SlackApiError as e:
        log.error(f"Erro ao buscar histórico: {e}")
    return threads


def get_replies(thread_ts):
    my_id = get_bot_user_id()
    try:
        result = client.conversations_replies(channel=CHANNEL_ID, ts=thread_ts)
        replies = []
        for msg in result.data.get("messages", []):
            if msg.get("user") == my_id:
                continue
            text = msg.get("text", "").strip()
            if not text or len(text) < 3:
                continue
            if IGNORE_PATTERNS.match(text):
                continue
            replies.append({"user": msg.get("user", ""), "text": text, "ts": msg["ts"]})
        return replies
    except SlackApiError:
        return []


def parse_reply(text):
    findings = []
    if DELAY_PATTERNS.search(text):
        findings.append("POTENCIAL ATRASO/BLOQUEIO")
    urls = URL_PATTERN.findall(text)
    if urls:
        findings.append("LINK: " + " | ".join(urls))
    if "?" in text:
        findings.append("TEM PERGUNTA")
    return findings


def send_dm_to_bianca(alerts):
    if not alerts:
        return
    for permalink in alerts:
        if permalink:
            msg = "Bianca, dá uma olhada aqui, please :eyes:\n\n" + "<" + permalink + "|Ver resposta>"
        else:
            msg = "Bianca, dá uma olhada aqui, please :eyes:"
        try:
            client.chat_postMessage(channel=BIANCA_USER_ID, text=msg)
            log.info("DM enviada para Bianca")
        except SlackApiError as e:
            log.error("Erro ao enviar DM: " + str(e))


def run():
    log.info("Monitor de updates iniciado")
    processed = load_processed()
    threads = find_bot_threads()
    log.info("Threads abertas encontradas: " + str(len(threads)))

    new_alerts = []

    for thread_ts in threads:
        replies = get_replies(thread_ts)
        if not replies:
            continue

        for reply in replies:
            reply_key = thread_ts + "|" + reply["ts"]
            if reply_key in processed:
                continue

            findings = parse_reply(reply["text"])
            if findings:
                new_alerts.append(get_permalink(reply["ts"]))
                log.info("ALERTA: " + reply["text"][:50])

            processed.add(reply_key)

    save_processed(processed)
    send_dm_to_bianca(new_alerts)

    if not new_alerts:
        log.info("Nenhum alerta encontrado")
    else:
        log.info("Concluido: " + str(len(new_alerts)) + " alerta(s)")


if __name__ == "__main__":
    run()

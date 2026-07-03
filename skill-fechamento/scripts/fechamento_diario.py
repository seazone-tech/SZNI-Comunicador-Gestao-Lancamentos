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

_hermes_env = os.path.expanduser("~/.hermes/scripts/.env")
_project_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.env")
load_dotenv(_hermes_env) if os.path.exists(_hermes_env) else load_dotenv(_project_env)

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
BRIEFING_STATE = os.path.expanduser("~/.hermes/scripts/.briefing_posted")


def get_bot_user_id():
    global BOT_USER_ID
    if BOT_USER_ID:
        return BOT_USER_ID
    BOT_USER_ID = client.auth_test()["user_id"]
    return BOT_USER_ID


def is_thread_done(msg_ts: str, channel_id: str = None) -> bool:
    if channel_id is None:
        channel_id = SLACK_CHANNEL_ID
    try:
        result = client.reactions_get(channel=channel_id, timestamp=msg_ts)
        for reaction in result.get("message", {}).get("reactions", []):
            if reaction.get("name") in ("white_check_mark", "check", "heavy_check_mark"):
                users = reaction.get("users", [])
                bot_id = get_bot_user_id()
                if bot_id in users or BIANCA_USER_ID in users:
                    return True
        return False
    except SlackApiError:
        return False


# Formato real no Slack (lido de conversations_replies):
# - Farol: :pushpin: [[5921] Farol da Barra Spot] [GESTÃO LANÇAMENTOS] Definir...
# - São Miguel: :pushpin: [São Miguel dos Milagres] [GESTÃO LANÇAMENTOS] Abrir...
#
# Bug corrigido 02/07/2026: o regex antigo usava `[^\]]+` dentro de [[...]],
# o que parava no primeiro ']' encontrado (depois do "5921" no caso do Farol)
# e quebrava o casamento do sheet com prefixo numérico. A regex abaixo tem
# dois branches: (a) [[ID] Nome] | (b) [Nome] — casando ambos os formatos.
# Grupos: (1)=ID, (2)=Nome-com-id, (3)=Nome-sem-id, (4)=Team, (5)=Task.
TASK_BLOCK_RE = re.compile(
    r":pushpin:\s*(?:\[\[([^\]]+)\]\s*([^\]]+)\]|\[([^\]]+)\])\s*\[([^\]]+)\]\s*(.+?)(?:\n|$)",
    re.DOTALL,
)

END_DATE_RE = re.compile(r"In[ií]cio:\s*\S+\s*→\s*Fim:\s*(\d{2}/\d{2})")

# Aceita datas com ou sem colchetes — o questionário do briefing usa [02/07]
DATE_ABSOLUTE_RE = re.compile(r"[\[\(]?(\d{2}/\d{2}(?:/\d{4})?)[\]\)]?")

DATE_FROM_REPLY_RE = re.compile(
    r"(?:inici[ou]|come[cç]ou|come[cç]amos|início|come[cç]ando)\s+.*?(\d{2}/\d{2})"
    r"|(?:conclu[ií]do|feito|pronto|finalizado|entregue|acab[ou]|acabamos)\s+.*?(\d{2}/\d{2})"
    r"|(?:ontem|hoje)\s*(?:à\s*s|as)?\s*(\d{2}/\d{2})?"
    r"|(\d{2}/\d{2})\s*(?:por|às|a)?\s*(?:início|iniciou|começou|fim|concluiu)?",
    re.IGNORECASE,
)

INICIOU_RE = re.compile(
    r"(come[cç]ou|come[cç]amos|inici[ou]|j[aá] come[cç]|come[cç]ando|"
    r"já tá|n[ãa]o come[cç]ou|não começamos|não inici|não j[aá]|"
    r"iniciada ontem|iniciada hoje|tarefa iniciada|iniciamos)",
    re.IGNORECASE,
)
CONCLUIU_RE = re.compile(
    r"(conclu[ií]do|feito|pronto|finalizado|entregue|"
    r"já tá pronto|j[áa] feiz|j[áa] concl|já entreg|"
    r"t[áa] feiz|tá pronto|acab[ou]|acabamos|"
    r"conclu[ií]da hoje|conclu[ií]mos|conclu[ií] |"
    r"iniciada e conclu[ií]da|conclu[ií]da|finalizada|entregue|"
    r"tarefa conclu[ií]da|termin[ou]u?|terminamos)",
    re.IGNORECASE,
)
BLOQUEIO_RE = re.compile(
    r"(atras[ao]|bloque|depende|n[ãa]o vai|não vai|n[ãa]o consigo|"
    r"não consigo|n[ãa]o dah|não dah|imposs|precisa|so depois|s[óo] depois|"
    r"n[ãa]o tem|não tem|n[ãa]o temos|não temos|não dah|não dá)",
    re.IGNORECASE,
)


def is_bot_briefing_thread(text):
    return ":pushpin:" in text or "📌" in text


def extract_tasks(text):
    tasks = []
    end_date_match = END_DATE_RE.search(text)
    end_date_str = end_date_match.group(1) if end_date_match else None
    for m in TASK_BLOCK_RE.finditer(text):
        # Grupos: (1)=ID, (2)=Nome-com-id, (3)=Nome-sem-id, (4)=Team, (5)=Task
        sheet = (m.group(2) or m.group(3) or "").strip()
        tasks.append({
            "sheet": sheet,
            "team":  m.group(4).strip(),
            "name":  m.group(5).strip(),
            "end_date_str": end_date_str,
        })
    return tasks


def parse_end_date(day_month_str):
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


def _parse_date_str(d_str, year):
    """Converte 'DD/MM' ou 'DD/MM/AAAA' para objeto date, ou None se inválida."""
    p = d_str.replace('/', '')
    try:
        if len(p) == 8:
            return dt.datetime.strptime(p, "%d%m%Y").date()
        elif len(p) == 4:
            return dt.datetime.strptime(p, "%d%m").date().replace(year=year)
    except (ValueError, TypeError):
        return None
    return None


def extract_dates_from_reply(text):
    """
    Extrai datas de início e fim do reply em texto livre.
    Estratégia:
      - Palavras-chave explícitas ("desde [DATA]" → início, "conclui [DATA]" → fim)
      - "ontem" → início = ontem
      - "hoje" → início = hoje e fim = hoje (assume tarefa feita hoje)
      - Datas avulsas: se o reply indica início/conclusão, atribui conforme o contexto;
        caso contrário, primeira data = início, última = fim.

    Retorna {'inicio': 'YYYY-MM-DD'|None, 'fim': 'YYYY-MM-DD'|None}.
    """
    today = dt.date.today()
    year = today.year
    inicio = None
    fim = None
    text_lower = text.lower()

    # 1) Marcadores relativos
    if 'ontem' in text_lower:
        d = today - dt.timedelta(days=1)
        if inicio is None:
            inicio = d.strftime("%Y-%m-%d")

    if 'hoje' in text_lower:
        if inicio is None:
            inicio = today.strftime("%Y-%m-%d")
        if fim is None:
            fim = today.strftime("%Y-%m-%d")

    consumed = set()  # datas (string original 'DD/MM' ou 'DD/MM/AAAA') já atribuídas por regex forte

    # 2) "desde [DATA]" → data de início
    DESDE_RE = re.compile(r"desde\s+[\[\(]?(\d{2}/\d{2}(?:/\d{4})?)[\]\)]?", re.IGNORECASE)
    m_desde = DESDE_RE.search(text)
    if m_desde:
        d = _parse_date_str(m_desde.group(1), year)
        if d and inicio is None:
            inicio = d.strftime("%Y-%m-%d")
            consumed.add(m_desde.group(1))

    # 3) "conclui/concluído/terminou/pronto [DATA]" → data de fim
    CONCL_DATA_RE = re.compile(
        r"(?:conclu[ií]do|conclu[ií]mos|conclu[ií]|termin[eo]u|terminamos|pronto|entregue|acabou|acabamos|fez|feita)"
        r"\s+.*?[\[\(]?(\d{2}/\d{2}(?:/\d{4})?)[\]\)]?",
        re.IGNORECASE,
    )
    m_concl = CONCL_DATA_RE.search(text)
    if m_concl and m_concl.group(1):
        d = _parse_date_str(m_concl.group(1), year)
        if d and fim is None:
            fim = d.strftime("%Y-%m-%d")
            consumed.add(m_concl.group(1))

    # 4) Datas avulsas: preenche só o que ainda está None, ignorando as já consumidas acima.
    #    Regra: primeira data nova → início; próxima data nova → fim.
    dates_found = DATE_ABSOLUTE_RE.findall(text)
    if dates_found:
        for d_str in dates_found:
            if d_str in consumed:
                continue
            d = _parse_date_str(d_str, year)
            if not d:
                continue
            iso = d.strftime("%Y-%m-%d")
            if inicio is None:
                inicio = iso
            elif fim is None:
                fim = iso

    return {"inicio": inicio, "fim": fim}


def get_replies(thread_ts, channel_id=None):
    if channel_id is None:
        channel_id = SLACK_CHANNEL_ID
    my_id = get_bot_user_id()
    try:
        result = client.conversations_replies(channel=channel_id, ts=thread_ts)
        replies = []
        end_date_from_reply = None
        for msg in result.get("messages", []):
            if msg.get("user") == my_id:
                if end_date_from_reply is None:
                    match = END_DATE_RE.search(msg.get("text", ""))
                    if match:
                        end_date_from_reply = match.group(1)
                continue
            text = msg.get("text", "").strip()
            if not text or len(text) < 3:
                continue
            replies.append({"text": text, "ts": msg["ts"]})
        return replies, end_date_from_reply
    except SlackApiError:
        return [], None


def find_briefing_threads():
    my_id = get_bot_user_id()
    threads = []
    known_ts = set()
    state_ts_to_channel = {}

    # Carrega do estado com canal correto
    try:
        with open(BRIEFING_STATE) as f:
            for line in f:
                parts = line.strip().split("|")
                if len(parts) >= 3:
                    ts = parts[2]
                    channel_id = parts[3] if len(parts) >= 4 and parts[3] else SLACK_CHANNEL_ID
                    known_ts.add(ts)
                    state_ts_to_channel[ts] = channel_id
                    if is_thread_done(ts, channel_id):
                        continue
                    threads.append({"ts": ts, "text": "", "sheet": "", "channel_id": channel_id})
    except FileNotFoundError:
        log.warning("BRIEFING_STATE não encontrado")

    # Fallback: histórico — busca o texto da mensagem raiz de TODAS as threads
    # conhecidas (do estado) e adiciona threads novas que não estavam no estado.
    # Bug corrigido 02/07/2026: antes só adicionava threads novas; agora também
    # preenche o `text` das threads do estado (que vinham vazio), permitindo
    # que `extract_tasks` identifique a tarefa.
    extra_channels = []
    channel_map_raw = os.getenv("CHANNEL_MAP", "")
    for item in channel_map_raw.split(","):
        if ":" in item:
            channel_id = item.strip().split(":")[1].strip()
            if channel_id not in extra_channels:
                extra_channels.append(channel_id)

    for channel_id in extra_channels:
        cursor = None
        while True:
            try:
                kwargs = {"channel": channel_id, "limit": 200}
                if cursor:
                    kwargs["cursor"] = cursor
                result = client.conversations_history(**kwargs)
            except SlackApiError as e:
                log.error(f"Erro ao buscar histórico do canal {channel_id}: {e}")
                break

            for msg in result.get("messages", []):
                if msg.get("user") != my_id:
                    continue
                text = msg.get("text", "")
                if not is_bot_briefing_thread(text):
                    continue
                msg_ts = msg["ts"]
                if is_thread_done(msg_ts, channel_id):
                    continue
                sheet_name = ""
                for line in text.split("\n"):
                    stripped = line.strip().replace("*", "")
                    if stripped.isupper() and len(stripped) > 3:
                        sheet_name = stripped
                        break
                # Substitui thread existente (do estado) com o texto real,
                # ou adiciona nova se não estava no estado.
                replaced = False
                for t in threads:
                    if t["ts"] == msg_ts and t["channel_id"] == channel_id:
                        t["text"] = msg["text"]
                        if sheet_name:
                            t["sheet"] = sheet_name
                        replaced = True
                        break
                if not replaced:
                    known_ts.add(msg_ts)
                    threads.append({"ts": msg_ts, "text": msg["text"], "sheet": sheet_name, "channel_id": channel_id})

            cursor = result.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

    return threads


def build_report(threads):
    today = dt.date.today()
    today_str = today.strftime("%d/%m/%Y")

    task_counter = 0
    all_tasks = []
    grouped = {}

    for thread in threads:
        replies, end_date_from_reply = get_replies(thread["ts"], thread.get("channel_id"))

        tasks = []
        if thread["text"]:
            tasks = extract_tasks(thread["text"])
        if not tasks:
            try:
                result = client.conversations_replies(channel=thread.get("channel_id", SLACK_CHANNEL_ID), ts=thread["ts"])
                for msg in result.get("messages", []):
                    if msg.get("user") == get_bot_user_id():
                        tasks = extract_tasks(msg.get("text", ""))
                        if tasks:
                            break
            except SlackApiError:
                pass

        if not tasks:
            continue

        for task in tasks:
            task_counter += 1
            counter = task_counter

            reply = replies[-1] if replies else None
            classification = classify_reply(reply["text"]) if reply else None
            datas = extract_dates_from_reply(reply["text"]) if reply else {"inicio": None, "fim": None}

            end_date = None
            if end_date_from_reply:
                end_date = parse_end_date(end_date_from_reply)
            elif task.get("end_date_str"):
                end_date = parse_end_date(task["end_date_str"])
            overdue = end_date < today if end_date else False

            suggestions = []
            warnings = []

            if classification == "concluiu":
                suggestions.append("Status → Concluída")
                if datas.get("inicio"):
                    suggestions.append(f"Início Realizada → {datas['inicio']}")
                else:
                    suggestions.append(f"Início Realizada → {today_str} (hoje)")
                    warnings.append("⚠️ Data de início não detectada no reply — assumindo hoje. Confira antes de aprovar.")
                if datas.get("fim"):
                    suggestions.append(f"Fim Realizada → {datas['fim']}")
                else:
                    suggestions.append(f"Fim Realizada → {today_str} (hoje)")
            elif classification == "iniciou":
                suggestions.append("Status → Em andamento")
                if datas.get("inicio"):
                    suggestions.append(f"Início Realizada → {datas['inicio']}")
                else:
                    suggestions.append(f"Início Realizada → {today_str} (hoje)")
                    warnings.append("⚠️ Data de início não detectada no reply — assumindo hoje. Confira antes de aprovar.")
                # Regra: Em andamento → Fim Realizada fica vazio
                suggestions.append("Fim Realizada → (vazio)")
            elif overdue:
                suggestions.append("Status → Atrasada")
                # Regra: Atrasada → Início Realizada = Data de Início Planejada (se houver), Fim Realizada = vazio
                if datas.get("inicio"):
                    suggestions.append(f"Início Realizada → {datas['inicio']}")
                else:
                    suggestions.append("Início Realizada → (preencher com Data de Início Planejada)")
                suggestions.append("Fim Realizada → (vazio)")

            # Caso sem classificação detectada mas com tarefa aberta → sempre sugerir
            # as colunas que precisam ser revisadas (regra: nunca ir sem sugestão)
            if not suggestions:
                suggestions.append("Status → (revisar — nenhuma resposta classificada)")
                suggestions.append("Início Realizada → (revisar)")
                suggestions.append("Fim Realizada → (revisar)")

            task_data = {
                "counter": counter,
                "task_name": task["name"],
                "sheet": task["sheet"],
                "team": task["team"],
                "classification": classification,
                "suggestions": suggestions,
                "warnings": warnings,
                "reply": reply,
                "thread_ts": thread["ts"],
                "inicio": datas.get("inicio", ""),
                "fim": datas.get("fim", ""),
            }
            all_tasks.append(task_data)

            sheet = task["sheet"] or "SEM CRONOGRAMA"
            team = task["team"] or "SEM TIME"
            if sheet not in grouped:
                grouped[sheet] = {}
            if team not in grouped[sheet]:
                grouped[sheet][team] = []
            grouped[sheet][team].append(task_data)

    if not all_tasks:
        return None

    lines = [f"📊 RELATÓRIO DE FECHAMENTO — {today_str}\n"]

    for sheet, teams in grouped.items():
        lines.append(f"\n:arrow_right: {sheet}")
        for team, team_tasks in teams.items():
            lines.append(f"\n:large_blue_circle: {team}")
            for t in team_tasks:
                lines.append(f"\n{t['counter']}. {t['task_name']}")
                reply_text = f"\"{t['reply']['text'][:150]}\"" if t['reply'] else "_sem reply_"
                lines.append(f"   Reply: {reply_text}")
                if t['suggestions']:
                    lines.append(f"   Sugestão: {' | '.join(t['suggestions'])}")
                elif t['warnings']:
                    lines.append(f"   {' | '.join(t['warnings'])}")
                else:
                    lines.append(f"   _sem sugestão_")

    # Relatório consolidado não é mais enviado (regra: uma DM por tarefa).
    return "", all_tasks


def send_dm(text):
    """Envia uma única DM. Retorna True se ok."""
    try:
        client.chat_postMessage(channel=BIANCA_USER_ID, text=text)
        return True
    except SlackApiError as e:
        log.error(f"Erro ao enviar DM: {e}")
        return False


def _format_task_msg(t):
    """Renderiza uma DM de tarefa (3 linhas: cabeçalho / reply / sugestão)."""
    sheet = t["sheet"] or "SEM CRONOGRAMA"
    team = t["team"] or "SEM TIME"
    reply_text = t["reply"]["text"] if t["reply"] else "_sem reply_"
    if len(reply_text) > 300:
        reply_text = reply_text[:300] + "..."

    sug_parts = []
    for s in t["suggestions"]:
        if " → " in s:
            k, v = s.split(" → ", 1)
            sug_parts.append(f"{k}: {v}")
        else:
            sug_parts.append(s)
    sug_inline = " | ".join(sug_parts)
    reply_inline = reply_text.replace("\n", " ").strip()

    msg = (
        f"📌 *{sheet}* / *{team}* — *{t['task_name']}*\n"
        f"💬 {reply_inline}\n"
        f"📝 {sug_inline}"
    )
    if t["warnings"]:
        warn_inline = " | ".join(t["warnings"])
        msg += f"\n⚠️ {warn_inline}"
    return msg


def send_per_sheet_threads(all_tasks):
    """
    Regra Bianca 02/07/2026 (refinamento): UMA thread por cronograma na DM da Bianca,
    e dentro de cada thread UMA mensagem por tarefa.

    - 1ª mensagem de cada thread = cabeçalho do cronograma (ex: "📊 Fechamento —
      [5921] Farol da Barra Spot — 02/07/2026 (10 tarefas)")
    - Mensagens seguintes = tarefas (via _format_task_msg), como replies da thread raiz

    Persiste o ts de cada thread raiz em ~/.hermes/scripts/.fechamento_threads
    pra次日 o bot não criar thread duplicada caso rode 2x no mesmo dia.
    Retorna True se tudo ok.
    """
    import collections
    ok_all = True

    # Agrupa tarefas por sheet, mantendo ordem original
    by_sheet = collections.OrderedDict()
    for t in all_tasks:
        sheet = t["sheet"] or "SEM CRONOGRAMA"
        by_sheet.setdefault(sheet, []).append(t)

    today_str = dt.date.today().strftime("%d/%m/%Y")
    state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".fechamento_threads")

    # Carrega threads já criadas hoje (ts -> sheet) — chave de idempotência.
    # Validação: ts do Slack real tem 6 dígitos depois do ponto. Aceita só
    # entradas com data de hoje + ts no formato Slack. Logs warn e ignora
    # entradas suspeitas (defesa contra contaminação de mock).
    existing = {}
    import re as _re
    if os.path.exists(state_path):
        with open(state_path) as f:
            for line in f:
                parts = line.strip().split("|")
                if len(parts) >= 3 and parts[0] == dt.date.today().isoformat():
                    if not _re.match(r"^\d{10}\.\d{6}$", parts[2]):
                        log.warning(f"Ignorando thread state suspeito: {parts[2]!r} (formato Slack esperado)")
                        continue
                    existing[parts[1]] = parts[2]

    new_state_lines = []

    for sheet, tasks in by_sheet.items():
        # Cabeçalho da thread
        header = (
            f"📊 *Fechamento — {sheet}* — {today_str} ({len(tasks)} tarefa{'s' if len(tasks) != 1 else ''})"
        )

        if sheet in existing:
            thread_ts = existing[sheet]
            log.info(f"Thread de '{sheet}' já existe hoje (ts={thread_ts}) — enviando replies")
        else:
            try:
                resp = client.chat_postMessage(channel=BIANCA_USER_ID, text=header)
                thread_ts = resp["ts"]
                new_state_lines.append(f"{dt.date.today().isoformat()}|{sheet}|{thread_ts}")
                log.info(f"Thread '{sheet}' criada (ts={thread_ts})")
            except SlackApiError as e:
                log.error(f"Erro ao criar thread '{sheet}': {e}")
                ok_all = False
                continue

        # Envia cada tarefa como reply da thread
        for t in tasks:
            msg = _format_task_msg(t)
            try:
                client.chat_postMessage(
                    channel=BIANCA_USER_ID,
                    text=msg,
                    thread_ts=thread_ts,
                )
                log.info(f"Reply em '{sheet}': #{t['counter']} {t['task_name']}")
            except SlackApiError as e:
                log.error(f"Erro reply '{sheet}' #{t['counter']}: {e}")
                ok_all = False

    # Persiste estado (append mode — múltiplas folhas por dia)
    if new_state_lines:
        with open(state_path, "a") as f:
            for line in new_state_lines:
                f.write(line + "\n")

    return ok_all


def send_per_task_dms(all_tasks):
    """
    MODO LEGADO — mantido para retrocompatibilidade.
    Regra Bianca 02/07/2026: uma DM por tarefa (sem thread).
    Substituído por `send_per_sheet_threads` em 02/07/2026 quando Bianca pediu
    "uma thread por cronograma, e cada tarefa uma mensagem dentro da thread".
    """
    ok_all = True
    for t in all_tasks:
        msg = _format_task_msg(t)
        if not send_dm(msg):
            ok_all = False
        else:
            log.info(f"DM enviada: #{t['counter']} {t['task_name']}")
    return ok_all


def save_tasks_state(all_tasks):
    state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".fechamento_state")
    with open(state_file, "w") as f:
        for t in all_tasks:
            reply_text = t["reply"]["text"][:200] if t["reply"] else ""
            suggestions_str = "||".join(t["suggestions"])
            f.write(
                f"{t['counter']}|{t['task_name']}|{t['sheet']}|{t['classification']}"
                f"|{suggestions_str}|{reply_text}|{t['thread_ts']}|{t.get('inicio', '')}|{t.get('fim', '')}\n"
            )


def run():
    log.info("Iniciando relatório de fechamento do dia")
    threads = find_briefing_threads()
    log.info(f"Threads abertas encontradas: {len(threads)}")

    result = build_report(threads)
    if result is None:
        log.info("Nenhuma tarefa — enviando aviso")
        send_dm("📊 RELATÓRIO DE FECHAMENTO — Nenhuma tarefa em aberto. Sem ações necessárias.")
        return

    # build_report agora retorna (None, all_tasks) — usamos o all_tasks diretamente
    _, all_tasks = result
    save_tasks_state(all_tasks)

    # Regra Bianca 02/07/2026 (refinamento 02/07): uma THREAD por cronograma,
    # e cada tarefa uma mensagem dentro da thread.
    send_per_sheet_threads(all_tasks)
    log.info(f"Relatório enviado: {len(all_tasks)} tarefa(s) em thread(s) por cronograma")


if __name__ == "__main__":
    run()

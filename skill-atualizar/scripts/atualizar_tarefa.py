#!/usr/bin/env python3
"""
atualizar_tarefa.py
Skill interativa: Bianca manda DM descrevendo o que quer atualizar no SmartSheet.
Bot encontra a tarefa, confirma com Bianca e aplica as mudanças.
Roda a cada 3 minutos via cron.
"""

import os
import re
import json
import logging
import datetime as dt
import difflib
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import smartsheet as ss_sdk

_script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.expanduser("~/.hermes/scripts/.env")) or load_dotenv(os.path.join(_script_dir, ".env"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SLACK_BOT_TOKEN  = os.environ["SLACK_BOT_TOKEN"]
BIANCA_USER_ID   = os.environ["BIANCA_USER_ID"]
SMARTSHEET_TOKEN = os.environ["SMARTSHEET_TOKEN"]
FOLDER_ID        = int(os.environ["SMARTSHEET_FOLDER_ID"])
SLACK_CHANNEL_ID = os.environ["SLACK_CHANNEL_ID"]

STATE_FILE    = os.path.expanduser("~/.hermes/scripts/.atualizar_state")
BRIEFING_FILE = os.path.expanduser("~/.hermes/scripts/.briefing_posted")

slack_client = WebClient(token=SLACK_BOT_TOKEN)
ss_client    = ss_sdk.Smartsheet(SMARTSHEET_TOKEN)
ss_client.errors_as_exceptions(True)

BOT_USER_ID = None

KNOWN_COLS = [
    "Fase", "Status", "Crítico", "Atividade",
    "Data de Início Planejada", "Data de Fim Planejada",
    "Data de Início Realizada", "Data de Fim Realizada",
    "Dependência", "Time Responsável", "Duração",
]

COL_DISPLAY_ORDER = [
    "Atividade", "Status", "Fase", "Time Responsável", "Crítico",
    "Data de Início Planejada", "Data de Fim Planejada",
    "Data de Início Realizada", "Data de Fim Realizada",
    "Duração", "Dependência",
]

DATE_COL_ALIASES = {
    "data de início planejada": "Data de Início Planejada",
    "data de fim planejada": "Data de Fim Planejada",
    "data de início realizada": "Data de Início Realizada",
    "data de fim realizada": "Data de Fim Realizada",
    "início planejado": "Data de Início Planejada",
    "fim planejado": "Data de Fim Planejada",
    "início realizado": "Data de Início Realizada",
    "fim realizado": "Data de Fim Realizada",
    "data de fim": "Data de Fim Realizada",
    "data de início": "Data de Início Realizada",
    "fim": "Data de Fim Realizada",
    "início": "Data de Início Realizada",
}

DONE_KEYWORDS    = ["terminei", "concluí", "conclui", "finalizei", "finalizou", "concluiu", "entregue", "entregou", "pronto", "pronta"]
START_KEYWORDS   = ["comecei", "iniciei", "começou", "iniciou"]
DELAYED_KEYWORDS = ["vai atrasar", "atrasou", "atrasado", "atrasada"]
CONFIRM_YES      = ["sim", "s", "confirma", "confirmo", "ok", "isso", "exato", "pode", "isso mesmo"]
CONFIRM_NO       = ["não", "nao", "n", "cancela", "cancelo", "errado", "errada"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_bot_user_id():
    global BOT_USER_ID
    if BOT_USER_ID:
        return BOT_USER_ID
    BOT_USER_ID = slack_client.auth_test()["user_id"]
    return BOT_USER_ID

def normalize(text):
    return re.sub(r'\s+', ' ', str(text).lower().strip())

def fmt_date_display(val):
    """Converte YYYY-MM-DD para DD/MM/YYYY para exibição."""
    if not val:
        return "—"
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', str(val))
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    return str(val)

def parse_date_value(text):
    """Extrai data do texto. Aceita DD/MM, DD/MM/YYYY, 'hoje'."""
    today = dt.date.today()
    if "hoje" in text.lower():
        return today.strftime("%Y-%m-%d")
    m = re.search(r'(\d{1,2})/(\d{1,2})(?:/(\d{4}))?', text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            return dt.date(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


# ── State ─────────────────────────────────────────────────────────────────────

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"pending": {}, "processed": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ── Slack DMs ─────────────────────────────────────────────────────────────────

def get_dm_channel():
    """Busca o channel ID da DM com Bianca via im:read."""
    try:
        result = slack_client.conversations_list(types="im", limit=100)
        for ch in result.get("channels", []):
            if ch.get("user") == BIANCA_USER_ID:
                return ch["id"]
    except SlackApiError as e:
        log.error(f"Erro ao buscar DM channel: {e}")
    return BIANCA_USER_ID  # fallback: usa user ID diretamente

def send_dm(dm_channel, text):
    slack_client.chat_postMessage(channel=BIANCA_USER_ID, text=text)

def get_new_dms(dm_channel, processed_ts):
    try:
        result = slack_client.conversations_history(channel=dm_channel, limit=30)
        messages = [
            msg for msg in result.get("messages", [])
            if msg.get("ts") not in processed_ts
            and msg.get("user") == BIANCA_USER_ID
            and msg.get("text", "").strip()
        ]
        return list(reversed(messages))
    except SlackApiError as e:
        log.error(f"Erro ao ler DMs: {e}")
        return []


# ── SmartSheet helpers ────────────────────────────────────────────────────────

def get_sheets():
    return ss_client.Folders.get_folder_children(FOLDER_ID).data

def get_col_map(sheet):
    return {col.title: col for col in sheet.columns}

def get_cell_value(row, col_id):
    for cell in row.cells:
        if cell.column_id == col_id:
            return cell.display_value or cell.value or ""
    return ""

def get_dropdown_options(sheet, col):
    """Busca opções válidas de uma coluna dropdown direto do SmartSheet."""
    try:
        col_detail = ss_client.Columns.get_column(sheet.id, col.id)
        if hasattr(col_detail, 'options') and col_detail.options:
            return list(col_detail.options)
    except Exception:
        pass
    return None


# ── Fuzzy search ──────────────────────────────────────────────────────────────

def find_sheet(query, sheets):
    norm_q = normalize(query)
    # Exact match
    for s in sheets:
        if normalize(s.name) == norm_q:
            return s
    # Partial match
    for s in sheets:
        if norm_q in normalize(s.name) or normalize(s.name) in norm_q:
            return s
    # Fuzzy
    names_norm = [normalize(s.name) for s in sheets]
    matches = difflib.get_close_matches(norm_q, names_norm, n=1, cutoff=0.4)
    if matches:
        return sheets[names_norm.index(matches[0])]
    return None

def find_tasks(query, sheet):
    """Retorna lista de (row, task_name, score) ordenada por score desc."""
    col_map = get_col_map(sheet)
    task_col = col_map.get("Atividade")
    if not task_col:
        return []
    norm_q = normalize(query)
    results = []
    for row in sheet.rows:
        val = get_cell_value(row, task_col.id)
        if not val:
            continue
        score = difflib.SequenceMatcher(None, norm_q, normalize(val)).ratio()
        results.append((row, str(val), score))
    results.sort(key=lambda x: x[2], reverse=True)
    return results[:5]


# ── Intent parsing ────────────────────────────────────────────────────────────

def parse_intent(text):
    """
    Retorna dict com sheet_name, task_name, updates.
    updates = {col_name: value}
    """
    text_lower = text.lower()
    today = dt.date.today()
    updates = {}

    # Intenção principal por palavras-chave
    if any(k in text_lower for k in DONE_KEYWORDS):
        updates["Status"] = "Concluída"
        updates["Data de Fim Realizada"] = today.strftime("%Y-%m-%d")

    if any(k in text_lower for k in START_KEYWORDS):
        updates.setdefault("Status", "Em andamento")
        updates["Data de Início Realizada"] = today.strftime("%Y-%m-%d")

    if any(k in text_lower for k in DELAYED_KEYWORDS):
        updates["Status"] = "Atrasada"

    # Status explícito: "status para X" / "status pra X"
    m = re.search(r'\bstatus\b\s+(?:para|pra|=|:)\s*["\']?([^\n,\."\'/]+)["\']?', text_lower)
    if m:
        updates["Status"] = m.group(1).strip().title()

    # Datas por alias: "data de fim realizada para DD/MM"
    for alias, col_name in DATE_COL_ALIASES.items():
        pattern = re.escape(alias) + r'\s+(?:para|pra|=|:)\s*(.+?)(?:,|\.|$|\se\s)'
        m = re.search(pattern, text_lower)
        if m:
            date_val = parse_date_value(m.group(1))
            if date_val:
                updates[col_name] = date_val

    # Colunas genéricas: "coluna X para Y" / "campo X para Y"
    for m in re.finditer(
        r'(?:coluna|campo)\s+["\']?([^"\']+?)["\']?\s+(?:para|pra|=|:)\s*["\']?([^,\.\n"\']+)["\']?',
        text_lower
    ):
        col_raw = m.group(1).strip()
        value   = m.group(2).strip()
        norm_known = [normalize(c) for c in KNOWN_COLS]
        matches = difflib.get_close_matches(col_raw, norm_known, n=1, cutoff=0.5)
        if matches:
            col_name = KNOWN_COLS[norm_known.index(matches[0])]
            # Se parece com data, tenta parsear
            date_val = parse_date_value(value)
            updates[col_name] = date_val if date_val else value.title()

    # Nome da tarefa: "tarefa X do cronograma Y"
    task_name = None
    m = re.search(
        r'(?:tarefa|atividade)\s+(?:de\s+)?["\']?(.+?)["\']?\s+do\s+(?:cronograma|empreendimento|projeto)',
        text, re.IGNORECASE
    )
    if m:
        task_name = m.group(1).strip()
    else:
        m = re.search(r'(?:tarefa|atividade)\s+(?:de\s+)?["\']?(.+?)["\']?(?:\s*[,\.]|$)', text, re.IGNORECASE)
        if m:
            task_name = m.group(1).strip()

    # Nome do empreendimento: "do cronograma X" / "do empreendimento X"
    sheet_name = None
    m = re.search(
        r'do\s+(?:cronograma|empreendimento|projeto)\s+["\']?(.+?)["\']?(?:\s*[,\.]|$|\s+(?:atuali|mud|coloc|e\s))',
        text, re.IGNORECASE
    )
    if m:
        sheet_name = m.group(1).strip()

    return {"sheet_name": sheet_name, "task_name": task_name, "updates": updates}


# ── Validate updates ──────────────────────────────────────────────────────────

def validate_updates(updates, col_map, sheet):
    """
    Valida valores de colunas com dropdown contra as opções reais do SmartSheet.
    Retorna (updates_corrigidos, erros).
    """
    fixed  = {}
    errors = []
    for col_name, value in updates.items():
        col = col_map.get(col_name)
        if not col:
            errors.append(f"Coluna '{col_name}' não existe nessa sheet.")
            continue
        options = get_dropdown_options(sheet, col)
        if options and isinstance(value, str) and not re.match(r'\d{4}-\d{2}-\d{2}', value):
            norm_opts = [normalize(o) for o in options]
            matches = difflib.get_close_matches(normalize(value), norm_opts, n=1, cutoff=0.5)
            if matches:
                fixed[col_name] = options[norm_opts.index(matches[0])]
            else:
                errors.append(
                    f"'{value}' não é válido para *{col_name}*. "
                    f"Opções disponíveis: {', '.join(options)}"
                )
        else:
            fixed[col_name] = value
    return fixed, errors


# ── Format output ─────────────────────────────────────────────────────────────

def format_task_row(row, col_map, sheet_name):
    lines = [f"*Sheet:* {sheet_name}"]
    for col_name in COL_DISPLAY_ORDER:
        col = col_map.get(col_name)
        if not col:
            continue
        val = get_cell_value(row, col.id)
        if re.match(r'\d{4}-\d{2}-\d{2}', str(val)):
            val = fmt_date_display(val)
        lines.append(f"   *{col_name}:* {val or '—'}")
    return "\n".join(lines)

def format_updates_display(updates):
    lines = []
    for col, val in updates.items():
        display_val = fmt_date_display(val) if re.match(r'\d{4}-\d{2}-\d{2}', str(val)) else val
        lines.append(f"   • {col} → {display_val}")
    return "\n".join(lines)


# ── Apply updates ─────────────────────────────────────────────────────────────

def apply_updates(row, col_map, updates, sheet):
    row_update = ss_client.models.Row()
    row_update.id = row.id
    row_update.cells = []
    for col_name, value in updates.items():
        col = col_map.get(col_name)
        if not col:
            continue
        cell = ss_client.models.Cell()
        cell.column_id = col.id
        cell.value = value
        row_update.cells.append(cell)
    if not row_update.cells:
        return False, "Nenhuma célula para atualizar."
    try:
        ss_client.Sheets.update_rows(sheet.id, [row_update])
        return True, None
    except Exception as e:
        return False, str(e)


# ── Thread ✅ ─────────────────────────────────────────────────────────────────

def add_check_to_thread(sheet_name, task_name):
    """
    Adiciona ✅ na thread do briefing correspondente à (sheet_name, task_name).
    Lê o canal correto do .briefing_posted (4ª coluna) — não assume SLACK_CHANNEL_ID
    porque diferentes cronogramas postam em canais diferentes (ex.: C0BEKBE1SUS vs C0BE0QE9E79).
    Retorna True se encontrou e marcou, False caso contrário.
    """
    try:
        with open(BRIEFING_FILE) as f:
            for line in f:
                parts = line.strip().split("|")
                if len(parts) < 3:
                    continue
                s_name, t_name, thread_ts = parts[0], parts[1], parts[2]
                # 4ª coluna (canal) — pode estar vazia
                channel_id = parts[3] if len(parts) >= 4 and parts[3] else SLACK_CHANNEL_ID
                # Match tolerante: o sheet_name pode vir com prefixo "[ID] " ou sem.
                # Usa contida-substring pra cobrir os dois formatos.
                s_norm = normalize(s_name)
                t_norm = normalize(t_name)
                sheet_norm = normalize(sheet_name)
                task_norm = normalize(task_name)
                sheet_match = (sheet_norm in s_norm) or (s_norm in sheet_norm)
                task_match = (task_norm in t_norm) or (t_norm in task_norm)
                if sheet_match and task_match:
                    try:
                        slack_client.reactions_add(
                            channel=channel_id,
                            name="white_check_mark",
                            timestamp=thread_ts,
                        )
                        log.info(f"✅ adicionado na thread {thread_ts} (canal {channel_id})")
                        return True
                    except SlackApiError as e:
                        # already_reactions é benigno (já tinha ✅) — não é erro
                        if e.response.get("error") == "already_reacted":
                            log.info(f"✅ já existia na thread {thread_ts}")
                            return True
                        log.warning(f"Erro reactions_add ({channel_id}/{thread_ts}): {e.response.get('error')}")
                        return False
        log.warning(f"Thread não encontrada pra ({sheet_name}, {task_name}) em {BRIEFING_FILE}")
        return False
    except Exception as e:
        log.warning(f"Não foi possível adicionar ✅: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def is_yes(text):
    t = normalize(text)
    return any(t == w or t.startswith(w + " ") for w in CONFIRM_YES)

def is_no(text):
    t = normalize(text)
    return any(t == w or t.startswith(w + " ") for w in CONFIRM_NO)

def run():
    state      = load_state()
    processed  = set(state.get("processed", []))
    pending    = state.get("pending", {})
    dm_channel = get_dm_channel()

    new_msgs = get_new_dms(dm_channel, processed)
    if not new_msgs:
        log.info("Nenhuma mensagem nova.")
        return

    sheets_list = get_sheets()

    for msg in new_msgs:
        ts   = msg["ts"]
        text = msg.get("text", "").strip()

        # ── Resposta a pedido pendente ────────────────────────────────────
        if pending:
            latest_ts   = max(pending.keys())
            pdata       = pending[latest_ts]

            # Seleção de tarefa (quando havia múltiplas opções)
            if pdata.get("awaiting") == "selection":
                m = re.match(r'^(\d+)$', text.strip())
                if m:
                    idx = int(m.group(1)) - 1
                    options = pdata.get("options", [])
                    if 0 <= idx < len(options):
                        pdata["task_name"] = options[idx]
                        pdata["awaiting"]  = "confirmation"

                        sheet_ref = find_sheet(pdata["sheet_name"], sheets_list)
                        sheet     = ss_client.Sheets.get_sheet(sheet_ref.id)
                        col_map   = get_col_map(sheet)
                        task_res  = find_tasks(pdata["task_name"], sheet)

                        if task_res:
                            row = task_res[0][0]
                            task_display   = format_task_row(row, col_map, sheet_ref.name)
                            updates_display = format_updates_display(pdata["updates"])
                            send_dm(dm_channel,
                                f"Tarefa selecionada:\n\n{task_display}\n\n"
                                f"Vou fazer as seguintes mudanças:\n{updates_display}\n\n"
                                f"Confirma? (sim/não)"
                            )
                            pending[latest_ts] = pdata
                        else:
                            send_dm(dm_channel, "Não consegui encontrar a tarefa. Tente novamente.")
                            del pending[latest_ts]
                    else:
                        send_dm(dm_channel, f"Número inválido. Escolha entre 1 e {len(options)}.")
                    processed.add(ts)
                    save_state({"pending": pending, "processed": list(processed)})
                    continue

            # Confirmação
            if pdata.get("awaiting") == "confirmation":
                if is_yes(text):
                    sheet_ref = find_sheet(pdata["sheet_name"], sheets_list)
                    if not sheet_ref:
                        send_dm(dm_channel, "Não encontrei a sheet. Tente novamente.")
                        del pending[latest_ts]
                        processed.add(ts)
                        save_state({"pending": pending, "processed": list(processed)})
                        continue

                    sheet   = ss_client.Sheets.get_sheet(sheet_ref.id)
                    col_map = get_col_map(sheet)
                    task_res = find_tasks(pdata["task_name"], sheet)

                    if not task_res:
                        send_dm(dm_channel, "Não encontrei a tarefa. Tente novamente.")
                        del pending[latest_ts]
                        processed.add(ts)
                        save_state({"pending": pending, "processed": list(processed)})
                        continue

                    row = task_res[0][0]
                    ok, err = apply_updates(row, col_map, pdata["updates"], sheet)

                    if ok:
                        # Regra Bianca 02/07/2026: ✅ na thread APENAS se Status final = Concluída
                        final_status = pdata["updates"].get("Status")
                        if final_status == "Concluída":
                            add_check_to_thread(pdata["sheet_name"], pdata["task_name"])
                        send_dm(dm_channel,
                            f"✅ *Atualizado com sucesso!*\n"
                            f"Tarefa: *{pdata['task_name']}*\n"
                            f"Sheet: {pdata['sheet_name']}\n\n"
                            f"Mudanças aplicadas:\n{format_updates_display(pdata['updates'])}"
                        )
                        log.info(f"Atualizado: {pdata['task_name']} | {pdata['updates']}")
                    else:
                        send_dm(dm_channel, f"❌ Erro ao atualizar: {err}")

                    del pending[latest_ts]
                    processed.add(ts)
                    save_state({"pending": pending, "processed": list(processed)})
                    continue

                elif is_no(text):
                    send_dm(dm_channel, "Cancelado. Se precisar de outra atualização, é só me chamar.")
                    del pending[latest_ts]
                    processed.add(ts)
                    save_state({"pending": pending, "processed": list(processed)})
                    continue

        # ── Novo pedido ───────────────────────────────────────────────────
        intent = parse_intent(text)

        if not intent["sheet_name"] or not intent["task_name"] or not intent["updates"]:
            # Não é um pedido de atualização reconhecido
            processed.add(ts)
            save_state({"pending": pending, "processed": list(processed)})
            continue

        sheet_ref = find_sheet(intent["sheet_name"], sheets_list)
        if not sheet_ref:
            send_dm(dm_channel,
                f"Não encontrei o cronograma *{intent['sheet_name']}*. "
                f"Verifique o nome e tente novamente."
            )
            processed.add(ts)
            save_state({"pending": pending, "processed": list(processed)})
            continue

        sheet   = ss_client.Sheets.get_sheet(sheet_ref.id)
        col_map = get_col_map(sheet)

        updates, errors = validate_updates(intent["updates"], col_map, sheet)

        if errors:
            send_dm(dm_channel, "⚠️ Alguns valores não são válidos:\n" + "\n".join(f"• {e}" for e in errors))
            if not updates:
                processed.add(ts)
                save_state({"pending": pending, "processed": list(processed)})
                continue

        task_results = find_tasks(intent["task_name"], sheet)
        good_matches = [(r, n, s) for r, n, s in task_results if s >= 0.4]

        if not good_matches:
            lines = [f"Não encontrei a tarefa *{intent['task_name']}* no cronograma *{sheet_ref.name}*."]
            if task_results:
                lines.append("\nAs mais parecidas que encontrei:")
                for i, (_, name, _) in enumerate(task_results[:3], 1):
                    lines.append(f"   {i}. {name}")
                lines.append("\nTente novamente com o nome mais próximo ao do SmartSheet.")
            send_dm(dm_channel, "\n".join(lines))
            processed.add(ts)
            save_state({"pending": pending, "processed": list(processed)})
            continue

        if len(good_matches) > 1:
            lines = ["Encontrei mais de uma tarefa parecida. Qual delas?"]
            for i, (_, name, _) in enumerate(good_matches[:5], 1):
                lines.append(f"   {i}. {name}")
            lines.append("\nResponda com o número.")
            send_dm(dm_channel, "\n".join(lines))
            pending[ts] = {
                "sheet_name": sheet_ref.name,
                "task_name":  good_matches[0][1],
                "updates":    updates,
                "awaiting":   "selection",
                "options":    [n for _, n, _ in good_matches[:5]],
            }
        else:
            row, best_name, _ = good_matches[0]
            task_display    = format_task_row(row, col_map, sheet_ref.name)
            updates_display = format_updates_display(updates)
            send_dm(dm_channel,
                f"Encontrei a tarefa:\n\n{task_display}\n\n"
                f"Vou fazer as seguintes mudanças:\n{updates_display}\n\n"
                f"Confirma? (sim/não)"
            )
            pending[ts] = {
                "sheet_name": sheet_ref.name,
                "task_name":  best_name,
                "updates":    updates,
                "awaiting":   "confirmation",
            }

        processed.add(ts)
        if len(processed) > 500:
            processed = set(list(processed)[-300:])
        save_state({"pending": pending, "processed": list(processed)})

    save_state({"pending": pending, "processed": list(processed)})
    log.info("Concluído.")


if __name__ == "__main__":
    run()

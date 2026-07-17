"""
szni-aprovador-dm.py — Detecta :white_check_mark: nas DMs das threads de
fechamento e aplica no SmartSheet automaticamente.

Roda por Bianca reagir ou responder na DM (ou manualmente quando ela
pedir "aplica as que reagi").
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
_hermes_env = os.path.expanduser('~/.hermes/scripts/.env')
_project_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../.env')
load_dotenv(_hermes_env) if os.path.exists(_hermes_env) else load_dotenv(_project_env)

import smartsheet
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from datetime import datetime

SLACK_BOT_TOKEN = os.environ['SLACK_BOT_TOKEN']
SMARTSHEET_TOKEN = os.environ['SMARTSHEET_TOKEN']
SMARTSHEET_FOLDER_ID = int(os.environ['SMARTSHEET_FOLDER_ID'])
BIANCA_USER_ID = os.environ['BIANCA_USER_ID']

slack = WebClient(token=SLACK_BOT_TOKEN)
ss = smartsheet.Smartsheet(SMARTSHEET_TOKEN)

DM_CHANNEL = 'D0BEAN1R08G'  # canal DM Bianca
PROCESSED_PATH = os.path.expanduser('~/.hermes/scripts/.aprovar_processed')
STATE_THREADS = os.path.expanduser('~/.hermes/scripts/.fechamento_threads')
STATE_TASKS = os.path.expanduser('~/.hermes/scripts/.fechamento_state')


def task_key(sheet_name, task_name, status='', inicio='', fim=''):
    return '|'.join([
        normalize(sheet_name).lower().strip(),
        normalize(task_name).lower().strip(),
        (status or '').strip(),
        (inicio or '').strip(),
        (fim or '').strip(),
    ])


def normalize_date(value):
    value = strip_formatting(value or '')
    if not value:
        return ''
    lower = value.lower().strip()
    placeholders = {
        '(vazio)',
        'vazio',
        '(hoje)',
        '(preencher com data de início planejada)',
        'preencher com data de início planejada',
        'none',
        'null',
    }
    if lower in placeholders:
        return ''
    value = re.sub(r'\s*\((?:hoje|vazio|preencher com data de início planejada)\)\s*', '', value, flags=re.IGNORECASE).strip()
    if not value:
        return ''
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d/%m/%y'):
        try:
            return datetime.strptime(value, fmt).strftime('%Y-%m-%d')
        except ValueError:
            pass
    return value

# --- helpers ---

def normalize(s):
    return (s.replace(':books:', '📚').replace(':bar_chart:', '📊')
             .replace(':pushpin:', '📌').replace(':warning:', '⚠️')
             .replace(':mega:', '📣').replace(':alarm:', '🚨')
             .replace('&gt;', '>').replace('&lt;', '<').replace('&amp;', '&')
             .strip())


def strip_formatting(value):
    value = normalize(value or '')
    value = re.sub(r'\*(.*?)\*', r'\1', value)
    value = re.sub(r'_(.*?)_', r'\1', value)
    value = re.sub(r'`(.*?)`', r'\1', value)
    return value.strip()

def find_sheet(sheet_hint):
    """sheet_hint = 'Farol' ou 'São Miguel'. Retorna sheet object."""
    children = ss.Folders.get_folder_children(SMARTSHEET_FOLDER_ID).data
    for item in children:
        if hasattr(item, 'name') and sheet_hint.upper() in item.name.upper():
            return ss.Sheets.get_sheet(item.id), item.name
    raise ValueError(f"Sheet '{sheet_hint}' não encontrada na pasta ativa")

def find_row(sheet, task_name):
    col_map = {c.title: c.id for c in sheet.columns}
    target = task_name.lower().strip()
    target_n = normalize(task_name).lower()
    # 1) match exato
    for row in sheet.rows:
        val = next((c.value or '').lower().strip() for c in row.cells if c.column_id == col_map['Atividade'])
        if val == target:
            return row, col_map
    # 2) match normalizado (📚 = :books:)
    for row in sheet.rows:
        val_n = normalize(next((c.value or '' for c in row.cells if c.column_id == col_map['Atividade']), '')).lower()
        if target_n == val_n:
            return row, col_map
    # 3) substring
    for row in sheet.rows:
        val = next((c.value or '').lower().strip() for c in row.cells if c.column_id == col_map['Atividade'])
        if target in val or val in target:
            return row, col_map
    return None, col_map

def load_today_threads():
    """Retorna dict {sheet: thread_ts} das threads de fechamento de hoje."""
    import datetime as dt
    today = dt.date.today().isoformat()
    out = {}
    if not os.path.exists(STATE_THREADS):
        return out
    for line in open(STATE_THREADS):
        parts = line.strip().split('|')
        if len(parts) >= 3 and parts[0] == today and re.match(r'^\d{10}\.\d{6}$', parts[2]):
            out[parts[1]] = parts[2]
    return out

def load_processed():
    if not os.path.exists(PROCESSED_PATH):
        return set()
    return set(line.strip() for line in open(PROCESSED_PATH))


def load_processed_keys():
    processed = set()
    for line in load_processed():
        parts = [p.strip() for p in line.split('|') if p.strip()]
        if not parts:
            continue
        task_name = parts[0]
        meta = {}
        for p in parts[1:]:
            if '=' in p:
                k, v = p.split('=', 1)
                meta[k.strip()] = v.strip()
        sheet_name = meta.get('sheet', '')
        status = meta.get('status', '')
        inicio = meta.get('inicio', '')
        fim = meta.get('fim', '')
        if sheet_name and sheet_name != '<auto>':
            processed.add(task_key(sheet_name, task_name, status, inicio, fim))
    return processed

def mark_processed(task_name, sheet_name, ts_iso):
    with open(PROCESSED_PATH, 'a') as f:
        f.write(f"{task_name}|sheet={sheet_name}|thread_ts=<dm>|channel={DM_CHANNEL}|applied_via=szni_aprovador_dm_at_{ts_iso}\n")


def mark_processed_applied(task_name, sheet_name, status, inicio, fim, ts_iso):
    with open(PROCESSED_PATH, 'a') as f:
        f.write(
            f"{task_name}|sheet={sheet_name}|status={status or ''}|inicio={inicio or ''}|fim={fim or ''}|thread_ts=<dm>|channel={DM_CHANNEL}|applied_via=szni_aprovador_dm_at_{ts_iso}\n"
        )

def get_approved_tasks_from_thread(thread_ts):
    """Varre replies da thread DM e retorna lista de tasks aprovadas (✅) ou
    com reply 'sim'/'pode aplicar'/'aprovado' da Bianca."""
    import datetime as dt
    try:
        r = slack.conversations_replies(channel=DM_CHANNEL, ts=thread_ts)
    except SlackApiError as e:
        print(f"ERRO listando thread {thread_ts}: {e}")
        return []
    msgs = r.get('messages', [])
    # Encontra a raiz (cabeçalho) pra extrair sheet
    root = msgs[0]
    # Encontra replies (msg do bot) com ✅ ou com texto de Bianca
    approved = []
    for m in msgs[1:]:
        user = m.get('user', '')
        text = m.get('text') or ''
        # é aprovação?
        # Só considera aprovação se a reação for da Bianca
        is_reaction = any(
            rx['name'] in ('white_check_mark', 'check', 'heavy_check_mark')
            and BIANCA_USER_ID in rx.get('users', [])
            for rx in m.get('reactions', [])
        )
        is_bianca_text = (user == BIANCA_USER_ID and
                          re.match(r'^\s*(sim|aprovado|pode aplicar|apl[ic]+ar|sigo)\b', text, re.IGNORECASE))
        if is_reaction or is_bianca_text:
            # Parsear "📌 {sheet} / {team} — {task}"
            m_obj = re.search(r':pushpin:\s*\*?([^*\n]+?)\*?\s*/\s*\*?([^*\n]+?)\*?\s*[—\-]\s*\*?(.+?)\*?(?:\n|$)', text)
            if not m_obj:
                continue
            sheet = m_obj.group(1).strip()
            team = m_obj.group(2).strip()
            task = m_obj.group(3).strip()
            # Encontrar linha de sugestão pra extrair Status/datas
            sug_match = re.search(r':memo:\s*(.+?)(?:\n⚠️|$)', text, re.DOTALL)
            suggestion_str = sug_match.group(1) if sug_match else ''
            suggestion_str = suggestion_str.split('─', 1)[0].strip()
            # Parsear "Status: X | Início Realizada: Y | Fim Realizada: Z"
            parsed = {}
            for part in suggestion_str.split('|'):
                if ':' in part:
                    k, _, v = part.partition(':')
                    parsed[k.strip()] = v.strip()
            approved.append({
                'sheet': strip_formatting(sheet),
                'team': strip_formatting(team),
                'task': strip_formatting(task),
                'status': strip_formatting(parsed.get('Status', '')),
                'inicio': normalize_date(strip_formatting(parsed.get('Início Realizada', ''))),
                'fim': normalize_date(strip_formatting(parsed.get('Fim Realizada', ''))),
            })
    return approved

def apply_to_smartsheet(approved):
    """Aplica lista de aprovações no SmartSheet."""
    import datetime as dt
    ts_iso = dt.datetime.now().isoformat(timespec='seconds')
    sheet_cache = {}
    applied = []
    skipped = []
    errors = []

    for a in approved:
        sheet_name = a['sheet']
        if sheet_name not in sheet_cache:
            try:
                sh, full_name = find_sheet(sheet_name)
                sheet_cache[sheet_name] = (sh, full_name)
            except ValueError as e:
                errors.append(f"  ✗ {a['task']}: {e}")
                continue
        sh, full_name = sheet_cache[sheet_name]
        row, col_map = find_row(sh, a['task'])
        if not row:
            errors.append(f"  ✗ {a['task']}: não encontrada em '{full_name}'")
            continue
        # Validar valores
        status = a['status']
        if status and not any(o == status for o in next(c for c in sh.columns if c.title == 'Status').options):
            errors.append(f"  ✗ {a['task']}: status '{status}' fora do picklist")
            continue
        # Aplicar
        row_update = smartsheet.models.Row()
        row_update.id = row.id
        row_update.cells = []

        status_cell = smartsheet.models.Cell()
        status_cell.column_id = col_map['Status']
        status_cell.value = status or ''
        row_update.cells.append(status_cell)

        inicio_cell = smartsheet.models.Cell()
        inicio_cell.column_id = col_map['Data de Início Realizada']
        inicio_cell.value = a['inicio'] or ''
        row_update.cells.append(inicio_cell)

        fim_cell = smartsheet.models.Cell()
        fim_cell.column_id = col_map['Data de Fim Realizada']
        fim_cell.value = a['fim'] or ''
        row_update.cells.append(fim_cell)
        try:
            ss.Sheets.update_rows(sh.id, [row_update])
            applied.append({
                'sheet': full_name,
                'task': a['task'],
                'status': status,
                'inicio': a['inicio'] or '(vazio)',
                'fim': a['fim'] or '(vazio)',
            })
            mark_processed_applied(a['task'], full_name, status, a['inicio'], a['fim'], ts_iso)
        except Exception as e:
            err_text = str(e)
            if 'strict requirements for type DATE' in err_text:
                errors.append(f"  ✗ {a['task']}: data inválida enviada ao SmartSheet ({err_text})")
            else:
                errors.append(f"  ✗ {a['task']}: {err_text}")

    return applied, errors

def main():
    out = []
    threads = load_today_threads()
    if not threads:
        return  # silêncio
    all_approved = []
    for sheet, ts in threads.items():
        approved = get_approved_tasks_from_thread(ts)
        for a in approved:
            out.append(f"  Aprovacao detectada: {a['task']}")
        all_approved.extend(approved)

    # Filtrar já processados
    processed_keys = load_processed_keys()
    pending = []
    seen_pending = set()
    for a in all_approved:
        key = task_key(a['sheet'], a['task'], a['status'], a['inicio'], a['fim'])
        if key in processed_keys or key in seen_pending:
            continue
        seen_pending.add(key)
        pending.append(a)
    if not pending:
        return  # silêncio

    applied, errors = apply_to_smartsheet(pending)
    out.append(f"=== Aplicando {len(applied)} no SmartSheet ===")
    for x in applied:
        out.append(f"  OK {x['sheet']} / {x['task']} => {x['status']}")
    for e in errors:
        out.append(e)
    out.append(f"Total: {len(applied)} aplicadas, {len(errors)} erros")

    if out:
        print("\n".join(out))

if __name__ == '__main__':
    main()
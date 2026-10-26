"""Capa de datos sobre Google Sheets.

Usa un Google Spreadsheet como base de datos principal. Cada "tabla" es una
pestaña (worksheet) del spreadsheet. Las credenciales se leen de la variable
de entorno GOOGLE_CREDENTIALS (JSON de la service account) o del archivo
credentials.json.
"""
import os
import json
import time
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SPREADSHEET_ID = os.environ.get(
    "SPREADSHEET_ID",
    "1juWj18xaFMCdLRDrLWXTPi3qYuLa_IA8FqGa7eE_c2w",
)

# Esquema de pestañas y sus cabeceras
SCHEMAS = {
    "Users": ["id", "username", "password_hash", "role", "is_active", "created_at", "teacher_id", "student_id"],
    "Teachers": ["id", "user_id", "full_name", "phone", "email", "specialty", "bio", "created_at"],
    "Students": ["id", "user_id", "full_name", "phone", "email", "birthdate", "parent_name", "parent_phone", "parent_email", "notes", "created_at"],
    "TeacherStudent": ["teacher_id", "student_id", "assigned_at"],
    "Schedules": ["id", "teacher_id", "student_id", "group_name", "day_of_week", "start_time", "end_time", "room", "notes", "created_at"],
}

_client = None
_spreadsheet = None
_worksheets = {}
_records_cache = {}
CACHE_TTL = 60


def _get_credentials():
    raw = os.environ.get("GOOGLE_CREDENTIALS")
    if raw:
        info = json.loads(raw)
    else:
        # Intentar archivo local
        path = os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")
        with open(path, "r", encoding="utf-8") as f:
            info = json.load(f)
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def get_client():
    global _client
    if _client is None:
        _client = gspread.authorize(_get_credentials())
    return _client


def get_spreadsheet():
    global _spreadsheet
    if _spreadsheet is None:
        _spreadsheet = get_client().open_by_key(SPREADSHEET_ID)
    return _spreadsheet


def get_worksheet(name):
    if name in _worksheets:
        return _worksheets[name]
    ss = get_spreadsheet()
    try:
        ws = ss.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=name, rows=1000, cols=len(SCHEMAS[name]))
        ws.append_row(SCHEMAS[name])
    if ws.row_count == 0 or not ws.row_values(1):
        ws.append_row(SCHEMAS[name])
    _worksheets[name] = ws
    return ws


def init_db():
    """Crea las pestañas si no existen y el director por defecto."""
    for name in SCHEMAS:
        get_worksheet(name)
    users = all_records("Users")
    if not users:
        from werkzeug.security import generate_password_hash
        append_record("Users", {
            "username": "director",
            "password_hash": generate_password_hash("director123"),
            "role": "director",
            "is_active": True,
            "created_at": datetime.utcnow().isoformat(),
            "teacher_id": "",
            "student_id": "",
        })
        invalidate("Users")
        print("Director por defecto creado: director / director123")


def _headers(name):
    return SCHEMAS[name]


def _row_to_dict(headers, row):
    d = {}
    for i, h in enumerate(headers):
        val = row[i] if i < len(row) else ""
        d[h] = val
    return d


def all_records(name):
    now = time.time()
    cached = _records_cache.get(name)
    if cached and (now - cached[0]) < CACHE_TTL:
        return cached[1]
    ws = get_worksheet(name)
    headers = _headers(name)
    rows = ws.get_all_values()
    if not rows:
        result = []
        _records_cache[name] = (now, result)
        return result
    data_rows = rows[1:]
    result = []
    for r in data_rows:
        if not any(str(c).strip() for c in r):
            continue
        d = _row_to_dict(headers, r)
        result.append(d)
    _records_cache[name] = (now, result)
    return result


def get_by_id(name, record_id, id_col="id"):
    for r in all_records(name):
        if str(r.get(id_col, "")).strip() == str(record_id).strip():
            return r
    return None


def _next_id(name):
    records = all_records(name)
    ids = []
    for r in records:
        try:
            ids.append(int(r.get("id", 0)))
        except (ValueError, TypeError):
            pass
    return (max(ids) if ids else 0) + 1


def append_record(name, data):
    ws = get_worksheet(name)
    headers = _headers(name)
    if "id" in headers and not data.get("id"):
        data = dict(data)
        data["id"] = _next_id(name)
    # normalizar valores
    row = []
    for h in headers:
        v = data.get(h, "")
        if v is None:
            v = ""
        if isinstance(v, bool):
            v = "TRUE" if v else "FALSE"
        row.append(v)
    ws.append_row(row, value_input_option="USER_ENTERED")
    invalidate(name)
    return data.get("id")


def _find_row_index(name, record_id, id_col="id"):
    ws = get_worksheet(name)
    values = ws.col_values(_headers(name).index(id_col) + 1)
    # values[0] es cabecera
    for i, v in enumerate(values[1:], start=2):
        if str(v).strip() == str(record_id).strip():
            return i
    return None


def update_record(name, record_id, data, id_col="id"):
    ws = get_worksheet(name)
    headers = _headers(name)
    idx = _find_row_index(name, record_id, id_col)
    if idx is None:
        return False
    # leer fila actual y mezclar
    current = all_records(name)
    cur = None
    for r in current:
        if str(r.get(id_col, "")).strip() == str(record_id).strip():
            cur = r
            break
    merged = dict(cur) if cur else {}
    merged.update(data)
    merged[id_col] = record_id
    row = []
    for h in headers:
        v = merged.get(h, "")
        if v is None:
            v = ""
        if isinstance(v, bool):
            v = "TRUE" if v else "FALSE"
        row.append(v)
    last_col = chr(ord("A") + len(headers) - 1) if len(headers) <= 26 else "Z"
    ws.update(f"A{idx}:{last_col}{idx}", [row])
    invalidate(name)
    return True


def delete_record(name, record_id, id_col="id"):
    idx = _find_row_index(name, record_id, id_col)
    if idx is None:
        return False
    ws = get_worksheet(name)
    ws.delete_rows(idx)
    invalidate(name)
    return True


# ---------- Helpers de conveniencia ----------
def to_int(v):
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def str_to_bool(v):
    return str(v).strip().upper() in ("TRUE", "1", "YES", "SI", "SÍ")


def parse_date(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    return None

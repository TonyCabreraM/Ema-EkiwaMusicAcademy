"""
Módulo de respaldo a Google Drive.

Permite conectar la aplicación con Google Drive (OAuth 2.0 con cuenta personal)
para subir automáticamente los respaldos JSON y, en producción, un dump de la
base de datos PostgreSQL.

Configuración requerida (variables de entorno):
  GOOGLE_DRIVE_CLIENT_ID      -> Client ID de las credenciales OAuth 2.0
  GOOGLE_DRIVE_CLIENT_SECRET  -> Client Secret de las credenciales OAuth 2.0
  GOOGLE_DRIVE_FOLDER_ID      -> (opcional) ID de la carpeta de Drive donde subir.
                                 Si no se define, se crea/usa una carpeta "JubiloBackups".
  DATABASE_URL                -> (producción) URL de PostgreSQL para el dump.

El token OAuth se guarda en la base de datos (tabla app_settings) para que
persista entre reinicios (el disco de Render es efímero).
"""

import os
import io
import json
import subprocess
from datetime import datetime

# Importaciones de Google (se instalan con requirements.txt)
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# Scopes necesarios: acceso completo a los archivos de Drive del usuario.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# Nombre de la carpeta raíz donde se guardan los respaldos en Drive.
DRIVE_ROOT_FOLDER = "JubiloBackups"


def get_client_config():
    """Devuelve la configuración del cliente OAuth desde variables de entorno."""
    client_id = os.environ.get("GOOGLE_DRIVE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return None
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost:5000/drive/callback"],
        }
    }


def is_configured():
    """True si hay credenciales OAuth configuradas."""
    return get_client_config() is not None


def get_credentials_from_db():
    """Lee el token OAuth guardado en la base de datos (tabla app_settings)."""
    try:
        from models import db, AppSetting
        row = AppSetting.query.filter_by(key="drive_token").first()
        if row and row.value:
            return Credentials.from_authorized_user_info(json.loads(row.value), SCOPES)
    except Exception:
        pass
    return None


def save_credentials_to_db(credentials):
    """Guarda el token OAuth en la base de datos."""
    try:
        from models import db, AppSetting
        token_json = json.dumps({
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes,
        })
        row = AppSetting.query.filter_by(key="drive_token").first()
        if row:
            row.value = token_json
        else:
            db.session.add(AppSetting(key="drive_token", value=token_json))
        db.session.commit()
        return True
    except Exception as e:
        print(f"[Drive] Error guardando token: {e}")
        return False


def is_connected():
    """True si hay un token válido guardado."""
    creds = get_credentials_from_db()
    if not creds:
        return False
    try:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            save_credentials_to_db(creds)
        return True
    except Exception:
        return False


def build_flow(base_url=None):
    """Construye el flujo OAuth para iniciar la autorización.
    base_url: URL pública de la app (ej. https://jubiloqrv2.onrender.com).
              Si no se pasa, usa PUBLIC_BASE_URL o localhost."""
    client_config = get_client_config()
    if not client_config:
        return None
    flow = Flow.from_client_config(client_config, scopes=SCOPES)
    # En Render la URL pública es https://...; en local es http://localhost:5000
    if not base_url:
        base_url = os.environ.get("PUBLIC_BASE_URL", "http://localhost:5000")
    base_url = base_url.rstrip("/")
    flow.redirect_uri = f"{base_url}/drive/callback"
    return flow


def get_redirect_uri(base_url=None):
    """Devuelve la URI de redirección exacta que debe registrarse en Google Cloud."""
    if not base_url:
        base_url = os.environ.get("PUBLIC_BASE_URL", "http://localhost:5000")
    return f"{base_url.rstrip('/')}/drive/callback"


def get_drive_service():
    """Devuelve un servicio autenticado de Google Drive."""
    creds = get_credentials_from_db()
    if not creds:
        return None
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_credentials_to_db(creds)
    return build("drive", "v3", credentials=creds)


def get_or_create_root_folder(service):
    """Busca (o crea) la carpeta raíz de respaldos en Drive."""
    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")
    if folder_id:
        return folder_id

    # Buscar carpeta por nombre
    query = f"name='{DRIVE_ROOT_FOLDER}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = service.files().list(q=query, fields="files(id, name)", pageSize=10).execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]

    # Crear la carpeta si no existe
    file_metadata = {
        "name": DRIVE_ROOT_FOLDER,
        "mimeType": "application/vnd.google-apps.folder",
    }
    folder = service.files().create(body=file_metadata, fields="id").execute()
    return folder["id"]


def upload_file(service, folder_id, filename, content_bytes, mime_type="application/json"):
    """Sube un archivo a la carpeta de Drive. Si ya existe con el mismo nombre, lo reemplaza."""
    # Buscar si ya existe un archivo con el mismo nombre en la carpeta
    query = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
    results = service.files().list(q=query, fields="files(id, name)", pageSize=10).execute()
    existing = results.get("files", [])

    media = MediaIoBaseUpload(io.BytesIO(content_bytes), mimetype=mime_type, resumable=True)

    if existing:
        # Actualizar el archivo existente
        file_id = existing[0]["id"]
        service.files().update(fileId=file_id, media_body=media).execute()
        return file_id, "updated"
    else:
        # Crear un archivo nuevo
        file_metadata = {
            "name": filename,
            "parents": [folder_id],
        }
        file = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
        return file["id"], "created"


def upload_backup_to_drive(backup_data, filename=None):
    """
    Sube un respaldo JSON a Google Drive.
    backup_data: dict con los datos del respaldo (o bytes).
    filename: nombre del archivo. Si es None, se genera automáticamente.
    Devuelve (success: bool, message: str).
    """
    if not is_configured():
        return False, "Google Drive no está configurado (faltan GOOGLE_DRIVE_CLIENT_ID/SECRET)."
    if not is_connected():
        return False, "No hay sesión de Google Drive conectada. Ve a Respaldos y pulsa 'Conectar con Google Drive'."

    try:
        service = get_drive_service()
        if not service:
            return False, "No se pudo autenticar con Google Drive."

        folder_id = get_or_create_root_folder(service)

        if isinstance(backup_data, dict):
            content = json.dumps(backup_data, indent=2, ensure_ascii=False).encode("utf-8")
        elif isinstance(backup_data, bytes):
            content = backup_data
        else:
            content = str(backup_data).encode("utf-8")

        if not filename:
            filename = f"backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"

        file_id, action = upload_file(service, folder_id, filename, content)
        return True, f"Respaldo subido a Google Drive ({action}): {filename}"
    except Exception as e:
        return False, f"Error subiendo a Google Drive: {e}"


def dump_postgres_to_bytes():
    """
    Genera un dump de la base de datos PostgreSQL usando pg_dump.
    Devuelve (bytes, filename) o (None, None) si no hay PostgreSQL configurado.
    """
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url or not db_url.startswith("postgres"):
        return None, None

    try:
        # Convertir postgresql+psycopg:// a postgresql:// para pg_dump
        pg_url = db_url
        if pg_url.startswith("postgresql+psycopg://"):
            pg_url = pg_url.replace("postgresql+psycopg://", "postgresql://", 1)
        elif pg_url.startswith("postgres://"):
            pg_url = pg_url.replace("postgres://", "postgresql://", 1)

        # Nombre por día: se reemplaza con cada cambio del día
        filename = f"postgres_dump_{datetime.utcnow().strftime('%Y%m%d')}.sql"
        result = subprocess.run(
            ["pg_dump", pg_url, "--no-owner", "--no-privileges"],
            capture_output=True,
            timeout=120,
        )
        if result.returncode == 0:
            return result.stdout, filename
        else:
            print(f"[Drive] Error en pg_dump: {result.stderr.decode('utf-8', errors='ignore')}")
            return None, None
    except FileNotFoundError:
        print("[Drive] pg_dump no está instalado en este entorno.")
        return None, None
    except Exception as e:
        print(f"[Drive] Error generando dump PostgreSQL: {e}")
        return None, None


def upload_postgres_dump():
    """Sube un dump de PostgreSQL a Drive. Devuelve (success, message)."""
    if not is_configured() or not is_connected():
        return False, "Google Drive no conectado."
    content, filename = dump_postgres_to_bytes()
    if content is None:
        return False, "No se pudo generar el dump de PostgreSQL (¿no hay DATABASE_URL o pg_dump?)."
    try:
        service = get_drive_service()
        folder_id = get_or_create_root_folder(service)
        file_id, action = upload_file(service, folder_id, filename, content, mime_type="application/sql")
        return True, f"Dump de PostgreSQL subido a Drive ({action}): {filename}"
    except Exception as e:
        return False, f"Error subiendo dump a Drive: {e}"

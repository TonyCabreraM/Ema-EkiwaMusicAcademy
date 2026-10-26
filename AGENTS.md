# AGENTS.md — JubiloQr (Jubilo)

## Proyecto
Aplicación web Flask para tarjetas de regalo digitales con código QR (membresías/gimnasio "Jubilo").
- Entrada: `app.py` (Flask app). Modelos en `models.py`, formularios en `forms.py`, utilidades en `utils.py`.
- Base de datos: SQLite local (`jubilo.db`) / PostgreSQL en producción (Render) vía `DATABASE_URL`.
- Despliegue: Render (`render.yaml`), gunicorn. Runtime Python 3.12.
- Dependencias: `requirements.txt`.

## Convenciones
- No commitear secretos (`.env`, `*.db`, backups, imágenes). Ver `.gitignore`.
- Código en español (mensajes, comentarios). Commits en español.
- Siempre hacer commit y push a GitHub tras cambios (repo: TonyCabreraM/JubiloQrv2).

## Memoria del agente
Esta sección se actualiza con las cosas que el usuario pide recordar. Cada entrada con fecha.

- 2026-10-26: El usuario quiere usar algunas cosas de este proyecto para otro proyecto. Guardar aquí cualquier detalle que pida recordar.
- 2026-10-26: Workflow acordado: guardar en memoria del agente las cosas que el usuario diga "recuerda"; crear/actualizar repo en GitHub y hacer commit siempre.

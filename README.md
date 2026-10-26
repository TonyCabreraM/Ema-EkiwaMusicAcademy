# EMA - Ekiwa Music Academy

Sistema web para la administración de una escuela de música.

## Roles
- **Director**: gestiona maestros, alumnos, asignaciones y horarios.
- **Maestro**: ve sus alumnos asignados y sus horarios.
- **Alumno/Padre**: ve su maestro y su horario.

## Requisitos
- Python 3.12+

## Instalación local
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```
Abre `http://127.0.0.1:5000`

## Acceso inicial
- Usuario: `director`
- Contraseña: `director123` (cámbiala en producción)

## Despliegue en Render
- Tipo de servicio: **Web Service** (Python) + **PostgreSQL** (Render Database).
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn --bind 0.0.0.0:$PORT --workers=1 --timeout=120 app:app`
- Variables de entorno: `SECRET_KEY`, `DATABASE_URL` (Internal Database URL del Postgres).

No usar SQLite en Render (disco efímero).

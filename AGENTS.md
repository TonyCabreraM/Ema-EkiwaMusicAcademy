# AGENTS.md — EMA (Ekiwa Music Academy)

## Proyecto
Sistema web Flask para administrar una escuela de música (EMA).
- Roles: director, maestro, alumno/padre.
- Director: gestiona maestros, alumnos, asignaciones maestro-alumno y horarios.
- Maestro: ve sus alumnos asignados y sus horarios.
- Alumno/Padre: ve su maestro y su horario.
- Entrada: `app.py` (Flask). Modelos en `models.py`. Templates en `templates/`.
- Base de datos: SQLite local (`ema.db`) / PostgreSQL en producción (Render) vía `DATABASE_URL`.
- Despliegue: Render (Web Service + PostgreSQL). Runtime Python 3.12.

## Convenciones
- No commitear secretos (`.env`, `*.db`, backups). Ver `.gitignore`.
- Código y commits en español.
- Siempre hacer commit y push a GitHub tras cambios (repo: TonyCabreraM/Ema-EkiwaMusicAcademy).
- Código original de Jubilo preservado en branch `jubilo-backup`.

## Memoria del agente
Esta sección se actualiza con las cosas que el usuario pide recordar. Cada entrada con fecha.

- 2026-10-26: El usuario quiere usar algunas cosas del proyecto Jubilo para EMA (Ekiwa Music Academy). Guardar aquí cualquier detalle que pida recordar.
- 2026-10-26: Workflow acordado: guardar en memoria del agente las cosas que el usuario diga "recuerda"; crear/actualizar repo en GitHub y hacer commit siempre.
- 2026-10-26: Base de datos migrada a Google Sheets (spreadsheet ID 1juWj18xaFMCdLRDrLWXTPi3qYuLa_IA8FqGa7eE_c2w). Requiere service account (env GOOGLE_CREDENTIALS) y compartir el Sheet con el email de la service account.
- 2026-10-26: IMPORTANTE: nunca subir el JSON de service account a GitHub (secret scanning lo bloquea). Si una clave se expuso, rotarla en Google Cloud. Agregado *.json a .gitignore.
- 2026-10-26: Rediseño visual: estilo iOS glassmorphism (glass, blur, orbs, tipografía San Francisco), alusivo a escuela de música (Musicity México). Paleta rojo acento sobre fondo oscuro.

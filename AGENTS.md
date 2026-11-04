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
- 2026-10-26: Cache en memoria (60s) en sheets_db para evitar quota 429 de Google Sheets; invalidación en escrituras.
- 2026-11-04: Fondo: static/Background01.jpg (patrón de guitarras EMA) con tarjetas/tablas de cristal esmerilado (backdrop-filter blur).
- 2026-11-04: Tipografías locales: Alfa Slab One (encabezados) y Roboto Slab (texto), servidas desde static/fonts. Tablas con texto blanco (variables --bs-table-*).
- 2026-11-04: Responsive móvil: navbar toggler, tablas con .table-responsive y media queries en style.css.
- 2026-11-04: Contraseñas: cada usuario cambia la suya en /account/password; recuperación en /forgot genera solicitud en hoja PasswordRequests que el director aprueba (/director/password-requests) generando contraseña temporal. Menú móvil ahora absoluto (se superpone).
- 2026-11-04: Clases muestra: hoja TrialClasses. El maestro registra en su panel (/teacher, formulario + historial y contadores); el director confirma o rechaza en /director/trial-classes. La lista de maestros muestra confirmadas/total. Menú hamburguesa con estilo glass (blur 26px, rgba(18,18,22,0.55)).
- 2026-11-04: Roles: el rol 'admin' (Administrador) tiene los mismos permisos que 'director' (role_required agrega admin cuando se pide director). El director crea administradores en /director/users (menú "Usuarios"). Menú móvil ahora es mosaico de botones (grid auto-fit) con blur 44px y fondo rgba(20,20,26,0.30).
- 2026-11-04: Clase muestra solo requiere día y hora de inicio (sin nombre, teléfono ni hora final); prospect_name se guarda como "Clase muestra". Menú móvil: fondo más sólido rgba(14,14,19,0.90) para legibilidad de los mosaicos.
- 2026-11-04: Diagnóstico: /director/diagnostics (enlace al pie) prueba lectura/escritura de Google Sheets y muestra el correo de la service account; errorhandler global muestra el detalle en templates/error.html. Las escrituras (crear usuario, clase muestra) capturan la excepción y la muestran con flash.
- 2026-11-04: BUG corregido: faltaba la función invalidate() en sheets_db, por lo que TODAS las escrituras (append/update/delete) fallaban con NameError. Ahora limpia la caché en memoria. También se agregó _api_retry (3 intentos con espera) para 429/5xx de Google Sheets.
- 2026-11-04: Clases muestra agrupadas por mes y año (helper group_trials_by_month) en panel del maestro y del director; el director puede borrar registros. Pagos mensuales: hoja Payments (student_id, period YYYY-MM, status pending/declared/paid). El alumno registra su pago en /student (botón, estado 'declared'); el director confirma en /director/payments (selector de mes, marca pagado/pendiente). Pendiente=rojo, declarado=amarillo, pagado=verde.

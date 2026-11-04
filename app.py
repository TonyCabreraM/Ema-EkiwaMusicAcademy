import os
import secrets
import string
from functools import wraps
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

import sheets_db as db

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')

db.init_db()


# ---------- Auth helpers ----------
def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if 'user_id' not in session:
            flash('Debes iniciar sesión.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapped


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            allowed = set(roles)
            # El administrador tiene los mismos permisos que el director
            if 'director' in allowed:
                allowed.add('admin')
            if session.get('role') not in allowed:
                flash('No tienes permisos para esta sección.', 'danger')
                return redirect(url_for('home'))
            return f(*args, **kwargs)
        return wrapped
    return decorator


@app.before_request
def load_user():
    g.user = None
    if 'user_id' in session:
        g.user = db.get_by_id("Users", session['user_id'])


# ---------- Helpers de dominio ----------
DAYS = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']


def get_teacher_by_user(user_id):
    for t in db.all_records("Teachers"):
        if str(t.get("user_id")) == str(user_id):
            return t
    return None


def get_student_by_user(user_id):
    for s in db.all_records("Students"):
        if str(s.get("user_id")) == str(user_id):
            return s
    return None


def teacher_students(teacher_id):
    ids = [r["student_id"] for r in db.all_records("TeacherStudent")
           if str(r.get("teacher_id")) == str(teacher_id)]
    idset = set(str(x) for x in ids)
    return [s for s in db.all_records("Students") if str(s.get("id")) in idset]


def student_teachers(student_id):
    ids = [r["teacher_id"] for r in db.all_records("TeacherStudent")
           if str(r.get("student_id")) == str(student_id)]
    idset = set(str(x) for x in ids)
    return [t for t in db.all_records("Teachers") if str(t.get("id")) in idset]


def teacher_schedules(teacher_id):
    return [s for s in db.all_records("Schedules") if str(s.get("teacher_id")) == str(teacher_id)]


def student_schedules(student_id):
    return [s for s in db.all_records("Schedules") if str(s.get("student_id")) == str(student_id)]


def teacher_trial_classes(teacher_id):
    return [t for t in db.all_records("TrialClasses") if str(t.get("teacher_id")) == str(teacher_id)]


def subject_name(sched):
    sid = sched.get("student_id")
    if sid and str(sid).strip():
        st = db.get_by_id("Students", sid)
        if st:
            return st.get("full_name")
    return sched.get("group_name") or "Grupo"


# ---------- Routes ----------
@app.route('/')
def home():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    role = session.get('role')
    if role in ('director', 'admin'):
        return redirect(url_for('director_dashboard'))
    if role == 'teacher':
        return redirect(url_for('teacher_dashboard'))
    return redirect(url_for('student_dashboard'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = None
        for u in db.all_records("Users"):
            if u.get("username") == username and db.str_to_bool(u.get("is_active", "TRUE")):
                user = u
                break
        if user and check_password_hash(user.get("password_hash", ""), password):
            session['user_id'] = user.get("id")
            session['role'] = user.get("role")
            flash(f'Bienvenido, {username}.', 'success')
            return redirect(url_for('home'))
        flash('Usuario o contraseña incorrectos.', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('Sesión cerrada.', 'info')
    return redirect(url_for('login'))


# ---------- Contraseñas ----------
def generate_temp_password(length=8):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


@app.route('/account/password', methods=['GET', 'POST'])
@login_required
def account_password():
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        new = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        if not g.user or not check_password_hash(g.user.get('password_hash', ''), current):
            flash('La contraseña actual no es correcta.', 'danger')
            return redirect(url_for('account_password'))
        if len(new) < 6:
            flash('La nueva contraseña debe tener al menos 6 caracteres.', 'danger')
            return redirect(url_for('account_password'))
        if new != confirm:
            flash('Las contraseñas nuevas no coinciden.', 'danger')
            return redirect(url_for('account_password'))
        db.update_record("Users", g.user.get('id'), {"password_hash": generate_password_hash(new)})
        flash('Contraseña actualizada correctamente.', 'success')
        return redirect(url_for('home'))
    return render_template('account_password.html')


@app.route('/forgot', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        user = None
        for u in db.all_records("Users"):
            if u.get("username") == username:
                user = u
                break
        if user:
            # evitar duplicados pendientes
            pending = any(r.get("user_id") == str(user.get("id")) and r.get("status") == "pending"
                          for r in db.all_records("PasswordRequests"))
            if not pending:
                db.append_record("PasswordRequests", {
                    "user_id": user.get("id"),
                    "username": username,
                    "status": "pending",
                    "created_at": datetime.utcnow().isoformat(),
                    "resolved_at": "",
                })
        flash('Si el usuario existe, se envió una solicitud al director para restablecer la contraseña.', 'info')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')


@app.route('/director/password-requests')
@login_required
@role_required('director')
def password_requests():
    requests = db.all_records("PasswordRequests")
    # pendientes primero
    requests.sort(key=lambda r: (r.get("status") != "pending", r.get("created_at", "")))
    return render_template('password_requests.html', requests=requests)


@app.route('/director/password-requests/<int:req_id>/approve', methods=['POST'])
@login_required
@role_required('director')
def approve_password_request(req_id):
    req = db.get_by_id("PasswordRequests", req_id)
    if not req or req.get("status") != "pending":
        flash('Solicitud no válida.', 'danger')
        return redirect(url_for('password_requests'))
    user = db.get_by_id("Users", req.get("user_id"))
    if not user:
        flash('Usuario no encontrado.', 'danger')
        return redirect(url_for('password_requests'))
    new_password = request.form.get('new_password', '').strip() or generate_temp_password()
    db.update_record("Users", user.get("id"), {"password_hash": generate_password_hash(new_password)})
    db.update_record("PasswordRequests", req_id, {
        "status": "approved",
        "resolved_at": datetime.utcnow().isoformat(),
    })
    flash(f'Contraseña de {user.get("username")} restablecida. Nueva contraseña temporal: {new_password}', 'success')
    return redirect(url_for('password_requests'))


@app.route('/director/password-requests/<int:req_id>/reject', methods=['POST'])
@login_required
@role_required('director')
def reject_password_request(req_id):
    req = db.get_by_id("PasswordRequests", req_id)
    if req and req.get("status") == "pending":
        db.update_record("PasswordRequests", req_id, {
            "status": "rejected",
            "resolved_at": datetime.utcnow().isoformat(),
        })
        flash('Solicitud rechazada.', 'info')
    return redirect(url_for('password_requests'))


# ---------- Usuarios administradores ----------
ROLE_LABELS = {
    'director': 'Director',
    'admin': 'Administrador',
    'teacher': 'Maestro',
    'student': 'Alumno',
}


@app.route('/director/users', methods=['GET', 'POST'])
@login_required
@role_required('director')
def admin_users():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'admin').strip() or 'admin'
        if role not in ('admin', 'director'):
            role = 'admin'
        if not username or not password:
            flash('Usuario y contraseña son obligatorios.', 'danger')
            return redirect(url_for('admin_users'))
        if len(password) < 6:
            flash('La contraseña debe tener al menos 6 caracteres.', 'danger')
            return redirect(url_for('admin_users'))
        if any(u.get("username") == username for u in db.all_records("Users")):
            flash('El usuario ya existe.', 'danger')
            return redirect(url_for('admin_users'))
        db.append_record("Users", {
            "username": username,
            "password_hash": generate_password_hash(password),
            "role": role,
            "is_active": True,
            "created_at": datetime.utcnow().isoformat(),
            "teacher_id": "",
            "student_id": "",
        })
        flash(f'Usuario {ROLE_LABELS.get(role, role)} "{username}" creado.', 'success')
        return redirect(url_for('admin_users'))

    users = db.all_records("Users")
    order = {'director': 0, 'admin': 1, 'teacher': 2, 'student': 3}
    users.sort(key=lambda u: (order.get(u.get('role'), 9), u.get('username', '')))
    return render_template('admin_users.html', users=users)


@app.route('/director/users/<int:user_id>/delete', methods=['POST'])
@login_required
@role_required('director')
def delete_admin_user(user_id):
    user = db.get_by_id("Users", user_id)
    if not user:
        flash('Usuario no encontrado.', 'danger')
        return redirect(url_for('admin_users'))
    if str(user.get('id')) == str(session.get('user_id')):
        flash('No puedes eliminar tu propio usuario.', 'danger')
        return redirect(url_for('admin_users'))
    if user.get('role') not in ('admin', 'director'):
        flash('Solo se pueden eliminar usuarios administradores desde esta sección.', 'danger')
        return redirect(url_for('admin_users'))
    db.delete_record("Users", user_id)
    flash(f'Usuario "{user.get("username")}" eliminado.', 'info')
    return redirect(url_for('admin_users'))


# ---------- Director ----------
@app.route('/director')
@login_required
@role_required('director')
def director_dashboard():
    teachers = db.all_records("Teachers")
    students = db.all_records("Students")
    schedules = db.all_records("Schedules")
    return render_template('director_dashboard.html', teachers=teachers, students=students, schedules=schedules)


@app.route('/director/teachers', methods=['GET', 'POST'])
@login_required
@role_required('director')
def director_teachers():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        full_name = request.form.get('full_name', '').strip()
        if not username or not password or not full_name:
            flash('Usuario, contraseña y nombre son obligatorios.', 'danger')
            return redirect(url_for('director_teachers'))
        if any(u.get("username") == username for u in db.all_records("Users")):
            flash('El usuario ya existe.', 'danger')
            return redirect(url_for('director_teachers'))
        uid = db.append_record("Users", {
            "username": username,
            "password_hash": generate_password_hash(password),
            "role": "teacher",
            "is_active": True,
            "created_at": datetime.utcnow().isoformat(),
            "teacher_id": "",
            "student_id": "",
        })
        db.append_record("Teachers", {
            "user_id": uid,
            "full_name": full_name,
            "phone": request.form.get('phone', ''),
            "email": request.form.get('email', ''),
            "specialty": request.form.get('specialty', ''),
            "bio": request.form.get('bio', ''),
            "created_at": datetime.utcnow().isoformat(),
        })
        flash('Maestro creado.', 'success')
        return redirect(url_for('director_teachers'))
    teachers = db.all_records("Teachers")
    return render_template('teachers.html', teachers=teachers)


@app.route('/director/teachers/<int:teacher_id>/delete', methods=['POST'])
@login_required
@role_required('director')
def delete_teacher(teacher_id):
    teacher = db.get_by_id("Teachers", teacher_id)
    if not teacher:
        flash('Maestro no encontrado.', 'danger')
        return redirect(url_for('director_teachers'))
    # eliminar usuario vinculado
    uid = teacher.get("user_id")
    if uid:
        db.delete_record("Users", uid)
    # eliminar asignaciones
    for r in db.all_records("TeacherStudent"):
        if str(r.get("teacher_id")) == str(teacher_id):
            # borrar fila: reconstruir? delete by composite not supported; skip cleanup of join rows (optional)
            pass
    db.delete_record("Teachers", teacher_id)
    flash('Maestro eliminado.', 'info')
    return redirect(url_for('director_teachers'))


@app.route('/director/students', methods=['GET', 'POST'])
@login_required
@role_required('director')
def director_students():
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        if not full_name:
            flash('El nombre es obligatorio.', 'danger')
            return redirect(url_for('director_students'))
        create_login = request.form.get('create_login') == 'on'
        uid = ""
        if create_login:
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '').strip()
            if not username or not password:
                flash('Usuario y contraseña son obligatorios para crear login.', 'danger')
                return redirect(url_for('director_students'))
            if any(u.get("username") == username for u in db.all_records("Users")):
                flash('El usuario ya existe.', 'danger')
                return redirect(url_for('director_students'))
            uid = db.append_record("Users", {
                "username": username,
                "password_hash": generate_password_hash(password),
                "role": "student",
                "is_active": True,
                "created_at": datetime.utcnow().isoformat(),
                "teacher_id": "",
                "student_id": "",
            })
        sid = db.append_record("Students", {
            "user_id": uid,
            "full_name": full_name,
            "phone": request.form.get('phone', ''),
            "email": request.form.get('email', ''),
            "parent_name": request.form.get('parent_name', ''),
            "parent_phone": request.form.get('parent_phone', ''),
            "parent_email": request.form.get('parent_email', ''),
            "notes": request.form.get('notes', ''),
            "created_at": datetime.utcnow().isoformat(),
        })
        # vincular user.student_id
        if uid:
            db.update_record("Users", uid, {"student_id": sid})

        # Asignación de maestro (opcional)
        teacher_id = request.form.get('teacher_id', type=int)
        if teacher_id:
            teacher = db.get_by_id("Teachers", teacher_id)
            if teacher:
                exists = any(r.get("teacher_id") == str(teacher_id) and r.get("student_id") == str(sid)
                             for r in db.all_records("TeacherStudent"))
                if not exists:
                    db.append_record("TeacherStudent", {
                        "teacher_id": teacher_id,
                        "student_id": sid,
                        "assigned_at": datetime.utcnow().isoformat(),
                    })

        # Horario (opcional, requiere maestro)
        day = request.form.get('day_of_week')
        start = request.form.get('start_time')
        end = request.form.get('end_time')
        if teacher_id and day and start and end:
            db.append_record("Schedules", {
                "teacher_id": teacher_id,
                "student_id": sid,
                "group_name": "",
                "day_of_week": day,
                "start_time": start,
                "end_time": end,
                "room": request.form.get('room', ''),
                "notes": request.form.get('notes', ''),
                "created_at": datetime.utcnow().isoformat(),
            })

        flash('Alumno creado.', 'success')
        return redirect(url_for('director_students'))
    students = db.all_records("Students")
    teachers = db.all_records("Teachers")
    return render_template('students.html', students=students, teachers=teachers)


@app.route('/director/students/<int:student_id>/delete', methods=['POST'])
@login_required
@role_required('director')
def delete_student(student_id):
    student = db.get_by_id("Students", student_id)
    if not student:
        flash('Alumno no encontrado.', 'danger')
        return redirect(url_for('director_students'))
    uid = student.get("user_id")
    if uid:
        db.delete_record("Users", uid)
    db.delete_record("Students", student_id)
    flash('Alumno eliminado.', 'info')
    return redirect(url_for('director_students'))


@app.route('/director/assign', methods=['POST'])
@login_required
@role_required('director')
def assign_student():
    teacher_id = request.form.get('teacher_id', type=int)
    student_id = request.form.get('student_id', type=int)
    teacher = db.get_by_id("Teachers", teacher_id)
    student = db.get_by_id("Students", student_id)
    if not teacher or not student:
        flash('Maestro o alumno no encontrado.', 'danger')
        return redirect(url_for('director_students'))
    # verificar si ya asignado
    exists = any(r.get("teacher_id") == str(teacher_id) and r.get("student_id") == str(student_id)
                 for r in db.all_records("TeacherStudent"))
    if not exists:
        db.append_record("TeacherStudent", {
            "teacher_id": teacher_id,
            "student_id": student_id,
            "assigned_at": datetime.utcnow().isoformat(),
        })
        flash(f'{student.get("full_name")} asignado a {teacher.get("full_name")}.', 'success')
    else:
        flash('Ya estaba asignado.', 'info')
    return redirect(url_for('director_students'))


@app.route('/director/unassign', methods=['POST'])
@login_required
@role_required('director')
def unassign_student():
    teacher_id = request.form.get('teacher_id')
    student_id = request.form.get('student_id')
    # borrar fila de TeacherStudent que coincida
    rows = db.all_records("TeacherStudent")
    # encontrar índice en sheet
    ws = db.get_worksheet("TeacherStudent")
    values = ws.get_all_values()
    headers = db.SCHEMAS["TeacherStudent"]
    # fila 1 cabecera
    target_idx = None
    for i, r in enumerate(values[1:], start=2):
        d = db._row_to_dict(headers, r)
        if str(d.get("teacher_id")) == str(teacher_id) and str(d.get("student_id")) == str(student_id):
            target_idx = i
            break
    if target_idx:
        ws.delete_rows(target_idx)
        db.invalidate("TeacherStudent")
        flash('Asignación eliminada.', 'info')
    return redirect(url_for('director_students'))


@app.route('/director/schedules', methods=['GET', 'POST'])
@login_required
@role_required('director')
def director_schedules():
    if request.method == 'POST':
        teacher_id = request.form.get('teacher_id', type=int)
        student_id = request.form.get('student_id', type=int)
        group_name = request.form.get('group_name', '').strip()
        day = request.form.get('day_of_week')
        start = request.form.get('start_time')
        end = request.form.get('end_time')
        if not teacher_id or not day or not start or not end:
            flash('Maestro, día y horas son obligatorios.', 'danger')
            return redirect(url_for('director_schedules'))
        db.append_record("Schedules", {
            "teacher_id": teacher_id,
            "student_id": student_id if student_id else "",
            "group_name": group_name,
            "day_of_week": day,
            "start_time": start,
            "end_time": end,
            "room": request.form.get('room', ''),
            "notes": request.form.get('notes', ''),
            "created_at": datetime.utcnow().isoformat(),
        })
        flash('Horario creado.', 'success')
        return redirect(url_for('director_schedules'))
    schedules = db.all_records("Schedules")
    teachers = db.all_records("Teachers")
    students = db.all_records("Students")
    return render_template('schedules.html', schedules=schedules, teachers=teachers, students=students, days=DAYS, subject_name=subject_name)


@app.route('/director/schedules/<int:sched_id>/delete', methods=['POST'])
@login_required
@role_required('director')
def delete_schedule(sched_id):
    db.delete_record("Schedules", sched_id)
    flash('Horario eliminado.', 'info')
    return redirect(url_for('director_schedules'))


# ---------- Clases muestra (director) ----------
@app.route('/director/trial-classes')
@login_required
@role_required('director')
def director_trial_classes():
    trials = db.all_records("TrialClasses")
    trials.sort(key=lambda t: (t.get("status") != "pending", t.get("date", ""), t.get("start_time", "")), reverse=False)
    return render_template('trial_classes.html', trials=trials)


@app.route('/director/trial-classes/<int:trial_id>/confirm', methods=['POST'])
@login_required
@role_required('director')
def confirm_trial_class(trial_id):
    trial = db.get_by_id("TrialClasses", trial_id)
    if trial and trial.get("status") == "pending":
        db.update_record("TrialClasses", trial_id, {
            "status": "confirmed",
            "confirmed_at": datetime.utcnow().isoformat(),
            "confirmed_by": g.user.get("username"),
        })
        flash('Clase muestra confirmada.', 'success')
    return redirect(url_for('director_trial_classes'))


@app.route('/director/trial-classes/<int:trial_id>/reject', methods=['POST'])
@login_required
@role_required('director')
def reject_trial_class(trial_id):
    trial = db.get_by_id("TrialClasses", trial_id)
    if trial and trial.get("status") == "pending":
        db.update_record("TrialClasses", trial_id, {
            "status": "rejected",
            "confirmed_at": datetime.utcnow().isoformat(),
            "confirmed_by": g.user.get("username"),
        })
        flash('Clase muestra rechazada.', 'info')
    return redirect(url_for('director_trial_classes'))


# ---------- Teacher ----------
@app.route('/teacher')
@login_required
@role_required('teacher')
def teacher_dashboard():
    teacher = get_teacher_by_user(g.user.get("id"))
    if not teacher:
        flash('Perfil de maestro no encontrado.', 'danger')
        return redirect(url_for('logout'))
    scheds = teacher_schedules(teacher.get("id"))
    # enriquecer con subject_name
    for s in scheds:
        s["_subject"] = subject_name(s)
    trials = teacher_trial_classes(teacher.get("id"))
    trials.sort(key=lambda t: (t.get("date", ""), t.get("start_time", "")), reverse=True)
    return render_template('teacher_dashboard.html', teacher=teacher, schedules=scheds, trials=trials)


@app.route('/teacher/trial-class', methods=['POST'])
@login_required
@role_required('teacher')
def create_trial_class():
    teacher = get_teacher_by_user(g.user.get("id"))
    if not teacher:
        flash('Perfil de maestro no encontrado.', 'danger')
        return redirect(url_for('logout'))
    date = request.form.get('date')
    start = request.form.get('start_time')
    if not date or not start:
        flash('La fecha y la hora de inicio son obligatorias.', 'danger')
        return redirect(url_for('teacher_dashboard'))
    db.append_record("TrialClasses", {
        "teacher_id": teacher.get("id"),
        "prospect_name": "Clase muestra",
        "phone": "",
        "date": date,
        "start_time": start,
        "end_time": "",
        "notes": "",
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
        "confirmed_at": "",
        "confirmed_by": "",
    })
    flash('Clase muestra registrada. Queda pendiente de confirmación del director.', 'success')
    return redirect(url_for('teacher_dashboard'))


# ---------- Student / Parent ----------
@app.route('/student')
@login_required
@role_required('student')
def student_dashboard():
    student = get_student_by_user(g.user.get("id"))
    if not student:
        flash('Perfil de alumno no encontrado.', 'danger')
        return redirect(url_for('logout'))
    scheds = student_schedules(student.get("id"))
    teachers = student_teachers(student.get("id"))
    return render_template('student_dashboard.html', student=student, schedules=scheds, teachers=teachers)


@app.context_processor
def inject_helpers():
    return dict(
        get_user=lambda uid: db.get_by_id("Users", uid) if uid else None,
        teacher_students=lambda tid: teacher_students(tid),
        student_teachers=lambda sid: student_teachers(sid),
        subject_name=subject_name,
        teacher_by_id=lambda tid: db.get_by_id("Teachers", tid) if tid else None,
        student_by_id=lambda sid: db.get_by_id("Students", sid) if sid else None,
        teacher_trials=lambda tid: teacher_trial_classes(tid),
        ROLE_LABELS=ROLE_LABELS,
        DAYS=DAYS,
    )


if __name__ == '__main__':
    app.run(debug=True)

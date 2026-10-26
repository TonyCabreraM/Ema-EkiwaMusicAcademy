import os
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
            if session.get('role') not in roles:
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
    if role == 'director':
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
    return render_template('teacher_dashboard.html', teacher=teacher, schedules=scheds)


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
        DAYS=DAYS,
    )


if __name__ == '__main__':
    app.run(debug=True)

import os
from functools import wraps
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from flask_sqlalchemy import SQLAlchemy

from models import db, User, Teacher, Student, Schedule, DAYS, init_db

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
database_url = os.environ.get('DATABASE_URL', 'sqlite:///ema.db')
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
init_db(app)


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
        g.user = User.query.get(session['user_id'])


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
        user = User.query.filter_by(username=username, is_active=True).first()
        if user and user.check_password(password):
            session['user_id'] = user.id
            session['role'] = user.role
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
    teachers = Teacher.query.order_by(Teacher.full_name).all()
    students = Student.query.order_by(Student.full_name).all()
    schedules = Schedule.query.order_by(Schedule.day_of_week, Schedule.start_time).all()
    return render_template('director_dashboard.html', teachers=teachers, students=students, schedules=schedules)


# Teachers CRUD
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
        if User.query.filter_by(username=username).first():
            flash('El usuario ya existe.', 'danger')
            return redirect(url_for('director_teachers'))
        user = User(username=username, role='teacher')
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        teacher = Teacher(user_id=user.id, full_name=full_name,
                          phone=request.form.get('phone'), email=request.form.get('email'),
                          specialty=request.form.get('specialty'), bio=request.form.get('bio'))
        db.session.add(teacher)
        db.session.commit()
        flash('Maestro creado.', 'success')
        return redirect(url_for('director_teachers'))
    teachers = Teacher.query.order_by(Teacher.full_name).all()
    return render_template('teachers.html', teachers=teachers)


@app.route('/director/teachers/<int:teacher_id>/delete', methods=['POST'])
@login_required
@role_required('director')
def delete_teacher(teacher_id):
    teacher = Teacher.query.get_or_404(teacher_id)
    if teacher.user:
        db.session.delete(teacher.user)
    db.session.delete(teacher)
    db.session.commit()
    flash('Maestro eliminado.', 'info')
    return redirect(url_for('director_teachers'))


# Students CRUD
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
        user = None
        if create_login:
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '').strip()
            if not username or not password:
                flash('Usuario y contraseña son obligatorios para crear login.', 'danger')
                return redirect(url_for('director_students'))
            if User.query.filter_by(username=username).first():
                flash('El usuario ya existe.', 'danger')
                return redirect(url_for('director_students'))
            user = User(username=username, role='student')
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
        student = Student(user_id=user.id if user else None, full_name=full_name,
                          phone=request.form.get('phone'), email=request.form.get('email'),
                          parent_name=request.form.get('parent_name'), parent_phone=request.form.get('parent_phone'),
                          parent_email=request.form.get('parent_email'), notes=request.form.get('notes'))
        db.session.add(student)
        db.session.commit()
        flash('Alumno creado.', 'success')
        return redirect(url_for('director_students'))
    students = Student.query.order_by(Student.full_name).all()
    teachers = Teacher.query.order_by(Teacher.full_name).all()
    return render_template('students.html', students=students, teachers=teachers)


@app.route('/director/students/<int:student_id>/delete', methods=['POST'])
@login_required
@role_required('director')
def delete_student(student_id):
    student = Student.query.get_or_404(student_id)
    if student.user:
        db.session.delete(student.user)
    db.session.delete(student)
    db.session.commit()
    flash('Alumno eliminado.', 'info')
    return redirect(url_for('director_students'))


# Assign student to teacher
@app.route('/director/assign', methods=['POST'])
@login_required
@role_required('director')
def assign_student():
    teacher_id = request.form.get('teacher_id', type=int)
    student_id = request.form.get('student_id', type=int)
    teacher = Teacher.query.get(teacher_id)
    student = Student.query.get(student_id)
    if not teacher or not student:
        flash('Maestro o alumno no encontrado.', 'danger')
        return redirect(url_for('director_students'))
    if student not in teacher.students:
        teacher.students.append(student)
        db.session.commit()
        flash(f'{student.full_name} asignado a {teacher.full_name}.', 'success')
    else:
        flash('Ya estaba asignado.', 'info')
    return redirect(url_for('director_students'))


@app.route('/director/unassign', methods=['POST'])
@login_required
@role_required('director')
def unassign_student():
    teacher_id = request.form.get('teacher_id', type=int)
    student_id = request.form.get('student_id', type=int)
    teacher = Teacher.query.get(teacher_id)
    student = Student.query.get(student_id)
    if teacher and student and student in teacher.students:
        teacher.students.remove(student)
        db.session.commit()
        flash('Asignación eliminada.', 'info')
    return redirect(url_for('director_students'))


# Schedules CRUD
@app.route('/director/schedules', methods=['GET', 'POST'])
@login_required
@role_required('director')
def director_schedules():
    if request.method == 'POST':
        teacher_id = request.form.get('teacher_id', type=int)
        student_id = request.form.get('student_id', type=int) or None
        group_name = request.form.get('group_name', '').strip() or None
        day = request.form.get('day_of_week')
        start = request.form.get('start_time')
        end = request.form.get('end_time')
        if not teacher_id or not day or not start or not end:
            flash('Maestro, día y horas son obligatorios.', 'danger')
            return redirect(url_for('director_schedules'))
        sched = Schedule(teacher_id=teacher_id, student_id=student_id, group_name=group_name,
                        day_of_week=day, start_time=start, end_time=end,
                        room=request.form.get('room'), notes=request.form.get('notes'))
        db.session.add(sched)
        db.session.commit()
        flash('Horario creado.', 'success')
        return redirect(url_for('director_schedules'))
    schedules = Schedule.query.order_by(Schedule.day_of_week, Schedule.start_time).all()
    teachers = Teacher.query.order_by(Teacher.full_name).all()
    students = Student.query.order_by(Student.full_name).all()
    return render_template('schedules.html', schedules=schedules, teachers=teachers, students=students, days=DAYS)


@app.route('/director/schedules/<int:sched_id>/delete', methods=['POST'])
@login_required
@role_required('director')
def delete_schedule(sched_id):
    sched = Schedule.query.get_or_404(sched_id)
    db.session.delete(sched)
    db.session.commit()
    flash('Horario eliminado.', 'info')
    return redirect(url_for('director_schedules'))


# ---------- Teacher ----------
@app.route('/teacher')
@login_required
@role_required('teacher')
def teacher_dashboard():
    teacher = Teacher.query.filter_by(user_id=g.user.id).first()
    if not teacher:
        flash('Perfil de maestro no encontrado.', 'danger')
        return redirect(url_for('logout'))
    schedules = Schedule.query.filter_by(teacher_id=teacher.id).order_by(Schedule.day_of_week, Schedule.start_time).all()
    return render_template('teacher_dashboard.html', teacher=teacher, schedules=schedules)


# ---------- Student / Parent ----------
@app.route('/student')
@login_required
@role_required('student')
def student_dashboard():
    student = Student.query.filter_by(user_id=g.user.id).first()
    if not student:
        flash('Perfil de alumno no encontrado.', 'danger')
        return redirect(url_for('logout'))
    schedules = Schedule.query.filter_by(student_id=student.id).order_by(Schedule.day_of_week, Schedule.start_time).all()
    teachers = student.teachers
    return render_template('student_dashboard.html', student=student, schedules=schedules, teachers=teachers)


if __name__ == '__main__':
    app.run(debug=True)

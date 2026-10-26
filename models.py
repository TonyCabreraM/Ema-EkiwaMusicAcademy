from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, time

db = SQLAlchemy()


class User(db.Model):
    """Usuario del sistema (director, maestro, alumno/padre)."""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='student')  # director / teacher / student
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    teacher = db.relationship('Teacher', backref='user', uselist=False, cascade='all, delete-orphan')
    student = db.relationship('Student', backref='user', uselist=False, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username} ({self.role})>"


# Tabla intermedia maestro <-> alumno
teacher_student = db.Table(
    'teacher_student',
    db.Column('teacher_id', db.Integer, db.ForeignKey('teachers.id'), primary_key=True),
    db.Column('student_id', db.Integer, db.ForeignKey('students.id'), primary_key=True),
    db.Column('assigned_at', db.DateTime, default=datetime.utcnow),
)


class Teacher(db.Model):
    """Perfil de maestro."""
    __tablename__ = 'teachers'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    full_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    specialty = db.Column(db.String(120), nullable=True)   # instrumento / materia
    bio = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    students = db.relationship('Student', secondary=teacher_student, backref='teachers', lazy='subquery')
    schedules = db.relationship('Schedule', backref='teacher', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f"<Teacher {self.full_name}>"


class Student(db.Model):
    """Perfil de alumno."""
    __tablename__ = 'students'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # login alumno/padre (opcional)
    full_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    birthdate = db.Column(db.Date, nullable=True)
    parent_name = db.Column(db.String(120), nullable=True)
    parent_phone = db.Column(db.String(30), nullable=True)
    parent_email = db.Column(db.String(120), nullable=True)
    notes = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    schedules = db.relationship('Schedule', backref='student', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f"<Student {self.full_name}>"


DAYS = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']


class Schedule(db.Model):
    """Horario de clase: maestro + alumno (o grupo) + día + hora."""
    __tablename__ = 'schedules'

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=True)
    group_name = db.Column(db.String(120), nullable=True)  # si es clase grupal
    day_of_week = db.Column(db.String(15), nullable=False)   # nombre del día
    start_time = db.Column(db.String(5), nullable=False)     # "HH:MM"
    end_time = db.Column(db.String(5), nullable=False)       # "HH:MM"
    room = db.Column(db.String(80), nullable=True)
    notes = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def subject_name(self):
        if self.student:
            return self.student.full_name
        return self.group_name or 'Grupo'

    def __repr__(self):
        return f"<Schedule {self.day_of_week} {self.start_time}-{self.end_time}>"


def init_db(app):
    with app.app_context():
        db.create_all()
        # Crear director por defecto si no existe
        if User.query.count() == 0:
            director = User(username='director', role='director')
            director.set_password('director123')
            db.session.add(director)
            db.session.commit()
            print("Director por defecto creado: director / director123")
        else:
            print("Base de datos ya configurada")

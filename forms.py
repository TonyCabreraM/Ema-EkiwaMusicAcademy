from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, FloatField, SubmitField
from wtforms.validators import DataRequired, NumberRange, Optional, Length, Email


class LoginForm(FlaskForm):
    """Formulario de inicio de sesión"""
    username = StringField('Usuario', validators=[
        DataRequired(message='El usuario es requerido'),
        Length(min=3, max=80, message='Entre 3 y 80 caracteres')
    ])
    password = PasswordField('Contraseña', validators=[
        DataRequired(message='La contraseña es requerida')
    ])
    submit = SubmitField('Ingresar')


class GenerateCardForm(FlaskForm):
    """Formulario para generar nueva tarjeta"""
    client_name = StringField('Nombre del Cliente', validators=[
        DataRequired(message='El nombre es obligatorio'),
        Length(min=2, max=120, message='Entre 2 y 120 caracteres')
    ])
    phone = StringField('Número de Celular (WhatsApp)', validators=[
        DataRequired(message='El número de celular es obligatorio'),
        Length(min=7, max=30, message='Formato inválido'),
        Optional()
    ])
    amount = FloatField('Monto Inicial ($)', validators=[
        DataRequired(message='El monto es obligatorio'),
        NumberRange(min=0.01, message='El monto debe ser mayor a $0.01')
    ], places=2)
    submit = SubmitField('Generar Tarjeta')


class BalanceActionForm(FlaskForm):
    """Formulario para recargar/canjear saldo"""
    amount = FloatField('Monto ($)', validators=[
        DataRequired(message='El monto es obligatorio'),
        NumberRange(min=0.01, message='El monto debe ser mayor a $0.01')
    ], places=2)
    note = StringField('Nota (Opcional)', validators=[
        Optional(),
        Length(max=500, message='Máximo 500 caracteres')
    ])
    submit = SubmitField('Confirmar')


class ExportForm(FlaskForm):
    """Formulario para exportar datos"""
    format = StringField('Formato', default='csv', validators=[
        DataRequired()
    ])
    include_inactive = BooleanField('Incluir tarjetas inactivas', default=False)
    submit = SubmitField('Exportar')
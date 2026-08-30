from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import secrets
import os

db = SQLAlchemy()


class Admin(db.Model):
    """Modelo de administrador"""
    __tablename__ = 'admins'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    # True = superusuario (puede eliminar tarjetas y gestionar usuarios).
    # False = usuario estándar (puede crear tarjetas, recargar y canjear,
    # pero NO puede eliminar tarjetas ni gestionar usuarios).
    is_superuser = db.Column(db.Boolean, default=False, nullable=False)
    # True = usuario de marketing (permisos de estándar + acceso a notificaciones).
    # False para superusuario y usuario estándar.
    is_marketing = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)
    
    def set_password(self, password: str) -> None:
        """Establece contraseña hasheada"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password: str) -> bool:
        """Verifica contraseña ingresada"""
        return check_password_hash(self.password_hash, password)
    
    def update_last_login(self) -> None:
        """Actualiza timestamp de último login"""
        self.last_login = datetime.utcnow()
        db.session.commit()
    
    def to_dict(self) -> dict:
        """Serializa a dict para logging"""
        return {
            'id': self.id,
            'username': self.username,
            'is_superuser': bool(self.is_superuser),
            'is_marketing': bool(self.is_marketing),
            'created_at': self.created_at.isoformat(),
            'last_login': self.last_login.isoformat() if self.last_login else None
        }


class Card(db.Model):
    """Modelo de tarjeta de regalo"""
    __tablename__ = 'cards'
    
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False, index=True)
    client_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), nullable=True)
    balance = db.Column(db.Float, nullable=False, default=0)
    custom_message = db.Column(db.String(500), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Campos de Membresía
    membership_type_id = db.Column(db.Integer, db.ForeignKey('membership_types.id'), nullable=True)
    membership_expires_at = db.Column(db.DateTime, nullable=True)
    
    # Contadores de beneficios mensuales
    drinks_redeemed_this_month = db.Column(db.Integer, default=0)
    grain_bags_redeemed_this_month = db.Column(db.Integer, default=0)
    last_benefit_reset = db.Column(db.DateTime, nullable=True) # Para saber cuándo reiniciar los contadores
    
    # Relaciones
    transactions = db.relationship('Transaction', backref='card', lazy=True, cascade='all, delete-orphan')
    membership_type = db.relationship('MembershipType', backref='cards', lazy=True)
    
    def add_transaction(self, amount: float, type_: str, note: str = '') -> 'Transaction':
        """Agrega una transacción a la tarjeta"""
        tx = Transaction(
            card_id=self.id,
            amount=amount,
            type=type_,
            note=note,
            created_at=datetime.utcnow()
        )
        db.session.add(tx)
        return tx
    
    def to_dict(self, include_transactions: bool = False) -> dict:
        """Serializa a dict con opciones"""
        data = {
            'id': self.id,
            'code': self.code,
            'client_name': self.client_name,
            'phone': self.phone,
            'balance': self.balance,
            'custom_message': self.custom_message,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }
        if include_transactions:
            data['transactions'] = [tx.to_dict() for tx in self.transactions]
        return data
    
    def __str__(self) -> str:
        return f"Card({self.code}, {self.client_name})"


class Transaction(db.Model):
    """Modelo de transacción"""
    __tablename__ = 'transactions'
    
    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.Integer, db.ForeignKey('cards.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(20), nullable=False)  # 'carga' o 'canje'
    note = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self) -> dict:
        """Serializa a dict"""
        return {
            'id': self.id,
            'card_id': self.card_id,
            'amount': self.amount,
            'type': self.type,
            'note': self.note,
            'created_at': self.created_at.isoformat()
        }
    
    def __str__(self) -> str:
        return f"Transaction({self.id}, {self.type}, ${self.amount:.2f})"


class Notification(db.Model):
    """Modelo de notificación generada desde el panel de marketing"""
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(140), nullable=False)
    message = db.Column(db.String(1000), nullable=False)
    image_url = db.Column(db.String(500), nullable=True)  # URL de imagen para OneSignal (big_picture)
    link_url = db.Column(db.String(500), nullable=True)  # URL a la que abre la notificación al hacer clic
    created_by_id = db.Column(db.Integer, db.ForeignKey('admins.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship('Admin', backref='notifications', lazy=True)

    def to_dict(self) -> dict:
        """Serializa a dict"""
        return {
            'id': self.id,
            'title': self.title,
            'message': self.message,
            'image_url': self.image_url,
            'link_url': self.link_url,
            'created_by': self.created_by.username if self.created_by else None,
            'created_at': self.created_at.isoformat(),
        }

    def __str__(self) -> str:
        return f"Notification({self.id}, {self.title})"


class MembershipType(db.Model):
    """Modelo para definir los tipos de membresía disponibles"""
    __tablename__ = 'membership_types'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500))
    price = db.Column(db.Float, nullable=False, default=0)
    cashback_percent = db.Column(db.Float, nullable=False, default=0)
    discount_percent = db.Column(db.Float, nullable=False, default=0) # Descuento en canjes
    drinks_count = db.Column(db.Integer, nullable=False, default=0) # Bebidas incluidas mensuales
    grain_bags = db.Column(db.Integer, nullable=False, default=0) # Bolsas de grano de regalo
    badge_icon = db.Column(db.String(200)) # Ruta de la imagen del icono de la membresía
    badge_color = db.Column(db.String(20), nullable=False, default='#FFD700') # Color del borde y textos
    background_image = db.Column(db.String(200)) # Ruta o nombre de la imagen de fondo
    is_active = db.Column(db.Boolean, default=True)
    is_default = db.Column(db.Boolean, default=False) # Si es la membresía por defecto (sin fecha de expiración)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'price': self.price,
            'cashback_percent': self.cashback_percent,
            'background_image': self.background_image
        }

class PushSubscription(db.Model):
    """Modelo de suscripción para notificaciones push"""
    __tablename__ = 'push_subscriptions'

    id = db.Column(db.Integer, primary_key=True)
    endpoint = db.Column(db.String(500), unique=True, nullable=False)
    p256dh = db.Column(db.String(200), nullable=False)
    auth = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            'endpoint': self.endpoint,
            'keys': {
                'p256dh': self.p256dh,
                'auth': self.auth
            }
        }


class Product(db.Model):
    """Modelo de producto para el inventario del negocio"""
    __tablename__ = 'products'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(140), nullable=False)
    description = db.Column(db.String(500), nullable=True)
    cost = db.Column(db.Float, nullable=False, default=0)          # Coste (lo que cuesta comprarlo)
    price = db.Column(db.Float, nullable=False, default=0)         # Precio de venta
    quantity = db.Column(db.Integer, nullable=False, default=0)    # Cantidad en stock
    reorder_point = db.Column(db.Integer, nullable=False, default=0)  # Mínimo: cuándo comprar más
    image_url = db.Column(db.String(500), nullable=True)           # Foto del producto
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> dict:
        """Serializa a dict"""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'cost': self.cost,
            'price': self.price,
            'quantity': self.quantity,
            'reorder_point': self.reorder_point,
            'image_url': self.image_url,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def __str__(self) -> str:
        return f"Product({self.id}, {self.name})"


class ProductMovement(db.Model):
    """Movimiento de stock de un producto (venta, reposición o ajuste).
    Permite calcular estadísticas de ventas, flujo de stock y reordenes."""
    __tablename__ = 'product_movements'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    change = db.Column(db.Integer, nullable=False)  # + reposición, - venta
    movement_type = db.Column(db.String(20), nullable=False, default='sale')
    # 'sale' (venta), 'restock' (reposición), 'adjustment' (ajuste), 'reorder' (alcanzó punto de reorden)
    note = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', backref='movements', lazy=True)

    def to_dict(self) -> dict:
        """Serializa a dict"""
        return {
            'id': self.id,
            'product_id': self.product_id,
            'change': self.change,
            'movement_type': self.movement_type,
            'note': self.note,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    def __str__(self) -> str:
        return f"ProductMovement({self.id}, {self.product_id}, {self.change})"


def init_db(app) -> None:
    """Inicializa base de datos y crea admin por defecto"""
    with app.app_context():
        db.create_all()

        # Migración: bases ya existentes (ej. PostgreSQL de Render) no reciben
        # columnas nuevas con create_all(). Agregar is_superuser si falta.
        from sqlalchemy import text, inspect
        inspector = inspect(db.engine)
        admin_cols = [c['name'] for c in inspector.get_columns('admins')]
        if 'is_superuser' not in admin_cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE admins ADD COLUMN is_superuser BOOLEAN DEFAULT FALSE"))
            print("🔧 Migración aplicada: columna 'is_superuser' agregada a la tabla admins")
        if 'is_marketing' not in admin_cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE admins ADD COLUMN is_marketing BOOLEAN DEFAULT FALSE"))
            print("🔧 Migración aplicada: columna 'is_marketing' agregada a la tabla admins")

        # Migración: agregar image_url a notifications si falta (bases existentes)
        notif_cols = [c['name'] for c in inspector.get_columns('notifications')]
        if 'image_url' not in notif_cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE notifications ADD COLUMN image_url VARCHAR(500)"))
            print("🔧 Migración aplicada: columna 'image_url' agregada a la tabla notifications")

        # Migración: agregar link_url a notifications si falta (bases existentes)
        if 'link_url' not in notif_cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE notifications ADD COLUMN link_url VARCHAR(500)"))
            print("🔧 Migración aplicada: columna 'link_url' agregada a la tabla notifications")

        # Crear admin por defecto si no existe (siempre superusuario)
        if Admin.query.count() == 0:
            admin = Admin(username='admin', is_superuser=True)
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print("✅ Base de datos inicializada con admin/admin123 (superusuario)")
        else:
            # Garantizar que el usuario 'admin' siempre sea superusuario
            admin = Admin.query.filter_by(username='admin').first()
            if admin and not admin.is_superuser:
                admin.is_superuser = True
                db.session.commit()
                print("🔧 El usuario 'admin' fue elevado a superusuario")
            print("✅ Base de datos ya configurada")


def generate_unique_code() -> str:
    """Genera código único alfanumérico seguro"""
    while True:
        code = secrets.token_urlsafe(12)
        if not Card.query.filter_by(code=code).first():
            return code
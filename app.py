import os, io, json, csv, secrets, socket, threading
from datetime import datetime, timedelta
from flask import Flask, flash, redirect, render_template, request, send_file, session, url_for, Response, jsonify, has_request_context
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from models import db, Admin, Card, Transaction, Notification, PushSubscription, MembershipType, Product, ProductMovement, AppSetting, PurchaseRequest, generate_unique_code, init_db
from utils import get_logger, generate_qr_image, generate_deep_link, sanitize_input, generate_whatsapp_image
import drive_backup
import requests as http_requests  # HTTP para PayPal API
from dotenv import load_dotenv

# -- OneSignal config --
import requests
base_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(base_dir, '.env')
load_dotenv(env_path)
ONESIGNAL_APP_ID = os.environ.get("ONESIGNAL_APP_ID", "")
ONESIGNAL_REST_API_KEY = os.environ.get("ONESIGNAL_REST_API_KEY", "")
ONESIGNAL_SAFARI_WEB_ID = os.environ.get("ONESIGNAL_SAFARI_WEB_ID", "")
HAS_ONESIGNAL = bool(ONESIGNAL_APP_ID and ONESIGNAL_REST_API_KEY)

# -- PayPal (pagos en línea) --
PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET", "")
PAYPAL_MODE = os.environ.get("PAYPAL_MODE", "live")  # 'live' o 'sandbox'
PAYPAL_BASE = "https://api-m.paypal.com" if PAYPAL_MODE == "live" else "https://api-m.sandbox.paypal.com"
PAYPAL_CURRENCY = os.environ.get("PAYPAL_CURRENCY", "MXN")
HAS_PAYPAL = bool(PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET)

def create_app():
    app = Flask(__name__)
    # Render sirve la app detrs de un proxy/load balancer: ProxyFix hace que
    # request.url_root e is_secure reflejen la URL pblica real
    # (https://jubiloqrv2.onrender.com) en vez de la interna del contenedor.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
    db_url = os.environ.get("DATABASE_URL", "")
    # Render entrega URLs postgres://... o postgresql://...; con Psycopg 3
    # SQLAlchemy necesita el esquema postgresql+psycopg://
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if db_url:
        app.config["SQLALCHEMY_DATABASE_URI"] = db_url
        # Log diagnstico: confirma en los logs de Render que usa Postgres
        host = db_url.split("@")[-1].split("/")[0] if "@" in db_url else "local"
        print(f" DATABASE: PostgreSQL persistente en {host} (los datos NO se pierden al reiniciar)")
    else:
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(base_dir, "jubilo.db")
        print("  DATABASE: SQLite en disco EFMERO (los datos SE PIERDEN con cada deploy/reinicio).")
        print("  FALTA la variable de entorno DATABASE_URL. Crala en Render apuntando a una base de datos PostgreSQL.")
    app.template_folder = os.path.join(base_dir, "templates")
    app.static_folder = os.path.join(base_dir, "static")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    # PostgreSQL (Render) corta conexiones inactivas; reciclar el pool
    # evita errores tipo "server closed the connection unexpectedly"
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_recycle": 180, "pool_pre_ping": True}
    public_url = os.environ.get("PUBLIC_BASE_URL")
    public_url_is_fallback = False
    if not public_url:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            local_ip = s.getsockname()[0]
        except Exception:
            local_ip = "127.0.0.1"
        finally:
            s.close()
        public_url = f"http://{local_ip}:5000"
        public_url_is_fallback = True
        print("  PUBLIC_BASE_URL no definida: los QR se derivarn de la URL real de cada request (ProxyFix) o usarn la IP local.")
    app.config["PUBLIC_BASE_URL"] = public_url.rstrip("/")
    app.config["PUBLIC_BASE_URL_IS_FALLBACK"] = public_url_is_fallback

    def get_public_base_url():
        """URL pblica real para generar QRs.
        1. Variable de entorno PUBLIC_BASE_URL (si est definida, manda).
          2. En produccin detrs de proxy (Render): se deriva del request
              actual gracias a ProxyFix  https://jubiloqrv2.onrender.com.
        3. Desarrollo local: IP de la red local."""
        if not app.config.get("PUBLIC_BASE_URL_IS_FALLBACK"):
            return app.config["PUBLIC_BASE_URL"]
        if has_request_context():
            if request.is_secure or request.headers.get("X-Forwarded-Proto") == "https":
                return request.url_root.rstrip("/")
        return app.config["PUBLIC_BASE_URL"]
    db.init_app(app)
    init_db(app)
    logger = get_logger()

    def create_auto_backup():
        """Crea un respaldo automático diario con todos los datos.
        Se ejecuta al iniciar la app (una vez por día) y conserva los últimos 30."""
        try:
            backup_dir = os.path.join(base_dir, 'backups')
            os.makedirs(backup_dir, exist_ok=True)
            today = datetime.utcnow().strftime('%Y%m%d')
            # Si ya existe un respaldo de hoy, no repetir
            existing = [f for f in os.listdir(backup_dir) if f.startswith(f'auto_{today}')]
            if existing:
                return
            with app.app_context():
                data = {
                    "version": "1.3",
                    "exported_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                    "cards": [{"code": c.code, "client_name": c.client_name, "phone": c.phone,
                               "balance": float(c.balance), "is_active": c.is_active,
                               "custom_message": c.custom_message,
                               "membership_type_id": c.membership_type_id,
                               "membership_expires_at": c.membership_expires_at.strftime("%Y-%m-%d %H:%M:%S") if c.membership_expires_at else None,
                               "drinks_redeemed_this_month": c.drinks_redeemed_this_month,
                               "grain_bags_redeemed_this_month": c.grain_bags_redeemed_this_month,
                               "last_benefit_reset": c.last_benefit_reset.strftime("%Y-%m-%d %H:%M:%S") if c.last_benefit_reset else None,
                               "created_at": c.created_at.strftime("%Y-%m-%d %H:%M:%S") if c.created_at else None}
                              for c in Card.query.all()],
                    "transactions": [{"card_code": (Card.query.filter_by(id=t.card_id).first().code if Card.query.filter_by(id=t.card_id).first() else None),
                                      "type": t.type, "amount": float(t.amount), "note": t.note,
                                      "created_at": t.created_at.strftime("%Y-%m-%d %H:%M:%S") if t.created_at else None}
                                     for t in Transaction.query.all()],
                    "notifications": [{"title": n.title, "message": n.message, "image_url": n.image_url,
                                       "link_url": n.link_url,
                                       "created_at": n.created_at.strftime("%Y-%m-%d %H:%M:%S") if n.created_at else None}
                                      for n in Notification.query.all()],
                    "admins": [{"username": a.username, "password_hash": a.password_hash,
                                "is_superuser": a.is_superuser, "is_marketing": a.is_marketing}
                               for a in Admin.query.all()],
                    "membership_types": [{"id": m.id, "name": m.name, "description": m.description,
                                          "price": m.price, "cashback_percent": m.cashback_percent,
                                          "discount_percent": m.discount_percent,
                                          "drinks_count": m.drinks_count, "grain_bags": m.grain_bags,
                                          "badge_icon": m.badge_icon, "badge_color": m.badge_color,
                                          "background_image": m.background_image, "is_active": m.is_active}
                                         for m in MembershipType.query.all()],
                    "products": [{"id": p.id, "name": p.name, "description": p.description,
                                  "cost": float(p.cost), "price": float(p.price),
                                  "quantity": p.quantity, "reorder_point": p.reorder_point,
                                  "image_url": p.image_url}
                                 for p in Product.query.all()],
                    "product_movements": [{"id": m.id, "product_id": m.product_id, "change": m.change,
                                           "movement_type": m.movement_type, "note": m.note,
                                           "created_at": m.created_at.strftime("%Y-%m-%d %H:%M:%S") if m.created_at else None}
                                          for m in ProductMovement.query.all()],
                }
                filename = f"auto_{today}_{datetime.utcnow().strftime('%H%M%S')}.json"
                filepath = os.path.join(backup_dir, filename)
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                # Conservar solo los últimos 30 respaldos automáticos
                auto_files = sorted([f for f in os.listdir(backup_dir) if f.startswith('auto_')])
                for old in auto_files[:-30]:
                    try:
                        os.remove(os.path.join(backup_dir, old))
                    except Exception:
                        pass
                logger.info(f"Respaldo automático creado: {filename}")
        except Exception as e:
            logger.error(f"Error creando respaldo automático: {e}")

    create_auto_backup()

    def collect_backup_data():
        """Recopila todos los datos de la app en un dict (para respaldos)."""
        return {
            "version": "1.3",
            "exported_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "cards": [{"code": c.code, "client_name": c.client_name, "phone": c.phone,
                       "balance": float(c.balance), "is_active": c.is_active,
                       "custom_message": c.custom_message,
                       "membership_type_id": c.membership_type_id,
                       "membership_expires_at": c.membership_expires_at.strftime("%Y-%m-%d %H:%M:%S") if c.membership_expires_at else None,
                       "drinks_redeemed_this_month": c.drinks_redeemed_this_month,
                       "grain_bags_redeemed_this_month": c.grain_bags_redeemed_this_month,
                       "last_benefit_reset": c.last_benefit_reset.strftime("%Y-%m-%d %H:%M:%S") if c.last_benefit_reset else None,
                       "created_at": c.created_at.strftime("%Y-%m-%d %H:%M:%S") if c.created_at else None}
                      for c in Card.query.all()],
            "transactions": [{"card_code": (Card.query.filter_by(id=t.card_id).first().code if Card.query.filter_by(id=t.card_id).first() else None),
                              "type": t.type, "amount": float(t.amount), "note": t.note,
                              "created_at": t.created_at.strftime("%Y-%m-%d %H:%M:%S") if t.created_at else None}
                             for t in Transaction.query.all()],
            "notifications": [{"title": n.title, "message": n.message, "image_url": n.image_url,
                               "link_url": n.link_url,
                               "created_at": n.created_at.strftime("%Y-%m-%d %H:%M:%S") if n.created_at else None}
                              for n in Notification.query.all()],
            "admins": [{"username": a.username, "password_hash": a.password_hash,
                        "is_superuser": a.is_superuser, "is_marketing": a.is_marketing}
                       for a in Admin.query.all()],
            "membership_types": [{"id": m.id, "name": m.name, "description": m.description,
                                  "price": m.price, "cashback_percent": m.cashback_percent,
                                  "discount_percent": m.discount_percent,
                                  "drinks_count": m.drinks_count, "grain_bags": m.grain_bags,
                                  "badge_icon": m.badge_icon, "badge_color": m.badge_color,
                                  "background_image": m.background_image, "is_active": m.is_active}
                                 for m in MembershipType.query.all()],
            "products": [{"id": p.id, "name": p.name, "description": p.description,
                          "cost": float(p.cost), "price": float(p.price),
                          "quantity": p.quantity, "reorder_point": p.reorder_point,
                          "image_url": p.image_url}
                         for p in Product.query.all()],
            "product_movements": [{"id": m.id, "product_id": m.product_id, "change": m.change,
                                   "movement_type": m.movement_type, "note": m.note,
                                   "created_at": m.created_at.strftime("%Y-%m-%d %H:%M:%S") if m.created_at else None}
                                  for m in ProductMovement.query.all()],
        }

    def upload_backup_to_drive_after_change():
        """Genera un respaldo completo y lo sube a Google Drive.
        Se llama después de cada cambio importante de datos.
        Solo sube si el backup automático está activado en configuración.
        Respeta el intervalo configurado para no saturar Drive.
        No lanza excepciones: solo registra en el log."""
        try:
            if not drive_backup.is_configured() or not drive_backup.is_connected():
                return
            # Verificar si el backup automático está activado
            auto_setting = AppSetting.query.filter_by(key="drive_auto_backup").first()
            if not auto_setting or auto_setting.value != "true":
                return
            # Verificar intervalo
            interval_setting = AppSetting.query.filter_by(key="drive_auto_backup_interval").first()
            interval_seconds = int(interval_setting.value) if interval_setting else 3600
            last_setting = AppSetting.query.filter_by(key="drive_last_auto_upload").first()
            if last_setting and last_setting.value:
                try:
                    last_time = datetime.fromisoformat(last_setting.value)
                    elapsed = (datetime.utcnow() - last_time).total_seconds()
                    if elapsed < interval_seconds:
                        return  # Aún no toca
                except Exception:
                    pass
            # Actualizar timestamp del último upload
            if last_setting:
                last_setting.value = datetime.utcnow().isoformat()
            else:
                db.session.add(AppSetting(key="drive_last_auto_upload", value=datetime.utcnow().isoformat()))
            db.session.commit()
            with app.app_context():
                data = collect_backup_data()
                filename = f"backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
                success, msg = drive_backup.upload_backup_to_drive(
                    data, filename=filename, subfolder=drive_backup.DRIVE_DB_SUBFOLDER
                )
                if success:
                    logger.info(f"[Drive Auto] {msg}")
                else:
                    logger.warning(f"[Drive Auto] {msg}")
                # En producción (PostgreSQL), subir también el dump de la base
                if os.environ.get("DATABASE_URL", "").startswith("postgres"):
                    pg_success, pg_msg = drive_backup.upload_postgres_dump()
                    if pg_success:
                        logger.info(f"[Drive Auto] {pg_msg}")
                    else:
                        logger.warning(f"[Drive Auto] {pg_msg}")
        except Exception as e:
            logger.error(f"[Drive Auto] Error en respaldo automático: {e}")

    def upload_backup_to_drive_manual():
        """Sube un respaldo completo a Google Drive (botón manual).
        No verifica intervalo ni configuración de auto-backup."""
        try:
            if not drive_backup.is_configured() or not drive_backup.is_connected():
                return False, "Google Drive no está conectado."
            with app.app_context():
                data = collect_backup_data()
                filename = f"backup_manual_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
                success, msg = drive_backup.upload_backup_to_drive(
                    data, filename=filename, subfolder=drive_backup.DRIVE_DB_SUBFOLDER
                )
                if success:
                    logger.info(f"[Drive Manual] {msg}")
                if os.environ.get("DATABASE_URL", "").startswith("postgres"):
                    pg_success, pg_msg = drive_backup.upload_postgres_dump()
                    if pg_success:
                        logger.info(f"[Drive Manual] {pg_msg}")
                return success, msg
        except Exception as e:
            logger.error(f"[Drive Manual] Error: {e}")
            return False, str(e)

    def restore_backup_data(data):
        """Restaura los datos de un respaldo JSON en la base de datos.
        Devuelve un dict con el resumen de lo importado."""
        memberships_imported = 0
        for md in data.get("membership_types", []):
            existing_m = MembershipType.query.filter_by(id=md["id"]).first()

            def restore_image(b64_data, original_path, prefix):
                if not b64_data: return None
                try:
                    img_dir = os.path.join(app.static_folder, 'img')
                    os.makedirs(img_dir, exist_ok=True)
                    filename = f"{prefix}_{secrets.token_hex(8)}.png"
                    filepath = os.path.join(img_dir, filename)
                    with open(filepath, "wb") as f:
                        f.write(base64.b64decode(b64_data))
                    return f"/static/img/{filename}"
                except Exception as e:
                    get_logger().error(f"Error restaurando imagen: {e}")
                    return original_path

            badge_icon_path = restore_image(md.get("badge_icon_b64"), md.get("badge_icon"), "badge")
            bg_image_path = restore_image(md.get("background_image_b64"), md.get("background_image"), "membership_bg")

            if existing_m:
                existing_m.name = md["name"]
                existing_m.description = md["description"]
                existing_m.price = md["price"]
                existing_m.cashback_percent = md["cashback_percent"]
                existing_m.discount_percent = md["discount_percent"]
                existing_m.drinks_count = md["drinks_count"]
                existing_m.grain_bags = md.get("grain_bags", 0)
                existing_m.badge_icon = badge_icon_path
                existing_m.badge_color = md.get("badge_color", "#FFD700")
                existing_m.background_image = bg_image_path
                existing_m.is_active = md.get("is_active", True)
            else:
                db.session.add(MembershipType(
                    id=md["id"], name=md["name"], description=md["description"],
                    price=md["price"], cashback_percent=md["cashback_percent"],
                    discount_percent=md["discount_percent"], drinks_count=md["drinks_count"],
                    grain_bags=md.get("grain_bags", 0),
                    badge_icon=badge_icon_path,
                    badge_color=md.get("badge_color", "#FFD700"),
                    background_image=bg_image_path, is_active=md.get("is_active", True)
                ))
            memberships_imported += 1

        cards_imported = 0
        for cd in data.get("cards", []):
            existing = Card.query.filter_by(code=cd["code"]).first()
            if existing:
                existing.client_name = cd["client_name"]
                existing.phone = cd["phone"]
                existing.balance = cd["balance"]
                existing.is_active = cd["is_active"]
                existing.custom_message = cd.get("custom_message")
                existing.membership_type_id = cd.get("membership_type_id")
                mem_exp = cd.get("membership_expires_at")
                existing.membership_expires_at = datetime.strptime(mem_exp, "%Y-%m-%d %H:%M:%S") if mem_exp else None
                existing.drinks_redeemed_this_month = cd.get("drinks_redeemed_this_month", 0)
                existing.grain_bags_redeemed_this_month = cd.get("grain_bags_redeemed_this_month", 0)
                last_reset = cd.get("last_benefit_reset")
                existing.last_benefit_reset = datetime.strptime(last_reset, "%Y-%m-%d %H:%M:%S") if last_reset else None
            else:
                mem_exp = cd.get("membership_expires_at")
                last_reset = cd.get("last_benefit_reset")
                db.session.add(Card(
                    code=cd["code"], client_name=cd["client_name"],
                    phone=cd["phone"], balance=cd["balance"],
                    is_active=cd["is_active"], custom_message=cd.get("custom_message"),
                    membership_type_id=cd.get("membership_type_id"),
                    membership_expires_at=datetime.strptime(mem_exp, "%Y-%m-%d %H:%M:%S") if mem_exp else None,
                    drinks_redeemed_this_month=cd.get("drinks_redeemed_this_month", 0),
                    grain_bags_redeemed_this_month=cd.get("grain_bags_redeemed_this_month", 0),
                    last_benefit_reset=datetime.strptime(last_reset, "%Y-%m-%d %H:%M:%S") if last_reset else None
                ))
            cards_imported += 1

        tx_imported = 0
        for td in data.get("transactions", []):
            card = Card.query.filter_by(code=td["card_code"]).first()
            if card:
                db.session.add(Transaction(card_id=card.id, type=td["type"],
                    amount=td["amount"], note=td.get("note")))
                tx_imported += 1

        not_imported = 0
        for nd in data.get("notifications", []):
            db.session.add(Notification(title=nd["title"], message=nd["message"],
                image_url=nd.get("image_url"),
                link_url=nd.get("link_url"),
                created_by_id=session.get("admin_id") if has_request_context() else None))
            not_imported += 1

        admins_imported = 0
        for ad in data.get("admins", []):
            existing_admin = Admin.query.filter_by(username=ad["username"]).first()
            if existing_admin:
                existing_admin.password_hash = ad["password_hash"]
                existing_admin.is_superuser = ad.get("is_superuser", False)
                existing_admin.is_marketing = ad.get("is_marketing", False)
            else:
                new_admin = Admin(
                    username=ad["username"],
                    password_hash=ad["password_hash"],
                    is_superuser=ad.get("is_superuser", False),
                    is_marketing=ad.get("is_marketing", False)
                )
                db.session.add(new_admin)
            admins_imported += 1

        products_imported = 0
        for pd in data.get("products", []):
            existing_p = Product.query.filter_by(id=pd["id"]).first()
            if existing_p:
                existing_p.name = pd["name"]
                existing_p.description = pd.get("description")
                existing_p.cost = pd.get("cost", 0)
                existing_p.price = pd.get("price", 0)
                existing_p.quantity = pd.get("quantity", 0)
                existing_p.reorder_point = pd.get("reorder_point", 0)
                existing_p.image_url = pd.get("image_url")
            else:
                db.session.add(Product(
                    id=pd["id"], name=pd["name"], description=pd.get("description"),
                    cost=pd.get("cost", 0), price=pd.get("price", 0),
                    quantity=pd.get("quantity", 0), reorder_point=pd.get("reorder_point", 0),
                    image_url=pd.get("image_url")
                ))
            products_imported += 1

        movements_imported = 0
        for md in data.get("product_movements", []):
            existing_mv = ProductMovement.query.filter_by(id=md["id"]).first()
            if not existing_mv:
                db.session.add(ProductMovement(
                    id=md["id"], product_id=md["product_id"],
                    change=md["change"], movement_type=md.get("movement_type", "sale"),
                    note=md.get("note")
                ))
                movements_imported += 1

        db.session.commit()
        return {
            "cards": cards_imported,
            "transactions": tx_imported,
            "notifications": not_imported,
            "admins": admins_imported,
            "products": products_imported,
            "movements": movements_imported,
        }

    def restore_latest_backup_from_drive():
        """Al iniciar la app, restaura el último respaldo de Drive si la base está vacía.
        Solo restaura cuando no hay tarjetas ni transacciones (base vacía) para no
        sobrescribir datos existentes (ej. PostgreSQL persistente)."""
        try:
            if not drive_backup.is_configured() or not drive_backup.is_connected():
                logger.info("[Drive] Restauración automática omitida: Drive no conectado.")
                return
            with app.app_context():
                has_cards = Card.query.first() is not None
                has_tx = Transaction.query.first() is not None
                if has_cards or has_tx:
                    logger.info("[Drive] Restauración automática omitida: la base ya tiene datos.")
                    return
                content, filename = drive_backup.get_latest_backup_content()
                if content is None:
                    logger.info("[Drive] Restauración automática omitida: no hay respaldo en Drive.")
                    return
                data = json.loads(content.decode("utf-8"))
                if "version" not in data or "cards" not in data:
                    logger.warning("[Drive] Respaldo inválido, restauración omitida.")
                    return
                summary = restore_backup_data(data)
                logger.info(f"[Drive] Base restaurada desde {filename}: {summary}")
        except Exception as e:
            logger.error(f"[Drive] Error en restauración automática: {e}")

    # Restauración automática desde Google Drive al iniciar (ACTIVA).
    # Solo se restaura si la base está vacía. El auto-respaldo hacia Drive
    # sigue desactivado (solo respaldos manuales).
    restore_latest_backup_from_drive()

    @app.context_processor
    def inject_push_config():
        return {
            "onesignal_app_id": ONESIGNAL_APP_ID,
            "onesignal_safari_web_id": ONESIGNAL_SAFARI_WEB_ID,
            "has_onesignal": bool(ONESIGNAL_APP_ID),
        }

    def send_push_to_all(title, body, url='/notifications', image_url=None):
        """Envia una notificacion push real a todos los usuarios via OneSignal."""
        if not HAS_ONESIGNAL:
            logger.warning('OneSignal no configurado (faltan ONESIGNAL_APP_ID o REST_API_KEY)')
            return {"ok": False, "sent": 0, "error": "OneSignal no esta configurado"}

        target_url = url if url.startswith(('http://', 'https://')) else f"{get_public_base_url()}{url}"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {ONESIGNAL_REST_API_KEY}"
        }
        
        payload = {
            "app_id": ONESIGNAL_APP_ID,
            "included_segments": ["All"],
            "headings": {"en": title, "es": title},
            "contents": {"en": body, "es": body},
            "url": target_url
        }
        
        # Imagen grande para la notificacion:
        # - big_picture: Android
        # - chrome_web_image: Chrome (web)
        # - adm_big_picture: Amazon
        # - ios_attachments: iOS (requiere formato {"id": "URL"})
        if image_url:
            payload["big_picture"] = image_url
            payload["chrome_web_image"] = image_url
            payload["adm_big_picture"] = image_url
            payload["ios_attachments"] = {"id": image_url}
        
        try:
            response = requests.post(
                "https://onesignal.com/api/v1/notifications",
                headers=headers,
                json=payload
            )
            
            # Leer respuesta incluso si es error (400 devuelve JSON con detalles)
            data = response.json()
            
            if response.status_code >= 400:
                error_msg = data.get("errors", [str(data)])
                logger.error(f'OneSignal error {response.status_code}: {error_msg}')
                return {"ok": False, "sent": 0, "error": f"{response.status_code}: {', '.join(error_msg) if isinstance(error_msg, list) else error_msg}"}
            
            sent = int(data.get("recipients") or 0)
            logger.info(f'Notificacion OneSignal enviada: {title} -> {sent} recipients')
            return {"ok": True, "sent": sent, "id": data.get("id")}
        except requests.exceptions.RequestException as e:
            logger.error(f'Error de red en OneSignal: {e}')
            return {"ok": False, "sent": 0, "error": f"Error de red: {str(e)}"}
        except Exception as e:
            logger.error(f'Error enviando notificacion OneSignal: {e}')
            return {"ok": False, "sent": 0, "error": str(e)}

    # Las rutas de suscripcion VAPID han sido eliminadas.
    # OneSignal gestiona las suscripciones y el envio de notificaciones automaticamente.

    @app.route("/")
    def index():
        if session.get("admin_id"): return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/favicon.ico")
    def favicon():
        # Algunos navegadores piden /favicon.ico directamente
        return send_file(os.path.join(base_dir, 'static', 'favicon.ico'), mimetype='image/x-icon')

    @app.route("/OneSignalSDKWorker.js")
    @app.route("/OneSignalSDKUpdaterWorker.js")
    def onesignal_worker():
        response = send_file(os.path.join(base_dir, 'static', 'OneSignalSDKWorker.js'), mimetype='application/javascript')
        response.headers['Service-Worker-Allowed'] = '/'
        return response

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("admin_id"): return redirect(url_for("dashboard"))
        if request.method == "POST":
            username = sanitize_input(request.form.get("username", ""))
            password = request.form.get("password", "")
            admin = Admin.query.filter_by(username=username).first()
            if admin and admin.check_password(password):
                session["admin_id"] = admin.id
                session["admin_username"] = admin.username
                session["is_superuser"] = bool(admin.is_superuser)
                session["is_marketing"] = bool(admin.is_marketing)
                admin.update_last_login()
                return redirect(url_for("dashboard"))
            flash("Usuario o contrasena incorrectos", "error")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/dashboard")
    def dashboard():
        if not session.get("admin_id"): return redirect(url_for("login"))
        search = sanitize_input(request.args.get("q", "").strip())
        if search:
            # Busca por nombre, telfono O por cdigo (el escner QR llena este campo
            # con el cdigo de la tarjeta para localizarla y canjear/recargar)
            cards = Card.query.filter(
                db.or_(
                    Card.client_name.like(f"%{search}%"),
                    Card.phone.like(f"%{search}%"),
                    Card.code.like(f"%{search}%")
                )
            ).order_by(Card.created_at.desc()).limit(50).all()
        else:
            cards = Card.query.order_by(Card.created_at.desc()).limit(50).all()
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        total_balance = db.session.query(db.func.coalesce(db.func.sum(Card.balance), 0)).scalar()
        today_cards = Card.query.filter(Card.created_at >= today_start).count()
        stats = {
            "total_cards": Card.query.count(),
            "active_cards": Card.query.filter_by(is_active=True).count(),
            "total_balance": float(total_balance),
            "today_cards": today_cards
        }
        return render_template("dashboard.html", cards=cards, stats=stats)

    @app.route("/stats")
    def stats():
        """Estadsticas de dinero exclusivas para superusuarios."""
        if not session.get("admin_id"):
            return redirect(url_for("login"))
        if not session.get("is_superuser"):
            flash("No tienes permiso para ver estadsticas", "error")
            return redirect(url_for("dashboard"))
        
        # Periodos
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=7)
        month_start = today_start - timedelta(days=30)
        
        # Totales de dinero
        total_cargas = db.session.query(
            db.func.coalesce(db.func.sum(Transaction.amount), 0)
        ).filter(Transaction.type == 'carga').scalar()
        total_canjes = db.session.query(
            db.func.coalesce(db.func.sum(Transaction.amount), 0)
        ).filter(Transaction.type == 'canje').scalar()
        total_balance_actual = db.session.query(
            db.func.coalesce(db.func.sum(Card.balance), 0)
        ).scalar()
        
        # Cargas/canjes hoy, semana, mes
        cargas_hoy = db.session.query(
            db.func.coalesce(db.func.sum(Transaction.amount), 0)
        ).filter(Transaction.type == 'carga', Transaction.created_at >= today_start).scalar()
        canjes_hoy = db.session.query(
            db.func.coalesce(db.func.sum(Transaction.amount), 0)
        ).filter(Transaction.type == 'canje', Transaction.created_at >= today_start).scalar()
        cargas_semana = db.session.query(
            db.func.coalesce(db.func.sum(Transaction.amount), 0)
        ).filter(Transaction.type == 'carga', Transaction.created_at >= week_start).scalar()
        canjes_semana = db.session.query(
            db.func.coalesce(db.func.sum(Transaction.amount), 0)
        ).filter(Transaction.type == 'canje', Transaction.created_at >= week_start).scalar()
        cargas_mes = db.session.query(
            db.func.coalesce(db.func.sum(Transaction.amount), 0)
        ).filter(Transaction.type == 'carga', Transaction.created_at >= month_start).scalar()
        canjes_mes = db.session.query(
            db.func.coalesce(db.func.sum(Transaction.amount), 0)
        ).filter(Transaction.type == 'canje', Transaction.created_at >= month_start).scalar()
        
        # Conteos de transacciones
        total_transacciones = Transaction.query.count()
        cargas_count = Transaction.query.filter_by(type='carga').count()
        canjes_count = Transaction.query.filter_by(type='canje').count()
        
        # Promedios
        promedio_carga = float(total_cargas) / cargas_count if cargas_count > 0 else 0
        promedio_canje = float(total_canjes) / canjes_count if canjes_count > 0 else 0
        
        # Tarjetas con saldo positivo / agotadas
        tarjetas_con_saldo = Card.query.filter(Card.balance > 0).count()
        tarjetas_agotadas = Card.query.filter(Card.balance <= 0, Card.is_active == True).count()
        
        # ltimas 10 transacciones
        ultimas_transacciones = Transaction.query.order_by(
            Transaction.created_at.desc()
        ).limit(10).all()
        
        # Transacciones por da (ltimos 7 das) para mini tabla
        transacciones_por_dia = []
        for i in range(6, -1, -1):
            dia = (today_start - timedelta(days=i))
            dia_siguiente = dia + timedelta(days=1)
            cargas_dia = db.session.query(
                db.func.coalesce(db.func.sum(Transaction.amount), 0)
            ).filter(
                Transaction.type == 'carga',
                Transaction.created_at >= dia,
                Transaction.created_at < dia_siguiente
            ).scalar()
            canjes_dia = db.session.query(
                db.func.coalesce(db.func.sum(Transaction.amount), 0)
            ).filter(
                Transaction.type == 'canje',
                Transaction.created_at >= dia,
                Transaction.created_at < dia_siguiente
            ).scalar()
            transacciones_por_dia.append({
                'fecha': dia.strftime('%d/%m'),
                'cargas': float(cargas_dia),
                'canjes': float(canjes_dia)
            })
        
        stats = {
            'total_cargas': float(total_cargas),
            'total_canjes': float(total_canjes),
            'total_balance_actual': float(total_balance_actual),
            'cargas_hoy': float(cargas_hoy),
            'canjes_hoy': float(canjes_hoy),
            'cargas_semana': float(cargas_semana),
            'canjes_semana': float(canjes_semana),
            'cargas_mes': float(cargas_mes),
            'canjes_mes': float(canjes_mes),
            'total_transacciones': total_transacciones,
            'cargas_count': cargas_count,
            'canjes_count': canjes_count,
            'promedio_carga': promedio_carga,
            'promedio_canje': promedio_canje,
            'tarjetas_con_saldo': tarjetas_con_saldo,
            'tarjetas_agotadas': tarjetas_agotadas,
            'ultimas_transacciones': ultimas_transacciones,
            'transacciones_por_dia': transacciones_por_dia
        }
        return render_template("stats.html", stats=stats)

    @app.route("/stats/download")
    def stats_download():
        """Descarga las estadsticas en formato CSV (compatible con Excel)"""
        if not session.get("admin_id"):
            return jsonify({"error": "No autorizado"}), 401
        if not session.get("is_superuser"):
            return jsonify({"error": "Sin permisos"}), 403
        
        import csv
        from io import StringIO
        
        # Obtener todas las transacciones
        transactions = Transaction.query.order_by(Transaction.created_at.desc()).all()
        
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(['ID', 'Fecha', 'Tarjeta', 'Cliente', 'Telfono', 'Tipo', 'Monto', 'Nota'])
        
        for tx in transactions:
            card = tx.card
            writer.writerow([
                tx.id,
                tx.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                card.code if card else '',
                card.client_name if card else '',
                card.phone if card else '',
                tx.type,
                f"{tx.amount:.2f}",
                tx.note or ''
            ])
        
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=estadisticas_jubilo.csv'}
        )

    @app.route("/stats/reset", methods=["POST"])
    def stats_reset():
        """Reinicia los contadores mensuales (elimina transacciones del mes actual)"""
        if not session.get("admin_id"):
            return jsonify({"error": "No autorizado"}), 401
        if not session.get("is_superuser"):
            return jsonify({"error": "Sin permisos"}), 403
        
        try:
            # Obtener el primer da del mes actual
            now = datetime.utcnow()
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            
            # Eliminar transacciones del mes actual
            deleted = Transaction.query.filter(Transaction.created_at >= month_start).delete()
            db.session.commit()
            
            return jsonify({
                "ok": True,
                "deleted": deleted,
                "message": f"Se eliminaron {deleted} transacciones del mes actual"
            })
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 500

    @app.route("/stats/inventory/download")
    def stats_inventory_download():
        """Descarga el inventario (productos + movimientos) en CSV."""
        if not session.get("admin_id"):
            return jsonify({"error": "No autorizado"}), 401
        if not session.get("is_superuser"):
            return jsonify({"error": "Sin permisos"}), 403

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['ID Producto', 'Nombre', 'Descripción', 'Costo', 'Precio', 'Stock', 'Pto Reorden', 'ID Movimiento', 'Fecha Movimiento', 'Tipo Movimiento', 'Cambio', 'Nota'])

        products = Product.query.all()
        product_map = {p.id: p for p in products}
        movements = ProductMovement.query.order_by(ProductMovement.created_at.desc()).all()

        # Primero productos sin movimientos
        written_products = set()
        for m in movements:
            p = product_map.get(m.product_id)
            if p:
                written_products.add(p.id)
            writer.writerow([
                m.product_id,
                p.name if p else '(eliminado)',
                p.description if p else '',
                f'{p.cost:.2f}' if p else '',
                f'{p.price:.2f}' if p else '',
                p.quantity if p else '',
                p.reorder_point if p else '',
                m.id,
                m.created_at.strftime('%Y-%m-%d %H:%M:%S') if m.created_at else '',
                m.movement_type,
                m.change,
                m.note or ''
            ])
        for p in products:
            if p.id not in written_products:
                writer.writerow([p.id, p.name, p.description, f'{p.cost:.2f}', f'{p.price:.2f}', p.quantity, p.reorder_point, '', '', '', '', ''])

        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=inventario_jubilo.csv'}
        )

    @app.route("/stats/inventory/reset", methods=["POST"])
    def stats_inventory_reset():
        """Elimina todos los movimientos de stock (reinicia inventario)."""
        if not session.get("admin_id"):
            return jsonify({"error": "No autorizado"}), 401
        if not session.get("is_superuser"):
            return jsonify({"error": "Sin permisos"}), 403
        try:
            deleted = ProductMovement.query.delete()
            db.session.commit()
            return jsonify({
                "ok": True,
                "deleted": deleted,
                "message": f"Se eliminaron {deleted} movimientos de stock"
            })
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 500

    @app.route("/memberships", methods=["GET", "POST"])
    def memberships():
        """Gestin de tipos de membresa (solo superusuarios)"""
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not session.get("is_superuser"):
            flash("Solo el superusuario puede gestionar membresas", "error")
            return redirect(url_for("dashboard"))
        
        # Obtener listas de imgenes disponibles (Ahora dentro de static/ImgenQr)
        icon_dir = os.path.join(base_dir, 'static', 'ImgenQr', 'Iconos')
        bg_dir = os.path.join(base_dir, 'static', 'ImgenQr', 'Fondos')
        
        available_icons = []
        available_bgs = []
        
        if os.path.exists(icon_dir):
            available_icons = sorted([f for f in os.listdir(icon_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg'))])
        
        if os.path.exists(bg_dir):
            available_bgs = sorted([f for f in os.listdir(bg_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg'))])
        
        if request.method == "POST":
            name = sanitize_input(request.form.get("name", ""))
            description = sanitize_input(request.form.get("description", ""))
            price = float(request.form.get("price") or 0)
            cashback_percent = float(request.form.get("cashback_percent") or 0)
            discount_percent = float(request.form.get("discount_percent") or 0)
            drinks_count = int(request.form.get("drinks_count") or 0)
            grain_bags = int(request.form.get("grain_bags") or 0)
            badge_color = request.form.get("badge_color", "#FFD700")
            is_default = request.form.get("is_default") == "on"
            
            # Validar y asignar rutas de imgenes seleccionadas
            selected_icon = request.form.get("badge_icon_file")
            selected_bg = request.form.get("background_image_file")
            
            badge_icon_path = f"/static/ImgenQr/Iconos/{selected_icon}" if selected_icon and selected_icon in available_icons else None
            background_image_path = f"/static/ImgenQr/Fondos/{selected_bg}" if selected_bg and selected_bg in available_bgs else None

            # Si esta es la default, desmarcar las demás
            if is_default:
                MembershipType.query.filter_by(is_default=True).update({MembershipType.is_default: False})

            new_membership = MembershipType(
                name=name,
                description=description,
                price=price,
                cashback_percent=cashback_percent,
                discount_percent=discount_percent,
                drinks_count=drinks_count,
                grain_bags=grain_bags,
                badge_icon=badge_icon_path,
                badge_color=badge_color,
                background_image=background_image_path,
                is_default=is_default
            )
            db.session.add(new_membership)
            db.session.commit()
            upload_backup_to_drive_after_change()
            flash(f"Tipo de membresa '{name}' creado correctamente", "success")
            return redirect(url_for("memberships"))

        memberships_list = MembershipType.query.order_by(MembershipType.price.asc()).all()
        default_membership = get_default_membership()
        return render_template("memberships.html", memberships=memberships_list, available_icons=available_icons, available_bgs=available_bgs, default_membership=default_membership)

    @app.route("/memberships/<int:membership_id>/edit", methods=["POST"])
    def edit_membership(membership_id):
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not session.get("is_superuser"):
            flash("Solo el superusuario puede gestionar membresas", "error")
            return redirect(url_for("dashboard"))
        
        membership = MembershipType.query.filter_by(id=membership_id).first_or_404()
        
        # Obtener listas de imgenes disponibles para validacin (static/ImgenQr)
        icon_dir = os.path.join(base_dir, 'static', 'ImgenQr', 'Iconos')
        bg_dir = os.path.join(base_dir, 'static', 'ImgenQr', 'Fondos')
        available_icons = [f for f in os.listdir(icon_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg'))] if os.path.exists(icon_dir) else []
        available_bgs = [f for f in os.listdir(bg_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg'))] if os.path.exists(bg_dir) else []
        
        membership.name = sanitize_input(request.form.get("name", membership.name))
        membership.description = sanitize_input(request.form.get("description", membership.description))
        membership.price = float(request.form.get("price") or membership.price)
        membership.cashback_percent = float(request.form.get("cashback_percent") or membership.cashback_percent)
        membership.discount_percent = float(request.form.get("discount_percent") or membership.discount_percent)
        membership.drinks_count = int(request.form.get("drinks_count") or membership.drinks_count)
        membership.grain_bags = int(request.form.get("grain_bags") or membership.grain_bags)
        membership.badge_color = request.form.get("badge_color", membership.badge_color)
        is_default = request.form.get("is_default") == "on"
        
        # Si esta es la default, desmarcar las demás (excepto ella misma)
        if is_default and not membership.is_default:
            MembershipType.query.filter_by(is_default=True).update({MembershipType.is_default: False})
        membership.is_default = is_default
        
        # Validar y asignar rutas de imgenes seleccionadas desde carpetas fijas
        selected_icon = request.form.get("badge_icon_file")
        selected_bg = request.form.get("background_image_file")
        
        if selected_icon and selected_icon in available_icons:
            membership.badge_icon = f"/static/ImgenQr/Iconos/{selected_icon}"
        elif selected_icon == "":
            membership.badge_icon = None
            
        if selected_bg and selected_bg in available_bgs:
            membership.background_image = f"/static/ImgenQr/Fondos/{selected_bg}"
        elif selected_bg == "":
            membership.background_image = None

        db.session.commit()
        upload_backup_to_drive_after_change()
        flash(f"Membresa '{membership.name}' actualizada correctamente", "success")
        return redirect(url_for("memberships"))

    @app.route("/memberships/<int:membership_id>/delete", methods=["POST"])
    def delete_membership(membership_id):
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not session.get("is_superuser"):
            flash("Solo el superusuario puede gestionar membresas", "error")
            return redirect(url_for("dashboard"))
        
        membership = MembershipType.query.filter_by(id=membership_id).first_or_404()
        # Eliminar imagen si existe
        if membership.background_image:
            try:
                img_path = os.path.join(base_dir, membership.background_image.lstrip('/'))
                if os.path.exists(img_path):
                    os.remove(img_path)
            except Exception:
                pass
        
        db.session.delete(membership)
        db.session.commit()
        upload_backup_to_drive_after_change()
        flash("Tipo de membresa eliminado", "success")
        return redirect(url_for("memberships"))

    @app.route("/planes")
    def planes():
        """Vista pública de membresías para clientes (no requiere login)."""
        memberships_list = MembershipType.query.filter_by(is_active=True).order_by(MembershipType.price.asc()).all()
        default_membership = get_default_membership()
        return render_template("planes.html", memberships=memberships_list, default_membership=default_membership)

    def get_default_membership():
        """Obtiene la membresía marcada como por defecto"""
        return MembershipType.query.filter_by(is_default=True).first()

    def check_and_revert_expired_memberships(card):
        """Verifica si la membresía de la tarjeta caducó y la revierte a la principal"""
        now = datetime.utcnow()
        if card.membership_type and card.membership_expires_at and now > card.membership_expires_at:
            default_m = get_default_membership()
            if default_m:
                card.membership_type_id = default_m.id
                card.membership_expires_at = None # La principal no tiene fecha de expiración
                db.session.commit()
                get_logger().info(f"Tarjeta {card.code} revertida a membresía principal '{default_m.name}' por caducidad.")

    def get_default_membership():
        """Obtiene la membresía marcada como por defecto"""
        return MembershipType.query.filter_by(is_default=True).first()

    def check_and_revert_expired_memberships(card):
        """Verifica si la membresía de la tarjeta caducó y la revierte a la principal"""
        now = datetime.utcnow()
        if card.membership_type and card.membership_expires_at and now > card.membership_expires_at:
            default_m = get_default_membership()
            if default_m:
                card.membership_type_id = default_m.id
                card.membership_expires_at = None # La principal no tiene fecha de expiración
                # Reiniciar contadores al cambiar de plan
                card.drinks_redeemed_this_month = 0
                card.grain_bags_redeemed_this_month = 0
                card.last_benefit_reset = now
                db.session.commit()
                get_logger().info(f"Tarjeta {card.code} revertida a membresía principal '{default_m.name}' por caducidad.")

    @app.route("/generate", methods=["GET", "POST"])
    def generate():
        if not session.get("admin_id"): return redirect(url_for("login"))
        
        # Obtener tipos de membresa para el selector
        membership_types = MembershipType.query.filter_by(is_active=True).order_by(MembershipType.price.asc()).all()
        default_membership = get_default_membership()
        
        if request.method == "POST":
            try:
                client_name = sanitize_input(request.form.get("client_name", ""))
                phone = sanitize_input(request.form.get("phone", ""))
                amount = float(request.form.get("amount", 0))
                custom_message = sanitize_input(request.form.get("custom_message", ""))
                if not custom_message:
                    custom_message = "¡Muchísimas felicidades! Disfruta de tu tarjeta Jubilo y de todos los beneficios que trae para ti."
                membership_type_id = request.form.get("membership_type_id")
                
                code = generate_unique_code()
                
                # Lgica de membresa
                final_membership_id = None
                membership_expires_at = None
                
                if membership_type_id:
                    selected_m = MembershipType.query.get(membership_type_id)
                    if selected_m:
                        final_membership_id = selected_m.id
                        # Si no es la default, poner fecha de expiración (30 días)
                        if not selected_m.is_default:
                            membership_expires_at = datetime.utcnow() + timedelta(days=30)
                else:
                    # Si no se selecciona ninguna, usar la default si existe
                    if default_membership:
                        final_membership_id = default_membership.id
                
                card = Card(
                    code=code, 
                    client_name=client_name, 
                    phone=phone, 
                    balance=amount, 
                    custom_message=custom_message, 
                    is_active=True, 
                    created_at=datetime.utcnow(),
                    membership_type_id=final_membership_id,
                    membership_expires_at=membership_expires_at
                )
                db.session.add(card)
                db.session.flush()
                card.add_transaction(amount, "carga", "Carga inicial")
                db.session.commit()
                upload_backup_to_drive_after_change()
                flash(f"Tarjeta creada para {client_name}", "success")
                return redirect(url_for("view_card", code=code))
            except Exception as e:
                db.session.rollback()
                flash(f"Error: {str(e)}", "error")
        
        return render_template("generate.html", membership_types=membership_types, default_membership=default_membership)

    @app.route("/card/<code>")
    def view_card(code):
        card = Card.query.filter_by(code=code).first_or_404()
        
        # Verificar si la membresía ha caducado y revertir a la principal
        check_and_revert_expired_memberships(card)
        
        is_admin = session.get("admin_id")
        wa_image_url = None
        if card.phone and is_admin:
            wa_image_url = url_for("whatsapp_image", code=code, _external=True)
        # Obtener ltimos 5 movimientos para la vista de cliente
        recent_transactions = Transaction.query.filter_by(card_id=card.id).order_by(Transaction.created_at.desc()).limit(5).all()
        
        # Obtener tipos de membresa para el selector (si es admin)
        membership_types = []
        if is_admin:
            membership_types = MembershipType.query.filter_by(is_active=True).order_by(MembershipType.price.asc()).all()
            
        html = render_template("card.html", card=card, is_admin=is_admin, wa_image_url=wa_image_url, recent_transactions=recent_transactions, membership_types=membership_types)
        # Forzar charset=UTF-8 para que el navegador reciba los emojis como
        # bytes UTF-8 reales y encodeURIComponent los codifique correctamente
        # (sin esto, WhatsApp muestra el texto literal en vez del emoji).
        return Response(html, mimetype='text/html; charset=utf-8')

    @app.route("/card/<code>/whatsapp-image")
    def whatsapp_image(code):
        """Genera imagen personalizada de la tarjeta (fondo, nombre, saldo, QR).
        Accesible tambin para el CLIENTE: quien tenga el enlace de la tarjeta
        ya puede ver esos mismos datos, y usa esta ruta para descargar su imagen
        (botn de descarga con ?dl=1). Sin ?dl=1 se muestra inline (vista previa)."""
        card = Card.query.filter_by(code=code).first_or_404()
        base_url = get_public_base_url()
        qr_data = f"{base_url}/card/{code}"
        
        # Permitir override del mensaje personalizado via query string
        custom_message = request.args.get("msg", card.custom_message)
        
        # Preparar datos de membresa para la imagen
        membership_data = None
        if card.membership_type:
            membership_data = {
                'name': card.membership_type.name,
                'badge_icon': card.membership_type.badge_icon,
                'background_image': card.membership_type.background_image,
                'badge_color': card.membership_type.badge_color,
                'cashback_percent': card.membership_type.cashback_percent,
                'discount_percent': card.membership_type.discount_percent,
                'drinks_count': card.membership_type.drinks_count,
                'grain_bags': card.membership_type.grain_bags,
                'is_default': card.membership_type.is_default # Importante para ocultar vencimiento
            }
        
        expires_str = card.membership_expires_at.strftime('%d/%m/%Y') if card.membership_expires_at else None
        
        img_bytes = generate_whatsapp_image(
            client_name=card.client_name,
            qr_data=qr_data,
            custom_message=custom_message,
            balance=card.balance,
            membership=membership_data,
            expires_at=expires_str
        )
        as_attachment = request.args.get("dl") == "1"
        return send_file(io.BytesIO(img_bytes), mimetype="image/png", as_attachment=as_attachment, download_name=f"tarjeta_{card.code}.png")

    @app.route("/card/<code>/add-balance", methods=["POST"])
    def add_balance(code):
        if not session.get("admin_id"): return redirect(url_for("login"))
        card = Card.query.filter_by(code=code).first_or_404()
        amount = float(request.form.get("amount", 0))
        note = request.form.get("note", "Recarga")
        
        # Verificar vencimiento de membresa
        if card.membership_type and card.membership_expires_at:
            if datetime.utcnow() > card.membership_expires_at:
                card.membership_type_id = None
                card.membership_expires_at = None
                flash(" La membresa ha vencido y ha sido desvinculada.", "info")
        
        # En recargas NO aplica cashback, solo se suma el monto
        card.add_transaction(amount, "carga", note)
        card.balance += amount
        db.session.commit()
        upload_backup_to_drive_after_change()
        flash(f"Recargado ${amount:.2f}", "success")
        return redirect(url_for("view_card", code=code))

    @app.route("/card/<code>/redeem", methods=["POST"])
    def redeem(code):
        if not session.get("admin_id"): return redirect(url_for("login"))
        card = Card.query.filter_by(code=code).first_or_404()
        amount = float(request.form.get("amount", 0))
        note = request.form.get("note", "Canje")
        
        # Validar que el saldo no quede negativo
        if card.balance < amount:
            flash(f" Saldo insuficiente. Saldo actual: ${card.balance:.2f}", "error")
            return redirect(url_for("view_card", code=code))
            
        # Lgica de Membresa en Canje
        discount_amount = 0
        cashback_amount = 0
        
        if card.membership_type and card.membership_expires_at:
            if datetime.utcnow() > card.membership_expires_at:
                card.membership_type_id = None
                card.membership_expires_at = None
                flash(" La membresa ha vencido.", "info")
            else:
                # Aplicar descuento
                discount_percent = card.membership_type.discount_percent
                if discount_percent > 0:
                    discount_amount = amount * (discount_percent / 100)
                    amount -= discount_amount # El cliente paga menos
                    note += f" (-${discount_amount:.2f} dto)"
                
                # Aplicar cashback SOLO en canjes
                cashback_percent = card.membership_type.cashback_percent
                if cashback_percent > 0:
                    cashback_amount = amount * (cashback_percent / 100)
                    note += f" (+${cashback_amount:.2f} cashback)"
        
        final_amount = amount + cashback_amount
        # El saldo final es: SaldoActual - (MontoCanje - Descuento) + Cashback
        # O simplemente: SaldoActual - MontoNetoPagado + Cashback
        card.balance = card.balance - amount + cashback_amount
        
        card.add_transaction(-final_amount, "canje", note)
        db.session.commit()
        upload_backup_to_drive_after_change()
        
        msg = f"Canjeado ${amount:.2f}"
        if discount_amount > 0: msg += f" con ${discount_amount:.2f} de descuento "
        if cashback_amount > 0: msg += f" y ganaste ${cashback_amount:.2f} de cashback "
        flash(msg, "success")
        return redirect(url_for("view_card", code=code))

    @app.route("/card/<code>/delete", methods=["POST"])
    def delete_card(code):
        if not session.get("admin_id"): return redirect(url_for("login"))
        # Solo superusuarios pueden eliminar tarjetas
        if not session.get("is_superuser"):
            flash("Solo el superusuario puede eliminar tarjetas", "error")
            return redirect(url_for("dashboard"))
        card = Card.query.filter_by(code=code).first_or_404()
        db.session.delete(card)
        db.session.commit()
        upload_backup_to_drive_after_change()
        flash("Tarjeta eliminada", "success")
        return redirect(url_for("dashboard"))

    # ===== GESTIN DE USUARIOS (solo superusuarios) =====
    @app.route("/users", methods=["GET", "POST"])
    def users():
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not session.get("is_superuser"):
            flash("Solo el superusuario puede gestionar usuarios", "error")
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            username = sanitize_input(request.form.get("username", ""))
            password = request.form.get("password", "")
            is_super = request.form.get("is_superuser") == "on"
            is_marketing = request.form.get("is_marketing") == "on"
            if not username or not password:
                flash("Usuario y contrasena son obligatorios", "error")
            elif len(password) < 6:
                flash("La contrasena debe tener al menos 6 caracteres", "error")
            elif Admin.query.filter_by(username=username).first():
                flash(f"El usuario '{username}' ya existe", "error")
            else:
                new_admin = Admin(username=username, is_superuser=is_super, is_marketing=is_marketing and not is_super)
                new_admin.set_password(password)
                db.session.add(new_admin)
                db.session.commit()
                upload_backup_to_drive_after_change()
                flash(f"Usuario '{username}' creado correctamente", "success")
                return redirect(url_for("users"))
        admins = Admin.query.order_by(Admin.created_at.desc()).all()
        return render_template("users.html", admins=admins)

    @app.route("/users/<int:admin_id>/delete", methods=["POST"])
    def delete_user(admin_id):
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not session.get("is_superuser"):
            flash("Solo el superusuario puede gestionar usuarios", "error")
            return redirect(url_for("dashboard"))
        if admin_id == session.get("admin_id"):
            flash("No puedes eliminar tu propio usuario", "error")
            return redirect(url_for("users"))
        target = Admin.query.filter_by(id=admin_id).first_or_404()
        db.session.delete(target)
        db.session.commit()
        upload_backup_to_drive_after_change()
        flash(f"Usuario '{target.username}' eliminado", "success")
        return redirect(url_for("users"))

    @app.route("/users/<int:admin_id>/password", methods=["POST"])
    def change_user_password(admin_id):
        if not session.get("admin_id"): return redirect(url_for("login"))
        # Solo el usuario iniciado puede cambiar SU PROPIA contraseña,
        # sin importar si es superusuario o no.
        if admin_id != session.get("admin_id"):
            flash("Solo puedes cambiar tu propia contrasena", "error")
            return redirect(url_for("users"))
        password = request.form.get("password", "")
        if len(password) < 6:
            flash("La contrasena debe tener al menos 6 caracteres", "error")
            return redirect(url_for("users"))
        target = Admin.query.filter_by(id=admin_id).first_or_404()
        target.set_password(password)
        db.session.commit()
        upload_backup_to_drive_after_change()
        flash("Contrasena actualizada correctamente", "success")
        return redirect(url_for("users"))

    # ===== NOTIFICACIONES (superusuarios y marketing) =====
    def can_manage_notifications():
        """Solo superusuarios o usuarios de marketing pueden gestionar notificaciones"""
        return bool(session.get("is_superuser") or session.get("is_marketing"))

    def save_notification_image(file_storage):
        """Guarda una imagen subida desde el dispositivo y devuelve su URL pública."""
        import uuid
        from werkzeug.utils import secure_filename

        ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
        filename = secure_filename(file_storage.filename or '')
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'png'
        if ext not in ALLOWED_EXT:
            ext = 'png'
        # Nombre único para evitar colisiones
        unique_name = f"notif_{uuid.uuid4().hex[:12]}.{ext}"
        upload_dir = os.path.join(app.static_folder, 'notif_images')
        os.makedirs(upload_dir, exist_ok=True)
        file_storage.save(os.path.join(upload_dir, unique_name))
        # URL pública absoluta
        base = get_public_base_url()
        return f"{base}/static/notif_images/{unique_name}"

    @app.route("/notifications", methods=["GET", "POST"])
    def notifications():
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not can_manage_notifications():
            flash("No tienes permisos para gestionar notificaciones", "error")
            return redirect(url_for("dashboard"))
        
        last_notif = None
        if request.method == "POST":
            title = sanitize_input(request.form.get("title", ""))
            message = sanitize_input(request.form.get("message", ""))
            image_url = sanitize_input(request.form.get("image_url", "")).strip()
            link_url = sanitize_input(request.form.get("link_url", "")).strip()
            
            # Procesar imagen subida desde el dispositivo
            uploaded_image = request.files.get("image_file")
            if uploaded_image and uploaded_image.filename:
                image_url = save_notification_image(uploaded_image)
            
            if not title or not message:
                flash("Titulo y mensaje son obligatorios", "error")
            else:
                notif = Notification(
                    title=title,
                    message=message,
                    image_url=image_url or None,
                    link_url=link_url or None,
                    created_by_id=session.get("admin_id"),
                    created_at=datetime.utcnow()
                )
                db.session.add(notif)
                db.session.commit()
                upload_backup_to_drive_after_change()
                result = send_push_to_all(title, message, link_url or '/notifications', image_url=image_url or None)
                if result["ok"]:
                    sent = result["sent"]
                    if sent > 0:
                        flash(f"Notificacion creada y enviada a {sent} dispositivo(s)", "success")
                    else:
                        flash("Notificacion creada y enviada a OneSignal", "success")
                else:
                    flash(f"Notificacion creada, pero OneSignal no la envio: {result['error']}", "error")
                return redirect(url_for("notifications"))
        notifs = Notification.query.order_by(Notification.created_at.desc()).all()
        return render_template("notifications.html", notifications=notifs, last_notification=None)

    @app.route("/notifications/<int:notification_id>/edit", methods=["GET", "POST"])
    def edit_notification(notification_id):
        """Editar una notificación ya creada (título, mensaje, imagen y link)."""
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not can_manage_notifications():
            flash("No tienes permisos para gestionar notificaciones", "error")
            return redirect(url_for("dashboard"))

        notif = Notification.query.filter_by(id=notification_id).first_or_404()

        if request.method == "POST":
            title = sanitize_input(request.form.get("title", ""))
            message = sanitize_input(request.form.get("message", ""))
            image_url = sanitize_input(request.form.get("image_url", "")).strip()
            link_url = sanitize_input(request.form.get("link_url", "")).strip()

            # Procesar imagen subida desde el dispositivo (reemplaza la anterior)
            uploaded_image = request.files.get("image_file")
            if uploaded_image and uploaded_image.filename:
                image_url = save_notification_image(uploaded_image)

            if not title or not message:
                flash("Titulo y mensaje son obligatorios", "error")
            else:
                notif.title = title
                notif.message = message
                notif.image_url = image_url or None
                notif.link_url = link_url or None
                db.session.commit()
                upload_backup_to_drive_after_change()
                flash("Notificacion actualizada correctamente", "success")
                return redirect(url_for("notifications"))

        return render_template("edit_notification.html", notif=notif)

    @app.route("/api/notifications/check")
    def check_notifications():
        """Endpoint para polling de nuevas notificaciones (cada 30 segundos)"""
        if not session.get("admin_id"):
            return jsonify({"error": "No autorizado"}), 401
        
        last_check = request.args.get("last_check", "", type=str)
        if last_check:
            from datetime import datetime as dt
            try:
                last_dt = dt.fromisoformat(last_check)
                new_notifs = Notification.query.filter(Notification.created_at >= last_dt).all()
            except ValueError:
                new_notifs = []
        else:
            new_notifs = Notification.query.all()
        
        return jsonify({
            "count": len(new_notifs),
            "notifications": [{"id": n.id, "title": n.title, "message": n.message, "link_url": n.link_url, "created_at": n.created_at.isoformat()} for n in new_notifs],
            "timestamp": datetime.utcnow().isoformat()
        })

    @app.route("/api/notifications/unread-count")
    def unread_count():
        """Endpoint para obtener cantidad de notificaciones no ledas (para el badge del campanita)"""
        if not session.get("admin_id"):
            return jsonify({"error": "No autorizado"}), 401
        
        # Contar todas las notificaciones (todas son "nuevas" hasta que se visiten)
        count = Notification.query.count()
        return jsonify({"unread": count})

    @app.route("/notifications/<int:notification_id>/resend", methods=["POST"])
    @app.route("/notifications/<int:notification_id>/whatsapp", methods=["POST"])
    def resend_notification(notification_id):
        """Reenviar notificacion como push notification via OneSignal."""
        # Detectar si es peticin AJAX por header o content-type
        is_ajax = (
            request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
            request.content_type and 'application/json' in request.content_type
        )
        
        if not session.get("admin_id"):
            if is_ajax:
                return jsonify({"error": "No autorizado"}), 401
            flash("No autorizado", "error")
            return redirect(url_for("login"))
        
        if not can_manage_notifications():
            if is_ajax:
                return jsonify({"error": "Sin permisos"}), 403
            flash("No tienes permisos para gestionar notificaciones", "error")
            return redirect(url_for("dashboard"))
        
        notif = Notification.query.get_or_404(notification_id)
        
        result = send_push_to_all(notif.title, notif.message, notif.link_url or '/notifications', image_url=notif.image_url)
        
        if is_ajax:
            status = 200 if result["ok"] else 502
            return jsonify(result), status
        
        if result["ok"]:
            sent = result["sent"]
            if sent > 0:
                flash(f"Notificacion reenviada a {sent} dispositivo(s)", "success")
            else:
                flash("Notificacion reenviada a OneSignal", "success")
        else:
            flash(f"OneSignal no pudo reenviar la notificacion: {result['error']}", "error")
        return redirect(url_for("notifications"))
    

    @app.route("/notifications/<int:notification_id>/delete", methods=["POST"])
    def delete_notification(notification_id):
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not can_manage_notifications():
            flash("No tienes permisos para gestionar notificaciones", "error")
            return redirect(url_for("dashboard"))
        notif = Notification.query.filter_by(id=notification_id).first_or_404()
        db.session.delete(notif)
        db.session.commit()
        upload_backup_to_drive_after_change()
        flash("Notificacion eliminada", "success")
        return redirect(url_for("notifications"))

    # ===== INVENTARIO =====
    def save_product_image(file_storage):
        """Guarda una foto de producto subida desde el dispositivo y devuelve su URL pública."""
        import uuid
        from werkzeug.utils import secure_filename

        ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
        filename = secure_filename(file_storage.filename or '')
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'png'
        if ext not in ALLOWED_EXT:
            ext = 'png'
        unique_name = f"prod_{uuid.uuid4().hex[:12]}.{ext}"
        upload_dir = os.path.join(app.static_folder, 'inventory_images')
        os.makedirs(upload_dir, exist_ok=True)
        file_storage.save(os.path.join(upload_dir, unique_name))
        base = get_public_base_url()
        return f"{base}/static/inventory_images/{unique_name}"

    @app.route("/inventory", methods=["GET", "POST"])
    def inventory():
        """Gestión del inventario del negocio (productos con foto, stock y precios)."""
        if not session.get("admin_id"): return redirect(url_for("login"))

        if request.method == "POST":
            name = sanitize_input(request.form.get("name", ""))
            description = sanitize_input(request.form.get("description", ""))
            cost = float(request.form.get("cost") or 0)
            price = float(request.form.get("price") or 0)
            quantity = int(request.form.get("quantity") or 0)
            reorder_point = int(request.form.get("reorder_point") or 0)

            image_url = None
            uploaded_image = request.files.get("image_file")
            if uploaded_image and uploaded_image.filename:
                image_url = save_product_image(uploaded_image)

            if not name:
                flash("El nombre del producto es obligatorio", "error")
            else:
                product = Product(
                    name=name,
                    description=description or None,
                    cost=cost,
                    price=price,
                    quantity=quantity,
                    reorder_point=reorder_point,
                    image_url=image_url
                )
                db.session.add(product)
                db.session.commit()
                # Registrar movimiento inicial de stock (reposición inicial)
                if quantity > 0:
                    db.session.add(ProductMovement(
                        product_id=product.id, change=quantity,
                        movement_type='restock', note='Stock inicial'
                    ))
                    db.session.commit()
                upload_backup_to_drive_after_change()
                flash(f"Producto '{name}' agregado al inventario", "success")
                return redirect(url_for("inventory"))

        products = Product.query.order_by(Product.created_at.desc()).all()
        out_of_stock = sum(1 for p in products if p.quantity <= 0)
        low_stock = sum(1 for p in products if p.quantity > 0 and p.quantity <= p.reorder_point)

        # ---- Estadísticas ----
        now = datetime.utcnow()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_ago = now - timedelta(days=7)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        sales_all = ProductMovement.query.filter_by(movement_type='sale').all()
        sales_day = [m for m in sales_all if m.created_at and m.created_at >= day_start]
        sales_week = [m for m in sales_all if m.created_at and m.created_at >= week_ago]
        sales_month = [m for m in sales_all if m.created_at and m.created_at >= month_start]

        def sales_summary(movements):
            units = sum(abs(m.change) for m in movements)
            revenue = sum(abs(m.change) * (m.product.price or 0) for m in movements)
            return units, revenue

        units_day, revenue_day = sales_summary(sales_day)
        units_week, revenue_week = sales_summary(sales_week)
        units_month, revenue_month = sales_summary(sales_month)
        units_total, revenue_total = sales_summary(sales_all)

        # ---- Inversión ----
        # Inversión actual en stock: coste × cantidad de cada producto
        current_investment = sum((p.cost or 0) * p.quantity for p in products)

        # Inversión en reposiciones: coste × unidades repuestas (diario / 7 días / mes / total)
        restocks_all = ProductMovement.query.filter_by(movement_type='restock').all()
        restocks_day = [m for m in restocks_all if m.created_at and m.created_at >= day_start]
        restocks_week = [m for m in restocks_all if m.created_at and m.created_at >= week_ago]
        restocks_month = [m for m in restocks_all if m.created_at and m.created_at >= month_start]

        def investment_summary(movements):
            return sum(m.change * (m.product.cost or 0) for m in movements)

        invest_day = investment_summary(restocks_day)
        invest_week = investment_summary(restocks_week)
        invest_month = investment_summary(restocks_month)
        invest_total = investment_summary(restocks_all)

        # ---- Ganancia (vendido - invertido) ----
        profit_day = revenue_day - invest_day
        profit_week = revenue_week - invest_week
        profit_month = revenue_month - invest_month
        profit_total = revenue_total - invest_total

        reorders_week = ProductMovement.query.filter(
            ProductMovement.movement_type == 'reorder',
            ProductMovement.created_at >= week_ago).count()
        reorders_month = ProductMovement.query.filter(
            ProductMovement.movement_type == 'reorder',
            ProductMovement.created_at >= month_start).count()
        reorders_total = ProductMovement.query.filter_by(movement_type='reorder').count()

        # Flujo de stock reciente (últimos 50 movimientos)
        recent_movements = ProductMovement.query.order_by(
            ProductMovement.created_at.desc()).limit(50).all()

        # Productos más vendidos (por unidades)
        sold_by_product = {}
        for m in sales_all:
            sold_by_product[m.product_id] = sold_by_product.get(m.product_id, 0) + abs(m.change)
        top_sold = sorted(
            [(Product.query.get(pid), units) for pid, units in sold_by_product.items()],
            key=lambda x: x[1], reverse=True)[:5]

        stats = {
            'units_day': units_day, 'revenue_day': revenue_day,
            'units_week': units_week, 'revenue_week': revenue_week,
            'units_month': units_month, 'revenue_month': revenue_month,
            'units_total': units_total, 'revenue_total': revenue_total,
            'current_investment': current_investment,
            'invest_day': invest_day, 'invest_week': invest_week,
            'invest_month': invest_month, 'invest_total': invest_total,
            'profit_day': profit_day, 'profit_week': profit_week,
            'profit_month': profit_month, 'profit_total': profit_total,
            'reorders_week': reorders_week, 'reorders_month': reorders_month,
            'reorders_total': reorders_total,
            'recent_movements': recent_movements,
            'top_sold': top_sold,
        }
        return render_template("inventory.html", products=products,
                               out_of_stock=out_of_stock, low_stock=low_stock,
                               stats=stats)

    @app.route("/inventory/<int:product_id>/edit", methods=["POST"])
    def edit_product(product_id):
        """Editar un producto del inventario."""
        if not session.get("admin_id"): return redirect(url_for("login"))
        product = Product.query.filter_by(id=product_id).first_or_404()

        product.name = sanitize_input(request.form.get("name", product.name))
        product.description = sanitize_input(request.form.get("description", "")) or None
        product.cost = float(request.form.get("cost") or 0)
        product.price = float(request.form.get("price") or 0)
        product.quantity = int(request.form.get("quantity") or 0)
        product.reorder_point = int(request.form.get("reorder_point") or 0)

        uploaded_image = request.files.get("image_file")
        if uploaded_image and uploaded_image.filename:
            product.image_url = save_product_image(uploaded_image)

        db.session.commit()
        upload_backup_to_drive_after_change()
        flash(f"Producto '{product.name}' actualizado", "success")
        return redirect(url_for("inventory"))

    @app.route("/inventory/<int:product_id>/adjust", methods=["POST"])
    def adjust_product_quantity(product_id):
        """Ajustar el stock de un producto (+/-). Registra el movimiento."""
        if not session.get("admin_id"): return redirect(url_for("login"))
        product = Product.query.filter_by(id=product_id).first_or_404()
        delta = int(request.form.get("delta") or 0)
        if delta == 0:
            return redirect(url_for("inventory"))

        old_qty = product.quantity
        product.quantity = max(0, product.quantity + delta)
        actual_change = product.quantity - old_qty
        if actual_change == 0:
            flash(f"No se pudo ajustar '{product.name}' (stock en 0)", "error")
            return redirect(url_for("inventory"))

        movement_type = 'restock' if actual_change > 0 else 'sale'
        db.session.add(ProductMovement(
            product_id=product.id, change=actual_change,
            movement_type=movement_type,
            note='Ajuste rápido' if movement_type == 'restock' else 'Venta'
        ))

        # Detectar punto de reorden: si tras la venta quedó <= reorder_point
        if movement_type == 'sale' and product.quantity <= product.reorder_point:
            db.session.add(ProductMovement(
                product_id=product.id, change=0,
                movement_type='reorder',
                note=f"Alcanzó punto de reorden (quedan {product.quantity}, mínimo {product.reorder_point})"
            ))

        db.session.commit()
        upload_backup_to_drive_after_change()
        flash(f"Stock de '{product.name}' actualizado", "success")
        return redirect(url_for("inventory"))

    @app.route("/inventory/<int:product_id>/sell", methods=["POST"])
    def sell_product(product_id):
        """Registrar una venta de producto (resta stock y guarda el movimiento)."""
        if not session.get("admin_id"): return redirect(url_for("login"))
        product = Product.query.filter_by(id=product_id).first_or_404()
        qty = int(request.form.get("quantity") or 1)
        if qty <= 0:
            flash("Cantidad inválida", "error")
            return redirect(url_for("inventory"))
        qty = min(qty, product.quantity)
        if qty <= 0:
            flash(f"'{product.name}' está agotado", "error")
            return redirect(url_for("inventory"))

        product.quantity -= qty
        db.session.add(ProductMovement(
            product_id=product.id, change=-qty,
            movement_type='sale', note='Venta registrada'
        ))
        if product.quantity <= product.reorder_point:
            db.session.add(ProductMovement(
                product_id=product.id, change=0,
                movement_type='reorder',
                note=f"Alcanzó punto de reorden (quedan {product.quantity}, mínimo {product.reorder_point})"
            ))
        db.session.commit()
        upload_backup_to_drive_after_change()
        flash(f"Venta registrada: {qty} × '{product.name}'", "success")
        return redirect(url_for("inventory"))

    @app.route("/inventory/<int:product_id>/restock", methods=["POST"])
    def restock_product(product_id):
        """Registrar reposición de stock (suma stock y guarda el movimiento)."""
        if not session.get("admin_id"): return redirect(url_for("login"))
        product = Product.query.filter_by(id=product_id).first_or_404()
        qty = int(request.form.get("quantity") or 1)
        if qty <= 0:
            flash("Cantidad inválida", "error")
            return redirect(url_for("inventory"))

        product.quantity += qty
        db.session.add(ProductMovement(
            product_id=product.id, change=qty,
            movement_type='restock', note='Reposición de stock'
        ))
        db.session.commit()
        upload_backup_to_drive_after_change()
        flash(f"Reposición registrada: +{qty} × '{product.name}'", "success")
        return redirect(url_for("inventory"))

    @app.route("/inventory/<int:product_id>/delete", methods=["POST"])
    def delete_product(product_id):
        """Eliminar un producto del inventario."""
        if not session.get("admin_id"): return redirect(url_for("login"))
        product = Product.query.filter_by(id=product_id).first_or_404()
        # Primero eliminar movimientos asociados para evitar error de clave foránea
        ProductMovement.query.filter_by(product_id=product.id).delete()
        db.session.delete(product)
        db.session.commit()
        upload_backup_to_drive_after_change()
        flash(f"Producto '{product.name}' eliminado", "success")
        return redirect(url_for("inventory"))

    @app.route("/qr/<code>")
    def card_qr(code):
        card = Card.query.filter_by(code=code).first_or_404()
        qr_data = f"{get_public_base_url()}/card/{code}"
        img_bytes = generate_qr_image(qr_data)
        return send_file(io.BytesIO(img_bytes), mimetype="image/png")

    @app.route("/card/<code>/add-membership", methods=["POST"])
    def add_membership(code):
        if not session.get("admin_id"): return redirect(url_for("login"))
        card = Card.query.filter_by(code=code).first_or_404()
        membership_type_id = request.form.get("membership_type_id")
        
        if membership_type_id:
            membership = MembershipType.query.get(membership_type_id)
            if membership:
                card.membership_type_id = membership.id
                # Renovar o iniciar vigencia por 30 das desde hoy
                card.membership_expires_at = datetime.utcnow() + timedelta(days=30)
                # Reiniciar contadores de beneficios al asignar/renovar
                card.drinks_redeemed_this_month = 0
                card.grain_bags_redeemed_this_month = 0
                card.last_benefit_reset = datetime.utcnow()
                db.session.commit()
                upload_backup_to_drive_after_change()
                flash(f"Membresa '{membership.name}' asignada. Vence el {card.membership_expires_at.strftime('%d/%m/%Y')}", "success")
            else:
                flash("Tipo de membresa no encontrado", "error")
        else:
            # Si no se selecciona, quitar membresa
            card.membership_type_id = None
            card.membership_expires_at = None
            db.session.commit()
            upload_backup_to_drive_after_change()
            flash("Membresa desvinculada", "info")
            
        return redirect(url_for("view_card", code=code))

    @app.route("/card/<code>/redeem-drink", methods=["POST"])
    def redeem_drink(code):
        if not session.get("admin_id"): return redirect(url_for("login"))
        card = Card.query.filter_by(code=code).first_or_404()
        
        now = datetime.utcnow()
        # Verificar si es un nuevo mes para reiniciar contadores
        if not card.last_benefit_reset or card.last_benefit_reset.month != now.month or card.last_benefit_reset.year != now.year:
            card.drinks_redeemed_this_month = 0
            card.grain_bags_redeemed_this_month = 0
            card.last_benefit_reset = now
            db.session.commit()

        if card.membership_type and card.membership_expires_at and now <= card.membership_expires_at:
            if card.drinks_redeemed_this_month < card.membership_type.drinks_count:
                card.drinks_redeemed_this_month += 1
                card.add_transaction(0, "canje_bebida", f"Bebida mensual ({card.drinks_redeemed_this_month}/{card.membership_type.drinks_count})")
                db.session.commit()
                upload_backup_to_drive_after_change()
                flash(f" Bebida canjeada. Llevas {card.drinks_redeemed_this_month} de {card.membership_type.drinks_count} este mes.", "success")
            else:
                flash(" Has alcanzado el lmite de bebidas mensuales de tu membresa.", "error")
        else:
            flash(" No tienes una membresa activa que incluya bebidas.", "error")
            
        return redirect(url_for("view_card", code=code))

    @app.route("/card/<code>/redeem-grain", methods=["POST"])
    def redeem_grain(code):
        if not session.get("admin_id"): return redirect(url_for("login"))
        card = Card.query.filter_by(code=code).first_or_404()
        
        now = datetime.utcnow()
        # Verificar si es un nuevo mes para reiniciar contadores
        if not card.last_benefit_reset or card.last_benefit_reset.month != now.month or card.last_benefit_reset.year != now.year:
            card.drinks_redeemed_this_month = 0
            card.grain_bags_redeemed_this_month = 0
            card.last_benefit_reset = now
            db.session.commit()

        if card.membership_type and card.membership_expires_at and now <= card.membership_expires_at:
            if card.grain_bags_redeemed_this_month < card.membership_type.grain_bags:
                card.grain_bags_redeemed_this_month += 1
                card.add_transaction(0, "canje_grano", f"Bolsa de grano mensual ({card.grain_bags_redeemed_this_month}/{card.membership_type.grain_bags})")
                db.session.commit()
                upload_backup_to_drive_after_change()
                flash(f" Bolsa de grano canjeada. Llevas {card.grain_bags_redeemed_this_month} de {card.membership_type.grain_bags} este mes.", "success")
            else:
                flash(" Has alcanzado el lmite de bolsas de grano de tu membresa.", "error")
        else:
            flash(" No tienes una membresa activa que incluya bolsas de grano.", "error")
            
        return redirect(url_for("view_card", code=code))

    @app.route("/card/<code>/transactions")
    def card_transactions(code):
        card = Card.query.filter_by(code=code).first_or_404()
        transactions = Transaction.query.filter_by(card_id=card.id).order_by(Transaction.created_at.desc()).all()
        return render_template("transactions.html", card=card, transactions=transactions)

    @app.route("/export")
    def export_data():
        if not session.get("admin_id"): return redirect(url_for("login"))
        cards = Card.query.order_by(Card.created_at.desc()).all()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Codigo", "Cliente", "Telefono", "Saldo", "Activo", "Fecha Creacion"])
        for card in cards:
            writer.writerow([card.code, card.client_name, card.phone, card.balance, card.is_active, card.created_at.strftime("%Y-%m-%d %H:%M")])
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment;filename=tarjetas_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"}
        )

    @app.route("/scan/<code>")
    def scan_card(code):
        card = Card.query.filter_by(code=code).first_or_404()
        is_admin = session.get("admin_id")
        transactions = Transaction.query.filter_by(card_id=card.id).order_by(Transaction.created_at.desc()).limit(5).all() if is_admin else []
        qr_image_url = url_for("card_qr", code=code, _external=True)
        return render_template("scan_results.html", card=card, is_admin=is_admin, transactions=transactions, qr_image_url=qr_image_url)

    @app.route("/api/card/<code>/balance")
    def api_get_balance(code):
        card = Card.query.filter_by(code=code).first_or_404()
        return jsonify({"balance": float(card.balance)})

    @app.route("/api/card/<code>/transactions")
    def api_get_transactions(code):
        card = Card.query.filter_by(code=code).first_or_404()
        transactions = Transaction.query.filter_by(card_id=card.id).order_by(Transaction.created_at.desc()).limit(10).all()
        return jsonify({"transactions": [{"created_at": tx.created_at.strftime("%d/%m %H:%M"), "type": tx.type, "amount": f"${tx.amount:.2f}"} for tx in transactions]})

    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("500.html"), 500

    # ===== GOOGLE DRIVE (respaldo en la nube) =====
    @app.route("/drive/auth")
    def drive_auth():
        """Inicia el flujo OAuth para conectar Google Drive."""
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not session.get("is_superuser"):
            flash("Solo el superusuario puede conectar Google Drive", "error")
            return redirect(url_for("backup"))
        if not drive_backup.is_configured():
            flash("Google Drive no está configurado. Faltan GOOGLE_DRIVE_CLIENT_ID y GOOGLE_DRIVE_CLIENT_SECRET en las variables de entorno.", "error")
            return redirect(url_for("backup"))
        flow = drive_backup.build_flow(base_url=get_public_base_url())
        if not flow:
            flash("No se pudo iniciar la conexión con Google Drive.", "error")
            return redirect(url_for("backup"))
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        drive_backup.record_drive_error("")
        session["drive_oauth_state"] = state
        # PKCE: guardar el code_verifier para que el callback pueda completar
        # el intercambio del código (Google lo exige para validar la solicitud).
        session["drive_oauth_code_verifier"] = flow.code_verifier
        return redirect(authorization_url)

    @app.route("/drive/callback")
    def drive_callback():
        """Recibe el código de autorización de Google y guarda el token."""
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not session.get("is_superuser"):
            flash("Solo el superusuario puede conectar Google Drive", "error")
            return redirect(url_for("backup"))
        state = session.get("drive_oauth_state")
        if not state or request.args.get("state") != state:
            drive_backup.record_drive_error(
                "Error de seguridad: el estado de la sesión no coincide. "
                "Esto pasa si SECRET_KEY no está definida en Render (la sesión se pierde "
                "entre /drive/auth y /drive/callback)."
            )
            logger.error("Error en callback de Drive: estado de sesión no coincide (posible SECRET_KEY no definida)")
            flash("Error de seguridad en la conexión con Google Drive. Intenta de nuevo.", "error")
            return redirect(url_for("backup"))
        flow = drive_backup.build_flow(base_url=get_public_base_url())
        if not flow:
            drive_backup.record_drive_error("Google Drive no está configurado (faltan GOOGLE_DRIVE_CLIENT_ID/SECRET).")
            flash("Google Drive no está configurado.", "error")
            return redirect(url_for("backup"))
        # PKCE: restaurar el code_verifier guardado en /drive/auth. Sin esto,
        # Google rechaza el intercambio del código (invalid_grant).
        flow.code_verifier = session.get("drive_oauth_code_verifier")
        try:
            flow.fetch_token(authorization_response=request.url)
            credentials = flow.credentials
            if drive_backup.save_credentials_to_db(credentials):
                drive_backup.record_drive_error("")
                flash("✅ Google Drive conectado correctamente. Los respaldos se subirán automáticamente.", "success")
            else:
                drive_backup.record_drive_error(
                    "No se pudo guardar el token en la base de datos (tabla app_settings). "
                    "Revisa los logs de Render para ver '[Drive] Error guardando token'."
                )
                flash("No se pudo guardar la conexión con Google Drive.", "error")
        except Exception as e:
            drive_backup.record_drive_error(f"Error en fetch_token: {e}")
            logger.error(f"Error en callback de Drive: {e}")
            flash(f"Error conectando con Google Drive: {e}", "error")
        session.pop("drive_oauth_state", None)
        session.pop("drive_oauth_code_verifier", None)
        return redirect(url_for("backup"))

    @app.route("/drive/disconnect", methods=["POST"])
    def drive_disconnect():
        """Desconecta Google Drive (borra el token guardado)."""
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not session.get("is_superuser"):
            flash("Solo el superusuario puede desconectar Google Drive", "error")
            return redirect(url_for("backup"))
        try:
            row = AppSetting.query.filter_by(key="drive_token").first()
            if row:
                db.session.delete(row)
                db.session.commit()
            flash("Google Drive desconectado.", "success")
        except Exception as e:
            logger.error(f"Error desconectando Drive: {e}")
            flash("Error al desconectar Google Drive.", "error")
        return redirect(url_for("backup"))

    @app.route("/drive/test", methods=["POST"])
    def drive_test():
        """Sube un respaldo de prueba a Google Drive para verificar la conexión."""
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not session.get("is_superuser"):
            flash("Solo el superusuario puede probar Google Drive", "error")
            return redirect(url_for("backup"))
        if not drive_backup.is_connected():
            flash("Google Drive no está conectado. Pulsa 'Conectar con Google Drive'.", "error")
            return redirect(url_for("backup"))
        # Generar un respaldo de prueba
        test_data = {
            "type": "test",
            "message": "Prueba de conexión con Google Drive",
            "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        }
        success, msg = drive_backup.upload_backup_to_drive(test_data, filename="test_connection.json")
        if success:
            flash(f"✅ {msg}", "success")
        else:
            flash(f"❌ {msg}", "error")
        return redirect(url_for("backup"))

    @app.route("/drive/token")
    def drive_token():
        """Muestra el token OAuth actual para copiarlo a GOOGLE_DRIVE_TOKEN en Render."""
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not session.get("is_superuser"):
            flash("Solo el superusuario puede ver el token", "error")
            return redirect(url_for("backup"))
        token_json = drive_backup.get_token_json()
        return render_template("drive_token.html", token_json=token_json)

    @app.route("/drive/diagnostico")
    def drive_diagnostico():
        """Muestra el estado de configuración de Google Drive para depurar."""
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not session.get("is_superuser"):
            flash("Solo el superusuario puede ver el diagnóstico", "error")
            return redirect(url_for("backup"))
        client_config = drive_backup.get_client_config()
        redirect_uri = drive_backup.get_redirect_uri(base_url=get_public_base_url())
        info = {
            "client_id_configurado": bool(os.environ.get("GOOGLE_DRIVE_CLIENT_ID")),
            "client_secret_configurado": bool(os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET")),
            "client_id": (os.environ.get("GOOGLE_DRIVE_CLIENT_ID", "")[:20] + "...") if os.environ.get("GOOGLE_DRIVE_CLIENT_ID") else "(vacío)",
            "public_base_url": get_public_base_url(),
            "redirect_uri_que_debe_registrarse": redirect_uri,
            "redirect_uri_en_codigo": client_config["web"]["redirect_uris"][0] if client_config else "(sin config)",
            "token_guardado": bool(drive_backup.get_credentials_from_db()),
            "token_env_configurado": bool(os.environ.get("GOOGLE_DRIVE_TOKEN")),
            "conectado": drive_backup.is_connected(),
            "last_error": drive_backup.get_last_drive_error(),
        }
        return render_template("drive_diagnostico.html", info=info)

    # ===== COMPRA DE MEMBRESÍAS EN LÍNEA (PayPal) =====
    def paypal_get_access_token():
        """Obtiene el token de acceso OAuth2 de PayPal."""
        if not HAS_PAYPAL:
            return None
        try:
            resp = http_requests.post(
                f"{PAYPAL_BASE}/v1/oauth2/token",
                auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
                data={"grant_type": "client_credentials"},
                headers={"Accept": "application/json", "Accept-Language": "en_US"},
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json().get("access_token")
            logger.error(f"PayPal token error {resp.status_code}: {resp.text}")
            return None
        except Exception as e:
            logger.error(f"PayPal token exception: {e}")
            return None

    def create_card_from_purchase(purchase):
        """Crea la tarjeta con QR único cuando se aprueba el pago de una membresía."""
        code = generate_unique_code()
        card = Card(
            code=code,
            client_name=purchase.client_name,
            phone=purchase.phone,
            balance=0,
            custom_message="¡Bienvenido a Júbilo! Disfruta de tu membresía.",
            is_active=True,
            membership_type_id=purchase.membership_type_id,
            membership_expires_at=datetime.utcnow() + timedelta(days=30),
        )
        db.session.add(card)
        db.session.flush()
        card.add_transaction(0, "carga", "Alta por compra de membresía en línea")
        purchase.status = "paid"
        purchase.card_code = code
        purchase.paid_at = datetime.utcnow()
        db.session.commit()
        upload_backup_to_drive_after_change()
        return card

    @app.route("/comprar", methods=["POST"])
    def comprar_submit():
        """Crea la solicitud de compra y redirige a PayPal Checkout."""
        client_name = sanitize_input(request.form.get("client_name", ""))
        phone = sanitize_input(request.form.get("phone", ""))
        membership_type_id = request.form.get("membership_type_id")
        if not client_name or not phone or not membership_type_id:
            flash("Completa todos los campos", "error")
            return redirect(url_for("planes"))
        membership = MembershipType.query.get(membership_type_id)
        if not membership or not membership.is_active:
            flash("Plan no válido", "error")
            return redirect(url_for("planes"))
        if not HAS_PAYPAL:
            flash("El pago en línea aún no está configurado.", "error")
            return redirect(url_for("planes"))

        purchase = PurchaseRequest(
            client_name=client_name,
            phone=phone,
            membership_type_id=membership.id,
            status="pending",
            amount=membership.price,
        )
        db.session.add(purchase)
        db.session.commit()

        access_token = paypal_get_access_token()
        if not access_token:
            purchase.status = "failed"
            db.session.commit()
            flash("Error al iniciar el pago. Intenta de nuevo.", "error")
            return redirect(url_for("planes"))

        try:
            # Crear la orden de pago en PayPal
            order_payload = {
                "intent": "CAPTURE",
                "purchase_units": [{
                    "reference_id": str(purchase.id),
                    "description": f"Membresía {membership.name}",
                    "amount": {
                        "currency_code": PAYPAL_CURRENCY,
                        "value": f"{membership.price:.2f}",
                    },
                }],
                "application_context": {
                    "brand_name": "Júbilo",
                    "user_action": "PAY_NOW",
                    "return_url": f"{get_public_base_url()}/comprar/confirmacion?purchase_id={purchase.id}",
                    "cancel_url": f"{get_public_base_url()}/planes?cancelado=1",
                },
            }
            resp = http_requests.post(
                f"{PAYPAL_BASE}/v2/checkout/orders",
                json=order_payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            if resp.status_code not in (200, 201):
                logger.error(f"PayPal order error {resp.status_code}: {resp.text}")
                purchase.status = "failed"
                db.session.commit()
                flash("Error al iniciar el pago. Intenta de nuevo.", "error")
                return redirect(url_for("planes"))
            order = resp.json()
            purchase.stripe_session_id = order["id"]  # reutilizamos el campo para el id de la orden PayPal
            db.session.commit()
            # Buscar el link de aprobación
            approve_url = next((l["href"] for l in order.get("links", []) if l.get("rel") == "approve"), None)
            if not approve_url:
                purchase.status = "failed"
                db.session.commit()
                flash("Error al iniciar el pago. Intenta de nuevo.", "error")
                return redirect(url_for("planes"))
            return redirect(approve_url)
        except Exception as e:
            logger.error(f"Error creando orden PayPal: {e}")
            purchase.status = "failed"
            db.session.commit()
            flash("Error al iniciar el pago. Intenta de nuevo.", "error")
            return redirect(url_for("planes"))

    @app.route("/comprar/confirmacion")
    def comprar_confirmacion():
        """PayPal redirige aquí tras aprobar el pago. Captura la orden y crea la tarjeta."""
        purchase_id = request.args.get("purchase_id")
        token = request.args.get("token")  # id de la orden PayPal
        if not purchase_id:
            return redirect(url_for("planes"))
        purchase = PurchaseRequest.query.get(purchase_id)
        if not purchase:
            return redirect(url_for("planes"))
        if purchase.status == "paid":
            return redirect(url_for("comprar_estado", phone=purchase.phone))
        if not token:
            flash("No se pudo confirmar el pago.", "error")
            return redirect(url_for("planes"))

        access_token = paypal_get_access_token()
        if not access_token:
            flash("Error al confirmar el pago. Intenta de nuevo.", "error")
            return redirect(url_for("planes"))
        try:
            # Capturar la orden aprobada
            resp = http_requests.post(
                f"{PAYPAL_BASE}/v2/checkout/orders/{token}/capture",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            if resp.status_code in (200, 201):
                result = resp.json()
                if result.get("status") == "COMPLETED" and purchase.status != "paid":
                    create_card_from_purchase(purchase)
                return redirect(url_for("comprar_estado", phone=purchase.phone))
            logger.error(f"PayPal capture error {resp.status_code}: {resp.text}")
            flash("El pago no se pudo completar. Intenta de nuevo.", "error")
            return redirect(url_for("planes"))
        except Exception as e:
            logger.error(f"Error capturando orden PayPal: {e}")
            flash("Error al confirmar el pago. Intenta de nuevo.", "error")
            return redirect(url_for("planes"))

    @app.route("/comprar/webhook", methods=["POST"])
    def comprar_webhook():
        """Webhook de PayPal (confirmación fiable del pago)."""
        # PayPal envía el evento; verificamos el id de la orden en el body
        try:
            data = request.get_json(force=True)
        except Exception:
            return "invalid payload", 400
        event_type = data.get("event_type", "")
        if event_type == "CHECKOUT.ORDER.APPROVED":
            resource = data.get("resource", {})
            order_id = resource.get("id")
            purchase = PurchaseRequest.query.filter_by(stripe_session_id=order_id).first()
            if purchase and purchase.status != "paid":
                # Intentar capturar la orden
                access_token = paypal_get_access_token()
                if access_token:
                    try:
                        resp = http_requests.post(
                            f"{PAYPAL_BASE}/v2/checkout/orders/{order_id}/capture",
                            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                            timeout=30,
                        )
                        if resp.status_code in (200, 201) and resp.json().get("status") == "COMPLETED":
                            create_card_from_purchase(purchase)
                    except Exception as e:
                        logger.error(f"PayPal webhook capture error: {e}")
        return "ok", 200

    @app.route("/comprar/estado", methods=["GET", "POST"])
    def comprar_estado():
        """Página pública: el cliente consulta su compra con su teléfono."""
        phone = sanitize_input(request.form.get("phone", request.args.get("phone", "")))
        result = None
        if phone:
            purchases = PurchaseRequest.query.filter_by(phone=phone).order_by(PurchaseRequest.created_at.desc()).all()
            paid = next((p for p in purchases if p.status == "paid" and p.card_code), None)
            if paid:
                card = Card.query.filter_by(code=paid.card_code).first()
                result = {"purchase": paid, "card": card}
            elif purchases:
                result = {"purchase": purchases[0], "card": None}
        return render_template("comprar_estado.html", phone=phone, result=result)

    @app.route("/compras")
    def compras():
        """Lista de compras de membresías (solo superusuario)."""
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not session.get("is_superuser"):
            flash("Solo el superusuario puede ver las compras", "error")
            return redirect(url_for("dashboard"))
        purchases = PurchaseRequest.query.order_by(PurchaseRequest.created_at.desc()).all()
        return render_template("compras.html", purchases=purchases)

    # ===== BACKUP (solo superusuarios) =====
    @app.route("/backup")
    def backup():
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not session.get("is_superuser"):
            flash("Solo el superusuario puede acceder a los respaldos", "error")
            return redirect(url_for("dashboard"))
        
        # Estadsticas actuales
        stats = {
            "cards": Card.query.count(),
            "transactions": Transaction.query.count(),
            "notifications": Notification.query.count(),
            "admins": Admin.query.count(),
            "memberships": MembershipType.query.count(),
            "products": Product.query.count(),
            "movements": ProductMovement.query.count()
        }
        drive_status = {
            "configured": drive_backup.is_configured(),
            "connected": drive_backup.is_connected(),
        }
        # Configuración de backup automático a Drive
        auto_setting = AppSetting.query.filter_by(key="drive_auto_backup").first()
        interval_setting = AppSetting.query.filter_by(key="drive_auto_backup_interval").first()
        drive_auto_enabled = bool(auto_setting and auto_setting.value == "true")
        drive_auto_interval = int(interval_setting.value) if interval_setting else 3600
        return render_template("backup.html", stats=stats, drive_status=drive_status,
                               drive_auto_enabled=drive_auto_enabled, drive_auto_interval=drive_auto_interval)

    @app.route("/backup/export")
    def export_backup():
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not session.get("is_superuser"):
            return jsonify({"error": "No autorizado"}), 403
        
        cards = Card.query.all()
        # Incluir tipos de membresa en el backup
        membership_types = MembershipType.query.all()
        memberships_data = []
        for m in membership_types:
            # Convertir imgenes a Base64 para guardarlas en el JSON
            badge_icon_b64 = None
            bg_image_b64 = None
            
            if m.badge_icon:
                try:
                    icon_path = os.path.join(base_dir, m.badge_icon.lstrip('/'))
                    if os.path.exists(icon_path):
                        with open(icon_path, "rb") as f:
                            badge_icon_b64 = base64.b64encode(f.read()).decode('utf-8')
                except: pass
                
            if m.background_image:
                try:
                    bg_path = os.path.join(base_dir, m.background_image.lstrip('/'))
                    if os.path.exists(bg_path):
                        with open(bg_path, "rb") as f:
                            bg_image_b64 = base64.b64encode(f.read()).decode('utf-8')
                except: pass

            memberships_data.append({
                "id": m.id,
                "name": m.name,
                "description": m.description,
                "price": m.price,
                "cashback_percent": m.cashback_percent,
                "discount_percent": m.discount_percent,
                "drinks_count": m.drinks_count,
                "grain_bags": m.grain_bags,
                "badge_icon": m.badge_icon,
                "badge_icon_b64": badge_icon_b64,
                "badge_color": m.badge_color,
                "background_image": m.background_image,
                "background_image_b64": bg_image_b64,
                "is_active": m.is_active
            })

        cards_data = [{
            "code": c.code, "client_name": c.client_name, "phone": c.phone,
            "balance": float(c.balance), "is_active": c.is_active,
            "custom_message": c.custom_message,
            "membership_type_id": c.membership_type_id,
            "membership_expires_at": c.membership_expires_at.strftime("%Y-%m-%d %H:%M:%S") if c.membership_expires_at else None,
            "drinks_redeemed_this_month": c.drinks_redeemed_this_month,
            "grain_bags_redeemed_this_month": c.grain_bags_redeemed_this_month,
            "last_benefit_reset": c.last_benefit_reset.strftime("%Y-%m-%d %H:%M:%S") if c.last_benefit_reset else None,
            "created_at": c.created_at.strftime("%Y-%m-%d %H:%M:%S") if c.created_at else None
        } for c in cards]
        
        transactions = Transaction.query.all()
        transactions_data = []
        for t in transactions:
            card = Card.query.filter_by(id=t.card_id).first()
            transactions_data.append({
                "card_code": card.code if card else None,
                "type": t.type, "amount": float(t.amount), "note": t.note,
                "created_at": t.created_at.strftime("%Y-%m-%d %H:%M:%S") if t.created_at else None
            })
        
        notifications = Notification.query.all()
        notifications_data = [{
            "title": n.title, "message": n.message,
            "image_url": n.image_url,
            "link_url": n.link_url,
            "created_at": n.created_at.strftime("%Y-%m-%d %H:%M:%S") if n.created_at else None
        } for n in notifications]
        
        # Incluir usuarios (Admin) en el backup
        admins = Admin.query.all()
        admins_data = [{
            "username": a.username,
            "password_hash": a.password_hash,
            "is_superuser": a.is_superuser,
            "is_marketing": a.is_marketing,
            "created_at": a.created_at.strftime("%Y-%m-%d %H:%M:%S") if a.created_at else None,
            "last_login": a.last_login.strftime("%Y-%m-%d %H:%M:%S") if a.last_login else None
        } for a in admins]

        # Incluir productos del inventario en el backup
        products = Product.query.all()
        products_data = [{
            "id": p.id,
            "name": p.name, "description": p.description,
            "cost": float(p.cost), "price": float(p.price),
            "quantity": p.quantity, "reorder_point": p.reorder_point,
            "image_url": p.image_url,
            "created_at": p.created_at.strftime("%Y-%m-%d %H:%M:%S") if p.created_at else None,
            "updated_at": p.updated_at.strftime("%Y-%m-%d %H:%M:%S") if p.updated_at else None
        } for p in products]

        # Incluir movimientos de stock en el backup
        movements = ProductMovement.query.all()
        movements_data = [{
            "id": m.id,
            "product_id": m.product_id,
            "change": m.change,
            "movement_type": m.movement_type,
            "note": m.note,
            "created_at": m.created_at.strftime("%Y-%m-%d %H:%M:%S") if m.created_at else None
        } for m in movements]

        backup_data = {
            "version": "1.3",
            "exported_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "cards": cards_data, "transactions": transactions_data,
            "notifications": notifications_data, "admins": admins_data,
            "membership_types": memberships_data,
            "products": products_data, "product_movements": movements_data
        }
        
        return Response(
            json.dumps(backup_data, indent=2, ensure_ascii=False),
            mimetype="application/json",
            headers={"Content-Disposition": f"attachment;filename=jubilo_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"}
        )

    @app.route("/backup/import", methods=["POST"])
    def import_backup():
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not session.get("is_superuser"):
            flash("Solo el superusuario puede importar respaldos", "error")
            return redirect(url_for("backup"))
        
        if "file" not in request.files:
            flash("No se selecciono archivo", "error")
            return redirect(url_for("backup"))
        
        file = request.files["file"]
        if file.filename == "":
            flash("No se selecciono archivo", "error")
            return redirect(url_for("backup"))
        
        try:
            content = file.read().decode("utf-8")
            data = json.loads(content)
            
            if "version" not in data or "cards" not in data:
                flash("Archivo de respaldo invalido", "error")
                return redirect(url_for("backup"))
            
            summary = restore_backup_data(data)
            upload_backup_to_drive_after_change()
            flash(f"Respaldo importado: {summary['cards']} tarjetas, {summary['transactions']} transacciones, {summary['notifications']} notificaciones, {summary['admins']} usuarios, {summary['products']} productos, {summary['movements']} movimientos", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Error al importar: {str(e)}", "error")
        
        return redirect(url_for("backup"))

    @app.route("/backup/drive/upload", methods=["POST"])
    def backup_drive_upload():
        """Sube un respaldo manual a Google Drive."""
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not session.get("is_superuser"):
            flash("Solo el superusuario puede usar Google Drive", "error")
            return redirect(url_for("backup"))
        if not drive_backup.is_configured() or not drive_backup.is_connected():
            flash("Google Drive no está conectado.", "error")
            return redirect(url_for("backup"))
        success, msg = upload_backup_to_drive_manual()
        if success:
            flash(f"✅ {msg}", "success")
        else:
            flash(f"❌ {msg}", "error")
        return redirect(url_for("backup"))

    @app.route("/backup/drive/auto-toggle", methods=["POST"])
    def backup_drive_auto_toggle():
        """Activa o desactiva el backup automático a Google Drive."""
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not session.get("is_superuser"):
            flash("Solo el superusuario puede cambiar esta configuración", "error")
            return redirect(url_for("backup"))
        enabled = request.form.get("enabled") == "true"
        setting = AppSetting.query.filter_by(key="drive_auto_backup").first()
        if setting:
            setting.value = "true" if enabled else "false"
        else:
            db.session.add(AppSetting(key="drive_auto_backup", value="true" if enabled else "false"))
        db.session.commit()
        flash("Backup automático activado ✅" if enabled else "Backup automático desactivado ⏸️", "success")
        return redirect(url_for("backup"))

    @app.route("/backup/drive/interval", methods=["POST"])
    def backup_drive_interval():
        """Cambia el intervalo del backup automático a Google Drive."""
        if not session.get("admin_id"): return redirect(url_for("login"))
        if not session.get("is_superuser"):
            flash("Solo el superusuario puede cambiar esta configuración", "error")
            return redirect(url_for("backup"))
        interval = request.form.get("interval", "3600")
        try:
            interval = str(max(60, int(interval)))  # mínimo 60 segundos
        except (ValueError, TypeError):
            interval = "3600"
        setting = AppSetting.query.filter_by(key="drive_auto_backup_interval").first()
        if setting:
            setting.value = interval
        else:
            db.session.add(AppSetting(key="drive_auto_backup_interval", value=interval))
        db.session.commit()
        flash(f"Intervalo de backup automático actualizado a {interval} segundos.", "success")
        return redirect(url_for("backup"))

    return app

if __name__ == "__main__":
    # Detectar si estamos en produccin (Render/Heroku) o desarrollo local
    port = int(os.environ.get("PORT", os.environ.get("FLASK_PORT", 5000)))
    is_production = "PORT" in os.environ  # Render/Heroku siempre definen PORT
    app = create_app()
    print(f"\n{'='*60}")
    print(f"  SERVIDOR INICIADO {'(PRODUCCIN)' if is_production else '(DESARROLLO)'}")
    print(f"{'='*60}")
    if app.config.get("PUBLIC_BASE_URL_IS_FALLBACK"):
        print("  URL pblica: derivada automticamente de cada request (ProxyFix)")
    else:
        print(f"  URL pblica: {app.config['PUBLIC_BASE_URL']}")
    print(f"  URL local:   http://127.0.0.1:{port}")
    print(f"{'='*60}\n")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# Instancia global de la app para gunicorn (produccin)
# Esta lnea es necesaria para que Render/gunicorn pueda encontrar la app
app = create_app()



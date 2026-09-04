"""Módulo de utilidades para Júbilo Alibaba Version v2.0"""

import os
import json
import logging
import hashlib
import base64
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import qrcode
from PIL import Image, ImageDraw, ImageFont
from flask import current_app


# -----------------------------------------------------------------------------
# Sistema de Logs
# -----------------------------------------------------------------------------

def setup_logging(app=None):
    """Configura sistema de logging"""
    log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    
    logger = logging.getLogger('jubilo')
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        file_handler = logging.FileHandler(
            os.path.join(log_dir, f'app_{datetime.now().strftime("%Y%m%d")}.log'),
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger


_logger = None


def get_logger():
    """Obtiene instancia del logger"""
    global _logger
    if _logger is None:
        _logger = setup_logging()
    return _logger


# -----------------------------------------------------------------------------
# Cache de QRs
# -----------------------------------------------------------------------------

class QRCache:
    """Cache en memoria para QRs generados"""
    
    def __init__(self):
        self._cache: Dict[str, bytes] = {}
        self._expiry: Dict[str, datetime] = {}
        self._max_age = 3600
    
    def get(self, key: str) -> Optional[bytes]:
        if key in self._cache and self._expiry.get(key):
            if datetime.utcnow() < self._expiry[key]:
                return self._cache[key]
        return None
    
    def set(self, key: str, value: bytes) -> None:
        self._cache[key] = value
        self._expiry[key] = datetime.utcnow() + timedelta(seconds=self._max_age)
    
    def clear(self) -> None:
        self._cache.clear()
        self._expiry.clear()
    
    def cleanup_expired(self) -> int:
        now = datetime.utcnow()
        expired_keys = [k for k, d in self._expiry.items() if d <= now]
        for key in expired_keys:
            self._cache.pop(key, None)
            self._expiry.pop(key, None)
        return len(expired_keys)


qr_cache = QRCache()


# -----------------------------------------------------------------------------
# Generación de QR Codes
# -----------------------------------------------------------------------------

def generate_qr_image(data: str, size: int = 256) -> bytes:
    """Genera imagen QR codificando datos proporcionados"""
    try:
        cache_key = hashlib.md5(data.encode()).hexdigest()
        cached = qr_cache.get(cache_key)
        if cached:
            get_logger().info(f"QR servido desde cache: {cache_key[:8]}...")
            return cached
        
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="#3E2723", back_color="#D7CCC8")
        img = img.convert('RGBA')
        
        # Aplicar esquinas redondeadas
        mask = Image.new('L', img.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle([0, 0, img.size[0], img.size[1]], radius=20, fill=255)
        img.putalpha(mask)
        
        import io
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        img_bytes = buffer.getvalue()
        
        qr_cache.set(cache_key, img_bytes)
        get_logger().info(f"QR generado nuevo: {cache_key[:8]}...")
        return img_bytes
    
    except Exception as e:
        get_logger().error(f"Error generando QR: {e}")
        raise


def generate_deep_link(base_url: str, card_code: str, token: str) -> str:
    """Genera URL profunda segura para acceso al QR"""
    encoded = base64.urlsafe_b64encode(
        json.dumps({'c': card_code, 't': token}).encode()
    ).decode().rstrip('=')
    return f"{base_url}/scan/{encoded}"


# -----------------------------------------------------------------------------
# Utilidades Varias
# -----------------------------------------------------------------------------

def sanitize_input(text: str) -> str:
    """Limpia input de caracteres peligrosos"""
    if not text:
        return ""
    import html
    return html.escape(str(text).strip())[:500]


def format_currency(amount: float, currency: str = '$') -> str:
    """Formatea monto como moneda"""
    return f"{currency}{amount:,.2f}"


def clear_qr_cache() -> int:
    """Función pública para limpiar cache de QRs"""
    count = qr_cache.cleanup_expired()
    get_logger().info(f"Caché limpiado: {count} entradas eliminadas")
    return count


# -----------------------------------------------------------------------------
# Generación de Imagen Personalizada para WhatsApp
# -----------------------------------------------------------------------------

def _get_font(size: int, bold: bool = False):
    """Carga fuente TrueType desde la carpeta fonts/ del proyecto. Usa fallback si no existe."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    font_dir = os.path.join(base_dir, 'fonts')
    
    # Lista de archivos de fuente disponibles en la carpeta fonts/
    try:
        if os.path.exists(font_dir):
            available_fonts = [f for f in os.listdir(font_dir) if f.lower().endswith('.ttf')]
        else:
            available_fonts = []
    except Exception:
        available_fonts = []
    
    # Intentar cargar ARIALUNI.TTF (negrita) o ARIALN.TTF (regular)
    target = None
    if bold:
        for f in available_fonts:
            if 'arialuni' in f.lower() or 'arialbd' in f.lower() or 'bold' in f.lower():
                target = f
                break
    else:
        for f in available_fonts:
            if 'arialn' in f.lower() or 'arial' in f.lower() and 'bold' not in f.lower():
                target = f
                break
    
    # Si no se encontró una específica, usar la primera disponible
    if not target and available_fonts:
        target = available_fonts[0]
    
    # Intentar cargar la fuente encontrada
    if target:
        try:
            font_path = os.path.join(font_dir, target)
            return ImageFont.truetype(font_path, size)
        except Exception:
            pass
    
    # Fallback: usar fuente del sistema (si existe) o la fuente por defecto de Pillow
    system_fonts = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        '/System/Library/Fonts/Helvetica.ttc',
        'arial.ttf',
    ]
    for sys_font in system_fonts:
        try:
            return ImageFont.truetype(sys_font, size)
        except Exception:
            continue
    
    # Último recurso: fuente por defecto de Pillow
    try:
        return ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()


def generate_whatsapp_image(client_name: str, qr_data: str, custom_message: str = None, balance: float = None, membership: dict = None, expires_at: str = None) -> bytes:
    """
    Genera imagen vertical (800x1200) replicando fielmente card.html usando Pillow.
    """
    try:
        import io
        width = 800
        height = 1200
        
        img = Image.new('RGB', (width, height), (62, 39, 35))
        draw = ImageDraw.Draw(img)
        
        # --- CARGA DE FUENTES ROBUSTA ---
        try:
            font_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')
            files = [f for f in os.listdir(font_dir) if f.lower().endswith('.ttf')] if os.path.exists(font_dir) else []
            
            def get_font(size, bold=False):
                target = None
                if bold:
                    for f in files:
                        if 'bold' in f.lower() or '-b' in f.lower() or '_b' in f.lower():
                            target = f; break
                else:
                    for f in files:
                        if 'regular' in f.lower() or 'arial' in f.lower():
                            target = f; break
                if not target and files: target = files[0]
                if not target: raise FileNotFoundError("No fonts")
                return ImageFont.truetype(os.path.join(font_dir, target), size)

            title_font = get_font(32, bold=True)   # Nombre membresía (pequeño)
            name_font = get_font(55, bold=True)    # Nombre cliente
            balance_font = get_font(70, bold=True) # Saldo
            message_font = get_font(32)            # Mensaje personalizado
            small_font = get_font(26)              # Vencimiento / Footer
            benefit_font = get_font(28)            # Beneficios
            benefit_title_font = get_font(30, bold=True) # Título beneficios
        except Exception as e:
            get_logger().warning(f"Fuentes fallback: {e}")
            title_font = name_font = balance_font = message_font = small_font = benefit_font = benefit_title_font = ImageFont.load_default()

        # --- 1. FONDO ---
        bg_path = None
        if membership and membership.get('background_image'):
            base_dir = os.path.dirname(os.path.abspath(__file__))
            potential_path = os.path.join(base_dir, membership['background_image'].lstrip('/'))
            if os.path.exists(potential_path): bg_path = potential_path
        if not bg_path:
            bg_path = os.path.join(os.path.dirname(__file__), 'ImgenQr', 'Fondo.png')

        if os.path.exists(bg_path):
            try:
                bg_img = Image.open(bg_path).convert('RGB').resize((width, height), Image.Resampling.LANCZOS)
                img.paste(bg_img, (0, 0))
                draw = ImageDraw.Draw(img)
            except: pass
        else:
            for y in range(height):
                r = int(62 - (62-26)*y/height); g = int(39 - (39-18)*y/height); b = int(35 - (35-16)*y/height)
                draw.line([(0,y),(width,y)], fill=(r,g,b))

        badge_color_hex = membership.get('badge_color', '#FFD700') if membership else '#FFD700'
        try: badge_color_rgb = tuple(int(badge_color_hex.lstrip('#')[i:i+2], 16) for i in (0,2,4))
        except: badge_color_rgb = (255, 215, 0)
        cream_color = (245, 233, 216)

        y_cursor = 50

        # --- 2. ICONO MEMBRESÍA (80px, borde 4px) ---
        badge_size = 80
        border_w = 4
        if membership and membership.get('badge_icon'):
            try:
                icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), membership['badge_icon'].lstrip('/'))
                if os.path.exists(icon_path):
                    icon = Image.open(icon_path).convert('RGBA').resize((badge_size, badge_size), Image.Resampling.LANCZOS)
                    mask = Image.new('L', (badge_size, badge_size), 0)
                    ImageDraw.Draw(mask).ellipse((0,0,badge_size,badge_size), fill=255)
                    out = Image.new('RGBA', (badge_size, badge_size), (0,0,0,0))
                    out.paste(icon, (0,0), mask)
                    img.paste(out, ((width-badge_size)//2, y_cursor), out)
            except: pass
        else:
            draw.ellipse([(width-badge_size)//2, y_cursor, (width+badge_size)//2, y_cursor+badge_size], fill=badge_color_rgb)
        
        draw.ellipse(
            [(width-badge_size)//2-border_w, y_cursor-border_w, (width+badge_size)//2+border_w, y_cursor+badge_size+border_w],
            outline=badge_color_rgb, width=border_w
        )
        y_cursor += badge_size + 15

        # --- 3. NOMBRE MEMBRESÍA (Recuadro café oscuro semitransparente, radius 25) ---
        if membership:
            m_name = membership.get('name', 'Júbilo')
            bbox_m = draw.textbbox((0,0), m_name, font=title_font)
            m_w, m_h = bbox_m[2]-bbox_m[0], bbox_m[3]-bbox_m[1]
            px, py = 30, 8  # Más margen en los costados
            bw, bh = m_w+px*2, m_h+py*2
            bx = (width-bw)//2
            layer = Image.new('RGBA', img.size, (0,0,0,0))
            ld = ImageDraw.Draw(layer)
            # Café oscuro con transparencia: (62,39,35,200) en lugar de negro
            ld.rounded_rectangle([bx, y_cursor, bx+bw, y_cursor+bh], radius=25, fill=(62,39,35,200), outline=badge_color_rgb, width=1)
            ld.text(((width-m_w)//2, y_cursor+py-bbox_m[1]), m_name, font=title_font, fill=cream_color)
            img = Image.alpha_composite(img.convert('RGBA'), layer).convert('RGB')
            draw = ImageDraw.Draw(img)
            y_cursor += bh + 30

        # --- 4. NOMBRE CLIENTE (con borde) ---
        c_name = client_name.upper()
        bbox_c = draw.textbbox((0,0), c_name, font=name_font)
        c_w = bbox_c[2]-bbox_c[0]
        c_h = bbox_c[3]-bbox_c[1]
        cx = (width - c_w) // 2
        cy = y_cursor
        # Dibujar borde alrededor del nombre (sombra / contorno)
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                if dx != 0 or dy != 0:
                    draw.text((cx + dx, cy + dy), c_name, font=name_font, fill=badge_color_rgb)
        draw.text((cx, cy), c_name, font=name_font, fill=(255,255,255))
        y_cursor += c_h + 20

        # --- 5. SALDO (con borde) ---
        if balance is not None:
            bal_text = f"${balance:,.2f}"
            bbox_b = draw.textbbox((0,0), bal_text, font=balance_font)
            b_w = bbox_b[2]-bbox_b[0]
            b_h = bbox_b[3]-bbox_b[1]
            bx = (width - b_w) // 2
            by = y_cursor
            # Dibujar borde alrededor del saldo
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    if dx != 0 or dy != 0:
                        draw.text((bx + dx, by + dy), bal_text, font=balance_font, fill=badge_color_rgb)
            draw.text((bx, by), bal_text, font=balance_font, fill=(255,255,255))
            y_cursor += b_h + 10
            sub = "Saldo disponible"
            bbox_s = draw.textbbox((0,0), sub, font=small_font)
            draw.text(((width-(bbox_s[2]-bbox_s[0]))//2, y_cursor), sub, font=small_font, fill=(200,200,200))
            y_cursor += 40

        # --- 6. VENCIMIENTO (Solo si NO es default) ---
        is_default = membership.get('is_default', False) if membership else False
        if expires_at and not is_default:
            exp_text = f"Vence: {expires_at}"
            # Usar icono PNG de calendario
            bbox_e = draw.textbbox((0,0), exp_text, font=small_font)
            ew, eh = bbox_e[2]-bbox_e[0], bbox_e[3]-bbox_e[1]
            ep = 10
            ebw, ebh = ew+ep*2, eh+ep*2
            # Agregar espacio extra para el icono (24px + padding)
            ebw += 30
            ex = (width-ebw)//2
            layer = Image.new('RGBA', img.size, (0,0,0,0))
            ld = ImageDraw.Draw(layer)
            ld.rounded_rectangle([ex, y_cursor, ex+ebw, y_cursor+ebh], radius=16, fill=(62,39,35,200), outline=badge_color_rgb, width=1)
            
            # Cargar y dibujar icono de calendario (centrado verticalmente)
            icon_path = os.path.join(os.path.dirname(__file__), 'ImgenQr', 'Iconos', 'calendar.png')
            try:
                icon_img = Image.open(icon_path).convert('RGBA')
                icon_img = icon_img.resize((22, 22), Image.Resampling.LANCZOS)
                icon_x = ex + 8
                # Centrar verticalmente dentro del recuadro
                icon_y = y_cursor + (ebh - 22) // 2
                layer.paste(icon_img, (icon_x, icon_y), icon_img)
            except Exception as e:
                get_logger().warning(f"No se pudo cargar el icono calendar.png: {e}")
            
            # Texto de vencimiento al lado del icono (centrado verticalmente)
            text_x = ex + 34
            # Centrar verticalmente el texto usando el ascent y descent
            # textbbox devuelve (left, top, right, bottom) donde top puede ser negativo
            # Para centrar, calculamos la altura del texto y lo posicionamos en el centro del recuadro
            text_height = bbox_e[3] - bbox_e[1]
            text_y = y_cursor + (ebh - text_height) // 2 - bbox_e[1]
            ld.text((text_x, text_y), exp_text, font=small_font, fill=cream_color)
            img = Image.alpha_composite(img.convert('RGBA'), layer).convert('RGB')
            draw = ImageDraw.Draw(img)
            y_cursor += ebh + 30
        else:
            y_cursor += 30

        # --- 7. QR (Debajo de vencimiento, tamaño dinámico) ---
        # Calcular espacio disponible para el QR
        espacio_disponible = height - y_cursor - 60  # dejar margen inferior
        
        # Calcular altura que ocuparán mensaje y beneficios si existen
        altura_mensaje = 0
        altura_beneficios = 0
        
        # Calcular altura del mensaje personalizado
        if custom_message:
            words = custom_message.split()
            lines = []; cur = []
            for w in words:
                test = ' '.join(cur+[w])
                tw = draw.textbbox((0,0), test, font=message_font)[2]
                if tw < width-100: cur.append(w)
                else:
                    if cur: lines.append(' '.join(cur))
                    cur = [w]
            if cur: lines.append(' '.join(cur))
            lines = lines[:4]
            if lines:
                altura_mensaje = len(lines) * 35 + 40  # 35px por línea + padding
        
        # Calcular altura de los beneficios
        if membership:
            benefits = []
            if membership.get('cashback_percent',0)>0: benefits.append(f"✨ {membership['cashback_percent']}% Cashback")
            if membership.get('discount_percent',0)>0: benefits.append(f"🏷️ {membership['discount_percent']}% Descuento")
            if membership.get('drinks_count',0)>0: benefits.append(f"☕ {membership['drinks_count']} Bebidas/mes")
            if membership.get('grain_bags',0)>0: benefits.append(f"🌾 {membership['grain_bags']} Bolsas grano")
            for db in (membership.get('daily_benefits') or []):
                db_text = f"🎁 {db.get('name') or 'Beneficio diario'}"
                if db.get('price', 0) > 0:
                    db_text += f" (${db['price']:.2f})"
                db_text += f" · {db.get('count', 1)}x/día"
                benefits.append(db_text)
            if benefits:
                altura_beneficios = 30 + len(benefits) * 35 + 30  # título + items + padding
        
        # Calcular espacio para el QR (mínimo 180, máximo 350)
        espacio_para_qr = espacio_disponible - altura_mensaje - altura_beneficios - 40  # separaciones
        qr_size = max(180, min(350, espacio_para_qr))
        qr_size = min(qr_size, width - 120)  # no más ancho que la imagen con márgenes
        
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(qr_data); qr.make(fit=True)
        qr_img = qr.make_image(fill_color="#3E2723", back_color="#D7CCC8").convert('RGBA')
        qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.LANCZOS)
        qr_mask = Image.new('L', qr_img.size, 0)
        ImageDraw.Draw(qr_mask).rounded_rectangle([0,0,qr_size,qr_size], radius=25, fill=255)
        qr_img.putalpha(qr_mask)
        
        y_qr = y_cursor
        x_qr = (width - qr_size) // 2
        img.paste(qr_img, (x_qr, y_qr), qr_img)
        y_cursor = y_qr + qr_size + 30
        
        # --- 8. MENSAJE PERSONALIZADO (Debajo del QR, centrado) ---
        if custom_message:
            words = custom_message.split()
            lines = []; cur = []
            for w in words:
                test = ' '.join(cur+[w])
                tw = draw.textbbox((0,0), test, font=message_font)[2]
                if tw < width-100: cur.append(w)
                else:
                    if cur: lines.append(' '.join(cur))
                    cur = [w]
            if cur: lines.append(' '.join(cur))
            lines = lines[:4]
            
            if lines:
                mp = 20; mlh = 35; max_mw = 0
                for l in lines:
                    lw = draw.textbbox((0,0), l, font=message_font)[2]
                    if lw > max_mw: max_mw = lw
                mbw = min(max_mw + mp*2, width - 80)
                mbh = len(lines)*mlh + mp*2
                mx = (width-mbw)//2
                
                layer = Image.new('RGBA', img.size, (0,0,0,0))
                ld = ImageDraw.Draw(layer)
                ld.rounded_rectangle([mx, y_cursor, mx+mbw, y_cursor+mbh], radius=18, fill=(62,39,35,200), outline=badge_color_rgb, width=1)
                yt = y_cursor + mp
                for l in lines:
                    lb = draw.textbbox((0,0), l, font=message_font)
                    ld.text(((width-(lb[2]-lb[0]))//2, yt-lb[1]), l, font=message_font, fill=(255,255,255))
                    yt += mlh
                img = Image.alpha_composite(img.convert('RGBA'), layer).convert('RGB')
                draw = ImageDraw.Draw(img)
                y_cursor += mbh + 20

        # --- 9. BENEFICIOS ACTIVOS (Debajo del mensaje) ---
        if membership:
            # Mapeo de beneficios a texto y nombre de archivo de icono
            benefit_labels = []
            if membership.get('cashback_percent',0)>0:
                benefit_labels.append(('Cashback', f"{membership['cashback_percent']}% Cashback", 'cashback.png'))
            if membership.get('discount_percent',0)>0:
                benefit_labels.append(('Descuento', f"{membership['discount_percent']}% Descuento", 'discount.png'))
            if membership.get('drinks_count',0)>0:
                benefit_labels.append(('Bebidas', f"{membership['drinks_count']} Bebidas/mes", 'drinks.png'))
            if membership.get('grain_bags',0)>0:
                benefit_labels.append(('Grano', f"{membership['grain_bags']} Bolsas grano", 'grain.png'))
            for db in (membership.get('daily_benefits') or []):
                db_text = f"{db.get('name') or 'Beneficio diario'}"
                if db.get('price', 0) > 0:
                    db_text += f" (${db['price']:.2f})"
                db_text += f" · {db.get('count', 1)}x/día"
                benefit_labels.append(('Diario', db_text, 'calendar.png'))
            
            if benefit_labels:
                ben_title = "Beneficios Activos"
                bt_bbox = draw.textbbox((0,0), ben_title, font=benefit_title_font)
                bt_w = bt_bbox[2] - bt_bbox[0]
                bt_h = bt_bbox[3] - bt_bbox[1]
                # Calcular ancho máximo entre todos los textos
                max_w = 0
                for _, text, _ in benefit_labels:
                    w = draw.textbbox((0,0), text, font=benefit_font)[2]
                    if w > max_w: max_w = w
                # Añadir espacio para el icono (24px + padding)
                bpx, bpy = 20, 15
                line_h = 35
                total_w = min(max_w + bpx*2 + 40, width - 80)
                # Altura: padding sup + título + gap + línea + gap + beneficios + padding inf
                total_h = bpy + bt_h + 12 + 1 + 12 + len(benefit_labels)*line_h + bpy
                ben_x = (width - total_w) // 2
                
                layer = Image.new('RGBA', img.size, (0,0,0,0))
                ld = ImageDraw.Draw(layer)
                ld.rounded_rectangle([ben_x, y_cursor, ben_x+total_w, y_cursor+total_h], radius=12, fill=(62,39,35,216), outline=badge_color_rgb, width=2)
                
                # Título centrado
                ld.text(((width-bt_w)//2, y_cursor+bpy), ben_title, font=benefit_title_font, fill=badge_color_rgb)
                
                # Línea divisoria debajo del título (entre el título y los beneficios)
                line_y = y_cursor + bpy + bt_h + 12
                ld.line([ben_x + 15, line_y, ben_x + total_w - 15, line_y], fill=badge_color_rgb, width=1)
                
                # Beneficios centrados con iconos
                yb = line_y + 12
                for label, text, icon_file in benefit_labels:
                    # Cargar y dibujar icono PNG
                    icon_path = os.path.join(os.path.dirname(__file__), 'ImgenQr', 'Iconos', icon_file)
                    try:
                        icon_img = Image.open(icon_path).convert('RGBA')
                        icon_img = icon_img.resize((24, 24), Image.Resampling.LANCZOS)
                        # Centrar el icono y el texto juntos
                        text_bbox = draw.textbbox((0,0), text, font=benefit_font)
                        text_w = text_bbox[2] - text_bbox[0]
                        total_item_w = 24 + 12 + text_w  # icono + separación + texto
                        item_x = (width - total_item_w) // 2
                        icon_x = item_x
                        icon_y = yb + 5
                        layer.paste(icon_img, (icon_x, icon_y), icon_img)
                        text_x = item_x + 24 + 12
                        ld.text((text_x, yb), text, font=benefit_font, fill=cream_color)
                    except Exception as e:
                        get_logger().warning(f"No se pudo cargar el icono {icon_file}: {e}")
                        # Fallback: texto sin icono, centrado
                        text_bbox = draw.textbbox((0,0), text, font=benefit_font)
                        text_w = text_bbox[2] - text_bbox[0]
                        ld.text(((width - text_w)//2, yb), text, font=benefit_font, fill=cream_color)
                    yb += line_h
                
                img = Image.alpha_composite(img.convert('RGBA'), layer).convert('RGB')
                draw = ImageDraw.Draw(img)
                y_cursor += total_h + 20

        # --- 9. FOOTER ---
        footer = "Tarjeta de regalo Júbilo"
        fb = draw.textbbox((0,0), footer, font=small_font)
        draw.text(((width-(fb[2]-fb[0]))//2, height-40), footer, font=small_font, fill=(161,136,127,200))
        
        buf = io.BytesIO(); img.save(buf, format="PNG"); return buf.getvalue()

    except Exception as e:
        get_logger().error(f"Error generando imagen WA: {e}", exc_info=True)
        img = Image.new('RGB', (800,1200), (200,0,0))
        d = ImageDraw.Draw(img); d.text((50,50), f"Error: {str(e)}", fill=(255,255,255))
        buf = io.BytesIO(); img.save(buf, format="PNG"); return buf.getvalue()
        
        # Construir lista de beneficios
        benefits_html = ""
        if membership:
            benefits = []
            if membership.get('cashback_percent', 0) > 0: benefits.append(f"✨ {membership['cashback_percent']}% Cashback")
            if membership.get('discount_percent', 0) > 0: benefits.append(f"🏷️ {membership['discount_percent']}% Desc.")
            if membership.get('drinks_count', 0) > 0: benefits.append(f"☕ {membership['drinks_count']} Bebidas/mes")
            if membership.get('grain_bags', 0) > 0: benefits.append(f"🌾 {membership['grain_bags']} Bolsas grano")
            for db in (membership.get('daily_benefits') or []):
                db_text = f"🎁 {db.get('name') or 'Beneficio diario'}"
                if db.get('price', 0) > 0:
                    db_text += f" (${db['price']:.2f})"
                db_text += f" · {db.get('count', 1)}x/día"
                benefits.append(db_text)
            
            if benefits:
                benefits_html = "<ul style='list-style: none; padding: 0; margin: 0; font-size: 14px; color: #F5E9D8;'>" + \
                    "".join([f"<li>{b}</li>" for b in benefits]) + "</ul>"

        # HTML Template para renderizar
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap');
                body {{ margin: 0; padding: 0; background: #3E2723; font-family: 'Inter', sans-serif; }}
                .card-container {{
                    width: 800px;
                    height: 1200px;
                    position: relative;
                    overflow: hidden;
                    color: white;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    padding: 40px;
                    box-sizing: border-box;
                    background-image: url('{bg_image_url}');
                    background-size: cover;
                    background-position: center;
                }}
                .overlay {{ position: absolute; top:0; left:0; right:0; bottom:0; background: rgba(62,39,35,0.8); z-index: 0; }}
                .content {{ position: relative; z-index: 1; width: 100%; text-align: center; }}
                .badge-icon {{ width: 100px; height: 100px; border-radius: 50%; border: 6px solid {badge_color}; object-fit: cover; margin-bottom: 20px; }}
                .m-name {{ background: rgba(0,0,0,0.7); padding: 8px 20px; border-radius: 20px; border: 1px solid {badge_color}; display: inline-block; font-size: 24px; font-weight: bold; margin-bottom: 30px; }}
                .client-name {{ font-size: 56px; font-weight: 800; margin: 0 0 20px; text-transform: uppercase; }}
                .balance-label {{ font-size: 24px; opacity: 0.8; margin: 0; }}
                .balance-amount {{ font-size: 72px; font-weight: 800; margin: 10px 0 40px; }}
                .expires {{ background: rgba(0,0,0,0.6); padding: 10px 20px; border-radius: 8px; border: 1px solid {badge_color}; display: inline-block; font-size: 20px; margin-bottom: 40px; }}
                .bottom-section {{ display: flex; justify-content: center; gap: 30px; align-items: center; margin-top: auto; }}
                .qr-img {{ width: 260px; height: 260px; border-radius: 25px; }}
                .benefits-box {{ background: rgba(62,39,35,0.85); padding: 20px; border-radius: 15px; border: 2px solid {badge_color}; text-align: left; min-width: 250px; }}
                .benefits-title {{ color: {badge_color}; font-weight: bold; margin-bottom: 10px; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 5px; }}
                .footer {{ position: absolute; bottom: 30px; width: 100%; text-align: center; font-size: 18px; opacity: 0.6; }}
            </style>
        </head>
        <body>
            <div class="card-container">
                <div class="overlay"></div>
                <div class="content">
                    <img src="https://cdn-icons-png.flaticon.com/512/1046/1046785.png" class="badge-icon" onerror="this.style.display='none'">
                    <div class="m-name">{m_name.upper()}</div>
                    <h1 class="client-name">{client_name.upper()}</h1>
                    <p class="balance-label">Saldo disponible</p>
                    <p class="balance-amount">${balance:,.2f}</p>
                    {f'<div class="expires">📅 Vence: {expires_at}</div>' if expires_at and not membership.get('is_default') else ''}
                    
                    <div class="bottom-section">
                        <img src="{qr_data}" class="qr-img" onerror="this.src='https://api.qrserver.com/v1/create-qr-code/?size=260x260&data=Error'">
                        <div class="benefits-box">
                            <div class="benefits-title">Beneficios Activos</div>
                            {benefits_html}
                        </div>
                    </div>
                </div>
                <div class="footer">Tarjeta de regalo Júbilo</div>
            </div>
        </body>
        </html>
        """

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.set_viewport_size({"width": 800, "height": 1200})
            page.set_content(html_content)
            # Esperar a que las imágenes carguen
            page.wait_for_load_state("networkidle")
            image_bytes = page.screenshot(type="png", full_page=True)
            browser.close()
            
        return image_bytes

    except Exception as e:
        get_logger().error(f"Error crítico generando imagen WhatsApp con Playwright: {e}", exc_info=True)
        # Fallback a imagen roja de error
        img = Image.new('RGB', (800, 1200), (200, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.text((50, 50), f"Error: {str(e)}", fill=(255,255,255))
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()

        # 6. Código QR Centrado
        qr_size = 350
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(qr_data)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert('RGB')
        qr_img = qr_img.resize((qr_size, qr_size))
        
        x_qr = (width - qr_size) // 2
        img.paste(qr_img, (x_qr, y_cursor))
        y_cursor += qr_size + 40
     
        draw.rounded_rectangle(
            [btn_x, btn_y, btn_x + btn_total_w, btn_y + btn_total_h],
            radius=15,
            fill=(40, 40, 40, 200)
        )
        draw.text(
            ((width - btn_w) // 2, btn_y + btn_pad_y - bbox_btn[1]),
            btn_text,
            font=btn_font,
            fill=(255, 255, 255)
        )
        
        # Convertir a bytes
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()

    except Exception as e:
        get_logger().error(f"Error crítico generando imagen WhatsApp: {e}")
        img = Image.new('RGB', (800, 1200), (200, 0, 0))
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()
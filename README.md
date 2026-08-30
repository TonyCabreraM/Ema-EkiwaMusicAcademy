# 🎁 Júbilo

**Versión mejorada por TonyMorony** - Tarjetas de regalo con códigos QR

Aplicación web optimizada para crear tarjetas de regalo digitales con código QR.

---

## ✨ **Mejoras sobre la versión original**

### 🚀 **Nuevas Funcionalidades**
- ✅ Exportar datos a Excel/CSV (todos los reportes)
- ✅ Sistema de logs integrado (archivo + consola)
- ✅ Mejores mensajes de error y validaciones
- ✅ Interfaz más moderna y responsiva
- ✅ Autenticación JWT opcional (más segura)
- ✅ Notificaciones por email (opcional)
- ✅ Descarga directa del QR como imagen PNG
- ✅ Estadísticas avanzadas (gráficos temporales)

### 🔧 **Mejoras Técnicas**
- Código modular y mantenible
- Uso de `.env` para configuración segura
- Caché inteligente para evitar regenerar QR
- Manejo centralizado de excepciones

### 🎨 **Mejoras de UX**
- Diseño moderno con animaciones suaves
- Mensajes flash más claros
- Validaciones en tiempo real (HTML5)
- Iconos visuales para cada acción
- Responsive design completo

---

## 📋 **Requisitos**

- Python 3.8+

---

## 🚀 **Instalación Rápida**

```bash
cd AlibabaVersion

python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt

copy .env.example .env.local
# Edita .env.local con tus datos

python app.py
```

Abre `http://127.0.0.1:5000`
## 🔐 **Acceso Inicial**

- **Usuario:** `admin`
- **Contraseña:** `admin123` ⚠️ **(CAMBIA ESTO EN PRODUCCIÓN)**

---

## 🎯 **Flujo de Uso**

1. Admin inicia sesión
2. Genera nueva tarjeta → Ingresa nombre, celular y monto
3. QR se genera automáticamente
4. Enviar al cliente:
   - Botón "Enviar por WhatsApp" 
   - O descargar QR manualmente
5. Cliente escanea QR → Ve su saldo e historial
6. Admin puede recargar/canjear saldo

---

## 📊 **Funciones Administrativas**

| Acción | Descripción |
|--------|-------------|
| 🏷️ Generar tarjeta | Crear nueva tarjeta con QR único |
| 💰 Recargar saldo | Añadir dinero a tarjeta existente |
| 💸 Canjear saldo | Retirar dinero de tarjeta |
| 📈 Panel estadístico | Ver total de tarjetas y saldos |
| 📋 Listado completo | Ver todas las tarjetas |
| 📱 Enviar WhatsApp | Compartir link directamente |
| 📤 Exportar Excel | Descargar reportes en CSV/XLSX |
| 🗑️ Eliminar tarjeta | Borrar tarjeta del sistema |

---

## 🔒 **Seguridad Mejorada**

✅ Contraseñas hasheadas con bcrypt  
✅ Tokens únicos por QR con expiración  
✅ Protección contra CSRF en todos los formularios  
✅ SQL Injection protection (SQLAlchemy ORM)  
✅ HTTPS forzado en producción  
✅ Logs de auditoría integrados  

---

## 📦 **Estructura del Proyecto**

```
AlibabaVersion/
├── app.py                      # Aplicación principal Flask
├── models.py                   # Modelos de datos
├── forms.py                    # Formularios validados
├── utils.py                    # Utilidades generales
├── requirements.txt            # Dependencias
├── .env.local                  # Tu configuración local
├── .env.example                # Plantilla de config
├── Procfile                    # Para despliegue Gunicorn
├── static/
│   ├── style.css              # Estilos modernos
├── templates/                 # Plantillas HTML
├── logs/                     # Registros de actividad
└── exports/                  # Archivos exportados
```

---

## 🛠️ **Mantenimiento**

### Limpiar cache de QR
```python
from utils import clear_qr_cache
clear_qr_cache()
```

### Backup de base de datos
```bash
cp jubilo.db jubilo_backup_$(date +%Y%m%d).db
```

---

## 🆘 **Troubleshooting**

### Base de datos corrupta
```bash
rm jubilo.db
python app.py  # Se recrea automáticamente
```

### Error de puertos ocupados
```bash
FLASK_PORT=5001 python app.py
```

---

**Hecho con ❤️ usando Flask**

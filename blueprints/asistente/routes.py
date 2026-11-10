import json
import os
import re
import hashlib
from collections import defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import quote_plus

from flask import current_app, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

from database import get_db
from blueprints.auth.routes import login_required, usuario_es_global_operacion, usuario_tiene_permiso
from helpers.centros_helper import centro_activo_id
from . import asistente_bp
from .decision_core import (
    CATEGORIAS_ESTABLECIMIENTO,
    FASES_NEGOCIO_ESCENARIO,
    FUENTES_DATOS_ESCENARIO,
    MODELOS_NEGOCIO_HOSTELERIA,
    ZONAS_MERCADO,
    analizar_cartera_escenarios as _analizar_cartera_escenarios,
    calcular_escenario_decision as _calcular_escenario_decision,
    datos_escenario_desde_form as _datos_escenario_desde_form,
    escenarios_demo_decision as _escenarios_demo_decision,
    escenarios_inversion_pirineo as _escenarios_inversion_pirineo,
)
from .knowledge_core import (
    AMBITOS_HOSTELERIA,
    AREAS_CONOCIMIENTO,
    TIPOS_FUENTE_CONOCIMIENTO,
    analizar_posicion_competitiva,
    detectar_tendencias_sectoriales,
    evaluar_fuente_conocimiento,
    extraer_fuentes_desde_feed,
    extraer_fuente_desde_url,
    generar_brief_ejecutivo,
    generar_radar_estrategico,
    clasificar_aprendizaje_chat,
    responder_chat_estrategico,
)
from .operativo_core import construir_contexto_operativo, responder_cerebro_operativo, sugerencia_contextual
from .operativo_signals import emitir_senal_operativa, ensure_senales_schema
from blueprints.recetas.routes import _crear_propuestas_busqueda_automatica_recetas, _crear_propuestas_internas_alergenos


ESTADOS_RESERVA_NO_ACTIVOS = ("cancelada", "cancelado", "no_show", "no show")
ESTADOS_CERRADOS = ("cerrado", "cerrada", "completado", "completada", "finalizado", "finalizada", "cancelado", "cancelada")
ESTADOS_VACACIONES_APROBADAS = ("aprobada", "aprobado", "aceptada", "aceptado", "concedida", "concedido", "validada", "validado")
ESTADOS_VACACIONES_PENDIENTES = ("solicitada", "solicitado", "pendiente", "pendiente_aprobacion")
PRIORIDAD_ORDEN = {"critica": 0, "alta": 1, "media": 2, "baja": 3, "info": 4}

OBJETIVOS_COCINA_IA = [
    ("general", "Revision general"),
    ("coste", "Bajar coste sin bajar calidad"),
    ("verano", "Menu fresco / verano"),
    ("familias", "Familias y ninos"),
    ("senior", "Senior / IMSERSO"),
    ("escandinavo", "Escandinavos"),
    ("britanico_aleman", "Britanicos / alemanes"),
    ("halal", "Halal"),
    ("celiaco", "Celiacos / sin gluten"),
    ("alergenos", "Alergenos y tarjetas buffet"),
]

TIPOS_DOCUMENTO_IA = [
    ("auto", "Detectar automaticamente"),
    ("ticket_tpv", "Ticket TPV / consumo cliente"),
    ("reserva", "Reserva / bono"),
    ("albaran", "Albaran proveedor"),
    ("factura", "Factura"),
    ("recepcion_pedido", "Recepcion de pedido"),
    ("encuesta_cliente", "Encuesta / comentario cliente"),
    ("otro", "Otro"),
]

MODULOS_DOCUMENTO_IA = [
    ("auto", "Detectar automaticamente"),
    ("tpv", "TPV y preferencias"),
    ("recepcion", "Recepcion / reservas"),
    ("compras", "Compras / recepcion pedidos"),
    ("contabilidad", "Contabilidad"),
    ("experiencia", "Experiencia cliente"),
    ("produccion", "Produccion / menus"),
    ("otro", "Otro"),
]

EXTENSIONES_DOCUMENTO_IA = {"pdf", "png", "jpg", "jpeg", "webp", "tif", "tiff", "txt", "csv"}

PERFILES_COCINA = {
    "verano": {
        "favor": ("ensalada", "gazpacho", "salmorejo", "fruta", "sandia", "melon", "pescado", "verdura", "frio", "fresco"),
        "revisar": ("sopa", "crema caliente", "guiso", "estofado"),
        "mensaje": "Con calor conviene subir platos frescos, frutas, pescados y ensaladas, y bajar sopas calientes si sobran.",
    },
    "familias": {
        "favor": ("pasta", "pizza", "pollo", "hamburg", "arroz", "croqueta", "tortilla", "fruta", "helado"),
        "revisar": ("picante", "casqueria", "menudillo"),
        "mensaje": "Familias y ninos suelen responder mejor a platos reconocibles, raciones faciles y opciones suaves.",
    },
    "senior": {
        "favor": ("pescado", "crema", "sopa", "guiso", "verdura", "arroz", "pollo", "fruta"),
        "revisar": ("picante", "frito pesado", "muy especiado"),
        "mensaje": "Senior/IMSERSO suele valorar cocina reconocible, digestiva, menos grasa y bien identificada.",
    },
    "escandinavo": {
        "favor": ("pescado", "salmon", "bacalao", "ensalada", "patata", "verdura", "parrilla", "fruta"),
        "revisar": ("guiso pesado", "frito"),
        "mensaje": "Para escandinavos suelen funcionar pescados, parrilla, verduras, ensaladas y platos frescos.",
    },
    "britanico_aleman": {
        "favor": ("pollo", "cerdo", "ternera", "salchicha", "patata", "huevo", "bacon", "pasta", "asado"),
        "revisar": ("muy picante",),
        "mensaje": "Britanicos/alemanes suelen pedir desayuno potente, grill, carnes, patatas y opciones claras.",
    },
    "halal": {
        "favor": ("pollo", "ternera", "pescado", "verdura", "arroz", "legumbre", "fruta"),
        "revisar": ("cerdo", "bacon", "jamon", "vino", "alcohol", "chorizo", "morcilla"),
        "mensaje": "Halal exige separar bien recetas, ingredientes y contaminacion cruzada; el asistente solo avisa.",
    },
    "celiaco": {
        "favor": ("arroz", "patata", "maiz", "pescado", "carne", "verdura", "fruta", "legumbre"),
        "revisar": ("gluten", "harina", "pan", "pasta", "trigo", "cebada", "centeno"),
        "mensaje": "Celiacos necesitan receta, producto, elaboracion y servicio controlados; no basta con cambiar un ingrediente.",
    },
}

ROLES_IA_ESTRATEGICA = {
    "admin",
    "administrador",
    "gerente",
    "director",
    "direccion",
    "direccion_general",
    "director_operaciones",
    "director_hotel",
    "subdirector",
    "propietario",
}

def _empresa_id():
    try:
        return int(session.get("user_empresa_id") or session.get("empresa_id") or 1)
    except (TypeError, ValueError):
        return 1


def _normalizar(texto):
    return str(texto or "").strip().lower()


def _table_exists(conn, table_name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _columns(conn, table_name):
    if not _table_exists(conn, table_name):
        return set()
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:
        return set()
    cols = set()
    for row in rows:
        try:
            cols.add(row["name"])
        except Exception:
            cols.add(row[1])
    return cols


def _safe_add_column(conn, table_name, column_sql):
    column_name = column_sql.split()[0]
    if column_name in _columns(conn, table_name):
        return
    try:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")
    except Exception:
        pass


def _ensure_ia_reportes_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ia_reportes_contenido (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER,
            centro_id INTEGER,
            usuario_id INTEGER,
            usuario_nombre TEXT,
            modulo TEXT,
            origen TEXT,
            referencia_id TEXT,
            motivo TEXT,
            comentario TEXT,
            contenido TEXT,
            url_origen TEXT,
            estado TEXT DEFAULT 'pendiente',
            dispositivo TEXT,
            user_agent TEXT,
            ip_hash TEXT,
            datos_minimizados INTEGER DEFAULT 1,
            fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            fecha_revision TIMESTAMP,
            revisado_por TEXT,
            resultado_revision TEXT
        )
        """
    )
    conn.commit()
    _safe_add_column(conn, "ia_reportes_contenido", "ip_hash TEXT")
    _safe_add_column(conn, "ia_reportes_contenido", "datos_minimizados INTEGER DEFAULT 1")
    conn.commit()


def _hash_dato_privado(valor):
    texto = (valor or "").strip()
    if not texto:
        return ""
    sal = current_app.config.get("SECRET_KEY") or "el-raco-local"
    return hashlib.sha256(f"{sal}:{texto}".encode("utf-8", errors="ignore")).hexdigest()


def _scalar(conn, query, params=(), default=0):
    try:
        row = conn.execute(query, params).fetchone()
    except Exception:
        return default
    if not row:
        return default
    try:
        value = row[0]
    except Exception:
        value = next(iter(dict(row).values()), default)
    return default if value is None else value


def _to_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        text = str(value).strip().replace("€", "").replace(" ", "")
        text = "".join(ch for ch in text if ch.isdigit() or ch in ",.-")
        if "," in text and "." in text:
            decimal = "," if text.rfind(",") > text.rfind(".") else "."
            thousands = "." if decimal == "," else ","
            text = text.replace(thousands, "").replace(decimal, ".")
        elif "," in text:
            text = text.replace(".", "").replace(",", ".")
        elif text.count(".") > 1:
            parts = text.split(".")
            text = "".join(parts[:-1]) + "." + parts[-1]
        return float(text)
    except (TypeError, ValueError):
        return default


def _format_euros(value, decimales=2):
    number = _to_float(value, 0.0)
    text = f"{number:,.{int(decimales)}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text}€"


def _row_value(row, key, default=None):
    try:
        return row[key]
    except Exception:
        return default


@asistente_bp.route("/reportar-contenido", methods=["POST"])
@login_required
def reportar_contenido_ia():
    puede_reportar = bool(session.get("user_id") or session.get("username")) or usuario_tiene_permiso("asistente_ver") or usuario_tiene_permiso("acceso_global")
    if not puede_reportar:
        flash("No tienes permisos para reportar contenido IA.", "warning")
        return redirect(url_for("asistente.dashboard"))
    conn = get_db()
    _ensure_ia_reportes_schema(conn)
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    usuario = session.get("user_nombre") or session.get("username") or session.get("email") or "usuario"
    motivo = (request.form.get("motivo") or "contenido_inapropiado").strip()[:80]
    comentario = (request.form.get("comentario") or "").strip()[:2000]
    contenido = (request.form.get("contenido") or "").strip()[:8000]
    modulo = (request.form.get("modulo") or "asistente_ia").strip()[:120]
    origen = (request.form.get("origen") or request.referrer or request.path or "").strip()[:300]
    referencia_id = (request.form.get("referencia_id") or "").strip()[:120]
    dispositivo = (request.form.get("dispositivo") or "Microsoft Surface Laptop").strip()[:120]
    return_to = request.form.get("return_to") or request.referrer or url_for("asistente.dashboard")
    user_agent = (request.headers.get("User-Agent") or "").strip()[:500]
    ip_hash = _hash_dato_privado(request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0])
    conn.execute(
        """
        INSERT INTO ia_reportes_contenido (
            empresa_id, centro_id, usuario_id, usuario_nombre, modulo, origen,
            referencia_id, motivo, comentario, contenido, url_origen,
            dispositivo, user_agent, ip_hash, datos_minimizados
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            empresa_id,
            centro_id,
            session.get("user_id"),
            usuario,
            modulo,
            origen,
            referencia_id,
            motivo,
            comentario,
            contenido,
            request.referrer or "",
            dispositivo,
            user_agent,
            ip_hash,
        ),
    )
    conn.commit()
    flash("Reporte de contenido IA recibido. Lo revisaremos antes de usar esa salida como conocimiento fiable.", "success")
    return redirect(return_to)


def _parse_date(value, default=None):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    texto = str(value or "").strip()
    if not texto:
        return default
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(texto[:19] if "%H" in fmt else texto[:10], fmt).date()
        except ValueError:
            continue
    return default


def _date_range(fecha_desde, fecha_hasta):
    actual = fecha_desde
    while actual <= fecha_hasta:
        yield actual
        actual += timedelta(days=1)


def _overlap(entrada, salida, dia):
    if not entrada or not salida:
        return False
    return entrada <= dia < salida


def _upload_dir_documentos_ia():
    base = os.path.join(current_app.root_path, "uploads", "asistente_documentos")
    os.makedirs(base, exist_ok=True)
    return base


def _extension_archivo(nombre):
    if not nombre or "." not in nombre:
        return ""
    return nombre.rsplit(".", 1)[-1].lower()


def _texto_desde_pdf(path):
    try:
        from pypdf import PdfReader
    except Exception:
        try:
            from PyPDF2 import PdfReader
        except Exception:
            return "", "No hay lector PDF disponible. Instalar pypdf o activar OCR."
    try:
        reader = PdfReader(path)
        texto = "\n".join((page.extract_text() or "") for page in reader.pages[:8])
        return texto.strip(), ""
    except Exception as exc:
        return "", f"No se pudo leer texto del PDF: {exc}"


def _texto_desde_imagen(path):
    try:
        from PIL import Image
        import pytesseract
    except Exception:
        return "", "OCR de imagen no disponible. Instalar pillow + pytesseract y Tesseract."
    try:
        return pytesseract.image_to_string(Image.open(path), lang="spa+eng").strip(), ""
    except Exception as exc:
        return "", f"No se pudo hacer OCR de la imagen: {exc}"


def _extraer_texto_documento(path, extension):
    if extension in {"txt", "csv"}:
        for encoding in ("utf-8", "latin-1", "cp1252"):
            try:
                with open(path, "r", encoding=encoding, errors="ignore") as fh:
                    return fh.read()[:60000], ""
            except Exception:
                continue
        return "", "No se pudo leer el texto del archivo."
    if extension == "pdf":
        return _texto_desde_pdf(path)
    if extension in {"png", "jpg", "jpeg", "webp", "tif", "tiff"}:
        return _texto_desde_imagen(path)
    return "", "Tipo de archivo no soportado para lectura automatica."


def _analizar_texto_documento(texto, tipo_documento="auto", modulo_destino="auto"):
    limpio = texto or ""
    t = limpio.lower()
    emails = sorted(set(re.findall(r"[\w.\-+]+@[\w.\-]+\.\w+", limpio)))[:8]
    fechas = sorted(set(re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b", limpio)))[:12]
    importes = re.findall(r"(?i)(?:total|importe|base|iva|igic|saldo|cambio|entregado)[^\d]{0,18}(\d{1,5}(?:[.,]\d{3})*(?:[.,]\d{2})?)", limpio)
    importes = importes[:12]

    modulo = modulo_destino if modulo_destino != "auto" else "otro"
    tipo = tipo_documento if tipo_documento != "auto" else "otro"
    senales = []

    if any(x in t for x in ("factura", "invoice", "iva", "igic", "nif", "cif")):
        tipo = "factura" if tipo_documento == "auto" else tipo
        modulo = "contabilidad" if modulo_destino == "auto" else modulo
        senales.append("Documento economico/factura")
    if any(x in t for x in ("albaran", "pedido", "proveedor", "lote", "caducidad")):
        tipo = "albaran" if tipo_documento == "auto" else tipo
        modulo = "compras" if modulo_destino == "auto" else modulo
        senales.append("Compra, recepcion o lote")
    if any(x in t for x in ("reserva", "booking", "habitacion", "check-in", "check in", "localizador")):
        tipo = "reserva" if tipo_documento == "auto" else tipo
        modulo = "recepcion" if modulo_destino == "auto" else modulo
        senales.append("Reserva o estancia")
    if any(x in t for x in ("ticket", "mesa", "tpv", "cambio", "efectivo", "tarjeta", "cerveza", "vino", "cafe", "refresco")):
        tipo = "ticket_tpv" if tipo_documento == "auto" else tipo
        modulo = "tpv" if modulo_destino == "auto" else modulo
        senales.append("Consumo de TPV o preferencias")
    if any(x in t for x in ("queja", "complaint", "review", "reseña", "excelente", "mal", "ruido", "limpieza")):
        modulo = "experiencia" if modulo_destino == "auto" else modulo
        senales.append("Experiencia cliente")
    if any(x in t for x in ("alerg", "celiac", "gluten", "halal", "apio", "lactosa", "frutos secos")):
        senales.append("Alergias, cultura o restriccion alimentaria")
    if any(x in t for x in ("desayuno", "media pension", "pension completa", "todo incluido", "buffet")):
        senales.append("Regimen o servicio de restauracion")

    propuesta = []
    if modulo == "tpv":
        propuesta.append("Usar el documento para enriquecer preferencias de consumo por cliente, mesa, punto de venta o franja horaria.")
    if modulo == "recepcion":
        propuesta.append("Cruzar con reserva y ficha 360 antes de crear datos nuevos.")
    if modulo == "compras":
        propuesta.append("Comparar contra pedido, lotes, caducidades, cantidades y almacen de destino.")
    if modulo == "contabilidad":
        propuesta.append("Validar tercero, base, impuesto, total, vencimiento y forma de pago antes de asentar.")
    if modulo == "experiencia":
        propuesta.append("Convertir hallazgos en queja, preferencia o aprendizaje solo tras revision humana.")
    if not propuesta:
        propuesta.append("Revisar el texto extraido y clasificarlo antes de integrarlo en la ERP.")

    return {
        "tipo_detectado": tipo,
        "modulo_detectado": modulo,
        "emails": emails,
        "fechas": fechas,
        "importes": importes,
        "senales": senales,
        "longitud_texto": len(limpio),
        "propuesta": " ".join(propuesta),
    }


def _rec(modulo, categoria, prioridad, titulo, detalle, accion, datos=None, enlace=None):
    return {
        "modulo": modulo,
        "categoria": categoria,
        "prioridad": prioridad,
        "titulo": titulo,
        "detalle": detalle,
        "accion_propuesta": accion,
        "datos_usados": datos or {},
        "enlace": enlace,
    }


def _ensure_asistente_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_recomendaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            empresa_id INTEGER DEFAULT 1,
            modulo TEXT NOT NULL,
            categoria TEXT,
            prioridad TEXT DEFAULT 'media',
            titulo TEXT NOT NULL,
            detalle TEXT,
            datos_usados TEXT,
            accion_propuesta TEXT,
            estado TEXT DEFAULT 'pendiente',
            aceptada_por TEXT,
            fecha_decision DATETIME,
            resultado TEXT,
            origen TEXT DEFAULT 'reglas_base'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            clave TEXT NOT NULL,
            valor TEXT,
            fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(empresa_id, clave)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_memoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            tipo TEXT NOT NULL,
            modulo TEXT,
            clave TEXT NOT NULL,
            valor TEXT,
            confianza REAL DEFAULT 0.5,
            origen TEXT DEFAULT 'manual',
            detalle TEXT,
            estado TEXT DEFAULT 'activa',
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_eventos_aprendizaje (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            recomendacion_id INTEGER,
            tipo TEXT NOT NULL,
            modulo TEXT,
            senal TEXT,
            decision TEXT,
            resultado TEXT,
            usuario TEXT,
            fecha_evento DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_investigaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            modulo TEXT DEFAULT 'IA Cocina',
            tipo TEXT DEFAULT 'investigacion_externa',
            objetivo TEXT,
            consulta TEXT,
            perfil_cliente TEXT,
            restricciones TEXT,
            idioma TEXT,
            fuente_titulo TEXT,
            fuente_url TEXT,
            resumen TEXT,
            propuesta TEXT,
            riesgos TEXT,
            estado TEXT DEFAULT 'pendiente_revision',
            recomendacion_id INTEGER,
            usuario TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_documentos_lectura (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            modulo_destino TEXT DEFAULT 'auto',
            tipo_documento TEXT DEFAULT 'auto',
            titulo TEXT,
            archivo_nombre TEXT,
            archivo_path TEXT,
            mime TEXT,
            estado TEXT DEFAULT 'pendiente_revision',
            texto_extraido TEXT,
            datos_detectados TEXT,
            propuesta TEXT,
            origen TEXT DEFAULT 'upload',
            usuario TEXT,
            validado_por TEXT,
            fecha_validacion DATETIME,
            observaciones TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_fuentes_conocimiento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            tipo TEXT DEFAULT 'otro',
            ambito TEXT DEFAULT 'multi_hosteleria',
            area TEXT DEFAULT 'mercado',
            titulo TEXT NOT NULL,
            url TEXT,
            autor TEXT,
            resumen TEXT,
            lectura TEXT,
            uso_recomendado TEXT,
            riesgos TEXT,
            confianza_pct REAL DEFAULT 30,
            estado TEXT DEFAULT 'pendiente_revision',
            lectura_ia TEXT,
            usuario TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_fuentes_externas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            nombre TEXT NOT NULL,
            url TEXT NOT NULL,
            tipo TEXT DEFAULT 'feed',
            ambito TEXT DEFAULT 'multi_hosteleria',
            area TEXT DEFAULT 'mercado',
            estado TEXT DEFAULT 'activa',
            ultima_lectura DATETIME,
            ultimo_resultado TEXT,
            usuario TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_chat_estrategico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            pregunta TEXT NOT NULL,
            respuesta TEXT,
            foco TEXT,
            confianza_pct REAL DEFAULT 0,
            contexto_json TEXT,
            usuario TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_operativo_chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            usuario TEXT,
            peticion TEXT NOT NULL,
            intencion TEXT,
            modulo TEXT,
            modo TEXT,
            riesgo TEXT DEFAULT 'medio',
            respuesta TEXT,
            contexto_json TEXT,
            estado TEXT DEFAULT 'respondido'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_operativo_acciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            usuario TEXT,
            chat_id INTEGER,
            intencion TEXT,
            modulo TEXT,
            accion TEXT,
            herramienta TEXT,
            descripcion TEXT,
            plan_json TEXT,
            payload_json TEXT,
            estado TEXT DEFAULT 'borrador',
            requiere_confirmacion INTEGER DEFAULT 1,
            riesgo TEXT DEFAULT 'medio',
            resultado TEXT
        )
        """
    )
    for col in [
        "herramienta TEXT",
        "plan_json TEXT",
    ]:
        nombre_col = col.split()[0]
        if nombre_col not in _columns(conn, "asistente_operativo_acciones"):
            conn.execute(f"ALTER TABLE asistente_operativo_acciones ADD COLUMN {col}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_operativo_objetivos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            modulo TEXT DEFAULT 'Direccion',
            objetivo TEXT NOT NULL,
            indicador TEXT,
            valor_objetivo TEXT,
            prioridad TEXT DEFAULT 'media',
            estado TEXT DEFAULT 'activo',
            usuario TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_operativo_tareas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            modulo TEXT DEFAULT 'Operacion',
            titulo TEXT NOT NULL,
            detalle TEXT,
            prioridad TEXT DEFAULT 'media',
            estado TEXT DEFAULT 'pendiente',
            origen TEXT DEFAULT 'cerebro_operativo',
            responsable TEXT,
            fecha_objetivo TEXT,
            usuario TEXT,
            resultado TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_operativo_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            salud_score INTEGER DEFAULT 0,
            estado TEXT,
            lectura TEXT,
            riesgos_json TEXT,
            acciones_json TEXT,
            causas_json TEXT,
            kpis_json TEXT,
            simulacion_json TEXT,
            indicadores_json TEXT,
            diagnosticos_json TEXT,
            origen TEXT DEFAULT 'manual',
            usuario TEXT
        )
        """
    )
    if "causas_json" not in _columns(conn, "asistente_operativo_snapshots"):
        conn.execute("ALTER TABLE asistente_operativo_snapshots ADD COLUMN causas_json TEXT")
    if "kpis_json" not in _columns(conn, "asistente_operativo_snapshots"):
        conn.execute("ALTER TABLE asistente_operativo_snapshots ADD COLUMN kpis_json TEXT")
    if "simulacion_json" not in _columns(conn, "asistente_operativo_snapshots"):
        conn.execute("ALTER TABLE asistente_operativo_snapshots ADD COLUMN simulacion_json TEXT")
    ensure_senales_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_escenarios_decision (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            nombre TEXT NOT NULL,
            tipo TEXT DEFAULT 'hotel',
            decision_tipo TEXT DEFAULT 'oferta',
            fase_negocio TEXT DEFAULT 'idea_inversion',
            zona_mercado TEXT DEFAULT 'sin_zona',
            categoria_establecimiento TEXT DEFAULT 'sin_categoria',
            calidad_datos_pct REAL DEFAULT 35,
            habitaciones INTEGER DEFAULT 0,
            ocupacion_pct REAL DEFAULT 0,
            adr REAL DEFAULT 0,
            dias INTEGER DEFAULT 30,
            pax_medio REAL DEFAULT 2,
            coste_variable_pax_dia REAL DEFAULT 0,
            coste_fijo_mes REAL DEFAULT 0,
            coste_personal_mes REAL DEFAULT 0,
            alcance_costes_pct REAL DEFAULT 100,
            comision_pct REAL DEFAULT 0,
            inversion REAL DEFAULT 0,
            satisfaccion_objetivo REAL DEFAULT 80,
            rotacion_riesgo_pct REAL DEFAULT 15,
            empleados_no_presentan INTEGER DEFAULT 0,
            retrasos_pct REAL DEFAULT 0,
            cambios_equipo INTEGER DEFAULT 0,
            aceptacion_sistema_pct REAL DEFAULT 90,
            cobertura_ops_pct REAL DEFAULT 70,
            fuente_datos TEXT DEFAULT 'simulado',
            confianza_pct REAL DEFAULT 35,
            notas TEXT,
            resultado_json TEXT,
            usuario TEXT
        )
        """
    )
    for col in [
        "centro_id INTEGER",
        "empleados_no_presentan INTEGER DEFAULT 0",
        "retrasos_pct REAL DEFAULT 0",
        "cambios_equipo INTEGER DEFAULT 0",
        "aceptacion_sistema_pct REAL DEFAULT 90",
        "cobertura_ops_pct REAL DEFAULT 70",
        "fuente_datos TEXT DEFAULT 'simulado'",
        "confianza_pct REAL DEFAULT 35",
        "alcance_costes_pct REAL DEFAULT 100",
        "fase_negocio TEXT DEFAULT 'idea_inversion'",
        "zona_mercado TEXT DEFAULT 'sin_zona'",
        "categoria_establecimiento TEXT DEFAULT 'sin_categoria'",
        "calidad_datos_pct REAL DEFAULT 35",
    ]:
        if "asistente_escenarios_decision" in {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
            nombre_col = col.split()[0]
            if nombre_col not in _columns(conn, "asistente_escenarios_decision"):
                conn.execute(f"ALTER TABLE asistente_escenarios_decision ADD COLUMN {col}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asistente_indicadores_externos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            periodo TEXT,
            mercado TEXT,
            indicador TEXT NOT NULL,
            valor REAL DEFAULT 0,
            unidad TEXT,
            fuente TEXT,
            lectura TEXT,
            impacto TEXT DEFAULT 'medio',
            usuario TEXT
        )
        """
    )
    conn.commit()


def _guardar_memoria(conn, empresa_id, tipo, modulo, clave, valor, detalle="", confianza=0.5, origen="manual"):
    clave = (clave or "").strip()
    if not clave:
        return
    conn.execute(
        """
        INSERT INTO asistente_memoria (
            empresa_id, tipo, modulo, clave, valor, detalle, confianza, origen, estado
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'activa')
        """,
        (empresa_id, tipo, modulo, clave, valor, detalle, confianza, origen),
    )


def _clasificar_resultado_decision(estado, resultado):
    texto = _normalizar(resultado or "")
    positivos = ("funciona", "bien", "mejora", "sube", "aumenta", "reduce", "ahorra", "exito", "positivo", "rentable")
    negativos = ("mal", "empeora", "baja", "pierde", "perdida", "queja", "caro", "fracaso", "negativo", "rotacion")
    if estado == "aceptada" and any(p in texto for p in positivos):
        return "reforzar", 0.82
    if estado == "aceptada" and any(n in texto for n in negativos):
        return "revisar", 0.72
    if estado == "descartada":
        return "evitar", 0.62
    if estado == "aceptada":
        return "reforzar", 0.68
    return "observar", 0.45


def _registrar_evento_aprendizaje(conn, empresa_id, recomendacion_id, estado, resultado):
    row = conn.execute(
        """
        SELECT id, modulo, categoria, prioridad, titulo, detalle, accion_propuesta
        FROM asistente_recomendaciones
        WHERE id = ? AND empresa_id = ?
        """,
        (recomendacion_id, empresa_id),
    ).fetchone()
    if not row:
        return

    usuario = session.get("user_nombre") or session.get("username") or "usuario"
    conn.execute(
        """
        INSERT INTO asistente_eventos_aprendizaje (
            empresa_id, recomendacion_id, tipo, modulo, senal, decision, resultado, usuario
        ) VALUES (?, ?, 'feedback_recomendacion', ?, ?, ?, ?, ?)
        """,
        (empresa_id, recomendacion_id, row["modulo"], row["titulo"], estado, resultado, usuario),
    )
    if estado in {"aceptada", "descartada"}:
        leccion, confianza = _clasificar_resultado_decision(estado, resultado)
        detalle = (
            f"Decision humana sobre recomendacion {row['prioridad']} / {row['categoria']}: "
            f"{row['accion_propuesta'] or row['detalle'] or ''}"
        )
        _guardar_memoria(
            conn,
            empresa_id,
            "feedback_regla",
            row["modulo"],
            row["titulo"],
            estado,
            resultado or detalle,
            confianza=confianza,
            origen="decision_humana",
        )
        _guardar_memoria(
            conn,
            empresa_id,
            "leccion_decision",
            row["modulo"],
            f"{leccion}: {row['titulo']}",
            leccion,
            (
                f"Estado: {estado}. Resultado: {resultado or 'sin nota'}. "
                f"Accion propuesta: {row['accion_propuesta'] or ''}. "
                "Usar esta leccion para ajustar futuras recomendaciones parecidas."
            ),
            confianza=confianza,
            origen="aprendizaje_resultado",
        )


def _puede_ver_asistente():
    return (
        usuario_es_global_operacion()
        or usuario_tiene_permiso("informes_ver")
        or usuario_tiene_permiso("contabilidad_ver")
        or usuario_tiene_permiso("rrhh_ver")
        or usuario_tiene_permiso("recepcion_gestionar")
    )


def _puede_ver_ia_estrategica():
    rol = _normalizar(session.get("user_rol"))
    return (
        usuario_es_global_operacion(ROLES_IA_ESTRATEGICA)
        or usuario_tiene_permiso("acceso_global")
        or usuario_tiene_permiso("ia_estrategica_ver")
        or rol in ROLES_IA_ESTRATEGICA
    )


def _requiere_ia_estrategica():
    if _puede_ver_ia_estrategica():
        return None
    flash("Esta zona es de IA estrategica y solo esta abierta a direccion, operaciones o propiedad.", "warning")
    return redirect(url_for("asistente.dashboard"))


def _puede_gestionar_simulador_decision():
    rol = _normalizar(session.get("user_rol"))
    return (
        usuario_es_global_operacion(ROLES_IA_ESTRATEGICA)
        or usuario_tiene_permiso("acceso_global")
        or usuario_tiene_permiso("ia_estrategica_gestionar")
        or usuario_tiene_permiso("asistente_simulador_gestionar")
        or rol in ROLES_IA_ESTRATEGICA
    )


def _requiere_gestion_simulador_decision():
    if _puede_gestionar_simulador_decision():
        return None
    flash("Puedes analizar escenarios, pero no guardar, borrar ni crear simulaciones estrategicas.", "warning")
    return redirect(url_for("asistente.simulador_decision"))


def _reservas_periodo(conn, empresa_id, fecha_desde, fecha_hasta):
    if not _table_exists(conn, "reservas_recepcion"):
        return []
    cols = _columns(conn, "reservas_recepcion")
    campos = [
        "id",
        "codigo",
        "fecha_entrada",
        "fecha_salida",
        "regimen",
        "adultos",
        "menores",
        "estado",
        "precio_total",
        "alergias_resumen",
        "titular_nombre",
        "origen",
    ]
    select = ", ".join(c for c in campos if c in cols)
    rows = conn.execute(
        f"""
        SELECT {select}
        FROM reservas_recepcion
        WHERE empresa_id = ?
          AND date(fecha_entrada) <= date(?)
          AND date(fecha_salida) >= date(?)
          AND lower(COALESCE(estado, 'confirmada')) NOT IN ({",".join("?" for _ in ESTADOS_RESERVA_NO_ACTIVOS)})
        """,
        (empresa_id, fecha_hasta.isoformat(), fecha_desde.isoformat(), *ESTADOS_RESERVA_NO_ACTIVOS),
    ).fetchall()
    return rows


def _analisis_ocupacion(conn, empresa_id, fecha_desde, fecha_hasta):
    recomendaciones = []
    reservas = _reservas_periodo(conn, empresa_id, fecha_desde, fecha_hasta)
    habitaciones = 0
    if _table_exists(conn, "habitaciones"):
        cols = _columns(conn, "habitaciones")
        filtro_activo = "AND COALESCE(activo, 1) = 1" if "activo" in cols else ""
        filtro_empresa = "WHERE empresa_id = ?" if "empresa_id" in cols else "WHERE 1 = 1"
        params = (empresa_id,) if "empresa_id" in cols else ()
        habitaciones = int(_scalar(conn, f"SELECT COUNT(*) FROM habitaciones {filtro_empresa} {filtro_activo}", params, 0))

    ocupadas_por_dia = []
    pax_por_dia = []
    llegadas = 0
    salidas = 0
    for dia in _date_range(fecha_desde, fecha_hasta):
        ocupadas = 0
        pax = 0
        for reserva in reservas:
            entrada = _parse_date(reserva["fecha_entrada"])
            salida = _parse_date(reserva["fecha_salida"])
            if entrada == dia:
                llegadas += 1
            if salida == dia:
                salidas += 1
            if _overlap(entrada, salida, dia):
                ocupadas += 1
                pax += int(_to_float(reserva["adultos"] if "adultos" in reserva.keys() else 0, 0))
                pax += int(_to_float(reserva["menores"] if "menores" in reserva.keys() else 0, 0))
        ocupadas_por_dia.append(ocupadas)
        pax_por_dia.append(pax)

    dias = max(1, (fecha_hasta - fecha_desde).days + 1)
    ocupadas_media = sum(ocupadas_por_dia) / dias if ocupadas_por_dia else 0
    pax_media = sum(pax_por_dia) / dias if pax_por_dia else 0
    ocupacion_pct = (ocupadas_media / habitaciones * 100) if habitaciones else 0
    empleados_activos = int(_scalar(conn, "SELECT COUNT(*) FROM empleados WHERE COALESCE(activo, 1) = 1 AND empresa_id = ?", (empresa_id,), 0)) if _table_exists(conn, "empleados") else 0

    datos = {
        "periodo": f"{fecha_desde.isoformat()} / {fecha_hasta.isoformat()}",
        "reservas_activas": len(reservas),
        "habitaciones": habitaciones,
        "ocupacion_media_pct": round(ocupacion_pct, 1),
        "pax_media": round(pax_media, 1),
        "llegadas": llegadas,
        "salidas": salidas,
        "empleados_activos": empleados_activos,
    }

    if habitaciones and ocupacion_pct < 35 and empleados_activos >= 8:
        recomendaciones.append(_rec(
            "Recepcion / RRHH",
            "Ocupacion y personal",
            "alta",
            "Baja ocupacion con plantilla activa alta",
            "La ocupacion media prevista es baja para el periodo y conviene revisar turnos, vacaciones y refuerzos antes de generar sobrecoste.",
            "Revisar cuadrantes por departamento y comprobar si hay vacaciones pendientes que puedan encajar sin dejar ningun subdepartamento huerfano.",
            datos,
            url_for("rrhh.cuadrantes"),
        ))
    elif habitaciones and ocupacion_pct > 85:
        recomendaciones.append(_rec(
            "Recepcion / Operaciones",
            "Alta ocupacion",
            "alta",
            "Alta ocupacion prevista",
            "El periodo viene fuerte de ocupacion. Las entradas, salidas y servicios pueden tensionar recepcion, pisos y cocina.",
            "Asegurar cobertura de recepcion, gobernanta, cocina central y mantenimiento de guardia.",
            datos,
            url_for("recepcion.ocupacion"),
        ))

    if llegadas >= 10 or salidas >= 10:
        recomendaciones.append(_rec(
            "Recepcion / Pisos",
            "Entradas y salidas",
            "media",
            "Movimiento alto de check-in/check-out",
            "Hay volumen de entradas o salidas en el periodo. Este dato afecta a recepcion, pisos, lavanderia y mantenimiento.",
            "Cruzar llegadas y salidas con partes de pisos y refuerzo de recepcion en horas punta.",
            datos,
            url_for("recepcion.reservas"),
        ))

    if not recomendaciones:
        recomendaciones.append(_rec(
            "Recepcion",
            "Lectura general",
            "info",
            "Ocupacion sin tension destacada",
            "No se detectan picos claros de ocupacion o movimientos para este periodo.",
            "Mantener seguimiento diario y revisar de nuevo si entran reservas de canales externos.",
            datos,
            url_for("recepcion.ocupacion"),
        ))
    return recomendaciones


def _analisis_clientes_especiales(conn, empresa_id, fecha_desde, fecha_hasta):
    recomendaciones = []
    if not (_table_exists(conn, "viajeros_recepcion") and _table_exists(conn, "reservas_recepcion")):
        return recomendaciones

    rows = conn.execute(
        """
        SELECT r.codigo, r.titular_nombre, r.fecha_entrada, r.fecha_salida, r.regimen,
               COALESCE(r.alergias_resumen, '') AS alergias_reserva,
               v.nombre, v.apellidos, v.alergias_intolerancias, v.observaciones_salud,
               v.avisos_departamentos, v.nacionalidad
        FROM viajeros_recepcion v
        JOIN reservas_recepcion r ON r.id = v.reserva_id
        WHERE r.empresa_id = ?
          AND date(r.fecha_entrada) <= date(?)
          AND date(r.fecha_salida) >= date(?)
          AND lower(COALESCE(r.estado, 'confirmada')) NOT IN ({})
          AND (
              TRIM(COALESCE(r.alergias_resumen, '')) <> ''
              OR TRIM(COALESCE(v.alergias_intolerancias, '')) <> ''
              OR TRIM(COALESCE(v.observaciones_salud, '')) <> ''
              OR TRIM(COALESCE(v.avisos_departamentos, '')) <> ''
          )
        ORDER BY r.fecha_entrada, r.codigo
        LIMIT 12
        """.format(",".join("?" for _ in ESTADOS_RESERVA_NO_ACTIVOS)),
        (empresa_id, fecha_hasta.isoformat(), fecha_desde.isoformat(), *ESTADOS_RESERVA_NO_ACTIVOS),
    ).fetchall()

    for row in rows:
        texto = " ".join(
            str(row[c] or "")
            for c in ("alergias_reserva", "alergias_intolerancias", "observaciones_salud", "avisos_departamentos", "nacionalidad")
        ).lower()
        avisos = []
        if any(p in texto for p in ("halal", "musul", "magreb", "marroq", "marruec")):
            avisos.append("halal/cultural")
        if any(p in texto for p in ("celiac", "gluten")):
            avisos.append("sin gluten")
        if "apio" in texto:
            avisos.append("apio")
        if any(p in texto for p in ("fruto seco", "frutos secos", "cacahuete", "almendra", "nuez")):
            avisos.append("frutos secos")
        if any(p in texto for p in ("lactosa", "leche")):
            avisos.append("lactosa")
        prioridad = "critica" if any(a in avisos for a in ("sin gluten", "apio", "frutos secos")) else "alta"
        nombre = " ".join(str(row[c] or "").strip() for c in ("nombre", "apellidos")).strip() or row["titular_nombre"] or "Cliente"
        recomendaciones.append(_rec(
            "Recepcion / Cocina",
            "Cliente sensible",
            prioridad,
            f"Aviso alimentario o cultural: {nombre}",
            "Hay datos de alergias, intolerancias, salud o preferencias que deben llegar a cocina, sala y recepcion.",
            "Confirmar el aviso en check-in, marcarlo en produccion/servicio y preparar alternativa segura antes del servicio.",
            {
                "reserva": row["codigo"],
                "fechas": f"{row['fecha_entrada']} / {row['fecha_salida']}",
                "regimen": row["regimen"],
                "avisos_detectados": ", ".join(avisos) if avisos else "aviso manual",
            },
            url_for("recepcion.avisos"),
        ))
    return recomendaciones


def _analisis_rrhh(conn, empresa_id, fecha_desde, fecha_hasta):
    recomendaciones = []
    if not (_table_exists(conn, "empleados") and _table_exists(conn, "vacaciones_rrhh")):
        return recomendaciones

    rows = conn.execute(
        """
        SELECT v.id, v.fecha_inicio, v.fecha_fin, v.estado, v.tipo, v.dias,
               e.id AS empleado_id, e.nombre, e.apellidos, e.departamento_id, e.subdepartamento_id,
               COALESCE(d.nombre, 'Sin departamento') AS departamento,
               COALESCE(s.nombre, 'Sin subdepartamento') AS subdepartamento
        FROM vacaciones_rrhh v
        JOIN empleados e ON e.id = v.empleado_id
        LEFT JOIN departamentos d ON d.id = e.departamento_id
        LEFT JOIN subdepartamentos s ON s.id = e.subdepartamento_id
        WHERE v.empresa_id = ?
          AND date(v.fecha_inicio) <= date(?)
          AND date(v.fecha_fin) >= date(?)
          AND COALESCE(e.activo, 1) = 1
        """,
        (empresa_id, fecha_hasta.isoformat(), fecha_desde.isoformat()),
    ).fetchall()

    aprobadas = [r for r in rows if _normalizar(r["estado"]) in ESTADOS_VACACIONES_APROBADAS]
    pendientes = [r for r in rows if _normalizar(r["estado"]) in ESTADOS_VACACIONES_PENDIENTES]
    grupos = defaultdict(list)
    for row in aprobadas:
        grupos[(row["departamento_id"], row["subdepartamento_id"], row["departamento"], row["subdepartamento"])].append(row)

    for (dept_id, sub_id, depto, subdepto), ausentes in grupos.items():
        params = [empresa_id]
        filtro = "empresa_id = ? AND COALESCE(activo, 1) = 1"
        if dept_id is None:
            filtro += " AND departamento_id IS NULL"
        else:
            filtro += " AND departamento_id = ?"
            params.append(dept_id)
        if sub_id is None:
            filtro += " AND subdepartamento_id IS NULL"
        else:
            filtro += " AND subdepartamento_id = ?"
            params.append(sub_id)
        activos = int(_scalar(conn, f"SELECT COUNT(*) FROM empleados WHERE {filtro}", tuple(params), 0))
        cobertura = activos - len(ausentes)
        nombres = ", ".join((" ".join([a["nombre"] or "", a["apellidos"] or ""]).strip()) for a in ausentes[:5])
        datos = {
            "departamento": depto,
            "subdepartamento": subdepto,
            "activos": activos,
            "ausentes_aprobados": len(ausentes),
            "cobertura_estimada": cobertura,
            "personas": nombres,
        }
        if activos > 0 and cobertura <= 0:
            recomendaciones.append(_rec(
                "RRHH",
                "Cobertura",
                "critica",
                f"{subdepto} puede quedarse sin cobertura",
                "Las vacaciones aprobadas dejan el subdepartamento sin personal activo suficiente en el periodo revisado.",
                "No conceder mas ausencias ahi, revisar cambios de turno y buscar apoyo de segundo responsable o polivalentes.",
                datos,
                url_for("rrhh.vacaciones"),
            ))
        elif activos > 1 and cobertura == 1:
            recomendaciones.append(_rec(
                "RRHH",
                "Cobertura",
                "alta",
                f"{subdepto} queda con cobertura minima",
                "El subdepartamento mantiene una sola persona disponible. Cualquier baja o incidencia lo deja huerfano.",
                "Revisar cuadrante, guardias y autorizaciones pendientes antes de confirmar mas vacaciones.",
                datos,
                url_for("rrhh.cuadrante_real"),
            ))
        elif activos > 0 and len(ausentes) / max(activos, 1) >= 0.35:
            recomendaciones.append(_rec(
                "RRHH",
                "Vacaciones",
                "media",
                f"Vacaciones concentradas en {subdepto}",
                "Hay una concentracion alta de vacaciones en el mismo equipo.",
                "Contrastar con ocupacion y produccion prevista antes de cerrar el cuadrante.",
                datos,
                url_for("rrhh.vacaciones"),
            ))

    if pendientes:
        recomendaciones.append(_rec(
            "RRHH",
            "Solicitudes",
            "media",
            "Vacaciones pendientes de decidir",
            "Hay solicitudes de vacaciones dentro del periodo. Conviene decidirlas con ocupacion y cobertura delante.",
            "Revisar pendientes por departamento y evitar aprobar solicitudes que dejen un area sin responsable.",
            {
                "pendientes": len(pendientes),
                "periodo": f"{fecha_desde.isoformat()} / {fecha_hasta.isoformat()}",
            },
            url_for("rrhh.vacaciones"),
        ))

    if _table_exists(conn, "resumen_prenomina"):
        inicio_mes = date.today().replace(day=1)
        anio = inicio_mes.year
        mes = inicio_mes.month
        rows_extra = conn.execute(
            """
            SELECT rp.horas_extra_aprobadas, rp.horas_extra_no_aprobadas, rp.coste_estimado,
                   e.nombre, e.apellidos, COALESCE(d.nombre, 'Sin departamento') AS departamento,
                   COALESCE(s.nombre, 'Sin subdepartamento') AS subdepartamento
            FROM resumen_prenomina rp
            JOIN empleados e ON e.id = rp.empleado_id
            LEFT JOIN departamentos d ON d.id = e.departamento_id
            LEFT JOIN subdepartamentos s ON s.id = e.subdepartamento_id
            WHERE rp.empresa_id = ?
              AND rp.anio = ?
              AND rp.mes = ?
            """,
            (empresa_id, anio, mes),
        ).fetchall()
        horas_no_aprobadas = sum(_to_float(r["horas_extra_no_aprobadas"], 0.0) for r in rows_extra)
        horas_aprobadas = sum(_to_float(r["horas_extra_aprobadas"], 0.0) for r in rows_extra)
        coste_estimado = sum(_to_float(r["coste_estimado"], 0.0) for r in rows_extra)
        if horas_no_aprobadas > 0:
            afectados = [
                f"{(r['nombre'] or '').strip()} {(r['apellidos'] or '').strip()} ({_to_float(r['horas_extra_no_aprobadas'], 0):g} h)"
                for r in rows_extra
                if _to_float(r["horas_extra_no_aprobadas"], 0.0) > 0
            ][:8]
            recomendaciones.append(_rec(
                "RRHH / Direccion",
                "Horas extra",
                "alta",
                "Horas extra pendientes de revisar",
                "Hay horas extra no aprobadas en pre-nomina. Si vienen de bajas, libranzas trabajadas o refuerzos, la empresa debe decidir compensacion o pago.",
                "Revisar el motivo de cada extra y dejar constancia: pagar, compensar con horas/dias o corregir fichaje.",
                {
                    "mes": f"{mes:02d}/{anio}",
                    "horas_extra_no_aprobadas": round(horas_no_aprobadas, 2),
                    "personas": afectados,
                },
                url_for("rrhh.lista_prenomina"),
            ))
        elif horas_aprobadas > 0 and coste_estimado > 0:
            recomendaciones.append(_rec(
                "RRHH / Contabilidad",
                "Coste laboral",
                "media",
                "Coste de personal calculado con horas extra",
                "La pre-nomina ya tiene horas extra aprobadas. Conviene compararlas con ocupacion, bajas y necesidades reales.",
                "Cruzar coste de personal con ocupacion e ingresos antes del cierre mensual.",
                {
                    "mes": f"{mes:02d}/{anio}",
                    "horas_extra_aprobadas": round(horas_aprobadas, 2),
                    "coste_estimado": round(coste_estimado, 2),
                },
                url_for("rrhh.coste_personal"),
            ))

    return recomendaciones


def _analisis_direccion_personal(conn, empresa_id, fecha_desde, fecha_hasta):
    recomendaciones = []
    if not (_table_exists(conn, "empleados") and _table_exists(conn, "reservas_recepcion")):
        return recomendaciones

    reservas = _reservas_periodo(conn, empresa_id, fecha_desde, fecha_hasta)
    pax_noches = 0
    habitaciones = 0
    if _table_exists(conn, "habitaciones"):
        cols_hab = _columns(conn, "habitaciones")
        filtros = []
        params = []
        if "empresa_id" in cols_hab:
            filtros.append("empresa_id = ?")
            params.append(empresa_id)
        if "activo" in cols_hab:
            filtros.append("COALESCE(activo, 1) = 1")
        where = "WHERE " + " AND ".join(filtros) if filtros else ""
        habitaciones = int(_scalar(conn, f"SELECT COUNT(*) FROM habitaciones {where}", tuple(params), 0))
    ocupadas_noches = 0
    dias = max(1, (fecha_hasta - fecha_desde).days + 1)
    for dia in _date_range(fecha_desde, fecha_hasta):
        for reserva in reservas:
            entrada = _parse_date(reserva["fecha_entrada"])
            salida = _parse_date(reserva["fecha_salida"])
            if _overlap(entrada, salida, dia):
                ocupadas_noches += 1
                pax_noches += int(_to_float(reserva["adultos"] if "adultos" in reserva.keys() else 0, 0))
                pax_noches += int(_to_float(reserva["menores"] if "menores" in reserva.keys() else 0, 0))

    ocupacion_pct = ((ocupadas_noches / dias) / habitaciones * 100) if habitaciones else 0
    deptos = conn.execute(
        """
        SELECT COALESCE(d.nombre, 'Sin departamento') AS departamento,
               COUNT(*) AS empleados
        FROM empleados e
        LEFT JOIN departamentos d ON d.id = e.departamento_id
        WHERE e.empresa_id = ?
          AND COALESCE(e.activo, 1) = 1
        GROUP BY departamento
        ORDER BY empleados DESC
        """,
        (empresa_id,),
    ).fetchall()
    total_empleados = sum(int(r["empleados"]) for r in deptos)
    ratio = (pax_noches / total_empleados) if total_empleados else 0
    datos = {
        "periodo": f"{fecha_desde.isoformat()} / {fecha_hasta.isoformat()}",
        "ocupacion_media_pct": round(ocupacion_pct, 1),
        "pax_noches": pax_noches,
        "empleados_activos": total_empleados,
        "pax_noches_por_empleado": round(ratio, 2),
        "departamentos": [f"{r['departamento']}: {r['empleados']}" for r in deptos[:8]],
    }
    if total_empleados >= 8 and ocupacion_pct < 30:
        recomendaciones.append(_rec(
            "Direccion / Operaciones",
            "Productividad",
            "alta",
            "Baja ocupacion: revisar dimensionamiento por departamento",
            "La ocupacion prevista es baja frente a la plantilla activa. La IA debe avisar antes de que el coste de personal pese demasiado.",
            "Revisar vacaciones concedibles, formacion, mantenimiento preventivo, tareas de puesta a punto y turnos minimos por subdepartamento.",
            datos,
            url_for("asistente.aprendizaje"),
        ))
    elif total_empleados and ratio < 2 and ocupacion_pct < 50:
        recomendaciones.append(_rec(
            "Direccion / Operaciones",
            "Productividad",
            "media",
            "Ratio pax/persona bajo",
            "El periodo muestra pocos pax-noches por empleado activo. No es malo por si solo, pero debe mirarse con coste e ingresos.",
            "Comparar con reservas de ultima hora, eventos, bajas medicas y trabajos internos antes de tocar plantilla.",
            datos,
            url_for("rrhh.coste_personal"),
        ))
    return recomendaciones


def _analisis_stock_compras(conn, empresa_id, fecha_desde, fecha_hasta):
    recomendaciones = []
    if _table_exists(conn, "stock_almacen") and _table_exists(conn, "productos") and _table_exists(conn, "almacenes"):
        rows = conn.execute(
            """
            SELECT p.nombre AS producto, a.nombre AS almacen, sa.cantidad,
                   COALESCE(NULLIF(sa.stock_minimo, 0), p.stock_minimo, 0) AS minimo,
                   p.unidad
            FROM stock_almacen sa
            JOIN productos p ON p.id = sa.producto_id
            JOIN almacenes a ON a.id = sa.almacen_id
            WHERE COALESCE(p.activo, 1) = 1
              AND COALESCE(NULLIF(sa.stock_minimo, 0), p.stock_minimo, 0) > 0
              AND COALESCE(sa.cantidad, 0) < COALESCE(NULLIF(sa.stock_minimo, 0), p.stock_minimo, 0)
            ORDER BY (COALESCE(NULLIF(sa.stock_minimo, 0), p.stock_minimo, 0) - COALESCE(sa.cantidad, 0)) DESC
            LIMIT 8
            """
        ).fetchall()
        if rows:
            recomendaciones.append(_rec(
                "Compras / Stock",
                "Stock minimo",
                "alta",
                "Productos por debajo de minimo",
                "Hay articulos por debajo del minimo configurado. Si coinciden con produccion o alta ocupacion, pueden provocar compras urgentes.",
                "Generar necesidad o pedido, priorizando productos de alta rotacion y productos criticos APPCC.",
                {
                    "productos": [
                        f"{r['producto']} ({_to_float(r['cantidad'], 0):g}/{_to_float(r['minimo'], 0):g} {r['unidad'] or ''}) - {r['almacen']}"
                        for r in rows
                    ]
                },
                url_for("compras.necesidades_compra"),
            ))

    if _table_exists(conn, "lotes") and _table_exists(conn, "productos"):
        rows = conn.execute(
            """
            SELECT p.nombre AS producto, l.codigo_lote, l.fecha_caducidad, l.cantidad_actual, p.unidad
            FROM lotes l
            JOIN productos p ON p.id = l.producto_id
            WHERE l.empresa_id = ?
              AND COALESCE(l.estado, 'activo') = 'activo'
              AND COALESCE(l.cantidad_actual, 0) > 0
              AND l.fecha_caducidad IS NOT NULL
              AND date(l.fecha_caducidad) <= date(?)
            ORDER BY date(l.fecha_caducidad), p.nombre
            LIMIT 10
            """,
            (empresa_id, (date.today() + timedelta(days=3)).isoformat()),
        ).fetchall()
        if rows:
            recomendaciones.append(_rec(
                "APPCC / Stock",
                "Caducidades",
                "alta",
                "Lotes proximos a caducar",
                "Hay producto con vida util corta. Esto afecta a coste, merma y trazabilidad.",
                "Aplicar FIFO, valorar uso seguro en produccion o registrar deterioro/merma si no es apto.",
                {
                    "lotes": [
                        f"{r['producto']} lote {r['codigo_lote']} caduca {r['fecha_caducidad']} ({_to_float(r['cantidad_actual'], 0):g} {r['unidad'] or ''})"
                        for r in rows
                    ]
                },
                url_for("lotes.lista_lotes"),
            ))

    if _table_exists(conn, "pedidos"):
        rows = conn.execute(
            """
            SELECT numero_pedido, fecha_entrega, estado
            FROM pedidos
            WHERE empresa_id = ?
              AND lower(COALESCE(estado, '')) NOT IN ('recibido', 'cerrado', 'cancelado', 'cancelado')
              AND fecha_entrega IS NOT NULL
              AND date(fecha_entrega) <= date(?)
            ORDER BY date(fecha_entrega)
            LIMIT 10
            """,
            (empresa_id, fecha_hasta.isoformat()),
        ).fetchall()
        if rows:
            recomendaciones.append(_rec(
                "Compras",
                "Pedidos",
                "media",
                "Pedidos pendientes con entrega proxima",
                "Hay pedidos abiertos con entrega dentro del periodo analizado.",
                "Comprobar recepcion prevista, almacenes destino, etiquetas y si la documentacion economica entra como albaran o factura.",
                {"pedidos": [f"{r['numero_pedido']} - {r['fecha_entrega']} ({r['estado']})" for r in rows]},
                url_for("compras.recepcion_pedido"),
            ))
    return recomendaciones


def _analisis_produccion_appcc(conn, empresa_id, fecha_desde, fecha_hasta):
    recomendaciones = []
    reservas = _reservas_periodo(conn, empresa_id, fecha_desde, fecha_hasta)
    pax_total = sum(
        int(_to_float(r["adultos"] if "adultos" in r.keys() else 0, 0)) + int(_to_float(r["menores"] if "menores" in r.keys() else 0, 0))
        for r in reservas
    )
    if _table_exists(conn, "partes_produccion"):
        partes = int(_scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM partes_produccion
            WHERE empresa_id = ?
              AND date(fecha) BETWEEN date(?) AND date(?)
            """,
            (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
            0,
        ))
        if pax_total > 80 and partes == 0:
            recomendaciones.append(_rec(
                "Produccion",
                "Planificacion",
                "alta",
                "Ocupacion con produccion sin generar",
                "Hay reservas/pax para el periodo, pero no se detectan partes de produccion.",
                "Generar produccion desde menus y revisar recetas con raciones, alergenos, descongelacion y coste.",
                {"pax_periodo": pax_total, "partes": partes},
                url_for("produccion.vista_semanal_produccion"),
            ))
        elif partes > 0 and _table_exists(conn, "lineas_produccion"):
            pendientes = int(_scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM lineas_produccion lp
                JOIN partes_produccion pp ON pp.id = lp.parte_id
                WHERE pp.empresa_id = ?
                  AND date(pp.fecha) BETWEEN date(?) AND date(?)
                  AND lower(COALESCE(lp.estado, 'pendiente')) IN ('pendiente', 'planificado')
                """,
                (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
                0,
            ))
            if pendientes:
                recomendaciones.append(_rec(
                    "Produccion",
                    "Seguimiento",
                    "media",
                    "Lineas de produccion pendientes",
                    "Hay recetas planificadas que aun no se han empezado o cerrado.",
                    "Revisar por subdepartamento, registrar consumos reales y cerrar elaboraciones con temperatura/conservacion.",
                    {"partes": partes, "lineas_pendientes": pendientes},
                url_for("produccion.produccion_index"),
                ))

    if _table_exists(conn, "lotes") and _table_exists(conn, "productos"):
        congelados = conn.execute(
            """
            SELECT p.nombre, l.codigo_lote, l.cantidad_actual, p.unidad, l.estado_descongelado
            FROM lotes l
            JOIN productos p ON p.id = l.producto_id
            WHERE l.empresa_id = ?
              AND COALESCE(l.estado, 'activo') = 'activo'
              AND COALESCE(l.cantidad_actual, 0) > 0
              AND (
                    lower(COALESCE(p.tipo_conservacion, '')) LIKE '%congel%'
                 OR lower(COALESCE(p.familia, '') || ' ' || COALESCE(p.nombre, '')) LIKE '%congel%'
                 OR lower(COALESCE(p.familia, '') || ' ' || COALESCE(p.nombre, '')) LIKE '%cong%'
              )
              AND lower(COALESCE(p.familia, '')) NOT IN ('helados', 'bebidas', 'bebidas_agua', 'bebidas_refrescos', 'bebidas_licores', 'bebidas_premium')
              AND lower(COALESCE(p.familia, '') || ' ' || COALESCE(p.nombre, '')) NOT LIKE '%helado%'
              AND lower(COALESCE(l.estado_descongelado, 'pendiente')) IN ('pendiente', 'en_curso')
            ORDER BY lower(COALESCE(l.estado_descongelado, 'pendiente')), p.nombre
            LIMIT 8
            """,
            (empresa_id,),
        ).fetchall()
        if congelados:
            recomendaciones.append(_rec(
                "APPCC / Produccion",
                "Descongelacion",
                "alta",
                "Congelados de produccion pendientes de control",
                "Hay lotes congelados pendientes o en proceso de descongelacion.",
                "Usar la pantalla directa de congelados, registrar salida de congelador, temperatura y etiqueta antes de producir.",
                {
                    "lotes": [
                        f"{r['nombre']} lote {r['codigo_lote']} ({_to_float(r['cantidad_actual'], 0):g} {r['unidad'] or ''}) - {r['estado_descongelado'] or 'pendiente'}"
                        for r in congelados
                    ]
                },
                url_for("lotes.panel_carnes_pescados"),
            ))

    if _table_exists(conn, "mermas"):
        total_mermas = _to_float(_scalar(
            conn,
            """
            SELECT COALESCE(SUM(cantidad), 0)
            FROM mermas
            WHERE empresa_id = ?
              AND date(fecha) >= date(?)
            """,
            (empresa_id, (date.today() - timedelta(days=7)).isoformat()),
            0,
        ), 0.0)
        if total_mermas > 0:
            recomendaciones.append(_rec(
                "APPCC / Sostenibilidad",
                "Mermas",
                "media",
                "Merma registrada en los ultimos dias",
                "La merma debe verse como coste, trazabilidad y oportunidad de ajustar produccion.",
                "Revisar motivos, lotes afectados y ajustar porcentajes de recetas/produccion si se repite.",
                {"cantidad_total_ultimos_7_dias": round(total_mermas, 2)},
                url_for("produccion.lista_mermas"),
            ))
    return recomendaciones


def _analisis_mantenimiento(conn, empresa_id):
    recomendaciones = []
    if not _table_exists(conn, "ordenes_trabajo"):
        return recomendaciones

    abiertas = conn.execute(
        """
        SELECT ot.numero_orden, ot.prioridad, ot.estado, ot.descripcion, ot.fecha_apertura,
               COALESCE(em.nombre, 'Sin equipo') AS equipo
        FROM ordenes_trabajo ot
        LEFT JOIN equipos_mantenimiento em ON em.id = ot.equipo_id
        WHERE ot.empresa_id = ?
          AND lower(COALESCE(ot.estado, '')) NOT IN ({})
        ORDER BY CASE lower(COALESCE(ot.prioridad, 'media'))
                    WHEN 'critica' THEN 0 WHEN 'urgente' THEN 1 WHEN 'alta' THEN 2 ELSE 3 END,
                 ot.fecha_apertura
        LIMIT 10
        """.format(",".join("?" for _ in ESTADOS_CERRADOS)),
        (empresa_id, *ESTADOS_CERRADOS),
    ).fetchall()
    urgentes = [r for r in abiertas if _normalizar(r["prioridad"]) in ("critica", "urgente", "alta")]
    if urgentes:
        recomendaciones.append(_rec(
            "Mantenimiento",
            "Ordenes urgentes",
            "alta",
            "Ordenes de mantenimiento prioritarias abiertas",
            "Hay avisos prioritarios sin cerrar. Si afectan a habitacion, cocina, frio, agua o electricidad, pueden impactar ventas y APPCC.",
            "Asignar tecnico, revisar repuesto necesario y dejar trazabilidad de solucion.",
            {"ordenes": [f"{r['numero_orden']} - {r['equipo']} ({r['prioridad']})" for r in urgentes]},
            url_for("mantenimiento.lista_ordenes"),
        ))

    repetidas = conn.execute(
        """
        SELECT ot.equipo_id, COALESCE(em.nombre, 'Sin equipo') AS equipo, COUNT(*) AS total
        FROM ordenes_trabajo ot
        LEFT JOIN equipos_mantenimiento em ON em.id = ot.equipo_id
        WHERE ot.empresa_id = ?
          AND date(ot.fecha_apertura) >= date(?)
        GROUP BY ot.equipo_id, equipo
        HAVING COUNT(*) >= 3
        ORDER BY total DESC
        LIMIT 5
        """,
        (empresa_id, (date.today() - timedelta(days=30)).isoformat()),
    ).fetchall()
    if repetidas:
        recomendaciones.append(_rec(
            "Mantenimiento",
            "Averia repetida",
            "media",
            "Equipos con incidencias repetidas",
            "Un equipo con varias ordenes en 30 dias suele indicar preventivo insuficiente, mal uso o fin de vida.",
            "Revisar historial, valorar preventivo, repuesto fijo o sustitucion.",
            {"equipos": [f"{r['equipo']} ({r['total']} ordenes)" for r in repetidas]},
            url_for("mantenimiento.lista_equipos"),
        ))
    return recomendaciones


def _analisis_contabilidad(conn, empresa_id, fecha_hasta):
    recomendaciones = []
    if not _table_exists(conn, "contabilidad_documentos"):
        return recomendaciones
    docs = conn.execute(
        """
        SELECT tipo, numero, fecha_vencimiento, pendiente, total, estado
        FROM contabilidad_documentos
        WHERE empresa_id = ?
          AND COALESCE(pendiente, total, 0) > 0
          AND fecha_vencimiento IS NOT NULL
          AND date(fecha_vencimiento) <= date(?)
          AND lower(COALESCE(estado, '')) NOT IN ('pagado', 'cobrado', 'cancelado', 'cerrado')
        ORDER BY date(fecha_vencimiento)
        LIMIT 12
        """,
        (empresa_id, fecha_hasta.isoformat()),
    ).fetchall()
    if docs:
        total = sum(_to_float(d["pendiente"] if d["pendiente"] is not None else d["total"], 0.0) for d in docs)
        recomendaciones.append(_rec(
            "Contabilidad",
            "Tesoreria",
            "alta",
            "Vencimientos economicos proximos",
            "Hay documentos pendientes con vencimiento dentro del periodo. La IA no decide pagos, pero debe avisar antes de que falte saldo.",
            "Revisar caja/banco, priorizar vencimientos criticos y confirmar si hay facturas agrupadas de albaranes.",
            {
                "documentos": len(docs),
                "importe_pendiente": round(total, 2),
                "primeros": [f"{d['numero'] or d['tipo']} - {d['fecha_vencimiento']} ({_format_euros(d['pendiente'] if d['pendiente'] is not None else d['total'])})" for d in docs[:5]],
            },
            url_for("contabilidad.documentos"),
        ))
    return recomendaciones


def _analisis_animacion(conn, empresa_id, fecha_desde, fecha_hasta):
    recomendaciones = []
    reservas = _reservas_periodo(conn, empresa_id, fecha_desde, fecha_hasta)
    pax_noches = 0
    menores_noches = 0
    for dia in _date_range(fecha_desde, fecha_hasta):
        for reserva in reservas:
            entrada = _parse_date(reserva["fecha_entrada"])
            salida = _parse_date(reserva["fecha_salida"])
            if _overlap(entrada, salida, dia):
                pax_noches += int(_to_float(reserva["adultos"] if "adultos" in reserva.keys() else 0, 0))
                menores_noches += int(_to_float(reserva["menores"] if "menores" in reserva.keys() else 0, 0))

    actividades = []
    infantiles = 0
    asistentes = 0
    coste = 0.0
    coste_propia = 0.0
    coste_subcontratada = 0.0
    coste_mixta = 0.0
    ingreso_directo = 0.0
    venta_indirecta = 0.0
    por_modelo = defaultdict(lambda: {"actividades": 0, "asistentes": 0, "coste": 0.0, "satisfaccion": []})
    satisfaccion = 0.0
    if _table_exists(conn, "animacion_actividades"):
        cols_animacion = _columns(conn, "animacion_actividades")
        actividades = conn.execute(
            """
            SELECT *
            FROM animacion_actividades
            WHERE empresa_id = ?
              AND date(fecha) BETWEEN date(?) AND date(?)
              AND lower(COALESCE(estado, 'planificada')) NOT IN ('cancelada', 'cancelado')
            ORDER BY date(fecha), hora_inicio
            """,
            (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
        ).fetchall()
        infantiles = sum(
            1
            for a in actividades
            if (a["tipo"] or "") == "infantil" or (a["publico"] or "") in {"ninos", "familias"}
        )
        for actividad in actividades:
            coste_real = _to_float(_row_value(actividad, "coste_real"), 0.0) if "coste_real" in cols_animacion else 0.0
            coste_desglosado = 0.0
            if "coste_personal_interno" in cols_animacion:
                coste_desglosado += _to_float(_row_value(actividad, "coste_personal_interno"), 0.0)
            if "coste_proveedor" in cols_animacion:
                coste_desglosado += _to_float(_row_value(actividad, "coste_proveedor"), 0.0)
            if "coste_materiales" in cols_animacion:
                coste_desglosado += _to_float(_row_value(actividad, "coste_materiales"), 0.0)
            coste_actividad = coste_real or coste_desglosado or _to_float(_row_value(actividad, "coste_estimado"), 0.0)
            modelo = (_row_value(actividad, "modelo_prestacion", "propia") or "propia").strip().lower()
            if modelo not in {"propia", "subcontratada", "mixta"}:
                modelo = "propia"
            coste += coste_actividad
            ingreso_directo += _to_float(_row_value(actividad, "ingreso_directo"), 0.0)
            venta_indirecta += _to_float(_row_value(actividad, "venta_indirecta_estimada"), 0.0)
            por_modelo[modelo]["actividades"] += 1
            por_modelo[modelo]["coste"] += coste_actividad
            if modelo == "subcontratada":
                coste_subcontratada += coste_actividad
            elif modelo == "mixta":
                coste_mixta += coste_actividad
            else:
                coste_propia += coste_actividad
        if _table_exists(conn, "animacion_asistencias") and actividades:
            modelo_expr = "COALESCE(a.modelo_prestacion, 'propia')" if "modelo_prestacion" in cols_animacion else "'propia'"
            rows_asistencia = conn.execute(
                f"""
                SELECT COALESCE(SUM(aa.pax), 0) AS asistentes,
                       AVG(aa.satisfaccion) AS satisfaccion,
                       {modelo_expr} AS modelo
                FROM animacion_asistencias aa
                JOIN animacion_actividades a ON a.id = aa.actividad_id
                WHERE a.empresa_id = ?
                  AND date(a.fecha) BETWEEN date(?) AND date(?)
                GROUP BY {modelo_expr}
                """,
                (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
            ).fetchall()
            for row in rows_asistencia:
                modelo = (row["modelo"] or "propia").strip().lower()
                if modelo not in {"propia", "subcontratada", "mixta"}:
                    modelo = "propia"
                asistentes_modelo = int(row["asistentes"] or 0)
                satisfaccion_modelo = _to_float(row["satisfaccion"], 0.0)
                asistentes += asistentes_modelo
                por_modelo[modelo]["asistentes"] += asistentes_modelo
                if satisfaccion_modelo:
                    por_modelo[modelo]["satisfaccion"].append(satisfaccion_modelo)
            satisfacciones_modelos = [valor for datos_modelo in por_modelo.values() for valor in datos_modelo["satisfaccion"]]
            satisfaccion = (sum(satisfacciones_modelos) / len(satisfacciones_modelos)) if satisfacciones_modelos else 0.0

    datos = {
        "periodo": f"{fecha_desde.isoformat()} / {fecha_hasta.isoformat()}",
        "pax_noches": pax_noches,
        "menores_noches": menores_noches,
        "actividades": len(actividades),
        "infantiles_familiares": infantiles,
        "asistentes": asistentes,
        "coste": round(coste, 2),
        "coste_propia": round(coste_propia, 2),
        "coste_subcontratada": round(coste_subcontratada, 2),
        "coste_mixta": round(coste_mixta, 2),
        "ingreso_directo": round(ingreso_directo, 2),
        "venta_indirecta": round(venta_indirecta, 2),
        "satisfaccion": round(satisfaccion, 1),
    }

    if pax_noches >= 120 and not actividades:
        recomendaciones.append(_rec(
            "Animacion",
            "Experiencia cliente",
            "alta",
            "Ocupacion con agenda de animacion vacia",
            "Hay volumen de clientes alojados, pero no se detectan actividades programadas. En vacacional esto afecta a experiencia y reputacion.",
            "Programar actividades por publico: infantil/familiar de dia y show o musica por la tarde/noche.",
            datos,
            url_for("animacion.dashboard"),
        ))
    if menores_noches >= 25 and infantiles == 0:
        recomendaciones.append(_rec(
            "Animacion",
            "Familias",
            "alta",
            "Menores alojados sin actividad infantil/familiar",
            "Hay menores previstos y no se detectan actividades infantiles o familiares. Las familias miran mucho este punto.",
            "Crear actividades infantiles, miniclub o juegos familiares y avisar a recepcion para comunicarlo en check-in.",
            datos,
            url_for("animacion.nueva_actividad"),
        ))
    if actividades and asistentes == 0 and fecha_hasta <= date.today():
        recomendaciones.append(_rec(
            "Animacion",
            "Seguimiento",
            "media",
            "Actividades sin asistencia registrada",
            "Hay actividades, pero no asistencia. Sin ese dato la IA no puede saber que funciona y que no engancha.",
            "Registrar pax, menores y satisfaccion despues de cada actividad.",
            datos,
            url_for("animacion.dashboard"),
        ))
    if asistentes > 0 and satisfaccion and satisfaccion < 3.5:
        recomendaciones.append(_rec(
            "Animacion",
            "Satisfaccion",
            "media",
            "Satisfaccion de animacion baja",
            "La satisfaccion media registrada es baja. Puede afectar opiniones y repeticion de clientes.",
            "Revisar idioma, horario, ubicacion, tipo de cliente y formato de actividad.",
            datos,
            url_for("animacion.dashboard"),
        ))
    if asistentes > 0 and coste / max(asistentes, 1) > 8:
        recomendaciones.append(_rec(
            "Animacion / Gestion",
            "Coste",
            "media",
            "Coste de animacion alto por asistente",
            "El coste por asistente es elevado para el periodo analizado.",
            "Valorar si la actividad mejora reputacion/venta indirecta o si conviene cambiar formato, horario o proveedor.",
            {**datos, "coste_por_asistente": round(coste / max(asistentes, 1), 2)},
            url_for("asistente.gestion_360"),
        ))
    if coste_subcontratada > 0 and por_modelo["subcontratada"]["asistentes"] == 0 and fecha_hasta <= date.today():
        recomendaciones.append(_rec(
            "Animacion / Gestion",
            "Proveedor externo",
            "media",
            "Animacion subcontratada sin asistencia medida",
            "Hay coste de proveedor externo, pero no asistencia asociada. Asi no se puede valorar si la empresa externa aporta experiencia o margen indirecto.",
            "Exigir registro de pax, horario, satisfaccion y observaciones por actividad subcontratada.",
            datos,
            url_for("animacion.dashboard"),
        ))
    propia_pax = por_modelo["propia"]["asistentes"]
    externa_pax = por_modelo["subcontratada"]["asistentes"]
    if propia_pax > 0 and externa_pax > 0:
        coste_pax_propia = por_modelo["propia"]["coste"] / propia_pax
        coste_pax_externa = por_modelo["subcontratada"]["coste"] / externa_pax
        if coste_pax_externa > coste_pax_propia * 1.35:
            recomendaciones.append(_rec(
                "Animacion / Gestion",
                "Coste comparado",
                "media",
                "Actividad externa mas cara por asistente que la propia",
                "La animacion subcontratada sale bastante mas cara por asistente que la propia en el periodo analizado.",
                "Comparar calidad, satisfaccion, repeticion y ventas indirectas antes de renovar o ampliar el proveedor.",
                {**datos, "coste_pax_propia": round(coste_pax_propia, 2), "coste_pax_externa": round(coste_pax_externa, 2)},
                url_for("asistente.gestion_360"),
            ))
    return recomendaciones


def _metricas_gestion_360(conn, empresa_id, fecha_desde, fecha_hasta):
    metricas = {
        "fecha_desde": fecha_desde.isoformat(),
        "fecha_hasta": fecha_hasta.isoformat(),
        "ingresos_contabilidad": 0.0,
        "gastos_contabilidad": 0.0,
        "ingresos_recepcion": 0.0,
        "ingresos_tpv": 0.0,
        "reservas_estimadas": 0.0,
        "comisiones_canal": 0.0,
        "pendiente_cobro": 0.0,
        "pendiente_pago": 0.0,
        "coste_personal": 0.0,
        "horas_extra": 0.0,
        "coste_produccion": 0.0,
        "coste_produccion_estimado": 0.0,
        "coste_mantenimiento": 0.0,
        "coste_mermas": 0.0,
        "coste_animacion": 0.0,
        "coste_animacion_propia": 0.0,
        "coste_animacion_subcontratada": 0.0,
        "coste_animacion_mixta": 0.0,
        "coste_animacion_interno": 0.0,
        "coste_animacion_proveedor": 0.0,
        "coste_animacion_materiales": 0.0,
        "ingreso_animacion_directo": 0.0,
        "venta_indirecta_animacion": 0.0,
        "impacto_animacion": 0.0,
        "asistentes_animacion": 0,
        "actividades_animacion": 0,
        "actividades_animacion_propia": 0,
        "actividades_animacion_subcontratada": 0,
        "actividades_animacion_mixta": 0,
        "satisfaccion_animacion": 0.0,
        "compras_periodo": 0.0,
        "documentos_venta": 0,
        "documentos_gasto": 0,
        "reservas": 0,
        "cuentas_tpv": 0,
        "ordenes_mantenimiento": 0,
        "mermas": 0,
        "quejas_cliente": 0,
        "quejas_abiertas": 0,
        "quejas_sensibles": 0,
        "quejas_vencidas": 0,
        "compensaciones_cliente": 0.0,
    }

    if _table_exists(conn, "contabilidad_documentos"):
        row = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN tipo IN ('factura_venta', 'ticket_venta') THEN total ELSE 0 END), 0) AS ingresos,
                COALESCE(SUM(CASE WHEN tipo NOT IN ('factura_venta', 'ticket_venta') THEN total ELSE 0 END), 0) AS gastos,
                COALESCE(SUM(CASE WHEN tipo IN ('factura_venta', 'ticket_venta') THEN pendiente ELSE 0 END), 0) AS pendiente_cobro,
                COALESCE(SUM(CASE WHEN tipo NOT IN ('factura_venta', 'ticket_venta') THEN pendiente ELSE 0 END), 0) AS pendiente_pago,
                SUM(CASE WHEN tipo IN ('factura_venta', 'ticket_venta') THEN 1 ELSE 0 END) AS docs_venta,
                SUM(CASE WHEN tipo NOT IN ('factura_venta', 'ticket_venta') THEN 1 ELSE 0 END) AS docs_gasto
            FROM contabilidad_documentos
            WHERE empresa_id = ?
              AND date(fecha_documento) BETWEEN date(?) AND date(?)
            """,
            (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
        ).fetchone()
        metricas["ingresos_contabilidad"] = _to_float(row["ingresos"], 0.0)
        metricas["gastos_contabilidad"] = _to_float(row["gastos"], 0.0)
        metricas["pendiente_cobro"] = _to_float(row["pendiente_cobro"], 0.0)
        metricas["pendiente_pago"] = _to_float(row["pendiente_pago"], 0.0)
        metricas["documentos_venta"] = int(row["docs_venta"] or 0)
        metricas["documentos_gasto"] = int(row["docs_gasto"] or 0)

    if _table_exists(conn, "recepcion_facturas"):
        row = conn.execute(
            """
            SELECT COALESCE(SUM(total), 0) AS total,
                   COALESCE(SUM(saldo), 0) AS saldo,
                   COUNT(*) AS docs
            FROM recepcion_facturas
            WHERE empresa_id = ?
              AND date(fecha) BETWEEN date(?) AND date(?)
              AND lower(COALESCE(estado, 'emitida')) NOT IN ('anulada', 'rectificada')
            """,
            (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
        ).fetchone()
        metricas["ingresos_recepcion"] = _to_float(row["total"], 0.0)
        metricas["pendiente_cobro"] = max(metricas["pendiente_cobro"], _to_float(row["saldo"], 0.0))

    if _table_exists(conn, "reservas_recepcion"):
        row = conn.execute(
            """
            SELECT COALESCE(SUM(precio_total + COALESCE(extras_total, 0)), 0) AS total,
                   COALESCE(SUM(comision_canal), 0) AS comisiones,
                   COALESCE(SUM(saldo), 0) AS saldo,
                   COUNT(*) AS reservas
            FROM reservas_recepcion
            WHERE empresa_id = ?
              AND date(fecha_entrada) BETWEEN date(?) AND date(?)
              AND lower(COALESCE(estado, 'confirmada')) NOT IN ({})
            """.format(",".join("?" for _ in ESTADOS_RESERVA_NO_ACTIVOS)),
            (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat(), *ESTADOS_RESERVA_NO_ACTIVOS),
        ).fetchone()
        metricas["reservas_estimadas"] = _to_float(row["total"], 0.0)
        metricas["comisiones_canal"] = _to_float(row["comisiones"], 0.0)
        metricas["pendiente_cobro"] = max(metricas["pendiente_cobro"], _to_float(row["saldo"], 0.0))
        metricas["reservas"] = int(row["reservas"] or 0)

    if _table_exists(conn, "tpv_cuentas"):
        row = conn.execute(
            """
            SELECT COALESCE(SUM(total), 0) AS total,
                   COUNT(*) AS cuentas
            FROM tpv_cuentas
            WHERE empresa_id = ?
              AND lower(COALESCE(estado, '')) IN ('cerrada', 'cerrado', 'pagada', 'pagado')
              AND date(COALESCE(fecha_cierre, fecha_apertura)) BETWEEN date(?) AND date(?)
            """,
            (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
        ).fetchone()
        metricas["ingresos_tpv"] = _to_float(row["total"], 0.0)
        metricas["cuentas_tpv"] = int(row["cuentas"] or 0)

    if _table_exists(conn, "resumen_prenomina"):
        meses = {(dia.year, dia.month) for dia in _date_range(fecha_desde, fecha_hasta)}
        total_coste = 0.0
        total_extra = 0.0
        for anio, mes in meses:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(coste_estimado), 0) AS coste,
                       COALESCE(SUM(horas_extra_aprobadas), 0) + COALESCE(SUM(horas_extra_no_aprobadas), 0) AS extras
                FROM resumen_prenomina
                WHERE empresa_id = ? AND anio = ? AND mes = ?
                """,
                (empresa_id, anio, mes),
            ).fetchone()
            total_coste += _to_float(row["coste"], 0.0)
            total_extra += _to_float(row["extras"], 0.0)
        metricas["coste_personal"] = total_coste
        metricas["horas_extra"] = total_extra

    if _table_exists(conn, "partes_produccion") and _table_exists(conn, "lineas_produccion"):
        lp_cols = _columns(conn, "lineas_produccion")
        coste_estimado_expr = "COALESCE(lp.coste_estimado, 0)" if "coste_estimado" in lp_cols else "0"
        if "coste_real" in lp_cols and "coste_estimado" in lp_cols:
            coste_real_expr = "COALESCE(lp.coste_real, lp.coste_estimado, 0)"
        elif "coste_real" in lp_cols:
            coste_real_expr = "COALESCE(lp.coste_real, 0)"
        elif "coste_estimado" in lp_cols:
            coste_real_expr = "COALESCE(lp.coste_estimado, 0)"
        else:
            coste_real_expr = "0"
        row = conn.execute(
            f"""
            SELECT COALESCE(SUM({coste_real_expr}), 0) AS coste_real,
                   COALESCE(SUM({coste_estimado_expr}), 0) AS coste_estimado
            FROM lineas_produccion lp
            JOIN partes_produccion pp ON pp.id = lp.parte_id
            WHERE pp.empresa_id = ?
              AND date(pp.fecha) BETWEEN date(?) AND date(?)
            """,
            (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
        ).fetchone()
        metricas["coste_produccion"] = _to_float(row["coste_real"], 0.0)
        metricas["coste_produccion_estimado"] = _to_float(row["coste_estimado"], 0.0)

    if _table_exists(conn, "recepciones") and _table_exists(conn, "lineas_recepcion"):
        row = conn.execute(
            """
            SELECT COALESCE(SUM(COALESCE(lr.importe_total, lr.cantidad_recibida * lr.precio_unitario, 0)), 0) AS total
            FROM lineas_recepcion lr
            JOIN recepciones r ON r.id = lr.recepcion_id
            WHERE r.empresa_id = ?
              AND date(r.fecha_recepcion) BETWEEN date(?) AND date(?)
            """,
            (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
        ).fetchone()
        metricas["compras_periodo"] = _to_float(row["total"], 0.0)

    if _table_exists(conn, "ordenes_trabajo"):
        row = conn.execute(
            """
            SELECT COALESCE(SUM(COALESCE(coste_real, coste_estimado, 0)), 0) AS total,
                   COUNT(*) AS total_ordenes
            FROM ordenes_trabajo
            WHERE empresa_id = ?
              AND date(fecha_apertura) BETWEEN date(?) AND date(?)
            """,
            (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
        ).fetchone()
        metricas["coste_mantenimiento"] = _to_float(row["total"], 0.0)
        metricas["ordenes_mantenimiento"] = int(row["total_ordenes"] or 0)

    if _table_exists(conn, "animacion_actividades"):
        cols_animacion = _columns(conn, "animacion_actividades")
        coste_real_expr = "COALESCE(coste_real, 0)" if "coste_real" in cols_animacion else "0"
        coste_estimado_expr = "COALESCE(coste_estimado, 0)" if "coste_estimado" in cols_animacion else "0"
        coste_desglose_partes = []
        if "coste_personal_interno" in cols_animacion:
            coste_desglose_partes.append("COALESCE(coste_personal_interno, 0)")
        if "coste_proveedor" in cols_animacion:
            coste_desglose_partes.append("COALESCE(coste_proveedor, 0)")
        if "coste_materiales" in cols_animacion:
            coste_desglose_partes.append("COALESCE(coste_materiales, 0)")
        coste_desglose_expr = " + ".join(coste_desglose_partes) if coste_desglose_partes else "0"
        coste_expr = (
            f"CASE WHEN {coste_real_expr} > 0 THEN {coste_real_expr} "
            f"WHEN ({coste_desglose_expr}) > 0 THEN ({coste_desglose_expr}) "
            f"ELSE {coste_estimado_expr} END"
        )
        modelo_expr = "lower(COALESCE(modelo_prestacion, 'propia'))" if "modelo_prestacion" in cols_animacion else "'propia'"
        ingreso_expr = "COALESCE(ingreso_directo, 0)" if "ingreso_directo" in cols_animacion else "0"
        venta_expr = "COALESCE(venta_indirecta_estimada, 0)" if "venta_indirecta_estimada" in cols_animacion else "0"
        interno_expr = "COALESCE(coste_personal_interno, 0)" if "coste_personal_interno" in cols_animacion else "0"
        proveedor_expr = "COALESCE(coste_proveedor, 0)" if "coste_proveedor" in cols_animacion else "0"
        materiales_expr = "COALESCE(coste_materiales, 0)" if "coste_materiales" in cols_animacion else "0"
        row = conn.execute(
            f"""
            SELECT COALESCE(SUM({coste_expr}), 0) AS coste,
                   COALESCE(SUM(CASE WHEN {modelo_expr} = 'propia' THEN {coste_expr} ELSE 0 END), 0) AS coste_propia,
                   COALESCE(SUM(CASE WHEN {modelo_expr} = 'subcontratada' THEN {coste_expr} ELSE 0 END), 0) AS coste_subcontratada,
                   COALESCE(SUM(CASE WHEN {modelo_expr} = 'mixta' THEN {coste_expr} ELSE 0 END), 0) AS coste_mixta,
                   COALESCE(SUM({interno_expr}), 0) AS coste_interno,
                   COALESCE(SUM({proveedor_expr}), 0) AS coste_proveedor,
                   COALESCE(SUM({materiales_expr}), 0) AS coste_materiales,
                   COALESCE(SUM({ingreso_expr}), 0) AS ingreso_directo,
                   COALESCE(SUM({venta_expr}), 0) AS venta_indirecta,
                   COUNT(*) AS actividades,
                   SUM(CASE WHEN {modelo_expr} = 'propia' THEN 1 ELSE 0 END) AS propias,
                   SUM(CASE WHEN {modelo_expr} = 'subcontratada' THEN 1 ELSE 0 END) AS subcontratadas,
                   SUM(CASE WHEN {modelo_expr} = 'mixta' THEN 1 ELSE 0 END) AS mixtas
            FROM animacion_actividades
            WHERE empresa_id = ?
              AND date(fecha) BETWEEN date(?) AND date(?)
            """,
            (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
        ).fetchone()
        metricas["coste_animacion"] = _to_float(row["coste"], 0.0)
        metricas["coste_animacion_propia"] = _to_float(row["coste_propia"], 0.0)
        metricas["coste_animacion_subcontratada"] = _to_float(row["coste_subcontratada"], 0.0)
        metricas["coste_animacion_mixta"] = _to_float(row["coste_mixta"], 0.0)
        metricas["coste_animacion_interno"] = _to_float(row["coste_interno"], 0.0)
        metricas["coste_animacion_proveedor"] = _to_float(row["coste_proveedor"], 0.0)
        metricas["coste_animacion_materiales"] = _to_float(row["coste_materiales"], 0.0)
        metricas["ingreso_animacion_directo"] = _to_float(row["ingreso_directo"], 0.0)
        metricas["venta_indirecta_animacion"] = _to_float(row["venta_indirecta"], 0.0)
        metricas["impacto_animacion"] = (
            metricas["ingreso_animacion_directo"]
            + metricas["venta_indirecta_animacion"]
            - metricas["coste_animacion"]
        )
        metricas["actividades_animacion"] = int(row["actividades"] or 0)
        metricas["actividades_animacion_propia"] = int(row["propias"] or 0)
        metricas["actividades_animacion_subcontratada"] = int(row["subcontratadas"] or 0)
        metricas["actividades_animacion_mixta"] = int(row["mixtas"] or 0)
        if _table_exists(conn, "animacion_asistencias"):
            row_asistencia = conn.execute(
                """
                SELECT COALESCE(SUM(aa.pax), 0) AS asistentes,
                       AVG(aa.satisfaccion) AS satisfaccion
                FROM animacion_asistencias aa
                JOIN animacion_actividades a ON a.id = aa.actividad_id
                WHERE a.empresa_id = ?
                  AND date(a.fecha) BETWEEN date(?) AND date(?)
                """,
                (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
            ).fetchone()
            metricas["asistentes_animacion"] = int(row_asistencia["asistentes"] or 0)
            metricas["satisfaccion_animacion"] = _to_float(row_asistencia["satisfaccion"], 0.0)

    if _table_exists(conn, "experiencia_quejas"):
        row = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN estado IN ('abierta', 'investigacion', 'accion', 'seguimiento') THEN 1 ELSE 0 END) AS abiertas,
                   SUM(CASE WHEN gravedad IN ('alta', 'critica') AND estado IN ('abierta', 'investigacion', 'accion', 'seguimiento') THEN 1 ELSE 0 END) AS sensibles,
                   SUM(CASE WHEN fecha_limite IS NOT NULL AND fecha_limite < date('now') AND estado IN ('abierta', 'investigacion', 'accion', 'seguimiento') THEN 1 ELSE 0 END) AS vencidas,
                   COALESCE(SUM(compensacion_importe), 0) AS compensaciones
            FROM experiencia_quejas
            WHERE empresa_id = ?
              AND date(fecha) BETWEEN date(?) AND date(?)
            """,
            (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
        ).fetchone()
        metricas["quejas_cliente"] = int(row["total"] or 0)
        metricas["quejas_abiertas"] = int(row["abiertas"] or 0)
        metricas["quejas_sensibles"] = int(row["sensibles"] or 0)
        metricas["quejas_vencidas"] = int(row["vencidas"] or 0)
        metricas["compensaciones_cliente"] = _to_float(row["compensaciones"], 0.0)

    if _table_exists(conn, "mermas") and _table_exists(conn, "productos"):
        row = conn.execute(
            """
            SELECT COALESCE(SUM(m.cantidad * COALESCE(p.precio, 0)), 0) AS total,
                   COUNT(*) AS total_mermas
            FROM mermas m
            JOIN productos p ON p.id = m.producto_id
            WHERE m.empresa_id = ?
              AND date(m.fecha) BETWEEN date(?) AND date(?)
            """,
            (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
        ).fetchone()
        metricas["coste_mermas"] = _to_float(row["total"], 0.0)
        metricas["mermas"] = int(row["total_mermas"] or 0)

    ingresos_operativos = max(
        metricas["ingresos_contabilidad"],
        metricas["ingresos_recepcion"] + metricas["ingresos_tpv"],
        metricas["reservas_estimadas"] + metricas["ingresos_tpv"],
    )
    costes_detectados = (
        metricas["gastos_contabilidad"]
        + metricas["coste_personal"]
        + metricas["coste_produccion"]
        + metricas["coste_mantenimiento"]
        + metricas["coste_mermas"]
        + metricas["coste_animacion"]
        + metricas["compensaciones_cliente"]
    )
    if metricas["gastos_contabilidad"] <= 0 and metricas["compras_periodo"] > 0:
        costes_detectados += metricas["compras_periodo"]
    metricas["ingresos_operativos"] = ingresos_operativos
    metricas["costes_detectados"] = costes_detectados
    metricas["resultado_aprox"] = ingresos_operativos - costes_detectados
    metricas["margen_aprox_pct"] = (metricas["resultado_aprox"] / ingresos_operativos * 100) if ingresos_operativos else 0.0
    metricas["ratio_personal_pct"] = (metricas["coste_personal"] / ingresos_operativos * 100) if ingresos_operativos else 0.0
    metricas["ratio_produccion_pct"] = (metricas["coste_produccion"] / ingresos_operativos * 100) if ingresos_operativos else 0.0
    metricas["ratio_comisiones_pct"] = (metricas["comisiones_canal"] / metricas["reservas_estimadas"] * 100) if metricas["reservas_estimadas"] else 0.0
    metricas["ventas_sincronizadas"] = metricas["ingresos_contabilidad"] >= (metricas["ingresos_recepcion"] + metricas["ingresos_tpv"]) * 0.8 if (metricas["ingresos_recepcion"] + metricas["ingresos_tpv"]) > 0 else True
    return metricas


def _analisis_gestion_360(conn, empresa_id, fecha_desde, fecha_hasta):
    recomendaciones = []
    m = _metricas_gestion_360(conn, empresa_id, fecha_desde, fecha_hasta)
    datos_base = {
        "ingresos_operativos": round(m["ingresos_operativos"], 2),
        "costes_detectados": round(m["costes_detectados"], 2),
        "resultado_aprox": round(m["resultado_aprox"], 2),
        "margen_aprox_pct": round(m["margen_aprox_pct"], 1),
        "coste_personal_pct": round(m["ratio_personal_pct"], 1),
        "coste_produccion_pct": round(m["ratio_produccion_pct"], 1),
    }

    if m["ingresos_operativos"] <= 0 and (m["costes_detectados"] > 0 or m["reservas"] > 0):
        recomendaciones.append(_rec(
            "Gestion 360",
            "Ingresos",
            "alta",
            "Costes o reservas sin ingresos consolidados",
            "Hay actividad, costes o reservas, pero no aparecen ingresos suficientes en el periodo. Puede faltar facturacion, sincronizacion de ventas o precios en reservas.",
            "Revisar facturas de recepcion, TPV y sincronizacion contable antes de interpretar el margen.",
            datos_base,
            url_for("asistente.gestion_360"),
        ))
    elif m["resultado_aprox"] < 0:
        recomendaciones.append(_rec(
            "Gestion 360",
            "Margen",
            "alta",
            "Resultado operativo aproximado negativo",
            "Con los datos detectados, los costes superan los ingresos del periodo. No es contabilidad oficial, pero es una senal fuerte.",
            "Revisar precios, personal, compras, produccion y gastos documentados antes de cerrar decisiones.",
            datos_base,
            url_for("contabilidad.explotacion"),
        ))
    elif m["ingresos_operativos"] > 0 and m["margen_aprox_pct"] < 12:
        recomendaciones.append(_rec(
            "Gestion 360",
            "Margen",
            "media",
            "Margen operativo estrecho",
            "El margen aproximado es bajo. Puede ser normal en arranque, baja ocupacion o dias de compras fuertes, pero debe revisarse.",
            "Comparar margen por servicio/departamento y revisar si la produccion o plantilla estan sobredimensionadas.",
            datos_base,
            url_for("asistente.gestion_360"),
        ))

    if m["ratio_personal_pct"] > 38:
        recomendaciones.append(_rec(
            "RRHH / Gestion",
            "Coste personal",
            "alta",
            "Coste de personal alto frente a ingresos",
            "El peso estimado del personal sobre ingresos es elevado para el periodo.",
            "Cruzar cuadrantes con ocupacion, vacaciones, bajas, horas extra y tareas internas antes de reforzar turnos.",
            {**datos_base, "horas_extra": round(m["horas_extra"], 2)},
            url_for("rrhh.coste_personal"),
        ))

    if m["ratio_produccion_pct"] > 32:
        recomendaciones.append(_rec(
            "Produccion / Gestion",
            "Coste produccion",
            "alta",
            "Coste de produccion alto frente a ingresos",
            "El coste de produccion detectado pesa demasiado sobre el ingreso operativo. Puede haber exceso de raciones, merma o ingredientes caros.",
            "Revisar produccion por servicio, platos estrella/ruina, sobrantes y mermas antes de repetir menu.",
            {**datos_base, "coste_produccion": round(m["coste_produccion"], 2), "coste_mermas": round(m["coste_mermas"], 2)},
            url_for("produccion.costes_produccion"),
        ))

    if m["coste_animacion"] > 0 and m["asistentes_animacion"] > 0:
        coste_pax_animacion = m["coste_animacion"] / max(m["asistentes_animacion"], 1)
        if coste_pax_animacion > 8:
            recomendaciones.append(_rec(
                "Animacion / Gestion",
                "Coste experiencia",
                "media",
                "Coste de animacion alto por asistente",
                "La animacion pesa bastante por cliente registrado. Puede estar bien si mejora reputacion o venta indirecta, pero hay que medirlo.",
                "Comparar coste propio, proveedor externo, satisfaccion, asistentes y venta indirecta antes de repetir formato.",
                {**datos_base, "coste_pax_animacion": round(coste_pax_animacion, 2), "impacto_animacion": round(m["impacto_animacion"], 2)},
                url_for("animacion.dashboard"),
            ))

    if m["coste_animacion_subcontratada"] > 0 and m["actividades_animacion_subcontratada"] > 0:
        peso_externo = m["coste_animacion_subcontratada"] / max(m["coste_animacion"], 1) * 100
        if peso_externo > 60 and m["satisfaccion_animacion"] and m["satisfaccion_animacion"] < 4:
            recomendaciones.append(_rec(
                "Animacion / Proveedores",
                "Subcontrata",
                "media",
                "Proveedor externo con peso alto y satisfaccion mejorable",
                "La subcontrata concentra gran parte del coste de animacion y la satisfaccion registrada no destaca.",
                "Revisar contrato, idioma, horarios, contenido y alternativa interna/mixta antes de renovar.",
                {**datos_base, "peso_externo_pct": round(peso_externo, 1), "satisfaccion_animacion": round(m["satisfaccion_animacion"], 1)},
                url_for("animacion.dashboard"),
            ))

    if m["ratio_comisiones_pct"] > 14:
        recomendaciones.append(_rec(
            "Recepcion / Ventas",
            "Canales",
            "media",
            "Comision de canales elevada",
            "La comision de reservas pesa bastante. La IA debe vigilar si el precio neto deja margen suficiente.",
            "Comparar canal, regimen, coste de servicio y margen; potenciar venta directa si procede.",
            {"reservas_estimadas": round(m["reservas_estimadas"], 2), "comisiones": round(m["comisiones_canal"], 2), "comision_pct": round(m["ratio_comisiones_pct"], 1)},
            url_for("recepcion.canales_reserva"),
        ))

    if m["quejas_sensibles"] > 0:
        recomendaciones.append(_rec(
            "Experiencia cliente",
            "RRPP",
            "alta",
            "Quejas sensibles abiertas",
            "Hay quejas de cliente de gravedad alta o critica sin cerrar. Esto afecta reputacion, repeticion y riesgo operativo.",
            "Revisar expediente, comprobaciones, acciones, compensacion y seguimiento antes del cierre.",
            {
                "quejas_sensibles": m["quejas_sensibles"],
                "quejas_abiertas": m["quejas_abiertas"],
                "compensaciones": round(m["compensaciones_cliente"], 2),
            },
            url_for("experiencia.dashboard"),
        ))

    if m["quejas_vencidas"] > 0:
        recomendaciones.append(_rec(
            "Experiencia cliente",
            "Plazos",
            "alta",
            "Quejas con plazo vencido",
            "Hay expedientes de cliente que pasaron su fecha limite. El problema puede escalar aunque la causa inicial fuera pequena.",
            "Asignar responsable, llamar al cliente si procede y cerrar acciones pendientes.",
            {"quejas_vencidas": m["quejas_vencidas"], "quejas_abiertas": m["quejas_abiertas"]},
            url_for("experiencia.quejas"),
        ))

    if m["compensaciones_cliente"] > max(150, m["ingresos_operativos"] * 0.015):
        recomendaciones.append(_rec(
            "Experiencia cliente",
            "Compensaciones",
            "media",
            "Compensaciones de cliente relevantes",
            "El importe de compensaciones empieza a pesar en el margen. Puede ser necesario, pero debe dejar aprendizaje.",
            "Buscar causa raiz por habitacion, servicio, turno, proveedor o punto de venta para reducir repeticion.",
            {
                "compensaciones": round(m["compensaciones_cliente"], 2),
                "ingresos_operativos": round(m["ingresos_operativos"], 2),
            },
            url_for("experiencia.dashboard"),
        ))

    if not m["ventas_sincronizadas"]:
        recomendaciones.append(_rec(
            "Contabilidad / Gestion",
            "Sincronizacion",
            "media",
            "Ventas no sincronizadas completamente con contabilidad",
            "Recepcion/TPV muestran ventas superiores a lo consolidado en contabilidad.",
            "Ejecutar o revisar sincronizacion de ventas para que explotacion y margen sean fiables.",
            {
                "ingresos_contabilidad": round(m["ingresos_contabilidad"], 2),
                "ingresos_recepcion_tpv": round(m["ingresos_recepcion"] + m["ingresos_tpv"], 2),
            },
            url_for("contabilidad.documentos"),
        ))

    if m["pendiente_cobro"] > 0 and m["pendiente_cobro"] > max(300, m["ingresos_operativos"] * 0.15):
        recomendaciones.append(_rec(
            "Gestion 360",
            "Cobros",
            "media",
            "Pendiente de cobro relevante",
            "Hay saldo pendiente de cobro que puede afectar caja y bancos.",
            "Revisar facturas, cargos a habitacion, saldos de reservas y cierre de caja.",
            {"pendiente_cobro": round(m["pendiente_cobro"], 2), "ingresos_operativos": round(m["ingresos_operativos"], 2)},
            url_for("recepcion.facturas"),
        ))

    if m["pendiente_pago"] > 0 and m["pendiente_pago"] > max(500, m["ingresos_operativos"] * 0.2):
        recomendaciones.append(_rec(
            "Gestion 360",
            "Pagos",
            "media",
            "Pendiente de pago relevante",
            "Hay pagos pendientes que deben cruzarse con saldo de caja/banco y previsiones de cobro.",
            "Priorizar vencimientos, proveedores criticos y gastos recurrentes.",
            {"pendiente_pago": round(m["pendiente_pago"], 2), "ingresos_operativos": round(m["ingresos_operativos"], 2)},
            url_for("contabilidad.pagos"),
        ))

    return recomendaciones


def _lagunas_datos_ia(conn):
    lagunas = []
    controles = [
        ("reservas_recepcion", "Recepcion", "Reservas, regimenes, canales y preferencias"),
        ("viajeros_recepcion", "Clientes", "Alergias, intolerancias, cultura y fidelizacion"),
        ("resumen_prenomina", "RRHH", "Horas extra, ausencias y coste estimado"),
        ("lotes", "APPCC", "Caducidades, descongelacion, trazabilidad y deterioros"),
        ("partes_produccion", "Produccion", "Menus, raciones, consumos reales y mermas"),
        ("animacion_actividades", "Animacion", "Agenda, asistencia, satisfaccion y coste de actividades"),
        ("experiencia_quejas", "Experiencia cliente", "Quejas, RRPP, comprobaciones, fidelizacion y reputacion"),
        ("ordenes_trabajo", "Mantenimiento", "Averias, preventivos, repuestos y costes"),
        ("contabilidad_documentos", "Contabilidad", "Facturas, vencimientos, pagos e impuestos"),
        ("asistente_documentos_lectura", "IA documentos", "Lectura de tickets, reservas, albaranes, facturas y escaneos"),
        ("asistente_memoria", "IA", "Feedback humano y aprendizaje interno"),
    ]
    for tabla, modulo, motivo in controles:
        if not _table_exists(conn, tabla):
            lagunas.append({"modulo": modulo, "tabla": tabla, "motivo": motivo})
    return lagunas


def _patrones_aprendizaje(conn, empresa_id):
    if not _table_exists(conn, "asistente_recomendaciones"):
        return []
    rows = conn.execute(
        """
        SELECT modulo, titulo,
               SUM(CASE WHEN estado = 'aceptada' THEN 1 ELSE 0 END) AS aceptadas,
               SUM(CASE WHEN estado = 'descartada' THEN 1 ELSE 0 END) AS descartadas,
               SUM(CASE WHEN estado = 'pendiente' THEN 1 ELSE 0 END) AS pendientes,
               COUNT(*) AS total
        FROM asistente_recomendaciones
        WHERE empresa_id = ?
        GROUP BY modulo, titulo
        HAVING COUNT(*) >= 1
        ORDER BY aceptadas DESC, descartadas DESC, total DESC
        LIMIT 25
        """,
        (empresa_id,),
    ).fetchall()
    patrones = []
    for row in rows:
        aceptadas = int(row["aceptadas"] or 0)
        descartadas = int(row["descartadas"] or 0)
        total = int(row["total"] or 0)
        if aceptadas >= 2:
            lectura = "Patron confirmado por direccion: conviene darle mas peso."
            estado = "reforzar"
        elif descartadas >= 2:
            lectura = "Se descarta a menudo: la regla puede estar demasiado sensible o mal contextualizada."
            estado = "ajustar"
        elif total >= 3 and aceptadas == 0:
            lectura = "Aparece varias veces sin aceptacion: revisar si aporta valor real."
            estado = "observar"
        else:
            lectura = "Aun necesita mas feedback humano."
            estado = "aprendiendo"
        patrones.append({
            "modulo": row["modulo"],
            "titulo": row["titulo"],
            "aceptadas": aceptadas,
            "descartadas": descartadas,
            "pendientes": int(row["pendientes"] or 0),
            "total": total,
            "lectura": lectura,
            "estado": estado,
        })
    return patrones


def _analisis_aprendizaje_interno(conn, empresa_id):
    recomendaciones = []
    patrones = _patrones_aprendizaje(conn, empresa_id)
    reforzar = [p for p in patrones if p["estado"] == "reforzar"]
    ajustar = [p for p in patrones if p["estado"] == "ajustar"]
    pendientes = int(_scalar(
        conn,
        "SELECT COUNT(*) FROM asistente_recomendaciones WHERE empresa_id = ? AND estado = 'pendiente'",
        (empresa_id,),
        0,
    )) if _table_exists(conn, "asistente_recomendaciones") else 0

    if reforzar:
        recomendaciones.append(_rec(
            "IA El Raco",
            "Aprendizaje",
            "media",
            "Patrones ya aceptados por direccion",
            "Hay recomendaciones que se han aceptado varias veces. La IA empieza a aprender que esas senales tienen valor en este establecimiento.",
            "Mantener feedback humano y, mas adelante, subir el peso de estas reglas para avisar antes.",
            {"patrones": [f"{p['modulo']} - {p['titulo']} ({p['aceptadas']} aceptadas)" for p in reforzar[:5]]},
            url_for("asistente.aprendizaje"),
        ))

    if ajustar:
        recomendaciones.append(_rec(
            "IA El Raco",
            "Calibracion",
            "media",
            "Reglas que conviene ajustar",
            "Algunas recomendaciones se han descartado varias veces. Eso es aprendizaje: no todo aviso merece el mismo peso.",
            "Revisar umbrales y contexto antes de automatizar mas avisos.",
            {"reglas": [f"{p['modulo']} - {p['titulo']} ({p['descartadas']} descartadas)" for p in ajustar[:5]]},
            url_for("asistente.aprendizaje"),
        ))

    if pendientes >= 10:
        recomendaciones.append(_rec(
            "IA El Raco",
            "Gobernanza",
            "baja",
            "Muchas recomendaciones pendientes de feedback",
            "Para que la IA aprenda de verdad necesita que direccion marque si un aviso fue util, descartado o revisado.",
            "Dedicar unos minutos al final del dia a cerrar recomendaciones guardadas.",
            {"pendientes": pendientes},
            url_for("asistente.aprendizaje"),
        ))

    lagunas = _lagunas_datos_ia(conn)
    if lagunas:
        recomendaciones.append(_rec(
            "IA El Raco",
            "Datos",
            "info",
            "Datos que faltan para una IA mas fina",
            "La calidad del consejo depende de los datos disponibles. Donde falten sensores, la IA solo podra hablar con prudencia.",
            "Priorizar captura de datos en los modulos marcados antes de pedir recomendaciones mas automaticas.",
            {"lagunas": [f"{l['modulo']}: {l['motivo']}" for l in lagunas[:8]]},
            url_for("asistente.aprendizaje"),
        ))
    return recomendaciones


def _metricas_oportunidades_establecimiento(conn, empresa_id, fecha_desde, fecha_hasta):
    metricas = {
        "reservas": 0,
        "pax": 0,
        "estancias_largas": 0,
        "regimenes": {},
        "solo_desayuno_pct": 0.0,
        "media_pension_pct": 0.0,
        "todo_incluido_pct": 0.0,
        "puntos_restauracion": 0,
        "puntos_bar": 0,
        "menus_periodo": 0,
        "producciones_periodo": 0,
        "ventas_tpv_restauracion": 0.0,
        "quejas_restauracion": 0,
        "avisos_alimentarios": 0,
        "clientes_especiales": 0,
    }
    if _table_exists(conn, "reservas_recepcion"):
        rows = conn.execute(
            """
            SELECT lower(COALESCE(regimen, 'solo_alojamiento')) AS regimen,
                   COUNT(*) AS reservas,
                   COALESCE(SUM(COALESCE(adultos, 0) + COALESCE(menores, 0)), 0) AS pax,
                   SUM(CASE WHEN julianday(fecha_salida) - julianday(fecha_entrada) >= 7 THEN 1 ELSE 0 END) AS largas
            FROM reservas_recepcion
            WHERE empresa_id = ?
              AND date(fecha_entrada) <= date(?)
              AND date(fecha_salida) >= date(?)
              AND lower(COALESCE(estado, 'confirmada')) NOT IN ({})
            GROUP BY lower(COALESCE(regimen, 'solo_alojamiento'))
            """.format(",".join("?" for _ in ESTADOS_RESERVA_NO_ACTIVOS)),
            (empresa_id, fecha_hasta.isoformat(), fecha_desde.isoformat(), *ESTADOS_RESERVA_NO_ACTIVOS),
        ).fetchall()
        for row in rows:
            regimen = row["regimen"] or "solo_alojamiento"
            reservas = int(row["reservas"] or 0)
            metricas["regimenes"][regimen] = reservas
            metricas["reservas"] += reservas
            metricas["pax"] += int(row["pax"] or 0)
            metricas["estancias_largas"] += int(row["largas"] or 0)
        total = max(metricas["reservas"], 1)
        solo_desayuno = sum(
            valor for clave, valor in metricas["regimenes"].items()
            if ("desayuno" in clave or clave in ("ad", "alojamiento_desayuno")) and "media" not in clave and "pension" not in clave and "todo" not in clave
        )
        media = sum(valor for clave, valor in metricas["regimenes"].items() if "media" in clave)
        todo = sum(valor for clave, valor in metricas["regimenes"].items() if "todo" in clave or "all" in clave)
        metricas["solo_desayuno_pct"] = solo_desayuno / total * 100
        metricas["media_pension_pct"] = media / total * 100
        metricas["todo_incluido_pct"] = todo / total * 100

    if _table_exists(conn, "puntos_venta"):
        pv_cols = _columns(conn, "puntos_venta")
        texto_expr = "COALESCE(nombre, '')"
        if "tipo" in pv_cols:
            texto_expr += " || ' ' || COALESCE(tipo, '')"
        if "categoria" in pv_cols:
            texto_expr += " || ' ' || COALESCE(categoria, '')"
        rows = conn.execute(
            f"""
            SELECT lower({texto_expr}) AS texto
            FROM puntos_venta
            WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
            """,
            (empresa_id,),
        ).fetchall()
        for row in rows:
            texto = row["texto"] or ""
            if any(p in texto for p in ("restaurante", "buffet", "snack", "comedor", "carta")):
                metricas["puntos_restauracion"] += 1
            if any(p in texto for p in ("bar", "pub", "cafeteria", "terraza")):
                metricas["puntos_bar"] += 1

    if _table_exists(conn, "menus_diarios"):
        metricas["menus_periodo"] = int(_scalar(
            conn,
            "SELECT COUNT(*) FROM menus_diarios WHERE empresa_id = ? AND date(fecha) BETWEEN date(?) AND date(?)",
            (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
            0,
        ))
    if _table_exists(conn, "partes_produccion"):
        metricas["producciones_periodo"] = int(_scalar(
            conn,
            "SELECT COUNT(*) FROM partes_produccion WHERE empresa_id = ? AND date(fecha) BETWEEN date(?) AND date(?)",
            (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
            0,
        ))
    if _table_exists(conn, "tpv_cuentas") and {"total", "empresa_id"}.issubset(_columns(conn, "tpv_cuentas")):
        cols_tpv = _columns(conn, "tpv_cuentas")
        fecha_expr = "fecha_cierre" if "fecha_cierre" in cols_tpv else ("fecha_apertura" if "fecha_apertura" in cols_tpv else ("fecha_creacion" if "fecha_creacion" in cols_tpv else ""))
        if fecha_expr:
            estado_filtro = "AND lower(COALESCE(estado, '')) IN ('cerrada', 'cerrado', 'pagada', 'pagado')" if "estado" in cols_tpv else ""
            metricas["ventas_tpv_restauracion"] = _to_float(_scalar(
                conn,
                f"""
                SELECT COALESCE(SUM(total), 0)
                FROM tpv_cuentas
                WHERE empresa_id = ?
                  AND date({fecha_expr}) BETWEEN date(?) AND date(?)
                  {estado_filtro}
                """,
                (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
                0,
            ), 0.0)
    if _table_exists(conn, "experiencia_quejas"):
        metricas["quejas_restauracion"] = int(_scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM experiencia_quejas
            WHERE empresa_id = ?
              AND date(fecha) BETWEEN date(?) AND date(?)
              AND categoria IN ('restauracion', 'alergias')
            """,
            (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
            0,
        ))
    if _table_exists(conn, "recepcion_avisos"):
        metricas["avisos_alimentarios"] = int(_scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM recepcion_avisos
            WHERE empresa_id = ?
              AND (lower(COALESCE(tipo, '')) LIKE '%alerg%' OR lower(COALESCE(descripcion, '')) LIKE '%halal%' OR lower(COALESCE(descripcion, '')) LIKE '%celiac%')
            """,
            (empresa_id,),
            0,
        ))
    if _table_exists(conn, "viajeros_recepcion"):
        metricas["clientes_especiales"] = int(_scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM viajeros_recepcion
            WHERE empresa_id = ?
              AND (
                lower(COALESCE(alergias_intolerancias, '')) <> ''
                OR lower(COALESCE(observaciones_salud, '')) <> ''
                OR lower(COALESCE(nacionalidad, '')) IN ('ma', 'maroc', 'marruecos', 'morocco')
              )
            """,
            (empresa_id,),
            0,
        ))
    return metricas


def _analisis_oportunidades_establecimiento(conn, empresa_id, fecha_desde, fecha_hasta):
    recomendaciones = []
    m = _metricas_oportunidades_establecimiento(conn, empresa_id, fecha_desde, fecha_hasta)
    infraestructura_ok = (m["puntos_restauracion"] > 0 or m["menus_periodo"] > 0) and m["producciones_periodo"] > 0
    datos = {
        "reservas": m["reservas"],
        "pax": m["pax"],
        "regimenes": m["regimenes"],
        "solo_desayuno_pct": round(m["solo_desayuno_pct"], 1),
        "media_pension_pct": round(m["media_pension_pct"], 1),
        "puntos_restauracion": m["puntos_restauracion"],
        "menus_periodo": m["menus_periodo"],
        "producciones_periodo": m["producciones_periodo"],
        "quejas_restauracion": m["quejas_restauracion"],
    }
    if m["reservas"] >= 5 and m["solo_desayuno_pct"] >= 65 and infraestructura_ok and m["quejas_restauracion"] == 0:
        recomendaciones.append(_rec(
            "IA Direccion",
            "Oportunidad comercial",
            "media",
            "Estudiar media pension para un hotel centrado en desayuno",
            "La mayoria de reservas van con desayuno, pero hay infraestructura y produccion suficiente para probar una oferta de media pension controlada.",
            "Simular precio neto, coste de menu, personal, mermas y ocupacion antes de publicarlo. La IA aconseja prueba piloto, no decide.",
            datos,
            url_for("asistente.oportunidades"),
        ))
    if m["reservas"] >= 5 and m["solo_desayuno_pct"] >= 65 and not infraestructura_ok:
        recomendaciones.append(_rec(
            "IA Direccion",
            "Infraestructura",
            "info",
            "No vender media pension sin preparar la operativa",
            "Hay base de clientes con desayuno, pero faltan datos de restaurante, menus o produccion para asegurar una media pension fiable.",
            "Antes de vender mas regimenes, validar cocina, sala, compras, APPCC, personal, horarios y coste real.",
            datos,
            url_for("asistente.oportunidades"),
        ))
    if m["media_pension_pct"] > 25 and m["quejas_restauracion"] > 0:
        recomendaciones.append(_rec(
            "IA Direccion",
            "Riesgo de servicio",
            "alta",
            "Revisar restauracion antes de ampliar regimenes",
            "Ya hay peso de media pension o restauracion y aparecen quejas relacionadas. Ampliar venta sin corregir puede empeorar reputacion.",
            "Investigar quejas, produccion, tiempos de servicio y calidad antes de empujar mas ventas de regimen.",
            datos,
            url_for("experiencia.dashboard"),
        ))
    if m["clientes_especiales"] >= 3 or m["avisos_alimentarios"] >= 3:
        recomendaciones.append(_rec(
            "IA Clientes",
            "Preferencias y restricciones",
            "media",
            "Clientes con restricciones que afectan menus y ventas",
            "Hay senales de alergias, salud, cultura o preferencias. La IA debe cruzarlas antes de proponer menus o paquetes.",
            "Preparar recetas y tarjetas con alergenos, halal/sin gluten si procede, y avisos a cocina/sala.",
            {**datos, "clientes_especiales": m["clientes_especiales"], "avisos_alimentarios": m["avisos_alimentarios"]},
            url_for("experiencia.cliente_360"),
        ))
    return recomendaciones


def _analisis_documentos_lectura(conn, empresa_id):
    recomendaciones = []
    if not _table_exists(conn, "asistente_documentos_lectura"):
        return recomendaciones
    pendientes_ocr = int(_scalar(
        conn,
        "SELECT COUNT(*) FROM asistente_documentos_lectura WHERE empresa_id = ? AND estado = 'pendiente_ocr'",
        (empresa_id,),
        0,
    ))
    pendientes_revision = int(_scalar(
        conn,
        "SELECT COUNT(*) FROM asistente_documentos_lectura WHERE empresa_id = ? AND estado = 'pendiente_revision'",
        (empresa_id,),
        0,
    ))
    if pendientes_ocr:
        recomendaciones.append(_rec(
            "IA Documentos",
            "OCR",
            "info",
            "Documentos pendientes de lectura automatica",
            "Hay documentos subidos que no se han podido leer porque falta lector PDF/OCR o son escaneos sin texto.",
            "Instalar OCR o revisar manualmente antes de integrarlos en compras, reservas, TPV o cliente 360.",
            {"pendientes_ocr": pendientes_ocr},
            url_for("asistente.documentos_ia"),
        ))
    if pendientes_revision >= 5:
        recomendaciones.append(_rec(
            "IA Documentos",
            "Revision humana",
            "baja",
            "Documentos leidos pendientes de validar",
            "La IA puede extraer senales, pero alguien debe validar antes de asentar datos economicos o preferencias.",
            "Revisar bandeja de documentos y convertir solo los datos correctos.",
            {"pendientes_revision": pendientes_revision},
            url_for("asistente.documentos_ia"),
        ))
    return recomendaciones


def _asesor_diario_contexto(conn, empresa_id, fecha_desde, fecha_hasta):
    recomendaciones = generar_recomendaciones(conn, empresa_id, fecha_desde, fecha_hasta)
    grupos = [
        {
            "clave": "direccion",
            "titulo": "Direccion y gerencia",
            "icono": "fa-compass",
            "detalle": "Margen, ocupacion, personal, ventas, bancos, reputacion y riesgos.",
            "keywords": ("Gestion", "Direccion", "Contabilidad", "RRHH", "Recepcion / Ventas", "IA Direccion"),
            "acciones": [
                "Ver prioridades criticas del dia antes de tocar precios o plantilla.",
                "Cruzar ocupacion, coste de personal y coste de produccion.",
                "Revisar cobros, pagos, vencimientos y quejas sensibles.",
            ],
        },
        {
            "clave": "departamentos",
            "titulo": "Jefes de departamento",
            "icono": "fa-people-arrows",
            "detalle": "Trabajo diario por area, sin invadir datos de otros departamentos.",
            "keywords": ("Produccion", "Compras", "Pisos", "Mantenimiento", "Animacion", "APPCC"),
            "acciones": [
                "Entrar con su permiso y revisar solo tareas, alertas y necesidades de su area.",
                "Pedir o escalar incidencias con trazabilidad, no por mensajes sueltos.",
                "Registrar consumos reales, incidencias y comprobaciones al terminar la tarea.",
            ],
        },
        {
            "clave": "cliente",
            "titulo": "Cliente y experiencia",
            "icono": "fa-heart",
            "detalle": "El cliente no ve la maquinaria interna; ve ventajas, cuidado y respuesta.",
            "keywords": ("Experiencia", "Clientes", "IA Clientes", "Animacion"),
            "acciones": [
                "Mostrar ventajas: atencion rapida, preferencias, alergias, descanso y actividades.",
                "Usar ficha 360 para recordar gustos y necesidades con privacidad.",
                "Responder quejas con comprobaciones y seguimiento humano.",
            ],
        },
    ]
    for grupo in grupos:
        grupo["recomendaciones"] = [
            rec for rec in recomendaciones
            if any(keyword.lower() in f"{rec['modulo']} {rec['categoria']} {rec['titulo']}".lower() for keyword in grupo["keywords"])
        ][:8]
    return grupos


def _texto_busqueda(valor):
    return _normalizar(valor).replace("_", " ")


def _contiene_alguna(texto, palabras):
    base = _texto_busqueda(texto)
    return any(_texto_busqueda(palabra) in base for palabra in palabras)


def _recetas_cocina(conn, empresa_id):
    if not _table_exists(conn, "recetas"):
        return []
    recetas_cols = _columns(conn, "recetas")
    productos_cols = _columns(conn, "productos") if _table_exists(conn, "productos") else set()
    raciones_expr = "COALESCE(r.raciones_base, 4)" if "raciones_base" in recetas_cols else "4"
    alergenos_producto_expr = "COALESCE(p.alergenos, '')" if "alergenos" in productos_cols else "''"
    tipo_conservacion_expr = "COALESCE(p.tipo_conservacion, '')" if "tipo_conservacion" in productos_cols else "''"
    familia_expr = "COALESCE(p.familia, '')" if "familia" in productos_cols else "''"
    precio_expr = "COALESCE(p.precio, 0)" if "precio" in productos_cols else "0"
    rows = conn.execute(
        f"""
        SELECT r.id, r.nombre, COALESCE(r.categoria, '') AS categoria,
               COALESCE(r.alergenos, '') AS alergenos,
               {raciones_expr} AS raciones_base,
               COUNT(ri.id) AS ingredientes,
               SUM(CASE WHEN COALESCE(ri.cantidad, 0) <= 0 THEN 1 ELSE 0 END) AS ingredientes_sin_cantidad,
               COALESCE(SUM(COALESCE(ri.cantidad, 0) * {precio_expr}), 0) AS coste_base,
               GROUP_CONCAT(DISTINCT {familia_expr}) AS familias,
               GROUP_CONCAT(DISTINCT {tipo_conservacion_expr}) AS conservaciones,
               GROUP_CONCAT(DISTINCT {alergenos_producto_expr}) AS alergenos_productos
        FROM recetas r
        LEFT JOIN recetas_ingredientes ri ON ri.receta_id = r.id
        LEFT JOIN productos p ON p.id = ri.producto_id
        WHERE r.empresa_id = ?
        GROUP BY r.id
        ORDER BY LOWER(r.nombre)
        """,
        (empresa_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _menus_cocina_periodo(conn, empresa_id, fecha_desde, fecha_hasta):
    requeridas = {"menus_diarios", "tipos_menu", "menu_subdepartamentos", "menu_platos", "recetas"}
    if not all(_table_exists(conn, tabla) for tabla in requeridas):
        return []
    rows = conn.execute(
        """
        SELECT md.fecha, t.nombre AS servicio, COALESCE(md.modo_servicio, 'buffet') AS modo_servicio,
               COALESCE(s.nombre, '') AS subdepartamento, COALESCE(ms.seccion, '') AS seccion,
               r.id AS receta_id, r.nombre AS receta_nombre, COALESCE(r.categoria, '') AS categoria,
               COALESCE(r.alergenos, '') AS alergenos
        FROM menus_diarios md
        JOIN tipos_menu t ON t.id = md.tipo_menu_id
        JOIN menu_subdepartamentos ms ON ms.menu_diario_id = md.id
        JOIN menu_platos mp ON mp.menu_subdepartamento_id = ms.id
        JOIN recetas r ON r.id = mp.receta_id
        LEFT JOIN subdepartamentos s ON s.id = ms.subdepartamento_id
        WHERE md.empresa_id = ?
          AND date(md.fecha) BETWEEN date(?) AND date(?)
        ORDER BY date(md.fecha), t.nombre, s.nombre, ms.seccion, r.nombre
        """,
        (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
    ).fetchall()
    return [dict(row) for row in rows]


def _recetas_sugeridas_por_perfil(recetas, objetivo, ingrediente=""):
    perfil = PERFILES_COCINA.get(objetivo, {})
    favor = perfil.get("favor", ())
    revisar = perfil.get("revisar", ())
    ingrediente = _texto_busqueda(ingrediente)
    sugeridas = []
    conflictivas = []
    for receta in recetas:
        texto = " ".join([
            receta.get("nombre") or "",
            receta.get("categoria") or "",
            receta.get("familias") or "",
            receta.get("alergenos") or "",
            receta.get("alergenos_productos") or "",
        ])
        if ingrediente and ingrediente not in _texto_busqueda(texto):
            continue
        score = 0
        if favor:
            score += sum(1 for palabra in favor if _contiene_alguna(texto, (palabra,)))
        if ingrediente:
            score += 3
        if revisar and _contiene_alguna(texto, revisar):
            conflictivas.append(receta)
            score -= 3
        if score > 0:
            sugeridas.append((score, receta))
    sugeridas.sort(key=lambda item: (item[0], -_to_float(item[1].get("coste_base"), 0.0)), reverse=True)
    return [item[1] for item in sugeridas[:10]], conflictivas[:10]


def _guardar_recomendaciones_lista(conn, empresa_id, recomendaciones, origen):
    for rec in recomendaciones:
        conn.execute(
            """
            INSERT INTO asistente_recomendaciones (
                empresa_id, modulo, categoria, prioridad, titulo, detalle,
                datos_usados, accion_propuesta, estado, origen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', ?)
            """,
            (
                empresa_id,
                rec["modulo"],
                rec["categoria"],
                rec["prioridad"],
                rec["titulo"],
                rec["detalle"],
                json.dumps(rec["datos_usados"], ensure_ascii=False),
                rec["accion_propuesta"],
                origen,
            ),
        )


def _busquedas_externas_cocina(objetivo, ingrediente="", perfil_cliente="", restricciones="", idioma="es"):
    etiqueta_objetivo = dict(OBJETIVOS_COCINA_IA).get(objetivo, objetivo or "cocina")
    partes = [
        "receta",
        "buffet hotel",
        etiqueta_objetivo,
        ingrediente,
        perfil_cliente,
        restricciones,
    ]
    consulta_base = " ".join(str(parte or "").strip() for parte in partes if str(parte or "").strip())
    if idioma == "en":
        consulta_base = f"hotel buffet recipe {ingrediente} {perfil_cliente} {restricciones} {etiqueta_objetivo}".strip()
    elif idioma == "de":
        consulta_base = f"hotel buffet rezept {ingrediente} {perfil_cliente} {restricciones} {etiqueta_objetivo}".strip()
    elif idioma == "it":
        consulta_base = f"ricetta buffet hotel {ingrediente} {perfil_cliente} {restricciones} {etiqueta_objetivo}".strip()

    consultas = [
        consulta_base,
        f"{consulta_base} escandallo coste racion",
        f"{consulta_base} alergenos appcc",
        f"{consulta_base} menu temporada hotel",
    ]
    if objetivo in {"halal", "celiaco", "alergenos"} or restricciones:
        consultas.append(f"{consulta_base} contaminacion cruzada seguridad alimentaria")

    motores = [
        ("DuckDuckGo", "https://duckduckgo.com/?q={}"),
        ("Google", "https://www.google.com/search?q={}"),
        ("YouTube", "https://www.youtube.com/results?search_query={}"),
    ]
    resultados = []
    usadas = set()
    for consulta in consultas:
        consulta = " ".join(consulta.split())
        if not consulta or consulta in usadas:
            continue
        usadas.add(consulta)
        for motor, plantilla in motores:
            resultados.append({
                "motor": motor,
                "consulta": consulta,
                "url": plantilla.format(quote_plus(consulta)),
            })
    return resultados


def _investigaciones_recientes(conn, empresa_id, limite=30):
    if not _table_exists(conn, "asistente_investigaciones"):
        return []
    return conn.execute(
        """
        SELECT *
        FROM asistente_investigaciones
        WHERE empresa_id = ?
        ORDER BY datetime(fecha_creacion) DESC, id DESC
        LIMIT ?
        """,
        (empresa_id, limite),
    ).fetchall()


def _guardar_investigacion_como_recomendacion(conn, empresa_id, investigacion_id):
    investigacion = conn.execute(
        """
        SELECT *
        FROM asistente_investigaciones
        WHERE id = ? AND empresa_id = ?
        """,
        (investigacion_id, empresa_id),
    ).fetchone()
    if not investigacion:
        return None
    titulo_fuente = investigacion["fuente_titulo"] or investigacion["consulta"] or "Investigacion externa cocina"
    titulo = f"Revisar propuesta externa: {titulo_fuente[:90]}"
    detalle = investigacion["resumen"] or "Se ha registrado una fuente externa para estudiar una mejora de cocina."
    accion = investigacion["propuesta"] or "Revisar fuente, adaptar a la operativa del hotel, calcular coste y validar alergenos antes de probar."
    datos = {
        "objetivo": investigacion["objetivo"],
        "consulta": investigacion["consulta"],
        "perfil_cliente": investigacion["perfil_cliente"],
        "restricciones": investigacion["restricciones"],
        "fuente": investigacion["fuente_url"],
        "riesgos": investigacion["riesgos"],
    }
    cursor = conn.execute(
        """
        INSERT INTO asistente_recomendaciones (
            empresa_id, modulo, categoria, prioridad, titulo, detalle,
            datos_usados, accion_propuesta, estado, origen
        ) VALUES (?, 'IA Cocina', 'Investigacion externa', 'media', ?, ?, ?, ?, 'pendiente', 'investigacion_externa')
        """,
        (
            empresa_id,
            titulo,
            detalle,
            json.dumps(datos, ensure_ascii=False),
            accion,
        ),
    )
    recomendacion_id = cursor.lastrowid
    conn.execute(
        """
        UPDATE asistente_investigaciones
        SET recomendacion_id = ?, estado = 'propuesta_generada'
        WHERE id = ? AND empresa_id = ?
        """,
        (recomendacion_id, investigacion_id, empresa_id),
    )
    return recomendacion_id


def _analisis_cocina_ia(conn, empresa_id, fecha_desde, fecha_hasta, objetivo="general", ingrediente=""):
    recomendaciones = []
    recetas = _recetas_cocina(conn, empresa_id)
    menus = _menus_cocina_periodo(conn, empresa_id, fecha_desde, fecha_hasta)
    objetivo = objetivo if objetivo in dict(OBJETIVOS_COCINA_IA) else "general"
    ingrediente = (ingrediente or "").strip()

    datos_base = {
        "periodo": f"{fecha_desde.isoformat()} / {fecha_hasta.isoformat()}",
        "objetivo": objetivo,
        "ingrediente": ingrediente,
        "recetas": len(recetas),
        "platos_menu_periodo": len(menus),
    }

    if not recetas:
        recomendaciones.append(_rec(
            "IA Cocina",
            "Datos base",
            "alta",
            "No hay recetas suficientes para asesorar cocina",
            "El asistente no puede proponer mejoras culinarias sin una base de recetas.",
            "Importar o crear recetas base con ingredientes, cantidades, alergenos y raciones.",
            datos_base,
            url_for("recetas.recetas"),
        ))
        return recomendaciones

    recetas_sin_ingredientes = [r for r in recetas if int(r.get("ingredientes") or 0) == 0]
    recetas_sin_cantidad = [r for r in recetas if int(r.get("ingredientes_sin_cantidad") or 0) > 0]
    recetas_menu_ids = {m["receta_id"] for m in menus}
    criticas_menu_sin_ingredientes = [r for r in recetas_sin_ingredientes if r["id"] in recetas_menu_ids]
    criticas_menu_sin_cantidad = [r for r in recetas_sin_cantidad if r["id"] in recetas_menu_ids]

    if criticas_menu_sin_ingredientes or (not menus and recetas_sin_ingredientes):
        muestra = criticas_menu_sin_ingredientes or recetas_sin_ingredientes
        recomendaciones.append(_rec(
            "IA Cocina",
            "Recetas",
            "alta",
            "Recetas sin ingredientes bloquean produccion y costes",
            "Hay recetas que no permiten calcular necesidades, coste ni trazabilidad. Si estan en menu, produccion queda ciega.",
            "Completar ingredientes para 4 raciones y validar alergenos antes de usar esas recetas en buffet.",
            {**datos_base, "recetas": [r["nombre"] for r in muestra[:12]]},
            url_for("recetas.rellenar_recetas_pruebas"),
        ))

    if criticas_menu_sin_cantidad or (not menus and recetas_sin_cantidad):
        muestra = criticas_menu_sin_cantidad or recetas_sin_cantidad
        recomendaciones.append(_rec(
            "IA Cocina",
            "Cantidades",
            "media",
            "Ingredientes sin cantidad",
            "Hay ingredientes asignados sin cantidad. La receta existe, pero el coste y la compra saldran mal.",
            "Revisar cantidades por receta base y confirmar si las unidades son kg, litros o unidades.",
            {**datos_base, "recetas": [r["nombre"] for r in muestra[:12]]},
            url_for("recetas.recetas"),
        ))

    alergenos_pendientes = []
    for receta in recetas:
        propios = _texto_busqueda(receta.get("alergenos"))
        producto_alergenos = _texto_busqueda(receta.get("alergenos_productos"))
        if producto_alergenos and not propios:
            alergenos_pendientes.append(receta)
    if alergenos_pendientes:
        recomendaciones.append(_rec(
            "IA Cocina",
            "Alergenos",
            "alta",
            "Alergenos de producto no reflejados en receta",
            "Hay productos con alergenos dentro de recetas cuya ficha no tiene alergenos. Esto afecta tarjetas de buffet y seguridad alimentaria.",
            "Recalcular alergenos desde ingredientes y validar por jefe de cocina antes de imprimir o mostrar tarjetas.",
            {**datos_base, "recetas": [r["nombre"] for r in alergenos_pendientes[:12]]},
            url_for("produccion.tarjetas_servicio"),
        ))

    if objetivo in PERFILES_COCINA or ingrediente:
        sugeridas, conflictivas = _recetas_sugeridas_por_perfil(recetas, objetivo, ingrediente)
        perfil = PERFILES_COCINA.get(objetivo, {})
        if sugeridas:
            recomendaciones.append(_rec(
                "IA Cocina",
                "Propuesta menu",
                "info",
                "Recetas candidatas para el objetivo seleccionado",
                perfil.get("mensaje") or "El asistente ha buscado recetas internas que encajan con el objetivo indicado.",
                "Revisar candidatas y, si encajan, incorporarlas al menu o crear una version ajustada.",
                {
                    **datos_base,
                    "recetas_sugeridas": [
                        {
                            "nombre": r["nombre"],
                            "categoria": r.get("categoria") or "",
                            "coste_base": round(_to_float(r.get("coste_base"), 0.0), 2),
                            "alergenos": r.get("alergenos") or r.get("alergenos_productos") or "",
                        }
                        for r in sugeridas[:10]
                    ],
                },
                url_for("menus.lista_menus_principal"),
            ))
        if conflictivas:
            recomendaciones.append(_rec(
                "IA Cocina",
                "Revision menu",
                "media",
                "Recetas a revisar para este perfil",
                "Algunas recetas contienen palabras o ingredientes que pueden chocar con el perfil seleccionado.",
                "Validar ingredientes, contaminacion cruzada, etiquetado y alternativa antes del servicio.",
                {**datos_base, "recetas_a_revisar": [r["nombre"] for r in conflictivas[:10]]},
                url_for("recetas.recetas"),
            ))

    if menus:
        repeticiones = defaultdict(int)
        for item in menus:
            repeticiones[item["receta_nombre"]] += 1
        repetidas = sorted(
            ((nombre, total) for nombre, total in repeticiones.items() if total >= 3),
            key=lambda item: item[1],
            reverse=True,
        )
        if repetidas:
            recomendaciones.append(_rec(
                "IA Cocina",
                "Variedad",
                "media",
                "Platos repetidos en el periodo",
                "Hay platos que aparecen varias veces. Puede ser correcto en postres o desayunos, pero en buffet de larga estancia conviene vigilar repeticion.",
                "Confirmar si son platos que deben repetirse o buscar sustitutos por estacion/seccion.",
                {**datos_base, "repetidos": [f"{nombre} ({total})" for nombre, total in repetidas[:12]]},
                url_for("menus.vista_semanal_menus"),
            ))

    recetas_con_coste = [r for r in recetas if _to_float(r.get("coste_base"), 0.0) > 0 and _to_float(r.get("raciones_base"), 4.0) > 0]
    caras = sorted(
        recetas_con_coste,
        key=lambda r: _to_float(r.get("coste_base"), 0.0) / max(_to_float(r.get("raciones_base"), 4.0), 1.0),
        reverse=True,
    )
    if caras:
        top = caras[:8]
        coste_pax_top = _to_float(top[0].get("coste_base"), 0.0) / max(_to_float(top[0].get("raciones_base"), 4.0), 1.0)
        if coste_pax_top > 2.5 or objetivo == "coste":
            recomendaciones.append(_rec(
                "IA Cocina / Costes",
                "Escandallo",
                "media",
                "Recetas con coste por racion alto",
                "El asistente detecta recetas que pueden pesar en el coste del buffet si se producen para muchos pax.",
                "Revisar gramajes, proveedor, merma y alternativa de guarnicion antes de subir produccion.",
                {
                    **datos_base,
                    "recetas": [
                        f"{r['nombre']} ({_format_euros(_to_float(r.get('coste_base'), 0.0) / max(_to_float(r.get('raciones_base'), 4.0), 1.0))}/racion)"
                        for r in top
                    ],
                },
                url_for("produccion.costes_produccion"),
            ))

    if _table_exists(conn, "produccion_ingredientes_reales") and _table_exists(conn, "lineas_produccion"):
        rows = conn.execute(
            """
            SELECT r.nombre AS receta, p.nombre AS producto,
                   COALESCE(SUM(pir.cantidad_planificada), 0) AS planificada,
                   COALESCE(SUM(pir.cantidad_real), 0) AS real
            FROM produccion_ingredientes_reales pir
            JOIN lineas_produccion lp ON lp.id = pir.linea_produccion_id
            JOIN partes_produccion pp ON pp.id = lp.parte_id
            JOIN recetas r ON r.id = lp.receta_id
            LEFT JOIN productos p ON p.id = pir.producto_id
            WHERE pp.empresa_id = ?
              AND date(pp.fecha) BETWEEN date(?) AND date(?)
            GROUP BY r.id, p.id
            HAVING real > planificada * 1.15 AND real > 0
            ORDER BY (real - planificada) DESC
            LIMIT 10
            """,
            (empresa_id, fecha_desde.isoformat(), fecha_hasta.isoformat()),
        ).fetchall()
        if rows:
            recomendaciones.append(_rec(
                "IA Cocina / Aprendizaje",
                "Produccion real",
                "media",
                "Produccion usa mas cantidad que la receta",
                "En algunas elaboraciones el operario registra mas cantidad real que la planificada. Puede ser receta corta, merma no contemplada o costumbre del equipo.",
                "Revisar si se ajusta la receta base o si solo fue una desviacion puntual.",
                {
                    **datos_base,
                    "desviaciones": [
                        f"{r['receta']} / {r['producto'] or 'producto'}: {_to_float(r['real'], 0):.2f} real vs {_to_float(r['planificada'], 0):.2f} plan"
                        for r in rows
                    ],
                },
                url_for("produccion.costes_produccion"),
            ))

    return recomendaciones


def _decision_metricas_generales(conn, empresa_id, fecha_desde, fecha_hasta):
    desde = fecha_desde.isoformat()
    hasta = fecha_hasta.isoformat()
    m = {
        "periodo": f"{desde} / {hasta}",
        "habitaciones": 0,
        "reservas_activas": 0,
        "pax_reservas": 0,
        "ingresos_facturados": 0.0,
        "ventas_tpv": 0.0,
        "compras_pedidas": 0.0,
        "nominas_coste": 0.0,
        "coste_laboral_origen": "sin_datos",
        "empleados_activos": 0,
        "fichajes": 0,
        "quejas": 0,
        "quejas_abiertas": 0,
        "ordenes_mantenimiento_abiertas": 0,
        "incidencias_abiertas": 0,
        "productos_bajo_minimo": 0,
        "proveedores_activos": 0,
        "produccion_partes": 0,
        "produccion_desviaciones": 0,
        "datos": {},
    }

    if _table_exists(conn, "habitaciones_recepcion"):
        m["habitaciones"] = int(_scalar(conn, "SELECT COUNT(*) FROM habitaciones_recepcion WHERE empresa_id = ? AND COALESCE(activo, 1) = 1", (empresa_id,), 0) or 0)
    if _table_exists(conn, "reservas_recepcion"):
        m["reservas_activas"] = int(_scalar(
            conn,
            """
            SELECT COUNT(*) FROM reservas_recepcion
            WHERE empresa_id = ?
              AND date(fecha_entrada) <= date(?)
              AND date(fecha_salida) >= date(?)
              AND LOWER(COALESCE(estado, '')) NOT IN ('cancelada','cancelado','no_show','no show')
            """,
            (empresa_id, hasta, desde),
            0,
        ) or 0)
        m["pax_reservas"] = int(_scalar(
            conn,
            """
            SELECT COALESCE(SUM(COALESCE(adultos, 0) + COALESCE(menores, 0)), 0)
            FROM reservas_recepcion
            WHERE empresa_id = ?
              AND date(fecha_entrada) <= date(?)
              AND date(fecha_salida) >= date(?)
              AND LOWER(COALESCE(estado, '')) NOT IN ('cancelada','cancelado','no_show','no show')
            """,
            (empresa_id, hasta, desde),
            0,
        ) or 0)
    if _table_exists(conn, "recepcion_factura_lineas") and _table_exists(conn, "recepcion_facturas"):
        m["ingresos_facturados"] = _to_float(_scalar(
            conn,
            """
            SELECT COALESCE(SUM(l.total), 0)
            FROM recepcion_factura_lineas l
            JOIN recepcion_facturas f ON f.id = l.factura_id
            WHERE f.empresa_id = ? AND date(f.fecha) BETWEEN date(?) AND date(?)
            """,
            (empresa_id, desde, hasta),
            0,
        ))
    if _table_exists(conn, "tpv_comanda_lineas") and _table_exists(conn, "tpv_comandas"):
        m["ventas_tpv"] = _to_float(_scalar(
            conn,
            """
            SELECT COALESCE(SUM(l.total), 0)
            FROM tpv_comanda_lineas l
            JOIN tpv_comandas c ON c.id = l.comanda_id
            WHERE c.empresa_id = ? AND date(c.fecha_creacion) BETWEEN date(?) AND date(?)
            """,
            (empresa_id, desde, hasta),
            0,
        ))
    if _table_exists(conn, "lineas_pedido") and _table_exists(conn, "pedidos"):
        m["compras_pedidas"] = _to_float(_scalar(
            conn,
            """
            SELECT COALESCE(SUM(lp.cantidad_solicitada * lp.precio_unitario), 0)
            FROM lineas_pedido lp
            JOIN pedidos p ON p.id = lp.pedido_id
            WHERE p.empresa_id = ? AND date(p.fecha_pedido) BETWEEN date(?) AND date(?)
            """,
            (empresa_id, desde, hasta),
            0,
        ))
    coste_laboral = _coste_laboral_ops_periodo(conn, empresa_id, fecha_desde, fecha_hasta)
    m["nominas_coste"] = coste_laboral["total"]
    m["coste_laboral_origen"] = coste_laboral["origen"]
    if _table_exists(conn, "empleados"):
        m["empleados_activos"] = int(_scalar(conn, "SELECT COUNT(*) FROM empleados WHERE empresa_id = ? AND COALESCE(activo, 1) = 1", (empresa_id,), 0) or 0)
    if _table_exists(conn, "fichajes"):
        m["fichajes"] = int(_scalar(conn, "SELECT COUNT(*) FROM fichajes WHERE empresa_id = ? AND date(fecha_hora) BETWEEN date(?) AND date(?)", (empresa_id, desde, hasta), 0) or 0)
    if _table_exists(conn, "experiencia_quejas"):
        m["quejas"] = int(_scalar(conn, "SELECT COUNT(*) FROM experiencia_quejas WHERE empresa_id = ? AND date(fecha) BETWEEN date(?) AND date(?)", (empresa_id, desde, hasta), 0) or 0)
        m["quejas_abiertas"] = int(_scalar(
            conn,
            """
            SELECT COUNT(*) FROM experiencia_quejas
            WHERE empresa_id = ? AND LOWER(COALESCE(estado, 'abierta')) NOT IN ('cerrada','cerrado','resuelta','resuelto')
            """,
            (empresa_id,),
            0,
        ) or 0)
    if _table_exists(conn, "ordenes_trabajo"):
        m["ordenes_mantenimiento_abiertas"] = int(_scalar(
            conn,
            """
            SELECT COUNT(*) FROM ordenes_trabajo
            WHERE empresa_id = ? AND LOWER(COALESCE(estado, 'abierta')) NOT IN ('cerrada','cerrado','finalizada','finalizado','cancelada','cancelado')
            """,
            (empresa_id,),
            0,
        ) or 0)
    if _table_exists(conn, "incidencias"):
        m["incidencias_abiertas"] = int(_scalar(
            conn,
            "SELECT COUNT(*) FROM incidencias WHERE empresa_id = ? AND LOWER(COALESCE(estado, 'abierta')) NOT IN ('cerrada','cerrado','resuelta','resuelto')",
            (empresa_id,),
            0,
        ) or 0)
    if _table_exists(conn, "productos"):
        m["productos_bajo_minimo"] = int(_scalar(
            conn,
            """
            SELECT COUNT(*) FROM productos
            WHERE empresa_id = ?
              AND COALESCE(stock_minimo, 0) > 0
              AND COALESCE(cantidad, 0) < COALESCE(stock_minimo, 0)
            """,
            (empresa_id,),
            0,
        ) or 0)
    if _table_exists(conn, "proveedores"):
        m["proveedores_activos"] = int(_scalar(conn, "SELECT COUNT(*) FROM proveedores WHERE empresa_id = ? AND COALESCE(activo, 1) = 1", (empresa_id,), 0) or 0)
    if _table_exists(conn, "partes_produccion"):
        m["produccion_partes"] = int(_scalar(conn, "SELECT COUNT(*) FROM partes_produccion WHERE empresa_id = ? AND date(fecha) BETWEEN date(?) AND date(?)", (empresa_id, desde, hasta), 0) or 0)
    if _table_exists(conn, "produccion_ingredientes_reales"):
        m["produccion_desviaciones"] = int(_scalar(
            conn,
            """
            SELECT COUNT(*) FROM produccion_ingredientes_reales
            WHERE empresa_id = ?
              AND date(fecha) BETWEEN date(?) AND date(?)
              AND COALESCE(cantidad_real, 0) > COALESCE(cantidad_planificada, 0) * 1.15
            """,
            (empresa_id, desde, hasta),
            0,
        ) or 0)

    m["ocupacion_aprox_pct"] = round((m["reservas_activas"] / m["habitaciones"]) * 100, 1) if m["habitaciones"] else 0
    m["ingresos_totales_aprox"] = round(m["ingresos_facturados"] + m["ventas_tpv"], 2)
    m["costes_controlados_aprox"] = round(m["compras_pedidas"] + m["nominas_coste"], 2)
    m["margen_aprox"] = round(m["ingresos_totales_aprox"] - m["costes_controlados_aprox"], 2)
    dias_periodo = max((fecha_hasta - fecha_desde).days + 1, 1)
    m["dias_periodo"] = dias_periodo
    m["fichajes_minimos_esperados"] = int(m["empleados_activos"] * dias_periodo * 0.55) if m["empleados_activos"] else 0
    m["cobertura_fichajes_pct"] = round((m["fichajes"] / max(m["fichajes_minimos_esperados"], 1)) * 100, 1) if m["fichajes_minimos_esperados"] else 0
    m["reservas_por_habitacion_pct"] = round((m["reservas_activas"] / max(m["habitaciones"], 1)) * 100, 1) if m["habitaciones"] else 0
    m["coste_laboral_por_reserva"] = round(m["nominas_coste"] / max(m["reservas_activas"], 1), 2) if m["nominas_coste"] else 0
    return m


def _decision_score(valor, bueno=0, malo=10, invertido=False):
    if malo == bueno:
        return 50
    ratio = (valor - bueno) / (malo - bueno)
    ratio = max(0.0, min(1.0, ratio))
    score = int(100 - ratio * 100)
    return 100 - score if invertido else score


def _decision_areas(metricas):
    ingresos = max(metricas["ingresos_totales_aprox"], 1)
    coste_ratio = metricas["costes_controlados_aprox"] / ingresos
    quejas_ratio = metricas["quejas"] / max(metricas["reservas_activas"], 1)
    fichajes_ratio = metricas["fichajes"] / max(metricas["empleados_activos"], 1)
    compras_ratio = metricas["productos_bajo_minimo"] / max(metricas["proveedores_activos"], 1)

    areas = [
        {
            "clave": "cliente",
            "titulo": "Cliente y fidelizacion",
            "score": _decision_score(quejas_ratio, 0.0, 0.25),
            "metricas": [("Quejas periodo", metricas["quejas"]), ("Quejas abiertas", metricas["quejas_abiertas"]), ("Reservas activas", metricas["reservas_activas"])],
            "lectura": "La satisfaccion manda. Si suben quejas o quedan abiertas, cualquier mejora comercial pierde fuerza.",
        },
        {
            "clave": "economia",
            "titulo": "Economia y margen",
            "score": _decision_score(coste_ratio, 0.45, 1.05),
            "metricas": [("Ingresos aprox.", _format_euros(metricas["ingresos_totales_aprox"])), ("Costes controlados", _format_euros(metricas["costes_controlados_aprox"])), ("Margen aprox.", _format_euros(metricas["margen_aprox"]))],
            "lectura": "No es contabilidad oficial: es una lectura operativa para detectar si una decision puede romper margen.",
        },
        {
            "clave": "rrhh",
            "titulo": "Equipo y rotacion",
            "score": _decision_score(fichajes_ratio, 0.0, 1.0, invertido=True) if metricas["empleados_activos"] else 35,
            "metricas": [("Empleados activos", metricas["empleados_activos"]), ("Fichajes periodo", metricas["fichajes"]), ("Coste laboral OPS", _format_euros(metricas["nominas_coste"])), ("Origen coste", metricas["coste_laboral_origen"])],
            "lectura": "Sin equipo estable no hay experiencia estable. El dato laboral debe cruzarse con quejas, ocupacion y carga real.",
        },
        {
            "clave": "cocina",
            "titulo": "Cocina, compras y produccion",
            "score": _decision_score(metricas["produccion_desviaciones"] + metricas["productos_bajo_minimo"], 0, 18),
            "metricas": [("Partes produccion", metricas["produccion_partes"]), ("Desviaciones consumo", metricas["produccion_desviaciones"]), ("Productos bajo minimo", metricas["productos_bajo_minimo"])],
            "lectura": "La cocina debe equilibrar satisfaccion, coste, APPCC y capacidad real. Si produce a ciegas, el margen se escapa.",
        },
        {
            "clave": "mantenimiento",
            "titulo": "Infraestructura y mantenimiento",
            "score": _decision_score(metricas["ordenes_mantenimiento_abiertas"] + metricas["incidencias_abiertas"], 0, 25),
            "metricas": [("Ordenes abiertas", metricas["ordenes_mantenimiento_abiertas"]), ("Incidencias abiertas", metricas["incidencias_abiertas"]), ("Habitaciones", metricas["habitaciones"])],
            "lectura": "La infraestructura marca lo que se puede vender. Prometer por encima de capacidad real destruye reputacion.",
        },
    ]
    for area in areas:
        if area["score"] < 45:
            area["estado"] = "riesgo"
        elif area["score"] < 70:
            area["estado"] = "vigilar"
        else:
            area["estado"] = "sano"
    return areas


def _decision_huecos(conn):
    requeridas = [
        ("reservas_recepcion", "PMS / reservas", "Sin reservas no se puede calcular demanda, ocupacion ni cliente objetivo."),
        ("recepcion_facturas", "Ingresos alojamiento", "Sin facturacion no hay margen real por estancia o canal."),
        ("tpv_comandas", "Ventas TPV", "Sin consumos de bares/restaurantes falta gasto extra y rentabilidad por punto de venta."),
        ("nominas", "Coste laboral", "Sin coste laboral no se puede decidir si una oferta o servicio es rentable."),
        ("fichajes", "Carga real de personal", "Sin fichajes no se detecta sobrecarga ni riesgo de rotacion."),
        ("experiencia_quejas", "Satisfaccion cliente", "Sin quejas/comentarios la IA solo ve dinero, no experiencia."),
        ("ordenes_trabajo", "Mantenimiento", "Sin mantenimiento no se cruza infraestructura con venta y reputacion."),
        ("produccion_ingredientes_reales", "Consumo real cocina", "Sin consumo real las recetas y menus se quedan teoricos."),
    ]
    huecos = []
    for tabla, titulo, impacto in requeridas:
        if tabla == "nominas" and _table_exists(conn, "resumen_prenomina"):
            total_prenomina = int(_scalar(conn, "SELECT COUNT(*) FROM resumen_prenomina", (), 0) or 0)
            if total_prenomina > 0:
                continue
        if not _table_exists(conn, tabla):
            huecos.append({"tabla": tabla, "titulo": titulo, "impacto": impacto, "estado": "falta_tabla"})
            continue
        total = int(_scalar(conn, f"SELECT COUNT(*) FROM {tabla}", (), 0) or 0)
        if total == 0:
            huecos.append({"tabla": tabla, "titulo": titulo, "impacto": impacto, "estado": "sin_datos"})
    return huecos


def _decision_calidad_datos(metricas, huecos):
    alertas = []
    score = 100

    if huecos:
        penalty = min(35, len(huecos) * 7)
        score -= penalty
        alertas.append({
            "nivel": "alta",
            "titulo": "Faltan bloques de datos",
            "detalle": f"Hay {len(huecos)} bloques con tabla ausente o sin datos. La IA debe marcar incertidumbre.",
        })

    if metricas["empleados_activos"] and metricas["cobertura_fichajes_pct"] < 25:
        score -= 28
        alertas.append({
            "nivel": "alta",
            "titulo": "Fichajes insuficientes para leer RRHH",
            "detalle": (
                f"{metricas['empleados_activos']} empleados activos y {metricas['fichajes']} fichajes en "
                f"{metricas['dias_periodo']} dias. Cobertura estimada {metricas['cobertura_fichajes_pct']}%."
            ),
        })
    elif metricas["empleados_activos"] and metricas["cobertura_fichajes_pct"] < 60:
        score -= 12
        alertas.append({
            "nivel": "media",
            "titulo": "Fichajes parciales",
            "detalle": f"Cobertura de fichajes {metricas['cobertura_fichajes_pct']}%. Sirve para alerta, no para nomina fina.",
        })

    if metricas["habitaciones"] and metricas["reservas_por_habitacion_pct"] < 5 and metricas["costes_controlados_aprox"] > 50000:
        score -= 22
        alertas.append({
            "nivel": "alta",
            "titulo": "Costes altos con ocupacion casi nula",
            "detalle": (
                f"Ocupacion aprox. {metricas['ocupacion_aprox_pct']}% y costes "
                f"{_format_euros(metricas['costes_controlados_aprox'])}. Puede ser negocio malo o datos de reservas incompletos."
            ),
        })

    if metricas["produccion_partes"] > 0 and metricas["reservas_activas"] <= 2:
        score -= 10
        alertas.append({
            "nivel": "media",
            "titulo": "Produccion con poca demanda registrada",
            "detalle": f"{metricas['produccion_partes']} partes de produccion con {metricas['reservas_activas']} reservas activas.",
        })

    if metricas["ingresos_totales_aprox"] <= 0 and metricas["costes_controlados_aprox"] > 0:
        score -= 25
        alertas.append({
            "nivel": "alta",
            "titulo": "Costes sin ingresos",
            "detalle": "Hay costes en el periodo pero no ingresos. Revisar facturacion, TPV o rango de fechas.",
        })

    score = max(0, min(100, score))
    hay_alta = any(a["nivel"] == "alta" for a in alertas)
    estado = "riesgo" if score < 45 else "vigilar" if score < 75 or hay_alta else "sano"
    lectura = "Datos suficientemente conectados para orientar decisiones."
    if estado == "riesgo":
        lectura = "No tomar decisiones finales: primero completar o reconciliar datos."
    elif estado == "vigilar":
        lectura = "Se puede orientar la decision, pero con advertencias de calidad de datos."
    return {"score": score, "estado": estado, "alertas": alertas, "lectura": lectura}


def _decision_recomendaciones(metricas, areas, huecos, calidad_datos=None):
    recomendaciones = []
    area_map = {a["clave"]: a for a in areas}
    calidad_datos = calidad_datos or {"estado": "sano", "alertas": []}

    if huecos or calidad_datos.get("estado") == "riesgo":
        recomendaciones.append({
            "prioridad": "alta",
            "area": "Datos",
            "titulo": "Completar datos antes de tomar decisiones finas",
            "decision": "Priorizar los modulos con huecos o datos incoherentes: " + ", ".join([h["titulo"] for h in huecos[:3]] + [a["titulo"] for a in calidad_datos.get("alertas", [])[:2]]) + ".",
            "impacto_cliente": "Evita decidir solo por intuicion o por un KPI incompleto.",
            "impacto_economico": "Reduce riesgo de ofertas, compras o inversiones mal calculadas.",
            "riesgo": "La IA debe marcar incertidumbre si falta el dato base.",
        })
    if area_map["cliente"]["score"] < 70:
        recomendaciones.append({
            "prioridad": "alta" if area_map["cliente"]["score"] < 45 else "media",
            "area": "Cliente",
            "titulo": "Cerrar causas de insatisfaccion antes de vender mas",
            "decision": "Revisar quejas abiertas por area y convertir las repetidas en tareas operativas con responsable.",
            "impacto_cliente": "Mejora percepcion y repeticion; protege la promesa del establecimiento.",
            "impacto_economico": "Fidelizar sale mas barato que captar cliente nuevo con descuento.",
            "riesgo": "Vender mas con quejas abiertas aumenta rotacion de cliente.",
        })
    if area_map["economia"]["score"] < 70:
        recomendaciones.append({
            "prioridad": "alta" if area_map["economia"]["score"] < 45 else "media",
            "area": "Economia",
            "titulo": "No lanzar ofertas sin margen minimo",
            "decision": "Crear simulacion de oferta con coste laboral, comida, comision, limpieza y mantenimiento antes de publicarla.",
            "impacto_cliente": "Permite anadir valor sin bajar servicio.",
            "impacto_economico": "Evita llenar el hotel perdiendo dinero.",
            "riesgo": "Ocupacion alta puede ocultar margen negativo.",
        })
    if area_map["rrhh"]["score"] < 70:
        recomendaciones.append({
            "prioridad": "media",
            "area": "RRHH",
            "titulo": "Medir carga real para reducir rotacion",
            "decision": "Cruzar fichajes, ocupacion, quejas y departamento para detectar equipos tensionados.",
            "impacto_cliente": "Un equipo estable reconoce al cliente y sostiene la experiencia familiar.",
            "impacto_economico": "Baja coste oculto de rotacion, errores y formacion repetida.",
            "riesgo": "Reducir personal para mejorar margen puede destruir satisfaccion.",
        })
    if area_map["cocina"]["score"] < 70:
        recomendaciones.append({
            "prioridad": "media",
            "area": "Cocina",
            "titulo": "Menus rentables ligados a tendencia y capacidad real",
            "decision": "Analizar recetas con coste real, produccion y cliente objetivo antes de cambiar buffet o carta.",
            "impacto_cliente": "Mejora percepcion gastronomica sin prometer lo que cocina no puede ejecutar.",
            "impacto_economico": "Controla merma y coste por pax.",
            "riesgo": "Una propuesta alimentaria bonita puede ser ruinosa sin escandallo.",
        })
    if area_map["mantenimiento"]["score"] < 70:
        recomendaciones.append({
            "prioridad": "media",
            "area": "Infraestructura",
            "titulo": "No posicionar por encima de la infraestructura",
            "decision": "Cruzar averias, habitaciones bloqueadas e incidencias antes de vender experiencias premium.",
            "impacto_cliente": "Evita frustracion por promesa incumplida.",
            "impacto_economico": "Prioriza inversiones con retorno y efecto real en reputacion.",
            "riesgo": "Marketing sin infraestructura crea queja y descuento futuro.",
        })
    if not recomendaciones:
        recomendaciones.append({
            "prioridad": "info",
            "area": "Direccion",
            "titulo": "Preparar simulador de decisiones",
            "decision": "Crear escenarios: oferta, tour operador, inversion, reforma, cambio de menu o reorganizacion de turnos.",
            "impacto_cliente": "Permite probar mejoras antes de tocar la operacion real.",
            "impacto_economico": "Mide riesgo a 1, 3 y 5 anos antes de comprometer caja.",
            "riesgo": "Sin simulador, la decision depende demasiado de intuicion.",
        })
    return recomendaciones


def _centro_decision_contexto(conn, empresa_id, fecha_desde, fecha_hasta):
    metricas = _decision_metricas_generales(conn, empresa_id, fecha_desde, fecha_hasta)
    areas = _decision_areas(metricas)
    huecos = _decision_huecos(conn)
    calidad_datos = _decision_calidad_datos(metricas, huecos)
    recomendaciones = _decision_recomendaciones(metricas, areas, huecos, calidad_datos)
    salud = int((sum(a["score"] for a in areas) + calidad_datos["score"]) / max(len(areas) + 1, 1))
    return {
        "metricas": metricas,
        "areas": areas,
        "huecos": huecos,
        "calidad_datos": calidad_datos,
        "recomendaciones": recomendaciones,
        "salud": salud,
        "estado": "riesgo" if salud < 45 else "vigilar" if salud < 70 else "sano",
    }


def _coste_laboral_ops_periodo(conn, empresa_id, fecha_desde, fecha_hasta):
    if not _table_exists(conn, "resumen_prenomina"):
        return {"total": 0.0, "mensual": 0.0, "origen": "sin_prenomina", "meses": 0}

    ss_pct = 0.332
    if _table_exists(conn, "configuracion_prenomina"):
        ss_pct = _to_float(_scalar(
            conn,
            "SELECT ss_empresa_pct FROM configuracion_prenomina WHERE empresa_id = ? ORDER BY id DESC LIMIT 1",
            (empresa_id,),
            ss_pct,
        ), ss_pct)
    if ss_pct > 1:
        ss_pct = ss_pct / 100.0

    meses = sorted({(dia.year, dia.month) for dia in _date_range(fecha_desde, fecha_hasta)})
    total = 0.0
    usa_nominas = False
    usa_prenomina = False
    tiene_nominas = _table_exists(conn, "nominas")
    for anio, mes in meses:
        if tiene_nominas:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(
                    CASE
                        WHEN COALESCE(n.coste_empresa, 0) > 0 THEN n.coste_empresa
                        ELSE COALESCE(rp.coste_estimado, 0) * ?
                    END
                ), 0) AS coste,
                SUM(CASE WHEN COALESCE(n.coste_empresa, 0) > 0 THEN 1 ELSE 0 END) AS nominas_reales,
                SUM(CASE WHEN COALESCE(n.coste_empresa, 0) <= 0 THEN 1 ELSE 0 END) AS estimadas
                FROM resumen_prenomina rp
                LEFT JOIN nominas n ON n.id = (
                    SELECT n2.id
                    FROM nominas n2
                    WHERE n2.empleado_id = rp.empleado_id
                      AND n2.empresa_id = rp.empresa_id
                      AND n2.anio = rp.anio
                      AND n2.mes = rp.mes
                      AND COALESCE(n2.estado, 'pendiente') NOT IN ('anulada','anulado')
                    ORDER BY n2.id DESC
                    LIMIT 1
                )
                WHERE rp.empresa_id = ? AND rp.anio = ? AND rp.mes = ?
                """,
                (1 + ss_pct, empresa_id, anio, mes),
            ).fetchone()
            total += _to_float(row["coste"], 0.0)
            usa_nominas = usa_nominas or int(row["nominas_reales"] or 0) > 0
            usa_prenomina = usa_prenomina or int(row["estimadas"] or 0) > 0
        else:
            total += _to_float(_scalar(
                conn,
                """
                SELECT COALESCE(SUM(coste_estimado), 0) * ?
                FROM resumen_prenomina
                WHERE empresa_id = ? AND anio = ? AND mes = ?
                """,
                (1 + ss_pct, empresa_id, anio, mes),
                0,
            ), 0.0)
            usa_prenomina = True

    if usa_nominas and usa_prenomina:
        origen = "nomina_y_prenomina_ops"
    elif usa_nominas:
        origen = "nomina_real"
    elif usa_prenomina:
        origen = "prenomina_ops"
    else:
        origen = "sin_datos"
    mensual = total / max(len(meses), 1)
    return {"total": round(total, 2), "mensual": round(mensual, 2), "origen": origen, "meses": len(meses)}


def _guardar_recomendacion_decision(conn, empresa_id, rec):
    datos = {
        "area": rec.get("area"),
        "impacto_cliente": rec.get("impacto_cliente"),
        "impacto_economico": rec.get("impacto_economico"),
        "riesgo": rec.get("riesgo"),
    }
    conn.execute(
        """
        INSERT INTO asistente_recomendaciones (
            empresa_id, modulo, categoria, prioridad, titulo, detalle,
            datos_usados, accion_propuesta, estado, origen
        ) VALUES (?, 'Centro Decision', ?, ?, ?, ?, ?, ?, 'pendiente', 'centro_decision')
        """,
        (
            empresa_id,
            rec.get("area") or "Direccion",
            rec.get("prioridad") or "media",
            rec.get("titulo") or "Decision pendiente",
            rec.get("decision") or "",
            json.dumps(datos, ensure_ascii=False),
            rec.get("decision") or "",
        ),
    )


def _centro_activo_resumen(conn, empresa_id, centro_id=None):
    centro_id = centro_id if centro_id is not None else centro_activo_id()
    centro = None
    if centro_id and _table_exists(conn, "centros_trabajo"):
        centro = conn.execute(
            """
            SELECT id, nombre, codigo, descripcion
            FROM centros_trabajo
            WHERE id = ? AND empresa_id = ?
            """,
            (centro_id, empresa_id),
        ).fetchone()
    if not centro and _table_exists(conn, "centros_trabajo"):
        centro = conn.execute(
            """
            SELECT id, nombre, codigo, descripcion
            FROM centros_trabajo
            WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
            ORDER BY id
            LIMIT 1
            """,
            (empresa_id,),
        ).fetchone()
        centro_id = centro["id"] if centro else None
    return dict(centro) if centro else {"id": centro_id, "nombre": "Centro activo", "codigo": "", "descripcion": ""}


def _presupuesto_operativo_centro(conn, empresa_id, centro_id):
    if not (_table_exists(conn, "contabilidad_presupuestos") and _table_exists(conn, "contabilidad_presupuesto_lineas")):
        return {"id": None, "anio": None, "ingresos": 0.0, "gastos": 0.0, "costes_fijos": 0.0, "personal": 0.0, "comisiones": 0.0, "otros_gastos": 0.0}
    presupuesto = conn.execute(
        """
        SELECT id, anio, nombre
        FROM contabilidad_presupuestos
        WHERE empresa_id = ?
          AND COALESCE(centro_id, 0) = COALESCE(?, 0)
        ORDER BY anio DESC, actualizado_en DESC, id DESC
        LIMIT 1
        """,
        (empresa_id, centro_id),
    ).fetchone()
    if not presupuesto:
        return {"id": None, "anio": None, "ingresos": 0.0, "gastos": 0.0, "costes_fijos": 0.0, "personal": 0.0, "comisiones": 0.0, "otros_gastos": 0.0}
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN tipo = 'ingreso' THEN importe ELSE 0 END), 0) AS ingresos,
            COALESCE(SUM(CASE WHEN tipo = 'gasto' THEN importe ELSE 0 END), 0) AS gastos,
            COALESCE(SUM(CASE WHEN tipo = 'gasto' AND bloque = 'Costes fijos' THEN importe ELSE 0 END), 0) AS costes_fijos,
            COALESCE(SUM(CASE WHEN tipo = 'gasto' AND bloque = 'Personal' THEN importe ELSE 0 END), 0) AS personal,
            COALESCE(SUM(CASE WHEN tipo = 'gasto' AND bloque = 'Comercial' THEN importe ELSE 0 END), 0) AS comisiones
        FROM contabilidad_presupuesto_lineas
        WHERE presupuesto_id = ?
        """,
        (presupuesto["id"],),
    ).fetchone()
    ingresos = float(row["ingresos"] or 0)
    gastos = float(row["gastos"] or 0)
    costes_fijos = float(row["costes_fijos"] or 0)
    personal = float(row["personal"] or 0)
    comisiones = float(row["comisiones"] or 0)
    return {
        "id": presupuesto["id"],
        "anio": presupuesto["anio"],
        "nombre": presupuesto["nombre"],
        "ingresos": ingresos,
        "gastos": gastos,
        "costes_fijos": costes_fijos,
        "personal": personal,
        "comisiones": comisiones,
        "otros_gastos": max(gastos - costes_fijos - personal - comisiones, 0.0),
        "resultado": ingresos - gastos,
    }


def _base_simulador_centro(conn, empresa_id):
    centro = _centro_activo_resumen(conn, empresa_id)
    centro_id = centro.get("id")
    habitaciones = 0
    capacidad = 0
    if centro_id and _table_exists(conn, "habitaciones_recepcion"):
        habitaciones = int(_scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM habitaciones_recepcion
            WHERE empresa_id = ? AND centro_id = ? AND COALESCE(activo, 1) = 1
            """,
            (empresa_id, centro_id),
            0,
        ) or 0)
        capacidad = int(_scalar(
            conn,
            """
            SELECT COALESCE(SUM(COALESCE(capacidad, 2)), 0)
            FROM habitaciones_recepcion
            WHERE empresa_id = ? AND centro_id = ? AND COALESCE(activo, 1) = 1
            """,
            (empresa_id, centro_id),
            0,
        ) or 0)

    presupuesto = _presupuesto_operativo_centro(conn, empresa_id, centro_id)
    dias = 30
    ingresos_mes = float(presupuesto.get("ingresos") or 0) / 12 if presupuesto.get("ingresos") else 0.0
    costes_fijos_mes = float(presupuesto.get("costes_fijos") or 0) / 12 if presupuesto.get("costes_fijos") else 0.0
    personal_mes = float(presupuesto.get("personal") or 0) / 12 if presupuesto.get("personal") else 0.0
    otros_gastos_mes = float(presupuesto.get("otros_gastos") or 0) / 12 if presupuesto.get("otros_gastos") else 0.0
    comision_pct = round((float(presupuesto.get("comisiones") or 0) / float(presupuesto.get("ingresos") or 1)) * 100, 2) if presupuesto.get("ingresos") else 12.0

    ocupacion_pct = 65.0
    adr = 60.0
    pax_medio = round((capacidad / habitaciones), 2) if habitaciones else 2.0
    anio = int(presupuesto.get("anio") or date.today().year)
    reservas_total = 0
    room_nights = 0.0
    ingresos_reservas = 0.0
    if centro_id and _table_exists(conn, "reservas_recepcion"):
        reservas = conn.execute(
            """
            SELECT fecha_entrada, fecha_salida, precio_total
            FROM reservas_recepcion
            WHERE empresa_id = ? AND centro_id = ?
              AND COALESCE(estado, '') NOT IN ('cancelada', 'cancelado', 'no_show', 'no show')
              AND date(fecha_salida) > date(?)
              AND date(fecha_entrada) < date(?)
            """,
            (empresa_id, centro_id, f"{anio}-01-01", f"{anio + 1}-01-01"),
        ).fetchall()
        reservas_total = len(reservas)
        for reserva in reservas:
            entrada = _parse_date(reserva["fecha_entrada"])
            salida = _parse_date(reserva["fecha_salida"])
            if not entrada or not salida:
                continue
            inicio = max(entrada, date(anio, 1, 1))
            fin = min(salida, date(anio + 1, 1, 1))
            noches = max((fin - inicio).days, 0)
            room_nights += noches
            ingresos_reservas += float(reserva["precio_total"] or 0)
        if habitaciones:
            ocupacion_pct = round(min(max((room_nights / (habitaciones * 365)) * 100, 0), 100), 1)
        if room_nights and ingresos_reservas:
            adr = round(ingresos_reservas / room_nights, 2)
    if habitaciones and ingresos_mes and ocupacion_pct:
        habitaciones_ocupadas_mes = habitaciones * dias * (ocupacion_pct / 100)
        if habitaciones_ocupadas_mes:
            adr = round(ingresos_mes / habitaciones_ocupadas_mes, 2)

    coste_variable = round(otros_gastos_mes / max(habitaciones * dias * (ocupacion_pct / 100) * max(pax_medio, 1), 1), 2) if otros_gastos_mes else 18.0
    calidad = 78 if presupuesto.get("id") and reservas_total else 62 if presupuesto.get("id") else 35
    return {
        "centro": centro,
        "centro_id": centro_id,
        "habitaciones": habitaciones or 1,
        "capacidad": capacidad,
        "reservas_total": reservas_total,
        "room_nights": round(room_nights, 0),
        "presupuesto": presupuesto,
        "anio": anio,
        "dias": dias,
        "ocupacion_pct": ocupacion_pct,
        "adr": adr,
        "pax_medio": pax_medio,
        "coste_variable_pax_dia": coste_variable,
        "coste_fijo_mes": round(costes_fijos_mes, 2),
        "coste_personal_mes": round(personal_mes, 2),
        "coste_personal_origen": "presupuesto_centro" if personal_mes else "sin_datos",
        "comision_pct": comision_pct,
        "calidad_datos_pct": calidad,
    }


def _escenarios_rapidos_centro(base_centro):
    centro = base_centro.get("centro") or {}
    centro_nombre = centro.get("nombre") or "Centro activo"
    base = {
        "nombre": f"Base actual - {centro_nombre}",
        "tipo": "hotel",
        "decision_tipo": "oferta",
        "fase_negocio": "operativo",
        "zona_mercado": "rural",
        "categoria_establecimiento": "3_estrellas",
        "calidad_datos_pct": base_centro.get("calidad_datos_pct", 35),
        "habitaciones": base_centro.get("habitaciones") or 1,
        "ocupacion_pct": base_centro.get("ocupacion_pct") or 0,
        "adr": base_centro.get("adr") or 0,
        "dias": base_centro.get("dias") or 30,
        "pax_medio": base_centro.get("pax_medio") or 2,
        "coste_variable_pax_dia": base_centro.get("coste_variable_pax_dia") or 0,
        "coste_fijo_mes": base_centro.get("coste_fijo_mes") or 0,
        "coste_personal_mes": base_centro.get("coste_personal_mes") or 0,
        "alcance_costes_pct": 100,
        "fuente_datos": "mixto",
        "comision_pct": base_centro.get("comision_pct", 12),
        "inversion": 0,
        "satisfaccion_objetivo": 85,
        "rotacion_riesgo_pct": 15,
        "empleados_no_presentan": 0,
        "retrasos_pct": 0,
        "cambios_equipo": 0,
        "aceptacion_sistema_pct": 90,
        "cobertura_ops_pct": 70,
        "notas": "Escenario rapido generado desde los datos del centro activo.",
    }
    pruebas = [
        ("base", "Base actual", "Lectura de la foto del centro sin tocar palancas.", {}),
        ("precio_5", "Subir precio 5%", "Comprueba si el mercado aguanta una mejora pequena de ingreso medio.", {"adr": round(base["adr"] * 1.05, 2)}),
        ("ocupacion_5", "Subir ocupacion 5 puntos", "Mide si llenar mas compensa el coste variable y la tension operativa.", {"ocupacion_pct": min(round(base["ocupacion_pct"] + 5, 1), 100)}),
        ("coste_10", "Estres costes +10%", "Simula energia, comida, proveedores o mantenimiento peor de lo previsto.", {
            "coste_variable_pax_dia": round(base["coste_variable_pax_dia"] * 1.10, 2),
            "coste_fijo_mes": round(base["coste_fijo_mes"] * 1.10, 2),
        }),
        ("ops_falla", "Falla equipo", "Prueba faltas, retrasos y menor cobertura OPS sin cambiar ventas.", {
            "empleados_no_presentan": 2,
            "retrasos_pct": 12,
            "cambios_equipo": 4,
            "aceptacion_sistema_pct": 72,
            "cobertura_ops_pct": 55,
        }),
    ]
    escenarios = []
    for codigo, titulo, lectura, cambios in pruebas:
        datos = dict(base)
        datos.update(cambios)
        datos["nombre"] = f"{titulo} - {centro_nombre}"
        resultado = _calcular_escenario_decision(datos)
        escenarios.append({"codigo": codigo, "titulo": titulo, "lectura": lectura, "datos": datos, "resultado": resultado})
    return escenarios


def _escenarios_decision(conn, empresa_id, limite=20, tipo=None, centro_id=None):
    if not _table_exists(conn, "asistente_escenarios_decision"):
        return []
    params = [empresa_id]
    filtro_tipo = ""
    if tipo:
        filtro_tipo = " AND tipo = ?"
        params.append(tipo)
    filtro_centro = ""
    if centro_id is not None and "centro_id" in _columns(conn, "asistente_escenarios_decision"):
        filtro_centro = " AND COALESCE(centro_id, 0) = COALESCE(?, 0)"
        params.append(centro_id)
    params.append(limite)
    rows = conn.execute(
        f"""
        SELECT *
        FROM asistente_escenarios_decision
        WHERE empresa_id = ?{filtro_tipo}{filtro_centro}
        ORDER BY datetime(fecha_creacion) DESC, id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    escenarios = []
    for row in rows:
        item = dict(row)
        try:
            item["resultado"] = json.loads(item.get("resultado_json") or "{}")
        except Exception:
            item["resultado"] = {}
        escenarios.append(item)
    return escenarios


def _resumen_escenarios_decision(conn, empresa_id, centro_id=None):
    if not _table_exists(conn, "asistente_escenarios_decision"):
        return {"total": 0, "por_modelo": [], "simulados": 0}
    filtro_centro = ""
    params_centro = []
    if centro_id is not None and "centro_id" in _columns(conn, "asistente_escenarios_decision"):
        filtro_centro = " AND COALESCE(centro_id, 0) = COALESCE(?, 0)"
        params_centro.append(centro_id)
    total = int(_scalar(
        conn,
        f"SELECT COUNT(*) FROM asistente_escenarios_decision WHERE empresa_id = ?{filtro_centro}",
        (empresa_id, *params_centro),
        0,
    ) or 0)
    simulados = int(_scalar(
        conn,
        f"SELECT COUNT(*) FROM asistente_escenarios_decision WHERE empresa_id = ?{filtro_centro} AND COALESCE(fuente_datos, 'simulado') = 'simulado'",
        (empresa_id, *params_centro),
        0,
    ) or 0)
    rows = conn.execute(
        f"""
        SELECT tipo, COUNT(*) AS total
        FROM asistente_escenarios_decision
        WHERE empresa_id = ?{filtro_centro}
        GROUP BY tipo
        ORDER BY total DESC, tipo ASC
        """,
        (empresa_id, *params_centro),
    ).fetchall()
    return {"total": total, "simulados": simulados, "por_modelo": [dict(r) for r in rows]}


def _guardar_escenario_decision(conn, empresa_id, datos, resultado, usuario, centro_id=None):
    conn.execute(
        """
        INSERT INTO asistente_escenarios_decision (
            empresa_id, centro_id, nombre, tipo, decision_tipo, habitaciones, ocupacion_pct,
            fase_negocio, zona_mercado, categoria_establecimiento, calidad_datos_pct,
            adr, dias, pax_medio, coste_variable_pax_dia, coste_fijo_mes,
            coste_personal_mes, alcance_costes_pct, comision_pct, inversion, satisfaccion_objetivo,
            rotacion_riesgo_pct, empleados_no_presentan, retrasos_pct,
            cambios_equipo, aceptacion_sistema_pct, cobertura_ops_pct,
            fuente_datos, confianza_pct, notas, resultado_json, usuario
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            empresa_id,
            centro_id,
            datos["nombre"],
            datos["tipo"],
            datos["decision_tipo"],
            datos["habitaciones"],
            datos["ocupacion_pct"],
            datos.get("fase_negocio", "idea_inversion"),
            datos.get("zona_mercado", "sin_zona"),
            datos.get("categoria_establecimiento", "sin_categoria"),
            datos.get("calidad_datos_pct", 35),
            datos["adr"],
            datos["dias"],
            datos["pax_medio"],
            datos["coste_variable_pax_dia"],
            datos["coste_fijo_mes"],
            datos["coste_personal_mes"],
            datos.get("alcance_costes_pct", 100),
            datos["comision_pct"],
            datos["inversion"],
            datos["satisfaccion_objetivo"],
            datos["rotacion_riesgo_pct"],
            datos["empleados_no_presentan"],
            datos["retrasos_pct"],
            datos["cambios_equipo"],
            datos["aceptacion_sistema_pct"],
            datos["cobertura_ops_pct"],
            datos.get("fuente_datos") or resultado.get("fuente_datos") or "simulado",
            resultado.get("confianza_pct") or 35,
            datos["notas"],
            json.dumps(resultado, ensure_ascii=False),
            usuario,
        ),
    )


def _month_iter(inicio, fin):
    actual = date(inicio.year, inicio.month, 1)
    fin_mes = date(fin.year, fin.month, 1)
    while actual <= fin_mes:
        yield actual
        if actual.month == 12:
            actual = date(actual.year + 1, 1, 1)
        else:
            actual = date(actual.year, actual.month + 1, 1)


def _fin_mes(fecha):
    if fecha.month == 12:
        return date(fecha.year + 1, 1, 1) - timedelta(days=1)
    return date(fecha.year, fecha.month + 1, 1) - timedelta(days=1)


def _indicadores_externos_temporada(conn, empresa_id, limite=30):
    if not _table_exists(conn, "asistente_indicadores_externos"):
        return []
    return conn.execute(
        """
        SELECT *
        FROM asistente_indicadores_externos
        WHERE empresa_id = ?
        ORDER BY datetime(fecha_creacion) DESC, id DESC
        LIMIT ?
        """,
        (empresa_id, limite),
    ).fetchall()


def _temporada_metricas(conn, empresa_id, fecha_desde, fecha_hasta):
    habitaciones = int(_scalar(
        conn,
        "SELECT COUNT(*) FROM habitaciones_recepcion WHERE empresa_id = ? AND COALESCE(activo, 1) = 1",
        (empresa_id,),
        0,
    ) or 0) if _table_exists(conn, "habitaciones_recepcion") else 0
    meses = []
    reservas_por_mes = defaultdict(lambda: {
        "reservas": 0,
        "noches": 0,
        "pax": 0,
        "ingresos": 0.0,
        "comisiones": 0.0,
        "canales": defaultdict(int),
    })
    creado_cutoff = (date.today() - timedelta(days=14)).isoformat()
    pickup_14 = 0

    if _table_exists(conn, "reservas_recepcion"):
        canal_join = "LEFT JOIN recepcion_canales_reserva c ON c.id = r.canal_id" if _table_exists(conn, "recepcion_canales_reserva") else ""
        canal_select = "COALESCE(c.nombre, r.origen, 'Sin canal') AS canal" if canal_join else "COALESCE(r.origen, 'Sin canal') AS canal"
        rows = conn.execute(
            f"""
            SELECT r.*, {canal_select}
            FROM reservas_recepcion r
            {canal_join}
            WHERE r.empresa_id = ?
              AND date(r.fecha_entrada) <= date(?)
              AND date(r.fecha_salida) >= date(?)
              AND LOWER(COALESCE(r.estado, 'confirmada')) NOT IN ({",".join("?" for _ in ESTADOS_RESERVA_NO_ACTIVOS)})
            """,
            (empresa_id, fecha_hasta.isoformat(), fecha_desde.isoformat(), *ESTADOS_RESERVA_NO_ACTIVOS),
        ).fetchall()
        for row in rows:
            entrada = _parse_date(row["fecha_entrada"], fecha_desde) or fecha_desde
            salida = _parse_date(row["fecha_salida"], entrada + timedelta(days=1)) or (entrada + timedelta(days=1))
            desde = max(entrada, fecha_desde)
            hasta = min(salida, fecha_hasta + timedelta(days=1))
            noches_total = max((salida - entrada).days, 1)
            noches_periodo = max((hasta - desde).days, 0)
            if noches_periodo <= 0:
                continue
            precio = _to_float(row["precio_total"], 0.0) + _to_float(row["extras_total"] if "extras_total" in row.keys() else 0, 0.0)
            comision = _to_float(row["comision_canal"] if "comision_canal" in row.keys() else 0, 0.0)
            adultos = int(row["adultos"] or 0) if "adultos" in row.keys() else 0
            menores = int(row["menores"] or 0) if "menores" in row.keys() else 0
            pax = max(adultos + menores, 1)
            canal = row["canal"] if "canal" in row.keys() else (row["origen"] or "Sin canal")
            if str(row["fecha_creacion"] or "")[:10] >= creado_cutoff:
                pickup_14 += 1
            actual = desde
            while actual < hasta:
                mes = date(actual.year, actual.month, 1)
                tramo_fin = min(hasta, _fin_mes(mes) + timedelta(days=1))
                noches = max((tramo_fin - actual).days, 0)
                peso = noches / noches_total
                item = reservas_por_mes[mes]
                item["reservas"] += 1
                item["noches"] += noches
                item["pax"] += pax * noches
                item["ingresos"] += precio * peso
                item["comisiones"] += comision * peso
                item["canales"][canal] += 1
                actual = tramo_fin

    for mes in _month_iter(fecha_desde, fecha_hasta):
        data = reservas_por_mes[mes]
        dias_mes = (_fin_mes(mes) - mes).days + 1
        capacidad = habitaciones * dias_mes
        ocupacion = (data["noches"] / capacidad * 100) if capacidad else 0
        adr = (data["ingresos"] / data["noches"]) if data["noches"] else 0
        canal_top = "-"
        if data["canales"]:
            canal_top = sorted(data["canales"].items(), key=lambda kv: kv[1], reverse=True)[0][0]
        meses.append({
            "mes": mes,
            "label": mes.strftime("%m/%Y"),
            "reservas": data["reservas"],
            "noches": round(data["noches"], 1),
            "ocupacion": round(ocupacion, 1),
            "ingresos": round(data["ingresos"], 2),
            "comisiones": round(data["comisiones"], 2),
            "adr": round(adr, 2),
            "pax": round(data["pax"], 1),
            "canal_top": canal_top,
        })
    return {"habitaciones": habitaciones, "meses": meses, "pickup_14": pickup_14}


def _temporada_recomendaciones(metricas, indicadores):
    recomendaciones = []
    meses = metricas["meses"]
    bajos = [m for m in meses if m["ocupacion"] < 45]
    altos = [m for m in meses if m["ocupacion"] > 88]
    comision_alta = [m for m in meses if m["ingresos"] > 0 and (m["comisiones"] / m["ingresos"] * 100) > 16]
    if bajos:
        recomendaciones.append({
            "prioridad": "alta",
            "titulo": "Meses flojos necesitan plan comercial antes de bajar precio",
            "detalle": "Hay meses con ocupacion futura baja: " + ", ".join(m["label"] for m in bajos[:4]) + ".",
            "accion": "Cruzar pickup, canales, eventos, clima, vuelos y mercado emisor antes de lanzar descuentos.",
        })
    if altos:
        recomendaciones.append({
            "prioridad": "media",
            "titulo": "Meses fuertes: proteger margen y operacion",
            "detalle": "Hay meses con ocupacion alta: " + ", ".join(m["label"] for m in altos[:4]) + ".",
            "accion": "Subir control de cupos, coste laboral OPS, mantenimiento preventivo y propuesta gastronomica.",
        })
    if comision_alta:
        recomendaciones.append({
            "prioridad": "media",
            "titulo": "Dependencia de canal o comision elevada",
            "detalle": "Algunos meses tienen comision alta frente a ingresos.",
            "accion": "Comparar tour operador frente a venta directa y crear escenario en el simulador antes de firmar cupos.",
        })
    if metricas["pickup_14"] <= 2 and meses:
        recomendaciones.append({
            "prioridad": "media",
            "titulo": "Pickup reciente bajo",
            "detalle": f"Solo han entrado {metricas['pickup_14']} reservas en los ultimos 14 dias para el periodo analizado.",
            "accion": "Revisar visibilidad, precio, canales y tendencia externa antes de esperar al ultimo momento.",
        })
    externos_fuertes = [i for i in indicadores if (i["impacto"] or "").lower() in {"alto", "critico"}]
    if externos_fuertes:
        recomendaciones.append({
            "prioridad": "alta",
            "titulo": "Indicadores externos con impacto alto",
            "detalle": "Hay senales externas fuertes registradas: " + ", ".join((i["indicador"] or "") for i in externos_fuertes[:3]) + ".",
            "accion": "Convertir cada senal externa en hipotesis: precio, producto, personal, compras, mantenimiento o experiencia.",
        })
    if not recomendaciones:
        recomendaciones.append({
            "prioridad": "info",
            "titulo": "Preparar aprendizaje de temporada",
            "detalle": "La vision futura no muestra riesgos claros con los datos actuales.",
            "accion": "Guardar indicadores externos y decisiones tomadas para que la IA compare resultado real contra lo previsto.",
        })
    return recomendaciones


def generar_recomendaciones(conn, empresa_id, fecha_desde, fecha_hasta):
    recomendaciones = []
    recomendaciones.extend(_analisis_gestion_360(conn, empresa_id, fecha_desde, fecha_hasta))
    recomendaciones.extend(_analisis_oportunidades_establecimiento(conn, empresa_id, fecha_desde, fecha_hasta))
    recomendaciones.extend(_analisis_ocupacion(conn, empresa_id, fecha_desde, fecha_hasta))
    recomendaciones.extend(_analisis_direccion_personal(conn, empresa_id, fecha_desde, fecha_hasta))
    recomendaciones.extend(_analisis_clientes_especiales(conn, empresa_id, fecha_desde, fecha_hasta))
    recomendaciones.extend(_analisis_rrhh(conn, empresa_id, fecha_desde, fecha_hasta))
    recomendaciones.extend(_analisis_stock_compras(conn, empresa_id, fecha_desde, fecha_hasta))
    recomendaciones.extend(_analisis_produccion_appcc(conn, empresa_id, fecha_desde, fecha_hasta))
    recomendaciones.extend(_analisis_animacion(conn, empresa_id, fecha_desde, fecha_hasta))
    recomendaciones.extend(_analisis_mantenimiento(conn, empresa_id))
    recomendaciones.extend(_analisis_contabilidad(conn, empresa_id, fecha_hasta))
    recomendaciones.extend(_analisis_documentos_lectura(conn, empresa_id))
    recomendaciones.extend(_analisis_aprendizaje_interno(conn, empresa_id))
    return sorted(recomendaciones, key=lambda r: (PRIORIDAD_ORDEN.get(r["prioridad"], 9), r["modulo"], r["titulo"]))


def _historico(conn, empresa_id):
    rows = conn.execute(
        """
        SELECT id, fecha_creacion, modulo, categoria, prioridad, titulo, estado,
               accion_propuesta, resultado
        FROM asistente_recomendaciones
        WHERE empresa_id = ?
        ORDER BY datetime(fecha_creacion) DESC
        LIMIT 30
        """,
        (empresa_id,),
    ).fetchall()
    return rows


def _resumen_aprendizaje(conn, empresa_id):
    if not _table_exists(conn, "asistente_recomendaciones"):
        return {"total": 0, "aceptadas": 0, "descartadas": 0, "pendientes": 0, "revisadas": 0}
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN estado = 'aceptada' THEN 1 ELSE 0 END) AS aceptadas,
               SUM(CASE WHEN estado = 'descartada' THEN 1 ELSE 0 END) AS descartadas,
               SUM(CASE WHEN estado = 'pendiente' THEN 1 ELSE 0 END) AS pendientes,
               SUM(CASE WHEN estado = 'revisada' THEN 1 ELSE 0 END) AS revisadas
        FROM asistente_recomendaciones
        WHERE empresa_id = ?
        """,
        (empresa_id,),
    ).fetchone()
    return {
        "total": int(row["total"] or 0),
        "aceptadas": int(row["aceptadas"] or 0),
        "descartadas": int(row["descartadas"] or 0),
        "pendientes": int(row["pendientes"] or 0),
        "revisadas": int(row["revisadas"] or 0),
    }


def _memoria(conn, empresa_id, limite=80):
    return conn.execute(
        """
        SELECT id, tipo, modulo, clave, valor, confianza, origen, detalle,
               estado, fecha_creacion, fecha_actualizacion
        FROM asistente_memoria
        WHERE empresa_id = ?
        ORDER BY datetime(fecha_creacion) DESC
        LIMIT ?
        """,
        (empresa_id, limite),
    ).fetchall()


def _lecciones_decision(conn, empresa_id, limite=30):
    if not _table_exists(conn, "asistente_memoria"):
        return []
    return conn.execute(
        """
        SELECT id, tipo, modulo, clave, valor, confianza, origen, detalle,
               estado, fecha_creacion, fecha_actualizacion
        FROM asistente_memoria
        WHERE empresa_id = ? AND tipo = 'leccion_decision'
        ORDER BY datetime(fecha_creacion) DESC, id DESC
        LIMIT ?
        """,
        (empresa_id, limite),
    ).fetchall()


def _fuentes_conocimiento(conn, empresa_id, limite=40):
    if not _table_exists(conn, "asistente_fuentes_conocimiento"):
        return []
    return conn.execute(
        """
        SELECT id, fecha_creacion, tipo, ambito, area, titulo, url, autor,
               resumen, lectura, uso_recomendado, riesgos, confianza_pct,
               estado, lectura_ia, usuario
        FROM asistente_fuentes_conocimiento
        WHERE empresa_id = ?
        ORDER BY datetime(fecha_creacion) DESC, id DESC
        LIMIT ?
        """,
        (empresa_id, limite),
    ).fetchall()


def _procesar_fuentes_conocimiento_pendientes(conn, empresa_id, limite=25):
    if not _table_exists(conn, "asistente_fuentes_conocimiento"):
        return {"procesadas": 0, "pendientes": 0}
    rows = conn.execute(
        """
        SELECT *
        FROM asistente_fuentes_conocimiento
        WHERE empresa_id = ?
          AND COALESCE(estado, 'pendiente_revision') IN ('pendiente_revision', 'pendiente_contrastar')
        ORDER BY datetime(fecha_creacion) DESC, id DESC
        LIMIT ?
        """,
        (empresa_id, limite),
    ).fetchall()
    procesadas = 0
    for row in rows:
        texto = " ".join([
            row["titulo"] or "",
            row["resumen"] or "",
            row["lectura"] or "",
            row["uso_recomendado"] or "",
            row["riesgos"] or "",
        ]).lower()
        temas = []
        if any(x in texto for x in ("metric", "metrica", "kpi", "revpar", "adr", "ocupacion", "revenue")):
            temas.append("metricas_pms_revenue")
        if any(x in texto for x in ("guest", "cliente", "experiencia", "journey", "fidelizacion", "satisfaccion")):
            temas.append("experiencia_cliente")
        if any(x in texto for x in ("staff", "personal", "equipo", "labor", "productividad", "rotacion")):
            temas.append("rrhh_productividad")
        if any(x in texto for x in ("vivienda", "habitacional", "alojamiento", "garaje", "garajes", "residencia", "canarias", "temporada")):
            temas.append("vivienda_laboral")
        if any(x in texto for x in ("coste", "cost", "gasto", "margen", "rentabilidad", "profit")):
            temas.append("finanzas_margen")
        if any(x in texto for x in ("automatizacion", "ia", "software", "digital", "datos", "pms")):
            temas.append("tecnologia_datos")
        if any(x in texto for x in ("food", "cocina", "menu", "buffet", "restaurante", "bebida")):
            temas.append("cocina_restauracion")
        if not temas:
            temas.append("senal_general_hosteleria")

        datos_internos = []
        if "metricas_pms_revenue" in temas:
            datos_internos.extend(["ocupacion por dia", "ADR", "RevPAR", "pickup", "cancelaciones", "canal y comision"])
        if "experiencia_cliente" in temas:
            datos_internos.extend(["quejas", "repeticion", "reviews", "avisos cliente", "compensaciones"])
        if "rrhh_productividad" in temas:
            datos_internos.extend(["fichajes", "horas extra", "ausencias", "rotacion", "coste laboral por departamento"])
        if "vivienda_laboral" in temas:
            datos_internos.extend(["bajas por falta de alojamiento", "renuncias por vivienda", "tiempo medio de cobertura", "coste de alojamiento de plantilla", "puestos dificiles de cubrir"])
        if "finanzas_margen" in temas:
            datos_internos.extend(["margen mensual", "costes fijos", "costes variables", "cash flow", "punto muerto"])
        if "cocina_restauracion" in temas:
            datos_internos.extend(["escandallos", "coste por plato", "mermas", "consumos buffet", "APPCC"])
        if "tecnologia_datos" in temas:
            datos_internos.extend(["integraciones PMS/ERP/TPV", "datos incompletos", "errores de sincronizacion"])
        datos_internos = list(dict.fromkeys(datos_internos))[:10]

        uso = (
            "Usar como hipotesis externa. Cruzar con "
            + ", ".join(datos_internos[:6])
            + ". Crear escenario antes de decidir precio, inversion, plantilla u operativa."
        )
        riesgos = (
            "No aplicar como regla directa. Verificar fecha, pais/mercado, sesgo comercial de la fuente, "
            "tipo de establecimiento y datos internos reales o simulados."
        )
        lectura = (
            f"Temas detectados: {', '.join(temas)}. "
            f"Datos internos necesarios: {', '.join(datos_internos) if datos_internos else 'datos operativos y financieros basicos'}."
        )
        lectura_ia = (
            "Fuente procesada para contraste. La IA puede citarla como senal, pero debe pedir datos internos "
            "antes de recomendar una decision fuerte."
        )
        confianza = min(88, max(float(row["confianza_pct"] or 30), 48) + min(len(temas) * 2, 8))
        conn.execute(
            """
            UPDATE asistente_fuentes_conocimiento
            SET lectura = ?,
                uso_recomendado = ?,
                riesgos = ?,
                confianza_pct = ?,
                estado = 'procesada_contraste',
                lectura_ia = ?
            WHERE id = ? AND empresa_id = ?
            """,
            (lectura, uso, riesgos, confianza, lectura_ia, row["id"], empresa_id),
        )
        procesadas += 1
    pendientes = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM asistente_fuentes_conocimiento
        WHERE empresa_id = ?
          AND COALESCE(estado, 'pendiente_revision') IN ('pendiente_revision', 'pendiente_contrastar')
        """,
        (empresa_id,),
    ).fetchone()["total"] or 0
    return {"procesadas": procesadas, "pendientes": pendientes}


def _fuentes_externas(conn, empresa_id, limite=30):
    if not _table_exists(conn, "asistente_fuentes_externas"):
        return []
    return conn.execute(
        """
        SELECT id, fecha_creacion, nombre, url, tipo, ambito, area, estado,
               ultima_lectura, ultimo_resultado, usuario
        FROM asistente_fuentes_externas
        WHERE empresa_id = ?
        ORDER BY datetime(fecha_creacion) DESC, id DESC
        LIMIT ?
        """,
        (empresa_id, limite),
    ).fetchall()


def _resumen_fuentes_conocimiento(conn, empresa_id):
    if not _table_exists(conn, "asistente_fuentes_conocimiento"):
        return []
    etiquetas_area = {valor: texto for valor, texto in AREAS_CONOCIMIENTO}
    rows = conn.execute(
        """
        SELECT area,
               COUNT(*) AS total,
               SUM(CASE WHEN estado = 'procesada_contraste' THEN 1 ELSE 0 END) AS procesadas,
               SUM(CASE WHEN COALESCE(estado, 'pendiente_revision') IN ('pendiente_revision', 'pendiente_contrastar') THEN 1 ELSE 0 END) AS pendientes
        FROM asistente_fuentes_conocimiento
        WHERE empresa_id = ?
        GROUP BY area
        ORDER BY total DESC, area ASC
        """,
        (empresa_id,),
    ).fetchall()
    resumen = []
    for row in rows:
        area = row["area"] or "mercado"
        resumen.append({
            "area": area,
            "etiqueta": etiquetas_area.get(area, area.replace("_", " ").title()),
            "total": int(row["total"] or 0),
            "procesadas": int(row["procesadas"] or 0),
            "pendientes": int(row["pendientes"] or 0),
        })
    return resumen


def _row_dicts(rows):
    return [dict(row) for row in rows]


def _chat_estrategico_historial(conn, empresa_id, limite=12):
    if not _table_exists(conn, "asistente_chat_estrategico"):
        return []
    return conn.execute(
        """
        SELECT id, fecha_creacion, pregunta, respuesta, foco, confianza_pct, usuario
        FROM asistente_chat_estrategico
        WHERE empresa_id = ?
        ORDER BY datetime(fecha_creacion) DESC, id DESC
        LIMIT ?
        """,
        (empresa_id, limite),
    ).fetchall()


def _eventos_aprendizaje(conn, empresa_id, limite=40):
    return conn.execute(
        """
        SELECT id, recomendacion_id, tipo, modulo, senal, decision, resultado, usuario, fecha_evento
        FROM asistente_eventos_aprendizaje
        WHERE empresa_id = ?
        ORDER BY datetime(fecha_evento) DESC
        LIMIT ?
        """,
        (empresa_id, limite),
    ).fetchall()


@asistente_bp.route("/")
@login_required
def dashboard():
    if not _puede_ver_asistente():
        flash("No tienes permisos para ver el asistente operativo.", "warning")
        return redirect(url_for("home"))

    hoy = date.today()
    fecha_desde = _parse_date(request.args.get("fecha_desde"), hoy) or hoy
    fecha_hasta = _parse_date(request.args.get("fecha_hasta"), fecha_desde + timedelta(days=7)) or (fecha_desde + timedelta(days=7))
    if fecha_hasta < fecha_desde:
        fecha_hasta = fecha_desde

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    recomendaciones = generar_recomendaciones(conn, empresa_id, fecha_desde, fecha_hasta)
    resumen = defaultdict(int)
    modulos = defaultdict(list)
    for rec in recomendaciones:
        resumen[rec["prioridad"]] += 1
        modulos[rec["modulo"]].append(rec)

    return render_template(
        "asistente/dashboard.html",
        recomendaciones=recomendaciones,
        modulos=dict(modulos),
        resumen=dict(resumen),
        historico=_historico(conn, empresa_id),
        fecha_desde=fecha_desde.isoformat(),
        fecha_hasta=fecha_hasta.isoformat(),
        puede_ia_estrategica=_puede_ver_ia_estrategica(),
    )


@asistente_bp.route("/reportes-contenido")
@login_required
def reportes_contenido_ia():
    if not (_puede_ver_ia_estrategica() or usuario_tiene_permiso("asistente_reportes_ia")):
        flash("No tienes permisos para revisar reportes de contenido IA.", "warning")
        return redirect(url_for("asistente.dashboard"))
    conn = get_db()
    _ensure_ia_reportes_schema(conn)
    empresa_id = _empresa_id()
    estado = (request.args.get("estado") or "pendiente").strip().lower()
    if estado not in {"pendiente", "revisado", "descartado", "todos"}:
        estado = "pendiente"
    params = [empresa_id]
    where = ["empresa_id = ?"]
    if estado != "todos":
        where.append("estado = ?")
        params.append(estado)
    reportes = conn.execute(
        f"""
        SELECT *
        FROM ia_reportes_contenido
        WHERE {' AND '.join(where)}
        ORDER BY datetime(fecha_creacion) DESC, id DESC
        LIMIT 200
        """,
        params,
    ).fetchall()
    resumen = {row["estado"]: row["total"] for row in conn.execute(
        """
        SELECT estado, COUNT(*) AS total
        FROM ia_reportes_contenido
        WHERE empresa_id = ?
        GROUP BY estado
        """,
        (empresa_id,),
    ).fetchall()}
    return render_template(
        "asistente/reportes_contenido.html",
        reportes=reportes,
        resumen=resumen,
        estado=estado,
    )


@asistente_bp.route("/reportes-contenido/<int:reporte_id>/estado", methods=["POST"])
@login_required
def reportes_contenido_ia_estado(reporte_id):
    if not (_puede_ver_ia_estrategica() or usuario_tiene_permiso("asistente_reportes_ia")):
        flash("No tienes permisos para revisar reportes de contenido IA.", "warning")
        return redirect(url_for("asistente.dashboard"))
    conn = get_db()
    _ensure_ia_reportes_schema(conn)
    empresa_id = _empresa_id()
    estado = (request.form.get("estado") or "revisado").strip().lower()
    if estado not in {"pendiente", "revisado", "descartado"}:
        estado = "revisado"
    resultado = (request.form.get("resultado_revision") or "").strip()[:2000]
    conn.execute(
        """
        UPDATE ia_reportes_contenido
        SET estado = ?, fecha_revision = CURRENT_TIMESTAMP, revisado_por = ?, resultado_revision = ?
        WHERE id = ? AND empresa_id = ?
        """,
        (
            estado,
            session.get("user_nombre") or session.get("username") or "usuario",
            resultado,
            reporte_id,
            empresa_id,
        ),
    )
    conn.commit()
    flash("Reporte IA actualizado.", "success")
    return redirect(url_for("asistente.reportes_contenido_ia", estado=request.args.get("estado") or "pendiente"))


@asistente_bp.route("/operativo", methods=["GET", "POST"])
@login_required
def cerebro_operativo():
    if not _puede_ver_asistente():
        flash("No tienes permisos para usar el cerebro operativo.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    _ensure_asistente_schema(conn)
    presupuesto_id = request.values.get("presupuesto_id", type=int)
    presupuestos = []
    if centro_id is not None and {"id", "nombre", "anio", "empresa_id", "centro_id"}.issubset(_columns(conn, "contabilidad_presupuestos")):
        presupuestos = conn.execute(
            "SELECT id, nombre, anio FROM contabilidad_presupuestos WHERE empresa_id=? AND centro_id=? ORDER BY anio DESC, id DESC",
            (empresa_id, centro_id),
        ).fetchall()
    contexto = construir_contexto_operativo(conn, empresa_id, centro_id, presupuesto_id=presupuesto_id)
    if presupuesto_id and not contexto.get("presupuesto_seleccionado"):
        flash("No se ha podido consultar ese presupuesto en el centro activo. Selecciona uno de la lista.", "warning")
    resultado_actual = None
    peticion = (request.args.get("peticion") or "").strip()
    origen_pantalla_get = (request.args.get("origen_pantalla") or "").strip()
    usuario = _usuario_actual("usuario")

    if request.method == "POST" or peticion:
        peticion = (request.form.get("peticion") or peticion or "").strip()
        origen_pantalla = (request.form.get("origen_pantalla") or origen_pantalla_get or request.referrer or "").strip()
        if not peticion:
            flash("Escribe que quieres revisar o preparar.", "warning")
            return redirect(url_for("asistente.cerebro_operativo"))
        if origen_pantalla:
            contexto["origen_pantalla"] = origen_pantalla
        resultado_actual = responder_cerebro_operativo(peticion, contexto)
        if origen_pantalla:
            resultado_actual.setdefault("contexto_resumen", {})["origen_pantalla"] = origen_pantalla
        if request.method == "POST":
            cur = conn.execute(
                """
                INSERT INTO asistente_operativo_chat (
                    empresa_id, centro_id, usuario, peticion, intencion, modulo,
                    modo, riesgo, respuesta, contexto_json, estado
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'respondido')
                """,
                (
                    empresa_id,
                    centro_id,
                    usuario,
                    peticion,
                    resultado_actual["intencion"],
                    resultado_actual["modulo"],
                    resultado_actual["modo"],
                    resultado_actual["riesgo"],
                    resultado_actual["respuesta"],
                    json.dumps(resultado_actual.get("contexto_resumen") or {}, ensure_ascii=False),
                ),
            )
            chat_id = cur.lastrowid
            for accion in resultado_actual.get("acciones") or []:
                conn.execute(
                    """
                    INSERT INTO asistente_operativo_acciones (
                        empresa_id, centro_id, usuario, chat_id, intencion, modulo,
                        accion, herramienta, descripcion, plan_json, payload_json, estado,
                        requiere_confirmacion, riesgo
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'borrador', ?, ?)
                    """,
                    (
                        empresa_id,
                        centro_id,
                        usuario,
                        chat_id,
                        accion.get("intencion"),
                        accion.get("modulo"),
                        accion.get("accion"),
                        (accion.get("payload") or {}).get("herramienta", {}).get("clave") or accion.get("accion"),
                        accion.get("descripcion"),
                        json.dumps((accion.get("payload") or {}).get("plan") or [], ensure_ascii=False),
                        json.dumps(accion.get("payload") or {}, ensure_ascii=False),
                        int(accion.get("requiere_confirmacion", 1)),
                        accion.get("riesgo") or "medio",
                    ),
                )
            conn.commit()

    historial = conn.execute(
        """
        SELECT id, fecha_creacion, usuario, peticion, intencion, modulo, modo, riesgo, respuesta
        FROM asistente_operativo_chat
        WHERE empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
        ORDER BY datetime(fecha_creacion) DESC, id DESC
        LIMIT 12
        """,
        (empresa_id, centro_id),
    ).fetchall()
    acciones = conn.execute(
        """
        SELECT id, fecha_creacion, usuario, intencion, modulo, accion, herramienta, descripcion, plan_json,
               estado, requiere_confirmacion, riesgo
        FROM asistente_operativo_acciones
        WHERE empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
        ORDER BY datetime(fecha_creacion) DESC, id DESC
        LIMIT 12
        """,
        (empresa_id, centro_id),
    ).fetchall()
    objetivos = conn.execute(
        """
        SELECT id, fecha_creacion, modulo, objetivo, indicador, valor_objetivo,
               prioridad, estado, usuario
        FROM asistente_operativo_objetivos
        WHERE empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
              AND COALESCE(estado, 'activo') = 'activo'
        ORDER BY CASE prioridad
            WHEN 'critica' THEN 0 WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END,
            datetime(fecha_creacion) DESC
        LIMIT 12
        """,
        (empresa_id, centro_id),
    ).fetchall()
    tareas = conn.execute(
        """
        SELECT id, fecha_creacion, modulo, titulo, detalle, prioridad, estado,
               responsable, fecha_objetivo, origen, resultado
        FROM asistente_operativo_tareas
        WHERE empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
              AND COALESCE(estado, 'pendiente') IN ('pendiente','en_curso','bloqueada')
        ORDER BY CASE prioridad
            WHEN 'critica' THEN 0 WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END,
            datetime(fecha_creacion) DESC
        LIMIT 16
        """,
        (empresa_id, centro_id),
    ).fetchall()
    snapshots = conn.execute(
        """
        SELECT id, fecha_creacion, salud_score, estado, lectura, origen
        FROM asistente_operativo_snapshots
        WHERE empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
        ORDER BY datetime(fecha_creacion) DESC, id DESC
        LIMIT 8
        """,
        (empresa_id, centro_id),
    ).fetchall()
    senales = conn.execute(
        """
        SELECT id, fecha_creacion, modulo, origen, titulo, detalle, severidad, estado
        FROM asistente_operativo_senales
        WHERE empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
              AND COALESCE(estado, 'pendiente') = 'pendiente'
        ORDER BY CASE severidad
            WHEN 'critica' THEN 0 WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END,
            datetime(fecha_creacion) DESC
        LIMIT 12
        """,
        (empresa_id, centro_id),
    ).fetchall()
    return render_template(
        "asistente/operativo.html",
        contexto=contexto,
        resultado=resultado_actual,
        peticion=peticion,
        historial=historial,
        acciones=acciones,
        objetivos=objetivos,
        tareas=tareas,
        snapshots=snapshots,
        senales=senales,
        presupuestos=presupuestos,
        presupuesto_id=presupuesto_id,
    )


def _guardar_snapshot_operativo(conn, empresa_id, centro_id, contexto, origen="manual", usuario=None):
    brief = contexto.get("brief") or {}
    conn.execute(
        """
        INSERT INTO asistente_operativo_snapshots (
            empresa_id, centro_id, salud_score, estado, lectura,
            riesgos_json, acciones_json, causas_json, kpis_json, simulacion_json, indicadores_json, diagnosticos_json,
            origen, usuario
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            empresa_id,
            centro_id,
            int(brief.get("score") or 0),
            brief.get("estado") or "",
            brief.get("lectura") or "",
            json.dumps(brief.get("riesgos") or [], ensure_ascii=False),
            json.dumps(brief.get("acciones") or [], ensure_ascii=False),
            json.dumps(brief.get("causas") or [], ensure_ascii=False),
            json.dumps(contexto.get("kpis") or {}, ensure_ascii=False),
            json.dumps(contexto.get("simulacion") or {}, ensure_ascii=False),
            json.dumps(contexto.get("indicadores") or {}, ensure_ascii=False),
            json.dumps(contexto.get("diagnosticos") or {}, ensure_ascii=False),
            origen,
            usuario or _usuario_actual("usuario"),
        ),
    )


@asistente_bp.route("/operativo/snapshot", methods=["POST"])
@login_required
def cerebro_operativo_snapshot():
    if not _puede_ver_asistente():
        flash("No tienes permisos para guardar la foto operativa.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    _ensure_asistente_schema(conn)
    contexto = construir_contexto_operativo(conn, empresa_id, centro_id)
    _guardar_snapshot_operativo(
        conn,
        empresa_id,
        centro_id,
        contexto,
        origen=request.form.get("origen") or "manual",
        usuario=_usuario_actual("usuario"),
    )
    conn.commit()
    flash("Foto operativa guardada.", "success")
    return redirect(url_for("asistente.cerebro_operativo"))


@asistente_bp.route("/operativo/recetas/buscar-propuestas", methods=["POST"])
@login_required
def cerebro_operativo_buscar_propuestas_recetas():
    if not _puede_ver_asistente() or not usuario_tiene_permiso("produccion_editar"):
        flash("No tienes permisos para preparar propuestas de recetas.", "warning")
        return redirect(url_for("asistente.cerebro_operativo"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    usuario = _usuario_actual("cerebro_operativo")
    _ensure_asistente_schema(conn)
    try:
        limite = int(request.form.get("limite") or 8)
    except Exception:
        limite = 8
    limite = max(1, min(limite, 25))
    usar_web = request.form.get("usar_web") == "1"

    try:
        internas = _crear_propuestas_internas_alergenos(
            conn,
            empresa_id,
            centro_id,
            limite=max(25, limite * 3),
        )
        resultado = _crear_propuestas_busqueda_automatica_recetas(
            conn,
            empresa_id,
            centro_id,
            limite_recetas=limite,
            usar_web=usar_web,
        )
        conn.execute(
            """
            INSERT INTO asistente_operativo_tareas (
                empresa_id, centro_id, modulo, titulo, descripcion, prioridad,
                estado, origen, usuario
            ) VALUES (?, ?, 'Cocina / Recetas', ?, ?, 'alta', 'pendiente', 'cerebro_recetas', ?)
            """,
            (
                empresa_id,
                centro_id,
                "Revisar propuestas de recetas generadas por el cerebro",
                (
                    f"Propuestas internas de alergenos: {internas.get('creadas', 0)}. "
                    f"Busqueda automatica reviso {resultado['revisadas']} recetas: "
                    f"{resultado.get('directas', 0)} propuestas directas, "
                    f"{resultado['biblioteca']} propuestas desde biblioteca, "
                    f"{resultado['web']} desde web permitida y "
                    f"{resultado['sin_resultado']} sin resultado claro. "
                    "No se ha aplicado nada a recetas reales."
                ),
                usuario,
            ),
        )
        conn.commit()
        flash(
            (
                f"Cerebro recetas: revisadas {resultado['revisadas']}. "
                f"Alergenos internos {internas.get('creadas', 0)}, directas {resultado.get('directas', 0)}, "
                f"biblioteca {resultado['biblioteca']}, web {resultado['web']}, "
                f"sin resultado {resultado['sin_resultado']}. Quedan pendientes de validar."
            ),
            "success" if (internas.get("creadas", 0) or resultado.get("directas", 0) or resultado["biblioteca"] or resultado["web"]) else "warning",
        )
    except Exception as exc:
        conn.rollback()
        flash(f"No he podido buscar propuestas de recetas: {exc}", "danger")
    finally:
        conn.close()
    return redirect(url_for("recetas.fuentes_externas_recetas", estado="pendiente"))


@asistente_bp.route("/operativo/senales", methods=["POST"])
@login_required
def cerebro_operativo_crear_senal():
    if not _puede_ver_asistente():
        flash("No tienes permisos para crear senales operativas.", "warning")
        return redirect(url_for("home"))
    titulo = (request.form.get("titulo") or "").strip()
    if not titulo:
        flash("Escribe el titulo de la señal.", "warning")
        return redirect(url_for("asistente.cerebro_operativo"))
    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    _ensure_asistente_schema(conn)
    emitir_senal_operativa(
        conn,
        empresa_id,
        centro_id,
        request.form.get("modulo") or "Operacion",
        titulo,
        detalle=request.form.get("detalle") or "",
        severidad=request.form.get("severidad") or "media",
        origen=request.form.get("origen") or "manual",
        payload={"origen_pantalla": request.form.get("origen_pantalla") or request.referrer or ""},
        usuario=_usuario_actual("usuario"),
    )
    conn.commit()
    flash("Señal operativa enviada al cerebro.", "success")
    return redirect(url_for("asistente.cerebro_operativo"))


def _usuario_actual(default="cerebro_operativo"):
    try:
        return session.get("user_nombre") or session.get("username") or default
    except RuntimeError:
        return default


def _insertar_senal_si_no_existe(conn, empresa_id, centro_id, modulo, origen, titulo, detalle, severidad, payload=None, usuario=None):
    creada, _ = emitir_senal_operativa(
        conn,
        empresa_id,
        centro_id,
        modulo,
        titulo,
        detalle=detalle,
        severidad=severidad,
        origen=origen,
        payload=payload,
        usuario=usuario or _usuario_actual(),
        deduplicar=True,
    )
    return creada


def _generar_senales_desde_contexto(conn, empresa_id, centro_id, contexto, usuario=None):
    diag = contexto.get("diagnosticos") or {}
    creadas = 0
    rrhh = diag.get("rrhh") or {}
    prod = diag.get("produccion") or {}
    menus = diag.get("menus") or {}
    alergenos = diag.get("alergenos") or {}
    appcc_prl = diag.get("appcc_prl") or {}
    tpv_sala = diag.get("tpv_sala") or {}
    verifactu = diag.get("verifactu") or {}
    seguridad_social = diag.get("seguridad_social") or {}
    aprendizaje = diag.get("aprendizaje") or {}
    pms = diag.get("pms") or {}
    fin = diag.get("finanzas") or {}
    tareas = diag.get("tareas") or {}
    kpis = (contexto.get("kpis") or {}).get("items") or []
    simulacion = contexto.get("simulacion") or {}

    if rrhh.get("empleados_sin_contrato", 0) > 0:
        creadas += int(_insertar_senal_si_no_existe(
            conn, empresa_id, centro_id, "RRHH", "diagnostico_ia",
            "Empleados activos sin contrato",
            f"{rrhh['empleados_sin_contrato']} empleados activos no tienen contrato activo detectado.",
            "critica",
            {"contador": rrhh.get("empleados_sin_contrato")},
            usuario=usuario,
        ))
    if pms.get("reservas_sin_habitacion", 0) > 0:
        creadas += int(_insertar_senal_si_no_existe(
            conn, empresa_id, centro_id, "Recepcion/PMS", "diagnostico_ia",
            "Reservas activas sin habitacion",
            f"{pms['reservas_sin_habitacion']} reservas activas no tienen habitacion asignada.",
            "critica",
            {"contador": pms.get("reservas_sin_habitacion")},
            usuario=usuario,
        ))
    if pms.get("reservas_sin_precio", 0) > 0:
        creadas += int(_insertar_senal_si_no_existe(
            conn, empresa_id, centro_id, "Revenue/PMS", "diagnostico_ia",
            "Reservas activas sin precio",
            f"{pms['reservas_sin_precio']} reservas activas no tienen precio total.",
            "alta",
            {"contador": pms.get("reservas_sin_precio")},
            usuario=usuario,
        ))
    if prod.get("recetas_sin_ingredientes", 0) > 0:
        creadas += int(_insertar_senal_si_no_existe(
            conn, empresa_id, centro_id, "Cocina", "diagnostico_ia",
            "Recetas sin ingredientes",
            f"{prod['recetas_sin_ingredientes']} recetas no tienen ingredientes y pueden falsear compras/costes.",
            "alta",
            {"contador": prod.get("recetas_sin_ingredientes")},
            usuario=usuario,
        ))
    if prod.get("productos_sin_precio", 0) > 0:
        creadas += int(_insertar_senal_si_no_existe(
            conn, empresa_id, centro_id, "Compras/Cocina", "diagnostico_ia",
            "Productos sin precio",
            f"{prod['productos_sin_precio']} productos activos no tienen precio.",
            "alta",
            {"contador": prod.get("productos_sin_precio")},
            usuario=usuario,
        ))
    if menus.get("menus_7d", 0) <= 0:
        creadas += int(_insertar_senal_si_no_existe(
            conn, empresa_id, centro_id, "Menus/Cocina", "diagnostico_ia",
            "Menus proximos sin planificar",
            "No hay menus diarios planificados para los proximos 7 dias.",
            "alta",
            {"menus_7d": menus.get("menus_7d", 0)},
            usuario=usuario,
        ))
    if alergenos.get("recetas_sin_alergenos", 0) > 0:
        creadas += int(_insertar_senal_si_no_existe(
            conn, empresa_id, centro_id, "Alergenos", "diagnostico_ia",
            "Recetas sin alergenos",
            f"{alergenos['recetas_sin_alergenos']} recetas no tienen alergenos informados.",
            "alta",
            {"contador": alergenos.get("recetas_sin_alergenos")},
            usuario=usuario,
        ))
    if appcc_prl.get("appcc_no_ok_mes", 0) > 0 or appcc_prl.get("prl_incidentes_abiertos", 0) > 0:
        creadas += int(_insertar_senal_si_no_existe(
            conn, empresa_id, centro_id, "APPCC/PRL", "diagnostico_ia",
            "Seguridad alimentaria o PRL con incidencias",
            f"APPCC no OK mes: {appcc_prl.get('appcc_no_ok_mes', 0)}. Incidentes PRL abiertos: {appcc_prl.get('prl_incidentes_abiertos', 0)}.",
            "critica",
            appcc_prl,
            usuario=usuario,
        ))
    if tpv_sala.get("cajas_abiertas", 0) > 0:
        creadas += int(_insertar_senal_si_no_existe(
            conn, empresa_id, centro_id, "TPV/Sala", "diagnostico_ia",
            "Cajas TPV abiertas",
            f"{tpv_sala['cajas_abiertas']} cajas siguen abiertas.",
            "alta",
            {"cajas_abiertas": tpv_sala.get("cajas_abiertas")},
            usuario=usuario,
        ))
    if verifactu.get("estructura_preparada", 0) <= 0 and verifactu.get("facturas_venta_mes", 0) > 0:
        creadas += int(_insertar_senal_si_no_existe(
            conn, empresa_id, centro_id, "Administracion/Facturacion", "diagnostico_ia",
            "Verifactu no preparado",
            "Hay facturas de venta, pero no veo estructura Verifactu para huella, QR, encadenamiento y registro.",
            "alta",
            verifactu,
            usuario=usuario,
        ))
    if seguridad_social.get("errores", 0) > 0 or seguridad_social.get("estructura_preparada", 0) <= 0:
        severidad_ss = "critica" if seguridad_social.get("errores", 0) > 0 else "alta"
        detalle_ss = "Hay ficheros de Seguridad Social con error o rechazo." if seguridad_social.get("errores", 0) > 0 else "No veo estructura de ficheros de Seguridad Social preparada."
        creadas += int(_insertar_senal_si_no_existe(
            conn, empresa_id, centro_id, "RRHH/Seguridad Social", "diagnostico_ia",
            "Seguridad Social requiere revision",
            detalle_ss,
            severidad_ss,
            seguridad_social,
            usuario=usuario,
        ))
    if aprendizaje.get("recomendaciones_pendientes", 0) > 12 or aprendizaje.get("feedback_sin_resultado", 0) > 0:
        creadas += int(_insertar_senal_si_no_existe(
            conn, empresa_id, centro_id, "Cerebro IA", "diagnostico_ia",
            "Aprendizaje sin cerrar",
            f"Pendientes: {aprendizaje.get('recomendaciones_pendientes', 0)}. Feedback sin resultado: {aprendizaje.get('feedback_sin_resultado', 0)}.",
            "media",
            aprendizaje,
            usuario=usuario,
        ))
    if fin.get("recurrentes", 0) <= 0:
        creadas += int(_insertar_senal_si_no_existe(
            conn, empresa_id, centro_id, "Finanzas", "diagnostico_ia",
            "Costes recurrentes sin configurar",
            "No hay costes recurrentes activos para este centro.",
            "alta",
            {"recurrentes": fin.get("recurrentes", 0)},
            usuario=usuario,
        ))
    if tareas.get("criticas", 0) > 0:
        creadas += int(_insertar_senal_si_no_existe(
            conn, empresa_id, centro_id, "Operacion", "diagnostico_ia",
            "Tareas criticas abiertas",
            f"{tareas['criticas']} tareas criticas siguen abiertas.",
            "critica",
            {"contador": tareas.get("criticas")},
            usuario=usuario,
        ))
    for kpi in [item for item in kpis if item.get("estado") == "rojo"][:5]:
        creadas += int(_insertar_senal_si_no_existe(
            conn,
            empresa_id,
            centro_id,
            kpi.get("modulo") or "KPI operativo",
            "kpi_sistema_nervioso",
            f"KPI critico: {kpi.get('titulo')}",
            f"{kpi.get('lectura') or ''} Accion: {kpi.get('accion') or 'Revisar KPI.'}",
            "critica",
            {"kpi": kpi},
            usuario=usuario,
        ))
    sim_resumen = simulacion.get("resumen") or {}
    if sim_resumen.get("criticos", 0) > 0:
        centro_sim = (simulacion.get("centro") or {}).get("nombre") or contexto.get("centro") or "centro activo"
        creadas += int(_insertar_senal_si_no_existe(
            conn,
            empresa_id,
            centro_id,
            "Simulador",
            "simulador_hipotesis",
            f"Hipotesis simulada con resultados criticos en {centro_sim}",
            f"{sim_resumen.get('criticos')} resultados criticos simulados. No mezclar con dato real. {sim_resumen.get('lectura') or ''}",
            "alta",
            {"simulacion": simulacion},
            usuario=usuario,
        ))
    return creadas


@asistente_bp.route("/operativo/senales/auto", methods=["POST"])
@login_required
def cerebro_operativo_generar_senales_auto():
    if not _puede_ver_asistente():
        flash("No tienes permisos para generar señales automaticas.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo
    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    _ensure_asistente_schema(conn)
    contexto = construir_contexto_operativo(conn, empresa_id, centro_id)
    creadas = _generar_senales_desde_contexto(conn, empresa_id, centro_id, contexto, usuario=_usuario_actual())
    conn.commit()
    flash(f"Señales automaticas generadas: {creadas}.", "success")
    return redirect(url_for("asistente.cerebro_operativo"))


@asistente_bp.route("/operativo/sistema-nervioso/escanear", methods=["POST"])
@login_required
def cerebro_operativo_escanear_sistema():
    if not _puede_ver_asistente():
        flash("No tienes permisos para escanear el sistema nervioso operativo.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    usuario = _usuario_actual("usuario")
    _ensure_asistente_schema(conn)

    contexto = construir_contexto_operativo(conn, empresa_id, centro_id)
    senales_creadas = _generar_senales_desde_contexto(conn, empresa_id, centro_id, contexto, usuario=usuario)
    _guardar_snapshot_operativo(
        conn,
        empresa_id,
        centro_id,
        contexto,
        origen="escaner_sistema_nervioso",
        usuario=usuario,
    )

    brief = contexto.get("brief") or {}
    conn.execute(
        """
        INSERT INTO asistente_eventos_aprendizaje (
            empresa_id, centro_id, fecha, modulo, tipo, senal, decision, resultado, usuario
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            empresa_id,
            centro_id,
            datetime.now().isoformat(timespec="seconds"),
            "Cerebro operativo",
            "escaner_sistema_nervioso",
            f"{brief.get('score', 0)}/100 {brief.get('estado', '')}",
            "Escaneo completo del centro activo",
            f"Se guardo snapshot operativo y se generaron {senales_creadas} senales nuevas.",
            usuario,
        ),
    )
    conn.commit()
    flash(
        f"Sistema nervioso escaneado: salud {brief.get('score', 0)}/100 y {senales_creadas} seÃ±ales nuevas.",
        "success",
    )
    return redirect(url_for("asistente.cerebro_operativo"))


@asistente_bp.route("/operativo/senales/<int:senal_id>/tarea", methods=["POST"])
@login_required
def cerebro_operativo_senal_tarea(senal_id):
    if not _puede_ver_asistente():
        flash("No tienes permisos para procesar señales.", "warning")
        return redirect(url_for("home"))
    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    _ensure_asistente_schema(conn)
    senal = conn.execute(
        """
        SELECT id, modulo, titulo, detalle, severidad
        FROM asistente_operativo_senales
        WHERE id = ? AND empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
        """,
        (senal_id, empresa_id, centro_id),
    ).fetchone()
    if not senal:
        flash("No encuentro esa señal.", "warning")
        return redirect(url_for("asistente.cerebro_operativo"))
    prioridad = "critica" if senal["severidad"] == "critica" else "alta" if senal["severidad"] == "alta" else "media"
    cur = conn.execute(
        """
        INSERT INTO asistente_operativo_tareas (
            empresa_id, centro_id, modulo, titulo, detalle, prioridad,
            estado, origen, usuario
        ) VALUES (?, ?, ?, ?, ?, ?, 'pendiente', 'senal_operativa', ?)
        """,
        (
            empresa_id,
            centro_id,
            senal["modulo"],
            f"Resolver señal IA: {senal['titulo']}",
            senal["detalle"] or "",
            prioridad,
            session.get("user_nombre") or session.get("username") or "usuario",
        ),
    )
    conn.execute(
        """
        UPDATE asistente_operativo_senales
        SET estado='procesada', procesado_en=CURRENT_TIMESTAMP, tarea_id=?
        WHERE id = ?
        """,
        (cur.lastrowid, senal_id),
    )
    conn.commit()
    flash("Señal convertida en tarea.", "success")
    return redirect(url_for("asistente.cerebro_operativo"))


@asistente_bp.route("/operativo/objetivos", methods=["POST"])
@login_required
def cerebro_operativo_objetivo():
    if not _puede_ver_asistente():
        flash("No tienes permisos para crear objetivos operativos.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    objetivo = (request.form.get("objetivo") or "").strip()
    if not objetivo:
        flash("Escribe un objetivo para el cerebro operativo.", "warning")
        return redirect(url_for("asistente.cerebro_operativo"))
    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    _ensure_asistente_schema(conn)
    conn.execute(
        """
        INSERT INTO asistente_operativo_objetivos (
            empresa_id, centro_id, modulo, objetivo, indicador, valor_objetivo,
            prioridad, estado, usuario
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'activo', ?)
        """,
        (
            empresa_id,
            centro_id,
            request.form.get("modulo") or "Direccion",
            objetivo,
            request.form.get("indicador") or "",
            request.form.get("valor_objetivo") or "",
            request.form.get("prioridad") or "media",
            session.get("user_nombre") or session.get("username") or "usuario",
        ),
    )
    _guardar_memoria(
        conn,
        empresa_id,
        "objetivo_operativo",
        request.form.get("modulo") or "Direccion",
        objetivo[:90],
        request.form.get("valor_objetivo") or request.form.get("indicador") or "objetivo",
        objetivo,
        confianza=0.82,
        origen="cerebro_operativo",
    )
    conn.commit()
    flash("Objetivo operativo guardado.", "success")
    return redirect(url_for("asistente.cerebro_operativo"))


@asistente_bp.route("/operativo/brief/guardar", methods=["POST"])
@login_required
def cerebro_operativo_guardar_brief():
    if not _puede_ver_asistente():
        flash("No tienes permisos para guardar el brief operativo.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    _ensure_asistente_schema(conn)
    contexto = construir_contexto_operativo(conn, empresa_id, centro_id)
    brief = contexto.get("brief") or {}
    prioridad = "alta" if brief.get("estado") == "rojo" else "media" if brief.get("estado") == "ambar" else "baja"
    conn.execute(
        """
        INSERT INTO asistente_recomendaciones (
            empresa_id, modulo, categoria, prioridad, titulo, detalle,
            datos_usados, accion_propuesta, estado, origen
        ) VALUES (?, 'Cerebro operativo', 'brief_operativo', ?, ?, ?, ?, ?, 'pendiente', 'cerebro_operativo')
        """,
        (
            empresa_id,
            prioridad,
            f"Brief operativo {contexto.get('centro')} {brief.get('score', 0)}/100",
            brief.get("lectura") or "",
            json.dumps({
                "centro_id": centro_id,
                "riesgos": brief.get("riesgos") or [],
                "acciones": brief.get("acciones") or [],
            }, ensure_ascii=False),
            "\n".join(brief.get("acciones") or []),
        ),
    )
    conn.commit()
    flash("Brief guardado como recomendacion pendiente.", "success")
    return redirect(url_for("asistente.cerebro_operativo"))


@asistente_bp.route("/operativo/tareas", methods=["POST"])
@login_required
def cerebro_operativo_crear_tarea():
    if not _puede_ver_asistente():
        flash("No tienes permisos para crear tareas operativas.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    titulo = (request.form.get("titulo") or "").strip()
    if not titulo:
        flash("Escribe el titulo de la tarea.", "warning")
        return redirect(url_for("asistente.cerebro_operativo"))
    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    _ensure_asistente_schema(conn)
    conn.execute(
        """
        INSERT INTO asistente_operativo_tareas (
            empresa_id, centro_id, modulo, titulo, detalle, prioridad,
            estado, origen, responsable, fecha_objetivo, usuario
        ) VALUES (?, ?, ?, ?, ?, ?, 'pendiente', ?, ?, ?, ?)
        """,
        (
            empresa_id,
            centro_id,
            request.form.get("modulo") or "Operacion",
            titulo,
            request.form.get("detalle") or "",
            request.form.get("prioridad") or "media",
            request.form.get("origen") or "manual",
            request.form.get("responsable") or "",
            request.form.get("fecha_objetivo") or "",
            session.get("user_nombre") or session.get("username") or "usuario",
        ),
    )
    conn.commit()
    flash("Tarea operativa creada.", "success")
    return redirect(url_for("asistente.cerebro_operativo"))


@asistente_bp.route("/operativo/brief/tareas", methods=["POST"])
@login_required
def cerebro_operativo_brief_tareas():
    if not _puede_ver_asistente():
        flash("No tienes permisos para crear tareas desde el brief.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    _ensure_asistente_schema(conn)
    contexto = construir_contexto_operativo(conn, empresa_id, centro_id)
    brief = contexto.get("brief") or {}
    prioridad = "alta" if brief.get("estado") == "rojo" else "media" if brief.get("estado") == "ambar" else "baja"
    usuario = session.get("user_nombre") or session.get("username") or "usuario"
    creadas = 0
    for accion in (brief.get("acciones") or [])[:6]:
        existe = conn.execute(
            """
            SELECT id FROM asistente_operativo_tareas
            WHERE empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
              AND titulo = ? AND COALESCE(estado,'pendiente') IN ('pendiente','en_curso')
            LIMIT 1
            """,
            (empresa_id, centro_id, accion),
        ).fetchone()
        if existe:
            continue
        conn.execute(
            """
            INSERT INTO asistente_operativo_tareas (
                empresa_id, centro_id, modulo, titulo, detalle, prioridad,
                estado, origen, usuario
            ) VALUES (?, ?, 'Cerebro operativo', ?, ?, ?, 'pendiente', 'brief_operativo', ?)
            """,
            (
                empresa_id,
                centro_id,
                accion,
                brief.get("lectura") or "",
                prioridad,
                usuario,
            ),
        )
        creadas += 1
    conn.commit()
    flash(f"Tareas creadas desde el brief: {creadas}.", "success")
    return redirect(url_for("asistente.cerebro_operativo"))


@asistente_bp.route("/operativo/causas/tareas", methods=["POST"])
@login_required
def cerebro_operativo_causas_tareas():
    if not _puede_ver_asistente():
        flash("No tienes permisos para crear tareas desde causas.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    _ensure_asistente_schema(conn)
    contexto = construir_contexto_operativo(conn, empresa_id, centro_id)
    causas = (contexto.get("brief") or {}).get("causas") or []
    usuario = session.get("user_nombre") or session.get("username") or "usuario"
    creadas = 0
    for causa in causas[:6]:
        modulo = causa.get("modulo") or "Operacion"
        motivo = causa.get("motivo") or "Causa operativa detectada."
        accion_recomendada = causa.get("accion") or motivo
        peso = int(causa.get("peso") or 0)
        prioridad = "critica" if peso >= 18 else "alta" if peso >= 10 else "media"
        titulo = f"Corregir causa IA: {modulo} - {motivo}"
        existe = conn.execute(
            """
            SELECT id FROM asistente_operativo_tareas
            WHERE empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
              AND titulo = ? AND COALESCE(estado,'pendiente') IN ('pendiente','en_curso','bloqueada')
            LIMIT 1
            """,
            (empresa_id, centro_id, titulo),
        ).fetchone()
        if existe:
            continue
        conn.execute(
            """
            INSERT INTO asistente_operativo_tareas (
                empresa_id, centro_id, modulo, titulo, detalle, prioridad,
                estado, origen, usuario
            ) VALUES (?, ?, ?, ?, ?, ?, 'pendiente', 'causa_probable_ia', ?)
            """,
            (
                empresa_id,
                centro_id,
                modulo,
                titulo,
                f"Peso {peso}. {motivo}. Accion recomendada: {accion_recomendada}",
                prioridad,
                usuario,
            ),
        )
        creadas += 1
    conn.commit()
    flash(f"Tareas creadas desde causas probables: {creadas}.", "success")
    return redirect(url_for("asistente.cerebro_operativo"))


@asistente_bp.route("/operativo/simulacion/tareas", methods=["POST"])
@login_required
def cerebro_operativo_simulacion_tareas():
    if not _puede_ver_asistente():
        flash("No tienes permisos para revisar simulaciones.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo
    bloqueo_gestion = _requiere_gestion_simulador_decision()
    if bloqueo_gestion:
        return bloqueo_gestion

    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    flash(
        f"Separacion aplicada para empresa {empresa_id}, centro {centro_id or 'sin centro'}: una simulacion no crea tareas operativas reales. Usala para laboratorio, hipotesis o decision humana revisada.",
        "info",
    )
    return redirect(url_for("asistente.cerebro_operativo"))


@asistente_bp.route("/operativo/rutina-diaria", methods=["POST"])
@login_required
def cerebro_operativo_rutina_diaria():
    if not _puede_ver_asistente():
        flash("No tienes permisos para crear rutina diaria.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    _ensure_asistente_schema(conn)
    contexto = construir_contexto_operativo(conn, empresa_id, centro_id)
    diag = contexto.get("diagnosticos") or {}
    usuario = session.get("user_nombre") or session.get("username") or "usuario"
    tareas_base = [
        ("Direccion", "Revisar brief operativo del dia", contexto.get("brief", {}).get("lectura") or "", "alta"),
        ("Recepcion", "Revisar reservas activas, habitaciones y precios", "Confirmar que no hay reservas sin habitacion, sin precio o con riesgo de overbooking.", "alta"),
        ("Cocina", "Revisar menus, produccion, recetas y productos sin precio", "No generar pedidos si hay recetas sin ingredientes o productos sin precio.", "alta"),
        ("RRHH", "Revisar fichajes, ausencias y cobertura de turnos", "Cruzar empleados activos, fichajes y posibles bloqueos de nomina.", "media"),
        ("Mantenimiento", "Revisar ordenes abiertas y riesgos APPCC", "Priorizar averias que afecten venta, seguridad o cliente.", "media"),
    ]
    if (diag.get("tareas") or {}).get("criticas", 0) > 0:
        tareas_base.insert(0, ("Operacion", "Resolver tareas criticas abiertas", "Hay tareas criticas pendientes que reducen la salud operativa.", "critica"))
    if (diag.get("finanzas") or {}).get("recurrentes", 0) <= 0:
        tareas_base.append(("Finanzas", "Configurar costes recurrentes del centro", "Luz, agua, gas, seguros, gestoria, internet, impuestos y otros fijos.", "alta"))
    for causa in (contexto.get("brief") or {}).get("causas", [])[:3]:
        modulo = causa.get("modulo") or "Operacion"
        motivo = causa.get("motivo") or "Causa operativa detectada."
        accion_recomendada = causa.get("accion") or motivo
        peso = int(causa.get("peso") or 0)
        prioridad = "critica" if peso >= 18 else "alta" if peso >= 10 else "media"
        tareas_base.append((modulo, f"Atacar causa IA: {motivo}", f"Peso {peso}. Modulo origen: {modulo}. Accion recomendada: {accion_recomendada}", prioridad))

    creadas = 0
    senales_creadas = _generar_senales_desde_contexto(conn, empresa_id, centro_id, contexto, usuario=usuario)
    hoy = date.today().isoformat()
    for modulo, titulo, detalle, prioridad in tareas_base:
        existe = conn.execute(
            """
            SELECT id FROM asistente_operativo_tareas
            WHERE empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
              AND titulo = ? AND fecha_objetivo = ?
              AND COALESCE(estado,'pendiente') IN ('pendiente','en_curso','bloqueada')
            LIMIT 1
            """,
            (empresa_id, centro_id, titulo, hoy),
        ).fetchone()
        if existe:
            continue
        conn.execute(
            """
            INSERT INTO asistente_operativo_tareas (
                empresa_id, centro_id, modulo, titulo, detalle, prioridad,
                estado, origen, fecha_objetivo, usuario
            ) VALUES (?, ?, ?, ?, ?, ?, 'pendiente', 'rutina_diaria_ia', ?, ?)
            """,
            (empresa_id, centro_id, modulo, titulo, detalle, prioridad, hoy, usuario),
        )
        creadas += 1
    _guardar_snapshot_operativo(conn, empresa_id, centro_id, contexto, origen="rutina_diaria_ia", usuario=usuario)
    conn.commit()
    flash(f"Rutina diaria creada: {creadas} tareas nuevas y {senales_creadas} señales IA.", "success")
    return redirect(url_for("asistente.cerebro_operativo"))


@asistente_bp.route("/operativo/tareas/<int:tarea_id>/estado", methods=["POST"])
@login_required
def cerebro_operativo_tarea_estado(tarea_id):
    if not _puede_ver_asistente():
        flash("No tienes permisos para actualizar tareas operativas.", "warning")
        return redirect(url_for("home"))
    estado = (request.form.get("estado") or "pendiente").strip().lower()
    if estado not in {"pendiente", "en_curso", "hecha", "bloqueada", "descartada"}:
        estado = "pendiente"
    resultado = (request.form.get("resultado") or "").strip()
    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    _ensure_asistente_schema(conn)
    tarea = conn.execute(
        """
        SELECT id, modulo, titulo, prioridad FROM asistente_operativo_tareas
        WHERE id = ? AND empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
        """,
        (tarea_id, empresa_id, centro_id),
    ).fetchone()
    if not tarea:
        flash("No encuentro esa tarea operativa.", "warning")
        return redirect(url_for("asistente.cerebro_operativo"))
    conn.execute(
        """
        UPDATE asistente_operativo_tareas
        SET estado = ?, resultado = ?
        WHERE id = ? AND empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
        """,
        (estado, resultado, tarea_id, empresa_id, centro_id),
    )
    conn.execute(
        """
        INSERT INTO asistente_eventos_aprendizaje (
            empresa_id, recomendacion_id, tipo, modulo, senal, decision,
            resultado, usuario
        ) VALUES (?, NULL, 'tarea_operativa', ?, ?, ?, ?, ?)
        """,
        (
            empresa_id,
            tarea["modulo"],
            tarea["titulo"],
            estado,
            resultado or f"Tarea marcada como {estado}.",
            session.get("user_nombre") or session.get("username") or "usuario",
        ),
    )
    conn.commit()
    flash("Tarea operativa actualizada.", "success")
    return redirect(url_for("asistente.cerebro_operativo"))


@asistente_bp.route("/operativo/api/contexto")
@login_required
def cerebro_operativo_api_contexto():
    if not _puede_ver_asistente():
        return jsonify({"ok": False, "error": "sin_permiso"}), 403
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return jsonify({"ok": False, "error": "requiere_permiso_ia_estrategica"}), 403

    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    _ensure_asistente_schema(conn)
    contexto = construir_contexto_operativo(conn, empresa_id, centro_id)
    origen = (request.args.get("origen_pantalla") or request.headers.get("Referer") or "").strip()
    tareas = conn.execute(
        """
        SELECT id, modulo, titulo, prioridad, estado, responsable, fecha_objetivo
        FROM asistente_operativo_tareas
        WHERE empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
              AND COALESCE(estado, 'pendiente') IN ('pendiente','en_curso','bloqueada')
        ORDER BY CASE prioridad
            WHEN 'critica' THEN 0 WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END,
            datetime(fecha_creacion) DESC
        LIMIT 8
        """,
        (empresa_id, centro_id),
    ).fetchall()
    snapshots = conn.execute(
        """
        SELECT id, fecha_creacion, salud_score, estado, origen
        FROM asistente_operativo_snapshots
        WHERE empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
        ORDER BY datetime(fecha_creacion) DESC, id DESC
        LIMIT 6
        """,
        (empresa_id, centro_id),
    ).fetchall()
    senales = conn.execute(
        """
        SELECT id, modulo, origen, titulo, severidad, estado
        FROM asistente_operativo_senales
        WHERE empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
              AND COALESCE(estado, 'pendiente') = 'pendiente'
        ORDER BY CASE severidad
            WHEN 'critica' THEN 0 WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END,
            datetime(fecha_creacion) DESC
        LIMIT 8
        """,
        (empresa_id, centro_id),
    ).fetchall()
    return jsonify({
        "ok": True,
        "empresa": contexto.get("empresa"),
        "centro": contexto.get("centro"),
        "brief": contexto.get("brief"),
        "tendencia": contexto.get("tendencia"),
        "indicadores": contexto.get("indicadores"),
        "diagnosticos": contexto.get("diagnosticos"),
        "huecos": contexto.get("huecos"),
        "sugerencia": sugerencia_contextual(origen, contexto) if origen else None,
        "tareas": [dict(t) for t in tareas],
        "senales": [dict(s) for s in senales],
        "snapshots": [dict(s) for s in snapshots],
    })


@asistente_bp.route("/operativo/api/senales", methods=["POST"])
@login_required
def cerebro_operativo_api_senales():
    if not _puede_ver_asistente():
        return jsonify({"ok": False, "error": "sin_permiso"}), 403
    data = request.get_json(silent=True) or request.form
    titulo = (data.get("titulo") or "").strip()
    if not titulo:
        return jsonify({"ok": False, "error": "titulo_requerido"}), 400
    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    _ensure_asistente_schema(conn)
    payload = data.get("payload") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        payload = {
            "origen_pantalla": data.get("origen_pantalla") or request.referrer or "",
        }
    creada, senal_id = emitir_senal_operativa(
        conn,
        empresa_id,
        centro_id,
        data.get("modulo") or "Operacion",
        titulo,
        detalle=data.get("detalle") or "",
        severidad=data.get("severidad") or "media",
        origen=data.get("origen") or "api",
        payload=payload,
        usuario=_usuario_actual("usuario"),
        deduplicar=str(data.get("deduplicar", "1")) not in {"0", "false", "False"},
    )
    conn.commit()
    return jsonify({"ok": True, "creada": creada, "senal_id": senal_id})


@asistente_bp.route("/operativo/acciones/<int:accion_id>/estado", methods=["POST"])
@login_required
def cerebro_operativo_accion_estado(accion_id):
    if not _puede_ver_asistente():
        flash("No tienes permisos para actualizar acciones operativas.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    estado = (request.form.get("estado") or "borrador").strip().lower()
    if estado not in {"borrador", "validada", "descartada", "bloqueada"}:
        estado = "borrador"
    resultado = (request.form.get("resultado") or "").strip()
    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    _ensure_asistente_schema(conn)
    accion = conn.execute(
        """
        SELECT id, intencion, modulo, accion, herramienta, descripcion, estado, riesgo
        FROM asistente_operativo_acciones
        WHERE id = ? AND empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
        """,
        (accion_id, empresa_id, centro_id),
    ).fetchone()
    if not accion:
        flash("No encuentro esa accion operativa.", "warning")
        return redirect(url_for("asistente.cerebro_operativo"))
    conn.execute(
        """
        UPDATE asistente_operativo_acciones
        SET estado = ?, resultado = ?
        WHERE id = ? AND empresa_id = ? AND COALESCE(centro_id, 0) = COALESCE(?, 0)
        """,
        (estado, resultado, accion_id, empresa_id, centro_id),
    )
    conn.execute(
        """
        INSERT INTO asistente_eventos_aprendizaje (
            empresa_id, recomendacion_id, tipo, modulo, senal, decision,
            resultado, usuario
        ) VALUES (?, NULL, 'accion_operativa', ?, ?, ?, ?, ?)
        """,
        (
            empresa_id,
            accion["modulo"],
            accion["descripcion"],
            estado,
            resultado or f"Accion {accion['herramienta'] or accion['accion']} marcada como {estado}.",
            session.get("user_nombre") or session.get("username") or "usuario",
        ),
    )
    if estado in {"validada", "descartada", "bloqueada"}:
        confianza = {"validada": 0.78, "bloqueada": 0.66, "descartada": 0.58}.get(estado, 0.5)
        _guardar_memoria(
            conn,
            empresa_id,
            "aprendizaje_operativo",
            accion["modulo"],
            f"{accion['herramienta'] or accion['accion']}:{estado}",
            estado,
            resultado or accion["descripcion"],
            confianza=confianza,
            origen="cerebro_operativo",
        )
    conn.commit()
    flash("Accion operativa actualizada.", "success")
    return redirect(url_for("asistente.cerebro_operativo"))


@asistente_bp.route("/decision")
@login_required
def centro_decision():
    if not _puede_ver_asistente():
        flash("No tienes permisos para ver el centro de decision.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    hoy = date.today()
    fecha_desde = _parse_date(request.args.get("fecha_desde"), hoy.replace(day=1)) or hoy.replace(day=1)
    fecha_hasta = _parse_date(request.args.get("fecha_hasta"), hoy + timedelta(days=30)) or (hoy + timedelta(days=30))
    if fecha_hasta < fecha_desde:
        fecha_hasta = fecha_desde

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    contexto = _centro_decision_contexto(conn, empresa_id, fecha_desde, fecha_hasta)
    return render_template(
        "asistente/decision.html",
        contexto=contexto,
        metricas=contexto["metricas"],
        areas=contexto["areas"],
        huecos=contexto["huecos"],
        recomendaciones=contexto["recomendaciones"],
        fecha_desde=fecha_desde.isoformat(),
        fecha_hasta=fecha_hasta.isoformat(),
    )


@asistente_bp.route("/decision/chat", methods=["GET", "POST"])
@login_required
def chat_estrategico():
    if not _puede_ver_asistente():
        flash("No tienes permisos para usar el chat estrategico.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    hoy = date.today()
    fecha_desde = hoy.replace(day=1)
    fecha_hasta = hoy + timedelta(days=30)
    contexto_decision = _centro_decision_contexto(conn, empresa_id, fecha_desde, fecha_hasta)
    resultado_actual = None
    pregunta = (request.args.get("pregunta") or "").strip()
    if request.method == "POST" or pregunta:
        pregunta = (request.form.get("pregunta") or "").strip()
        if not pregunta:
            pregunta = (request.args.get("pregunta") or "").strip()
        if not pregunta:
            flash("Escribe una pregunta para la IA estrategica.", "warning")
            return redirect(url_for("asistente.chat_estrategico"))
        aprendizaje_guardado = None
        if request.method == "POST" and request.form.get("guardar_aprendizaje") == "1":
            aprendizaje_guardado = clasificar_aprendizaje_chat(
                pregunta,
                request.form.get("tipo_aprendizaje") or "auto",
            )
            _guardar_memoria(
                conn,
                empresa_id,
                aprendizaje_guardado["tipo"],
                aprendizaje_guardado["modulo"],
                aprendizaje_guardado["clave"],
                aprendizaje_guardado["valor"],
                aprendizaje_guardado["detalle"],
                confianza=aprendizaje_guardado["confianza"],
                origen=f"chat:{session.get('user_nombre') or session.get('username') or 'usuario'}",
            )
        fuentes = _row_dicts(_fuentes_conocimiento(conn, empresa_id, limite=80))
        memoria = _row_dicts(_memoria(conn, empresa_id, limite=80))
        escenarios = _escenarios_decision(conn, empresa_id, limite=20)
        resultado_actual = responder_chat_estrategico(
            pregunta,
            {
                "fuentes": fuentes,
                "memoria": memoria,
                "escenarios": escenarios,
                "metricas": contexto_decision["metricas"],
                "aprendizaje_guardado": aprendizaje_guardado,
            },
        )
        if request.method == "POST":
            conn.execute(
                """
                INSERT INTO asistente_chat_estrategico (
                    empresa_id, pregunta, respuesta, foco, confianza_pct,
                    contexto_json, usuario
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    empresa_id,
                    pregunta,
                    resultado_actual["respuesta"],
                    resultado_actual["foco"],
                    resultado_actual["confianza_pct"],
                    json.dumps({
                        "fuentes": [f.get("id") for f in resultado_actual["fuentes_usadas"]],
                        "memoria": [m.get("id") for m in resultado_actual["memoria_usada"]],
                        "escenarios": [e.get("id") for e in resultado_actual["escenarios_usados"]],
                        "modelo_negocio": resultado_actual.get("modelo_negocio"),
                        "aprendizaje_guardado": resultado_actual.get("aprendizaje_guardado"),
                        "contraste": {
                            "nivel": (resultado_actual.get("contraste") or {}).get("nivel"),
                            "fuentes": [f.get("id") for f in (resultado_actual.get("contraste") or {}).get("fuentes", [])],
                            "memoria": [m.get("id") for m in (resultado_actual.get("contraste") or {}).get("memoria", [])],
                            "escenarios": [e.get("id") for e in (resultado_actual.get("contraste") or {}).get("escenarios", [])],
                        },
                        "plan_contraste": resultado_actual.get("plan_contraste"),
                        "analisis_datos": resultado_actual.get("analisis_datos"),
                    }, ensure_ascii=False),
                    session.get("user_nombre") or session.get("username") or "usuario",
                ),
            )
            conn.commit()

    historial = _chat_estrategico_historial(conn, empresa_id)
    return render_template(
        "asistente/chat_estrategico.html",
        pregunta=pregunta,
        resultado=resultado_actual,
        historial=historial,
        fuentes=_fuentes_conocimiento(conn, empresa_id, limite=8),
        escenarios=_escenarios_decision(conn, empresa_id, limite=5),
        memoria=_memoria(conn, empresa_id, limite=8),
        metricas=contexto_decision["metricas"],
    )


@asistente_bp.route("/decision/brief")
@login_required
def brief_ejecutivo():
    if not _puede_ver_asistente():
        flash("No tienes permisos para ver el brief ejecutivo.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    hoy = date.today()
    contexto_decision = _centro_decision_contexto(conn, empresa_id, hoy.replace(day=1), hoy + timedelta(days=30))
    fuentes = _row_dicts(_fuentes_conocimiento(conn, empresa_id, limite=200))
    memoria = _row_dicts(_memoria(conn, empresa_id, limite=100))
    escenarios = _escenarios_decision(conn, empresa_id, limite=30)
    tendencias = detectar_tendencias_sectoriales(fuentes)
    radar = generar_radar_estrategico({
        "fuentes": fuentes,
        "memoria": memoria,
        "escenarios": escenarios,
        "metricas": contexto_decision["metricas"],
    })
    brief = generar_brief_ejecutivo({
        "salud": contexto_decision["salud"],
        "metricas": contexto_decision["metricas"],
        "radar": radar,
        "tendencias": tendencias,
        "escenarios": escenarios,
        "fuentes": fuentes,
    })
    return render_template(
        "asistente/brief_ejecutivo.html",
        brief=brief,
        radar=radar,
        tendencias=tendencias[:5],
        fecha=hoy,
    )


@asistente_bp.route("/decision/radar")
@login_required
def radar_estrategico():
    if not _puede_ver_asistente():
        flash("No tienes permisos para ver el radar estrategico.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    hoy = date.today()
    contexto_decision = _centro_decision_contexto(conn, empresa_id, hoy.replace(day=1), hoy + timedelta(days=30))
    fuentes = _row_dicts(_fuentes_conocimiento(conn, empresa_id, limite=120))
    memoria = _row_dicts(_memoria(conn, empresa_id, limite=100))
    escenarios = _escenarios_decision(conn, empresa_id, limite=30)
    radar = generar_radar_estrategico({
        "fuentes": fuentes,
        "memoria": memoria,
        "escenarios": escenarios,
        "metricas": contexto_decision["metricas"],
    })
    return render_template(
        "asistente/radar_estrategico.html",
        radar=radar,
        oportunidades=radar["oportunidades"],
        resumen=radar["resumen"],
    )


@asistente_bp.route("/decision/tendencias")
@login_required
def tendencias_estrategicas():
    if not _puede_ver_asistente():
        flash("No tienes permisos para ver tendencias estrategicas.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    fuentes = _row_dicts(_fuentes_conocimiento(conn, empresa_id, limite=200))
    tendencias = detectar_tendencias_sectoriales(fuentes)
    return render_template(
        "asistente/tendencias_estrategicas.html",
        tendencias=tendencias,
        fuentes_total=len(fuentes),
    )


@asistente_bp.route("/decision/competitividad")
@login_required
def mapa_competitivo():
    if not _puede_ver_asistente():
        flash("No tienes permisos para ver el mapa competitivo.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    hoy = date.today()
    contexto_decision = _centro_decision_contexto(conn, empresa_id, hoy.replace(day=1), hoy + timedelta(days=30))
    fuentes = _row_dicts(_fuentes_conocimiento(conn, empresa_id, limite=250))
    escenarios = _escenarios_decision(conn, empresa_id, limite=30)
    mapa = analizar_posicion_competitiva({
        "fuentes": fuentes,
        "escenarios": escenarios,
        "metricas": contexto_decision["metricas"],
    })
    return render_template(
        "asistente/mapa_competitivo.html",
        mapa=mapa,
    )


@asistente_bp.route("/decision/tendencias/guardar", methods=["POST"])
@login_required
def guardar_tendencia_estrategica():
    if not _puede_ver_asistente():
        flash("No tienes permisos para guardar tendencias.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    tema = (request.form.get("tema") or "tendencia").strip()
    titulo = (request.form.get("titulo") or "Tendencia sectorial").strip()
    prioridad = (request.form.get("prioridad") or "media").strip().lower()
    if prioridad not in {"alta", "media", "baja", "info", "critica"}:
        prioridad = "media"
    lectura = (request.form.get("lectura") or "").strip()
    accion = (request.form.get("accion") or "").strip()
    datos = {
        "tema": tema,
        "senales": _to_float(request.form.get("senales"), 0),
        "confianza_pct": _to_float(request.form.get("confianza_pct"), 0),
        "origen": "tendencias_estrategicas",
    }
    conn.execute(
        """
        INSERT INTO asistente_recomendaciones (
            empresa_id, modulo, categoria, prioridad, titulo, detalle,
            datos_usados, accion_propuesta, estado, origen
        ) VALUES (?, 'Tendencias Estrategicas', ?, ?, ?, ?, ?, ?, 'pendiente', 'tendencias_estrategicas')
        """,
        (
            empresa_id,
            tema,
            prioridad,
            titulo,
            lectura,
            json.dumps(datos, ensure_ascii=False),
            accion or "Contrastar tendencia con datos internos y crear escenario antes de decidir.",
        ),
    )
    conn.commit()
    flash("Tendencia guardada para seguimiento.", "success")
    return redirect(url_for("asistente.tendencias_estrategicas"))


@asistente_bp.route("/decision/radar/guardar", methods=["POST"])
@login_required
def guardar_oportunidad_radar():
    if not _puede_ver_asistente():
        flash("No tienes permisos para guardar oportunidades del radar.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    area = (request.form.get("area") or "Direccion").strip()
    prioridad = (request.form.get("prioridad") or "media").strip().lower()
    if prioridad not in {"alta", "media", "baja", "info", "critica"}:
        prioridad = "media"
    titulo = (request.form.get("titulo") or "Oportunidad del radar").strip()
    lectura = (request.form.get("lectura") or "").strip()
    accion = (request.form.get("accion") or "").strip()
    datos = {
        "area": area,
        "confianza_pct": _to_float(request.form.get("confianza_pct"), 0),
        "cruce_datos": {
            "nivel": request.form.get("cruce_nivel") or "",
            "idea": request.form.get("cruce_idea") or "",
            "dato_interno": request.form.get("cruce_dato_interno") or "",
            "senal_externa": request.form.get("cruce_senal_externa") or "",
            "escenario_sugerido": request.form.get("cruce_escenario_sugerido") or "",
            "riesgo_inventar": request.form.get("cruce_riesgo_inventar") or "",
        },
        "plan_piloto": {
            "hipotesis": request.form.get("hipotesis") or "",
            "prueba": request.form.get("prueba") or "",
            "metrica_exito": request.form.get("metrica_exito") or "",
            "limite_parada": request.form.get("limite_parada") or "",
            "duracion": request.form.get("duracion") or "",
            "responsable": request.form.get("responsable") or "",
        },
    }
    detalle = lectura
    plan = datos["plan_piloto"]
    cruce = datos["cruce_datos"]
    if any(cruce.values()):
        detalle = (
            f"{detalle}\n\nCruce de datos: nivel {cruce['nivel']} | Idea: {cruce['idea']} | "
            f"Interno: {cruce['dato_interno']} | Externo: {cruce['senal_externa']} | "
            f"Escenario: {cruce['escenario_sugerido']} | Riesgo inventar: {cruce['riesgo_inventar']}"
        ).strip()
    if any(plan.values()):
        detalle = (
            f"{lectura}\n\nPlan piloto: {plan['hipotesis']} | Prueba: {plan['prueba']} | "
            f"Metrica: {plan['metrica_exito']} | Limite: {plan['limite_parada']} | "
            f"Duracion: {plan['duracion']} | Responsable: {plan['responsable']}"
        ).strip()
    conn.execute(
        """
        INSERT INTO asistente_recomendaciones (
            empresa_id, modulo, categoria, prioridad, titulo, detalle,
            datos_usados, accion_propuesta, estado, origen
        ) VALUES (?, 'Radar Estrategico', ?, ?, ?, ?, ?, ?, 'pendiente', 'radar_estrategico')
        """,
        (
            empresa_id,
            area,
            prioridad,
            titulo,
            detalle,
            json.dumps(datos, ensure_ascii=False),
            accion or "Revisar oportunidad del radar y convertirla en piloto controlado.",
        ),
    )
    conn.commit()
    flash("Oportunidad del radar guardada para seguimiento.", "success")
    return redirect(url_for("asistente.radar_estrategico"))


@asistente_bp.route("/decision/guardar", methods=["POST"])
@login_required
def guardar_centro_decision():
    if not _puede_ver_asistente():
        flash("No tienes permisos para guardar decisiones.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    hoy = date.today()
    fecha_desde = _parse_date(request.form.get("fecha_desde"), hoy.replace(day=1)) or hoy.replace(day=1)
    fecha_hasta = _parse_date(request.form.get("fecha_hasta"), hoy + timedelta(days=30)) or (hoy + timedelta(days=30))
    if fecha_hasta < fecha_desde:
        fecha_hasta = fecha_desde

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    contexto = _centro_decision_contexto(conn, empresa_id, fecha_desde, fecha_hasta)
    for rec in contexto["recomendaciones"]:
        _guardar_recomendacion_decision(conn, empresa_id, rec)
    conn.commit()
    flash(f"Centro de decision ha guardado {len(contexto['recomendaciones'])} propuestas para seguimiento.", "success")
    return redirect(url_for("asistente.centro_decision", fecha_desde=fecha_desde.isoformat(), fecha_hasta=fecha_hasta.isoformat()))


@asistente_bp.route("/decision/simulador", methods=["GET", "POST"])
@login_required
def simulador_decision():
    if not _puede_ver_asistente():
        flash("No tienes permisos para usar el simulador de decisiones.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    hoy = date.today()
    base_centro = _base_simulador_centro(conn, empresa_id)
    centro_id = base_centro.get("centro_id")
    coste_laboral_ops = _coste_laboral_ops_periodo(conn, empresa_id, hoy.replace(day=1), hoy)
    coste_personal_default = base_centro["coste_personal_mes"] or coste_laboral_ops["mensual"] or 55000
    centro_nombre = (base_centro.get("centro") or {}).get("nombre") or "Centro activo"

    resultado_actual = None
    form_data = {
        "nombre": f"Decision rentabilidad - {centro_nombre}",
        "tipo": "hotel",
        "decision_tipo": "oferta",
        "fase_negocio": "operativo",
        "zona_mercado": "rural",
        "categoria_establecimiento": "3_estrellas",
        "calidad_datos_pct": base_centro["calidad_datos_pct"],
        "habitaciones": base_centro["habitaciones"],
        "ocupacion_pct": base_centro["ocupacion_pct"],
        "adr": base_centro["adr"],
        "dias": base_centro["dias"],
        "pax_medio": base_centro["pax_medio"],
        "coste_variable_pax_dia": base_centro["coste_variable_pax_dia"],
        "coste_fijo_mes": base_centro["coste_fijo_mes"],
        "coste_personal_mes": coste_personal_default,
        "alcance_costes_pct": 100,
        "coste_personal_origen": base_centro["coste_personal_origen"] or coste_laboral_ops["origen"],
        "fuente_datos": "mixto",
        "comision_pct": base_centro.get("comision_pct", 12),
        "inversion": 0,
        "satisfaccion_objetivo": 85,
        "rotacion_riesgo_pct": 15,
        "empleados_no_presentan": 0,
        "retrasos_pct": 0,
        "cambios_equipo": 0,
        "aceptacion_sistema_pct": 90,
        "cobertura_ops_pct": 70,
        "notas": "",
    }

    if request.method == "POST":
        datos = _datos_escenario_desde_form(request.form)
        datos["centro_id"] = centro_id
        resultado_actual = _calcular_escenario_decision(datos)
        form_data.update(datos)
        accion = request.form.get("accion") or "calcular"
        if accion == "guardar":
            bloqueo_gestion = _requiere_gestion_simulador_decision()
            if bloqueo_gestion:
                return bloqueo_gestion
            _guardar_escenario_decision(
                conn,
                empresa_id,
                datos,
                resultado_actual,
                session.get("user_nombre") or session.get("username") or "usuario",
                centro_id=centro_id,
            )
            conn.commit()
            flash("Escenario guardado para comparar decisiones.", "success")
            return redirect(url_for("asistente.simulador_decision"))

    filtro_tipo = (request.args.get("tipo") or "").strip()
    tipos_validos = {valor for valor, _ in MODELOS_NEGOCIO_HOSTELERIA}
    if filtro_tipo not in tipos_validos:
        filtro_tipo = ""
    escenarios = _escenarios_decision(conn, empresa_id, tipo=filtro_tipo or None, centro_id=centro_id)
    lectura_cartera = _analizar_cartera_escenarios(escenarios)
    escenarios_rapidos = _escenarios_rapidos_centro(base_centro)
    return render_template(
        "asistente/simulador_decision.html",
        base_centro=base_centro,
        centro_activo=base_centro.get("centro"),
        form_data=form_data,
        resultado_actual=resultado_actual,
        escenarios=escenarios,
        escenarios_rapidos=escenarios_rapidos,
        lectura_cartera=lectura_cartera,
        resumen_escenarios=_resumen_escenarios_decision(conn, empresa_id, centro_id=centro_id),
        mostrar_laboratorio=(int(empresa_id or 0) == 1),
        filtro_tipo=filtro_tipo,
        modelos_negocio=MODELOS_NEGOCIO_HOSTELERIA,
        fases_negocio=FASES_NEGOCIO_ESCENARIO,
        zonas_mercado=ZONAS_MERCADO,
        categorias_establecimiento=CATEGORIAS_ESTABLECIMIENTO,
        fuentes_datos=FUENTES_DATOS_ESCENARIO,
        puede_gestionar_simulador=_puede_gestionar_simulador_decision(),
    )


@asistente_bp.route("/decision/simulador/limpiar", methods=["POST"])
@login_required
def limpiar_escenarios_decision():
    if not _puede_ver_asistente():
        flash("No tienes permisos para limpiar escenarios.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo
    bloqueo_gestion = _requiere_gestion_simulador_decision()
    if bloqueo_gestion:
        return bloqueo_gestion

    confirmacion = (request.form.get("confirmacion") or "").strip().upper()
    if confirmacion != "LIMPIAR":
        flash("Para limpiar escenarios escribe LIMPIAR en la confirmacion.", "warning")
        return redirect(url_for("asistente.simulador_decision"))

    accion = (request.form.get("accion_limpieza") or "").strip()
    modelo = (request.form.get("modelo") or "").strip()
    escenario_id = request.form.get("escenario_id")
    tipos_validos = {valor for valor, _ in MODELOS_NEGOCIO_HOSTELERIA}

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    centro_id = centro_activo_id()
    filtro_centro = ""
    params_centro = []
    if centro_id is not None and "centro_id" in _columns(conn, "asistente_escenarios_decision"):
        filtro_centro = " AND COALESCE(centro_id, 0) = COALESCE(?, 0)"
        params_centro.append(centro_id)
    borrados = 0
    if accion == "escenario" and escenario_id:
        cur = conn.execute(
            f"DELETE FROM asistente_escenarios_decision WHERE empresa_id = ? AND id = ?{filtro_centro}",
            (empresa_id, escenario_id, *params_centro),
        )
        borrados = cur.rowcount
    elif accion == "simulados":
        cur = conn.execute(
            f"""
            DELETE FROM asistente_escenarios_decision
            WHERE empresa_id = ?
              {filtro_centro}
              AND COALESCE(fuente_datos, 'simulado') = 'simulado'
            """,
            (empresa_id, *params_centro),
        )
        borrados = cur.rowcount
    elif accion == "modelo" and modelo in tipos_validos:
        cur = conn.execute(
            f"DELETE FROM asistente_escenarios_decision WHERE empresa_id = ? AND tipo = ?{filtro_centro}",
            (empresa_id, modelo, *params_centro),
        )
        borrados = cur.rowcount
    elif accion == "todos":
        cur = conn.execute(
            f"DELETE FROM asistente_escenarios_decision WHERE empresa_id = ?{filtro_centro}",
            (empresa_id, *params_centro),
        )
        borrados = cur.rowcount
    else:
        flash("Selecciona una limpieza valida.", "warning")
        return redirect(url_for("asistente.simulador_decision"))

    conn.commit()
    flash(f"Escenarios limpiados: {borrados}.", "success")
    return redirect(url_for("asistente.simulador_decision"))


@asistente_bp.route("/decision/simulador/demo", methods=["POST"])
@login_required
def crear_escenarios_demo_decision():
    if not _puede_ver_asistente():
        flash("No tienes permisos para crear escenarios de simulacion.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo
    bloqueo_gestion = _requiere_gestion_simulador_decision()
    if bloqueo_gestion:
        return bloqueo_gestion

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    base_centro = _base_simulador_centro(conn, empresa_id)
    centro_id = base_centro.get("centro_id")
    hoy = date.today()
    coste_laboral_ops = _coste_laboral_ops_periodo(conn, empresa_id, hoy.replace(day=1), hoy)
    usuario = session.get("user_nombre") or session.get("username") or "usuario"
    creados = 0
    for datos in _escenarios_demo_decision(coste_laboral_ops["mensual"]):
        datos["centro_id"] = centro_id
        resultado = _calcular_escenario_decision(datos)
        _guardar_escenario_decision(conn, empresa_id, datos, resultado, usuario, centro_id=centro_id)
        creados += 1
    conn.commit()
    flash(f"Se han creado {creados} escenarios de prueba para comparar hoteles y decisiones.", "success")
    return redirect(url_for("asistente.simulador_decision"))


@asistente_bp.route("/decision/simulador/inversion-pirineo", methods=["POST"])
@login_required
def crear_escenarios_inversion_pirineo():
    if not _puede_ver_asistente():
        flash("No tienes permisos para crear escenarios de inversion.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo
    bloqueo_gestion = _requiere_gestion_simulador_decision()
    if bloqueo_gestion:
        return bloqueo_gestion

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    base_centro = _base_simulador_centro(conn, empresa_id)
    centro_id = base_centro.get("centro_id")
    hoy = date.today()
    coste_laboral_ops = _coste_laboral_ops_periodo(conn, empresa_id, hoy.replace(day=1), hoy)
    usuario = session.get("user_nombre") or session.get("username") or "usuario"
    creados = 0
    for datos in _escenarios_inversion_pirineo(coste_laboral_ops["mensual"]):
        datos["centro_id"] = centro_id
        resultado = _calcular_escenario_decision(datos)
        _guardar_escenario_decision(conn, empresa_id, datos, resultado, usuario, centro_id=centro_id)
        creados += 1
    conn.commit()
    flash(f"Se han creado {creados} hipotesis de inversion para hotel en Pirineo.", "success")
    return redirect(url_for("asistente.simulador_decision", tipo="hotel"))


@asistente_bp.route("/decision/temporada", methods=["GET", "POST"])
@login_required
def vision_temporada():
    if not _puede_ver_asistente():
        flash("No tienes permisos para ver la vision de temporada.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    hoy = date.today()
    temporada = request.args.get("temporada") or request.form.get("temporada") or "verano"
    if temporada == "invierno":
        fecha_desde_default = date(hoy.year, 11, 1) if hoy.month < 11 else date(hoy.year, hoy.month, 1)
        fecha_hasta_default = date(fecha_desde_default.year + 1, 3, 31)
    else:
        fecha_desde_default = date(hoy.year, 6, 1)
        fecha_hasta_default = date(hoy.year, 9, 30)
        if hoy > fecha_hasta_default:
            fecha_desde_default = date(hoy.year + 1, 6, 1)
            fecha_hasta_default = date(hoy.year + 1, 9, 30)

    fecha_desde = _parse_date(request.args.get("fecha_desde") or request.form.get("fecha_desde"), fecha_desde_default) or fecha_desde_default
    fecha_hasta = _parse_date(request.args.get("fecha_hasta") or request.form.get("fecha_hasta"), fecha_hasta_default) or fecha_hasta_default
    if fecha_hasta < fecha_desde:
        fecha_hasta = fecha_desde

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)

    if request.method == "POST" and request.form.get("accion") == "indicador":
        indicador = (request.form.get("indicador") or "").strip()
        if indicador:
            conn.execute(
                """
                INSERT INTO asistente_indicadores_externos (
                    empresa_id, periodo, mercado, indicador, valor, unidad,
                    fuente, lectura, impacto, usuario
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    empresa_id,
                    request.form.get("periodo") or f"{fecha_desde.isoformat()} / {fecha_hasta.isoformat()}",
                    request.form.get("mercado") or "",
                    indicador,
                    _to_float(request.form.get("valor"), 0.0),
                    request.form.get("unidad") or "",
                    request.form.get("fuente") or "",
                    request.form.get("lectura") or "",
                    request.form.get("impacto") or "medio",
                    session.get("user_nombre") or session.get("username") or "usuario",
                ),
            )
            conn.commit()
            flash("Indicador externo guardado para aprendizaje de temporada.", "success")
        return redirect(url_for(
            "asistente.vision_temporada",
            temporada=temporada,
            fecha_desde=fecha_desde.isoformat(),
            fecha_hasta=fecha_hasta.isoformat(),
        ))

    metricas = _temporada_metricas(conn, empresa_id, fecha_desde, fecha_hasta)
    indicadores = _indicadores_externos_temporada(conn, empresa_id)
    recomendaciones = _temporada_recomendaciones(metricas, indicadores)
    return render_template(
        "asistente/temporada.html",
        temporada=temporada,
        fecha_desde=fecha_desde.isoformat(),
        fecha_hasta=fecha_hasta.isoformat(),
        metricas=metricas,
        meses=metricas["meses"],
        indicadores=indicadores,
        recomendaciones=recomendaciones,
    )


@asistente_bp.route("/generar", methods=["POST"])
@login_required
def guardar_recomendaciones():
    if not _puede_ver_asistente():
        flash("No tienes permisos para guardar recomendaciones.", "warning")
        return redirect(url_for("home"))

    hoy = date.today()
    fecha_desde = _parse_date(request.form.get("fecha_desde"), hoy) or hoy
    fecha_hasta = _parse_date(request.form.get("fecha_hasta"), fecha_desde + timedelta(days=7)) or (fecha_desde + timedelta(days=7))
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    recomendaciones = generar_recomendaciones(conn, empresa_id, fecha_desde, fecha_hasta)
    for rec in recomendaciones:
        conn.execute(
            """
            INSERT INTO asistente_recomendaciones (
                empresa_id, modulo, categoria, prioridad, titulo, detalle,
                datos_usados, accion_propuesta, estado, origen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', 'reglas_base')
            """,
            (
                empresa_id,
                rec["modulo"],
                rec["categoria"],
                rec["prioridad"],
                rec["titulo"],
                rec["detalle"],
                json.dumps(rec["datos_usados"], ensure_ascii=False),
                rec["accion_propuesta"],
            ),
        )
    conn.commit()
    flash(f"Asistente El Raco ha guardado {len(recomendaciones)} recomendaciones para seguimiento.", "success")
    return redirect(url_for("asistente.dashboard", fecha_desde=fecha_desde.isoformat(), fecha_hasta=fecha_hasta.isoformat()))


@asistente_bp.route("/aprendizaje")
@login_required
def aprendizaje():
    if not _puede_ver_asistente():
        flash("No tienes permisos para ver el aprendizaje IA.", "warning")
        return redirect(url_for("home"))

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    patrones = _patrones_aprendizaje(conn, empresa_id)
    return render_template(
        "asistente/aprendizaje.html",
        resumen=_resumen_aprendizaje(conn, empresa_id),
        patrones=patrones,
        memoria=_memoria(conn, empresa_id),
        lecciones_decision=_lecciones_decision(conn, empresa_id),
        fuentes_conocimiento=_fuentes_conocimiento(conn, empresa_id),
        fuentes_externas=_fuentes_externas(conn, empresa_id),
        resumen_fuentes_conocimiento=_resumen_fuentes_conocimiento(conn, empresa_id),
        tipos_fuente_conocimiento=TIPOS_FUENTE_CONOCIMIENTO,
        ambitos_hosteleria=AMBITOS_HOSTELERIA,
        areas_conocimiento=AREAS_CONOCIMIENTO,
        eventos=_eventos_aprendizaje(conn, empresa_id),
        lagunas=_lagunas_datos_ia(conn),
    )


@asistente_bp.route("/aprendizaje/procesar-fuentes", methods=["POST"])
@login_required
def procesar_fuentes_conocimiento():
    if not _puede_ver_asistente():
        flash("No tienes permisos para procesar fuentes IA.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    resultado = _procesar_fuentes_conocimiento_pendientes(conn, empresa_id)
    conn.commit()
    conn.close()
    flash(
        f"Fuentes procesadas: {resultado['procesadas']}. Pendientes: {resultado['pendientes']}.",
        "success" if resultado["procesadas"] else "info",
    )
    return redirect(url_for("asistente.aprendizaje"))


@asistente_bp.route("/cocina")
@login_required
def cocina_ia():
    if not _puede_ver_asistente():
        flash("No tienes permisos para ver IA Cocina.", "warning")
        return redirect(url_for("home"))

    hoy = date.today()
    fecha_desde = _parse_date(request.args.get("fecha_desde"), hoy) or hoy
    fecha_hasta = _parse_date(request.args.get("fecha_hasta"), fecha_desde + timedelta(days=14)) or (fecha_desde + timedelta(days=14))
    if fecha_hasta < fecha_desde:
        fecha_hasta = fecha_desde
    objetivo = request.args.get("objetivo") or "general"
    ingrediente = request.args.get("ingrediente") or ""

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    recomendaciones = _analisis_cocina_ia(conn, empresa_id, fecha_desde, fecha_hasta, objetivo, ingrediente)
    resumen = defaultdict(int)
    for rec in recomendaciones:
        resumen[rec["prioridad"]] += 1

    return render_template(
        "asistente/cocina.html",
        recomendaciones=recomendaciones,
        resumen=dict(resumen),
        objetivos=OBJETIVOS_COCINA_IA,
        objetivo=objetivo,
        ingrediente=ingrediente,
        fecha_desde=fecha_desde.isoformat(),
        fecha_hasta=fecha_hasta.isoformat(),
    )


@asistente_bp.route("/cocina/guardar", methods=["POST"])
@login_required
def guardar_cocina_ia():
    if not _puede_ver_asistente():
        flash("No tienes permisos para guardar recomendaciones de IA Cocina.", "warning")
        return redirect(url_for("home"))

    hoy = date.today()
    fecha_desde = _parse_date(request.form.get("fecha_desde"), hoy) or hoy
    fecha_hasta = _parse_date(request.form.get("fecha_hasta"), fecha_desde + timedelta(days=14)) or (fecha_desde + timedelta(days=14))
    if fecha_hasta < fecha_desde:
        fecha_hasta = fecha_desde
    objetivo = request.form.get("objetivo") or "general"
    ingrediente = request.form.get("ingrediente") or ""

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    recomendaciones = _analisis_cocina_ia(conn, empresa_id, fecha_desde, fecha_hasta, objetivo, ingrediente)
    _guardar_recomendaciones_lista(conn, empresa_id, recomendaciones, "ia_cocina_reglas")
    conn.commit()
    flash(f"IA Cocina ha guardado {len(recomendaciones)} propuestas para seguimiento.", "success")
    return redirect(url_for(
        "asistente.cocina_ia",
        fecha_desde=fecha_desde.isoformat(),
        fecha_hasta=fecha_hasta.isoformat(),
        objetivo=objetivo,
        ingrediente=ingrediente,
    ))


@asistente_bp.route("/cocina/investigar", methods=["GET", "POST"])
@login_required
def cocina_investigar():
    if not _puede_ver_asistente():
        flash("No tienes permisos para investigar con IA Cocina.", "warning")
        return redirect(url_for("home"))

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)

    if request.method == "POST":
        objetivo = request.form.get("objetivo") or "general"
        ingrediente = (request.form.get("ingrediente") or "").strip()
        perfil_cliente = (request.form.get("perfil_cliente") or "").strip()
        restricciones = (request.form.get("restricciones") or "").strip()
        idioma = request.form.get("idioma") or "es"
        consulta = (request.form.get("consulta") or "").strip()
        fuente_titulo = (request.form.get("fuente_titulo") or "").strip()
        fuente_url = (request.form.get("fuente_url") or "").strip()
        resumen = (request.form.get("resumen") or "").strip()
        propuesta = (request.form.get("propuesta") or "").strip()
        riesgos = (request.form.get("riesgos") or "").strip()
        accion = request.form.get("accion") or "guardar"

        cursor = conn.execute(
            """
            INSERT INTO asistente_investigaciones (
                empresa_id, modulo, tipo, objetivo, consulta, perfil_cliente,
                restricciones, idioma, fuente_titulo, fuente_url, resumen,
                propuesta, riesgos, estado, usuario
            ) VALUES (?, 'IA Cocina', 'investigacion_externa', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendiente_revision', ?)
            """,
            (
                empresa_id,
                objetivo,
                consulta,
                perfil_cliente,
                restricciones,
                idioma,
                fuente_titulo,
                fuente_url,
                resumen,
                propuesta,
                riesgos,
                session.get("user_nombre") or session.get("username") or "usuario",
            ),
        )
        investigacion_id = cursor.lastrowid
        if accion == "guardar_recomendacion":
            _guardar_investigacion_como_recomendacion(conn, empresa_id, investigacion_id)
            flash("Investigacion guardada y convertida en propuesta para revision.", "success")
        else:
            flash("Investigacion externa guardada para revision.", "success")
        conn.commit()
        return redirect(url_for(
            "asistente.cocina_investigar",
            objetivo=objetivo,
            ingrediente=ingrediente,
            perfil_cliente=perfil_cliente,
            restricciones=restricciones,
            idioma=idioma,
        ))

    objetivo = request.args.get("objetivo") or "general"
    ingrediente = request.args.get("ingrediente") or ""
    perfil_cliente = request.args.get("perfil_cliente") or ""
    restricciones = request.args.get("restricciones") or ""
    idioma = request.args.get("idioma") or "es"
    busquedas = _busquedas_externas_cocina(objetivo, ingrediente, perfil_cliente, restricciones, idioma)
    consulta_principal = busquedas[0]["consulta"] if busquedas else ""
    return render_template(
        "asistente/cocina_investigar.html",
        objetivos=OBJETIVOS_COCINA_IA,
        objetivo=objetivo,
        ingrediente=ingrediente,
        perfil_cliente=perfil_cliente,
        restricciones=restricciones,
        idioma=idioma,
        busquedas=busquedas,
        consulta_principal=consulta_principal,
        investigaciones=_investigaciones_recientes(conn, empresa_id),
    )


@asistente_bp.route("/gestion")
@login_required
def gestion_360():
    if not _puede_ver_asistente():
        flash("No tienes permisos para ver Gestion 360.", "warning")
        return redirect(url_for("home"))

    hoy = date.today()
    fecha_desde = _parse_date(request.args.get("fecha_desde"), hoy.replace(day=1)) or hoy.replace(day=1)
    fecha_hasta = _parse_date(request.args.get("fecha_hasta"), hoy) or hoy
    if fecha_hasta < fecha_desde:
        fecha_hasta = fecha_desde

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    metricas = _metricas_gestion_360(conn, empresa_id, fecha_desde, fecha_hasta)
    recomendaciones = _analisis_gestion_360(conn, empresa_id, fecha_desde, fecha_hasta)
    desglose_costes = [
        {"nombre": "Gastos contables", "valor": metricas["gastos_contabilidad"]},
        {"nombre": "Personal", "valor": metricas["coste_personal"]},
        {"nombre": "Produccion", "valor": metricas["coste_produccion"]},
        {"nombre": "Animacion propia", "valor": metricas["coste_animacion_propia"]},
        {"nombre": "Animacion subcontratada", "valor": metricas["coste_animacion_subcontratada"]},
        {"nombre": "Animacion mixta", "valor": metricas["coste_animacion_mixta"]},
        {"nombre": "Mantenimiento", "valor": metricas["coste_mantenimiento"]},
        {"nombre": "Mermas", "valor": metricas["coste_mermas"]},
        {"nombre": "Compensaciones cliente", "valor": metricas["compensaciones_cliente"]},
        {"nombre": "Compras no asentadas", "valor": metricas["compras_periodo"] if metricas["gastos_contabilidad"] <= 0 else 0},
    ]
    desglose_ingresos = [
        {"nombre": "Contabilidad", "valor": metricas["ingresos_contabilidad"]},
        {"nombre": "Facturas recepcion", "valor": metricas["ingresos_recepcion"]},
        {"nombre": "TPV", "valor": metricas["ingresos_tpv"]},
        {"nombre": "Reservas estimadas", "valor": metricas["reservas_estimadas"]},
        {"nombre": "Animacion directa", "valor": metricas["ingreso_animacion_directo"]},
    ]
    return render_template(
        "asistente/gestion.html",
        metricas=metricas,
        recomendaciones=recomendaciones,
        desglose_costes=desglose_costes,
        desglose_ingresos=desglose_ingresos,
        fecha_desde=fecha_desde.isoformat(),
        fecha_hasta=fecha_hasta.isoformat(),
    )


@asistente_bp.route("/oportunidades")
@login_required
def oportunidades():
    if not _puede_ver_asistente():
        flash("No tienes permisos para ver oportunidades IA.", "warning")
        return redirect(url_for("home"))

    hoy = date.today()
    fecha_desde = _parse_date(request.args.get("fecha_desde"), hoy) or hoy
    fecha_hasta = _parse_date(request.args.get("fecha_hasta"), hoy + timedelta(days=90)) or (hoy + timedelta(days=90))
    if fecha_hasta < fecha_desde:
        fecha_hasta = fecha_desde

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    metricas = _metricas_oportunidades_establecimiento(conn, empresa_id, fecha_desde, fecha_hasta)
    recomendaciones = _analisis_oportunidades_establecimiento(conn, empresa_id, fecha_desde, fecha_hasta)
    conn.close()
    return render_template(
        "asistente/oportunidades.html",
        metricas=metricas,
        recomendaciones=recomendaciones,
        fecha_desde=fecha_desde.isoformat(),
        fecha_hasta=fecha_hasta.isoformat(),
    )


@asistente_bp.route("/diario")
@login_required
def asesor_diario():
    if not _puede_ver_asistente():
        flash("No tienes permisos para ver el asesor diario.", "warning")
        return redirect(url_for("home"))
    hoy = date.today()
    fecha_desde = _parse_date(request.args.get("fecha_desde"), hoy) or hoy
    fecha_hasta = _parse_date(request.args.get("fecha_hasta"), hoy + timedelta(days=7)) or (hoy + timedelta(days=7))
    if fecha_hasta < fecha_desde:
        fecha_hasta = fecha_desde
    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    _ensure_asistente_schema(conn)
    grupos = _asesor_diario_contexto(conn, empresa_id, fecha_desde, fecha_hasta)
    contexto_operativo = construir_contexto_operativo(conn, empresa_id, centro_id)
    conn.close()
    return render_template(
        "asistente/diario.html",
        grupos=grupos,
        contexto_operativo=contexto_operativo,
        fecha_desde=fecha_desde.isoformat(),
        fecha_hasta=fecha_hasta.isoformat(),
    )


@asistente_bp.route("/documentos", methods=["GET", "POST"])
@login_required
def documentos_ia():
    if not _puede_ver_asistente():
        flash("No tienes permisos para leer documentos con IA.", "warning")
        return redirect(url_for("home"))

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    if request.method == "POST":
        archivo = request.files.get("archivo")
        tipo_documento = request.form.get("tipo_documento") or "auto"
        modulo_destino = request.form.get("modulo_destino") or "auto"
        titulo = (request.form.get("titulo") or "").strip()
        if not archivo or not archivo.filename:
            flash("Selecciona un documento.", "warning")
            conn.close()
            return redirect(url_for("asistente.documentos_ia"))
        extension = _extension_archivo(archivo.filename)
        if extension not in EXTENSIONES_DOCUMENTO_IA:
            flash("Formato no soportado para lectura IA.", "warning")
            conn.close()
            return redirect(url_for("asistente.documentos_ia"))

        nombre_seguro = secure_filename(archivo.filename)
        sello = datetime.now().strftime("%Y%m%d%H%M%S%f")
        nombre_final = f"{sello}_{nombre_seguro}"
        ruta = os.path.join(_upload_dir_documentos_ia(), nombre_final)
        archivo.save(ruta)
        texto, aviso = _extraer_texto_documento(ruta, extension)
        datos = _analizar_texto_documento(texto, tipo_documento, modulo_destino)
        estado = "pendiente_revision" if texto.strip() else "pendiente_ocr"
        propuesta = datos["propuesta"]
        if aviso:
            propuesta = f"{propuesta} Aviso: {aviso}"
        conn.execute(
            """
            INSERT INTO asistente_documentos_lectura (
                empresa_id, modulo_destino, tipo_documento, titulo, archivo_nombre,
                archivo_path, mime, estado, texto_extraido, datos_detectados,
                propuesta, usuario
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                empresa_id,
                datos["modulo_detectado"] if modulo_destino == "auto" else modulo_destino,
                datos["tipo_detectado"] if tipo_documento == "auto" else tipo_documento,
                titulo or nombre_seguro,
                nombre_seguro,
                ruta,
                archivo.mimetype or "",
                estado,
                texto[:60000],
                json.dumps(datos, ensure_ascii=False),
                propuesta,
                session.get("user_nombre") or session.get("username") or "usuario",
            ),
        )
        conn.commit()
        flash("Documento cargado. Queda pendiente de revision humana antes de integrarlo.", "success" if texto else "warning")
        conn.close()
        return redirect(url_for("asistente.documentos_ia"))

    estado = request.args.get("estado") or ""
    modulo = request.args.get("modulo") or ""
    query = "SELECT * FROM asistente_documentos_lectura WHERE empresa_id = ?"
    params = [empresa_id]
    if estado:
        query += " AND estado = ?"
        params.append(estado)
    if modulo:
        query += " AND modulo_destino = ?"
        params.append(modulo)
    query += " ORDER BY fecha_creacion DESC LIMIT 100"
    documentos = conn.execute(query, params).fetchall()
    resumen_rows = conn.execute(
        """
        SELECT estado, COUNT(*) AS total
        FROM asistente_documentos_lectura
        WHERE empresa_id = ?
        GROUP BY estado
        """,
        (empresa_id,),
    ).fetchall()
    conn.close()
    return render_template(
        "asistente/documentos.html",
        documentos=documentos,
        resumen={row["estado"]: row["total"] for row in resumen_rows},
        tipos_documento=TIPOS_DOCUMENTO_IA,
        modulos_documento=MODULOS_DOCUMENTO_IA,
        filtros={"estado": estado, "modulo": modulo},
    )


@asistente_bp.route("/documentos/<int:documento_id>/estado", methods=["POST"])
@login_required
def documento_estado(documento_id):
    if not _puede_ver_asistente():
        flash("No tienes permisos para actualizar documentos IA.", "warning")
        return redirect(url_for("home"))
    estado = request.form.get("estado") or "pendiente_revision"
    if estado not in {"pendiente_revision", "validado", "descartado", "pendiente_ocr"}:
        estado = "pendiente_revision"
    conn = get_db()
    _ensure_asistente_schema(conn)
    conn.execute(
        """
        UPDATE asistente_documentos_lectura
        SET estado = ?, validado_por = ?, fecha_validacion = CURRENT_TIMESTAMP,
            observaciones = ?
        WHERE id = ? AND empresa_id = ?
        """,
        (
            estado,
            session.get("user_nombre") or session.get("username") or "usuario",
            request.form.get("observaciones") or "",
            documento_id,
            _empresa_id(),
        ),
    )
    conn.commit()
    conn.close()
    flash("Documento actualizado.", "success")
    return redirect(url_for("asistente.documentos_ia"))


@asistente_bp.route("/memoria", methods=["POST"])
@login_required
def guardar_memoria_manual():
    if not _puede_ver_asistente():
        flash("No tienes permisos para guardar memoria IA.", "warning")
        return redirect(url_for("home"))

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    tipo = (request.form.get("tipo") or "nota_operativa").strip()
    modulo = (request.form.get("modulo") or "General").strip()
    clave = (request.form.get("clave") or "").strip()
    valor = (request.form.get("valor") or "").strip()
    detalle = (request.form.get("detalle") or "").strip()
    if not clave:
        flash("La memoria necesita una clave o titulo.", "warning")
        return redirect(url_for("asistente.aprendizaje"))
    _guardar_memoria(
        conn,
        empresa_id,
        tipo,
        modulo,
        clave,
        valor,
        detalle,
        confianza=0.65,
        origen=f"manual:{session.get('user_nombre') or session.get('username') or 'usuario'}",
    )
    conn.commit()
    flash("Memoria guardada para que el asistente la tenga en cuenta.", "success")
    return redirect(url_for("asistente.aprendizaje"))


@asistente_bp.route("/conocimiento/fuente", methods=["POST"])
@login_required
def guardar_fuente_conocimiento():
    if not _puede_ver_asistente():
        flash("No tienes permisos para guardar fuentes de conocimiento.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    fuente = evaluar_fuente_conocimiento(request.form)
    if not fuente["titulo"]:
        flash("La fuente necesita un titulo.", "warning")
        return redirect(url_for("asistente.aprendizaje"))
    conn.execute(
        """
        INSERT INTO asistente_fuentes_conocimiento (
            empresa_id, tipo, ambito, area, titulo, url, autor, resumen,
            lectura, uso_recomendado, riesgos, confianza_pct, estado,
            lectura_ia, usuario
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            empresa_id,
            fuente["tipo"],
            fuente["ambito"],
            fuente["area"],
            fuente["titulo"],
            fuente["url"],
            fuente["autor"],
            fuente["resumen"],
            fuente["lectura"],
            fuente["uso_recomendado"],
            fuente["riesgos"],
            fuente["confianza_pct"],
            fuente["estado"],
            fuente["lectura_ia"],
            session.get("user_nombre") or session.get("username") or "usuario",
        ),
    )
    conn.commit()
    flash(f"Fuente guardada como {fuente['estado']} con confianza {fuente['confianza_pct']}%.", "success")
    return redirect(url_for("asistente.aprendizaje"))


@asistente_bp.route("/conocimiento/fuente/url", methods=["POST"])
@login_required
def guardar_fuente_desde_url():
    if not _puede_ver_asistente():
        flash("No tienes permisos para guardar fuentes de conocimiento.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    url = (request.form.get("url") or "").strip()
    if not url:
        flash("Pega un enlace para capturar la fuente.", "warning")
        return redirect(url_for("asistente.aprendizaje"))
    capturada = extraer_fuente_desde_url(url)
    if not capturada.get("ok"):
        flash(capturada.get("error") or "No se pudo leer el enlace.", "warning")
        return redirect(url_for("asistente.aprendizaje"))

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    fuente = evaluar_fuente_conocimiento(capturada)
    conn.execute(
        """
        INSERT INTO asistente_fuentes_conocimiento (
            empresa_id, tipo, ambito, area, titulo, url, autor, resumen,
            lectura, uso_recomendado, riesgos, confianza_pct, estado,
            lectura_ia, usuario
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            empresa_id,
            fuente["tipo"],
            fuente["ambito"],
            fuente["area"],
            fuente["titulo"],
            fuente["url"],
            fuente["autor"],
            fuente["resumen"],
            fuente["lectura"],
            fuente["uso_recomendado"],
            fuente["riesgos"],
            fuente["confianza_pct"],
            fuente["estado"],
            fuente["lectura_ia"],
            session.get("user_nombre") or session.get("username") or "usuario",
        ),
    )
    conn.commit()
    flash(f"Enlace leido y guardado como {fuente['estado']} con confianza {fuente['confianza_pct']}%.", "success")
    return redirect(url_for("asistente.aprendizaje"))


def _insertar_fuente_conocimiento(conn, empresa_id, fuente, usuario):
    conn.execute(
        """
        INSERT INTO asistente_fuentes_conocimiento (
            empresa_id, tipo, ambito, area, titulo, url, autor, resumen,
            lectura, uso_recomendado, riesgos, confianza_pct, estado,
            lectura_ia, usuario
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            empresa_id,
            fuente["tipo"],
            fuente["ambito"],
            fuente["area"],
            fuente["titulo"],
            fuente["url"],
            fuente["autor"],
            fuente["resumen"],
            fuente["lectura"],
            fuente["uso_recomendado"],
            fuente["riesgos"],
            fuente["confianza_pct"],
            fuente["estado"],
            fuente["lectura_ia"],
            usuario,
        ),
    )


def _fuente_conocimiento_existe(conn, empresa_id, url, titulo):
    url = (url or "").strip()
    titulo = (titulo or "").strip()
    if url:
        existe = conn.execute(
            """
            SELECT 1 FROM asistente_fuentes_conocimiento
            WHERE empresa_id = ? AND url = ?
            LIMIT 1
            """,
            (empresa_id, url),
        ).fetchone()
        if existe:
            return True
    if titulo:
        existe = conn.execute(
            """
            SELECT 1 FROM asistente_fuentes_conocimiento
            WHERE empresa_id = ? AND lower(titulo) = lower(?)
            LIMIT 1
            """,
            (empresa_id, titulo),
        ).fetchone()
        if existe:
            return True
    return False


def _importar_items_fuente_externa(conn, empresa_id, fuente_row, limite, usuario):
    resultado = extraer_fuentes_desde_feed(fuente_row["url"], limite=limite)
    if not resultado.get("ok"):
        return {"ok": False, "error": resultado.get("error") or "Error leyendo fuente", "creadas": 0, "duplicadas": 0}

    creadas = 0
    duplicadas = 0
    for item in resultado.get("items") or []:
        item["ambito"] = fuente_row["ambito"] or item.get("ambito") or "multi_hosteleria"
        item["area"] = fuente_row["area"] or item.get("area") or "mercado"
        item["tipo"] = item.get("tipo") or "otro"
        evaluada = evaluar_fuente_conocimiento(item)
        if _fuente_conocimiento_existe(conn, empresa_id, evaluada.get("url"), evaluada.get("titulo")):
            duplicadas += 1
            continue
        _insertar_fuente_conocimiento(conn, empresa_id, evaluada, usuario)
        creadas += 1
    return {"ok": True, "modo": resultado.get("modo"), "creadas": creadas, "duplicadas": duplicadas}


FUENTES_EXTERNAS_RECOMENDADAS = [
    {
        "nombre": "Hosteltur - actualidad turismo",
        "url": "https://www.hosteltur.com/rss",
        "tipo": "feed",
        "area": "mercado",
        "ambito": "multi_hosteleria",
    },
    {
        "nombre": "Google News - turismo Espana",
        "url": "https://news.google.com/rss/search?q=turismo%20Espa%C3%B1a%20hosteler%C3%ADa&hl=es&gl=ES&ceid=ES:es",
        "tipo": "feed",
        "area": "mercado",
        "ambito": "multi_hosteleria",
    },
    {
        "nombre": "Google News - hoteles revenue",
        "url": "https://news.google.com/rss/search?q=hoteles%20revenue%20management%20Espa%C3%B1a&hl=es&gl=ES&ceid=ES:es",
        "tipo": "feed",
        "area": "revenue",
        "ambito": "hotel",
    },
    {
        "nombre": "Google News - viajeros y demanda turistica",
        "url": "https://news.google.com/rss/search?q=previsi%C3%B3n%20viajeros%20turismo%20Espa%C3%B1a&hl=es&gl=ES&ceid=ES:es",
        "tipo": "feed",
        "area": "mercado",
        "ambito": "hotel",
    },
    {
        "nombre": "Google News - tendencias gastronomicas",
        "url": "https://news.google.com/rss/search?q=tendencias%20gastron%C3%B3micas%20restauraci%C3%B3n%20hosteler%C3%ADa&hl=es&gl=ES&ceid=ES:es",
        "tipo": "feed",
        "area": "cocina",
        "ambito": "restaurante",
    },
    {
        "nombre": "Google News - costes alimentos hosteleria",
        "url": "https://news.google.com/rss/search?q=coste%20alimentos%20proveedores%20hosteler%C3%ADa&hl=es&gl=ES&ceid=ES:es",
        "tipo": "feed",
        "area": "compras",
        "ambito": "multi_hosteleria",
    },
    {
        "nombre": "Google News - empleo hosteleria",
        "url": "https://news.google.com/rss/search?q=empleo%20hosteler%C3%ADa%20Espa%C3%B1a&hl=es&gl=ES&ceid=ES:es",
        "tipo": "feed",
        "area": "rrhh",
        "ambito": "multi_hosteleria",
    },
    {
        "nombre": "Google News - PRL hosteleria",
        "url": "https://news.google.com/rss/search?q=prevenci%C3%B3n%20riesgos%20laborales%20hosteler%C3%ADa&hl=es&gl=ES&ceid=ES:es",
        "tipo": "feed",
        "area": "rrhh",
        "ambito": "multi_hosteleria",
    },
    {
        "nombre": "Google News - tour operadores agencias",
        "url": "https://news.google.com/rss/search?q=tour%20operadores%20agencias%20viajes%20hoteles%20Espa%C3%B1a&hl=es&gl=ES&ceid=ES:es",
        "tipo": "feed",
        "area": "comercial",
        "ambito": "hotel",
    },
]


@asistente_bp.route("/conocimiento/fuentes-recomendadas", methods=["POST"])
@login_required
def cargar_fuentes_externas_recomendadas():
    if not _puede_ver_asistente():
        flash("No tienes permisos para cargar fuentes externas.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    usuario = session.get("user_nombre") or session.get("username") or "usuario"
    creadas = 0
    existentes = 0
    for fuente in FUENTES_EXTERNAS_RECOMENDADAS:
        existe = conn.execute(
            """
            SELECT 1
            FROM asistente_fuentes_externas
            WHERE empresa_id = ?
              AND (LOWER(url) = LOWER(?) OR LOWER(nombre) = LOWER(?))
            """,
            (empresa_id, fuente["url"], fuente["nombre"]),
        ).fetchone()
        if existe:
            existentes += 1
            continue
        conn.execute(
            """
            INSERT INTO asistente_fuentes_externas
            (empresa_id, nombre, url, tipo, ambito, area, estado, usuario)
            VALUES (?, ?, ?, ?, ?, ?, 'activa', ?)
            """,
            (
                empresa_id,
                fuente["nombre"],
                fuente["url"],
                fuente["tipo"],
                fuente["ambito"],
                fuente["area"],
                usuario,
            ),
        )
        creadas += 1
    conn.commit()
    flash(f"Fuentes recomendadas cargadas: {creadas} nuevas, {existentes} ya existian.", "success")
    return redirect(url_for("asistente.aprendizaje"))


@asistente_bp.route("/conocimiento/fuente-externa", methods=["POST"])
@login_required
def guardar_fuente_externa():
    if not _puede_ver_asistente():
        flash("No tienes permisos para guardar fuentes externas.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    nombre = (request.form.get("nombre") or "").strip()
    url = (request.form.get("url") or "").strip()
    if not nombre or not url:
        flash("La fuente externa necesita nombre y URL.", "warning")
        return redirect(url_for("asistente.aprendizaje"))
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    conn.execute(
        """
        INSERT INTO asistente_fuentes_externas (
            empresa_id, nombre, url, tipo, ambito, area, estado, usuario
        ) VALUES (?, ?, ?, ?, ?, ?, 'activa', ?)
        """,
        (
            empresa_id,
            nombre,
            url,
            request.form.get("tipo") or "feed",
            request.form.get("ambito") or "multi_hosteleria",
            request.form.get("area") or "mercado",
            session.get("user_nombre") or session.get("username") or "usuario",
        ),
    )
    conn.commit()
    flash("Fuente externa guardada. Ya puedes leerla para importar senales.", "success")
    return redirect(url_for("asistente.aprendizaje"))


@asistente_bp.route("/conocimiento/fuente-externa/<int:fuente_id>/leer", methods=["POST"])
@login_required
def leer_fuente_externa(fuente_id):
    if not _puede_ver_asistente():
        flash("No tienes permisos para leer fuentes externas.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    fuente_row = conn.execute(
        "SELECT * FROM asistente_fuentes_externas WHERE id = ? AND empresa_id = ?",
        (fuente_id, empresa_id),
    ).fetchone()
    if not fuente_row:
        flash("Fuente externa no encontrada.", "warning")
        return redirect(url_for("asistente.aprendizaje"))
    try:
        limite = int(float(str(request.form.get("limite") or 8).replace(",", ".")))
    except (TypeError, ValueError):
        limite = 8
    usuario = session.get("user_nombre") or session.get("username") or "usuario"
    resultado = _importar_items_fuente_externa(conn, empresa_id, fuente_row, limite, usuario)
    if not resultado.get("ok"):
        conn.execute(
            """
            UPDATE asistente_fuentes_externas
            SET ultima_lectura = CURRENT_TIMESTAMP, ultimo_resultado = ?
            WHERE id = ? AND empresa_id = ?
            """,
            (resultado.get("error") or "Error leyendo fuente", fuente_id, empresa_id),
        )
        conn.commit()
        flash(resultado.get("error") or "No se pudo leer la fuente externa.", "warning")
        return redirect(url_for("asistente.aprendizaje"))

    conn.execute(
        """
        UPDATE asistente_fuentes_externas
        SET ultima_lectura = CURRENT_TIMESTAMP, ultimo_resultado = ?
        WHERE id = ? AND empresa_id = ?
        """,
        (
            f"Importadas {resultado['creadas']} nuevas; {resultado['duplicadas']} duplicadas desde {resultado.get('modo')}",
            fuente_id,
            empresa_id,
        ),
    )
    conn.commit()
    flash(f"Fuente externa leida: {resultado['creadas']} nuevas, {resultado['duplicadas']} duplicadas.", "success")
    return redirect(url_for("asistente.aprendizaje"))


@asistente_bp.route("/conocimiento/fuente-externa/leer-todas", methods=["POST"])
@login_required
def leer_todas_fuentes_externas():
    if not _puede_ver_asistente():
        flash("No tienes permisos para leer fuentes externas.", "warning")
        return redirect(url_for("home"))
    bloqueo = _requiere_ia_estrategica()
    if bloqueo:
        return bloqueo

    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    try:
        limite = int(float(str(request.form.get("limite") or 6).replace(",", ".")))
    except (TypeError, ValueError):
        limite = 6
    fuentes = conn.execute(
        """
        SELECT * FROM asistente_fuentes_externas
        WHERE empresa_id = ? AND estado = 'activa'
        ORDER BY datetime(COALESCE(ultima_lectura, fecha_creacion)) ASC
        LIMIT 12
        """,
        (empresa_id,),
    ).fetchall()
    usuario = session.get("user_nombre") or session.get("username") or "usuario"
    total_nuevas = 0
    total_duplicadas = 0
    errores = 0
    for fuente_row in fuentes:
        resultado = _importar_items_fuente_externa(conn, empresa_id, fuente_row, limite, usuario)
        if resultado.get("ok"):
            total_nuevas += resultado["creadas"]
            total_duplicadas += resultado["duplicadas"]
            texto = f"Importadas {resultado['creadas']} nuevas; {resultado['duplicadas']} duplicadas desde {resultado.get('modo')}"
        else:
            errores += 1
            texto = resultado.get("error") or "Error leyendo fuente"
        conn.execute(
            """
            UPDATE asistente_fuentes_externas
            SET ultima_lectura = CURRENT_TIMESTAMP, ultimo_resultado = ?
            WHERE id = ? AND empresa_id = ?
            """,
            (texto, fuente_row["id"], empresa_id),
        )
    conn.commit()
    flash(
        f"Lectura externa completada: {total_nuevas} nuevas, {total_duplicadas} duplicadas, {errores} errores.",
        "success" if errores == 0 else "warning",
    )
    return redirect(url_for("asistente.aprendizaje"))


@asistente_bp.route("/recomendaciones/<int:recomendacion_id>/estado", methods=["POST"])
@login_required
def actualizar_estado(recomendacion_id):
    if not _puede_ver_asistente():
        flash("No tienes permisos para actualizar recomendaciones.", "warning")
        return redirect(url_for("home"))

    estado = (request.form.get("estado") or "revisada").strip().lower()
    if estado not in {"pendiente", "revisada", "aceptada", "descartada"}:
        estado = "revisada"
    resultado = (request.form.get("resultado") or "").strip()
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_asistente_schema(conn)
    conn.execute(
        """
        UPDATE asistente_recomendaciones
        SET estado = ?, resultado = ?, aceptada_por = ?, fecha_decision = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (estado, resultado, session.get("user_nombre") or session.get("username") or "usuario", recomendacion_id, empresa_id),
    )
    _registrar_evento_aprendizaje(conn, empresa_id, recomendacion_id, estado, resultado)
    conn.commit()
    flash("Recomendacion actualizada.", "success")
    return redirect(request.referrer or url_for("asistente.dashboard"))

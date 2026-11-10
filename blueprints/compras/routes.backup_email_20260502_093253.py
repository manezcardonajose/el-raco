# blueprints/compras/routes.py
from flask import render_template, request, redirect, url_for, session, flash
from database import get_db
from datetime import datetime, timedelta
from . import compras_bp
from blueprints.auth.routes import login_required
import pandas as pd
from io import BytesIO
from flask import send_file
from flask import current_app
import uuid
import os
from werkzeug.utils import secure_filename
from flask import send_file
from pathlib import Path
import re
from typing import Any

# ============================================
# FUNCIONES AUXILIARES
# ============================================

def _columnas_tabla(cursor, tabla):
    try:
        return [row[1] for row in cursor.execute(f"PRAGMA table_info({tabla})").fetchall()]
    except Exception:
        return []


def _safe_add_column(cursor, tabla, definicion):
    nombre = definicion.strip().split()[0]
    if nombre not in _columnas_tabla(cursor, tabla):
        cursor.execute(f"ALTER TABLE {tabla} ADD COLUMN {definicion}")


def _asegurar_estructura_compras(cursor):
    """Migración defensiva de compras. No falla si algo ya existe."""
    _safe_add_column(cursor, 'pedidos', 'departamento_id INTEGER')
    _safe_add_column(cursor, 'pedidos', 'estado_recepcion TEXT')
    _safe_add_column(cursor, 'pedidos', 'fecha_recepcion DATE')
    _safe_add_column(cursor, 'pedidos', 'fecha_envio DATETIME')
    _safe_add_column(cursor, 'pedidos', 'cerrado INTEGER DEFAULT 0')
    _safe_add_column(cursor, 'pedidos', 'fecha_cierre DATETIME')
    _safe_add_column(cursor, 'recepciones', 'departamento_id INTEGER')
    _safe_add_column(cursor, 'recepciones', 'tipo_recepcion TEXT')
    _safe_add_column(cursor, 'recepciones', 'validada_operativamente INTEGER DEFAULT 0')
    _safe_add_column(cursor, 'recepciones', 'validada_economicamente INTEGER DEFAULT 0')
    _safe_add_column(cursor, 'recepciones', 'stock_aplicado INTEGER DEFAULT 0')
    _safe_add_column(cursor, 'recepciones', 'documento_adjunto_id INTEGER')
    _safe_add_column(cursor, 'lineas_recepcion', 'almacen_id INTEGER')
    _safe_add_column(cursor, 'lineas_recepcion', 'lote TEXT')
    _safe_add_column(cursor, 'lineas_recepcion', 'fecha_caducidad DATE')
    _safe_add_column(cursor, 'lineas_recepcion', 'temperatura REAL')
    _safe_add_column(cursor, 'lineas_recepcion', 'precio_unitario REAL')
    _safe_add_column(cursor, 'lineas_recepcion', 'importe_total REAL')
    _safe_add_column(cursor, 'lineas_recepcion', 'aceptado INTEGER DEFAULT 1')
    _safe_add_column(cursor, 'lineas_recepcion', 'motivo_rechazo TEXT')
    _safe_add_column(cursor, 'lineas_recepcion', 'incidencia_id INTEGER')
    _safe_add_column(cursor, 'lineas_recepcion', 'empresa_id INTEGER DEFAULT 1')
    _safe_add_column(cursor, 'lotes', 'responsable_recepcion TEXT')
    _safe_add_column(cursor, 'lotes', 'incidencia_id INTEGER')
    _safe_add_column(cursor, 'lotes', 'documento_origen TEXT')
    _safe_add_column(cursor, 'lotes', 'notas_trazabilidad TEXT')
    _safe_add_column(cursor, 'productos', 'visible_en_compras INTEGER DEFAULT 1')
    _safe_add_column(cursor, 'productos', 'visible_en_produccion INTEGER DEFAULT 1')
    _safe_add_column(cursor, 'productos', 'visible_en_venta INTEGER DEFAULT 0')
    _safe_add_column(cursor, 'productos', 'permite_compra_directa INTEGER DEFAULT 1')
    _safe_add_column(cursor, 'productos', 'codigo_interno TEXT')
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documentos_compra (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor_id INTEGER,
            pedido_id INTEGER,
            recepcion_id INTEGER,
            tipo_documento TEXT DEFAULT 'albaran',
            nombre_archivo TEXT,
            ruta_archivo TEXT,
            mime_type TEXT,
            origen_subida TEXT,
            texto_extraido TEXT,
            procesado_ia INTEGER DEFAULT 0,
            validado INTEGER DEFAULT 0,
            fecha_subida DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documento_compra_lineas_detectadas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            documento_id INTEGER,
            producto_id INTEGER,
            descripcion_detectada TEXT,
            cantidad_detectada REAL,
            precio_detectado REAL,
            estado TEXT,
            observaciones TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recepcion_validaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recepcion_id INTEGER NOT NULL,
            tipo_validacion TEXT,
            estado TEXT,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            observaciones TEXT
        )
    """)


def _departamentos_activos(conn, empresa_id):
    """Devuelve departamentos reales disponibles para compras.

    Algunos ZIP tienen departamentos con empresa_id NULL o con activo vacío.
    Para no dejar el selector sin opciones, se intenta en orden:
    1) activos de la empresa o globales
    2) activos de cualquier empresa
    3) empresa o globales aunque activo no exista/no coincida
    4) cualquier departamento
    """
    consultas = [
        ("""
            SELECT id, nombre
            FROM departamentos
            WHERE COALESCE(activo, 1) = 1
              AND (empresa_id = ? OR empresa_id IS NULL)
            ORDER BY nombre
        """, (empresa_id,)),
        ("""
            SELECT id, nombre
            FROM departamentos
            WHERE COALESCE(activo, 1) = 1
            ORDER BY nombre
        """, ()),
        ("""
            SELECT id, nombre
            FROM departamentos
            WHERE empresa_id = ? OR empresa_id IS NULL
            ORDER BY nombre
        """, (empresa_id,)),
        ("""
            SELECT id, nombre
            FROM departamentos
            ORDER BY nombre
        """, ()),
    ]

    for sql, params in consultas:
        try:
            rows = conn.execute(sql, params).fetchall()
            if rows:
                return rows
        except Exception:
            continue

    return []


def _to_float_safe(valor: Any, defecto: Any = 0.0):
    try:
        txt = str(valor if valor is not None else '').strip().replace(',', '.')
        return float(txt) if txt else defecto
    except Exception:
        return defecto


def _to_int_or_none(valor):
    try:
        txt = str(valor if valor is not None else '').strip()
        return int(txt) if txt else None
    except Exception:
        return None

def generar_numero_pedido():
    """Genera un número de pedido único"""
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    fecha = datetime.now().strftime("%Y%m%d")
    
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as total FROM pedidos WHERE fecha_pedido = date('now') AND empresa_id = ?", (empresa_id,))
    total_hoy = cursor.fetchone()['total'] + 1
    conn.close()
    
    return f"PED-{fecha}-{total_hoy:03d}"



def _recalcular_estado_pedido(cursor, pedido_id, empresa_id):
    """
    Recalcula el estado del pedido según lo realmente recibido.
    Estados usados:
    - borrador
    - enviado
    - recibido_parcial
    - recibido
    - cerrado
    - cancelado
    """
    pedido = cursor.execute(
        "SELECT estado FROM pedidos WHERE id = ? AND empresa_id = ?",
        (pedido_id, empresa_id)
    ).fetchone()

    if not pedido:
        return None

    # No tocar estados finales administrativos
    if pedido['estado'] in ('cerrado', 'cancelado'):
        return pedido['estado']

    lineas = cursor.execute("""
        SELECT COALESCE(cantidad_solicitada, 0) AS cantidad_solicitada,
               COALESCE(cantidad_recibida, 0) AS cantidad_recibida
        FROM lineas_pedido
        WHERE pedido_id = ?
    """, (pedido_id,)).fetchall()

    if not lineas:
        return pedido['estado']

    total_lineas = len(lineas)
    lineas_con_recepcion = 0
    lineas_completas = 0

    for linea in lineas:
        pedida = float(linea['cantidad_solicitada'] or 0)
        recibida = float(linea['cantidad_recibida'] or 0)

        if recibida > 0:
            lineas_con_recepcion += 1
        if pedida > 0 and recibida >= pedida:
            lineas_completas += 1

    if lineas_completas == total_lineas:
        nuevo_estado = 'recibido'
    elif lineas_con_recepcion > 0:
        nuevo_estado = 'recibido_parcial'
    else:
        # Si aún no hay recepción, solo puede seguir en borrador o enviado
        nuevo_estado = 'enviado' if pedido['estado'] != 'borrador' else 'borrador'

    if nuevo_estado != pedido['estado']:
        cursor.execute(
            "UPDATE pedidos SET estado = ? WHERE id = ? AND empresa_id = ?",
            (nuevo_estado, pedido_id, empresa_id)
        )
    return nuevo_estado


def _pedido_tiene_recepciones(cursor, pedido_id):
    row = cursor.execute(
        "SELECT COUNT(*) AS total FROM recepciones WHERE pedido_id = ?",
        (pedido_id,)
    ).fetchone()
    return bool(row and row['total'])

def guardar_documento_compra_en_cursor(
    cursor,
    file,
    proveedor_id=None,
    pedido_id=None,
    recepcion_id=None,
    tipo_documento='albaran',
    origen_subida='manual'
):
    if not file or file.filename == '':
        return None

    nombre_original = secure_filename(str(file.filename or 'documento'))
    extension = os.path.splitext(nombre_original)[1].lower()
    nombre_archivo = f"{uuid.uuid4().hex}{extension}"

    upload_dir = os.path.join(current_app.instance_path, 'uploads', 'compras')
    os.makedirs(upload_dir, exist_ok=True)

    ruta_final = os.path.join(upload_dir, nombre_archivo)
    file.save(ruta_final)

    cursor.execute("""
        INSERT INTO documentos_compra (
            proveedor_id, pedido_id, recepcion_id, tipo_documento,
            nombre_archivo, ruta_archivo, mime_type, origen_subida,
            procesado_ia, validado
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
    """, (
        proveedor_id, pedido_id, recepcion_id, tipo_documento,
        nombre_archivo, ruta_final, file.mimetype, origen_subida
    ))

    return cursor.lastrowid


def procesar_documento_compra_ocr_en_cursor(cursor, documento_id, empresa_id, proveedor_id=None):
    """Extrae texto de cualquier documento de compra, guarda líneas detectadas y devuelve resumen.

    Esta función es la que usan las recepciones cuando se pulsa "Leer / analizar".
    No crea productos automáticamente: si no reconoce un producto, lo deja pendiente para revisión.
    """
    if not documento_id:
        return {'texto': '', 'lineas': 0, 'asociadas': 0}

    documento = cursor.execute(
        "SELECT * FROM documentos_compra WHERE id = ?",
        (documento_id,)
    ).fetchone()

    if not documento:
        return {'texto': '', 'lineas': 0, 'asociadas': 0}

    texto = extraer_texto_documento_compra(documento['ruta_archivo'])

    cursor.execute("""
        UPDATE documentos_compra
        SET texto_extraido = ?, procesado_ia = 1
        WHERE id = ?
    """, (texto, documento_id))

    cursor.execute(
        "DELETE FROM documento_compra_lineas_detectadas WHERE documento_id = ?",
        (documento_id,)
    )

    detectadas = 0
    asociadas = 0

    for linea in detectar_lineas_documento_compra(texto):
        descripcion = (linea.get('descripcion') or '').strip()
        referencia = (linea.get('referencia') or '').strip()

        producto_id = buscar_producto_por_texto(
            cursor.connection,
            descripcion,
            empresa_id,
            referencia=referencia
        )

        if producto_id:
            asociadas += 1

        cursor.execute("""
            INSERT INTO documento_compra_lineas_detectadas (
                documento_id,
                producto_id,
                descripcion_detectada,
                cantidad_detectada,
                precio_detectado,
                estado,
                observaciones
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            documento_id,
            producto_id,
            descripcion,
            linea.get('cantidad'),
            linea.get('precio'),
            'asociado' if producto_id else 'pendiente_producto',
            f"Ref: {referencia or '-'} | Importe: {linea.get('importe') or '-'}"
        ))
        detectadas += 1

    return {'texto': texto, 'lineas': detectadas, 'asociadas': asociadas}

def extraer_texto_documento_compra(ruta_archivo):
    
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if not ruta_archivo or not os.path.exists(ruta_archivo):
        return ''

    extension = Path(ruta_archivo).suffix.lower()

    if extension == '.pdf':
        try:
            from pypdf import PdfReader
            reader = PdfReader(ruta_archivo)
            textos = []

            for page in reader.pages:
                textos.append(page.extract_text() or '')

            texto = '\n'.join(textos).strip()

            if texto:
                return texto

            # PDF escaneado: convertir páginas a imagen y pasar OCR.
            # Requiere pdf2image + Poppler instalados en Windows. Si no están,
            # devolvemos un mensaje claro sin romper la recepción.
            try:
                from pdf2image import convert_from_path
                from PIL import ImageOps, ImageFilter
                import pytesseract

                poppler_path = current_app.config.get('POPPLER_PATH')
                if poppler_path:
                    paginas = convert_from_path(ruta_archivo, dpi=250, poppler_path=poppler_path)
                else:
                    paginas = convert_from_path(ruta_archivo, dpi=250)

                textos_ocr = []
                for pagina in paginas:
                    try:
                        imagen_base = ImageOps.exif_transpose(pagina)
                        imagen_base = imagen_base.convert('L')
                        imagen_base = ImageOps.autocontrast(imagen_base)
                        imagen_base = imagen_base.filter(ImageFilter.SHARPEN)
                        for config in ['--oem 3 --psm 6', '--oem 3 --psm 4', '--oem 3 --psm 11']:
                            t = pytesseract.image_to_string(imagen_base, lang='spa', config=config)
                            if t and t.strip():
                                textos_ocr.append(t.strip())
                    except Exception:
                        continue

                if textos_ocr:
                    textos_ocr.sort(key=lambda t: len(detectar_lineas_documento_compra(t)), reverse=True)
                    return textos_ocr[0].strip()

                return 'OCR_SIN_TEXTO: PDF escaneado convertido, pero no se pudo leer texto.'
            except Exception as e:
                return f'PDF_ESCANEADO_SIN_TEXTO: instala/configura pdf2image + Poppler para leer este PDF. Detalle: {str(e)}'

        except Exception as e:
            return f'ERROR_EXTRACCION_PDF: {str(e)}'

    if extension in ['.jpg', '.jpeg', '.png', '.webp']:
        try:
            from PIL import Image
            import pytesseract

            # Si Tesseract no está en PATH, descomenta y ajusta esta ruta:
            # pytesseract.pytesseract.tesseract_cmd = r"C:\\Program Files\\Tesseract-OCR\\tesseract.exe"

            imagen = Image.open(ruta_archivo)

            # Preprocesado prudente para fotos de albaranes/tickets.
            # No altera el documento guardado; solo mejora la lectura OCR.
            try:
                from PIL import ImageOps, ImageFilter
                imagen_base = ImageOps.exif_transpose(imagen)
                imagen_base = imagen_base.convert('L')
                imagen_base = ImageOps.autocontrast(imagen_base)

                ancho, alto = imagen_base.size
                if ancho < 1400:
                    factor = max(2, int(1400 / max(ancho, 1)))
                    imagen_base = imagen_base.resize((ancho * factor, alto * factor))

                imagen_base = imagen_base.filter(ImageFilter.SHARPEN)
            except Exception:
                imagen_base = imagen

            textos = []
            for config in [
                '--oem 3 --psm 6',
                '--oem 3 --psm 4',
                '--oem 3 --psm 11'
            ]:
                try:
                    t = pytesseract.image_to_string(imagen_base, lang='spa', config=config)
                    if t and t.strip():
                        textos.append(t.strip())
                except Exception:
                    pass

            if textos:
                # Elegimos el texto que genera más líneas de compra válidas.
                try:
                    textos.sort(key=lambda t: len(detectar_lineas_documento_compra(t)), reverse=True)
                except Exception:
                    textos.sort(key=len, reverse=True)
                return textos[0].strip()

            return 'OCR_SIN_TEXTO: no se pudo leer texto de la imagen.'

        except Exception as e:
            return f'ERROR_OCR_IMAGEN: {str(e)}'

    return 'TIPO_DOCUMENTO_NO_SOPORTADO'

import re


def _normalizar_importe_ocr(valor):
    if not valor:
        return None

    texto = str(valor)

    # 🔥 CORRECCIÓN OCR
    texto = texto.replace("'", ".")
    texto = texto.replace("/", ".")
    texto = texto.replace(" ", "")
    texto = texto.replace(",", ".")

    # eliminar cosas raras tipo "7..10"
    texto = re.sub(r'\.+', '.', texto)

    try:
        return float(texto)
    except:
        return None


def _extraer_total_documento(texto):
    if not texto:
        return None
    candidatos = []
    for linea in texto.splitlines():
        limpia = " ".join(linea.strip().split())
        lower = limpia.lower()
        if 'total' in lower or 'entregado' in lower:
            for n in re.findall(r'\d+(?:[.,]\d+)?', limpia):
                valor = _normalizar_importe_ocr(n)
                if valor is not None and 0 < valor < 100000:
                    candidatos.append(valor)
    return candidatos[-1] if candidatos else None


def detectar_numero_pedido_desde_texto(texto):
    """Detecta número de pedido Nexora tipo PED-20260429-001 en OCR/PDF."""
    if not texto:
        return None

    patrones = [
        r'\bPED[-\s]*(\d{8})[-\s]*(\d{3,5})\b',
        r'\bPEDIDO\s*(?:N[ºO]\.?\s*)?(PED[-\s]*\d{8}[-\s]*\d{3,5})\b',
    ]

    texto_limpio = str(texto).upper()
    for patron in patrones:
        m = re.search(patron, texto_limpio, flags=re.I)
        if not m:
            continue
        if len(m.groups()) == 2:
            return f"PED-{m.group(1)}-{m.group(2)}"
        return m.group(1).replace(' ', '').replace('PED-', 'PED-')

    return None


def detectar_lineas_documento_compra(texto):
    """
    Parser OCR reforzado para albaranes/tickets de compras en hostelería.

    Objetivo:
    - Leer PDF digital, PDF escaneado convertido a texto por OCR y fotos.
    - Separar código de proveedor + nombre cuando vienen pegados: 15225CALABACIN.
    - Interpretar líneas tipo: codigo descripcion cantidad precio total.
    - Ignorar totales, IVA/IGIC, cabeceras y datos de empresa.
    - No inventar productos: devuelve descripción/código/cantidad/precio para que otra capa enlace con BD.
    - El precio detectado queda como dato económico/documental, no como dato operativo de recepción.
    """

    import re
    import unicodedata

    resultado = []

    if not texto:
        return resultado

    def _normalizar_espacios(valor):
        valor = (valor or '').replace('\t', ' ')
        valor = valor.replace('\xa0', ' ')
        valor = re.sub(r'\s+', ' ', valor).strip()
        return valor

    def _sin_acentos(valor):
        valor = str(valor or '')
        return ''.join(
            c for c in unicodedata.normalize('NFD', valor)
            if unicodedata.category(c) != 'Mn'
        )

    def _lower_plano(valor):
        return _sin_acentos(valor).lower()

    def _numero_desde_token(token):
        if token is None:
            return None
        txt = str(token).strip()
        if not txt:
            return None

        txt = txt.replace('€', '')
        txt = txt.replace('%', '')
        txt = txt.replace(' ', '')
        txt = txt.replace("'", ',')
        txt = txt.replace('´', ',')
        txt = txt.replace('`', ',')
        txt = txt.replace('/', ',')
        txt = txt.replace('O', '0').replace('o', '0')

        # miles europeos/americanos
        if re.match(r'^\d{1,3}(\.\d{3})+,\d+$', txt):
            txt = txt.replace('.', '').replace(',', '.')
        elif re.match(r'^\d{1,3}(,\d{3})+\.\d+$', txt):
            txt = txt.replace(',', '')
        else:
            txt = txt.replace(',', '.')

        txt = re.sub(r'[^0-9.\-]', '', txt)
        txt = re.sub(r'\.+', '.', txt)
        if txt in ('', '.', '-', '-.'):
            return None
        try:
            return float(txt)
        except Exception:
            return None

    def _tiene_letras(valor):
        return bool(re.search(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]', valor or ''))

    def _limpiar_linea(linea):
        linea = _normalizar_espacios(linea)
        # Quitar marcadores frecuentes en PDFs exportados o escaneados.
        linea = re.sub(r'^[\-–—_·•\.\s]+', '', linea).strip()
        linea = re.sub(r'\s+[\-–—_·•]+\s*$', '', linea).strip()
        return _normalizar_espacios(linea)

    palabras_excluir = [
        'hoja', 'pagina', 'producto cantidad', 'cantidad unidad', 'precio total',
        'codigo', 'código', 'ref proveedor', 'referencia proveedor', 'proveedor',
        'empresa', 'principal', 'direccion', 'dirección', 'cif', 'nif', 'telefono',
        'teléfono', 'email', 'www', 'http', 'fecha', 'factura', 'ticket', 'albaran',
        'albarán', 'pedido nº', 'pedido no', 'pedido num', 'pedido n', 'total pedido',
        'total:', 'subtotal', 'base imponible', 'iva', 'igic', 'forma de pago',
        'tarjeta', 'entregado', 'cambio', 'cajero', 'operacion', 'operación',
        'autorizacion', 'autorización', 'cliente', 'gracias'
    ]

    def _es_linea_administrativa(linea):
        if not linea or len(linea) < 3:
            return True
        lower = _lower_plano(linea)
        if any(p in lower for p in [_lower_plano(x) for x in palabras_excluir]):
            # Permitir productos que contengan una palabra ambigua solo si tienen estructura clara de línea.
            if not re.search(r'\b\d+(?:[,.]\d+)?\s+\d+(?:[,.]\d+)?\s+\d+(?:[,.]\d+)?\b', linea):
                return True
        # Fechas y documentos administrativos.
        if re.search(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', linea):
            return True
        if re.match(r'^(total|subtotal|iva|igic|base)\b', lower):
            return True
        # Línea casi solo numérica: totales/IVA.
        letras = re.findall(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]', linea)
        numeros = re.findall(r'\d', linea)
        if len(letras) < 3 and len(numeros) >= 2:
            return True
        return False

    def _extraer_tokens_numericos(linea):
        tokens = []
        # Números separados; evita coger partes internas de palabras salvo casos 2manojos.
        patron = r'(?<![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])[-+]?\d+(?:[.,\'´`/]\d+)?%?'
        for m in re.finditer(patron, linea):
            raw = m.group(0)
            if '%' in raw:
                continue
            valor = _numero_desde_token(raw)
            if valor is None:
                continue
            tokens.append({
                'raw': raw,
                'valor': valor,
                'start': m.start(),
                'end': m.end(),
            })
        return tokens

    def _separar_codigo_nombre(descripcion):
        descripcion = _normalizar_espacios(descripcion)
        if not descripcion:
            return '', ''

        # Caso frecuente: codigo pegado al nombre: 15225CALABACIN / pr022PIMIENTOS.
        m = re.match(r'^([A-Za-z]{1,6}\d{1,8}|\d{2,10})([A-Za-zÁÉÍÓÚÜÑáéíóúüñ].*)$', descripcion)
        if m:
            codigo = m.group(1).strip()
            nombre = m.group(2).strip()
            return codigo, nombre

        partes = descripcion.split(' ', 1)
        if len(partes) == 2:
            posible_codigo, resto = partes[0].strip(), partes[1].strip()
            # Código proveedor normal: PR022, PV011, 15225, etc.
            if re.match(r'^[A-Za-z]{0,6}\d{1,10}[A-Za-z]{0,4}$', posible_codigo) and _tiene_letras(resto):
                return posible_codigo, resto

        return '', descripcion

    def _limpiar_descripcion_producto(descripcion):
        descripcion = _normalizar_espacios(descripcion)
        descripcion = re.sub(r'^[\-–—_·•\.\s]+', '', descripcion).strip()
        descripcion = re.sub(r'[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 .,/+\-xX]', ' ', descripcion)
        descripcion = _normalizar_espacios(descripcion)
        # Quitar restos de columnas de unidad si quedan al final sin aportar producto.
        descripcion = re.sub(r'\b(kg|kgs|kilo|kilos|unidad|unidades|ud|uds)\b$', '', descripcion, flags=re.I).strip()
        return _normalizar_espacios(descripcion)

    def _linea_a_producto(linea):
        original = _limpiar_linea(linea)
        if _es_linea_administrativa(original):
            return None

        tokens = _extraer_tokens_numericos(original)
        if len(tokens) < 2:
            return None

        # La mayoría de albaranes vienen como: descripcion cantidad precio total.
        # Tomamos los 3 últimos números como cantidad/precio/importe cuando cuadran.
        candidatos = []
        if len(tokens) >= 3:
            candidatos.append((tokens[-3], tokens[-2], tokens[-1]))
        if len(tokens) >= 2:
            candidatos.append((tokens[-2], None, tokens[-1]))

        mejor = None
        mejor_score = -1

        for cantidad_t, precio_t, importe_t in candidatos:
            cantidad = cantidad_t['valor']
            precio = precio_t['valor'] if precio_t else None
            importe = importe_t['valor']

            if cantidad is None or cantidad <= 0 or cantidad > 100000:
                continue
            if importe is None or importe <= 0 or importe > 1000000:
                continue

            if precio is None or precio <= 0:
                precio_calc = round(importe / cantidad, 6) if cantidad else None
                precio = precio_calc
            else:
                precio_calc = round(importe / cantidad, 6) if cantidad else None
                # Si no cuadra, puede ser una línea cantidad/total o un OCR desplazado.
                if precio_calc is not None:
                    diferencia = abs((cantidad * precio) - importe)
                    tolerancia = max(0.05, abs(importe) * 0.10)
                    if diferencia > tolerancia:
                        # Si precio detectado parece total o ruido, usar cálculo.
                        precio = precio_calc

            if precio is None or precio <= 0 or precio > 10000:
                continue

            descripcion_bruta = original[:cantidad_t['start']].strip()
            if len(descripcion_bruta) < 3:
                descripcion_bruta = original
                for t in [cantidad_t, precio_t, importe_t]:
                    if t:
                        descripcion_bruta = descripcion_bruta[:t['start']] + ' ' + descripcion_bruta[t['end']:]
                descripcion_bruta = _normalizar_espacios(descripcion_bruta)

            codigo, descripcion = _separar_codigo_nombre(descripcion_bruta)
            descripcion = _limpiar_descripcion_producto(descripcion)
            codigo = _normalizar_espacios(codigo)

            if len(descripcion) < 3 or not _tiene_letras(descripcion):
                continue

            desc_lower = _lower_plano(descripcion)
            if any(p in desc_lower for p in ['total', 'factura', 'tarjeta', 'cambio', 'base imponible']):
                continue

            # Score para elegir candidato más creíble.
            score = 0
            if codigo:
                score += 2
            if len(descripcion) >= 5:
                score += 2
            if precio_t:
                score += 1
            if abs(round(cantidad * precio, 2) - round(importe, 2)) <= max(0.05, importe * 0.02):
                score += 3

            item = {
                'referencia': codigo,
                'codigo_proveedor': codigo,
                'descripcion': descripcion,
                'cantidad': round(cantidad, 3),
                'precio': round(precio, 6),
                'importe': round(importe, 2),
                'precio_origen': 'documento_ocr',
                'linea_original': original,
                'estado_ocr': 'detectado'
            }

            if score > mejor_score:
                mejor_score = score
                mejor = item

        return mejor

    # Preprocesado: algunas herramientas parten una tabla en varias líneas. Aquí solo
    # procesamos líneas con estructura suficiente y descartamos cabeceras/totales.
    for linea in str(texto).splitlines():
        item = _linea_a_producto(linea)
        if item:
            resultado.append(item)

    # Deduplicación prudente por código/descripción/cantidad/importe.
    limpios = []
    vistos = set()
    for linea in resultado:
        clave = (
            (linea.get('referencia') or '').lower(),
            (linea.get('descripcion') or '').lower(),
            linea.get('cantidad'),
            linea.get('importe')
        )
        if clave in vistos:
            continue
        vistos.add(clave)
        limpios.append(linea)

    return limpios

def buscar_producto_por_texto(conn, descripcion, empresa_id, referencia=None):
    descripcion = (descripcion or '').strip()
    referencia = (referencia or '').strip()

    if referencia:
        for sql, params in [
            ("""
                SELECT producto_id AS id
                FROM productos_proveedores
                WHERE codigo_proveedor = ? AND activo = 1
                LIMIT 1
            """, (referencia,)),
            ("""
                SELECT id
                FROM productos
                WHERE empresa_id = ? AND (codigo = ? OR codigo_interno = ?)
                LIMIT 1
            """, (empresa_id, referencia, referencia))
        ]:
            try:
                row = conn.execute(sql, params).fetchone()
                if row:
                    return row['id']
            except Exception:
                pass

    palabras = [p.lower() for p in re.findall(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+', descripcion) if len(p) >= 3]
    if not palabras:
        return None

    productos = conn.execute("""
        SELECT id, nombre
        FROM productos
        WHERE empresa_id = ? AND activo = 1
    """, (empresa_id,)).fetchall()

    mejor_match = None
    mejor_score = 0
    for p in productos:
        nombre = (p['nombre'] or '').lower()
        score = sum(1 for palabra in palabras if palabra in nombre)
        if score > mejor_score:
            mejor_score = score
            mejor_match = p['id']

    return mejor_match if mejor_score >= 2 else None



def obtener_o_crear_proveedor(cursor, texto, empresa_id):
    import re

    lineas = texto.splitlines()

    for linea in lineas[:5]:  # primeras líneas = proveedor
        limpio = linea.strip()

        if len(limpio) < 5:
            continue

        if any(p in limpio.lower() for p in ['cif', 'factura', 'ticket']):
            continue

        nombre = re.sub(r'[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 ]', '', limpio).strip()

        if len(nombre) < 4:
            continue

        existente = cursor.execute("""
            SELECT id FROM proveedores
            WHERE nombre LIKE ?
            AND empresa_id = ?
            LIMIT 1
        """, (f"%{nombre}%", empresa_id)).fetchone()

        if existente:
            return existente['id']

        cursor.execute("""
            INSERT INTO proveedores (nombre, empresa_id)
            VALUES (?, ?)
        """, (nombre, empresa_id))

        return cursor.lastrowid

    return None

def obtener_o_crear_producto(cursor, descripcion, empresa_id, referencia=None):
    producto_id = buscar_producto_por_texto(cursor.connection, descripcion, empresa_id, referencia)
    if producto_id:
        return producto_id
    nombre = (descripcion or '').strip()
    if len(nombre) < 3:
        return None
    try:
        cursor.execute("""
            INSERT INTO productos (
                nombre, codigo, activo, empresa_id,
                visible_en_compras, visible_en_produccion,
                visible_en_venta, permite_compra_directa
            )
            VALUES (?, ?, 1, ?, 1, 0, 0, 1)
        """, (nombre, referencia or None, empresa_id))
    except Exception:
        cursor.execute("""
            INSERT INTO productos (nombre, codigo, activo, empresa_id)
            VALUES (?, ?, 1, ?)
        """, (nombre, referencia or None, empresa_id))
    return cursor.lastrowid

# ============================================
# RUTAS DE COMPRAS
# ============================================

@compras_bp.route('/pedidos')
@login_required
def lista_pedidos():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()
    
    proveedor_id = request.args.get('proveedor', '')
    estado = request.args.get('estado', '')
    fecha_desde = request.args.get('fecha_desde', '')
    fecha_hasta = request.args.get('fecha_hasta', '')
    tipo_fecha = request.args.get('tipo_fecha', 'pedido')
    
    query = """
        SELECT p.*, pr.nombre as proveedor_nombre,
               (SELECT COUNT(*) FROM lineas_pedido WHERE pedido_id = p.id) as num_lineas,
               (SELECT SUM(cantidad_solicitada * precio_unitario) FROM lineas_pedido WHERE pedido_id = p.id) as total_pedido
        FROM pedidos p
        JOIN proveedores pr ON p.proveedor_id = pr.id
        WHERE p.empresa_id = ?
    """
    params = [empresa_id]
    
    if proveedor_id:
        query += " AND p.proveedor_id = ?"
        params.append(proveedor_id)
    if estado:
        query += " AND p.estado = ?"
        params.append(estado)
    if fecha_desde:
        if tipo_fecha == 'pedido':
            query += " AND p.fecha_pedido >= ?"
        elif tipo_fecha == 'entrega':
            query += " AND p.fecha_entrega >= ?"
        params.append(fecha_desde)
    if fecha_hasta:
        if tipo_fecha == 'pedido':
            query += " AND p.fecha_pedido <= ?"
        elif tipo_fecha == 'entrega':
            query += " AND p.fecha_entrega <= ?"
        params.append(fecha_hasta)
    
    query += " ORDER BY p.fecha_pedido DESC"
    
    pedidos = conn.execute(query, params).fetchall()
    
    proveedores = conn.execute("SELECT id, nombre FROM proveedores WHERE activo = 1 AND empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    
    conn.close()
    
    return render_template('compras/pedidos.html', 
                         pedidos=pedidos,
                         proveedores=proveedores,
                         filtros={
                             'proveedor': proveedor_id,
                             'estado': estado,
                             'fecha_desde': fecha_desde,
                             'fecha_hasta': fecha_hasta,
                             'tipo_fecha': tipo_fecha
                         })

@compras_bp.route('/pedidos/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_pedido():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    if request.method == "POST":
        proveedor_id = request.form.get("proveedor_id")
        departamento_id = request.form.get("departamento_id")
        fecha_entrega = request.form.get("fecha_entrega", "")
        observaciones = request.form.get("observaciones", "")

        if not proveedor_id:
            conn.close()
            flash('Debes seleccionar proveedor.', 'warning')
            return redirect(url_for('compras.nuevo_pedido'))

        if not departamento_id:
            conn.close()
            flash('Debes seleccionar departamento.', 'warning')
            return redirect(url_for('compras.nuevo_pedido'))

        numero_pedido = generar_numero_pedido()

        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO pedidos (
                numero_pedido, proveedor_id, departamento_id, fecha_entrega,
                observaciones, creado_por, estado, empresa_id
            )
            VALUES (?, ?, ?, ?, ?, ?, 'borrador', ?)
        """, (
            numero_pedido,
            proveedor_id,
            departamento_id,
            fecha_entrega,
            observaciones,
            session.get('user_nombre', 'admin'),
            empresa_id
        ))

        pedido_id = cursor.lastrowid
        conn.commit()
        conn.close()

        flash(f' Pedido {numero_pedido} creado', 'success')
        return redirect(url_for('compras.editar_pedido', pedido_id=pedido_id))

    proveedores = conn.execute("""
        SELECT *
        FROM proveedores
        WHERE COALESCE(activo, 1) = 1
          AND empresa_id = ?
        ORDER BY nombre
    """, (empresa_id,)).fetchall()
    departamentos = _departamentos_activos(conn, empresa_id)

    conn.close()
    return render_template(
        'compras/nuevo_pedido.html',
        proveedores=proveedores,
        departamentos=departamentos
    )

@compras_bp.route('/pedidos/<int:pedido_id>')
@login_required
def ver_pedido(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()
    
    pedido = conn.execute("""
        SELECT p.*, pr.nombre as proveedor_nombre, pr.email, pr.telefono
        FROM pedidos p
        JOIN proveedores pr ON p.proveedor_id = pr.id
        WHERE p.id = ? AND p.empresa_id = ?
    """, (pedido_id, empresa_id)).fetchone()
    
    lineas = conn.execute("""
        SELECT l.*, prod.nombre, prod.unidad
        FROM lineas_pedido l
        JOIN productos prod ON l.producto_id = prod.id
        WHERE l.pedido_id = ? AND prod.empresa_id = ?
    """, (pedido_id, empresa_id)).fetchall()
    
    tiene_recepciones = any(float((l['cantidad_recibida'] or 0)) > 0 for l in lineas)
    estado = pedido['estado'] or 'borrador'
    acciones = {
        'puede_editar': estado == 'borrador',
        'puede_enviar_email': estado == 'borrador',
        'puede_eliminar': estado == 'borrador' and not tiene_recepciones,
        'puede_cancelar': estado == 'borrador' or (estado == 'enviado' and not tiene_recepciones),
        'puede_recibir': estado in ('enviado', 'recibido_parcial'),
        'puede_cerrar': estado == 'recibido',
        'puede_imprimir': True,
    }

    conn.close()
    return render_template('compras/ver_pedido.html', pedido=pedido, lineas=lineas, acciones=acciones)

@compras_bp.route('/pedidos/<int:pedido_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_pedido(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    
    if request.method == "POST":
        productos = request.form.getlist("producto_id[]")
        cantidades = request.form.getlist("cantidad[]")
        precios = request.form.getlist("precio[]")
        
        conn.execute("DELETE FROM lineas_pedido WHERE pedido_id = ?", (pedido_id,))
        
        for i in range(len(productos)):
            if productos[i] and cantidades[i] and float(cantidades[i]) > 0:
                conn.execute("""
                    INSERT INTO lineas_pedido (pedido_id, producto_id, cantidad_solicitada, precio_unitario)
                    VALUES (?, ?, ?, ?)
                """, (pedido_id, productos[i], float(cantidades[i]), float(precios[i])))
        
        conn.commit()
        conn.close()
        flash(' Pedido actualizado', 'success')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))
    
    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ? AND empresa_id = ?", (pedido_id, empresa_id)).fetchone()
    productos = conn.execute("SELECT id, nombre, codigo, precio FROM productos WHERE empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    lineas = conn.execute("""
        SELECT l.*, p.nombre, p.unidad
        FROM lineas_pedido l
        JOIN productos p ON l.producto_id = p.id
        WHERE l.pedido_id = ? AND p.empresa_id = ?
    """, (pedido_id, empresa_id)).fetchall()
    
    conn.close()
    return render_template('compras/editar_pedido.html', pedido=pedido, productos=productos, lineas=lineas)

@compras_bp.route('/pedidos/<int:pedido_id>/cambiar-estado/<string:estado>')
@login_required
def cambiar_estado_pedido(pedido_id, estado):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    
    conn.execute("UPDATE pedidos SET estado = ? WHERE id = ? AND empresa_id = ?", (estado, pedido_id, empresa_id))
    conn.commit()
    conn.close()
    flash(f' Estado cambiado a {estado}', 'success')
    return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))



@compras_bp.route('/pedidos/<int:pedido_id>/cancelar')
@login_required
def cancelar_pedido(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cursor = conn.cursor()

    pedido = cursor.execute(
        "SELECT * FROM pedidos WHERE id = ? AND empresa_id = ?",
        (pedido_id, empresa_id)
    ).fetchone()

    if not pedido:
        conn.close()
        flash(' Pedido no encontrado', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    tiene_recepciones = _pedido_tiene_recepciones(cursor, pedido_id)

    if pedido['estado'] == 'borrador':
        pass
    elif pedido['estado'] == 'enviado' and not tiene_recepciones:
        pass
    else:
        conn.close()
        flash(' Solo se puede cancelar un borrador o un pedido enviado sin recepciones', 'warning')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

    cursor.execute("""
        UPDATE pedidos
        SET estado = 'cancelado'
        WHERE id = ? AND empresa_id = ?
    """, (pedido_id, empresa_id))
    conn.commit()
    conn.close()

    flash(' Pedido cancelado correctamente', 'success')
    return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))


@compras_bp.route('/pedidos/<int:pedido_id>/cerrar')
@login_required
def cerrar_pedido(pedido_id):
    conn = get_db()

    conn.execute("""
        UPDATE pedidos
        SET estado = 'cerrado',
            cerrado = 1,
            fecha_cierre = datetime('now')
        WHERE id = ?
    """, (pedido_id,))

    conn.commit()
    conn.close()

    flash('Pedido cerrado correctamente', 'success')
    return redirect(url_for('compras.lista_pedidos'))


@compras_bp.route('/pedidos/<int:pedido_id>/eliminar')
@login_required
def eliminar_pedido(pedido_id):
    conn = get_db()

    pedido = conn.execute("SELECT estado FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()

    if pedido['estado'] != 'borrador':
        flash('Solo se pueden eliminar pedidos en borrador', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    conn.execute("DELETE FROM pedidos WHERE id = ?", (pedido_id,))
    conn.commit()
    conn.close()

    flash('Pedido eliminado', 'success')
    return redirect(url_for('compras.lista_pedidos'))

@compras_bp.route('/pedidos/<int:pedido_id>/imprimir')
@login_required
def imprimir_pedido(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    
    pedido = conn.execute("""
        SELECT p.*, pr.nombre as proveedor_nombre, pr.email, pr.telefono, pr.nif
        FROM pedidos p
        JOIN proveedores pr ON p.proveedor_id = pr.id
        WHERE p.id = ? AND p.empresa_id = ?
    """, (pedido_id, empresa_id)).fetchone()
    
    lineas = conn.execute("""
        SELECT l.*, prod.nombre, prod.unidad, prod.codigo, prod.referencia_proveedor
        FROM lineas_pedido l
        JOIN productos prod ON l.producto_id = prod.id
        WHERE l.pedido_id = ? AND prod.empresa_id = ?
    """, (pedido_id, empresa_id)).fetchall()
    
    conn.close()
    
    return render_template('compras/imprimir_pedido.html', pedido=pedido, lineas=lineas)

@compras_bp.route('/pedidos/<int:pedido_id>/enviar-email')
@login_required
def enviar_pedido_por_email(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    
    pedido = conn.execute("""
        SELECT p.*, pr.nombre as proveedor_nombre, pr.email
        FROM pedidos p
        JOIN proveedores pr ON p.proveedor_id = pr.id
        WHERE p.id = ? AND p.empresa_id = ?
    """, (pedido_id, empresa_id)).fetchone()
    
    lineas = conn.execute("""
        SELECT l.*, prod.nombre, prod.codigo
        FROM lineas_pedido l
        JOIN productos prod ON l.producto_id = prod.id
        WHERE l.pedido_id = ?
    """, (pedido_id,)).fetchall()
    conn.close()
    
    from flask_mail import Message
    from app import mail
    
    html = render_template('compras/pedido.html', pedido=pedido, lineas=lineas)
    
    try:
        msg = Message(
            subject=f'Pedido {pedido["numero_pedido"]}',
            recipients=[pedido['email']],
            html=html
        )
        mail.send(msg)
        conn = get_db()
        conn.execute("""
            UPDATE pedidos
            SET estado = 'enviado', fecha_envio = ?
            WHERE id = ? AND empresa_id = ?
        """, (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), pedido_id, empresa_id))
        conn.commit()
        conn.close()
        flash(' Email enviado al proveedor y pedido marcado como enviado', 'success')
    except Exception as e:
        flash(f' Error al enviar email: {str(e)}', 'danger')
    
    return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

# ============================================
# RECEPCIÓN DE PEDIDOS
# ============================================

def _generar_lote_interno(producto_id, fecha_recepcion):
    fecha_txt = (fecha_recepcion or datetime.now().strftime("%Y-%m-%d")).replace('-', '')
    return f"INT-{producto_id}-{fecha_txt}"



def _asegurar_columna_recepcion_stock_aplicado(cursor):
    """Añade stock_aplicado si no existe. Evita duplicar entradas de stock al validar."""
    columnas = [row[1] for row in cursor.execute("PRAGMA table_info(recepciones)").fetchall()]
    if 'stock_aplicado' not in columnas:
        cursor.execute("ALTER TABLE recepciones ADD COLUMN stock_aplicado INTEGER DEFAULT 0")


def _crear_incidencia_recepcion(cursor, empresa_id, recepcion_id, usuario, tipo_txt, descripcion_txt, gravedad='media'):
    descripcion_final = f"{tipo_txt}: {descripcion_txt}".strip(': ')
    cursor.execute("""
        INSERT INTO incidencias (tipo, referencia_id, usuario, descripcion, gravedad, estado, empresa_id)
        VALUES ('recepcion', ?, ?, ?, ?, 'abierta', ?)
    """, (recepcion_id, usuario, descripcion_final, gravedad, empresa_id))
    return cursor.lastrowid


def _aplicar_stock_recepcion(cursor, recepcion_id, empresa_id, usuario='sistema'):
    """
    Aplica stock/lotes SOLO cuando la recepción ya ha pasado por validación.
    La recepción operativa guarda líneas e incidencias, pero NO toca stock real.
    """
    _asegurar_columna_recepcion_stock_aplicado(cursor)

    recepcion = cursor.execute("""
        SELECT r.*, p.numero_pedido
        FROM recepciones r
        LEFT JOIN pedidos p ON p.id = r.pedido_id
        WHERE r.id = ? AND r.empresa_id = ?
    """, (recepcion_id, empresa_id)).fetchone()

    if not recepcion:
        return 0

    if int(recepcion['stock_aplicado'] or 0) == 1:
        return 0

    lineas = cursor.execute("""
        SELECT lr.*, prod.nombre AS producto_nombre
        FROM lineas_recepcion lr
        JOIN productos prod ON prod.id = lr.producto_id
        WHERE lr.recepcion_id = ?
          AND (lr.empresa_id = ? OR lr.empresa_id IS NULL)
          AND COALESCE(lr.cantidad_recibida, 0) > 0
          AND COALESCE(lr.aceptado, 1) = 1
          AND lr.almacen_id IS NOT NULL
    """, (recepcion_id, empresa_id)).fetchall()

    aplicadas = 0
    for linea in lineas:
        producto_id = linea['producto_id']
        almacen_id = linea['almacen_id']
        cantidad = float(linea['cantidad_recibida'] or 0)
        if cantidad <= 0:
            continue

        stock = cursor.execute("""
            SELECT id, cantidad
            FROM stock_almacen
            WHERE producto_id = ? AND almacen_id = ?
        """, (producto_id, almacen_id)).fetchone()

        if stock:
            cursor.execute("""
                UPDATE stock_almacen
                SET cantidad = COALESCE(cantidad, 0) + ?
                WHERE id = ?
            """, (cantidad, stock['id']))
        else:
            cursor.execute("""
                INSERT INTO stock_almacen (producto_id, almacen_id, cantidad, stock_minimo)
                VALUES (?, ?, ?, 0)
            """, (producto_id, almacen_id, cantidad))

        documento = recepcion['numero_albaran'] or recepcion['numero_pedido'] or f"RECEP-{recepcion_id}"
        cursor.execute("""
            INSERT INTO movimientos_stock (producto_id, almacen_id, tipo, cantidad, motivo, usuario, documento, empresa_id)
            VALUES (?, ?, 'entrada', ?, ?, ?, ?, ?)
        """, (producto_id, almacen_id, cantidad, 'Recepción validada', usuario, documento, empresa_id))

        codigo_lote = linea['lote'] or f"REC-{recepcion_id}-{producto_id}-{linea['id']}"
        documento_origen = f"Recepción {recepcion_id}"
        if recepcion['numero_pedido']:
            documento_origen = f"Pedido {recepcion['numero_pedido']}"

        cursor.execute("""
            INSERT INTO lotes (
                codigo_lote, producto_id, cantidad_inicial, cantidad_actual, fecha_recepcion,
                fecha_caducidad, proveedor_id, almacen_id, temperatura_recepcion, lote_proveedor,
                albaran, estado, observaciones, empresa_id, responsable_recepcion,
                incidencia_id, documento_origen, notas_trazabilidad
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'activo', ?, ?, ?, ?, ?, ?)
        """, (
            codigo_lote, producto_id, cantidad, cantidad,
            recepcion['fecha_recepcion'] or datetime.now().strftime('%Y-%m-%d'),
            linea['fecha_caducidad'], recepcion['proveedor_id'], almacen_id,
            linea['temperatura'], linea['lote'], recepcion['numero_albaran'],
            recepcion['observaciones'], empresa_id, recepcion['recibido_por'] or usuario,
            linea['incidencia_id'], documento_origen,
            'Stock aplicado tras validación económica/administrativa'
        ))
        aplicadas += 1

    cursor.execute("UPDATE recepciones SET stock_aplicado = 1 WHERE id = ?", (recepcion_id,))
    return aplicadas



def _ajustar_stock_almacen(cursor, producto_id, almacen_id, diferencia, empresa_id, usuario, documento, motivo):
    """Ajusta stock por diferencia. Positivo suma, negativo resta. Siempre deja movimiento trazable."""
    if not producto_id or not almacen_id:
        return

    diferencia = _to_float_safe(diferencia, 0.0)
    if not diferencia:
        return

    stock = cursor.execute("""
        SELECT id, cantidad
        FROM stock_almacen
        WHERE producto_id = ? AND almacen_id = ?
    """, (producto_id, almacen_id)).fetchone()

    if stock:
        cursor.execute("""
            UPDATE stock_almacen
            SET cantidad = COALESCE(cantidad, 0) + ?
            WHERE id = ?
        """, (diferencia, stock['id']))
    else:
        cursor.execute("""
            INSERT INTO stock_almacen (producto_id, almacen_id, cantidad, stock_minimo)
            VALUES (?, ?, ?, 0)
        """, (producto_id, almacen_id, diferencia))

    tipo_mov = 'entrada' if diferencia > 0 else 'salida'
    cursor.execute("""
        INSERT INTO movimientos_stock (producto_id, almacen_id, tipo, cantidad, motivo, usuario, documento, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        producto_id,
        almacen_id,
        tipo_mov,
        abs(diferencia),
        motivo,
        usuario,
        documento,
        empresa_id
    ))


def _ajustar_lote_recepcion_linea(cursor, recepcion, linea, cantidad_nueva, almacen_nuevo, lote_nuevo, caducidad_nueva, temperatura_nueva):
    """Actualiza el lote creado por la recepción cuando se edita la línea."""
    producto_id = linea['producto_id']
    linea_id = linea['id']
    codigo_lote_anterior = linea['lote'] or f"REC-{recepcion['id']}-{producto_id}-{linea_id}"
    codigo_lote_nuevo = lote_nuevo or f"REC-{recepcion['id']}-{producto_id}-{linea_id}"

    lote = cursor.execute("""
        SELECT id
        FROM lotes
        WHERE producto_id = ?
          AND codigo_lote = ?
          AND empresa_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (producto_id, codigo_lote_anterior, recepcion['empresa_id'])).fetchone()

    if lote:
        cursor.execute("""
            UPDATE lotes
            SET codigo_lote = ?,
                cantidad_inicial = ?,
                cantidad_actual = ?,
                almacen_id = ?,
                fecha_caducidad = ?,
                temperatura_recepcion = ?,
                lote_proveedor = ?
            WHERE id = ?
        """, (
            codigo_lote_nuevo,
            cantidad_nueva,
            cantidad_nueva,
            almacen_nuevo,
            caducidad_nueva,
            temperatura_nueva,
            lote_nuevo or None,
            lote['id']
        ))


def _recalcular_cantidad_recibida_pedido(cursor, pedido_id):
    """Recalcula lineas_pedido.cantidad_recibida desde las recepciones reales del pedido."""
    if not pedido_id:
        return

    lineas = cursor.execute("""
        SELECT id, producto_id
        FROM lineas_pedido
        WHERE pedido_id = ?
    """, (pedido_id,)).fetchall()

    for linea in lineas:
        total = cursor.execute("""
            SELECT SUM(COALESCE(lr.cantidad_recibida, 0)) AS total
            FROM lineas_recepcion lr
            JOIN recepciones r ON r.id = lr.recepcion_id
            WHERE r.pedido_id = ?
              AND lr.producto_id = ?
              AND COALESCE(lr.aceptado, 1) = 1
        """, (pedido_id, linea['producto_id'])).fetchone()
        cursor.execute("""
            UPDATE lineas_pedido
            SET cantidad_recibida = ?
            WHERE id = ?
        """, (_to_float_safe(total['total'], 0.0) if total else 0.0, linea['id']))


def _procesar_linea_recepcion(cursor, empresa_id, pedido, recepcion_id, linea, form_data, usuario, numero_albaran, fecha_recepcion, observaciones_generales):
    pendiente = float(linea['cantidad_solicitada'] or 0) - float(linea['cantidad_recibida'] or 0)
    if pendiente <= 0:
        return False

    cantidad_recibida = float(form_data.get(f"cantidad_{linea['id']}", 0) or 0)
    if cantidad_recibida <= 0:
        return False

    if cantidad_recibida > pendiente:
        cantidad_recibida = pendiente

    lote_proveedor = (form_data.get(f"lote_{linea['id']}") or '').strip()
    fecha_caducidad = (form_data.get(f"caducidad_{linea['id']}") or '').strip() or None
    temperatura_txt = (form_data.get(f"temperatura_{linea['id']}") or '').strip()
    precio_txt = (form_data.get(f"precio_{linea['id']}") or '').strip()
    almacen_txt = (form_data.get(f"almacen_{linea['id']}") or '').strip()
    incidencia_marcada = form_data.get(f"incidencia_{linea['id']}")
    incidencia_tipo = (form_data.get(f"incidencia_tipo_{linea['id']}") or '').strip()
    incidencia_texto = (form_data.get(f"incidencia_texto_{linea['id']}") or '').strip()

    temperatura = float(temperatura_txt) if temperatura_txt else None
    precio_unitario = float(precio_txt) if precio_txt else float(linea['precio_unitario'] or 0)
    almacen_id = int(almacen_txt) if almacen_txt else None
    importe_total = round(cantidad_recibida * precio_unitario, 2)

    incidencia_id = None
    gravedad = 'media'
    if incidencia_marcada:
        if not incidencia_tipo:
            incidencia_tipo = 'Incidencia de recepción'
        if not incidencia_texto:
            incidencia_texto = f"Incidencia detectada al recibir {linea['nombre']}"
        if incidencia_tipo == 'Temperatura incorrecta':
            gravedad = 'alta'
        incidencia_id = _crear_incidencia_recepcion(cursor, empresa_id, recepcion_id, usuario, incidencia_tipo, incidencia_texto, gravedad=gravedad)

    cursor.execute("""
        INSERT INTO lineas_recepcion (
            recepcion_id, producto_id, cantidad_recibida, lote, fecha_caducidad,
            temperatura, almacen_id, precio_unitario, importe_total, aceptado,
            motivo_rechazo, incidencia_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        recepcion_id, linea['producto_id'], cantidad_recibida, lote_proveedor or None,
        fecha_caducidad, temperatura, almacen_id, precio_unitario, importe_total,
        0 if incidencia_marcada else 1,
        incidencia_texto if incidencia_marcada else None,
        incidencia_id
    ))

    cursor.execute("""
        UPDATE lineas_pedido
        SET cantidad_recibida = COALESCE(cantidad_recibida, 0) + ?
        WHERE id = ?
    """, (cantidad_recibida, linea['id']))

    if almacen_id:
        existe = cursor.execute("""
            SELECT id FROM stock_almacen
            WHERE producto_id = ? AND almacen_id = ?
        """, (linea['producto_id'], almacen_id)).fetchone()

        if existe:
            cursor.execute("""
                UPDATE stock_almacen
                SET cantidad = COALESCE(cantidad, 0) + ?
                WHERE producto_id = ? AND almacen_id = ?
            """, (cantidad_recibida, linea['producto_id'], almacen_id))
        else:
            cursor.execute("""
                INSERT INTO stock_almacen (producto_id, almacen_id, cantidad)
                VALUES (?, ?, ?)
            """, (linea['producto_id'], almacen_id, cantidad_recibida))

        cursor.execute("""
            INSERT INTO movimientos_stock (producto_id, almacen_id, tipo, cantidad, motivo, usuario, documento, empresa_id)
            VALUES (?, ?, 'entrada', ?, ?, ?, ?, ?)
        """, (
            linea['producto_id'], almacen_id, cantidad_recibida,
            f"Recepción pedido {pedido['numero_pedido']}", usuario,
            numero_albaran or pedido['numero_pedido'], empresa_id
        ))

    codigo_lote = lote_proveedor or _generar_lote_interno(linea['producto_id'], fecha_recepcion)
    notas_trazabilidad = observaciones_generales or f"Recepción desde pedido {pedido['numero_pedido']}"
    cursor.execute("""
        INSERT INTO lotes (
            codigo_lote, producto_id, cantidad_inicial, cantidad_actual, fecha_recepcion,
            fecha_caducidad, proveedor_id, almacen_id, temperatura_recepcion,
            lote_proveedor, albaran, observaciones, empresa_id, responsable_recepcion,
            incidencia_id, documento_origen, notas_trazabilidad
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        codigo_lote, linea['producto_id'], cantidad_recibida, cantidad_recibida,
        fecha_recepcion or datetime.now().strftime('%Y-%m-%d'), fecha_caducidad,
        pedido['proveedor_id'], almacen_id, temperatura, lote_proveedor or None,
        numero_albaran or None, observaciones_generales or None, empresa_id, usuario,
        incidencia_id, f"Pedido {pedido['id']}", notas_trazabilidad
    ))

    return True


def _obtener_resumen_recepcion_por_producto(conn, pedido_id):
    rows = conn.execute("""
        SELECT
            lr.*,
            r.fecha_recepcion,
            r.numero_albaran,
            r.recibido_por,
            a.nombre AS almacen_nombre,
            prod.nombre AS producto_nombre
        FROM lineas_recepcion lr
        JOIN recepciones r ON lr.recepcion_id = r.id
        JOIN productos prod ON lr.producto_id = prod.id
        LEFT JOIN almacenes a ON lr.almacen_id = a.id
        WHERE r.pedido_id = ?
        ORDER BY r.id DESC, lr.id DESC
    """, (pedido_id,)).fetchall()

    resumen = {}
    for row in rows:
        pid = row['producto_id']
        resumen.setdefault(pid, []).append(row)
    return resumen


def _obtener_stock_actual_por_producto(conn, producto_ids):
    if not producto_ids:
        return {}
    placeholders = ','.join(['?'] * len(producto_ids))
    rows = conn.execute(f"""
        SELECT producto_id, SUM(COALESCE(cantidad, 0)) AS total_stock
        FROM stock_almacen
        WHERE producto_id IN ({placeholders})
        GROUP BY producto_id
    """, tuple(producto_ids)).fetchall()
    return {row['producto_id']: row['total_stock'] for row in rows}


def _obtener_lotes_por_producto(conn, pedido_id):
    rows = conn.execute("""
        SELECT
            l.*,
            a.nombre AS almacen_nombre,
            prod.nombre AS producto_nombre
        FROM lotes l
        JOIN productos prod ON l.producto_id = prod.id
        LEFT JOIN almacenes a ON l.almacen_id = a.id
        WHERE l.documento_origen = ?
        ORDER BY l.id DESC
    """, (f"Pedido {pedido_id}",)).fetchall()
    # compatibilidad con datos anteriores generados usando numero_pedido
    if not rows:
        rows = conn.execute("""
            SELECT
                l.*,
                a.nombre AS almacen_nombre,
                prod.nombre AS producto_nombre
            FROM lotes l
            JOIN productos prod ON l.producto_id = prod.id
            LEFT JOIN almacenes a ON l.almacen_id = a.id
            WHERE l.documento_origen LIKE 'Pedido %'
            ORDER BY l.id DESC
        """).fetchall()
    resumen = {}
    for row in rows:
        resumen.setdefault(row['producto_id'], []).append(row)
    return resumen


@compras_bp.route('/recepcion', methods=['GET', 'POST'])
@login_required
def recepcion_pedido():
    if request.method == "POST":
        numero_pedido = (request.form.get("numero_pedido") or '').strip()
        if numero_pedido:
            return redirect(url_for('compras.recibir_pedido', numero_pedido=numero_pedido))

    return render_template("compras/buscar_recepcion.html")


@compras_bp.route('/recepcion/<string:numero_pedido>', methods=['GET', 'POST'])
@login_required
def recibir_pedido(numero_pedido):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    usuario = (session.get('user_nombre') or 'admin').strip()
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    pedido = conn.execute("""
        SELECT p.*, pr.nombre AS proveedor_nombre, d.nombre AS departamento_nombre
        FROM pedidos p
        JOIN proveedores pr ON p.proveedor_id = pr.id
        LEFT JOIN departamentos d ON d.id = p.departamento_id
        WHERE p.numero_pedido = ? AND p.empresa_id = ?
    """, (numero_pedido, empresa_id)).fetchone()

    if not pedido:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    lineas = conn.execute("""
        SELECT l.*, prod.nombre, prod.unidad, prod.codigo, prod.tipo_conservacion
        FROM lineas_pedido l
        JOIN productos prod ON l.producto_id = prod.id
        WHERE l.pedido_id = ? AND prod.empresa_id = ?
        ORDER BY prod.nombre
    """, (pedido['id'], empresa_id)).fetchall()

    almacenes = conn.execute("""
        SELECT id, nombre
        FROM almacenes
        WHERE activo = 1 AND empresa_id = ?
        ORDER BY nombre
    """, (empresa_id,)).fetchall()

    total_pendiente = sum(
        max(_to_float_safe(l['cantidad_solicitada']) - _to_float_safe(l['cantidad_recibida']), 0.0)
        for l in lineas
    )
    estado_pedido = (pedido['estado'] or '').strip().lower()

    if request.method == 'GET' and (estado_pedido in ('recibido', 'cerrado', 'cancelado') or total_pendiente <= 0):
        conn.close()
        flash('Este pedido ya no tiene cantidades pendientes para recepcionar.', 'info')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido['id']))

    if request.method == 'POST':
        accion = (request.form.get('accion_recepcion') or 'guardar').strip().lower()
        numero_albaran = (request.form.get('numero_albaran') or '').strip()
        fecha_recepcion = (request.form.get('fecha_recepcion') or datetime.now().strftime('%Y-%m-%d')).strip()
        recibido_por = (request.form.get('recibido_por') or usuario).strip()
        observaciones = (request.form.get('observaciones') or '').strip()
        documento_id_form = _to_int_or_none(request.form.get('documento_id'))

        if accion == 'leer':
            documento = request.files.get('documento_compra')
            if not documento or not (documento.filename or '').strip():
                conn.close()
                flash('Selecciona un albarán, foto o PDF para leer.', 'warning')
                return redirect(url_for('compras.recibir_pedido', numero_pedido=numero_pedido))

            try:
                cur = conn.cursor()
                _asegurar_estructura_compras(cur)
                documento_id = guardar_documento_compra_en_cursor(
                    cur,
                    documento,
                    proveedor_id=pedido['proveedor_id'],
                    pedido_id=pedido['id'],
                    recepcion_id=None,
                    tipo_documento='albaran',
                    origen_subida='lectura_recepcion_pedido'
                )

                resultado = procesar_documento_compra_ocr_en_cursor(
                    cur,
                    documento_id,
                    empresa_id,
                    proveedor_id=pedido['proveedor_id']
                )

                texto_ocr = resultado.get('texto') or ''
                albaran_detectado = detectar_numero_albaran_desde_texto(texto_ocr) or numero_albaran

                lineas_doc = cur.execute("""
                    SELECT *
                    FROM documento_compra_lineas_detectadas
                    WHERE documento_id = ?
                    ORDER BY id
                """, (documento_id,)).fetchall()

                incidencias_preview = []
                cantidades_ocr = {}

                for ld in lineas_doc:
                    producto_id = ld['producto_id']
                    cantidad_doc = _to_float_safe(ld['cantidad_detectada'])

                    if not producto_id:
                        incidencias_preview.append({
                            'tipo': 'Producto no reconocido',
                            'descripcion': ld['descripcion_detectada'] or ''
                        })
                        continue

                    cantidades_ocr[producto_id] = cantidades_ocr.get(producto_id, 0.0) + cantidad_doc

                    pedido_linea = cur.execute("""
                        SELECT lp.*, prod.nombre
                        FROM lineas_pedido lp
                        JOIN productos prod ON prod.id = lp.producto_id
                        WHERE lp.pedido_id = ? AND lp.producto_id = ?
                        LIMIT 1
                    """, (pedido['id'], producto_id)).fetchone()

                    if not pedido_linea:
                        incidencias_preview.append({
                            'tipo': 'Producto no pedido',
                            'descripcion': ld['descripcion_detectada'] or ''
                        })
                        continue

                    pendiente = max(
                        _to_float_safe(pedido_linea['cantidad_solicitada']) - _to_float_safe(pedido_linea['cantidad_recibida']),
                        0.0
                    )
                    if cantidad_doc < pendiente:
                        incidencias_preview.append({
                            'tipo': 'Falta unidades',
                            'descripcion': f"{pedido_linea['nombre']}: pedido pendiente {pendiente} / albarán {cantidad_doc}"
                        })
                    elif cantidad_doc > pendiente:
                        incidencias_preview.append({
                            'tipo': 'Exceso unidades',
                            'descripcion': f"{pedido_linea['nombre']}: pedido pendiente {pendiente} / albarán {cantidad_doc}"
                        })

                conn.commit()
                flash(
                    f"OCR finalizado: {resultado.get('lineas', 0)} línea(s), "
                    f"{resultado.get('asociadas', 0)} asociada(s), "
                    f"{len(incidencias_preview)} aviso(s). Revisa antes de guardar.",
                    'success'
                )
                conn.close()
                return render_template(
                    'compras/recibir_pedido.html',
                    pedido=pedido,
                    lineas=lineas,
                    almacenes=almacenes,
                    now=datetime.now(),
                    documento_id=documento_id,
                    albaran_detectado=albaran_detectado,
                    texto_ocr=texto_ocr,
                    incidencias_preview=incidencias_preview,
                    cantidades_ocr=cantidades_ocr
                )
            except Exception as e:
                conn.rollback()
                conn.close()
                flash(f'Error leyendo documento: {str(e)}', 'danger')
                return redirect(url_for('compras.recibir_pedido', numero_pedido=numero_pedido))

        if estado_pedido in ('recibido', 'cerrado', 'cancelado') or total_pendiente <= 0:
            conn.close()
            flash('Este pedido ya está recibido/cerrado o no tiene cantidades pendientes.', 'warning')
            return redirect(url_for('compras.ver_pedido', pedido_id=pedido['id']))

        cerrar_pedido_final = accion == 'guardar_cerrar_pendiente'
        recibir_todo = accion == 'recibir_todo'

        try:
            cur = conn.cursor()
            _asegurar_estructura_compras(cur)
            documento_adjunto_id = documento_id_form

            cur.execute("""
                INSERT INTO recepciones (
                    pedido_id, proveedor_id, departamento_id, fecha_recepcion,
                    numero_albaran, observaciones, recibido_por, empresa_id,
                    tipo_recepcion, validada_operativamente, validada_economicamente,
                    stock_aplicado, documento_adjunto_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pedido', 1, 0, 0, ?)
            """, (
                pedido['id'],
                pedido['proveedor_id'],
                pedido['departamento_id'],
                fecha_recepcion,
                numero_albaran,
                observaciones,
                recibido_por,
                empresa_id,
                documento_adjunto_id
            ))
            recepcion_id = cur.lastrowid

            if documento_adjunto_id:
                cur.execute("""
                    UPDATE documentos_compra
                    SET recepcion_id = ?, proveedor_id = ?, pedido_id = ?
                    WHERE id = ?
                """, (recepcion_id, pedido['proveedor_id'], pedido['id'], documento_adjunto_id))
            else:
                documento = request.files.get('documento_compra')
                if documento and (documento.filename or '').strip():
                    documento_adjunto_id = guardar_documento_compra_en_cursor(
                        cur,
                        documento,
                        proveedor_id=pedido['proveedor_id'],
                        pedido_id=pedido['id'],
                        recepcion_id=recepcion_id,
                        tipo_documento='albaran',
                        origen_subida='recepcion_pedido'
                    )
                    if documento_adjunto_id:
                        procesar_documento_compra_ocr_en_cursor(
                            cur,
                            documento_adjunto_id,
                            empresa_id,
                            proveedor_id=pedido['proveedor_id']
                        )
                        if not numero_albaran:
                            doc_row = cur.execute(
                                'SELECT texto_extraido FROM documentos_compra WHERE id = ?',
                                (documento_adjunto_id,)
                            ).fetchone()
                            albaran_detectado = detectar_numero_albaran_desde_texto(doc_row['texto_extraido'] if doc_row else '')
                            if albaran_detectado:
                                numero_albaran = albaran_detectado
                                cur.execute('UPDATE recepciones SET numero_albaran = ? WHERE id = ?', (numero_albaran, recepcion_id))
                        cur.execute('UPDATE recepciones SET documento_adjunto_id = ? WHERE id = ?', (documento_adjunto_id, recepcion_id))

            lineas_guardadas = 0
            incidencias_guardadas = 0

            for l in lineas:
                pendiente = max(_to_float_safe(l['cantidad_solicitada']) - _to_float_safe(l['cantidad_recibida']), 0.0)
                if pendiente <= 0:
                    continue

                cantidad_raw = request.form.get(f'cantidad_{l["id"]}')
                if recibir_todo and not (cantidad_raw or '').strip():
                    cantidad_raw = str(pendiente)
                cantidad = _to_float_safe(cantidad_raw)
                if cantidad > pendiente and not cerrar_pedido_final:
                    cantidad = pendiente

                estado_appcc = (request.form.get(f'estado_{l["id"]}') or 'ok').strip()
                incidencia_texto = (request.form.get(f'incidencia_texto_{l["id"]}') or '').strip()
                tiene_incidencia = estado_appcc != 'ok'

                if cantidad <= 0 and not tiene_incidencia:
                    continue

                almacen_id = _to_int_or_none(request.form.get(f'almacen_{l["id"]}'))
                if cantidad > 0 and not almacen_id:
                    conn.rollback()
                    conn.close()
                    flash(f'Debes indicar almacén en la línea: {l["nombre"]}.', 'warning')
                    return redirect(url_for('compras.recibir_pedido', numero_pedido=numero_pedido))

                lote = (request.form.get(f'lote_{l["id"]}') or '').strip()
                caducidad = (request.form.get(f'caducidad_{l["id"]}') or '').strip() or None
                temperatura_raw = (request.form.get(f'temperatura_{l["id"]}') or '').strip()
                temperatura = _to_float_safe(temperatura_raw, 0.0) if temperatura_raw else None
                precio_unitario = _to_float_safe(request.form.get(f'precio_{l["id"]}'), _to_float_safe(l['precio_unitario']))

                incidencia_id = None
                aceptado = 1
                motivo_rechazo = None

                if cantidad < pendiente and cerrar_pedido_final and not incidencia_texto:
                    tiene_incidencia = True
                    estado_appcc = 'falta_unidades'
                    incidencia_texto = f"Pedido cerrado con faltante. Pendiente {pendiente}, recibido {cantidad}."

                if tiene_incidencia:
                    tipo_map = {
                        'falta_unidades': 'Falta de unidades',
                        'falta_peso': 'Falta de peso',
                        'rotura': 'Rotura',
                        'mal_estado': 'Mal estado',
                        'temperatura_incorrecta': 'Temperatura incorrecta'
                    }
                    incidencia_tipo = tipo_map.get(estado_appcc, 'Incidencia en recepción')
                    if not incidencia_texto:
                        incidencia_texto = f'{incidencia_tipo} en {l["nombre"]} durante recepción {numero_pedido}.'
                    motivo_rechazo = incidencia_texto
                    if estado_appcc in ('rotura', 'mal_estado', 'temperatura_incorrecta'):
                        aceptado = 0
                    incidencia_id = _crear_incidencia_recepcion(
                        cur,
                        empresa_id,
                        recepcion_id,
                        recibido_por,
                        incidencia_tipo,
                        incidencia_texto,
                        gravedad='alta' if aceptado == 0 else 'media'
                    )
                    incidencias_guardadas += 1

                importe_total = round(cantidad * precio_unitario, 2) if precio_unitario else None
                cur.execute("""
                    INSERT INTO lineas_recepcion (
                        recepcion_id, producto_id, cantidad_recibida, lote,
                        fecha_caducidad, temperatura, almacen_id, precio_unitario,
                        importe_total, aceptado, motivo_rechazo, incidencia_id,
                        empresa_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    recepcion_id,
                    l['producto_id'],
                    cantidad,
                    lote or None,
                    caducidad,
                    temperatura,
                    almacen_id,
                    precio_unitario,
                    importe_total,
                    aceptado,
                    motivo_rechazo,
                    incidencia_id,
                    empresa_id
                ))
                lineas_guardadas += 1

                if cantidad > 0:
                    cur.execute(
                        'UPDATE lineas_pedido SET cantidad_recibida = COALESCE(cantidad_recibida,0) + ? WHERE id = ?',
                        (cantidad, l['id'])
                    )

            if lineas_guardadas == 0:
                conn.rollback()
                conn.close()
                flash('No se ha guardado ninguna línea ni incidencia.', 'warning')
                return redirect(url_for('compras.recibir_pedido', numero_pedido=numero_pedido))

            if cerrar_pedido_final:
                cur.execute("""
                    UPDATE pedidos
                    SET estado = 'cerrado', cerrado = 1, fecha_cierre = datetime('now'),
                        estado_recepcion = 'cerrado', fecha_recepcion = ?
                    WHERE id = ? AND empresa_id = ?
                """, (fecha_recepcion, pedido['id'], empresa_id))
            else:
                _recalcular_estado_pedido(cur, pedido['id'], empresa_id)
                cur.execute("""
                    UPDATE pedidos
                    SET fecha_recepcion = ?, estado_recepcion = estado
                    WHERE id = ? AND empresa_id = ?
                """, (fecha_recepcion, pedido['id'], empresa_id))

            aplicadas = _aplicar_stock_recepcion(cur, recepcion_id, empresa_id, recibido_por)
            conn.commit()
            conn.close()

            mensaje = f'Recepción guardada: {lineas_guardadas} línea(s), {incidencias_guardadas} incidencia(s), stock aplicado en {aplicadas} línea(s).'
            if cerrar_pedido_final:
                mensaje += ' Pedido cerrado manualmente.'
            flash(mensaje, 'success')
            return redirect(url_for('compras.validar_recepciones'))

        except Exception as e:
            conn.rollback()
            conn.close()
            flash(f'Error registrando recepción: {str(e)}', 'danger')
            return redirect(url_for('compras.recibir_pedido', numero_pedido=numero_pedido))

    conn.close()
    return render_template(
        'compras/recibir_pedido.html',
        pedido=pedido,
        lineas=lineas,
        almacenes=almacenes,
        now=datetime.now(),
        documento_id=None,
        albaran_detectado=None,
        texto_ocr='',
        incidencias_preview=[],
        cantidades_ocr={}
    )

@compras_bp.route('/recepcion/<string:numero_pedido>/conformidad')
@login_required
def hoja_conformidad(numero_pedido):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    recepcion = conn.execute("""
        SELECT r.*, p.nombre as proveedor_nombre, p.nif, p.email, p.telefono,
               ped.numero_pedido, ped.fecha_pedido
        FROM recepciones r
        JOIN proveedores p ON r.proveedor_id = p.id
        JOIN pedidos ped ON r.pedido_id = ped.id
        WHERE ped.numero_pedido = ?
        ORDER BY r.fecha_recepcion DESC, r.id DESC
        LIMIT 1
    """, (numero_pedido,)).fetchone()

    if not recepcion:
        conn.close()
        flash('No existe recepción registrada para este pedido.', 'warning')
        return redirect(url_for('compras.recibir_pedido', numero_pedido=numero_pedido))

    lineas = conn.execute("""
        SELECT lr.*, prod.nombre as producto_nombre, prod.codigo, prod.unidad
        FROM lineas_recepcion lr
        JOIN productos prod ON lr.producto_id = prod.id
        WHERE lr.recepcion_id = ? AND prod.empresa_id = ?
    """, (recepcion['id'], empresa_id)).fetchall()

    conn.close()
    return render_template('compras/hoja_conformidad.html', recepcion=recepcion, lineas=lineas, now=datetime.now())


@compras_bp.route('/recepcion/<string:numero_pedido>/incidencias')
@login_required
def hoja_incidencias_recepcion(numero_pedido):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    pedido = conn.execute("""
        SELECT p.*, pr.nombre as proveedor_nombre
        FROM pedidos p
        JOIN proveedores pr ON p.proveedor_id = pr.id
        WHERE p.numero_pedido = ? AND p.empresa_id = ?
    """, (numero_pedido, empresa_id)).fetchone()

    if not pedido:
        conn.close()
        flash('Pedido no encontrado', 'danger')
        return redirect(url_for('compras.recepcion_pedido'))

    incidencias = conn.execute("""
        SELECT i.*, lr.producto_id, prod.nombre as producto_nombre, r.fecha_recepcion, r.numero_albaran
        FROM incidencias i
        LEFT JOIN lineas_recepcion lr ON lr.incidencia_id = i.id
        LEFT JOIN recepciones r ON lr.recepcion_id = r.id
        LEFT JOIN productos prod ON lr.producto_id = prod.id
        WHERE r.pedido_id = ? AND i.empresa_id = ?
        ORDER BY i.id DESC
    """, (pedido['id'], empresa_id)).fetchall()

    conn.close()
    return render_template('compras/hoja_incidencias_recepcion.html', pedido=pedido, incidencias=incidencias, now=datetime.now())

# ============================================
# COMPRAS - OPCIN A (Pedido por lista de productos)
# ============================================
@compras_bp.route('/pedidos/nuevo-tipo-a', methods=['GET', 'POST'])
@login_required
def nuevo_pedido_tipo_a():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    if request.method == "POST":
        departamento_id = request.form.get("departamento_id")
        productos_seleccionados = request.form.getlist("producto_id[]")
        cantidades = request.form.getlist("cantidad[]")
        fecha_entrega = request.form.get("fecha_entrega", "")
        observaciones = request.form.get("observaciones", "")

        if not departamento_id:
            conn.close()
            flash('Debes seleccionar departamento.', 'warning')
            return redirect(url_for('compras.nuevo_pedido_tipo_a'))

        pedidos_por_proveedor = {}

        for i in range(len(productos_seleccionados)):
            producto_raw = productos_seleccionados[i] if i < len(productos_seleccionados) else ''
            cantidad_raw = cantidades[i] if i < len(cantidades) else ''
            if not producto_raw or not cantidad_raw:
                continue

            cantidad = _to_float_safe(cantidad_raw, 0.0)
            if cantidad <= 0:
                continue

            producto_id = int(producto_raw)
            proveedor = conn.execute("""
                SELECT proveedor_id,
                       COALESCE(precio_vigente, precio_ultimo, precio_compra, precio, 0) AS precio
                FROM productos_proveedores
                WHERE producto_id = ?
                  AND COALESCE(activo, 1) = 1
                ORDER BY COALESCE(es_principal, 0) DESC, id DESC
                LIMIT 1
            """, (producto_id,)).fetchone()

            if proveedor and proveedor['proveedor_id']:
                proveedor_id = proveedor['proveedor_id']
                pedidos_por_proveedor.setdefault(proveedor_id, []).append({
                    'producto_id': producto_id,
                    'cantidad': cantidad,
                    'precio': _to_float_safe(proveedor['precio'], 0.0)
                })

        if not pedidos_por_proveedor:
            conn.close()
            flash('No se ha creado ningún pedido. Revisa que los productos tengan proveedor asociado.', 'warning')
            return redirect(url_for('compras.nuevo_pedido_tipo_a'))

        pedidos_creados = []
        fecha = datetime.now().strftime("%Y%m%d")
        cursor = conn.cursor()

        for proveedor_id, lineas in pedidos_por_proveedor.items():
            cursor.execute(
                "SELECT COUNT(*) as total FROM pedidos WHERE fecha_pedido = date('now') AND empresa_id = ?",
                (empresa_id,)
            )
            total_hoy = cursor.fetchone()['total'] + 1
            numero_pedido = f"PED-{fecha}-{total_hoy:03d}"

            cursor.execute("""
                INSERT INTO pedidos (
                    numero_pedido, proveedor_id, departamento_id, fecha_entrega,
                    observaciones, creado_por, estado, empresa_id
                )
                VALUES (?, ?, ?, ?, ?, ?, 'borrador', ?)
            """, (
                numero_pedido,
                proveedor_id,
                departamento_id,
                fecha_entrega,
                observaciones,
                session.get('user_nombre', 'admin'),
                empresa_id
            ))

            pedido_id = cursor.lastrowid
            pedidos_creados.append(pedido_id)

            for linea in lineas:
                cursor.execute("""
                    INSERT INTO lineas_pedido (pedido_id, producto_id, cantidad_solicitada, precio_unitario)
                    VALUES (?, ?, ?, ?)
                """, (pedido_id, linea['producto_id'], linea['cantidad'], linea['precio']))

        conn.commit()
        conn.close()

        if len(pedidos_creados) == 1:
            flash(' Pedido creado correctamente', 'success')
            return redirect(url_for('compras.ver_pedido', pedido_id=pedidos_creados[0]))

        flash(f' Se han creado {len(pedidos_creados)} pedidos (uno por proveedor)', 'success')
        return redirect(url_for('compras.lista_pedidos'))

    productos = conn.execute("""
        SELECT p.*,
               (SELECT proveedor_id
                FROM productos_proveedores
                WHERE producto_id = p.id
                  AND COALESCE(activo, 1) = 1
                ORDER BY COALESCE(es_principal, 0) DESC, id DESC
                LIMIT 1) as proveedor_principal_id
        FROM productos p
        WHERE COALESCE(p.activo, 1) = 1
          AND p.empresa_id = ?
        ORDER BY p.familia, p.nombre
    """, (empresa_id,)).fetchall()

    departamentos = _departamentos_activos(conn, empresa_id)

    conn.close()
    return render_template(
        'compras/nuevo_pedido_tipo_a.html',
        productos=productos,
        departamentos=departamentos
    )

# ============================================
# COMPRAS - OPCIN B# ============================================
# COMPRAS - OPCIN B (Pedido por proveedor)
# ============================================
@compras_bp.route('/pedidos/nuevo-tipo-b', methods=['GET', 'POST'])
@login_required
def nuevo_pedido_tipo_b():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    if request.method == "POST":
        proveedor_id = request.form.get("proveedor_id")
        departamento_id = request.form.get("departamento_id")
        productos_seleccionados = request.form.getlist("producto_id[]")
        cantidades = request.form.getlist("cantidad[]")
        precios = request.form.getlist("precio[]")
        fecha_entrega = request.form.get("fecha_entrega", "")
        observaciones = request.form.get("observaciones", "")

        if not proveedor_id:
            conn.close()
            flash('Debes seleccionar proveedor.', 'warning')
            return redirect(url_for('compras.nuevo_pedido_tipo_b'))

        if not departamento_id:
            conn.close()
            flash('Debes seleccionar departamento.', 'warning')
            return redirect(url_for('compras.nuevo_pedido_tipo_b'))

        fecha = datetime.now().strftime("%Y%m%d")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as total FROM pedidos WHERE fecha_pedido = date('now') AND empresa_id = ?",
            (empresa_id,)
        )
        total_hoy = cursor.fetchone()['total'] + 1
        numero_pedido = f"PED-{fecha}-{total_hoy:03d}"

        cursor.execute("""
            INSERT INTO pedidos (
                numero_pedido, proveedor_id, departamento_id, fecha_entrega,
                observaciones, creado_por, estado, empresa_id
            )
            VALUES (?, ?, ?, ?, ?, ?, 'borrador', ?)
        """, (
            numero_pedido,
            proveedor_id,
            departamento_id,
            fecha_entrega,
            observaciones,
            session.get('user_nombre', 'admin'),
            empresa_id
        ))

        pedido_id = cursor.lastrowid
        lineas_guardadas = 0

        for i in range(len(productos_seleccionados)):
            producto_id = productos_seleccionados[i]
            cantidad_raw = cantidades[i] if i < len(cantidades) else ''
            if not producto_id or not cantidad_raw:
                continue

            cantidad = _to_float_safe(cantidad_raw, 0.0)
            if cantidad <= 0:
                continue

            precio_linea = 0.0
            if i < len(precios) and str(precios[i] or '').strip():
                precio_linea = _to_float_safe(precios[i], 0.0)

            cursor.execute("""
                INSERT INTO lineas_pedido (pedido_id, producto_id, cantidad_solicitada, precio_unitario)
                VALUES (?, ?, ?, ?)
            """, (pedido_id, producto_id, cantidad, precio_linea))
            lineas_guardadas += 1

        if lineas_guardadas == 0:
            conn.rollback()
            conn.close()
            flash('No se ha guardado ningún producto. Selecciona producto existente y cantidad.', 'warning')
            return redirect(url_for('compras.nuevo_pedido_tipo_b'))

        conn.commit()
        conn.close()

        flash(f' Pedido {numero_pedido} creado correctamente', 'success')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

    proveedores = conn.execute("""
        SELECT *
        FROM proveedores
        WHERE COALESCE(activo, 1) = 1
          AND empresa_id = ?
        ORDER BY nombre
    """, (empresa_id,)).fetchall()
    departamentos = _departamentos_activos(conn, empresa_id)

    columnas_productos = _columnas_tabla(cur, 'productos')
    columnas_pp = _columnas_tabla(cur, 'productos_proveedores')

    select_extra = []
    select_extra.append("p.codigo AS codigo" if 'codigo' in columnas_productos else "'' AS codigo")
    select_extra.append("p.codigo_interno AS codigo_interno" if 'codigo_interno' in columnas_productos else "'' AS codigo_interno")
    select_extra.append("p.unidad AS unidad" if 'unidad' in columnas_productos else "'' AS unidad")
    select_extra.append("p.familia AS familia" if 'familia' in columnas_productos else "'' AS familia")
    select_extra.append("p.precio AS precio_producto" if 'precio' in columnas_productos else "NULL AS precio_producto")
    select_extra.append("pp.codigo_proveedor AS codigo_proveedor" if 'codigo_proveedor' in columnas_pp else "'' AS codigo_proveedor")

    for col in ['precio', 'precio_compra', 'precio_ultimo', 'precio_vigente', 'precio_medio']:
        select_extra.append(f"pp.{col} AS {col}" if col in columnas_pp else f"NULL AS {col}")

    select_extra.append("pp.es_principal AS es_principal" if 'es_principal' in columnas_pp else "0 AS es_principal")

    productos = conn.execute(f"""
        SELECT p.id,
               p.nombre,
               pp.proveedor_id AS proveedor_id_producto,
               prov.nombre AS proveedor_nombre,
               {', '.join(select_extra)}
        FROM productos p
        JOIN productos_proveedores pp ON p.id = pp.producto_id
        JOIN proveedores prov ON pp.proveedor_id = prov.id
        WHERE COALESCE(prov.activo, 1) = 1
          AND prov.empresa_id = ?
          AND COALESCE(pp.activo, 1) = 1
        ORDER BY prov.nombre, p.nombre
    """, (empresa_id,)).fetchall()

    productos_json = []
    for p in productos:
        precio_actual = (
            p['precio_vigente'] if p['precio_vigente'] is not None else
            p['precio_ultimo'] if p['precio_ultimo'] is not None else
            p['precio_compra'] if p['precio_compra'] is not None else
            p['precio'] if p['precio'] is not None else
            p['precio_producto'] if p['precio_producto'] is not None else
            None
        )

        codigo_mostrar = p['codigo_proveedor'] or p['codigo'] or p['codigo_interno'] or ''
        etiqueta = f"{codigo_mostrar or 'SIN COD'} - {p['nombre'] or ''}"
        detalle = f"Proveedor: {p['proveedor_nombre'] or '-'}"
        if p['codigo_proveedor']:
            detalle += f" | Cód. proveedor: {p['codigo_proveedor']}"
        if p['codigo']:
            detalle += f" | Cód. ERP: {p['codigo']}"

        productos_json.append({
            'id': p['id'],
            'nombre': p['nombre'] or '',
            'codigo': p['codigo'] or '',
            'codigo_interno': p['codigo_interno'] or '',
            'codigo_proveedor': p['codigo_proveedor'] or '',
            'unidad': p['unidad'] or '',
            'familia': p['familia'] or '',
            'proveedor_id': p['proveedor_id_producto'],
            'proveedor_nombre': p['proveedor_nombre'] or '',
            'precio': precio_actual if precio_actual is not None else '',
            'es_principal': p['es_principal'] or 0,
            'etiqueta': etiqueta,
            'detalle': detalle,
            'texto_busqueda': " ".join([
                str(p['nombre'] or ''),
                str(p['codigo'] or ''),
                str(p['codigo_interno'] or ''),
                str(p['codigo_proveedor'] or ''),
                str(p['familia'] or ''),
            ])
        })

    conn.close()
    return render_template(
        'compras/nuevo_pedido_tipo_b.html',
        proveedores=proveedores,
        departamentos=departamentos,
        productos=productos,
        productos_json=productos_json
    )


# ============================================
# IMPRESIÓN RECEPCIÓN# ============================================
# IMPRESIÓN RECEPCIÓN
# ============================================

@compras_bp.route('/recepcion/<numero_pedido>/imprimir-conformidad')
@login_required
def imprimir_conformidad_recepcion(numero_pedido):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()

    pedido = cur.execute("""
        SELECT p.*, prov.nombre AS proveedor_nombre
        FROM pedidos p
        LEFT JOIN proveedores prov ON prov.id = p.proveedor_id
        WHERE p.numero_pedido = ? AND p.empresa_id = ?
    """, (numero_pedido, empresa_id)).fetchone()

    if not pedido:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    recepcion = cur.execute("""
        SELECT *
        FROM recepciones
        WHERE pedido_numero = ? AND empresa_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (numero_pedido, empresa_id)).fetchone()

    lineas = cur.execute("""
        SELECT lr.*,
               lp.producto_id,
               pr.nombre,
               pr.codigo,
               pr.unidad
        FROM lineas_recepcion lr
        LEFT JOIN lineas_pedido lp ON lp.id = lr.linea_pedido_id
        LEFT JOIN productos pr ON pr.id = lp.producto_id
        WHERE lr.pedido_numero = ? AND lr.empresa_id = ?
        ORDER BY lr.id
    """, (numero_pedido, empresa_id)).fetchall()

    conn.close()
    return render_template(
        'compras/hoja_conformidad.html',
        pedido=pedido,
        recepcion=recepcion,
        lineas=lineas
    )


@compras_bp.route('/recepcion/<numero_pedido>/imprimir-incidencias')
@login_required
def imprimir_incidencias_recepcion(numero_pedido):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()

    pedido = cur.execute("""
        SELECT p.*, prov.nombre AS proveedor_nombre
        FROM pedidos p
        LEFT JOIN proveedores prov ON prov.id = p.proveedor_id
        WHERE p.numero_pedido = ? AND p.empresa_id = ?
    """, (numero_pedido, empresa_id)).fetchone()

    if not pedido:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    recepcion = cur.execute("""
        SELECT *
        FROM recepciones
        WHERE pedido_numero = ? AND empresa_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (numero_pedido, empresa_id)).fetchone()

    incidencias = cur.execute("""
        SELECT i.*,
               pr.nombre AS producto_nombre,
               pr.codigo AS producto_codigo
        FROM incidencias i
        LEFT JOIN productos pr ON pr.id = i.producto_id
        WHERE i.empresa_id = ?
          AND (
                i.referencia_id = ?
                OR i.documento_origen = ?
              )
        ORDER BY i.id DESC
    """, (empresa_id, numero_pedido, numero_pedido)).fetchall()

    conn.close()
    return render_template(
        'compras/hoja_incidencia_recepcion.html',
        pedido=pedido,
        recepcion=recepcion,
        incidencias=incidencias
    )
@compras_bp.route('/pedidos/<int:pedido_id>/enviar')
@login_required
def enviar_pedido(pedido_id):
    conn = get_db()

    conn.execute("""
        UPDATE pedidos
        SET estado = 'enviado',
            fecha_envio = datetime('now')
        WHERE id = ?
    """, (pedido_id,))

    conn.commit()
    conn.close()

    flash('Pedido enviado correctamente', 'success')
    return redirect(url_for('compras.lista_pedidos'))


@compras_bp.route('/recepcion-directa', methods=['GET', 'POST'])
@login_required
def recepcion_directa():
    conn = get_db(); empresa_id = session.get('user_empresa_id', 1); usuario = (session.get('user_nombre') or 'admin').strip()
    cur = conn.cursor(); _asegurar_estructura_compras(cur); conn.commit()
    proveedores=[dict(p) for p in conn.execute('SELECT id, nombre FROM proveedores WHERE activo = 1 AND empresa_id = ? ORDER BY nombre',(empresa_id,)).fetchall()]
    almacenes=[dict(a) for a in conn.execute('SELECT id, nombre FROM almacenes WHERE activo = 1 AND empresa_id = ? ORDER BY nombre',(empresa_id,)).fetchall()]
    departamentos=[dict(d) for d in _departamentos_activos(conn, empresa_id)]
    productos=[dict(p) for p in conn.execute('SELECT id, nombre, codigo, precio FROM productos WHERE activo = 1 AND empresa_id = ? ORDER BY nombre',(empresa_id,)).fetchall()]
    if request.method == 'POST':
        accion=(request.form.get('accion') or 'guardar').strip().lower(); proveedor_id=request.form.get('proveedor_id') or None; departamento_id=request.form.get('departamento_id') or None
        numero_albaran=(request.form.get('numero_albaran') or '').strip(); observaciones=(request.form.get('observaciones') or '').strip(); documento=request.files.get('documento_compra')
        hay_documento=bool(documento and documento.filename)
        if accion == 'leer':
            lineas_ocr=[]; texto_ocr=''
            if not hay_documento: flash('Selecciona un albarán, foto o PDF para leer.', 'warning')
            else:
                ruta_tmp=None
                try:
                    if documento is None or not documento.filename:
                        flash('Selecciona un albarán, foto o PDF para leer.', 'warning')
                        raise ValueError('Documento no seleccionado')
                    nombre_original=secure_filename(str(documento.filename or 'documento')); extension=os.path.splitext(nombre_original)[1].lower(); nombre_tmp=f'tmp_ocr_{uuid.uuid4().hex}{extension}'
                    tmp_dir=os.path.join(current_app.instance_path,'uploads','compras','tmp_ocr'); os.makedirs(tmp_dir, exist_ok=True); ruta_tmp=os.path.join(tmp_dir,nombre_tmp); documento.save(ruta_tmp)
                    texto_ocr=extraer_texto_documento_compra(ruta_tmp)
                    numero_pedido_detectado = detectar_numero_pedido_desde_texto(texto_ocr)
                    if numero_pedido_detectado:
                        pedido_detectado = conn.execute("""
                            SELECT id, numero_pedido
                            FROM pedidos
                            WHERE numero_pedido = ? AND empresa_id = ?
                            LIMIT 1
                        """, (numero_pedido_detectado, empresa_id)).fetchone()
                        if pedido_detectado:
                            recepcion_existente = conn.execute("""
                                SELECT id
                                FROM recepciones
                                WHERE pedido_id = ?
                                  AND empresa_id = ?
                                  AND COALESCE(tipo_recepcion, 'pedido') = 'pedido'
                                ORDER BY id DESC
                                LIMIT 1
                            """, (pedido_detectado['id'], empresa_id)).fetchone()
                            conn.close()
                            if recepcion_existente:
                                flash(f'OCR detectó el pedido {numero_pedido_detectado}. Ya está recepcionado; abre edición para corregir sin duplicar stock.', 'warning')
                                return redirect(url_for('compras.editar_recepcion', recepcion_id=recepcion_existente['id']))
                            flash(f'OCR detectó el pedido {numero_pedido_detectado}. Recepciona desde el pedido para no crear entrada directa.', 'info')
                            return redirect(url_for('compras.recibir_pedido', numero_pedido=numero_pedido_detectado))
                    for idx,linea in enumerate(detectar_lineas_documento_compra(texto_ocr), start=1):
                        producto_id=buscar_producto_por_texto(conn, linea.get('descripcion') or '', empresa_id, referencia=linea.get('referencia') or '')
                        lineas_ocr.append({'idx':idx,'descripcion':linea.get('descripcion') or '','producto_id':producto_id or '','cantidad':linea.get('cantidad') or '','precio':linea.get('precio') or '','importe':linea.get('importe') or '','almacen_id':'','lote':'','fecha_caducidad':'','temperatura':'','incidencia_texto':''})
                    flash(f'Se han leído {len(lineas_ocr)} líneas. Revisa antes de guardar.', 'success')
                except Exception as e: flash(f'Error leyendo documento: {str(e)}', 'danger')
                finally:
                    try:
                        if ruta_tmp and os.path.exists(ruta_tmp): os.remove(ruta_tmp)
                    except Exception: pass
            form_cache={'proveedor_id':proveedor_id or '', 'departamento_id':departamento_id or '', 'numero_albaran':numero_albaran, 'observaciones':observaciones}
            conn.close(); return render_template('compras/recepcion_directa.html', proveedores=proveedores, productos=productos, almacenes=almacenes, departamentos=departamentos, lineas_ocr=lineas_ocr, texto_ocr=texto_ocr, form_cache=form_cache)
        if not proveedor_id or not departamento_id:
            conn.close(); flash('Debes seleccionar proveedor y departamento.', 'warning'); return redirect(url_for('compras.recepcion_directa'))
        indices=request.form.getlist('linea_idx[]')
        if not indices:
            conn.close(); flash('No hay líneas para guardar. Añade productos o lee un albarán.', 'warning'); return redirect(url_for('compras.recepcion_directa'))
        try:
            cur=conn.cursor(); _asegurar_estructura_compras(cur)
            cur.execute("""INSERT INTO recepciones (pedido_id, proveedor_id, departamento_id, fecha_recepcion, numero_albaran, observaciones, recibido_por, empresa_id, tipo_recepcion, validada_operativamente, validada_economicamente, stock_aplicado) VALUES (NULL, ?, ?, date('now'), ?, ?, ?, ?, 'directa', 1, 0, 0)""",(proveedor_id,departamento_id,numero_albaran,observaciones,usuario,empresa_id))
            recepcion_id=cur.lastrowid
            documento_id=guardar_documento_compra_en_cursor(cur, documento, proveedor_id=proveedor_id, pedido_id=None, recepcion_id=recepcion_id, tipo_documento='albaran', origen_subida='foto' if hay_documento else 'manual')
            if documento_id: cur.execute('UPDATE recepciones SET documento_adjunto_id = ? WHERE id = ?', (documento_id,recepcion_id))
            lineas_guardadas=0; incidencias_guardadas=0
            for idx in indices:
                producto_id=_to_int_or_none(request.form.get(f'producto_id_{idx}')); cantidad=_to_float_safe(request.form.get(f'cantidad_{idx}')); precio=_to_float_safe(request.form.get(f'precio_{idx}')); importe=_to_float_safe(request.form.get(f'importe_{idx}')); almacen_id=_to_int_or_none(request.form.get(f'almacen_id_{idx}'))
                estado=(request.form.get(f'estado_{idx}') or 'ok').strip(); incidencia_texto=(request.form.get(f'incidencia_texto_{idx}') or '').strip(); descripcion=(request.form.get(f'descripcion_{idx}') or '').strip(); lote=(request.form.get(f'lote_{idx}') or '').strip(); caducidad=(request.form.get(f'caducidad_{idx}') or '').strip() or None
                temp_raw=(request.form.get(f'temperatura_{idx}') or '').strip(); temperatura=_to_float_safe(temp_raw, 0.0) if temp_raw else None; tiene_incidencia=estado!='ok'
                if not producto_id: continue
                if cantidad <= 0 and not tiene_incidencia: continue
                if cantidad > 0 and not almacen_id:
                    conn.rollback(); flash('Cada línea recibida debe tener almacén destino.', 'warning'); return redirect(url_for('compras.recepcion_directa'))
                if precio <= 0 and importe > 0 and cantidad > 0: precio=round(importe/cantidad,6)
                incidencia_id=None; aceptado=1; motivo_rechazo=None
                if tiene_incidencia:
                    tipo_map={'falta_unidades':'Falta de unidades','falta_peso':'Falta de peso','rotura':'Rotura','mal_estado':'Mal estado','temperatura_incorrecta':'Temperatura incorrecta'}; incidencia_tipo=tipo_map.get(estado,'Incidencia en recepción directa')
                    if not incidencia_texto: incidencia_texto=f'{incidencia_tipo} en recepción directa. Producto: {descripcion or producto_id}.'
                    motivo_rechazo=incidencia_texto
                    if estado in ('rotura','mal_estado','temperatura_incorrecta'): aceptado=0
                    incidencia_id=_crear_incidencia_recepcion(cur, empresa_id, recepcion_id, usuario, incidencia_tipo, incidencia_texto, gravedad='alta' if aceptado == 0 else 'media'); incidencias_guardadas += 1
                importe_total=round(cantidad*precio,2) if precio else None
                cur.execute("""INSERT INTO lineas_recepcion (recepcion_id, producto_id, cantidad_recibida, lote, fecha_caducidad, temperatura, almacen_id, precio_unitario, importe_total, aceptado, motivo_rechazo, incidencia_id, empresa_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",(recepcion_id,producto_id,cantidad,lote or None,caducidad,temperatura,almacen_id,precio if precio else None,importe_total,aceptado,motivo_rechazo,incidencia_id,empresa_id))
                lineas_guardadas += 1
            if lineas_guardadas == 0:
                conn.rollback(); flash('No se ha guardado ninguna línea válida.', 'warning'); return redirect(url_for('compras.recepcion_directa'))
            aplicadas=_aplicar_stock_recepcion(cur, recepcion_id, empresa_id, usuario); conn.commit(); flash(f'Recepción directa guardada: {lineas_guardadas} línea(s), {incidencias_guardadas} incidencia(s), stock aplicado en {aplicadas} línea(s). Validación económica pendiente.', 'success'); return redirect(url_for('compras.validar_recepciones'))
        except Exception as e:
            conn.rollback(); flash(f'Error guardando recepción directa: {str(e)}', 'danger'); return redirect(url_for('compras.recepcion_directa'))
        finally: conn.close()
    conn.close(); return render_template('compras/recepcion_directa.html', proveedores=proveedores, productos=productos, almacenes=almacenes, departamentos=departamentos, lineas_ocr=[], texto_ocr='', form_cache=None)


@compras_bp.route('/recepciones/<int:recepcion_id>/conformidad')
@login_required
def hoja_conformidad_recepcion_id(recepcion_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    recepcion = conn.execute("""
        SELECT r.*, pr.nombre AS proveedor_nombre, pr.nif, pr.email, pr.telefono,
               p.numero_pedido, p.fecha_pedido
        FROM recepciones r
        LEFT JOIN proveedores pr ON r.proveedor_id = pr.id
        LEFT JOIN pedidos p ON r.pedido_id = p.id
        WHERE r.id = ? AND r.empresa_id = ?
    """, (recepcion_id, empresa_id)).fetchone()

    if not recepcion:
        conn.close()
        flash('Recepción no encontrada.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    lineas = conn.execute("""
        SELECT lr.*, prod.nombre AS producto_nombre, prod.codigo, prod.unidad
        FROM lineas_recepcion lr
        JOIN productos prod ON lr.producto_id = prod.id
        WHERE lr.recepcion_id = ?
          AND (lr.empresa_id = ? OR lr.empresa_id IS NULL)
        ORDER BY lr.id
    """, (recepcion_id, empresa_id)).fetchall()

    conn.close()
    return render_template(
        'compras/hoja_conformidad.html',
        recepcion=recepcion,
        lineas=lineas,
        now=datetime.now()
    )


@compras_bp.route('/recepciones/<int:recepcion_id>/incidencias')
@login_required
def hoja_incidencias_recepcion_id(recepcion_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    recepcion = conn.execute("""
        SELECT r.*, pr.nombre AS proveedor_nombre, p.numero_pedido
        FROM recepciones r
        LEFT JOIN proveedores pr ON r.proveedor_id = pr.id
        LEFT JOIN pedidos p ON r.pedido_id = p.id
        WHERE r.id = ? AND r.empresa_id = ?
    """, (recepcion_id, empresa_id)).fetchone()

    if not recepcion:
        conn.close()
        flash('Recepción no encontrada.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    pedido = {
        'id': recepcion['pedido_id'],
        'numero_pedido': recepcion['numero_pedido'] or 'Recepción directa',
        'proveedor_nombre': recepcion['proveedor_nombre'] or '-',
    }

    incidencias = conn.execute("""
        SELECT i.*, lr.producto_id, prod.nombre AS producto_nombre,
               r.fecha_recepcion, r.numero_albaran
        FROM incidencias i
        LEFT JOIN lineas_recepcion lr ON lr.incidencia_id = i.id
        LEFT JOIN recepciones r ON lr.recepcion_id = r.id
        LEFT JOIN productos prod ON lr.producto_id = prod.id
        WHERE r.id = ? AND i.empresa_id = ?
        ORDER BY i.id DESC
    """, (recepcion_id, empresa_id)).fetchall()

    conn.close()
    return render_template(
        'compras/hoja_incidencias_recepcion.html',
        pedido=pedido,
        incidencias=incidencias,
        now=datetime.now()
    )
@compras_bp.route('/recepciones/<int:recepcion_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_recepcion(recepcion_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    usuario = (session.get('user_nombre') or 'admin').strip()
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    recepcion = conn.execute("""
        SELECT r.*, pr.nombre AS proveedor_nombre, d.nombre AS departamento_nombre,
               p.numero_pedido, p.id AS pedido_id_real
        FROM recepciones r
        LEFT JOIN proveedores pr ON pr.id = r.proveedor_id
        LEFT JOIN departamentos d ON d.id = r.departamento_id
        LEFT JOIN pedidos p ON p.id = r.pedido_id
        WHERE r.id = ? AND r.empresa_id = ?
    """, (recepcion_id, empresa_id)).fetchone()

    if not recepcion:
        conn.close()
        flash('Recepción no encontrada.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    almacenes = conn.execute("""
        SELECT id, nombre
        FROM almacenes
        WHERE activo = 1 AND empresa_id = ?
        ORDER BY nombre
    """, (empresa_id,)).fetchall()

    if request.method == 'POST':
        try:
            numero_albaran = (request.form.get('numero_albaran') or '').strip()
            observaciones = (request.form.get('observaciones') or '').strip()
            fecha_recepcion = (request.form.get('fecha_recepcion') or recepcion['fecha_recepcion'] or datetime.now().strftime('%Y-%m-%d')).strip()

            lineas = conn.execute("""
                SELECT *
                FROM lineas_recepcion
                WHERE recepcion_id = ?
                  AND (empresa_id = ? OR empresa_id IS NULL)
                ORDER BY id
            """, (recepcion_id, empresa_id)).fetchall()

            documento = numero_albaran or recepcion['numero_pedido'] or f"RECEP-{recepcion_id}"

            for linea in lineas:
                linea_id = linea['id']
                cantidad_anterior = _to_float_safe(linea['cantidad_recibida'], 0.0)
                almacen_anterior = linea['almacen_id']
                aceptado_anterior = int(linea['aceptado'] if linea['aceptado'] is not None else 1)

                cantidad_nueva = _to_float_safe(request.form.get(f'cantidad_{linea_id}'), 0.0)
                almacen_nuevo = _to_int_or_none(request.form.get(f'almacen_{linea_id}'))
                lote_nuevo = (request.form.get(f'lote_{linea_id}') or '').strip()
                caducidad_nueva = (request.form.get(f'caducidad_{linea_id}') or '').strip() or None
                temperatura_raw = (request.form.get(f'temperatura_{linea_id}') or '').strip()
                temperatura_nueva = _to_float_safe(temperatura_raw, 0.0) if temperatura_raw else None
                estado = (request.form.get(f'estado_{linea_id}') or 'ok').strip()
                incidencia_texto = (request.form.get(f'incidencia_texto_{linea_id}') or '').strip()

                if cantidad_nueva < 0:
                    cantidad_nueva = 0.0

                if cantidad_nueva > 0 and not almacen_nuevo:
                    conn.rollback()
                    flash('Toda línea con cantidad recibida debe tener almacén destino.', 'warning')
                    return redirect(url_for('compras.editar_recepcion', recepcion_id=recepcion_id))

                aceptado_nuevo = 0 if estado in ('rotura', 'mal_estado', 'temperatura_incorrecta', 'rechazado') else 1
                motivo_rechazo = incidencia_texto if aceptado_nuevo == 0 or estado != 'ok' else None

                cantidad_stock_anterior = cantidad_anterior if aceptado_anterior == 1 else 0.0
                cantidad_stock_nueva = cantidad_nueva if aceptado_nuevo == 1 else 0.0

                if almacen_anterior and almacen_nuevo and int(almacen_anterior) != int(almacen_nuevo):
                    if cantidad_stock_anterior:
                        _ajustar_stock_almacen(cur, linea['producto_id'], almacen_anterior, -cantidad_stock_anterior, empresa_id, usuario, documento, 'Ajuste edición recepción: cambio de almacén')
                    if cantidad_stock_nueva:
                        _ajustar_stock_almacen(cur, linea['producto_id'], almacen_nuevo, cantidad_stock_nueva, empresa_id, usuario, documento, 'Ajuste edición recepción: cambio de almacén')
                else:
                    almacen_para_ajuste = almacen_nuevo or almacen_anterior
                    diferencia = cantidad_stock_nueva - cantidad_stock_anterior
                    if diferencia:
                        _ajustar_stock_almacen(cur, linea['producto_id'], almacen_para_ajuste, diferencia, empresa_id, usuario, documento, 'Ajuste edición recepción: corrección de cantidad/estado')

                incidencia_id = linea['incidencia_id']
                if estado != 'ok' and not incidencia_id:
                    tipo_map = {
                        'falta_unidades': 'Falta de unidades',
                        'falta_peso': 'Falta de peso',
                        'rotura': 'Rotura',
                        'mal_estado': 'Mal estado',
                        'temperatura_incorrecta': 'Temperatura incorrecta',
                        'rechazado': 'Producto rechazado'
                    }
                    incidencia_tipo = tipo_map.get(estado, 'Incidencia en recepción')
                    if not incidencia_texto:
                        incidencia_texto = f'{incidencia_tipo} corregida en edición de recepción {recepcion_id}.'
                    incidencia_id = _crear_incidencia_recepcion(cur, empresa_id, recepcion_id, usuario, incidencia_tipo, incidencia_texto, gravedad='alta' if aceptado_nuevo == 0 else 'media')

                cur.execute("""
                    UPDATE lineas_recepcion
                    SET cantidad_recibida = ?,
                        almacen_id = ?,
                        lote = ?,
                        fecha_caducidad = ?,
                        temperatura = ?,
                        aceptado = ?,
                        motivo_rechazo = ?,
                        incidencia_id = ?
                    WHERE id = ?
                """, (
                    cantidad_nueva,
                    almacen_nuevo,
                    lote_nuevo or None,
                    caducidad_nueva,
                    temperatura_nueva,
                    aceptado_nuevo,
                    motivo_rechazo,
                    incidencia_id,
                    linea_id
                ))

                _ajustar_lote_recepcion_linea(cur, recepcion, linea, cantidad_stock_nueva, almacen_nuevo, lote_nuevo, caducidad_nueva, temperatura_nueva)

            cur.execute("""
                UPDATE recepciones
                SET numero_albaran = ?,
                    observaciones = ?,
                    fecha_recepcion = ?,
                    validada_economicamente = 0
                WHERE id = ? AND empresa_id = ?
            """, (numero_albaran, observaciones, fecha_recepcion, recepcion_id, empresa_id))

            if recepcion['pedido_id']:
                _recalcular_cantidad_recibida_pedido(cur, recepcion['pedido_id'])
                _recalcular_estado_pedido(cur, recepcion['pedido_id'], empresa_id)

            conn.commit()
            flash('Recepción modificada correctamente. El stock se ha ajustado por diferencia.', 'success')
            return redirect(url_for('compras.validar_recepciones'))

        except Exception as e:
            conn.rollback()
            flash(f'Error modificando recepción: {str(e)}', 'danger')
            return redirect(url_for('compras.editar_recepcion', recepcion_id=recepcion_id))
        finally:
            conn.close()

    lineas = conn.execute("""
        SELECT lr.*, p.nombre AS producto_nombre, p.codigo AS producto_codigo, p.unidad,
               a.nombre AS almacen_nombre
        FROM lineas_recepcion lr
        JOIN productos p ON p.id = lr.producto_id
        LEFT JOIN almacenes a ON a.id = lr.almacen_id
        WHERE lr.recepcion_id = ?
          AND (lr.empresa_id = ? OR lr.empresa_id IS NULL)
        ORDER BY lr.id
    """, (recepcion_id, empresa_id)).fetchall()

    conn.close()
    return render_template(
        'compras/editar_recepcion.html',
        recepcion=recepcion,
        lineas=lineas,
        almacenes=almacenes,
        now=datetime.now()
    )

@compras_bp.route('/recepciones/validar')
@login_required
def validar_recepciones():
    conn = get_db(); empresa_id = session.get('user_empresa_id', 1); cur = conn.cursor(); _asegurar_estructura_compras(cur); conn.commit()
    recepciones = conn.execute("""
        SELECT r.*, p.numero_pedido, pr.nombre AS proveedor_nombre, d.nombre AS departamento_nombre, dc.id AS documento_id, dc.nombre_archivo,
               (SELECT COUNT(*) FROM lineas_recepcion lr WHERE lr.recepcion_id = r.id) AS num_lineas,
               (SELECT COUNT(*) FROM lineas_recepcion lr WHERE lr.recepcion_id = r.id AND lr.incidencia_id IS NOT NULL) AS num_incidencias
        FROM recepciones r
        LEFT JOIN pedidos p ON r.pedido_id = p.id
        LEFT JOIN proveedores pr ON r.proveedor_id = pr.id
        LEFT JOIN departamentos d ON d.id = r.departamento_id
        LEFT JOIN documentos_compra dc ON dc.id = r.documento_adjunto_id
        WHERE r.empresa_id = ?
        ORDER BY r.id DESC
    """, (empresa_id,)).fetchall()
    conn.close(); return render_template('compras/validar_recepciones.html', recepciones=recepciones)

@compras_bp.route('/recepciones/<int:id>/validar-operativa')
@login_required
def validar_operativa(id):
    conn = get_db(); empresa_id = session.get('user_empresa_id', 1); usuario = (session.get('user_nombre') or 'admin').strip()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE recepciones SET validada_operativamente = 1 WHERE id = ? AND empresa_id = ?", (id, empresa_id))
        cur.execute("""INSERT INTO recepcion_validaciones (recepcion_id, tipo_validacion, estado, fecha, observaciones) VALUES (?, 'operativa', 'validada', datetime('now'), ?)""", (id, f'Validada operativamente por {usuario}'))
        conn.commit(); flash('Recepción validada operativamente. Aún no entra en stock hasta validación económica/administrativa.', 'success')
    except Exception as e:
        conn.rollback(); flash(f'Error validando recepción operativa: {str(e)}', 'danger')
    finally:
        conn.close()
    return redirect(url_for('compras.validar_recepciones'))

@compras_bp.route('/recepciones/<int:id>/validar-economica')
@login_required
def validar_economica(id):
    conn = get_db(); empresa_id = session.get('user_empresa_id', 1); usuario = (session.get('user_nombre') or 'admin').strip()
    try:
        cur = conn.cursor(); _asegurar_columna_recepcion_stock_aplicado(cur)
        cur.execute("UPDATE recepciones SET validada_economicamente = 1 WHERE id = ? AND empresa_id = ?", (id, empresa_id))
        cur.execute("""INSERT INTO recepcion_validaciones (recepcion_id, tipo_validacion, estado, fecha, observaciones) VALUES (?, 'economica', 'validada', datetime('now'), ?)""", (id, f'Validada económicamente por {usuario}'))
        aplicadas = _aplicar_stock_recepcion(cur, id, empresa_id, usuario)
        conn.commit(); flash(f'Recepción validada económicamente. Líneas aplicadas a stock: {aplicadas}.', 'success')
    except Exception as e:
        conn.rollback(); flash(f'Error validando económicamente: {str(e)}', 'danger')
    finally:
        conn.close()
    return redirect(url_for('compras.validar_recepciones')) 
@compras_bp.route('/documentos-compra/<int:documento_id>/validar-economico')
@login_required
def validar_economico(documento_id):
    conn = get_db()
    cursor = conn.cursor()

    try:
        lineas = conn.execute("""
            SELECT *
            FROM documento_compra_lineas_detectadas
            WHERE documento_id = ?
        """, (documento_id,)).fetchall()

        for l in lineas:
            if not l['producto_id']:
                continue

            precio = l['precio_detectado']

            if not precio or precio <= 0:
                continue

            # guardar precio en producto_proveedor
            existente = cursor.execute("""
                SELECT id
                FROM productos_proveedores
                WHERE producto_id = ?
                LIMIT 1
            """, (l['producto_id'],)).fetchone()

            if existente:
                cursor.execute("""
                    UPDATE productos_proveedores
                    SET precio_compra = ?
                    WHERE id = ?
                """, (precio, existente['id']))
            else:
                cursor.execute("""
                    INSERT INTO productos_proveedores (
                        producto_id,
                        precio_compra,
                        activo
                    )
                    VALUES (?, ?, 1)
                """, (l['producto_id'], precio))

        conn.commit()
        flash('Precios validados correctamente', 'success')

    except Exception as e:
        conn.rollback()
        flash(f'Error validando precios: {str(e)}', 'danger')

    finally:
        conn.close()

    return redirect(url_for('compras.validar_recepciones'))

@compras_bp.route('/recepciones/<int:id>/detalle')
@login_required
def detalle_validacion_recepcion(id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    recepcion = conn.execute("""
        SELECT r.*, pr.nombre AS proveedor_nombre
        FROM recepciones r
        LEFT JOIN proveedores pr ON r.proveedor_id = pr.id
        WHERE r.id = ? AND r.empresa_id = ?
    """, (id, empresa_id)).fetchone()

    lineas = conn.execute("""
        SELECT lr.*, p.nombre, p.codigo
        FROM lineas_recepcion lr
        JOIN productos p ON lr.producto_id = p.id
        WHERE lr.recepcion_id = ?
    """, (id,)).fetchall()

    conn.close()

    return render_template(
        'compras/detalle_validacion_recepcion.html',
        recepcion=recepcion,
        lineas=lineas
    )
    
@compras_bp.route('/compras/guardar-validacion-economica', methods=['POST'])
@login_required
def guardar_validacion_economica():
    conn = get_db()
    cursor = conn.cursor()

    linea_id = request.form.get('linea_id')
    precio = request.form.get('precio_final')

    try:
        linea = conn.execute("""
            SELECT producto_id
            FROM documento_compra_lineas_detectadas
            WHERE id = ?
        """, (linea_id,)).fetchone()

        if linea and linea['producto_id'] and precio:
            cursor.execute("""
                INSERT INTO productos_proveedores (producto_id, precio_compra, activo)
                VALUES (?, ?, 1)
            """, (linea['producto_id'], precio))

        conn.commit()
        flash('Precio actualizado', 'success')

    except Exception as e:
        conn.rollback()
        flash(str(e), 'danger')

    finally:
        conn.close()

    return redirect(request.referrer)



@compras_bp.route('/recepciones/<int:id>/guardar-validacion-economica', methods=['POST'])
@login_required
def guardar_validacion_economica_detalle(id):
    conn = get_db()
    cursor = conn.cursor()
    empresa_id = session.get('user_empresa_id', 1)

    try:
        recepcion = conn.execute("""
            SELECT *
            FROM recepciones
            WHERE id = ? AND empresa_id = ?
        """, (id, empresa_id)).fetchone()

        if not recepcion:
            flash('Recepción no encontrada.', 'danger')
            conn.close()
            return redirect(url_for('compras.validar_recepciones'))

        proveedor_id = recepcion['proveedor_id']

        linea_ids = request.form.getlist('linea_id[]')

        for linea_id in linea_ids:
            precio_raw = (request.form.get(f'precio_final_{linea_id}') or '').strip()
            cantidad_raw = (request.form.get(f'cantidad_{linea_id}') or '').strip()

            try:
                precio_final = float(precio_raw.replace(',', '.')) if precio_raw else None
            except Exception:
                precio_final = None

            try:
                cantidad_final = float(cantidad_raw.replace(',', '.')) if cantidad_raw else None
            except Exception:
                cantidad_final = None

            linea = conn.execute("""
                SELECT *
                FROM documento_compra_lineas_detectadas
                WHERE id = ?
            """, (linea_id,)).fetchone()

            if not linea:
                continue

            cursor.execute("""
                UPDATE documento_compra_lineas_detectadas
                SET precio_detectado = COALESCE(?, precio_detectado),
                    cantidad_detectada = COALESCE(?, cantidad_detectada),
                    estado = 'precio_validado'
                WHERE id = ?
            """, (precio_final, cantidad_final, linea_id))

            producto_id = linea['producto_id']
            if not producto_id or precio_final is None or precio_final <= 0:
                continue

            existente = cursor.execute("""
                SELECT id
                FROM productos_proveedores
                WHERE producto_id = ?
                  AND proveedor_id = ?
                LIMIT 1
            """, (producto_id, proveedor_id)).fetchone()

            if existente:
                cursor.execute("""
                    UPDATE productos_proveedores
                    SET precio_ultimo = ?,
                        precio_vigente = ?,
                        activo = 1
                    WHERE id = ?
                """, (precio_final, precio_final, existente['id']))
            else:
                cursor.execute("""
                    INSERT INTO productos_proveedores (
                        producto_id,
                        proveedor_id,
                        precio_ultimo,
                        precio_vigente,
                        activo,
                        preferente
                    )
                    VALUES (?, ?, ?, ?, 1, 0)
                """, (producto_id, proveedor_id, precio_final, precio_final))

        cursor.execute("""
            UPDATE recepciones
            SET validada_economicamente = 1
            WHERE id = ? AND empresa_id = ?
        """, (id, empresa_id))

        conn.commit()
        flash('Precios revisados y recepción validada económicamente.', 'success')

    except Exception as e:
        conn.rollback()
        flash(f'Error guardando validación económica: {str(e)}', 'danger')

    finally:
        conn.close()

    return redirect(url_for('compras.validar_recepciones'))

@compras_bp.route('/recepciones/<int:id>/validacion-economica')
@login_required
def pantalla_validacion_economica(id):
    """
    Pantalla económica: siempre debe mostrar líneas para validar precios.
    Prioridad:
    1) Líneas OCR guardadas en documento_compra_lineas_detectadas, si existen.
    2) Líneas reales de lineas_recepcion, aunque no haya OCR o el OCR no haya generado líneas económicas.
    """
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    recepcion = conn.execute("""
        SELECT r.*, pr.nombre AS proveedor_nombre, p.numero_pedido
        FROM recepciones r
        LEFT JOIN proveedores pr ON r.proveedor_id = pr.id
        LEFT JOIN pedidos p ON r.pedido_id = p.id
        WHERE r.id = ? AND r.empresa_id = ?
    """, (id, empresa_id)).fetchone()

    if not recepcion:
        conn.close()
        flash('Recepción no encontrada.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    columnas_pp = _columnas_tabla(cur, 'productos_proveedores')
    columnas_p = _columnas_tabla(cur, 'productos')

    precio_cols = [
        c for c in ['precio_vigente', 'precio_ultimo', 'precio_medio', 'precio_compra', 'precio']
        if c in columnas_pp
    ]

    def _precio_actual_producto_validacion(producto_id, proveedor_id):
        if not producto_id or not precio_cols:
            return None

        select_precio = "COALESCE(" + ", ".join(precio_cols) + ") AS precio_actual"
        sql = f"""
            SELECT {select_precio}
            FROM productos_proveedores
            WHERE producto_id = ?
        """
        params = [producto_id]

        if 'proveedor_id' in columnas_pp and proveedor_id:
            sql += " AND (proveedor_id = ? OR proveedor_id IS NULL)"
            params.append(proveedor_id)

        if 'activo' in columnas_pp:
            sql += " AND COALESCE(activo, 1) = 1"

        orden = []
        if 'proveedor_id' in columnas_pp and proveedor_id:
            orden.append("CASE WHEN proveedor_id = ? THEN 0 ELSE 1 END")
            params.append(proveedor_id)
        if 'es_principal' in columnas_pp:
            orden.append("COALESCE(es_principal, 0) DESC")
        if 'preferente' in columnas_pp:
            orden.append("COALESCE(preferente, 0) DESC")
        orden.append("id DESC")
        sql += " ORDER BY " + ", ".join(orden) + " LIMIT 1"

        row = conn.execute(sql, tuple(params)).fetchone()
        if row and row['precio_actual'] is not None:
            return row['precio_actual']
        return None

    documento = conn.execute("""
        SELECT *
        FROM documentos_compra
        WHERE recepcion_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (id,)).fetchone()

    lineas_con_precio = []

    # 1) Si el documento tiene líneas OCR guardadas, se usan como base económica.
    if documento:
        lineas_doc = conn.execute("""
            SELECT l.*,
                   p.nombre AS producto_nombre,
                   p.codigo AS producto_codigo
            FROM documento_compra_lineas_detectadas l
            LEFT JOIN productos p ON p.id = l.producto_id
            WHERE l.documento_id = ?
            ORDER BY l.id
        """, (documento['id'],)).fetchall()

        for l in lineas_doc:
            producto_id = l['producto_id']
            precio_actual = _precio_actual_producto_validacion(producto_id, recepcion['proveedor_id'])
            precio_documento = l['precio_detectado']

            lineas_con_precio.append({
                "id": f"doc_{l['id']}",
                "producto_id": producto_id,
                "producto_nombre": l['producto_nombre'] or l['descripcion_detectada'],
                "producto_codigo": l['producto_codigo'],
                "descripcion_detectada": l['descripcion_detectada'],
                "cantidad_detectada": l['cantidad_detectada'],
                "cantidad": l['cantidad_detectada'],
                "precio_actual": precio_actual,
                "precio_pedido": None,
                "precio_documento": precio_documento,
                "precio_detectado": precio_documento,
                "diferencia": (precio_documento - precio_actual) if precio_documento is not None and precio_actual is not None else None,
                "estado": l['estado'],
                "observaciones": l['observaciones'] if 'observaciones' in l.keys() else '',
                "origen": "documento"
            })

    # 2) Si no hay líneas OCR económicas, usamos SIEMPRE lineas_recepcion.
    # Esto evita que una recepción directa o una recepción con OCR temporal quede sin validar.
    if not lineas_con_precio:
        codigo_expr = "p.codigo AS producto_codigo" if 'codigo' in columnas_p else "'' AS producto_codigo"

        lineas_recepcion = conn.execute(f"""
            SELECT lr.*,
                   p.nombre AS producto_nombre,
                   {codigo_expr},
                   lp.precio_unitario AS precio_pedido
            FROM lineas_recepcion lr
            JOIN productos p ON p.id = lr.producto_id
            LEFT JOIN lineas_pedido lp
                   ON lp.pedido_id = ?
                  AND lp.producto_id = lr.producto_id
            WHERE lr.recepcion_id = ?
              AND (lr.empresa_id = ? OR lr.empresa_id IS NULL)
            ORDER BY lr.id
        """, (recepcion['pedido_id'], id, empresa_id)).fetchall()

        for l in lineas_recepcion:
            producto_id = l['producto_id']
            precio_actual = _precio_actual_producto_validacion(producto_id, recepcion['proveedor_id'])
            precio_pedido = l['precio_pedido'] if 'precio_pedido' in l.keys() else None
            precio_documento = l['precio_unitario'] if 'precio_unitario' in l.keys() else None

            lineas_con_precio.append({
                "id": f"lr_{l['id']}",
                "producto_id": producto_id,
                "producto_nombre": l['producto_nombre'],
                "producto_codigo": l['producto_codigo'],
                "descripcion_detectada": l['producto_nombre'],
                "cantidad_detectada": l['cantidad_recibida'],
                "cantidad": l['cantidad_recibida'],
                "precio_actual": precio_actual,
                "precio_pedido": precio_pedido,
                "precio_documento": precio_documento,
                "precio_detectado": precio_documento,
                "diferencia": (precio_documento - precio_actual) if precio_documento is not None and precio_actual is not None else None,
                "estado": "pendiente_precio" if precio_documento is None else "pendiente_validacion",
                "observaciones": "Línea procedente de recepción operativa; precio pendiente de compras." if precio_documento is None else "",
                "origen": "recepcion"
            })

    conn.close()

    return render_template(
        'compras/validacion_economica.html',
        recepcion=recepcion,
        documento=documento,
        lineas=lineas_con_precio
    )


@compras_bp.route('/documentos-compra/<int:documento_id>/ver')
@login_required
def ver_documento_compra(documento_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    documento = conn.execute("""
        SELECT dc.*
        FROM documentos_compra dc
        LEFT JOIN proveedores pr ON dc.proveedor_id = pr.id
        WHERE dc.id = ?
    """, (documento_id,)).fetchone()

    conn.close()

    if not documento:
        flash('Documento no encontrado.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    ruta_archivo = documento['ruta_archivo']

    if not ruta_archivo or not os.path.exists(ruta_archivo):
        flash('El archivo no existe en el servidor.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    return send_file(
        ruta_archivo,
        mimetype=documento['mime_type'] or None,
        as_attachment=False,
        download_name=documento['nombre_archivo']
    )
    

def detectar_proveedor_desde_texto(conn, texto, empresa_id):
    if not texto:
        return None

    texto_lower = texto.lower()

    # 1️⃣ Buscar coincidencia con proveedores existentes
    proveedores = conn.execute("""
        SELECT id, nombre
        FROM proveedores
        WHERE empresa_id = ?
    """, (empresa_id,)).fetchall()

    for p in proveedores:
        nombre = (p['nombre'] or '').strip().lower()

        if not nombre or nombre == 'pendiente ia':
            continue

        if nombre in texto_lower:
            return p['id']

        palabras = [
            w for w in re.findall(r'[a-záéíóúüñ0-9]+', nombre)
            if len(w) >= 4
        ]

        if palabras and sum(1 for w in palabras if w in texto_lower) >= min(2, len(palabras)):
            return p['id']

    # 2️⃣ Detectar proveedor REAL del ticket (solo cabecera)
    candidatos = []

    for linea in texto.splitlines()[:12]:
        limpia = " ".join(linea.strip().split())
        lower = limpia.lower()

        if len(limpia) < 6:
            continue

        if any(x in lower for x in [
            'factura', 'fecha', 'cajero', 'ticket', 'total',
            'tarjeta', 'entregado', 'cambio'
        ]):
            continue

        if re.search(r'\d{5,}', limpia):
            continue

        if any(x in lower for x in [
            's.l', 's.l.', 'sl', 's.a', 'sa',
            'supermercado', 'hipermercado',
            'eurospar', 'mercado'
        ]):
            candidatos.append(limpia)

    if not candidatos:
        return None

    nombre = candidatos[0]
    nombre = re.sub(r'[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 .,&/-]', ' ', nombre)
    nombre = " ".join(nombre.split())[:100]

    existente = conn.execute("""
        SELECT id
        FROM proveedores
        WHERE empresa_id = ?
        AND LOWER(nombre) = LOWER(?)
        LIMIT 1
    """, (empresa_id, nombre)).fetchone()

    if existente:
        return existente['id']

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO proveedores (nombre, activo, empresa_id)
        VALUES (?, 1, ?)
    """, (nombre, empresa_id))

    return cur.lastrowid

def obtener_o_crear_producto_desde_linea(cursor, descripcion, empresa_id, referencia=None):
    descripcion = (descripcion or '').strip()
    referencia = (referencia or '').strip()

    if len(descripcion) < 3:
        return None

    if referencia:
        existente_ref = cursor.execute("""
            SELECT id
            FROM productos
            WHERE empresa_id = ?
              AND (
                    codigo = ?
                    OR codigo_interno = ?
                  )
            LIMIT 1
        """, (empresa_id, referencia, referencia)).fetchone()

        if existente_ref:
            return existente_ref['id']

    palabras = [
        p.lower()
        for p in re.findall(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+', descripcion)
        if len(p) >= 3
    ]

    if palabras:
        productos = cursor.execute("""
            SELECT id, nombre
            FROM productos
            WHERE empresa_id = ?
              AND activo = 1
        """, (empresa_id,)).fetchall()

        mejor_id = None
        mejor_score = 0

        for p in productos:
            nombre = (p['nombre'] or '').lower()
            score = sum(1 for palabra in palabras if palabra in nombre)

            if score > mejor_score:
                mejor_score = score
                mejor_id = p['id']

        if mejor_score >= 2:
            return mejor_id

    cursor.execute("""
        INSERT INTO productos (
            nombre,
            codigo,
            activo,
            empresa_id,
            visible_en_compras,
            visible_en_produccion,
            visible_en_venta,
            permite_compra_directa
        )
        VALUES (?, ?, 1, ?, 1, 0, 0, 1)
    """, (
        descripcion,
        referencia or None,
        empresa_id
    ))

    return cursor.lastrowid


@compras_bp.route('/documentos-compra/<int:documento_id>/procesar')
@login_required
def procesar_documento_compra(documento_id):
    conn = get_db()
    cursor = conn.cursor()
    empresa_id = session.get('user_empresa_id', 1)

    try:
        documento = conn.execute("""
            SELECT *
            FROM documentos_compra
            WHERE id = ?
        """, (documento_id,)).fetchone()

        if not documento:
            conn.close()
            flash('Documento no encontrado.', 'danger')
            return redirect(url_for('compras.validar_recepciones'))

        texto_extraido = extraer_texto_documento_compra(documento['ruta_archivo'])

        proveedor_id = detectar_proveedor_desde_texto(conn, texto_extraido, empresa_id)

        if proveedor_id:
            cursor.execute("""
                UPDATE documentos_compra
                SET proveedor_id = ?
                WHERE id = ?
            """, (proveedor_id, documento_id))

            if documento['recepcion_id']:
                cursor.execute("""
                    UPDATE recepciones
                    SET proveedor_id = ?
                    WHERE id = ?
                """, (proveedor_id, documento['recepcion_id']))

        cursor.execute("""
            UPDATE documentos_compra
            SET texto_extraido = ?,
                procesado_ia = 1
            WHERE id = ?
        """, (texto_extraido, documento_id))

        lineas = detectar_lineas_documento_compra(texto_extraido)

        cursor.execute("""
            DELETE FROM documento_compra_lineas_detectadas
            WHERE documento_id = ?
        """, (documento_id,))

        detectadas = 0
        asociadas = 0
        creadas = 0

        for linea in lineas:
            referencia = linea.get('referencia') or ''
            descripcion = linea.get('descripcion') or ''
            cantidad = linea.get('cantidad') or 0
            precio = linea.get('precio')
            precio_origen = linea.get('precio_origen') or 'ocr'

            producto_id = buscar_producto_por_texto(
                conn,
                descripcion,
                empresa_id,
                referencia=referencia
            )

            estado = 'detectado'

            if producto_id:
                asociadas += 1
            else:
                producto_id = obtener_o_crear_producto_desde_linea(
                    cursor,
                    descripcion,
                    empresa_id,
                    referencia=referencia
                )

                if producto_id:
                    creadas += 1
                    estado = 'producto_creado'

            cursor.execute("""
                INSERT INTO documento_compra_lineas_detectadas (
                    documento_id,
                    producto_id,
                    descripcion_detectada,
                    cantidad_detectada,
                    precio_detectado,
                    estado,
                    observaciones
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                documento_id,
                producto_id,
                descripcion,
                cantidad,
                precio,
                estado,
                f"Referencia: {referencia or '-'} | Precio origen: {precio_origen}"
            ))

            detectadas += 1

        conn.commit()

        flash(
            f'Documento procesado: {detectadas} líneas detectadas, '
            f'{asociadas} asociadas y {creadas} productos creados.',
            'success'
        )

    except Exception as e:
        conn.rollback()
        flash(f'Error procesando documento: {str(e)}', 'danger')

    finally:
        conn.close()

    return redirect(url_for('compras.validar_recepciones'))

@compras_bp.route('/documentos-compra/<int:documento_id>/detalle')
@login_required
def detalle_documento_compra(documento_id):
    conn = get_db()

    documento = conn.execute("""
        SELECT *
        FROM documentos_compra
        WHERE id = ?
    """, (documento_id,)).fetchone()

    conn.close()

    if not documento:
        flash('Documento no encontrado.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    return render_template(
        'compras/detalle_documento_compra.html',
        documento=documento
    ) 
    
@compras_bp.route('/documentos-compra/<int:documento_id>/detectar-lineas')
@login_required
def detectar_lineas_documento(documento_id):
    conn = get_db()
    cursor = conn.cursor()

    documento = conn.execute("""
        SELECT *
        FROM documentos_compra
        WHERE id = ?
    """, (documento_id,)).fetchone()

    if not documento:
        conn.close()
        flash('Documento no encontrado.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    texto = documento['texto_extraido'] or ''

    if not texto:
        conn.close()
        flash('El documento no tiene texto extraído. Procesa primero el documento.', 'warning')
        return redirect(url_for('compras.detalle_documento_compra', documento_id=documento_id))

    cursor.execute("""
        DELETE FROM documento_compra_lineas_detectadas
        WHERE documento_id = ?
    """, (documento_id,))

    lineas = detectar_lineas_documento_compra(texto)

    for linea in lineas:
        cursor.execute("""
            INSERT INTO documento_compra_lineas_detectadas (
                documento_id,
                descripcion_detectada,
                cantidad_detectada,
                precio_detectado,
                estado
            )
            VALUES (?, ?, ?, ?, 'pendiente')
        """, (
            documento_id,
            linea['descripcion'],
            linea['cantidad'],
            linea['precio']
        ))

    conn.commit()
    conn.close()

    flash(f'Se han detectado {len(lineas)} posibles líneas.', 'success')
    return redirect(url_for('compras.revisar_lineas_detectadas', documento_id=documento_id))

@compras_bp.route('/documentos-compra/<int:documento_id>/lineas-detectadas')
@login_required
def revisar_lineas_detectadas(documento_id):
    conn = get_db()

    documento = conn.execute("""
        SELECT *
        FROM documentos_compra
        WHERE id = ?
    """, (documento_id,)).fetchone()

    lineas = conn.execute("""
        SELECT *
        FROM documento_compra_lineas_detectadas
        WHERE documento_id = ?
        ORDER BY id
    """, (documento_id,)).fetchall()

    productos = conn.execute("""
        SELECT id, nombre, codigo_interno
        FROM productos
        WHERE activo = 1
        ORDER BY nombre
    """).fetchall()

    conn.close()

    if not documento:
        flash('Documento no encontrado.', 'danger')
        return redirect(url_for('compras.validar_recepciones'))

    return render_template(
        'compras/lineas_detectadas_documento.html',
        documento=documento,
        lineas=lineas,
        productos=productos
    )   
    
def crear_proveedor_desde_texto(cursor, texto, empresa_id):
    """
    Función conservadora: solo crea proveedor si el nombre es muy claro.
    Si no lo ve claro, devuelve None para mantener PENDIENTE IA.
    """
    if not texto:
        return None

    palabras_prohibidas = [
        'error_', 'pdf_', 'ocr_', 'no module', 'tesseract',
        'sin_texto', 'extraccion', 'extraction', 'fecha', 'cajero',
        'factura', 'ticket', 'total', 'tarjeta', 'entregado', 'cambio',
        'cliente', 'clientes', 'documento', 'artículo', 'articulo',
        'cantidad', 'precio', 'importe', 'base', 'igic', 'iva',
        'pedido', 'terminal', 'autorizacion', 'autorización'
    ]

    indicios_empresa = [
        's.l', 's.l.', 'sl', 's.a', 's.a.', 'sa',
        'supermercado', 'hipermercado', 'ferreteria', 'ferretería'
    ]

    for linea in texto.splitlines()[:10]:
        limpia = " ".join((linea or '').strip().split())
        lower = limpia.lower()

        if len(limpia) < 6:
            continue
        if any(p in lower for p in palabras_prohibidas):
            continue
        if not any(x in lower for x in indicios_empresa):
            continue
        if re.search(r'\b\d{5,}\b', limpia):
            continue

        letras = re.findall(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]', limpia)
        if len(letras) < 6:
            continue

        nombre = re.sub(r'[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 .,&/-]', ' ', limpia)
        nombre = " ".join(nombre.split())[:100]

        existente = cursor.execute("""
            SELECT id
            FROM proveedores
            WHERE empresa_id = ?
              AND LOWER(nombre) = LOWER(?)
            LIMIT 1
        """, (empresa_id, nombre)).fetchone()

        if existente:
            return existente['id']

        cursor.execute("""
            INSERT INTO proveedores (
                nombre,
                activo,
                empresa_id
            )
            VALUES (?, 1, ?)
        """, (nombre, empresa_id))
        return cursor.lastrowid

    return None

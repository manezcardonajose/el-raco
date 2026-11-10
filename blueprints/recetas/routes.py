# blueprints/recetas/routes.py
from flask import render_template, request, redirect, url_for, session, flash
from database import get_db
from datetime import datetime
from . import recetas_bp
from blueprints.auth.routes import login_required, usuario_tiene_permiso
from ia.procesar_pdf import ProcesadorPDF
from ia.alergenos import detectar_alergenos, unir_alergenos
from werkzeug.utils import secure_filename
import csv
import hashlib
import shutil
import json
import os
import re
import unicodedata
import zipfile
from io import BytesIO
from blueprints.menus import routes as menus_routes
from helpers.centros_helper import centro_activo_id
from helpers.receta_traducciones import autocompletar_traducciones
from urllib.parse import quote_plus, unquote, urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from html import unescape
from difflib import SequenceMatcher

# ============================================
# RUTAS DE RECETAS
# ============================================

ENDPOINTS_RECETAS_LECTURA = {
    "recetas.recetas",
    "recetas.ver_receta",
    "recetas.imprimir_receta",
    "recetas.biblioteca_recetas",
    "recetas.analizador_libros",
    "recetas.recetas_libros",
    "recetas.generador_culinario",
    "recetas.asesor_culinario",
    "recetas.fuentes_externas_recetas",
}


@recetas_bp.before_request
def _bloquear_gestion_recetas_sin_permiso():
    if "user_id" not in session:
        return None
    if usuario_tiene_permiso("produccion_editar"):
        return None
    if request.method == "GET" and request.endpoint in ENDPOINTS_RECETAS_LECTURA:
        return None
    flash("Puedes consultar recetas y alergenos, pero no crear, editar ni eliminar.", "warning")
    return redirect(url_for("recetas.recetas"))

FAMILIAS_NO_COCINA = {
    'limpieza', 'papeleria', 'mantenimiento', 'lavanderia', 'amenities',
    'bebidas refrescos', 'bebidas agua', 'bebidas licores', 'bebidas premium'
}

STOP_RECETA = {
    'con', 'sin', 'de', 'del', 'la', 'las', 'los', 'el', 'al', 'a', 'en',
    'y', 'o', 'para', 'por', 'un', 'una', 'variedades', 'variedad', 'gusto',
    'dia', 'menu', 'buffet', 'dados', 'dado', 'tiras', 'rodajas', 'trozos',
    'negro', 'negra', 'verdes', 'verde', 'blanco', 'blanca', 'rojo', 'roja',
    'natural', 'naturales', 'salteado', 'salteada', 'salteadas', 'grill',
    'plancha', 'horno', 'frito', 'frita', 'guisado', 'guisada', 'estofado',
    'estofada', 'cremoso', 'cremosa'
}

MISE_CACHE = os.path.join(os.getcwd(), "instance", "mise_en_place_analisis.json")
BIBLIOTECA_RECETAS_DIR = os.path.join(os.getcwd(), "biblioteca_recetas")
BIBLIOTECA_RECETAS_CACHE = os.path.join(os.getcwd(), "instance", "biblioteca_recetas_propuestas.json")
BIBLIOTECA_RECETAS_EXTRAIDA = os.path.join(os.getcwd(), "instance", "biblioteca_recetas_extraida")
BIBLIOTECA_LIBROS_GRANDES = os.path.join(os.getcwd(), "instance", "biblioteca_libros_grandes")
BIBLIOTECA_ARCHIVOS_MAX_BYTES = 35 * 1024 * 1024


def _columnas_tabla(cursor, tabla):
    try:
        return [row[1] for row in cursor.execute(f"PRAGMA table_info({tabla})").fetchall()]
    except Exception:
        return []


def _safe_add_column(cursor, tabla, definicion):
    nombre = definicion.strip().split()[0]
    if nombre not in _columnas_tabla(cursor, tabla):
        cursor.execute(f"ALTER TABLE {tabla} ADD COLUMN {definicion}")


def _asegurar_recetas_schema(conn):
    cursor = conn.cursor()
    _safe_add_column(cursor, "recetas", "raciones_base REAL DEFAULT 4")
    _safe_add_column(cursor, "recetas", "centro_id INTEGER")
    _safe_add_column(cursor, "recetas", "nombre_en TEXT")
    _safe_add_column(cursor, "recetas", "nombre_fr TEXT")
    _safe_add_column(cursor, "recetas", "nombre_de TEXT")
    _safe_add_column(cursor, "recetas", "nombre_it TEXT")
    _safe_add_column(cursor, "productos", "visible_en_produccion INTEGER DEFAULT 1")
    _safe_add_column(cursor, "productos", "alergenos TEXT")
    _safe_add_column(cursor, "productos", "alergenos_validado INTEGER DEFAULT 0")
    _safe_add_column(cursor, "productos", "centro_id INTEGER")
    _safe_add_column(cursor, "productos", "tipo_producto TEXT")
    _safe_add_column(cursor, "productos", "familia TEXT DEFAULT 'otros'")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recetas_fuentes_propuestas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receta_id INTEGER,
            receta_nombre TEXT,
            fuente_url TEXT,
            fuente_dominio TEXT,
            fuente_titulo TEXT,
            ingredientes_fuente TEXT,
            alergenos_actuales TEXT,
            alergenos_propuestos TEXT,
            alergenos_nuevos TEXT,
            confianza REAL DEFAULT 0,
            estado TEXT DEFAULT 'pendiente',
            notas TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            creado_en DATETIME DEFAULT CURRENT_TIMESTAMP,
            aplicado_en DATETIME,
            FOREIGN KEY (receta_id) REFERENCES recetas(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_recetas_ingredientes_receta ON recetas_ingredientes(receta_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lineas_produccion_receta_parte ON lineas_produccion(receta_id, parte_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_partes_produccion_empresa ON partes_produccion(id, empresa_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_menu_platos_receta_subdep ON menu_platos(receta_id, menu_subdepartamento_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_menu_subdepartamentos_menu ON menu_subdepartamentos(id, menu_diario_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_menus_diarios_empresa ON menus_diarios(id, empresa_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_recetas_fuentes_estado ON recetas_fuentes_propuestas(empresa_id, centro_id, estado)")


def _normalizar(valor):
    texto = str(valor or '').lower()
    texto = texto.replace("ñ", "n")
    texto = unicodedata.normalize('NFKD', texto).encode('ascii', 'ignore').decode('ascii')
    return " ".join(re.findall(r"[a-z0-9]+", texto))


def _float_receta(valor, default=0.0):
    if valor in (None, ""):
        return default
    txt = str(valor).strip().replace("€", "").replace(" ", "")
    if "," in txt and "." in txt:
        txt = txt.replace(".", "").replace(",", ".")
    elif "," in txt:
        txt = txt.replace(",", ".")
    try:
        return float(txt)
    except (TypeError, ValueError):
        return default


def _normalizar_tipo_producto_receta(valor):
    normal = _normalizar(valor)
    if normal in {"5 gama", "quinta gama", "5gama", "v gama"}:
        return "5_gama"
    return (valor or "").strip()


def _obtener_o_crear_producto_receta(conn, empresa_id, centro_id, nombre, unidad="ud", alergenos="", tipo_producto="", cantidad_stock=0):
    nombre = (nombre or "").strip()
    if not nombre:
        return None
    tipo_producto = _normalizar_tipo_producto_receta(tipo_producto)
    familia = "quinta_gama" if tipo_producto == "5_gama" else "otros"
    producto = conn.execute("""
        SELECT id FROM productos
        WHERE empresa_id = ?
          AND (? IS NULL OR centro_id = ?)
          AND LOWER(TRIM(nombre)) = LOWER(TRIM(?))
        ORDER BY CASE WHEN centro_id = ? THEN 0 ELSE 1 END, id
        LIMIT 1
    """, (empresa_id, centro_id, centro_id, nombre, centro_id)).fetchone()
    if producto:
        conn.execute("""
            UPDATE productos
            SET unidad = COALESCE(NULLIF(?, ''), unidad),
                alergenos = COALESCE(NULLIF(?, ''), alergenos),
                tipo_producto = COALESCE(NULLIF(?, ''), tipo_producto),
                familia = CASE WHEN ? = '5_gama' THEN 'quinta_gama' ELSE COALESCE(familia, 'otros') END,
                centro_id = COALESCE(centro_id, ?)
            WHERE id = ? AND empresa_id = ?
        """, (unidad or "", alergenos or "", tipo_producto or "", tipo_producto, centro_id, producto["id"], empresa_id))
        return producto["id"]

    cursor = conn.execute("""
        INSERT INTO productos (
            nombre, unidad, cantidad, precio, activo, empresa_id,
            alergenos, alergenos_validado, centro_id, tipo_producto, familia
        )
        VALUES (?, ?, ?, 0, 1, ?, ?, 0, ?, ?, ?)
    """, (
        nombre,
        unidad or "ud",
        cantidad_stock or 0,
        empresa_id,
        alergenos or "",
        centro_id,
        tipo_producto or None,
        familia,
    ))
    return cursor.lastrowid


def _limpiar_html(texto):
    texto = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", texto or "")
    texto = re.sub(r"(?is)<br\s*/?>", "\n", texto)
    texto = re.sub(r"(?is)</(p|li|h[1-6]|div)>", "\n", texto)
    texto = re.sub(r"(?is)<[^>]+>", " ", texto)
    texto = unescape(texto)
    return re.sub(r"\s+", " ", texto).strip()


def _extraer_texto_tag(html, patron):
    match = re.search(patron, html or "", re.I | re.S)
    return _limpiar_html(match.group(1)) if match else ""


def _iter_jsonld(obj):
    if isinstance(obj, dict):
        yield obj
        graph = obj.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _iter_jsonld(item)
        for value in obj.values():
            if isinstance(value, (dict, list)):
                yield from _iter_jsonld(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_jsonld(item)


def _es_recipe_jsonld(item):
    tipo = item.get("@type") if isinstance(item, dict) else None
    if isinstance(tipo, list):
        tipos = {_normalizar(t) for t in tipo}
        return "recipe" in tipos or "receta" in tipos
    return _normalizar(tipo) in {"recipe", "receta"}


def _texto_instrucciones_jsonld(valor):
    if isinstance(valor, str):
        return valor
    if isinstance(valor, dict):
        return valor.get("text") or valor.get("name") or ""
    if isinstance(valor, list):
        return " | ".join(_texto_instrucciones_jsonld(item) for item in valor if item)
    return ""


def _extraer_receta_desde_html(html, url=""):
    receta = {
        "titulo": "",
        "ingredientes": [],
        "elaboracion": "",
        "texto": "",
        "dominio": urlparse(url).netloc.lower(),
    }
    for bloque in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html or "", re.I | re.S):
        try:
            datos = json.loads(unescape(bloque).strip())
        except Exception:
            continue
        for item in _iter_jsonld(datos):
            if not isinstance(item, dict) or not _es_recipe_jsonld(item):
                continue
            receta["titulo"] = item.get("name") or receta["titulo"]
            ingredientes = item.get("recipeIngredient") or item.get("ingredients") or []
            if isinstance(ingredientes, str):
                ingredientes = [ingredientes]
            receta["ingredientes"] = [str(i).strip() for i in ingredientes if str(i or "").strip()]
            receta["elaboracion"] = _texto_instrucciones_jsonld(item.get("recipeInstructions"))
            break
        if receta["titulo"] or receta["ingredientes"]:
            break

    if not receta["titulo"]:
        receta["titulo"] = (
            _extraer_texto_tag(html, r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']')
            or _extraer_texto_tag(html, r"<title[^>]*>(.*?)</title>")
        )
    texto_limpio = _limpiar_html(html)
    receta["texto"] = texto_limpio[:12000]
    return receta


def _descargar_fuente_receta(url):
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Pega una URL valida con http o https.")
    req = Request(
        url,
        headers={
            "User-Agent": "ElRacoERP/1.0 (+recetas-alergenos)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urlopen(req, timeout=15) as response:
            content_type = response.headers.get("content-type", "")
            if "html" not in content_type and "text" not in content_type:
                raise ValueError("La URL no parece una pagina HTML de receta.")
            data = response.read(2_500_000)
    except HTTPError as exc:
        raise ValueError(f"No he podido leer la pagina: HTTP {exc.code}") from exc
    except URLError as exc:
        raise ValueError(f"No he podido conectar con la fuente: {exc.reason}") from exc
    return data.decode("utf-8", errors="replace")


def _score_receta_fuente(receta, fuente):
    nombre = _normalizar(receta["nombre"])
    titulo = _normalizar(fuente.get("titulo"))
    if not nombre or not titulo:
        return 0.0
    ratio = SequenceMatcher(None, nombre, titulo).ratio()
    tokens_nombre = set(nombre.split())
    tokens_titulo = set(titulo.split())
    inter = tokens_nombre & tokens_titulo
    cobertura = len(inter) / max(1, len(tokens_nombre))
    return round((ratio * 0.55 + cobertura * 0.45) * 100, 1)


def _partes_alergenos(valor):
    return [parte.strip() for parte in re.split(r"[,;/|]+", valor or "") if parte.strip()]


UNIDADES_INGREDIENTE_FUENTE = {
    "kg": ("kg", 1.0),
    "kilo": ("kg", 1.0),
    "kilos": ("kg", 1.0),
    "g": ("kg", 0.001),
    "gr": ("kg", 0.001),
    "grs": ("kg", 0.001),
    "gramo": ("kg", 0.001),
    "gramos": ("kg", 0.001),
    "l": ("l", 1.0),
    "lt": ("l", 1.0),
    "litro": ("l", 1.0),
    "litros": ("l", 1.0),
    "ml": ("l", 0.001),
    "cl": ("l", 0.01),
    "unidad": ("ud", 1.0),
    "unidades": ("ud", 1.0),
    "ud": ("ud", 1.0),
    "uds": ("ud", 1.0),
    "count": ("ud", 1.0),
    "unit": ("ud", 1.0),
    "units": ("ud", 1.0),
    "diente": ("ud", 1.0),
    "dientes": ("ud", 1.0),
    "huevo": ("ud", 1.0),
    "huevos": ("ud", 1.0),
    "cucharada": ("cucharada", 1.0),
    "cucharadas": ("cucharada", 1.0),
    "cucharadita": ("cucharadita", 1.0),
    "cucharaditas": ("cucharadita", 1.0),
}


def _parsear_numero_ingrediente_fuente(valor):
    txt = str(valor or "").strip().lower().replace(",", ".")
    fracciones = {"1/2": 0.5, "1/3": 1 / 3, "1/4": 0.25, "3/4": 0.75}
    if txt in fracciones:
        return fracciones[txt]
    if "/" in txt:
        partes = txt.split("/", 1)
        try:
            return float(partes[0]) / float(partes[1])
        except Exception:
            return 0.0
    try:
        return float(txt)
    except Exception:
        return 0.0


def _parsear_ingrediente_fuente(linea):
    texto = re.sub(r"\s+", " ", str(linea or "").strip(" .;:-"))
    if not texto:
        return None

    match = re.match(
        r"^(?P<cantidad>\d+(?:[,.]\d+)?|\d+/\d+)\s*(?P<unidad>[a-zA-ZáéíóúÁÉÍÓÚñÑ\.]+)?\s+(?:de\s+|del\s+|la\s+|el\s+)?(?P<nombre>.+)$",
        texto,
    )
    if not match:
        match_nombre_primero = re.match(
            r"^(?P<nombre>.+?)\s+(?P<cantidad>\d+(?:[,.]\d+)?|\d+/\d+)\s*(?P<unidad>kg|kilos?|g|grs?|l|lt|litros?|ml|cl|ud|uds|unidad(?:es)?)?\s*(?:\d+(?:[,.]\d+)?)?$",
            texto,
            re.I,
        )
        if not match_nombre_primero:
            return None
        cantidad = _parsear_numero_ingrediente_fuente(match_nombre_primero.group("cantidad"))
        if cantidad <= 0:
            return None
        unidad_raw = _normalizar((match_nombre_primero.group("unidad") or "kg").replace(".", ""))
        unidad, factor = UNIDADES_INGREDIENTE_FUENTE.get(unidad_raw, (unidad_raw or "kg", 1.0))
        nombre = re.sub(r"\s*\([^)]*\)", "", match_nombre_primero.group("nombre") or "").strip(" .;:-")
        nombre = re.sub(r"^(de|del|la|el|los|las)\s+", "", nombre, flags=re.I).strip()
        if len(nombre) < 2 or _linea_memoria_ruido(nombre):
            return None
        return {
            "nombre": nombre,
            "cantidad": round(cantidad * factor, 4),
            "unidad": unidad,
            "linea": texto,
        }

    cantidad = _parsear_numero_ingrediente_fuente(match.group("cantidad"))
    if cantidad <= 0:
        return None

    unidad_raw = _normalizar((match.group("unidad") or "ud").replace(".", ""))
    unidad, factor = UNIDADES_INGREDIENTE_FUENTE.get(unidad_raw, (unidad_raw or "ud", 1.0))
    nombre = re.sub(r"\s*\([^)]*\)", "", match.group("nombre") or "").strip(" .;:-")
    nombre = re.sub(r"^(de|del|la|el|los|las)\s+", "", nombre, flags=re.I).strip()
    if len(nombre) < 2:
        return None

    return {
        "nombre": nombre,
        "cantidad": round(cantidad * factor, 4),
        "unidad": unidad,
        "linea": texto,
    }


def _previsualizar_ingredientes_fuente(ingredientes_txt):
    lineas = [linea for linea in (ingredientes_txt or "").splitlines() if linea.strip()]
    creables = []
    omitidas = []
    for linea in lineas:
        item = _parsear_ingrediente_fuente(linea)
        if item:
            creables.append(item)
        else:
            omitidas.append(linea.strip())
    return {
        "total": len(lineas),
        "creables": len(creables),
        "omitidas": len(omitidas),
        "creables_detalle": creables[:8],
        "omitidas_detalle": omitidas[:8],
    }


def _aplicar_ingredientes_fuente_a_receta(conn, empresa_id, receta, propuesta):
    existentes = conn.execute(
        "SELECT COUNT(*) AS total FROM recetas_ingredientes WHERE receta_id = ?",
        (receta["id"],),
    ).fetchone()
    if _float_receta(existentes["total"] if existentes else 0, 0) > 0:
        return {"creados": 0, "omitidos": 0, "motivo": "La receta ya tenia ingredientes."}

    ingredientes_txt = (propuesta["ingredientes_fuente"] or "").strip()
    if not ingredientes_txt:
        return {"creados": 0, "omitidos": 0, "motivo": "La fuente no traia ingredientes."}

    centro_id = receta["centro_id"] if "centro_id" in receta.keys() else None
    creados = 0
    omitidos = 0
    for linea in ingredientes_txt.splitlines():
        item = _parsear_ingrediente_fuente(linea)
        if not item:
            omitidos += 1
            continue
        alergenos = unir_alergenos(detectar_alergenos(item["nombre"], item["linea"]))
        producto_id = _obtener_o_crear_producto_receta(
            conn,
            empresa_id,
            centro_id,
            item["nombre"],
            unidad=item["unidad"],
            alergenos=alergenos,
        )
        if not producto_id:
            omitidos += 1
            continue
        existente = conn.execute("""
            SELECT id
            FROM recetas_ingredientes
            WHERE receta_id = ? AND producto_id = ?
            LIMIT 1
        """, (receta["id"], producto_id)).fetchone()
        if existente:
            conn.execute(
                "UPDATE recetas_ingredientes SET cantidad = ? WHERE id = ?",
                (item["cantidad"], existente["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO recetas_ingredientes (receta_id, producto_id, cantidad) VALUES (?, ?, ?)",
                (receta["id"], producto_id, item["cantidad"]),
            )
        creados += 1

    return {"creados": creados, "omitidos": omitidos, "motivo": ""}


DOMINIOS_RECETAS_PERMITIDOS = {
    "www.directoalpaladar.com",
    "directoalpaladar.com",
}


def _recetas_pendientes_busqueda_automatica(conn, empresa_id, centro_id=None, limite=20):
    where = [
        "r.empresa_id = ?",
        """(
            NOT EXISTS (SELECT 1 FROM recetas_ingredientes ri WHERE ri.receta_id = r.id)
            OR TRIM(COALESCE(r.alergenos, '')) = ''
        )""",
    ]
    params = [empresa_id]
    if centro_id:
        where.append("(r.centro_id = ? OR r.centro_id IS NULL)")
        params.append(centro_id)

    return conn.execute(f"""
        SELECT r.id, r.nombre, r.descripcion, r.categoria, r.alergenos, r.centro_id,
               CASE WHEN EXISTS (
                    SELECT 1
                    FROM lineas_produccion lp
                    JOIN partes_produccion pp ON pp.id = lp.parte_id
                    WHERE lp.receta_id = r.id AND pp.empresa_id = r.empresa_id
               ) THEN 1 ELSE 0 END AS usada_produccion,
               CASE WHEN EXISTS (
                    SELECT 1
                    FROM menu_platos mp
                    JOIN menu_subdepartamentos ms ON ms.id = mp.menu_subdepartamento_id
                    JOIN menus_diarios md ON md.id = ms.menu_diario_id
                    WHERE mp.receta_id = r.id AND md.empresa_id = r.empresa_id
               ) THEN 1 ELSE 0 END AS usada_menu,
               (SELECT COUNT(*) FROM recetas_ingredientes ri WHERE ri.receta_id = r.id) AS ingredientes
        FROM recetas r
        WHERE {" AND ".join(where)}
        ORDER BY usada_produccion DESC, usada_menu DESC, LOWER(r.nombre)
        LIMIT ?
    """, params + [limite]).fetchall()


def _insertar_propuesta_fuente_receta(
    conn,
    empresa_id,
    centro_id,
    receta,
    fuente,
    confianza,
    notas,
):
    ingredientes = "\n".join(fuente.get("ingredientes") or [])
    detectados = unir_alergenos([", ".join(detectar_alergenos(
        fuente.get("titulo", ""),
        ingredientes,
        fuente.get("elaboracion", ""),
        fuente.get("texto", "")[:4000],
    ))])
    if not detectados:
        detectados = unir_alergenos([receta["alergenos"] or ""])

    url = fuente.get("url", "")
    existente = conn.execute("""
        SELECT id
        FROM recetas_fuentes_propuestas
        WHERE empresa_id = ?
          AND receta_id = ?
          AND fuente_url = ?
          AND estado = 'pendiente'
        LIMIT 1
    """, (empresa_id, receta["id"], url)).fetchone()
    if existente:
        return False

    actuales = receta["alergenos"] or ""
    nuevos = unir_alergenos([
        alergeno
        for alergeno in _partes_alergenos(detectados)
        if _normalizar(alergeno) not in {_normalizar(a) for a in _partes_alergenos(actuales)}
    ])
    conn.execute("""
        INSERT INTO recetas_fuentes_propuestas (
            receta_id, receta_nombre, fuente_url, fuente_dominio, fuente_titulo,
            ingredientes_fuente, alergenos_actuales, alergenos_propuestos,
            alergenos_nuevos, confianza, estado, notas, empresa_id, centro_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', ?, ?, ?)
    """, (
        receta["id"],
        receta["nombre"],
        url,
        fuente.get("dominio", ""),
        fuente.get("titulo", ""),
        ingredientes[:6000],
        actuales,
        detectados,
        nuevos or detectados,
        max(0.0, min(100.0, float(confianza or 0))),
        notas,
        empresa_id,
        centro_id,
    ))
    return True


def _fuente_desde_propuesta_biblioteca(propuesta):
    calidad = _calidad_propuesta_memoria(propuesta)
    return {
        "titulo": propuesta.get("nombre", ""),
        "ingredientes": calidad["ingredientes_limpios"][:40],
        "elaboracion": propuesta.get("elaboracion", ""),
        "texto": " ".join([
            propuesta.get("nombre", ""),
            " ".join(calidad["ingredientes_limpios"][:40]),
            propuesta.get("elaboracion", ""),
        ]),
        "dominio": "biblioteca-interna",
        "url": f"biblioteca:{propuesta.get('id', '')}",
    }


def _buscar_candidatos_biblioteca_para_receta(datos, receta, limite=3):
    candidatos = _filtrar_memoria_culinaria(datos, receta["nombre"], "", "", "")
    tokens_receta = set(_tokens_busqueda_biblioteca(receta["nombre"]))
    resultado = []
    for candidato in candidatos[:12]:
        calidad = _calidad_propuesta_memoria(candidato)
        if calidad["score"] < 58 or not calidad["ingredientes_limpios"]:
            continue
        fuente = _fuente_desde_propuesta_biblioteca(candidato)
        score_titulo = _score_receta_fuente(receta, fuente)
        tokens_fuente = set(_tokens_busqueda_biblioteca(fuente.get("titulo", "")))
        coincidencias_titulo = tokens_receta & tokens_fuente
        ratio_titulo = SequenceMatcher(None, _normalizar(receta["nombre"]), _normalizar(fuente.get("titulo", ""))).ratio()
        min_coincidencias = max(1, min(3, len(tokens_receta)))
        if len(tokens_receta) >= 2:
            min_coincidencias = min(3, len(tokens_receta))
        if len(coincidencias_titulo) < min_coincidencias and ratio_titulo < 0.84:
            continue
        score = min(95.0, float(score_titulo) + min(10.0, float(calidad["score"]) / 12.0))
        resultado.append((score, candidato))
        if len(resultado) >= limite:
            break
    if not resultado:
        recetas_validas = _propuestas_memoria_validas(datos)
        nombre_receta = _normalizar(receta["nombre"])
        for candidato in recetas_validas:
            calidad = _calidad_propuesta_memoria(candidato)
            if calidad["score"] < 62 or len(calidad["ingredientes_limpios"]) < 2:
                continue
            fuente = _fuente_desde_propuesta_biblioteca(candidato)
            nombre_fuente = _normalizar(fuente.get("titulo", ""))
            tokens_fuente = set(_tokens_busqueda_biblioteca(fuente.get("titulo", "")))
            coincidencias = tokens_receta & tokens_fuente
            ratio_titulo = SequenceMatcher(None, nombre_receta, nombre_fuente).ratio()
            min_coincidencias = 2 if len(tokens_receta) >= 2 else 1
            if ratio_titulo < 0.84 and len(coincidencias) < min_coincidencias:
                continue
            score = min(82.0, max(58.0, ratio_titulo * 70 + len(coincidencias) * 8))
            resultado.append((score, candidato))
            if len(resultado) >= limite:
                break
    return resultado


def _limpiar_url_resultado_receta(href):
    href = unescape(href or "")
    if "uddg=" in href:
        match = re.search(r"uddg=([^&]+)", href)
        if match:
            href = unquote(match.group(1))
    if not href.startswith("http"):
        return ""
    parsed = urlparse(href)
    dominio = parsed.netloc.lower().replace("m.directoalpaladar.com", "www.directoalpaladar.com")
    if dominio not in DOMINIOS_RECETAS_PERMITIDOS:
        return ""
    return href.split("#", 1)[0]


def _urls_busqueda_web_receta(nombre, max_urls=3):
    consulta = quote_plus(f"site:directoalpaladar.com {nombre} receta")
    buscadores = [
        f"https://duckduckgo.com/html/?q={consulta}",
        f"https://www.bing.com/search?q={consulta}",
    ]
    urls = []
    for url_busqueda in buscadores:
        req = Request(
            url_busqueda,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        try:
            with urlopen(req, timeout=12) as response:
                html = response.read(1_500_000).decode("utf-8", errors="replace")
        except Exception:
            continue

        for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
            limpio = _limpiar_url_resultado_receta(href)
            if not limpio or limpio in urls:
                continue
            urls.append(limpio)
            if len(urls) >= max_urls:
                return urls
    return urls


def _buscar_fuentes_web_para_receta(receta, limite=1):
    fuentes = []
    for url in _urls_busqueda_web_receta(receta["nombre"], max_urls=max(5, limite * 3)):
        try:
            html = _descargar_fuente_receta(url)
            fuente = _extraer_receta_desde_html(html, url)
            fuente["url"] = url
        except Exception:
            continue
        if not fuente.get("ingredientes"):
            continue
        score = _score_receta_fuente(receta, fuente)
        if score < 60:
            continue
        fuentes.append((score, fuente))
    fuentes.sort(key=lambda item: item[0], reverse=True)
    return fuentes[:limite]


RECETA_DIRECTA_TOKENS = {
    "pepino", "pepinillo", "cebolleta", "cebollita", "aceituna", "maiz",
    "pepinilos",
    "lechuga", "tomate", "zanahoria", "remolacha", "esparrago",
    "jamon", "york", "queso", "fiambre", "chorizo", "salchichon",
    "fruta", "naranja", "manzana", "platano", "melon", "sandia",
    "albaricoque", "pera", "kiwi", "uva", "melocoton", "piña", "pina",
    "yogur", "pan", "mantequilla", "mermelada", "cereal", "croissant",
}

RECETA_GENERICA_TOKENS = {
    "tipo", "tipos", "proteina", "crudites", "guarnicion", "salsa",
    "varios", "varias", "otra", "otros", "seleccion", "surtido",
}

RECETA_CORTE_TOKENS = {
    "rodaja", "rodajas", "tira", "tiras", "dados", "lamina", "laminas",
    "loncha", "lonchas", "trozo", "trozos", "gajo", "gajos",
}


def _ingrediente_directo_desde_receta(nombre):
    normal = _normalizar(nombre)
    tokens = normal.split()
    if not tokens:
        return ""
    if any(token.isdigit() for token in tokens):
        return ""
    if any(token in RECETA_GENERICA_TOKENS for token in tokens):
        return ""
    if not any(token in RECETA_DIRECTA_TOKENS for token in tokens):
        return ""
    filtrados = [
        token for token in tokens
        if token not in STOP_RECETA
        and token not in RECETA_CORTE_TOKENS
        and token not in {"natural", "naturales", "fresco", "fresca", "cortado", "cortada"}
    ]
    if not filtrados:
        return ""
    if any(token not in RECETA_DIRECTA_TOKENS for token in filtrados):
        return ""
    if "jamon" in filtrados and "york" in filtrados:
        return "Jamon york"
    if "fruta" in filtrados and len(filtrados) <= 3:
        return "Fruta variada"
    return " ".join(filtrados[:4]).title()


def _insertar_propuesta_receta_directa(conn, empresa_id, centro_id, receta):
    ingrediente = _ingrediente_directo_desde_receta(receta["nombre"])
    if not ingrediente:
        return False
    url = f"interno:receta-directa:{receta['id']}"
    existente = conn.execute("""
        SELECT id
        FROM recetas_fuentes_propuestas
        WHERE empresa_id = ?
          AND receta_id = ?
          AND fuente_url = ?
          AND estado = 'pendiente'
        LIMIT 1
    """, (empresa_id, receta["id"], url)).fetchone()
    if existente:
        return False
    alergenos = unir_alergenos([", ".join(detectar_alergenos(ingrediente, receta["nombre"]))])
    conn.execute("""
        INSERT INTO recetas_fuentes_propuestas (
            receta_id, receta_nombre, fuente_url, fuente_dominio, fuente_titulo,
            ingredientes_fuente, alergenos_actuales, alergenos_propuestos,
            alergenos_nuevos, confianza, estado, notas, empresa_id, centro_id
        )
        VALUES (?, ?, ?, 'ERP interna', ?, ?, ?, ?, ?, 88, 'pendiente', ?, ?, ?)
    """, (
        receta["id"],
        receta["nombre"],
        url,
        "Receta directa desde nombre de plato",
        f"1 kg {ingrediente}",
        receta["alergenos"] or "",
        alergenos,
        alergenos,
        "Propuesta interna: plato servido tal cual o corte simple. Validar unidad, gramaje y alergeno antes de aplicar.",
        empresa_id,
        centro_id,
    ))
    return True


def _crear_propuestas_busqueda_automatica_recetas(
    conn,
    empresa_id,
    centro_id=None,
    limite_recetas=10,
    usar_web=True,
):
    _asegurar_recetas_schema(conn)
    datos = _cargar_biblioteca_recetas()
    recetas = _recetas_pendientes_busqueda_automatica(conn, empresa_id, centro_id, limite_recetas)
    creadas_directas = 0
    creadas_biblioteca = 0
    creadas_web = 0
    sin_resultado = 0
    revisadas = 0

    for receta in recetas:
        revisadas += 1
        creada = False
        if _insertar_propuesta_receta_directa(conn, empresa_id, centro_id, receta):
            creadas_directas += 1
            creada = True

        if creada:
            continue

        for score, candidato in _buscar_candidatos_biblioteca_para_receta(datos, receta):
            fuente = _fuente_desde_propuesta_biblioteca(candidato)
            if _insertar_propuesta_fuente_receta(
                conn,
                empresa_id,
                centro_id,
                receta,
                fuente,
                score,
                "Busqueda automatica en biblioteca privada. Revisar antes de aplicar.",
            ):
                creadas_biblioteca += 1
                creada = True

        if usar_web and not creada:
            for score, fuente in _buscar_fuentes_web_para_receta(receta):
                if _insertar_propuesta_fuente_receta(
                    conn,
                    empresa_id,
                    centro_id,
                    receta,
                    fuente,
                    score,
                    "Busqueda automatica externa en dominio permitido. Revisar fuente e ingredientes antes de aplicar.",
                ):
                    creadas_web += 1
                    creada = True

        if not creada:
            sin_resultado += 1

    return {
        "revisadas": revisadas,
        "directas": creadas_directas,
        "biblioteca": creadas_biblioteca,
        "web": creadas_web,
        "sin_resultado": sin_resultado,
    }


def _crear_propuestas_fuente_recetas(conn, empresa_id, centro_id, fuente, limite=25):
    textos_alergenos = [
        fuente.get("titulo", ""),
        "\n".join(fuente.get("ingredientes") or []),
        fuente.get("elaboracion", ""),
        fuente.get("texto", "")[:4000],
    ]
    detectados = unir_alergenos([", ".join(detectar_alergenos(*textos_alergenos))])
    if not detectados:
        return {"creadas": 0, "mensaje": "La fuente no ha dado alergenos claros.", "detectados": ""}

    where = ["empresa_id = ?", "TRIM(COALESCE(alergenos, '')) = ''"]
    params = [empresa_id]
    if centro_id:
        where.append("(centro_id = ? OR centro_id IS NULL)")
        params.append(centro_id)

    recetas_rows = conn.execute(f"""
        SELECT id, nombre, descripcion, categoria, alergenos
        FROM recetas
        WHERE {" AND ".join(where)}
    """, params).fetchall()

    candidatas = []
    for receta in recetas_rows:
        score = _score_receta_fuente(receta, fuente)
        tokens_receta = set(_tokens_busqueda_biblioteca(receta["nombre"]))
        tokens_fuente = set(_tokens_busqueda_biblioteca(fuente.get("titulo", "")))
        coincidencias = tokens_receta & tokens_fuente
        ratio_titulo = SequenceMatcher(None, _normalizar(receta["nombre"]), _normalizar(fuente.get("titulo", ""))).ratio()
        if score >= 58 and (len(coincidencias) >= 2 or ratio_titulo >= 0.68):
            candidatas.append((score, receta))
    candidatas.sort(key=lambda item: item[0], reverse=True)

    creadas = 0
    ingredientes = "\n".join(fuente.get("ingredientes") or [])
    dominio = fuente.get("dominio", "")
    url = fuente.get("url", "")
    for score, receta in candidatas[:limite]:
        actuales = receta["alergenos"] or ""
        nuevos = unir_alergenos([
            alergeno
            for alergeno in _partes_alergenos(detectados)
            if _normalizar(alergeno) not in {_normalizar(a) for a in _partes_alergenos(actuales)}
        ])
        if not nuevos and actuales:
            continue
        existente = conn.execute("""
            SELECT id FROM recetas_fuentes_propuestas
            WHERE empresa_id = ?
              AND receta_id = ?
              AND fuente_url = ?
              AND estado = 'pendiente'
            LIMIT 1
        """, (empresa_id, receta["id"], url)).fetchone()
        if existente:
            continue
        conn.execute("""
            INSERT INTO recetas_fuentes_propuestas (
                receta_id, receta_nombre, fuente_url, fuente_dominio, fuente_titulo,
                ingredientes_fuente, alergenos_actuales, alergenos_propuestos,
                alergenos_nuevos, confianza, estado, notas, empresa_id, centro_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', ?, ?, ?)
        """, (
            receta["id"],
            receta["nombre"],
            url,
            dominio,
            fuente.get("titulo", ""),
            ingredientes[:6000],
            actuales,
            detectados,
            nuevos or detectados,
            score,
            "Propuesta externa: revisar ingredientes antes de aplicar.",
            empresa_id,
            centro_id,
        ))
        creadas += 1
    return {"creadas": creadas, "mensaje": "", "detectados": detectados}


def _crear_propuestas_internas_alergenos(conn, empresa_id, centro_id=None, limite=250):
    where = ["r.empresa_id = ?", "TRIM(COALESCE(r.alergenos, '')) = ''"]
    params = [empresa_id]
    if centro_id:
        where.append("(r.centro_id = ? OR r.centro_id IS NULL)")
        params.append(centro_id)

    recetas_rows = conn.execute(f"""
        SELECT r.id, r.nombre, r.descripcion, r.categoria, r.centro_id,
               GROUP_CONCAT(p.nombre, ' | ') AS ingredientes,
               GROUP_CONCAT(COALESCE(p.alergenos, ''), ' | ') AS alergenos_productos
        FROM recetas r
        LEFT JOIN recetas_ingredientes ri ON ri.receta_id = r.id
        LEFT JOIN productos p ON p.id = ri.producto_id
        WHERE {" AND ".join(where)}
        GROUP BY r.id
        ORDER BY r.nombre
        LIMIT ?
    """, params + [limite]).fetchall()

    creadas = 0
    sin_deteccion = 0
    for receta in recetas_rows:
        textos = [
            receta["nombre"],
            receta["descripcion"],
            receta["categoria"],
            receta["ingredientes"],
        ]
        analisis_nombre = detectar_alergenos(*textos)
        detectados_nombre = analisis_nombre
        alergenos = unir_alergenos([receta["alergenos_productos"], ", ".join(detectados_nombre)])
        if not alergenos:
            sin_deteccion += 1
            continue

        existente = conn.execute("""
            SELECT id
            FROM recetas_fuentes_propuestas
            WHERE empresa_id = ?
              AND receta_id = ?
              AND fuente_url = 'interno:ingredientes-productos'
              AND estado = 'pendiente'
            LIMIT 1
        """, (empresa_id, receta["id"])).fetchone()
        if existente:
            continue

        evidencias = []
        if receta["alergenos_productos"]:
            evidencias.append("alergenos ya presentes en productos")
        if detectados_nombre:
            evidencias.append("palabras detectadas en nombre/ingredientes")
        confianza = 86 if receta["alergenos_productos"] else 68
        ingredientes_con_evidencia = []
        for ingrediente in (receta["ingredientes"] or "").split(" | "):
            alergenos_ing = unir_alergenos([", ".join(detectar_alergenos(ingrediente))])
            if alergenos_ing:
                ingredientes_con_evidencia.append(f"{ingrediente}: {alergenos_ing}")
            else:
                ingredientes_con_evidencia.append(ingrediente)

        conn.execute("""
            INSERT INTO recetas_fuentes_propuestas (
                receta_id, receta_nombre, fuente_url, fuente_dominio, fuente_titulo,
                ingredientes_fuente, alergenos_actuales, alergenos_propuestos,
                alergenos_nuevos, confianza, estado, notas, empresa_id, centro_id
            )
            VALUES (?, ?, 'interno:ingredientes-productos', 'ERP interna', ?, ?, '', ?, ?, ?, 'pendiente', ?, ?, ?)
        """, (
            receta["id"],
            receta["nombre"],
            "Ingredientes y productos de la ERP",
            "\n".join(ingredientes_con_evidencia) or receta["ingredientes"] or "",
            alergenos,
            alergenos,
            confianza,
            "Propuesta interna revisable: " + ", ".join(evidencias),
            empresa_id,
            receta["centro_id"] or centro_id,
        ))
        creadas += 1

    return {"creadas": creadas, "sin_deteccion": sin_deteccion, "revisadas": len(recetas_rows)}


def _tokens(valor):
    resultado = set()
    for token in _normalizar(valor).split():
        if len(token) <= 2 or token in STOP_RECETA:
            continue
        resultado.add(token)
        if len(token) > 4 and token.endswith('es'):
            resultado.add(token[:-2])
        if len(token) > 3 and token.endswith('s'):
            resultado.add(token[:-1])
    return resultado


def _cantidad_ingrediente_4_raciones(producto, receta_nombre='', total_ingredientes=1):
    nombre = _normalizar(producto['nombre'])
    familia = _normalizar(producto['familia'])
    unidad = _normalizar(producto['unidad'])
    receta_tokens = _tokens(receta_nombre)
    producto_tokens = _tokens(producto['nombre'])
    es_nombre_receta = bool(receta_tokens & producto_tokens)

    if unidad in {'ud', 'unidad', 'unidades'}:
        if any(x in nombre for x in ['huevo']):
            return 4.0
        return 4.0 if es_nombre_receta else 1.0

    if unidad in {'l', 'litro', 'litros'}:
        if any(x in nombre for x in ['aceite']):
            return 0.05
        if any(x in nombre for x in ['vino', 'nata', 'leche', 'caldo']):
            return 0.20 if es_nombre_receta else 0.10
        return 0.25 if es_nombre_receta else 0.10

    if unidad in {'lata', 'bote', 'botella'}:
        return 1.0

    if unidad in {'bolsa', 'paquete'}:
        return 1.0 if es_nombre_receta else 0.5

    if any(x in nombre for x in ['sal', 'pimienta', 'comino', 'curry', 'pimenton', 'canela', 'azafran', 'laurel', 'oregano']):
        return 0.01
    if any(x in nombre for x in ['ajo', 'perejil']):
        return 0.04
    if any(x in nombre for x in ['aceite']):
        return 0.05
    if any(x in nombre for x in ['vino', 'vinagre']):
        return 0.08
    if any(x in nombre for x in ['caldo', 'salsa', 'tomate triturado']):
        return 0.25

    if familia in {'carnes', 'pescados', 'mariscos'}:
        return 0.80 if es_nombre_receta or total_ingredientes <= 3 else 0.45
    if familia in {'pastas_arroces'}:
        return 0.36 if es_nombre_receta or total_ingredientes <= 4 else 0.22
    if familia in {'legumbres'}:
        return 0.45 if es_nombre_receta or total_ingredientes <= 4 else 0.25
    if familia in {'verduras'}:
        return 0.70 if es_nombre_receta or total_ingredientes <= 3 else 0.18
    if familia in {'frutas'}:
        return 0.65 if es_nombre_receta or total_ingredientes <= 3 else 0.20
    if familia in {'lacteos'}:
        return 0.35 if es_nombre_receta else 0.18
    if familia in {'panaderia', 'frutos secos', 'frutos_secos'}:
        return 0.20 if es_nombre_receta else 0.10

    return 0.30 if es_nombre_receta or total_ingredientes <= 2 else 0.08


def _buscar_producto(catalogo, texto, familias_preferidas=None):
    objetivo = _tokens(texto)
    if not objetivo:
        return None
    familias_preferidas = set(familias_preferidas or [])
    candidatos = []
    for p in catalogo:
        if p['familia_norm'] in FAMILIAS_NO_COCINA:
            continue
        pt = p['tokens']
        if not pt:
            continue
        inter = objetivo & pt
        if not inter:
            continue
        # Si solo coincide una palabra generica de preparacion, no vale.
        if len(inter) == 1:
            palabra = next(iter(inter))
            if palabra in {'dado', 'trozo', 'tira', 'rodaja', 'natural'}:
                continue
        score = len(inter) * 10
        if p['familia_norm'] in familias_preferidas:
            score += 5
        if _normalizar(texto) == p['nombre_norm']:
            score += 50
        if p['nombre_norm'] in _normalizar(texto) or _normalizar(texto) in p['nombre_norm']:
            score += 15
        score -= max(len(pt) - len(inter), 0)
        candidatos.append((score, p))
    if not candidatos:
        return None
    candidatos.sort(key=lambda x: x[0], reverse=True)
    return candidatos[0][1] if candidatos[0][0] >= 8 else None


def _catalogo_productos(conn, empresa_id):
    productos = []
    centro_id = centro_activo_id()
    centro_sql = " AND centro_id = ?" if centro_id else ""
    params = [empresa_id] + ([centro_id] if centro_id else [])
    rows = conn.execute(f"""
        SELECT id, nombre, familia, unidad, tipo_conservacion, precio
        FROM productos
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND COALESCE(visible_en_produccion, 1) = 1
          {centro_sql}
        ORDER BY nombre
    """, params).fetchall()
    for row in rows:
        item = dict(row)
        item['nombre_norm'] = _normalizar(row['nombre'])
        item['familia_norm'] = _normalizar(row['familia'])
        item['tokens'] = _tokens(row['nombre'])
        productos.append(item)
    return productos


def _guardar_analisis_mise(analisis):
    os.makedirs(os.path.dirname(MISE_CACHE), exist_ok=True)
    with open(MISE_CACHE, "w", encoding="utf-8") as fh:
        json.dump(analisis, fh, ensure_ascii=False, indent=2)


def _cargar_analisis_mise():
    if not os.path.exists(MISE_CACHE):
        return None
    try:
        with open(MISE_CACHE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _to_int(value, default=0):
    try:
        if value in (None, ""):
            return default
        return int(value)
    except Exception:
        return default


def _ingredientes_textarea_mise(texto):
    ingredientes = []
    vistos = set()
    for linea in str(texto or "").replace(";", "\n").splitlines():
        nombre = " ".join(linea.strip(" -\t,;").split())
        if not nombre:
            continue
        clave = _normalizar(nombre)
        if clave and clave not in vistos:
            ingredientes.append(nombre[:180])
            vistos.add(clave)
    return ingredientes


def _nombre_mise_plato_valido(nombre):
    nombre = " ".join(str(nombre or "").split())
    norm = _normalizar(nombre)
    if not norm:
        return False
    letras = sum(c.isalpha() for c in nombre)
    digitos = sum(c.isdigit() for c in nombre)
    if letras < 5 or letras / max(len(nombre), 1) < 0.58:
        return False
    if digitos >= 3:
        return False
    if len(re.findall(r"[<>/\\\\|_{}\\[\\]~^]", nombre)) >= 2:
        return False
    tokens = set(norm.split())
    if tokens & {"poo", "doo", "sss", "ctt", "mmm", "onop", "oooo"}:
        return False
    compacta = norm.replace(" ", "")
    ingredientes_base = [
        "masafresca", "masadepizza", "salsadetomate", "quesorallado",
        "quesoricotta", "reducciondebalsamico", "aceitedeoliva",
    ]
    pistas_plato = [
        "pizza", "risotto", "rissoto", "lasana", "lasaña", "canelon",
        "berenjena", "ensalada", "tiramisu", "pannacota", "coulat",
        "solomillo", "rodaballo", "carpaccio", "saltimboca",
    ]
    es_base = any(x in norm or x in compacta for x in ingredientes_base)
    tiene_pista = any(x in norm or x in compacta for x in pistas_plato)
    return not (es_base and not tiene_pista)


def _analisis_mise_desde_revision(form, analisis_base=None):
    base = analisis_base or {}
    hojas = []
    total_ingredientes = 0
    total_hojas = max(_to_int(form.get("hoja_count"), 0), 0)
    for idx in range(total_hojas):
        if form.get(f"hoja_{idx}_incluir") != "1":
            continue
        seccion = (form.get(f"hoja_{idx}_nombre") or f"Seccion {idx + 1}").strip()[:120]
        pagina = _to_int(form.get(f"hoja_{idx}_pagina"), idx + 1)
        platos = []
        ingredientes_hoja = []
        total_platos = max(_to_int(form.get(f"hoja_{idx}_plato_count"), 0), 0)
        for plato_idx in range(total_platos):
            if form.get(f"hoja_{idx}_plato_{plato_idx}_incluir") != "1":
                continue
            nombre = (form.get(f"hoja_{idx}_plato_{plato_idx}_nombre") or "").strip()[:180]
            if not nombre or not _nombre_mise_plato_valido(nombre):
                continue
            ingredientes = _ingredientes_textarea_mise(form.get(f"hoja_{idx}_plato_{plato_idx}_ingredientes"))
            total_ingredientes += len(ingredientes)
            ingredientes_hoja.extend(ingredientes)
            platos.append({
                "nombre": nombre,
                "ingredientes": ingredientes,
                "estado": "revisado",
            })
        if not platos:
            continue
        hojas.append({
            "pagina": pagina,
            "nombre": seccion,
            "metodo": "revision",
            "recetas_detectadas": [p["nombre"] for p in platos],
            "recetas_detalle": platos,
            "ingredientes": ingredientes_hoja,
            "alergenos": ProcesadorPDF().buscar_alergenos(" ".join([seccion] + ingredientes_hoja)),
            "texto": "",
        })
    return {
        "archivo": base.get("archivo") or "",
        "paginas": base.get("paginas") or len(hojas),
        "hojas": hojas,
        "total_ingredientes": total_ingredientes,
        "ocr_disponible": base.get("ocr_disponible", True),
        "revisado": True,
    }


def _producto_por_nombre(conn, empresa_id, nombre):
    normal = _normalizar(nombre)
    if not normal:
        return None
    productos = conn.execute(
        "SELECT id, nombre FROM productos WHERE empresa_id = ?",
        (empresa_id,),
    ).fetchall()
    for producto in productos:
        if _normalizar(producto["nombre"]) == normal:
            return producto
    return None


def _crear_o_actualizar_receta_mise(conn, empresa_id, hoja):
    nombre = (hoja.get("nombre") or "").strip()[:180]
    if not nombre:
        return None, False
    ingredientes = hoja.get("ingredientes") or []
    descripcion = "Importada desde hoja de mise en place.\n\nIngredientes detectados:\n- " + "\n- ".join(ingredientes)
    alergenos = ", ".join(hoja.get("alergenos") or [])
    receta = conn.execute(
        "SELECT * FROM recetas WHERE empresa_id = ? AND LOWER(nombre) = LOWER(?)",
        (empresa_id, nombre),
    ).fetchone()
    creada = False
    if receta:
        receta_id = receta["id"]
        conn.execute(
            """
            UPDATE recetas
            SET descripcion = ?, alergenos = ?, raciones_base = COALESCE(raciones_base, 4)
            WHERE id = ? AND empresa_id = ?
            """,
            (descripcion, alergenos, receta_id, empresa_id),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO recetas (nombre, descripcion, alergenos, empresa_id, raciones_base)
            VALUES (?, ?, ?, ?, 4)
            """,
            (nombre, descripcion, alergenos, empresa_id),
        )
        receta_id = cur.lastrowid
        creada = True

    conn.execute("DELETE FROM recetas_ingredientes WHERE receta_id = ?", (receta_id,))
    for ingrediente in ingredientes:
        ingrediente = (ingrediente or "").strip()
        if not ingrediente:
            continue
        producto = _producto_por_nombre(conn, empresa_id, ingrediente)
        if producto:
            producto_id = producto["id"]
        else:
            cur = conn.execute(
                """
                INSERT INTO productos
                (nombre, cantidad, unidad, familia, precio, activo, empresa_id,
                 visible_en_produccion, visible_en_compras, alergenos_validado)
                VALUES (?, 0, 'ud', 'mise_en_place', 0, 1, ?, 1, 0, 0)
                """,
                (ingrediente[:180], empresa_id),
            )
            producto_id = cur.lastrowid
        conn.execute(
            "INSERT INTO recetas_ingredientes (receta_id, producto_id, cantidad) VALUES (?, ?, 0)",
            (receta_id, producto_id),
        )
    return receta_id, creada


def _importar_recetas_mise_revisadas(conn, empresa_id, analisis):
    resumen = {"recetas_creadas": 0, "recetas_actualizadas": 0, "productos_creados": 0, "ingredientes_enlazados": 0}
    for hoja in analisis.get("hojas") or []:
        seccion = hoja.get("nombre") or ""
        for plato in hoja.get("recetas_detalle") or []:
            nombre = (plato.get("nombre") or "").strip()
            if not nombre or not _nombre_mise_plato_valido(nombre):
                continue
            receta_id, creada = _crear_receta_mise_si_falta(conn, empresa_id, nombre, seccion)
            if not receta_id:
                continue
            if creada:
                resumen["recetas_creadas"] += 1
            else:
                resumen["recetas_actualizadas"] += 1
            productos_creados, ingredientes_enlazados = _sincronizar_ingredientes_receta_mise(
                conn,
                empresa_id,
                receta_id,
                plato.get("ingredientes") or [],
                seccion,
                nombre,
            )
            resumen["productos_creados"] += productos_creados
            resumen["ingredientes_enlazados"] += ingredientes_enlazados
    return resumen


def _tipo_menu_para_mise(conn, empresa_id, punto_venta_id=None):
    params = [empresa_id]
    filtro_punto = ""
    if punto_venta_id:
        filtro_punto = "AND (punto_venta_id = ? OR COALESCE(punto_venta_id, '') = '')"
        params.append(str(punto_venta_id))
    tipo = conn.execute(
        f"""
        SELECT id
        FROM tipos_menu
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 {filtro_punto}
        ORDER BY CASE WHEN LOWER(nombre) LIKE '%carta%' THEN 0 ELSE 1 END, id
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    if tipo:
        return tipo["id"]
    cur = conn.execute(
        "INSERT INTO tipos_menu (nombre, punto_venta_id, activo, empresa_id) VALUES ('Carta', ?, 1, ?)",
        (punto_venta_id or "", empresa_id),
    )
    return cur.lastrowid


def _subdepartamento_para_mise(conn, empresa_id, punto_venta_id=None):
    if punto_venta_id:
        punto = conn.execute(
            """
            SELECT id, nombre, departamento_id, subdepartamento_id
            FROM puntos_venta
            WHERE id = ? AND empresa_id = ?
            LIMIT 1
            """,
            (punto_venta_id, empresa_id),
        ).fetchone()
        if punto:
            if punto["subdepartamento_id"]:
                return punto["subdepartamento_id"]

            dep_id = punto["departamento_id"]
            if not dep_id:
                dep = conn.execute(
                    "SELECT id FROM departamentos WHERE empresa_id = ? AND LOWER(nombre) = LOWER(?) LIMIT 1",
                    (empresa_id, punto["nombre"]),
                ).fetchone()
                if dep:
                    dep_id = dep["id"]
                else:
                    dep_cur = conn.execute(
                        """
                        INSERT INTO departamentos (nombre, descripcion, activo, empresa_id)
                        VALUES (?, '', 1, ?)
                        """,
                        (punto["nombre"], empresa_id),
                    )
                    dep_id = dep_cur.lastrowid
                conn.execute(
                    "UPDATE puntos_venta SET departamento_id = ? WHERE id = ? AND empresa_id = ?",
                    (dep_id, punto_venta_id, empresa_id),
                )

            sub = conn.execute(
                """
                SELECT id
                FROM subdepartamentos
                WHERE empresa_id = ?
                  AND departamento_id = ?
                  AND LOWER(nombre) = LOWER(?)
                LIMIT 1
                """,
                (empresa_id, dep_id, punto["nombre"]),
            ).fetchone()
            if sub:
                sub_id = sub["id"]
            else:
                cur = conn.execute(
                    """
                    INSERT INTO subdepartamentos (nombre, departamento_id, descripcion, activo, empresa_id)
                    VALUES (?, ?, 'Destino operativo TPV', 1, ?)
                    """,
                    (punto["nombre"], dep_id, empresa_id),
                )
                sub_id = cur.lastrowid
            conn.execute(
                "UPDATE puntos_venta SET subdepartamento_id = ? WHERE id = ? AND empresa_id = ?",
                (sub_id, punto_venta_id, empresa_id),
            )
            return sub_id

    sub = conn.execute(
        """
        SELECT id
        FROM subdepartamentos
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY CASE WHEN LOWER(nombre) LIKE '%cocina%' THEN 0 ELSE 1 END, id
        LIMIT 1
        """,
        (empresa_id,),
    ).fetchone()
    if sub:
        return sub["id"]
    dep = conn.execute(
        "SELECT id FROM departamentos WHERE empresa_id = ? ORDER BY id LIMIT 1",
        (empresa_id,),
    ).fetchone()
    if not dep:
        dep_cur = conn.execute(
            "INSERT INTO departamentos (nombre, descripcion, activo, empresa_id) VALUES ('Cocina', '', 1, ?)",
            (empresa_id,),
        )
        dep_id = dep_cur.lastrowid
    else:
        dep_id = dep["id"]
    cur = conn.execute(
        "INSERT INTO subdepartamentos (nombre, departamento_id, descripcion, activo, empresa_id) VALUES ('Cocina central', ?, '', 1, ?)",
        (dep_id, empresa_id),
    )
    return cur.lastrowid


def _receta_y_producto_mise(conn, empresa_id, nombre, seccion=""):
    nombre = (nombre or "").strip()[:180]
    if not nombre:
        return None, False, False
    producto = _producto_por_nombre(conn, empresa_id, nombre)
    producto_creado = False
    if producto:
        producto_id = producto["id"]
    else:
        cur = conn.execute(
            """
            INSERT INTO productos
            (nombre, cantidad, unidad, familia, precio, activo, empresa_id,
             visible_en_produccion, visible_en_compras, alergenos_validado)
            VALUES (?, 0, 'ud', ?, 0, 1, ?, 1, 0, 0)
            """,
            (nombre, f"mise_{_normalizar(seccion)[:30] or 'menu'}", empresa_id),
        )
        producto_id = cur.lastrowid
        producto_creado = True

    receta = conn.execute(
        "SELECT id FROM recetas WHERE empresa_id = ? AND LOWER(nombre) = LOWER(?)",
        (empresa_id, nombre),
    ).fetchone()
    receta_creada = False
    descripcion = f"Creada desde mise en place. Seccion: {seccion or '-'}."
    alergenos = ", ".join(ProcesadorPDF().buscar_alergenos(nombre))
    if receta:
        receta_id = receta["id"]
        conn.execute(
            """
            UPDATE recetas
            SET descripcion = COALESCE(NULLIF(descripcion, ''), ?),
                alergenos = CASE WHEN COALESCE(alergenos, '') = '' THEN ? ELSE alergenos END,
                raciones_base = COALESCE(raciones_base, 4)
            WHERE id = ? AND empresa_id = ?
            """,
            (descripcion, alergenos, receta_id, empresa_id),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO recetas (nombre, descripcion, alergenos, categoria, empresa_id, raciones_base)
            VALUES (?, ?, ?, ?, ?, 4)
            """,
            (nombre, descripcion, alergenos, seccion or "", empresa_id),
        )
        receta_id = cur.lastrowid
        receta_creada = True

    existe_ing = conn.execute(
        "SELECT id FROM recetas_ingredientes WHERE receta_id = ? AND producto_id = ? LIMIT 1",
        (receta_id, producto_id),
    ).fetchone()
    if not existe_ing:
        conn.execute(
            "INSERT INTO recetas_ingredientes (receta_id, producto_id, cantidad) VALUES (?, ?, 0)",
            (receta_id, producto_id),
        )
    return receta_id, receta_creada, producto_creado


def _crear_producto_mise_si_falta(conn, empresa_id, nombre, seccion=""):
    nombre = (nombre or "").strip()[:180]
    if not nombre:
        return None, False
    producto = _producto_por_nombre(conn, empresa_id, nombre)
    if producto:
        return producto["id"], False
    cur = conn.execute(
        """
        INSERT INTO productos
        (nombre, cantidad, unidad, familia, precio, activo, empresa_id,
         visible_en_produccion, visible_en_compras, alergenos_validado)
        VALUES (?, 0, 'ud', ?, 0, 1, ?, 1, 0, 0)
        """,
        (nombre, f"mise_{_normalizar(seccion)[:30] or 'menu'}", empresa_id),
    )
    return cur.lastrowid, True


def _crear_receta_mise_si_falta(conn, empresa_id, nombre, seccion=""):
    nombre = (nombre or "").strip()[:180]
    if not nombre:
        return None, False
    receta = conn.execute(
        "SELECT id FROM recetas WHERE empresa_id = ? AND LOWER(nombre) = LOWER(?)",
        (empresa_id, nombre),
    ).fetchone()
    descripcion = f"Creada desde mise en place. Seccion: {seccion or '-'}."
    alergenos = ", ".join(ProcesadorPDF().buscar_alergenos(nombre))
    if receta:
        receta_id = receta["id"]
        conn.execute(
            """
            UPDATE recetas
            SET categoria = CASE WHEN COALESCE(categoria, '') = '' THEN ? ELSE categoria END,
                alergenos = CASE WHEN COALESCE(alergenos, '') = '' THEN ? ELSE alergenos END,
                raciones_base = COALESCE(raciones_base, 4)
            WHERE id = ? AND empresa_id = ?
            """,
            (seccion or "", alergenos, receta_id, empresa_id),
        )
        return receta_id, False
    cur = conn.execute(
        """
        INSERT INTO recetas (nombre, descripcion, alergenos, categoria, empresa_id, raciones_base)
        VALUES (?, ?, ?, ?, ?, 4)
        """,
        (nombre, descripcion, alergenos, seccion or "", empresa_id),
    )
    return cur.lastrowid, True


def _sincronizar_ingredientes_receta_mise(conn, empresa_id, receta_id, ingredientes, seccion="", receta_nombre=""):
    productos_creados = 0
    ingredientes_enlazados = 0
    ingredientes = [i for i in (ingredientes or []) if (i or "").strip()]
    total = len(ingredientes) or 1
    for ingrediente in ingredientes:
        producto_id, producto_creado = _crear_producto_mise_si_falta(conn, empresa_id, ingrediente, seccion)
        if not producto_id:
            continue
        productos_creados += 1 if producto_creado else 0
        existe = conn.execute(
            "SELECT id FROM recetas_ingredientes WHERE receta_id = ? AND producto_id = ? LIMIT 1",
            (receta_id, producto_id),
        ).fetchone()
        if existe:
            continue
        producto = conn.execute(
            "SELECT nombre, familia, unidad, precio FROM productos WHERE id = ? AND empresa_id = ?",
            (producto_id, empresa_id),
        ).fetchone()
        cantidad = _cantidad_ingrediente_4_raciones(producto, receta_nombre, total) if producto else 0
        conn.execute(
            "INSERT INTO recetas_ingredientes (receta_id, producto_id, cantidad) VALUES (?, ?, ?)",
            (receta_id, producto_id, cantidad),
        )
        ingredientes_enlazados += 1
    return productos_creados, ingredientes_enlazados


def _crear_menu_completo_mise(conn, empresa_id, analisis, nombre_menu, punto_venta_id=None):
    tipo_menu_id = _tipo_menu_para_mise(conn, empresa_id, punto_venta_id)
    subdepartamento_id = _subdepartamento_para_mise(conn, empresa_id, punto_venta_id)
    departamento_id = None
    if punto_venta_id:
        punto = conn.execute(
            "SELECT departamento_id FROM puntos_venta WHERE id = ? AND empresa_id = ? LIMIT 1",
            (punto_venta_id, empresa_id),
        ).fetchone()
        departamento_id = punto["departamento_id"] if punto else None
    nombre_menu = (nombre_menu or "Menu mise en place").strip()[:180]
    menu = conn.execute(
        "SELECT id FROM menus WHERE empresa_id = ? AND LOWER(nombre) = LOWER(?) LIMIT 1",
        (empresa_id, nombre_menu),
    ).fetchone()
    if menu:
        menu_id = menu["id"]
        conn.execute(
            """
            UPDATE menus
            SET tipo_menu_id = ?, punto_venta_id = ?, departamento_id = ?, observaciones = ?
            WHERE id = ? AND empresa_id = ?
            """,
            (tipo_menu_id, punto_venta_id, departamento_id, "Generado desde hojas de mise en place", menu_id, empresa_id),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO menus
            (nombre, tipo_menu_id, punto_venta_id, departamento_id, modo_servicio, fecha_inicio, fecha_fin, observaciones, activo, empresa_id)
            VALUES (?, ?, ?, ?, 'menu_diario', '', '', 'Generado desde hojas de mise en place', 1, ?)
            """,
            (nombre_menu, tipo_menu_id, punto_venta_id, departamento_id, empresa_id),
        )
        menu_id = cur.lastrowid

    resumen = {"menu_id": menu_id, "secciones": 0, "secciones_reubicadas": 0, "recetas_creadas": 0, "productos_creados": 0, "ingredientes_enlazados": 0, "platos_enlazados": 0, "recetas_detectadas": 0}
    for orden_seccion, hoja in enumerate(analisis.get("hojas") or [], start=1):
        seccion_nombre = (hoja.get("nombre") or f"Seccion {orden_seccion}").strip()[:120]
        seccion = conn.execute(
            """
            SELECT id, subdepartamento_id FROM menu_fijo_subdepartamentos
            WHERE menu_id = ? AND LOWER(seccion) = LOWER(?)
            LIMIT 1
            """,
            (menu_id, seccion_nombre),
        ).fetchone()
        if seccion:
            seccion_id = seccion["id"]
            if seccion["subdepartamento_id"] != subdepartamento_id:
                conn.execute(
                    "UPDATE menu_fijo_subdepartamentos SET subdepartamento_id = ? WHERE id = ?",
                    (subdepartamento_id, seccion_id),
                )
                resumen["secciones_reubicadas"] += 1
        else:
            cur = conn.execute(
                """
                INSERT INTO menu_fijo_subdepartamentos (menu_id, subdepartamento_id, seccion, orden)
                VALUES (?, ?, ?, ?)
                """,
                (menu_id, subdepartamento_id, seccion_nombre, orden_seccion),
            )
            seccion_id = cur.lastrowid
            resumen["secciones"] += 1

        detalles = hoja.get("recetas_detalle") or []
        recetas_hoja = [d.get("nombre") for d in detalles if d.get("nombre")] or (hoja.get("recetas_detectadas") or [])
        resumen["recetas_detectadas"] += len(recetas_hoja)
        detalle_por_nombre = {_normalizar(d.get("nombre")): d for d in detalles}
        for orden_plato, item in enumerate(recetas_hoja, start=1):
            if not _nombre_mise_plato_valido(item):
                continue
            receta_id, receta_creada = _crear_receta_mise_si_falta(conn, empresa_id, item, seccion_nombre)
            if not receta_id:
                continue
            resumen["recetas_creadas"] += 1 if receta_creada else 0
            detalle = detalle_por_nombre.get(_normalizar(item)) or {}
            if detalle.get("ingredientes"):
                productos_creados, ingredientes_enlazados = _sincronizar_ingredientes_receta_mise(
                    conn, empresa_id, receta_id, detalle.get("ingredientes"), seccion_nombre, item
                )
                resumen["productos_creados"] += productos_creados
                resumen["ingredientes_enlazados"] += ingredientes_enlazados
            existe = conn.execute(
                """
                SELECT id FROM menu_fijo_platos
                WHERE menu_fijo_subdepartamento_id = ? AND receta_id = ?
                LIMIT 1
                """,
                (seccion_id, receta_id),
            ).fetchone()
            if not existe:
                conn.execute(
                    "INSERT INTO menu_fijo_platos (menu_fijo_subdepartamento_id, receta_id, orden) VALUES (?, ?, ?)",
                    (seccion_id, receta_id, orden_plato),
                )
                resumen["platos_enlazados"] += 1
    return resumen


def _sugerir_productos_para_receta(receta, catalogo, max_items=6):
    nombre = receta['nombre']
    nt = _tokens(nombre)
    sugeridos = []
    usados = set()

    def add(producto):
        if producto and producto['id'] not in usados:
            usados.add(producto['id'])
            sugeridos.append(producto)

    exacto = _buscar_producto(catalogo, nombre)
    add(exacto)

    reglas = [
        (('arroz',), ['arroz', 'aceite', 'ajo', 'sal']),
        (('pasta', 'macarron', 'spaghetti', 'espagueti', 'tallarin', 'tallarines'), ['pasta', 'aceite', 'sal']),
        (('alubia', 'alubias'), ['alubias', 'cebolla', 'ajo', 'aceite', 'sal']),
        (('garbanzo', 'garbanzos'), ['garbanzos', 'cebolla', 'ajo', 'aceite', 'sal']),
        (('lenteja', 'lentejas'), ['lentejas', 'cebolla', 'ajo', 'aceite', 'sal']),
        (('pollo',), ['pollo', 'aceite', 'ajo', 'sal']),
        (('cerdo', 'lomo'), ['cerdo', 'aceite', 'sal']),
        (('bacon',), ['bacon']),
        (('ternera',), ['ternera', 'aceite', 'sal']),
        (('carne',), ['carne', 'aceite', 'sal']),
        (('pescado', 'merluza', 'bacalao', 'atun'), ['pescado', 'merluza', 'bacalao', 'atun', 'aceite', 'sal']),
        (('calamar', 'calamares'), ['calamar', 'aceite', 'sal']),
        (('ensalada',), ['lechuga', 'tomate', 'cebolla', 'aceite', 'vinagre', 'sal']),
        (('crema', 'sopa'), ['cebolla', 'ajo', 'aceite', 'caldo', 'sal']),
        (('batido',), ['leche', 'platano', 'fruta']),
        (('fruta', 'frutas'), ['manzana', 'melon', 'sandia', 'platano']),
        (('ajo blanco', 'ajoblanco'), ['ajo', 'almendra', 'pan', 'aceite', 'sal']),
        (('arroz leche',), ['arroz', 'leche', 'azucar', 'canela']),
    ]
    for claves, busquedas in reglas:
        if any(_normalizar(c) in nt for c in claves) or any(c in _normalizar(nombre) for c in claves):
            for busqueda in busquedas:
                add(_buscar_producto(catalogo, busqueda))

    for palabra in sorted(nt, key=len, reverse=True):
        if len(sugeridos) >= max_items:
            break
        add(_buscar_producto(catalogo, palabra))

    if not sugeridos:
        return []

    # Evita recetas de prueba absurdamente largas por coincidencias de palabras.
    principales = []
    apoyo = []
    for p in sugeridos:
        if p['tokens'] & nt:
            principales.append(p)
        else:
            apoyo.append(p)
    return (principales + apoyo)[:max_items]


def _plan_relleno_recetas(conn, empresa_id, limite=None):
    _asegurar_recetas_schema(conn)
    catalogo = _catalogo_productos(conn, empresa_id)
    recetas = conn.execute("""
        SELECT id, nombre, categoria
        FROM recetas
        WHERE empresa_id = ?
        ORDER BY nombre
    """, (empresa_id,)).fetchall()

    actualizar_cantidades = []
    crear_ingredientes = []
    recetas_sin_sugerencia = []

    procesadas_sin_ing = 0
    for receta in recetas:
        ingredientes = conn.execute("""
            SELECT ri.id, ri.producto_id, ri.cantidad, p.nombre, p.familia, p.unidad, p.precio, p.tipo_conservacion
            FROM recetas_ingredientes ri
            JOIN productos p ON p.id = ri.producto_id
            WHERE ri.receta_id = ?
        """, (receta['id'],)).fetchall()

        if ingredientes:
            total = len(ingredientes)
            for ing in ingredientes:
                if _cantidad_valida(ing['cantidad']):
                    continue
                cantidad = _cantidad_ingrediente_4_raciones(ing, receta['nombre'], total)
                actualizar_cantidades.append({
                    'ingrediente_id': ing['id'],
                    'receta_id': receta['id'],
                    'receta': receta['nombre'],
                    'producto_id': ing['producto_id'],
                    'producto': ing['nombre'],
                    'cantidad': round(cantidad, 3),
                    'unidad': ing['unidad'] or '',
                })
            continue

        if limite and procesadas_sin_ing >= limite:
            continue
        procesadas_sin_ing += 1
        sugeridos = _sugerir_productos_para_receta(receta, catalogo)
        if not sugeridos:
            recetas_sin_sugerencia.append({'id': receta['id'], 'nombre': receta['nombre']})
            continue
        total = len(sugeridos)
        for producto in sugeridos:
            cantidad = _cantidad_ingrediente_4_raciones(producto, receta['nombre'], total)
            crear_ingredientes.append({
                'receta_id': receta['id'],
                'receta': receta['nombre'],
                'producto_id': producto['id'],
                'producto': producto['nombre'],
                'cantidad': round(cantidad, 3),
                'unidad': producto['unidad'] or '',
            })

    return {
        'actualizar_cantidades': actualizar_cantidades,
        'crear_ingredientes': crear_ingredientes,
        'recetas_sin_sugerencia': recetas_sin_sugerencia,
    }


def _cantidad_valida(valor):
    try:
        return float(valor or 0) > 0
    except Exception:
        return False


def _asegurar_biblioteca_recetas():
    os.makedirs(BIBLIOTECA_RECETAS_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(BIBLIOTECA_RECETAS_CACHE), exist_ok=True)
    os.makedirs(BIBLIOTECA_RECETAS_EXTRAIDA, exist_ok=True)
    os.makedirs(BIBLIOTECA_LIBROS_GRANDES, exist_ok=True)


def _cargar_biblioteca_recetas():
    _asegurar_biblioteca_recetas()
    if not os.path.exists(BIBLIOTECA_RECETAS_CACHE):
        return {"propuestas": [], "errores": [], "actualizado": None}
    try:
        with open(BIBLIOTECA_RECETAS_CACHE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"propuestas": [], "errores": [], "actualizado": None}


def _guardar_biblioteca_recetas(datos):
    _asegurar_biblioteca_recetas()
    with open(BIBLIOTECA_RECETAS_CACHE, "w", encoding="utf-8") as fh:
        json.dump(datos, fh, ensure_ascii=False, indent=2)


def _clasificar_fuente_biblioteca(nombre, texto):
    base = _normalizar(nombre)
    muestra = _normalizar((texto or "")[:12000])
    combinado = f"{base} {muestra}"
    extension = os.path.splitext(nombre)[1].lower()
    no_cocina = [
        "multitennant", "unemployment", "desempleados", "participantes", "benefits",
        "presentacion hotr", "informacion importante",
    ]
    referencias = {
        "appcc": ["appcc", "autocontrol", "trazabilidad", "limpieza", "higiene", "microorganismos", "buenas practicas"],
        "alergenos": ["alergeno", "alergenos", "celiaco", "celiacos", "gluten", "intolerancia"],
        "nutricion": ["valor nutricional", "alimentacion", "nutricional", "denominaciones de origen"],
        "gestion": ["ingenieria de menus", "escandallo", "direccion en cocina", "proyecto de direccion"],
        "tecnica": ["tecnicas culinarias", "gelificantes", "manual de cocina profesional", "hosteleria"],
    }
    recetario_nombre = [
        "recetas", "recetario", "receptari", "pasteleria", "panaderia", "cocina china",
        "cocina arabe", "orientales", "platos elaborados", "fichas tecnicas culinarias",
        "cocina grado medio", "cocina profesional",
    ]
    recetario_texto = [
        "ingredientes", "elaboracion", "preparacion", "modo de elaboracion",
        "raciones", "gramos", "kilogramos", "hornear", "cocer", "freir",
    ]
    if any(p in combinado for p in no_cocina):
        return "descartado", "No parece material culinario util para la memoria."
    if extension in {".xlsx", ".xls"} and any(p in combinado for p in ["producto cantidad unidad", "ingredientes", "elaboracion", "mise on place"]):
        return "recetario", "Ficha tecnica tratada como recetario."
    if any(p in base for p in recetario_nombre):
        return "recetario", "Fuente tratada como recetario."
    puntos_receta = sum(1 for p in recetario_texto if p in combinado)
    if puntos_receta >= 3 or ("ingredientes" in combinado and any(p in combinado for p in ["elaboracion", "preparacion", "mise", "escandallo"])):
        return "recetario", "Fuente tratada como recetario por estructura de ingredientes/elaboracion."
    for tipo, claves in referencias.items():
        if any(clave in combinado for clave in claves):
            return f"referencia_{tipo}", f"Documento de referencia: {tipo}."
    if puntos_receta >= 1 and len(muestra) > 500:
        return "posible_recetario", "Posible recetario, revisar si genera propuestas utiles."
    return "descartado", "No se han detectado recetas ni referencia culinaria clara."


def _parece_nombre_receta_archivo(nombre):
    base = os.path.splitext(os.path.basename(nombre))[0].replace("_", " ").replace("-", " ")
    normal = _normalizar(base)
    if not normal or any(p in normal for p in ["ficha base", "acciones higiene", "informacion", "presentacion", "manual", "plantilla"]):
        return False
    palabras = [p for p in normal.split() if p not in {"de", "del", "la", "las", "los", "con", "y", "al"}]
    if not (1 <= len(palabras) <= 9):
        return False
    claves = [
        "arroz", "sopa", "crema", "bacalao", "rape", "pescado", "marisco", "solomillo",
        "cordero", "pollo", "canelon", "gazpacho", "salsa", "aceite", "torrija",
        "tortellini", "ensalada", "flan", "mejillon", "pimiento", "albondiga",
        "merluza", "gamba", "gambas", "salmon", "salmón", "pastel", "muffin",
        "muffins", "pan", "brandada", "caldereta", "saquito", "teja",
    ]
    return any(clave in normal for clave in claves) or base.isupper() or base.istitle()


def _clasificar_nombre_biblioteca(nombre):
    base = _normalizar(nombre)
    if any(p in base for p in ["multitennant", "unemployment", "desempleados", "participantes", "benefits", "presentacion hotr"]):
        return "descartado", "No parece material culinario util para la memoria."
    referencias = {
        "referencia_appcc": ["appcc", "autocontrol", "trazabilidad", "limpieza", "higiene", "microorganismos", "buenas practicas"],
        "referencia_alergenos": ["alergeno", "alergenos", "celiaco", "celiacos", "gluten", "intolerancia"],
        "referencia_nutricion": ["valor nutricional", "alimentacion", "nutricional", "denominaciones de origen"],
        "referencia_gestion": ["ingenieria de menus", "escandallo", "direccion en cocina", "proyecto de direccion"],
        "referencia_tecnica": ["tecnicas culinarias", "gelificantes", "manual de cocina profesional"],
    }
    for tipo, claves in referencias.items():
        if any(clave in base for clave in claves):
            return tipo, f"Documento de referencia: {tipo.replace('referencia_', '')}."
    if any(p in base for p in ["fichas tecnicas culinarias", "recetas", "recetario", "receptari", "pasteleria", "panaderia", "cocina china", "cocina arabe", "orientales"]):
        return "recetario", "Fuente tratada como recetario por nombre."
    if any(p in base for p in ["diagrama pasos elaborar receta", "pasos elaborar receta"]):
        return "referencia_tecnica", "Documento de referencia: tecnica."
    if os.path.splitext(nombre)[1].lower() in {".doc", ".docx"} and _parece_nombre_receta_archivo(nombre):
        return "posible_recetario", "Parece una ficha de receta por nombre de archivo."
    return "", ""


def _resumen_referencia_biblioteca(nombre, texto, tipo, motivo):
    lineas = []
    for linea in (texto or "").splitlines():
        limpia = _limpiar_linea_receta(linea)
        if len(limpia) >= 25 and not re.search(r"\.{5,}\s*\d+$", limpia):
            lineas.append(limpia)
        if len(lineas) >= 6:
            break
    return {
        "fuente": os.path.basename(nombre),
        "tipo": tipo,
        "motivo": motivo,
        "resumen": " ".join(lineas)[:600],
    }


def _leer_archivo_biblioteca(ruta):
    extension = os.path.splitext(ruta)[1].lower()
    nombre_base = os.path.splitext(os.path.basename(ruta))[0].replace("_", " ").replace("-", " ").strip()
    if extension == ".pdf":
        paginas = ProcesadorPDF().extraer_texto_paginas(ruta, usar_ocr=False)
        texto = "\n".join((p.get("texto") or "") for p in paginas[:12])
        return f"{nombre_base}\n{texto}"
    if extension in {".txt", ".md"}:
        with open(ruta, "r", encoding="utf-8", errors="ignore") as fh:
            return f"{nombre_base}\n{fh.read()}"
    if extension == ".csv":
        lineas = [nombre_base]
        with open(ruta, "r", encoding="utf-8", errors="ignore", newline="") as fh:
            for fila in csv.reader(fh):
                lineas.append(" | ".join(str(celda or "").strip() for celda in fila if str(celda or "").strip()))
        return "\n".join(lineas)
    if extension in {".xlsx", ".xls"}:
        try:
            import pandas as pd
        except Exception:
            return ""
        partes = [nombre_base]
        hojas = pd.read_excel(ruta, sheet_name=None, dtype=str, header=None)
        for nombre_hoja, df in hojas.items():
            partes.append(str(nombre_hoja))
            df = df.fillna("")
            for _, fila in df.iterrows():
                texto = " | ".join(str(valor).strip() for valor in fila.tolist() if str(valor).strip())
                if texto:
                    partes.append(texto)
        return "\n".join(partes)
    if extension == ".docx":
        try:
            from docx import Document
        except Exception:
            return ""
        doc = Document(ruta)
        partes = [p.text for p in doc.paragraphs if p.text.strip()]
        for tabla in doc.tables:
            for fila in tabla.rows:
                texto = " | ".join(celda.text.strip() for celda in fila.cells if celda.text.strip())
                if texto:
                    partes.append(texto)
        if not partes:
            partes = [nombre_base]
        return f"{nombre_base}\n" + "\n".join(partes)
    if extension == ".odt":
        try:
            with zipfile.ZipFile(ruta) as zf:
                contenido = zf.read("content.xml").decode("utf-8", errors="ignore")
            texto = re.sub(r"<[^>]+>", "\n", contenido)
            return f"{nombre_base}\n" + re.sub(r"\n{2,}", "\n", texto)
        except Exception:
            return ""
    if extension == ".doc":
        # Word antiguo (.doc) no tiene lectura fiable sin conversor externo.
        # Usamos el nombre como propuesta y dejamos que el usuario revise.
        return f"{nombre_base}\n"
    return ""


def _limpiar_linea_receta(linea):
    linea = re.sub(r"\s+", " ", str(linea or "")).strip(" -:\t")
    return linea[:240]


def _parece_titulo_receta(linea):
    linea = _limpiar_linea_receta(linea)
    if len(linea) < 4 or len(linea) > 90:
        return False
    bajo = linea.lower()
    prohibidas = {
        "ingredientes", "elaboracion", "elaboración", "preparacion", "preparación",
        "modo de elaboracion", "modo de elaboración", "observaciones", "alergenos",
        "alérgenos", "raciones", "cantidad", "producto", "unidad"
    }
    if bajo in prohibidas:
        return False
    if re.search(r"\b\d+\s*(g|kg|ml|l|cl|ud|uds|unidad|unidades)\b", bajo):
        return False
    letras = [c for c in linea if c.isalpha()]
    if not letras:
        return False
    mayus = sum(1 for c in letras if c.isupper())
    palabras = [p for p in re.split(r"\s+", bajo) if p]
    conectores = {"de", "del", "la", "las", "los", "el", "al", "a", "en", "con", "y", "o"}
    palabras_utiles = [p for p in palabras if p not in conectores]
    if 1 <= len(palabras_utiles) <= 9 and not any(p in STOP_RECETA for p in palabras_utiles[:1]):
        if not any(sep in linea for sep in ["|", ";"]):
            return True
    return mayus / max(len(letras), 1) >= 0.45 or linea.istitle()


def _extraer_propuestas_recetas(texto, fuente):
    lineas = [_limpiar_linea_receta(l) for l in (texto or "").splitlines()]
    lineas = [l for l in lineas if l]
    propuestas = []
    procesador = ProcesadorPDF()
    extension_fuente = os.path.splitext(fuente)[1].lower()
    nombre_fuente = os.path.splitext(os.path.basename(fuente))[0].replace("_", " ").replace("-", " ").strip()

    if extension_fuente in {".xlsx", ".xls"}:
        ingredientes = []
        elaboracion = []
        en_elaboracion = False
        for linea in lineas[1:]:
            bajo = linea.lower()
            if any(p in bajo for p in ["tecnicas utilizadas", "técnicas utilizadas", "elaboracion", "elaboración", "preparacion", "preparación"]):
                en_elaboracion = True
                continue
            if en_elaboracion:
                if len(linea.split()) >= 3 and "precio de venta" not in bajo:
                    elaboracion.append(linea)
                continue
            if "|" not in linea:
                continue
            partes = [p.strip() for p in linea.split("|") if p.strip()]
            if not partes:
                continue
            primero = partes[0]
            primero_bajo = primero.lower()
            if primero_bajo in {"producto", "escandallo", "total"} or "ingredientes" in primero_bajo:
                continue
            if any(p in primero_bajo for p in ["precio", "coste", "racion", "beneficio"]):
                continue
            if len(primero) < 3:
                continue
            ingredientes.append(" ".join(partes[:3]))
        texto_propuesta = "\n".join([nombre_fuente] + ingredientes + elaboracion)
        propuesta_id = hashlib.sha1(f"{fuente}|{texto_propuesta[:500]}".encode("utf-8", errors="ignore")).hexdigest()[:16]
        return [{
            "id": propuesta_id,
            "fuente": os.path.basename(fuente),
            "nombre": nombre_fuente[:120],
            "categoria": "",
            "ingredientes": ingredientes[:80],
            "elaboracion": " ".join(elaboracion)[:4000],
            "alergenos": ", ".join(procesador.buscar_alergenos(texto_propuesta)),
            "confianza": min(95, 55 + len(ingredientes) * 2 + (15 if elaboracion else 0)),
        }]

    if extension_fuente == ".doc" and nombre_fuente:
        propuesta_id = hashlib.sha1(f"{fuente}|{nombre_fuente}".encode("utf-8", errors="ignore")).hexdigest()[:16]
        return [{
            "id": propuesta_id,
            "fuente": os.path.basename(fuente),
            "nombre": nombre_fuente[:120],
            "categoria": "",
            "ingredientes": [],
            "elaboracion": "",
            "alergenos": "",
            "confianza": 30,
        }]

    titulos = [i for i, linea in enumerate(lineas) if _parece_titulo_receta(linea)]

    if not titulos and lineas:
        nombre = os.path.splitext(os.path.basename(fuente))[0].replace("_", " ").replace("-", " ").strip()
        titulos = [0]
        lineas.insert(0, nombre or "Receta sin titulo")

    for pos, inicio in enumerate(titulos[:80]):
        fin = titulos[pos + 1] if pos + 1 < len(titulos) else min(len(lineas), inicio + 80)
        bloque = lineas[inicio:fin]
        if not bloque:
            continue
        nombre = bloque[0]
        if re.search(r"\.{5,}\s*\d+\s*$", nombre) or re.match(r"^\d+\s*[–.-].*\.{5,}", nombre):
            continue
        if nombre.lower() in {"saladas", "dulces", "indice", "índice"}:
            continue
        ingredientes = []
        elaboracion = []
        seccion = ""
        for linea in bloque[1:]:
            bajo = linea.lower()
            if "ingrediente" in bajo:
                seccion = "ingredientes"
                continue
            if any(p in bajo for p in ["elaboracion", "elaboración", "preparacion", "preparación", "modo de"]):
                seccion = "elaboracion"
                continue
            if "alerg" in bajo or bajo in {"raciones", "observaciones"}:
                seccion = ""
                continue
            if seccion == "ingredientes" or re.search(r"\b\d+([,.]\d+)?\s*(g|kg|ml|l|cl|ud|uds|unidad|unidades|cucharada|cucharadas)\b", bajo):
                ingredientes.append(linea)
            elif seccion == "elaboracion" or len(linea.split()) >= 5:
                elaboracion.append(linea)

        texto_propuesta = "\n".join([nombre] + ingredientes + elaboracion)
        if len(texto_propuesta) < 20:
            continue
        if extension_fuente == ".pdf" and not ingredientes:
            continue
        propuesta_id = hashlib.sha1(f"{fuente}|{nombre}|{texto_propuesta[:500]}".encode("utf-8", errors="ignore")).hexdigest()[:16]
        propuestas.append({
            "id": propuesta_id,
            "fuente": os.path.basename(fuente),
            "nombre": nombre[:120],
            "categoria": "",
            "ingredientes": ingredientes[:60],
            "elaboracion": " ".join(elaboracion)[:4000],
            "alergenos": ", ".join(procesador.buscar_alergenos(texto_propuesta)),
            "confianza": min(95, 35 + len(ingredientes) * 5 + (20 if elaboracion else 0)),
        })
    return propuestas


def _extraer_zips_biblioteca():
    _asegurar_biblioteca_recetas()
    extensiones = {".pdf", ".txt", ".md", ".csv", ".xlsx", ".xls", ".docx", ".doc", ".odt"}
    errores = []
    for ruta_zip in sorted(
        [os.path.join(raiz, archivo)
         for raiz, _, archivos in os.walk(BIBLIOTECA_RECETAS_DIR)
         for archivo in archivos
         if archivo.lower().endswith(".zip")]
    ):
        zip_hash = hashlib.sha1(ruta_zip.encode("utf-8", errors="ignore")).hexdigest()[:12]
        destino = os.path.join(BIBLIOTECA_RECETAS_EXTRAIDA, zip_hash)
        os.makedirs(destino, exist_ok=True)
        try:
            with zipfile.ZipFile(ruta_zip) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    extension = os.path.splitext(info.filename)[1].lower()
                    if extension not in extensiones:
                        continue
                    if info.file_size > BIBLIOTECA_ARCHIVOS_MAX_BYTES:
                        errores.append({
                            "archivo": os.path.basename(ruta_zip),
                            "error": f"Saltado {info.filename}: pesa mas de 35 MB.",
                        })
                        continue
                    nombre_seguro = os.path.normpath(info.filename).replace("\\", "/")
                    if nombre_seguro.startswith("../") or "/../" in nombre_seguro:
                        continue
                    ruta_destino = os.path.join(destino, nombre_seguro)
                    os.makedirs(os.path.dirname(ruta_destino), exist_ok=True)
                    if os.path.exists(ruta_destino) and os.path.getsize(ruta_destino) == info.file_size:
                        continue
                    with zf.open(info) as origen, open(ruta_destino, "wb") as salida:
                        shutil.copyfileobj(origen, salida)
        except Exception as exc:
            errores.append({"archivo": os.path.basename(ruta_zip), "error": str(exc)[:240]})
    return errores


def _escanear_biblioteca_recetas():
    _asegurar_biblioteca_recetas()
    datos_previos = _cargar_biblioteca_recetas()
    recetas_libros_previas = [
        p for p in datos_previos.get("propuestas", [])
        if p.get("tipo_fuente") == "libro_grande"
    ]
    errores = _extraer_zips_biblioteca()
    extensiones = {".pdf", ".txt", ".md", ".csv", ".xlsx", ".xls", ".docx", ".doc", ".odt"}
    propuestas = []
    referencias = []
    raices = [BIBLIOTECA_RECETAS_DIR, BIBLIOTECA_RECETAS_EXTRAIDA]
    vistos = set()
    for base in raices:
        for raiz, _, archivos in os.walk(base):
            for archivo in archivos:
                if os.path.splitext(archivo)[1].lower() not in extensiones:
                    continue
                ruta = os.path.join(raiz, archivo)
                firma = (os.path.basename(ruta).lower(), os.path.getsize(ruta))
                if firma in vistos:
                    continue
                vistos.add(firma)
                try:
                    if os.path.getsize(ruta) > BIBLIOTECA_ARCHIVOS_MAX_BYTES:
                        errores.append({"archivo": archivo, "error": "Saltado: pesa mas de 35 MB."})
                        continue
                    tipo_nombre, motivo_nombre = _clasificar_nombre_biblioteca(archivo)
                    if tipo_nombre == "descartado":
                        errores.append({"archivo": archivo, "error": f"Descartado del recetario: {motivo_nombre}"})
                        continue
                    if tipo_nombre.startswith("referencia_"):
                        texto_ref = ""
                        if os.path.getsize(ruta) <= BIBLIOTECA_ARCHIVOS_MAX_BYTES:
                            try:
                                texto_ref = _leer_archivo_biblioteca(ruta)
                            except Exception:
                                texto_ref = ""
                        referencias.append(_resumen_referencia_biblioteca(archivo, texto_ref, tipo_nombre, motivo_nombre))
                        continue
                    texto = _leer_archivo_biblioteca(ruta)
                    if not texto.strip():
                        errores.append({"archivo": archivo, "error": "No se pudo leer texto util."})
                        continue
                    tipo_fuente, motivo = (tipo_nombre, motivo_nombre) if tipo_nombre else _clasificar_fuente_biblioteca(archivo, texto)
                    if tipo_fuente.startswith("referencia_"):
                        referencias.append(_resumen_referencia_biblioteca(archivo, texto, tipo_fuente, motivo))
                        continue
                    if tipo_fuente == "descartado":
                        errores.append({"archivo": archivo, "error": f"Descartado del recetario: {motivo}"})
                        continue
                    extraidas = _extraer_propuestas_recetas(texto, archivo)
                    if not extraidas:
                        errores.append({"archivo": archivo, "error": "No se detectaron recetas estructuradas."})
                        continue
                    for propuesta in extraidas:
                        propuesta["tipo_fuente"] = tipo_fuente
                    propuestas.extend(extraidas)
                except Exception as exc:
                    errores.append({"archivo": archivo, "error": str(exc)[:240]})
    prioridad = {".xlsx": 0, ".xls": 0, ".docx": 1, ".odt": 2, ".doc": 3, ".pdf": 4}
    propuestas.sort(
        key=lambda p: (
            prioridad.get(os.path.splitext(p.get("fuente", ""))[1].lower(), 9),
            -int(p.get("confianza") or 0),
            p.get("nombre", "").lower(),
        )
    )
    ids = {p.get("id") for p in propuestas}
    for propuesta in recetas_libros_previas:
        if propuesta.get("id") not in ids:
            propuestas.append(propuesta)
            ids.add(propuesta.get("id"))
    datos = {
        "propuestas": propuestas,
        "referencias": referencias,
        "errores": errores,
        "actualizado": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _guardar_biblioteca_recetas(datos)
    return datos


def _importar_propuesta_biblioteca(conn, empresa_id, centro_id, propuesta):
    nombre = (request.form.get(f"nombre_{propuesta['id']}") or propuesta.get("nombre") or "").strip()
    if not nombre:
        return False, "Nombre vacio"
    categoria = (request.form.get(f"categoria_{propuesta['id']}") or propuesta.get("categoria") or "").strip()
    alergenos = (request.form.get(f"alergenos_{propuesta['id']}") or propuesta.get("alergenos") or "").strip()
    ingredientes = request.form.get(f"ingredientes_{propuesta['id']}") or "\n".join(propuesta.get("ingredientes") or [])
    elaboracion = request.form.get(f"elaboracion_{propuesta['id']}") or propuesta.get("elaboracion") or ""
    descripcion = (
        f"Fuente: {propuesta.get('fuente', '')}\n\n"
        f"Ingredientes:\n{ingredientes.strip()}\n\n"
        f"Elaboracion:\n{elaboracion.strip()}"
    ).strip()
    existe = conn.execute(
        """
        SELECT id FROM recetas
        WHERE empresa_id = ?
          AND LOWER(nombre) = LOWER(?)
          AND ((? IS NULL AND centro_id IS NULL) OR centro_id = ?)
        LIMIT 1
        """,
        (empresa_id, nombre, centro_id, centro_id),
    ).fetchone()
    if existe:
        return False, f"{nombre}: ya existe"
    conn.execute(
        """
        INSERT INTO recetas (nombre, descripcion, alergenos, categoria, empresa_id, centro_id, raciones_base)
        VALUES (?, ?, ?, ?, ?, ?, 4)
        """,
        (nombre, descripcion, alergenos, categoria, empresa_id, centro_id),
    )
    return True, nombre


def _tokens_busqueda_biblioteca(*valores):
    texto = " ".join(str(v or "") for v in valores)
    texto = _normalizar(texto)
    tokens = re.findall(r"[a-z0-9]{3,}", texto)
    stop = {
        "para", "con", "sin", "una", "uno", "del", "las", "los", "que",
        "menu", "receta", "plato", "servicio", "hotel", "cocina"
    }
    return [t for t in tokens if t not in stop]


def _expandir_tokens_culinarios(tokens):
    grupos = {
        "pescado": ["pescado", "merluza", "bacalao", "salmon", "atun", "rape", "lubina", "dorada", "rodaballo"],
        "carne": ["carne", "ternera", "cerdo", "cordero", "pollo", "conejo", "ave", "solomillo", "lomo"],
        "verdura": ["verdura", "hortaliza", "calabacin", "berenjena", "zanahoria", "puerro", "alcachofa", "esparrago"],
        "legumbre": ["legumbre", "garbanzo", "lenteja", "alubia", "judia", "hummus"],
        "arroz": ["arroz", "risotto", "paella", "meloso", "caldoso"],
        "pasta": ["pasta", "canelon", "lasana", "ravioli", "tallarines", "macarron"],
        "postre": ["postre", "tarta", "flan", "bizcocho", "crema", "helado", "dulce"],
        "monacal": ["monacal", "monasterio", "convento", "clarisas", "tradicional", "reposteria"],
        "termal": ["termal", "balneario", "saludable", "ligero", "verdura", "pescado", "crema", "vapor"],
        "saludable": ["saludable", "ligero", "vapor", "plancha", "verdura", "pescado", "ensalada", "crema"],
        "elegante": ["elegante", "carta", "premium", "salsa", "reduccion", "guarnicion", "crujiente"],
        "economico": ["economico", "coste", "rentable", "aprovechamiento", "guiso", "legumbre", "arroz"],
        "buffet": ["buffet", "bandeja", "reposicion", "horno", "guiso", "crema", "ensalada"],
        "carta": ["carta", "emplatado", "racion", "salsa", "guarnicion", "acabado"],
        "banquete": ["banquete", "evento", "regenerar", "horno", "racion", "mise"],
        "sin_fritos": ["sin", "frito", "fritos", "horno", "vapor", "plancha", "guiso"],
    }
    expandidos = set(tokens)
    base = set(tokens)
    if "balneario" in base:
        base.add("termal")
    if "barato" in base or "coste" in base or "rentable" in base:
        base.add("economico")
    if "sano" in base or "salud" in base:
        base.add("saludable")
    if "monasterio" in base or "convento" in base:
        base.add("monacal")
    if "fritos" in base or "frito" in base:
        base.add("sin_fritos")
    for token in list(base):
        for clave, valores in grupos.items():
            if token == clave or token in valores:
                expandidos.update(valores)
    return sorted(expandidos)


def _coincide_token_culinario(token, texto):
    if token in texto:
        return True
    if len(token) >= 6:
        raiz = token[: max(5, len(token) - 2)]
        return raiz in texto
    return False


def _perfil_consulta_culinaria(consulta, contexto, servicio, criterio):
    texto = _normalizar(f"{consulta} {contexto} {servicio} {criterio}")
    return {
        "sin_fritos": any(p in texto for p in ["sin frito", "sin fritos", "no frito"]),
        "saludable": any(p in texto for p in ["salud", "sano", "saludable", "ligero", "termal", "balneario"]),
        "elegante": any(p in texto for p in ["elegante", "premium", "carta", "presentacion", "emplat"]),
        "economico": any(p in texto for p in ["econom", "coste", "barato", "rentable", "margen"]),
        "appcc": any(p in texto for p in ["appcc", "alergen", "temperatura", "abat", "riesgo"]),
        "buffet": "buffet" in texto,
        "monacal": any(p in texto for p in ["monacal", "monasterio", "convento"]),
    }


def _nombre_memoria_debil(nombre):
    normal = _normalizar(nombre)
    palabras = normal.split()
    if not normal or len(palabras) > 10:
        return True
    platos_claros = {
        "arroz", "paella", "risotto", "pasta", "lasana", "canelon", "lenteja",
        "garbanzo", "alubia", "crema", "sopa", "gazpacho", "ensalada",
        "pollo", "ternera", "cerdo", "cordero", "pescado", "merluza",
        "bacalao", "salmon", "atun", "langostino", "gamba", "marisco",
        "empanada", "tortilla", "estofado", "guiso", "flan", "tarta",
        "bizcocho", "helado",
    }
    basura = [
        "confeccionar", "pagina", "propuesta", "ingredientes", "elaboracion",
        "cantidad", "mercaderias", "precio", "unidad", "subtotal", "total",
        "guarnicion salsa", "ficha tecnica", "escandallo", "observaciones",
        "mise place", "modo elaboracion", "descripcion",
    ]
    if any(p in normal for p in basura):
        return True
    if len(normal) < 4:
        return True
    if palabras and palabras[0] in STOP_RECETA and not (set(palabras) & platos_claros):
        return True
    if normal.startswith(("aceite de ", "vinagreta ", "salsa ", "guarnicion ")):
        return True
    if len(palabras) == 1 and normal not in platos_claros:
        return True
    if sum(1 for c in str(nombre or "") if c.isdigit()) >= 2:
        return True
    if "|" in str(nombre or "") or str(nombre or "").count("/") >= 2:
        return True
    repetidas = len(palabras) - len(set(palabras))
    if repetidas >= 2:
        return True
    return False


def _linea_memoria_ruido(valor):
    normal = _normalizar(valor)
    if not normal:
        return True
    palabras = normal.split()
    if len(palabras) > 28:
        unicas = len(set(palabras))
        if unicas and len(palabras) / unicas >= 2.2:
            return True
    ruido = {
        "cantidad", "mercaderias", "producto", "productos", "precio", "unidad", "importe", "alergenos",
        "elaboracion", "ingredientes", "observaciones", "total", "subtotal",
        "ficha tecnica", "escandallo", "codigo", "familia",
    }
    return normal in ruido or any(normal.startswith(p) for p in ruido)


def _nombre_desde_contenido_propuesta(propuesta):
    textos = []
    textos.extend(str(i or "") for i in propuesta.get("ingredientes") or [])
    if propuesta.get("elaboracion"):
        textos.append(str(propuesta.get("elaboracion") or ""))
    texto = "\n".join(textos)
    patrones = [
        r"(?i)\breceta\s*:\s*([^|\n\r]{4,100})",
        r"(?i)\bplato\s*:\s*([^|\n\r]{4,100})",
    ]
    for patron in patrones:
        match = re.search(patron, texto)
        if not match:
            continue
        nombre = re.sub(r"\b(Categor[iÃí]a|T[eÃé]cnicas?|Alumn@?|Ingredientes?)\s*:.*$", "", match.group(1), flags=re.IGNORECASE)
        nombre = re.sub(r"\s+", " ", nombre).strip(" -.;:,")
        if 4 <= len(nombre) <= 90:
            return nombre
    return ""


def _propuesta_con_nombre_inferido(propuesta):
    nombre = propuesta.get("nombre") or ""
    inferido = _nombre_desde_contenido_propuesta(propuesta)
    if inferido and (_nombre_memoria_debil(nombre) or len(_normalizar(inferido).split()) > len(_normalizar(nombre).split())):
        copia = dict(propuesta)
        copia["nombre_original_ocr"] = nombre
        copia["nombre"] = inferido[:120]
        return copia
    return propuesta


def _ingredientes_memoria_limpios(propuesta, limite=None):
    limpios = []
    vistos = set()
    for ingrediente in propuesta.get("ingredientes") or []:
        if _linea_memoria_ruido(ingrediente):
            continue
        normal = _normalizar(ingrediente)
        if not normal or normal in vistos:
            continue
        vistos.add(normal)
        limpios.append(ingrediente)
        if limite and len(limpios) >= limite:
            break
    return limpios


def _calidad_propuesta_memoria(propuesta):
    propuesta = _propuesta_con_nombre_inferido(propuesta)
    score = int(propuesta.get("confianza") or 70)
    avisos = []
    nombre = propuesta.get("nombre") or ""
    if _nombre_memoria_debil(nombre):
        score -= 55
        avisos.append("nombre debil")
    ingredientes = _ingredientes_memoria_limpios(propuesta)
    elaboracion = propuesta.get("elaboracion") or ""
    palabras_elaboracion = len(_normalizar(elaboracion).split())
    if not ingredientes:
        score -= 25
        avisos.append("sin ingredientes claros")
    elif len(ingredientes) < 2 and propuesta.get("tipo_fuente") == "libro_grande":
        score -= 20
        avisos.append("pocos ingredientes")
    if palabras_elaboracion < 8:
        score -= 5
        avisos.append("elaboracion pobre")
    if len(ingredientes) > 24:
        score -= 10
        avisos.append("ingredientes excesivos")
    texto_ingredientes = _normalizar(" ".join(ingredientes))
    palabras = texto_ingredientes.split()
    if palabras:
        repeticion = len(palabras) / max(len(set(palabras)), 1)
        if repeticion >= 2.6:
            score -= 35
            avisos.append("texto repetido")
    if propuesta.get("tipo_fuente") == "libro_grande" and len(ingredientes) < 2 and palabras_elaboracion < 12:
        score -= 35
        avisos.append("bloque OCR flojo")
    return {
        "score": max(0, min(100, score)),
        "avisos": avisos,
        "ingredientes_limpios": ingredientes,
    }


def _propuesta_memoria_valida(propuesta):
    propuesta = _propuesta_con_nombre_inferido(propuesta)
    calidad = _calidad_propuesta_memoria(propuesta)
    if calidad["score"] < 45:
        return False
    ingredientes = calidad["ingredientes_limpios"]
    elaboracion = propuesta.get("elaboracion") or ""
    if propuesta.get("tipo_fuente") == "libro_grande" and len(ingredientes) < 2 and len(_normalizar(elaboracion).split()) < 12:
        return False
    return True


def _propuestas_memoria_validas(datos):
    propuestas = []
    for propuesta in datos.get("propuestas") or []:
        normalizada = _propuesta_con_nombre_inferido(propuesta)
        if _propuesta_memoria_valida(normalizada):
            propuestas.append(normalizada)
    return propuestas


def _filtrar_memoria_culinaria(datos, consulta="", contexto="", servicio="", criterio=""):
    propuestas = _propuestas_memoria_validas(datos)
    tokens_originales = _tokens_busqueda_biblioteca(consulta, contexto, servicio, criterio)
    tokens = _expandir_tokens_culinarios(tokens_originales)
    if not tokens:
        return []

    criterio_norm = _normalizar(criterio)
    perfil = _perfil_consulta_culinaria(consulta, contexto, servicio, criterio)
    resultados = []
    for propuesta in propuestas:
        texto = _normalizar(
            " ".join([
                propuesta.get("nombre", ""),
                propuesta.get("fuente", ""),
                propuesta.get("categoria", ""),
                propuesta.get("alergenos", ""),
                " ".join(propuesta.get("ingredientes") or []),
                propuesta.get("elaboracion", ""),
            ])
        )
        score = int(propuesta.get("confianza") or 0)
        coincidencias = 0
        coincidencias_directas = 0
        for token in tokens:
            if _coincide_token_culinario(token, texto):
                coincidencias += 1
                score += 12
                if token in tokens_originales:
                    coincidencias_directas += 1
                    score += 15
        ingredientes = len(propuesta.get("ingredientes") or [])
        coste = _estimar_coste_culinario(propuesta)
        dificultad = _estimar_dificultad_culinaria(propuesta)
        if "econom" in criterio_norm or "coste" in criterio_norm or "barat" in criterio_norm:
            score += max(0, 25 - ingredientes)
        if perfil["economico"] and coste == "bajo":
            score += 30
        if "present" in criterio_norm or "elegant" in criterio_norm or "carta" in criterio_norm:
            score += int(propuesta.get("confianza") or 0) // 3
        if perfil["elegante"] and any(p in texto for p in ["salsa", "reduccion", "guarnicion", "rellen", "crema"]):
            score += 18
        if "buffet" in criterio_norm or "servicio" in criterio_norm:
            score += 10 if ingredientes >= 4 else 0
        if perfil["buffet"] and dificultad != "alta":
            score += 20
        if perfil["saludable"] and any(p in texto for p in ["verdura", "pescado", "vapor", "plancha", "ensalada", "crema"]):
            score += 28
        if perfil["sin_fritos"] and any(p in texto for p in ["frito", "freir", "reboz", "enharin"]):
            score -= 35
        if perfil["appcc"] and any(p in texto for p in ["huevo", "mayonesa", "pollo", "pescado", "marisco", "nata"]):
            score += 12
        if perfil["monacal"] and any(p in texto for p in ["monacal", "monasterio", "convento", "clarisas", "tradicional"]):
            score += 40
        calidad = _calidad_propuesta_memoria(propuesta)
        score += (calidad["score"] - 50)
        if coincidencias or score >= int(propuesta.get("confianza") or 0) + 25:
            enriquecida = dict(propuesta)
            enriquecida["score_memoria"] = score
            enriquecida["coincidencias"] = coincidencias
            enriquecida["coincidencias_directas"] = coincidencias_directas
            enriquecida["calidad_memoria"] = calidad["score"]
            enriquecida["avisos_memoria"] = calidad["avisos"]
            enriquecida["ingredientes_limpios"] = calidad["ingredientes_limpios"]
            resultados.append(enriquecida)

    resultados.sort(key=lambda p: (-p.get("score_memoria", 0), p.get("nombre", "").lower()))
    deduplicados = []
    vistos = set()
    for item in resultados:
        clave = (
            _normalizar(item.get("nombre", "")),
            _normalizar(item.get("fuente", "")),
            str(item.get("paginas") or ""),
        )
        if clave in vistos:
            continue
        vistos.add(clave)
        deduplicados.append(item)
    return deduplicados[:80]


def _estadisticas_memoria_culinaria(datos, archivos):
    por_extension = {}
    for archivo in archivos:
        extension = archivo.get("extension") or "sin extension"
        por_extension[extension] = por_extension.get(extension, 0) + 1
    return {
        "archivos": len(archivos),
        "propuestas": len(datos.get("propuestas") or []),
        "referencias": len(datos.get("referencias") or []),
        "avisos": len(datos.get("errores") or []),
        "por_extension": sorted(por_extension.items(), key=lambda item: (-item[1], item[0])),
    }


def _id_fuente_libro(tipo, ruta, miembro=""):
    return hashlib.sha1(f"{tipo}|{ruta}|{miembro}".encode("utf-8", errors="ignore")).hexdigest()[:16]


def _listar_libros_grandes():
    _asegurar_biblioteca_recetas()
    libros = []
    for raiz, _, archivos in os.walk(BIBLIOTECA_RECETAS_DIR):
        for archivo in archivos:
            ruta = os.path.join(raiz, archivo)
            extension = os.path.splitext(archivo)[1].lower()
            if extension == ".pdf":
                tamano = os.path.getsize(ruta)
                libros.append({
                    "id": _id_fuente_libro("file", ruta),
                    "tipo": "file",
                    "ruta": ruta,
                    "miembro": "",
                    "nombre": os.path.relpath(ruta, BIBLIOTECA_RECETAS_DIR),
                    "tamano": tamano,
                    "grande": tamano > BIBLIOTECA_ARCHIVOS_MAX_BYTES,
                })
            elif extension == ".zip":
                try:
                    with zipfile.ZipFile(ruta) as zf:
                        for info in zf.infolist():
                            if info.is_dir() or not info.filename.lower().endswith(".pdf"):
                                continue
                            libros.append({
                                "id": _id_fuente_libro("zip", ruta, info.filename),
                                "tipo": "zip",
                                "ruta": ruta,
                                "miembro": info.filename,
                                "nombre": info.filename,
                                "zip": os.path.basename(ruta),
                                "tamano": info.file_size,
                                "grande": info.file_size > BIBLIOTECA_ARCHIVOS_MAX_BYTES,
                            })
                except Exception:
                    continue
    libros.sort(key=lambda item: (-int(item["grande"]), -item["tamano"], item["nombre"].lower()))
    return libros


def _resolver_fuente_libro(source_id):
    for libro in _listar_libros_grandes():
        if libro["id"] == source_id:
            return libro
    return None


def _preparar_pdf_libro(libro):
    if libro["tipo"] == "file":
        return libro["ruta"]
    destino = os.path.join(BIBLIOTECA_LIBROS_GRANDES, f"{libro['id']}.pdf")
    if os.path.exists(destino) and os.path.getsize(destino) == libro["tamano"]:
        return destino
    with zipfile.ZipFile(libro["ruta"]) as zf:
        with zf.open(libro["miembro"]) as origen, open(destino, "wb") as salida:
            shutil.copyfileobj(origen, salida)
    return destino


def _contar_paginas_pdf(ruta_pdf):
    try:
        from pypdf import PdfReader
    except Exception:
        try:
            from PyPDF2 import PdfReader
        except Exception:
            return None
    try:
        return len(PdfReader(ruta_pdf).pages)
    except Exception:
        return None


def _ocr_disponible():
    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401
        import fitz  # noqa: F401
        import numpy  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except Exception:
        return False


_RAPIDOCR_ENGINE = None


def _ocr_pdf_rango(ruta_pdf, inicio, fin):
    global _RAPIDOCR_ENGINE
    from rapidocr_onnxruntime import RapidOCR
    import fitz
    import numpy as np
    from PIL import Image

    if _RAPIDOCR_ENGINE is None:
        _RAPIDOCR_ENGINE = RapidOCR()

    doc = fitz.open(ruta_pdf)
    partes = []
    try:
        for numero in range(inicio, fin + 1):
            page = doc.load_page(numero - 1)
            pix = page.get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            resultado, _ = _RAPIDOCR_ENGINE(np.array(img))
            lineas = []
            for item in resultado or []:
                if len(item) >= 3 and float(item[2] or 0) >= 0.45:
                    texto = str(item[1] or "").strip()
                    if texto:
                        lineas.append(texto)
            if lineas:
                partes.append(f"\nPAGINA {numero}\n" + "\n".join(lineas))
    finally:
        doc.close()
    return "\n".join(partes)


def _texto_pdf_rango(ruta_pdf, pagina_inicio, pagina_fin, usar_ocr=False):
    total = _contar_paginas_pdf(ruta_pdf) or 1
    inicio = max(1, int(pagina_inicio or 1))
    fin = min(total, max(inicio, int(pagina_fin or inicio)))
    candidatos = []

    try:
        from pypdf import PdfReader
        reader = PdfReader(ruta_pdf)
        total = len(reader.pages)
        partes = []
        for numero in range(inicio, fin + 1):
            try:
                texto = reader.pages[numero - 1].extract_text() or ""
            except Exception:
                texto = ""
            if texto.strip():
                partes.append(f"\nPAGINA {numero}\n{texto}")
        candidatos.append("\n".join(partes))
    except Exception:
        pass

    try:
        import fitz
        doc = fitz.open(ruta_pdf)
        total = doc.page_count
        partes = []
        for numero in range(inicio, fin + 1):
            try:
                texto = doc.load_page(numero - 1).get_text("text") or ""
            except Exception:
                texto = ""
            if texto.strip():
                partes.append(f"\nPAGINA {numero}\n{texto}")
        doc.close()
        candidatos.append("\n".join(partes))
    except Exception:
        pass

    texto = max(candidatos or [""], key=len)
    if (usar_ocr or len(texto.strip()) < 80) and _ocr_disponible():
        try:
            texto_ocr = _ocr_pdf_rango(ruta_pdf, inicio, fin)
            if len(texto_ocr.strip()) > len(texto.strip()):
                texto = texto_ocr
        except Exception:
            pass
    return texto, total, inicio, fin


def _lineas_limpias_libro(texto):
    lineas = []
    for linea in (texto or "").splitlines():
        limpia = re.sub(r"\s+", " ", linea).strip(" .,-;:|")
        if limpia:
            lineas.append(limpia)
    return lineas


def _titulo_desde_contexto_libro(texto_previo, nombre_libro, pagina, etiqueta, contador):
    descartes = (
        "pagina", "ingredientes", "elaboracion", "preparacion", "monasterio", "fundacion",
        "cantidad", "presentacion", "libro de recetas", "tiempo", "raciones", "esha",
    )
    candidatas = []
    for linea in _lineas_limpias_libro(texto_previo)[-18:]:
        normal = _normalizar(linea)
        palabras = normal.split()
        if not (1 <= len(palabras) <= 8):
            continue
        if any(normal.startswith(d) for d in descartes):
            continue
        if len(linea) < 4 or len(linea) > 80:
            continue
        candidatas.append(linea)
    for linea in reversed(candidatas):
        letras = [c for c in linea if c.isalpha()]
        if letras and sum(1 for c in letras if c.isupper()) / max(len(letras), 1) > 0.65:
            return linea[:120]
    if candidatas:
        return candidatas[-1][:120]
    return f"{etiqueta or nombre_libro} pagina {pagina} propuesta {contador}"


def _es_linea_cantidad_libro(linea):
    normal = _normalizar(linea)
    if normal in {"presentacion", "cantidad", "ingredientes"}:
        return False
    return bool(re.match(r"^(\d+([,.]\d+)?|[¼½¾]|1/2|1/4|3/4)\s*(kg|g|gr|ml|l|dl|cl|ud|u|rac)?\b", str(linea or "").strip(), re.IGNORECASE))


def _normalizar_ingredientes_libro(ingredientes):
    limpios = [
        i for i in ingredientes
        if _normalizar(i) not in {"presentacion", "cantidad", "ingredientes"}
    ]
    cantidades = [i for i in limpios if _es_linea_cantidad_libro(i)]
    nombres = [i for i in limpios if not _es_linea_cantidad_libro(i)]
    if len(cantidades) >= 3 and len(nombres) >= 3 and abs(len(cantidades) - len(nombres)) <= 3:
        combinados = []
        for idx, nombre in enumerate(nombres):
            cantidad = cantidades[idx] if idx < len(cantidades) else ""
            combinados.append(f"{nombre} - {cantidad}".strip(" -"))
        return combinados
    return limpios


def _extraer_ingredientes_libro(texto):
    ingredientes = []
    for linea in _lineas_limpias_libro(texto):
        if re.search(r"(?i)\b(elaboraci[oó0]n|preparaci[oó0]n|modo de hacerlo|observaciones?)\b", linea):
            break
        if len(linea) > 120:
            continue
        limpia = _limpiar_linea_receta(linea)
        normal = _normalizar(limpia)
        if len(limpia) < 3:
            continue
        if normal.startswith(("tiempo ", "dificultad ", "preparacion ", "elaboracion ")):
            continue
        ingredientes.append(limpia)
    return _normalizar_ingredientes_libro(ingredientes[:80])


def _ingredientes_libro_son_receta(ingredientes):
    if len(ingredientes or []) < 2:
        return False
    con_medida = 0
    cortos = 0
    largas = 0
    alimentos = {
        "harina", "azucar", "huevo", "huevos", "yema", "yemas", "leche", "aceite",
        "manteca", "mantequilla", "limon", "limones", "arroz", "pollo", "carne",
        "pescado", "merluza", "bacalao", "salmon", "gamba", "verdura", "cebolla",
        "ajo", "tomate", "nata", "queso", "almendra", "almendras", "chocolate",
        "patata", "patatas", "sal", "pimienta", "vino", "coñac", "brandy",
    }
    alimento_detectado = False
    for linea in ingredientes:
        normal = _normalizar(linea)
        partes = set(normal.split())
        tiene_alimento = any(a in partes for a in alimentos)
        tiene_unidad = any(u in partes for u in ["kg", "gr", "gramos", "g", "ml", "litro", "litros", "unidad", "unidades", "cucharada"])
        if tiene_unidad or (re.search(r"\b\d+([,.]\d+)?\b", linea) and tiene_alimento):
            con_medida += 1
        if len(linea) <= 70 and len(linea.split()) <= 8:
            cortos += 1
        if len(linea.split()) > 9:
            largas += 1
        if tiene_alimento:
            alimento_detectado = True
    if largas > max(2, len(ingredientes) * 0.35):
        return False
    return con_medida >= 2 or (cortos >= 4 and alimento_detectado)


def _extraer_propuestas_libro_grande(texto, libro, inicio, fin, etiqueta="", contexto=""):
    procesador = ProcesadorPDF()
    propuestas = []
    bloques = re.split(r"\nPAGINA\s+(\d+)\n", texto or "")
    nombre_libro = os.path.splitext(os.path.basename(libro["nombre"]))[0]
    contador = 1
    for idx in range(1, len(bloques), 2):
        pagina = bloques[idx]
        contenido = bloques[idx + 1] if idx + 1 < len(bloques) else ""
        partes = re.split(r"(?i)ingredientes\s*(?:para[^:\n]*)?:", contenido, maxsplit=1)
        if len(partes) < 2:
            continue
        antes = [l.strip(" .,-;:") for l in partes[0].splitlines() if l.strip()]
        despues = partes[1]
        corte = re.split(r"(?i)elaboraci[oó]n\s*:", despues, maxsplit=1)
        ingredientes_txt = corte[0]
        elaboracion = corte[1] if len(corte) > 1 else ""
        ingredientes = []
        for linea in ingredientes_txt.splitlines():
            linea = _limpiar_linea_receta(linea)
            if len(linea) >= 3:
                ingredientes.append(linea)
        if not ingredientes:
            continue

        titulo = ""
        for linea in reversed(antes[-8:]):
            if 5 <= len(linea) <= 70 and not linea.lower().startswith(("este", "esta", "con ", "la ", "el ")):
                titulo = linea
                break
        if not titulo or " " not in titulo:
            titulo = f"{etiqueta or nombre_libro} pagina {pagina} propuesta {contador}"

        texto_propuesta = "\n".join([titulo, contexto, "\n".join(ingredientes), elaboracion])
        propuesta_id = hashlib.sha1(
            f"{libro['id']}|{pagina}|{titulo}|{texto_propuesta[:300]}".encode("utf-8", errors="ignore")
        ).hexdigest()[:16]
        propuestas.append({
            "id": propuesta_id,
            "fuente": libro["nombre"],
            "tipo_fuente": "libro_grande",
            "paginas": str(pagina),
            "nombre": titulo[:120],
            "categoria": etiqueta or "",
            "ingredientes": ingredientes[:80],
            "elaboracion": re.sub(r"\s+", " ", elaboracion).strip()[:4000],
            "alergenos": ", ".join(procesador.buscar_alergenos(texto_propuesta)),
            "confianza": min(80, 45 + len(ingredientes) * 3 + (10 if elaboracion.strip() else 0)),
        })
        contador += 1
    return propuestas


def _extraer_propuestas_libro_grande(texto, libro, inicio, fin, etiqueta="", contexto=""):
    procesador = ProcesadorPDF()
    propuestas = []
    bloques = re.split(r"\nPAGINA\s+(\d+)\n", texto or "")
    nombre_libro = os.path.splitext(os.path.basename(libro["nombre"]))[0]
    contador = 1
    for idx in range(1, len(bloques), 2):
        pagina = bloques[idx]
        contenido = bloques[idx + 1] if idx + 1 < len(bloques) else ""
        marcas = list(re.finditer(r"(?i)\bingredientes?\b\s*(?:para[^:\n]{0,80})?[:\.]?", contenido))
        if not marcas:
            continue
        for posicion, marca in enumerate(marcas):
            siguiente = marcas[posicion + 1].start() if posicion + 1 < len(marcas) else len(contenido)
            antes = contenido[:marca.start()]
            despues = contenido[marca.end():siguiente]
            corte = re.split(
                r"(?i)\b(elaboraci[oó0]n|elaboraci[oÃ³]n|preparaci[oó0]n|modo de hacerlo)\b\s*[:\.]?",
                despues,
                maxsplit=1,
            )
            ingredientes_txt = corte[0]
            elaboracion = corte[2] if len(corte) > 2 else ""
            ingredientes = _extraer_ingredientes_libro(ingredientes_txt)
            if not _ingredientes_libro_son_receta(ingredientes):
                continue
            titulo = _titulo_desde_contexto_libro(antes, nombre_libro, pagina, etiqueta, contador)
            if _nombre_memoria_debil(titulo):
                titulo = f"{etiqueta or nombre_libro} pagina {pagina} propuesta {contador}"
            texto_propuesta = "\n".join([titulo, contexto, "\n".join(ingredientes), elaboracion])
            propuesta_id = hashlib.sha1(
                f"{libro['id']}|{pagina}|{titulo}|{texto_propuesta[:300]}".encode("utf-8", errors="ignore")
            ).hexdigest()[:16]
            propuestas.append({
                "id": propuesta_id,
                "fuente": libro["nombre"],
                "tipo_fuente": "libro_grande",
                "paginas": str(pagina),
                "nombre": titulo[:120],
                "categoria": etiqueta or "",
                "ingredientes": ingredientes[:80],
                "elaboracion": re.sub(r"\s+", " ", elaboracion).strip()[:4000],
                "alergenos": ", ".join(procesador.buscar_alergenos(texto_propuesta)),
                "confianza": min(85, 45 + len(ingredientes) * 3 + (10 if elaboracion.strip() else 0)),
            })
            contador += 1
    return propuestas


def _indexar_libro_por_paginas(source_id, pagina_inicio, pagina_fin, etiqueta="", contexto="", usar_ocr=False):
    libro = _resolver_fuente_libro(source_id)
    if not libro:
        raise ValueError("No se encontro el libro seleccionado.")
    ruta_pdf = _preparar_pdf_libro(libro)
    texto, total, inicio, fin = _texto_pdf_rango(ruta_pdf, pagina_inicio, pagina_fin, usar_ocr=usar_ocr)
    if not texto.strip():
        return {
            "libro": libro,
            "total": total,
            "inicio": inicio,
            "fin": fin,
            "nuevas": 0,
            "propuestas": [],
            "caracteres": 0,
            "detectadas": 0,
            "aviso": "No se ha podido leer texto en esas paginas. Probablemente es un PDF escaneado y necesita OCR.",
            "ocr": bool(usar_ocr),
        }

    propuestas = _extraer_propuestas_libro_grande(texto, libro, inicio, fin, etiqueta, contexto)
    aviso = ""
    if not propuestas:
        aviso = (
            "Se ha leido texto, pero no se han detectado recetas estructuradas. "
            "Prueba otro tramo o paginas donde aparezcan ingredientes."
        )

    datos = _cargar_biblioteca_recetas()
    existentes = {p.get("id") for p in datos.get("propuestas", [])}
    nuevas = [p for p in propuestas if p.get("id") not in existentes]
    propuestas_resultado = []
    for propuesta in propuestas:
        copia = dict(propuesta)
        copia["ya_existia"] = propuesta.get("id") in existentes
        propuestas_resultado.append(copia)
    datos.setdefault("propuestas", []).extend(nuevas)
    datos["propuestas"].sort(key=lambda p: (-int(p.get("confianza") or 0), p.get("nombre", "").lower()))
    datos["actualizado"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    _guardar_biblioteca_recetas(datos)
    return {
        "libro": libro,
        "total": total,
        "inicio": inicio,
        "fin": fin,
        "nuevas": len(nuevas),
        "existentes": len(propuestas) - len(nuevas),
        "propuestas": propuestas_resultado,
        "caracteres": len(texto),
        "detectadas": len(propuestas),
        "aviso": aviso,
        "ocr": bool(usar_ocr),
    }


def _guardar_propuestas_libro(libro, propuestas, texto_len, total, inicio, fin, usar_ocr=False, aviso=""):
    datos = _cargar_biblioteca_recetas()
    existentes = {p.get("id") for p in datos.get("propuestas", [])}
    nuevas = [p for p in propuestas if p.get("id") not in existentes]
    propuestas_resultado = []
    for propuesta in propuestas:
        copia = dict(propuesta)
        copia["ya_existia"] = propuesta.get("id") in existentes
        propuestas_resultado.append(copia)
    datos.setdefault("propuestas", []).extend(nuevas)
    datos.setdefault("libros_indexados", {})
    datos["libros_indexados"][libro["nombre"]] = {
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "paginas": total,
        "inicio": inicio,
        "fin": fin,
        "detectadas": len(propuestas),
        "nuevas": len(nuevas),
        "ocr": bool(usar_ocr),
        "aviso": aviso,
    }
    datos["propuestas"].sort(key=lambda p: (-int(p.get("confianza") or 0), p.get("nombre", "").lower()))
    datos["actualizado"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    _guardar_biblioteca_recetas(datos)
    return {
        "libro": libro,
        "total": total,
        "inicio": inicio,
        "fin": fin,
        "nuevas": len(nuevas),
        "existentes": len(propuestas) - len(nuevas),
        "propuestas": propuestas_resultado,
        "caracteres": texto_len,
        "detectadas": len(propuestas),
        "aviso": aviso,
        "ocr": bool(usar_ocr),
    }


def _indexar_libro_completo(source_id, etiqueta="", contexto="", usar_ocr=False):
    libro = _resolver_fuente_libro(source_id)
    if not libro:
        raise ValueError("No se encontro el libro seleccionado.")
    ruta_pdf = _preparar_pdf_libro(libro)
    total = _contar_paginas_pdf(ruta_pdf) or 1
    if usar_ocr and total > 40:
        raise ValueError("Para OCR, indexa por tramos de hasta 8 paginas. Un libro entero escaneado tardaria demasiado.")

    tamano_tramo = 8 if usar_ocr else 25
    propuestas = []
    caracteres = 0
    for inicio in range(1, total + 1, tamano_tramo):
        fin = min(total, inicio + tamano_tramo - 1)
        texto, _, inicio_real, fin_real = _texto_pdf_rango(ruta_pdf, inicio, fin, usar_ocr=usar_ocr)
        caracteres += len(texto or "")
        if not texto.strip():
            continue
        propuestas.extend(_extraer_propuestas_libro_grande(texto, libro, inicio_real, fin_real, etiqueta, contexto))

    aviso = ""
    if not propuestas:
        if caracteres < 200:
            aviso = "No se ha podido extraer texto util. Probablemente es un PDF escaneado y necesita OCR por tramos."
        else:
            aviso = "Se ha leido el libro, pero no se han detectado recetas estructuradas."
    return _guardar_propuestas_libro(libro, propuestas, caracteres, total, 1, total, usar_ocr, aviso)


def _fuentes_libros_indexadas():
    datos = _cargar_biblioteca_recetas()
    desde_propuestas = {
        p.get("fuente")
        for p in datos.get("propuestas", [])
        if p.get("tipo_fuente") == "libro_grande" and p.get("fuente")
    }
    desde_registro = set((datos.get("libros_indexados") or {}).keys())
    return desde_propuestas | desde_registro


def _estado_indexacion_libros():
    libros = _listar_libros_grandes()
    indexadas = _fuentes_libros_indexadas()
    datos = _cargar_biblioteca_recetas()
    registros = datos.get("libros_indexados") or {}
    pendientes = [libro for libro in libros if libro.get("nombre") not in indexadas]
    necesita_ocr = [
        nombre for nombre, info in registros.items()
        if "necesita OCR" in (info.get("aviso") or "")
    ]
    sin_recetas = [
        nombre for nombre, info in registros.items()
        if int(info.get("detectadas") or 0) == 0 and nombre not in necesita_ocr
    ]
    return {
        "total": len(libros),
        "indexados": len(libros) - len(pendientes),
        "pendientes": len(pendientes),
        "pendientes_lista": pendientes,
        "necesita_ocr": len(necesita_ocr),
        "sin_recetas": len(sin_recetas),
    }


def _indexar_lote_libros_pendientes(limite=5, etiqueta="", contexto=""):
    estado = _estado_indexacion_libros()
    pendientes = estado["pendientes_lista"][: max(1, min(int(limite or 5), 20))]
    resultados = []
    for libro in pendientes:
        try:
            resultado = _indexar_libro_completo(libro["id"], etiqueta, contexto, usar_ocr=False)
            resultados.append({
                "libro": libro["nombre"],
                "nuevas": resultado.get("nuevas", 0),
                "detectadas": resultado.get("detectadas", 0),
                "paginas": resultado.get("total", 0),
                "aviso": resultado.get("aviso", ""),
                "ok": True,
            })
        except Exception as exc:
            resultados.append({
                "libro": libro["nombre"],
                "nuevas": 0,
                "detectadas": 0,
                "paginas": 0,
                "aviso": str(exc)[:240],
                "ok": False,
            })
    return {
        "procesados": len(resultados),
        "nuevas": sum(r["nuevas"] for r in resultados),
        "detectadas": sum(r["detectadas"] for r in resultados),
        "resultados": resultados,
        "estado": _estado_indexacion_libros(),
    }


def _tecnicas_receta_libro(propuesta):
    texto = _texto_propuesta_memoria(propuesta)
    tecnicas = []
    mapa = [
        ("horno", ["horno", "hornear", "gratinar", "asar"]),
        ("plancha/salteado", ["plancha", "saltear", "salteado", "sarten"]),
        ("coccion", ["cocer", "coccion", "hervir", "blanquear"]),
        ("fritura/rebozado", ["freir", "frito", "rebozar", "enharinar"]),
        ("guiso/salsa", ["guisar", "estofar", "salsa", "fondo", "veloute"]),
        ("frio/ensalada", ["frio", "ensalada", "marinar", "escabeche"]),
        ("postre/pasteleria", ["hornear", "batir", "almibar", "crema", "bizcocho"]),
    ]
    for nombre, claves in mapa:
        if any(clave in texto for clave in claves):
            tecnicas.append(nombre)
    return tecnicas[:4] or ["revisar tecnica"]


def _riesgos_receta_libro(propuesta):
    texto = _texto_propuesta_memoria(propuesta)
    riesgos = []
    if any(p in texto for p in ["huevo", "mayonesa", "mahonesa", "crema", "nata"]):
        riesgos.append("huevo/lacteo o salsa sensible")
    if any(p in texto for p in ["pescado", "merluza", "bacalao", "salmon", "marisco", "gamba", "langostino"]):
        riesgos.append("pescado/marisco")
    if any(p in texto for p in ["pollo", "carne", "cordero", "cerdo", "ternera"]):
        riesgos.append("proteina carnica")
    if any(p in texto for p in ["frito", "reboz", "enharin"]):
        riesgos.append("fritura/rebozado")
    return riesgos or ["riesgo general bajo-medio"]


def _acciones_receta_libro(propuesta):
    pase = _clasificar_pase_culinario(propuesta)
    coste = _estimar_coste_culinario(propuesta)
    acciones = ["Revisar alergenos antes de publicar."]
    if pase in {"segundos", "primeros"}:
        acciones.append("Definir racion, guarnicion y merma.")
    if coste != "bajo":
        acciones.append("Validar escandallo con producto real del centro.")
    if "fritura/rebozado" in _riesgos_receta_libro(propuesta):
        acciones.append("Valorar alternativa horno/plancha si el servicio es buffet o saludable.")
    acciones.append("Probar una racion testigo antes de pasarla a carta/menu.")
    return acciones[:5]


def _enriquecer_receta_libro(propuesta):
    item = dict(propuesta)
    item["pase"] = _clasificar_pase_culinario(propuesta)
    item["coste_estimado"] = _estimar_coste_culinario(propuesta)
    item["dificultad"] = _estimar_dificultad_culinaria(propuesta)
    item["tecnicas"] = _tecnicas_receta_libro(propuesta)
    item["riesgos"] = _riesgos_receta_libro(propuesta)
    item["acciones_chef"] = _acciones_receta_libro(propuesta)
    return item


def _clasificar_pase_culinario(propuesta):
    nombre_categoria = _normalizar(" ".join([propuesta.get("nombre", ""), propuesta.get("categoria", "")]))
    texto = _normalizar(
        " ".join([
            nombre_categoria,
            " ".join(propuesta.get("ingredientes") or []),
        ])
    )
    salado = any(p in texto for p in [
        "pescado", "marisco", "langostino", "gamba", "rodaballo", "merluza",
        "pollo", "ternera", "cerdo", "atun", "bacalao", "salsa", "pimenton",
        "empanada", "arroz", "pasta", "legumbre", "verdura", "garbanzo",
    ])
    postres_fuertes = [
        "postre", "tarta", "flan", "bizcocho", "helado", "sorbete", "natillas",
        "crema catalana", "arroz con leche", "tiramisu", "panna cotta", "pannacota",
        "mousse", "brownie", "profiterol", "pastel", "fruta preparada",
    ]
    nombre_envuelto = f" {nombre_categoria} "
    texto_envuelto = f" {texto} "
    if any(f" {p} " in nombre_envuelto for p in postres_fuertes):
        return "postres"
    if any(f" {p} " in texto_envuelto for p in ["chocolate", "caramelo", "vainilla", "merengue"]) and not any(
        f" {p} " in texto_envuelto for p in ["salsa", "agridulce", "pimenton dulce", "dulce de membrillo"]
    ) and not salado:
        return "postres"
    if any(p in texto for p in ["sopa", "crema", "ensalada", "entrante", "tosta", "gazpacho", "croqueta"]):
        return "entrantes"
    if any(p in texto for p in ["empanada", "quiche", "tortilla"]):
        return "primeros"
    if any(p in texto for p in ["pescado", "merluza", "salmon", "bacalao", "pollo", "ternera", "cerdo", "cordero", "conejo", "marisco", "gamba", "langostino", "rodaballo", "atun"]):
        return "segundos"
    if any(p in texto for p in ["arroz", "pasta", "lasa", "canelon", "legumbre", "garbanzo", "lenteja", "verdura"]):
        return "primeros"
    return "primeros"


def _estimar_coste_culinario(propuesta):
    texto = _normalizar(" ".join([propuesta.get("nombre", ""), " ".join(propuesta.get("ingredientes") or [])]))
    ingredientes = len(propuesta.get("ingredientes") or [])
    if any(p in texto for p in ["bogavante", "langosta", "foie", "solomillo", "caviar", "trufa"]):
        return "alto"
    if any(p in texto for p in ["bacalao", "salmon", "cordero", "ternera", "gamba", "marisco"]) or ingredientes > 14:
        return "medio"
    return "bajo"


def _estimar_dificultad_culinaria(propuesta):
    texto = _normalizar((propuesta.get("elaboracion") or "") + " " + propuesta.get("nombre", ""))
    if any(p in texto for p in ["rellen", "wellington", "hojaldre", "emulsion", "baño maria", "banomaria"]):
        return "alta"
    if len(texto.split()) > 70 or len(propuesta.get("ingredientes") or []) > 12:
        return "media"
    return "baja"


def _puntuar_item_culinario(propuesta, contexto, concepto, servicio, objetivo, cliente="", precio_objetivo="", stock=""):
    texto = _normalizar(
        " ".join([
            propuesta.get("nombre", ""),
            propuesta.get("fuente", ""),
            propuesta.get("categoria", ""),
            propuesta.get("elaboracion", ""),
            " ".join(propuesta.get("ingredientes") or []),
        ])
    )
    contexto_total = _normalizar(f"{contexto} {concepto} {servicio} {objetivo} {cliente} {precio_objetivo}")
    coste = _estimar_coste_culinario(propuesta)
    dificultad = _estimar_dificultad_culinaria(propuesta)
    ingredientes = len(propuesta.get("ingredientes") or [])
    puntuacion = {
        "encaje": 45,
        "coste": 60 if coste == "bajo" else 45 if coste == "medio" else 25,
        "presentacion": 55,
        "produccion": 70 if dificultad == "baja" else 55 if dificultad == "media" else 35,
        "appcc": 75,
        "stock": 20,
    }
    if any(p in contexto_total for p in ["monasterio", "convento", "monacal"]) and any(p in texto for p in ["monacal", "monasterio", "convento"]):
        puntuacion["encaje"] += 35
        puntuacion["presentacion"] += 10
    if any(p in contexto_total for p in ["termal", "balneario", "salud", "senior", "wellness"]) and any(p in texto for p in ["verdura", "pescado", "crema", "sopa", "liger"]):
        puntuacion["encaje"] += 25
        puntuacion["appcc"] += 5
    if "buffet" in contexto_total:
        puntuacion["produccion"] += 15 if dificultad != "alta" else -10
    if any(p in contexto_total for p in ["econom", "coste", "rentable", "barato"]) and coste == "bajo":
        puntuacion["coste"] += 25
    if any(p in contexto_total for p in ["elegante", "premium", "carta", "banquete"]):
        puntuacion["presentacion"] += 15 if coste != "bajo" or ingredientes >= 6 else 5
    if any(p in texto for p in ["huevo", "mayonesa", "pescado", "marisco", "pollo", "rellen"]):
        puntuacion["appcc"] -= 15
    stock_tokens = _tokens_busqueda_biblioteca(stock)
    if stock_tokens:
        coincidencias_stock = sum(1 for token in stock_tokens if token in texto)
        puntuacion["stock"] = min(100, 20 + coincidencias_stock * 25)
    for clave in puntuacion:
        puntuacion[clave] = max(0, min(100, int(puntuacion[clave])))
    puntuacion["total"] = int(
        puntuacion["encaje"] * 0.28
        + puntuacion["coste"] * 0.16
        + puntuacion["presentacion"] * 0.16
        + puntuacion["produccion"] * 0.16
        + puntuacion["appcc"] * 0.14
        + puntuacion["stock"] * 0.10
    )
    return puntuacion


def _motivos_contexto_culinario(propuesta, contexto, concepto, servicio, objetivo, cliente="", stock=""):
    texto = _normalizar(
        " ".join([
            propuesta.get("nombre", ""),
            propuesta.get("fuente", ""),
            propuesta.get("categoria", ""),
            propuesta.get("elaboracion", ""),
            " ".join(propuesta.get("ingredientes") or []),
        ])
    )
    motivos = []
    contexto_total = _normalizar(f"{contexto} {concepto} {servicio} {objetivo} {cliente}")
    if any(p in contexto_total for p in ["monasterio", "convento", "monacal"]) and any(p in texto for p in ["monacal", "monasterio", "convento"]):
        motivos.append("refuerza el relato historico del establecimiento")
    if any(p in contexto_total for p in ["termal", "balneario", "salud"]) and any(p in texto for p in ["verdura", "pescado", "crema", "sopa", "liger"]):
        motivos.append("encaja con una linea saludable y ligera")
    if "buffet" in contexto_total:
        motivos.append("puede adaptarse a produccion por bandejas y reposicion")
    if any(p in contexto_total for p in ["econom", "coste", "rentable"]) and _estimar_coste_culinario(propuesta) == "bajo":
        motivos.append("ayuda a controlar coste")
    if stock and any(token in texto for token in _tokens_busqueda_biblioteca(stock)):
        motivos.append("aprovecha productos indicados en stock")
    if not motivos:
        motivos.append("aporta variedad usando memoria culinaria privada")
    return motivos[:3]


def _concepto_gastronomico(modo, contexto, concepto, servicio, temporada, objetivo, cliente):
    base = concepto or "cocina de temporada"
    piezas = [base]
    if contexto:
        piezas.append(f"adaptada a {contexto}")
    if cliente:
        piezas.append(f"pensada para {cliente}")
    if objetivo:
        piezas.append(f"con enfoque {objetivo}")
    if temporada:
        piezas.append(f"en {temporada}")
    return f"{modo.replace('_', ' ').capitalize()}: " + ", ".join(piezas) + "."


def _limites_por_modo(modo, servicio):
    modo = (modo or "menu").lower()
    if modo == "receta":
        return {"entrantes": 1, "primeros": 1, "segundos": 1, "postres": 1}
    if modo == "carta":
        return {"entrantes": 4, "primeros": 4, "segundos": 5, "postres": 3}
    if modo == "buffet":
        return {"entrantes": 4, "primeros": 4, "segundos": 4, "postres": 3}
    if modo == "banquete":
        return {"entrantes": 3, "primeros": 2, "segundos": 3, "postres": 2}
    if modo == "stock":
        return {"entrantes": 2, "primeros": 3, "segundos": 3, "postres": 1}
    return {"entrantes": 3, "primeros": 3, "segundos": 3, "postres": 2}


def _generar_propuesta_culinaria(datos, contexto, concepto, servicio, temporada, objetivo, restricciones, pax, modo="menu", cliente="", precio_objetivo="", stock=""):
    criterio = " ".join([objetivo, temporada, restricciones, precio_objetivo, stock])
    consulta = " ".join([concepto, contexto, servicio, temporada, restricciones, cliente, stock])
    candidatos = _filtrar_memoria_culinaria(datos, consulta, contexto, servicio, criterio)
    if not candidatos:
        candidatos = sorted(
            _propuestas_memoria_validas(datos),
            key=lambda p: (-int(p.get("confianza") or 0), p.get("nombre", "").lower()),
        )[:40]

    usados = set()
    for candidato in candidatos:
        candidato["ranking"] = _puntuar_item_culinario(candidato, contexto, concepto, servicio, objetivo, cliente, precio_objetivo, stock)
    candidatos.sort(key=lambda p: (-p.get("ranking", {}).get("total", 0), -int(p.get("confianza") or 0), p.get("nombre", "").lower()))

    pases = {"entrantes": [], "primeros": [], "segundos": [], "postres": []}
    limites = _limites_por_modo(modo, servicio)
    for propuesta in candidatos:
        if propuesta.get("id") in usados:
            continue
        calidad = _calidad_propuesta_memoria(propuesta)
        if calidad["score"] < 55:
            continue
        pase = _clasificar_pase_culinario(propuesta)
        limite = limites.get(pase, 2)
        if len(pases[pase]) >= limite:
            continue
        ranking = propuesta.get("ranking") or _puntuar_item_culinario(propuesta, contexto, concepto, servicio, objetivo, cliente, precio_objetivo, stock)
        item = {
            "id": propuesta.get("id"),
            "nombre": propuesta.get("nombre"),
            "fuente": propuesta.get("fuente"),
            "paginas": propuesta.get("paginas"),
            "alergenos": propuesta.get("alergenos") or "revisar",
            "coste": _estimar_coste_culinario(propuesta),
            "dificultad": _estimar_dificultad_culinaria(propuesta),
            "ranking": ranking,
            "motivos": _motivos_contexto_culinario(propuesta, contexto, concepto, servicio, objetivo, cliente, stock),
            "ingredientes": calidad["ingredientes_limpios"][:8],
            "calidad_memoria": calidad["score"],
            "avisos_memoria": calidad["avisos"],
        }
        pases[pase].append(item)
        usados.add(propuesta.get("id"))

    alertas = [
        "Revisar alergenos antes de publicar o imprimir carta.",
        "Validar escandallo con productos reales del centro.",
        "Comprobar APPCC si hay huevo, pescado, carne rellena, abatimiento o mantenimiento en caliente.",
    ]
    if "buffet" in _normalizar(servicio):
        alertas.append("Para buffet, definir reposicion, temperatura de servicio y vida util por bandeja.")
    if modo == "stock" or stock:
        alertas.append("Confirmar existencias reales y caducidades antes de cerrar la propuesta de aprovechamiento.")
    if restricciones:
        alertas.append(f"Contrastar restricciones indicadas: {restricciones}.")

    return {
        "modo": modo,
        "contexto": contexto,
        "concepto": concepto,
        "servicio": servicio,
        "temporada": temporada,
        "objetivo": objetivo,
        "restricciones": restricciones,
        "pax": pax,
        "cliente": cliente,
        "precio_objetivo": precio_objetivo,
        "stock": stock,
        "concepto_gastronomico": _concepto_gastronomico(modo, contexto, concepto, servicio, temporada, objetivo, cliente),
        "pases": pases,
        "alertas": alertas,
        "candidatos_usados": len(usados),
        "candidatos_revisados": len(candidatos),
    }


def _texto_propuesta_memoria(propuesta):
    return _normalizar(
        " ".join([
            propuesta.get("nombre", ""),
            propuesta.get("fuente", ""),
            propuesta.get("categoria", ""),
            propuesta.get("alergenos", ""),
            propuesta.get("elaboracion", ""),
            " ".join(propuesta.get("ingredientes") or []),
        ])
    )


def _ingredientes_receta_actual(conn, receta_id):
    rows = conn.execute(
        """
        SELECT p.nombre, COALESCE(ri.cantidad, 0) AS cantidad,
               COALESCE(p.unidad, '') AS unidad,
               COALESCE(p.alergenos, '') AS alergenos,
               COALESCE(p.precio, 0) AS precio
        FROM recetas_ingredientes ri
        JOIN productos p ON p.id = ri.producto_id
        WHERE ri.receta_id = ?
        ORDER BY p.nombre
        """,
        (receta_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _recetas_para_selector_culinario(conn, empresa_id, centro_id=None, limite=350):
    where = ["empresa_id = ?"]
    params = [empresa_id]
    if centro_id:
        where.append("centro_id = ?")
        params.append(centro_id)
    rows = conn.execute(
        f"""
        SELECT id, nombre, COALESCE(categoria, '') AS categoria
        FROM recetas
        WHERE {" AND ".join(where)}
        ORDER BY LOWER(nombre)
        LIMIT ?
        """,
        params + [limite],
    ).fetchall()
    return [dict(row) for row in rows]


def _receta_actual_detalle(conn, empresa_id, receta_id, centro_id=None):
    row = conn.execute(
        """
        SELECT *
        FROM recetas
        WHERE id = ? AND empresa_id = ?
          AND (? IS NULL OR centro_id = ?)
        """,
        (receta_id, empresa_id, centro_id, centro_id),
    ).fetchone()
    if not row:
        return None
    receta = dict(row)
    receta["ingredientes_detalle"] = _ingredientes_receta_actual(conn, receta_id)
    receta["ingredientes"] = [
        f"{ing['nombre']} - {ing['cantidad']:g} {ing['unidad']}".strip()
        for ing in receta["ingredientes_detalle"]
    ]
    return receta


def _tokens_ingredientes_actuales(receta):
    tokens = set()
    for ing in receta.get("ingredientes_detalle") or []:
        tokens.update(_tokens_busqueda_biblioteca(ing.get("nombre", "")))
    return tokens


def _resumir_elaboracion_candidata(texto, limite=4):
    partes = re.split(r"(?<=[.;:])\s+|\s+\|\s+|\n+", texto or "")
    limpias = []
    vistos = set()
    for parte in partes:
        parte = re.sub(r"\s+", " ", parte).strip(" -.;:")
        normal = _normalizar(parte)
        if len(parte) < 12 or normal in vistos or _linea_memoria_ruido(parte):
            continue
        vistos.add(normal)
        limpias.append(parte[:180])
        if len(limpias) >= limite:
            break
    return limpias


def _mejoras_receta_existente(datos, receta, contexto="", servicio="", objetivo="", restricciones="", stock=""):
    consulta = " ".join([
        receta.get("nombre", ""),
        receta.get("categoria", ""),
        " ".join(ing.get("nombre", "") for ing in receta.get("ingredientes_detalle") or []),
        contexto,
        servicio,
        objetivo,
        restricciones,
        stock,
    ])
    candidatos = _filtrar_memoria_culinaria(datos, consulta, contexto, servicio, objetivo + " " + restricciones + " " + stock)
    if not candidatos:
        actuales = _tokens_ingredientes_actuales(receta)
        candidatos = []
        for propuesta in _propuestas_memoria_validas(datos):
            calidad = _calidad_propuesta_memoria(propuesta)
            if calidad["score"] < 55:
                continue
            texto = _texto_propuesta_memoria(propuesta)
            score = sum(1 for token in actuales if token in texto)
            if score:
                item = dict(propuesta)
                item["score_memoria"] = int(propuesta.get("confianza") or 0) + score * 18
                item["calidad_memoria"] = calidad["score"]
                item["avisos_memoria"] = calidad["avisos"]
                item["ingredientes_limpios"] = calidad["ingredientes_limpios"]
                candidatos.append(item)
        candidatos.sort(key=lambda p: (-p.get("score_memoria", 0), p.get("nombre", "").lower()))

    comparaciones = []
    actuales_tokens = _tokens_ingredientes_actuales(receta)
    for candidato in candidatos[:8]:
        calidad = _calidad_propuesta_memoria(candidato)
        if calidad["score"] < 55:
            continue
        ingredientes_fuente = calidad["ingredientes_limpios"][:12]
        candidatos_limpios = [_ingrediente_limpio(i) for i in ingredientes_fuente]
        candidatos_limpios = [i for i in candidatos_limpios if i]
        fuente_tokens = set()
        for ing in candidatos_limpios:
            fuente_tokens.update(_tokens_busqueda_biblioteca(ing))
        comunes = sorted(actuales_tokens & fuente_tokens)
        posibles_altas = [
            ing for ing in candidatos_limpios
            if _normalizar(ing) and not any(token in actuales_tokens for token in _tokens_busqueda_biblioteca(ing))
        ][:6]
        revisar_actuales = [
            ing["nombre"] for ing in receta.get("ingredientes_detalle") or []
            if not any(token in fuente_tokens for token in _tokens_busqueda_biblioteca(ing.get("nombre", "")))
        ][:6]
        acciones = [
            "Mantener la receta actual como base correcta y usar la fuente solo como apoyo.",
            "No cambiar cantidades automaticamente: revisar coste, merma y racion real antes de aplicar.",
        ]
        if posibles_altas:
            acciones.append("Valorar altas o mejoras de sabor: " + ", ".join(posibles_altas[:4]) + ".")
        if candidato.get("elaboracion"):
            acciones.append("Mejorar elaboracion escrita con pasos mas limpios y ordenados.")
        alergenos_actuales = receta.get("alergenos") or "sin declarar"
        alergenos_fuente = candidato.get("alergenos") or "revisar"
        comparaciones.append({
            "receta_id": receta.get("id"),
            "receta_nombre": receta.get("nombre"),
            "fuente_nombre": candidato.get("nombre"),
            "fuente": candidato.get("fuente"),
            "paginas": candidato.get("paginas"),
            "score": candidato.get("score_memoria") or candidato.get("ranking", {}).get("total") or candidato.get("confianza") or 0,
            "ingredientes_actuales": receta.get("ingredientes") or [],
            "ingredientes_fuente": ingredientes_fuente,
            "coincidencias": comunes[:8],
            "posibles_altas": posibles_altas,
            "revisar_actuales": revisar_actuales,
            "elaboracion_actual": receta.get("descripcion") or "",
            "elaboracion_propuesta": _resumir_elaboracion_candidata(candidato.get("elaboracion")),
            "alergenos_actuales": alergenos_actuales,
            "alergenos_fuente": alergenos_fuente,
            "acciones": acciones[:5],
        })
    return comparaciones[:4]


def _detectar_intencion_asesor(consulta):
    texto = _normalizar(consulta)
    if any(p in texto for p in ["mejorar", "refinar", "presentacion", "emplat", "elegante", "premium"]):
        return "mejora"
    if any(p in texto for p in ["coste", "barato", "econom", "rentable", "margen", "escandallo"]):
        return "coste"
    if any(p in texto for p in ["appcc", "riesgo", "huevo", "mayonesa", "temperatura", "abat", "congel", "alergen"]):
        return "appcc"
    if any(p in texto for p in ["sustitu", "alternativa", "sin gluten", "sin lactosa", "vegano", "vegetar"]):
        return "alternativas"
    if any(p in texto for p in ["buffet", "banquete", "carta", "menu", "servicio"]):
        return "servicio"
    return "general"


def _recomendaciones_para_item(item, consulta, contexto, servicio, objetivo):
    texto = _texto_propuesta_memoria(item)
    intencion = _detectar_intencion_asesor(consulta + " " + objetivo)
    coste = _estimar_coste_culinario(item)
    dificultad = _estimar_dificultad_culinaria(item)
    recomendaciones = []
    cambios = []
    riesgos = []

    if intencion == "mejora":
        recomendaciones.append("Trabajar una version de carta: nombre corto, guarnicion definida, salsa separada y acabado al pase.")
        cambios.append("Separar base, salsa y crujiente para que el plato tenga lectura clara.")
        if coste == "bajo":
            cambios.append("Subir percepcion con hierbas frescas, aceite aromatizado o encurtido ligero sin disparar coste.")
    elif intencion == "coste":
        recomendaciones.append("Usar esta idea como base, pero controlar proteina principal y racionado.")
        cambios.append("Convertir ingredientes caros en acento, no en volumen principal.")
        cambios.append("Preparar una version buffet con guarnicion vegetal o cereal para mejorar margen.")
    elif intencion == "appcc":
        recomendaciones.append("Definir punto critico antes de publicar: temperatura, abatimiento, regeneracion y vida util.")
        if any(p in texto for p in ["huevo", "mayonesa", "pollo", "pescado", "marisco"]):
            riesgos.append("Tiene ingredientes sensibles: registrar temperatura y evitar esperas largas.")
        cambios.append("Documentar alergenos y contaminacion cruzada antes de sacarlo a carta.")
    elif intencion == "alternativas":
        recomendaciones.append("Usar la receta como matriz y crear variantes por restriccion.")
        if "gluten" in _normalizar(consulta):
            cambios.append("Sustituir harina/pasta/pan por base sin gluten validada y separar utensilios.")
        if "lactosa" in _normalizar(consulta):
            cambios.append("Cambiar nata/queso/mantequilla por alternativa vegetal o aceite emulsionado.")
        if "veg" in _normalizar(consulta):
            cambios.append("Sustituir proteina animal por legumbre, seta, berenjena o cereal con salsa intensa.")
    elif intencion == "servicio":
        recomendaciones.append("Adaptar formato al servicio antes de usarla.")
        if "buffet" in _normalizar(servicio + " " + consulta):
            cambios.append("Pensar en bandeja, reposicion, merma y estabilidad a temperatura.")
        if "carta" in _normalizar(servicio + " " + consulta):
            cambios.append("Reducir componentes y dejar acabado final para el pase.")
    else:
        recomendaciones.append("Buena candidata como punto de partida, pero debe transformarse al contexto del establecimiento.")
        cambios.append("Ajustar nombre, racion, guarnicion y tecnica antes de crear receta definitiva.")

    if dificultad == "alta":
        riesgos.append("Complejidad alta: revisar si el equipo y el servicio pueden ejecutarla sin cuellos de botella.")
    if coste == "alto":
        riesgos.append("Coste alto: conviene reservarla para carta, suplemento o evento.")
    if not riesgos:
        riesgos.append("Riesgo bajo-medio, pendiente de escandallo y APPCC real.")

    return {
        "id": item.get("id"),
        "nombre": item.get("nombre"),
        "fuente": item.get("fuente"),
        "paginas": item.get("paginas"),
        "coste": coste,
        "dificultad": dificultad,
        "alergenos": item.get("alergenos") or "revisar",
        "recomendaciones": recomendaciones,
        "cambios": cambios[:4],
        "riesgos": riesgos[:4],
        "ingredientes": (item.get("ingredientes") or [])[:10],
        "ranking": _puntuar_item_culinario(item, contexto, item.get("categoria", ""), servicio, objetivo),
    }


def _ingrediente_limpio(valor):
    texto = re.sub(r"\s+", " ", str(valor or "")).strip(" -|,.;")
    texto = re.sub(r"\b\d+([.,]\d+)?\b", "", texto)
    texto = re.sub(r"\b(kg|gr|g|l|ml|ud|uds|cs|c/s|pax|total)\b", "", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\s+", " ", texto).strip(" -|,.;")
    return texto[:60]


def _ingredientes_principales(item, limite=6):
    principales = []
    vistos = set()
    descartes = {
        "sal", "pimienta", "aceite", "agua", "harina", "azucar", "perejil",
        "ajo", "ajos", "laurel", "tomillo", "oregano", "pax total",
    }
    for ingrediente in item.get("ingredientes") or []:
        limpio = _ingrediente_limpio(ingrediente)
        clave = _normalizar(limpio)
        if not limpio or clave in descartes or len(clave) < 4 or clave in vistos:
            continue
        principales.append(limpio)
        vistos.add(clave)
        if len(principales) >= limite:
            break
    return principales


def _detectar_tecnicas_culinarias(item):
    texto = _texto_propuesta_memoria(item)
    tecnicas = []
    mapa = [
        ("horno", ["horno", "asar", "asado", "gratin"]),
        ("plancha", ["plancha", "marcar"]),
        ("guiso", ["guis", "estof", "brase", "fondo"]),
        ("frito", ["freir", "frito", "reboz", "enharin"]),
        ("vapor/coccion suave", ["vapor", "cocer", "hervir", "caldo"]),
        ("frio", ["frio", "ensalada", "marinar", "escabech"]),
        ("abatir/regenerar", ["abat", "regener", "vacio", "barqueta"]),
    ]
    for nombre, claves in mapa:
        if any(clave in texto for clave in claves):
            tecnicas.append(nombre)
    return tecnicas[:4] or ["definir tecnica"]


def _formato_servicio_sugerido(item, servicio, consulta):
    texto = _normalizar(f"{servicio} {consulta}")
    dificultad = _estimar_dificultad_culinaria(item)
    coste = _estimar_coste_culinario(item)
    if "buffet" in texto:
        return "Buffet: bandeja pequena, reposicion frecuente y salsa aparte para controlar merma."
    if "banquete" in texto or "evento" in texto:
        return "Banquete: mise en place cerrada, regeneracion controlada y emplatado repetible."
    if "carta" in texto or coste == "alto" or dificultad == "alta":
        return "Carta: racion individual, acabado al pase y guarnicion estable."
    if "menu" in texto:
        return "Menu diario: elaboracion sencilla, porcion clara y coste cerrado."
    return "Servicio flexible: validar si ira a carta, buffet o menu antes de escandallar."


def _plan_appcc_asesor(item, restricciones="", servicio=""):
    texto = _texto_propuesta_memoria(item)
    riesgos = []
    controles = []
    if any(p in texto for p in ["pollo", "ave", "carne", "cerdo", "ternera"]):
        riesgos.append("proteina carnica")
        controles.append("Controlar coccion completa, enfriado rapido si se produce antes y regeneracion segura.")
    if any(p in texto for p in ["pescado", "merluza", "bacalao", "atun", "salmon", "marisco", "gamba"]):
        riesgos.append("pescado/marisco")
        controles.append("Verificar origen, cadena de frio, posible anisakis y tiempo fuera de frio.")
    if any(p in texto for p in ["huevo", "mahonesa", "mayonesa", "crema", "nata"]):
        riesgos.append("huevo/lacteo o salsa sensible")
        controles.append("Mantener salsas frias, lote pequeno, fecha/hora de elaboracion y vida util corta.")
    if "buffet" in _normalizar(servicio):
        riesgos.append("exposicion en buffet")
        controles.append("Definir temperatura de exposicion, reposicion, retirada y registro por franja.")
    if restricciones:
        controles.append(f"Separar utiles y validar contaminacion cruzada por restricciones: {restricciones}.")
    if not riesgos:
        riesgos.append("riesgo general")
    if not controles:
        controles.append("Registrar lote, alergenos, temperatura si aplica y responsable de validacion.")
    return {"riesgos": riesgos[:4], "controles": controles[:5]}


def _alternativas_asesor(item, restricciones="", stock=""):
    texto = _texto_propuesta_memoria(item)
    restricciones_norm = _normalizar(restricciones)
    stock_tokens = _tokens_busqueda_biblioteca(stock)
    alternativas = []
    if "gluten" in restricciones_norm or any(p in texto for p in ["harina", "pan", "pasta", "reboz"]):
        alternativas.append("Version sin gluten: sustituir harina/pan por maicena, arroz, patata o pan certificado.")
    if "lactosa" in restricciones_norm or any(p in texto for p in ["nata", "queso", "mantequilla", "leche"]):
        alternativas.append("Version sin lactosa: usar aceite emulsionado, caldo reducido o lacteo certificado.")
    if "veg" in restricciones_norm:
        alternativas.append("Version vegetal: cambiar proteina por setas, legumbre o berenjena con fondo intenso.")
    if stock_tokens:
        base = ", ".join(sorted(stock_tokens)[:5])
        alternativas.append(f"Aprovechamiento de stock: priorizar {base} como guarnicion, crema, fondo o salsa.")
    if not alternativas:
        alternativas.append("Crear variante economica y variante premium antes de pasarla a carta definitiva.")
    return alternativas[:4]


def _borrador_receta_asesor(item, consulta, contexto, servicio, objetivo, restricciones, stock):
    principales = _ingredientes_principales(item)
    tecnicas = _detectar_tecnicas_culinarias(item)
    nombre_base = item.get("nombre") or "Propuesta"
    contexto_norm = _normalizar(f"{consulta} {contexto} {objetivo}")
    coletilla = ""
    if any(p in contexto_norm for p in ["termal", "balneario", "salud", "wellness"]):
        coletilla = " ligera"
    elif any(p in contexto_norm for p in ["monasterio", "monacal", "convento"]):
        coletilla = " de inspiracion monacal"
    elif any(p in contexto_norm for p in ["premium", "elegante", "carta"]):
        coletilla = " de carta"
    nombre = f"{nombre_base}{coletilla}"
    pasos = [
        "Definir racion y coste objetivo antes de producir.",
        "Preparar mise en place separando base, salsa y guarnicion.",
        f"Aplicar tecnica principal: {', '.join(tecnicas)}.",
        "Probar punto de sal, textura y presentacion con una racion testigo.",
        "Registrar alergenos, APPCC y vida util antes de incorporarla al menu.",
    ]
    if "sin frit" in _normalizar(restricciones + " " + consulta):
        pasos.insert(3, "Evitar fritura: terminar al horno, vapor, plancha o glaseado ligero.")
    return {
        "nombre": nombre,
        "ingredientes_base": principales or ["pendiente de definir"],
        "tecnicas": tecnicas,
        "formato": _formato_servicio_sugerido(item, servicio, consulta),
        "pasos": pasos,
        "alternativas": _alternativas_asesor(item, restricciones, stock),
        "appcc": _plan_appcc_asesor(item, restricciones, servicio),
    }


def _preguntas_asesor(consulta, contexto, servicio, objetivo):
    texto = _normalizar(f"{consulta} {contexto} {servicio} {objetivo}")
    preguntas = []
    if not any(p in texto for p in ["buffet", "carta", "menu", "banquete"]):
        preguntas.append("Esto va a carta, menu, buffet o evento?")
    if not any(p in texto for p in ["coste", "precio", "margen", "econom"]):
        preguntas.append("Que coste maximo por racion quieres permitir?")
    if not any(p in texto for p in ["pax", "racion", "personas"]):
        preguntas.append("Para cuantas raciones o pax hay que plantearlo?")
    if not any(p in texto for p in ["alergen", "sin gluten", "sin lactosa", "veg"]):
        preguntas.append("Hay restricciones de alergenos o dietas que deban condicionar la receta?")
    if not preguntas:
        preguntas.append("Quieres que la convierta en receta borrador lista para revisar e importar?")
    return preguntas[:4]


def _referencias_aplicables(datos, consulta, contexto="", servicio="", objetivo="", restricciones=""):
    referencias = datos.get("referencias") or []
    texto_consulta = _normalizar(f"{consulta} {contexto} {servicio} {objetivo} {restricciones}")
    tokens = set(_expandir_tokens_culinarios(_tokens_busqueda_biblioteca(consulta, contexto, servicio, objetivo, restricciones)))
    tipos_preferidos = set()
    if any(p in texto_consulta for p in ["appcc", "riesgo", "temperatura", "higiene", "buffet"]):
        tipos_preferidos.add("referencia_appcc")
    if any(p in texto_consulta for p in ["alergen", "gluten", "lactosa", "celiac", "intolerancia"]):
        tipos_preferidos.add("referencia_alergenos")
    if any(p in texto_consulta for p in ["nutricion", "salud", "saludable", "balneario", "termal", "senior"]):
        tipos_preferidos.add("referencia_nutricion")
    if any(p in texto_consulta for p in ["tecnica", "gelificante", "carta", "emplat", "buffet", "banquete"]):
        tipos_preferidos.add("referencia_tecnica")
    if any(p in texto_consulta for p in ["coste", "escandallo", "margen", "ingenieria", "menu"]):
        tipos_preferidos.add("referencia_gestion")
    if not tipos_preferidos:
        tipos_preferidos.update({"referencia_appcc", "referencia_alergenos", "referencia_tecnica"})

    resultados = []
    for ref in referencias:
        texto_ref = _normalizar(" ".join([ref.get("fuente", ""), ref.get("tipo", ""), ref.get("resumen", ""), ref.get("motivo", "")]))
        score = 20 if ref.get("tipo") in tipos_preferidos else 0
        score += sum(5 for token in tokens if token in texto_ref)
        if score <= 0:
            continue
        recomendacion = "Usar como apoyo de control y revision antes de publicar."
        if ref.get("tipo") == "referencia_appcc":
            recomendacion = "Aplicar sus criterios a higiene, temperaturas, trazabilidad y vida util."
        elif ref.get("tipo") == "referencia_alergenos":
            recomendacion = "Cruzar la receta con alergenos, gluten/lactosa y contaminacion cruzada."
        elif ref.get("tipo") == "referencia_nutricion":
            recomendacion = "Usar para orientar perfil saludable, racion y comunicacion nutricional."
        elif ref.get("tipo") == "referencia_tecnica":
            recomendacion = "Usar para mejorar tecnica, textura, mise en place y acabado."
        elif ref.get("tipo") == "referencia_gestion":
            recomendacion = "Usar para escandallo, margen, ingenieria de menu o decision de carta."
        item = dict(ref)
        item["score"] = score
        item["recomendacion"] = recomendacion
        resultados.append(item)
    resultados.sort(key=lambda r: (-r.get("score", 0), r.get("fuente", "")))
    return resultados[:5]


def _asesorar_culinaria(datos, consulta, contexto="", servicio="", objetivo="", restricciones="", stock=""):
    criterio = " ".join([objetivo, restricciones, stock, consulta])
    candidatos = _filtrar_memoria_culinaria(datos, consulta, contexto, servicio, criterio)
    if not candidatos:
        tokens = _tokens_busqueda_biblioteca(consulta, contexto, servicio, objetivo, restricciones, stock)
        candidatos = []
        for item in datos.get("propuestas") or []:
            texto = _texto_propuesta_memoria(item)
            if any(token in texto for token in tokens):
                candidatos.append(item)
        candidatos = candidatos[:40]
    if not candidatos:
        candidatos = sorted(datos.get("propuestas") or [], key=lambda p: -int(p.get("confianza") or 0))[:15]

    for item in candidatos:
        item["ranking"] = _puntuar_item_culinario(item, contexto, item.get("categoria", ""), servicio, objetivo, "", "", stock)
    candidatos.sort(key=lambda p: (-p.get("ranking", {}).get("total", 0), -int(p.get("confianza") or 0)))
    seleccionados = candidatos[:6]

    intencion = _detectar_intencion_asesor(consulta + " " + objetivo)
    diagnostico = {
        "general": "Se han buscado referencias privadas y se proponen caminos de cocina, no copias literales.",
        "mejora": "La consulta pide refinar o hacer mas presentable una idea.",
        "coste": "La consulta pide controlar coste, margen o escandallo.",
        "appcc": "La consulta tiene foco APPCC o riesgo operativo.",
        "alternativas": "La consulta pide variantes o sustituciones.",
        "servicio": "La consulta depende del tipo de servicio y formato de pase.",
    }.get(intencion, "Se han buscado referencias privadas y se proponen caminos de cocina.")

    soluciones = [
        _recomendaciones_para_item(item, consulta + " " + restricciones, contexto, servicio, objetivo)
        for item in seleccionados
    ]
    for solucion, item in zip(soluciones, seleccionados):
        solucion["borrador"] = _borrador_receta_asesor(item, consulta, contexto, servicio, objetivo, restricciones, stock)
        solucion["tecnicas"] = solucion["borrador"]["tecnicas"]
        solucion["formato_servicio"] = solucion["borrador"]["formato"]

    if seleccionados:
        receta_guia = _borrador_receta_asesor(seleccionados[0], consulta, contexto, servicio, objetivo, restricciones, stock)
    else:
        receta_guia = {
            "nombre": "Propuesta pendiente",
            "ingredientes_base": ["sin referencias suficientes"],
            "tecnicas": ["definir tecnica"],
            "formato": "Necesita mas contexto de servicio.",
            "pasos": ["Anadir mas recetas a la biblioteca o acotar mejor la consulta."],
            "alternativas": [],
            "appcc": {"riesgos": ["sin evaluar"], "controles": ["validar manualmente"]},
        }

    return {
        "consulta": consulta,
        "contexto": contexto,
        "servicio": servicio,
        "objetivo": objetivo,
        "restricciones": restricciones,
        "stock": stock,
        "intencion": intencion,
        "diagnostico": diagnostico,
        "soluciones": soluciones,
        "receta_guia": receta_guia,
        "referencias": _referencias_aplicables(datos, consulta, contexto, servicio, objetivo, restricciones),
        "preguntas": _preguntas_asesor(consulta, contexto, servicio, objetivo),
        "acciones": [
            "Elegir una propuesta y convertirla en receta borrador solo despues de revisar alergenos.",
            "Si va a buffet, hacer prueba de mantenimiento en caliente/frio y calcular merma.",
            "Si va a carta, definir emplatado, guarnicion y mise en place de pase.",
            "Contrastar coste con productos reales del centro antes de publicar.",
        ],
        "candidatos_revisados": len(candidatos),
    }


@recetas_bp.route('/biblioteca', methods=['GET', 'POST'])
@login_required
def biblioteca_recetas():
    _asegurar_biblioteca_recetas()
    datos = _cargar_biblioteca_recetas()

    if request.method == "POST":
        accion = request.form.get("accion") or ""
        if accion == "escanear":
            datos = _escanear_biblioteca_recetas()
            flash(
                f"Biblioteca revisada: {len(datos.get('propuestas', []))} propuestas y "
                f"{len(datos.get('errores', []))} avisos.",
                "success",
            )
            return redirect(url_for("recetas.biblioteca_recetas"))

        if accion == "importar":
            seleccionadas = set(request.form.getlist("propuesta_id"))
            propuestas = [p for p in datos.get("propuestas", []) if p.get("id") in seleccionadas]
            if not propuestas:
                flash("Selecciona alguna propuesta para copiarla a recetas.", "warning")
                return redirect(url_for("recetas.biblioteca_recetas"))
            conn = get_db()
            _asegurar_recetas_schema(conn)
            empresa_id = session.get("user_empresa_id", 1)
            centro_id = centro_activo_id()
            creadas = []
            avisos = []
            for propuesta in propuestas:
                ok, mensaje = _importar_propuesta_biblioteca(conn, empresa_id, centro_id, propuesta)
                if ok:
                    creadas.append(mensaje)
                else:
                    avisos.append(mensaje)
            conn.commit()
            conn.close()
            if creadas:
                flash(f"Recetas copiadas: {len(creadas)}.", "success")
            if avisos:
                flash("; ".join(avisos[:5]), "warning")
            return redirect(url_for("recetas.recetas"))

    archivos = []
    for raiz, _, nombres in os.walk(BIBLIOTECA_RECETAS_DIR):
        for nombre in nombres:
            ruta = os.path.join(raiz, nombre)
            archivos.append({
                "nombre": os.path.relpath(ruta, BIBLIOTECA_RECETAS_DIR),
                "tamano": os.path.getsize(ruta),
                "extension": os.path.splitext(nombre)[1].lower(),
            })
    archivos.sort(key=lambda item: item["nombre"].lower())
    consulta = request.args.get("consulta", "").strip()
    contexto = request.args.get("contexto", "").strip()
    servicio = request.args.get("servicio", "").strip()
    criterio = request.args.get("criterio", "").strip()
    propuestas_filtradas = _filtrar_memoria_culinaria(datos, consulta, contexto, servicio, criterio)
    estadisticas_memoria = _estadisticas_memoria_culinaria(datos, archivos)
    return render_template(
        "recetas/biblioteca.html",
        carpeta=BIBLIOTECA_RECETAS_DIR,
        archivos=archivos,
        datos=datos,
        consulta=consulta,
        contexto=contexto,
        servicio=servicio,
        criterio=criterio,
        propuestas_filtradas=propuestas_filtradas,
        estadisticas_memoria=estadisticas_memoria,
        puede_editar=usuario_tiene_permiso("produccion_editar"),
    )


@recetas_bp.route('/biblioteca/libros', methods=['GET', 'POST'])
@login_required
def analizador_libros():
    _asegurar_biblioteca_recetas()
    libros = _listar_libros_grandes()
    resultado = None
    resultado_lote = None
    libro_actual = None
    paginas_total = None

    source_id = request.values.get("source_id", "").strip()
    if source_id:
        libro_actual = _resolver_fuente_libro(source_id)
        if libro_actual:
            try:
                paginas_total = _contar_paginas_pdf(_preparar_pdf_libro(libro_actual))
            except Exception:
                paginas_total = None

    if request.method == "POST":
        source_id = request.form.get("source_id", "").strip()
        pagina_inicio = request.form.get("pagina_inicio", 1, type=int)
        usar_ocr = request.form.get("usar_ocr") == "1"
        limite_paginas = 8 if usar_ocr else 40
        paginas = max(1, min(request.form.get("paginas", 10, type=int), limite_paginas))
        etiqueta = request.form.get("etiqueta", "").strip()
        contexto = request.form.get("contexto", "").strip()
        accion = request.form.get("accion", "tramo")
        try:
            if accion == "lote_pendientes":
                limite_lote = request.form.get("limite_lote", 5, type=int)
                resultado_lote = _indexar_lote_libros_pendientes(limite_lote, etiqueta, contexto)
                flash(
                    f"Lote indexado: {resultado_lote['procesados']} libros, "
                    f"{resultado_lote['nuevas']} recetas nuevas.",
                    "success" if resultado_lote["nuevas"] else "warning",
                )
            elif accion == "completo":
                resultado = _indexar_libro_completo(source_id, etiqueta, contexto, usar_ocr)
            else:
                resultado = _indexar_libro_por_paginas(
                    source_id,
                    pagina_inicio,
                    pagina_inicio + paginas - 1,
                    etiqueta,
                    contexto,
                    usar_ocr,
                )
            libro_actual = resultado["libro"]
            paginas_total = resultado["total"]
            flash(
                f"Libro indexado: paginas {resultado['inicio']}-{resultado['fin']}. "
                f"Ideas nuevas: {resultado['nuevas']}.",
                "success",
            ) if resultado else None
        except Exception as exc:
            flash(f"No se pudo analizar el libro: {exc}", "danger")

    estado_indexacion = _estado_indexacion_libros()
    return render_template(
        "recetas/analizador_libros.html",
        libros=libros,
        libro_actual=libro_actual,
        paginas_total=paginas_total,
        resultado=resultado,
        resultado_lote=resultado_lote,
        estado_indexacion=estado_indexacion,
        ocr_disponible=_ocr_disponible(),
    )


@recetas_bp.route('/biblioteca/libros/recetas', methods=['GET', 'POST'])
@login_required
def recetas_libros():
    datos = _cargar_biblioteca_recetas()
    if request.method == "POST":
        accion = request.form.get("accion", "")
        propuesta_id = request.form.get("propuesta_id", "").strip()
        propuesta = next((p for p in datos.get("propuestas", []) if p.get("id") == propuesta_id), None)
        if not propuesta:
            flash("No he encontrado esa receta de libro.", "warning")
            return redirect(url_for("recetas.recetas_libros"))
        if accion == "copiar":
            conn = get_db()
            _asegurar_recetas_schema(conn)
            ok, mensaje = _importar_propuesta_biblioteca(
                conn,
                session.get("user_empresa_id", 1),
                centro_activo_id(),
                propuesta,
            )
            conn.commit()
            conn.close()
            flash(
                f"Receta borrador creada: {mensaje}" if ok else f"No se ha creado: {mensaje}",
                "success" if ok else "warning",
            )
            return redirect(url_for("recetas.recetas_libros", fuente=request.form.get("fuente_actual", "")))
        if accion == "asesorar":
            ingredientes_stock = [
                _ingrediente_limpio(ingrediente)
                for ingrediente in (propuesta.get("ingredientes") or [])[:6]
            ]
            ingredientes_stock = [i for i in ingredientes_stock if i][:6]
            return redirect(url_for(
                "recetas.asesor_culinario",
                consulta=propuesta.get("nombre", ""),
                contexto="receta de libro",
                servicio="",
                objetivo="mejorar appcc coste presentacion",
                restricciones="",
                stock=", ".join(ingredientes_stock),
            ))

    consulta = request.args.get("consulta", "").strip()
    fuente = request.args.get("fuente", "").strip()
    pase = request.args.get("pase", "").strip()
    coste = request.args.get("coste", "").strip()
    dificultad = request.args.get("dificultad", "").strip()
    riesgo = request.args.get("riesgo", "").strip()
    propuestas = [
        p for p in datos.get("propuestas", [])
        if p.get("tipo_fuente") == "libro_grande"
    ]
    if consulta:
        tokens = _tokens_busqueda_biblioteca(consulta)
        propuestas = [
            p for p in propuestas
            if any(token in _texto_propuesta_memoria(p) for token in tokens)
        ]
    if fuente:
        fuente_norm = _normalizar(fuente)
        propuestas = [
            p for p in propuestas
            if fuente_norm in _normalizar(p.get("fuente", ""))
        ]
    propuestas = [_enriquecer_receta_libro(p) for p in propuestas]
    if pase:
        propuestas = [p for p in propuestas if p.get("pase") == pase]
    if coste:
        propuestas = [p for p in propuestas if p.get("coste_estimado") == coste]
    if dificultad:
        propuestas = [p for p in propuestas if p.get("dificultad") == dificultad]
    if riesgo:
        riesgo_norm = _normalizar(riesgo)
        propuestas = [p for p in propuestas if any(riesgo_norm in _normalizar(r) for r in p.get("riesgos", []))]
    propuestas.sort(key=lambda p: (p.get("fuente", ""), str(p.get("paginas") or "").zfill(4), p.get("nombre", "")))
    fuentes = sorted({p.get("fuente", "") for p in datos.get("propuestas", []) if p.get("tipo_fuente") == "libro_grande"})
    return render_template(
        "recetas/recetas_libros.html",
        propuestas=propuestas,
        fuentes=fuentes,
        consulta=consulta,
        fuente=fuente,
        pase=pase,
        coste=coste,
        dificultad=dificultad,
        riesgo=riesgo,
    )


@recetas_bp.route('/biblioteca/generador', methods=['GET', 'POST'])
@login_required
def generador_culinario():
    datos = _cargar_biblioteca_recetas()
    propuesta = None
    mejora_receta = None
    recetas_selector = []
    refinado = request.values.get("refinado", "").strip()
    formulario = {
        "modo": request.values.get("modo", "menu").strip() or "menu",
        "receta_id": request.values.get("receta_id", "").strip(),
        "contexto": request.values.get("contexto", "").strip(),
        "concepto": request.values.get("concepto", "").strip(),
        "servicio": request.values.get("servicio", "").strip(),
        "temporada": request.values.get("temporada", "").strip(),
        "objetivo": request.values.get("objetivo", "").strip(),
        "restricciones": request.values.get("restricciones", "").strip(),
        "cliente": request.values.get("cliente", "").strip(),
        "precio_objetivo": request.values.get("precio_objetivo", "").strip(),
        "stock": request.values.get("stock", "").strip(),
        "pax": request.values.get("pax", "50").strip(),
    }
    if refinado == "economico":
        formulario["objetivo"] = " ".join([formulario["objetivo"], "mas economico rentable"]).strip()
    elif refinado == "elegante":
        formulario["objetivo"] = " ".join([formulario["objetivo"], "mas elegante presentable"]).strip()
    elif refinado == "buffet":
        formulario["modo"] = "buffet"
        formulario["servicio"] = "buffet " + formulario["servicio"]
    elif refinado == "carta":
        formulario["modo"] = "carta"
        formulario["servicio"] = "carta " + formulario["servicio"]
    conn = get_db()
    _asegurar_recetas_schema(conn)
    empresa_id = session.get("user_empresa_id", 1)
    centro_id = centro_activo_id()
    recetas_selector = _recetas_para_selector_culinario(conn, empresa_id, centro_id)
    if request.method == "POST":
        if formulario["modo"] == "mejorar_receta":
            receta_id = int(formulario["receta_id"] or 0) if formulario["receta_id"].isdigit() else 0
            receta = _receta_actual_detalle(conn, empresa_id, receta_id, centro_id)
            if receta:
                mejora_receta = {
                    "receta": receta,
                    "comparaciones": _mejoras_receta_existente(
                        datos,
                        receta,
                        formulario["contexto"],
                        formulario["servicio"],
                        formulario["objetivo"],
                        formulario["restricciones"],
                        formulario["stock"],
                    ),
                }
            else:
                flash("Elige una receta existente para poder compararla.", "warning")
        else:
            propuesta = _generar_propuesta_culinaria(
                datos,
                formulario["contexto"],
                formulario["concepto"],
                formulario["servicio"],
                formulario["temporada"],
                formulario["objetivo"],
                formulario["restricciones"],
                formulario["pax"],
                formulario["modo"],
                formulario["cliente"],
                formulario["precio_objetivo"],
                formulario["stock"],
            )
    conn.close()
    return render_template(
        "recetas/generador_culinario.html",
        propuesta=propuesta,
        mejora_receta=mejora_receta,
        recetas_selector=recetas_selector,
        formulario=formulario,
        memoria_total=len(_propuestas_memoria_validas(datos)),
        memoria_actualizada=datos.get("actualizado"),
    )


@recetas_bp.route('/biblioteca/asesor', methods=['GET', 'POST'])
@login_required
def asesor_culinario():
    datos = _cargar_biblioteca_recetas()
    formulario = {
        "consulta": request.values.get("consulta", "").strip(),
        "contexto": request.values.get("contexto", "").strip(),
        "servicio": request.values.get("servicio", "").strip(),
        "objetivo": request.values.get("objetivo", "").strip(),
        "restricciones": request.values.get("restricciones", "").strip(),
        "stock": request.values.get("stock", "").strip(),
    }
    if request.method == "POST" and request.form.get("accion") == "importar_asesor":
        propuesta_id = request.form.get("propuesta_id", "").strip()
        propuesta = next((p for p in datos.get("propuestas") or [] if p.get("id") == propuesta_id), None)
        if not propuesta:
            flash("No he encontrado esa propuesta en la biblioteca privada.", "warning")
            return redirect(url_for("recetas.asesor_culinario"))
        conn = get_db()
        _asegurar_recetas_schema(conn)
        ok, mensaje = _importar_propuesta_biblioteca(
            conn,
            session.get("user_empresa_id", 1),
            centro_activo_id(),
            propuesta,
        )
        conn.commit()
        flash(
            f"Receta borrador creada: {mensaje}" if ok else f"No se ha creado la receta: {mensaje}",
            "success" if ok else "warning",
        )
        return redirect(url_for("recetas.recetas"))

    resultado = None
    if request.method == "POST" and formulario["consulta"]:
        resultado = _asesorar_culinaria(
            datos,
            formulario["consulta"],
            formulario["contexto"],
            formulario["servicio"],
            formulario["objetivo"],
            formulario["restricciones"],
            formulario["stock"],
        )
    return render_template(
        "recetas/asesor_culinario.html",
        formulario=formulario,
        resultado=resultado,
        memoria_total=len(datos.get("propuestas") or []),
        memoria_actualizada=datos.get("actualizado"),
    )


@recetas_bp.route('/recetas')
@recetas_bp.route('/')
@login_required
def recetas():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_recetas_schema(conn)
    conn.commit()
    centro_id = centro_activo_id()

    categoria = request.args.get('categoria', '').strip()
    buscar = request.args.get('buscar', '').strip()
    estado = request.args.get('estado', '').strip()
    pagina = request.args.get('pagina', 1, type=int)
    por_pagina = 25
    offset = (pagina - 1) * por_pagina

    where = ["r.empresa_id = ?"]
    params = [empresa_id]
    if centro_id:
        where.append("r.centro_id = ?")
        params.append(centro_id)

    if categoria:
        if categoria == 'sin_categoria':
            where.append("TRIM(COALESCE(r.categoria, '')) = ''")
        else:
            where.append("LOWER(COALESCE(r.categoria, '')) = ?")
            params.append(categoria.lower())

    if buscar:
        where.append("""
            (
                LOWER(COALESCE(r.nombre, '')) LIKE ?
                OR LOWER(COALESCE(r.descripcion, '')) LIKE ?
                OR LOWER(COALESCE(r.categoria, '')) LIKE ?
            )
        """)
        buscar_like = f"%{buscar.lower()}%"
        params.extend([buscar_like, buscar_like, buscar_like])

    if estado == 'sin_ingredientes':
        where.append("NOT EXISTS (SELECT 1 FROM recetas_ingredientes ri2 WHERE ri2.receta_id = r.id)")
    elif estado == 'con_ingredientes':
        where.append("EXISTS (SELECT 1 FROM recetas_ingredientes ri2 WHERE ri2.receta_id = r.id)")
    elif estado == 'sin_alergenos':
        where.append("TRIM(COALESCE(r.alergenos, '')) = ''")

    where_sql = " AND ".join(where)

    total = conn.execute(f"""
        SELECT COUNT(*) AS total
        FROM recetas r
        WHERE {where_sql}
    """, params).fetchone()["total"]

    recetas = conn.execute(f"""
        SELECT
            r.*,
            COUNT(DISTINCT ri.id) AS num_ingredientes,
            COALESCE(SUM(ri.cantidad * COALESCE(p.precio, 0)), 0) AS coste_total
        FROM recetas r
        LEFT JOIN recetas_ingredientes ri ON r.id = ri.receta_id
        LEFT JOIN productos p ON ri.producto_id = p.id
        WHERE {where_sql}
        GROUP BY r.id
        ORDER BY r.nombre
        LIMIT ? OFFSET ?
    """, params + [por_pagina, offset]).fetchall()

    estadisticas_params = [empresa_id]
    estadisticas_centro_sql = ""
    if centro_id:
        estadisticas_centro_sql = " AND r.centro_id = ?"
        estadisticas_params.append(centro_id)
    estadisticas = conn.execute(f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN NOT EXISTS (
                SELECT 1 FROM recetas_ingredientes ri WHERE ri.receta_id = r.id
            ) THEN 1 ELSE 0 END) AS sin_ingredientes,
            SUM(CASE WHEN TRIM(COALESCE(r.alergenos, '')) = '' THEN 1 ELSE 0 END) AS sin_alergenos,
            SUM(CASE WHEN COALESCE(r.raciones_base, 0) > 0 THEN 1 ELSE 0 END) AS con_raciones
        FROM recetas r
        WHERE r.empresa_id = ?
          {estadisticas_centro_sql}
    """, estadisticas_params).fetchone()

    categorias_params = [empresa_id]
    categorias_centro_sql = ""
    if centro_id:
        categorias_centro_sql = " AND centro_id = ?"
        categorias_params.append(centro_id)
    categorias = conn.execute(f"""
        SELECT COALESCE(NULLIF(TRIM(categoria), ''), 'sin_categoria') AS categoria,
               COUNT(*) AS total
        FROM recetas
        WHERE empresa_id = ?
          {categorias_centro_sql}
        GROUP BY COALESCE(NULLIF(TRIM(categoria), ''), 'sin_categoria')
        ORDER BY categoria
    """, categorias_params).fetchall()

    conn.close()

    total_paginas = (total + por_pagina - 1) // por_pagina

    return render_template(
        'recetas/recetas.html',
        recetas=recetas,
        pagina_actual=pagina,
        total_paginas=total_paginas,
        total_registros=total,
        estadisticas=estadisticas,
        categorias=categorias,
        estado_actual=estado,
        categoria_actual=categoria,
        buscar_actual=buscar
    )


@recetas_bp.route('/recalcular-alergenos', methods=['POST'])
@login_required
def recalcular_alergenos_recetas():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    _asegurar_recetas_schema(conn)

    where = ["r.empresa_id = ?", "TRIM(COALESCE(r.alergenos, '')) = ''"]
    params = [empresa_id]
    if centro_id:
        where.append("r.centro_id = ?")
        params.append(centro_id)

    recetas_rows = conn.execute(f"""
        SELECT r.id, r.nombre, r.descripcion, r.categoria
        FROM recetas r
        WHERE {" AND ".join(where)}
        ORDER BY r.nombre
    """, params).fetchall()

    actualizadas = 0
    sin_deteccion = 0
    for receta in recetas_rows:
        ingredientes = conn.execute("""
            SELECT p.nombre, p.alergenos
            FROM recetas_ingredientes ri
            JOIN productos p ON p.id = ri.producto_id
            WHERE ri.receta_id = ?
        """, (receta["id"],)).fetchall()

        textos = [receta["nombre"], receta["descripcion"], receta["categoria"]]
        alergenos_productos = []
        for ingrediente in ingredientes:
            textos.append(ingrediente["nombre"])
            if ingrediente["alergenos"]:
                alergenos_productos.append(ingrediente["alergenos"])

        detectados = detectar_alergenos(*textos)
        alergenos = unir_alergenos([*alergenos_productos, ", ".join(detectados)])
        if not alergenos:
            sin_deteccion += 1
            continue

        conn.execute(
            "UPDATE recetas SET alergenos = ? WHERE id = ? AND empresa_id = ?",
            (alergenos, receta["id"], empresa_id),
        )
        actualizadas += 1

    conn.commit()
    conn.close()
    flash(
        f"Alergenos inferidos en {actualizadas} recetas. "
        f"{sin_deteccion} quedaron pendientes de revision manual.",
        "success" if actualizadas else "warning",
    )
    return redirect(url_for('recetas.recetas', estado='sin_alergenos'))


@recetas_bp.route('/fuentes-externas', methods=['GET', 'POST'])
@login_required
def fuentes_externas_recetas():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    _asegurar_recetas_schema(conn)
    mensaje_fuente = ""
    estado_filtro = request.args.get("estado", "pendiente").strip() or "pendiente"
    confianza_filtro = request.args.get("confianza", "").strip()

    if request.method == "POST":
        accion = request.form.get("accion", "")
        if accion == "generar_internas":
            resultado = _crear_propuestas_internas_alergenos(conn, empresa_id, centro_id)
            conn.commit()
            conn.close()
            flash(
                f"Revision interna preparada: {resultado['creadas']} propuestas nuevas, "
                f"{resultado['sin_deteccion']} sin deteccion clara de {resultado['revisadas']} recetas revisadas.",
                "success" if resultado["creadas"] else "warning",
            )
            return redirect(url_for("recetas.fuentes_externas_recetas"))

        if accion == "buscar_pendientes_auto":
            limite = max(1, min(request.form.get("limite_recetas", 10, type=int), 30))
            usar_web = request.form.get("usar_web") == "1"
            resultado = _crear_propuestas_busqueda_automatica_recetas(
                conn,
                empresa_id,
                centro_id,
                limite_recetas=limite,
                usar_web=usar_web,
            )
            conn.commit()
            conn.close()
            flash(
                f"Busqueda automatica: {resultado['revisadas']} recetas revisadas, "
                f"{resultado['biblioteca']} propuestas de biblioteca, "
                f"{resultado['web']} propuestas web, "
                f"{resultado['sin_resultado']} sin resultado claro.",
                "success" if (resultado["biblioteca"] or resultado["web"]) else "warning",
            )
            return redirect(url_for("recetas.fuentes_externas_recetas"))

        if accion == "analizar_url":
            url = (request.form.get("url") or "").strip()
            texto_manual = (request.form.get("texto_manual") or "").strip()
            try:
                if url:
                    html = _descargar_fuente_receta(url)
                    fuente = _extraer_receta_desde_html(html, url)
                    fuente["url"] = url
                elif texto_manual:
                    fuente = {
                        "titulo": (request.form.get("titulo_manual") or "Fuente externa pegada").strip(),
                        "ingredientes": [line.strip() for line in texto_manual.splitlines() if line.strip()],
                        "elaboracion": texto_manual,
                        "texto": texto_manual,
                        "dominio": "texto-manual",
                        "url": f"manual:{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    }
                else:
                    raise ValueError("Pega una URL o texto de ingredientes.")
                resultado = _crear_propuestas_fuente_recetas(conn, empresa_id, centro_id, fuente)
                conn.commit()
                if resultado["creadas"]:
                    flash(
                        f"Fuente analizada. Creadas {resultado['creadas']} propuestas de alergenos "
                        f"({resultado['detectados']}).",
                        "success",
                    )
                else:
                    flash(resultado["mensaje"] or "No he encontrado recetas pendientes que encajen con esa fuente.", "warning")
            except ValueError as exc:
                conn.rollback()
                flash(str(exc), "warning")
            except Exception as exc:
                conn.rollback()
                flash(f"No he podido analizar la fuente externa: {exc}", "danger")
            conn.close()
            return redirect(url_for("recetas.fuentes_externas_recetas"))

        if accion == "aplicar":
            seleccionadas = request.form.getlist("propuesta_id")
            aplicadas = 0
            for propuesta_id in seleccionadas:
                propuesta = conn.execute("""
                    SELECT *
                    FROM recetas_fuentes_propuestas
                    WHERE id = ? AND empresa_id = ? AND estado = 'pendiente'
                """, (propuesta_id, empresa_id)).fetchone()
                if not propuesta:
                    continue
                receta = conn.execute("""
                    SELECT id, alergenos, centro_id
                    FROM recetas
                    WHERE id = ? AND empresa_id = ?
                """, (propuesta["receta_id"], empresa_id)).fetchone()
                if not receta:
                    continue
                alergenos_finales = unir_alergenos([receta["alergenos"], propuesta["alergenos_propuestos"]])
                resultado_ingredientes = _aplicar_ingredientes_fuente_a_receta(
                    conn,
                    empresa_id,
                    receta,
                    propuesta,
                )
                notas = propuesta["notas"] or ""
                if resultado_ingredientes["creados"]:
                    notas = (
                        f"{notas} Ingredientes aplicados desde fuente: "
                        f"{resultado_ingredientes['creados']} creados, "
                        f"{resultado_ingredientes['omitidos']} omitidos por lectura no clara."
                    ).strip()
                conn.execute(
                    "UPDATE recetas SET alergenos = ? WHERE id = ? AND empresa_id = ?",
                    (alergenos_finales, receta["id"], empresa_id),
                )
                conn.execute("""
                    UPDATE recetas_fuentes_propuestas
                    SET estado = 'aplicada', aplicado_en = CURRENT_TIMESTAMP, notas = ?
                    WHERE id = ? AND empresa_id = ?
                """, (notas, propuesta["id"], empresa_id))
                aplicadas += 1
            conn.commit()
            conn.close()
            flash(f"Aplicadas {aplicadas} propuestas a recetas.", "success" if aplicadas else "warning")
            return redirect(url_for("recetas.fuentes_externas_recetas"))

        if accion == "descartar":
            seleccionadas = request.form.getlist("propuesta_id")
            if seleccionadas:
                marks = ",".join("?" for _ in seleccionadas)
                conn.execute(
                    f"""
                    UPDATE recetas_fuentes_propuestas
                    SET estado = 'descartada'
                    WHERE empresa_id = ? AND estado = 'pendiente' AND id IN ({marks})
                    """,
                    [empresa_id] + seleccionadas,
                )
                conn.commit()
            conn.close()
            flash(f"Descartadas {len(seleccionadas)} propuestas.", "info")
            return redirect(url_for("recetas.fuentes_externas_recetas"))

    estado_sql = ""
    estado_params = []
    if estado_filtro != "todos":
        estado_sql = " AND p.estado = ?"
        estado_params.append(estado_filtro)
    confianza_sql = ""
    confianza_params = []
    if confianza_filtro == "alta":
        confianza_sql = " AND COALESCE(p.confianza, 0) >= 75"
    elif confianza_filtro == "revision":
        confianza_sql = " AND COALESCE(p.confianza, 0) < 75"

    propuestas_rows = conn.execute(f"""
        SELECT p.*, r.nombre AS receta_nombre_actual, r.categoria AS receta_categoria
        FROM recetas_fuentes_propuestas p
        LEFT JOIN recetas r ON r.id = p.receta_id
        WHERE p.empresa_id = ?
          AND (? IS NULL OR p.centro_id = ? OR p.centro_id IS NULL)
          {estado_sql}
          {confianza_sql}
        ORDER BY CASE p.estado WHEN 'pendiente' THEN 0 WHEN 'aplicada' THEN 1 ELSE 2 END,
                 p.creado_en DESC,
                 p.confianza DESC
        LIMIT 120
    """, [empresa_id, centro_id, centro_id] + estado_params + confianza_params).fetchall()
    propuestas = []
    for propuesta in propuestas_rows:
        item = dict(propuesta)
        preview = _previsualizar_ingredientes_fuente(item.get("ingredientes_fuente"))
        item["ingredientes_total"] = preview["total"]
        item["ingredientes_creables"] = preview["creables"]
        item["ingredientes_omitidas"] = preview["omitidas"]
        item["ingredientes_creables_detalle"] = preview["creables_detalle"]
        item["ingredientes_omitidas_detalle"] = preview["omitidas_detalle"]
        propuestas.append(item)
    resumen = conn.execute("""
        SELECT
            SUM(CASE WHEN estado = 'pendiente' THEN 1 ELSE 0 END) AS pendientes,
            SUM(CASE WHEN estado = 'aplicada' THEN 1 ELSE 0 END) AS aplicadas,
            SUM(CASE WHEN estado = 'descartada' THEN 1 ELSE 0 END) AS descartadas,
            COUNT(*) AS total
        FROM recetas_fuentes_propuestas
        WHERE empresa_id = ?
          AND (? IS NULL OR centro_id = ? OR centro_id IS NULL)
    """, (empresa_id, centro_id, centro_id)).fetchone()
    sin_alergenos = conn.execute("""
        SELECT COUNT(*) AS total
        FROM recetas
        WHERE empresa_id = ?
          AND TRIM(COALESCE(alergenos, '')) = ''
          AND (? IS NULL OR centro_id = ? OR centro_id IS NULL)
    """, (empresa_id, centro_id, centro_id)).fetchone()["total"]
    diagnostico = conn.execute("""
        SELECT
            COUNT(*) AS total_recetas,
            SUM(CASE WHEN NOT EXISTS (
                SELECT 1 FROM recetas_ingredientes ri WHERE ri.receta_id = r.id
            ) THEN 1 ELSE 0 END) AS sin_ingredientes,
            SUM(CASE WHEN TRIM(COALESCE(r.alergenos, '')) = '' THEN 1 ELSE 0 END) AS sin_alergenos,
            SUM(CASE WHEN NOT EXISTS (
                SELECT 1 FROM recetas_ingredientes ri WHERE ri.receta_id = r.id
            ) AND TRIM(COALESCE(r.alergenos, '')) = '' THEN 1 ELSE 0 END) AS vacias_criticas,
            SUM(CASE WHEN EXISTS (
                SELECT 1
                FROM lineas_produccion lp
                JOIN partes_produccion pp ON pp.id = lp.parte_id
                WHERE lp.receta_id = r.id AND pp.empresa_id = r.empresa_id
            ) AND NOT EXISTS (
                SELECT 1 FROM recetas_ingredientes ri WHERE ri.receta_id = r.id
            ) THEN 1 ELSE 0 END) AS produccion_sin_ingredientes,
            SUM(CASE WHEN EXISTS (
                SELECT 1
                FROM lineas_produccion lp
                JOIN partes_produccion pp ON pp.id = lp.parte_id
                WHERE lp.receta_id = r.id AND pp.empresa_id = r.empresa_id
            ) AND TRIM(COALESCE(r.alergenos, '')) = '' THEN 1 ELSE 0 END) AS produccion_sin_alergenos,
            SUM(CASE WHEN EXISTS (
                SELECT 1
                FROM menu_platos mp
                JOIN menu_subdepartamentos ms ON ms.id = mp.menu_subdepartamento_id
                JOIN menus_diarios md ON md.id = ms.menu_diario_id
                WHERE mp.receta_id = r.id AND md.empresa_id = r.empresa_id
            ) AND NOT EXISTS (
                SELECT 1 FROM recetas_ingredientes ri WHERE ri.receta_id = r.id
            ) THEN 1 ELSE 0 END) AS menu_sin_ingredientes,
            SUM(CASE WHEN EXISTS (
                SELECT 1
                FROM menu_platos mp
                JOIN menu_subdepartamentos ms ON ms.id = mp.menu_subdepartamento_id
                JOIN menus_diarios md ON md.id = ms.menu_diario_id
                WHERE mp.receta_id = r.id AND md.empresa_id = r.empresa_id
            ) AND TRIM(COALESCE(r.alergenos, '')) = '' THEN 1 ELSE 0 END) AS menu_sin_alergenos
        FROM recetas r
        WHERE r.empresa_id = ?
          AND (? IS NULL OR r.centro_id = ? OR r.centro_id IS NULL)
    """, (empresa_id, centro_id, centro_id)).fetchone()
    recetas_prioritarias = conn.execute("""
        SELECT
            r.id,
            r.nombre,
            COALESCE(r.categoria, 'Sin categoria') AS categoria,
            COALESCE(r.alergenos, '') AS alergenos,
            (SELECT COUNT(*) FROM recetas_ingredientes ri WHERE ri.receta_id = r.id) AS ingredientes,
            (SELECT COUNT(*)
             FROM lineas_produccion lp
             JOIN partes_produccion pp ON pp.id = lp.parte_id
             WHERE lp.receta_id = r.id AND pp.empresa_id = r.empresa_id
               AND (? IS NULL OR pp.centro_id = ?)) AS usos_produccion,
            (SELECT COUNT(*)
             FROM menu_platos mp
             JOIN menu_subdepartamentos ms ON ms.id = mp.menu_subdepartamento_id
             JOIN menus_diarios md ON md.id = ms.menu_diario_id
             WHERE mp.receta_id = r.id AND md.empresa_id = r.empresa_id
               AND (? IS NULL OR md.centro_id = ?)) AS usos_menu
        FROM recetas r
        WHERE r.empresa_id = ?
          AND (? IS NULL OR r.centro_id = ? OR r.centro_id IS NULL)
          AND (
              NOT EXISTS (SELECT 1 FROM recetas_ingredientes ri WHERE ri.receta_id = r.id)
              OR TRIM(COALESCE(r.alergenos, '')) = ''
          )
        ORDER BY
            (usos_produccion + usos_menu) DESC,
            CASE WHEN ingredientes = 0 THEN 0 ELSE 1 END,
            LOWER(r.nombre)
        LIMIT 12
    """, (centro_id, centro_id, centro_id, centro_id, empresa_id, centro_id, centro_id)).fetchall()
    conn.close()
    return render_template(
        "recetas/fuentes_externas.html",
        propuestas=propuestas,
        resumen=resumen,
        sin_alergenos=sin_alergenos,
        diagnostico=diagnostico,
        recetas_prioritarias=recetas_prioritarias,
        mensaje_fuente=mensaje_fuente,
        estado_filtro=estado_filtro,
        confianza_filtro=confianza_filtro,
        puede_editar=usuario_tiene_permiso("produccion_editar"),
    )


@recetas_bp.route('/rellenar-pruebas', methods=['GET', 'POST'])
@login_required
def rellenar_recetas_pruebas():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _asegurar_recetas_schema(conn)
    conn.commit()

    plan = _plan_relleno_recetas(conn, empresa_id)

    if request.method == 'POST':
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE recetas
            SET raciones_base = 4
            WHERE empresa_id = ?
              AND (raciones_base IS NULL OR raciones_base <= 0)
        """, (empresa_id,))
        raciones_actualizadas = cursor.rowcount if cursor.rowcount is not None else 0

        for item in plan['actualizar_cantidades']:
            cursor.execute("""
                UPDATE recetas_ingredientes
                SET cantidad = ?
                WHERE id = ?
                  AND (cantidad IS NULL OR cantidad <= 0)
            """, (item['cantidad'], item['ingrediente_id']))

        for item in plan['crear_ingredientes']:
            existe = cursor.execute("""
                SELECT 1
                FROM recetas_ingredientes
                WHERE receta_id = ? AND producto_id = ?
                LIMIT 1
            """, (item['receta_id'], item['producto_id'])).fetchone()
            if existe:
                continue
            cursor.execute("""
                INSERT INTO recetas_ingredientes (receta_id, producto_id, cantidad)
                VALUES (?, ?, ?)
            """, (item['receta_id'], item['producto_id'], item['cantidad']))

        conn.commit()
        conn.close()
        flash(
            f"Recetas preparadas para pruebas: {len(plan['actualizar_cantidades'])} cantidades rellenadas, "
            f"{len(plan['crear_ingredientes'])} ingredientes creados y {raciones_actualizadas} recetas a 4 raciones.",
            'success'
        )
        if plan['recetas_sin_sugerencia']:
            flash(f"{len(plan['recetas_sin_sugerencia'])} recetas siguen sin sugerencia automatica.", 'warning')
        return redirect(url_for('recetas.recetas'))

    resumen = {
        'cantidades': len(plan['actualizar_cantidades']),
        'ingredientes': len(plan['crear_ingredientes']),
        'recetas_afectadas': len({
            item['receta_id'] for item in plan['actualizar_cantidades']
        } | {
            item['receta_id'] for item in plan['crear_ingredientes']
        }),
        'sin_sugerencia': len(plan['recetas_sin_sugerencia']),
    }
    conn.close()

    return render_template('recetas/rellenar_pruebas.html', plan=plan, resumen=resumen)


@recetas_bp.route('/analizar-mise-en-place', methods=['GET', 'POST'])
@login_required
def analizar_mise_en_place():
    empresa_id = session.get('user_empresa_id', 1)
    ruta_default = r"c:\Users\jmpep\Downloads\HOJAS DE MICE EMPLACE LA CALDERA.pdf"
    analisis = _cargar_analisis_mise()

    if request.method == "POST":
        accion = request.form.get("accion") or "analizar"
        if accion == "importar":
            base = _cargar_analisis_mise()
            analisis = _analisis_mise_desde_revision(request.form, base) if request.form.get("revision_mise") == "1" else base
            if not analisis:
                flash("Primero analiza un PDF.", "warning")
                return redirect(url_for("recetas.analizar_mise_en_place"))
            conn = get_db()
            _asegurar_recetas_schema(conn)
            resumen = _importar_recetas_mise_revisadas(conn, empresa_id, analisis)
            conn.commit()
            conn.close()
            _guardar_analisis_mise(analisis)
            flash(
                f"Platos importados. Recetas nuevas: {resumen['recetas_creadas']}. "
                f"Actualizadas: {resumen['recetas_actualizadas']}. Productos nuevos: {resumen['productos_creados']}. "
                f"Ingredientes enlazados: {resumen['ingredientes_enlazados']}.",
                "success",
            )
            return redirect(url_for("recetas.recetas", estado="con_ingredientes"))

        if accion == "crear_menu":
            base = _cargar_analisis_mise()
            analisis = _analisis_mise_desde_revision(request.form, base) if request.form.get("revision_mise") == "1" else base
            if not analisis:
                flash("Primero analiza un PDF.", "warning")
                return redirect(url_for("recetas.analizar_mise_en_place"))
            if not analisis.get("hojas"):
                flash("No hay platos seleccionados para subir.", "warning")
                return redirect(url_for("recetas.analizar_mise_en_place"))
            punto_venta_id = request.form.get("punto_venta_id") or ""
            punto_id = int(punto_venta_id) if punto_venta_id.isdigit() else None
            nombre_menu = request.form.get("nombre_menu") or "Restaurante la Caldera"
            conn = get_db()
            _asegurar_recetas_schema(conn)
            resumen = _crear_menu_completo_mise(conn, empresa_id, analisis, nombre_menu, punto_id)
            if punto_id:
                try:
                    menus_routes._publicar_menu_fijo_en_tpv(conn, empresa_id, resumen["menu_id"], punto_id, 0)
                    resumen["tpv_publicado"] = True
                except Exception:
                    resumen["tpv_publicado"] = False
            conn.commit()
            conn.close()
            _guardar_analisis_mise(analisis)
            flash(
                f"Menu completo creado/actualizado. Secciones nuevas: {resumen['secciones']}. "
                f"Recetas detectadas: {resumen['recetas_detectadas']}. Recetas nuevas: {resumen['recetas_creadas']}. Productos nuevos: {resumen['productos_creados']}. "
                f"Ingredientes enlazados: {resumen['ingredientes_enlazados']}. Platos enlazados: {resumen['platos_enlazados']}.",
                "success",
            )
            return redirect(url_for("menus.ver_menu_fijo", menu_id=resumen["menu_id"]))

        ruta_pdf = (request.form.get("ruta_pdf") or "").strip()
        archivo = request.files.get("pdf")
        if archivo and archivo.filename:
            carpeta = os.path.join(os.getcwd(), "instance", "uploads")
            os.makedirs(carpeta, exist_ok=True)
            nombre = secure_filename(archivo.filename) or "mise_en_place.pdf"
            ruta_pdf = os.path.join(carpeta, nombre)
            archivo.save(ruta_pdf)
        if not ruta_pdf or not os.path.exists(ruta_pdf):
            flash("Indica una ruta valida o sube el PDF.", "warning")
            return redirect(url_for("recetas.analizar_mise_en_place"))
        procesador = ProcesadorPDF()
        analisis = procesador.analizar_mise_en_place(ruta_pdf)
        _guardar_analisis_mise(analisis)
        if not analisis.get("hojas"):
            flash("No se detectaron hojas. Revisa que Tesseract/OCR este disponible.", "warning")
        else:
            flash(f"Analisis terminado. Hojas detectadas: {len(analisis['hojas'])}.", "success")

    conn = get_db()
    centro_id = centro_activo_id()
    pv_centro_sql = " AND centro_id = ?" if centro_id else ""
    pv_params = [empresa_id] + ([centro_id] if centro_id else [])
    puntos_venta = conn.execute(
        f"SELECT id, nombre FROM puntos_venta WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 {pv_centro_sql} ORDER BY nombre",
        pv_params,
    ).fetchall()
    conn.close()
    return render_template(
        "recetas/analizar_mise_en_place.html",
        analisis=analisis,
        puntos_venta=puntos_venta,
        ruta_default=ruta_default if os.path.exists(ruta_default) else "",
    )

@recetas_bp.route('/nueva', methods=['GET', 'POST'])
@login_required
def nueva_receta():
    conn = get_db()
    _asegurar_recetas_schema(conn)
    conn.commit()
    conn.close()

    if request.method == "POST":
        nombre = request.form["nombre"]
        descripcion = request.form.get("descripcion", "")
        alergenos = request.form.get("alergenos", "")
        empresa_id = session.get('user_empresa_id', 1)
        centro_id = centro_activo_id()
        traducciones = autocompletar_traducciones(nombre)
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO recetas (nombre, descripcion, alergenos, empresa_id, centro_id,
                                 nombre_en, nombre_fr, nombre_de, nombre_it)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            nombre, descripcion, alergenos, empresa_id, centro_id,
            traducciones["nombre_en"], traducciones["nombre_fr"],
            traducciones["nombre_de"], traducciones["nombre_it"],
        ))
        
        receta_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        flash('✅ Receta creada', 'success')
        return redirect(url_for('recetas.ver_receta', receta_id=receta_id))
    
    return render_template("recetas/nueva_receta.html")

@recetas_bp.route('/<int:receta_id>', methods=['GET', 'POST'])
@login_required
def ver_receta(receta_id):
    conn = get_db()
    _asegurar_recetas_schema(conn)
    conn.commit()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    receta_centro_sql = " AND centro_id = ?" if centro_id else ""
    receta_params = [receta_id, empresa_id] + ([centro_id] if centro_id else [])
    cursor = conn.cursor()
    
    if request.method == "POST":
        producto_id = request.form["producto_id"]
        cantidad = request.form["cantidad"]
        
        cursor.execute("""
            INSERT INTO recetas_ingredientes (receta_id, producto_id, cantidad)
            VALUES (?, ?, ?)
        """, (receta_id, producto_id, cantidad))
        
        conn.commit()
        flash('✅ Ingrediente añadido', 'success')
        return redirect(url_for('recetas.ver_receta', receta_id=receta_id))
    
    cursor.execute(f"SELECT * FROM recetas WHERE id = ? AND empresa_id = ? {receta_centro_sql}", receta_params)
    receta = cursor.fetchone()
    if not receta:
        conn.close()
        flash('Receta no encontrada en el centro activo', 'warning')
        return redirect(url_for('recetas.recetas'))
    
    cursor.execute("""
        SELECT ri.id, p.nombre, ri.cantidad, p.unidad, p.precio,
               p.alergenos, p.alergenos_validado,
               (ri.cantidad * p.precio) as coste
        FROM recetas_ingredientes ri
        JOIN productos p ON ri.producto_id = p.id
        WHERE ri.receta_id = ? AND p.empresa_id = ?
    """, (receta_id, empresa_id))
    
    ingredientes = cursor.fetchall()
    
    total_receta = 0
    for ing in ingredientes:
        total_receta += ing['cantidad'] * ing['precio']
    
    cursor.execute("""
        SELECT id, nombre, unidad, precio, alergenos, alergenos_validado
        FROM productos
        WHERE empresa_id = ?
          AND (? IS NULL OR centro_id = ?)
        ORDER BY nombre
    """, (empresa_id, centro_id, centro_id))
    productos = cursor.fetchall()
    
    conn.close()
    
    return render_template("recetas/ver_receta.html", 
                         receta=receta, 
                         ingredientes=ingredientes, 
                         productos=productos,
                         total_receta=total_receta)

@recetas_bp.route('/<int:receta_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_receta(receta_id):
    conn = get_db()
    _asegurar_recetas_schema(conn)
    conn.commit()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    
    receta = conn.execute("""
        SELECT * FROM recetas 
        WHERE id = ? AND empresa_id = ?
          AND (? IS NULL OR centro_id = ?)
    """, (receta_id, empresa_id, centro_id, centro_id)).fetchone()
    
    if not receta:
        flash('Receta no encontrada', 'danger')
        return redirect(url_for('recetas.recetas'))
    
    if request.method == 'POST':
        nombre = request.form['nombre']
        categoria = request.form.get('categoria', '')
        tiempo = request.form.get('tiempo_preparacion')
        alergenos = request.form.get('alergenos', '')
        nombre_en = request.form.get('nombre_en', '')
        nombre_fr = request.form.get('nombre_fr', '')
        nombre_de = request.form.get('nombre_de', '')
        nombre_it = request.form.get('nombre_it', '')
        traducciones = autocompletar_traducciones(nombre, {
            "nombre_en": nombre_en,
            "nombre_fr": nombre_fr,
            "nombre_de": nombre_de,
            "nombre_it": nombre_it,
        })
        nombre_en = traducciones["nombre_en"]
        nombre_fr = traducciones["nombre_fr"]
        nombre_de = traducciones["nombre_de"]
        nombre_it = traducciones["nombre_it"]
        
        conn.execute("""
            UPDATE recetas 
            SET nombre = ?, categoria = ?, tiempo_preparacion = ?, alergenos = ?,
                nombre_en = ?, nombre_fr = ?, nombre_de = ?, nombre_it = ?
            WHERE id = ? AND empresa_id = ?
        """, (nombre, categoria, tiempo, alergenos, nombre_en, nombre_fr, nombre_de, nombre_it, receta_id, empresa_id))
        
        conn.execute("DELETE FROM recetas_ingredientes WHERE receta_id = ?", (receta_id,))
        
        ingredientes_ids = request.form.getlist('ingrediente_id[]')
        cantidades = request.form.getlist('cantidad[]')
        nuevos_nombres = request.form.getlist('nuevo_ingrediente_nombre[]')
        nuevas_cantidades = request.form.getlist('nuevo_ingrediente_cantidad[]')
        nuevas_unidades = request.form.getlist('nuevo_ingrediente_unidad[]')
        nuevos_alergenos = request.form.getlist('nuevo_ingrediente_alergenos[]')
        nuevos_tipos = request.form.getlist('nuevo_ingrediente_tipo_producto[]')
        
        for ing_id, cant in zip(ingredientes_ids, cantidades):
            cantidad = _float_receta(cant, 0)
            if ing_id and cantidad > 0:
                conn.execute("""
                    INSERT INTO recetas_ingredientes (receta_id, producto_id, cantidad)
                    VALUES (?, ?, ?)
                """, (receta_id, ing_id, cantidad))
        
        for i in range(len(nuevos_nombres)):
            nombre_nuevo = (nuevos_nombres[i] or "").strip()
            if nombre_nuevo:
                cantidad_nueva = _float_receta(nuevas_cantidades[i] if i < len(nuevas_cantidades) else "", 1.0)
                if cantidad_nueva <= 0:
                    cantidad_nueva = 1.0
                nuevo_alergeno = nuevos_alergenos[i] if i < len(nuevos_alergenos) else ''
                nuevo_tipo = nuevos_tipos[i] if i < len(nuevos_tipos) else ''
                nuevo_producto_id = _obtener_o_crear_producto_receta(
                    conn,
                    empresa_id,
                    centro_id,
                    nombre_nuevo,
                    nuevas_unidades[i] if i < len(nuevas_unidades) and nuevas_unidades[i] else 'ud',
                    nuevo_alergeno,
                    nuevo_tipo,
                )
                if not nuevo_producto_id:
                    continue
                
                existe_linea = conn.execute("""
                    SELECT id FROM recetas_ingredientes
                    WHERE receta_id = ? AND producto_id = ?
                    LIMIT 1
                """, (receta_id, nuevo_producto_id)).fetchone()
                if existe_linea:
                    conn.execute(
                        "UPDATE recetas_ingredientes SET cantidad = ? WHERE id = ?",
                        (cantidad_nueva, existe_linea["id"]),
                    )
                else:
                    conn.execute("""
                        INSERT INTO recetas_ingredientes (receta_id, producto_id, cantidad)
                        VALUES (?, ?, ?)
                    """, (receta_id, nuevo_producto_id, cantidad_nueva))
        
        conn.commit()
        conn.close()
        flash('✅ Receta actualizada correctamente', 'success')
        return redirect(url_for('recetas.ver_receta', receta_id=receta_id))
    
    ingredientes_actuales = conn.execute("""
        SELECT ri.*, p.nombre as producto_nombre, p.unidad, p.alergenos
        FROM recetas_ingredientes ri
        JOIN productos p ON ri.producto_id = p.id
        WHERE ri.receta_id = ?
        ORDER BY ri.id
    """, (receta_id,)).fetchall()
    
    productos = conn.execute("""
        SELECT id, nombre, unidad, alergenos
        FROM productos 
        WHERE empresa_id = ? AND activo = 1
          AND (? IS NULL OR centro_id = ?)
        ORDER BY nombre
    """, (empresa_id, centro_id, centro_id)).fetchall()
    
    conn.close()
    
    return render_template('recetas/editar_receta.html', 
                           receta=receta, 
                           ingredientes=ingredientes_actuales,
                           productos=productos)


@recetas_bp.route('/<int:receta_id>/ingrediente/ventana', methods=['POST'])
@login_required
def agregar_ingrediente_ventana(receta_id):
    """Compatibilidad con el selector emergente antiguo de productos."""
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    producto_id = request.form.get("producto_id")
    cantidad = _float_receta(request.form.get("cantidad"), 0)
    receta = conn.execute(
        "SELECT id FROM recetas WHERE id = ? AND empresa_id = ?",
        (receta_id, empresa_id),
    ).fetchone()
    producto = conn.execute(
        "SELECT id FROM productos WHERE id = ? AND empresa_id = ?",
        (producto_id, empresa_id),
    ).fetchone() if producto_id else None
    if not receta or not producto or cantidad <= 0:
        conn.close()
        flash("No se pudo añadir el ingrediente: revisa producto y cantidad.", "warning")
        return redirect(url_for("recetas.editar_receta", receta_id=receta_id))

    existente = conn.execute(
        "SELECT id FROM recetas_ingredientes WHERE receta_id = ? AND producto_id = ?",
        (receta_id, producto["id"]),
    ).fetchone()
    if existente:
        conn.execute(
            "UPDATE recetas_ingredientes SET cantidad = ? WHERE id = ?",
            (cantidad, existente["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO recetas_ingredientes (receta_id, producto_id, cantidad) VALUES (?, ?, ?)",
            (receta_id, producto["id"], cantidad),
        )
    conn.commit()
    conn.close()
    flash("Ingrediente añadido a la receta.", "success")
    return redirect(url_for("recetas.editar_receta", receta_id=receta_id))


@recetas_bp.route('/<int:receta_id>/producto/ventana', methods=['POST'])
@login_required
def nuevo_producto_ventana(receta_id):
    """Crea un producto simple desde el popup antiguo y lo enlaza a la receta."""
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    nombre = (request.form.get("nombre") or "").strip()
    unidad = (request.form.get("unidad") or "ud").strip() or "ud"
    cantidad = _float_receta(request.form.get("cantidad"), 1.0)
    precio = _float_receta(request.form.get("precio"), 0)
    tipo_conservacion = (request.form.get("tipo_conservacion") or "seco").strip()
    alergenos = unir_alergenos(detectar_alergenos(nombre))
    receta = conn.execute(
        "SELECT id FROM recetas WHERE id = ? AND empresa_id = ?",
        (receta_id, empresa_id),
    ).fetchone()
    if not receta or not nombre:
        conn.close()
        flash("No se pudo crear el producto: falta nombre o receta valida.", "warning")
        return redirect(url_for("recetas.editar_receta", receta_id=receta_id))

    producto_id = _obtener_o_crear_producto_receta(
        conn,
        empresa_id,
        centro_id,
        nombre,
        unidad=unidad,
        alergenos=alergenos,
        tipo_producto=tipo_conservacion,
    )
    if producto_id and precio > 0:
        conn.execute(
            "UPDATE productos SET precio = ?, tipo_conservacion = ? WHERE id = ? AND empresa_id = ?",
            (precio, tipo_conservacion, producto_id, empresa_id),
        )
    if producto_id:
        existente = conn.execute(
            "SELECT id FROM recetas_ingredientes WHERE receta_id = ? AND producto_id = ?",
            (receta_id, producto_id),
        ).fetchone()
        if existente:
            conn.execute(
                "UPDATE recetas_ingredientes SET cantidad = ? WHERE id = ?",
                (cantidad if cantidad > 0 else 1.0, existente["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO recetas_ingredientes (receta_id, producto_id, cantidad) VALUES (?, ?, ?)",
                (receta_id, producto_id, cantidad if cantidad > 0 else 1.0),
            )
    conn.commit()
    conn.close()
    flash("Producto creado y añadido a la receta.", "success")
    return redirect(url_for("recetas.editar_receta", receta_id=receta_id))


@recetas_bp.route('/ingrediente/<int:ingrediente_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_ingrediente(ingrediente_id):
    conn = get_db()
    cursor = conn.cursor()
    
    if request.method == "POST":
        nueva_cantidad = float(request.form["cantidad"])
        
        cursor.execute("""
            UPDATE recetas_ingredientes SET cantidad = ? WHERE id = ?
        """, (nueva_cantidad, ingrediente_id))
        
        cursor.execute("SELECT receta_id FROM recetas_ingredientes WHERE id = ?", (ingrediente_id,))
        receta_id = cursor.fetchone()['receta_id']
        
        conn.commit()
        conn.close()
        
        flash('✅ Cantidad actualizada', 'success')
        return redirect(url_for('recetas.ver_receta', receta_id=receta_id))
    
    cursor.execute("""
        SELECT ri.*, p.nombre, p.unidad
        FROM recetas_ingredientes ri
        JOIN productos p ON ri.producto_id = p.id
        WHERE ri.id = ?
    """, (ingrediente_id,))
    ingrediente = cursor.fetchone()
    conn.close()
    
    return render_template('recetas/editar_ingrediente.html', ingrediente=ingrediente)

@recetas_bp.route('/ingrediente/<int:ingrediente_id>/eliminar', methods=['POST'])
@login_required
def eliminar_ingrediente(ingrediente_id):
    conn = get_db()
    cursor = conn.cursor()
    empresa_id = session.get('user_empresa_id', 1)
    
    cursor.execute(
        """
        SELECT ri.receta_id
        FROM recetas_ingredientes ri
        JOIN recetas r ON r.id = ri.receta_id
        WHERE ri.id = ? AND r.empresa_id = ?
        """,
        (ingrediente_id, empresa_id),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash('Ingrediente no encontrado para esta empresa.', 'warning')
        return redirect(url_for('recetas.recetas'))
    receta_id = row['receta_id']
    
    cursor.execute("DELETE FROM recetas_ingredientes WHERE id = ?", (ingrediente_id,))
    conn.commit()
    conn.close()
    
    flash('✅ Ingrediente eliminado', 'success')
    return redirect(url_for('recetas.ver_receta', receta_id=receta_id))

@recetas_bp.route('/<int:receta_id>/elaborar', methods=['POST'])
@login_required
def elaborar_receta(receta_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    receta = conn.execute(
        "SELECT id FROM recetas WHERE id = ? AND empresa_id = ?",
        (receta_id, empresa_id),
    ).fetchone()
    if not receta:
        conn.close()
        flash('Receta no encontrada para esta empresa.', 'warning')
        return redirect(url_for('recetas.recetas'))

    ingredientes = conn.execute("""
        SELECT ri.producto_id, ri.cantidad
        FROM recetas_ingredientes ri
        JOIN productos p ON p.id = ri.producto_id AND p.empresa_id = ?
        WHERE ri.receta_id = ?
    """, (empresa_id, receta_id)).fetchall()

    for ing in ingredientes:
        conn.execute("""
            UPDATE productos
            SET cantidad = cantidad - ?
            WHERE id = ? AND empresa_id = ?
        """, (ing["cantidad"], ing["producto_id"], empresa_id))

    conn.commit()
    conn.close()

    flash('Stock descontado desde receta elaborada.', 'success')
    return redirect(url_for('productos.productos'))

@recetas_bp.route('/<int:receta_id>/imprimir')
@login_required
def imprimir_receta(receta_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    
    receta = conn.execute("""
        SELECT * FROM recetas WHERE id = ? AND empresa_id = ?
    """, (receta_id, empresa_id)).fetchone()
    
    if not receta:
        flash('Receta no encontrada', 'danger')
        return redirect(url_for('recetas.recetas'))
    
    ingredientes = conn.execute("""
        SELECT ri.id, p.nombre, ri.cantidad, p.unidad, p.precio,
               (ri.cantidad * p.precio) as coste
        FROM recetas_ingredientes ri
        JOIN productos p ON ri.producto_id = p.id
        WHERE ri.receta_id = ? AND p.empresa_id = ?
    """, (receta_id, empresa_id)).fetchall()
    
    # Calcular total de la receta
    total_receta = sum(ing['coste'] for ing in ingredientes) if ingredientes else 0
    
    conn.close()
    
    return render_template('recetas/imprimir_receta.html', 
                           receta=receta, 
                           ingredientes=ingredientes,
                           total_receta=total_receta,
                           now=datetime.now)

@recetas_bp.route('/<int:receta_id>/eliminar', methods=['POST'])
@login_required
def eliminar_receta(receta_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    
    cursor = conn.cursor()
    receta = cursor.execute("SELECT * FROM recetas WHERE id = ? AND empresa_id = ?", (receta_id, empresa_id)).fetchone()
    
    if not receta:
        flash('❌ Receta no encontrada', 'danger')
        return redirect(url_for('recetas.recetas'))
    
    cursor.execute("DELETE FROM recetas_ingredientes WHERE receta_id = ?", (receta_id,))
    cursor.execute("DELETE FROM recetas WHERE id = ?", (receta_id,))
    
    conn.commit()
    conn.close()
    
    flash('✅ Receta eliminada', 'success')
    return redirect(url_for('recetas.recetas'))

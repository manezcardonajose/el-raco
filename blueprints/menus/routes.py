# blueprints/menus/routes.py
from flask import render_template, request, redirect, url_for, session, flash, send_file
from database import get_db
from datetime import datetime, timedelta
from . import menus_bp
from blueprints.auth.routes import login_required, usuario_tiene_permiso
from openpyxl import Workbook
from io import BytesIO
import re
import unicodedata
from helpers.centros_helper import (
    asignar_centro_desde_departamento,
    centro_activo_id,
    ids_departamentos_centro,
)
from helpers.receta_traducciones import autocompletar_traducciones


ENDPOINTS_MENUS_LECTURA = {
    "menus.lista_tipos_menu",
    "menus.lista_menus_fijos",
    "menus.ver_menu_fijo",
    "menus.imprimir_menu_fijo",
    "menus.lista_menus_principal",
    "menus.ver_menu_diario",
    "menus.imprimir_menu_diario",
    "menus.vista_semanal_menus",
    "menus.exportar_menus",
    "menus.importar_menus_centro",
}


@menus_bp.before_request
def _bloquear_gestion_menus_sin_permiso():
    if "user_id" not in session:
        return None
    if usuario_tiene_permiso("produccion_editar"):
        return None
    if request.method == "GET" and request.endpoint in ENDPOINTS_MENUS_LECTURA:
        return None
    flash("Puedes consultar menus y alergenos, pero no crear, editar ni eliminar.", "warning")
    return redirect(url_for("menus.lista_menus_principal"))


# =========================================================
# HELPERS MENÚ FIJO
# =========================================================
def _normalizar_menu_txt(valor):
    txt = str(valor or "").strip().lower()
    txt = unicodedata.normalize("NFKD", txt)
    return "".join(c for c in txt if not unicodedata.combining(c))


def _dia_semana_es(fecha_dt):
    return ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"][fecha_dt.weekday()]


def _semana_y_dia_menu(fecha_menu):
    txt = _normalizar_menu_txt(fecha_menu)
    semana = None
    match = re.search(r"semana\s*(\d+)", txt)
    if match:
        semana = int(match.group(1))
    dia = None
    for nombre in ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"):
        if nombre in txt:
            dia = nombre
            break
    return semana, dia


def _nombre_plato_publicable_tpv(nombre):
    nombre = " ".join(str(nombre or "").split())
    if not nombre:
        return False
    normal = _normalizar_menu_txt(nombre)
    letras = sum(c.isalpha() for c in nombre)
    digitos = sum(c.isdigit() for c in nombre)
    if len(normal) < 5 or letras < 5:
        return False
    if digitos >= 3:
        return False
    if letras / max(len(nombre), 1) < 0.58:
        return False
    if len(re.findall(r"[<>/\\\\|_{}\\[\\]~^]", nombre)) >= 2:
        return False
    tokens = normal.split()
    ruido = {"poo", "doo", "sss", "333", "ctt", "mmm", "onop", "oooo", "ooooo"}
    if any(token in ruido for token in tokens):
        return False
    compacta = normal.replace(" ", "")
    ingredientes_base = [
        "masafresca", "masa fresca", "salsadetomate", "salsa de tomate",
        "quesorallado", "queso rallado", "reducciondebalsamico",
        "reduccion de balsamico", "aceitedeoliva", "aceite de oliva",
    ]
    pistas_plato = [
        "pizza", "risotto", "rissoto", "lasana", "lasaña", "canelon",
        "berenjena", "ensalada", "tiramisu", "pannacota", "coulat",
        "solomillo", "rodaballo", "carpaccio",
    ]
    es_base = any(item in normal or item.replace(" ", "") in compacta for item in ingredientes_base)
    tiene_pista = any(item in normal or item.replace(" ", "") in compacta for item in pistas_plato)
    if es_base and not tiene_pista:
        return False
    return True


def _column_exists(conn, table_name, column_name):
    try:
        return any(row["name"] == column_name for row in conn.execute(f"PRAGMA table_info({table_name})"))
    except Exception:
        return False


def _add_column_if_missing(conn, table_name, column_sql):
    column_name = column_sql.strip().split()[0]
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    if table_exists and not _column_exists(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def _row_dict(row):
    return dict(row) if row else {}


def _ensure_menu_centros_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_fijo_subdepartamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_id INTEGER NOT NULL,
            subdepartamento_id INTEGER,
            seccion TEXT,
            orden INTEGER DEFAULT 0,
            FOREIGN KEY (menu_id) REFERENCES menus(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_fijo_platos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_fijo_subdepartamento_id INTEGER NOT NULL,
            receta_id INTEGER NOT NULL,
            orden INTEGER DEFAULT 0,
            FOREIGN KEY (menu_fijo_subdepartamento_id)
                REFERENCES menu_fijo_subdepartamentos(id) ON DELETE CASCADE,
            FOREIGN KEY (receta_id) REFERENCES recetas(id)
        )
    """)
    for table in ("tipos_menu", "menus", "menus_diarios", "planificacion_servicios"):
        _add_column_if_missing(conn, table, "centro_id INTEGER")


def _centros_disponibles(conn, empresa_id):
    return conn.execute("""
        SELECT id, nombre
        FROM centros_trabajo
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
        ORDER BY nombre
    """, (empresa_id,)).fetchall()


def _obtener_o_crear_tipo_menu(conn, empresa_id, centro_destino_id, tipo_origen_id):
    if not tipo_origen_id:
        return None
    origen = conn.execute(
        "SELECT * FROM tipos_menu WHERE id = ? AND empresa_id = ?",
        (tipo_origen_id, empresa_id),
    ).fetchone()
    if not origen:
        return None
    existente = conn.execute("""
        SELECT id FROM tipos_menu
        WHERE empresa_id = ?
          AND centro_id = ?
          AND LOWER(nombre) = LOWER(?)
        LIMIT 1
    """, (empresa_id, centro_destino_id, origen["nombre"])).fetchone()
    if existente:
        return existente["id"]
    columnas = _row_dict(origen)
    conn.execute("""
        INSERT INTO tipos_menu (nombre, punto_venta_id, activo, empresa_id, centro_id)
        VALUES (?, NULL, ?, ?, ?)
    """, (columnas.get("nombre"), columnas.get("activo", 1), empresa_id, centro_destino_id))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _buscar_subdepartamento_destino(conn, empresa_id, centro_destino_id, subdepartamento_origen_id):
    if not subdepartamento_origen_id:
        return None
    origen = conn.execute(
        """
        SELECT s.*, d.nombre AS departamento_nombre
        FROM subdepartamentos s
        LEFT JOIN departamentos d ON d.id = s.departamento_id
        WHERE s.id = ? AND s.empresa_id = ?
        """,
        (subdepartamento_origen_id, empresa_id),
    ).fetchone()
    if not origen:
        return None
    destino = conn.execute("""
        SELECT id FROM subdepartamentos
        WHERE empresa_id = ?
          AND centro_id = ?
          AND LOWER(nombre) = LOWER(?)
        ORDER BY id
        LIMIT 1
    """, (empresa_id, centro_destino_id, origen["nombre"])).fetchone()
    if destino:
        return destino["id"]

    dep_destino = None
    if origen["departamento_nombre"]:
        dep_destino = conn.execute("""
            SELECT id FROM departamentos
            WHERE empresa_id = ?
              AND centro_id = ?
              AND LOWER(nombre) = LOWER(?)
            ORDER BY id
            LIMIT 1
        """, (empresa_id, centro_destino_id, origen["departamento_nombre"])).fetchone()

    if not dep_destino and origen["departamento_id"]:
        dep_origen = conn.execute(
            "SELECT * FROM departamentos WHERE id = ? AND empresa_id = ?",
            (origen["departamento_id"], empresa_id),
        ).fetchone()
        if dep_origen:
            cur_dep = conn.execute("""
                INSERT INTO departamentos
                (nombre, descripcion, activo, empresa_id, tipo_operativo, departamento_padre_id,
                 es_cocina, independiente_cocina, gestiona_appcc, gestiona_stock, gestiona_mantenimiento,
                 permite_tpv, permite_pedidos_internos, permite_pedidos_externos, plantilla_operativa, centro_id)
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                dep_origen["nombre"],
                dep_origen["descripcion"],
                dep_origen["activo"],
                empresa_id,
                dep_origen["tipo_operativo"] if "tipo_operativo" in dep_origen.keys() else "general",
                dep_origen["es_cocina"] if "es_cocina" in dep_origen.keys() else 0,
                dep_origen["independiente_cocina"] if "independiente_cocina" in dep_origen.keys() else 0,
                dep_origen["gestiona_appcc"] if "gestiona_appcc" in dep_origen.keys() else 0,
                dep_origen["gestiona_stock"] if "gestiona_stock" in dep_origen.keys() else 0,
                dep_origen["gestiona_mantenimiento"] if "gestiona_mantenimiento" in dep_origen.keys() else 0,
                dep_origen["permite_tpv"] if "permite_tpv" in dep_origen.keys() else 0,
                dep_origen["permite_pedidos_internos"] if "permite_pedidos_internos" in dep_origen.keys() else 0,
                dep_origen["permite_pedidos_externos"] if "permite_pedidos_externos" in dep_origen.keys() else 0,
                dep_origen["plantilla_operativa"] if "plantilla_operativa" in dep_origen.keys() else None,
                centro_destino_id,
            ))
            dep_destino = {"id": cur_dep.lastrowid}

    if not dep_destino:
        return None

    cur = conn.execute("""
        INSERT INTO subdepartamentos (nombre, departamento_id, descripcion, activo, empresa_id, centro_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        origen["nombre"],
        dep_destino["id"],
        origen["descripcion"] or f"Creado al importar menús desde otro centro.",
        origen["activo"] if "activo" in origen.keys() else 1,
        empresa_id,
        centro_destino_id,
    ))
    return cur.lastrowid


def _obtener_o_crear_producto_destino(conn, empresa_id, centro_destino_id, producto_origen_id):
    if not producto_origen_id:
        return None
    origen = conn.execute(
        "SELECT * FROM productos WHERE id = ? AND empresa_id = ?",
        (producto_origen_id, empresa_id),
    ).fetchone()
    if not origen:
        return None

    existente = conn.execute("""
        SELECT id FROM productos
        WHERE empresa_id = ?
          AND centro_id = ?
          AND LOWER(nombre) = LOWER(?)
        LIMIT 1
    """, (empresa_id, centro_destino_id, origen["nombre"])).fetchone()
    if existente:
        return existente["id"]

    cols_disponibles = [row["name"] for row in conn.execute("PRAGMA table_info(productos)").fetchall()]
    data = _row_dict(origen)
    columnas = []
    valores = []
    for col in cols_disponibles:
        if col == "id":
            continue
        columnas.append(col)
        if col == "empresa_id":
            valores.append(empresa_id)
        elif col == "centro_id":
            valores.append(centro_destino_id)
        elif col == "codigo":
            valores.append(None)
        else:
            valores.append(data.get(col))
    placeholders = ", ".join("?" for _ in columnas)
    conn.execute(
        f"INSERT INTO productos ({', '.join(columnas)}) VALUES ({placeholders})",
        valores,
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _copiar_ingredientes_receta(conn, empresa_id, receta_origen_id, receta_destino_id, centro_destino_id):
    ingredientes = conn.execute("""
        SELECT ri.*, p.id AS producto_origen_id, p.nombre AS producto_nombre
        FROM recetas_ingredientes ri
        JOIN productos p ON p.id = ri.producto_id
        WHERE ri.receta_id = ?
        ORDER BY ri.id
    """, (receta_origen_id,)).fetchall()
    copiados = 0
    for ing in ingredientes:
        producto_destino = _obtener_o_crear_producto_destino(
            conn,
            empresa_id,
            centro_destino_id,
            ing["producto_origen_id"],
        )
        if not producto_destino:
            continue
        existe_linea = conn.execute("""
            SELECT id FROM recetas_ingredientes
            WHERE receta_id = ? AND producto_id = ?
            LIMIT 1
        """, (receta_destino_id, producto_destino)).fetchone()
        if existe_linea:
            continue
        conn.execute("""
            INSERT INTO recetas_ingredientes (receta_id, producto_id, cantidad)
            VALUES (?, ?, ?)
        """, (receta_destino_id, producto_destino, ing["cantidad"]))
        copiados += 1
    return copiados


def _obtener_o_crear_receta_destino(conn, empresa_id, centro_destino_id, receta_origen_id, importar_recetas=True):
    if not receta_origen_id:
        return None
    origen = conn.execute(
        "SELECT * FROM recetas WHERE id = ? AND empresa_id = ?",
        (receta_origen_id, empresa_id),
    ).fetchone()
    if not origen:
        return None
    existente = conn.execute("""
        SELECT id FROM recetas
        WHERE empresa_id = ?
          AND centro_id = ?
          AND LOWER(nombre) = LOWER(?)
        LIMIT 1
    """, (empresa_id, centro_destino_id, origen["nombre"])).fetchone()
    if existente:
        destino_actual = conn.execute(
            "SELECT * FROM recetas WHERE id = ? AND empresa_id = ?",
            (existente["id"], empresa_id),
        ).fetchone()
        traducciones = autocompletar_traducciones(origen["nombre"], _row_dict(destino_actual))
        conn.execute("""
            UPDATE recetas
            SET nombre_en = COALESCE(NULLIF(nombre_en, ''), ?),
                nombre_fr = COALESCE(NULLIF(nombre_fr, ''), ?),
                nombre_de = COALESCE(NULLIF(nombre_de, ''), ?),
                nombre_it = COALESCE(NULLIF(nombre_it, ''), ?)
            WHERE id = ? AND empresa_id = ?
        """, (
            traducciones["nombre_en"],
            traducciones["nombre_fr"],
            traducciones["nombre_de"],
            traducciones["nombre_it"],
            existente["id"],
            empresa_id,
        ))
        tiene_ingredientes = conn.execute(
            "SELECT 1 FROM recetas_ingredientes WHERE receta_id = ? LIMIT 1",
            (existente["id"],),
        ).fetchone()
        if not tiene_ingredientes:
            _copiar_ingredientes_receta(conn, empresa_id, receta_origen_id, existente["id"], centro_destino_id)
        return existente["id"]
    if not importar_recetas:
        return None

    data = _row_dict(origen)
    traducciones = autocompletar_traducciones(origen["nombre"], data)
    cur = conn.execute("""
        INSERT INTO recetas (nombre, descripcion, alergenos, imagen, tiempo_preparacion, dificultad, categoria,
                             empresa_id, raciones_base, centro_id, nombre_en, nombre_fr, nombre_de, nombre_it)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get("nombre"),
        data.get("descripcion"),
        data.get("alergenos"),
        data.get("imagen"),
        data.get("tiempo_preparacion"),
        data.get("dificultad"),
        data.get("categoria"),
        empresa_id,
        data.get("raciones_base") or 4,
        centro_destino_id,
        traducciones["nombre_en"],
        traducciones["nombre_fr"],
        traducciones["nombre_de"],
        traducciones["nombre_it"],
    ))
    receta_destino_id = cur.lastrowid

    _copiar_ingredientes_receta(conn, empresa_id, receta_origen_id, receta_destino_id, centro_destino_id)
    return receta_destino_id


def _copiar_menu_fijo_a_centro(conn, empresa_id, menu_id, centro_destino_id, importar_recetas=True):
    origen = conn.execute("SELECT * FROM menus WHERE id = ? AND empresa_id = ?", (menu_id, empresa_id)).fetchone()
    if not origen:
        return None, "omitido"
    tipo_destino_id = _obtener_o_crear_tipo_menu(conn, empresa_id, centro_destino_id, origen["tipo_menu_id"])
    existente = conn.execute("""
        SELECT id FROM menus
        WHERE empresa_id = ?
          AND centro_id = ?
          AND LOWER(nombre) = LOWER(?)
          AND COALESCE(tipo_menu_id, 0) = COALESCE(?, 0)
        LIMIT 1
    """, (empresa_id, centro_destino_id, origen["nombre"], tipo_destino_id)).fetchone()
    if existente:
        return existente["id"], "existente"

    cur = conn.execute("""
        INSERT INTO menus (nombre, tipo_menu_id, fecha_inicio, fecha_fin, activo, observaciones, empresa_id,
                           departamento_id, punto_venta_id, modo_servicio, centro_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
    """, (
        origen["nombre"],
        tipo_destino_id,
        origen["fecha_inicio"],
        origen["fecha_fin"],
        origen["activo"],
        origen["observaciones"],
        empresa_id,
        origen["modo_servicio"],
        centro_destino_id,
    ))
    nuevo_menu_id = cur.lastrowid
    secciones = conn.execute("SELECT * FROM menu_fijo_subdepartamentos WHERE menu_id = ? ORDER BY orden, id", (menu_id,)).fetchall()
    for seccion in secciones:
        subdest = _buscar_subdepartamento_destino(conn, empresa_id, centro_destino_id, seccion["subdepartamento_id"])
        cur_sec = conn.execute("""
            INSERT INTO menu_fijo_subdepartamentos (menu_id, subdepartamento_id, seccion, orden)
            VALUES (?, ?, ?, ?)
        """, (nuevo_menu_id, subdest, seccion["seccion"], seccion["orden"]))
        nueva_seccion_id = cur_sec.lastrowid
        platos = conn.execute("SELECT * FROM menu_fijo_platos WHERE menu_fijo_subdepartamento_id = ? ORDER BY orden, id", (seccion["id"],)).fetchall()
        for plato in platos:
            receta_dest = _obtener_o_crear_receta_destino(conn, empresa_id, centro_destino_id, plato["receta_id"], importar_recetas)
            if receta_dest:
                conn.execute("""
                    INSERT INTO menu_fijo_platos (menu_fijo_subdepartamento_id, receta_id, orden)
                    VALUES (?, ?, ?)
                """, (nueva_seccion_id, receta_dest, plato["orden"]))
    return nuevo_menu_id, "creado"


def _copiar_menu_diario_a_centro(conn, empresa_id, menu_id, centro_destino_id, importar_recetas=True):
    origen = conn.execute("SELECT * FROM menus_diarios WHERE id = ? AND empresa_id = ?", (menu_id, empresa_id)).fetchone()
    if not origen:
        return None, "omitido"
    tipo_destino_id = _obtener_o_crear_tipo_menu(conn, empresa_id, centro_destino_id, origen["tipo_menu_id"])
    existente = conn.execute("""
        SELECT id FROM menus_diarios
        WHERE empresa_id = ?
          AND centro_id = ?
          AND fecha = ?
          AND COALESCE(tipo_menu_id, 0) = COALESCE(?, 0)
        LIMIT 1
    """, (empresa_id, centro_destino_id, origen["fecha"], tipo_destino_id)).fetchone()
    if existente:
        return existente["id"], "existente"

    cur = conn.execute("""
        INSERT INTO menus_diarios (fecha, tipo_menu_id, observaciones, empresa_id, departamento_id, punto_venta_id, modo_servicio, centro_id)
        VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)
    """, (
        origen["fecha"],
        tipo_destino_id,
        origen["observaciones"],
        empresa_id,
        origen["modo_servicio"],
        centro_destino_id,
    ))
    nuevo_menu_id = cur.lastrowid
    secciones = conn.execute("SELECT * FROM menu_subdepartamentos WHERE menu_diario_id = ? ORDER BY orden, id", (menu_id,)).fetchall()
    for seccion in secciones:
        subdest = _buscar_subdepartamento_destino(conn, empresa_id, centro_destino_id, seccion["subdepartamento_id"])
        cur_sec = conn.execute("""
            INSERT INTO menu_subdepartamentos (menu_diario_id, subdepartamento_id, seccion, orden)
            VALUES (?, ?, ?, ?)
        """, (nuevo_menu_id, subdest, seccion["seccion"], seccion["orden"]))
        nueva_seccion_id = cur_sec.lastrowid
        platos = conn.execute("SELECT * FROM menu_platos WHERE menu_subdepartamento_id = ? ORDER BY orden, id", (seccion["id"],)).fetchall()
        for plato in platos:
            receta_dest = _obtener_o_crear_receta_destino(conn, empresa_id, centro_destino_id, plato["receta_id"], importar_recetas)
            if receta_dest:
                conn.execute("""
                    INSERT INTO menu_platos (menu_subdepartamento_id, receta_id, orden)
                    VALUES (?, ?, ?)
                """, (nueva_seccion_id, receta_dest, plato["orden"]))
    return nuevo_menu_id, "creado"


def _menu_centro_sql(alias):
    centro_id = centro_activo_id()
    if not centro_id:
        return "", []
    return f" AND {alias}.centro_id = ?", [centro_id]


def _subdepartamentos_centro_sql(conn, empresa_id, alias="s"):
    centro_id = centro_activo_id()
    if not centro_id:
        return "", []
    dep_ids = ids_departamentos_centro(conn, empresa_id, centro_id)
    condiciones = [f"{alias}.centro_id = ?"]
    params = [centro_id]
    if dep_ids:
        condiciones.append(f"{alias}.departamento_id IN ({','.join('?' for _ in dep_ids)})")
        params.extend(dep_ids)
    return f" AND ({' OR '.join(condiciones)})", params


def _menu_diario_en_ambito(conn, menu_id, empresa_id, alias="md"):
    _ensure_menu_centros_schema(conn)
    centro_sql, centro_params = _menu_centro_sql(alias)
    return conn.execute(f"""
        SELECT {alias}.*
        FROM menus_diarios {alias}
        WHERE {alias}.id = ?
          AND {alias}.empresa_id = ?
          {centro_sql}
    """, [menu_id, empresa_id] + centro_params).fetchone()


def _seccion_menu_en_ambito(conn, seccion_id, empresa_id):
    _ensure_menu_centros_schema(conn)
    centro_sql, centro_params = _menu_centro_sql("md")
    return conn.execute(f"""
        SELECT ms.*, md.centro_id, md.empresa_id
        FROM menu_subdepartamentos ms
        JOIN menus_diarios md ON md.id = ms.menu_diario_id
        WHERE ms.id = ?
          AND md.empresa_id = ?
          {centro_sql}
    """, [seccion_id, empresa_id] + centro_params).fetchone()


def _plato_menu_en_ambito(conn, plato_id, empresa_id):
    _ensure_menu_centros_schema(conn)
    centro_sql, centro_params = _menu_centro_sql("md")
    return conn.execute(f"""
        SELECT mp.*, ms.menu_diario_id, md.centro_id, md.empresa_id
        FROM menu_platos mp
        JOIN menu_subdepartamentos ms ON ms.id = mp.menu_subdepartamento_id
        JOIN menus_diarios md ON md.id = ms.menu_diario_id
        WHERE mp.id = ?
          AND md.empresa_id = ?
          {centro_sql}
    """, [plato_id, empresa_id] + centro_params).fetchone()


def _menu_fijo_en_ambito(conn, menu_id, empresa_id, alias="m"):
    _ensure_menu_centros_schema(conn)
    centro_sql, centro_params = _menu_centro_sql(alias)
    return conn.execute(f"""
        SELECT {alias}.*
        FROM menus {alias}
        WHERE {alias}.id = ?
          AND {alias}.empresa_id = ?
          {centro_sql}
    """, [menu_id, empresa_id] + centro_params).fetchone()


def _seccion_menu_fijo_en_ambito(conn, seccion_id, empresa_id):
    _ensure_menu_centros_schema(conn)
    centro_sql, centro_params = _menu_centro_sql("m")
    return conn.execute(f"""
        SELECT mfs.*, m.centro_id, m.empresa_id
        FROM menu_fijo_subdepartamentos mfs
        JOIN menus m ON m.id = mfs.menu_id
        WHERE mfs.id = ?
          AND m.empresa_id = ?
          {centro_sql}
    """, [seccion_id, empresa_id] + centro_params).fetchone()


def _plato_menu_fijo_en_ambito(conn, plato_id, empresa_id):
    _ensure_menu_centros_schema(conn)
    centro_sql, centro_params = _menu_centro_sql("m")
    return conn.execute(f"""
        SELECT mfp.*, mfs.menu_id, m.centro_id, m.empresa_id
        FROM menu_fijo_platos mfp
        JOIN menu_fijo_subdepartamentos mfs ON mfs.id = mfp.menu_fijo_subdepartamento_id
        JOIN menus m ON m.id = mfs.menu_id
        WHERE mfp.id = ?
          AND m.empresa_id = ?
          {centro_sql}
    """, [plato_id, empresa_id] + centro_params).fetchone()


def _indexar_menus_ciclo(conn, empresa_id):
    _ensure_menu_centros_schema(conn)
    centro_sql, centro_params = _menu_centro_sql("md")
    rows = conn.execute("""
        SELECT md.*, t.nombre AS tipo_nombre
        FROM menus_diarios md
        JOIN tipos_menu t ON t.id = md.tipo_menu_id
        WHERE md.empresa_id = ?
        """ + centro_sql + """
        ORDER BY md.id
    """, [empresa_id] + centro_params).fetchall()

    indice = {}
    for row in rows:
        semana, dia = _semana_y_dia_menu(row["fecha"])
        if not dia:
            continue
        semana_key = semana or 1
        indice[(row["tipo_menu_id"], semana_key, dia)] = row
    return indice


def _guardar_planificacion_servicio(conn, empresa_id, fecha, menu_diario_id, subdepartamento_id=None, orden=0):
    _ensure_menu_centros_schema(conn)
    menu = conn.execute("""
        SELECT id, tipo_menu_id, centro_id
        FROM menus_diarios
        WHERE id = ? AND empresa_id = ?
    """, (menu_diario_id, empresa_id)).fetchone()
    if not menu:
        return False

    subdepartamento_id = int(subdepartamento_id) if subdepartamento_id else None
    tipo_menu_id = menu["tipo_menu_id"]

    conn.execute("""
        DELETE FROM planificacion_servicios
        WHERE fecha = ?
          AND tipo_menu_id = ?
          AND empresa_id = ?
          AND COALESCE(centro_id, 0) = COALESCE(?, 0)
          AND (
                (? IS NULL AND subdepartamento_id IS NULL)
                OR subdepartamento_id = ?
          )
    """, (fecha, tipo_menu_id, empresa_id, menu["centro_id"], subdepartamento_id, subdepartamento_id))

    conn.execute("""
        INSERT INTO planificacion_servicios
        (fecha, tipo_menu_id, menu_diario_id, subdepartamento_id, orden, empresa_id, centro_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (fecha, tipo_menu_id, menu_diario_id, subdepartamento_id, orden, empresa_id, menu["centro_id"]))
    return True


def obtener_secciones_menu_fijo(conn, menu_id):
    secciones = conn.execute("""
        SELECT
            mfs.*,
            s.nombre AS subdepartamento_nombre,
            d.nombre AS departamento_nombre
        FROM menu_fijo_subdepartamentos mfs
        JOIN subdepartamentos s ON mfs.subdepartamento_id = s.id
        JOIN departamentos d ON s.departamento_id = d.id
        WHERE mfs.menu_id = ?
        ORDER BY
            CASE
                WHEN LOWER(TRIM(s.nombre)) = 'cuarto frío' OR LOWER(TRIM(s.nombre)) = 'cuarto frio' THEN 1
                WHEN LOWER(TRIM(s.nombre)) = 'cocina caliente' THEN 2
                WHEN LOWER(TRIM(s.nombre)) = 'postres' THEN 3
                WHEN LOWER(TRIM(s.nombre)) = 'desayunos' THEN 1
                WHEN LOWER(TRIM(s.nombre)) = 'snack' THEN 1
                ELSE 9
            END,
            mfs.orden,
            LOWER(TRIM(COALESCE(mfs.seccion, '')))
    """, (menu_id,)).fetchall()

    secciones_con_platos = []

    for s in secciones:
        platos = conn.execute("""
            SELECT
                mfp.*,
                r.nombre AS receta_nombre
            FROM menu_fijo_platos mfp
            JOIN recetas r ON mfp.receta_id = r.id
            WHERE mfp.menu_fijo_subdepartamento_id = ?
            ORDER BY mfp.orden, LOWER(TRIM(r.nombre))
        """, (s["id"],)).fetchall()

        seccion_dict = dict(s)
        seccion_dict["platos"] = platos
        secciones_con_platos.append(seccion_dict)

    return secciones_con_platos


def _to_float_menu(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _destino_tpv_desde_seccion(seccion):
    texto = _normalizar_menu_txt(seccion or "")
    if "postre" in texto:
        return "postres"
    if "bar" in texto or "bebida" in texto:
        return "barra"
    if "buffet" in texto:
        return "buffet"
    return "cocina"


def _categoria_tpv_menu(conn, empresa_id, punto_venta_id, nombre, orden=0):
    nombre = (nombre or "Menu fijo").strip() or "Menu fijo"
    row = conn.execute(
        """
        SELECT id
        FROM tpv_categorias
        WHERE empresa_id = ? AND punto_venta_id = ? AND LOWER(nombre) = LOWER(?)
        LIMIT 1
        """,
        (empresa_id, punto_venta_id, nombre),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        """
        INSERT INTO tpv_categorias
        (punto_venta_id, nombre, tipo, color, orden, activo, empresa_id)
        VALUES (?, ?, 'menu_fijo', '#16a34a', ?, 1, ?)
        """,
        (punto_venta_id, nombre, orden, empresa_id),
    )
    return cur.lastrowid


def _publicar_menu_fijo_en_tpv(conn, empresa_id, menu_id, punto_venta_id, precio_base=0):
    from blueprints.tpv.routes import _ensure_tpv_schema

    _ensure_tpv_schema(conn)
    menu = _menu_fijo_en_ambito(conn, menu_id, empresa_id)
    if not menu:
        return {"publicados": 0, "actualizados": 0, "platos": 0}
    punto_venta = conn.execute("""
        SELECT id
        FROM puntos_venta
        WHERE id = ?
          AND empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND (? IS NULL OR centro_id = ?)
    """, (punto_venta_id, empresa_id, menu["centro_id"], menu["centro_id"])).fetchone()
    if not punto_venta:
        return {"publicados": 0, "actualizados": 0, "platos": 0}
    secciones = obtener_secciones_menu_fijo(conn, menu_id)
    publicados = 0
    actualizados = 0
    platos_total = 0
    precio = _to_float_menu(precio_base, 0)
    for orden_seccion, seccion in enumerate(secciones, start=1):
        categoria_nombre = seccion.get("seccion") or seccion.get("subdepartamento_nombre") or menu["nombre"]
        categoria_id = _categoria_tpv_menu(conn, empresa_id, punto_venta_id, categoria_nombre, 50 + orden_seccion)
        destino = _destino_tpv_desde_seccion(f"{categoria_nombre} {seccion.get('subdepartamento_nombre')}")
        for orden_plato, plato in enumerate(seccion.get("platos") or [], start=1):
            receta_id = plato["receta_id"]
            receta_nombre = plato["receta_nombre"]
            if not _nombre_plato_publicable_tpv(receta_nombre):
                continue
            platos_total += 1
            existe_rel = conn.execute(
                """
                SELECT id
                FROM punto_venta_recetas
                WHERE punto_venta_id = ? AND receta_id = ?
                LIMIT 1
                """,
                (punto_venta_id, receta_id),
            ).fetchone()
            if existe_rel:
                conn.execute(
                    "UPDATE punto_venta_recetas SET precio_venta = ?, activo = 1 WHERE id = ?",
                    (precio, existe_rel["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO punto_venta_recetas
                    (punto_venta_id, receta_id, precio_venta, activo)
                    VALUES (?, ?, ?, 1)
                    """,
                    (punto_venta_id, receta_id, precio),
                )
            articulo = conn.execute(
                """
                SELECT id
                FROM tpv_articulos
                WHERE empresa_id = ?
                  AND punto_venta_id = ?
                  AND receta_id = ?
                LIMIT 1
                """,
                (empresa_id, punto_venta_id, receta_id),
            ).fetchone()
            if articulo:
                conn.execute(
                    """
                    UPDATE tpv_articulos
                    SET categoria_id = ?, nombre = ?, tipo = 'receta', precio = ?,
                        destino_preparacion = ?, visible_tpv = 1, visible_comandero = 1,
                        orden = ?, activo = 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND empresa_id = ?
                    """,
                    (categoria_id, receta_nombre, precio, destino, orden_plato, articulo["id"], empresa_id),
                )
                actualizados += 1
            else:
                conn.execute(
                    """
                    INSERT INTO tpv_articulos
                    (punto_venta_id, categoria_id, receta_id, nombre, tipo, precio,
                     incluido_regimenes, suplemento, destino_preparacion,
                     visible_tpv, visible_comandero, orden, activo, empresa_id)
                    VALUES (?, ?, ?, ?, 'receta', ?, '', 0, ?, 1, 1, ?, 1, ?)
                    """,
                    (punto_venta_id, categoria_id, receta_id, receta_nombre, precio, destino, orden_plato, empresa_id),
                )
                publicados += 1
    conn.execute(
        """
        UPDATE menus
        SET punto_venta_id = ?
        WHERE id = ? AND empresa_id = ?
          AND COALESCE(centro_id, 0) = COALESCE(?, 0)
        """,
        (punto_venta_id, menu_id, empresa_id, menu["centro_id"]),
    )
    return {"publicados": publicados, "actualizados": actualizados, "platos": platos_total}


# =========================================================
# TIPOS DE MENÚ
# =========================================================
@menus_bp.route('/tipos')
@login_required
def lista_tipos_menu():
    conn = get_db()
    _ensure_menu_centros_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    centro_sql, centro_params = _menu_centro_sql("t")

    tipos = conn.execute("""
        SELECT t.*, pv.nombre as punto_venta_nombre,
               (SELECT COUNT(*) FROM menus WHERE tipo_menu_id = t.id AND centro_id = t.centro_id) as num_menus_fijos,
               (SELECT COUNT(*) FROM menus_diarios WHERE tipo_menu_id = t.id AND centro_id = t.centro_id) as num_menus_diarios
        FROM tipos_menu t
        LEFT JOIN puntos_venta pv ON t.punto_venta_id = pv.id
        WHERE t.empresa_id = ?
    """ + centro_sql + """
        ORDER BY t.nombre
    """, [empresa_id] + centro_params).fetchall()
    conn.close()

    return render_template('menus/tipos.html', tipos=tipos)


@menus_bp.route('/tipos/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_tipo_menu():
    conn = get_db()
    _ensure_menu_centros_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()

    if request.method == 'POST':
        nombre = request.form['nombre']
        punto_venta_id = request.form.get('punto_venta_id') or None
        if punto_venta_id:
            punto = conn.execute("""
                SELECT id
                FROM puntos_venta
                WHERE id = ?
                  AND empresa_id = ?
                  AND COALESCE(activo, 1) = 1
                  AND (? IS NULL OR centro_id = ?)
            """, (punto_venta_id, empresa_id, centro_id, centro_id)).fetchone()
            if not punto:
                conn.close()
                flash('Punto de venta no válido para este centro', 'danger')
                return redirect(url_for('menus.nuevo_tipo_menu'))

        conn.execute(
            "INSERT INTO tipos_menu (nombre, punto_venta_id, empresa_id, centro_id) VALUES (?, ?, ?, ?)",
            (nombre, punto_venta_id, empresa_id, centro_id)
        )
        conn.commit()
        conn.close()
        flash('Tipo de menú creado', 'success')
        return redirect(url_for('menus.lista_tipos_menu'))

    puntos_venta = conn.execute("""
        SELECT id, nombre
        FROM puntos_venta
        WHERE activo = 1
          AND empresa_id = ?
          AND (? IS NULL OR centro_id = ?)
        ORDER BY nombre
    """, (empresa_id, centro_id, centro_id)).fetchall()
    conn.close()

    return render_template('menus/nuevo_tipo.html', puntos_venta=puntos_venta)


@menus_bp.route('/tipos/<int:tipo_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_tipo_menu(tipo_id):
    conn = get_db()
    _ensure_menu_centros_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    centro_sql, centro_params = _menu_centro_sql("tm")
    tipo_actual = conn.execute("""
        SELECT tm.*
        FROM tipos_menu tm
        WHERE tm.id = ?
          AND tm.empresa_id = ?
        """ + centro_sql, [tipo_id, empresa_id] + centro_params).fetchone()
    if not tipo_actual:
        conn.close()
        flash('Tipo de menú no encontrado en este centro', 'danger')
        return redirect(url_for('menus.lista_tipos_menu'))

    if request.method == 'POST':
        nombre = request.form['nombre']
        punto_venta_id = request.form.get('punto_venta_id') or None
        activo = 1 if request.form.get('activo') else 0
        if punto_venta_id:
            punto = conn.execute("""
                SELECT id
                FROM puntos_venta
                WHERE id = ?
                  AND empresa_id = ?
                  AND COALESCE(activo, 1) = 1
                  AND (? IS NULL OR centro_id = ?)
            """, (punto_venta_id, empresa_id, tipo_actual["centro_id"], tipo_actual["centro_id"])).fetchone()
            if not punto:
                conn.close()
                flash('Punto de venta no válido para este centro', 'danger')
                return redirect(url_for('menus.editar_tipo_menu', tipo_id=tipo_id))

        conn.execute(
            "UPDATE tipos_menu SET nombre=?, punto_venta_id=?, activo=? WHERE id=? AND empresa_id=? AND COALESCE(centro_id, 0)=COALESCE(?, 0)",
            (nombre, punto_venta_id, activo, tipo_id, empresa_id, tipo_actual["centro_id"])
        )
        conn.commit()
        conn.close()
        flash('Tipo de menú actualizado', 'success')
        return redirect(url_for('menus.lista_tipos_menu'))

    tipo = tipo_actual
    puntos_venta = conn.execute("""
        SELECT id, nombre
        FROM puntos_venta
        WHERE activo = 1
          AND empresa_id = ?
          AND (? IS NULL OR centro_id = ?)
        ORDER BY nombre
    """, (empresa_id, tipo["centro_id"], tipo["centro_id"])).fetchall()
    conn.close()

    return render_template('menus/editar_tipo.html', tipo=tipo, puntos_venta=puntos_venta)


# =========================================================
# MENÚS FIJOS
# =========================================================
@menus_bp.route('/fijos')
@login_required
def lista_menus_fijos():
    conn = get_db()
    _ensure_menu_centros_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    centro_sql, centro_params = _menu_centro_sql("m")

    menus = conn.execute("""
        SELECT
            m.*,
            t.nombre as tipo_nombre,
            COUNT(DISTINCT mfs.id) as num_secciones,
            COUNT(DISTINCT mfp.id) as num_platos
        FROM menus m
        JOIN tipos_menu t ON m.tipo_menu_id = t.id
        LEFT JOIN menu_fijo_subdepartamentos mfs ON m.id = mfs.menu_id
        LEFT JOIN menu_fijo_platos mfp ON mfs.id = mfp.menu_fijo_subdepartamento_id
        WHERE m.empresa_id = ?
    """ + centro_sql + """
        GROUP BY m.id
        ORDER BY m.nombre
    """, [empresa_id] + centro_params).fetchall()

    conn.close()
    return render_template('menus/fijos.html', menus=menus)


@menus_bp.route('/fijos/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_menu_fijo():
    conn = get_db()
    _ensure_menu_centros_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)

    if request.method == 'POST':
        nombre = request.form['nombre'].strip()
        tipo_menu_id = request.form['tipo_menu_id']
        departamento_id = request.form.get('departamento_id') or None
        punto_venta_id = request.form.get('punto_venta_id') or None
        modo_servicio = request.form.get('modo_servicio', 'buffet')
        fecha_inicio = request.form.get('fecha_inicio') or None
        fecha_fin = request.form.get('fecha_fin') or None
        observaciones = request.form.get('observaciones', '')

        if fecha_inicio and fecha_fin and fecha_inicio > fecha_fin:
            flash('La fecha de inicio no puede ser posterior a la fecha fin.', 'warning')
            tipos = conn.execute(
                "SELECT id, nombre, punto_venta_id FROM tipos_menu WHERE activo = 1 AND empresa_id = ? ORDER BY nombre",
                (empresa_id,)
            ).fetchall()
            departamentos = conn.execute("SELECT id, nombre FROM departamentos WHERE activo = 1 AND empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
            puntos_venta = conn.execute("SELECT id, nombre, departamento_id FROM puntos_venta WHERE activo = 1 AND empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
            conn.close()
            return render_template(
                'menus/nuevo_menu_fijo.html',
                tipos=tipos,
                departamentos=departamentos,
                puntos_venta=puntos_venta,
                form_cache=request.form
            )

        cursor = conn.cursor()
        centro_id = asignar_centro_desde_departamento(conn, departamento_id)
        cursor.execute("""
            INSERT INTO menus (nombre, tipo_menu_id, departamento_id, punto_venta_id, modo_servicio, fecha_inicio, fecha_fin, observaciones, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (nombre, tipo_menu_id, departamento_id, punto_venta_id, modo_servicio, fecha_inicio, fecha_fin, observaciones, empresa_id, centro_id))

        menu_id = cursor.lastrowid
        conn.commit()
        conn.close()

        flash('Menú fijo creado', 'success')
        return redirect(url_for('menus.ver_menu_fijo', menu_id=menu_id))

    tipos = conn.execute(
        "SELECT id, nombre, punto_venta_id FROM tipos_menu WHERE activo = 1 AND empresa_id = ? ORDER BY nombre",
        (empresa_id,)
    ).fetchall()
    departamentos = conn.execute("SELECT id, nombre FROM departamentos WHERE activo = 1 AND empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    puntos_venta = conn.execute("SELECT id, nombre, departamento_id FROM puntos_venta WHERE activo = 1 AND empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    conn.close()

    return render_template('menus/nuevo_menu_fijo.html', tipos=tipos, departamentos=departamentos, puntos_venta=puntos_venta, form_cache={})


@menus_bp.route('/fijos/<int:menu_id>')
@login_required
def ver_menu_fijo(menu_id):
    conn = get_db()
    _ensure_menu_centros_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    centro_sql, centro_params = _menu_centro_sql("m")

    menu = conn.execute("""
        SELECT m.*, t.nombre as tipo_nombre
        FROM menus m
        JOIN tipos_menu t ON m.tipo_menu_id = t.id
        WHERE m.id = ? AND m.empresa_id = ?
        """ + centro_sql, [menu_id, empresa_id] + centro_params).fetchone()

    if not menu:
        conn.close()
        flash('Menú no encontrado', 'danger')
        return redirect(url_for('menus.lista_menus_fijos'))

    secciones = obtener_secciones_menu_fijo(conn, menu_id)
    recetas = conn.execute("""
        SELECT id, nombre
        FROM recetas
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND (? IS NULL OR COALESCE(centro_id, ?) = ?)
        ORDER BY nombre
    """, (empresa_id, menu["centro_id"], menu["centro_id"], menu["centro_id"])).fetchall()
    puntos_venta = conn.execute("""
        SELECT id, nombre
        FROM puntos_venta
        WHERE activo = 1
          AND empresa_id = ?
          AND (? IS NULL OR centro_id = ?)
        ORDER BY nombre
    """, (empresa_id, menu["centro_id"], menu["centro_id"])).fetchall()

    conn.close()
    return render_template('menus/ver_menu_fijo.html', menu=menu, secciones=secciones, recetas=recetas, puntos_venta=puntos_venta)


@menus_bp.route('/fijos/<int:menu_id>/publicar-tpv', methods=['POST'])
@login_required
def publicar_menu_fijo_tpv(menu_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    punto_venta_id = request.form.get('punto_venta_id') or ''
    precio_base = request.form.get('precio_base') or 0
    if not punto_venta_id.isdigit():
        conn.close()
        flash('Selecciona el punto de venta donde quieres publicar el menú fijo.', 'warning')
        return redirect(url_for('menus.ver_menu_fijo', menu_id=menu_id))
    menu = _menu_fijo_en_ambito(conn, menu_id, empresa_id)
    if not menu:
        conn.close()
        flash('Menú fijo no encontrado en este centro.', 'danger')
        return redirect(url_for('menus.lista_menus_fijos'))
    punto = conn.execute("""
        SELECT id, nombre
        FROM puntos_venta
        WHERE id = ?
          AND empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND (? IS NULL OR centro_id = ?)
    """, (int(punto_venta_id), empresa_id, menu["centro_id"], menu["centro_id"])).fetchone()
    if not punto:
        conn.close()
        flash('Punto de venta no encontrado en este centro.', 'danger')
        return redirect(url_for('menus.ver_menu_fijo', menu_id=menu_id))
    resultado = _publicar_menu_fijo_en_tpv(conn, empresa_id, menu_id, int(punto_venta_id), precio_base)
    if resultado.get("platos", 0) <= 0:
        conn.close()
        flash('No se publicaron platos. Revisa que el menú tenga recetas válidas para TPV.', 'warning')
        return redirect(url_for('menus.ver_menu_fijo', menu_id=menu_id))
    conn.commit()
    conn.close()
    flash(
        f"Menú fijo publicado en {punto['nombre']}. Nuevos: {resultado['publicados']}. Actualizados: {resultado['actualizados']}.",
        'success',
    )
    return redirect(url_for('tpv.catalogo', punto_venta_id=punto_venta_id))


@menus_bp.route('/fijos/<int:menu_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_menu_fijo(menu_id):
    conn = get_db()
    _ensure_menu_centros_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)

    menu = _menu_fijo_en_ambito(conn, menu_id, empresa_id)

    if not menu:
        conn.close()
        flash('Menú no encontrado en este centro', 'danger')
        return redirect(url_for('menus.lista_menus_fijos'))

    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        tipo_menu_id = request.form.get('tipo_menu_id')
        fecha_inicio = request.form.get('fecha_inicio')
        fecha_fin = request.form.get('fecha_fin')
        observaciones = request.form.get('observaciones', '')
        activo = 1 if request.form.get('activo') else 0
        tipo = conn.execute("""
            SELECT id
            FROM tipos_menu
            WHERE id = ?
              AND activo = 1
              AND empresa_id = ?
              AND (? IS NULL OR COALESCE(centro_id, ?) = ?)
        """, (tipo_menu_id, empresa_id, menu["centro_id"], menu["centro_id"], menu["centro_id"])).fetchone()
        if not tipo:
            conn.close()
            flash('Tipo de menú no válido para este centro', 'danger')
            return redirect(url_for('menus.editar_menu_fijo', menu_id=menu_id))

        conn.execute("""
            UPDATE menus
            SET nombre = ?, tipo_menu_id = ?, fecha_inicio = ?, fecha_fin = ?, observaciones = ?, activo = ?
            WHERE id = ? AND empresa_id = ?
        """, (
            nombre,
            tipo_menu_id,
            fecha_inicio,
            fecha_fin,
            observaciones,
            activo,
            menu_id,
            empresa_id
        ))

        conn.commit()
        conn.close()
        flash('Cabecera del menú fijo actualizada', 'success')
        return redirect(url_for('menus.ver_menu_fijo', menu_id=menu_id))

    tipos = conn.execute("""
        SELECT id, nombre
        FROM tipos_menu
        WHERE activo = 1 AND empresa_id = ?
          AND (? IS NULL OR COALESCE(centro_id, ?) = ?)
        ORDER BY nombre
    """, (empresa_id, menu["centro_id"], menu["centro_id"], menu["centro_id"])).fetchall()

    conn.close()
    return render_template('menus/editar_menu_fijo.html', menu=menu, tipos=tipos)


@menus_bp.route('/fijos/<int:menu_id>/secciones/nueva', methods=['GET', 'POST'])
@login_required
def nueva_seccion_menu_fijo(menu_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    menu = _menu_fijo_en_ambito(conn, menu_id, empresa_id)

    if not menu:
        conn.close()
        flash('Menú fijo no encontrado en este centro', 'danger')
        return redirect(url_for('menus.lista_menus_fijos'))

    if request.method == 'POST':
        subdepartamento_id = request.form['subdepartamento_id']
        seccion = request.form.get('seccion', '')
        orden = request.form.get('orden', 0)
        sub_sql, sub_params = _subdepartamentos_centro_sql(conn, empresa_id, "s")
        subdepto = conn.execute("""
            SELECT s.id
            FROM subdepartamentos s
            WHERE s.id = ?
              AND s.activo = 1
              AND s.empresa_id = ?
        """ + sub_sql, [subdepartamento_id, empresa_id] + sub_params).fetchone()
        if not subdepto:
            conn.close()
            flash('Subdepartamento no válido para este centro', 'danger')
            return redirect(url_for('menus.ver_menu_fijo', menu_id=menu_id))

        conn.execute("""
            INSERT INTO menu_fijo_subdepartamentos (menu_id, subdepartamento_id, seccion, orden)
            VALUES (?, ?, ?, ?)
        """, (menu_id, subdepartamento_id, seccion, orden))
        conn.commit()
        conn.close()

        flash('Sección añadida al menú fijo', 'success')
        return redirect(url_for('menus.ver_menu_fijo', menu_id=menu_id))

    sub_sql, sub_params = _subdepartamentos_centro_sql(conn, empresa_id, "s")
    subdeptos = conn.execute("""
        SELECT s.*, d.nombre as departamento_nombre
        FROM subdepartamentos s
        JOIN departamentos d ON s.departamento_id = d.id
        WHERE s.activo = 1 AND s.empresa_id = ?
        """ + sub_sql + """
        ORDER BY d.nombre, s.nombre
    """, [empresa_id] + sub_params).fetchall()

    conn.close()
    return render_template('menus/nueva_seccion_fijo.html', menu_id=menu_id, subdeptos=subdeptos)


@menus_bp.route('/fijos/secciones/<int:seccion_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_seccion_menu_fijo(seccion_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_sql, centro_params = _menu_centro_sql("m")

    seccion = conn.execute("""
        SELECT
            mfs.*,
            s.nombre as subdepartamento_nombre,
            m.empresa_id
        FROM menu_fijo_subdepartamentos mfs
        JOIN subdepartamentos s ON mfs.subdepartamento_id = s.id
        JOIN menus m ON mfs.menu_id = m.id
        WHERE mfs.id = ? AND m.empresa_id = ?
        """ + centro_sql, [seccion_id, empresa_id] + centro_params).fetchone()

    if not seccion:
        conn.close()
        flash('Sección no encontrada', 'danger')
        return redirect(url_for('menus.lista_menus_fijos'))

    if request.method == 'POST':
        nombre_seccion = request.form.get('seccion', '')
        orden = request.form.get('orden', 0)

        conn.execute("""
            UPDATE menu_fijo_subdepartamentos
            SET seccion = ?, orden = ?
            WHERE id = ?
        """, (nombre_seccion, orden, seccion_id))
        conn.commit()
        conn.close()

        flash('Sección del menú fijo actualizada', 'success')
        return redirect(url_for('menus.ver_menu_fijo', menu_id=seccion['menu_id']))

    conn.close()
    return render_template('menus/editar_seccion_fijo.html', seccion=seccion)


@menus_bp.route('/fijos/secciones/<int:seccion_id>/eliminar', methods=['POST'])
@login_required
def eliminar_seccion_menu_fijo(seccion_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    seccion = _seccion_menu_fijo_en_ambito(conn, seccion_id, empresa_id)

    if not seccion:
        conn.close()
        flash('Sección no encontrada', 'danger')
        return redirect(url_for('menus.lista_menus_fijos'))

    conn.execute("""
        DELETE FROM menu_fijo_platos
        WHERE menu_fijo_subdepartamento_id = ?
    """, (seccion_id,))

    conn.execute("""
        DELETE FROM menu_fijo_subdepartamentos
        WHERE id = ?
    """, (seccion_id,))

    conn.commit()
    conn.close()

    flash('Sección del menú fijo eliminada', 'success')
    return redirect(url_for('menus.ver_menu_fijo', menu_id=seccion['menu_id']))


@menus_bp.route('/fijos/secciones/<int:seccion_id>/platos/nuevo', methods=['POST'])
@login_required
def nuevo_plato_menu_fijo(seccion_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    seccion = _seccion_menu_fijo_en_ambito(conn, seccion_id, empresa_id)

    if not seccion:
        conn.close()
        flash('Sección no encontrada', 'danger')
        return redirect(url_for('menus.lista_menus_fijos'))

    receta_id = request.form.get('receta_id')
    nombre_plato = request.form.get('nombre_plato', '').strip()
    orden = request.form.get('orden', 0)

    if not receta_id and nombre_plato:
        receta_existente = conn.execute("""
            SELECT id
            FROM recetas
            WHERE LOWER(TRIM(nombre)) = LOWER(TRIM(?))
              AND empresa_id = ?
              AND COALESCE(centro_id, ?) = ?
            LIMIT 1
        """, (nombre_plato, empresa_id, seccion["centro_id"], seccion["centro_id"])).fetchone()

        if receta_existente:
            receta_id = receta_existente['id']
        else:
            columnas_info = conn.execute("PRAGMA table_info(recetas)").fetchall()
            columnas = [col["name"] for col in columnas_info]

            datos = {}

            if "nombre" in columnas:
                datos["nombre"] = nombre_plato
            if "descripcion" in columnas:
                datos["descripcion"] = ""
            if "alergenos" in columnas:
                datos["alergenos"] = ""
            if "categoria" in columnas:
                datos["categoria"] = ""
            if "activo" in columnas:
                datos["activo"] = 1
            if "empresa_id" in columnas:
                datos["empresa_id"] = empresa_id
            if "centro_id" in columnas:
                datos["centro_id"] = seccion["centro_id"]

            cols = ", ".join(datos.keys())
            vals = ", ".join(["?"] * len(datos))

            cursor = conn.cursor()
            cursor.execute(
                f"INSERT INTO recetas ({cols}) VALUES ({vals})",
                list(datos.values())
            )
            receta_id = cursor.lastrowid

    if not receta_id:
        conn.close()
        flash('Debes seleccionar una receta o escribir el nombre del plato', 'danger')
        return redirect(url_for('menus.ver_menu_fijo', menu_id=seccion['menu_id']))

    receta = conn.execute("""
        SELECT id
        FROM recetas
        WHERE id = ?
          AND empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND COALESCE(centro_id, ?) = ?
    """, (receta_id, empresa_id, seccion["centro_id"], seccion["centro_id"])).fetchone()
    if not receta:
        conn.close()
        flash('Receta no válida para este centro', 'danger')
        return redirect(url_for('menus.ver_menu_fijo', menu_id=seccion['menu_id']))

    conn.execute("""
        INSERT INTO menu_fijo_platos (menu_fijo_subdepartamento_id, receta_id, orden)
        VALUES (?, ?, ?)
    """, (seccion_id, receta_id, orden))

    conn.commit()
    conn.close()

    flash('Plato añadido al menú fijo', 'success')
    return redirect(url_for('menus.ver_menu_fijo', menu_id=seccion['menu_id']))


@menus_bp.route('/fijos/platos/<int:plato_id>/eliminar', methods=['POST'])
@login_required
def eliminar_plato_menu_fijo(plato_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    plato = _plato_menu_fijo_en_ambito(conn, plato_id, empresa_id)

    if not plato:
        conn.close()
        flash('Plato no encontrado', 'danger')
        return redirect(url_for('menus.lista_menus_fijos'))

    conn.execute("""
        DELETE FROM menu_fijo_platos
        WHERE id = ?
    """, (plato_id,))

    conn.commit()
    conn.close()

    flash('Plato eliminado del menú fijo', 'success')
    return redirect(url_for('menus.ver_menu_fijo', menu_id=plato['menu_id']))


@menus_bp.route('/fijos/<int:menu_id>/imprimir')
@login_required
def imprimir_menu_fijo(menu_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    menu = conn.execute("""
        SELECT m.*, t.nombre as tipo_nombre
        FROM menus m
        JOIN tipos_menu t ON m.tipo_menu_id = t.id
        WHERE m.id = ? AND m.empresa_id = ?
    """, (menu_id, empresa_id)).fetchone()

    if not menu:
        conn.close()
        flash('Menú no encontrado', 'danger')
        return redirect(url_for('menus.lista_menus_fijos'))

    secciones = obtener_secciones_menu_fijo(conn, menu_id)
    conn.close()

    return render_template('menus/imprimir_menu_fijo.html', menu=menu, secciones=secciones, now=datetime.now)


# =========================================================
# MENÚS DIARIOS
# =========================================================
@menus_bp.route('/')
@menus_bp.route('/diarios')
@login_required
def lista_menus_principal():
    conn = get_db()
    _ensure_menu_centros_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)

    busqueda = request.args.get('q', '').strip().lower()
    tipo_id = request.args.get('tipo', '')
    servicio = request.args.get('servicio', '')

    query = """
        SELECT m.*, t.nombre as tipo_nombre,
               COUNT(DISTINCT ms.id) as num_secciones,
               COUNT(DISTINCT mp.id) as num_platos
        FROM menus_diarios m
        JOIN tipos_menu t ON m.tipo_menu_id = t.id
        LEFT JOIN menu_subdepartamentos ms ON m.id = ms.menu_diario_id
        LEFT JOIN menu_platos mp ON ms.id = mp.menu_subdepartamento_id
        WHERE m.empresa_id = ?
    """

    params = [empresa_id]
    centro_sql, centro_params = _menu_centro_sql("m")
    query += centro_sql
    params.extend(centro_params)

    if tipo_id:
        query += " AND m.tipo_menu_id = ?"
        params.append(tipo_id)

    if servicio:
        query += " AND LOWER(t.nombre) = ?"
        params.append(servicio.lower())

    if busqueda:
        query += " AND (LOWER(m.fecha) LIKE ? OR LOWER(m.observaciones) LIKE ?)"
        params.append(f"%{busqueda}%")
        params.append(f"%{busqueda}%")

    query += """
        GROUP BY m.id
        ORDER BY
            CASE
                WHEN m.fecha LIKE 'SEMANA 1%' THEN 1
                WHEN m.fecha LIKE 'SEMANA 2%' THEN 2
                WHEN m.fecha LIKE 'SEMANA 3%' THEN 3
                WHEN m.fecha LIKE 'SEMANA 4%' THEN 4
                ELSE 99
            END,
            CASE
                WHEN m.fecha LIKE '%LUNES%' THEN 1
                WHEN m.fecha LIKE '%MARTES%' THEN 2
                WHEN m.fecha LIKE '%MIERCOLES%' OR m.fecha LIKE '%MIÉRCOLES%' THEN 3
                WHEN m.fecha LIKE '%JUEVES%' THEN 4
                WHEN m.fecha LIKE '%VIERNES%' THEN 5
                WHEN m.fecha LIKE '%SABADO%' OR m.fecha LIKE '%SÁBADO%' THEN 6
                WHEN m.fecha LIKE '%DOMINGO%' THEN 7
                ELSE 99
            END,
            CASE
                WHEN LOWER(t.nombre) = 'desayuno' THEN 1
                WHEN LOWER(t.nombre) = 'snack' THEN 2
                WHEN LOWER(t.nombre) = 'almuerzo' THEN 3
                WHEN LOWER(t.nombre) = 'cena' THEN 4
                ELSE 99
            END,
            m.fecha,
            t.nombre
    """

    menus = conn.execute(query, params).fetchall()

    tipos_centro_sql, tipos_centro_params = _menu_centro_sql("tm")
    tipos = conn.execute("""
        SELECT id, nombre
        FROM tipos_menu tm
        WHERE activo = 1 AND empresa_id = ?
    """ + tipos_centro_sql + """
        ORDER BY nombre
    """, [empresa_id] + tipos_centro_params).fetchall()

    conn.close()

    return render_template(
        'menus/menus.html',
        menus=menus,
        tipos=tipos,
        busqueda=busqueda,
        tipo_id=tipo_id,
        servicio=servicio
    )


@menus_bp.route('/diarios/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_menu_diario():
    conn = get_db()
    _ensure_menu_centros_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)

    if request.method == 'POST':
        fecha = request.form['fecha']
        tipo_menu_id = request.form['tipo_menu_id']
        departamento_id = request.form.get('departamento_id')
        punto_venta_id = request.form.get('punto_venta_id')
        modo_servicio = request.form.get('modo_servicio', 'buffet')
        observaciones = request.form.get('observaciones', '')

        centro_id = asignar_centro_desde_departamento(conn, departamento_id)
        existente = conn.execute(
            "SELECT id FROM menus_diarios WHERE fecha = ? AND tipo_menu_id = ? AND empresa_id = ? AND centro_id = ?",
            (fecha, tipo_menu_id, empresa_id, centro_id)
        ).fetchone()

        if existente:
            conn.close()
            flash('Ya existe un menú para esa fecha y tipo. Redirigiendo a la edición.', 'info')
            return redirect(url_for('menus.ver_menu_diario', menu_id=existente['id']))

        cursor = conn.execute("""
            INSERT INTO menus_diarios (fecha, tipo_menu_id, departamento_id, punto_venta_id, modo_servicio, observaciones, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (fecha, tipo_menu_id, departamento_id, punto_venta_id, modo_servicio, observaciones, empresa_id, centro_id))

        conn.commit()
        menu_id = cursor.lastrowid
        conn.close()

        flash('Menú diario creado correctamente', 'success')
        return redirect(url_for('menus.ver_menu_diario', menu_id=menu_id))

    fecha_seleccionada = request.args.get('fecha', '')
    tipo_seleccionado = request.args.get('tipo', '')
    tipos_centro_sql, tipos_centro_params = _menu_centro_sql("tipos_menu")
    tipos = conn.execute(
        "SELECT id, nombre FROM tipos_menu WHERE activo = 1 AND empresa_id = ?"
        + tipos_centro_sql +
        " ORDER BY nombre",
        [empresa_id] + tipos_centro_params
    ).fetchall()
    conn.close()

    return render_template(
        'menus/nuevo_menu_diario.html',
        tipos=tipos,
        fecha_seleccionada=fecha_seleccionada,
        tipo_seleccionado=tipo_seleccionado
    )


@menus_bp.route('/<int:menu_id>/secciones/nueva', methods=['GET', 'POST'])
@login_required
def nueva_seccion_menu(menu_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    menu = _menu_diario_en_ambito(conn, menu_id, empresa_id)
    if not menu:
        conn.close()
        flash('Menú no encontrado en este centro', 'danger')
        return redirect(url_for('menus.lista_menus_principal'))

    if request.method == 'POST':
        subdepartamento_id = request.form['subdepartamento_id']
        seccion = request.form.get('seccion', '')
        orden = request.form.get('orden', 0)
        sub_sql, sub_params = _subdepartamentos_centro_sql(conn, empresa_id, "s")
        subdepto = conn.execute("""
            SELECT s.id
            FROM subdepartamentos s
            WHERE s.id = ?
              AND s.activo = 1
              AND s.empresa_id = ?
        """ + sub_sql, [subdepartamento_id, empresa_id] + sub_params).fetchone()
        if not subdepto:
            conn.close()
            flash('Subdepartamento no válido para este centro', 'danger')
            return redirect(url_for('menus.ver_menu_diario', menu_id=menu_id))

        conn.execute("""
            INSERT INTO menu_subdepartamentos (menu_diario_id, subdepartamento_id, seccion, orden)
            VALUES (?, ?, ?, ?)
        """, (menu_id, subdepartamento_id, seccion, orden))
        conn.commit()
        conn.close()

        flash('Sección añadida al menú', 'success')
        return redirect(url_for('menus.ver_menu_diario', menu_id=menu_id))

    sub_sql, sub_params = _subdepartamentos_centro_sql(conn, empresa_id, "s")
    subdeptos = conn.execute("""
        SELECT s.*, d.nombre as departamento_nombre
        FROM subdepartamentos s
        JOIN departamentos d ON s.departamento_id = d.id
        WHERE s.activo = 1 AND s.empresa_id = ?
        """ + sub_sql + """
        ORDER BY d.nombre, s.nombre
    """, [empresa_id] + sub_params).fetchall()
    conn.close()

    return render_template('menus/nueva_seccion.html', menu_id=menu_id, subdeptos=subdeptos)


@menus_bp.route('/secciones/<int:seccion_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_seccion_menu(seccion_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    seccion_scope = _seccion_menu_en_ambito(conn, seccion_id, empresa_id)
    if not seccion_scope:
        conn.close()
        flash('Sección no encontrada en este centro', 'danger')
        return redirect(url_for('menus.lista_menus_principal'))

    if request.method == 'POST':
        seccion = request.form.get('seccion', '')
        orden = request.form.get('orden', 0)

        conn.execute("""
            UPDATE menu_subdepartamentos
            SET seccion = ?, orden = ?
            WHERE id = ?
        """, (seccion, orden, seccion_id))
        conn.commit()

        menu_id = seccion_scope['menu_diario_id']
        conn.close()

        flash('Sección actualizada', 'success')
        return redirect(url_for('menus.ver_menu_diario', menu_id=menu_id))

    seccion = conn.execute("""
        SELECT ms.*, s.nombre as subdepartamento_nombre
        FROM menu_subdepartamentos ms
        JOIN subdepartamentos s ON ms.subdepartamento_id = s.id
        WHERE ms.id = ?
    """, (seccion_id,)).fetchone()
    conn.close()

    return render_template('menus/editar_seccion.html', seccion=seccion)


@menus_bp.route('/secciones/<int:seccion_id>/eliminar', methods=['POST'])
@login_required
def eliminar_seccion_menu(seccion_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    seccion = _seccion_menu_en_ambito(conn, seccion_id, empresa_id)
    if not seccion:
        conn.close()
        flash('Sección no encontrada en este centro', 'danger')
        return redirect(url_for('menus.lista_menus_principal'))
    menu_id = seccion['menu_diario_id']

    conn.execute("DELETE FROM menu_platos WHERE menu_subdepartamento_id = ?", (seccion_id,))
    conn.execute("DELETE FROM menu_subdepartamentos WHERE id = ?", (seccion_id,))
    conn.commit()
    conn.close()

    flash('Sección eliminada', 'success')
    return redirect(url_for('menus.ver_menu_diario', menu_id=menu_id))


@menus_bp.route('/secciones/<int:seccion_id>/platos/nuevo', methods=['POST'])
@login_required
def nuevo_plato_menu(seccion_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    seccion = _seccion_menu_en_ambito(conn, seccion_id, empresa_id)
    if not seccion:
        conn.close()
        flash('Sección no encontrada en este centro', 'danger')
        return redirect(url_for('menus.lista_menus_principal'))

    receta_id = request.form.get('receta_id')
    nombre_plato = request.form.get('nombre_plato', '').strip()
    orden = request.form.get('orden', 0)

    if not receta_id and nombre_plato:
        receta_existente = conn.execute("""
            SELECT id
            FROM recetas
            WHERE LOWER(TRIM(nombre)) = LOWER(TRIM(?))
              AND empresa_id = ?
              AND COALESCE(centro_id, ?) = ?
            LIMIT 1
        """, (nombre_plato, empresa_id, seccion["centro_id"], seccion["centro_id"])).fetchone()

        if receta_existente:
            receta_id = receta_existente['id']
        else:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO recetas (nombre, activo, empresa_id, centro_id)
                VALUES (?, 1, ?, ?)
            """, (nombre_plato, empresa_id, seccion["centro_id"]))
            receta_id = cursor.lastrowid

    if not receta_id:
        conn.close()
        flash('Debes seleccionar una receta o escribir el nombre del plato', 'danger')
        return redirect(url_for('menus.ver_menu_diario', menu_id=seccion['menu_diario_id']))

    receta = conn.execute("""
        SELECT id
        FROM recetas
        WHERE id = ?
          AND empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND COALESCE(centro_id, ?) = ?
    """, (receta_id, empresa_id, seccion["centro_id"], seccion["centro_id"])).fetchone()
    if not receta:
        conn.close()
        flash('Receta no válida para este centro', 'danger')
        return redirect(url_for('menus.ver_menu_diario', menu_id=seccion['menu_diario_id']))

    conn.execute("""
        INSERT INTO menu_platos (menu_subdepartamento_id, receta_id, orden)
        VALUES (?, ?, ?)
    """, (seccion_id, receta_id, orden))

    conn.commit()

    menu_id = seccion['menu_diario_id']

    conn.close()

    flash('Plato añadido al menú', 'success')
    return redirect(url_for('menus.ver_menu_diario', menu_id=menu_id))


@menus_bp.route('/platos/<int:plato_id>/eliminar', methods=['POST'])
@login_required
def eliminar_plato_menu(plato_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    plato = _plato_menu_en_ambito(conn, plato_id, empresa_id)

    if plato:
        menu_id = plato['menu_diario_id']
        conn.execute("DELETE FROM menu_platos WHERE id = ?", (plato_id,))
        conn.commit()
        flash('Plato eliminado', 'success')
    else:
        flash('Plato no encontrado', 'danger')
        conn.close()
        return redirect(url_for('menus.lista_menus_principal'))

    conn.close()
    return redirect(url_for('menus.ver_menu_diario', menu_id=menu_id))


@menus_bp.route('/diarios/<int:menu_id>')
@login_required
def ver_menu_diario(menu_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_sql, centro_params = _menu_centro_sql("m")

    menu = conn.execute("""
        SELECT m.*, t.nombre as tipo_nombre
        FROM menus_diarios m
        JOIN tipos_menu t ON m.tipo_menu_id = t.id
        WHERE m.id = ? AND m.empresa_id = ?
        """ + centro_sql, [menu_id, empresa_id] + centro_params).fetchone()

    if not menu:
        conn.close()
        flash('Menú no encontrado', 'danger')
        return redirect(url_for('menus.lista_menus_principal'))

    secciones = conn.execute("""
        SELECT
            ms.*,
            s.nombre as subdepartamento_nombre,
            d.nombre as departamento_nombre
        FROM menu_subdepartamentos ms
        JOIN subdepartamentos s ON ms.subdepartamento_id = s.id
        JOIN departamentos d ON s.departamento_id = d.id
        WHERE ms.menu_diario_id = ?
        ORDER BY
            CASE
                WHEN LOWER(TRIM(s.nombre)) = 'cuarto frío' OR LOWER(TRIM(s.nombre)) = 'cuarto frio' THEN 1
                WHEN LOWER(TRIM(s.nombre)) = 'cocina caliente' THEN 2
                WHEN LOWER(TRIM(s.nombre)) = 'postres' THEN 3
                WHEN LOWER(TRIM(s.nombre)) = 'desayunos' THEN 1
                WHEN LOWER(TRIM(s.nombre)) = 'snack' THEN 1
                ELSE 9
            END,
            ms.orden,
            LOWER(TRIM(ms.seccion))
    """, (menu_id,)).fetchall()

    secciones_con_platos = []

    for s in secciones:
        platos = conn.execute("""
            SELECT mp.*, r.nombre as receta_nombre
            FROM menu_platos mp
            JOIN recetas r ON mp.receta_id = r.id
            WHERE mp.menu_subdepartamento_id = ?
            ORDER BY mp.orden, LOWER(TRIM(r.nombre))
        """, (s['id'],)).fetchall()

        seccion_dict = dict(s)
        seccion_dict.update({'platos': platos})
        secciones_con_platos.append(seccion_dict)

    recetas = conn.execute("""
        SELECT id, nombre
        FROM recetas
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND COALESCE(centro_id, ?) = ?
        ORDER BY nombre
    """, (empresa_id, menu["centro_id"], menu["centro_id"])).fetchall()
    conn.close()

    return render_template('menus/ver_dia.html', menu=menu, secciones=secciones_con_platos, recetas=recetas)


@menus_bp.route('/diarios/<int:menu_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_menu_diario(menu_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    menu_scope = _menu_diario_en_ambito(conn, menu_id, empresa_id)
    if not menu_scope:
        conn.close()
        flash('Menú no encontrado en este centro', 'danger')
        return redirect(url_for('menus.lista_menus_principal'))

    if request.method == 'POST':
        fecha = request.form['fecha']
        tipo_menu_id = request.form['tipo_menu_id']
        observaciones = request.form.get('observaciones', '')
        tipo = conn.execute("""
            SELECT id
            FROM tipos_menu
            WHERE id = ?
              AND activo = 1
              AND empresa_id = ?
              AND COALESCE(centro_id, ?) = ?
        """, (tipo_menu_id, empresa_id, menu_scope["centro_id"], menu_scope["centro_id"])).fetchone()
        if not tipo:
            conn.close()
            flash('Tipo de menú no válido para este centro', 'danger')
            return redirect(url_for('menus.editar_menu_diario', menu_id=menu_id))

        conn.execute(
            "UPDATE menus_diarios SET fecha=?, tipo_menu_id=?, observaciones=? WHERE id=? AND empresa_id=?",
            (fecha, tipo_menu_id, observaciones, menu_id, empresa_id)
        )
        conn.commit()
        conn.close()

        flash('Menú diario actualizado', 'success')
        return redirect(url_for('menus.ver_menu_diario', menu_id=menu_id))

    menu = menu_scope
    tipos = conn.execute("""
        SELECT id, nombre
        FROM tipos_menu
        WHERE activo=1
          AND empresa_id=?
          AND COALESCE(centro_id, ?) = ?
        ORDER BY nombre
    """, (empresa_id, menu["centro_id"], menu["centro_id"])).fetchall()
    recetas = conn.execute("""
        SELECT id, nombre
        FROM recetas
        WHERE empresa_id=?
          AND COALESCE(activo, 1) = 1
          AND COALESCE(centro_id, ?) = ?
        ORDER BY nombre
    """, (empresa_id, menu["centro_id"], menu["centro_id"])).fetchall()
    conn.close()

    return render_template('menus/editar_menu_diario.html', menu=menu, tipos=tipos, recetas=recetas)


@menus_bp.route('/diarios/<int:menu_id>/imprimir')
@login_required
def imprimir_menu_diario(menu_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    menu = conn.execute("""
        SELECT m.*, t.nombre as tipo_nombre
        FROM menus_diarios m
        JOIN tipos_menu t ON m.tipo_menu_id = t.id
        WHERE m.id = ? AND m.empresa_id = ?
    """, (menu_id, empresa_id)).fetchone()

    if not menu:
        conn.close()
        flash('Menú no encontrado', 'danger')
        return redirect(url_for('menus.lista_menus_principal'))

    secciones = conn.execute("""
        SELECT
            ms.*,
            s.nombre as subdepartamento_nombre,
            d.nombre as departamento_nombre
        FROM menu_subdepartamentos ms
        JOIN subdepartamentos s ON ms.subdepartamento_id = s.id
        JOIN departamentos d ON s.departamento_id = d.id
        WHERE ms.menu_diario_id = ?
        ORDER BY
            CASE
                WHEN LOWER(TRIM(s.nombre)) = 'cuarto frío' OR LOWER(TRIM(s.nombre)) = 'cuarto frio' THEN 1
                WHEN LOWER(TRIM(s.nombre)) = 'cocina caliente' THEN 2
                WHEN LOWER(TRIM(s.nombre)) = 'postres' THEN 3
                WHEN LOWER(TRIM(s.nombre)) = 'desayunos' THEN 1
                WHEN LOWER(TRIM(s.nombre)) = 'snack' THEN 1
                ELSE 9
            END,
            ms.orden,
            LOWER(TRIM(ms.seccion))
    """, (menu_id,)).fetchall()

    secciones_con_platos = []

    for s in secciones:
        platos = conn.execute("""
            SELECT mp.*, r.nombre as receta_nombre
            FROM menu_platos mp
            JOIN recetas r ON mp.receta_id = r.id
            WHERE mp.menu_subdepartamento_id = ?
            ORDER BY mp.orden, LOWER(TRIM(r.nombre))
        """, (s['id'],)).fetchall()

        seccion_dict = dict(s)
        seccion_dict['platos'] = platos
        secciones_con_platos.append(seccion_dict)

    conn.close()
    return render_template('menus/imprimir_menu.html', menu=menu, secciones=secciones_con_platos, now=datetime.now)


@menus_bp.route('/diarios/<int:menu_id>/eliminar', methods=['POST'])
@login_required
def eliminar_menu_diario(menu_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    menu = conn.execute(
        "SELECT id FROM menus_diarios WHERE id=? AND empresa_id=?",
        (menu_id, empresa_id)
    ).fetchone()

    if not menu:
        conn.close()
        flash('Menú no encontrado', 'danger')
        return redirect(url_for('menus.lista_menus_principal'))

    subdeptos = conn.execute(
        "SELECT id FROM menu_subdepartamentos WHERE menu_diario_id=?",
        (menu_id,)
    ).fetchall()

    for s in subdeptos:
        conn.execute("DELETE FROM menu_platos WHERE menu_subdepartamento_id=?", (s['id'],))

    conn.execute("DELETE FROM menu_subdepartamentos WHERE menu_diario_id=?", (menu_id,))
    conn.execute("DELETE FROM menus_diarios WHERE id=?", (menu_id,))
    conn.commit()
    conn.close()

    flash('Menú diario eliminado', 'success')
    return redirect(url_for('menus.lista_menus_principal'))


# =========================================================
# VISTA SEMANAL MENÚS
# =========================================================
@menus_bp.route('/semanal')
@login_required
def vista_semanal_menus():
    conn = get_db()
    _ensure_menu_centros_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    centro_sql_tipos, centro_params_tipos = _menu_centro_sql("tipos_menu")

    fecha_str = request.args.get('fecha', '')

    if fecha_str:
        try:
            fecha_dt = datetime.strptime(fecha_str, '%Y-%m-%d')
        except ValueError:
            flash('Formato de fecha inválido. Usando fecha actual.', 'warning')
            fecha_dt = datetime.now()
    else:
        fecha_dt = datetime.now()

    inicio_semana = fecha_dt - timedelta(days=fecha_dt.weekday())
    fin_semana = inicio_semana + timedelta(days=6)

    dias_semana = []
    for i in range(7):
        dia = inicio_semana + timedelta(days=i)
        dias_semana.append({
            'fecha': dia.strftime('%Y-%m-%d'),
            'nombre': dia.strftime('%A').capitalize()
        })

    tipos = conn.execute(
        "SELECT id, nombre FROM tipos_menu WHERE activo = 1 AND empresa_id = ?"
        + centro_sql_tipos +
        " ORDER BY nombre",
        [empresa_id] + centro_params_tipos
    ).fetchall()

    sub_sql, sub_params = _subdepartamentos_centro_sql(conn, empresa_id, "s")
    subdeptos = conn.execute("""
        SELECT s.id, s.nombre, d.nombre as departamento_nombre
        FROM subdepartamentos s
        JOIN departamentos d ON s.departamento_id = d.id
        WHERE s.activo = 1 AND s.empresa_id = ?
    """ + sub_sql + """
        ORDER BY d.nombre, s.nombre
    """, [empresa_id] + sub_params).fetchall()

    planificacion = {}
    for dia in dias_semana:
        planificacion[dia['fecha']] = {}
        for tipo in tipos:
            centro_sql_ps, centro_params_ps = _menu_centro_sql("ps")
            menus_asignados = conn.execute("""
                SELECT
                    ps.id,
                    ps.menu_diario_id,
                    ps.orden,
                    ps.subdepartamento_id,
                    md.observaciones,
                    s.id as subdepartamento_id_real,
                    s.nombre as subdepartamento_nombre,
                    (SELECT COUNT(*) FROM menu_platos mp
                     JOIN menu_subdepartamentos ms ON mp.menu_subdepartamento_id = ms.id
                     WHERE ms.menu_diario_id = md.id) as num_platos
                FROM planificacion_servicios ps
                JOIN menus_diarios md ON ps.menu_diario_id = md.id
                LEFT JOIN subdepartamentos s ON ps.subdepartamento_id = s.id
                WHERE ps.fecha = ? AND ps.tipo_menu_id = ? AND ps.empresa_id = ?
            """ + centro_sql_ps + """
                ORDER BY ps.orden
            """, [dia['fecha'], tipo['id'], empresa_id] + centro_params_ps).fetchall()

            planificacion[dia['fecha']][tipo['id']] = menus_asignados

    centro_sql_md, centro_params_md = _menu_centro_sql("md")
    menus_disponibles = conn.execute("""
        SELECT md.*, t.nombre as tipo_nombre,
            (SELECT COUNT(*) FROM menu_platos mp
             JOIN menu_subdepartamentos ms ON mp.menu_subdepartamento_id = ms.id
             WHERE ms.menu_diario_id = md.id) as num_platos
        FROM menus_diarios md
        JOIN tipos_menu t ON md.tipo_menu_id = t.id
        WHERE md.empresa_id = ?
    """ + centro_sql_md + """
        ORDER BY md.fecha DESC
    """, [empresa_id] + centro_params_md).fetchall()

    conn.close()

    return render_template(
        'menus/semanal.html',
        dias_semana=dias_semana,
        tipos=tipos,
        subdeptos=subdeptos,
        planificacion=planificacion,
        menus_disponibles=menus_disponibles,
        semana_inicio=inicio_semana.strftime('%Y-%m-%d'),
        semana_fin=fin_semana.strftime('%Y-%m-%d')
    )


@menus_bp.route('/semanal/anadir', methods=['POST'])
@login_required
def anadir_menu_a_semanal():
    fecha = request.form.get('fecha')
    menu_diario_id = request.form.get('menu_diario_id')
    subdepartamento_id = request.form.get('subdepartamento_id')

    if not all([fecha, menu_diario_id]):
        flash('Faltan parámetros', 'danger')
        return redirect(url_for('menus.vista_semanal_menus', fecha=fecha))

    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    guardado = _guardar_planificacion_servicio(
        conn,
        empresa_id,
        fecha,
        menu_diario_id,
        subdepartamento_id=subdepartamento_id,
    )
    if not guardado:
        flash('Menú no encontrado', 'danger')
        conn.close()
        return redirect(url_for('menus.vista_semanal_menus', fecha=fecha))

    conn.commit()
    conn.close()

    flash('Menú añadido a la planificación', 'success')
    return redirect(url_for('menus.vista_semanal_menus', fecha=fecha))


@menus_bp.route('/semanal/aplicar-ciclo', methods=['POST'])
@login_required
def aplicar_ciclo_menus():
    fecha_inicio = request.form.get('fecha_inicio') or request.form.get('fecha')
    ciclo_dias = int(request.form.get('ciclo_dias') or 14)
    semanas = int(request.form.get('semanas') or 2)
    reemplazar = request.form.get('reemplazar') == '1'

    if ciclo_dias not in (7, 14):
        ciclo_dias = 14
    semanas = max(1, min(semanas, 12))

    try:
        inicio_dt = datetime.strptime(fecha_inicio, '%Y-%m-%d')
    except Exception:
        flash('Fecha de inicio no válida', 'danger')
        return redirect(url_for('menus.vista_semanal_menus'))

    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_sql_tipos, centro_params_tipos = _menu_centro_sql("tm")
    tipos = conn.execute("""
        SELECT id, nombre
        FROM tipos_menu tm
        WHERE activo = 1 AND empresa_id = ?
        """ + centro_sql_tipos + """
        ORDER BY id
    """, [empresa_id] + centro_params_tipos).fetchall()
    indice = _indexar_menus_ciclo(conn, empresa_id)
    tipos = [tipo for tipo in tipos if any(key[0] == tipo["id"] for key in indice)]

    total_dias = semanas * 7
    fecha_fin = inicio_dt + timedelta(days=total_dias - 1)
    if reemplazar:
        centro_sql_ps, centro_params_ps = _menu_centro_sql("ps")
        conn.execute("""
            DELETE FROM planificacion_servicios
            WHERE id IN (
                SELECT ps.id
                FROM planificacion_servicios ps
                WHERE ps.empresa_id = ?
                  """ + centro_sql_ps + """
              AND date(fecha) BETWEEN date(?) AND date(?)
            )
        """, [empresa_id] + centro_params_ps + [inicio_dt.strftime('%Y-%m-%d'), fecha_fin.strftime('%Y-%m-%d')])

    creados = 0
    sin_menu = []
    for offset in range(total_dias):
        fecha_dt = inicio_dt + timedelta(days=offset)
        fecha_txt = fecha_dt.strftime('%Y-%m-%d')
        dia = _dia_semana_es(fecha_dt)
        semana_ciclo = 1 if ciclo_dias == 7 else ((offset // 7) % 2) + 1

        for tipo in tipos:
            menu = indice.get((tipo["id"], semana_ciclo, dia))
            if not menu and semana_ciclo != 1:
                menu = indice.get((tipo["id"], 1, dia))
            if not menu:
                sin_menu.append(f"{fecha_txt} {tipo['nombre']}")
                continue
            if _guardar_planificacion_servicio(conn, empresa_id, fecha_txt, menu["id"]):
                creados += 1

    conn.commit()
    conn.close()

    if sin_menu:
        flash(f'{len(sin_menu)} servicios sin menú de ciclo. Primeros: {", ".join(sin_menu[:5])}', 'warning')
    flash(f'Ciclo aplicado: {creados} servicios planificados del {inicio_dt.strftime("%Y-%m-%d")} al {fecha_fin.strftime("%Y-%m-%d")}.', 'success')
    return redirect(url_for('menus.vista_semanal_menus', fecha=fecha_inicio))


@menus_bp.route('/semanal/quitar/<int:planificacion_id>', methods=['POST'])
@login_required
def quitar_menu_de_semanal(planificacion_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_sql_ps, centro_params_ps = _menu_centro_sql("ps")

    plan = conn.execute("""
        SELECT ps.fecha
        FROM planificacion_servicios ps
        WHERE ps.id = ?
          AND ps.empresa_id = ?
        """ + centro_sql_ps, [planificacion_id, empresa_id] + centro_params_ps).fetchone()

    if plan:
        conn.execute("DELETE FROM planificacion_servicios WHERE id = ? AND empresa_id = ?", (planificacion_id, empresa_id))
        conn.commit()
        flash('Menú eliminado de la planificación', 'success')
        fecha = plan['fecha']
    else:
        flash('No se encontró la planificación', 'danger')
        fecha = None

    conn.close()
    return redirect(url_for('menus.vista_semanal_menus', fecha=fecha))


# =========================================================
# COPIADOS
# =========================================================
@menus_bp.route('/copiar-fijo', methods=['POST'])
@login_required
def copiar_menu_fijo_a_diario():
    menu_fijo_id = request.values.get('menu_fijo_id')
    fecha = request.values.get('fecha')
    tipo_id = request.values.get('tipo_id')

    if not all([menu_fijo_id, fecha, tipo_id]):
        flash('Faltan parámetros', 'danger')
        return redirect(url_for('menus.vista_semanal_menus'))

    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _ensure_menu_centros_schema(conn)
    centro_sql_m, centro_params_m = _menu_centro_sql("m")

    menu_fijo = conn.execute("""
        SELECT m.id, m.observaciones, m.centro_id
        FROM menus m
        WHERE m.id = ? AND m.empresa_id = ?
        """ + centro_sql_m, [menu_fijo_id, empresa_id] + centro_params_m).fetchone()

    if not menu_fijo:
        conn.close()
        flash('Menú fijo no encontrado en este centro', 'danger')
        return redirect(url_for('menus.vista_semanal_menus'))

    existente = conn.execute("""
        SELECT id FROM menus_diarios
        WHERE fecha = ? AND tipo_menu_id = ? AND empresa_id = ?
          AND COALESCE(centro_id, 0) = COALESCE(?, 0)
    """, (fecha, tipo_id, empresa_id, menu_fijo["centro_id"])).fetchone()

    if existente:
        conn.close()
        flash('Ya existe un menú para esa fecha y tipo. Redirigiendo a edición.', 'warning')
        return redirect(url_for('menus.ver_menu_diario', menu_id=existente['id']))

    secciones_fijas = conn.execute("""
        SELECT *
        FROM menu_fijo_subdepartamentos
        WHERE menu_id = ?
        ORDER BY orden, id
    """, (menu_fijo_id,)).fetchall()

    if not secciones_fijas:
        conn.close()
        flash('El menú fijo no tiene secciones', 'warning')
        return redirect(url_for('menus.vista_semanal_menus'))

    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO menus_diarios (fecha, tipo_menu_id, observaciones, empresa_id, centro_id)
        VALUES (?, ?, ?, ?, ?)
    """, (fecha, tipo_id, menu_fijo['observaciones'] or 'Copiado de menú fijo', empresa_id, menu_fijo["centro_id"]))
    menu_id = cursor.lastrowid

    for seccion in secciones_fijas:
        cursor.execute("""
            INSERT INTO menu_subdepartamentos (menu_diario_id, subdepartamento_id, seccion, orden)
            VALUES (?, ?, ?, ?)
        """, (menu_id, seccion['subdepartamento_id'], seccion['seccion'], seccion['orden']))
        nueva_seccion_id = cursor.lastrowid

        platos = conn.execute("""
            SELECT *
            FROM menu_fijo_platos
            WHERE menu_fijo_subdepartamento_id = ?
            ORDER BY orden, id
        """, (seccion['id'],)).fetchall()

        for plato in platos:
            cursor.execute("""
                INSERT INTO menu_platos (menu_subdepartamento_id, receta_id, orden)
                VALUES (?, ?, ?)
            """, (nueva_seccion_id, plato['receta_id'], plato['orden']))

    conn.commit()
    conn.close()

    flash('Menú copiado correctamente desde menú fijo', 'success')
    return redirect(url_for('menus.ver_menu_diario', menu_id=menu_id))


@menus_bp.route('/copiar-diario', methods=['POST'])
@login_required
def copiar_menu_diario():
    menu_origen_id = request.values.get('menu_origen_id')
    fecha = request.values.get('fecha')
    tipo_id = request.values.get('tipo_id')

    if not all([menu_origen_id, fecha, tipo_id]):
        flash('Faltan parámetros', 'danger')
        return redirect(url_for('menus.vista_semanal_menus'))

    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    _ensure_menu_centros_schema(conn)
    menu_origen = _menu_diario_en_ambito(conn, menu_origen_id, empresa_id)
    if not menu_origen:
        conn.close()
        flash('Menú origen no encontrado en este centro', 'danger')
        return redirect(url_for('menus.vista_semanal_menus'))

    existente = conn.execute("""
        SELECT id FROM menus_diarios
        WHERE fecha = ? AND tipo_menu_id = ? AND empresa_id = ?
          AND COALESCE(centro_id, 0) = COALESCE(?, 0)
    """, (fecha, tipo_id, empresa_id, menu_origen["centro_id"])).fetchone()

    if existente:
        conn.close()
        flash('Ya existe un menú para esa fecha y tipo. Redirigiendo a edición.', 'warning')
        return redirect(url_for('menus.ver_menu_diario', menu_id=existente['id']))

    secciones = conn.execute("""
        SELECT * FROM menu_subdepartamentos
        WHERE menu_diario_id = ?
    """, (menu_origen_id,)).fetchall()

    if not secciones:
        conn.close()
        flash('El menú origen no tiene secciones', 'warning')
        return redirect(url_for('menus.vista_semanal_menus'))

    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO menus_diarios (fecha, tipo_menu_id, observaciones, empresa_id, centro_id)
        VALUES (?, ?, 'Copiado de otro menú', ?, ?)
    """, (fecha, tipo_id, empresa_id, menu_origen["centro_id"]))
    menu_id = cursor.lastrowid

    for seccion in secciones:
        cursor.execute("""
            INSERT INTO menu_subdepartamentos (menu_diario_id, subdepartamento_id, seccion, orden)
            VALUES (?, ?, ?, ?)
        """, (menu_id, seccion['subdepartamento_id'], seccion['seccion'], seccion['orden']))
        nueva_seccion_id = cursor.lastrowid

        platos = conn.execute("""
            SELECT * FROM menu_platos
            WHERE menu_subdepartamento_id = ?
        """, (seccion['id'],)).fetchall()

        for plato in platos:
            cursor.execute("""
                INSERT INTO menu_platos (menu_subdepartamento_id, receta_id, orden)
                VALUES (?, ?, ?)
            """, (nueva_seccion_id, plato['receta_id'], plato['orden']))

    conn.commit()
    conn.close()

    flash('Menú copiado correctamente desde otro menú diario', 'success')
    return redirect(url_for('menus.ver_menu_diario', menu_id=menu_id))


# =========================================================
# EXPORTAR
# =========================================================
@menus_bp.route('/importar-centro', methods=['GET', 'POST'])
@login_required
def importar_menus_centro():
    conn = get_db()
    _ensure_menu_centros_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    centro_destino_id = centro_activo_id()

    if not centro_destino_id:
        conn.close()
        flash('Selecciona un centro destino antes de importar menús.', 'warning')
        return redirect(url_for('menus.lista_menus_principal'))

    centros = _centros_disponibles(conn, empresa_id)
    centro_destino = conn.execute("""
        SELECT id, nombre
        FROM centros_trabajo
        WHERE id = ? AND empresa_id = ?
          AND COALESCE(activo, 1) = 1
    """, (centro_destino_id, empresa_id)).fetchone()
    if not centro_destino:
        conn.close()
        flash('El centro destino activo no pertenece a tu empresa o no está activo.', 'danger')
        return redirect(url_for('menus.lista_menus_principal'))

    centro_origen_id = request.values.get('centro_origen_id') or ''
    importar_fijos = request.values.get('importar_fijos', '1') in ('1', 'on', 'true', 'True')
    importar_diarios = request.values.get('importar_diarios', '1') in ('1', 'on', 'true', 'True')
    importar_recetas = request.values.get('importar_recetas', '1') in ('1', 'on', 'true', 'True')

    resumen_origen = None
    centro_origen = None
    if centro_origen_id and str(centro_origen_id).isdigit():
        centro_origen = conn.execute("""
            SELECT id, nombre
            FROM centros_trabajo
            WHERE id = ? AND empresa_id = ?
              AND COALESCE(activo, 1) = 1
        """, (int(centro_origen_id), empresa_id)).fetchone()

    if centro_origen and int(centro_origen["id"]) != int(centro_destino_id):
        resumen_origen = {
            "fijos": conn.execute(
                "SELECT COUNT(*) AS total FROM menus WHERE empresa_id = ? AND centro_id = ?",
                (empresa_id, centro_origen["id"]),
            ).fetchone()["total"],
            "diarios": conn.execute(
                "SELECT COUNT(*) AS total FROM menus_diarios WHERE empresa_id = ? AND centro_id = ?",
                (empresa_id, centro_origen["id"]),
            ).fetchone()["total"],
            "recetas": conn.execute(
                "SELECT COUNT(*) AS total FROM recetas WHERE empresa_id = ? AND centro_id = ?",
                (empresa_id, centro_origen["id"]),
            ).fetchone()["total"],
        }

    if request.method == 'POST':
        if not centro_origen:
            conn.close()
            flash('Selecciona un centro origen válido dentro de la empresa activa.', 'warning')
            return redirect(url_for('menus.importar_menus_centro'))
        centro_origen_id = int(centro_origen["id"])
        if centro_origen_id == int(centro_destino_id):
            conn.close()
            flash('El centro origen y destino no pueden ser el mismo.', 'warning')
            return redirect(url_for('menus.importar_menus_centro'))
        if request.form.get('confirmar_importacion') != '1':
            conn.close()
            flash('Confirma origen y destino antes de importar.', 'warning')
            return redirect(url_for('menus.importar_menus_centro', centro_origen_id=centro_origen_id))

        creados = {"fijos": 0, "diarios": 0, "recetas": 0}
        existentes = {"fijos": 0, "diarios": 0}
        recetas_antes = conn.execute(
            "SELECT COUNT(*) AS total FROM recetas WHERE empresa_id = ? AND centro_id = ?",
            (empresa_id, centro_destino_id),
        ).fetchone()["total"]

        if importar_fijos:
            menus_fijos = conn.execute("""
                SELECT id FROM menus
                WHERE empresa_id = ?
                  AND centro_id = ?
                ORDER BY nombre, id
            """, (empresa_id, centro_origen_id)).fetchall()
            for menu in menus_fijos:
                _nuevo_id, estado = _copiar_menu_fijo_a_centro(conn, empresa_id, menu["id"], centro_destino_id, importar_recetas)
                if estado == "creado":
                    creados["fijos"] += 1
                elif estado == "existente":
                    existentes["fijos"] += 1

        if importar_diarios:
            menus_diarios = conn.execute("""
                SELECT id FROM menus_diarios
                WHERE empresa_id = ?
                  AND centro_id = ?
                ORDER BY fecha, tipo_menu_id, id
            """, (empresa_id, centro_origen_id)).fetchall()
            for menu in menus_diarios:
                _nuevo_id, estado = _copiar_menu_diario_a_centro(conn, empresa_id, menu["id"], centro_destino_id, importar_recetas)
                if estado == "creado":
                    creados["diarios"] += 1
                elif estado == "existente":
                    existentes["diarios"] += 1

        recetas_despues = conn.execute(
            "SELECT COUNT(*) AS total FROM recetas WHERE empresa_id = ? AND centro_id = ?",
            (empresa_id, centro_destino_id),
        ).fetchone()["total"]
        creados["recetas"] = max(0, recetas_despues - recetas_antes)
        conn.commit()
        conn.close()
        flash(
            f"Importación completada: {creados['fijos']} menús fijos, {creados['diarios']} menús diarios y {creados['recetas']} recetas nuevas. "
            f"Ya existían: {existentes['fijos']} fijos y {existentes['diarios']} diarios.",
            'success',
        )
        return redirect(url_for('menus.lista_menus_principal'))

    conn.close()
    return render_template(
        'menus/importar_centro.html',
        centros=centros,
        centro_destino=centro_destino,
        centro_destino_id=centro_destino_id,
        centro_origen=centro_origen,
        centro_origen_id=int(centro_origen_id) if str(centro_origen_id).isdigit() else None,
        resumen_origen=resumen_origen,
        importar_fijos=importar_fijos,
        importar_diarios=importar_diarios,
        importar_recetas=importar_recetas,
    )


@menus_bp.route('/exportar')
@login_required
def exportar_menus():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_sql, centro_params = _menu_centro_sql("m")

    rows = conn.execute("""
        SELECT
            c.nombre as centro,
            m.fecha,
            t.nombre as servicio,
            s.nombre as subdepartamento,
            ms.seccion,
            r.nombre as plato
        FROM menus_diarios m
        JOIN tipos_menu t ON m.tipo_menu_id = t.id
        LEFT JOIN centros_trabajo c ON c.id = m.centro_id
        LEFT JOIN menu_subdepartamentos ms ON m.id = ms.menu_diario_id
        LEFT JOIN subdepartamentos s ON ms.subdepartamento_id = s.id
        LEFT JOIN menu_platos mp ON ms.id = mp.menu_subdepartamento_id
        LEFT JOIN recetas r ON mp.receta_id = r.id
        WHERE m.empresa_id = ?
        """ + centro_sql + """
        ORDER BY m.fecha, t.nombre, s.nombre, ms.orden, mp.orden
    """, [empresa_id] + centro_params).fetchall()

    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Menus"

    ws.append(["Centro", "Fecha", "Servicio", "Subdepartamento", "Sección", "Plato"])

    for row in rows:
        ws.append([
            row["centro"] or "Empresa completa",
            row["fecha"],
            row["servicio"],
            row["subdepartamento"],
            row["seccion"],
            row["plato"]
        ])

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="menus.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

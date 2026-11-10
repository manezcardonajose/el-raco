import csv
import io
import json
from datetime import date, datetime, timedelta

from flask import abort, flash, jsonify, redirect, render_template, request, session, url_for

from database import get_db
from blueprints.auth.routes import login_required, usuario_tiene_permiso
from helpers.centros_helper import centro_activo_id
from . import tpv_bp


TIPOS_TERMINAL = {
    "tpv": "TPV",
    "comandero": "Comandero",
    "pantalla_cocina": "Pantalla cocina",
    "pantalla_barra": "Pantalla barra",
    "impresora": "Impresora",
}

TIPOS_PUNTO_TPV = {
    "buffet": "Buffet",
    "carta": "Restaurante carta",
    "bar": "Bar",
    "snack": "Snack",
    "mixto": "Mixto",
    "ocio": "Zona ocio",
}

DENOMINACIONES_ARQUEO_TPV = [
    {"tipo": "moneda", "valor": 0.05, "label": "0,05 EUR"},
    {"tipo": "moneda", "valor": 0.10, "label": "0,10 EUR"},
    {"tipo": "moneda", "valor": 0.20, "label": "0,20 EUR"},
    {"tipo": "moneda", "valor": 0.50, "label": "0,50 EUR"},
    {"tipo": "moneda", "valor": 1.00, "label": "1 EUR"},
    {"tipo": "moneda", "valor": 2.00, "label": "2 EUR"},
    {"tipo": "billete", "valor": 5.00, "label": "5 EUR"},
    {"tipo": "billete", "valor": 10.00, "label": "10 EUR"},
    {"tipo": "billete", "valor": 20.00, "label": "20 EUR"},
    {"tipo": "billete", "valor": 50.00, "label": "50 EUR"},
    {"tipo": "billete", "valor": 100.00, "label": "100 EUR"},
    {"tipo": "billete", "valor": 200.00, "label": "200 EUR"},
]

DESTINOS_PREPARACION = {
    "sin_preparacion": "Sin preparacion",
    "barra": "Barra",
    "cocina": "Cocina",
    "buffet": "Buffet",
    "postres": "Postres",
}

ESTADOS_COMANDA = {
    "abierta": "Abierta",
    "enviada": "Enviada",
    "en_preparacion": "En preparacion",
    "lista": "Lista",
    "entregada": "Entregada",
    "cobrada": "Cobrada",
    "cancelada": "Cancelada",
}

ESTADOS_CUENTA = {
    "abierta": "Abierta",
    "cobrada": "Cobrada",
    "cerrada": "Cerrada",
    "cancelada": "Cancelada",
}

FORMAS_PAGO_TPV = {
    "efectivo": "Efectivo",
    "tarjeta": "Tarjeta",
    "cargo_habitacion": "Cargo habitacion",
    "consumo_empleado": "Consumo empleado",
    "invitacion": "Invitacion",
    "otro": "Otro",
}

ESTADOS_CAJA_TPV = {
    "abierta": "Abierta",
    "cerrada": "Cerrada",
}

PRIORIDADES_COMANDA = {
    "normal": "Normal",
    "entrante": "Sale antes",
    "ninos": "Ninos primero",
    "urgente": "Urgente",
    "esperar": "Esperar orden",
}

TIPOS_COBRO_TPV = {
    "incluido": "Incluido / libre",
    "suplemento": "Suplemento",
    "gourmet": "Gourmet / cobro",
    "cobro": "Cobro normal",
}

ESTADOS_LINEA_COMANDA = {
    "retenida": "Retenida",
    "pendiente": "Pendiente",
    "en_preparacion": "En preparacion",
    "lista": "Lista",
    "entregada": "Entregada",
    "cancelada": "Cancelada",
}

REGIMENES_TPV = {
    "solo_alojamiento": "Solo alojamiento",
    "alojamiento_desayuno": "Alojamiento y desayuno",
    "media_pension": "Media pension",
    "pension_completa": "Pension completa",
    "todo_incluido": "Todo incluido",
}

SERVICIOS_BUFFET = {
    "desayuno": "Desayuno",
    "comida": "Comida",
    "cena": "Cena",
    "bebidas_snacks": "Bebidas / snacks",
}

TURNOS_TPV = {
    "manana": "Manana",
    "tarde": "Tarde",
    "noche": "Noche",
    "partido": "Partido",
}

SERVICIOS_BUFFET_BASE = [
    ("desayuno", "Desayuno", "07:30", "10:30", 18.00, 9.00),
    ("comida", "Comida", "13:00", "15:30", 24.00, 12.00),
    ("cena", "Cena", "19:00", "22:00", 28.00, 14.00),
    ("bebidas_snacks", "Bebidas / snacks", "11:00", "18:00", 12.00, 6.00),
]

CATALOGO_BASE_TPV = [
    ("Agua 0.5L", "bebidas_agua", "botella", "seco", 0.25, 1.50, "barra", "bebida"),
    ("Agua 1.5L", "bebidas_agua", "botella", "seco", 0.45, 2.50, "barra", "bebida"),
    ("Agua con gas 0.5L", "bebidas_agua", "botella", "seco", 0.35, 2.00, "barra", "bebida"),
    ("Refresco cola botella", "bebidas_refrescos", "botella", "seco", 0.55, 2.50, "barra", "bebida"),
    ("Refresco cola zero botella", "bebidas_refrescos", "botella", "seco", 0.55, 2.50, "barra", "bebida"),
    ("Refresco naranja lata", "bebidas_refrescos", "lata", "seco", 0.45, 2.30, "barra", "bebida"),
    ("Refresco limon lata", "bebidas_refrescos", "lata", "seco", 0.45, 2.30, "barra", "bebida"),
    ("Tonica botella", "bebidas_refrescos", "botella", "seco", 0.45, 2.30, "barra", "bebida"),
    ("Nestea botella", "bebidas_refrescos", "botella", "seco", 0.55, 2.50, "barra", "bebida"),
    ("Cerveza botella 33cl", "bebidas_cerveza", "botella", "seco", 0.65, 3.00, "barra", "bebida"),
    ("Cerveza lata 33cl", "bebidas_cerveza", "lata", "seco", 0.55, 2.80, "barra", "bebida"),
    ("Cerveza tirador cana", "bebidas_cerveza", "servicio", "seco", 0.35, 2.20, "barra", "bebida"),
    ("Cerveza tirador doble", "bebidas_cerveza", "servicio", "seco", 0.55, 3.20, "barra", "bebida"),
    ("Cerveza tirador pinta", "bebidas_cerveza", "servicio", "seco", 0.70, 4.00, "barra", "bebida"),
    ("Jarra cerveza 0.5L", "bebidas_cerveza", "servicio", "seco", 0.85, 4.80, "barra", "bebida"),
    ("Cerveza sin alcohol", "bebidas_cerveza", "botella", "seco", 0.65, 3.00, "barra", "bebida"),
    ("Vino tinto botella", "bebidas_premium", "botella", "seco", 4.50, 18.00, "barra", "premium"),
    ("Vino tinto copa", "bebidas_premium", "copa", "seco", 0.80, 3.50, "barra", "premium"),
    ("Vino blanco botella", "bebidas_premium", "botella", "seco", 4.50, 18.00, "barra", "premium"),
    ("Vino blanco copa", "bebidas_premium", "copa", "seco", 0.80, 3.50, "barra", "premium"),
    ("Vino rosado botella", "bebidas_premium", "botella", "seco", 4.20, 16.00, "barra", "premium"),
    ("Vino rosado copa", "bebidas_premium", "copa", "seco", 0.75, 3.20, "barra", "premium"),
    ("Cava botella", "bebidas_premium", "botella", "seco", 5.50, 22.00, "barra", "premium"),
    ("Cava copa", "bebidas_premium", "copa", "seco", 1.00, 4.50, "barra", "premium"),
    ("Ginebra copa", "bebidas_licores", "servicio", "seco", 1.10, 6.50, "barra", "premium"),
    ("Ginebra cubata", "bebidas_licores", "servicio", "seco", 1.35, 7.50, "barra", "premium"),
    ("Ginebra chupito", "bebidas_licores", "servicio", "seco", 0.55, 3.00, "barra", "premium"),
    ("Ron copa", "bebidas_licores", "servicio", "seco", 1.00, 6.00, "barra", "premium"),
    ("Ron cubata", "bebidas_licores", "servicio", "seco", 1.30, 7.00, "barra", "premium"),
    ("Ron chupito", "bebidas_licores", "servicio", "seco", 0.50, 3.00, "barra", "premium"),
    ("Whisky copa", "bebidas_licores", "servicio", "seco", 1.20, 7.00, "barra", "premium"),
    ("Whisky cubata", "bebidas_licores", "servicio", "seco", 1.45, 8.00, "barra", "premium"),
    ("Whisky chupito", "bebidas_licores", "servicio", "seco", 0.60, 3.50, "barra", "premium"),
    ("Vodka copa", "bebidas_licores", "servicio", "seco", 1.00, 6.00, "barra", "premium"),
    ("Vodka cubata", "bebidas_licores", "servicio", "seco", 1.25, 7.00, "barra", "premium"),
    ("Vodka chupito", "bebidas_licores", "servicio", "seco", 0.50, 3.00, "barra", "premium"),
    ("Licor copa", "bebidas_licores", "servicio", "seco", 0.90, 5.00, "barra", "premium"),
    ("Licor chupito", "bebidas_licores", "servicio", "seco", 0.45, 2.80, "barra", "premium"),
    ("Patatas fritas bolsa", "snacks", "bolsa", "seco", 0.45, 2.00, "sin_preparacion", "snack"),
    ("Frutos secos bolsa", "snacks", "bolsa", "seco", 0.60, 2.50, "sin_preparacion", "snack"),
    ("Gominolas bolsa", "snacks", "bolsa", "seco", 0.35, 1.80, "sin_preparacion", "snack"),
    ("Aceitunas snack", "snacks", "ud", "refrigerado", 0.40, 2.20, "barra", "snack"),
    ("Helado polo", "helados", "ud", "congelado", 0.45, 2.00, "sin_preparacion", "snack"),
    ("Helado tarrina", "helados", "ud", "congelado", 0.60, 2.80, "sin_preparacion", "snack"),
    ("Cafe solo", "cafeteria", "servicio", "seco", 0.18, 1.40, "barra", "bebida"),
    ("Cafe con leche", "cafeteria", "servicio", "refrigerado", 0.30, 1.80, "barra", "bebida"),
    ("Entrada buffet adulto", "buffet", "servicio", "seco", 0.00, 18.00, "buffet", "buffet"),
    ("Entrada buffet menor", "buffet", "servicio", "seco", 0.00, 9.00, "buffet", "buffet"),
]


def _empresa_id():
    return session.get("user_empresa_id", 1) or 1


def _puede_operar_tpv():
    return (
        usuario_tiene_permiso("tpv_ver")
        or usuario_tiene_permiso("tpv_gestionar")
        or usuario_tiene_permiso("acceso_global")
    )


def _puede_gestionar_tpv():
    return usuario_tiene_permiso("tpv_gestionar") or usuario_tiene_permiso("acceso_global")


def _requiere_operar_tpv(conn=None, destino="tpv.dashboard"):
    if _puede_operar_tpv():
        return None
    if conn:
        conn.close()
    flash("No tienes permisos para operar TPV.", "warning")
    return redirect(url_for(destino))


def _requiere_gestion_tpv(conn=None, destino="tpv.dashboard"):
    if _puede_gestionar_tpv():
        return None
    if conn:
        conn.close()
    flash("No tienes permisos para configurar TPV.", "warning")
    return redirect(url_for(destino))


def _table_exists(conn, table_name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _column_exists(conn, table_name, column_name):
    try:
        cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:
        return False
    return any(col["name"] == column_name or col[1] == column_name for col in cols)


def _add_column_if_missing(conn, table_name, column_sql):
    column_name = column_sql.strip().split()[0]
    if _table_exists(conn, table_name) and not _column_exists(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")
        return True
    return False


def _to_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _to_int(value, default=0):
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def _parse_date(value, default=None):
    if not value:
        return default
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return default


def _usuario_actual():
    return session.get("user_nombre") or session.get("user_email") or str(session.get("user_id") or "")


def _row_snapshot(row, fields):
    if not row:
        return {}
    return {field: row[field] for field in fields if field in row.keys()}


def _registrar_tpv_auditoria(conn, empresa_id, entidad, entidad_id, accion, antes=None, despues=None, punto_venta_id=None, motivo=""):
    conn.execute(
        """
        INSERT INTO tpv_auditoria
        (entidad, entidad_id, punto_venta_id, accion, antes_json, despues_json,
         motivo, usuario_id, usuario, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entidad,
            entidad_id,
            punto_venta_id,
            accion,
            json.dumps(antes or {}, ensure_ascii=True, sort_keys=True),
            json.dumps(despues or {}, ensure_ascii=True, sort_keys=True),
            motivo or "",
            session.get("user_id"),
            _usuario_actual(),
            empresa_id,
        ),
    )


def _codigo_terminal_auto(conn, empresa_id, punto_venta_id, tipo):
    codigo = (session.get("tpv_device_code") or "").strip().upper()
    if codigo:
        return codigo
    ip = request.remote_addr or ""
    terminal = conn.execute(
        """
        SELECT codigo
        FROM tpv_terminales
        WHERE empresa_id = ?
          AND punto_venta_id = ?
          AND tipo = ?
          AND COALESCE(activo, 1) = 1
          AND COALESCE(ip, '') = ?
          AND (codigo LIKE 'AUTO-%' OR codigo LIKE 'LOCAL-%')
        ORDER BY ultimo_inicio DESC, id DESC
        LIMIT 1
        """,
        (empresa_id, punto_venta_id, tipo, ip),
    ).fetchone()
    if terminal and terminal["codigo"]:
        codigo = terminal["codigo"].strip().upper()
    else:
        codigo = f"AUTO-{int(datetime.now().timestamp())}"
    session["tpv_device_code"] = codigo
    return codigo


def _terminal_activo_tpv(conn, empresa_id, punto_venta_id=None):
    centro_id = centro_activo_id()
    terminal_id = request.values.get("terminal_id") or str(session.get("tpv_terminal_id") or "")
    terminal = None
    if terminal_id.isdigit():
        filtros = ["tpv_terminales.empresa_id = ?", "COALESCE(tpv_terminales.activo, 1) = 1", "tpv_terminales.id = ?"]
        params = [empresa_id, int(terminal_id)]
        if punto_venta_id:
            filtros.append("tpv_terminales.punto_venta_id = ?")
            params.append(int(punto_venta_id))
        centro_join = ""
        if centro_id:
            centro_join = "JOIN puntos_venta pv ON pv.id = tpv_terminales.punto_venta_id AND pv.centro_id = ?"
            params.insert(0, centro_id)
        terminal = conn.execute(
            f"""
            SELECT tpv_terminales.*
            FROM tpv_terminales
            {centro_join}
            WHERE {" AND ".join(filtros)}
            ORDER BY ultimo_inicio DESC, id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    if not terminal:
        filtros = ["tpv_terminales.empresa_id = ?", "COALESCE(tpv_terminales.activo, 1) = 1"]
        params = [empresa_id]
        if punto_venta_id:
            filtros.append("tpv_terminales.punto_venta_id = ?")
            params.append(int(punto_venta_id))
        centro_join = ""
        if centro_id:
            centro_join = "JOIN puntos_venta pv ON pv.id = tpv_terminales.punto_venta_id AND pv.centro_id = ?"
            params.insert(0, centro_id)
        terminal = conn.execute(
            f"""
            SELECT tpv_terminales.*
            FROM tpv_terminales
            {centro_join}
            WHERE {" AND ".join(filtros)}
            ORDER BY ultimo_inicio DESC, id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    if terminal:
        session["tpv_terminal_id"] = terminal["id"]
    return terminal


def _opciones_menu_tpv(conn, empresa_id, punto_venta_id):
    opciones = {"salsas": []}
    if not punto_venta_id or not _table_exists(conn, "menus") or not _table_exists(conn, "menu_fijo_subdepartamentos"):
        return opciones
    menu = conn.execute(
        """
        SELECT id
        FROM menus
        WHERE empresa_id = ?
          AND punto_venta_id = ?
        ORDER BY COALESCE(activo, 0) DESC, id DESC
        LIMIT 1
        """,
        (empresa_id, punto_venta_id),
    ).fetchone()
    if not menu:
        return opciones
    rows = conn.execute(
        """
        SELECT r.id, r.nombre, mfs.seccion, mfp.orden
        FROM menu_fijo_subdepartamentos mfs
        JOIN menu_fijo_platos mfp ON mfp.menu_fijo_subdepartamento_id = mfs.id
        JOIN recetas r ON r.id = mfp.receta_id
        WHERE mfs.menu_id = ?
          AND (UPPER(COALESCE(mfs.seccion, '')) LIKE '%SALSA%')
        ORDER BY COALESCE(mfs.orden, 0), COALESCE(mfp.orden, 0), r.nombre
        """,
        (menu["id"],),
    ).fetchall()
    seen = set()
    for row in rows:
        nombre = (row["nombre"] or "").strip()
        key = nombre.lower()
        if nombre and key not in seen:
            opciones["salsas"].append({"id": row["id"], "nombre": nombre})
            seen.add(key)
    return opciones


def _ensure_tpv_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS puntos_venta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            tipo TEXT DEFAULT 'tpv',
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            almacen_id INTEGER,
            imprimir_comanda INTEGER DEFAULT 0,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1
        )
        """
    )
    for col in [
        "nombre TEXT",
        "tipo TEXT DEFAULT 'tpv'",
        "departamento_id INTEGER",
        "subdepartamento_id INTEGER",
        "almacen_id INTEGER",
        "imprimir_comanda INTEGER DEFAULT 0",
        "activo INTEGER DEFAULT 1",
        "empresa_id INTEGER DEFAULT 1",
    ]:
        _add_column_if_missing(conn, "puntos_venta", col)
    tpv_tipo_added = False
    tpv_control_buffet_added = False
    for col in [
        "tpv_tipo TEXT DEFAULT 'bar'",
        "tpv_control_buffet INTEGER DEFAULT 0",
        "tpv_admite_cargo_habitacion INTEGER DEFAULT 1",
        "tpv_permite_todo_incluido INTEGER DEFAULT 1",
        "tpv_bebidas_incluidas INTEGER DEFAULT 0",
    ]:
        added = _add_column_if_missing(conn, "puntos_venta", col)
        tpv_tipo_added = tpv_tipo_added or col.startswith("tpv_tipo ") and added
        tpv_control_buffet_added = tpv_control_buffet_added or col.startswith("tpv_control_buffet ") and added
    if tpv_tipo_added:
        conn.execute(
            """
            UPDATE puntos_venta
            SET tpv_tipo = LOWER(tipo)
            WHERE LOWER(COALESCE(tipo, '')) IN ('buffet', 'carta', 'bar', 'snack', 'mixto', 'ocio')
              AND (tpv_tipo IS NULL OR TRIM(tpv_tipo) = '' OR tpv_tipo = 'bar')
            """
        )
        conn.execute(
            """
            UPDATE puntos_venta
            SET tpv_tipo = 'buffet'
            WHERE LOWER(COALESCE(nombre, '')) LIKE '%buffet%'
              AND (tpv_tipo IS NULL OR TRIM(tpv_tipo) = '' OR tpv_tipo = 'bar')
            """
        )
    if tpv_control_buffet_added:
        conn.execute(
            """
            UPDATE puntos_venta
            SET tpv_control_buffet = 1
            WHERE tpv_tipo = 'buffet' OR LOWER(COALESCE(nombre, '')) LIKE '%buffet%'
            """
        )
    if _table_exists(conn, "productos"):
        for col in [
            "visible_en_venta INTEGER DEFAULT 1",
            "formato_venta TEXT",
            "precio_venta REAL DEFAULT 0",
        ]:
            _add_column_if_missing(conn, "productos", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tpv_terminales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            punto_venta_id INTEGER,
            codigo TEXT NOT NULL,
            nombre TEXT NOT NULL,
            tipo TEXT DEFAULT 'tpv',
            mac TEXT,
            ip TEXT,
            ubicacion TEXT,
            modo_arranque TEXT DEFAULT 'manual',
            servicio_actual TEXT DEFAULT 'desayuno',
            turno_actual TEXT DEFAULT 'manana',
            fecha_servicio TEXT,
            ultimo_inicio TEXT,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "punto_venta_id INTEGER",
        "codigo TEXT",
        "nombre TEXT",
        "tipo TEXT DEFAULT 'tpv'",
        "mac TEXT",
        "ip TEXT",
        "ubicacion TEXT",
        "modo_arranque TEXT DEFAULT 'manual'",
        "servicio_actual TEXT DEFAULT 'desayuno'",
        "turno_actual TEXT DEFAULT 'manana'",
        "fecha_servicio TEXT",
        "comandero_zonas TEXT",
        "comandero_categorias TEXT",
        "comandero_ver_precios INTEGER DEFAULT 1",
        "comandero_puede_cobrar INTEGER DEFAULT 0",
        "ultimo_inicio TEXT",
        "activo INTEGER DEFAULT 1",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "tpv_terminales", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tpv_categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            punto_venta_id INTEGER NOT NULL,
            nombre TEXT NOT NULL,
            tipo TEXT DEFAULT 'venta',
            color TEXT DEFAULT '#2563eb',
            orden INTEGER DEFAULT 0,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1
        )
        """
    )
    for col in [
        "punto_venta_id INTEGER",
        "nombre TEXT",
        "tipo TEXT DEFAULT 'venta'",
        "color TEXT DEFAULT '#2563eb'",
        "orden INTEGER DEFAULT 0",
        "activo INTEGER DEFAULT 1",
        "empresa_id INTEGER DEFAULT 1",
    ]:
        _add_column_if_missing(conn, "tpv_categorias", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tpv_articulos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            punto_venta_id INTEGER NOT NULL,
            categoria_id INTEGER,
            producto_id INTEGER,
            receta_id INTEGER,
            nombre TEXT NOT NULL,
            tipo TEXT DEFAULT 'producto',
            precio REAL DEFAULT 0,
            incluido_regimenes TEXT,
            suplemento REAL DEFAULT 0,
            tipo_cobro TEXT DEFAULT 'cobro',
            destino_preparacion TEXT DEFAULT 'sin_preparacion',
            visible_tpv INTEGER DEFAULT 1,
            visible_comandero INTEGER DEFAULT 1,
            orden INTEGER DEFAULT 0,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1
        )
        """
    )
    for col in [
        "punto_venta_id INTEGER",
        "categoria_id INTEGER",
        "producto_id INTEGER",
        "receta_id INTEGER",
        "nombre TEXT",
        "tipo TEXT DEFAULT 'producto'",
        "precio REAL DEFAULT 0",
        "incluido_regimenes TEXT",
        "suplemento REAL DEFAULT 0",
        "tipo_cobro TEXT DEFAULT 'cobro'",
        "destino_preparacion TEXT DEFAULT 'sin_preparacion'",
        "visible_tpv INTEGER DEFAULT 1",
        "visible_comandero INTEGER DEFAULT 1",
        "orden INTEGER DEFAULT 0",
        "activo INTEGER DEFAULT 1",
        "empresa_id INTEGER DEFAULT 1",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "tpv_articulos", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tpv_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entidad TEXT NOT NULL,
            entidad_id INTEGER,
            punto_venta_id INTEGER,
            accion TEXT NOT NULL,
            antes_json TEXT,
            despues_json TEXT,
            motivo TEXT,
            usuario_id INTEGER,
            usuario TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "entidad TEXT",
        "entidad_id INTEGER",
        "punto_venta_id INTEGER",
        "accion TEXT",
        "antes_json TEXT",
        "despues_json TEXT",
        "motivo TEXT",
        "usuario_id INTEGER",
        "usuario TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "fecha TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "tpv_auditoria", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tpv_servicios_buffet (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            punto_venta_id INTEGER NOT NULL,
            servicio TEXT NOT NULL,
            nombre TEXT NOT NULL,
            hora_inicio TEXT,
            hora_fin TEXT,
            precio_adulto REAL DEFAULT 0,
            precio_menor REAL DEFAULT 0,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1
        )
        """
    )
    for col in [
        "punto_venta_id INTEGER",
        "servicio TEXT",
        "nombre TEXT",
        "hora_inicio TEXT",
        "hora_fin TEXT",
        "precio_adulto REAL DEFAULT 0",
        "precio_menor REAL DEFAULT 0",
        "activo INTEGER DEFAULT 1",
        "empresa_id INTEGER DEFAULT 1",
    ]:
        _add_column_if_missing(conn, "tpv_servicios_buffet", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tpv_mesas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            punto_venta_id INTEGER NOT NULL,
            zona TEXT,
            numero TEXT,
            nombre TEXT,
            x REAL DEFAULT 0,
            y REAL DEFAULT 0,
            capacidad INTEGER DEFAULT 2,
            estado TEXT DEFAULT 'libre',
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1
        )
        """
    )
    for col in [
        "punto_venta_id INTEGER",
        "zona TEXT",
        "numero TEXT",
        "nombre TEXT",
        "x REAL DEFAULT 0",
        "y REAL DEFAULT 0",
        "capacidad INTEGER DEFAULT 2",
        "estado TEXT DEFAULT 'libre'",
        "activo INTEGER DEFAULT 1",
        "empresa_id INTEGER DEFAULT 1",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "tpv_mesas", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tpv_cuentas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero TEXT,
            punto_venta_id INTEGER NOT NULL,
            terminal_id INTEGER,
            mesa_id INTEGER,
            mesa_nombre TEXT,
            modo_servicio TEXT DEFAULT 'mesa',
            reserva_id INTEGER,
            habitacion TEXT,
            cliente TEXT,
            pax INTEGER DEFAULT 0,
            estado TEXT DEFAULT 'abierta',
            subtotal REAL DEFAULT 0,
            descuento REAL DEFAULT 0,
            total REAL DEFAULT 0,
            cargo_habitacion INTEGER DEFAULT 0,
            forma_pago TEXT,
            importe_entregado REAL DEFAULT 0,
            cambio REAL DEFAULT 0,
            observaciones TEXT,
            usuario_apertura TEXT,
            usuario_cierre TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_apertura TEXT DEFAULT CURRENT_TIMESTAMP,
            fecha_cierre TEXT,
            updated_at TEXT
        )
        """
    )
    for col in [
        "numero TEXT",
        "punto_venta_id INTEGER",
        "terminal_id INTEGER",
        "mesa_id INTEGER",
        "mesa_nombre TEXT",
        "modo_servicio TEXT DEFAULT 'mesa'",
        "reserva_id INTEGER",
        "habitacion TEXT",
        "cliente TEXT",
        "pax INTEGER DEFAULT 0",
        "estado TEXT DEFAULT 'abierta'",
        "subtotal REAL DEFAULT 0",
        "descuento REAL DEFAULT 0",
        "total REAL DEFAULT 0",
        "cargo_habitacion INTEGER DEFAULT 0",
        "forma_pago TEXT",
        "importe_entregado REAL DEFAULT 0",
        "cambio REAL DEFAULT 0",
        "observaciones TEXT",
        "usuario_apertura TEXT",
        "usuario_cierre TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_apertura TEXT DEFAULT CURRENT_TIMESTAMP",
        "fecha_cierre TEXT",
        "updated_at TEXT",
        "caja_sesion_id INTEGER",
    ]:
        _add_column_if_missing(conn, "tpv_cuentas", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tpv_comandas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero TEXT,
            cuenta_id INTEGER,
            punto_venta_id INTEGER NOT NULL,
            terminal_id INTEGER,
            mesa_id INTEGER,
            mesa_nombre TEXT,
            modo_servicio TEXT DEFAULT 'mesa',
            reserva_id INTEGER,
            habitacion TEXT,
            cliente TEXT,
            pax INTEGER DEFAULT 0,
            estado TEXT DEFAULT 'enviada',
            subtotal REAL DEFAULT 0,
            suplemento REAL DEFAULT 0,
            total REAL DEFAULT 0,
            cargo_habitacion INTEGER DEFAULT 0,
            observaciones TEXT,
            avisos TEXT,
            usuario TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "numero TEXT",
        "cuenta_id INTEGER",
        "punto_venta_id INTEGER",
        "terminal_id INTEGER",
        "mesa_id INTEGER",
        "mesa_nombre TEXT",
        "modo_servicio TEXT DEFAULT 'mesa'",
        "reserva_id INTEGER",
        "habitacion TEXT",
        "cliente TEXT",
        "pax INTEGER DEFAULT 0",
        "estado TEXT DEFAULT 'enviada'",
        "subtotal REAL DEFAULT 0",
        "suplemento REAL DEFAULT 0",
        "total REAL DEFAULT 0",
        "cargo_habitacion INTEGER DEFAULT 0",
        "observaciones TEXT",
        "avisos TEXT",
        "camarero TEXT",
        "prioridad TEXT DEFAULT 'normal'",
        "usuario TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "tpv_comandas", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tpv_comanda_lineas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comanda_id INTEGER NOT NULL,
            punto_venta_id INTEGER NOT NULL,
            articulo_id INTEGER,
            producto_id INTEGER,
            receta_id INTEGER,
            nombre TEXT NOT NULL,
            cantidad REAL DEFAULT 1,
            precio REAL DEFAULT 0,
            suplemento REAL DEFAULT 0,
            total REAL DEFAULT 0,
            destino_preparacion TEXT DEFAULT 'sin_preparacion',
            estado TEXT DEFAULT 'pendiente',
            comentario TEXT,
            avisos TEXT,
            incluido INTEGER DEFAULT 0,
            recepcion_cargo_id INTEGER,
            usuario TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "comanda_id INTEGER",
        "punto_venta_id INTEGER",
        "articulo_id INTEGER",
        "producto_id INTEGER",
        "receta_id INTEGER",
        "nombre TEXT",
        "cantidad REAL DEFAULT 1",
        "precio REAL DEFAULT 0",
        "suplemento REAL DEFAULT 0",
        "total REAL DEFAULT 0",
        "destino_preparacion TEXT DEFAULT 'sin_preparacion'",
        "estado TEXT DEFAULT 'pendiente'",
        "comentario TEXT",
        "avisos TEXT",
        "prioridad TEXT DEFAULT 'normal'",
        "incluido INTEGER DEFAULT 0",
        "recepcion_cargo_id INTEGER",
        "usuario TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "tpv_comanda_lineas", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tpv_caja_sesiones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            punto_venta_id INTEGER NOT NULL,
            terminal_id INTEGER,
            fecha TEXT DEFAULT CURRENT_DATE,
            turno TEXT,
            estado TEXT DEFAULT 'abierta',
            saldo_inicial REAL DEFAULT 0,
            efectivo_teorico REAL DEFAULT 0,
            efectivo_contado REAL DEFAULT 0,
            tarjeta_teorico REAL DEFAULT 0,
            otros_teorico REAL DEFAULT 0,
            diferencia REAL DEFAULT 0,
            usuario_apertura TEXT,
            usuario_cierre TEXT,
            observaciones TEXT,
            arqueo_apertura_json TEXT,
            arqueo_cierre_json TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_apertura TEXT DEFAULT CURRENT_TIMESTAMP,
            fecha_cierre TEXT,
            updated_at TEXT
        )
        """
    )
    for col in [
        "punto_venta_id INTEGER",
        "terminal_id INTEGER",
        "fecha TEXT DEFAULT CURRENT_DATE",
        "turno TEXT",
        "estado TEXT DEFAULT 'abierta'",
        "saldo_inicial REAL DEFAULT 0",
        "efectivo_teorico REAL DEFAULT 0",
        "efectivo_contado REAL DEFAULT 0",
        "tarjeta_teorico REAL DEFAULT 0",
        "otros_teorico REAL DEFAULT 0",
        "diferencia REAL DEFAULT 0",
        "usuario_apertura TEXT",
        "usuario_cierre TEXT",
        "observaciones TEXT",
        "arqueo_apertura_json TEXT",
        "arqueo_cierre_json TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_apertura TEXT DEFAULT CURRENT_TIMESTAMP",
        "fecha_cierre TEXT",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "tpv_caja_sesiones", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tpv_caja_movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caja_sesion_id INTEGER NOT NULL,
            punto_venta_id INTEGER,
            terminal_id INTEGER,
            tipo TEXT DEFAULT 'retirada_recepcion',
            importe REAL DEFAULT 0,
            destino TEXT,
            motivo TEXT,
            usuario TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "caja_sesion_id INTEGER",
        "punto_venta_id INTEGER",
        "terminal_id INTEGER",
        "tipo TEXT DEFAULT 'retirada_recepcion'",
        "importe REAL DEFAULT 0",
        "destino TEXT",
        "motivo TEXT",
        "usuario TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "fecha TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "tpv_caja_movimientos", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tpv_mapa_elementos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            punto_venta_id INTEGER NOT NULL,
            tipo TEXT DEFAULT 'zona',
            etiqueta TEXT,
            x REAL DEFAULT 0,
            y REAL DEFAULT 0,
            ancho REAL DEFAULT 12,
            alto REAL DEFAULT 8,
            rotacion REAL DEFAULT 0,
            color TEXT DEFAULT '#334155',
            orden INTEGER DEFAULT 0,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            updated_at TEXT
        )
        """
    )
    for col in [
        "punto_venta_id INTEGER",
        "tipo TEXT DEFAULT 'zona'",
        "etiqueta TEXT",
        "x REAL DEFAULT 0",
        "y REAL DEFAULT 0",
        "ancho REAL DEFAULT 12",
        "alto REAL DEFAULT 8",
        "rotacion REAL DEFAULT 0",
        "color TEXT DEFAULT '#334155'",
        "orden INTEGER DEFAULT 0",
        "activo INTEGER DEFAULT 1",
        "empresa_id INTEGER DEFAULT 1",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "tpv_mapa_elementos", col)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tpv_buffet_entradas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            punto_venta_id INTEGER NOT NULL,
            terminal_id INTEGER,
            fecha TEXT DEFAULT CURRENT_DATE,
            servicio TEXT,
            reserva_id INTEGER,
            habitacion_id INTEGER,
            habitacion TEXT,
            titular TEXT,
            regimen TEXT,
            adultos INTEGER DEFAULT 0,
            menores INTEGER DEFAULT 0,
            observaciones TEXT,
            usuario TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_registro TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "punto_venta_id INTEGER",
        "terminal_id INTEGER",
        "fecha TEXT DEFAULT CURRENT_DATE",
        "servicio TEXT",
        "reserva_id INTEGER",
        "habitacion_id INTEGER",
        "habitacion TEXT",
        "titular TEXT",
        "regimen TEXT",
        "adultos INTEGER DEFAULT 0",
        "menores INTEGER DEFAULT 0",
        "observaciones TEXT",
        "usuario TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_registro TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "tpv_buffet_entradas", col)
    conn.commit()


def _puntos_venta(conn, empresa_id):
    centro_id = centro_activo_id()
    centro_sql = " AND centro_id = ?" if centro_id else ""
    params = [empresa_id] + ([centro_id] if centro_id else [])
    return conn.execute(
        f"""
        SELECT *
        FROM puntos_venta
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
          {centro_sql}
        ORDER BY nombre
        """,
        params,
    ).fetchall()


def _producto_existente(conn, empresa_id, nombre):
    return conn.execute(
        "SELECT * FROM productos WHERE empresa_id = ? AND LOWER(nombre) = LOWER(?) LIMIT 1",
        (empresa_id, nombre),
    ).fetchone()


def _codigo_producto_tpv(conn, empresa_id, familia):
    prefijo = "TPV" + "".join(ch for ch in (familia or "otros").upper() if ch.isalnum())[:4].ljust(4, "X")
    total = conn.execute(
        "SELECT COUNT(*) AS total FROM productos WHERE empresa_id = ? AND codigo LIKE ?",
        (empresa_id, f"{prefijo}%"),
    ).fetchone()["total"] or 0
    for extra in range(1, 1000):
        codigo = f"{prefijo}{total + extra:04d}"
        existe = conn.execute(
            "SELECT id FROM productos WHERE empresa_id = ? AND codigo = ?",
            (empresa_id, codigo),
        ).fetchone()
        if not existe:
            return codigo
    return f"{prefijo}{int(datetime.now().timestamp())}"


def _crear_producto_catalogo(conn, empresa_id, item):
    nombre, familia, unidad, conservacion, coste, precio_venta, destino, tipo_articulo = item
    existente = _producto_existente(conn, empresa_id, nombre)
    if existente:
        return existente["id"], False
    codigo = _codigo_producto_tpv(conn, empresa_id, familia)
    cursor = conn.execute(
        """
        INSERT INTO productos
        (nombre, codigo, cantidad, unidad, familia, precio, stock_minimo,
         tipo_conservacion, ubicacion, activo, empresa_id, visible_en_venta,
         formato_venta, precio_venta)
        VALUES (?, ?, 0, ?, ?, ?, 0, ?, 'TPV', 1, ?, 1, ?, ?)
        """,
        (nombre, codigo, unidad, familia, coste, conservacion, empresa_id, unidad, precio_venta),
    )
    return cursor.lastrowid, True


def _categoria_tpv(conn, empresa_id, punto_venta_id, nombre, tipo, orden):
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
    cursor = conn.execute(
        """
        INSERT INTO tpv_categorias
        (punto_venta_id, nombre, tipo, color, orden, activo, empresa_id)
        VALUES (?, ?, ?, ?, ?, 1, ?)
        """,
        (punto_venta_id, nombre, tipo, "#2563eb", orden, empresa_id),
    )
    return cursor.lastrowid


def _categoria_catalogo_tpv(familia, tipo_articulo, nombre=""):
    familia = (familia or "").lower()
    nombre = (nombre or "").lower()
    if familia == "bebidas_agua":
        return "Aguas", "bebidas", 10
    if familia == "bebidas_refrescos":
        return "Refrescos", "bebidas", 20
    if familia == "bebidas_cerveza":
        return "Cervezas", "bebidas", 30
    if familia == "bebidas_premium" and ("vino" in nombre or "cava" in nombre):
        return "Vinos", "bebidas", 40
    if familia == "bebidas_licores":
        return "Licores", "premium", 50
    if familia == "cafeteria":
        return "Cafes", "bebidas", 60
    if familia == "snacks":
        return "Snacks", "snacks", 70
    if familia == "helados":
        return "Helados", "snacks", 80
    if familia == "buffet" or tipo_articulo == "buffet":
        return "Buffet", "buffet", 90
    if tipo_articulo == "premium":
        return "Premium", "premium", 95
    return "Otros", "otros", 99


def _mapear_catalogo_punto(conn, empresa_id, punto_venta_id, productos_ids):
    categorias = {}
    creados = 0
    for item, producto_id in zip(CATALOGO_BASE_TPV, productos_ids):
        nombre, familia, unidad, conservacion, coste, precio_venta, destino, tipo_articulo = item
        cat_nombre, cat_tipo, cat_orden = _categoria_catalogo_tpv(familia, tipo_articulo, nombre)
        if cat_nombre not in categorias:
            categorias[cat_nombre] = _categoria_tpv(conn, empresa_id, punto_venta_id, cat_nombre, cat_tipo, cat_orden)
        existe = conn.execute(
            """
            SELECT id
            FROM tpv_articulos
            WHERE empresa_id = ? AND punto_venta_id = ? AND producto_id = ?
            LIMIT 1
            """,
            (empresa_id, punto_venta_id, producto_id),
        ).fetchone()
        if existe:
            continue
        incluido = "todo_incluido" if tipo_articulo in {"bebida", "snack", "buffet"} else ""
        suplemento = precio_venta if tipo_articulo == "premium" else 0
        conn.execute(
            """
            INSERT INTO tpv_articulos
            (punto_venta_id, categoria_id, producto_id, nombre, tipo, precio,
             incluido_regimenes, suplemento, destino_preparacion,
             visible_tpv, visible_comandero, orden, activo, empresa_id)
            VALUES (?, ?, ?, ?, 'producto', ?, ?, ?, ?, 1, 1, ?, 1, ?)
            """,
            (
                punto_venta_id,
                categorias[cat_nombre],
                producto_id,
                nombre,
                precio_venta,
                incluido,
                suplemento,
                destino,
                creados + 1,
                empresa_id,
            ),
        )
        creados += 1
    return creados


def _asegurar_catalogo_terminal_tpv(conn, empresa_id, punto_venta_id):
    if not punto_venta_id:
        return
    productos_ids = []
    for item in CATALOGO_BASE_TPV:
        producto_id, _creado = _crear_producto_catalogo(conn, empresa_id, item)
        productos_ids.append(producto_id)
    _mapear_catalogo_punto(conn, empresa_id, punto_venta_id, productos_ids)
    categorias = {}
    for item in CATALOGO_BASE_TPV:
        nombre, familia, _unidad, _conservacion, _coste, _precio, _destino, tipo_articulo = item
        cat_nombre, cat_tipo, cat_orden = _categoria_catalogo_tpv(familia, tipo_articulo, nombre)
        categorias[cat_nombre] = _categoria_tpv(conn, empresa_id, punto_venta_id, cat_nombre, cat_tipo, cat_orden)
    rows = conn.execute(
        """
        SELECT a.id, a.nombre, p.familia, a.tipo
        FROM tpv_articulos a
        LEFT JOIN productos p ON p.id = a.producto_id
        WHERE a.empresa_id = ? AND a.punto_venta_id = ?
        """,
        (empresa_id, punto_venta_id),
    ).fetchall()
    for row in rows:
        cat_nombre, _cat_tipo, _cat_orden = _categoria_catalogo_tpv(row["familia"] or "", row["tipo"] or "", row["nombre"] or "")
        categoria_id = categorias.get(cat_nombre)
        if categoria_id:
            conn.execute(
                "UPDATE tpv_articulos SET categoria_id = ? WHERE id = ? AND empresa_id = ?",
                (categoria_id, row["id"], empresa_id),
            )


def _crear_lote_productos_tpv(conn, empresa_id, punto_venta_id=None):
    productos_ids = []
    nuevos = 0
    for item in CATALOGO_BASE_TPV:
        producto_id, creado = _crear_producto_catalogo(conn, empresa_id, item)
        productos_ids.append(producto_id)
        nuevos += 1 if creado else 0
    articulos = 0
    if punto_venta_id:
        articulos = _mapear_catalogo_punto(conn, empresa_id, punto_venta_id, productos_ids)
    return nuevos, articulos


def _precio_sugerido_tpv(coste, multiplicador=3.2):
    coste = _to_float(coste, 0)
    if coste <= 0:
        return 0
    bruto = coste * multiplicador
    return round((int(bruto * 2 + 0.9999) / 2), 2)


def _parse_menu_fijo_tpv(texto):
    filas = []
    contenido = (texto or "").strip()
    if not contenido:
        return filas
    sample = contenido[:1200]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";\t,")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    reader = csv.reader(io.StringIO(contenido), dialect)
    for raw in reader:
        cols = [str(col or "").strip() for col in raw]
        if not cols or not any(cols):
            continue
        primera = cols[0].lower()
        if primera in {"categoria", "categoría", "familia", "seccion", "sección"}:
            continue
        if len(cols) == 1:
            categoria, nombre, precio, destino = "Menu fijo", cols[0], "0", "cocina"
        elif len(cols) == 2:
            categoria, nombre, precio, destino = "Menu fijo", cols[0], cols[1], "cocina"
        else:
            categoria, nombre, precio = cols[0], cols[1], cols[2]
            destino = cols[3] if len(cols) > 3 and cols[3] else "cocina"
        nombre = (nombre or "").strip()
        if not nombre:
            continue
        destino = destino if destino in DESTINOS_PREPARACION else "cocina"
        filas.append(
            {
                "categoria": categoria or "Menu fijo",
                "nombre": nombre,
                "precio": _to_float(precio, 0),
                "destino": destino,
            }
        )
    return filas


def _importar_menu_fijo_tpv(conn, empresa_id, punto_venta_id, texto):
    filas = _parse_menu_fijo_tpv(texto)
    creados = 0
    actualizados = 0
    productos_nuevos = 0
    categorias_cache = {}
    for orden, fila in enumerate(filas, start=1):
        categoria_nombre = fila["categoria"]
        if categoria_nombre not in categorias_cache:
            categorias_cache[categoria_nombre] = _categoria_tpv(
                conn,
                empresa_id,
                punto_venta_id,
                categoria_nombre,
                "menu_fijo",
                100 + len(categorias_cache),
            )
        producto_id, producto_creado = _crear_producto_catalogo(
            conn,
            empresa_id,
            (
                fila["nombre"],
                "menu_fijo",
                "servicio",
                "seco",
                0,
                fila["precio"],
                fila["destino"],
                "menu_fijo",
            ),
        )
        productos_nuevos += 1 if producto_creado else 0
        articulo = conn.execute(
            """
            SELECT *
            FROM tpv_articulos
            WHERE empresa_id = ?
              AND punto_venta_id = ?
              AND LOWER(nombre) = LOWER(?)
            LIMIT 1
            """,
            (empresa_id, punto_venta_id, fila["nombre"]),
        ).fetchone()
        if articulo:
            conn.execute(
                """
                UPDATE tpv_articulos
                SET categoria_id = ?, producto_id = ?, precio = ?, destino_preparacion = ?,
                    visible_tpv = 1, visible_comandero = 1, activo = 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND empresa_id = ?
                """,
                (
                    categorias_cache[categoria_nombre],
                    producto_id,
                    fila["precio"],
                    fila["destino"],
                    articulo["id"],
                    empresa_id,
                ),
            )
            actualizados += 1
        else:
            conn.execute(
                """
                INSERT INTO tpv_articulos
                (punto_venta_id, categoria_id, producto_id, nombre, tipo, precio,
                 incluido_regimenes, suplemento, destino_preparacion,
                 visible_tpv, visible_comandero, orden, activo, empresa_id)
                VALUES (?, ?, ?, ?, 'menu_fijo', ?, '', 0, ?, 1, 1, ?, 1, ?)
                """,
                (
                    punto_venta_id,
                    categorias_cache[categoria_nombre],
                    producto_id,
                    fila["nombre"],
                    fila["precio"],
                    fila["destino"],
                    orden,
                    empresa_id,
                ),
            )
            creados += 1
    return {
        "filas": len(filas),
        "creados": creados,
        "actualizados": actualizados,
        "productos_nuevos": productos_nuevos,
    }


def _numero_comanda(conn, empresa_id):
    prefijo = f"TPV{datetime.now().strftime('%Y%m%d')}"
    row = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM tpv_comandas
        WHERE empresa_id = ? AND numero LIKE ?
        """,
        (empresa_id, f"{prefijo}%"),
    ).fetchone()
    return f"{prefijo}-{(row['total'] or 0) + 1:04d}"


def _numero_cuenta(conn, empresa_id):
    prefijo = f"CTA{datetime.now().strftime('%Y%m%d')}"
    row = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM tpv_cuentas
        WHERE empresa_id = ? AND numero LIKE ?
        """,
        (empresa_id, f"{prefijo}%"),
    ).fetchone()
    return f"{prefijo}-{(row['total'] or 0) + 1:04d}"


def _mesas_punto(conn, empresa_id, punto_venta_id):
    return conn.execute(
        """
        SELECT *
        FROM tpv_mesas
        WHERE empresa_id = ? AND punto_venta_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY zona, CAST(numero AS INTEGER), numero, nombre
        """,
        (empresa_id, punto_venta_id),
    ).fetchall()


def _cuentas_abiertas_punto(conn, empresa_id, punto_venta_id):
    return conn.execute(
        """
        SELECT *
        FROM tpv_cuentas
        WHERE empresa_id = ?
          AND punto_venta_id = ?
          AND estado = 'abierta'
        ORDER BY fecha_apertura DESC, id DESC
        """,
        (empresa_id, punto_venta_id),
    ).fetchall()


def _caja_abierta_terminal(conn, empresa_id, terminal_id, punto_venta_id=None):
    filtros = ["empresa_id = ?", "terminal_id = ?", "estado = 'abierta'"]
    params = [empresa_id, terminal_id]
    if punto_venta_id:
        filtros.append("punto_venta_id = ?")
        params.append(punto_venta_id)
    return conn.execute(
        f"""
        SELECT *
        FROM tpv_caja_sesiones
        WHERE {" AND ".join(filtros)}
        ORDER BY fecha_apertura DESC, id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()


def _resumen_caja(conn, empresa_id, caja_id):
    row = conn.execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN forma_pago = 'efectivo' THEN total ELSE 0 END), 0) AS efectivo,
          COALESCE(SUM(CASE WHEN forma_pago = 'tarjeta' THEN total ELSE 0 END), 0) AS tarjeta,
          COALESCE(SUM(CASE WHEN forma_pago = 'cargo_habitacion' THEN total ELSE 0 END), 0) AS cargo_habitacion,
          COALESCE(SUM(CASE WHEN forma_pago = 'consumo_empleado' THEN total ELSE 0 END), 0) AS consumo_empleado,
          COALESCE(SUM(CASE WHEN forma_pago = 'invitacion' THEN total ELSE 0 END), 0) AS invitacion,
          COALESCE(SUM(CASE WHEN forma_pago NOT IN ('efectivo', 'tarjeta', 'cargo_habitacion', 'consumo_empleado', 'invitacion') OR forma_pago IS NULL THEN total ELSE 0 END), 0) AS otros,
          COUNT(*) AS cuentas
        FROM tpv_cuentas
        WHERE empresa_id = ?
          AND caja_sesion_id = ?
          AND estado IN ('cobrada', 'cerrada')
        """,
        (empresa_id, caja_id),
    ).fetchone()
    mov_row = conn.execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN tipo IN ('entrada', 'entrada_cambio', 'fondo_extra') THEN importe ELSE 0 END), 0) AS entradas,
          COALESCE(SUM(CASE WHEN tipo IN ('retirada_recepcion', 'retirada_direccion', 'retirada') THEN importe ELSE 0 END), 0) AS retiradas,
          COUNT(*) AS movimientos
        FROM tpv_caja_movimientos
        WHERE empresa_id = ?
          AND caja_sesion_id = ?
        """,
        (empresa_id, caja_id),
    ).fetchone()
    efectivo = _to_float(row["efectivo"] if row else 0)
    tarjeta = _to_float(row["tarjeta"] if row else 0)
    cargo_habitacion = _to_float(row["cargo_habitacion"] if row else 0)
    consumo_empleado = _to_float(row["consumo_empleado"] if row else 0)
    invitacion = _to_float(row["invitacion"] if row else 0)
    otros = _to_float(row["otros"] if row else 0)
    entradas = _to_float(mov_row["entradas"] if mov_row else 0)
    retiradas = _to_float(mov_row["retiradas"] if mov_row else 0)
    return {
        "efectivo": efectivo,
        "tarjeta": tarjeta,
        "cargo_habitacion": cargo_habitacion,
        "consumo_empleado": consumo_empleado,
        "invitacion": invitacion,
        "otros": otros,
        "entradas": entradas,
        "retiradas": retiradas,
        "movimientos": int(mov_row["movimientos"] or 0) if mov_row else 0,
        "efectivo_neto": round(efectivo + entradas - retiradas, 2),
        "cuentas": int(row["cuentas"] or 0) if row else 0,
        "total": round(efectivo + tarjeta + cargo_habitacion + consumo_empleado + invitacion + otros, 2),
    }


def _movimientos_caja(conn, empresa_id, caja_id):
    return conn.execute(
        """
        SELECT *
        FROM tpv_caja_movimientos
        WHERE empresa_id = ?
          AND caja_sesion_id = ?
        ORDER BY fecha DESC, id DESC
        """,
        (empresa_id, caja_id),
    ).fetchall()


def _ventas_articulos_caja(conn, empresa_id, caja_id):
    return conn.execute(
        """
        SELECT
          l.nombre,
          COALESCE(SUM(l.cantidad), 0) AS cantidad,
          COALESCE(SUM(l.total), 0) AS total
        FROM tpv_comanda_lineas l
        JOIN tpv_comandas co ON co.id = l.comanda_id
        JOIN tpv_cuentas cu ON cu.id = co.cuenta_id
        WHERE cu.empresa_id = ?
          AND cu.caja_sesion_id = ?
          AND cu.estado IN ('cobrada', 'cerrada')
          AND COALESCE(l.estado, '') != 'cancelada'
        GROUP BY l.nombre
        ORDER BY cantidad DESC, l.nombre
        """,
        (empresa_id, caja_id),
    ).fetchall()


def _consumo_stock_previsto_caja(conn, empresa_id, caja_id):
    return conn.execute(
        """
        SELECT producto_id, producto_nombre, unidad, SUM(cantidad) AS cantidad
        FROM (
            SELECT
              l.producto_id AS producto_id,
              p.nombre AS producto_nombre,
              p.unidad AS unidad,
              COALESCE(l.cantidad, 0) AS cantidad
            FROM tpv_comanda_lineas l
            JOIN tpv_comandas co ON co.id = l.comanda_id
            JOIN tpv_cuentas cu ON cu.id = co.cuenta_id
            LEFT JOIN productos p ON p.id = l.producto_id
            WHERE cu.empresa_id = ?
              AND cu.caja_sesion_id = ?
              AND cu.estado IN ('cobrada', 'cerrada')
              AND COALESCE(l.estado, '') != 'cancelada'
              AND l.producto_id IS NOT NULL
            UNION ALL
            SELECT
              ri.producto_id AS producto_id,
              p.nombre AS producto_nombre,
              p.unidad AS unidad,
              COALESCE(ri.cantidad, 0) * COALESCE(l.cantidad, 0) AS cantidad
            FROM tpv_comanda_lineas l
            JOIN tpv_comandas co ON co.id = l.comanda_id
            JOIN tpv_cuentas cu ON cu.id = co.cuenta_id
            JOIN recetas_ingredientes ri ON ri.receta_id = l.receta_id
            LEFT JOIN productos p ON p.id = ri.producto_id
            WHERE cu.empresa_id = ?
              AND cu.caja_sesion_id = ?
              AND cu.estado IN ('cobrada', 'cerrada')
              AND COALESCE(l.estado, '') != 'cancelada'
              AND l.receta_id IS NOT NULL
        )
        WHERE producto_id IS NOT NULL
        GROUP BY producto_id, producto_nombre, unidad
        HAVING SUM(cantidad) > 0
        ORDER BY producto_nombre
        """,
        (empresa_id, caja_id, empresa_id, caja_id),
    ).fetchall()


def _arqueo_desde_form(form, prefijo):
    lineas = []
    total = 0.0
    for item in DENOMINACIONES_ARQUEO_TPV:
        key = f"{prefijo}_{str(item['valor']).replace('.', '_')}"
        cantidad = max(_to_int(form.get(key), 0), 0)
        importe = round(cantidad * item["valor"], 2)
        lineas.append({
            "tipo": item["tipo"],
            "valor": item["valor"],
            "label": item["label"],
            "cantidad": cantidad,
            "importe": importe,
        })
        total += importe
    return {"total": round(total, 2), "lineas": lineas}


def _form_tiene_arqueo(form, prefijo):
    return any(key.startswith(f"{prefijo}_") for key in form.keys())


def _arqueo_json(value):
    if not value:
        return {"total": 0.0, "lineas": []}
    try:
        data = json.loads(value)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"total": 0.0, "lineas": []}


def _actualizar_cuenta_totales(conn, cuenta_id, empresa_id):
    resumen = conn.execute(
        """
        SELECT COALESCE(SUM(total), 0) AS subtotal
        FROM tpv_comandas
        WHERE cuenta_id = ?
          AND empresa_id = ?
          AND COALESCE(estado, 'enviada') <> 'cancelada'
        """,
        (cuenta_id, empresa_id),
    ).fetchone()
    subtotal = round(_to_float(resumen["subtotal"] if resumen else 0), 2)
    cuenta = conn.execute(
        "SELECT descuento FROM tpv_cuentas WHERE id = ? AND empresa_id = ?",
        (cuenta_id, empresa_id),
    ).fetchone()
    descuento = _to_float(cuenta["descuento"] if cuenta else 0, 0)
    total = max(round(subtotal - descuento, 2), 0)
    conn.execute(
        """
        UPDATE tpv_cuentas
        SET subtotal = ?, total = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (subtotal, total, cuenta_id, empresa_id),
    )
    return total


def _cuenta_para_comanda(
    conn,
    empresa_id,
    punto_venta_id,
    terminal_id,
    cuenta_id=None,
    mesa_id=None,
    mesa_nombre="",
    modo_servicio="mesa",
    reserva_id=None,
    habitacion="",
    cliente="",
    pax=0,
    cargo_habitacion=False,
    observaciones="",
    caja_sesion_id=None,
):
    if cuenta_id:
        cuenta = conn.execute(
            """
            SELECT *
            FROM tpv_cuentas
            WHERE id = ?
              AND empresa_id = ?
              AND punto_venta_id = ?
              AND estado = 'abierta'
            """,
            (cuenta_id, empresa_id, punto_venta_id),
        ).fetchone()
        if cuenta:
            return cuenta
    cuenta = None
    if mesa_id:
        cuenta = conn.execute(
            """
            SELECT *
            FROM tpv_cuentas
            WHERE empresa_id = ?
              AND punto_venta_id = ?
              AND mesa_id = ?
              AND estado = 'abierta'
            ORDER BY id DESC
            LIMIT 1
            """,
            (empresa_id, punto_venta_id, mesa_id),
        ).fetchone()
    if not cuenta and reserva_id:
        cuenta = conn.execute(
            """
            SELECT *
            FROM tpv_cuentas
            WHERE empresa_id = ?
              AND punto_venta_id = ?
              AND reserva_id = ?
              AND estado = 'abierta'
            ORDER BY id DESC
            LIMIT 1
            """,
            (empresa_id, punto_venta_id, reserva_id),
        ).fetchone()
    if not cuenta and habitacion:
        cuenta = conn.execute(
            """
            SELECT *
            FROM tpv_cuentas
            WHERE empresa_id = ?
              AND punto_venta_id = ?
              AND LOWER(COALESCE(habitacion, '')) = LOWER(?)
              AND estado = 'abierta'
            ORDER BY id DESC
            LIMIT 1
            """,
            (empresa_id, punto_venta_id, habitacion),
        ).fetchone()
    if not cuenta and mesa_nombre and not mesa_id:
        cuenta = conn.execute(
            """
            SELECT *
            FROM tpv_cuentas
            WHERE empresa_id = ?
              AND punto_venta_id = ?
              AND LOWER(COALESCE(mesa_nombre, '')) = LOWER(?)
              AND COALESCE(modo_servicio, '') = ?
              AND estado = 'abierta'
            ORDER BY id DESC
            LIMIT 1
            """,
            (empresa_id, punto_venta_id, mesa_nombre, modo_servicio),
        ).fetchone()
    if cuenta:
        return cuenta
    numero = _numero_cuenta(conn, empresa_id)
    cur = conn.execute(
        """
        INSERT INTO tpv_cuentas
        (numero, punto_venta_id, terminal_id, mesa_id, mesa_nombre, modo_servicio,
         reserva_id, habitacion, cliente, pax, estado, cargo_habitacion,
         observaciones, usuario_apertura, empresa_id, caja_sesion_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'abierta', ?, ?, ?, ?, ?)
        """,
        (
            numero,
            punto_venta_id,
            terminal_id,
            mesa_id,
            mesa_nombre,
            modo_servicio,
            reserva_id,
            habitacion,
            cliente,
            pax,
            1 if cargo_habitacion else 0,
            observaciones,
            _usuario_actual(),
            empresa_id,
            caja_sesion_id,
        ),
    )
    cuenta_id_nueva = cur.lastrowid
    _registrar_tpv_auditoria(
        conn,
        empresa_id,
        "tpv_cuenta",
        cuenta_id_nueva,
        "abrir_cuenta",
        despues={
            "numero": numero,
            "mesa": mesa_nombre,
            "habitacion": habitacion,
            "cliente": cliente,
            "modo_servicio": modo_servicio,
        },
        punto_venta_id=punto_venta_id,
        motivo="Apertura de ticket/cuenta TPV",
    )
    return conn.execute(
        "SELECT * FROM tpv_cuentas WHERE id = ? AND empresa_id = ?",
        (cuenta_id_nueva, empresa_id),
    ).fetchone()


def _lineas_json_form(value):
    try:
        data = json.loads(value or "[]")
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _terminal_contexto_form(form):
    servicio = form.get("servicio_actual") or form.get("servicio") or "desayuno"
    turno = form.get("turno_actual") or form.get("turno") or "manana"
    fecha = form.get("fecha_servicio") or form.get("fecha") or date.today().isoformat()
    if servicio not in SERVICIOS_BUFFET:
        servicio = "desayuno"
    if turno not in TURNOS_TPV:
        turno = "manana"
    fecha_obj = _parse_date(fecha, date.today())
    return servicio, turno, fecha_obj.isoformat()


def _nombre_terminal_contexto(punto, tipo, codigo, servicio):
    punto_nombre = punto["nombre"] if punto and "nombre" in punto.keys() else ""
    if punto_nombre:
        if (punto["tpv_tipo"] if punto and "tpv_tipo" in punto.keys() else "") == "buffet" or "buffet" in punto_nombre.lower():
            return f"{punto_nombre} - {SERVICIOS_BUFFET.get(servicio, servicio)}"
        return punto_nombre
    return f"{TIPOS_TERMINAL.get(tipo, 'Terminal')} {codigo[-6:]}"


def _actualizar_estado_cuenta_tpv(
    conn,
    empresa_id,
    cuenta,
    accion,
    forma_pago="",
    importe_entregado=None,
    cambio=None,
    motivo="",
):
    if accion not in {"cobrar", "cerrar", "cancelar", "reabrir"}:
        return None
    antes = _row_snapshot(
        cuenta,
        [
            "numero",
            "estado",
            "total",
            "forma_pago",
            "importe_entregado",
            "cambio",
            "mesa_nombre",
            "habitacion",
            "cliente",
        ],
    )
    total_cuenta = _to_float(cuenta["total"] if "total" in cuenta.keys() else 0, 0)
    if accion == "reabrir":
        nuevo_estado = "abierta"
        forma_pago_final = cuenta["forma_pago"] if "forma_pago" in cuenta.keys() else ""
        usuario_cierre = ""
        fecha_cierre_sql = "NULL"
        importe_entregado_final = 0
        cambio_final = 0
    else:
        nuevo_estado = {"cobrar": "cobrada", "cerrar": "cerrada", "cancelar": "cancelada"}[accion]
        forma_pago_final = forma_pago or ("cargo_habitacion" if cuenta["cargo_habitacion"] else "efectivo")
        usuario_cierre = _usuario_actual()
        fecha_cierre_sql = "CURRENT_TIMESTAMP"
        if nuevo_estado == "cobrada" and forma_pago_final == "efectivo":
            importe_entregado_final = _to_float(importe_entregado, 0)
            if importe_entregado_final <= 0:
                importe_entregado_final = total_cuenta
            cambio_final = max(round(_to_float(cambio, importe_entregado_final - total_cuenta), 2), 0)
        else:
            importe_entregado_final = 0
            cambio_final = 0
    conn.execute(
        f"""
        UPDATE tpv_cuentas
        SET estado = ?, forma_pago = ?, importe_entregado = ?, cambio = ?, usuario_cierre = ?,
            fecha_cierre = {fecha_cierre_sql}, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (
            nuevo_estado,
            forma_pago_final,
            importe_entregado_final,
            cambio_final,
            usuario_cierre,
            cuenta["id"],
            empresa_id,
        ),
    )
    if nuevo_estado in {"cobrada", "cerrada", "cancelada"}:
        conn.execute(
            """
            UPDATE tpv_comandas
            SET estado = CASE WHEN ? = 'cancelada' THEN 'cancelada' ELSE 'cobrada' END,
                updated_at = CURRENT_TIMESTAMP
            WHERE cuenta_id = ? AND empresa_id = ? AND COALESCE(estado, '') <> 'cancelada'
            """,
            (nuevo_estado, cuenta["id"], empresa_id),
        )
        if cuenta["mesa_id"]:
            conn.execute(
                """
                UPDATE tpv_mesas
                SET estado = 'libre', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND empresa_id = ?
                """,
                (cuenta["mesa_id"], empresa_id),
            )
    elif nuevo_estado == "abierta" and cuenta["mesa_id"]:
        conn.execute(
            """
            UPDATE tpv_mesas
            SET estado = 'ocupada', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND empresa_id = ?
            """,
            (cuenta["mesa_id"], empresa_id),
        )
    despues = dict(antes)
    despues.update(
        {
            "estado": nuevo_estado,
            "forma_pago": forma_pago_final,
            "importe_entregado": importe_entregado_final,
            "cambio": cambio_final,
        }
    )
    _registrar_tpv_auditoria(
        conn,
        empresa_id,
        "tpv_cuenta",
        cuenta["id"],
        accion,
        antes=antes,
        despues=despues,
        punto_venta_id=cuenta["punto_venta_id"],
        motivo=motivo or "",
    )
    return nuevo_estado


def _mesa_nombre_tpv(row):
    if not row:
        return ""
    return row["nombre"] or row["numero"] or f"Mesa {row['id']}"


def _cuenta_abierta_mesa(conn, empresa_id, punto_venta_id, mesa_id):
    return conn.execute(
        """
        SELECT *
        FROM tpv_cuentas
        WHERE empresa_id = ?
          AND punto_venta_id = ?
          AND mesa_id = ?
          AND estado = 'abierta'
        ORDER BY id DESC
        LIMIT 1
        """,
        (empresa_id, punto_venta_id, mesa_id),
    ).fetchone()


def _cuenta_abierta_servicio(conn, empresa_id, punto_venta_id, modo_servicio, mesa_nombre):
    return conn.execute(
        """
        SELECT *
        FROM tpv_cuentas
        WHERE empresa_id = ?
          AND punto_venta_id = ?
          AND mesa_id IS NULL
          AND LOWER(COALESCE(mesa_nombre, '')) = LOWER(?)
          AND COALESCE(modo_servicio, '') = ?
          AND estado = 'abierta'
        ORDER BY id DESC
        LIMIT 1
        """,
        (empresa_id, punto_venta_id, mesa_nombre, modo_servicio),
    ).fetchone()


def _liberar_mesa_si_sin_cuenta(conn, empresa_id, punto_venta_id, mesa_id, cuenta_id_ignorar=None):
    if not mesa_id:
        return
    cuenta_abierta = _cuenta_abierta_mesa(conn, empresa_id, punto_venta_id, mesa_id)
    if not cuenta_abierta or (cuenta_id_ignorar and cuenta_abierta["id"] == cuenta_id_ignorar):
        conn.execute(
            "UPDATE tpv_mesas SET estado = 'libre', updated_at = CURRENT_TIMESTAMP WHERE id = ? AND empresa_id = ?",
            (mesa_id, empresa_id),
        )


def _traspasar_cuenta_mesa_tpv(conn, empresa_id, cuenta, mesa_destino):
    mesa_origen_id = cuenta["mesa_id"] if "mesa_id" in cuenta.keys() else None
    conn.execute(
        """
        UPDATE tpv_cuentas
        SET mesa_id = ?, mesa_nombre = ?, modo_servicio = 'mesa', updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (mesa_destino["id"], _mesa_nombre_tpv(mesa_destino), cuenta["id"], empresa_id),
    )
    conn.execute(
        """
        UPDATE tpv_comandas
        SET mesa_id = ?, mesa_nombre = ?, modo_servicio = 'mesa', updated_at = CURRENT_TIMESTAMP
        WHERE cuenta_id = ? AND empresa_id = ?
        """,
        (mesa_destino["id"], _mesa_nombre_tpv(mesa_destino), cuenta["id"], empresa_id),
    )
    conn.execute(
        "UPDATE tpv_mesas SET estado = 'ocupada', updated_at = CURRENT_TIMESTAMP WHERE id = ? AND empresa_id = ?",
        (mesa_destino["id"], empresa_id),
    )
    if mesa_origen_id and mesa_origen_id != mesa_destino["id"]:
        _liberar_mesa_si_sin_cuenta(conn, empresa_id, cuenta["punto_venta_id"], mesa_origen_id, cuenta["id"])


def _traspasar_cuenta_servicio_tpv(conn, empresa_id, cuenta, modo_destino, etiqueta_destino):
    mesa_origen_id = cuenta["mesa_id"] if "mesa_id" in cuenta.keys() else None
    conn.execute(
        """
        UPDATE tpv_cuentas
        SET mesa_id = NULL, mesa_nombre = ?, modo_servicio = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (etiqueta_destino, modo_destino, cuenta["id"], empresa_id),
    )
    conn.execute(
        """
        UPDATE tpv_comandas
        SET mesa_id = NULL, mesa_nombre = ?, modo_servicio = ?, updated_at = CURRENT_TIMESTAMP
        WHERE cuenta_id = ? AND empresa_id = ?
        """,
        (etiqueta_destino, modo_destino, cuenta["id"], empresa_id),
    )
    if mesa_origen_id:
        _liberar_mesa_si_sin_cuenta(conn, empresa_id, cuenta["punto_venta_id"], mesa_origen_id, cuenta["id"])


def _juntar_cuentas_tpv(conn, empresa_id, cuenta_origen, cuenta_destino, mesa_destino):
    conn.execute(
        """
        UPDATE tpv_comandas
        SET cuenta_id = ?, mesa_id = ?, mesa_nombre = ?, updated_at = CURRENT_TIMESTAMP
        WHERE cuenta_id = ? AND empresa_id = ?
        """,
        (cuenta_destino["id"], mesa_destino["id"], _mesa_nombre_tpv(mesa_destino), cuenta_origen["id"], empresa_id),
    )
    _actualizar_cuenta_totales(conn, cuenta_destino["id"], empresa_id)
    conn.execute(
        """
        UPDATE tpv_cuentas
        SET estado = 'cerrada', observaciones = COALESCE(observaciones, '') || ?,
            total = 0, subtotal = 0, fecha_cierre = CURRENT_TIMESTAMP,
            usuario_cierre = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (f"\nJuntada con cuenta {cuenta_destino['numero']}.", _usuario_actual(), cuenta_origen["id"], empresa_id),
    )
    if cuenta_origen["mesa_id"] and cuenta_origen["mesa_id"] != mesa_destino["id"]:
        conn.execute(
            "UPDATE tpv_mesas SET estado = 'libre', updated_at = CURRENT_TIMESTAMP WHERE id = ? AND empresa_id = ?",
            (cuenta_origen["mesa_id"], empresa_id),
        )
    conn.execute(
        "UPDATE tpv_mesas SET estado = 'ocupada', updated_at = CURRENT_TIMESTAMP WHERE id = ? AND empresa_id = ?",
        (mesa_destino["id"], empresa_id),
    )


def _juntar_cuentas_servicio_tpv(conn, empresa_id, cuenta_origen, cuenta_destino, modo_destino, etiqueta_destino):
    conn.execute(
        """
        UPDATE tpv_comandas
        SET cuenta_id = ?, mesa_id = NULL, mesa_nombre = ?, modo_servicio = ?, updated_at = CURRENT_TIMESTAMP
        WHERE cuenta_id = ? AND empresa_id = ?
        """,
        (cuenta_destino["id"], etiqueta_destino, modo_destino, cuenta_origen["id"], empresa_id),
    )
    _actualizar_cuenta_totales(conn, cuenta_destino["id"], empresa_id)
    conn.execute(
        """
        UPDATE tpv_cuentas
        SET estado = 'cerrada', observaciones = COALESCE(observaciones, '') || ?,
            total = 0, subtotal = 0, fecha_cierre = CURRENT_TIMESTAMP,
            usuario_cierre = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (f"\nJuntada con cuenta {cuenta_destino['numero']}.", _usuario_actual(), cuenta_origen["id"], empresa_id),
    )
    if cuenta_origen["mesa_id"]:
        _liberar_mesa_si_sin_cuenta(conn, empresa_id, cuenta_origen["punto_venta_id"], cuenta_origen["mesa_id"], cuenta_origen["id"])


def _ensure_recepcion_for_tpv(conn):
    try:
        from blueprints.recepcion.routes import _ensure_recepcion_schema

        _ensure_recepcion_schema(conn)
    except Exception:
        pass
    if _table_exists(conn, "recepcion_cargos_reserva"):
        for col in [
            "tpv_comanda_id INTEGER",
            "tpv_linea_id INTEGER",
        ]:
            _add_column_if_missing(conn, "recepcion_cargos_reserva", col)


def _registrar_cargo_habitacion_cuenta_tpv(conn, empresa_id, cuenta):
    _ensure_recepcion_for_tpv(conn)
    if not _table_exists(conn, "recepcion_cargos_reserva"):
        return False, "No existe la tabla de cargos de recepcion."
    reserva_id = _to_int(cuenta["reserva_id"] if "reserva_id" in cuenta.keys() else 0, 0)
    habitacion = (cuenta["habitacion"] if "habitacion" in cuenta.keys() else "") or ""
    reserva = _reserva_activa_tpv(conn, empresa_id, reserva_id if reserva_id else None, habitacion)
    if not reserva:
        return False, "No hay una reserva activa para cargar a habitacion."
    existentes = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM tpv_comanda_lineas l
        JOIN tpv_comandas c ON l.comanda_id = c.id
        WHERE c.cuenta_id = ?
          AND c.empresa_id = ?
          AND l.empresa_id = ?
          AND l.recepcion_cargo_id IS NOT NULL
        """,
        (cuenta["id"], empresa_id, empresa_id),
    ).fetchone()
    if existentes and (existentes["total"] or 0) > 0:
        return True, ""
    total = _to_float(cuenta["total"] if "total" in cuenta.keys() else 0, 0)
    if total <= 0:
        return True, ""
    conn.execute(
        """
        INSERT INTO recepcion_cargos_reserva
        (reserva_id, punto_venta_id, fecha, servicio, concepto, cantidad,
         precio_unitario, total, incluido, cargo_habitacion, estado,
         observaciones, empresa_id, usuario_id, tpv_comanda_id, tpv_linea_id)
        VALUES (?, ?, ?, 'tpv', ?, 1, ?, ?, 0, 1, 'pendiente', ?, ?, ?, NULL, NULL)
        """,
        (
            reserva["id"],
            cuenta["punto_venta_id"],
            date.today().isoformat(),
            f"TPV cuenta {cuenta['numero']}",
            total,
            total,
            f"Cargo a habitacion desde TPV. Cuenta {cuenta['numero']}.",
            empresa_id,
            session.get("user_id"),
        ),
    )
    return True, ""


def _punto_venta_por_id(conn, empresa_id, punto_venta_id):
    return conn.execute(
        """
        SELECT *
        FROM puntos_venta
        WHERE id = ? AND empresa_id = ?
        """,
        (punto_venta_id, empresa_id),
    ).fetchone()


def _crear_servicios_buffet_base(conn, empresa_id, punto_venta_id):
    creados = 0
    for servicio, nombre, hora_inicio, hora_fin, precio_adulto, precio_menor in SERVICIOS_BUFFET_BASE:
        existe = conn.execute(
            """
            SELECT id
            FROM tpv_servicios_buffet
            WHERE empresa_id = ? AND punto_venta_id = ? AND servicio = ?
            LIMIT 1
            """,
            (empresa_id, punto_venta_id, servicio),
        ).fetchone()
        if existe:
            continue
        conn.execute(
            """
            INSERT INTO tpv_servicios_buffet
            (punto_venta_id, servicio, nombre, hora_inicio, hora_fin,
             precio_adulto, precio_menor, activo, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                punto_venta_id,
                servicio,
                nombre,
                hora_inicio,
                hora_fin,
                precio_adulto,
                precio_menor,
                empresa_id,
            ),
        )
        creados += 1
    return creados


def _servicios_regimen_tpv(regimen):
    regimen = regimen or "solo_alojamiento"
    if regimen == "alojamiento_desayuno":
        return ["desayuno"]
    if regimen == "media_pension":
        return ["desayuno", "cena"]
    if regimen == "pension_completa":
        return ["desayuno", "comida", "cena"]
    if regimen == "todo_incluido":
        return ["desayuno", "comida", "cena", "bebidas_snacks"]
    return []


def _servicio_aplica_fecha_tpv(servicio, entrada, salida, fecha_obj):
    if not entrada or not salida or not fecha_obj:
        return False
    if servicio == "desayuno":
        return entrada < fecha_obj <= salida
    return entrada <= fecha_obj < salida


def _habitacion_nombre(row):
    if not row:
        return "-"
    numero = row["habitacion_numero"] if "habitacion_numero" in row.keys() else None
    nombre = row["habitacion_nombre"] if "habitacion_nombre" in row.keys() else None
    return numero or nombre or "-"


def _reservas_para_buffet(conn, empresa_id, fecha_obj, servicio):
    if not _table_exists(conn, "reservas_recepcion"):
        return []
    rows = conn.execute(
        """
        SELECT r.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        WHERE r.empresa_id = ?
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'no_show')
          AND date(r.fecha_entrada) <= date(?)
          AND date(r.fecha_salida) >= date(?)
        ORDER BY h.numero, r.titular_nombre, r.codigo
        """,
        (empresa_id, fecha_obj.isoformat(), fecha_obj.isoformat()),
    ).fetchall()
    reservas = []
    for row in rows:
        entrada = _parse_date(row["fecha_entrada"])
        salida = _parse_date(row["fecha_salida"])
        servicios = _servicios_regimen_tpv(row["regimen"])
        if servicio not in servicios:
            continue
        if not _servicio_aplica_fecha_tpv(servicio, entrada, salida, fecha_obj):
            continue
        reservas.append(row)
    return reservas


def _reservas_activas_tpv(conn, empresa_id, fecha_obj=None):
    fecha_obj = fecha_obj or date.today()
    if not _table_exists(conn, "reservas_recepcion"):
        return []
    return conn.execute(
        """
        SELECT r.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        WHERE r.empresa_id = ?
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'no_show', 'checkout')
          AND date(r.fecha_entrada) <= date(?)
          AND date(r.fecha_salida) >= date(?)
        ORDER BY h.numero, r.titular_nombre, r.codigo
        """,
        (empresa_id, fecha_obj.isoformat(), fecha_obj.isoformat()),
    ).fetchall()


def _reserva_activa_tpv(conn, empresa_id, reserva_id=None, habitacion="", fecha_obj=None):
    fecha_obj = fecha_obj or date.today()
    if not _table_exists(conn, "reservas_recepcion"):
        return None
    if reserva_id:
        row = conn.execute(
            """
            SELECT r.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre
            FROM reservas_recepcion r
            LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
            WHERE r.id = ?
              AND r.empresa_id = ?
              AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'no_show', 'checkout')
            LIMIT 1
            """,
            (reserva_id, empresa_id),
        ).fetchone()
        if row:
            return row
    habitacion = (habitacion or "").strip()
    if not habitacion:
        return None
    return conn.execute(
        """
        SELECT r.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        WHERE r.empresa_id = ?
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'no_show', 'checkout')
          AND date(r.fecha_entrada) <= date(?)
          AND date(r.fecha_salida) >= date(?)
          AND (
              LOWER(COALESCE(h.numero, '')) = LOWER(?)
              OR LOWER(COALESCE(h.nombre, '')) = LOWER(?)
          )
        ORDER BY date(r.fecha_entrada) DESC, r.id DESC
        LIMIT 1
        """,
        (empresa_id, fecha_obj.isoformat(), fecha_obj.isoformat(), habitacion, habitacion),
    ).fetchone()


def _articulo_incluido_regimen(articulo, regimen):
    incluidos = (articulo["incluido_regimenes"] if "incluido_regimenes" in articulo.keys() else "") or ""
    valores = {item.strip() for item in incluidos.replace(";", ",").split(",") if item.strip()}
    return bool(regimen and regimen in valores)


def _entradas_buffet_resumen(conn, empresa_id, punto_venta_id, fecha_txt, servicio):
    rows = conn.execute(
        """
        SELECT reserva_id,
               SUM(COALESCE(adultos, 0)) AS adultos,
               SUM(COALESCE(menores, 0)) AS menores
        FROM tpv_buffet_entradas
        WHERE empresa_id = ?
          AND punto_venta_id = ?
          AND fecha = ?
          AND servicio = ?
        GROUP BY reserva_id
        """,
        (empresa_id, punto_venta_id, fecha_txt, servicio),
    ).fetchall()
    return {
        row["reserva_id"] or 0: {
            "adultos": row["adultos"] or 0,
            "menores": row["menores"] or 0,
            "total": (row["adultos"] or 0) + (row["menores"] or 0),
        }
        for row in rows
    }


def _entrada_buffet_reserva_total(conn, empresa_id, punto_venta_id, fecha_txt, servicio, reserva_id):
    row = conn.execute(
        """
        SELECT COALESCE(SUM(adultos), 0) AS adultos,
               COALESCE(SUM(menores), 0) AS menores
        FROM tpv_buffet_entradas
        WHERE empresa_id = ?
          AND punto_venta_id = ?
          AND fecha = ?
          AND servicio = ?
          AND reserva_id = ?
        """,
        (empresa_id, punto_venta_id, fecha_txt, servicio, reserva_id),
    ).fetchone()
    adultos = row["adultos"] or 0
    menores = row["menores"] or 0
    return {"adultos": adultos, "menores": menores, "total": adultos + menores}


def _precios_servicio_buffet(conn, empresa_id, punto_venta_id, servicio):
    row = conn.execute(
        """
        SELECT precio_adulto, precio_menor
        FROM tpv_servicios_buffet
        WHERE empresa_id = ?
          AND punto_venta_id = ?
          AND servicio = ?
          AND COALESCE(activo, 1) = 1
        ORDER BY hora_inicio, id
        LIMIT 1
        """,
        (empresa_id, punto_venta_id, servicio),
    ).fetchone()
    if row:
        return _to_float(row["precio_adulto"], 0), _to_float(row["precio_menor"], 0)
    base = next((item for item in SERVICIOS_BUFFET_BASE if item[0] == servicio), None)
    if base:
        return _to_float(base[4], 0), _to_float(base[5], 0)
    return 0, 0


def _extras_acceso_buffet(conn, empresa_id, punto_venta_id, fecha_txt, servicio, reserva, adultos, menores):
    if not reserva:
        return adultos, menores
    servicios = _servicios_regimen_tpv(reserva["regimen"])
    entrada = _entrada_buffet_reserva_total(conn, empresa_id, punto_venta_id, fecha_txt, servicio, reserva["id"])
    fecha_obj = _parse_date(fecha_txt, date.today())
    derecho = servicio in servicios and _servicio_aplica_fecha_tpv(
        servicio,
        _parse_date(reserva["fecha_entrada"]),
        _parse_date(reserva["fecha_salida"]),
        fecha_obj,
    )
    adultos_pendientes = max(_to_int(reserva["adultos"], 0) - _to_int(entrada["adultos"], 0), 0) if derecho else 0
    menores_pendientes = max(_to_int(reserva["menores"], 0) - _to_int(entrada["menores"], 0), 0) if derecho else 0
    extra_adultos = max(_to_int(adultos, 0) - adultos_pendientes, 0)
    extra_menores = max(_to_int(menores, 0) - menores_pendientes, 0)
    return extra_adultos, extra_menores


def _crear_ticket_extra_buffet(
    conn,
    empresa_id,
    punto_venta_id,
    terminal_id,
    fecha_txt,
    servicio,
    reserva=None,
    habitacion="",
    titular="",
    extra_adultos=0,
    extra_menores=0,
):
    extra_adultos = max(_to_int(extra_adultos, 0), 0)
    extra_menores = max(_to_int(extra_menores, 0), 0)
    if extra_adultos + extra_menores <= 0:
        return None
    precio_adulto, precio_menor = _precios_servicio_buffet(conn, empresa_id, punto_venta_id, servicio)
    cuenta_numero = _numero_cuenta(conn, empresa_id)
    cuenta_cur = conn.execute(
        """
        INSERT INTO tpv_cuentas
        (numero, punto_venta_id, terminal_id, mesa_nombre, modo_servicio,
         reserva_id, habitacion, cliente, pax, estado, cargo_habitacion,
         observaciones, usuario_apertura, empresa_id)
        VALUES (?, ?, ?, ?, 'buffet', ?, ?, ?, ?, 'abierta', 0, ?, ?, ?)
        """,
        (
            cuenta_numero,
            punto_venta_id,
            terminal_id,
            f"Extra {SERVICIOS_BUFFET.get(servicio, servicio)}",
            reserva["id"] if reserva else None,
            habitacion,
            titular,
            extra_adultos + extra_menores,
            f"Extras de acceso {SERVICIOS_BUFFET.get(servicio, servicio)} {fecha_txt}",
            _usuario_actual(),
            empresa_id,
        ),
    )
    cuenta = conn.execute(
        "SELECT * FROM tpv_cuentas WHERE id = ? AND empresa_id = ?",
        (cuenta_cur.lastrowid, empresa_id),
    ).fetchone()
    numero = _numero_comanda(conn, empresa_id)
    total_adultos = round(extra_adultos * precio_adulto, 2)
    total_menores = round(extra_menores * precio_menor, 2)
    total = round(total_adultos + total_menores, 2)
    cur = conn.execute(
        """
        INSERT INTO tpv_comandas
        (numero, cuenta_id, punto_venta_id, terminal_id, modo_servicio, reserva_id,
         habitacion, cliente, pax, estado, subtotal, suplemento, total,
         cargo_habitacion, observaciones, avisos, usuario, empresa_id)
        VALUES (?, ?, ?, ?, 'buffet', ?, ?, ?, ?, 'enviada', ?, 0, ?, 0, ?, ?, ?, ?)
        """,
        (
            numero,
            cuenta["id"],
            punto_venta_id,
            terminal_id,
            reserva["id"] if reserva else None,
            habitacion,
            titular,
            extra_adultos + extra_menores,
            total,
            total,
            f"Acompanantes extra no incluidos en reserva. Servicio {SERVICIOS_BUFFET.get(servicio, servicio)}.",
            f"Extra acceso {fecha_txt}",
            _usuario_actual(),
            empresa_id,
        ),
    )
    comanda_id = cur.lastrowid
    if extra_adultos:
        conn.execute(
            """
            INSERT INTO tpv_comanda_lineas
            (comanda_id, punto_venta_id, nombre, cantidad, precio, suplemento, total,
             destino_preparacion, estado, comentario, usuario, empresa_id)
            VALUES (?, ?, ?, ?, ?, 0, ?, 'sin_preparacion', 'entregada', ?, ?, ?)
            """,
            (
                comanda_id,
                punto_venta_id,
                f"Extra buffet {SERVICIOS_BUFFET.get(servicio, servicio)} adulto",
                extra_adultos,
                precio_adulto,
                total_adultos,
                "Acompanante no incluido en reserva",
                _usuario_actual(),
                empresa_id,
            ),
        )
    if extra_menores:
        conn.execute(
            """
            INSERT INTO tpv_comanda_lineas
            (comanda_id, punto_venta_id, nombre, cantidad, precio, suplemento, total,
             destino_preparacion, estado, comentario, usuario, empresa_id)
            VALUES (?, ?, ?, ?, ?, 0, ?, 'sin_preparacion', 'entregada', ?, ?, ?)
            """,
            (
                comanda_id,
                punto_venta_id,
                f"Extra buffet {SERVICIOS_BUFFET.get(servicio, servicio)} menor",
                extra_menores,
                precio_menor,
                total_menores,
                "Acompanante menor no incluido en reserva",
                _usuario_actual(),
                empresa_id,
            ),
        )
    _actualizar_cuenta_totales(conn, cuenta["id"], empresa_id)
    _registrar_tpv_auditoria(
        conn,
        empresa_id,
        "tpv_cuenta",
        cuenta["id"],
        "extra_buffet",
        despues={
            "servicio": servicio,
            "adultos_extra": extra_adultos,
            "menores_extra": extra_menores,
            "total": total,
            "reserva": reserva["codigo"] if reserva and "codigo" in reserva.keys() else "",
        },
        punto_venta_id=punto_venta_id,
        motivo="Acompanantes no incluidos en reserva",
    )
    return {
        "cuenta_id": cuenta["id"],
        "numero": cuenta["numero"],
        "total": total,
        "adultos": extra_adultos,
        "menores": extra_menores,
    }


def _buscar_reservas_acceso(conn, empresa_id, busqueda, fecha_obj):
    busqueda = (busqueda or "").strip()
    if not busqueda:
        return []
    like = f"%{busqueda}%"
    rows = conn.execute(
        """
        SELECT r.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
        WHERE r.empresa_id = ?
          AND COALESCE(r.estado, 'confirmada') NOT IN ('cancelada', 'no_show', 'checkout')
          AND date(r.fecha_entrada) <= date(?)
          AND date(r.fecha_salida) >= date(?)
          AND (
              LOWER(COALESCE(h.numero, '')) LIKE LOWER(?)
              OR LOWER(COALESCE(h.nombre, '')) LIKE LOWER(?)
              OR LOWER(COALESCE(r.codigo, '')) LIKE LOWER(?)
              OR LOWER(COALESCE(r.titular_nombre, '')) LIKE LOWER(?)
              OR LOWER(COALESCE(r.localizador_externo, '')) LIKE LOWER(?)
          )
        ORDER BY CASE WHEN LOWER(COALESCE(h.numero, '')) = LOWER(?) THEN 0 ELSE 1 END,
                 h.numero, r.titular_nombre, r.codigo
        LIMIT 12
        """,
        (
            empresa_id,
            fecha_obj.isoformat(),
            fecha_obj.isoformat(),
            like,
            like,
            like,
            like,
            like,
            busqueda,
        ),
    ).fetchall()
    return rows


@tpv_bp.route("/")
@login_required
def dashboard():
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    puntos = conn.execute(
        """
        SELECT pv.*,
               (SELECT COUNT(*) FROM tpv_terminales t WHERE t.punto_venta_id = pv.id AND t.empresa_id = pv.empresa_id) AS terminales,
               (SELECT COUNT(*) FROM tpv_articulos a WHERE a.punto_venta_id = pv.id AND a.empresa_id = pv.empresa_id AND COALESCE(a.activo, 1) = 1) AS articulos,
               (SELECT COUNT(*) FROM tpv_servicios_buffet s WHERE s.punto_venta_id = pv.id AND s.empresa_id = pv.empresa_id AND COALESCE(s.activo, 1) = 1) AS servicios_buffet
        FROM puntos_venta pv
        WHERE pv.empresa_id = ?
        ORDER BY pv.nombre
        """,
        (empresa_id,),
    ).fetchall()
    terminales = conn.execute(
        """
        SELECT t.*, pv.nombre AS punto_nombre
        FROM tpv_terminales t
        LEFT JOIN puntos_venta pv ON t.punto_venta_id = pv.id
        WHERE t.empresa_id = ?
        ORDER BY COALESCE(t.activo, 1) DESC, pv.nombre, t.nombre
        LIMIT 12
        """,
        (empresa_id,),
    ).fetchall()
    conn.close()
    return render_template(
        "tpv/dashboard.html",
        puntos=puntos,
        terminales=terminales,
        tipos_punto=TIPOS_PUNTO_TPV,
        tipos_terminal=TIPOS_TERMINAL,
    )


@tpv_bp.route("/puntos/<int:punto_venta_id>/config", methods=["GET", "POST"])
@login_required
def punto_config(punto_venta_id):
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        bloqueo = _requiere_gestion_tpv(conn)
        if bloqueo:
            return bloqueo
    punto = _punto_venta_por_id(conn, empresa_id, punto_venta_id)
    if not punto:
        conn.close()
        abort(404)
    if request.method == "POST":
        accion = request.form.get("accion") or "config"
        if accion == "config":
            conn.execute(
                """
                UPDATE puntos_venta
                SET tpv_tipo = ?, tpv_control_buffet = ?, tpv_admite_cargo_habitacion = ?,
                    tpv_permite_todo_incluido = ?, tpv_bebidas_incluidas = ?,
                    imprimir_comanda = ?
                WHERE id = ? AND empresa_id = ?
                """,
                (
                    request.form.get("tpv_tipo") or "bar",
                    1 if request.form.get("tpv_control_buffet") else 0,
                    1 if request.form.get("tpv_admite_cargo_habitacion") else 0,
                    1 if request.form.get("tpv_permite_todo_incluido") else 0,
                    1 if request.form.get("tpv_bebidas_incluidas") else 0,
                    1 if request.form.get("imprimir_comanda") else 0,
                    punto_venta_id,
                    empresa_id,
                ),
            )
            flash("Configuracion TPV guardada.", "success")
        elif accion == "servicios_base":
            creados = _crear_servicios_buffet_base(conn, empresa_id, punto_venta_id)
            flash(f"Servicios buffet revisados. Nuevos: {creados}.", "success")
        elif accion == "servicio":
            servicio = request.form.get("servicio") or ""
            nombre = (request.form.get("nombre") or SERVICIOS_BUFFET.get(servicio, servicio)).strip()
            if servicio and nombre:
                conn.execute(
                    """
                    INSERT INTO tpv_servicios_buffet
                    (punto_venta_id, servicio, nombre, hora_inicio, hora_fin,
                     precio_adulto, precio_menor, activo, empresa_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        punto_venta_id,
                        servicio,
                        nombre,
                        request.form.get("hora_inicio") or "",
                        request.form.get("hora_fin") or "",
                        _to_float(request.form.get("precio_adulto"), 0),
                        _to_float(request.form.get("precio_menor"), 0),
                        empresa_id,
                    ),
                )
                flash("Servicio buffet anadido.", "success")
        elif accion == "toggle_servicio":
            servicio_id = request.form.get("servicio_id") or ""
            if servicio_id.isdigit():
                row = conn.execute(
                    """
                    SELECT activo
                    FROM tpv_servicios_buffet
                    WHERE id = ? AND punto_venta_id = ? AND empresa_id = ?
                    """,
                    (int(servicio_id), punto_venta_id, empresa_id),
                ).fetchone()
                if row:
                    conn.execute(
                        """
                        UPDATE tpv_servicios_buffet
                        SET activo = ?
                        WHERE id = ? AND punto_venta_id = ? AND empresa_id = ?
                        """,
                        (0 if row["activo"] else 1, int(servicio_id), punto_venta_id, empresa_id),
                    )
        conn.commit()
        conn.close()
        return redirect(url_for("tpv.punto_config", punto_venta_id=punto_venta_id))
    servicios = conn.execute(
        """
        SELECT *
        FROM tpv_servicios_buffet
        WHERE empresa_id = ? AND punto_venta_id = ?
        ORDER BY servicio, hora_inicio, nombre
        """,
        (empresa_id, punto_venta_id),
    ).fetchall()
    conn.close()
    return render_template(
        "tpv/punto_config.html",
        punto=punto,
        servicios=servicios,
        tipos_punto=TIPOS_PUNTO_TPV,
        servicios_buffet=SERVICIOS_BUFFET,
    )


@tpv_bp.route("/buffet", methods=["GET", "POST"])
@login_required
def buffet():
    conn = get_db()
    _ensure_tpv_schema(conn)
    _ensure_recepcion_for_tpv(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        bloqueo = _requiere_operar_tpv(conn)
        if bloqueo:
            return bloqueo
    terminal_id_arg = request.values.get("terminal_id") or str(session.get("tpv_terminal_id") or "")
    terminal_row = None
    if terminal_id_arg.isdigit():
        terminal_row = conn.execute(
            """
            SELECT t.*, pv.nombre AS punto_nombre, pv.tpv_tipo
            FROM tpv_terminales t
            LEFT JOIN puntos_venta pv ON t.punto_venta_id = pv.id
            WHERE t.id = ? AND t.empresa_id = ?
            """,
            (int(terminal_id_arg), empresa_id),
        ).fetchone()
        if not terminal_row:
            terminal_id_arg = ""
    if request.method == "POST":
        punto_venta_id = request.form.get("punto_venta_id") or (str(terminal_row["punto_venta_id"]) if terminal_row else "")
        fecha_txt = request.form.get("fecha") or (terminal_row["fecha_servicio"] if terminal_row and "fecha_servicio" in terminal_row.keys() else "") or date.today().isoformat()
        servicio = request.form.get("servicio") or (terminal_row["servicio_actual"] if terminal_row and "servicio_actual" in terminal_row.keys() else "") or "desayuno"
        if not punto_venta_id.isdigit():
            conn.close()
            flash("Selecciona el punto de buffet.", "warning")
            return redirect(url_for("tpv.buffet", terminal_id=terminal_id_arg, fecha=fecha_txt, servicio=servicio))
        reserva_id = request.form.get("reserva_id") or ""
        reserva = None
        habitacion_id = None
        habitacion = request.form.get("habitacion") or ""
        titular = request.form.get("titular") or "Entrada manual"
        regimen = request.form.get("regimen") or ""
        if reserva_id.isdigit():
            reserva = conn.execute(
                """
                SELECT r.*, h.numero AS habitacion_numero, h.nombre AS habitacion_nombre
                FROM reservas_recepcion r
                LEFT JOIN habitaciones_recepcion h ON r.habitacion_id = h.id
                WHERE r.id = ? AND r.empresa_id = ?
                """,
                (int(reserva_id), empresa_id),
            ).fetchone()
            if reserva:
                habitacion_id = reserva["habitacion_id"]
                habitacion = _habitacion_nombre(reserva)
                titular = reserva["titular_nombre"] or reserva["codigo"] or titular
                regimen = reserva["regimen"] or regimen
        adultos = max(_to_int(request.form.get("adultos"), 0), 0)
        menores = max(_to_int(request.form.get("menores"), 0), 0)
        extra_adultos, extra_menores = _extras_acceso_buffet(
            conn,
            empresa_id,
            int(punto_venta_id),
            fecha_txt,
            servicio,
            reserva,
            adultos,
            menores,
        )
        conn.execute(
            """
            INSERT INTO tpv_buffet_entradas
            (punto_venta_id, terminal_id, fecha, servicio, reserva_id, habitacion_id,
             habitacion, titular, regimen, adultos, menores, observaciones, usuario, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(punto_venta_id),
                int(terminal_id_arg) if terminal_id_arg.isdigit() else None,
                fecha_txt,
                servicio,
                int(reserva_id) if reserva_id.isdigit() else None,
                habitacion_id,
                habitacion,
                titular,
                regimen,
                adultos,
                menores,
                request.form.get("observaciones") or "",
                _usuario_actual(),
                empresa_id,
            ),
        )
        ticket_extra = _crear_ticket_extra_buffet(
            conn,
            empresa_id,
            int(punto_venta_id),
            int(terminal_id_arg) if terminal_id_arg.isdigit() else None,
            fecha_txt,
            servicio,
            reserva=reserva,
            habitacion=habitacion,
            titular=titular,
            extra_adultos=extra_adultos,
            extra_menores=extra_menores,
        )
        conn.commit()
        conn.close()
        if ticket_extra:
            flash(f"Entrada registrada. Extra a cobrar: {ticket_extra['total']:.2f} EUR en {ticket_extra['numero']}.", "warning")
            if terminal_id_arg.isdigit():
                return redirect(url_for("tpv.terminal", terminal_id=int(terminal_id_arg), cuenta_id=ticket_extra["cuenta_id"]))
        else:
            flash("Entrada registrada.", "success")
        return redirect(url_for("tpv.buffet", terminal_id=terminal_id_arg, punto_venta_id=punto_venta_id, fecha=fecha_txt, servicio=servicio))

    fecha_default = (terminal_row["fecha_servicio"] if terminal_row and "fecha_servicio" in terminal_row.keys() else "") or date.today().isoformat()
    fecha_obj = _parse_date(request.args.get("fecha") or fecha_default, date.today())
    fecha_txt = fecha_obj.isoformat()
    servicio = request.args.get("servicio") or (terminal_row["servicio_actual"] if terminal_row and "servicio_actual" in terminal_row.keys() else "") or "desayuno"
    puntos = conn.execute(
        """
        SELECT *
        FROM puntos_venta
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY CASE WHEN COALESCE(tpv_control_buffet, 0) = 1 THEN 0 ELSE 1 END, nombre
        """,
        (empresa_id,),
    ).fetchall()
    punto_venta_id = request.args.get("punto_venta_id") or (str(terminal_row["punto_venta_id"]) if terminal_row else "")
    if not punto_venta_id and puntos:
        candidato = next((p for p in puntos if p["tpv_control_buffet"] or p["tpv_tipo"] == "buffet"), puntos[0])
        punto_venta_id = str(candidato["id"])
    punto_id = int(punto_venta_id) if str(punto_venta_id).isdigit() else 0
    servicios_config = []
    if punto_id:
        servicios_config = conn.execute(
            """
            SELECT *
            FROM tpv_servicios_buffet
            WHERE empresa_id = ?
              AND punto_venta_id = ?
              AND COALESCE(activo, 1) = 1
            ORDER BY hora_inicio, servicio, nombre
            """,
            (empresa_id, punto_id),
        ).fetchall()
    reservas = _reservas_para_buffet(conn, empresa_id, fecha_obj, servicio) if punto_id else []
    entradas = _entradas_buffet_resumen(conn, empresa_id, punto_id, fecha_txt, servicio) if punto_id else {}
    detalle = []
    totales = {
        "previstos_adultos": 0,
        "previstos_menores": 0,
        "previstos": 0,
        "entrados": 0,
        "faltan": 0,
    }
    for reserva in reservas:
        adultos = _to_int(reserva["adultos"], 0)
        menores = _to_int(reserva["menores"], 0)
        previstos = adultos + menores
        entrada = entradas.get(reserva["id"], {"adultos": 0, "menores": 0, "total": 0})
        adultos_pendientes = max(adultos - entrada["adultos"], 0)
        menores_pendientes = max(menores - entrada["menores"], 0)
        faltan = max(previstos - entrada["total"], 0)
        totales["previstos_adultos"] += adultos
        totales["previstos_menores"] += menores
        totales["previstos"] += previstos
        totales["entrados"] += entrada["total"]
        totales["faltan"] += faltan
        detalle.append(
            {
                "reserva": reserva,
                "habitacion": _habitacion_nombre(reserva),
                "adultos": adultos,
                "menores": menores,
                "previstos": previstos,
                "entrados_adultos": entrada["adultos"],
                "entrados_menores": entrada["menores"],
                "entrados": entrada["total"],
                "adultos_pendientes": adultos_pendientes,
                "menores_pendientes": menores_pendientes,
                "faltan": faltan,
            }
        )
    registros = []
    if punto_id:
        registros = conn.execute(
            """
            SELECT *
            FROM tpv_buffet_entradas
            WHERE empresa_id = ? AND punto_venta_id = ? AND fecha = ? AND servicio = ?
            ORDER BY fecha_registro DESC
            LIMIT 50
            """,
            (empresa_id, punto_id, fecha_txt, servicio),
        ).fetchall()
    conn.close()
    return render_template(
        "tpv/buffet.html",
        puntos=puntos,
        punto_venta_id=str(punto_venta_id),
        fecha=fecha_txt,
        servicio=servicio,
        servicios=SERVICIOS_BUFFET,
        servicios_config=servicios_config,
        regimenes=REGIMENES_TPV,
        detalle=detalle,
        totales=totales,
        registros=registros,
        terminal=terminal_row,
        terminal_id=terminal_id_arg,
    )


@tpv_bp.route("/acceso", methods=["GET", "POST"])
@login_required
def acceso():
    conn = get_db()
    _ensure_tpv_schema(conn)
    _ensure_recepcion_for_tpv(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        bloqueo = _requiere_operar_tpv(conn)
        if bloqueo:
            return bloqueo
    terminal_id_arg = request.values.get("terminal_id") or str(session.get("tpv_terminal_id") or "")
    terminal_row = None
    if terminal_id_arg.isdigit():
        terminal_row = conn.execute(
            """
            SELECT t.*, pv.nombre AS punto_nombre, pv.tpv_tipo
            FROM tpv_terminales t
            LEFT JOIN puntos_venta pv ON t.punto_venta_id = pv.id
            WHERE t.id = ? AND t.empresa_id = ?
            """,
            (int(terminal_id_arg), empresa_id),
        ).fetchone()
        if not terminal_row:
            terminal_id_arg = ""
    if request.method == "POST":
        punto_venta_id = request.form.get("punto_venta_id") or (str(terminal_row["punto_venta_id"]) if terminal_row else "")
        fecha_txt = request.form.get("fecha") or (terminal_row["fecha_servicio"] if terminal_row and "fecha_servicio" in terminal_row.keys() else "") or date.today().isoformat()
        servicio = request.form.get("servicio") or (terminal_row["servicio_actual"] if terminal_row and "servicio_actual" in terminal_row.keys() else "") or "desayuno"
        reserva_id = request.form.get("reserva_id") or ""
        if not punto_venta_id.isdigit():
            conn.close()
            flash("Selecciona el punto de venta.", "warning")
            return redirect(url_for("tpv.acceso", terminal_id=terminal_id_arg, punto_venta_id=punto_venta_id, fecha=fecha_txt, servicio=servicio))
        reserva = None
        habitacion_id = None
        habitacion = (request.form.get("habitacion") or "").strip()
        titular = (request.form.get("titular") or "").strip()
        regimen = request.form.get("regimen") or ""
        if reserva_id.isdigit():
            reserva = _reserva_activa_tpv(conn, empresa_id, int(reserva_id), fecha_obj=_parse_date(fecha_txt, date.today()))
            if not reserva:
                conn.close()
                flash("No se encontro una reserva activa.", "warning")
                return redirect(url_for("tpv.acceso", terminal_id=terminal_id_arg, punto_venta_id=punto_venta_id, fecha=fecha_txt, servicio=servicio))
            habitacion_id = reserva["habitacion_id"]
            habitacion = _habitacion_nombre(reserva)
            titular = reserva["titular_nombre"] or reserva["codigo"] or titular
            regimen = reserva["regimen"] or regimen
        else:
            habitacion = habitacion or "Cliente calle"
            titular = titular or "Cliente calle"
            regimen = regimen or "sin_reserva"
        adultos = max(_to_int(request.form.get("adultos"), 0), 0)
        menores = max(_to_int(request.form.get("menores"), 0), 0)
        if adultos + menores <= 0:
            if reserva:
                adultos = max(_to_int(reserva["adultos"], 1), 1)
                menores = _to_int(reserva["menores"], 0)
            else:
                adultos = 1
                menores = 0
        extra_adultos, extra_menores = _extras_acceso_buffet(
            conn,
            empresa_id,
            int(punto_venta_id),
            fecha_txt,
            servicio,
            reserva,
            adultos,
            menores,
        )
        conn.execute(
            """
            INSERT INTO tpv_buffet_entradas
            (punto_venta_id, terminal_id, fecha, servicio, reserva_id, habitacion_id,
             habitacion, titular, regimen, adultos, menores, observaciones, usuario, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(punto_venta_id),
                int(terminal_id_arg) if terminal_id_arg.isdigit() else None,
                fecha_txt,
                servicio,
                reserva["id"] if reserva else None,
                habitacion_id,
                habitacion,
                titular,
                regimen,
                adultos,
                menores,
                request.form.get("observaciones") or ("Cliente calle TPV" if not reserva else "Acceso rapido TPV"),
                _usuario_actual(),
                empresa_id,
            ),
        )
        ticket_extra = _crear_ticket_extra_buffet(
            conn,
            empresa_id,
            int(punto_venta_id),
            int(terminal_id_arg) if terminal_id_arg.isdigit() else None,
            fecha_txt,
            servicio,
            reserva=reserva,
            habitacion=habitacion,
            titular=titular,
            extra_adultos=extra_adultos,
            extra_menores=extra_menores,
        )
        _registrar_tpv_auditoria(
            conn,
            empresa_id,
            "tpv_acceso",
            reserva["id"] if reserva else 0,
            "registrar_entrada",
            despues={
                "fecha": fecha_txt,
                "servicio": servicio,
                "habitacion": habitacion,
                "titular": titular,
                "tipo": "reserva" if reserva else "cliente_calle",
                "adultos": adultos,
                "menores": menores,
            },
            punto_venta_id=int(punto_venta_id),
            motivo="Control de acceso rapido",
        )
        conn.commit()
        conn.close()
        if ticket_extra:
            flash(f"Entrada registrada. Extra a cobrar: {ticket_extra['total']:.2f} EUR en {ticket_extra['numero']}.", "warning")
            if terminal_id_arg.isdigit():
                return redirect(url_for("tpv.terminal", terminal_id=int(terminal_id_arg), cuenta_id=ticket_extra["cuenta_id"]))
        else:
            flash("Entrada registrada.", "success")
        return redirect(url_for("tpv.acceso", terminal_id=terminal_id_arg, punto_venta_id=punto_venta_id, fecha=fecha_txt, servicio=servicio, q=request.form.get("q") or ""))

    fecha_default = (terminal_row["fecha_servicio"] if terminal_row and "fecha_servicio" in terminal_row.keys() else "") or date.today().isoformat()
    fecha_obj = _parse_date(request.args.get("fecha") or fecha_default, date.today())
    fecha_txt = fecha_obj.isoformat()
    servicio = request.args.get("servicio") or (terminal_row["servicio_actual"] if terminal_row and "servicio_actual" in terminal_row.keys() else "") or "desayuno"
    q = request.args.get("q") or ""
    puntos = conn.execute(
        """
        SELECT *
        FROM puntos_venta
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY CASE WHEN COALESCE(tpv_control_buffet, 0) = 1 THEN 0 ELSE 1 END, nombre
        """,
        (empresa_id,),
    ).fetchall()
    punto_venta_id = request.args.get("punto_venta_id") or (str(terminal_row["punto_venta_id"]) if terminal_row else "")
    if not punto_venta_id and puntos:
        candidato = next((p for p in puntos if p["tpv_control_buffet"] or p["tpv_tipo"] == "buffet"), puntos[0])
        punto_venta_id = str(candidato["id"])
    punto_id = int(punto_venta_id) if str(punto_venta_id).isdigit() else 0
    resultados = []
    for reserva in _buscar_reservas_acceso(conn, empresa_id, q, fecha_obj):
        servicios = _servicios_regimen_tpv(reserva["regimen"])
        derecho = servicio in servicios and _servicio_aplica_fecha_tpv(
            servicio,
            _parse_date(reserva["fecha_entrada"]),
            _parse_date(reserva["fecha_salida"]),
            fecha_obj,
        )
        registrados = _entrada_buffet_reserva_total(conn, empresa_id, punto_id, fecha_txt, servicio, reserva["id"]) if punto_id else {"total": 0}
        pax = _to_int(reserva["adultos"], 0) + _to_int(reserva["menores"], 0)
        resultados.append(
            {
                "reserva": reserva,
                "habitacion": _habitacion_nombre(reserva),
                "servicios": servicios,
                "derecho": derecho,
                "registrados": registrados,
                "pax": pax,
                "faltan": max(pax - registrados["total"], 0),
            }
        )
    conn.close()
    return render_template(
        "tpv/acceso.html",
        puntos=puntos,
        punto_venta_id=str(punto_venta_id),
        fecha=fecha_txt,
        servicio=servicio,
        servicios=SERVICIOS_BUFFET,
        regimenes=REGIMENES_TPV,
        resultados=resultados,
        q=q,
        terminal=terminal_row,
        terminal_id=terminal_id_arg,
    )


@tpv_bp.route("/catalogo", methods=["GET", "POST"])
@login_required
def catalogo():
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        bloqueo = _requiere_gestion_tpv(conn)
        if bloqueo:
            return bloqueo
    if request.method == "POST":
        accion = request.form.get("accion") or "lote"
        punto_venta_id = request.form.get("punto_venta_id") or ""
        punto_id = int(punto_venta_id) if punto_venta_id.isdigit() else None
        if accion == "lote":
            nuevos, articulos = _crear_lote_productos_tpv(conn, empresa_id, punto_id)
            _registrar_tpv_auditoria(
                conn,
                empresa_id,
                "catalogo",
                punto_id,
                "crear_lote",
                despues={"productos_nuevos": nuevos, "articulos_vinculados": articulos},
                punto_venta_id=punto_id,
                motivo="Lote base TPV",
            )
            flash(f"Lote TPV creado/revisado. Productos nuevos: {nuevos}. Articulos vinculados: {articulos}.", "success")
        elif accion == "importar_menu_fijo":
            if not punto_id:
                flash("Selecciona un punto de venta para subir el menu fijo.", "warning")
            else:
                resultado = _importar_menu_fijo_tpv(conn, empresa_id, punto_id, request.form.get("menu_fijo") or "")
                _registrar_tpv_auditoria(
                    conn,
                    empresa_id,
                    "catalogo",
                    punto_id,
                    "importar_menu_fijo",
                    despues=resultado,
                    punto_venta_id=punto_id,
                    motivo="Menu fijo TPV",
                )
                if resultado["filas"]:
                    flash(
                        f"Menu fijo subido. Nuevos: {resultado['creados']}. Actualizados: {resultado['actualizados']}.",
                        "success",
                    )
                else:
                    flash("No se encontraron lineas validas en el menu fijo.", "warning")
        elif accion in {"toggle_articulo", "toggle_visible_tpv", "toggle_visible_comandero"}:
            articulo_id = request.form.get("articulo_id") or ""
            articulo = None
            if articulo_id.isdigit():
                articulo = conn.execute(
                    """
                    SELECT *
                    FROM tpv_articulos
                    WHERE id = ? AND empresa_id = ?
                    """,
                    (int(articulo_id), empresa_id),
                ).fetchone()
            if not articulo:
                flash("Articulo TPV no encontrado.", "warning")
            else:
                campo = {
                    "toggle_articulo": "activo",
                    "toggle_visible_tpv": "visible_tpv",
                    "toggle_visible_comandero": "visible_comandero",
                }[accion]
                nuevo = 0 if articulo[campo] else 1
                antes = _row_snapshot(
                    articulo,
                    ["nombre", "activo", "visible_tpv", "visible_comandero", "precio", "destino_preparacion"],
                )
                conn.execute(
                    f"""
                    UPDATE tpv_articulos
                    SET {campo} = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND empresa_id = ?
                    """,
                    (nuevo, articulo["id"], empresa_id),
                )
                despues = dict(antes)
                despues[campo] = nuevo
                _registrar_tpv_auditoria(
                    conn,
                    empresa_id,
                    "tpv_articulo",
                    articulo["id"],
                    accion,
                    antes=antes,
                    despues=despues,
                    punto_venta_id=articulo["punto_venta_id"],
                    motivo=request.form.get("motivo") or "",
                )
                estado = "activado" if nuevo else "desactivado"
                flash(f"Articulo {articulo['nombre']} {estado}.", "success")
                punto_id = articulo["punto_venta_id"]
        elif accion == "toggle_categoria":
            categoria_id = request.form.get("categoria_id") or ""
            categoria = None
            if categoria_id.isdigit():
                categoria = conn.execute(
                    """
                    SELECT *
                    FROM tpv_categorias
                    WHERE id = ? AND empresa_id = ?
                    """,
                    (int(categoria_id), empresa_id),
                ).fetchone()
            if not categoria:
                flash("Familia TPV no encontrada.", "warning")
            else:
                nuevo = 0 if categoria["activo"] else 1
                conn.execute(
                    """
                    UPDATE tpv_categorias
                    SET activo = ?
                    WHERE id = ? AND empresa_id = ?
                    """,
                    (nuevo, categoria["id"], empresa_id),
                )
                _registrar_tpv_auditoria(
                    conn,
                    empresa_id,
                    "tpv_categoria",
                    categoria["id"],
                    "toggle_categoria",
                    antes={"nombre": categoria["nombre"], "activo": categoria["activo"]},
                    despues={"nombre": categoria["nombre"], "activo": nuevo},
                    punto_venta_id=categoria["punto_venta_id"],
                    motivo=request.form.get("motivo") or "",
                )
                punto_id = categoria["punto_venta_id"]
                flash(f"Familia {categoria['nombre']} {'activada' if nuevo else 'ocultada'} del terminal.", "success")
        elif accion == "toggle_producto_venta":
            producto_id = request.form.get("producto_id") or ""
            producto = None
            if producto_id.isdigit():
                producto = conn.execute(
                    """
                    SELECT *
                    FROM productos
                    WHERE id = ? AND empresa_id = ?
                    """,
                    (int(producto_id), empresa_id),
                ).fetchone()
            if not producto:
                flash("Producto no encontrado.", "warning")
            else:
                actual = producto["visible_en_venta"] if "visible_en_venta" in producto.keys() else 1
                nuevo = 0 if actual else 1
                antes = _row_snapshot(producto, ["nombre", "codigo", "visible_en_venta", "activo", "precio_venta"])
                conn.execute(
                    """
                    UPDATE productos
                    SET visible_en_venta = ?
                    WHERE id = ? AND empresa_id = ?
                    """,
                    (nuevo, producto["id"], empresa_id),
                )
                despues = dict(antes)
                despues["visible_en_venta"] = nuevo
                _registrar_tpv_auditoria(
                    conn,
                    empresa_id,
                    "producto",
                    producto["id"],
                    "toggle_producto_venta",
                    antes=antes,
                    despues=despues,
                    punto_venta_id=punto_id,
                    motivo=request.form.get("motivo") or "",
                )
                flash(f"Producto {producto['nombre']} {'visible' if nuevo else 'oculto'} para venta.", "success")
        elif accion == "guardar_precios":
            if not punto_id:
                flash("Selecciona un punto de venta para guardar precios.", "warning")
            else:
                precio_todos = request.form.get("precio_todos") or ""
                articulos = conn.execute(
                    """
                    SELECT *
                    FROM tpv_articulos
                    WHERE empresa_id = ? AND punto_venta_id = ?
                    """,
                    (empresa_id, punto_id),
                ).fetchall()
                cambios = 0
                for articulo in articulos:
                    campo = f"precio_{articulo['id']}"
                    precio = _to_float(precio_todos, None) if precio_todos.strip() else _to_float(request.form.get(campo), None)
                    tipo_cobro = request.form.get(f"tipo_cobro_{articulo['id']}") or articulo["tipo_cobro"] if "tipo_cobro" in articulo.keys() else "cobro"
                    categoria_id = request.form.get(f"categoria_{articulo['id']}") or ""
                    if precio is None:
                        precio = _to_float(articulo["precio"], 0)
                    precio = max(round(precio, 2), 0)
                    if tipo_cobro == "incluido":
                        precio = 0
                    suplemento = precio if tipo_cobro == "suplemento" else 0
                    categoria_update = int(categoria_id) if str(categoria_id).isdigit() else articulo["categoria_id"]
                    visible_tpv = 1 if request.form.get(f"visible_tpv_{articulo['id']}") else 0
                    visible_comandero = 1 if request.form.get(f"visible_comandero_{articulo['id']}") else 0
                    activo = 1 if request.form.get(f"activo_{articulo['id']}") else 0
                    if (
                        round(_to_float(articulo["precio"], 0), 2) == precio
                        and (articulo["tipo_cobro"] if "tipo_cobro" in articulo.keys() else "cobro") == tipo_cobro
                        and _to_int(articulo["categoria_id"], 0) == _to_int(categoria_update, 0)
                        and _to_int(articulo["visible_tpv"], 1) == visible_tpv
                        and _to_int(articulo["visible_comandero"], 1) == visible_comandero
                        and _to_int(articulo["activo"], 1) == activo
                    ):
                        continue
                    conn.execute(
                        """
                        UPDATE tpv_articulos
                        SET precio = ?, tipo_cobro = ?, suplemento = ?, categoria_id = ?,
                            visible_tpv = ?, visible_comandero = ?, activo = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND empresa_id = ?
                        """,
                        (
                            precio,
                            tipo_cobro,
                            suplemento,
                            categoria_update,
                            visible_tpv,
                            visible_comandero,
                            activo,
                            articulo["id"],
                            empresa_id,
                        ),
                    )
                    if articulo["receta_id"]:
                        conn.execute(
                            """
                            UPDATE punto_venta_recetas
                            SET precio_venta = ?
                            WHERE punto_venta_id = ? AND receta_id = ?
                            """,
                            (precio, punto_id, articulo["receta_id"]),
                        )
                    cambios += 1
                _registrar_tpv_auditoria(
                    conn,
                    empresa_id,
                    "catalogo",
                    punto_id,
                    "guardar_precios",
                    despues={"cambios": cambios, "precio_todos": precio_todos},
                    punto_venta_id=punto_id,
                    motivo="Edicion masiva de precios TPV",
                )
                flash(f"Precios guardados. Cambios: {cambios}.", "success")
        elif accion == "crear_familia":
            nombre = (request.form.get("nombre_familia") or "").strip()
            if not punto_id or not nombre:
                flash("Indica punto de venta y nombre de familia.", "warning")
            else:
                _categoria_tpv(conn, empresa_id, punto_id, nombre, "menu_fijo", 10)
                flash(f"Familia {nombre} creada.", "success")
        conn.commit()
        conn.close()
        return redirect(url_for("tpv.catalogo", punto_venta_id=punto_id or ""))
    punto_venta_id = request.args.get("punto_venta_id") or ""
    puntos = _puntos_venta(conn, empresa_id)
    productos = conn.execute(
        """
        SELECT *
        FROM productos
        WHERE empresa_id = ?
          AND (COALESCE(visible_en_venta, 1) = 1 OR familia LIKE 'bebidas%' OR familia IN ('snacks', 'helados', 'buffet', 'cafeteria'))
        ORDER BY familia, nombre
        LIMIT 200
        """,
        (empresa_id,),
    ).fetchall()
    articulos = []
    auditoria = []
    categorias = []
    if punto_venta_id.isdigit():
        categorias = conn.execute(
            """
            SELECT *
            FROM tpv_categorias
            WHERE empresa_id = ? AND punto_venta_id = ?
            ORDER BY orden, nombre
            """,
            (empresa_id, int(punto_venta_id)),
        ).fetchall()
        articulos = conn.execute(
            """
            SELECT a.*, c.nombre AS categoria_nombre, p.codigo AS producto_codigo,
                   CASE
                     WHEN a.receta_id IS NOT NULL THEN (
                       SELECT COALESCE(SUM(ri.cantidad * COALESCE(pr.precio, 0)), 0)
                       FROM recetas_ingredientes ri
                       LEFT JOIN productos pr ON pr.id = ri.producto_id
                       WHERE ri.receta_id = a.receta_id
                     )
                     ELSE COALESCE(p.precio, 0)
                   END AS coste_total,
                   CASE
                     WHEN a.receta_id IS NOT NULL THEN (
                       SELECT COALESCE(r.raciones_base, 1)
                       FROM recetas r
                       WHERE r.id = a.receta_id
                     )
                     ELSE 1
                   END AS raciones_base
            FROM tpv_articulos a
            LEFT JOIN tpv_categorias c ON a.categoria_id = c.id
            LEFT JOIN productos p ON a.producto_id = p.id
            WHERE a.empresa_id = ? AND a.punto_venta_id = ?
            ORDER BY c.orden, a.orden, a.nombre
            """,
            (empresa_id, int(punto_venta_id)),
        ).fetchall()
        articulos = [
            {
                **dict(a),
                "coste_unitario": round(_to_float(a["coste_total"], 0) / max(_to_float(a["raciones_base"], 1), 1), 2),
                "precio_sugerido": _precio_sugerido_tpv(_to_float(a["coste_total"], 0) / max(_to_float(a["raciones_base"], 1), 1)),
            }
            for a in articulos
        ]
        auditoria = conn.execute(
            """
            SELECT *
            FROM tpv_auditoria
            WHERE empresa_id = ? AND punto_venta_id = ?
            ORDER BY fecha DESC, id DESC
            LIMIT 20
            """,
            (empresa_id, int(punto_venta_id)),
        ).fetchall()
    conn.close()
    return render_template(
        "tpv/catalogo.html",
        puntos=puntos,
        productos=productos,
        articulos=articulos,
        auditoria=auditoria,
        punto_venta_id=punto_venta_id,
        destinos=DESTINOS_PREPARACION,
        regimenes=REGIMENES_TPV,
        categorias=categorias,
        tipos_cobro=TIPOS_COBRO_TPV,
    )


@tpv_bp.route("/terminales", methods=["GET", "POST"])
@login_required
def terminales():
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    if request.method == "POST":
        bloqueo = _requiere_gestion_tpv(conn)
        if bloqueo:
            return bloqueo
        accion = request.form.get("accion") or "crear"
        if accion == "crear":
            punto_venta_id = request.form.get("punto_venta_id") or ""
            codigo = (request.form.get("codigo") or "").strip().upper()
            nombre = (request.form.get("nombre") or "").strip()
            if not codigo:
                codigo = f"TPV-{int(datetime.now().timestamp())}"
            if nombre:
                if punto_venta_id.isdigit() and centro_id:
                    punto_ok = conn.execute(
                        "SELECT id FROM puntos_venta WHERE id = ? AND empresa_id = ? AND centro_id = ? AND COALESCE(activo, 1) = 1",
                        (int(punto_venta_id), empresa_id, centro_id),
                    ).fetchone()
                    if not punto_ok:
                        conn.close()
                        flash("Ese punto de venta no pertenece al centro activo.", "warning")
                        return redirect(url_for("tpv.terminales"))
                conn.execute(
                    """
                    INSERT INTO tpv_terminales
                    (punto_venta_id, codigo, nombre, tipo, mac, ip, ubicacion, modo_arranque,
                     comandero_zonas, comandero_categorias, comandero_ver_precios, comandero_puede_cobrar,
                     activo, empresa_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        int(punto_venta_id) if punto_venta_id.isdigit() else None,
                        codigo,
                        nombre,
                        request.form.get("tipo") or "tpv",
                        request.form.get("mac") or "",
                        request.form.get("ip") or request.remote_addr or "",
                        request.form.get("ubicacion") or "",
                        request.form.get("modo_arranque") or "manual",
                        request.form.get("comandero_zonas") or "",
                        request.form.get("comandero_categorias") or "",
                        1 if request.form.get("comandero_ver_precios") else 0,
                        1 if request.form.get("comandero_puede_cobrar") else 0,
                        empresa_id,
                    ),
                )
                flash("Terminal TPV creado.", "success")
        elif accion == "config":
            terminal_id = request.form.get("terminal_id") or ""
            if terminal_id.isdigit():
                conn.execute(
                    """
                    UPDATE tpv_terminales
                    SET comandero_zonas = ?, comandero_categorias = ?,
                        comandero_ver_precios = ?, comandero_puede_cobrar = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND empresa_id = ?
                      AND (? IS NULL OR punto_venta_id IN (
                          SELECT id FROM puntos_venta WHERE empresa_id = ? AND centro_id = ?
                      ))
                    """,
                    (
                        request.form.get("comandero_zonas") or "",
                        request.form.get("comandero_categorias") or "",
                        1 if request.form.get("comandero_ver_precios") else 0,
                        1 if request.form.get("comandero_puede_cobrar") else 0,
                        int(terminal_id),
                        empresa_id,
                        centro_id,
                        empresa_id,
                        centro_id,
                    ),
                )
                flash("Configuracion del comandero guardada.", "success")
        elif accion == "toggle":
            terminal_id = request.form.get("terminal_id") or ""
            if terminal_id.isdigit():
                row = conn.execute(
                    """
                    SELECT t.activo
                    FROM tpv_terminales t
                    LEFT JOIN puntos_venta pv ON pv.id = t.punto_venta_id
                    WHERE t.id = ? AND t.empresa_id = ?
                      AND (? IS NULL OR pv.centro_id = ?)
                    """,
                    (int(terminal_id), empresa_id, centro_id, centro_id),
                ).fetchone()
                if row:
                    conn.execute(
                        """
                        UPDATE tpv_terminales
                        SET activo = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND empresa_id = ?
                          AND (? IS NULL OR punto_venta_id IN (
                              SELECT id FROM puntos_venta WHERE empresa_id = ? AND centro_id = ?
                          ))
                        """,
                        (0 if row["activo"] else 1, int(terminal_id), empresa_id, centro_id, empresa_id, centro_id),
                    )
        conn.commit()
        conn.close()
        return redirect(url_for("tpv.terminales"))
    puntos = _puntos_venta(conn, empresa_id)
    centro_sql = " AND pv.centro_id = ?" if centro_id else ""
    params = [empresa_id] + ([centro_id] if centro_id else [])
    terminales_rows = conn.execute(
        f"""
        SELECT t.*, pv.nombre AS punto_nombre
        FROM tpv_terminales t
        LEFT JOIN puntos_venta pv ON t.punto_venta_id = pv.id
        WHERE t.empresa_id = ?
          {centro_sql}
        ORDER BY COALESCE(t.activo, 1) DESC, pv.nombre, t.nombre
        """,
        params,
    ).fetchall()
    conn.close()
    return render_template(
        "tpv/terminales.html",
        puntos=puntos,
        terminales=terminales_rows,
        tipos_terminal=TIPOS_TERMINAL,
    )


@tpv_bp.route("/arranque", methods=["GET", "POST"])
@login_required
def arranque():
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    if request.method == "POST":
        bloqueo = _requiere_operar_tpv(conn)
        if bloqueo:
            return bloqueo
        punto_venta_id = request.form.get("punto_venta_id") or ""
        codigo = (request.form.get("codigo") or "").strip().upper()
        nombre_form = (request.form.get("nombre") or "").strip()
        tipo = request.form.get("tipo") or "tpv"
        servicio_actual, turno_actual, fecha_servicio = _terminal_contexto_form(request.form)
        if not punto_venta_id.isdigit():
            conn.close()
            flash("Selecciona el punto de venta.", "warning")
            return redirect(url_for("tpv.arranque"))
        punto = conn.execute(
            """
            SELECT *
            FROM puntos_venta
            WHERE id = ? AND empresa_id = ?
              AND (? IS NULL OR centro_id = ?)
            """,
            (int(punto_venta_id), empresa_id, centro_id, centro_id),
        ).fetchone()
        if not punto:
            conn.close()
            flash("Ese punto de venta no pertenece al centro activo.", "warning")
            return redirect(url_for("tpv.arranque"))
        if not codigo:
            codigo = _codigo_terminal_auto(conn, empresa_id, int(punto_venta_id), tipo)
        else:
            session["tpv_device_code"] = codigo
        terminal = conn.execute(
            """
            SELECT *
            FROM tpv_terminales
            WHERE empresa_id = ? AND UPPER(codigo) = UPPER(?)
            LIMIT 1
            """,
            (empresa_id, codigo),
        ).fetchone()
        if terminal:
            terminal_id = terminal["id"]
            nombre = nombre_form or _nombre_terminal_contexto(punto, tipo, codigo, servicio_actual)
            conn.execute(
                """
                UPDATE tpv_terminales
                SET punto_venta_id = ?, nombre = ?, tipo = ?, ip = ?, ubicacion = ?,
                    servicio_actual = ?, turno_actual = ?, fecha_servicio = ?,
                    ultimo_inicio = CURRENT_TIMESTAMP, activo = 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND empresa_id = ?
                """,
                (
                    int(punto_venta_id),
                    nombre,
                    tipo,
                    request.remote_addr or "",
                    request.form.get("ubicacion") or terminal["ubicacion"] or "",
                    servicio_actual,
                    turno_actual,
                    fecha_servicio,
                    terminal_id,
                    empresa_id,
                ),
            )
        else:
            nombre = nombre_form or _nombre_terminal_contexto(punto, tipo, codigo, servicio_actual)
            cur = conn.execute(
                """
                INSERT INTO tpv_terminales
                (punto_venta_id, codigo, nombre, tipo, ip, ubicacion, modo_arranque,
                 servicio_actual, turno_actual, fecha_servicio,
                 ultimo_inicio, activo, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, 'arranque', ?, ?, ?, CURRENT_TIMESTAMP, 1, ?)
                """,
                (
                    int(punto_venta_id),
                    codigo,
                    nombre,
                    tipo,
                    request.remote_addr or "",
                    request.form.get("ubicacion") or "",
                    servicio_actual,
                    turno_actual,
                    fecha_servicio,
                    empresa_id,
                ),
            )
            terminal_id = cur.lastrowid
        conn.commit()
        conn.close()
        session["tpv_terminal_id"] = terminal_id
        if tipo == "comandero":
            return redirect(url_for("tpv.comandero", terminal_id=terminal_id))
        return redirect(url_for("tpv.terminal", terminal_id=terminal_id))
    puntos = _puntos_venta(conn, empresa_id)
    conn.close()
    return render_template(
        "tpv/arranque.html",
        puntos=puntos,
        tipos_terminal=TIPOS_TERMINAL,
        servicios=SERVICIOS_BUFFET,
        turnos=TURNOS_TPV,
        fecha_hoy=date.today().isoformat(),
    )


@tpv_bp.route("/terminal/<int:terminal_id>/contexto", methods=["POST"])
@login_required
def terminal_contexto(terminal_id):
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    bloqueo = _requiere_operar_tpv(conn)
    if bloqueo:
        return bloqueo
    centro_id = centro_activo_id()
    terminal_row = conn.execute(
        """
        SELECT t.*, pv.nombre AS punto_nombre, pv.tpv_tipo, pv.centro_id AS punto_centro_id
        FROM tpv_terminales t
        LEFT JOIN puntos_venta pv ON t.punto_venta_id = pv.id
        WHERE t.id = ? AND t.empresa_id = ?
          AND (? IS NULL OR pv.centro_id = ?)
        """,
        (terminal_id, empresa_id, centro_id, centro_id),
    ).fetchone()
    if not terminal_row:
        conn.close()
        abort(404)
    session["tpv_terminal_id"] = terminal_id
    servicio_actual, turno_actual, fecha_servicio = _terminal_contexto_form(request.form)
    punto_like = {
        "nombre": terminal_row["punto_nombre"] or "",
        "tpv_tipo": terminal_row["tpv_tipo"] or "",
    }
    nombre = request.form.get("nombre") or _nombre_terminal_contexto(punto_like, terminal_row["tipo"] or "tpv", terminal_row["codigo"] or "", servicio_actual)
    conn.execute(
        """
        UPDATE tpv_terminales
        SET nombre = ?, servicio_actual = ?, turno_actual = ?, fecha_servicio = ?,
            ultimo_inicio = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (nombre, servicio_actual, turno_actual, fecha_servicio, terminal_id, empresa_id),
    )
    conn.commit()
    conn.close()
    destino = request.form.get("destino") or "terminal"
    if destino == "acceso":
        return redirect(
            url_for(
                "tpv.acceso",
                terminal_id=terminal_id,
                punto_venta_id=terminal_row["punto_venta_id"],
                fecha=fecha_servicio,
                servicio=servicio_actual,
            )
        )
    if destino == "buffet":
        return redirect(
            url_for(
                "tpv.buffet",
                terminal_id=terminal_id,
                punto_venta_id=terminal_row["punto_venta_id"],
                fecha=fecha_servicio,
                servicio=servicio_actual,
            )
        )
    return redirect(url_for("tpv.terminal", terminal_id=terminal_id))


@tpv_bp.route("/terminal/<int:terminal_id>")
@login_required
def terminal(terminal_id):
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    terminal_row = conn.execute(
        """
        SELECT t.*, pv.nombre AS punto_nombre, pv.tpv_tipo, pv.tpv_control_buffet,
               pv.tpv_admite_cargo_habitacion, pv.tpv_permite_todo_incluido,
               pv.centro_id AS punto_centro_id
        FROM tpv_terminales t
        LEFT JOIN puntos_venta pv ON t.punto_venta_id = pv.id
        WHERE t.id = ? AND t.empresa_id = ?
          AND (? IS NULL OR pv.centro_id = ?)
        """,
        (terminal_id, empresa_id, centro_id, centro_id),
    ).fetchone()
    if not terminal_row:
        conn.close()
        abort(404)
    session["tpv_terminal_id"] = terminal_id
    if (terminal_row["tipo"] or "") == "comandero" and request.args.get("vista") != "tpv":
        conn.close()
        return redirect(url_for("tpv.comandero", terminal_id=terminal_id))
    servicio_actual = request.args.get("servicio") or (terminal_row["servicio_actual"] if "servicio_actual" in terminal_row.keys() else "") or "desayuno"
    if servicio_actual not in SERVICIOS_BUFFET:
        servicio_actual = "desayuno"
    turno_actual = request.args.get("turno") or (terminal_row["turno_actual"] if "turno_actual" in terminal_row.keys() else "") or "manana"
    if turno_actual not in TURNOS_TPV:
        turno_actual = "manana"
    fecha_servicio = request.args.get("fecha") or (terminal_row["fecha_servicio"] if "fecha_servicio" in terminal_row.keys() else "") or date.today().isoformat()
    _asegurar_catalogo_terminal_tpv(conn, empresa_id, terminal_row["punto_venta_id"])
    conn.commit()
    categorias = conn.execute(
        """
        SELECT *
        FROM tpv_categorias
        WHERE empresa_id = ? AND punto_venta_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY orden, nombre
        """,
        (empresa_id, terminal_row["punto_venta_id"]),
    ).fetchall()
    articulos = conn.execute(
        """
        SELECT a.*, c.nombre AS categoria_nombre
        FROM tpv_articulos a
        LEFT JOIN tpv_categorias c ON a.categoria_id = c.id
        WHERE a.empresa_id = ?
          AND a.punto_venta_id = ?
          AND COALESCE(a.activo, 1) = 1
          AND COALESCE(a.visible_tpv, 1) = 1
          AND (a.categoria_id IS NULL OR COALESCE(c.activo, 1) = 1)
          AND (? <> 'comandero' OR COALESCE(a.visible_comandero, 1) = 1)
        ORDER BY c.orden, a.orden, a.nombre
        """,
        (empresa_id, terminal_row["punto_venta_id"], terminal_row["tipo"] or ""),
    ).fetchall()
    mesas_rows = _mesas_punto(conn, empresa_id, terminal_row["punto_venta_id"])
    mapa_elementos = conn.execute(
        """
        SELECT *
        FROM tpv_mapa_elementos
        WHERE empresa_id = ? AND punto_venta_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY orden, id
        """,
        (empresa_id, terminal_row["punto_venta_id"]),
    ).fetchall()
    comandas_recientes = conn.execute(
        """
        SELECT c.*, ct.numero AS cuenta_numero
        FROM tpv_comandas c
        LEFT JOIN tpv_cuentas ct ON c.cuenta_id = ct.id
        WHERE c.empresa_id = ? AND c.punto_venta_id = ?
        ORDER BY fecha_creacion DESC
        LIMIT 8
        """,
        (empresa_id, terminal_row["punto_venta_id"]),
    ).fetchall()
    cuentas_rows = _cuentas_abiertas_punto(conn, empresa_id, terminal_row["punto_venta_id"])
    cuenta_ids = [row["id"] for row in cuentas_rows]
    lineas_por_cuenta = {}
    if cuenta_ids:
        placeholders = ",".join("?" for _ in cuenta_ids)
        lineas_rows = conn.execute(
            f"""
            SELECT c.cuenta_id, l.nombre, l.precio, l.destino_preparacion,
                   SUM(l.cantidad) AS cantidad,
                   SUM(l.total) AS total
            FROM tpv_comanda_lineas l
            JOIN tpv_comandas c ON l.comanda_id = c.id
            WHERE c.cuenta_id IN ({placeholders})
              AND c.empresa_id = ?
              AND l.empresa_id = ?
              AND COALESCE(c.estado, 'enviada') <> 'cancelada'
              AND COALESCE(l.estado, 'pendiente') <> 'cancelada'
            GROUP BY c.cuenta_id, l.nombre, l.precio, l.destino_preparacion
            ORDER BY MAX(l.fecha_creacion), MAX(l.id)
            """,
            (*cuenta_ids, empresa_id, empresa_id),
        ).fetchall()
        for linea in lineas_rows:
            lineas_por_cuenta.setdefault(linea["cuenta_id"], []).append(
                {
                    "nombre": linea["nombre"],
                    "cantidad": _to_float(linea["cantidad"], 0),
                    "precio": _to_float(linea["precio"], 0),
                    "total": _to_float(linea["total"], 0),
                    "destino": linea["destino_preparacion"] or "sin_preparacion",
                }
            )
    cuentas_abiertas = []
    for row in cuentas_rows:
        cuenta_dict = dict(row)
        cuenta_dict["lineas"] = lineas_por_cuenta.get(row["id"], [])
        cuenta_dict["articulos"] = sum(_to_float(linea["cantidad"], 0) for linea in cuenta_dict["lineas"])
        cuentas_abiertas.append(cuenta_dict)
    cuenta_por_mesa = {
        cuenta["mesa_id"]: cuenta
        for cuenta in cuentas_abiertas
        if cuenta.get("mesa_id")
    }
    mesas = []
    for row in mesas_rows:
        mesa_dict = dict(row)
        cuenta = cuenta_por_mesa.get(row["id"])
        mesa_dict["cuenta_id"] = cuenta["id"] if cuenta else None
        mesa_dict["cuenta_numero"] = cuenta["numero"] if cuenta else ""
        mesa_dict["cuenta_total"] = _to_float(cuenta["total"], 0) if cuenta else 0
        mesa_dict["cuenta_articulos"] = _to_float(cuenta["articulos"], 0) if cuenta else 0
        mesa_dict["cuenta_cliente"] = cuenta["cliente"] if cuenta else ""
        mesa_dict["cuenta_lineas"] = cuenta["lineas"] if cuenta else []
        mesas.append(mesa_dict)
    referencias_abiertas = [cuenta for cuenta in cuentas_abiertas if not cuenta.get("mesa_id")]
    _ensure_recepcion_for_tpv(conn)
    reservas_activas = _reservas_activas_tpv(conn, empresa_id)
    opciones_menu = _opciones_menu_tpv(conn, empresa_id, terminal_row["punto_venta_id"])
    caja_actual = _caja_abierta_terminal(conn, empresa_id, terminal_id, terminal_row["punto_venta_id"])
    seleccion_cuenta_id = request.args.get("cuenta_id") or ""
    seleccion_mesa_id = request.args.get("mesa_id") or ""
    conn.close()
    return render_template(
        "tpv/terminal.html",
        terminal=terminal_row,
        categorias=categorias,
        articulos=articulos,
        mesas=mesas,
        mapa_elementos=mapa_elementos,
        cuentas_abiertas=cuentas_abiertas,
        referencias_abiertas=referencias_abiertas,
        reservas_activas=reservas_activas,
        comandas_recientes=comandas_recientes,
        destinos=DESTINOS_PREPARACION,
        regimenes=REGIMENES_TPV,
        formas_pago=FORMAS_PAGO_TPV,
        servicios=SERVICIOS_BUFFET,
        turnos=TURNOS_TPV,
        opciones_menu=opciones_menu,
        caja_actual=caja_actual,
        prioridades=PRIORIDADES_COMANDA,
        servicio_actual=servicio_actual,
        turno_actual=turno_actual,
        fecha_servicio=fecha_servicio,
        seleccion_cuenta_id=seleccion_cuenta_id,
        seleccion_mesa_id=seleccion_mesa_id,
        modo_pantalla_operativa=True,
    )


@tpv_bp.route("/comandero")
@login_required
def comandero_directo():
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    terminal = None
    session_terminal_id = _to_int(session.get("tpv_terminal_id"), 0)
    if session_terminal_id:
        terminal = conn.execute(
            """
            SELECT t.*
            FROM tpv_terminales t
            LEFT JOIN puntos_venta pv ON pv.id = t.punto_venta_id
            WHERE t.id = ? AND t.empresa_id = ? AND t.tipo = 'comandero' AND COALESCE(t.activo, 1) = 1
              AND (? IS NULL OR pv.centro_id = ?)
            """,
            (session_terminal_id, empresa_id, centro_id, centro_id),
        ).fetchone()
    if not terminal and request.remote_addr:
        terminal = conn.execute(
            """
            SELECT t.*
            FROM tpv_terminales t
            LEFT JOIN puntos_venta pv ON pv.id = t.punto_venta_id
            WHERE t.empresa_id = ? AND t.tipo = 'comandero' AND COALESCE(t.activo, 1) = 1
              AND t.ip = ?
              AND (? IS NULL OR pv.centro_id = ?)
            ORDER BY t.ultimo_inicio DESC, t.updated_at DESC, t.id DESC
            LIMIT 1
            """,
            (empresa_id, request.remote_addr, centro_id, centro_id),
        ).fetchone()
    conn.close()
    if terminal:
        session["tpv_terminal_id"] = terminal["id"]
        return redirect(url_for("tpv.comandero", terminal_id=terminal["id"]))
    return redirect(url_for("tpv.arranque", tipo="comandero"))


@tpv_bp.route("/comandero/<int:terminal_id>")
@login_required
def comandero(terminal_id):
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    terminal_row = conn.execute(
        """
        SELECT t.*, pv.nombre AS punto_nombre, pv.tpv_tipo, pv.tpv_control_buffet,
               pv.tpv_admite_cargo_habitacion, pv.centro_id AS punto_centro_id
        FROM tpv_terminales t
        LEFT JOIN puntos_venta pv ON t.punto_venta_id = pv.id
        WHERE t.id = ? AND t.empresa_id = ?
          AND (? IS NULL OR pv.centro_id = ?)
        """,
        (terminal_id, empresa_id, centro_id, centro_id),
    ).fetchone()
    if not terminal_row:
        conn.close()
        abort(404)
    session["tpv_terminal_id"] = terminal_id
    _asegurar_catalogo_terminal_tpv(conn, empresa_id, terminal_row["punto_venta_id"])
    conn.commit()
    zonas_permitidas = {
        item.strip().lower()
        for item in (terminal_row["comandero_zonas"] or "").split(",")
        if item.strip()
    } if "comandero_zonas" in terminal_row.keys() else set()
    categorias_permitidas = {
        item.strip().lower()
        for item in (terminal_row["comandero_categorias"] or "").split(",")
        if item.strip()
    } if "comandero_categorias" in terminal_row.keys() else set()
    categorias = conn.execute(
        """
        SELECT *
        FROM tpv_categorias
        WHERE empresa_id = ? AND punto_venta_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY orden, nombre
        """,
        (empresa_id, terminal_row["punto_venta_id"]),
    ).fetchall()
    if categorias_permitidas:
        categorias = [
            row for row in categorias
            if str(row["id"]).lower() in categorias_permitidas or (row["nombre"] or "").strip().lower() in categorias_permitidas
        ]
    categoria_ids_visibles = {row["id"] for row in categorias}
    articulos = conn.execute(
        """
        SELECT a.*, c.nombre AS categoria_nombre
        FROM tpv_articulos a
        LEFT JOIN tpv_categorias c ON a.categoria_id = c.id
        WHERE a.empresa_id = ?
          AND a.punto_venta_id = ?
          AND COALESCE(a.activo, 1) = 1
          AND COALESCE(a.visible_comandero, 1) = 1
          AND (a.categoria_id IS NULL OR COALESCE(c.activo, 1) = 1)
        ORDER BY c.orden, a.orden, a.nombre
        """,
        (empresa_id, terminal_row["punto_venta_id"]),
    ).fetchall()
    if categorias_permitidas:
        articulos = [row for row in articulos if row["categoria_id"] in categoria_ids_visibles]
    mesas_rows = _mesas_punto(conn, empresa_id, terminal_row["punto_venta_id"])
    if zonas_permitidas:
        mesas_rows = [
            row for row in mesas_rows
            if (row["zona"] or "").strip().lower() in zonas_permitidas
        ]
    cuentas_rows = _cuentas_abiertas_punto(conn, empresa_id, terminal_row["punto_venta_id"])
    cuenta_ids = [row["id"] for row in cuentas_rows]
    lineas_por_cuenta = {}
    if cuenta_ids:
        placeholders = ",".join("?" for _ in cuenta_ids)
        lineas_rows = conn.execute(
            f"""
            SELECT c.cuenta_id, l.articulo_id, l.nombre, l.cantidad, l.precio, l.total, l.estado,
                   l.destino_preparacion, l.comentario
            FROM tpv_comanda_lineas l
            JOIN tpv_comandas c ON c.id = l.comanda_id
            WHERE c.cuenta_id IN ({placeholders})
              AND l.empresa_id = ?
              AND c.empresa_id = ?
              AND COALESCE(l.estado, '') <> 'cancelada'
            ORDER BY c.fecha_creacion DESC, l.id DESC
            """,
            (*cuenta_ids, empresa_id, empresa_id),
        ).fetchall()
        for linea in lineas_rows:
            lineas_por_cuenta.setdefault(linea["cuenta_id"], []).append(
                {
                    "nombre": linea["nombre"],
                    "articulo_id": linea["articulo_id"],
                    "cantidad": _to_float(linea["cantidad"], 0),
                    "precio": _to_float(linea["precio"], 0),
                    "total": _to_float(linea["total"], 0),
                    "estado": linea["estado"] or "",
                    "destino": linea["destino_preparacion"] or "",
                    "comentario": linea["comentario"] or "",
                }
            )
    cuentas_por_mesa = {row["mesa_id"]: row for row in cuentas_rows if row["mesa_id"]}
    mesas = []
    for mesa in mesas_rows:
        cuenta = cuentas_por_mesa.get(mesa["id"])
        item = dict(mesa)
        if cuenta:
            item["cuenta_id"] = cuenta["id"]
            item["cuenta_total"] = cuenta["total"]
            item["cuenta_numero"] = cuenta["numero"]
            item["cuenta_lineas"] = lineas_por_cuenta.get(cuenta["id"], [])
        mesas.append(item)
    listas = _lineas_listas_comandero(conn, empresa_id, terminal_row["punto_venta_id"])
    opciones_menu = _opciones_menu_tpv(conn, empresa_id, terminal_row["punto_venta_id"])
    seleccion_cuenta_id = request.args.get("cuenta_id") or ""
    conn.close()
    return render_template(
        "tpv/comandero.html",
        terminal=terminal_row,
        categorias=categorias,
        articulos=articulos,
        mesas=mesas,
        listas=listas,
        opciones_menu=opciones_menu,
        seleccion_cuenta_id=seleccion_cuenta_id,
        modo_pantalla_operativa=True,
    )


def _lineas_listas_comandero(conn, empresa_id, punto_venta_id):
    return conn.execute(
        """
        SELECT l.id, l.nombre, l.cantidad, l.comentario, l.destino_preparacion,
               l.updated_at, c.numero AS comanda_numero, c.mesa_nombre,
               c.modo_servicio, c.habitacion, c.cliente
        FROM tpv_comanda_lineas l
        JOIN tpv_comandas c ON c.id = l.comanda_id
        LEFT JOIN tpv_cuentas cu ON cu.id = c.cuenta_id
        WHERE l.empresa_id = ?
          AND l.punto_venta_id = ?
          AND COALESCE(l.estado, 'pendiente') = 'lista'
          AND COALESCE(c.estado, '') <> 'cancelada'
          AND COALESCE(c.estado, '') NOT IN ('cobrada', 'entregada')
          AND (cu.id IS NULL OR cu.estado = 'abierta')
        ORDER BY l.updated_at DESC, l.id DESC
        LIMIT 30
        """,
        (empresa_id, punto_venta_id),
    ).fetchall()


@tpv_bp.route("/comandero/<int:terminal_id>/avisos")
@login_required
def comandero_avisos(terminal_id):
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    terminal_row = conn.execute(
        """
        SELECT *
        FROM tpv_terminales
        WHERE id = ? AND empresa_id = ?
        """,
        (terminal_id, empresa_id),
    ).fetchone()
    if not terminal_row:
        conn.close()
        abort(404)
    lineas = _lineas_listas_comandero(conn, empresa_id, terminal_row["punto_venta_id"])
    conn.close()
    return jsonify(
        {
            "lineas": [
                {
                    "id": row["id"],
                    "nombre": row["nombre"],
                    "cantidad": row["cantidad"],
                    "comentario": row["comentario"],
                    "destino": row["destino_preparacion"],
                    "updated_at": row["updated_at"],
                    "comanda": row["comanda_numero"],
                    "mesa": row["mesa_nombre"] or row["habitacion"] or row["cliente"] or row["modo_servicio"] or "-",
                }
                for row in lineas
            ]
        }
    )


@tpv_bp.route("/comandero/<int:terminal_id>/marchar", methods=["POST"])
@login_required
def comandero_marchar(terminal_id):
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    bloqueo = _requiere_operar_tpv(conn)
    if bloqueo:
        return bloqueo
    terminal_row = conn.execute(
        """
        SELECT *
        FROM tpv_terminales
        WHERE id = ? AND empresa_id = ?
        """,
        (terminal_id, empresa_id),
    ).fetchone()
    if not terminal_row:
        conn.close()
        abort(404)
    cuenta_id = _to_int(request.form.get("cuenta_id"), 0)
    curso = (request.form.get("curso") or "").strip()
    cursos_validos = {"Primero", "Segundo", "Postre", "Entrante"}
    if not cuenta_id or curso not in cursos_validos:
        conn.close()
        flash("Selecciona una cuenta y un pase para marchar.", "warning")
        return redirect(url_for("tpv.comandero", terminal_id=terminal_id))
    cuenta = conn.execute(
        """
        SELECT *
        FROM tpv_cuentas
        WHERE id = ? AND empresa_id = ? AND punto_venta_id = ? AND estado = 'abierta'
        """,
        (cuenta_id, empresa_id, terminal_row["punto_venta_id"]),
    ).fetchone()
    if not cuenta:
        conn.close()
        flash("Cuenta abierta no encontrada para marchar.", "warning")
        return redirect(url_for("tpv.comandero", terminal_id=terminal_id))
    lineas = conn.execute(
        """
        SELECT l.id, l.comentario
        FROM tpv_comanda_lineas l
        JOIN tpv_comandas c ON c.id = l.comanda_id
        WHERE c.cuenta_id = ?
          AND l.empresa_id = ?
          AND l.punto_venta_id = ?
          AND COALESCE(l.estado, 'pendiente') = 'retenida'
          AND COALESCE(l.comentario, '') LIKE ?
        """,
        (cuenta_id, empresa_id, terminal_row["punto_venta_id"], f"%{curso}%"),
    ).fetchall()
    if not lineas:
        conn.close()
        flash(f"No hay {curso.lower()} retenidos para marchar.", "warning")
        return redirect(url_for("tpv.comandero", terminal_id=terminal_id))
    for linea in lineas:
        comentario = linea["comentario"] or curso
        if not comentario.startswith("Marchar"):
            comentario = f"Marchar {curso} | {comentario}"
        conn.execute(
            """
            UPDATE tpv_comanda_lineas
            SET estado = 'pendiente', comentario = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND empresa_id = ?
            """,
            (comentario, linea["id"], empresa_id),
        )
    conn.execute(
        """
        UPDATE tpv_comandas
        SET estado = 'en_preparacion', updated_at = CURRENT_TIMESTAMP
        WHERE id IN (
            SELECT DISTINCT comanda_id
            FROM tpv_comanda_lineas
            WHERE id IN ({})
        ) AND empresa_id = ?
        """.format(",".join("?" for _ in lineas)),
        (*[linea["id"] for linea in lineas], empresa_id),
    )
    conn.commit()
    conn.close()
    flash(f"Marchados {len(lineas)} articulos de {curso.lower()}.", "success")
    return redirect(url_for("tpv.comandero", terminal_id=terminal_id))


@tpv_bp.route("/terminal/<int:terminal_id>/caja", methods=["GET", "POST"])
@login_required
def caja_terminal(terminal_id):
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        bloqueo = _requiere_operar_tpv(conn)
        if bloqueo:
            return bloqueo
    terminal_row = conn.execute(
        """
        SELECT t.*, pv.nombre AS punto_nombre
        FROM tpv_terminales t
        LEFT JOIN puntos_venta pv ON pv.id = t.punto_venta_id
        WHERE t.id = ? AND t.empresa_id = ?
        """,
        (terminal_id, empresa_id),
    ).fetchone()
    if not terminal_row:
        conn.close()
        abort(404)
    caja = _caja_abierta_terminal(conn, empresa_id, terminal_id, terminal_row["punto_venta_id"])
    if request.method == "POST":
        accion = request.form.get("accion") or "abrir"
        if accion == "abrir":
            if caja:
                flash("Ya hay una caja abierta para este terminal.", "warning")
            else:
                arqueo_apertura = _arqueo_desde_form(request.form, "apertura")
                usar_arqueo_apertura = request.form.get("usar_arqueo_apertura") == "1" and _form_tiene_arqueo(request.form, "apertura")
                saldo_inicial = arqueo_apertura["total"] if usar_arqueo_apertura else _to_float(request.form.get("saldo_inicial"), 0)
                cur = conn.execute(
                    """
                    INSERT INTO tpv_caja_sesiones
                    (punto_venta_id, terminal_id, fecha, turno, estado, saldo_inicial,
                     usuario_apertura, observaciones, arqueo_apertura_json, empresa_id)
                    VALUES (?, ?, ?, ?, 'abierta', ?, ?, ?, ?, ?)
                    """,
                    (
                        terminal_row["punto_venta_id"],
                        terminal_id,
                        date.today().isoformat(),
                        request.form.get("turno") or terminal_row["turno_actual"] or "",
                        saldo_inicial,
                        _usuario_actual(),
                        request.form.get("observaciones") or "",
                        json.dumps(arqueo_apertura, ensure_ascii=False),
                        empresa_id,
                    ),
                )
                session["tpv_caja_sesion_id"] = cur.lastrowid
                flash("Caja abierta.", "success")
        elif accion == "cerrar":
            caja_id = _to_int(request.form.get("caja_id"), 0)
            caja_cierre = conn.execute(
                "SELECT * FROM tpv_caja_sesiones WHERE id = ? AND terminal_id = ? AND empresa_id = ? AND estado = 'abierta'",
                (caja_id, terminal_id, empresa_id),
            ).fetchone()
            if not caja_cierre:
                flash("No hay caja abierta para cerrar.", "warning")
            else:
                resumen = _resumen_caja(conn, empresa_id, caja_cierre["id"])
                saldo_inicial = _to_float(caja_cierre["saldo_inicial"], 0)
                efectivo_teorico = round(saldo_inicial + resumen["efectivo"] + resumen["entradas"] - resumen["retiradas"], 2)
                arqueo_cierre = _arqueo_desde_form(request.form, "cierre")
                usar_arqueo_cierre = request.form.get("usar_arqueo_cierre") == "1" and _form_tiene_arqueo(request.form, "cierre")
                efectivo_contado = arqueo_cierre["total"] if usar_arqueo_cierre else _to_float(request.form.get("efectivo_contado"), efectivo_teorico)
                diferencia = round(efectivo_contado - efectivo_teorico, 2)
                conn.execute(
                    """
                    UPDATE tpv_caja_sesiones
                    SET estado = 'cerrada', efectivo_teorico = ?, efectivo_contado = ?,
                        tarjeta_teorico = ?, otros_teorico = ?, diferencia = ?,
                        usuario_cierre = ?, observaciones = ?, arqueo_cierre_json = ?,
                        fecha_cierre = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND empresa_id = ?
                    """,
                    (
                        efectivo_teorico,
                        efectivo_contado,
                        resumen["tarjeta"],
                        resumen["otros"],
                        diferencia,
                        _usuario_actual(),
                        request.form.get("observaciones") or caja_cierre["observaciones"] or "",
                        json.dumps(arqueo_cierre, ensure_ascii=False),
                        caja_cierre["id"],
                        empresa_id,
                    ),
                )
                flash(f"Caja cerrada. Diferencia: {diferencia:.2f} EUR.", "success" if abs(diferencia) < 0.01 else "warning")
        elif accion == "movimiento":
            if not caja:
                flash("Abre caja antes de registrar retiradas o entradas.", "warning")
            else:
                tipo = request.form.get("tipo") or "retirada_recepcion"
                if tipo not in {"retirada_recepcion", "retirada_direccion", "retirada", "entrada", "entrada_cambio", "fondo_extra"}:
                    tipo = "retirada_recepcion"
                importe = round(abs(_to_float(request.form.get("importe"), 0)), 2)
                if importe <= 0:
                    flash("Indica un importe para el movimiento de caja.", "warning")
                else:
                    conn.execute(
                        """
                        INSERT INTO tpv_caja_movimientos
                        (caja_sesion_id, punto_venta_id, terminal_id, tipo, importe,
                         destino, motivo, usuario, empresa_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            caja["id"],
                            terminal_row["punto_venta_id"],
                            terminal_id,
                            tipo,
                            importe,
                            request.form.get("destino") or "",
                            request.form.get("motivo") or "",
                            _usuario_actual(),
                            empresa_id,
                        ),
                    )
                    flash("Movimiento de caja registrado.", "success")
        conn.commit()
        conn.close()
        return redirect(url_for("tpv.caja_terminal", terminal_id=terminal_id))

    caja = _caja_abierta_terminal(conn, empresa_id, terminal_id, terminal_row["punto_venta_id"])
    resumen = _resumen_caja(conn, empresa_id, caja["id"]) if caja else {
        "efectivo": 0, "tarjeta": 0, "cargo_habitacion": 0, "consumo_empleado": 0,
        "invitacion": 0, "otros": 0, "entradas": 0, "retiradas": 0, "efectivo_neto": 0,
        "total": 0, "cuentas": 0, "movimientos": 0,
    }
    movimientos = _movimientos_caja(conn, empresa_id, caja["id"]) if caja else []
    historial = conn.execute(
        """
        SELECT *
        FROM tpv_caja_sesiones
        WHERE empresa_id = ? AND terminal_id = ?
        ORDER BY fecha_apertura DESC, id DESC
        LIMIT 20
        """,
        (empresa_id, terminal_id),
    ).fetchall()
    conn.close()
    return render_template(
        "tpv/caja.html",
        terminal=terminal_row,
        caja=caja,
        resumen=resumen,
        movimientos=movimientos,
        historial=historial,
        estados=ESTADOS_CAJA_TPV,
        turnos=TURNOS_TPV,
        denominaciones=DENOMINACIONES_ARQUEO_TPV,
        arqueo_apertura=_arqueo_json(caja["arqueo_apertura_json"] if caja and "arqueo_apertura_json" in caja.keys() else None),
    )


@tpv_bp.route("/terminal/<int:terminal_id>/caja/<int:caja_id>/reporte")
@login_required
def reporte_caja_terminal(terminal_id, caja_id):
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    caja = conn.execute(
        """
        SELECT c.*, t.nombre AS terminal_nombre, pv.nombre AS punto_nombre
        FROM tpv_caja_sesiones c
        LEFT JOIN tpv_terminales t ON t.id = c.terminal_id
        LEFT JOIN puntos_venta pv ON pv.id = c.punto_venta_id
        WHERE c.id = ? AND c.terminal_id = ? AND c.empresa_id = ?
        """,
        (caja_id, terminal_id, empresa_id),
    ).fetchone()
    if not caja:
        conn.close()
        abort(404)
    resumen = _resumen_caja(conn, empresa_id, caja_id)
    movimientos = _movimientos_caja(conn, empresa_id, caja_id)
    ventas_articulos = _ventas_articulos_caja(conn, empresa_id, caja_id)
    consumo_stock = _consumo_stock_previsto_caja(conn, empresa_id, caja_id)
    arqueo_apertura = _arqueo_json(caja["arqueo_apertura_json"] if "arqueo_apertura_json" in caja.keys() else None)
    arqueo_cierre = _arqueo_json(caja["arqueo_cierre_json"] if "arqueo_cierre_json" in caja.keys() else None)
    conn.close()
    modo = "Z" if caja["estado"] == "cerrada" else "X"
    return render_template(
        "tpv/caja_reporte.html",
        caja=caja,
        resumen=resumen,
        movimientos=movimientos,
        ventas_articulos=ventas_articulos,
        consumo_stock=consumo_stock,
        arqueo_apertura=arqueo_apertura,
        arqueo_cierre=arqueo_cierre,
        modo=modo,
        formas_pago=FORMAS_PAGO_TPV,
    )


@tpv_bp.route("/terminal/<int:terminal_id>/comanda", methods=["POST"])
@login_required
def crear_comanda(terminal_id):
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    bloqueo = _requiere_operar_tpv(conn)
    if bloqueo:
        return bloqueo
    terminal_row = conn.execute(
        """
        SELECT t.*, pv.nombre AS punto_nombre, pv.tpv_admite_cargo_habitacion,
               pv.tpv_permite_todo_incluido
        FROM tpv_terminales t
        LEFT JOIN puntos_venta pv ON t.punto_venta_id = pv.id
        WHERE t.id = ? AND t.empresa_id = ?
        """,
        (terminal_id, empresa_id),
    ).fetchone()
    if not terminal_row:
        conn.close()
        abort(404)
    lineas_form = _lineas_json_form(request.form.get("lineas_json"))
    if not lineas_form:
        conn.close()
        flash("Anade articulos antes de enviar la comanda.", "warning")
        return redirect(url_for("tpv.terminal", terminal_id=terminal_id))

    punto_id = terminal_row["punto_venta_id"]
    ids = []
    for item in lineas_form:
        articulo_id = _to_int(item.get("articulo_id") if isinstance(item, dict) else None, 0)
        if articulo_id:
            ids.append(articulo_id)
    if not ids:
        conn.close()
        flash("La comanda no tiene articulos validos.", "warning")
        return redirect(url_for("tpv.terminal", terminal_id=terminal_id))

    placeholders = ",".join("?" for _ in ids)
    articulos = conn.execute(
        f"""
        SELECT *
        FROM tpv_articulos
        WHERE empresa_id = ? AND punto_venta_id = ? AND id IN ({placeholders})
        """,
        (empresa_id, punto_id, *ids),
    ).fetchall()
    articulos_map = {row["id"]: row for row in articulos}

    mesa_id = request.form.get("mesa_id") or ""
    mesa = None
    if mesa_id.isdigit():
        mesa = conn.execute(
            """
            SELECT *
            FROM tpv_mesas
            WHERE id = ? AND punto_venta_id = ? AND empresa_id = ?
            """,
            (int(mesa_id), punto_id, empresa_id),
        ).fetchone()
    mesa_nombre = ""
    if mesa:
        mesa_nombre = mesa["nombre"] or mesa["numero"] or f"Mesa {mesa['id']}"
    elif request.form.get("mesa_nombre"):
        mesa_nombre = request.form.get("mesa_nombre")

    subtotal = 0.0
    suplemento_total = 0.0
    lineas_insertar = []
    marchar_curso_form = (request.form.get("marchar_curso") or "").strip()
    for item in lineas_form:
        if not isinstance(item, dict):
            continue
        articulo_id = _to_int(item.get("articulo_id"), 0)
        articulo = articulos_map.get(articulo_id)
        if not articulo:
            continue
        cantidad = max(_to_float(item.get("cantidad"), 1), 0)
        if cantidad <= 0:
            continue
        precio = _to_float(articulo["precio"], 0)
        suplemento = _to_float(articulo["suplemento"], 0)
        total_linea = cantidad * precio
        subtotal += total_linea
        suplemento_total += cantidad * suplemento
        destino = articulo["destino_preparacion"] or "sin_preparacion"
        curso = (item.get("course") if isinstance(item, dict) else "") or ""
        comentario_linea = item.get("comentario") or item.get("course") or ""
        if marchar_curso_form and curso == marchar_curso_form and "Marchar" not in comentario_linea:
            comentario_linea = f"Marchar {curso} | {comentario_linea or curso}"
        retenible = curso in {"Primero", "Segundo", "Postre"} and "Marchar" not in comentario_linea
        estado_linea = "entregada" if destino == "sin_preparacion" else ("retenida" if retenible else "pendiente")
        lineas_insertar.append(
            {
                "articulo": articulo,
                "cantidad": cantidad,
                "precio": precio,
                "suplemento": suplemento,
                "total": total_linea,
                "estado": estado_linea,
                "comentario": comentario_linea,
            }
        )
    if not lineas_insertar:
        conn.close()
        flash("La comanda no tiene articulos validos.", "warning")
        return redirect(url_for("tpv.terminal", terminal_id=terminal_id))

    finalizar_form = request.form.get("finalizar") or ""
    forma_pago_form = request.form.get("forma_pago") or ""
    cargo_habitacion = (
        bool(request.form.get("cargo_habitacion"))
        or (finalizar_form == "cobrar" and forma_pago_form == "cargo_habitacion")
    ) and bool(terminal_row["tpv_admite_cargo_habitacion"])
    reserva_cargo = None
    cargo_bloqueado = False
    if cargo_habitacion:
        _ensure_recepcion_for_tpv(conn)
        reserva_id_form = request.form.get("reserva_id") or ""
        reserva_cargo = _reserva_activa_tpv(
            conn,
            empresa_id,
            _to_int(reserva_id_form, 0) if reserva_id_form.isdigit() else None,
            request.form.get("habitacion") or "",
        )
        if reserva_cargo:
            politica = (reserva_cargo["politica_credito"] if "politica_credito" in reserva_cargo.keys() else "abierto") or "abierto"
            cargo_bloqueado = politica == "sin_credito"

    habitacion_txt = (request.form.get("habitacion") or _habitacion_nombre(reserva_cargo) if reserva_cargo else request.form.get("habitacion") or "").strip()
    cliente_txt = (request.form.get("cliente") or (reserva_cargo["titular_nombre"] if reserva_cargo else "") or "").strip()
    modo_servicio = request.form.get("modo_servicio") or "mesa"
    pax = _to_int(request.form.get("pax"), 0)
    camarero = (request.form.get("camarero") or _usuario_actual()).strip()
    prioridad = request.form.get("prioridad") or "normal"
    if prioridad not in PRIORIDADES_COMANDA:
        prioridad = "normal"
    caja_actual = _caja_abierta_terminal(conn, empresa_id, terminal_id, punto_id)
    cuenta_form = request.form.get("cuenta_id") or ""
    cuenta = _cuenta_para_comanda(
        conn,
        empresa_id,
        punto_id,
        terminal_id,
        cuenta_id=_to_int(cuenta_form, 0) if cuenta_form.isdigit() else None,
        mesa_id=int(mesa_id) if mesa_id.isdigit() else None,
        mesa_nombre=mesa_nombre,
        modo_servicio=modo_servicio,
        reserva_id=reserva_cargo["id"] if reserva_cargo else None,
        habitacion=habitacion_txt,
        cliente=cliente_txt,
        pax=pax,
        cargo_habitacion=cargo_habitacion,
        observaciones=request.form.get("observaciones") or "",
        caja_sesion_id=caja_actual["id"] if caja_actual else None,
    )

    numero = _numero_comanda(conn, empresa_id)
    cur = conn.execute(
        """
        INSERT INTO tpv_comandas
        (numero, cuenta_id, punto_venta_id, terminal_id, mesa_id, mesa_nombre, modo_servicio,
         reserva_id, habitacion, cliente, pax, estado, subtotal, suplemento, total,
         cargo_habitacion, observaciones, avisos, camarero, prioridad, usuario, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'enviada', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            numero,
            cuenta["id"] if cuenta else None,
            punto_id,
            terminal_id,
            int(mesa_id) if mesa_id.isdigit() else None,
            mesa_nombre,
            modo_servicio,
            reserva_cargo["id"] if reserva_cargo else None,
            habitacion_txt,
            cliente_txt,
            pax,
            subtotal,
            suplemento_total,
            subtotal,
            1 if cargo_habitacion else 0,
            request.form.get("observaciones") or "",
            request.form.get("avisos") or "",
            camarero,
            prioridad,
            _usuario_actual(),
            empresa_id,
        ),
    )
    comanda_id = cur.lastrowid
    cargos_creados = 0
    for linea in lineas_insertar:
        articulo = linea["articulo"]
        incluido_regimen = False
        if reserva_cargo and terminal_row["tpv_permite_todo_incluido"]:
            incluido_regimen = _articulo_incluido_regimen(articulo, reserva_cargo["regimen"] or "")
        linea_cur = conn.execute(
            """
            INSERT INTO tpv_comanda_lineas
            (comanda_id, punto_venta_id, articulo_id, producto_id, receta_id, nombre,
             cantidad, precio, suplemento, total, destino_preparacion, estado,
             comentario, avisos, prioridad, incluido, usuario, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                comanda_id,
                punto_id,
                articulo["id"],
                articulo["producto_id"],
                articulo["receta_id"],
                articulo["nombre"],
                linea["cantidad"],
                linea["precio"],
                linea["suplemento"],
                linea["total"],
                articulo["destino_preparacion"] or "sin_preparacion",
                linea["estado"],
                linea["comentario"],
                request.form.get("avisos") or "",
                prioridad,
                1 if incluido_regimen else 0,
                _usuario_actual(),
                empresa_id,
            ),
        )
        linea_id = linea_cur.lastrowid
        if cargo_habitacion and reserva_cargo and not cargo_bloqueado:
            cargo_total = round(linea["cantidad"] * linea["suplemento"], 2) if incluido_regimen else round(linea["total"], 2)
            cargo_incluido = 1 if incluido_regimen and cargo_total <= 0 else 0
            concepto = linea["articulo"]["nombre"]
            if incluido_regimen and cargo_total > 0:
                concepto = f"Suplemento {concepto}"
            observaciones = " | ".join(
                item for item in [
                    f"TPV {terminal_row['punto_nombre'] or ''}".strip(),
                    f"Comanda {numero}",
                    request.form.get("avisos") or "",
                    linea["comentario"],
                ]
                if item
            )
            cargo_cur = conn.execute(
                """
                INSERT INTO recepcion_cargos_reserva
                (reserva_id, punto_venta_id, fecha, servicio, concepto, cantidad,
                 precio_unitario, total, incluido, cargo_habitacion, estado,
                 observaciones, empresa_id, usuario_id, tpv_comanda_id, tpv_linea_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'pendiente', ?, ?, ?, ?, ?)
                """,
                (
                    reserva_cargo["id"],
                    punto_id,
                    date.today().isoformat(),
                    request.form.get("modo_servicio") or "tpv",
                    concepto,
                    linea["cantidad"],
                    round(cargo_total / linea["cantidad"], 2) if linea["cantidad"] else 0,
                    cargo_total,
                    cargo_incluido,
                    observaciones,
                    empresa_id,
                    session.get("user_id"),
                    comanda_id,
                    linea_id,
                ),
            )
            conn.execute(
                """
                UPDATE tpv_comanda_lineas
                SET recepcion_cargo_id = ?
                WHERE id = ? AND empresa_id = ?
                """,
                (cargo_cur.lastrowid, linea_id, empresa_id),
            )
            cargos_creados += 1
    finalizar = finalizar_form
    forma_pago = forma_pago_form
    importe_entregado = request.form.get("importe_entregado") or ""
    cambio = request.form.get("cambio") or ""
    cuenta_cobrada = False
    cobro_bloqueado = ""
    if cuenta:
        _actualizar_cuenta_totales(conn, cuenta["id"], empresa_id)
        if finalizar == "cobrar":
            cuenta_actualizada = conn.execute(
                "SELECT * FROM tpv_cuentas WHERE id = ? AND empresa_id = ?",
                (cuenta["id"], empresa_id),
            ).fetchone()
            if cuenta_actualizada:
                total_cuenta = _to_float(cuenta_actualizada["total"], 0)
                recibido = _to_float(importe_entregado, 0)
                if forma_pago == "efectivo" and recibido + 0.001 < total_cuenta:
                    cobro_bloqueado = f"Falta efectivo: total {total_cuenta:.2f} EUR, entregado {recibido:.2f} EUR."
                elif forma_pago == "cargo_habitacion":
                    cargo_ok, cargo_msg = _registrar_cargo_habitacion_cuenta_tpv(conn, empresa_id, cuenta_actualizada)
                    if not cargo_ok:
                        cobro_bloqueado = cargo_msg
                else:
                    cargo_ok = True
                if not cobro_bloqueado:
                    _actualizar_estado_cuenta_tpv(
                        conn,
                        empresa_id,
                        cuenta_actualizada,
                        "cobrar",
                        forma_pago=forma_pago,
                        importe_entregado=importe_entregado,
                        cambio=cambio,
                        motivo="Cobro directo desde terminal TPV",
                    )
                    cuenta_cobrada = True
    if mesa and not cuenta_cobrada:
        conn.execute(
            """
            UPDATE tpv_mesas
            SET estado = 'ocupada', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND empresa_id = ?
            """,
            (mesa["id"], empresa_id),
        )
    conn.commit()
    conn.close()
    cuenta_txt = f" en cuenta {cuenta['numero']}" if cuenta else ""
    if cobro_bloqueado:
        flash(f"Comanda {numero}{cuenta_txt} enviada, pero no se cobro. {cobro_bloqueado}", "warning")
    elif cuenta_cobrada:
        flash(f"Comanda {numero}{cuenta_txt} enviada y cobrada.", "success")
    elif cargo_habitacion and cargo_bloqueado:
        flash(f"Comanda {numero}{cuenta_txt} enviada. No se cargo a habitacion porque la reserva no tiene credito abierto.", "warning")
    elif cargo_habitacion and not reserva_cargo:
        flash(f"Comanda {numero}{cuenta_txt} enviada. No se encontro una reserva activa para cargar a habitacion.", "warning")
    elif cargo_habitacion:
        flash(f"Comanda {numero}{cuenta_txt} enviada. Cargos enviados a recepcion: {cargos_creados}.", "success")
    else:
        flash(f"Comanda {numero}{cuenta_txt} enviada.", "success")
    if request.form.get("origen") == "comandero":
        return redirect(url_for("tpv.comandero", terminal_id=terminal_id, cuenta_id=cuenta["id"] if cuenta else ""))
    if cuenta and not cuenta_cobrada:
        return redirect(url_for("tpv.terminal", terminal_id=terminal_id, cuenta_id=cuenta["id"]))
    return redirect(url_for("tpv.terminal", terminal_id=terminal_id))


@tpv_bp.route("/terminal/<int:terminal_id>/cobrar", methods=["POST"])
@login_required
def cobrar_terminal(terminal_id):
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    bloqueo = _requiere_operar_tpv(conn)
    if bloqueo:
        return bloqueo
    terminal_row = conn.execute(
        """
        SELECT *
        FROM tpv_terminales
        WHERE id = ? AND empresa_id = ?
        """,
        (terminal_id, empresa_id),
    ).fetchone()
    if not terminal_row:
        conn.close()
        abort(404)
    cuenta_id = request.form.get("cuenta_id") or ""
    accion = request.form.get("accion") or "cobrar"
    cuenta = None
    if cuenta_id.isdigit():
        cuenta = conn.execute(
            """
            SELECT *
            FROM tpv_cuentas
            WHERE id = ?
              AND empresa_id = ?
              AND punto_venta_id = ?
            """,
            (int(cuenta_id), empresa_id, terminal_row["punto_venta_id"]),
        ).fetchone()
    if not cuenta:
        flash("Selecciona una cuenta abierta para cobrar.", "warning")
    elif cuenta["estado"] != "abierta":
        flash(f"La cuenta {cuenta['numero']} ya no esta abierta.", "warning")
    else:
        _actualizar_cuenta_totales(conn, cuenta["id"], empresa_id)
        cuenta = conn.execute(
            "SELECT * FROM tpv_cuentas WHERE id = ? AND empresa_id = ?",
            (cuenta["id"], empresa_id),
        ).fetchone()
        forma_pago = request.form.get("forma_pago") or ""
        importe_entregado = request.form.get("importe_entregado") or ""
        total_cuenta = _to_float(cuenta["total"], 0)
        recibido = _to_float(importe_entregado, 0)
        caja_actual = _caja_abierta_terminal(conn, empresa_id, terminal_id, terminal_row["punto_venta_id"])
        if caja_actual and not cuenta["caja_sesion_id"]:
            conn.execute(
                "UPDATE tpv_cuentas SET caja_sesion_id = ? WHERE id = ? AND empresa_id = ?",
                (caja_actual["id"], cuenta["id"], empresa_id),
            )
            cuenta = conn.execute(
                "SELECT * FROM tpv_cuentas WHERE id = ? AND empresa_id = ?",
                (cuenta["id"], empresa_id),
            ).fetchone()
        if accion == "cobrar" and forma_pago == "efectivo" and recibido + 0.001 < total_cuenta:
            flash(f"No se cobro la cuenta {cuenta['numero']}: entregado {recibido:.2f} EUR y total {total_cuenta:.2f} EUR.", "warning")
        elif accion == "cobrar" and forma_pago == "cargo_habitacion":
            cargo_ok, cargo_msg = _registrar_cargo_habitacion_cuenta_tpv(conn, empresa_id, cuenta)
            if not cargo_ok:
                flash(f"No se cobro la cuenta {cuenta['numero']}: {cargo_msg}", "warning")
            else:
                nuevo_estado = _actualizar_estado_cuenta_tpv(
                    conn,
                    empresa_id,
                    cuenta,
                    accion,
                    forma_pago=forma_pago,
                    importe_entregado=importe_entregado,
                    cambio=request.form.get("cambio") or "",
                    motivo=request.form.get("motivo") or "Accion directa desde terminal TPV",
                )
                flash(f"Cuenta {cuenta['numero']} cargada a habitacion y mesa liberada.", "success")
        else:
            nuevo_estado = _actualizar_estado_cuenta_tpv(
                conn,
                empresa_id,
                cuenta,
                accion,
                forma_pago=forma_pago,
                importe_entregado=importe_entregado,
                cambio=request.form.get("cambio") or "",
                motivo=request.form.get("motivo") or "Accion directa desde terminal TPV",
            )
            if nuevo_estado == "cobrada":
                cambio_txt = ""
                if forma_pago == "efectivo":
                    cambio_txt = f" Cambio: {_to_float(request.form.get('cambio'), 0):.2f} EUR."
                flash(f"Cuenta {cuenta['numero']} cobrada y mesa liberada.{cambio_txt}", "success")
            elif nuevo_estado == "cerrada":
                flash(f"Cuenta {cuenta['numero']} cerrada.", "success")
            elif nuevo_estado == "cancelada":
                flash(f"Cuenta {cuenta['numero']} cancelada.", "success")
            else:
                flash(f"Cuenta {cuenta['numero']} actualizada.", "success")
    conn.commit()
    conn.close()
    return redirect(url_for("tpv.terminal", terminal_id=terminal_id))


@tpv_bp.route("/terminal/<int:terminal_id>/mesa-operacion", methods=["POST"])
@login_required
def mesa_operacion_terminal(terminal_id):
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    bloqueo = _requiere_operar_tpv(conn)
    if bloqueo:
        return bloqueo
    terminal_row = conn.execute(
        """
        SELECT *
        FROM tpv_terminales
        WHERE id = ? AND empresa_id = ?
        """,
        (terminal_id, empresa_id),
    ).fetchone()
    if not terminal_row:
        conn.close()
        abort(404)
    cuenta_id = _to_int(request.form.get("cuenta_id"), 0)
    mesa_destino_id = _to_int(request.form.get("mesa_destino_id"), 0)
    modo_destino = (request.form.get("destino_modo") or "barra").strip() or "barra"
    etiqueta_destino = (request.form.get("destino_label") or "").strip()
    accion = request.form.get("accion") or "traspasar"
    cuenta = conn.execute(
        """
        SELECT *
        FROM tpv_cuentas
        WHERE id = ? AND empresa_id = ? AND punto_venta_id = ? AND estado = 'abierta'
        """,
        (cuenta_id, empresa_id, terminal_row["punto_venta_id"]),
    ).fetchone()
    mesa_destino = None
    if mesa_destino_id:
        mesa_destino = conn.execute(
            """
            SELECT *
            FROM tpv_mesas
            WHERE id = ? AND empresa_id = ? AND punto_venta_id = ? AND activo = 1
            """,
            (mesa_destino_id, empresa_id, terminal_row["punto_venta_id"]),
        ).fetchone()
    destino_cuenta_id = cuenta_id
    if not cuenta:
        flash("Selecciona una cuenta abierta para operar con mesas.", "warning")
    elif not mesa_destino and not etiqueta_destino:
        flash("Selecciona una mesa, barra o taburete destino.", "warning")
    elif accion == "juntar":
        cuenta_destino = (
            _cuenta_abierta_mesa(conn, empresa_id, terminal_row["punto_venta_id"], mesa_destino["id"])
            if mesa_destino else
            _cuenta_abierta_servicio(conn, empresa_id, terminal_row["punto_venta_id"], modo_destino, etiqueta_destino)
        )
        if not cuenta_destino:
            if mesa_destino:
                _traspasar_cuenta_mesa_tpv(conn, empresa_id, cuenta, mesa_destino)
                flash(f"No habia cuenta en {_mesa_nombre_tpv(mesa_destino)}; se traspaso la cuenta.", "success")
            else:
                _traspasar_cuenta_servicio_tpv(conn, empresa_id, cuenta, modo_destino, etiqueta_destino)
                flash(f"No habia cuenta en {etiqueta_destino}; se traspaso la cuenta.", "success")
        elif cuenta_destino["id"] == cuenta["id"]:
            flash("La cuenta ya esta en ese destino.", "warning")
        else:
            if mesa_destino:
                _juntar_cuentas_tpv(conn, empresa_id, cuenta, cuenta_destino, mesa_destino)
            else:
                _juntar_cuentas_servicio_tpv(conn, empresa_id, cuenta, cuenta_destino, modo_destino, etiqueta_destino)
            destino_cuenta_id = cuenta_destino["id"]
            flash(f"Cuentas juntadas en {_mesa_nombre_tpv(mesa_destino) if mesa_destino else etiqueta_destino}.", "success")
    else:
        cuenta_destino = (
            _cuenta_abierta_mesa(conn, empresa_id, terminal_row["punto_venta_id"], mesa_destino["id"])
            if mesa_destino else
            _cuenta_abierta_servicio(conn, empresa_id, terminal_row["punto_venta_id"], modo_destino, etiqueta_destino)
        )
        if cuenta_destino and cuenta_destino["id"] != cuenta["id"]:
            flash("El destino ya tiene cuenta abierta. Usa Juntar.", "warning")
        else:
            if mesa_destino:
                _traspasar_cuenta_mesa_tpv(conn, empresa_id, cuenta, mesa_destino)
                flash(f"Cuenta traspasada a {_mesa_nombre_tpv(mesa_destino)}.", "success")
            else:
                _traspasar_cuenta_servicio_tpv(conn, empresa_id, cuenta, modo_destino, etiqueta_destino)
                flash(f"Cuenta traspasada a {etiqueta_destino}.", "success")
    conn.commit()
    conn.close()
    return redirect(url_for("tpv.terminal", terminal_id=terminal_id, cuenta_id=destino_cuenta_id))


@tpv_bp.route("/cuentas", methods=["GET", "POST"])
@login_required
def cuentas():
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    terminal_id_arg = request.values.get("terminal_id") or str(session.get("tpv_terminal_id") or "")
    if request.method == "POST":
        bloqueo = _requiere_operar_tpv(conn)
        if bloqueo:
            return bloqueo
        cuenta_id = request.form.get("cuenta_id") or ""
        accion = request.form.get("accion") or "cobrar"
        cuenta = None
        if cuenta_id.isdigit():
            cuenta = conn.execute(
                "SELECT * FROM tpv_cuentas WHERE id = ? AND empresa_id = ?",
                (int(cuenta_id), empresa_id),
            ).fetchone()
        if not cuenta:
            flash("Cuenta TPV no encontrada.", "warning")
        elif accion in {"cobrar", "cerrar", "cancelar", "reabrir"}:
            _actualizar_cuenta_totales(conn, cuenta["id"], empresa_id)
            cuenta = conn.execute(
                "SELECT * FROM tpv_cuentas WHERE id = ? AND empresa_id = ?",
                (cuenta["id"], empresa_id),
            ).fetchone()
            terminal_id_pago = _to_int(terminal_id_arg, 0)
            if terminal_id_pago and not cuenta["caja_sesion_id"]:
                caja_actual = _caja_abierta_terminal(conn, empresa_id, terminal_id_pago, cuenta["punto_venta_id"])
                if caja_actual:
                    conn.execute(
                        "UPDATE tpv_cuentas SET caja_sesion_id = ? WHERE id = ? AND empresa_id = ?",
                        (caja_actual["id"], cuenta["id"], empresa_id),
                    )
                    cuenta = conn.execute(
                        "SELECT * FROM tpv_cuentas WHERE id = ? AND empresa_id = ?",
                        (cuenta["id"], empresa_id),
                    ).fetchone()
            forma_pago = request.form.get("forma_pago") or ""
            if accion == "cobrar" and forma_pago == "cargo_habitacion":
                cargo_ok, cargo_msg = _registrar_cargo_habitacion_cuenta_tpv(conn, empresa_id, cuenta)
                if not cargo_ok:
                    flash(f"No se pudo cargar la cuenta {cuenta['numero']}: {cargo_msg}", "warning")
                else:
                    _actualizar_estado_cuenta_tpv(
                        conn,
                        empresa_id,
                        cuenta,
                        accion,
                        forma_pago=forma_pago,
                        importe_entregado=request.form.get("importe_entregado") or "",
                        cambio=request.form.get("cambio") or "",
                        motivo=request.form.get("motivo") or "",
                    )
                    flash(f"Cuenta {cuenta['numero']} cargada a habitacion.", "success")
            else:
                _actualizar_estado_cuenta_tpv(
                    conn,
                    empresa_id,
                    cuenta,
                    accion,
                    forma_pago=forma_pago,
                    importe_entregado=request.form.get("importe_entregado") or "",
                    cambio=request.form.get("cambio") or "",
                    motivo=request.form.get("motivo") or "",
                )
                flash(f"Cuenta {cuenta['numero']} actualizada.", "success")
        conn.commit()
        conn.close()
        destino_id = int(cuenta_id) if cuenta_id.isdigit() else 0
        if destino_id and accion != "cancelar":
            return redirect(url_for("tpv.cuenta_detalle", cuenta_id=destino_id, terminal_id=terminal_id_arg))
        return redirect(url_for("tpv.cuentas", terminal_id=terminal_id_arg))

    estado = request.args.get("estado") or "abierta"
    punto_venta_id = request.args.get("punto_venta_id") or ""
    terminal = _terminal_activo_tpv(conn, empresa_id, _to_int(punto_venta_id, 0) if punto_venta_id.isdigit() else None)
    if terminal and not punto_venta_id:
        punto_venta_id = str(terminal["punto_venta_id"] or "")
    filtros = ["c.empresa_id = ?"]
    params = [empresa_id]
    if estado:
        filtros.append("c.estado = ?")
        params.append(estado)
    if punto_venta_id.isdigit():
        filtros.append("c.punto_venta_id = ?")
        params.append(int(punto_venta_id))
    cuentas_rows = conn.execute(
        f"""
        SELECT c.*, pv.nombre AS punto_nombre,
               (SELECT COUNT(*) FROM tpv_comandas co WHERE co.cuenta_id = c.id AND co.empresa_id = c.empresa_id) AS comandas
        FROM tpv_cuentas c
        LEFT JOIN puntos_venta pv ON c.punto_venta_id = pv.id
        WHERE {" AND ".join(filtros)}
        ORDER BY CASE c.estado WHEN 'abierta' THEN 1 ELSE 2 END, c.fecha_apertura DESC, c.id DESC
        LIMIT 200
        """,
        params,
    ).fetchall()
    puntos = _puntos_venta(conn, empresa_id)
    conn.close()
    return render_template(
        "tpv/cuentas.html",
        cuentas=cuentas_rows,
        puntos=puntos,
        estado=estado,
        punto_venta_id=punto_venta_id,
        estados=ESTADOS_CUENTA,
        formas_pago=FORMAS_PAGO_TPV,
        terminal=terminal,
        terminal_id=str(terminal["id"]) if terminal else terminal_id_arg,
    )


@tpv_bp.route("/cuentas/<int:cuenta_id>")
@login_required
def cuenta_detalle(cuenta_id):
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    cuenta = conn.execute(
        """
        SELECT c.*, pv.nombre AS punto_nombre
        FROM tpv_cuentas c
        LEFT JOIN puntos_venta pv ON c.punto_venta_id = pv.id
        WHERE c.id = ? AND c.empresa_id = ?
        """,
        (cuenta_id, empresa_id),
    ).fetchone()
    if not cuenta:
        conn.close()
        abort(404)
    comandas = conn.execute(
        """
        SELECT *
        FROM tpv_comandas
        WHERE cuenta_id = ? AND empresa_id = ?
        ORDER BY fecha_creacion, id
        """,
        (cuenta_id, empresa_id),
    ).fetchall()
    lineas = conn.execute(
        """
        SELECT l.*, c.numero AS comanda_numero
        FROM tpv_comanda_lineas l
        JOIN tpv_comandas c ON l.comanda_id = c.id
        WHERE c.cuenta_id = ? AND l.empresa_id = ?
        ORDER BY c.fecha_creacion, l.id
        """,
        (cuenta_id, empresa_id),
    ).fetchall()
    auditoria = conn.execute(
        """
        SELECT *
        FROM tpv_auditoria
        WHERE empresa_id = ? AND entidad = 'tpv_cuenta' AND entidad_id = ?
        ORDER BY fecha DESC, id DESC
        """,
        (empresa_id, cuenta_id),
    ).fetchall()
    terminal = _terminal_activo_tpv(conn, empresa_id, cuenta["punto_venta_id"])
    conn.close()
    return render_template(
        "tpv/cuenta_detalle.html",
        cuenta=cuenta,
        comandas=comandas,
        lineas=lineas,
        auditoria=auditoria,
        terminal=terminal,
        terminal_id=str(terminal["id"]) if terminal else "",
        estados=ESTADOS_CUENTA,
        formas_pago=FORMAS_PAGO_TPV,
    )


@tpv_bp.route("/pantalla", methods=["GET"])
@login_required
def pantalla():
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    destino = request.args.get("destino") or "cocina"
    punto_venta_id = request.args.get("punto_venta_id") or ""
    puntos = _puntos_venta(conn, empresa_id)
    terminal = _terminal_activo_tpv(conn, empresa_id, _to_int(punto_venta_id, 0) if punto_venta_id.isdigit() else None)
    if terminal and not punto_venta_id:
        punto_venta_id = str(terminal["punto_venta_id"] or "")
    filtros = [
        "l.empresa_id = ?",
        "COALESCE(l.estado, 'pendiente') NOT IN ('retenida', 'entregada', 'cancelada')",
        "COALESCE(c.estado, '') NOT IN ('cancelada', 'cobrada', 'entregada')",
        "(cu.id IS NULL OR cu.estado = 'abierta')",
    ]
    params = [empresa_id]
    if destino:
        filtros.append("l.destino_preparacion = ?")
        params.append(destino)
    if punto_venta_id.isdigit():
        filtros.append("l.punto_venta_id = ?")
        params.append(int(punto_venta_id))
    lineas = conn.execute(
        f"""
        SELECT l.*, c.numero AS comanda_numero, c.cuenta_id, c.mesa_nombre, c.modo_servicio,
               c.habitacion, c.cliente, c.pax, c.observaciones AS comanda_observaciones,
               c.avisos AS comanda_avisos, c.fecha_creacion AS comanda_fecha,
               c.camarero, c.prioridad AS comanda_prioridad,
               pv.nombre AS punto_nombre
        FROM tpv_comanda_lineas l
        JOIN tpv_comandas c ON l.comanda_id = c.id
        LEFT JOIN tpv_cuentas cu ON cu.id = c.cuenta_id
        LEFT JOIN puntos_venta pv ON l.punto_venta_id = pv.id
        WHERE {" AND ".join(filtros)}
        ORDER BY CASE l.estado WHEN 'pendiente' THEN 1 WHEN 'en_preparacion' THEN 2 WHEN 'lista' THEN 3 ELSE 4 END,
                 l.fecha_creacion, l.id
        """,
        params,
    ).fetchall()
    grupos_dict = {}
    for linea in lineas:
        key = linea["cuenta_id"] or linea["comanda_id"]
        comentario = linea["comentario"] or ""
        curso = "Marchar"
        for candidato in ("Entrante", "Primero", "Segundo", "Postre", "Bebida"):
            if candidato.lower() in comentario.lower():
                curso = candidato
                break
        grupo = grupos_dict.setdefault(
            key,
            {
                "key": key,
                "mesa": linea["mesa_nombre"] or linea["habitacion"] or linea["modo_servicio"] or "-",
                "pax": linea["pax"] or 0,
                "camarero": linea["camarero"] or "",
                "cliente": linea["cliente"] or "",
                "punto": linea["punto_nombre"] or "",
                "prioridad": linea["prioridad"] or linea["comanda_prioridad"] or "normal",
                "lineas": [],
                "pases": {},
            },
        )
        grupo["lineas"].append(linea)
        grupo["pases"].setdefault(curso, []).append(linea)
    grupos = list(grupos_dict.values())
    conn.close()
    return render_template(
        "tpv/pantalla.html",
        lineas=lineas,
        grupos=grupos,
        puntos=puntos,
        punto_venta_id=punto_venta_id,
        destino=destino,
        destinos=DESTINOS_PREPARACION,
        estados_linea=ESTADOS_LINEA_COMANDA,
        prioridades=PRIORIDADES_COMANDA,
        terminal=terminal,
        terminal_id=str(terminal["id"]) if terminal else "",
    )


@tpv_bp.route("/lineas/<int:linea_id>/estado", methods=["POST"])
@login_required
def linea_estado(linea_id):
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    bloqueo = _requiere_operar_tpv(conn)
    if bloqueo:
        return bloqueo
    nuevo_estado = request.form.get("estado") or ""
    destino = request.form.get("destino") or "cocina"
    punto_venta_id = request.form.get("punto_venta_id") or ""
    terminal_id = request.form.get("terminal_id") or str(session.get("tpv_terminal_id") or "")
    volver = request.form.get("volver") or ""
    if nuevo_estado not in ESTADOS_LINEA_COMANDA:
        conn.close()
        flash("Estado no valido.", "warning")
        if volver == "comandero" and terminal_id.isdigit():
            return redirect(url_for("tpv.comandero", terminal_id=int(terminal_id)))
        return redirect(url_for("tpv.pantalla", destino=destino, punto_venta_id=punto_venta_id, terminal_id=terminal_id))
    linea = conn.execute(
        """
        SELECT *
        FROM tpv_comanda_lineas
        WHERE id = ? AND empresa_id = ?
        """,
        (linea_id, empresa_id),
    ).fetchone()
    if not linea:
        conn.close()
        abort(404)
    conn.execute(
        """
        UPDATE tpv_comanda_lineas
        SET estado = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (nuevo_estado, linea_id, empresa_id),
    )
    comanda_id = linea["comanda_id"]
    resumen = conn.execute(
        """
        SELECT
            SUM(CASE WHEN estado IN ('pendiente', 'en_preparacion') THEN 1 ELSE 0 END) AS pendientes,
            SUM(CASE WHEN estado = 'lista' THEN 1 ELSE 0 END) AS listas,
            SUM(CASE WHEN estado = 'entregada' THEN 1 ELSE 0 END) AS entregadas,
            COUNT(*) AS total
        FROM tpv_comanda_lineas
        WHERE comanda_id = ? AND empresa_id = ? AND COALESCE(estado, '') <> 'cancelada'
        """,
        (comanda_id, empresa_id),
    ).fetchone()
    estado_comanda = "enviada"
    if resumen and (resumen["pendientes"] or 0) > 0:
        estado_comanda = "en_preparacion"
    elif resumen and (resumen["listas"] or 0) > 0:
        estado_comanda = "lista"
    elif resumen and (resumen["entregadas"] or 0) >= (resumen["total"] or 0):
        estado_comanda = "entregada"
    conn.execute(
        """
        UPDATE tpv_comandas
        SET estado = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (estado_comanda, comanda_id, empresa_id),
    )
    conn.commit()
    conn.close()
    if volver == "comandero" and terminal_id.isdigit():
        return redirect(url_for("tpv.comandero", terminal_id=int(terminal_id)))
    return redirect(url_for("tpv.pantalla", destino=destino, punto_venta_id=punto_venta_id, terminal_id=terminal_id))


@tpv_bp.route("/puntos/<int:punto_venta_id>/mesas", methods=["GET", "POST"])
@login_required
def mesas(punto_venta_id):
    conn = get_db()
    _ensure_tpv_schema(conn)
    empresa_id = _empresa_id()
    punto = _punto_venta_por_id(conn, empresa_id, punto_venta_id)
    if not punto:
        conn.close()
        abort(404)
    if request.method == "POST":
        bloqueo = _requiere_gestion_tpv(conn)
        if bloqueo:
            return bloqueo
        accion = request.form.get("accion") or "crear"
        if accion == "crear":
            nombre = (request.form.get("nombre") or "").strip()
            numero = (request.form.get("numero") or "").strip()
            if nombre or numero:
                conn.execute(
                    """
                    INSERT INTO tpv_mesas
                    (punto_venta_id, zona, numero, nombre, capacidad, estado, activo, empresa_id)
                    VALUES (?, ?, ?, ?, ?, 'libre', 1, ?)
                    """,
                    (
                        punto_venta_id,
                        request.form.get("zona") or "Sala",
                        numero,
                        nombre or f"Mesa {numero}",
                        max(_to_int(request.form.get("capacidad"), 2), 1),
                        empresa_id,
                    ),
                )
                flash("Mesa creada.", "success")
        elif accion == "lote":
            zona = request.form.get("zona") or "Sala"
            prefijo = request.form.get("prefijo") or "M"
            desde = max(_to_int(request.form.get("desde"), 1), 1)
            hasta = max(_to_int(request.form.get("hasta"), desde), desde)
            capacidad = max(_to_int(request.form.get("capacidad"), 2), 1)
            creadas = 0
            for numero in range(desde, hasta + 1):
                numero_txt = f"{prefijo}{numero:02d}"
                existe = conn.execute(
                    """
                    SELECT id FROM tpv_mesas
                    WHERE empresa_id = ? AND punto_venta_id = ? AND numero = ?
                    """,
                    (empresa_id, punto_venta_id, numero_txt),
                ).fetchone()
                if existe:
                    continue
                conn.execute(
                    """
                    INSERT INTO tpv_mesas
                    (punto_venta_id, zona, numero, nombre, capacidad, estado, activo, empresa_id)
                    VALUES (?, ?, ?, ?, ?, 'libre', 1, ?)
                    """,
                    (punto_venta_id, zona, numero_txt, f"Mesa {numero_txt}", capacidad, empresa_id),
                )
                creadas += 1
            flash(f"Mesas creadas: {creadas}.", "success")
        elif accion == "toggle":
            mesa_id = request.form.get("mesa_id") or ""
            if mesa_id.isdigit():
                row = conn.execute(
                    "SELECT activo FROM tpv_mesas WHERE id = ? AND punto_venta_id = ? AND empresa_id = ?",
                    (int(mesa_id), punto_venta_id, empresa_id),
                ).fetchone()
                if row:
                    conn.execute(
                        """
                        UPDATE tpv_mesas
                        SET activo = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND punto_venta_id = ? AND empresa_id = ?
                        """,
                        (0 if row["activo"] else 1, int(mesa_id), punto_venta_id, empresa_id),
                    )
        elif accion == "liberar":
            mesa_id = request.form.get("mesa_id") or ""
            if mesa_id.isdigit():
                conn.execute(
                    """
                    UPDATE tpv_mesas
                    SET estado = 'libre', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND punto_venta_id = ? AND empresa_id = ?
                    """,
                    (int(mesa_id), punto_venta_id, empresa_id),
                )
        elif accion == "guardar_mapa":
            mesas_data = _lineas_json_form(request.form.get("mesas_json"))
            elementos_data = _lineas_json_form(request.form.get("elementos_json"))
            actualizadas = 0
            for item in mesas_data:
                if not isinstance(item, dict):
                    continue
                mesa_id = _to_int(item.get("id"), 0)
                if not mesa_id:
                    continue
                x = min(max(_to_float(item.get("x"), 0), 0), 100)
                y = min(max(_to_float(item.get("y"), 0), 0), 100)
                conn.execute(
                    """
                    UPDATE tpv_mesas
                    SET x = ?, y = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND punto_venta_id = ? AND empresa_id = ?
                    """,
                    (x, y, mesa_id, punto_venta_id, empresa_id),
                )
                actualizadas += 1
            elementos_actualizados = 0
            for item in elementos_data:
                if not isinstance(item, dict):
                    continue
                elemento_id = _to_int(item.get("id"), 0)
                if not elemento_id:
                    continue
                conn.execute(
                    """
                    UPDATE tpv_mapa_elementos
                    SET x = ?, y = ?, ancho = ?, alto = ?, rotacion = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND punto_venta_id = ? AND empresa_id = ?
                    """,
                    (
                        min(max(_to_float(item.get("x"), 0), 0), 100),
                        min(max(_to_float(item.get("y"), 0), 0), 100),
                        min(max(_to_float(item.get("ancho"), 8), 3), 100),
                        min(max(_to_float(item.get("alto"), 5), 3), 100),
                        _to_float(item.get("rotacion"), 0) % 360,
                        elemento_id,
                        punto_venta_id,
                        empresa_id,
                    ),
                )
                elementos_actualizados += 1
            flash(f"Mapa actualizado: {actualizadas} mesas y {elementos_actualizados} elementos.", "success")
        elif accion == "crear_elemento":
            tipo = request.form.get("tipo") or "zona"
            tipos_elemento = {
                "zona", "barra", "taburete", "sofa", "pared", "puerta", "ventana",
                "texto", "planta", "columna", "caja", "cocina", "bano", "escalera",
            }
            if tipo == "mesa":
                numero = (request.form.get("numero") or "").strip()
                if not numero:
                    total_mesas = conn.execute(
                        """
                        SELECT COUNT(*) AS total
                        FROM tpv_mesas
                        WHERE empresa_id = ? AND punto_venta_id = ?
                        """,
                        (empresa_id, punto_venta_id),
                    ).fetchone()["total"]
                    numero = f"M{int(total_mesas or 0) + 1:02d}"
                nombre = (request.form.get("etiqueta") or "").strip() or f"Mesa {numero}"
                conn.execute(
                    """
                    INSERT INTO tpv_mesas
                    (punto_venta_id, zona, numero, nombre, capacidad, estado, activo, x, y, empresa_id)
                    VALUES (?, ?, ?, ?, ?, 'libre', 1, ?, ?, ?)
                    """,
                    (
                        punto_venta_id,
                        request.form.get("zona") or "Sala",
                        numero,
                        nombre,
                        max(_to_int(request.form.get("capacidad"), 2), 1),
                        min(max(_to_float(request.form.get("x"), 8), 0), 100),
                        min(max(_to_float(request.form.get("y"), 8), 0), 100),
                        empresa_id,
                    ),
                )
                flash("Mesa insertada en el plano.", "success")
            else:
                if tipo not in tipos_elemento:
                    tipo = "zona"
                conn.execute(
                    """
                    INSERT INTO tpv_mapa_elementos
                    (punto_venta_id, tipo, etiqueta, x, y, ancho, alto, rotacion, color, orden, activo, empresa_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?)
                    """,
                    (
                        punto_venta_id,
                        tipo,
                        request.form.get("etiqueta") or tipo.title(),
                        min(max(_to_float(request.form.get("x"), 8), 0), 100),
                        min(max(_to_float(request.form.get("y"), 8), 0), 100),
                        min(max(_to_float(request.form.get("ancho"), 18), 3), 100),
                        min(max(_to_float(request.form.get("alto"), 8), 3), 100),
                        _to_float(request.form.get("rotacion"), 0) % 360,
                        request.form.get("color") or "#334155",
                        empresa_id,
                    ),
                )
                flash("Elemento anadido al mapa.", "success")
        elif accion == "borrar_elemento":
            elemento_id = request.form.get("elemento_id") or ""
            if elemento_id.isdigit():
                conn.execute(
                    """
                    UPDATE tpv_mapa_elementos
                    SET activo = 0, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND punto_venta_id = ? AND empresa_id = ?
                    """,
                    (int(elemento_id), punto_venta_id, empresa_id),
                )
                flash("Elemento ocultado.", "success")
        elif accion == "editar":
            mesa_id = request.form.get("mesa_id") or ""
            if mesa_id.isdigit():
                conn.execute(
                    """
                    UPDATE tpv_mesas
                    SET zona = ?, numero = ?, nombre = ?, capacidad = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND punto_venta_id = ? AND empresa_id = ?
                    """,
                    (
                        request.form.get("zona") or "Sala",
                        request.form.get("numero") or "",
                        request.form.get("nombre") or "",
                        max(_to_int(request.form.get("capacidad"), 2), 1),
                        int(mesa_id),
                        punto_venta_id,
                        empresa_id,
                    ),
                )
                flash("Mesa actualizada.", "success")
        conn.commit()
        conn.close()
        return redirect(url_for("tpv.mesas", punto_venta_id=punto_venta_id))
    mesas_rows = conn.execute(
        """
        SELECT *
        FROM tpv_mesas
        WHERE empresa_id = ? AND punto_venta_id = ?
        ORDER BY zona, CAST(numero AS INTEGER), numero, nombre
        """,
        (empresa_id, punto_venta_id),
    ).fetchall()
    elementos = conn.execute(
        """
        SELECT *
        FROM tpv_mapa_elementos
        WHERE empresa_id = ? AND punto_venta_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY orden, id
        """,
        (empresa_id, punto_venta_id),
    ).fetchall()
    conn.close()
    return render_template("tpv/mesas.html", punto=punto, mesas=mesas_rows, elementos=elementos)

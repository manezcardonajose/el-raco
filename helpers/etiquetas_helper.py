from datetime import datetime
import secrets
from helpers.centros_helper import asignar_centro_desde_departamento, centro_activo_id


DEFAULT_LABEL_CONFIGS = [
    {
        "codigo": "rollo_58x40",
        "nombre": "Rollo 58 x 40 mm",
        "tipo_impresora": "termica",
        "formato": "rollo",
        "ancho_mm": 58,
        "alto_mm": 40,
        "columnas": 1,
        "margen_mm": 0,
        "separacion_mm": 0,
        "padding_mm": 2,
        "fuente_base_px": 9,
        "es_default": 1,
        "descripcion": "Etiqueta pequena para productos y lotes individuales.",
    },
    {
        "codigo": "rollo_80x50",
        "nombre": "Rollo 80 x 50 mm",
        "tipo_impresora": "termica",
        "formato": "rollo",
        "ancho_mm": 80,
        "alto_mm": 50,
        "columnas": 1,
        "margen_mm": 0,
        "separacion_mm": 0,
        "padding_mm": 3,
        "fuente_base_px": 10,
        "es_default": 0,
        "descripcion": "Etiqueta termica media, mas legible para APPCC.",
    },
    {
        "codigo": "rollo_100x75",
        "nombre": "Rollo 100 x 75 mm",
        "tipo_impresora": "termica",
        "formato": "rollo",
        "ancho_mm": 100,
        "alto_mm": 75,
        "columnas": 1,
        "margen_mm": 0,
        "separacion_mm": 0,
        "padding_mm": 4,
        "fuente_base_px": 11,
        "es_default": 0,
        "descripcion": "Etiqueta grande para elaboraciones con ingredientes y alergenos.",
    },
    {
        "codigo": "a4_2x4",
        "nombre": "A4 2 columnas x 4 filas",
        "tipo_impresora": "laser_inkjet",
        "formato": "a4",
        "ancho_mm": 99,
        "alto_mm": 68,
        "columnas": 2,
        "margen_mm": 6,
        "separacion_mm": 3,
        "padding_mm": 4,
        "fuente_base_px": 10,
        "es_default": 0,
        "descripcion": "Hoja A4 de etiquetas grandes.",
    },
    {
        "codigo": "a4_3x8",
        "nombre": "A4 3 columnas x 8 filas",
        "tipo_impresora": "laser_inkjet",
        "formato": "a4",
        "ancho_mm": 63.5,
        "alto_mm": 33.9,
        "columnas": 3,
        "margen_mm": 6,
        "separacion_mm": 2,
        "padding_mm": 2,
        "fuente_base_px": 8,
        "es_default": 0,
        "descripcion": "Hoja A4 de etiquetas pequenas.",
    },
]


def _to_float(value, default=0.0):
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _to_int(value, default=0):
    try:
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def _columnas_tabla(conn, tabla):
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({tabla})").fetchall()]
    except Exception:
        return []


def _safe_add_column(conn, tabla, definicion):
    nombre = definicion.strip().split()[0]
    if nombre not in _columnas_tabla(conn, tabla):
        conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {definicion}")


def ensure_etiquetas_schema(conn, empresa_id=1):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config_etiquetas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            codigo TEXT NOT NULL,
            nombre TEXT NOT NULL,
            tipo_impresora TEXT DEFAULT 'termica',
            impresora_nombre TEXT,
            formato TEXT DEFAULT 'rollo',
            ancho_mm REAL DEFAULT 58,
            alto_mm REAL DEFAULT 40,
            columnas INTEGER DEFAULT 1,
            margen_mm REAL DEFAULT 0,
            separacion_mm REAL DEFAULT 0,
            padding_mm REAL DEFAULT 2,
            fuente_base_px INTEGER DEFAULT 9,
            auto_print INTEGER DEFAULT 0,
            activo INTEGER DEFAULT 1,
            es_default INTEGER DEFAULT 0,
            descripcion TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(empresa_id, codigo)
        )
        """
    )
    _safe_add_column(conn, "config_etiquetas", "impresora_nombre TEXT")
    _safe_add_column(conn, "config_etiquetas", "auto_print INTEGER DEFAULT 0")
    _safe_add_column(conn, "config_etiquetas", "updated_at TEXT")

    rows = conn.execute(
        "SELECT COUNT(*) AS total FROM config_etiquetas WHERE empresa_id = ?",
        (empresa_id,),
    ).fetchone()
    if not rows or int(rows["total"] or 0) == 0:
        for cfg in DEFAULT_LABEL_CONFIGS:
            conn.execute(
                """
                INSERT OR IGNORE INTO config_etiquetas (
                    empresa_id, codigo, nombre, tipo_impresora, formato,
                    ancho_mm, alto_mm, columnas, margen_mm, separacion_mm,
                    padding_mm, fuente_base_px, auto_print, activo, es_default, descripcion
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?)
                """,
                (
                    empresa_id,
                    cfg["codigo"],
                    cfg["nombre"],
                    cfg["tipo_impresora"],
                    cfg["formato"],
                    cfg["ancho_mm"],
                    cfg["alto_mm"],
                    cfg["columnas"],
                    cfg["margen_mm"],
                    cfg["separacion_mm"],
                    cfg["padding_mm"],
                    cfg["fuente_base_px"],
                    cfg["es_default"],
                    cfg["descripcion"],
                ),
            )

    default_row = conn.execute(
        """
        SELECT id FROM config_etiquetas
        WHERE empresa_id = ? AND activo = 1 AND es_default = 1
        ORDER BY id
        LIMIT 1
        """,
        (empresa_id,),
    ).fetchone()
    if not default_row:
        first_active = conn.execute(
            """
            SELECT id FROM config_etiquetas
            WHERE empresa_id = ? AND activo = 1
            ORDER BY id
            LIMIT 1
            """,
            (empresa_id,),
        ).fetchone()
        if first_active:
            conn.execute(
                "UPDATE config_etiquetas SET es_default = CASE WHEN id = ? THEN 1 ELSE 0 END WHERE empresa_id = ?",
                (first_active["id"], empresa_id),
            )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config_dispositivos_impresion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            device_uid TEXT NOT NULL,
            nombre TEXT NOT NULL,
            tipo_dispositivo TEXT DEFAULT 'tablet',
            ubicacion TEXT,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            equipo_mantenimiento_id INTEGER,
            etiqueta_config_id INTEGER,
            impresora_etiquetas_nombre TEXT,
            impresora_documentos_nombre TEXT,
            api_token TEXT,
            ultimo_ping TEXT,
            version_agente TEXT,
            ip_ultima TEXT,
            auto_print_etiquetas INTEGER DEFAULT 0,
            activo INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(empresa_id, device_uid)
        )
        """
    )
    _safe_add_column(conn, "config_dispositivos_impresion", "tipo_dispositivo TEXT DEFAULT 'tablet'")
    _safe_add_column(conn, "config_dispositivos_impresion", "ubicacion TEXT")
    _safe_add_column(conn, "config_dispositivos_impresion", "departamento_id INTEGER")
    _safe_add_column(conn, "config_dispositivos_impresion", "subdepartamento_id INTEGER")
    _safe_add_column(conn, "config_dispositivos_impresion", "punto_venta_id INTEGER")
    _safe_add_column(conn, "config_dispositivos_impresion", "equipo_mantenimiento_id INTEGER")
    _safe_add_column(conn, "config_dispositivos_impresion", "etiqueta_config_id INTEGER")
    _safe_add_column(conn, "config_dispositivos_impresion", "impresora_etiquetas_nombre TEXT")
    _safe_add_column(conn, "config_dispositivos_impresion", "impresora_documentos_nombre TEXT")
    _safe_add_column(conn, "config_dispositivos_impresion", "api_token TEXT")
    _safe_add_column(conn, "config_dispositivos_impresion", "ultimo_ping TEXT")
    _safe_add_column(conn, "config_dispositivos_impresion", "version_agente TEXT")
    _safe_add_column(conn, "config_dispositivos_impresion", "ip_ultima TEXT")
    _safe_add_column(conn, "config_dispositivos_impresion", "auto_print_etiquetas INTEGER DEFAULT 0")
    _safe_add_column(conn, "config_dispositivos_impresion", "updated_at TEXT")
    _safe_add_column(conn, "config_dispositivos_impresion", "centro_id INTEGER")
    for row in conn.execute(
        """
        SELECT id
        FROM config_dispositivos_impresion
        WHERE empresa_id = ? AND COALESCE(api_token, '') = ''
        """,
        (empresa_id,),
    ).fetchall():
        conn.execute(
            "UPDATE config_dispositivos_impresion SET api_token = ? WHERE id = ?",
            (secrets.token_urlsafe(24), row["id"]),
        )


def label_config_to_dict(row):
    if not row:
        row = DEFAULT_LABEL_CONFIGS[0]
    data = dict(row)
    data["ancho_mm"] = max(_to_float(data.get("ancho_mm"), 58), 20)
    data["alto_mm"] = max(_to_float(data.get("alto_mm"), 40), 15)
    data["columnas"] = max(_to_int(data.get("columnas"), 1), 1)
    data["margen_mm"] = max(_to_float(data.get("margen_mm"), 0), 0)
    data["separacion_mm"] = max(_to_float(data.get("separacion_mm"), 0), 0)
    data["padding_mm"] = max(_to_float(data.get("padding_mm"), 2), 0)
    data["fuente_base_px"] = max(_to_int(data.get("fuente_base_px"), 9), 7)
    data["formato"] = (data.get("formato") or "rollo").strip().lower()
    data["auto_print"] = 1 if _to_int(data.get("auto_print"), 0) else 0
    return data


def get_label_configs(conn, empresa_id=1, solo_activas=False):
    ensure_etiquetas_schema(conn, empresa_id)
    sql = "SELECT * FROM config_etiquetas WHERE empresa_id = ?"
    params = [empresa_id]
    if solo_activas:
        sql += " AND activo = 1"
    sql += " ORDER BY es_default DESC, formato, ancho_mm, alto_mm, nombre"
    return [label_config_to_dict(row) for row in conn.execute(sql, params).fetchall()]


def get_label_config(conn, empresa_id=1, codigo=None, device_uid=None):
    ensure_etiquetas_schema(conn, empresa_id)
    row = None
    if codigo:
        row = conn.execute(
            """
            SELECT * FROM config_etiquetas
            WHERE empresa_id = ? AND codigo = ? AND activo = 1
            LIMIT 1
            """,
            (empresa_id, codigo),
        ).fetchone()
    if not row and device_uid:
        row = conn.execute(
            """
            SELECT ce.*
            FROM config_dispositivos_impresion di
            JOIN config_etiquetas ce ON ce.id = di.etiqueta_config_id
            WHERE di.empresa_id = ?
              AND di.device_uid = ?
              AND COALESCE(di.activo, 1) = 1
              AND COALESCE(ce.activo, 1) = 1
            LIMIT 1
            """,
            (empresa_id, device_uid),
        ).fetchone()
    if not row:
        row = conn.execute(
            """
            SELECT * FROM config_etiquetas
            WHERE empresa_id = ? AND activo = 1 AND es_default = 1
            ORDER BY id
            LIMIT 1
            """,
            (empresa_id,),
        ).fetchone()
    if not row:
        row = conn.execute(
            """
            SELECT * FROM config_etiquetas
            WHERE empresa_id = ? AND activo = 1
            ORDER BY id
            LIMIT 1
            """,
            (empresa_id,),
        ).fetchone()
    return label_config_to_dict(row)


def print_device_to_dict(row):
    if not row:
        return None
    data = dict(row)
    data["auto_print_etiquetas"] = 1 if _to_int(data.get("auto_print_etiquetas"), 0) else 0
    data["activo"] = 1 if _to_int(data.get("activo"), 1) else 0
    data["online"] = False
    if data.get("ultimo_ping"):
        try:
            ultimo = datetime.strptime(str(data["ultimo_ping"])[:19], "%Y-%m-%d %H:%M:%S")
            data["online"] = (datetime.now() - ultimo).total_seconds() <= 150
        except Exception:
            data["online"] = False
    return data


def get_print_devices(conn, empresa_id=1):
    ensure_etiquetas_schema(conn, empresa_id)
    centro_id = centro_activo_id()
    centro_sql = " AND di.centro_id = ?" if centro_id else ""
    centro_params = [centro_id] if centro_id else []
    rows = conn.execute(
        """
        SELECT di.*, ce.nombre AS etiqueta_formato_nombre, ce.codigo AS etiqueta_formato_codigo
        FROM config_dispositivos_impresion di
        LEFT JOIN config_etiquetas ce ON ce.id = di.etiqueta_config_id
        WHERE di.empresa_id = ?
        """ + centro_sql + """
        ORDER BY COALESCE(di.activo, 1) DESC, di.nombre
        """,
        [empresa_id] + centro_params,
    ).fetchall()
    return [print_device_to_dict(row) for row in rows]


def get_current_print_device(conn, empresa_id=1, device_uid=None, create=False):
    ensure_etiquetas_schema(conn, empresa_id)
    if not device_uid:
        return None
    centro_id = centro_activo_id()
    centro_sql = " AND di.centro_id = ?" if centro_id else ""
    centro_params = [centro_id] if centro_id else []
    row = conn.execute(
        """
        SELECT di.*, ce.nombre AS etiqueta_formato_nombre, ce.codigo AS etiqueta_formato_codigo
        FROM config_dispositivos_impresion di
        LEFT JOIN config_etiquetas ce ON ce.id = di.etiqueta_config_id
        WHERE di.empresa_id = ? AND di.device_uid = ?
        """ + centro_sql + """
        LIMIT 1
        """,
        [empresa_id, device_uid] + centro_params,
    ).fetchone()
    if row or not create:
        return print_device_to_dict(row)

    cfg = get_label_config(conn, empresa_id)
    conn.execute(
        """
        INSERT OR IGNORE INTO config_dispositivos_impresion (
            empresa_id, device_uid, nombre, tipo_dispositivo, etiqueta_config_id, api_token, activo, centro_id
        )
        VALUES (?, ?, ?, 'tablet', ?, ?, 1, ?)
        """,
        (
            empresa_id,
            device_uid,
            f"Equipo {str(device_uid)[-6:].upper()}",
            cfg.get("id"),
            secrets.token_urlsafe(24),
            centro_id,
        ),
    )
    return get_current_print_device(conn, empresa_id, device_uid, create=False)


def update_print_device_from_form(conn, empresa_id, form, device_uid=None):
    ensure_etiquetas_schema(conn, empresa_id)
    device_id = _to_int(form.get("device_id"), 0)
    uid = device_uid or (form.get("device_uid") or "").strip()
    if device_id:
        where = "id = ? AND empresa_id = ?"
        where_params = (device_id, empresa_id)
    elif uid:
        where = "device_uid = ? AND empresa_id = ?"
        where_params = (uid, empresa_id)
    else:
        return False

    conn.execute(
        f"""
        UPDATE config_dispositivos_impresion
        SET nombre = ?, tipo_dispositivo = ?, ubicacion = ?,
            departamento_id = ?, subdepartamento_id = ?, punto_venta_id = ?,
            etiqueta_config_id = ?, impresora_etiquetas_nombre = ?,
            impresora_documentos_nombre = ?, auto_print_etiquetas = ?,
            activo = ?, updated_at = ?, centro_id = ?
        WHERE {where}
        """,
        (
            (form.get("nombre") or "").strip() or "Equipo sin nombre",
            (form.get("tipo_dispositivo") or "tablet").strip(),
            (form.get("ubicacion") or "").strip(),
            _to_int(form.get("departamento_id"), 0) or None,
            _to_int(form.get("subdepartamento_id"), 0) or None,
            _to_int(form.get("punto_venta_id"), 0) or None,
            _to_int(form.get("etiqueta_config_id"), 0) or None,
            (form.get("impresora_etiquetas_nombre") or "").strip(),
            (form.get("impresora_documentos_nombre") or "").strip(),
            1 if form.get("auto_print_etiquetas") else 0,
            1 if form.get("activo") else 0,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            asignar_centro_desde_departamento(conn, _to_int(form.get("departamento_id"), 0) or None),
            *where_params,
        ),
    )
    return True


def create_print_device_from_form(conn, empresa_id, form):
    ensure_etiquetas_schema(conn, empresa_id)
    nombre = (form.get("nombre") or "").strip()
    if not nombre:
        return None
    device_uid = (form.get("device_uid") or "").strip() or secrets.token_hex(16)
    api_token = secrets.token_urlsafe(24)
    cursor = conn.execute(
        """
        INSERT INTO config_dispositivos_impresion (
            empresa_id, device_uid, nombre, tipo_dispositivo, ubicacion,
            departamento_id, subdepartamento_id, punto_venta_id,
            etiqueta_config_id, impresora_etiquetas_nombre,
            impresora_documentos_nombre, api_token, auto_print_etiquetas,
            activo, created_at, updated_at, centro_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (
            empresa_id,
            device_uid,
            nombre,
            (form.get("tipo_dispositivo") or "tablet").strip(),
            (form.get("ubicacion") or "").strip(),
            _to_int(form.get("departamento_id"), 0) or None,
            _to_int(form.get("subdepartamento_id"), 0) or None,
            _to_int(form.get("punto_venta_id"), 0) or None,
            _to_int(form.get("etiqueta_config_id"), 0) or None,
            (form.get("impresora_etiquetas_nombre") or "").strip(),
            (form.get("impresora_documentos_nombre") or "").strip(),
            api_token,
            1 if form.get("auto_print_etiquetas") else 0,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            asignar_centro_desde_departamento(conn, _to_int(form.get("departamento_id"), 0) or None),
        ),
    )
    return cursor.lastrowid


def build_label_css(config):
    cfg = label_config_to_dict(config)
    page_size = "A4" if cfg["formato"] == "a4" else f"{cfg['ancho_mm']}mm {cfg['alto_mm']}mm"
    page_margin = f"{cfg['margen_mm']}mm" if cfg["formato"] == "a4" else "0mm"
    return {
        "page_size": page_size,
        "page_margin": page_margin,
        "ancho_mm": cfg["ancho_mm"],
        "alto_mm": cfg["alto_mm"],
        "columnas": cfg["columnas"],
        "margen_mm": cfg["margen_mm"],
        "separacion_mm": cfg["separacion_mm"],
        "padding_mm": cfg["padding_mm"],
        "fuente_base_px": cfg["fuente_base_px"],
        "modo_rollo": cfg["formato"] != "a4",
    }


def update_label_config_from_form(conn, empresa_id, form):
    config_id = _to_int(form.get("config_id"), 0)
    if not config_id:
        return False
    es_default = 1 if form.get("es_default") else 0
    activo = 1 if form.get("activo") else 0
    if es_default:
        conn.execute(
            "UPDATE config_etiquetas SET es_default = 0 WHERE empresa_id = ?",
            (empresa_id,),
        )
    conn.execute(
        """
        UPDATE config_etiquetas
        SET nombre = ?, tipo_impresora = ?, impresora_nombre = ?, formato = ?,
            ancho_mm = ?, alto_mm = ?, columnas = ?, margen_mm = ?,
            separacion_mm = ?, padding_mm = ?, fuente_base_px = ?,
            auto_print = ?, activo = ?, es_default = ?, descripcion = ?, updated_at = ?
        WHERE id = ? AND empresa_id = ?
        """,
        (
            (form.get("nombre") or "").strip() or "Formato etiqueta",
            (form.get("tipo_impresora") or "termica").strip(),
            (form.get("impresora_nombre") or "").strip(),
            (form.get("formato") or "rollo").strip(),
            _to_float(form.get("ancho_mm"), 58),
            _to_float(form.get("alto_mm"), 40),
            max(_to_int(form.get("columnas"), 1), 1),
            _to_float(form.get("margen_mm"), 0),
            _to_float(form.get("separacion_mm"), 0),
            _to_float(form.get("padding_mm"), 2),
            max(_to_int(form.get("fuente_base_px"), 9), 7),
            1 if form.get("auto_print") else 0,
            activo,
            es_default,
            (form.get("descripcion") or "").strip(),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            config_id,
            empresa_id,
        ),
    )
    return True


def create_label_config_from_form(conn, empresa_id, form):
    codigo = (form.get("codigo") or "").strip().lower().replace(" ", "_")
    nombre = (form.get("nombre") or "").strip()
    if not codigo or not nombre:
        return False
    conn.execute(
        """
        INSERT INTO config_etiquetas (
            empresa_id, codigo, nombre, tipo_impresora, impresora_nombre, formato,
            ancho_mm, alto_mm, columnas, margen_mm, separacion_mm, padding_mm,
            fuente_base_px, auto_print, activo, es_default, descripcion
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?)
        """,
        (
            empresa_id,
            codigo,
            nombre,
            (form.get("tipo_impresora") or "termica").strip(),
            (form.get("impresora_nombre") or "").strip(),
            (form.get("formato") or "rollo").strip(),
            _to_float(form.get("ancho_mm"), 58),
            _to_float(form.get("alto_mm"), 40),
            max(_to_int(form.get("columnas"), 1), 1),
            _to_float(form.get("margen_mm"), 0),
            _to_float(form.get("separacion_mm"), 0),
            _to_float(form.get("padding_mm"), 2),
            max(_to_int(form.get("fuente_base_px"), 9), 7),
            1 if form.get("auto_print") else 0,
            (form.get("descripcion") or "").strip(),
        ),
    )
    return True

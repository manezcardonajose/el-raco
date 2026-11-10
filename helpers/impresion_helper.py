from datetime import datetime

from helpers.etiquetas_helper import ensure_etiquetas_schema, get_current_print_device, get_label_config
from helpers.centros_helper import centro_activo_id


ESTADOS_COLA = ("pendiente", "procesando", "impreso", "error", "cancelado")


def _columnas_tabla(conn, tabla):
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({tabla})").fetchall()]
    except Exception:
        return []


def _safe_add_column(conn, tabla, definicion):
    nombre = definicion.strip().split()[0]
    if nombre not in _columnas_tabla(conn, tabla):
        conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {definicion}")


def ensure_print_queue_schema(conn, empresa_id=1):
    ensure_etiquetas_schema(conn, empresa_id)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cola_impresion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            device_uid TEXT,
            tipo TEXT DEFAULT 'etiqueta',
            origen_tabla TEXT,
            origen_id INTEGER,
            titulo TEXT,
            formato_codigo TEXT,
            impresora_nombre TEXT,
            lenguaje_impresion TEXT DEFAULT 'html',
            contenido_html TEXT,
            contenido_raw TEXT,
            contenido_texto TEXT,
            estado TEXT DEFAULT 'pendiente',
            intentos INTEGER DEFAULT 0,
            error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            processed_at TEXT
        )
        """
    )
    _safe_add_column(conn, "cola_impresion", "device_uid TEXT")
    _safe_add_column(conn, "cola_impresion", "formato_codigo TEXT")
    _safe_add_column(conn, "cola_impresion", "impresora_nombre TEXT")
    _safe_add_column(conn, "cola_impresion", "lenguaje_impresion TEXT DEFAULT 'html'")
    _safe_add_column(conn, "cola_impresion", "contenido_html TEXT")
    _safe_add_column(conn, "cola_impresion", "contenido_raw TEXT")
    _safe_add_column(conn, "cola_impresion", "contenido_texto TEXT")
    _safe_add_column(conn, "cola_impresion", "intentos INTEGER DEFAULT 0")
    _safe_add_column(conn, "cola_impresion", "error TEXT")
    _safe_add_column(conn, "cola_impresion", "updated_at TEXT")
    _safe_add_column(conn, "cola_impresion", "processed_at TEXT")
    _safe_add_column(conn, "cola_impresion", "centro_id INTEGER")


def create_print_job(
    conn,
    empresa_id,
    device_uid,
    tipo,
    origen_tabla,
    origen_id,
    titulo,
    contenido_html,
    contenido_raw=None,
    contenido_texto=None,
    formato_codigo=None,
    lenguaje_impresion=None,
):
    ensure_print_queue_schema(conn, empresa_id)
    dispositivo = get_current_print_device(conn, empresa_id, device_uid, create=True)
    config = get_label_config(conn, empresa_id, formato_codigo, device_uid)
    impresora_nombre = ""
    if dispositivo:
        impresora_nombre = dispositivo.get("impresora_etiquetas_nombre") or ""
    centro_id = (dispositivo or {}).get("centro_id") or centro_activo_id()
    if not impresora_nombre:
        impresora_nombre = config.get("impresora_nombre") or ""
    cursor = conn.execute(
        """
        INSERT INTO cola_impresion (
            empresa_id, device_uid, tipo, origen_tabla, origen_id, titulo,
            formato_codigo, impresora_nombre, lenguaje_impresion,
            contenido_html, contenido_raw, contenido_texto,
            estado, intentos, created_at, updated_at, centro_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', 0, ?, ?, ?)
        """,
        (
            empresa_id,
            device_uid,
            tipo or "etiqueta",
            origen_tabla or "",
            origen_id,
            titulo or "Trabajo de impresion",
            config.get("codigo") or formato_codigo or "",
            impresora_nombre,
            lenguaje_impresion or ("zpl" if contenido_raw else "html"),
            contenido_html or "",
            contenido_raw or "",
            contenido_texto or "",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            centro_id,
        ),
    )
    return cursor.lastrowid


def list_print_jobs(conn, empresa_id=1, estado=None, limit=80):
    ensure_print_queue_schema(conn, empresa_id)
    centro_id = centro_activo_id()
    sql = "SELECT * FROM cola_impresion WHERE empresa_id = ?"
    params = [empresa_id]
    if centro_id:
        sql += " AND centro_id = ?"
        params.append(centro_id)
    if estado:
        sql += " AND estado = ?"
        params.append(estado)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit or 80))
    return conn.execute(sql, params).fetchall()


def api_get_device(conn, empresa_id, device_uid, token):
    ensure_print_queue_schema(conn, empresa_id)
    if not device_uid or not token:
        return None
    return conn.execute(
        """
        SELECT *
        FROM config_dispositivos_impresion
        WHERE empresa_id = ?
          AND device_uid = ?
          AND api_token = ?
          AND COALESCE(activo, 1) = 1
        LIMIT 1
        """,
        (empresa_id, device_uid, token),
    ).fetchone()


def api_touch_device(conn, empresa_id, device_uid, token, version_agente="", ip_ultima=""):
    device = api_get_device(conn, empresa_id, device_uid, token)
    if not device:
        return None
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        UPDATE config_dispositivos_impresion
        SET ultimo_ping = ?,
            version_agente = COALESCE(NULLIF(?, ''), version_agente),
            ip_ultima = COALESCE(NULLIF(?, ''), ip_ultima),
            updated_at = ?
        WHERE id = ? AND empresa_id = ?
        """,
        (now, version_agente or "", ip_ultima or "", now, device["id"], empresa_id),
    )
    return device


def api_pending_jobs(conn, empresa_id, device_uid, limit=5):
    ensure_print_queue_schema(conn, empresa_id)
    return conn.execute(
        """
        SELECT id, tipo, titulo, formato_codigo, impresora_nombre, lenguaje_impresion, created_at, intentos
        FROM cola_impresion
        WHERE empresa_id = ?
          AND device_uid = ?
          AND estado IN ('pendiente', 'error')
          AND COALESCE(intentos, 0) < 3
        ORDER BY id
        LIMIT ?
        """,
        (empresa_id, device_uid, int(limit or 5)),
    ).fetchall()


def get_job_for_device(conn, empresa_id, device_uid, job_id):
    ensure_print_queue_schema(conn, empresa_id)
    return conn.execute(
        """
        SELECT *
        FROM cola_impresion
        WHERE id = ? AND empresa_id = ? AND device_uid = ?
        LIMIT 1
        """,
        (job_id, empresa_id, device_uid),
    ).fetchone()


def update_job_status(conn, empresa_id, device_uid, job_id, estado, error=""):
    ensure_print_queue_schema(conn, empresa_id)
    estado = estado if estado in ESTADOS_COLA else "error"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    processed_at = now if estado in {"impreso", "cancelado"} else None
    conn.execute(
        """
        UPDATE cola_impresion
        SET estado = ?,
            error = ?,
            intentos = COALESCE(intentos, 0) + CASE WHEN ? IN ('procesando', 'error') THEN 1 ELSE 0 END,
            updated_at = ?,
            processed_at = COALESCE(?, processed_at)
        WHERE id = ? AND empresa_id = ? AND device_uid = ?
        """,
        (estado, error or "", estado, now, processed_at, job_id, empresa_id, device_uid),
    )

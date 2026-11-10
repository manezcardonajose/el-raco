from datetime import date, datetime, timedelta
import hashlib

from flask import flash, redirect, render_template, request, session, url_for

from database import get_db
from blueprints.auth.routes import login_required, usuario_es_global_operacion, usuario_tiene_permiso
from helpers.operativa_helper import ensure_incidencias_context_schema
from . import experiencia_bp


CANALES_QUEJA = [
    ("recepcion", "Recepcion"),
    ("email", "Email"),
    ("telefono", "Telefono"),
    ("web", "Web"),
    ("agencia", "Agencia / OTA"),
    ("rrpp", "RRPP"),
    ("restaurante", "Restaurante / bar"),
    ("pisos", "Pisos"),
    ("otro", "Otro"),
]

TIPOS_QUEJA = [
    ("queja", "Queja"),
    ("sugerencia", "Sugerencia"),
    ("felicitacion", "Felicitacion"),
    ("incidencia_cliente", "Incidencia cliente"),
]

CATEGORIAS_QUEJA = [
    ("habitacion", "Habitacion"),
    ("limpieza", "Limpieza"),
    ("mantenimiento", "Mantenimiento"),
    ("restauracion", "Restauracion"),
    ("alergias", "Alergias / salud"),
    ("servicio", "Servicio"),
    ("animacion", "Animacion"),
    ("ruido", "Ruido"),
    ("facturacion", "Facturacion"),
    ("reserva", "Reserva"),
    ("accesibilidad", "Accesibilidad"),
    ("otro", "Otro"),
]

PUBLIC_MAX_HOUR = 5
PUBLIC_MAX_DAY = 15

GRAVEDADES = [
    ("baja", "Baja"),
    ("media", "Media"),
    ("alta", "Alta"),
    ("critica", "Critica"),
]

ESTADOS = [
    ("abierta", "Abierta"),
    ("investigacion", "Investigacion"),
    ("accion", "Accion correctiva"),
    ("seguimiento", "Seguimiento cliente"),
    ("cerrada", "Cerrada"),
    ("cancelada", "Cancelada"),
]

ESTADOS_ABIERTOS = ("abierta", "investigacion", "accion", "seguimiento")
PRIORIDAD_ORDEN = {"critica": 0, "alta": 1, "media": 2, "baja": 3}


def _empresa_id():
    try:
        return int(session.get("user_empresa_id") or session.get("empresa_id") or 1)
    except (TypeError, ValueError):
        return 1


def _table_exists(conn, table_name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(conn, table_name, column_name):
    if not _table_exists(conn, table_name):
        return False
    try:
        cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:
        return False
    for col in cols:
        try:
            name = col["name"]
        except Exception:
            name = col[1]
        if name == column_name:
            return True
    return False


def _add_column_if_missing(conn, table_name, column_sql):
    column_name = column_sql.strip().split()[0]
    if _table_exists(conn, table_name) and not _column_exists(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def _safe_int(value, default=None):
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _row_value(row, key, default=None):
    try:
        return row[key]
    except Exception:
        return default


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


def _public_request_fingerprint() -> tuple[str, str]:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    user_agent = (request.headers.get("User-Agent") or "")[:240]
    raw = f"{ip}|{user_agent}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest(), user_agent


def _public_submission_allowed(conn, empresa_id: int, fingerprint: str) -> tuple[bool, str]:
    hour_count = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM experiencia_public_rate
        WHERE empresa_id = ? AND fingerprint = ? AND created_at >= datetime('now', '-1 hour')
        """,
        (empresa_id, fingerprint),
    ).fetchone()["total"]
    if int(hour_count or 0) >= PUBLIC_MAX_HOUR:
        return False, "Demasiados mensajes en poco tiempo. Espere un rato antes de volver a enviar."
    day_count = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM experiencia_public_rate
        WHERE empresa_id = ? AND fingerprint = ? AND created_at >= datetime('now', '-1 day')
        """,
        (empresa_id, fingerprint),
    ).fetchone()["total"]
    if int(day_count or 0) >= PUBLIC_MAX_DAY:
        return False, "Se ha alcanzado el limite diario de mensajes desde este dispositivo."
    return True, ""


def _nombre_usuario():
    return session.get("user_nombre") or session.get("username") or session.get("usuario") or "usuario"


def _empresa_publica(conn, empresa_id):
    if not _table_exists(conn, "empresas"):
        return {"id": empresa_id, "nombre": "El Raco", "telefono": "", "email": "", "logo": ""}
    row = conn.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
    if not row:
        return {"id": empresa_id, "nombre": "El Raco", "telefono": "", "email": "", "logo": ""}
    return {
        "id": empresa_id,
        "nombre": _row_value(row, "nombre", "El Raco") or _row_value(row, "razon_social", "El Raco") or "El Raco",
        "telefono": _row_value(row, "telefono", "") or "",
        "email": _row_value(row, "email", "") or "",
        "logo": _row_value(row, "logo_path", "") or _row_value(row, "logo", "") or "",
    }


def _puede_ver():
    return (
        usuario_es_global_operacion()
        or usuario_tiene_permiso("experiencia_ver")
        or usuario_tiene_permiso("experiencia_gestionar")
        or usuario_tiene_permiso("recepcion_gestionar")
        or usuario_tiene_permiso("informes_ver")
    )


def _puede_gestionar():
    return (
        usuario_es_global_operacion()
        or usuario_tiene_permiso("experiencia_gestionar")
        or usuario_tiene_permiso("recepcion_gestionar")
    )


def _ensure_experiencia_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS clientes_360 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            cliente_key TEXT,
            nombre TEXT,
            apellidos TEXT,
            documento TEXT,
            email TEXT,
            telefono TEXT,
            nacionalidad TEXT,
            idioma TEXT,
            vip_nivel TEXT,
            estado TEXT DEFAULT 'activo',
            preferencias TEXT,
            alergias_resumen TEXT,
            observaciones TEXT,
            consentimiento_marketing INTEGER DEFAULT 0,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(empresa_id, cliente_key)
        )
        """
    )
    for col in [
        "empresa_id INTEGER DEFAULT 1", "cliente_key TEXT", "nombre TEXT", "apellidos TEXT",
        "documento TEXT", "email TEXT", "telefono TEXT", "nacionalidad TEXT", "idioma TEXT",
        "vip_nivel TEXT", "estado TEXT DEFAULT 'activo'", "preferencias TEXT",
        "alergias_resumen TEXT", "observaciones TEXT", "consentimiento_marketing INTEGER DEFAULT 0",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP", "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "clientes_360", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS experiencia_quejas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            fecha TEXT DEFAULT CURRENT_TIMESTAMP,
            canal TEXT DEFAULT 'recepcion',
            origen_modulo TEXT DEFAULT 'experiencia',
            reserva_id INTEGER,
            viajero_id INTEGER,
            cliente_id INTEGER,
            titular_nombre TEXT,
            habitacion TEXT,
            nacionalidad TEXT,
            idioma TEXT,
            tipo TEXT DEFAULT 'queja',
            categoria TEXT DEFAULT 'otro',
            subcategoria TEXT,
            descripcion TEXT NOT NULL,
            gravedad TEXT DEFAULT 'media',
            impacto TEXT,
            estado TEXT DEFAULT 'abierta',
            responsable TEXT,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            requiere_compensacion INTEGER DEFAULT 0,
            compensacion_tipo TEXT,
            compensacion_importe REAL DEFAULT 0,
            requiere_mantenimiento INTEGER DEFAULT 0,
            requiere_appcc INTEGER DEFAULT 0,
            incidencia_id INTEGER,
            orden_trabajo_id INTEGER,
            causa_raiz TEXT,
            comprobaciones_resumen TEXT,
            pruebas TEXT,
            acciones_inmediatas TEXT,
            acciones_correctivas TEXT,
            seguimiento_cliente TEXT,
            resultado_cliente TEXT,
            aprendizaje TEXT,
            fecha_limite TEXT,
            fecha_cierre TEXT,
            creado_por TEXT,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "empresa_id INTEGER DEFAULT 1", "fecha TEXT DEFAULT CURRENT_TIMESTAMP", "canal TEXT DEFAULT 'recepcion'",
        "origen_modulo TEXT DEFAULT 'experiencia'", "reserva_id INTEGER", "viajero_id INTEGER",
        "cliente_id INTEGER", "titular_nombre TEXT", "habitacion TEXT", "nacionalidad TEXT", "idioma TEXT",
        "tipo TEXT DEFAULT 'queja'", "categoria TEXT DEFAULT 'otro'", "subcategoria TEXT",
        "descripcion TEXT", "gravedad TEXT DEFAULT 'media'", "impacto TEXT", "estado TEXT DEFAULT 'abierta'",
        "responsable TEXT", "departamento_id INTEGER", "subdepartamento_id INTEGER", "punto_venta_id INTEGER",
        "requiere_compensacion INTEGER DEFAULT 0", "compensacion_tipo TEXT",
        "compensacion_importe REAL DEFAULT 0", "requiere_mantenimiento INTEGER DEFAULT 0",
        "requiere_appcc INTEGER DEFAULT 0", "incidencia_id INTEGER", "orden_trabajo_id INTEGER",
        "causa_raiz TEXT", "comprobaciones_resumen TEXT", "pruebas TEXT",
        "acciones_inmediatas TEXT", "acciones_correctivas TEXT", "seguimiento_cliente TEXT",
        "resultado_cliente TEXT", "aprendizaje TEXT", "fecha_limite TEXT", "fecha_cierre TEXT",
        "creado_por TEXT", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP", "updated_at TEXT",
        "public_fingerprint TEXT", "public_user_agent TEXT",
    ]:
        _add_column_if_missing(conn, "experiencia_quejas", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS experiencia_public_rate (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            fingerprint TEXT NOT NULL,
            ruta TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_experiencia_public_rate ON experiencia_public_rate(empresa_id, fingerprint, created_at)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS experiencia_comprobaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queja_id INTEGER NOT NULL,
            empresa_id INTEGER DEFAULT 1,
            fecha TEXT DEFAULT CURRENT_TIMESTAMP,
            area TEXT,
            comprobacion TEXT NOT NULL,
            resultado TEXT,
            evidencia TEXT,
            usuario TEXT,
            FOREIGN KEY (queja_id) REFERENCES experiencia_quejas(id) ON DELETE CASCADE
        )
        """
    )
    for col in [
        "queja_id INTEGER", "empresa_id INTEGER DEFAULT 1", "fecha TEXT DEFAULT CURRENT_TIMESTAMP",
        "area TEXT", "comprobacion TEXT", "resultado TEXT", "evidencia TEXT", "usuario TEXT",
    ]:
        _add_column_if_missing(conn, "experiencia_comprobaciones", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS experiencia_acciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queja_id INTEGER NOT NULL,
            empresa_id INTEGER DEFAULT 1,
            tipo TEXT DEFAULT 'correctiva',
            descripcion TEXT NOT NULL,
            responsable TEXT,
            coste REAL DEFAULT 0,
            fecha_limite TEXT,
            estado TEXT DEFAULT 'pendiente',
            resultado TEXT,
            fecha_cierre TEXT,
            usuario TEXT,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (queja_id) REFERENCES experiencia_quejas(id) ON DELETE CASCADE
        )
        """
    )
    for col in [
        "queja_id INTEGER", "empresa_id INTEGER DEFAULT 1", "tipo TEXT DEFAULT 'correctiva'",
        "descripcion TEXT", "responsable TEXT", "coste REAL DEFAULT 0", "fecha_limite TEXT",
        "estado TEXT DEFAULT 'pendiente'", "resultado TEXT", "fecha_cierre TEXT", "usuario TEXT",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "experiencia_acciones", col)


def _upsert_cliente_360(conn, empresa_id, datos):
    documento = (datos.get("documento") or "").strip()
    email = (datos.get("email") or "").strip().lower()
    telefono = (datos.get("telefono") or "").strip()
    nombre = (datos.get("nombre") or "").strip()
    apellidos = (datos.get("apellidos") or "").strip()
    cliente_key = documento or email or telefono or f"{nombre.lower()}|{apellidos.lower()}"[:120]
    if not cliente_key.strip("|"):
        return None

    row = conn.execute(
        "SELECT id FROM clientes_360 WHERE empresa_id = ? AND cliente_key = ?",
        (empresa_id, cliente_key),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE clientes_360
            SET nombre = COALESCE(NULLIF(?, ''), nombre),
                apellidos = COALESCE(NULLIF(?, ''), apellidos),
                documento = COALESCE(NULLIF(?, ''), documento),
                email = COALESCE(NULLIF(?, ''), email),
                telefono = COALESCE(NULLIF(?, ''), telefono),
                nacionalidad = COALESCE(NULLIF(?, ''), nacionalidad),
                idioma = COALESCE(NULLIF(?, ''), idioma),
                alergias_resumen = COALESCE(NULLIF(?, ''), alergias_resumen),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND empresa_id = ?
            """,
            (
                nombre,
                apellidos,
                documento,
                email,
                telefono,
                datos.get("nacionalidad") or "",
                datos.get("idioma") or "",
                datos.get("alergias_resumen") or "",
                row["id"],
                empresa_id,
            ),
        )
        return row["id"]

    cur = conn.execute(
        """
        INSERT INTO clientes_360 (
            empresa_id, cliente_key, nombre, apellidos, documento, email, telefono,
            nacionalidad, idioma, alergias_resumen, fecha_creacion
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            empresa_id,
            cliente_key,
            nombre,
            apellidos,
            documento,
            email,
            telefono,
            datos.get("nacionalidad") or "",
            datos.get("idioma") or "",
            datos.get("alergias_resumen") or "",
        ),
    )
    return cur.lastrowid


def _habitacion_reserva(conn, reserva_id, empresa_id):
    if not reserva_id or not (_table_exists(conn, "reservas_recepcion") and _table_exists(conn, "habitaciones_recepcion")):
        return ""
    row = conn.execute(
        """
        SELECT COALESCE(h.numero, h.nombre, CAST(r.habitacion_id AS TEXT), '') AS habitacion
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON h.id = r.habitacion_id AND h.empresa_id = r.empresa_id
        WHERE r.id = ? AND r.empresa_id = ?
        """,
        (reserva_id, empresa_id),
    ).fetchone()
    return _row_value(row, "habitacion", "") if row else ""


def _contexto_reserva_viajero(conn, reserva_id, viajero_id, empresa_id):
    datos = {}
    if reserva_id and _table_exists(conn, "reservas_recepcion"):
        reserva = conn.execute(
            "SELECT * FROM reservas_recepcion WHERE id = ? AND empresa_id = ?",
            (reserva_id, empresa_id),
        ).fetchone()
        if reserva:
            datos.update(
                {
                    "reserva_id": reserva["id"],
                    "titular_nombre": reserva["titular_nombre"] or "",
                    "email": reserva["titular_email"] or "",
                    "telefono": reserva["titular_telefono"] or "",
                    "alergias_resumen": reserva["alergias_resumen"] or "",
                    "habitacion": _habitacion_reserva(conn, reserva["id"], empresa_id),
                }
            )
    if viajero_id and _table_exists(conn, "viajeros_recepcion"):
        if _table_exists(conn, "reservas_recepcion"):
            viajero = conn.execute(
                """
                SELECT v.*, r.titular_nombre, r.titular_email, r.titular_telefono, r.id AS reserva_id_real
                FROM viajeros_recepcion v
                LEFT JOIN reservas_recepcion r ON r.id = v.reserva_id AND r.empresa_id = v.empresa_id
                WHERE v.id = ? AND v.empresa_id = ?
                """,
                (viajero_id, empresa_id),
            ).fetchone()
        else:
            viajero = conn.execute(
                "SELECT v.*, NULL AS titular_nombre, NULL AS titular_email, NULL AS titular_telefono, v.reserva_id AS reserva_id_real FROM viajeros_recepcion v WHERE v.id = ? AND v.empresa_id = ?",
                (viajero_id, empresa_id),
            ).fetchone()
        if viajero:
            datos.update(
                {
                    "viajero_id": viajero["id"],
                    "reserva_id": viajero["reserva_id_real"] or viajero["reserva_id"],
                    "nombre": viajero["nombre"] or "",
                    "apellidos": viajero["apellidos"] or "",
                    "titular_nombre": f"{viajero['nombre'] or ''} {viajero['apellidos'] or ''}".strip()
                    or viajero["titular_nombre"]
                    or "",
                    "documento": viajero["numero_documento"] or "",
                    "email": viajero["email"] or viajero["titular_email"] or "",
                    "telefono": viajero["telefono"] or viajero["titular_telefono"] or "",
                    "nacionalidad": viajero["nacionalidad"] or "",
                    "idioma": viajero["pais"] or "",
                    "alergias_resumen": viajero["alergias_intolerancias"] or datos.get("alergias_resumen", ""),
                    "habitacion": datos.get("habitacion") or _habitacion_reserva(conn, viajero["reserva_id"], empresa_id),
                }
            )
    return datos


def _reservas_recientes(conn, empresa_id):
    if not _table_exists(conn, "reservas_recepcion"):
        return []
    if not _table_exists(conn, "habitaciones_recepcion"):
        return conn.execute(
            """
            SELECT r.id, r.codigo, r.titular_nombre, r.fecha_entrada, r.fecha_salida,
                   CAST(r.habitacion_id AS TEXT) AS habitacion
            FROM reservas_recepcion r
            WHERE r.empresa_id = ?
            ORDER BY COALESCE(r.fecha_entrada, r.fecha_creacion) DESC, r.id DESC
            LIMIT 80
            """,
            (empresa_id,),
        ).fetchall()
    return conn.execute(
        """
        SELECT r.id, r.codigo, r.titular_nombre, r.fecha_entrada, r.fecha_salida,
               COALESCE(h.numero, h.nombre, CAST(r.habitacion_id AS TEXT), '') AS habitacion
        FROM reservas_recepcion r
        LEFT JOIN habitaciones_recepcion h ON h.id = r.habitacion_id AND h.empresa_id = r.empresa_id
        WHERE r.empresa_id = ?
        ORDER BY COALESCE(r.fecha_entrada, r.fecha_creacion) DESC, r.id DESC
        LIMIT 80
        """,
        (empresa_id,),
    ).fetchall()


def _viajeros_recientes(conn, empresa_id):
    if not _table_exists(conn, "viajeros_recepcion"):
        return []
    if not _table_exists(conn, "reservas_recepcion"):
        return conn.execute(
            """
            SELECT v.id, v.reserva_id, v.nombre, v.apellidos, v.numero_documento, v.nacionalidad,
                   NULL AS reserva_codigo, NULL AS fecha_entrada, NULL AS fecha_salida
            FROM viajeros_recepcion v
            WHERE v.empresa_id = ?
            ORDER BY v.fecha_creacion DESC, v.id DESC
            LIMIT 100
            """,
            (empresa_id,),
        ).fetchall()
    return conn.execute(
        """
        SELECT v.id, v.reserva_id, v.nombre, v.apellidos, v.numero_documento, v.nacionalidad,
               r.codigo AS reserva_codigo, r.fecha_entrada, r.fecha_salida
        FROM viajeros_recepcion v
        LEFT JOIN reservas_recepcion r ON r.id = v.reserva_id AND r.empresa_id = v.empresa_id
        WHERE v.empresa_id = ?
        ORDER BY COALESCE(v.fecha_creacion, r.fecha_entrada) DESC, v.id DESC
        LIMIT 100
        """,
        (empresa_id,),
    ).fetchall()


def _analisis_investigador(conn, queja, comprobaciones, acciones):
    hoy = date.today()
    analisis = []
    categoria = (queja["categoria"] or "").lower()
    gravedad = (queja["gravedad"] or "media").lower()
    estado = (queja["estado"] or "abierta").lower()
    fecha_limite = _parse_date(queja["fecha_limite"])

    if gravedad in ("alta", "critica") and estado in ESTADOS_ABIERTOS:
        analisis.append(
            {
                "tipo": "riesgo",
                "titulo": "Queja sensible abierta",
                "detalle": "Conviene dejar pruebas, responsable y llamada de seguimiento registrada antes de cerrar.",
            }
        )
    if fecha_limite and fecha_limite < hoy and estado in ESTADOS_ABIERTOS:
        analisis.append(
            {
                "tipo": "riesgo",
                "titulo": "Plazo vencido",
                "detalle": f"La fecha limite era {fecha_limite.strftime('%d/%m/%Y')}. Priorizar respuesta al cliente.",
            }
        )
    if not comprobaciones:
        analisis.append(
            {
                "tipo": "comprobacion",
                "titulo": "Faltan comprobaciones",
                "detalle": "Antes de dar una solucion, registra que se reviso, quien lo vio y que evidencia existe.",
            }
        )
    if categoria in ("mantenimiento", "habitacion") and not queja["incidencia_id"]:
        analisis.append(
            {
                "tipo": "escalado",
                "titulo": "Puede necesitar parte operativo",
                "detalle": "Si hay averia real, escala a incidencias/mantenimiento para que quede trazabilidad tecnica.",
            }
        )
    if categoria in ("alergias", "restauracion") and not queja["comprobaciones_resumen"]:
        analisis.append(
            {
                "tipo": "seguridad",
                "titulo": "Revisar cocina y servicio",
                "detalle": "Cruza reserva, avisos, receta, servicio y comunicacion al departamento antes de cerrar.",
            }
        )
    if queja["requiere_compensacion"] and not acciones:
        analisis.append(
            {
                "tipo": "accion",
                "titulo": "Compensacion sin accion registrada",
                "detalle": "Si se compensa, debe quedar quien lo autoriza, importe o detalle y motivo.",
            }
        )

    repetidas_cliente = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM experiencia_quejas
        WHERE empresa_id = ?
          AND id <> ?
          AND COALESCE(titular_nombre, '') <> ''
          AND lower(titular_nombre) = lower(?)
        """,
        (queja["empresa_id"], queja["id"], queja["titular_nombre"] or ""),
    ).fetchone()["total"]
    if repetidas_cliente:
        analisis.append(
            {
                "tipo": "cliente",
                "titulo": "Cliente con historial",
                "detalle": f"Hay {repetidas_cliente} registro(s) anteriores con este nombre. Revisar ficha 360 antes de responder.",
            }
        )

    repetidas_habitacion = 0
    if queja["habitacion"]:
        repetidas_habitacion = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM experiencia_quejas
            WHERE empresa_id = ?
              AND id <> ?
              AND COALESCE(habitacion, '') <> ''
              AND lower(habitacion) = lower(?)
              AND fecha >= date('now', '-90 day')
            """,
            (queja["empresa_id"], queja["id"], queja["habitacion"]),
        ).fetchone()["total"]
    if repetidas_habitacion:
        analisis.append(
            {
                "tipo": "reincidencia",
                "titulo": "Reincidencia por habitacion",
                "detalle": f"La habitacion tiene {repetidas_habitacion} queja(s) en los ultimos 90 dias. Buscar causa raiz.",
            }
        )

    if not analisis:
        analisis.append(
            {
                "tipo": "ok",
                "titulo": "Expediente limpio",
                "detalle": "No veo bloqueos graves: completar comprobaciones, cerrar acciones y registrar aprendizaje.",
            }
        )
    return analisis


def _sugerencias_comprobacion(categoria):
    base = [
        "Confirmar relato con el cliente y anotar hora/persona que atiende.",
        "Revisar si hay reservas, avisos o preferencias previas relacionadas.",
        "Registrar evidencia disponible: foto, ticket, parte, factura o testigo.",
    ]
    mapa = {
        "habitacion": [
            "Comprobar habitacion in situ con pisos y/o mantenimiento.",
            "Revisar historial de partes de la habitacion.",
        ],
        "limpieza": [
            "Revisar parte de limpieza, hora de cierre y responsable.",
            "Verificar si se ofrecio repaso o cambio de habitacion.",
        ],
        "mantenimiento": [
            "Crear parte si existe averia real.",
            "Registrar equipo, zona y prioridad tecnica.",
        ],
        "restauracion": [
            "Cruzar servicio, TPV/comanda, reserva y responsable de sala.",
            "Comprobar si cocina recibio avisos de alergias o intolerancias.",
        ],
        "alergias": [
            "Revisar ficha del viajero, avisos a cocina y tarjeta de buffet.",
            "Registrar producto/receta implicada y posible contaminacion cruzada.",
        ],
        "facturacion": [
            "Revisar factura, pagos, caja, extras y politica de credito.",
            "Anotar correccion economica y autorizacion.",
        ],
    }
    return base + mapa.get((categoria or "").lower(), [])


def _crear_incidencia_desde_queja(conn, queja, destino):
    if not _table_exists(conn, "incidencias"):
        return None
    ensure_incidencias_context_schema(conn)
    descripcion = (
        f"Queja cliente #{queja['id']} - {queja['categoria']}\n"
        f"Cliente: {queja['titular_nombre'] or '-'}\n"
        f"Habitacion: {queja['habitacion'] or '-'}\n"
        f"Detalle: {queja['descripcion']}"
    )
    requiere_mantenimiento = 1 if destino == "mantenimiento" else 0
    requiere_appcc = 1 if destino == "appcc" else 0
    cur = conn.execute(
        """
        INSERT INTO incidencias (
            tipo, referencia_id, usuario, descripcion, gravedad, estado, observaciones,
            origen_modulo, destino_modulo, categoria_operativa, ubicacion,
            departamento_id, subdepartamento_id, punto_venta_id, requiere_mantenimiento,
            requiere_appcc, origen_tabla, origen_id, empresa_id
        )
        VALUES (?, ?, ?, ?, ?, 'abierta', ?, 'experiencia', ?, ?, ?, ?, ?, ?, ?, ?, 'experiencia_quejas', ?, ?)
        """,
        (
            "queja_cliente",
            queja["id"],
            _nombre_usuario(),
            descripcion,
            queja["gravedad"] or "media",
            "Escalada desde Experiencia Cliente / RRPP.",
            destino,
            queja["categoria"] or "otro",
            queja["habitacion"] or "",
            queja["departamento_id"],
            queja["subdepartamento_id"],
            queja["punto_venta_id"],
            requiere_mantenimiento,
            requiere_appcc,
            queja["id"],
            queja["empresa_id"],
        ),
    )
    incidencia_id = cur.lastrowid
    conn.execute(
        """
        UPDATE experiencia_quejas
        SET incidencia_id = ?, requiere_mantenimiento = ?, requiere_appcc = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (incidencia_id, requiere_mantenimiento, requiere_appcc, queja["id"], queja["empresa_id"]),
    )
    return incidencia_id


@experiencia_bp.route("/publico")
def publico_cliente():
    conn = get_db()
    _ensure_experiencia_schema(conn)
    conn.commit()
    empresa_id = _safe_int(request.args.get("empresa_id"), 1) or 1
    empresa = _empresa_publica(conn, empresa_id)
    conn.close()
    return render_template(
        "experiencia/publico_cliente.html",
        empresa=empresa,
        empresa_id=empresa_id,
    )


@experiencia_bp.route("/publico/quejas", methods=["GET", "POST"])
def publico_queja():
    conn = get_db()
    _ensure_experiencia_schema(conn)
    conn.commit()
    empresa_id = _safe_int(request.values.get("empresa_id"), 1) or 1
    empresa = _empresa_publica(conn, empresa_id)
    if request.method == "POST":
        fingerprint, user_agent = _public_request_fingerprint()
        if (request.form.get("web") or "").strip():
            conn.close()
            return render_template("experiencia/publico_gracias.html", empresa=empresa)
        permitido, razon_bloqueo = _public_submission_allowed(conn, empresa_id, fingerprint)
        if not permitido:
            flash(razon_bloqueo, "warning")
            conn.close()
            return redirect(url_for("experiencia.publico_queja", empresa_id=empresa_id))
        descripcion = (request.form.get("descripcion") or "").strip()
        acepta = request.form.get("acepta_privacidad")
        if not descripcion:
            flash("Cuente brevemente que necesita para que podamos ayudarle.", "warning")
            conn.close()
            return redirect(url_for("experiencia.publico_queja", empresa_id=empresa_id))
        if len(descripcion) > 3000:
            flash("El mensaje es demasiado largo. Resumalo un poco para que el equipo pueda gestionarlo mejor.", "warning")
            conn.close()
            return redirect(url_for("experiencia.publico_queja", empresa_id=empresa_id))
        if not acepta:
            flash("Necesitamos su autorizacion para tratar este mensaje.", "warning")
            conn.close()
            return redirect(url_for("experiencia.publico_queja", empresa_id=empresa_id))

        tipo = request.form.get("tipo") or "queja"
        if tipo not in {value for value, _ in TIPOS_QUEJA}:
            tipo = "queja"
        categoria = request.form.get("categoria") or "servicio"
        if categoria not in {value for value, _ in CATEGORIAS_QUEJA}:
            categoria = "servicio"
        titular = (request.form.get("titular_nombre") or "").strip()
        habitacion = (request.form.get("habitacion") or "").strip()
        email = (request.form.get("email") or "").strip()
        telefono = (request.form.get("telefono") or "").strip()
        idioma = (request.form.get("idioma") or "").strip()
        cliente_id = _upsert_cliente_360(
            conn,
            empresa_id,
            {
                "nombre": titular,
                "email": email,
                "telefono": telefono,
                "idioma": idioma,
            },
        )
        conn.execute(
            """
            INSERT INTO experiencia_quejas (
                empresa_id, fecha, canal, origen_modulo, cliente_id, titular_nombre,
                habitacion, idioma, tipo, categoria, subcategoria, descripcion,
                gravedad, impacto, estado, responsable, acciones_inmediatas,
                seguimiento_cliente, fecha_limite, creado_por, public_fingerprint, public_user_agent
            )
            VALUES (?, CURRENT_TIMESTAMP, 'web_publica', 'cliente_publico', ?, ?, ?, ?, ?, ?, ?, ?,
                    'media', ?, 'abierta', 'RRPP', ?, ?, date('now', '+1 day'), 'cliente', ?, ?)
            """,
            (
                empresa_id,
                cliente_id,
                titular,
                habitacion,
                idioma,
                tipo,
                categoria,
                request.form.get("subcategoria") or "",
                descripcion,
                request.form.get("impacto") or "",
                "Entrada publica pendiente de validar por RRPP.",
                "Responder al cliente por el canal indicado.",
                fingerprint,
                user_agent,
            ),
        )
        conn.execute(
            """
            INSERT INTO experiencia_public_rate (empresa_id, fingerprint, ruta)
            VALUES (?, ?, ?)
            """,
            (empresa_id, fingerprint, request.path),
        )
        conn.commit()
        conn.close()
        return render_template("experiencia/publico_gracias.html", empresa=empresa)

    conn.close()
    return render_template(
        "experiencia/publico_queja.html",
        empresa=empresa,
        empresa_id=empresa_id,
        tipos=TIPOS_QUEJA,
        categorias=CATEGORIAS_QUEJA,
    )


@experiencia_bp.route("/")
@login_required
def dashboard():
    if not _puede_ver():
        flash("No tienes permisos para Experiencia Cliente.", "warning")
        return redirect(url_for("home"))
    conn = get_db()
    _ensure_experiencia_schema(conn)
    conn.commit()
    empresa_id = _empresa_id()
    fecha_desde = _parse_date(request.args.get("desde"), date.today() - timedelta(days=30))
    fecha_hasta = _parse_date(request.args.get("hasta"), date.today())
    desde_txt = fecha_desde.isoformat()
    hasta_txt = fecha_hasta.isoformat()

    resumen = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN estado IN ('abierta', 'investigacion', 'accion', 'seguimiento') THEN 1 ELSE 0 END) AS abiertas,
            SUM(CASE WHEN gravedad IN ('alta', 'critica') AND estado IN ('abierta', 'investigacion', 'accion', 'seguimiento') THEN 1 ELSE 0 END) AS sensibles,
            SUM(CASE WHEN fecha_limite IS NOT NULL AND fecha_limite < date('now') AND estado IN ('abierta', 'investigacion', 'accion', 'seguimiento') THEN 1 ELSE 0 END) AS vencidas,
            SUM(COALESCE(compensacion_importe, 0)) AS compensaciones,
            SUM(CASE WHEN estado = 'cerrada' THEN 1 ELSE 0 END) AS cerradas
        FROM experiencia_quejas
        WHERE empresa_id = ? AND date(fecha) BETWEEN ? AND ?
        """,
        (empresa_id, desde_txt, hasta_txt),
    ).fetchone()

    por_categoria = conn.execute(
        """
        SELECT categoria, COUNT(*) AS total
        FROM experiencia_quejas
        WHERE empresa_id = ? AND date(fecha) BETWEEN ? AND ?
        GROUP BY categoria
        ORDER BY total DESC
        LIMIT 8
        """,
        (empresa_id, desde_txt, hasta_txt),
    ).fetchall()

    recientes = conn.execute(
        """
        SELECT *
        FROM experiencia_quejas
        WHERE empresa_id = ?
        ORDER BY CASE gravedad WHEN 'critica' THEN 0 WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END,
                 CASE estado WHEN 'abierta' THEN 0 WHEN 'investigacion' THEN 1 WHEN 'accion' THEN 2 WHEN 'seguimiento' THEN 3 ELSE 4 END,
                 fecha DESC
        LIMIT 12
        """,
        (empresa_id,),
    ).fetchall()

    reincidencias = conn.execute(
        """
        SELECT COALESCE(NULLIF(habitacion, ''), NULLIF(titular_nombre, ''), categoria) AS clave,
               categoria, COUNT(*) AS total
        FROM experiencia_quejas
        WHERE empresa_id = ?
          AND fecha >= date('now', '-90 day')
          AND COALESCE(NULLIF(habitacion, ''), NULLIF(titular_nombre, ''), categoria) IS NOT NULL
        GROUP BY clave, categoria
        HAVING COUNT(*) > 1
        ORDER BY total DESC
        LIMIT 10
        """,
        (empresa_id,),
    ).fetchall()

    conn.close()
    return render_template(
        "experiencia/dashboard.html",
        resumen=resumen,
        por_categoria=por_categoria,
        recientes=recientes,
        reincidencias=reincidencias,
        desde=desde_txt,
        hasta=hasta_txt,
        puede_gestionar=_puede_gestionar(),
    )


@experiencia_bp.route("/quejas")
@login_required
def quejas():
    if not _puede_ver():
        flash("No tienes permisos para Experiencia Cliente.", "warning")
        return redirect(url_for("home"))
    conn = get_db()
    _ensure_experiencia_schema(conn)
    conn.commit()
    empresa_id = _empresa_id()
    estado = request.args.get("estado", "")
    gravedad = request.args.get("gravedad", "")
    categoria = request.args.get("categoria", "")
    q = (request.args.get("q") or "").strip()

    query = "SELECT * FROM experiencia_quejas WHERE empresa_id = ?"
    params = [empresa_id]
    if estado:
        query += " AND estado = ?"
        params.append(estado)
    if gravedad:
        query += " AND gravedad = ?"
        params.append(gravedad)
    if categoria:
        query += " AND categoria = ?"
        params.append(categoria)
    if q:
        query += """
            AND (
                titular_nombre LIKE ? OR habitacion LIKE ? OR descripcion LIKE ?
                OR categoria LIKE ? OR subcategoria LIKE ?
            )
        """
        like = f"%{q}%"
        params.extend([like, like, like, like, like])
    query += " ORDER BY fecha DESC, id DESC LIMIT 250"
    registros = conn.execute(query, params).fetchall()
    conn.close()
    return render_template(
        "experiencia/quejas.html",
        quejas=registros,
        filtros={"estado": estado, "gravedad": gravedad, "categoria": categoria, "q": q},
        estados=ESTADOS,
        gravedades=GRAVEDADES,
        categorias=CATEGORIAS_QUEJA,
        puede_gestionar=_puede_gestionar(),
    )


@experiencia_bp.route("/quejas/nueva", methods=["GET", "POST"])
@login_required
def nueva_queja():
    if not _puede_gestionar():
        flash("No tienes permisos para crear quejas.", "warning")
        return redirect(url_for("experiencia.quejas"))
    conn = get_db()
    _ensure_experiencia_schema(conn)
    conn.commit()
    empresa_id = _empresa_id()

    if request.method == "POST":
        reserva_id = _safe_int(request.form.get("reserva_id"))
        viajero_id = _safe_int(request.form.get("viajero_id"))
        contexto = _contexto_reserva_viajero(conn, reserva_id, viajero_id, empresa_id)
        titular_nombre = (request.form.get("titular_nombre") or contexto.get("titular_nombre") or "").strip()
        habitacion = (request.form.get("habitacion") or contexto.get("habitacion") or "").strip()
        descripcion = (request.form.get("descripcion") or "").strip()
        if not descripcion:
            flash("La descripcion de la queja es obligatoria.", "warning")
            return redirect(url_for("experiencia.nueva_queja"))

        cliente_id = _upsert_cliente_360(
            conn,
            empresa_id,
            {
                "nombre": contexto.get("nombre") or titular_nombre,
                "apellidos": contexto.get("apellidos") or "",
                "documento": contexto.get("documento") or "",
                "email": request.form.get("email") or contexto.get("email") or "",
                "telefono": request.form.get("telefono") or contexto.get("telefono") or "",
                "nacionalidad": request.form.get("nacionalidad") or contexto.get("nacionalidad") or "",
                "idioma": request.form.get("idioma") or contexto.get("idioma") or "",
                "alergias_resumen": contexto.get("alergias_resumen") or "",
            },
        )
        fecha_limite = request.form.get("fecha_limite") or (
            date.today() + timedelta(days=1 if request.form.get("gravedad") in ("alta", "critica") else 3)
        ).isoformat()
        cur = conn.execute(
            """
            INSERT INTO experiencia_quejas (
                empresa_id, fecha, canal, reserva_id, viajero_id, cliente_id,
                titular_nombre, habitacion, nacionalidad, idioma, tipo, categoria, subcategoria,
                descripcion, gravedad, impacto, estado, responsable, requiere_compensacion,
                compensacion_tipo, compensacion_importe, acciones_inmediatas, fecha_limite, creado_por
            )
            VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'abierta', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                empresa_id,
                request.form.get("canal") or "recepcion",
                reserva_id,
                viajero_id,
                cliente_id,
                titular_nombre,
                habitacion,
                request.form.get("nacionalidad") or contexto.get("nacionalidad") or "",
                request.form.get("idioma") or contexto.get("idioma") or "",
                request.form.get("tipo") or "queja",
                request.form.get("categoria") or "otro",
                request.form.get("subcategoria") or "",
                descripcion,
                request.form.get("gravedad") or "media",
                request.form.get("impacto") or "",
                request.form.get("responsable") or _nombre_usuario(),
                1 if request.form.get("requiere_compensacion") else 0,
                request.form.get("compensacion_tipo") or "",
                _to_float(request.form.get("compensacion_importe")),
                request.form.get("acciones_inmediatas") or "",
                fecha_limite,
                _nombre_usuario(),
            ),
        )
        queja_id = cur.lastrowid
        for sugerencia in _sugerencias_comprobacion(request.form.get("categoria"))[:3]:
            conn.execute(
                """
                INSERT INTO experiencia_comprobaciones (queja_id, empresa_id, area, comprobacion, resultado, usuario)
                VALUES (?, ?, 'sugerida', ?, '', ?)
                """,
                (queja_id, empresa_id, sugerencia, _nombre_usuario()),
            )
        conn.commit()
        conn.close()
        flash("Queja registrada. Ya puedes completar investigacion y acciones.", "success")
        return redirect(url_for("experiencia.detalle_queja", queja_id=queja_id))

    reservas = _reservas_recientes(conn, empresa_id)
    viajeros = _viajeros_recientes(conn, empresa_id)
    conn.close()
    return render_template(
        "experiencia/queja_form.html",
        reservas=reservas,
        viajeros=viajeros,
        canales=CANALES_QUEJA,
        tipos=TIPOS_QUEJA,
        categorias=CATEGORIAS_QUEJA,
        gravedades=GRAVEDADES,
    )


@experiencia_bp.route("/quejas/<int:queja_id>")
@login_required
def detalle_queja(queja_id):
    if not _puede_ver():
        flash("No tienes permisos para Experiencia Cliente.", "warning")
        return redirect(url_for("home"))
    conn = get_db()
    _ensure_experiencia_schema(conn)
    conn.commit()
    empresa_id = _empresa_id()
    queja = conn.execute(
        "SELECT * FROM experiencia_quejas WHERE id = ? AND empresa_id = ?",
        (queja_id, empresa_id),
    ).fetchone()
    if not queja:
        conn.close()
        flash("Queja no encontrada.", "warning")
        return redirect(url_for("experiencia.quejas"))
    comprobaciones = conn.execute(
        "SELECT * FROM experiencia_comprobaciones WHERE queja_id = ? AND empresa_id = ? ORDER BY fecha DESC, id DESC",
        (queja_id, empresa_id),
    ).fetchall()
    acciones = conn.execute(
        "SELECT * FROM experiencia_acciones WHERE queja_id = ? AND empresa_id = ? ORDER BY fecha_creacion DESC, id DESC",
        (queja_id, empresa_id),
    ).fetchall()
    analisis = _analisis_investigador(conn, queja, comprobaciones, acciones)
    sugerencias = _sugerencias_comprobacion(queja["categoria"])
    conn.close()
    return render_template(
        "experiencia/queja_detalle.html",
        queja=queja,
        comprobaciones=comprobaciones,
        acciones=acciones,
        analisis=analisis,
        sugerencias=sugerencias,
        estados=ESTADOS,
        categorias=CATEGORIAS_QUEJA,
        gravedades=GRAVEDADES,
        puede_gestionar=_puede_gestionar(),
    )


@experiencia_bp.route("/quejas/<int:queja_id>/comprobacion", methods=["POST"])
@login_required
def agregar_comprobacion(queja_id):
    if not _puede_gestionar():
        flash("No tienes permisos para investigar quejas.", "warning")
        return redirect(url_for("experiencia.detalle_queja", queja_id=queja_id))
    conn = get_db()
    _ensure_experiencia_schema(conn)
    empresa_id = _empresa_id()
    queja = conn.execute(
        "SELECT id FROM experiencia_quejas WHERE id = ? AND empresa_id = ?",
        (queja_id, empresa_id),
    ).fetchone()
    if not queja:
        conn.close()
        flash("Queja no encontrada.", "warning")
        return redirect(url_for("experiencia.quejas"))
    comprobacion = (request.form.get("comprobacion") or "").strip()
    if comprobacion:
        conn.execute(
            """
            INSERT INTO experiencia_comprobaciones (queja_id, empresa_id, area, comprobacion, resultado, evidencia, usuario)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                queja_id,
                empresa_id,
                request.form.get("area") or "",
                comprobacion,
                request.form.get("resultado") or "",
                request.form.get("evidencia") or "",
                _nombre_usuario(),
            ),
        )
        conn.execute(
            "UPDATE experiencia_quejas SET estado = CASE WHEN estado = 'abierta' THEN 'investigacion' ELSE estado END, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND empresa_id = ?",
            (queja_id, empresa_id),
        )
        conn.commit()
        flash("Comprobacion registrada.", "success")
    conn.close()
    return redirect(url_for("experiencia.detalle_queja", queja_id=queja_id))


@experiencia_bp.route("/quejas/<int:queja_id>/accion", methods=["POST"])
@login_required
def agregar_accion(queja_id):
    if not _puede_gestionar():
        flash("No tienes permisos para gestionar acciones.", "warning")
        return redirect(url_for("experiencia.detalle_queja", queja_id=queja_id))
    conn = get_db()
    _ensure_experiencia_schema(conn)
    empresa_id = _empresa_id()
    descripcion = (request.form.get("descripcion") or "").strip()
    if descripcion:
        conn.execute(
            """
            INSERT INTO experiencia_acciones (
                queja_id, empresa_id, tipo, descripcion, responsable, coste, fecha_limite, estado, resultado, usuario
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                queja_id,
                empresa_id,
                request.form.get("tipo") or "correctiva",
                descripcion,
                request.form.get("responsable") or _nombre_usuario(),
                _to_float(request.form.get("coste")),
                request.form.get("fecha_limite") or "",
                request.form.get("estado") or "pendiente",
                request.form.get("resultado") or "",
                _nombre_usuario(),
            ),
        )
        conn.execute(
            "UPDATE experiencia_quejas SET estado = CASE WHEN estado IN ('abierta', 'investigacion') THEN 'accion' ELSE estado END, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND empresa_id = ?",
            (queja_id, empresa_id),
        )
        conn.commit()
        flash("Accion registrada.", "success")
    conn.close()
    return redirect(url_for("experiencia.detalle_queja", queja_id=queja_id))


@experiencia_bp.route("/quejas/<int:queja_id>/actualizar", methods=["POST"])
@login_required
def actualizar_queja(queja_id):
    if not _puede_gestionar():
        flash("No tienes permisos para actualizar quejas.", "warning")
        return redirect(url_for("experiencia.detalle_queja", queja_id=queja_id))
    conn = get_db()
    _ensure_experiencia_schema(conn)
    empresa_id = _empresa_id()
    estado = request.form.get("estado") or "abierta"
    fecha_cierre = datetime.now().isoformat(timespec="seconds") if estado == "cerrada" else None
    conn.execute(
        """
        UPDATE experiencia_quejas
        SET estado = ?, gravedad = ?, responsable = ?, causa_raiz = ?,
            comprobaciones_resumen = ?, acciones_correctivas = ?, seguimiento_cliente = ?,
            resultado_cliente = ?, aprendizaje = ?, fecha_cierre = COALESCE(?, fecha_cierre),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (
            estado,
            request.form.get("gravedad") or "media",
            request.form.get("responsable") or "",
            request.form.get("causa_raiz") or "",
            request.form.get("comprobaciones_resumen") or "",
            request.form.get("acciones_correctivas") or "",
            request.form.get("seguimiento_cliente") or "",
            request.form.get("resultado_cliente") or "",
            request.form.get("aprendizaje") or "",
            fecha_cierre,
            queja_id,
            empresa_id,
        ),
    )
    conn.commit()
    conn.close()
    flash("Queja actualizada.", "success")
    return redirect(url_for("experiencia.detalle_queja", queja_id=queja_id))


@experiencia_bp.route("/quejas/<int:queja_id>/escalar", methods=["POST"])
@login_required
def escalar_queja(queja_id):
    if not _puede_gestionar():
        flash("No tienes permisos para escalar quejas.", "warning")
        return redirect(url_for("experiencia.detalle_queja", queja_id=queja_id))
    destino = request.form.get("destino") or "incidencias"
    if destino not in ("incidencias", "mantenimiento", "appcc", "recepcion", "pisos"):
        destino = "incidencias"
    conn = get_db()
    _ensure_experiencia_schema(conn)
    empresa_id = _empresa_id()
    queja = conn.execute(
        "SELECT * FROM experiencia_quejas WHERE id = ? AND empresa_id = ?",
        (queja_id, empresa_id),
    ).fetchone()
    if not queja:
        conn.close()
        flash("Queja no encontrada.", "warning")
        return redirect(url_for("experiencia.quejas"))
    if queja["incidencia_id"]:
        conn.close()
        flash("Esta queja ya tiene incidencia enlazada.", "info")
        return redirect(url_for("experiencia.detalle_queja", queja_id=queja_id))
    incidencia_id = _crear_incidencia_desde_queja(conn, queja, destino)
    conn.commit()
    conn.close()
    if incidencia_id:
        flash(f"Queja escalada a incidencias #{incidencia_id}.", "success")
    else:
        flash("No se pudo crear la incidencia operativa.", "warning")
    return redirect(url_for("experiencia.detalle_queja", queja_id=queja_id))


@experiencia_bp.route("/cliente-360")
@login_required
def cliente_360():
    if not _puede_ver():
        flash("No tienes permisos para Experiencia Cliente.", "warning")
        return redirect(url_for("home"))
    conn = get_db()
    _ensure_experiencia_schema(conn)
    conn.commit()
    empresa_id = _empresa_id()
    buscar = (request.args.get("q") or "").strip()
    viajero_id = _safe_int(request.args.get("viajero_id"))
    reserva_id = _safe_int(request.args.get("reserva_id"))
    cliente_id = _safe_int(request.args.get("cliente_id"))
    resultados = []
    perfil = {}
    reservas = []
    viajeros = []
    quejas_cliente = []
    avisos = []
    asistencias = []

    if buscar:
        like = f"%{buscar}%"
        if _table_exists(conn, "viajeros_recepcion"):
            if _table_exists(conn, "reservas_recepcion"):
                resultados.extend(
                    conn.execute(
                        """
                        SELECT 'viajero' AS tipo, v.id, v.reserva_id, v.nombre, v.apellidos,
                               v.numero_documento AS documento, v.email, v.telefono,
                               r.codigo AS reserva_codigo
                        FROM viajeros_recepcion v
                        LEFT JOIN reservas_recepcion r ON r.id = v.reserva_id AND r.empresa_id = v.empresa_id
                        WHERE v.empresa_id = ?
                          AND (v.nombre LIKE ? OR v.apellidos LIKE ? OR v.numero_documento LIKE ? OR v.email LIKE ? OR v.telefono LIKE ?)
                        ORDER BY v.fecha_creacion DESC
                        LIMIT 30
                        """,
                        (empresa_id, like, like, like, like, like),
                    ).fetchall()
                )
            else:
                resultados.extend(
                    conn.execute(
                        """
                        SELECT 'viajero' AS tipo, v.id, v.reserva_id, v.nombre, v.apellidos,
                               v.numero_documento AS documento, v.email, v.telefono,
                               NULL AS reserva_codigo
                        FROM viajeros_recepcion v
                        WHERE v.empresa_id = ?
                          AND (v.nombre LIKE ? OR v.apellidos LIKE ? OR v.numero_documento LIKE ? OR v.email LIKE ? OR v.telefono LIKE ?)
                        ORDER BY v.fecha_creacion DESC
                        LIMIT 30
                        """,
                        (empresa_id, like, like, like, like, like),
                    ).fetchall()
                )
        resultados.extend(
            conn.execute(
                """
                SELECT 'cliente' AS tipo, id, NULL AS reserva_id, nombre, apellidos,
                       documento, email, telefono, NULL AS reserva_codigo
                FROM clientes_360
                WHERE empresa_id = ?
                  AND (nombre LIKE ? OR apellidos LIKE ? OR documento LIKE ? OR email LIKE ? OR telefono LIKE ?)
                ORDER BY updated_at DESC, fecha_creacion DESC
                LIMIT 30
                """,
                (empresa_id, like, like, like, like, like),
            ).fetchall()
        )

    if viajero_id:
        perfil = _contexto_reserva_viajero(conn, reserva_id, viajero_id, empresa_id)
        reserva_id = perfil.get("reserva_id") or reserva_id
        if perfil:
            cliente_id = _upsert_cliente_360(conn, empresa_id, perfil)
            conn.commit()
    elif cliente_id:
        cliente = conn.execute(
            "SELECT * FROM clientes_360 WHERE id = ? AND empresa_id = ?",
            (cliente_id, empresa_id),
        ).fetchone()
        if cliente:
            perfil = dict(cliente)
    elif reserva_id:
        perfil = _contexto_reserva_viajero(conn, reserva_id, None, empresa_id)

    documento = perfil.get("documento") or perfil.get("numero_documento") or ""
    email = perfil.get("email") or ""
    telefono = perfil.get("telefono") or ""
    nombre_ref = perfil.get("titular_nombre") or f"{perfil.get('nombre', '')} {perfil.get('apellidos', '')}".strip()

    if reserva_id and _table_exists(conn, "reservas_recepcion"):
        if _table_exists(conn, "habitaciones_recepcion"):
            reservas = conn.execute(
                """
                SELECT r.*, COALESCE(h.numero, h.nombre, CAST(r.habitacion_id AS TEXT), '') AS habitacion
                FROM reservas_recepcion r
                LEFT JOIN habitaciones_recepcion h ON h.id = r.habitacion_id AND h.empresa_id = r.empresa_id
                WHERE r.id = ? AND r.empresa_id = ?
                """,
                (reserva_id, empresa_id),
            ).fetchall()
        else:
            reservas = conn.execute(
                """
                SELECT r.*, CAST(r.habitacion_id AS TEXT) AS habitacion
                FROM reservas_recepcion r
                WHERE r.id = ? AND r.empresa_id = ?
                """,
                (reserva_id, empresa_id),
            ).fetchall()
    elif nombre_ref and _table_exists(conn, "reservas_recepcion"):
        if _table_exists(conn, "habitaciones_recepcion"):
            reservas = conn.execute(
                """
                SELECT r.*, COALESCE(h.numero, h.nombre, CAST(r.habitacion_id AS TEXT), '') AS habitacion
                FROM reservas_recepcion r
                LEFT JOIN habitaciones_recepcion h ON h.id = r.habitacion_id AND h.empresa_id = r.empresa_id
                WHERE r.empresa_id = ?
                  AND (r.titular_nombre LIKE ? OR r.titular_email = ? OR r.titular_telefono = ?)
                ORDER BY r.fecha_entrada DESC
                LIMIT 20
                """,
                (empresa_id, f"%{nombre_ref}%", email, telefono),
            ).fetchall()
        else:
            reservas = conn.execute(
                """
                SELECT r.*, CAST(r.habitacion_id AS TEXT) AS habitacion
                FROM reservas_recepcion r
                WHERE r.empresa_id = ?
                  AND (r.titular_nombre LIKE ? OR r.titular_email = ? OR r.titular_telefono = ?)
                ORDER BY r.fecha_entrada DESC
                LIMIT 20
                """,
                (empresa_id, f"%{nombre_ref}%", email, telefono),
            ).fetchall()

    if reserva_id and _table_exists(conn, "viajeros_recepcion"):
        viajeros = conn.execute(
            "SELECT * FROM viajeros_recepcion WHERE reserva_id = ? AND empresa_id = ? ORDER BY es_titular DESC, id",
            (reserva_id, empresa_id),
        ).fetchall()
    elif (documento or email or telefono or nombre_ref) and _table_exists(conn, "viajeros_recepcion"):
        viajeros = conn.execute(
            """
            SELECT *
            FROM viajeros_recepcion
            WHERE empresa_id = ?
              AND (
                (? <> '' AND numero_documento = ?)
                OR (? <> '' AND email = ?)
                OR (? <> '' AND telefono = ?)
                OR (? <> '' AND (nombre || ' ' || COALESCE(apellidos, '')) LIKE ?)
              )
            ORDER BY fecha_creacion DESC
            LIMIT 20
            """,
            (empresa_id, documento, documento, email, email, telefono, telefono, nombre_ref, f"%{nombre_ref}%"),
        ).fetchall()

    if nombre_ref or reserva_id or viajero_id or cliente_id:
        params = [empresa_id]
        query = "SELECT * FROM experiencia_quejas WHERE empresa_id = ?"
        clauses = []
        if reserva_id:
            clauses.append("reserva_id = ?")
            params.append(reserva_id)
        if viajero_id:
            clauses.append("viajero_id = ?")
            params.append(viajero_id)
        if cliente_id:
            clauses.append("cliente_id = ?")
            params.append(cliente_id)
        if nombre_ref:
            clauses.append("titular_nombre LIKE ?")
            params.append(f"%{nombre_ref}%")
        query += " AND (" + " OR ".join(clauses or ["1=0"]) + ") ORDER BY fecha DESC"
        quejas_cliente = conn.execute(query, params).fetchall()

    if reserva_id and _table_exists(conn, "recepcion_avisos"):
        avisos = conn.execute(
            "SELECT * FROM recepcion_avisos WHERE reserva_id = ? AND empresa_id = ? ORDER BY fecha_creacion DESC",
            (reserva_id, empresa_id),
        ).fetchall()

    if (reserva_id or viajero_id) and _table_exists(conn, "animacion_asistencias"):
        asistencias = conn.execute(
            """
            SELECT aa.*, ac.titulo, ac.fecha
            FROM animacion_asistencias aa
            LEFT JOIN animacion_actividades ac ON ac.id = aa.actividad_id AND ac.empresa_id = aa.empresa_id
            WHERE aa.empresa_id = ?
              AND ((? IS NOT NULL AND aa.reserva_id = ?) OR (? IS NOT NULL AND aa.viajero_id = ?))
            ORDER BY aa.fecha_creacion DESC
            LIMIT 30
            """,
            (empresa_id, reserva_id, reserva_id, viajero_id, viajero_id),
        ).fetchall()

    conn.close()
    return render_template(
        "experiencia/cliente_360.html",
        buscar=buscar,
        resultados=resultados,
        perfil=perfil,
        reservas=reservas,
        viajeros=viajeros,
        quejas_cliente=quejas_cliente,
        avisos=avisos,
        asistencias=asistencias,
        puede_gestionar=_puede_gestionar(),
    )

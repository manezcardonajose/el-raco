import os
import re
import unicodedata
import json

from flask import render_template, request, redirect, url_for, session, flash, g
from database import get_db
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from . import auth_bp  # <-- IMPORTANTE: así importamos el blueprint
# ============================================
# FUNCIONES AUXILIARES (AADIR AL FINAL DEL ARCHIVO)
# ============================================
from functools import wraps
from flask import session, flash, redirect, url_for
from database import get_db


def _auth_table_exists(conn, table_name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _auth_column_exists(conn, table_name, column_name):
    try:
        cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:
        return False
    return any(col[1] == column_name for col in cols)


def _auth_add_column_if_missing(conn, table_name, column_sql):
    column_name = column_sql.strip().split()[0]
    if _auth_table_exists(conn, table_name) and not _auth_column_exists(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def _ensure_security_audit_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS erp_seguridad_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            usuario_id INTEGER,
            usuario_email TEXT,
            empresa_id INTEGER,
            centro_id INTEGER,
            modulo TEXT,
            accion TEXT NOT NULL,
            resultado TEXT,
            ip TEXT,
            user_agent TEXT,
            detalle TEXT,
            origen TEXT DEFAULT 'humano'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS erp_login_intentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            identificador TEXT,
            ip TEXT,
            exitoso INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_login_intentos_fecha ON erp_login_intentos(fecha)")


def _login_origen():
    return (request.remote_addr or "")[:64]


def _login_bloqueado(conn, identificador):
    desde = "-15 minutes"
    fallos_cuenta = conn.execute(
        """SELECT COUNT(*) FROM erp_login_intentos
           WHERE exitoso=0 AND LOWER(identificador)=LOWER(?) AND fecha >= datetime('now', ?)""",
        (identificador[:120], desde),
    ).fetchone()[0]
    fallos_ip = conn.execute(
        """SELECT COUNT(*) FROM erp_login_intentos
           WHERE exitoso=0 AND ip=? AND fecha >= datetime('now', ?)""",
        (_login_origen(), desde),
    ).fetchone()[0]
    return fallos_cuenta >= 8 or fallos_ip >= 25


def _registrar_intento_login(conn, identificador, exitoso):
    conn.execute(
        "INSERT INTO erp_login_intentos(identificador,ip,exitoso) VALUES(?,?,?)",
        (identificador[:120], _login_origen(), 1 if exitoso else 0),
    )
    conn.execute("DELETE FROM erp_login_intentos WHERE fecha < datetime('now','-2 days')")


def _registrar_auditoria_seguridad(
    accion,
    resultado="ok",
    usuario_id=None,
    usuario_email=None,
    empresa_id=None,
    centro_id=None,
    modulo="auth",
    detalle=None,
    origen="humano",
    conn=None,
):
    own_conn = conn is None
    if own_conn:
        conn = get_db()
    try:
        _ensure_security_audit_schema(conn)
        payload = detalle
        if isinstance(detalle, (dict, list)):
            payload = json.dumps(detalle, ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO erp_seguridad_auditoria
            (usuario_id, usuario_email, empresa_id, centro_id, modulo, accion, resultado, ip, user_agent, detalle, origen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                usuario_id if usuario_id is not None else session.get("user_id"),
                usuario_email or session.get("user_email"),
                empresa_id if empresa_id is not None else session.get("user_empresa_id"),
                centro_id if centro_id is not None else session.get("centro_activo_id"),
                modulo,
                accion,
                resultado,
                request.headers.get("X-Forwarded-For", request.remote_addr or "") if request else "",
                request.headers.get("User-Agent", "")[:300] if request else "",
                payload,
                origen,
            ),
        )
        if own_conn:
            conn.commit()
    except Exception:
        if own_conn:
            conn.rollback()
    finally:
        if own_conn:
            conn.close()


def _normalizar_codigo_acceso(valor):
    return re.sub(r"[^A-Za-z0-9]", "", valor or "").upper()[:20]


PERMISOS_BASE_ERP = [
    ("acceso_global", "Acceso global a todos los departamentos"),
    ("ver_inicio_ejecutivo", "Ver el inicio ejecutivo de la empresa"),
    ("gestionar_usuarios", "Crear y editar usuarios"),
    ("gestionar_empresas", "Gestionar empresas y configuracion general"),
    ("gestionar_estructura", "Gestionar departamentos, puntos de venta y almacenes"),
    ("productos_ver", "Ver articulos, stock y proveedores"),
    ("productos_gestionar", "Crear y editar articulos"),
    ("proveedores_ver", "Ver proveedores"),
    ("proveedores_gestionar", "Crear y editar proveedores y acuerdos"),
    ("ver_precios", "Ver precios, costes e importes"),
    ("modificar_precios", "Modificar precios y costes"),
    ("compras_solicitar", "Crear solicitudes de compra pendientes de autorizacion"),
    ("compras_autorizar_departamento", "Autorizar compras del propio departamento"),
    ("compras_recepcionar", "Recepcionar pedidos y albaranes con tablet o movil"),
    ("compras_gestionar", "Gestionar compras, recepciones y economato"),
    ("compras_negociar", "Cerrar acuerdos con proveedores, articulos y precios"),
    ("compras_validar_economico", "Validar documentos de compra economicamente"),
    ("produccion_ver", "Ver produccion del departamento"),
    ("produccion_editar", "Registrar produccion del departamento"),
    ("appcc_ver", "Ver controles APPCC y trazabilidad"),
    ("appcc_gestionar", "Gestionar controles APPCC y trazabilidad"),
    ("rrhh_ver", "Ver RRHH"),
    ("rrhh_gestionar", "Gestionar RRHH"),
    ("ver_cuadrantes", "Ver cuadrantes de horarios"),
    ("gestionar_cuadrantes", "Crear y editar cuadrantes de horarios"),
    ("copiar_cuadrantes", "Copiar cuadrantes de una semana a otra"),
    ("recepcion_ver", "Ver recepcion"),
    ("recepcion_gestionar", "Gestionar recepcion"),
    ("recepcion_facturacion", "Gestionar caja, facturas e impuestos"),
    ("contabilidad_ver", "Ver control economico, documentos, vencimientos y pagos"),
    ("contabilidad_gestionar", "Gestionar documentos economicos, terceros y pagos"),
    ("pagos_ver", "Ver pasarelas y operaciones de cobro"),
    ("pagos_configurar", "Configurar pasarelas de pago y secretos tecnicos"),
    ("pagos_operar", "Crear operaciones, links, preautorizaciones y devoluciones"),
    ("pisos_ver", "Ver pisos"),
    ("pisos_gestionar", "Gestionar pisos"),
    ("mantenimiento_ver", "Ver mantenimiento"),
    ("mantenimiento_gestionar", "Gestionar mantenimiento"),
    ("tpv_ver", "Ver TPV"),
    ("tpv_gestionar", "Configurar y operar TPV"),
    ("asistente_ver", "Ver asistente IA y gestion 360"),
    ("ia_estrategica_ver", "Ver IA estrategica, simuladores y vision de temporada"),
    ("animacion_ver", "Ver agenda, asistencia y costes de animacion"),
    ("animacion_gestionar", "Crear y gestionar actividades de animacion"),
    ("experiencia_ver", "Ver experiencia cliente, RRPP y ficha 360"),
    ("experiencia_gestionar", "Gestionar quejas, comprobaciones y seguimiento cliente"),
    ("informes_ver", "Ver informes"),
    ("ver_rrhh", "Acceso al modulo RRHH"),
    ("gestionar_empleados", "Crear y editar empleados"),
    ("ver_contratos", "Ver contratos"),
    ("gestionar_contratos", "Crear y editar contratos"),
    ("ver_nominas", "Ver nominas"),
    ("gestionar_nominas", "Crear y editar nominas"),
    ("ver_fichajes", "Ver fichajes"),
    ("gestionar_fichajes", "Registrar y corregir fichajes"),
]


ROLES_BASE_ERP = [
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
    "contable",
    "contabilidad",
    "administracion",
    "rrhh",
    "recursos_humanos",
    "compras",
    "economato",
    "jefe_compras",
    "responsable_compras",
    "empleado_compras",
    "auxiliar_compras",
    "jefe_departamento",
    "segundo_jefe",
    "jefe_cocina",
    "chef",
    "jefe_partida",
    "cocinero",
    "ayudante",
    "ayudante_cocina",
    "pinche",
    "friegue",
    "fregador",
    "jefe_bares",
    "maitre",
    "metre",
    "jefe_rango",
    "camarero",
    "recepcion",
    "jefe_recepcion",
    "relaciones_publicas",
    "jefe_relaciones_publicas",
    "animacion",
    "jefe_animacion",
    "monitor_animacion",
    "mantenimiento",
    "jefe_mantenimiento",
    "segundo_mantenimiento",
    "pisos",
    "gobernanta",
    "supervisora",
    "camarera",
    "lavanderia",
    "tablet_pisos",
    "empleado",
    "kiosco",
]


PERMISOS_TODOS = {nombre for nombre, _ in PERMISOS_BASE_ERP}
PERMISOS_GLOBALES = set(PERMISOS_TODOS)

PERMISOS_COMPRAS_JEFE = {
    "productos_ver",
    "productos_gestionar",
    "proveedores_ver",
    "proveedores_gestionar",
    "ver_precios",
    "modificar_precios",
    "compras_solicitar",
    "compras_autorizar_departamento",
    "compras_recepcionar",
    "compras_gestionar",
    "compras_negociar",
    "compras_validar_economico",
    "informes_ver",
}

PERMISOS_COMPRAS_OPERATIVO = {
    "productos_ver",
    "proveedores_ver",
    "ver_precios",
    "compras_solicitar",
    "compras_recepcionar",
    "compras_gestionar",
    "informes_ver",
}

PERMISOS_JEFATURA = {
    "produccion_ver",
    "produccion_editar",
    "appcc_ver",
    "appcc_gestionar",
    "compras_solicitar",
    "compras_autorizar_departamento",
    "compras_recepcionar",
    "productos_ver",
    "productos_gestionar",
    "proveedores_ver",
    "ver_precios",
    "ver_cuadrantes",
    "gestionar_cuadrantes",
    "copiar_cuadrantes",
}

PERMISOS_MANDO_PRODUCCION = {
    "produccion_ver",
    "produccion_editar",
    "appcc_ver",
    "compras_solicitar",
    "productos_ver",
}

PERMISOS_PRODUCCION = {
    "produccion_ver",
    "produccion_editar",
    "appcc_ver",
}

PERMISOS_SALA_OPERATIVO = {
    "tpv_ver",
    "appcc_ver",
    "pagos_operar",
}

PERMISOS_SALA_MANDO = PERMISOS_SALA_OPERATIVO | {
    "compras_solicitar",
    "productos_ver",
}

PERMISOS_SALA_JEFE = PERMISOS_SALA_OPERATIVO | {
    "tpv_gestionar",
    "appcc_gestionar",
    "compras_solicitar",
    "compras_autorizar_departamento",
    "compras_recepcionar",
    "productos_ver",
    "proveedores_ver",
    "ver_cuadrantes",
    "gestionar_cuadrantes",
    "copiar_cuadrantes",
}

PERMISOS_FINANZAS = {
    "contabilidad_ver",
    "contabilidad_gestionar",
    "pagos_ver",
    "pagos_operar",
    "compras_validar_economico",
    "productos_ver",
    "proveedores_ver",
    "ver_precios",
    "informes_ver",
}

PERMISOS_ROL_BASE = {
    "admin": PERMISOS_GLOBALES,
    "administrador": PERMISOS_GLOBALES,
    "gerente": PERMISOS_GLOBALES,
    "director": PERMISOS_GLOBALES,
    "direccion": PERMISOS_GLOBALES,
    "direccion_general": PERMISOS_GLOBALES,
    "director_operaciones": PERMISOS_GLOBALES,
    "director_hotel": PERMISOS_GLOBALES,
    "subdirector": PERMISOS_GLOBALES,
    "propietario": PERMISOS_GLOBALES,
    "contable": PERMISOS_FINANZAS,
    "contabilidad": PERMISOS_FINANZAS,
    "administracion": PERMISOS_FINANZAS | {"recepcion_facturacion"},
    "rrhh": {
        "rrhh_ver", "rrhh_gestionar", "ver_rrhh", "gestionar_empleados",
        "ver_contratos", "gestionar_contratos", "ver_nominas",
        "gestionar_nominas", "ver_fichajes", "gestionar_fichajes",
        "gestionar_usuarios", "informes_ver",
    },
    "recursos_humanos": {
        "rrhh_ver", "rrhh_gestionar", "ver_rrhh", "gestionar_empleados",
        "ver_contratos", "gestionar_contratos", "ver_nominas",
        "gestionar_nominas", "ver_fichajes", "gestionar_fichajes",
        "gestionar_usuarios", "informes_ver",
    },
    "compras": PERMISOS_COMPRAS_JEFE,
    "economato": PERMISOS_COMPRAS_JEFE,
    "jefe_compras": PERMISOS_COMPRAS_JEFE,
    "responsable_compras": PERMISOS_COMPRAS_JEFE,
    "empleado_compras": PERMISOS_COMPRAS_OPERATIVO,
    "auxiliar_compras": PERMISOS_COMPRAS_OPERATIVO,
    "jefe_departamento": PERMISOS_JEFATURA,
    "segundo_jefe": PERMISOS_JEFATURA,
    "jefe_cocina": PERMISOS_JEFATURA,
    "chef": PERMISOS_JEFATURA,
    "jefe_partida": PERMISOS_MANDO_PRODUCCION,
    "cocinero": PERMISOS_PRODUCCION,
    "ayudante": set(),
    "ayudante_cocina": set(),
    "pinche": set(),
    "friegue": set(),
    "fregador": set(),
    "jefe_bares": PERMISOS_SALA_JEFE,
    "maitre": PERMISOS_SALA_JEFE,
    "metre": PERMISOS_SALA_JEFE,
    "jefe_rango": PERMISOS_SALA_MANDO,
    "camarero": PERMISOS_SALA_OPERATIVO,
    "recepcion": {"recepcion_ver", "recepcion_gestionar", "recepcion_facturacion", "tpv_ver", "experiencia_ver", "experiencia_gestionar", "pagos_ver", "pagos_operar"},
    "jefe_recepcion": {"recepcion_ver", "recepcion_gestionar", "recepcion_facturacion", "tpv_ver", "tpv_gestionar", "informes_ver", "experiencia_ver", "experiencia_gestionar", "pagos_ver", "pagos_operar"},
    "relaciones_publicas": {"recepcion_ver", "experiencia_ver", "experiencia_gestionar", "animacion_ver", "informes_ver"},
    "jefe_relaciones_publicas": {"recepcion_ver", "recepcion_gestionar", "experiencia_ver", "experiencia_gestionar", "animacion_ver", "animacion_gestionar", "informes_ver"},
    "animacion": {"animacion_ver", "animacion_gestionar", "compras_solicitar"},
    "jefe_animacion": {"animacion_ver", "animacion_gestionar", "compras_solicitar", "compras_autorizar_departamento", "informes_ver"},
    "monitor_animacion": {"animacion_ver"},
    "mantenimiento": {"mantenimiento_ver", "mantenimiento_gestionar", "compras_solicitar"},
    "jefe_mantenimiento": {"mantenimiento_ver", "mantenimiento_gestionar", "compras_solicitar", "compras_autorizar_departamento", "compras_recepcionar", "informes_ver"},
    "segundo_mantenimiento": {"mantenimiento_ver", "mantenimiento_gestionar", "compras_solicitar", "compras_autorizar_departamento", "compras_recepcionar", "informes_ver"},
    "pisos": {"pisos_ver", "pisos_gestionar", "compras_solicitar"},
    "gobernanta": {"pisos_ver", "pisos_gestionar", "compras_solicitar", "compras_autorizar_departamento", "compras_recepcionar", "informes_ver"},
    "supervisora": {"pisos_ver", "pisos_gestionar", "compras_solicitar"},
    "camarera": {"pisos_ver"},
    "lavanderia": {"pisos_ver"},
    "tablet_pisos": {"pisos_ver"},
    "empleado": set(),
    "kiosco": set(),
}


def _insertar_permiso_rol_si_falta(conn, rol_id, permiso_id):
    existe = conn.execute(
        "SELECT 1 FROM roles_permisos WHERE rol_id = ? AND permiso_id = ? LIMIT 1",
        (rol_id, permiso_id),
    ).fetchone()
    if not existe:
        conn.execute(
            "INSERT INTO roles_permisos (rol_id, permiso_id) VALUES (?, ?)",
            (rol_id, permiso_id),
        )


def _sembrar_permisos_auth(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS permisos (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT UNIQUE, descripcion TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS roles_permisos (rol_id INTEGER, permiso_id INTEGER)")
    for nombre, descripcion in PERMISOS_BASE_ERP:
        conn.execute(
            "INSERT OR IGNORE INTO permisos (nombre, descripcion) VALUES (?, ?)",
            (nombre, descripcion),
        )
    for rol in ROLES_BASE_ERP:
        conn.execute("INSERT OR IGNORE INTO roles (nombre) VALUES (?)", (rol,))

    permisos_por_nombre = {
        row["nombre"]: row["id"]
        for row in conn.execute("SELECT id, nombre FROM permisos").fetchall()
    }
    roles_por_nombre = {
        normalizar_rol(row["nombre"]): row["id"]
        for row in conn.execute("SELECT id, nombre FROM roles").fetchall()
    }
    for rol, permisos in PERMISOS_ROL_BASE.items():
        rol_id = roles_por_nombre.get(normalizar_rol(rol))
        if not rol_id:
            continue
        for permiso in permisos:
            permiso_id = permisos_por_nombre.get(permiso)
            if permiso_id:
                _insertar_permiso_rol_si_falta(conn, rol_id, permiso_id)


def _normalizar_permisos_roles_operativos(conn):
    roles_limitados = {
        "contable": PERMISOS_FINANZAS,
        "contabilidad": PERMISOS_FINANZAS,
        "administracion": PERMISOS_FINANZAS | {"recepcion_facturacion"},
        "jefe_departamento": PERMISOS_JEFATURA,
        "segundo_jefe": PERMISOS_JEFATURA,
        "jefe_cocina": PERMISOS_JEFATURA,
        "chef": PERMISOS_JEFATURA,
        "jefe_partida": PERMISOS_MANDO_PRODUCCION,
        "cocinero": PERMISOS_PRODUCCION,
        "jefe_bares": PERMISOS_SALA_JEFE,
        "maitre": PERMISOS_SALA_JEFE,
        "metre": PERMISOS_SALA_JEFE,
        "jefe_rango": PERMISOS_SALA_MANDO,
        "camarero": PERMISOS_SALA_OPERATIVO,
        "ayudante": set(),
        "ayudante_cocina": set(),
        "pinche": set(),
        "friegue": set(),
        "fregador": set(),
    }
    permisos_compatibles = {
        "appcc_ver",
        "ver_appcc",
        "ver_compras",
        "ver_productos",
        "ver_precios",
    }
    permisos_por_nombre = {
        row["nombre"]: row["id"]
        for row in conn.execute("SELECT id, nombre FROM permisos").fetchall()
    }
    for rol in conn.execute("SELECT id, nombre FROM roles").fetchall():
        rol_key = normalizar_rol(rol["nombre"])
        if rol_key not in roles_limitados:
            continue
        permisos_permitidos = roles_limitados[rol_key] | permisos_compatibles
        ids_permitidos = {
            permisos_por_nombre[nombre]
            for nombre in permisos_permitidos
            if nombre in permisos_por_nombre
        }
        if not ids_permitidos:
            conn.execute("DELETE FROM roles_permisos WHERE rol_id = ?", (rol["id"],))
            continue
        placeholders = ",".join("?" for _ in ids_permitidos)
        conn.execute(
            f"""
            DELETE FROM roles_permisos
            WHERE rol_id = ?
              AND permiso_id NOT IN ({placeholders})
            """,
            [rol["id"]] + list(ids_permitidos),
        )
        for permiso in roles_limitados[rol_key]:
            permiso_id = permisos_por_nombre.get(permiso)
            if permiso_id:
                _insertar_permiso_rol_si_falta(conn, rol["id"], permiso_id)


def _ensure_auth_schema(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT UNIQUE NOT NULL
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        rol_id INTEGER NOT NULL,
        empresa_id INTEGER DEFAULT 1,
        activo BOOLEAN DEFAULT 1,
        ultimo_acceso DATETIME,
        empleado_id INTEGER,
        codigo_acceso TEXT,
        fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (rol_id) REFERENCES roles(id),
        FOREIGN KEY (empresa_id) REFERENCES empresas(id)
    )
    """)
    _ensure_security_audit_schema(conn)
    _auth_add_column_if_missing(conn, "usuarios", "empleado_id INTEGER")
    _auth_add_column_if_missing(conn, "usuarios", "codigo_acceso TEXT")
    _auth_add_column_if_missing(conn, "usuarios", "fecha_registro DATETIME")
    _auth_add_column_if_missing(conn, "usuarios", "debe_cambiar_password INTEGER DEFAULT 0")
    _auth_add_column_if_missing(conn, "usuarios", "es_global INTEGER DEFAULT 0")
    _auth_add_column_if_missing(conn, "usuarios", "puede_ver_precios INTEGER DEFAULT 0")
    _auth_add_column_if_missing(conn, "usuarios", "puede_modificar_precios INTEGER DEFAULT 0")
    _auth_add_column_if_missing(conn, "usuarios", "puede_validar_recepcion INTEGER DEFAULT 0")
    _auth_add_column_if_missing(conn, "usuarios", "puede_validar_economicamente INTEGER DEFAULT 0")
    if _auth_table_exists(conn, "empleados"):
        _auth_add_column_if_missing(conn, "empleados", "centro_id INTEGER")
    _sembrar_permisos_auth(conn)
    _normalizar_permisos_roles_operativos(conn)
    conn.execute("""
        UPDATE usuarios
        SET codigo_acceso = 'U' || printf('%05d', id)
        WHERE codigo_acceso IS NULL OR TRIM(codigo_acceso) = ''
    """)
    conn.execute("""
        UPDATE usuarios
        SET fecha_registro = CURRENT_TIMESTAMP
        WHERE fecha_registro IS NULL OR TRIM(fecha_registro) = ''
    """)


def _codigo_acceso_para_guardar(conn, empresa_id, codigo, usuario_id=None):
    codigo_limpio = _normalizar_codigo_acceso(codigo)
    if not codigo_limpio:
        if usuario_id:
            codigo_limpio = f"U{int(usuario_id):05d}"
        else:
            return None
    params = [empresa_id, codigo_limpio]
    extra = ""
    if usuario_id:
        extra = " AND id <> ?"
        params.append(usuario_id)
    existe = conn.execute(
        f"""
        SELECT id FROM usuarios
        WHERE empresa_id = ? AND UPPER(COALESCE(codigo_acceso, '')) = ?
        {extra}
        LIMIT 1
        """,
        params,
    ).fetchone()
    if existe:
        raise ValueError("Ese codigo de acceso ya esta en uso.")
    return codigo_limpio


def _empleados_para_usuario_select(conn, empresa_id):
    if not _auth_table_exists(conn, "empleados"):
        return []
    return conn.execute(
        """
        SELECT e.id, e.nombre, e.apellidos, e.usuario_id
        FROM empleados e
        WHERE e.empresa_id = ? AND COALESCE(e.activo, 1) = 1
        ORDER BY e.nombre, e.apellidos
        """,
        (empresa_id,),
    ).fetchall()


def _usuario_flags_desde_form():
    return {
        "es_global": 1 if request.form.get("es_global") else 0,
        "puede_ver_precios": 1 if request.form.get("puede_ver_precios") else 0,
        "puede_modificar_precios": 1 if request.form.get("puede_modificar_precios") else 0,
        "puede_validar_recepcion": 1 if request.form.get("puede_validar_recepcion") else 0,
        "puede_validar_economicamente": 1 if request.form.get("puede_validar_economicamente") else 0,
    }


def _vincular_usuario_empleado_desde_auth(conn, usuario_id, empleado_id, empresa_id):
    empleado_id = empleado_id or None
    if _auth_column_exists(conn, "usuarios", "empleado_id"):
        if empleado_id:
            conn.execute(
                "UPDATE usuarios SET empleado_id = NULL WHERE empresa_id = ? AND empleado_id = ? AND id <> ?",
                (empresa_id, empleado_id, usuario_id),
            )
        conn.execute(
            "UPDATE usuarios SET empleado_id = ? WHERE id = ? AND empresa_id = ?",
            (empleado_id, usuario_id, empresa_id),
        )
    if not _auth_table_exists(conn, "empleados") or not _auth_column_exists(conn, "empleados", "usuario_id"):
        return
    conn.execute(
        "UPDATE empleados SET usuario_id = NULL WHERE empresa_id = ? AND usuario_id = ?",
        (empresa_id, usuario_id),
    )
    if empleado_id:
        conn.execute(
            "UPDATE empleados SET usuario_id = NULL WHERE empresa_id = ? AND id <> ? AND usuario_id = ?",
            (empresa_id, empleado_id, usuario_id),
        )
        conn.execute(
            "UPDATE empleados SET usuario_id = ? WHERE id = ? AND empresa_id = ?",
            (usuario_id, empleado_id, empresa_id),
        )

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash(' Debes iniciar sesión para acceder a esta página', 'danger')
            return redirect(url_for('auth.login'))
        if session.get('debe_cambiar_password') and request.endpoint not in {'auth.cambiar_password', 'auth.logout'}:
            destino = 'portal_empleado' if (session.get('user_rol') or '').lower() == 'empleado' else ''
            flash('Debes cambiar la contrasena temporal antes de continuar.', 'warning')
            if destino:
                return redirect(url_for('auth.cambiar_password', next=destino))
            return redirect(url_for('auth.cambiar_password'))
        return f(*args, **kwargs)
    return decorated_function

def permiso_requerido(permiso):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            roles_gestion = {'admin', 'gerente', 'direccion', 'dirección', 'rrhh', 'recursos_humanos'}
            if not usuario_tiene_permiso(permiso):
                flash('No tienes permisos para acceder a esta pantalla.', 'warning')
                return redirect(url_for('home'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


ROLES_GLOBALES_OPERACION = {
    'admin',
    'administrador',
    'gerente',
    'director',
    'direccion',
    'direccion_general',
    'director_operaciones',
    'director_de_operaciones',
    'director_hotel',
    'director_de_hotel',
    'subdirector',
    'propietario',
    'contable',
    'contable_general',
    'administracion',
    'contabilidad',
}


# Un rol puede trabajar en varios centros sin ser por ello superusuario. Esta
# lista deliberadamente no incluye contabilidad ni administracion.
ROLES_SUPERUSUARIO = {
    'admin',
    'administrador',
    'gerente',
    'director',
    'direccion',
    'direccion_general',
    'director_operaciones',
    'director_de_operaciones',
    'director_hotel',
    'director_de_hotel',
    'subdirector',
    'propietario',
}

# Cambiar de empresa afecta al aislamiento de datos, no solo a la operacion
# de un centro. No concederlo por ser director o jefe de un establecimiento.
ROLES_CAMBIO_EMPRESA = {'admin', 'administrador', 'superadmin', 'super_admin'}


def usuario_puede_cambiar_empresa():
    return _rol_key(session.get('user_rol')) in ROLES_CAMBIO_EMPRESA


ROLES_LEGACY_AUDITORIA = {
    "bar": {
        "sugerido": "camarero",
        "motivo": "Rol antiguo de barra demasiado generico. Para sala/bar conviene usar camarero, jefe_rango, maitre o jefe_bares.",
        "migrable": True,
    },
    "cocina": {
        "sugerido": "cocinero",
        "motivo": "Rol antiguo demasiado generico. Para cocina conviene usar cocinero, jefe_partida, chef o jefe_cocina.",
        "migrable": True,
    },
    "jefe de partida": {
        "sugerido": "jefe_partida",
        "motivo": "Mismo rol con nombre escrito de otra forma.",
        "migrable": True,
    },
    "2o jefe cocina": {
        "sugerido": "segundo_jefe",
        "motivo": "Rol antiguo. Hay que confirmar si debe ser segundo_jefe o jefe_cocina segun responsabilidad real.",
        "migrable": False,
    },
    "2 jefe cocina": {
        "sugerido": "segundo_jefe",
        "motivo": "Rol antiguo. Hay que confirmar si debe ser segundo_jefe o jefe_cocina segun responsabilidad real.",
        "migrable": False,
    },
}


def normalizar_rol(valor):
    texto = str(valor or '').strip().lower()
    texto = unicodedata.normalize('NFKD', texto).encode('ascii', 'ignore').decode('ascii')
    return texto


def _clave_auditoria_rol(valor):
    texto = normalizar_rol(valor)
    texto = re.sub(r"[^a-z0-9]+", "_", texto).strip("_")
    return texto


def _legacy_auditoria_rol(valor):
    texto = normalizar_rol(valor)
    texto_plano = re.sub(r"[^a-z0-9]+", " ", texto).strip()
    return ROLES_LEGACY_AUDITORIA.get(texto) or ROLES_LEGACY_AUDITORIA.get(texto_plano)


def usuario_es_global_operacion(extra_roles=None):
    if session.get("user_centro_bloqueado") in (1, True, "1", "true", "True"):
        return False
    roles = set(ROLES_GLOBALES_OPERACION)
    if extra_roles:
        roles.update(_rol_key(rol) for rol in extra_roles)
    rol = _rol_key(session.get('user_rol'))
    return rol in roles


def usuario_tiene_permiso(permiso):
    if not permiso:
        return False

    permiso = normalizar_rol(permiso)
    rol = _rol_key(session.get("user_rol"))
    if rol in ROLES_SUPERUSUARIO:
        return True

    if permiso == "ver_precios" and session.get("puede_ver_precios") in (1, True, "1", "true", "True"):
        return True
    if permiso == "modificar_precios" and session.get("puede_modificar_precios") in (1, True, "1", "true", "True"):
        return True
    if permiso == "recepcion_facturacion" and session.get("puede_validar_recepcion") in (1, True, "1", "true", "True"):
        return True
    if permiso == "compras_validar_economico" and session.get("puede_validar_economicamente") in (1, True, "1", "true", "True"):
        return True

    permisos_rol = PERMISOS_ROL_BASE.get(rol, set())
    if permiso in permisos_rol:
        return True

    permisos_cache = getattr(g, "permisos_usuario_actual", None)
    if permisos_cache is not None:
        return permiso in permisos_cache

    user_id = session.get("user_id")
    if not user_id:
        return False
    conn = None
    try:
        conn = get_db()
        _ensure_auth_schema(conn)
        permisos_db = {
            row["nombre"].lower()
            for row in conn.execute(
                """
                SELECT p.nombre
                FROM usuarios u
                JOIN roles_permisos rp ON rp.rol_id = u.rol_id
                JOIN permisos p ON p.id = rp.permiso_id
                WHERE u.id = ?
                """,
                (user_id,),
            ).fetchall()
        }
        permisos_db.update(permisos_rol)
        g.permisos_usuario_actual = permisos_db
        return permiso in permisos_db
    except Exception:
        return False
    finally:
        if conn:
            conn.close()


ROLES_JEFATURA_DEPARTAMENTO = {
    'jefe_departamento',
    'jefe',
    'jefa',
    'segundo_jefe',
    'segunda_jefa',
    '2_jefe',
    '2_jefa',
    '2o_jefe',
    '2a_jefa',
    'subjefe',
    'subjefa',
    'responsable',
    'encargado',
    'encargada',
    'gobernanta',
    'maitre',
    'metre',
    'jefe_bares',
    'jefe_rango',
    'chef',
    'jefe_cocina',
    'jefe_partida',
    'jefe_recepcion',
    'jefe_mantenimiento',
    'segundo_mantenimiento',
    'jefe_compras',
    'responsable_compras',
}


def _rol_key(valor):
    texto = normalizar_rol(valor)
    for separador in (' ', '-', '/', '.'):
        texto = texto.replace(separador, '_')
    while '__' in texto:
        texto = texto.replace('__', '_')
    return texto.strip('_')


def usuario_es_jefatura_departamento():
    roles = {
        _rol_key(session.get('user_rol')),
        _rol_key(session.get('user_empleado_rol')),
    }
    if roles & ROLES_JEFATURA_DEPARTAMENTO:
        return True
    return any(rol.endswith('_jefe') or rol.endswith('_jefa') for rol in roles)


def usuario_departamento_id():
    return session.get('user_departamento_id') or session.get('departamento_id')


def usuario_subdepartamento_id():
    return session.get('user_subdepartamento_id') or session.get('subdepartamento_id')


def usuario_puede_ver_departamento(departamento_id, extra_roles=None):
    if usuario_es_global_operacion(extra_roles):
        return True
    depto_usuario = usuario_departamento_id()
    return bool(depto_usuario and departamento_id and str(depto_usuario) == str(departamento_id))


def departamento_scope_sql(alias, empresa_id, departamento_col='departamento_id', extra_roles=None):
    """Filtro SQL comun para datos que pertenecen a un departamento.

    Devuelve (sql, params). Si el usuario no es global y no tiene departamento
    asociado, no se muestra nada.
    """
    if usuario_es_global_operacion(extra_roles):
        return f"{alias}.empresa_id = ?", [empresa_id]
    depto_usuario = usuario_departamento_id()
    if depto_usuario:
        return f"{alias}.empresa_id = ? AND {alias}.{departamento_col} = ?", [empresa_id, depto_usuario]
    return f"{alias}.empresa_id = ? AND 1 = 0", [empresa_id]


def _cargar_empleado_en_sesion(conn, usuario):
    empresa_id = usuario['empresa_id']
    empleado = None
    if 'empleado_id' in usuario.keys() and usuario['empleado_id']:
        empleado = conn.execute(
            """
            SELECT id, departamento_id, subdepartamento_id, centro_id, rol
            FROM empleados
            WHERE id = ? AND empresa_id = ? AND COALESCE(activo, 1) = 1
            """,
            (usuario['empleado_id'], empresa_id),
        ).fetchone()

    if not empleado:
        try:
            empleado = conn.execute(
                """
                SELECT id, departamento_id, subdepartamento_id, centro_id, rol
                FROM empleados
                WHERE usuario_id = ? AND empresa_id = ? AND COALESCE(activo, 1) = 1
                """,
                (usuario['id'], empresa_id),
            ).fetchone()
        except Exception:
            empleado = None

    if not empleado and usuario['email']:
        empleado = conn.execute(
            """
            SELECT id, departamento_id, subdepartamento_id, centro_id, rol
            FROM empleados
            WHERE LOWER(COALESCE(email, '')) = LOWER(?) AND empresa_id = ? AND COALESCE(activo, 1) = 1
            """,
            (usuario['email'], empresa_id),
        ).fetchone()

    if empleado:
        session['user_empleado_id'] = empleado['id']
        if 'centro_id' in empleado.keys() and empleado['centro_id']:
            session['user_centro_id'] = empleado['centro_id']
            session['centro_activo_id'] = empleado['centro_id']
            session['user_centro_bloqueado'] = 1
            session['user_es_global'] = 0
        if empleado['departamento_id']:
            session['user_departamento_id'] = empleado['departamento_id']
        if empleado['subdepartamento_id']:
            session['user_subdepartamento_id'] = empleado['subdepartamento_id']
        if 'rol' in empleado.keys() and empleado['rol']:
            session['user_empleado_rol'] = empleado['rol']

def init_auth_db():
    """Inicializa las tablas de autenticación si no existen"""
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        _ensure_auth_schema(conn)
        
        # Crear tabla de roles si no existe
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE NOT NULL
        )
        """)
        
        # Insertar roles por defecto (uno por uno con commit después de cada uno)
        roles = ROLES_BASE_ERP
        for rol in roles:
            try:
                cursor.execute("INSERT OR IGNORE INTO roles (nombre) VALUES (?)", (rol,))
                conn.commit()  # Commit después de cada inserción
                print(f" Rol '{rol}' verificado")
            except Exception as e:
                print(f"️ Error con rol '{rol}': {e}")
                conn.rollback()
        
        # Crear tabla de usuarios si no existe
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            rol_id INTEGER NOT NULL,
            empresa_id INTEGER DEFAULT 1,
            activo BOOLEAN DEFAULT 1,
            ultimo_acceso DATETIME,
            empleado_id INTEGER,
            codigo_acceso TEXT,
            fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (rol_id) REFERENCES roles(id),
            FOREIGN KEY (empresa_id) REFERENCES empresas(id)
        )
        """)
        _ensure_auth_schema(conn)
        
        # El administrador inicial solo se crea con credenciales explicitas.
        # Nunca se debe dejar un admin/admin conocido en una instalacion nueva.
        admin_email = (os.environ.get("ERP_ADMIN_EMAIL") or "").strip().lower()
        admin_password = os.environ.get("ERP_ADMIN_PASSWORD") or ""
        if admin_email or admin_password:
            if not admin_email or len(admin_password) < 12:
                raise RuntimeError(
                    "ERP_ADMIN_EMAIL y ERP_ADMIN_PASSWORD (minimo 12 caracteres) "
                    "son obligatorios para crear el administrador inicial."
                )
            cursor.execute("SELECT id FROM usuarios WHERE LOWER(email) = ?", (admin_email,))
            if not cursor.fetchone():
                cursor.execute("SELECT id FROM roles WHERE nombre = 'admin'")
                admin_role = cursor.fetchone()
                if not admin_role:
                    raise RuntimeError("No se encontro el rol admin.")
                cursor.execute("""
                    INSERT INTO usuarios (
                        nombre, email, password_hash, rol_id, empresa_id,
                        codigo_acceso, debe_cambiar_password
                    ) VALUES (?, ?, ?, ?, ?, ?, 1)
                """, (
                    (os.environ.get("ERP_ADMIN_NAME") or "Administrador").strip(),
                    admin_email,
                    generate_password_hash(admin_password),
                    admin_role['id'],
                    int(os.environ.get("ERP_ADMIN_EMPRESA_ID", "1")),
                    _normalizar_codigo_acceso(os.environ.get("ERP_ADMIN_CODE")) or None,
                ))
                conn.commit()
                print(" Usuario administrador inicial creado; debera cambiar la clave al entrar")
        
        print(" Tablas de autenticación inicializadas correctamente")
        
    except Exception as e:
        print(f" Error en init_auth_db: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Inicia sesión de usuario"""
    if request.method == 'POST':
        identificador = (request.form.get('email') or '').strip()
        password = request.form['password']
        codigo = _normalizar_codigo_acceso(identificador)
        
        conn = get_db()
        _ensure_auth_schema(conn)
        if _login_bloqueado(conn, identificador):
            conn.commit()
            conn.close()
            _registrar_auditoria_seguridad(
                "login",
                "bloqueado",
                usuario_email=identificador[:120],
                detalle={"motivo": "demasiados_intentos"},
            )
            flash('Demasiados intentos. Espera 15 minutos antes de volver a probar.', 'danger')
            return render_template('auth/login.html'), 429
        usuario = conn.execute("""
            SELECT u.*, r.nombre as rol_nombre
            FROM usuarios u
            LEFT JOIN roles r ON u.rol_id = r.id
            WHERE u.activo = 1
              AND (LOWER(u.email) = LOWER(?) OR UPPER(COALESCE(u.codigo_acceso, '')) = ?)
        """, (identificador, codigo)).fetchone()
        conn.close()
        
        if usuario and check_password_hash(usuario['password_hash'], password):
            conn = get_db()
            _ensure_auth_schema(conn)
            _registrar_intento_login(conn, identificador, True)
            conn.commit()
            conn.close()
            session.clear()
            session['user_id'] = usuario['id']
            session['user_nombre'] = usuario['nombre']
            session['user_email'] = usuario['email']
            session['user_rol'] = usuario['rol_nombre']
            session['user_empresa_id'] = usuario['empresa_id']
            session['debe_cambiar_password'] = usuario['debe_cambiar_password'] if 'debe_cambiar_password' in usuario.keys() else 0
            es_global_db = usuario['es_global'] if 'es_global' in usuario.keys() else 0
            session['user_es_global'] = 1 if es_global_db in (1, True, '1', 'true', 'True') or _rol_key(usuario['rol_nombre']) in ROLES_GLOBALES_OPERACION else 0
            session['puede_ver_precios'] = usuario['puede_ver_precios'] if 'puede_ver_precios' in usuario.keys() else 0
            session['puede_modificar_precios'] = usuario['puede_modificar_precios'] if 'puede_modificar_precios' in usuario.keys() else 0
            session['puede_validar_recepcion'] = usuario['puede_validar_recepcion'] if 'puede_validar_recepcion' in usuario.keys() else 0
            session['puede_validar_economicamente'] = usuario['puede_validar_economicamente'] if 'puede_validar_economicamente' in usuario.keys() else 0
            
            # Actualizar último acceso
            conn = get_db()
            _cargar_empleado_en_sesion(conn, usuario)
            conn.execute("UPDATE usuarios SET ultimo_acceso = CURRENT_TIMESTAMP WHERE id = ?", (usuario['id'],))
            _registrar_auditoria_seguridad(
                "login",
                "ok",
                usuario_id=usuario["id"],
                usuario_email=usuario["email"],
                empresa_id=usuario["empresa_id"],
                centro_id=session.get("centro_activo_id"),
                detalle={"identificador": identificador[:120], "rol": usuario["rol_nombre"]},
                conn=conn,
            )
            conn.commit()
            conn.close()
            
            flash(f' Bienvenido {usuario["nombre"]}', 'success')
            rol_login = (usuario['rol_nombre'] or '').lower()
            if session.get('debe_cambiar_password') and rol_login not in {'kiosco', 'tablet', 'fichaje'}:
                flash('Cambia tu contrasena temporal para continuar.', 'warning')
                if rol_login == 'empleado':
                    return redirect(url_for('auth.cambiar_password', next='portal_empleado'))
                return redirect(url_for('auth.cambiar_password'))
            if rol_login in {'kiosco', 'tablet', 'fichaje'}:
                return redirect(url_for('rrhh.kiosco_fichaje'))
            if rol_login == 'empleado':
                return redirect(url_for('rrhh.portal_empleado'))
            return redirect(url_for('home'))
        else:
            flash(' Email/codigo o contraseña incorrectos', 'danger')
            conn = get_db()
            _ensure_auth_schema(conn)
            _registrar_intento_login(conn, identificador, False)
            conn.commit()
            conn.close()
        _registrar_auditoria_seguridad(
            "login",
            "fallido",
            usuario_id=usuario["id"] if usuario else None,
            usuario_email=usuario["email"] if usuario else identificador[:120],
            empresa_id=usuario["empresa_id"] if usuario else None,
            detalle={"identificador": identificador[:120], "motivo": "credenciales_invalidas"},
        )

    return render_template('auth/login.html')


@auth_bp.route("/cambiar-empresa", methods=["POST"])
@login_required
def cambiar_empresa():
    """Permite a usuarios globales cambiar el ambito de empresa activo."""
    if not usuario_puede_cambiar_empresa():
        flash("No tienes permisos para cambiar de empresa.", "danger")
        return redirect(request.referrer or url_for("home"))

    try:
        empresa_id = int(request.form.get("empresa_id") or 0)
    except Exception:
        empresa_id = 0
    if empresa_id <= 0:
        flash("Empresa no valida.", "warning")
        return redirect(request.referrer or url_for("home"))

    conn = get_db()
    try:
        empresa = conn.execute(
            "SELECT id, nombre FROM empresas WHERE id=? AND COALESCE(activo, 1)=1",
            (empresa_id,),
        ).fetchone()
        if not empresa:
            flash("Empresa no encontrada o inactiva.", "warning")
            return redirect(request.referrer or url_for("home"))

        anterior = session.get("user_empresa_id")
        session["user_empresa_id"] = int(empresa["id"])
        session["centro_activo_id"] = None
        session.pop("user_centro_id", None)
        session.pop("centro_id", None)
        session.pop("user_centro_bloqueado", None)
        session.pop("user_departamento_id", None)
        session.pop("user_subdepartamento_id", None)
        session.pop("user_empleado_id", None)
        session.pop("user_empleado_rol", None)
        session["user_es_global"] = 1
        _registrar_auditoria_seguridad(
            "cambiar_empresa",
            "ok",
            usuario_id=session.get("user_id"),
            usuario_email=session.get("user_email"),
            empresa_id=empresa_id,
            centro_id=None,
            detalle={"empresa_anterior": anterior, "empresa_nueva": empresa["nombre"]},
            conn=conn,
        )
        conn.commit()
        flash(f"Empresa activa: {empresa['nombre']}.", "success")
    finally:
        conn.close()
    return redirect(request.referrer or url_for("home"))

@auth_bp.route('/usuarios')
@login_required
@permiso_requerido('gestionar_usuarios')
def listar_usuarios():
    """Lista todos los usuarios del sistema"""
    conn = get_db()
    _ensure_auth_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    if _auth_table_exists(conn, "empleados"):
        usuarios = conn.execute("""
            SELECT u.*, r.nombre as rol_nombre,
                   TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) AS empleado_nombre
            FROM usuarios u
            LEFT JOIN roles r ON u.rol_id = r.id
            LEFT JOIN empleados e ON u.empleado_id = e.id AND e.empresa_id = u.empresa_id
            WHERE u.empresa_id = ?
            ORDER BY u.nombre
        """, (empresa_id,)).fetchall()
    else:
        usuarios = conn.execute("""
            SELECT u.*, r.nombre as rol_nombre, NULL AS empleado_nombre
            FROM usuarios u
            LEFT JOIN roles r ON u.rol_id = r.id
            WHERE u.empresa_id = ?
            ORDER BY u.nombre
        """, (empresa_id,)).fetchall()
    
    conn.close()
    return render_template('auth/usuarios.html', usuarios=usuarios)


MODULOS_AUDITORIA_PERMISOS = [
    {
        "grupo": "Direccion",
        "modulo": "Inicio ejecutivo / Informes",
        "permisos": ("acceso_global", "informes_ver", "ver_inicio_ejecutivo"),
        "alcance": "Global",
    },
    {
        "grupo": "Operacion",
        "modulo": "Recepcion",
        "permisos": ("recepcion_ver", "recepcion_gestionar", "recepcion_facturacion"),
        "alcance": "Departamento",
    },
    {
        "grupo": "Operacion",
        "modulo": "Pisos",
        "permisos": ("pisos_ver", "pisos_gestionar"),
        "alcance": "Departamento",
    },
    {
        "grupo": "Operacion",
        "modulo": "Mantenimiento",
        "permisos": ("mantenimiento_ver", "mantenimiento_gestionar"),
        "alcance": "Departamento",
    },
    {
        "grupo": "Cocina",
        "modulo": "Produccion",
        "permisos": ("produccion_ver", "produccion_editar"),
        "alcance": "Departamento / subdepartamento",
    },
    {
        "grupo": "Cocina",
        "modulo": "APPCC y lotes",
        "permisos": ("appcc_ver", "appcc_gestionar"),
        "alcance": "Departamento / trazabilidad",
    },
    {
        "grupo": "Compras",
        "modulo": "Compras y recepcion",
        "permisos": ("compras_solicitar", "compras_recepcionar", "compras_gestionar"),
        "alcance": "Departamento / economato",
    },
    {
        "grupo": "Compras",
        "modulo": "Negociacion y precios",
        "permisos": ("compras_negociar", "compras_validar_economico", "modificar_precios"),
        "alcance": "Compras / contabilidad",
    },
    {
        "grupo": "Stock",
        "modulo": "Productos y proveedores",
        "permisos": ("productos_ver", "productos_gestionar", "proveedores_ver", "proveedores_gestionar"),
        "alcance": "Compras / jefatura",
    },
    {
        "grupo": "Venta",
        "modulo": "TPV",
        "permisos": ("tpv_ver", "tpv_gestionar"),
        "alcance": "Punto de venta",
    },
    {
        "grupo": "Gestion",
        "modulo": "Contabilidad",
        "permisos": ("contabilidad_ver", "contabilidad_gestionar"),
        "alcance": "Global / administracion",
    },
    {
        "grupo": "Personas",
        "modulo": "RRHH",
        "permisos": ("rrhh_ver", "rrhh_gestionar", "ver_rrhh"),
        "alcance": "RRHH / direccion",
    },
    {
        "grupo": "Cliente",
        "modulo": "Experiencia y quejas",
        "permisos": ("experiencia_ver", "experiencia_gestionar", "recepcion_gestionar"),
        "alcance": "Recepcion / RRPP",
    },
    {
        "grupo": "Cliente",
        "modulo": "Animacion",
        "permisos": ("animacion_ver", "animacion_gestionar"),
        "alcance": "Departamento",
    },
    {
        "grupo": "IA",
        "modulo": "Asistente IA 360",
        "permisos": ("asistente_ver", "informes_ver", "acceso_global"),
        "alcance": "Segun datos autorizados",
    },
    {
        "grupo": "Sistema",
        "modulo": "Usuarios y estructura",
        "permisos": ("gestionar_usuarios", "gestionar_empresas", "gestionar_estructura", "acceso_global"),
        "alcance": "Administracion",
    },
]


@auth_bp.route('/permisos/auditoria')
@login_required
@permiso_requerido('gestionar_usuarios')
def auditoria_permisos():
    conn = get_db()
    _ensure_auth_schema(conn)
    roles = conn.execute("SELECT id, nombre FROM roles ORDER BY nombre").fetchall()
    permisos_rows = conn.execute("SELECT id, nombre, descripcion FROM permisos ORDER BY nombre").fetchall()
    permisos_por_id = {p["id"]: p["nombre"] for p in permisos_rows}
    roles_permisos = conn.execute("SELECT rol_id, permiso_id FROM roles_permisos").fetchall()
    permisos_db_por_rol = {}
    for row in roles_permisos:
        permisos_db_por_rol.setdefault(row["rol_id"], set()).add(permisos_por_id.get(row["permiso_id"]))

    matriz = []
    for rol in roles:
        rol_key = normalizar_rol(rol["nombre"])
        permisos_base = set(PERMISOS_ROL_BASE.get(rol_key, set()))
        permisos_db = {p for p in permisos_db_por_rol.get(rol["id"], set()) if p}
        permisos_efectivos = permisos_base | permisos_db
        es_global = rol_key in ROLES_GLOBALES_OPERACION or "acceso_global" in permisos_efectivos
        modulos = []
        for modulo in MODULOS_AUDITORIA_PERMISOS:
            concedidos = [p for p in modulo["permisos"] if p in permisos_efectivos or es_global]
            modulos.append({
                **modulo,
                "permitido": bool(concedidos),
                "concedidos": concedidos,
            })
        matriz.append({
            "rol": rol,
            "rol_key": rol_key,
            "clave_auditoria": _clave_auditoria_rol(rol["nombre"]),
            "es_global": es_global,
            "permisos_total": len(permisos_efectivos),
            "permisos_base": sorted(permisos_base),
            "permisos_db": sorted(permisos_db),
            "modulos": modulos,
        })

    usuarios_por_rol = {
        row["rol_id"]: row["total"]
        for row in conn.execute("""
            SELECT rol_id, COUNT(*) AS total
            FROM usuarios
            WHERE empresa_id = ?
            GROUP BY rol_id
        """, (session.get('user_empresa_id', 1),)).fetchall()
    }
    duplicados_por_clave = {}
    for item in matriz:
        duplicados_por_clave.setdefault(item["clave_auditoria"], []).append(item)
    duplicados_roles = [
        {
            "clave": clave,
            "roles": [
                {
                    "id": item["rol"]["id"],
                    "nombre": item["rol"]["nombre"],
                    "usuarios": usuarios_por_rol.get(item["rol"]["id"], 0),
                    "permisos": item["permisos_total"],
                }
                for item in items
            ],
        }
        for clave, items in sorted(duplicados_por_clave.items())
        if len(items) > 1
    ]
    roles_legacy = []
    roles_sin_usuarios = []
    roles_sin_permisos = []
    for item in matriz:
        usuarios_total = usuarios_por_rol.get(item["rol"]["id"], 0)
        legacy = _legacy_auditoria_rol(item["rol"]["nombre"])
        if legacy:
            roles_legacy.append({
                "id": item["rol"]["id"],
                "nombre": item["rol"]["nombre"],
                "usuarios": usuarios_total,
                "permisos": item["permisos_total"],
                **legacy,
            })
        if usuarios_total == 0:
            roles_sin_usuarios.append({
                "id": item["rol"]["id"],
                "nombre": item["rol"]["nombre"],
                "permisos": item["permisos_total"],
                "legacy": bool(legacy),
            })
        if item["permisos_total"] == 0:
            roles_sin_permisos.append({
                "id": item["rol"]["id"],
                "nombre": item["rol"]["nombre"],
                "usuarios": usuarios_total,
            })

    usuarios = conn.execute("""
        SELECT u.id, u.nombre, u.email, COALESCE(r.nombre, '') AS rol_nombre,
               COALESCE(u.es_global, 0) AS es_global,
               COALESCE(u.activo, 1) AS activo,
               COALESCE(u.puede_ver_precios, 0) AS puede_ver_precios,
               COALESCE(u.puede_modificar_precios, 0) AS puede_modificar_precios,
               COALESCE(u.puede_validar_recepcion, 0) AS puede_validar_recepcion,
               COALESCE(u.puede_validar_economicamente, 0) AS puede_validar_economicamente,
               COALESCE(ct.nombre, 'Empresa completa') AS centro_nombre,
               COALESCE(e.nombre || ' ' || COALESCE(e.apellidos, ''), '') AS empleado_nombre
        FROM usuarios u
        LEFT JOIN roles r ON r.id = u.rol_id
        LEFT JOIN empleados e ON e.usuario_id = u.id OR e.id = u.empleado_id
        LEFT JOIN centros_trabajo ct ON ct.id = e.centro_id
        WHERE u.empresa_id = ?
        GROUP BY u.id
        ORDER BY ct.nombre, r.nombre, u.nombre
    """, (session.get('user_empresa_id', 1),)).fetchall()
    resumen = {
        "roles": len(matriz),
        "permisos": len(permisos_rows),
        "usuarios": len(usuarios),
        "duplicados": len(duplicados_roles),
        "legacy": len(roles_legacy),
        "sin_usuarios": len(roles_sin_usuarios),
        "sin_permisos": len(roles_sin_permisos),
    }
    conn.close()
    return render_template(
        'auth/auditoria_permisos.html',
        matriz=matriz,
        modulos=MODULOS_AUDITORIA_PERMISOS,
        usuarios=usuarios,
        resumen=resumen,
        duplicados_roles=duplicados_roles,
        roles_legacy=roles_legacy,
        roles_sin_usuarios=roles_sin_usuarios,
        roles_sin_permisos=roles_sin_permisos,
    )


@auth_bp.route('/permisos/auditoria/normalizar-legacy', methods=['POST'])
@login_required
@permiso_requerido('gestionar_usuarios')
def normalizar_roles_legacy():
    conn = get_db()
    _ensure_auth_schema(conn)
    roles = conn.execute("SELECT id, nombre FROM roles").fetchall()
    roles_por_clave = {_clave_auditoria_rol(row["nombre"]): row for row in roles}
    migrados = 0
    omitidos = []
    for rol in roles:
        legacy = _legacy_auditoria_rol(rol["nombre"])
        if not legacy or not legacy.get("migrable"):
            continue
        destino = roles_por_clave.get(_clave_auditoria_rol(legacy["sugerido"]))
        if not destino:
            omitidos.append(f"{rol['nombre']} -> {legacy['sugerido']}")
            continue
        cur = conn.execute(
            "UPDATE usuarios SET rol_id = ? WHERE rol_id = ?",
            (destino["id"], rol["id"]),
        )
        migrados += cur.rowcount or 0
    conn.commit()
    conn.close()
    if migrados:
        flash(f"Roles legacy normalizados en {migrados} usuario(s). Los roles antiguos se mantienen para auditoria, pero sin usuarios.", "success")
    else:
        flash("No habia usuarios usando roles legacy migrables.", "info")
    if omitidos:
        flash("No se migraron algunos roles porque falta el rol destino: " + ", ".join(omitidos), "warning")
    return redirect(url_for('auth.auditoria_permisos'))

@auth_bp.route('/perfil')
@login_required
def perfil():
    """Muestra el perfil del usuario actual"""
    conn = get_db()
    _ensure_auth_schema(conn)
    usuario_id = session.get('user_id')
    
    usuario = conn.execute("""
        SELECT u.*, r.nombre as rol_nombre
        FROM usuarios u
        LEFT JOIN roles r ON u.rol_id = r.id
        WHERE u.id = ?
    """, (usuario_id,)).fetchone()
    
    conn.close()
    
    return render_template('auth/perfil.html', usuario=usuario)


@auth_bp.route('/perfil/cambiar-password', methods=['GET', 'POST'])
@login_required
def cambiar_password():
    conn = get_db()
    _ensure_auth_schema(conn)
    conn.close()
    destino = (request.form.get('next') if request.method == 'POST' else request.args.get('next')) or ''

    def _redirect_formulario():
        if destino == 'portal_empleado':
            return redirect(url_for('auth.cambiar_password', next='portal_empleado'))
        return redirect(url_for('auth.cambiar_password'))

    if request.method == 'POST':
        actual = request.form.get('password_actual') or ''
        nueva = request.form.get('password_nueva') or ''
        confirmar = request.form.get('confirm_password') or ''
        if nueva != confirmar:
            flash('La nueva contraseña no coincide.', 'warning')
            return _redirect_formulario()
        if len(nueva) < 12:
            flash('La nueva contrasena debe tener al menos 12 caracteres.', 'warning')
            return _redirect_formulario()
        conn = get_db()
        _ensure_auth_schema(conn)
        usuario = conn.execute(
            "SELECT id, password_hash FROM usuarios WHERE id = ?",
            (session.get('user_id'),),
        ).fetchone()
        if not usuario or not check_password_hash(usuario['password_hash'], actual):
            conn.close()
            flash('La contraseña actual no es correcta.', 'danger')
            return _redirect_formulario()
        conn.execute(
            "UPDATE usuarios SET password_hash = ?, debe_cambiar_password = 0 WHERE id = ?",
            (generate_password_hash(nueva), session.get('user_id')),
        )
        _registrar_auditoria_seguridad(
            "cambio_password",
            "ok",
            usuario_id=session.get("user_id"),
            usuario_email=session.get("user_email"),
            empresa_id=session.get("user_empresa_id"),
            centro_id=session.get("centro_activo_id"),
            detalle={"motivo": "cambio_usuario"},
            conn=conn,
        )
        conn.commit()
        conn.close()
        session['debe_cambiar_password'] = 0
        flash('Contraseña actualizada correctamente.', 'success')
        if destino == 'portal_empleado':
            return redirect(url_for('rrhh.portal_empleado'))
        return redirect(url_for('auth.perfil'))
    return render_template('auth/cambiar_password.html', next_destino=destino)

@auth_bp.route('/logout')
def logout():
    """Cierra la sesión del usuario"""
    if session.get("user_id"):
        _registrar_auditoria_seguridad(
            "logout",
            "ok",
            usuario_id=session.get("user_id"),
            usuario_email=session.get("user_email"),
            empresa_id=session.get("user_empresa_id"),
            centro_id=session.get("centro_activo_id"),
        )
    session.clear()
    flash(' Sesión cerrada correctamente', 'success')
    return redirect(url_for('auth.login'))

# ============================================
# NUEVO USUARIO
# ============================================
@auth_bp.route('/usuarios/nuevo', methods=['GET', 'POST'])
@login_required
@permiso_requerido('gestionar_usuarios')
def nuevo_usuario():
    """Crea un nuevo usuario"""
    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        rol_id = request.form.get('rol_id')
        codigo_form = request.form.get('codigo_acceso')
        empleado_id = request.form.get('empleado_id') or None
        flags = _usuario_flags_desde_form()
        empresa_id = session.get('user_empresa_id', 1)

        if not nombre or not email or '@' not in email or not rol_id:
            flash('Nombre, email valido y rol son obligatorios.', 'danger')
            return redirect(url_for('auth.nuevo_usuario'))
        if len(password) < 12:
            flash('La contrasena temporal debe tener al menos 12 caracteres.', 'danger')
            return redirect(url_for('auth.nuevo_usuario'))
        
        from werkzeug.security import generate_password_hash
        password_hash = generate_password_hash(password)
        
        conn = get_db()
        _ensure_auth_schema(conn)
        try:
            codigo_acceso = _codigo_acceso_para_guardar(conn, empresa_id, codigo_form)
            conn.execute("""
                INSERT INTO usuarios (
                    nombre, email, password_hash, rol_id, empresa_id, debe_cambiar_password,
                    es_global, puede_ver_precios, puede_modificar_precios,
                    puede_validar_recepcion, puede_validar_economicamente
                )
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """, (
                nombre, email, password_hash, rol_id, empresa_id,
                flags["es_global"],
                flags["puede_ver_precios"],
                flags["puede_modificar_precios"],
                flags["puede_validar_recepcion"],
                flags["puede_validar_economicamente"],
            ))
            nuevo_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()['id']
            if not codigo_acceso:
                codigo_acceso = _codigo_acceso_para_guardar(conn, empresa_id, None, nuevo_id)
            conn.execute(
                "UPDATE usuarios SET codigo_acceso = ? WHERE id = ? AND empresa_id = ?",
                (codigo_acceso, nuevo_id, empresa_id),
            )
            _vincular_usuario_empleado_desde_auth(conn, nuevo_id, empleado_id, empresa_id)
            conn.commit()
            flash(' Usuario creado correctamente', 'success')
        except ValueError as e:
            conn.rollback()
            flash(str(e), 'danger')
        except Exception as e:
            conn.rollback()
            flash(f' Error al crear usuario: {str(e)}', 'danger')
        finally:
            conn.close()
        
        return redirect(url_for('auth.listar_usuarios'))
    
    # GET: mostrar formulario
    conn = get_db()
    _ensure_auth_schema(conn)
    roles = conn.execute("SELECT id, nombre FROM roles ORDER BY nombre").fetchall()
    empleados = _empleados_para_usuario_select(conn, session.get('user_empresa_id', 1))
    conn.close()
    
    return render_template('auth/nuevo_usuario.html', roles=roles, empleados=empleados)

# ============================================
# EDITAR USUARIO
# ============================================
@auth_bp.route('/usuarios/<int:usuario_id>/editar', methods=['GET', 'POST'])
@login_required
@permiso_requerido('gestionar_usuarios')
def editar_usuario(usuario_id):
    """Edita un usuario existente"""
    conn = get_db()
    _ensure_auth_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    
    # Verificar que el usuario pertenece a la empresa
    usuario = conn.execute("""
        SELECT * FROM usuarios 
        WHERE id = ? AND empresa_id = ?
    """, (usuario_id, empresa_id)).fetchone()
    
    if not usuario:
        flash(' Usuario no encontrado', 'danger')
        return redirect(url_for('auth.listar_usuarios'))
    
    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        email = request.form.get('email', '').strip()
        rol_id = request.form.get('rol_id')
        activo = 1 if request.form.get('activo') else 0
        empleado_id = request.form.get('empleado_id') or None
        flags = _usuario_flags_desde_form()
        nueva_password = request.form.get('password') or ''
        if not nombre or not email or '@' not in email or not rol_id:
            conn.close()
            flash('Nombre, email valido y rol son obligatorios.', 'danger')
            return redirect(url_for('auth.editar_usuario', usuario_id=usuario_id))
        if nueva_password and len(nueva_password) < 12:
            conn.close()
            flash('La contrasena temporal debe tener al menos 12 caracteres.', 'danger')
            return redirect(url_for('auth.editar_usuario', usuario_id=usuario_id))
        try:
            codigo_acceso = _codigo_acceso_para_guardar(
                conn,
                empresa_id,
                request.form.get('codigo_acceso'),
                usuario_id,
            )
            conn.execute("""
                UPDATE usuarios
                SET nombre = ?, email = ?, rol_id = ?, activo = ?, codigo_acceso = ?,
                    es_global = ?, puede_ver_precios = ?, puede_modificar_precios = ?,
                    puede_validar_recepcion = ?, puede_validar_economicamente = ?
                WHERE id = ? AND empresa_id = ?
            """, (
                nombre, email, rol_id, activo, codigo_acceso,
                flags["es_global"],
                flags["puede_ver_precios"],
                flags["puede_modificar_precios"],
                flags["puede_validar_recepcion"],
                flags["puede_validar_economicamente"],
                usuario_id, empresa_id,
            ))
            if nueva_password:
                password_hash = generate_password_hash(nueva_password)
                conn.execute(
                    "UPDATE usuarios SET password_hash = ?, debe_cambiar_password = 1 WHERE id = ? AND empresa_id = ?",
                    (password_hash, usuario_id, empresa_id),
                )
            _vincular_usuario_empleado_desde_auth(conn, usuario_id, empleado_id, empresa_id)
            conn.commit()
            conn.close()
            flash(' Usuario actualizado correctamente', 'success')
            return redirect(url_for('auth.listar_usuarios'))
        except ValueError as e:
            conn.rollback()
            flash(str(e), 'danger')
        except Exception as e:
            conn.rollback()
            flash(f' Error al actualizar usuario: {str(e)}', 'danger')
    
    # GET: mostrar formulario
    roles = conn.execute("SELECT id, nombre FROM roles ORDER BY nombre").fetchall()
    empleados = _empleados_para_usuario_select(conn, empresa_id)
    conn.close()
    
    return render_template('auth/editar_usuario.html', usuario=usuario, roles=roles, empleados=empleados)

# ============================================
# ELIMINAR USUARIO
# ============================================
@auth_bp.route('/usuarios/<int:usuario_id>/eliminar', methods=['POST'])
@login_required
@permiso_requerido('gestionar_usuarios')
def eliminar_usuario(usuario_id):
    """Elimina un usuario (solo si no es el propio usuario)"""
    conn = get_db()
    _ensure_auth_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    
    # No permitir eliminar el propio usuario
    if usuario_id == session.get('user_id'):
        flash(' No puedes eliminar tu propio usuario', 'danger')
        conn.close()
        return redirect(url_for('auth.listar_usuarios'))
    
    # Verificar que el usuario existe y pertenece a la empresa
    usuario = conn.execute("""
        SELECT * FROM usuarios 
        WHERE id = ? AND empresa_id = ?
    """, (usuario_id, empresa_id)).fetchone()
    
    if not usuario:
        flash(' Usuario no encontrado', 'danger')
        conn.close()
        return redirect(url_for('auth.listar_usuarios'))
    
    if _auth_table_exists(conn, "empleados") and _auth_column_exists(conn, "empleados", "usuario_id"):
        conn.execute(
            "UPDATE empleados SET usuario_id = NULL WHERE empresa_id = ? AND usuario_id = ?",
            (empresa_id, usuario_id),
        )
    conn.execute("DELETE FROM usuarios WHERE id = ? AND empresa_id = ?", (usuario_id, empresa_id))
    conn.commit()
    conn.close()
    
    flash(' Usuario eliminado correctamente', 'success')
    return redirect(url_for('auth.listar_usuarios'))
    
    conn.commit()
    conn.close()

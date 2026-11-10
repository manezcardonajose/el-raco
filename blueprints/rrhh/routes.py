from datetime import datetime, date, timedelta
import csv
import hashlib
import io
import math
import secrets
import string
from pathlib import Path
from functools import wraps

from flask import render_template, request, redirect, url_for, session, flash, send_file, abort, current_app
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from database import get_db
from . import rrhh_bp
from blueprints.auth.routes import (
    MODULOS_AUDITORIA_PERMISOS,
    PERMISOS_ROL_BASE,
    _ensure_auth_schema,
    login_required,
    normalizar_rol,
    usuario_tiene_permiso,
)
from helpers.email_helper import enviar_email
from helpers.centros_helper import (
    asignar_centro_desde_departamento,
    centro_activo_id,
    ids_departamentos_centro,
    ids_puntos_venta_centro,
)


ROOT = Path(__file__).resolve().parents[2]


# ============================================================
# RRHH — utilidades internas
# ============================================================

ROLES_GLOBALES_RRHH = {
    "admin",
    "direccion",
    "dirección",
    "gerente",
    "rrhh",
    "recursos_humanos",
}


def _normalizar_rol(valor):
    return (valor or "").strip().lower()


def _grupo_rol_por_permisos(nombre, permisos):
    rol = normalizar_rol(nombre)
    permisos = set(permisos or [])
    if "acceso_global" in permisos or rol in {"admin", "administrador", "gerente", "director", "direccion", "direccion_general", "director_hotel", "subdirector", "propietario"}:
        return "Direccion y administracion"
    if permisos & {"rrhh_ver", "rrhh_gestionar", "ver_rrhh", "gestionar_empleados", "gestionar_usuarios"}:
        return "RRHH y permisos"
    if permisos & {"compras_solicitar", "compras_recepcionar", "compras_gestionar", "compras_negociar", "productos_ver", "productos_gestionar", "proveedores_ver"}:
        return "Compras, stock y proveedores"
    if permisos & {"produccion_ver", "produccion_editar", "appcc_ver", "appcc_gestionar"}:
        return "Recetas, produccion y APPCC"
    if permisos & {"tpv_ver", "tpv_gestionar"} or rol in {"jefe_bares", "maitre", "metre", "jefe_rango", "camarero"}:
        return "TPV, sala y bares"
    if permisos & {"recepcion_ver", "recepcion_gestionar", "recepcion_facturacion"}:
        return "Recepcion"
    if permisos & {"pisos_ver", "pisos_gestionar"}:
        return "Pisos"
    if permisos & {"mantenimiento_ver", "mantenimiento_gestionar"}:
        return "Mantenimiento"
    if permisos & {"experiencia_ver", "experiencia_gestionar", "animacion_ver", "animacion_gestionar"}:
        return "Cliente, experiencia y animacion"
    if permisos & {"contabilidad_ver", "contabilidad_gestionar", "informes_ver"}:
        return "Contabilidad e informes"
    return "Otros roles"


def _empresa_id():
    return session.get("user_empresa_id", 1) or 1


def _usuario_es_global_rrhh():
    rol = _normalizar_rol(session.get("user_rol"))
    if rol in ROLES_GLOBALES_RRHH:
        return True
    if session.get("user_es_global") in (1, True, "1", "true", "True"):
        return True
    return False


def _session_departamento_id():
    return session.get("user_departamento_id") or session.get("departamento_id")


def _ip_cliente_hash():
    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
    if not ip:
        return ""
    semilla = current_app.config.get("SECRET_KEY") or "el-raco-local"
    return hashlib.sha256(f"{semilla}:rrhh:{ip}".encode("utf-8", errors="ignore")).hexdigest()


def _table_exists(conn, table_name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(conn, table_name, column_name):
    try:
        cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:
        return False
    return any(c[1] == column_name for c in cols)


def _add_column_if_missing(conn, table_name, column_sql):
    column_name = column_sql.strip().split()[0]
    if _table_exists(conn, table_name) and not _column_exists(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def ensure_rrhh_schema(conn):
    """
    Garantiza que la fase RRHH pueda arrancar sin borrar datos existentes.
    Solo crea tablas/columnas faltantes.
    """
    # Tabla de historial para trazabilidad total
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rrhh_historial_cambios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tabla TEXT NOT NULL,
            registro_id INTEGER NOT NULL,
            accion TEXT NOT NULL,
            motivo TEXT,
            usuario_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            fecha TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rrhh_portal_accesos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER,
            usuario_id INTEGER,
            accion TEXT NOT NULL,
            detalle TEXT,
            ip TEXT,
            ip_hash TEXT,
            datos_minimizados INTEGER DEFAULT 1,
            user_agent TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for col in [
        "empleado_id INTEGER",
        "usuario_id INTEGER",
        "accion TEXT",
        "detalle TEXT",
        "ip TEXT",
        "ip_hash TEXT",
        "datos_minimizados INTEGER DEFAULT 1",
        "user_agent TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "fecha TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "rrhh_portal_accesos", col)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rrhh_portal_aceptaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER NOT NULL,
            usuario_id INTEGER,
            tipo TEXT NOT NULL,
            version TEXT NOT NULL,
            texto TEXT,
            hash_aceptacion TEXT,
            ip TEXT,
            ip_hash TEXT,
            datos_minimizados INTEGER DEFAULT 1,
            user_agent TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for col in [
        "empleado_id INTEGER",
        "usuario_id INTEGER",
        "tipo TEXT",
        "version TEXT",
        "texto TEXT",
        "hash_aceptacion TEXT",
        "ip TEXT",
        "ip_hash TEXT",
        "datos_minimizados INTEGER DEFAULT 1",
        "user_agent TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "fecha TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "rrhh_portal_aceptaciones", col)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prl_riesgos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT,
            area TEXT NOT NULL,
            puesto TEXT,
            riesgo TEXT NOT NULL,
            categoria TEXT,
            probabilidad INTEGER DEFAULT 2,
            severidad INTEGER DEFAULT 2,
            nivel TEXT,
            medidas TEXT,
            epis TEXT,
            formacion_requerida TEXT,
            responsable TEXT,
            frecuencia_revision TEXT DEFAULT 'mensual',
            fuente TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            activo INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(codigo, empresa_id, centro_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prl_acciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            riesgo_id INTEGER,
            titulo TEXT NOT NULL,
            descripcion TEXT,
            prioridad TEXT DEFAULT 'media',
            estado TEXT DEFAULT 'pendiente',
            responsable TEXT,
            fecha_limite TEXT,
            evidencia TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prl_formaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT,
            titulo TEXT NOT NULL,
            area TEXT,
            duracion_minutos INTEGER DEFAULT 30,
            periodicidad TEXT DEFAULT 'anual',
            contenido TEXT,
            evaluacion TEXT,
            obligatoria INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            activo INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(codigo, empresa_id, centro_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prl_formacion_empleados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            formacion_id INTEGER NOT NULL,
            empleado_id INTEGER NOT NULL,
            estado TEXT DEFAULT 'pendiente',
            fecha_asignacion TEXT DEFAULT CURRENT_TIMESTAMP,
            fecha_realizacion TEXT,
            nota REAL,
            evidencia TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            UNIQUE(formacion_id, empleado_id, empresa_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prl_incidentes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT DEFAULT CURRENT_TIMESTAMP,
            empleado_id INTEGER,
            area TEXT,
            tipo TEXT,
            descripcion TEXT NOT NULL,
            lesion TEXT,
            gravedad TEXT DEFAULT 'leve',
            causa_probable TEXT,
            accion_correctiva TEXT,
            estado TEXT DEFAULT 'abierto',
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)
    _sembrar_prl_base(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS categorias_laborales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            salario_base REAL DEFAULT 0,
            convenio TEXT,
            empresa_id INTEGER DEFAULT 1,
            activo INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _add_column_if_missing(conn, "categorias_laborales", "salario_base REAL DEFAULT 0")
    _add_column_if_missing(conn, "categorias_laborales", "convenio TEXT")
    _add_column_if_missing(conn, "categorias_laborales", "activo INTEGER DEFAULT 1")
    _add_column_if_missing(conn, "categorias_laborales", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS empleados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            apellidos TEXT,
            nif TEXT,
            email TEXT,
            telefono TEXT,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            categoria_id INTEGER,
            rol TEXT,
            fecha_alta TEXT,
            fecha_baja TEXT,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            activo INTEGER DEFAULT 1
        )
        """
    )
    for col in [
        "apellidos TEXT",
        "nif TEXT",
        "email TEXT",
        "telefono TEXT",
        "centro_id INTEGER",
        "categoria_id INTEGER",
        "fecha_alta TEXT",
        "fecha_baja TEXT",
        "observaciones TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "subdepartamento_id INTEGER",
        "punto_venta_id INTEGER",
        "rol TEXT",
        "usuario_id INTEGER",
        "activo INTEGER DEFAULT 1",
        # Datos personales / laborales reales
        "numero_seguridad_social TEXT",
        "fecha_nacimiento TEXT",
        "nacionalidad TEXT",
        "estado_civil TEXT",
        "direccion TEXT",
        "codigo_postal TEXT",
        "localidad TEXT",
        "provincia TEXT",
        "pais TEXT DEFAULT 'España'",
        "iban TEXT",
        "contacto_emergencia_nombre TEXT",
        "contacto_emergencia_telefono TEXT",
        "contacto_emergencia_relacion TEXT",
        "nivel_formacion TEXT",
        "talla_uniforme TEXT",
        "talla_calzado TEXT",
        "alergias_observaciones TEXT",
        "carnet_manipulador INTEGER DEFAULT 0",
        "fecha_caducidad_carnet TEXT",
        "prl_realizada INTEGER DEFAULT 0",
        "fecha_prl TEXT",
        "reconocimiento_medico INTEGER DEFAULT 0",
        "fecha_reconocimiento TEXT",
        "pin_fichaje_hash TEXT",
        "pin_fichaje_updated_at TEXT",
        "irpf_pct REAL DEFAULT 0",
        "modelo145_situacion_familiar TEXT",
        "modelo145_conyuge_nif TEXT",
        "modelo145_discapacidad_pct REAL DEFAULT 0",
        "modelo145_movilidad_geografica INTEGER DEFAULT 0",
        "modelo145_hijos INTEGER DEFAULT 0",
        "modelo145_hijos_menores3 INTEGER DEFAULT 0",
        "modelo145_ascendientes INTEGER DEFAULT 0",
        "modelo145_pension_compensatoria REAL DEFAULT 0",
        "modelo145_anualidades_alimentos REAL DEFAULT 0",
        "modelo145_vivienda_habitual INTEGER DEFAULT 0",
        "modelo145_fecha_firma TEXT",
        "modelo145_fecha_actualizacion TEXT",
        "modelo145_observaciones TEXT",
        "tipo_empleado TEXT DEFAULT 'real'",
        "origen_importacion TEXT",
        "codigo_importacion TEXT",
        "motivo_cambio TEXT",
    ]:
        _add_column_if_missing(conn, "empleados", col)

    if _table_exists(conn, "usuarios"):
        _add_column_if_missing(conn, "usuarios", "empleado_id INTEGER")
        _add_column_if_missing(conn, "usuarios", "codigo_acceso TEXT")
        _add_column_if_missing(conn, "usuarios", "debe_cambiar_password INTEGER DEFAULT 0")
        conn.execute("""
            UPDATE usuarios
            SET codigo_acceso = 'U' || printf('%05d', id)
            WHERE codigo_acceso IS NULL OR TRIM(codigo_acceso) = ''
        """)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contratos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER NOT NULL,
            tipo_contrato TEXT,
            fecha_inicio TEXT,
            fecha_fin TEXT,
            jornada TEXT,
            horas_semanales REAL DEFAULT 0,
            salario_bruto REAL DEFAULT 0,
            categoria_id INTEGER,
            estado TEXT DEFAULT 'activo',
            archivo TEXT,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "tipo_contrato TEXT",
        "fecha_inicio TEXT",
        "fecha_fin TEXT",
        "jornada TEXT",
        "horas_semanales REAL DEFAULT 0",
        "salario_bruto REAL DEFAULT 0",
        "categoria_id INTEGER",
        "estado TEXT DEFAULT 'activo'",
        "archivo TEXT",
        "observaciones TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        # Datos ampliados de contrato
        "centro_trabajo TEXT",
        "puesto_trabajo TEXT",
        "grupo_profesional TEXT",
        "convenio TEXT",
        "tipo_jornada TEXT",
        "turno TEXT",
        "dias_trabajo TEXT",
        "periodo_prueba TEXT",
        "salario_bruto_anual REAL DEFAULT 0",
        "salario_bruto_mensual REAL DEFAULT 0",
        "pagas INTEGER DEFAULT 14",
        "vacaciones_dias REAL DEFAULT 30",
        "fecha_preaviso TEXT",
        "motivo_fin TEXT",
        "firmado_empleado INTEGER DEFAULT 0",
        "firma_empleado_at TEXT",
        "firma_empleado_ip TEXT",
        "firma_empleado_user_agent TEXT",
        "firma_empleado_hash TEXT",
    ]:
        _add_column_if_missing(conn, "contratos", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nominas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER NOT NULL,
            mes INTEGER NOT NULL,
            anio INTEGER NOT NULL,
            bruto REAL DEFAULT 0,
            neto REAL DEFAULT 0,
            coste_empresa REAL DEFAULT 0,
            estado TEXT DEFAULT 'pendiente',
            archivo TEXT,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "bruto REAL DEFAULT 0",
        "coste_empresa REAL DEFAULT 0",
        "estado TEXT DEFAULT 'pendiente'",
        "archivo TEXT",
        "observaciones TEXT",
        "periodo_desde TEXT",
        "periodo_hasta TEXT",
        "salario_base REAL DEFAULT 0",
        "complementos REAL DEFAULT 0",
        "horas_extra REAL DEFAULT 0",
        "pluses REAL DEFAULT 0",
        "prorrata_pagas REAL DEFAULT 0",
        "irpf REAL DEFAULT 0",
        "seguridad_social_trabajador REAL DEFAULT 0",
        "seguridad_social_empresa REAL DEFAULT 0",
        "anticipos_embargos REAL DEFAULT 0",
        "fecha_pago TEXT",
        "forma_pago TEXT",
        "visible_empleado INTEGER DEFAULT 1",
    ]:
        _add_column_if_missing(conn, "nominas", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fichajes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            fecha_hora TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            origen TEXT DEFAULT 'manual',
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            usuario_id INTEGER
        )
        """
    )
    for col in [
        "tipo TEXT",
        "fecha_hora TEXT DEFAULT CURRENT_TIMESTAMP",
        "origen TEXT DEFAULT 'manual'",
        "observaciones TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "usuario_id INTEGER",
        "ip_origen TEXT",
        "ip_hash TEXT",
        "user_agent TEXT",
        "hash_registro TEXT",
        "fichaje_origen_id INTEGER",
        "corregido_por INTEGER",
        "fecha_correccion TEXT",
        "motivo_correccion TEXT",
        "ip_correccion TEXT",
    ]:
        _add_column_if_missing(conn, "fichajes", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS solicitudes_correccion_fichaje (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            tipo TEXT NOT NULL,
            fecha_hora_solicitada TEXT NOT NULL,
            motivo TEXT,
            estado TEXT DEFAULT 'pendiente',
            respuesta_rrhh TEXT,
            fichaje_id INTEGER,
            solicitado_por INTEGER,
            resuelto_por INTEGER,
            fecha_solicitud TEXT DEFAULT CURRENT_TIMESTAMP,
            fecha_resolucion TEXT,
            empresa_id INTEGER DEFAULT 1
        )
        """
    )
    for col in [
        "empleado_id INTEGER",
        "fecha TEXT",
        "tipo TEXT",
        "fecha_hora_solicitada TEXT",
        "motivo TEXT",
        "estado TEXT DEFAULT 'pendiente'",
        "respuesta_rrhh TEXT",
        "fichaje_id INTEGER",
        "solicitado_por INTEGER",
        "resuelto_por INTEGER",
        "fecha_solicitud TEXT DEFAULT CURRENT_TIMESTAMP",
        "fecha_resolucion TEXT",
        "empresa_id INTEGER DEFAULT 1",
    ]:
        _add_column_if_missing(conn, "solicitudes_correccion_fichaje", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cierres_jornada_rrhh (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anio INTEGER NOT NULL,
            mes INTEGER NOT NULL,
            periodo_desde TEXT NOT NULL,
            periodo_hasta TEXT NOT NULL,
            estado TEXT DEFAULT 'cerrado',
            cerrado_por INTEGER,
            fecha_cierre TEXT DEFAULT CURRENT_TIMESTAMP,
            reabierto_por INTEGER,
            fecha_reapertura TEXT,
            observaciones TEXT,
            hash_cierre TEXT,
            empresa_id INTEGER DEFAULT 1,
            UNIQUE(empresa_id, anio, mes)
        )
        """
    )
    for col in [
        "anio INTEGER",
        "mes INTEGER",
        "periodo_desde TEXT",
        "periodo_hasta TEXT",
        "estado TEXT DEFAULT 'cerrado'",
        "cerrado_por INTEGER",
        "fecha_cierre TEXT DEFAULT CURRENT_TIMESTAMP",
        "reabierto_por INTEGER",
        "fecha_reapertura TEXT",
        "observaciones TEXT",
        "hash_cierre TEXT",
        "empresa_id INTEGER DEFAULT 1",
    ]:
        _add_column_if_missing(conn, "cierres_jornada_rrhh", col)

    conn.execute("CREATE TABLE IF NOT EXISTS permisos (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT UNIQUE, descripcion TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS roles (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT UNIQUE)")
    conn.execute("CREATE TABLE IF NOT EXISTS roles_permisos (rol_id INTEGER, permiso_id INTEGER)")
    permisos_base = [
        ("ver_rrhh", "Acceso al módulo RRHH"),
        ("gestionar_empleados", "Crear y editar empleados"),
        ("ver_contratos", "Ver contratos"),
        ("gestionar_contratos", "Crear y editar contratos"),
        ("ver_nominas", "Ver nóminas"),
        ("gestionar_nominas", "Crear y editar nóminas"),
        ("ver_fichajes", "Ver fichajes"),
        ("gestionar_fichajes", "Registrar y corregir fichajes"),
        ("acceso_global", "Ver todos los departamentos"),
    ]
    for nombre, descripcion in permisos_base:
        conn.execute("INSERT OR IGNORE INTO permisos (nombre, descripcion) VALUES (?, ?)", (nombre, descripcion))
    for rol in ["admin", "direccion", "rrhh", "jefe_departamento", "segundo_jefe", "recepcion", "mantenimiento", "pisos", "gobernanta", "supervisora", "camarera", "lavanderia", "tablet_pisos", "empleado", "kiosco"]:
        conn.execute("INSERT OR IGNORE INTO roles (nombre) VALUES (?)", (rol,))

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usuario_ambitos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            tipo_acceso TEXT DEFAULT 'departamento'
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documentos_rrhh (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER NOT NULL,
            tipo_documento TEXT NOT NULL,
            nombre TEXT,
            fecha_documento TEXT,
            fecha_caducidad TEXT,
            archivo TEXT,
            estado TEXT DEFAULT 'vigente',
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "tipo_documento TEXT", "nombre TEXT", "fecha_documento TEXT", "fecha_caducidad TEXT",
        "archivo TEXT", "estado TEXT DEFAULT 'vigente'", "observaciones TEXT",
        "empresa_id INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        "visible_empleado INTEGER DEFAULT 1", "origen TEXT DEFAULT 'rrhh'",
        "subido_por_usuario_id INTEGER",
    ]:
        _add_column_if_missing(conn, "documentos_rrhh", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rrhh_seguridad_social_ficheros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT DEFAULT 'afiliacion',
            direccion TEXT DEFAULT 'salida',
            periodo TEXT,
            ccc TEXT,
            autorizado_red TEXT,
            canal TEXT DEFAULT 'Sistema RED / SILTRA',
            estado TEXT DEFAULT 'preparado',
            descripcion TEXT,
            archivo TEXT,
            nombre_original TEXT,
            hash_archivo TEXT,
            fecha_generacion TEXT,
            fecha_envio TEXT,
            fecha_respuesta TEXT,
            respuesta TEXT,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            usuario_id INTEGER,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
        """
    )
    for col in [
        "tipo TEXT DEFAULT 'afiliacion'",
        "direccion TEXT DEFAULT 'salida'",
        "periodo TEXT",
        "ccc TEXT",
        "autorizado_red TEXT",
        "canal TEXT DEFAULT 'Sistema RED / SILTRA'",
        "estado TEXT DEFAULT 'preparado'",
        "descripcion TEXT",
        "archivo TEXT",
        "nombre_original TEXT",
        "hash_archivo TEXT",
        "fecha_generacion TEXT",
        "fecha_envio TEXT",
        "fecha_respuesta TEXT",
        "respuesta TEXT",
        "observaciones TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "usuario_id INTEGER",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        "updated_at TEXT",
    ]:
        _add_column_if_missing(conn, "rrhh_seguridad_social_ficheros", col)

    # Columnas de auditoría para todas las tablas principales
    for table in [
        'empleados', 'categorias_laborales', 'contratos', 'nominas', 'documentos_rrhh', 'fichajes',
        'turnos_rrhh', 'variables_prenomina', 'vacaciones_rrhh', 'centros_trabajo', 'puestos_operativos',
        'necesidades_operativas', 'rrhh_seguridad_social_ficheros'
    ]:
        if _table_exists(conn, table):
            _add_column_if_missing(conn, table, 'updated_at TEXT')
            _add_column_if_missing(conn, table, 'created_by INTEGER')
            _add_column_if_missing(conn, table, 'updated_by INTEGER')
            _add_column_if_missing(conn, table, 'motivo_cambio TEXT')
    for table in ['documentos_rrhh', 'fichajes', 'turnos_rrhh', 'variables_prenomina', 'vacaciones_rrhh']:
        if _table_exists(conn, table):
            _add_column_if_missing(conn, table, "estado TEXT DEFAULT 'activo'")

    conn.commit()


def _departamento_scope_sql(alias="e"):
    """
    Devuelve SQL y parámetros para visibilidad real por departamento.
    Dirección/admin/RRHH ven todo. El resto se limita a su departamento si existe en sesión.
    """
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    centro_sql = ""
    centro_params = []
    if centro_id:
        conn = get_db()
        try:
            dep_ids = ids_departamentos_centro(conn, empresa_id, centro_id)
            pv_ids = ids_puntos_venta_centro(conn, empresa_id, centro_id)
            tiene_centro_empleado = _column_exists(conn, "empleados", "centro_id")
        finally:
            conn.close()
        condiciones = []
        if tiene_centro_empleado:
            condiciones.append(f"{alias}.centro_id = ?")
            centro_params.append(centro_id)
        if dep_ids:
            condiciones.append(f"{alias}.departamento_id IN ({','.join('?' for _ in dep_ids)})")
            centro_params.extend(dep_ids)
        if pv_ids:
            condiciones.append(f"{alias}.punto_venta_id IN ({','.join('?' for _ in pv_ids)})")
            centro_params.extend(pv_ids)
        centro_sql = f" AND ({' OR '.join(condiciones)})" if condiciones else " AND 1 = 0"
    if _usuario_es_global_rrhh():
        return f"{alias}.empresa_id = ?{centro_sql}", [empresa_id] + centro_params

    departamento_id = _session_departamento_id()
    if departamento_id:
        return f"{alias}.empresa_id = ? AND {alias}.departamento_id = ?{centro_sql}", [empresa_id, departamento_id] + centro_params

    # Si no sabemos el departamento del usuario, no exponemos otros departamentos.
    return f"{alias}.empresa_id = ? AND 1 = 0", [empresa_id]


def _require_rrhh_global(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _usuario_es_global_rrhh():
            flash("No tienes permisos para modificar datos globales de RRHH.", "warning")
            return redirect(url_for("rrhh.panel_rrhh"))
        return func(*args, **kwargs)

    return wrapper


def _usuario_puede_gestionar_cuadrantes():
    return _usuario_es_global_rrhh() or usuario_tiene_permiso("gestionar_cuadrantes")


def _usuario_puede_ver_cuadrantes():
    return _usuario_puede_gestionar_cuadrantes() or usuario_tiene_permiso("ver_cuadrantes")


def _require_cuadrantes(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _usuario_puede_gestionar_cuadrantes():
            flash("No tienes permisos para modificar cuadrantes.", "warning")
            return redirect(url_for("rrhh.cuadrante_real"))
        return func(*args, **kwargs)

    return wrapper


def _empleado_en_ambito_rrhh(conn, empleado_id):
    empleado_id = _to_int(empleado_id, 0)
    if not empleado_id:
        return None
    scope_sql, scope_params = _departamento_scope_sql("e")
    return conn.execute(
        f"""
        SELECT e.*
        FROM empleados e
        WHERE e.id = ? AND {scope_sql}
        LIMIT 1
        """,
        [empleado_id] + scope_params,
    ).fetchone()


def _turno_en_ambito_rrhh(conn, turno_id):
    turno_id = _to_int(turno_id, 0)
    if not turno_id:
        return None
    scope_sql, scope_params = _departamento_scope_sql("e")
    return conn.execute(
        f"""
        SELECT t.*
        FROM turnos_rrhh t
        JOIN empleados e ON e.id = t.empleado_id AND e.empresa_id = t.empresa_id
        WHERE t.id = ? AND {scope_sql}
        LIMIT 1
        """,
        [turno_id] + scope_params,
    ).fetchone()


def _tiempo_extra_en_ambito_rrhh(conn, tiempo_extra_id):
    tiempo_extra_id = _to_int(tiempo_extra_id, 0)
    if not tiempo_extra_id:
        return None
    scope_sql, scope_params = _departamento_scope_sql("e")
    return conn.execute(
        f"""
        SELECT te.*
        FROM tiempo_extra_rrhh te
        JOIN empleados e ON e.id = te.empleado_id AND e.empresa_id = te.empresa_id
        WHERE te.id = ? AND {scope_sql}
        LIMIT 1
        """,
        [tiempo_extra_id] + scope_params,
    ).fetchone()


def _registro_empleado_en_ambito_rrhh(conn, tabla, registro_id):
    registro_id = _to_int(registro_id, 0)
    if not registro_id:
        return None
    scope_sql, scope_params = _departamento_scope_sql("e")
    return conn.execute(
        f"""
        SELECT r.*
        FROM {tabla} r
        JOIN empleados e ON e.id = r.empleado_id AND e.empresa_id = r.empresa_id
        WHERE r.id = ? AND {scope_sql}
        LIMIT 1
        """,
        [registro_id] + scope_params,
    ).fetchone()


def _usuario_es_empleado_puro():
    return _normalizar_rol(session.get("user_rol")) == "empleado" and not _usuario_es_global_rrhh()


def _usuario_es_kiosco():
    return _normalizar_rol(session.get("user_rol")) in {"kiosco", "tablet", "fichaje"}


def _cargar_empleado_actual(conn):
    empresa_id = _empresa_id()

    def _query(where_sql, params):
        return conn.execute(
            f"""
            SELECT e.id, e.nombre, e.apellidos, e.email, e.departamento_id, e.subdepartamento_id,
                   e.categoria_id, e.usuario_id, e.pin_fichaje_hash, e.pin_fichaje_updated_at,
                   d.nombre AS departamento_nombre,
                   sd.nombre AS subdepartamento_nombre, c.nombre AS categoria_nombre
            FROM empleados e
            LEFT JOIN departamentos d ON e.departamento_id = d.id
            LEFT JOIN subdepartamentos sd ON e.subdepartamento_id = sd.id
            LEFT JOIN categorias_laborales c ON e.categoria_id = c.id
            WHERE e.empresa_id = ? AND COALESCE(e.activo, 1) = 1 AND {where_sql}
            LIMIT 1
            """,
            [empresa_id] + list(params),
        ).fetchone()

    empleado_id = session.get("user_empleado_id")
    empleado = None
    if empleado_id:
        empleado = _query("e.id = ?", [empleado_id])

    if not empleado and session.get("user_id") and _column_exists(conn, "empleados", "usuario_id"):
        empleado = _query("e.usuario_id = ?", [session.get("user_id")])

    if not empleado and _table_exists(conn, "usuarios") and _column_exists(conn, "usuarios", "empleado_id"):
        row = conn.execute(
            "SELECT empleado_id FROM usuarios WHERE id = ? AND empresa_id = ?",
            (session.get("user_id"), empresa_id),
        ).fetchone()
        if row and row["empleado_id"]:
            empleado = _query("e.id = ?", [row["empleado_id"]])

    if not empleado and session.get("user_email"):
        empleado = _query("LOWER(COALESCE(e.email, '')) = LOWER(?)", [session.get("user_email")])

    if empleado:
        session["user_empleado_id"] = empleado["id"]
        if empleado["departamento_id"]:
            session["user_departamento_id"] = empleado["departamento_id"]
        if empleado["subdepartamento_id"]:
            session["user_subdepartamento_id"] = empleado["subdepartamento_id"]
    return empleado


@rrhh_bp.before_request
def _bloquear_rrhh_global_a_empleado():
    if "user_id" not in session:
        return None

    permitidos_empleado = {
        "rrhh.portal_empleado",
        "rrhh.fichar_empleado",
        "rrhh.subir_documento_empleado",
        "rrhh.descargar_nomina_empleado",
        "rrhh.descargar_documento_empleado",
        "rrhh.descargar_contrato_empleado",
        "rrhh.firmar_contrato_empleado",
        "rrhh.solicitar_ausencia_empleado",
        "rrhh.cancelar_solicitud_empleado",
        "rrhh.solicitar_correccion_fichaje_empleado",
        "rrhh.cambiar_pin_fichaje_empleado",
        "rrhh.informe_jornada_empleado_csv",
        "rrhh.aceptar_aviso_jornada_empleado",
    }
    permitidos_kiosco = {"rrhh.kiosco_fichaje", "rrhh.kiosco_registrar_fichaje"}

    if _usuario_es_kiosco() and request.endpoint not in permitidos_kiosco:
        return redirect(url_for("rrhh.kiosco_fichaje"))

    if _usuario_es_empleado_puro() and request.endpoint not in permitidos_empleado:
        return redirect(url_for("rrhh.portal_empleado"))
    return None


def _usuarios_acceso_select(conn):
    if not _table_exists(conn, "usuarios"):
        return []
    return conn.execute(
        """
        SELECT u.id, u.nombre, u.email, u.codigo_acceso, r.nombre AS rol_nombre
        FROM usuarios u
        LEFT JOIN roles r ON u.rol_id = r.id
        LEFT JOIN empleados e ON e.id = u.empleado_id AND e.empresa_id = u.empresa_id
        WHERE u.empresa_id = ? AND COALESCE(u.activo, 1) = 1
          AND (? IS NULL OR e.centro_id = ? OR u.empleado_id IS NULL)
        ORDER BY u.nombre
        """,
        (_empresa_id(), centro_activo_id(), centro_activo_id()),
    ).fetchall()


def _vincular_usuario_empleado(conn, empleado_id, usuario_id, empresa_id):
    usuario_id = usuario_id or None
    if _column_exists(conn, "empleados", "usuario_id"):
        if usuario_id:
            conn.execute(
                "UPDATE empleados SET usuario_id = NULL WHERE empresa_id = ? AND usuario_id = ? AND id <> ?",
                (empresa_id, usuario_id, empleado_id),
            )
        conn.execute(
            "UPDATE empleados SET usuario_id = ? WHERE id = ? AND empresa_id = ?",
            (usuario_id, empleado_id, empresa_id),
        )

    if _table_exists(conn, "usuarios") and _column_exists(conn, "usuarios", "empleado_id"):
        conn.execute(
            "UPDATE usuarios SET empleado_id = NULL WHERE empresa_id = ? AND empleado_id = ?",
            (empresa_id, empleado_id),
        )
        if usuario_id:
            conn.execute(
                "UPDATE usuarios SET empleado_id = ? WHERE id = ? AND empresa_id = ?",
                (empleado_id, usuario_id, empresa_id),
            )


def _actualizar_pin_fichaje(conn, empleado_id, pin):
    pin = (pin or "").strip()
    if not pin:
        return
    conn.execute(
        """
        UPDATE empleados
        SET pin_fichaje_hash = ?, pin_fichaje_updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (generate_password_hash(pin), empleado_id, _empresa_id()),
    )


AVISO_JORNADA_VERSION = "registro_jornada_2026_05_v1"
RETENCION_REGISTRO_JORNADA_ANIOS = 4


def _texto_aviso_jornada():
    return (
        "He recibido la informacion sobre el uso del portal de empleado y del sistema de registro de jornada. "
        "El sistema registra mis marcajes de entrada, salida, pausas y reanudaciones, junto con la fecha, hora, "
        "origen del marcaje y datos tecnicos necesarios para la trazabilidad. Estos registros se conservan durante "
        f"{RETENCION_REGISTRO_JORNADA_ANIOS} anos y pueden estar disponibles para la persona trabajadora, "
        "representacion legal y autoridad competente cuando proceda. Si detecto un error, debo solicitar correccion "
        "desde el portal para que RRHH lo revise."
    )


def _registrar_acceso_portal(conn, empleado_id, accion, detalle=None):
    if not _table_exists(conn, "rrhh_portal_accesos"):
        return
    conn.execute(
        """
        INSERT INTO rrhh_portal_accesos
        (empleado_id, usuario_id, accion, detalle, ip, ip_hash, datos_minimizados, user_agent, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            empleado_id,
            session.get("user_id"),
            accion,
            (detalle or "")[:500],
            "",
            _ip_cliente_hash(),
            str(request.user_agent)[:500],
            _empresa_id(),
        ),
    )


def _acuse_jornada_actual(conn, empleado_id):
    if not _table_exists(conn, "rrhh_portal_aceptaciones"):
        return None
    return conn.execute(
        """
        SELECT *
        FROM rrhh_portal_aceptaciones
        WHERE empresa_id = ?
          AND empleado_id = ?
          AND tipo = 'registro_jornada'
          AND version = ?
        ORDER BY datetime(fecha) DESC, id DESC
        LIMIT 1
        """,
        (_empresa_id(), empleado_id, AVISO_JORNADA_VERSION),
    ).fetchone()


def _avisos_portal_empleado(conn, empleado_id, acuse_jornada=None):
    avisos = []
    if not acuse_jornada:
        avisos.append({
            "nivel": "warning",
            "titulo": "Informacion de registro de jornada pendiente",
            "detalle": "Revisa y acepta el aviso para dejar constancia de recepcion.",
        })
    contratos = conn.execute(
        """
        SELECT COUNT(*) total
        FROM contratos
        WHERE empresa_id = ?
          AND empleado_id = ?
          AND COALESCE(estado, 'activo') != 'anulado'
          AND COALESCE(firmado_empleado, 0) = 0
        """,
        (_empresa_id(), empleado_id),
    ).fetchone()["total"] or 0
    if contratos:
        avisos.append({
            "nivel": "danger",
            "titulo": "Contratos pendientes de firma",
            "detalle": f"Tienes {contratos} contrato(s) pendiente(s).",
        })
    justificantes = conn.execute(
        """
        SELECT COUNT(*) total
        FROM vacaciones_rrhh
        WHERE empresa_id = ?
          AND empleado_id = ?
          AND COALESCE(requiere_documento, 0) = 1
          AND documento_id IS NULL
          AND COALESCE(estado, 'solicitada') IN ('solicitada', 'aprobada')
        """,
        (_empresa_id(), empleado_id),
    ).fetchone()["total"] or 0
    if justificantes:
        avisos.append({
            "nivel": "warning",
            "titulo": "Justificantes pendientes",
            "detalle": f"Tienes {justificantes} solicitud(es) con justificante pendiente.",
        })
    documentos_rechazados = conn.execute(
        """
        SELECT COUNT(*) total
        FROM documentos_rrhh
        WHERE empresa_id = ?
          AND empleado_id = ?
          AND origen = 'portal_empleado'
          AND COALESCE(estado, '') IN ('rechazado', 'rechazada')
        """,
        (_empresa_id(), empleado_id),
    ).fetchone()["total"] or 0
    if documentos_rechazados:
        avisos.append({
            "nivel": "danger",
            "titulo": "Documentos rechazados",
            "detalle": f"Hay {documentos_rechazados} documento(s) que debes revisar o volver a subir.",
        })
    documentos_revision = conn.execute(
        """
        SELECT COUNT(*) total
        FROM documentos_rrhh
        WHERE empresa_id = ?
          AND empleado_id = ?
          AND origen = 'portal_empleado'
          AND COALESCE(estado, '') = 'pendiente_revision'
        """,
        (_empresa_id(), empleado_id),
    ).fetchone()["total"] or 0
    if documentos_revision:
        avisos.append({
            "nivel": "info",
            "titulo": "Documentos en revision",
            "detalle": f"{documentos_revision} documento(s) siguen pendientes de revision por RRHH.",
        })
    correcciones = conn.execute(
        """
        SELECT COUNT(*) total
        FROM solicitudes_correccion_fichaje
        WHERE empresa_id = ?
          AND empleado_id = ?
          AND estado = 'pendiente'
        """,
        (_empresa_id(), empleado_id),
    ).fetchone()["total"] or 0
    if correcciones:
        avisos.append({
            "nivel": "info",
            "titulo": "Correcciones de fichaje pendientes",
            "detalle": f"Tienes {correcciones} solicitud(es) esperando revision.",
        })
    return avisos


def _generar_password_temporal(longitud=10):
    alfabeto = string.ascii_letters + string.digits
    while True:
        valor = "".join(secrets.choice(alfabeto) for _ in range(longitud))
        if any(c.islower() for c in valor) and any(c.isupper() for c in valor) and any(c.isdigit() for c in valor):
            return valor


def _generar_pin_temporal(longitud=6):
    return "".join(secrets.choice(string.digits) for _ in range(longitud))


def _password_temporal_desde_form():
    password = (request.form.get("password") or "").strip()
    repetir = (request.form.get("password_repetir") or "").strip()
    if not password:
        return _generar_password_temporal(), None
    if password != repetir:
        return None, "La contrasena no coincide."
    if len(password) < 8:
        return None, "La contrasena debe tener al menos 8 caracteres."
    return password, None


def _codigo_acceso_unico(conn, empresa_id, empleado_id):
    base = f"E{int(empleado_id):05d}"
    codigo = base
    contador = 1
    while True:
        existe = conn.execute(
            """
            SELECT 1
            FROM usuarios
            WHERE empresa_id = ? AND UPPER(COALESCE(codigo_acceso, '')) = ?
            LIMIT 1
            """,
            (empresa_id, codigo.upper()),
        ).fetchone()
        if not existe:
            return codigo
        contador += 1
        codigo = f"{base}{contador}"[:20]


def _usuario_acceso_empleado(conn, empleado_id, empresa_id):
    empleado = conn.execute(
        """
        SELECT id, nombre, apellidos, email, usuario_id
        FROM empleados
        WHERE id = ? AND empresa_id = ?
        """,
        (empleado_id, empresa_id),
    ).fetchone()
    if not empleado or not _table_exists(conn, "usuarios"):
        return empleado, None

    usuario = None
    if "usuario_id" in empleado.keys() and empleado["usuario_id"]:
        usuario = conn.execute(
            "SELECT * FROM usuarios WHERE id = ? AND empresa_id = ?",
            (empleado["usuario_id"], empresa_id),
        ).fetchone()

    if not usuario and _column_exists(conn, "usuarios", "empleado_id"):
        usuario = conn.execute(
            "SELECT * FROM usuarios WHERE empleado_id = ? AND empresa_id = ? ORDER BY id LIMIT 1",
            (empleado_id, empresa_id),
        ).fetchone()

    return empleado, usuario


def _email_unico_usuario(conn, empresa_id, empleado_id, email):
    email = (email or "").strip().lower()
    if not email:
        email = f"empleado{empleado_id}@portal.local"
    contador = 1
    while True:
        existe = conn.execute(
            "SELECT 1 FROM usuarios WHERE empresa_id = ? AND LOWER(email) = LOWER(?) LIMIT 1",
            (empresa_id, email),
        ).fetchone()
        if not existe:
            return email
        contador += 1
        email = f"empleado{empleado_id}_{contador}@portal.local"


def _rol_empleado_id(conn):
    row = conn.execute("SELECT id FROM roles WHERE LOWER(nombre) = 'empleado' LIMIT 1").fetchone()
    if row:
        return row["id"]
    cursor = conn.execute("INSERT INTO roles (nombre) VALUES ('empleado')")
    return cursor.lastrowid


def _rol_acceso_id(conn, nombre_rol):
    rol = (nombre_rol or "empleado").strip().lower()
    row = conn.execute("SELECT id FROM roles WHERE LOWER(nombre) = LOWER(?) LIMIT 1", (rol,)).fetchone()
    if row:
        return row["id"]
    cursor = conn.execute("INSERT INTO roles (nombre) VALUES (?)", (rol,))
    return cursor.lastrowid


def _crear_usuario_operativo_empleado(conn, empleado_id, empresa_id, rol_acceso=None, password=None):
    empleado, usuario = _usuario_acceso_empleado(conn, empleado_id, empresa_id)
    if not empleado:
        return None, None, "Empleado no encontrado."
    if usuario:
        rol_id = _rol_acceso_id(conn, rol_acceso)
        conn.execute(
            """
            UPDATE usuarios
            SET rol_id = ?, activo = 1, empleado_id = ?, nombre = COALESCE(NULLIF(nombre, ''), ?)
            WHERE id = ? AND empresa_id = ?
            """,
            (rol_id, empleado_id, f"{empleado['nombre']} {empleado['apellidos'] or ''}".strip(), usuario["id"], empresa_id),
        )
        _vincular_usuario_empleado(conn, empleado_id, usuario["id"], empresa_id)
        return usuario["id"], None, None
    password = password or _generar_password_temporal()
    email = _email_unico_usuario(conn, empresa_id, empleado_id, empleado["email"])
    codigo = _codigo_acceso_unico(conn, empresa_id, empleado_id)
    rol_id = _rol_acceso_id(conn, rol_acceso)
    nombre_usuario = f"{empleado['nombre']} {empleado['apellidos'] or ''}".strip()
    cursor = conn.execute(
        """
        INSERT INTO usuarios (nombre, email, password_hash, rol_id, empresa_id, activo, empleado_id, codigo_acceso, debe_cambiar_password)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?, 1)
        """,
        (nombre_usuario, email, generate_password_hash(password), rol_id, empresa_id, empleado_id, codigo),
    )
    usuario_id = cursor.lastrowid
    _vincular_usuario_empleado(conn, empleado_id, usuario_id, empresa_id)
    return usuario_id, {"codigo": codigo, "password": password, "email": email}, None


def _estado_jornada_desde_ultimo(ultimo_tipo):
    tipo = (ultimo_tipo or "").lower()
    if tipo in {"entrada", "reanudacion"}:
        return "trabajando", {"pausa", "salida"}
    if tipo == "pausa":
        return "pausa", {"reanudacion", "salida"}
    return "fuera", {"entrada"}


def _hash_fichaje(conn, empleado_id, tipo, fecha_hora, usuario_id, empresa_id):
    previo = ""
    if _column_exists(conn, "fichajes", "hash_registro"):
        row = conn.execute(
            """
            SELECT hash_registro
            FROM fichajes
            WHERE empleado_id = ? AND empresa_id = ? AND hash_registro IS NOT NULL
            ORDER BY datetime(fecha_hora) DESC, id DESC
            LIMIT 1
            """,
            (empleado_id, empresa_id),
        ).fetchone()
        previo = row["hash_registro"] if row else ""
    payload = f"{previo}|{empresa_id}|{empleado_id}|{usuario_id or ''}|{tipo}|{fecha_hora}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _insertar_fichaje(conn, empleado_id, tipo, observaciones, origen="portal_empleado", fecha_hora=None):
    empresa_id = _empresa_id()
    fecha_hora = fecha_hora or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    usuario_id = session.get("user_id")
    hash_registro = _hash_fichaje(conn, empleado_id, tipo, fecha_hora, usuario_id, empresa_id)
    conn.execute(
        """
        INSERT INTO fichajes
        (empleado_id, tipo, fecha_hora, origen, observaciones, empresa_id, usuario_id, ip_origen, ip_hash, user_agent, hash_registro)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            empleado_id,
            tipo,
            fecha_hora,
            origen,
            observaciones,
            empresa_id,
            usuario_id,
            request.remote_addr,
            _ip_cliente_hash(),
            str(request.user_agent)[:500],
            hash_registro,
        ),
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _actualizar_campos_correccion_fichaje(conn, fichaje_id, campos):
    sets = []
    params = []
    for campo, valor in campos.items():
        if _column_exists(conn, "fichajes", campo):
            sets.append(f"{campo} = ?")
            params.append(valor)
    if not sets:
        return
    params.extend([fichaje_id, _empresa_id()])
    conn.execute(f"UPDATE fichajes SET {', '.join(sets)} WHERE id = ? AND empresa_id = ?", params)


def _crear_correccion_manual_fichaje(conn, fichaje_original, empleado_id, tipo, fecha_hora, observaciones, motivo):
    motivo = (motivo or "Correccion manual RRHH").strip()
    nuevo_id = _insertar_fichaje(
        conn,
        empleado_id,
        tipo,
        f"Correccion manual de fichaje #{fichaje_original['id']}. {observaciones or ''}".strip(),
        "correccion_manual_rrhh",
        fecha_hora,
    )
    auditoria = {
        "fichaje_origen_id": fichaje_original["id"],
        "corregido_por": session.get("user_id"),
        "fecha_correccion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "motivo_correccion": motivo,
        "ip_correccion": request.remote_addr,
    }
    _actualizar_campos_correccion_fichaje(conn, nuevo_id, auditoria)
    _actualizar_campos_correccion_fichaje(
        conn,
        fichaje_original["id"],
        {
            "estado": "anulado",
            "corregido_por": session.get("user_id"),
            "fecha_correccion": auditoria["fecha_correccion"],
            "motivo_correccion": f"Anulado por correccion manual. Nuevo fichaje #{nuevo_id}. {motivo}",
            "ip_correccion": request.remote_addr,
            "updated_by": session.get("user_id"),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "motivo_cambio": motivo,
        },
    )
    _registrar_historial(conn, "fichajes", fichaje_original["id"], "anular_por_correccion", f"Nuevo fichaje #{nuevo_id}. {motivo}")
    _registrar_historial(conn, "fichajes", nuevo_id, "crear_correccion_manual", f"Corrige fichaje #{fichaje_original['id']}. {motivo}")
    return nuevo_id


def _archivo_rrhh_permitido(filename):
    return Path(filename or "").suffix.lower() in {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".doc", ".docx"}


def _archivo_seguridad_social_permitido(filename):
    extensiones = {
        ".pdf", ".txt", ".csv", ".xml", ".zip", ".xlsx", ".xls",
        ".dat", ".afi", ".fdi", ".cra", ".rnt", ".rnl", ".frp",
    }
    return Path(filename or "").suffix.lower() in extensiones


def _hash_archivo(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for bloque in iter(lambda: handle.read(65536), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _guardar_archivo_seguridad_social(archivo, empresa_id):
    if not archivo or not getattr(archivo, "filename", None):
        raise ValueError("Sube un fichero para registrar.")
    if not _archivo_seguridad_social_permitido(archivo.filename):
        raise ValueError("Formato no permitido para Seguridad Social.")

    carpeta = Path("uploads") / "rrhh" / "seguridad_social" / str(empresa_id)
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre_original = secure_filename(archivo.filename)
    marca = datetime.now().strftime("%Y%m%d%H%M%S")
    token = secrets.token_hex(4)
    ruta = carpeta / f"{marca}_{token}_{nombre_original}"
    archivo.save(str(ruta))
    return str(ruta).replace("\\", "/"), nombre_original, _hash_archivo(ruta)


def _guardar_archivo_empleado(empleado_id, archivo):
    if not archivo or not getattr(archivo, "filename", None):
        raise ValueError("Sube un archivo.")
    if not _archivo_rrhh_permitido(archivo.filename):
        raise ValueError("Formato no permitido. Usa PDF, imagen, DOC o DOCX.")

    carpeta = Path("uploads") / "rrhh" / "empleados" / str(empleado_id)
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = secure_filename(archivo.filename)
    marca = datetime.now().strftime("%Y%m%d%H%M%S")
    ruta = carpeta / f"{marca}_{nombre}"
    archivo.save(str(ruta))
    return str(ruta).replace("\\", "/")


def _resolver_archivo_rrhh(ruta_texto):
    if not ruta_texto:
        return None
    ruta = Path(str(ruta_texto).replace("\\", "/"))
    if not ruta.is_absolute():
        ruta = Path.cwd() / ruta
    try:
        ruta_resuelta = ruta.resolve()
    except Exception:
        return None

    raices = [
        Path.cwd().resolve(),
        (Path.cwd() / "uploads").resolve(),
        (Path.cwd() / "instance" / "uploads").resolve(),
    ]
    if not any(ruta_resuelta == raiz or raiz in ruta_resuelta.parents for raiz in raices):
        return None
    return ruta_resuelta if ruta_resuelta.exists() and ruta_resuelta.is_file() else None


def _enviar_archivo_rrhh(ruta_texto, nombre_descarga=None):
    ruta = _resolver_archivo_rrhh(ruta_texto)
    if not ruta:
        abort(404)
    return send_file(ruta, as_attachment=True, download_name=nombre_descarga or ruta.name)


SELECCION_ESTADOS = [
    ("recibido", "Recibido"),
    ("preseleccionado", "Preseleccionado"),
    ("entrevista", "Entrevista"),
    ("reserva", "Bolsa/reserva"),
    ("seleccionado", "Seleccionado"),
    ("descartado", "Descartado"),
]


SELECCION_CRITERIOS = [
    ("experiencia_sector", "Experiencia en hosteleria", 3),
    ("puesto_similar", "Puesto similar", 3),
    ("idiomas", "Idiomas", 2),
    ("disponibilidad", "Disponibilidad", 2),
    ("formacion", "Formacion/carnets", 1),
    ("actitud", "Actitud y encaje", 2),
]


def _ensure_seleccion_schema(conn):
    ensure_rrhh_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rrhh_seleccion_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            dias_respuesta_descarte INTEGER DEFAULT 7,
            envio_automatico INTEGER DEFAULT 0,
            asunto_descarte TEXT,
            plantilla_descarte TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_by INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rrhh_ofertas_trabajo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            titulo TEXT NOT NULL,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            centro_id INTEGER,
            puesto_id INTEGER,
            categoria_id INTEGER,
            tipo_contrato TEXT,
            jornada TEXT,
            salario_desde REAL DEFAULT 0,
            salario_hasta REAL DEFAULT 0,
            fecha_inicio TEXT,
            fecha_cierre TEXT,
            estado TEXT DEFAULT 'abierta',
            descripcion TEXT,
            requisitos TEXT,
            competencias_clave TEXT,
            dias_respuesta_descarte INTEGER,
            asunto_descarte TEXT,
            plantilla_descarte TEXT,
            creado_por INTEGER,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rrhh_candidatos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            oferta_id INTEGER,
            nombre TEXT NOT NULL,
            apellidos TEXT,
            email TEXT,
            telefono TEXT,
            fuente TEXT,
            fecha_recepcion TEXT DEFAULT CURRENT_TIMESTAMP,
            cv_path TEXT,
            cv_filename TEXT,
            carta_presentacion TEXT,
            experiencia_texto TEXT,
            formacion_texto TEXT,
            disponibilidad TEXT,
            salario_esperado REAL DEFAULT 0,
            notas TEXT,
            puntuacion_total REAL DEFAULT 0,
            recomendacion_ia TEXT,
            estado TEXT DEFAULT 'recibido',
            motivo_descarte TEXT,
            fecha_descarte TEXT,
            descarte_email_programado TEXT,
            descarte_email_enviado_at TEXT,
            descarte_email_error TEXT,
            privacidad_aceptada INTEGER DEFAULT 0,
            evaluado_por INTEGER,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rrhh_candidato_valoraciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            candidato_id INTEGER NOT NULL,
            criterio TEXT NOT NULL,
            etiqueta TEXT,
            puntuacion INTEGER DEFAULT 0,
            peso INTEGER DEFAULT 1,
            comentario TEXT,
            usuario_id INTEGER,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for tabla, columnas in {
        "rrhh_seleccion_config": [
            "empresa_id INTEGER DEFAULT 1",
            "dias_respuesta_descarte INTEGER DEFAULT 7",
            "envio_automatico INTEGER DEFAULT 0",
            "asunto_descarte TEXT",
            "plantilla_descarte TEXT",
            "updated_at TEXT DEFAULT CURRENT_TIMESTAMP",
            "updated_by INTEGER",
        ],
        "rrhh_ofertas_trabajo": [
            "empresa_id INTEGER DEFAULT 1",
            "departamento_id INTEGER",
            "subdepartamento_id INTEGER",
            "centro_id INTEGER",
            "puesto_id INTEGER",
            "categoria_id INTEGER",
            "tipo_contrato TEXT",
            "jornada TEXT",
            "salario_desde REAL DEFAULT 0",
            "salario_hasta REAL DEFAULT 0",
            "fecha_inicio TEXT",
            "fecha_cierre TEXT",
            "estado TEXT DEFAULT 'abierta'",
            "descripcion TEXT",
            "requisitos TEXT",
            "competencias_clave TEXT",
            "dias_respuesta_descarte INTEGER",
            "asunto_descarte TEXT",
            "plantilla_descarte TEXT",
            "creado_por INTEGER",
            "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
            "updated_at TEXT DEFAULT CURRENT_TIMESTAMP",
        ],
        "rrhh_candidatos": [
            "empresa_id INTEGER DEFAULT 1",
            "oferta_id INTEGER",
            "apellidos TEXT",
            "email TEXT",
            "telefono TEXT",
            "fuente TEXT",
            "fecha_recepcion TEXT DEFAULT CURRENT_TIMESTAMP",
            "cv_path TEXT",
            "cv_filename TEXT",
            "carta_presentacion TEXT",
            "experiencia_texto TEXT",
            "formacion_texto TEXT",
            "disponibilidad TEXT",
            "salario_esperado REAL DEFAULT 0",
            "notas TEXT",
            "puntuacion_total REAL DEFAULT 0",
            "recomendacion_ia TEXT",
            "estado TEXT DEFAULT 'recibido'",
            "motivo_descarte TEXT",
            "fecha_descarte TEXT",
            "descarte_email_programado TEXT",
            "descarte_email_enviado_at TEXT",
            "descarte_email_error TEXT",
            "privacidad_aceptada INTEGER DEFAULT 0",
            "evaluado_por INTEGER",
            "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
            "updated_at TEXT DEFAULT CURRENT_TIMESTAMP",
        ],
        "rrhh_candidato_valoraciones": [
            "empresa_id INTEGER DEFAULT 1",
            "candidato_id INTEGER",
            "criterio TEXT",
            "etiqueta TEXT",
            "puntuacion INTEGER DEFAULT 0",
            "peso INTEGER DEFAULT 1",
            "comentario TEXT",
            "usuario_id INTEGER",
            "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        ],
    }.items():
        for columna in columnas:
            _add_column_if_missing(conn, tabla, columna)


def _seleccion_config(conn, empresa_id):
    _ensure_seleccion_schema(conn)
    row = conn.execute(
        "SELECT * FROM rrhh_seleccion_config WHERE empresa_id = ? ORDER BY id DESC LIMIT 1",
        (empresa_id,),
    ).fetchone()
    if row:
        return dict(row)
    return {
        "dias_respuesta_descarte": 7,
        "envio_automatico": 0,
        "asunto_descarte": "Proceso de seleccion - {oferta}",
        "plantilla_descarte": (
            "Hola {nombre},\n\n"
            "Gracias por tu interes en la oferta {oferta}.\n"
            "En esta ocasion no continuaremos con tu candidatura, pero agradecemos el tiempo dedicado.\n\n"
            "Un saludo,\n{empresa}"
        ),
    }


def _empresa_nombre_rrhh(conn, empresa_id):
    if _table_exists(conn, "empresas"):
        row = conn.execute("SELECT nombre FROM empresas WHERE id = ? LIMIT 1", (empresa_id,)).fetchone()
        if row and row["nombre"]:
            return row["nombre"]
    return "El Raco"


def _seleccion_recomendacion(puntuacion):
    if puntuacion >= 80:
        return "Muy interesante: revisar pronto y citar si encaja con el puesto."
    if puntuacion >= 60:
        return "Interesante: guardar y valorar entrevista segun necesidad."
    if puntuacion >= 40:
        return "Dudoso: revisar manualmente antes de descartar."
    return "Baja afinidad: conservar trazabilidad y valorar descarte."


def _guardar_cv_seleccion(empresa_id, archivo):
    if not archivo or not getattr(archivo, "filename", None):
        return None, None
    if not _archivo_rrhh_permitido(archivo.filename):
        raise ValueError("Formato no permitido. Usa PDF, imagen, DOC o DOCX.")
    carpeta = Path("uploads") / "rrhh" / "seleccion" / str(empresa_id)
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre_original = secure_filename(archivo.filename)
    marca = datetime.now().strftime("%Y%m%d%H%M%S")
    token = secrets.token_hex(4)
    ruta = carpeta / f"{marca}_{token}_{nombre_original}"
    archivo.save(str(ruta))
    return str(ruta).replace("\\", "/"), nombre_original


def _seleccion_calcular_valoraciones(form):
    valores = []
    puntos = 0
    maximo = 0
    for criterio, etiqueta, peso in SELECCION_CRITERIOS:
        puntuacion = max(0, min(5, _to_int(form.get(f"score_{criterio}"), 0)))
        comentario = (form.get(f"comentario_{criterio}") or "").strip()
        valores.append((criterio, etiqueta, puntuacion, peso, comentario))
        puntos += puntuacion * peso
        maximo += 5 * peso
    total = round((puntos / maximo) * 100, 2) if maximo else 0
    return total, valores


def _seleccion_guardar_valoraciones(conn, candidato_id, empresa_id, valores):
    conn.execute(
        "DELETE FROM rrhh_candidato_valoraciones WHERE candidato_id = ? AND empresa_id = ?",
        (candidato_id, empresa_id),
    )
    for criterio, etiqueta, puntuacion, peso, comentario in valores:
        conn.execute(
            """
            INSERT INTO rrhh_candidato_valoraciones
            (empresa_id, candidato_id, criterio, etiqueta, puntuacion, peso, comentario, usuario_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (empresa_id, candidato_id, criterio, etiqueta, puntuacion, peso, comentario, session.get("user_id")),
        )


def _seleccion_dias_descarte(oferta, config):
    if oferta and oferta["dias_respuesta_descarte"] not in (None, ""):
        return max(0, _to_int(oferta["dias_respuesta_descarte"], config.get("dias_respuesta_descarte", 7)))
    return max(0, _to_int(config.get("dias_respuesta_descarte"), 7))


def _seleccion_email_texto(candidato, config, empresa_nombre):
    nombre = " ".join([candidato["nombre"] or "", candidato["apellidos"] or ""]).strip() or "candidato/a"
    oferta = candidato["oferta_titulo"] or "la oferta"
    asunto = candidato["oferta_asunto_descarte"] or config.get("asunto_descarte") or "Proceso de seleccion - {oferta}"
    plantilla = candidato["oferta_plantilla_descarte"] or config.get("plantilla_descarte") or ""
    reemplazos = {
        "nombre": nombre,
        "oferta": oferta,
        "empresa": empresa_nombre,
    }
    for clave, valor in reemplazos.items():
        asunto = asunto.replace("{" + clave + "}", str(valor))
        plantilla = plantilla.replace("{" + clave + "}", str(valor))
    cuerpo = "<p>" + plantilla.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
    return asunto, cuerpo


def _seleccion_emails_pendientes(conn, empresa_id, solo_vencidos=True):
    fecha_sql = "AND date(c.descarte_email_programado) <= date('now')" if solo_vencidos else ""
    return conn.execute(
        f"""
        SELECT c.*,
               o.titulo AS oferta_titulo,
               o.asunto_descarte AS oferta_asunto_descarte,
               o.plantilla_descarte AS oferta_plantilla_descarte
        FROM rrhh_candidatos c
        LEFT JOIN rrhh_ofertas_trabajo o ON c.oferta_id = o.id AND o.empresa_id = c.empresa_id
        WHERE c.empresa_id = ?
          AND COALESCE(c.estado, 'recibido') = 'descartado'
          AND COALESCE(c.email, '') != ''
          AND c.descarte_email_programado IS NOT NULL
          AND c.descarte_email_enviado_at IS NULL
          {fecha_sql}
        ORDER BY date(c.descarte_email_programado) ASC, c.id ASC
        """,
        (empresa_id,),
    ).fetchall()


def _firma_hash_empleado(tabla, registro_id, empleado_id):
    payload = f"{tabla}|{registro_id}|{empleado_id}|{session.get('user_id')}|{datetime.now().isoformat()}|{request.remote_addr}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _tipos_ausencia_select():
    return [
        ("vacaciones", "Vacaciones"),
        ("baja_medica", "Baja medica"),
        ("accidente_laboral", "Accidente laboral"),
        ("permiso_retribuido", "Permiso retribuido"),
        ("permiso_no_retribuido", "Permiso no retribuido"),
        ("ausencia_justificada", "Ausencia justificada"),
        ("ausencia_injustificada", "Ausencia injustificada"),
    ]


def _tipo_ausencia_valido(valor):
    tipos = {codigo for codigo, _nombre in _tipos_ausencia_select()}
    valor = (valor or "vacaciones").strip().lower()
    return valor if valor in tipos else "vacaciones"


def _redirect_post_rrhh(default_endpoint, **values):
    if (request.form.get("next") or "").strip() == "pendientes":
        return redirect(url_for("rrhh.pendientes_rrhh"))
    return redirect(url_for(default_endpoint, **values))


def _resumen_pendientes_rrhh(conn, empresa_id, scope_sql, scope_params):
    correcciones = conn.execute(
        f"""
        SELECT COUNT(*) total
        FROM solicitudes_correccion_fichaje s
        JOIN empleados e ON s.empleado_id = e.id
        WHERE {scope_sql}
          AND s.empresa_id = ?
          AND COALESCE(s.estado, 'pendiente') = 'pendiente'
        """,
        scope_params + [empresa_id],
    ).fetchone()["total"]
    solicitudes = conn.execute(
        f"""
        SELECT COUNT(*) total
        FROM vacaciones_rrhh v
        JOIN empleados e ON v.empleado_id = e.id
        WHERE {scope_sql}
          AND v.empresa_id = ?
          AND COALESCE(v.estado, 'solicitada') = 'solicitada'
        """,
        scope_params + [empresa_id],
    ).fetchone()["total"]
    documentos = conn.execute(
        f"""
        SELECT COUNT(*) total
        FROM documentos_rrhh doc
        JOIN empleados e ON doc.empleado_id = e.id
        WHERE {scope_sql}
          AND doc.empresa_id = ?
          AND LOWER(COALESCE(doc.estado, 'vigente')) = 'pendiente_revision'
        """,
        scope_params + [empresa_id],
    ).fetchone()["total"]
    contratos = conn.execute(
        f"""
        SELECT COUNT(*) total
        FROM contratos co
        JOIN empleados e ON co.empleado_id = e.id
        WHERE {scope_sql}
          AND co.empresa_id = ?
          AND COALESCE(co.estado, 'activo') NOT IN ('anulado', 'cancelado', 'borrado')
          AND COALESCE(co.firmado_empleado, 0) = 0
        """,
        scope_params + [empresa_id],
    ).fetchone()["total"]
    total = correcciones + solicitudes + documentos + contratos
    return {
        "correcciones": correcciones,
        "solicitudes": solicitudes,
        "documentos": documentos,
        "contratos": contratos,
        "total": total,
    }


def _alertas_administrativas_rrhh(conn, empresa_id, scope_sql, scope_params):
    documentos = conn.execute(
        f"""
        SELECT doc.*,
               TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre,
               d.nombre departamento_nombre,
               CAST(julianday(date(doc.fecha_caducidad)) - julianday(date('now')) AS INTEGER) dias_restantes
        FROM documentos_rrhh doc
        JOIN empleados e ON doc.empleado_id = e.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        WHERE {scope_sql}
          AND doc.empresa_id = ?
          AND doc.fecha_caducidad IS NOT NULL
          AND TRIM(doc.fecha_caducidad) != ''
          AND date(doc.fecha_caducidad) <= date('now', '+30 day')
          AND COALESCE(doc.estado, 'vigente') NOT IN ('anulado', 'rechazado', 'cancelado')
        ORDER BY date(doc.fecha_caducidad) ASC, doc.id ASC
        LIMIT 100
        """,
        scope_params + [empresa_id],
    ).fetchall()
    contratos = conn.execute(
        f"""
        SELECT co.*,
               TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre,
               d.nombre departamento_nombre,
               CAST(julianday(date(co.fecha_fin)) - julianday(date('now')) AS INTEGER) dias_restantes
        FROM contratos co
        JOIN empleados e ON co.empleado_id = e.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        WHERE {scope_sql}
          AND co.empresa_id = ?
          AND co.fecha_fin IS NOT NULL
          AND TRIM(co.fecha_fin) != ''
          AND date(co.fecha_fin) <= date('now', '+30 day')
          AND COALESCE(co.estado, 'activo') NOT IN ('anulado', 'cancelado', 'borrado')
        ORDER BY date(co.fecha_fin) ASC, co.id ASC
        LIMIT 100
        """,
        scope_params + [empresa_id],
    ).fetchall()
    return {
        "documentos": documentos,
        "contratos": contratos,
        "resumen": {
            "documentos_caducidad": len(documentos),
            "contratos_fin": len(contratos),
            "total": len(documentos) + len(contratos),
        },
    }


def _nivel_prl(probabilidad, severidad):
    score = int(probabilidad or 0) * int(severidad or 0)
    if score >= 12:
        return "critico"
    if score >= 8:
        return "alto"
    if score >= 4:
        return "medio"
    return "bajo"


def _sembrar_prl_base(conn):
    riesgos = [
        ("PRL-COC-001", "Cocina", "Cocina caliente", "Cortes con cuchillos, mandolinas y maquinaria", "seguridad", 3, 4, "Guantes anticorte cuando proceda, orden de tablas, cuchillos afilados y protegidos, parada segura de maquinaria.", "guante anticorte | calzado seguridad", "Corte seguro y maquinaria de cocina", "Jefe de cocina", "mensual"),
        ("PRL-COC-002", "Cocina", "Cocina caliente", "Quemaduras por hornos, freidoras, planchas, vapor y abatidores", "seguridad", 3, 4, "Separar circulaciones, avisar superficies calientes, revisar freidoras, usar manoplas y procedimientos de vapor.", "manoplas termicas | delantal | calzado seguridad", "Quemaduras y calor en cocina", "Jefe de cocina", "mensual"),
        ("PRL-COC-003", "Cocina", "Plonge y lavado", "Resbalones por suelos mojados, grasa o restos", "seguridad", 4, 3, "Suelos secos, senalizacion inmediata, alfombras antideslizantes, limpieza por turnos y drenajes libres.", "calzado antideslizante", "Caidas al mismo nivel", "Segundo de cocina", "semanal"),
        ("PRL-COC-004", "Cocina", "Plonge y limpieza", "Contacto con detergentes, desinfectantes y productos quimicos", "quimico", 3, 4, "Fichas de seguridad disponibles, dosificacion, no mezclar productos, ventilacion y almacenamiento cerrado.", "guantes quimicos | gafas | mascarilla si aplica", "Productos quimicos y fichas de seguridad", "Gobernanta/Jefe cocina", "mensual"),
        ("PRL-COC-005", "Cocina", "Cocina y cuarto frio", "Riesgo biologico por alimentos, residuos, heridas y contaminacion cruzada", "biologico", 3, 4, "Curar heridas, guantes cuando proceda, retirada de residuos, APPCC, lavado de manos y separacion crudo/cocinado.", "guantes nitrilo | proteccion heridas", "APPCC y riesgo biologico laboral", "Responsable APPCC", "mensual"),
        ("PRL-ALI-001", "Cocina", "Manipulacion alimentos", "Trabajador manipulando alimentos sin formacion/carnet vigente", "alimentario", 4, 5, "No asignar manipulacion alimentaria sin carnet vigente. Revisar caducidad, renovacion, APPCC, higiene personal y alergenos.", "guantes cuando proceda | proteccion heridas | uniforme limpio", "Manipulador de alimentos y seguridad alimentaria", "Responsable APPCC/RRHH", "mensual"),
        ("PRL-ERG-001", "Pisos", "Camarera de pisos", "Sobreesfuerzos por camas, carros, banos y posturas forzadas", "ergonomico", 4, 4, "Dimensionar carga por planta, carros adecuados, pausas, tecnica de levantamiento y rotacion de tareas.", "calzado antideslizante | guantes", "Ergonomia en pisos", "Gobernanta", "mensual"),
        ("PRL-SAL-001", "Sala y bares", "Camarero/a", "Caidas, choques y sobrecarga en servicios de alta ocupacion", "seguridad", 3, 3, "Pasillos libres, bandejas equilibradas, reposicion segura, control de derrames y limites de carga.", "calzado antideslizante", "Servicio seguro en sala y bar", "Maitre", "mensual"),
        ("PRL-MAN-001", "Mantenimiento", "Tecnico mantenimiento", "Electricidad, herramientas, altura, sala maquinas y legionela", "seguridad", 3, 5, "Bloqueo/etiquetado, permiso de trabajo, EPIs, revision de herramientas y coordinacion con APPCC.", "gafas | guantes | casco si aplica | arnes si aplica", "Trabajos de mantenimiento seguros", "Jefe mantenimiento", "mensual"),
        ("PRL-REC-001", "Recepcion", "Recepcion y administracion", "Pantallas, nocturnidad, clientes conflictivos, carga mental y emergencias", "psicosocial", 3, 3, "Ajuste ergonomico del puesto, pausas visuales, protocolo de conflictos, relevo claro y plan de emergencia.", "segun plan emergencia", "Recepcion, administracion y atencion segura", "Jefe recepcion/Direccion", "trimestral"),
        ("PRL-ANI-001", "Animacion", "Animacion y eventos", "Caidas, montajes, sonido/luces, piscina, menores, calor y clientes conflictivos", "seguridad", 3, 4, "Revision de zona, cables protegidos, coordinacion con socorrista, control de menores, parada de actividad ante riesgo.", "calzado adecuado | proteccion solar | segun montaje", "Animacion, eventos y actividades seguras", "Jefe animacion/Direccion", "mensual"),
        ("PRL-PSI-001", "General", "Todos", "Riesgo psicosocial por turnos partidos, nocturnidad, presion y clientes conflictivos", "psicosocial", 3, 4, "Control de jornada, descansos reales, canal de incidencias, apoyo de mandos y seguimiento de cargas.", "no aplica", "Riesgos psicosociales en hosteleria", "Direccion/RRHH", "trimestral"),
        ("PRL-EME-001", "General", "Todos", "Emergencias, incendios y evacuacion", "emergencia", 2, 5, "Plan de emergencia, simulacros, extintores revisados, salidas libres y roles por turno.", "segun puesto", "Emergencias y evacuacion", "Direccion/Mantenimiento", "trimestral"),
    ]
    for codigo, area, puesto, riesgo, categoria, prob, sev, medidas, epis, formacion, responsable, frecuencia in riesgos:
        conn.execute(
            """
            INSERT OR IGNORE INTO prl_riesgos
            (codigo, area, puesto, riesgo, categoria, probabilidad, severidad, nivel,
             medidas, epis, formacion_requerida, responsable, frecuencia_revision,
             fuente, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 3)
            """,
            (
                codigo,
                area,
                puesto,
                riesgo,
                categoria,
                prob,
                sev,
                _nivel_prl(prob, sev),
                medidas,
                epis,
                formacion,
                responsable,
                frecuencia,
                "Fuentes PRL hosteleria: Winterhalter, UNIR, USO y criterio operativo El Raco",
            ),
        )
    formaciones = [
        ("PRL-FOR-COC", "PRL cocina industrial", "Cocina", 45, "Cortes, quemaduras, suelos mojados, maquinaria, quimicos, biologico y emergencias."),
        ("PRL-FOR-MANIP", "Manipulador de alimentos y alergenos", "Alimentaria", 60, "Higiene personal, contaminacion cruzada, temperaturas, alergenos, heridas, lotes, etiquetado y buenas practicas APPCC."),
        ("PRL-FOR-SAL", "PRL sala, bar y buffet", "Sala y bares", 35, "Derrames, carga de bandejas, flujo de clientes, terrazas, conflictos y evacuacion."),
        ("PRL-FOR-PIS", "PRL pisos y limpieza", "Pisos", 45, "Ergonomia, carros, productos quimicos, banos, camas y comunicacion de incidencias."),
        ("PRL-FOR-MAN", "PRL mantenimiento hotelero", "Mantenimiento", 60, "Electricidad, altura, herramientas, sala maquinas, legionela y permisos de trabajo."),
        ("PRL-FOR-REC", "PRL recepcion y administracion", "Recepcion", 35, "Pantallas, ergonomia, nocturnidad, clientes conflictivos, proteccion de datos y emergencias."),
        ("PRL-FOR-ANI", "PRL animacion, eventos y actividades", "Animacion", 45, "Montajes, escenarios, sonido, piscina, menores, calor, clientes conflictivos y evacuacion."),
        ("PRL-FOR-APPCC", "APPCC conectado con seguridad laboral", "General", 40, "Riesgo biologico, higiene, residuos, heridas, trazabilidad y seguridad alimentaria."),
        ("PRL-FOR-EME", "Emergencias, incendios y evacuacion", "General", 35, "Actuacion ante incendio, evacuacion, primeros avisos y puntos de encuentro."),
    ]
    for codigo, titulo, area, duracion, contenido in formaciones:
        conn.execute(
            """
            INSERT OR IGNORE INTO prl_formaciones
            (codigo, titulo, area, duracion_minutos, periodicidad, contenido,
             evaluacion, obligatoria, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, 'anual', ?, 'Cuestionario corto y evidencia de asistencia.', 1, 1, 3)
            """,
            (codigo, titulo, area, duracion, contenido),
        )


def _prl_resumen(conn, empresa_id, centro_id):
    ensure_rrhh_schema(conn)
    params = [empresa_id, centro_id]
    riesgos = conn.execute(
        """
        SELECT COUNT(*) total,
               SUM(CASE WHEN nivel IN ('critico','alto') THEN 1 ELSE 0 END) altos,
               SUM(CASE WHEN categoria='ergonomico' THEN 1 ELSE 0 END) ergonomicos,
               SUM(CASE WHEN categoria='quimico' THEN 1 ELSE 0 END) quimicos,
               SUM(CASE WHEN categoria='psicosocial' THEN 1 ELSE 0 END) psicosociales
        FROM prl_riesgos
        WHERE empresa_id=? AND centro_id=? AND COALESCE(activo,1)=1
        """,
        params,
    ).fetchone()
    acciones = conn.execute(
        """
        SELECT COUNT(*) total,
               SUM(CASE WHEN estado='pendiente' THEN 1 ELSE 0 END) pendientes,
               SUM(CASE WHEN prioridad IN ('alta','critica') AND estado!='cerrada' THEN 1 ELSE 0 END) altas
        FROM prl_acciones
        WHERE empresa_id=? AND centro_id=?
        """,
        params,
    ).fetchone()
    formaciones = conn.execute(
        """
        SELECT COUNT(*) total
        FROM prl_formaciones
        WHERE empresa_id=? AND centro_id=? AND COALESCE(activo,1)=1
        """,
        params,
    ).fetchone()
    incidentes = conn.execute(
        """
        SELECT COUNT(*) total,
               SUM(CASE WHEN estado!='cerrado' THEN 1 ELSE 0 END) abiertos
        FROM prl_incidentes
        WHERE empresa_id=? AND centro_id=?
        """,
        params,
    ).fetchone()
    return {
        "riesgos": riesgos or {},
        "acciones": acciones or {},
        "formaciones": formaciones or {},
        "incidentes": incidentes or {},
    }


def _normalizar_area_prl(texto):
    valor = (texto or "").strip().lower()
    if any(x in valor for x in ["cocina", "plonge", "frio", "caliente", "chef"]):
        return "Cocina"
    if any(x in valor for x in ["bar", "bares", "restaurante", "sala", "maitre", "camarero", "buffet"]):
        return "Sala y bares"
    if any(x in valor for x in ["piso", "pisos", "limpieza", "gobernanta", "lavanderia"]):
        return "Pisos"
    if any(x in valor for x in ["mantenimiento", "tecnico", "tecnica"]):
        return "Mantenimiento"
    if any(x in valor for x in ["recepcion", "reservas", "administracion", "night audit", "auditor"]):
        return "Recepcion"
    if any(x in valor for x in ["animacion", "animador", "eventos", "monitor", "miniclub"]):
        return "Animacion"
    return "General"


def _formaciones_prl_para_empleado(empleado, formaciones):
    textos = [
        empleado["departamento_nombre"] if "departamento_nombre" in empleado.keys() else "",
        empleado["subdepartamento_nombre"] if "subdepartamento_nombre" in empleado.keys() else "",
        empleado["rol"] if "rol" in empleado.keys() else "",
    ]
    area = _normalizar_area_prl(" ".join(str(t or "") for t in textos))
    requeridas = []
    for formacion in formaciones:
        f_area = formacion["area"] or "General"
        if f_area == "General" or f_area == area:
            requeridas.append(formacion)
        elif f_area == "Alimentaria" and area in {"Cocina", "Sala y bares"}:
            requeridas.append(formacion)
        elif area == "Cocina" and f_area == "General":
            requeridas.append(formacion)
    return requeridas


def _empleado_requiere_carnet_alimentos(empleado):
    textos = [
        empleado["departamento_nombre"] if "departamento_nombre" in empleado.keys() else "",
        empleado["subdepartamento_nombre"] if "subdepartamento_nombre" in empleado.keys() else "",
        empleado["rol"] if "rol" in empleado.keys() else "",
    ]
    area = _normalizar_area_prl(" ".join(str(t or "") for t in textos))
    return area in {"Cocina", "Sala y bares"}


def _prl_carnet_manipulador_estado(conn, empresa_id, centro_id):
    ensure_rrhh_schema(conn)
    empleados = conn.execute(
        """
        SELECT e.id, e.nombre, e.apellidos, e.rol, e.carnet_manipulador,
               e.fecha_caducidad_carnet, d.nombre AS departamento_nombre,
               sd.nombre AS subdepartamento_nombre
        FROM empleados e
        LEFT JOIN departamentos d ON d.id=e.departamento_id
        LEFT JOIN subdepartamentos sd ON sd.id=e.subdepartamento_id
        WHERE e.empresa_id=? AND COALESCE(e.activo,1)=1
          AND (? IS NULL OR COALESCE(e.centro_id, ?) = ?)
        ORDER BY d.nombre, e.nombre, e.apellidos
        """,
        (empresa_id, centro_id, centro_id, centro_id),
    ).fetchall()
    alertas = []
    total_requieren = 0
    sin_carnet = caducados = caducan = ok = 0
    hoy = date.today()
    for empleado in empleados:
        if not _empleado_requiere_carnet_alimentos(empleado):
            continue
        total_requieren += 1
        nombre = f"{empleado['nombre'] or ''} {empleado['apellidos'] or ''}".strip()
        fecha_txt = (empleado["fecha_caducidad_carnet"] or "").strip()
        carnet = int(empleado["carnet_manipulador"] or 0) == 1
        estado = "ok"
        dias = None
        motivo = "Carnet vigente."
        if not carnet:
            estado = "sin_carnet"
            motivo = "Manipula alimentos y no consta carnet/formacion de manipulador."
            sin_carnet += 1
        elif not fecha_txt:
            estado = "sin_caducidad"
            motivo = "Consta carnet, pero falta fecha de caducidad."
            sin_carnet += 1
        else:
            try:
                caducidad = date.fromisoformat(fecha_txt[:10])
                dias = (caducidad - hoy).days
                if dias < 0:
                    estado = "caducado"
                    motivo = "Carnet de manipulador caducado."
                    caducados += 1
                elif dias <= 30:
                    estado = "caduca_pronto"
                    motivo = "Carnet de manipulador caduca en 30 dias o menos."
                    caducan += 1
                else:
                    ok += 1
            except Exception:
                estado = "fecha_invalida"
                motivo = "Fecha de caducidad no valida."
                sin_carnet += 1
        if estado != "ok":
            alertas.append({
                "empleado_id": empleado["id"],
                "empleado_nombre": nombre,
                "departamento_nombre": empleado["departamento_nombre"] or "-",
                "subdepartamento_nombre": empleado["subdepartamento_nombre"] or "-",
                "rol": empleado["rol"] or "-",
                "estado": estado,
                "fecha_caducidad_carnet": fecha_txt or "-",
                "dias": dias,
                "motivo": motivo,
            })
    resumen = {
        "requieren": total_requieren,
        "ok": ok,
        "sin_carnet": sin_carnet,
        "caducados": caducados,
        "caducan": caducan,
        "alertas": len(alertas),
    }
    return resumen, alertas


def _asignar_formaciones_prl(conn, empresa_id, centro_id):
    ensure_rrhh_schema(conn)
    empleados = conn.execute(
        """
        SELECT e.*, d.nombre AS departamento_nombre, sd.nombre AS subdepartamento_nombre
        FROM empleados e
        LEFT JOIN departamentos d ON d.id=e.departamento_id
        LEFT JOIN subdepartamentos sd ON sd.id=e.subdepartamento_id
        WHERE e.empresa_id=? AND COALESCE(e.activo,1)=1
          AND (? IS NULL OR COALESCE(e.centro_id, ?) = ?)
        """,
        (empresa_id, centro_id, centro_id, centro_id),
    ).fetchall()
    formaciones = conn.execute(
        """
        SELECT *
        FROM prl_formaciones
        WHERE empresa_id=? AND centro_id=? AND COALESCE(activo,1)=1
        """,
        (empresa_id, centro_id),
    ).fetchall()
    creadas = 0
    for empleado in empleados:
        for formacion in _formaciones_prl_para_empleado(empleado, formaciones):
            before = conn.total_changes
            conn.execute(
                """
                INSERT OR IGNORE INTO prl_formacion_empleados
                (formacion_id, empleado_id, estado, empresa_id, centro_id)
                VALUES (?, ?, 'pendiente', ?, ?)
                """,
                (formacion["id"], empleado["id"], empresa_id, centro_id),
            )
            if conn.total_changes > before:
                creadas += 1
    return creadas


def _prl_pendientes_resumen(conn, empresa_id, centro_id):
    ensure_rrhh_schema(conn)
    rows = conn.execute(
        """
        SELECT COALESCE(d.nombre, 'Sin departamento') departamento,
               COUNT(*) total,
               SUM(CASE WHEN pfe.estado='pendiente' THEN 1 ELSE 0 END) pendientes,
               SUM(CASE WHEN pfe.estado='realizada' THEN 1 ELSE 0 END) realizadas
        FROM prl_formacion_empleados pfe
        JOIN empleados e ON e.id=pfe.empleado_id
        LEFT JOIN departamentos d ON d.id=e.departamento_id
        WHERE pfe.empresa_id=? AND pfe.centro_id=?
        GROUP BY COALESCE(d.nombre, 'Sin departamento')
        ORDER BY pendientes DESC, total DESC
        """,
        (empresa_id, centro_id),
    ).fetchall()
    total = conn.execute(
        """
        SELECT COUNT(*) total,
               SUM(CASE WHEN estado='pendiente' THEN 1 ELSE 0 END) pendientes,
               SUM(CASE WHEN estado='realizada' THEN 1 ELSE 0 END) realizadas
        FROM prl_formacion_empleados
        WHERE empresa_id=? AND centro_id=?
        """,
        (empresa_id, centro_id),
    ).fetchone()
    return total or {}, rows


PRL_MATERIALES = {
    "PRL-FOR-MANIP": {
        "titulo": "Seguridad alimentaria y manipulador de alimentos",
        "path": ROOT / "docs" / "formacion" / "seguridad_alimentaria_manipulador.md",
    },
    "PRL-FOR-APPCC": {
        "titulo": "Seguridad alimentaria y manipulador de alimentos",
        "path": ROOT / "docs" / "formacion" / "seguridad_alimentaria_manipulador.md",
    },
    "PRL-FOR-COC": {
        "titulo": "Informacion PRL - Trabajos en cocinas",
        "path": ROOT / "docs" / "formacion" / "prl_cocinas_hosteleria_informacion_trabajador.md",
    },
    "PRL-FOR-SAL": {
        "titulo": "Informacion PRL - Sala, bares y buffet",
        "path": ROOT / "docs" / "formacion" / "prl_sala_bares_buffet_informacion_trabajador.md",
    },
    "PRL-FOR-PIS": {
        "titulo": "Informacion PRL - Pisos y limpieza",
        "path": ROOT / "docs" / "formacion" / "prl_pisos_limpieza_informacion_trabajador.md",
    },
    "PRL-FOR-MAN": {
        "titulo": "Informacion PRL - Mantenimiento",
        "path": ROOT / "docs" / "formacion" / "prl_mantenimiento_hosteleria_informacion_trabajador.md",
    },
    "PRL-FOR-REC": {
        "titulo": "Informacion PRL - Recepcion y administracion",
        "path": ROOT / "docs" / "formacion" / "prl_recepcion_administracion_informacion_trabajador.md",
    },
    "PRL-FOR-ANI": {
        "titulo": "Informacion PRL - Animacion y eventos",
        "path": ROOT / "docs" / "formacion" / "prl_animacion_eventos_informacion_trabajador.md",
    },
    "PRL-FOR-EME": {
        "titulo": "Informacion PRL - Emergencias y evacuacion",
        "path": ROOT / "docs" / "formacion" / "prl_emergencias_evacuacion_informacion_trabajador.md",
    },
}


def _insertar_documento_portal_empleado(conn, empleado_id, archivo, tipo_documento, nombre, observaciones=None):
    if not archivo or not getattr(archivo, "filename", None):
        return None
    ruta = _guardar_archivo_empleado(empleado_id, archivo)
    nombre = (nombre or archivo.filename or "Documento empleado").strip()
    tipo_documento = (tipo_documento or "Documento empleado").strip()
    conn.execute(
        """
        INSERT INTO documentos_rrhh
        (empleado_id, tipo_documento, nombre, fecha_documento, archivo, estado, observaciones,
         empresa_id, visible_empleado, origen, subido_por_usuario_id)
        VALUES (?, ?, ?, ?, ?, 'pendiente_revision', ?, ?, 1, 'portal_empleado', ?)
        """,
        (
            empleado_id,
            tipo_documento,
            nombre,
            date.today().isoformat(),
            ruta,
            observaciones,
            _empresa_id(),
            session.get("user_id"),
        ),
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]



def _listas_maestras(conn):
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    centro_sql = " AND centro_id = ?" if centro_id else ""
    centro_params = [centro_id] if centro_id else []
    categorias = conn.execute(
        "SELECT id, nombre FROM categorias_laborales WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre",
        (empresa_id,),
    ).fetchall()
    departamentos = conn.execute(
        "SELECT id, nombre FROM departamentos WHERE empresa_id = ? AND COALESCE(activo, 1) = 1" + centro_sql + " ORDER BY nombre",
        [empresa_id] + centro_params,
    ).fetchall()
    subdepartamentos = conn.execute(
        "SELECT id, nombre, departamento_id FROM subdepartamentos WHERE empresa_id = ? AND COALESCE(activo, 1) = 1" + centro_sql + " ORDER BY nombre",
        [empresa_id] + centro_params,
    ).fetchall() if _table_exists(conn, "subdepartamentos") else []
    puntos_venta = conn.execute(
        "SELECT id, nombre FROM puntos_venta WHERE empresa_id = ? AND COALESCE(activo, 1) = 1" + centro_sql + " ORDER BY nombre",
        [empresa_id] + centro_params,
    ).fetchall() if _table_exists(conn, "puntos_venta") else []
    roles = conn.execute("SELECT id, nombre FROM roles ORDER BY nombre").fetchall() if _table_exists(conn, "roles") else []
    return categorias, departamentos, subdepartamentos, puntos_venta, roles


# ============================================================
# SELECCION DE PERSONAL
# ============================================================

@rrhh_bp.route("/seleccion")
@login_required
@_require_rrhh_global
def seleccion():
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_seleccion_schema(conn)
    config = _seleccion_config(conn, empresa_id)

    ofertas = conn.execute(
        """
        SELECT o.*,
               d.nombre AS departamento_nombre,
               sd.nombre AS subdepartamento_nombre,
               COUNT(c.id) AS candidatos_total,
               SUM(CASE WHEN c.estado IN ('preseleccionado', 'entrevista', 'reserva', 'seleccionado') THEN 1 ELSE 0 END) AS candidatos_interesantes
        FROM rrhh_ofertas_trabajo o
        LEFT JOIN departamentos d ON o.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON o.subdepartamento_id = sd.id
        LEFT JOIN rrhh_candidatos c ON c.oferta_id = o.id AND c.empresa_id = o.empresa_id
        WHERE o.empresa_id = ?
        GROUP BY o.id
        ORDER BY CASE COALESCE(o.estado, 'abierta') WHEN 'abierta' THEN 0 WHEN 'pausada' THEN 1 ELSE 2 END,
                 datetime(o.fecha_creacion) DESC
        """,
        (empresa_id,),
    ).fetchall()
    candidatos = conn.execute(
        """
        SELECT c.*, o.titulo AS oferta_titulo
        FROM rrhh_candidatos c
        LEFT JOIN rrhh_ofertas_trabajo o ON c.oferta_id = o.id AND o.empresa_id = c.empresa_id
        WHERE c.empresa_id = ?
        ORDER BY datetime(c.fecha_creacion) DESC
        LIMIT 80
        """,
        (empresa_id,),
    ).fetchall()
    estados_rows = conn.execute(
        """
        SELECT COALESCE(estado, 'recibido') AS estado, COUNT(*) AS total
        FROM rrhh_candidatos
        WHERE empresa_id = ?
        GROUP BY COALESCE(estado, 'recibido')
        """,
        (empresa_id,),
    ).fetchall()
    resumen_estados = {codigo: 0 for codigo, _ in SELECCION_ESTADOS}
    for row in estados_rows:
        resumen_estados[row["estado"]] = row["total"]
    emails_pendientes = _seleccion_emails_pendientes(conn, empresa_id, solo_vencidos=True)
    conn.close()
    return render_template(
        "rrhh/seleccion.html",
        ofertas=ofertas,
        candidatos=candidatos,
        resumen_estados=resumen_estados,
        estados=SELECCION_ESTADOS,
        emails_pendientes=emails_pendientes,
        config=config,
    )


@rrhh_bp.route("/seleccion/configuracion", methods=["POST"])
@login_required
@_require_rrhh_global
def seleccion_configuracion():
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_seleccion_schema(conn)
    existe = conn.execute(
        "SELECT id FROM rrhh_seleccion_config WHERE empresa_id = ? ORDER BY id DESC LIMIT 1",
        (empresa_id,),
    ).fetchone()
    datos = (
        max(0, _to_int(request.form.get("dias_respuesta_descarte"), 7)),
        1 if request.form.get("envio_automatico") == "1" else 0,
        request.form.get("asunto_descarte") or "Proceso de seleccion - {oferta}",
        request.form.get("plantilla_descarte") or "",
        session.get("user_id"),
        empresa_id,
    )
    if existe:
        conn.execute(
            """
            UPDATE rrhh_seleccion_config
            SET dias_respuesta_descarte = ?, envio_automatico = ?, asunto_descarte = ?,
                plantilla_descarte = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP
            WHERE empresa_id = ? AND id = ?
            """,
            datos + (existe["id"],),
        )
        _registrar_historial(conn, "rrhh_seleccion_config", existe["id"], "editar", "Configuracion seleccion")
    else:
        cur = conn.execute(
            """
            INSERT INTO rrhh_seleccion_config
            (dias_respuesta_descarte, envio_automatico, asunto_descarte, plantilla_descarte, updated_by, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            datos,
        )
        _registrar_historial(conn, "rrhh_seleccion_config", cur.lastrowid, "crear", "Configuracion seleccion")
    conn.commit()
    conn.close()
    flash("Configuracion de seleccion actualizada.", "success")
    return redirect(url_for("rrhh.seleccion"))


@rrhh_bp.route("/seleccion/ofertas/nueva", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def seleccion_nueva_oferta():
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_seleccion_schema(conn)
    config = _seleccion_config(conn, empresa_id)
    categorias, departamentos, subdepartamentos, _puntos_venta, _roles = _listas_maestras(conn)
    centros = _centros_select(conn) if _table_exists(conn, "centros_trabajo") else []
    puestos = _puestos_select(conn) if _table_exists(conn, "puestos_operativos") else []
    if request.method == "POST":
        titulo = (request.form.get("titulo") or "").strip()
        if not titulo:
            flash("Indica el titulo de la oferta.", "warning")
        else:
            cur = conn.execute(
                """
                INSERT INTO rrhh_ofertas_trabajo
                (empresa_id, titulo, departamento_id, subdepartamento_id, centro_id, puesto_id, categoria_id,
                 tipo_contrato, jornada, salario_desde, salario_hasta, fecha_inicio, fecha_cierre, estado,
                 descripcion, requisitos, competencias_clave, dias_respuesta_descarte, asunto_descarte,
                 plantilla_descarte, creado_por)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    empresa_id,
                    titulo,
                    _to_int_or_none(request.form.get("departamento_id")),
                    _to_int_or_none(request.form.get("subdepartamento_id")),
                    _to_int_or_none(request.form.get("centro_id")),
                    _to_int_or_none(request.form.get("puesto_id")),
                    _to_int_or_none(request.form.get("categoria_id")),
                    request.form.get("tipo_contrato"),
                    request.form.get("jornada"),
                    _to_float(request.form.get("salario_desde")),
                    _to_float(request.form.get("salario_hasta")),
                    request.form.get("fecha_inicio"),
                    request.form.get("fecha_cierre"),
                    request.form.get("estado") or "abierta",
                    request.form.get("descripcion"),
                    request.form.get("requisitos"),
                    request.form.get("competencias_clave"),
                    _to_int(request.form.get("dias_respuesta_descarte"), config.get("dias_respuesta_descarte", 7)),
                    request.form.get("asunto_descarte"),
                    request.form.get("plantilla_descarte"),
                    session.get("user_id"),
                ),
            )
            _registrar_historial(conn, "rrhh_ofertas_trabajo", cur.lastrowid, "crear", "Nueva oferta de trabajo")
            conn.commit()
            conn.close()
            flash("Oferta creada correctamente.", "success")
            return redirect(url_for("rrhh.seleccion_oferta_detalle", oferta_id=cur.lastrowid))
    conn.close()
    return render_template(
        "rrhh/oferta_form.html",
        oferta=None,
        config=config,
        categorias=categorias,
        departamentos=departamentos,
        subdepartamentos=subdepartamentos,
        centros=centros,
        puestos=puestos,
    )


@rrhh_bp.route("/seleccion/ofertas/<int:oferta_id>")
@login_required
@_require_rrhh_global
def seleccion_oferta_detalle(oferta_id):
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_seleccion_schema(conn)
    oferta = conn.execute(
        """
        SELECT o.*, d.nombre AS departamento_nombre, sd.nombre AS subdepartamento_nombre,
               c.nombre AS categoria_nombre, ct.nombre AS centro_nombre, p.nombre AS puesto_nombre
        FROM rrhh_ofertas_trabajo o
        LEFT JOIN departamentos d ON o.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON o.subdepartamento_id = sd.id
        LEFT JOIN categorias_laborales c ON o.categoria_id = c.id
        LEFT JOIN centros_trabajo ct ON o.centro_id = ct.id
        LEFT JOIN puestos_operativos p ON o.puesto_id = p.id
        WHERE o.id = ? AND o.empresa_id = ?
        """,
        (oferta_id, empresa_id),
    ).fetchone()
    if not oferta:
        conn.close()
        abort(404)
    candidatos = conn.execute(
        """
        SELECT *
        FROM rrhh_candidatos
        WHERE empresa_id = ? AND oferta_id = ?
        ORDER BY puntuacion_total DESC, datetime(fecha_creacion) DESC
        """,
        (empresa_id, oferta_id),
    ).fetchall()
    conn.close()
    return render_template("rrhh/oferta_detalle.html", oferta=oferta, candidatos=candidatos, estados=SELECCION_ESTADOS)


@rrhh_bp.route("/seleccion/candidatos/nuevo", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def seleccion_nuevo_candidato():
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_seleccion_schema(conn)
    ofertas = conn.execute(
        "SELECT id, titulo, estado FROM rrhh_ofertas_trabajo WHERE empresa_id = ? ORDER BY datetime(fecha_creacion) DESC",
        (empresa_id,),
    ).fetchall()
    oferta_id_pre = _to_int_or_none(request.args.get("oferta_id"))
    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            flash("Indica el nombre del candidato.", "warning")
        else:
            try:
                cv_path, cv_filename = _guardar_cv_seleccion(empresa_id, request.files.get("cv"))
            except ValueError as exc:
                flash(str(exc), "warning")
                cv_path, cv_filename = None, None
            total, valoraciones = _seleccion_calcular_valoraciones(request.form)
            estado = request.form.get("estado") or ("preseleccionado" if total >= 70 else "recibido")
            cur = conn.execute(
                """
                INSERT INTO rrhh_candidatos
                (empresa_id, oferta_id, nombre, apellidos, email, telefono, fuente, cv_path, cv_filename,
                 carta_presentacion, experiencia_texto, formacion_texto, disponibilidad, salario_esperado,
                 notas, puntuacion_total, recomendacion_ia, estado, privacidad_aceptada, evaluado_por)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    empresa_id,
                    _to_int_or_none(request.form.get("oferta_id")),
                    nombre,
                    request.form.get("apellidos"),
                    request.form.get("email"),
                    request.form.get("telefono"),
                    request.form.get("fuente"),
                    cv_path,
                    cv_filename,
                    request.form.get("carta_presentacion"),
                    request.form.get("experiencia_texto"),
                    request.form.get("formacion_texto"),
                    request.form.get("disponibilidad"),
                    _to_float(request.form.get("salario_esperado")),
                    request.form.get("notas"),
                    total,
                    _seleccion_recomendacion(total),
                    estado,
                    1 if request.form.get("privacidad_aceptada") == "1" else 0,
                    session.get("user_id"),
                ),
            )
            candidato_id = cur.lastrowid
            _seleccion_guardar_valoraciones(conn, candidato_id, empresa_id, valoraciones)
            _registrar_historial(conn, "rrhh_candidatos", candidato_id, "crear", "Nuevo candidato")
            conn.commit()
            conn.close()
            flash("Candidato registrado y valorado.", "success")
            return redirect(url_for("rrhh.seleccion_candidato_detalle", candidato_id=candidato_id))
    conn.close()
    return render_template(
        "rrhh/candidato_form.html",
        candidato=None,
        ofertas=ofertas,
        estados=SELECCION_ESTADOS,
        criterios=SELECCION_CRITERIOS,
        oferta_id_pre=oferta_id_pre,
    )


@rrhh_bp.route("/seleccion/candidatos/<int:candidato_id>")
@login_required
@_require_rrhh_global
def seleccion_candidato_detalle(candidato_id):
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_seleccion_schema(conn)
    candidato = conn.execute(
        """
        SELECT c.*, o.titulo AS oferta_titulo, o.dias_respuesta_descarte AS oferta_dias_descarte
        FROM rrhh_candidatos c
        LEFT JOIN rrhh_ofertas_trabajo o ON c.oferta_id = o.id AND o.empresa_id = c.empresa_id
        WHERE c.id = ? AND c.empresa_id = ?
        """,
        (candidato_id, empresa_id),
    ).fetchone()
    if not candidato:
        conn.close()
        abort(404)
    valoraciones = conn.execute(
        """
        SELECT *
        FROM rrhh_candidato_valoraciones
        WHERE candidato_id = ? AND empresa_id = ?
        ORDER BY id
        """,
        (candidato_id, empresa_id),
    ).fetchall()
    oferta = None
    if candidato["oferta_id"]:
        oferta = conn.execute(
            "SELECT * FROM rrhh_ofertas_trabajo WHERE id = ? AND empresa_id = ?",
            (candidato["oferta_id"], empresa_id),
        ).fetchone()
    config = _seleccion_config(conn, empresa_id)
    conn.close()
    return render_template(
        "rrhh/candidato_detalle.html",
        candidato=candidato,
        oferta=oferta,
        valoraciones=valoraciones,
        valoraciones_map={v["criterio"]: v for v in valoraciones},
        criterios=SELECCION_CRITERIOS,
        estados=SELECCION_ESTADOS,
        config=config,
    )


@rrhh_bp.route("/seleccion/candidatos/<int:candidato_id>/cv")
@login_required
@_require_rrhh_global
def seleccion_descargar_cv(candidato_id):
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_seleccion_schema(conn)
    candidato = conn.execute(
        "SELECT cv_path, cv_filename FROM rrhh_candidatos WHERE id = ? AND empresa_id = ?",
        (candidato_id, empresa_id),
    ).fetchone()
    conn.close()
    if not candidato or not candidato["cv_path"]:
        abort(404)
    return _enviar_archivo_rrhh(candidato["cv_path"], candidato["cv_filename"] or None)


@rrhh_bp.route("/seleccion/candidatos/<int:candidato_id>/valorar", methods=["POST"])
@login_required
@_require_rrhh_global
def seleccion_valorar_candidato(candidato_id):
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_seleccion_schema(conn)
    candidato = conn.execute(
        "SELECT id FROM rrhh_candidatos WHERE id = ? AND empresa_id = ?",
        (candidato_id, empresa_id),
    ).fetchone()
    if not candidato:
        conn.close()
        abort(404)
    total, valoraciones = _seleccion_calcular_valoraciones(request.form)
    conn.execute(
        """
        UPDATE rrhh_candidatos
        SET puntuacion_total = ?, recomendacion_ia = ?, evaluado_por = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (total, _seleccion_recomendacion(total), session.get("user_id"), candidato_id, empresa_id),
    )
    _seleccion_guardar_valoraciones(conn, candidato_id, empresa_id, valoraciones)
    _registrar_historial(conn, "rrhh_candidatos", candidato_id, "valorar", "Valoracion CV actualizada")
    conn.commit()
    conn.close()
    flash("Valoracion actualizada.", "success")
    return redirect(url_for("rrhh.seleccion_candidato_detalle", candidato_id=candidato_id))


@rrhh_bp.route("/seleccion/candidatos/<int:candidato_id>/estado", methods=["POST"])
@login_required
@_require_rrhh_global
def seleccion_estado_candidato(candidato_id):
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_seleccion_schema(conn)
    candidato = conn.execute(
        "SELECT * FROM rrhh_candidatos WHERE id = ? AND empresa_id = ?",
        (candidato_id, empresa_id),
    ).fetchone()
    if not candidato:
        conn.close()
        abort(404)
    estado = request.form.get("estado") or "recibido"
    estados_validos = {codigo for codigo, _ in SELECCION_ESTADOS}
    if estado not in estados_validos:
        estado = "recibido"
    motivo = (request.form.get("motivo_descarte") or "").strip()
    campos = ["estado = ?", "updated_at = CURRENT_TIMESTAMP"]
    params = [estado]
    if estado == "descartado":
        oferta = None
        if candidato["oferta_id"]:
            oferta = conn.execute(
                "SELECT * FROM rrhh_ofertas_trabajo WHERE id = ? AND empresa_id = ?",
                (candidato["oferta_id"], empresa_id),
            ).fetchone()
        config = _seleccion_config(conn, empresa_id)
        dias = _seleccion_dias_descarte(oferta, config)
        fecha_programada = (date.today() + timedelta(days=dias)).isoformat()
        campos.extend([
            "motivo_descarte = ?",
            "fecha_descarte = CURRENT_TIMESTAMP",
            "descarte_email_programado = ?",
            "descarte_email_enviado_at = NULL",
            "descarte_email_error = NULL",
        ])
        params.extend([motivo, fecha_programada])
    elif estado in {"preseleccionado", "entrevista", "reserva", "seleccionado"}:
        campos.extend(["motivo_descarte = NULL", "fecha_descarte = NULL", "descarte_email_programado = NULL", "descarte_email_error = NULL"])
    params.extend([candidato_id, empresa_id])
    conn.execute(
        f"UPDATE rrhh_candidatos SET {', '.join(campos)} WHERE id = ? AND empresa_id = ?",
        params,
    )
    _registrar_historial(conn, "rrhh_candidatos", candidato_id, "cambiar_estado", f"Estado: {estado}. {motivo}")
    conn.commit()
    conn.close()
    flash("Estado del candidato actualizado.", "success")
    return redirect(url_for("rrhh.seleccion_candidato_detalle", candidato_id=candidato_id))


@rrhh_bp.route("/seleccion/emails/enviar", methods=["POST"])
@login_required
@_require_rrhh_global
def seleccion_enviar_emails_descarte():
    conn = get_db()
    empresa_id = _empresa_id()
    _ensure_seleccion_schema(conn)
    config = _seleccion_config(conn, empresa_id)
    empresa_nombre = _empresa_nombre_rrhh(conn, empresa_id)
    pendientes = _seleccion_emails_pendientes(conn, empresa_id, solo_vencidos=True)
    mail_instance = current_app.extensions.get("mail")
    enviados = 0
    errores = 0
    for candidato in pendientes:
        asunto, cuerpo = _seleccion_email_texto(candidato, config, empresa_nombre)
        ok, resultado = enviar_email(candidato["email"], asunto, cuerpo, html=True, mail_instance=mail_instance)
        if ok:
            enviados += 1
            conn.execute(
                """
                UPDATE rrhh_candidatos
                SET descarte_email_enviado_at = CURRENT_TIMESTAMP, descarte_email_error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND empresa_id = ?
                """,
                (candidato["id"], empresa_id),
            )
            _registrar_historial(conn, "rrhh_candidatos", candidato["id"], "email_descarte", "Email de descarte enviado")
        else:
            errores += 1
            conn.execute(
                """
                UPDATE rrhh_candidatos
                SET descarte_email_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND empresa_id = ?
                """,
                (resultado, candidato["id"], empresa_id),
            )
    conn.commit()
    conn.close()
    if enviados:
        flash(f"Emails de descarte enviados: {enviados}.", "success")
    if errores:
        flash(f"No se pudieron enviar {errores} emails. Revisa configuracion SMTP.", "warning")
    if not enviados and not errores:
        flash("No hay emails de descarte vencidos pendientes.", "info")
    return redirect(url_for("rrhh.seleccion"))


# ============================================================
# PANEL RRHH
# ============================================================

@rrhh_bp.route("/")
@login_required
def panel_rrhh():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    _ensure_control_tiempo_schema(conn)
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    prl = _prl_resumen(conn, empresa_id, centro_id or 3)
    scope_sql, scope_params = _departamento_scope_sql("e")

    total_empleados = conn.execute(f"SELECT COUNT(*) total FROM empleados e WHERE {scope_sql} AND COALESCE(e.activo, 1) = 1", scope_params).fetchone()["total"]
    total_contratos = conn.execute(
        f"""
        SELECT COUNT(*) total
        FROM contratos co
        JOIN empleados e ON co.empleado_id = e.id
        WHERE {scope_sql} AND co.empresa_id = ? AND COALESCE(co.estado, 'activo') = 'activo'
        """,
        scope_params + [empresa_id],
    ).fetchone()["total"]
    fichajes_hoy = conn.execute(
        f"""
        SELECT COUNT(*) total
        FROM fichajes f
        JOIN empleados e ON f.empleado_id = e.id
        WHERE {scope_sql} AND f.empresa_id = ? AND date(f.fecha_hora) = date('now')
        """,
        scope_params + [empresa_id],
    ).fetchone()["total"]
    nominas_pendientes = conn.execute(
        f"""
        SELECT COUNT(*) total
        FROM nominas n
        JOIN empleados e ON n.empleado_id = e.id
        WHERE {scope_sql} AND n.empresa_id = ? AND COALESCE(n.estado, 'pendiente') != 'pagada'
        """,
        scope_params + [empresa_id],
    ).fetchone()["total"]
    pendientes = _resumen_pendientes_rrhh(conn, empresa_id, scope_sql, scope_params)
    alertas_admin = _alertas_administrativas_rrhh(conn, empresa_id, scope_sql, scope_params)

    ultimos_fichajes = conn.execute(
        f"""
        SELECT f.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre, d.nombre departamento_nombre
        FROM fichajes f
        JOIN empleados e ON f.empleado_id = e.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        WHERE {scope_sql} AND f.empresa_id = ?
        ORDER BY f.fecha_hora DESC
        LIMIT 8
        """,
        scope_params + [empresa_id],
    ).fetchall()
    conn.close()

    return render_template(
        "rrhh/panel.html",
        total_empleados=total_empleados,
        total_contratos=total_contratos,
        fichajes_hoy=fichajes_hoy,
        nominas_pendientes=nominas_pendientes,
        pendientes=pendientes,
        alertas_admin=alertas_admin,
        ultimos_fichajes=ultimos_fichajes,
        prl=prl,
    )


@rrhh_bp.route("/prl", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def prl_operativa():
    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id() or 3
    ensure_rrhh_schema(conn)
    if request.method == "POST":
        tipo = (request.form.get("tipo") or "").strip()
        if tipo == "accion":
            titulo = (request.form.get("titulo") or "").strip()
            if titulo:
                conn.execute(
                    """
                    INSERT INTO prl_acciones
                    (riesgo_id, titulo, descripcion, prioridad, responsable, fecha_limite,
                     empresa_id, centro_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.form.get("riesgo_id") or None,
                        titulo,
                        request.form.get("descripcion"),
                        request.form.get("prioridad") or "media",
                        request.form.get("responsable"),
                        request.form.get("fecha_limite"),
                        empresa_id,
                        centro_id,
                    ),
                )
                flash("Accion PRL registrada.", "success")
        elif tipo == "incidente":
            descripcion = (request.form.get("descripcion") or "").strip()
            if descripcion:
                conn.execute(
                    """
                    INSERT INTO prl_incidentes
                    (fecha, empleado_id, area, tipo, descripcion, lesion, gravedad,
                     causa_probable, accion_correctiva, empresa_id, centro_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.form.get("fecha") or datetime.now().strftime("%Y-%m-%d"),
                        request.form.get("empleado_id") or None,
                        request.form.get("area"),
                        request.form.get("incidente_tipo"),
                        descripcion,
                        request.form.get("lesion"),
                        request.form.get("gravedad") or "leve",
                        request.form.get("causa_probable"),
                        request.form.get("accion_correctiva"),
                        empresa_id,
                        centro_id,
                    ),
                )
                flash("Incidente o casi accidente PRL registrado.", "success")
        elif tipo == "asignar_formaciones":
            creadas = _asignar_formaciones_prl(conn, empresa_id, centro_id)
            flash(f"Formaciones PRL asignadas: {creadas}.", "success" if creadas else "info")
        conn.commit()
        conn.close()
        return redirect(url_for("rrhh.prl_operativa"))

    resumen = _prl_resumen(conn, empresa_id, centro_id)
    prl_formacion_total, prl_pendientes_departamento = _prl_pendientes_resumen(conn, empresa_id, centro_id)
    carnet_resumen, carnet_alertas = _prl_carnet_manipulador_estado(conn, empresa_id, centro_id)
    riesgos = conn.execute(
        """
        SELECT *
        FROM prl_riesgos
        WHERE empresa_id=? AND centro_id=? AND COALESCE(activo,1)=1
        ORDER BY CASE nivel WHEN 'critico' THEN 1 WHEN 'alto' THEN 2 WHEN 'medio' THEN 3 ELSE 4 END,
                 area, codigo
        """,
        (empresa_id, centro_id),
    ).fetchall()
    acciones = conn.execute(
        """
        SELECT a.*, r.codigo AS riesgo_codigo, r.riesgo AS riesgo_nombre
        FROM prl_acciones a
        LEFT JOIN prl_riesgos r ON r.id=a.riesgo_id
        WHERE a.empresa_id=? AND a.centro_id=?
        ORDER BY CASE a.prioridad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 WHEN 'media' THEN 3 ELSE 4 END,
                 CASE a.estado WHEN 'pendiente' THEN 1 WHEN 'en_curso' THEN 2 ELSE 3 END,
                 date(a.fecha_limite)
        LIMIT 80
        """,
        (empresa_id, centro_id),
    ).fetchall()
    formaciones = conn.execute(
        """
        SELECT *
        FROM prl_formaciones
        WHERE empresa_id=? AND centro_id=? AND COALESCE(activo,1)=1
        ORDER BY area, titulo
        """,
        (empresa_id, centro_id),
    ).fetchall()
    formacion_empleados = conn.execute(
        """
        SELECT pfe.*, f.codigo, f.titulo, f.area,
               TRIM(e.nombre || ' ' || COALESCE(e.apellidos,'')) empleado_nombre,
               d.nombre AS departamento_nombre
        FROM prl_formacion_empleados pfe
        JOIN prl_formaciones f ON f.id=pfe.formacion_id
        JOIN empleados e ON e.id=pfe.empleado_id
        LEFT JOIN departamentos d ON d.id=e.departamento_id
        WHERE pfe.empresa_id=? AND pfe.centro_id=?
          AND pfe.estado='pendiente'
        ORDER BY d.nombre, empleado_nombre, f.area, f.titulo
        LIMIT 120
        """,
        (empresa_id, centro_id),
    ).fetchall()
    incidentes = conn.execute(
        """
        SELECT i.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos,'')) empleado_nombre
        FROM prl_incidentes i
        LEFT JOIN empleados e ON e.id=i.empleado_id
        WHERE i.empresa_id=? AND i.centro_id=?
        ORDER BY datetime(i.fecha) DESC, i.id DESC
        LIMIT 80
        """,
        (empresa_id, centro_id),
    ).fetchall()
    empleados = conn.execute(
        """
        SELECT id, TRIM(nombre || ' ' || COALESCE(apellidos,'')) nombre
        FROM empleados
        WHERE empresa_id=? AND COALESCE(activo,1)=1
        ORDER BY nombre
        LIMIT 300
        """,
        (empresa_id,),
    ).fetchall()
    conn.close()
    return render_template(
        "rrhh/prl.html",
        resumen=resumen,
        riesgos=riesgos,
        acciones=acciones,
        formaciones=formaciones,
        materiales=PRL_MATERIALES,
        formacion_empleados=formacion_empleados,
        prl_formacion_total=prl_formacion_total,
        prl_pendientes_departamento=prl_pendientes_departamento,
        carnet_resumen=carnet_resumen,
        carnet_alertas=carnet_alertas,
        incidentes=incidentes,
        empleados=empleados,
    )


@rrhh_bp.route("/prl/material/<codigo>")
@login_required
@_require_rrhh_global
def prl_material_formacion(codigo):
    codigo = (codigo or "").strip().upper()
    material = PRL_MATERIALES.get(codigo)
    if not material:
        flash("No hay material asociado a esta formacion.", "warning")
        return redirect(url_for("rrhh.prl_operativa"))
    path = material["path"]
    if not path.exists():
        flash("El documento de formacion no existe todavia.", "warning")
        return redirect(url_for("rrhh.prl_operativa"))
    return send_file(path, mimetype="text/markdown", as_attachment=False)


@rrhh_bp.route("/pendientes")
@login_required
@_require_rrhh_global
def pendientes_rrhh():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    _ensure_control_tiempo_schema(conn)
    empresa_id = _empresa_id()
    scope_sql, scope_params = _departamento_scope_sql("e")
    resumen = _resumen_pendientes_rrhh(conn, empresa_id, scope_sql, scope_params)

    correcciones = conn.execute(
        f"""
        SELECT s.*,
               TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre,
               d.nombre departamento_nombre
        FROM solicitudes_correccion_fichaje s
        JOIN empleados e ON s.empleado_id = e.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        WHERE {scope_sql}
          AND s.empresa_id = ?
          AND COALESCE(s.estado, 'pendiente') = 'pendiente'
        ORDER BY datetime(s.fecha_solicitud) ASC, s.id ASC
        LIMIT 100
        """,
        scope_params + [empresa_id],
    ).fetchall()
    solicitudes = conn.execute(
        f"""
        SELECT v.*,
               TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre,
               d.nombre departamento_nombre,
               doc.nombre documento_nombre,
               doc.archivo documento_archivo
        FROM vacaciones_rrhh v
        JOIN empleados e ON v.empleado_id = e.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        LEFT JOIN documentos_rrhh doc ON v.documento_id = doc.id AND doc.empresa_id = v.empresa_id
        WHERE {scope_sql}
          AND v.empresa_id = ?
          AND COALESCE(v.estado, 'solicitada') = 'solicitada'
        ORDER BY date(v.fecha_inicio) ASC, v.id ASC
        LIMIT 100
        """,
        scope_params + [empresa_id],
    ).fetchall()
    documentos = conn.execute(
        f"""
        SELECT doc.*,
               TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre,
               d.nombre departamento_nombre
        FROM documentos_rrhh doc
        JOIN empleados e ON doc.empleado_id = e.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        WHERE {scope_sql}
          AND doc.empresa_id = ?
          AND LOWER(COALESCE(doc.estado, 'vigente')) = 'pendiente_revision'
        ORDER BY datetime(COALESCE(doc.fecha_creacion, doc.updated_at, '1900-01-01')) ASC, doc.id ASC
        LIMIT 100
        """,
        scope_params + [empresa_id],
    ).fetchall()
    contratos = conn.execute(
        f"""
        SELECT co.*,
               TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre,
               d.nombre departamento_nombre
        FROM contratos co
        JOIN empleados e ON co.empleado_id = e.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        WHERE {scope_sql}
          AND co.empresa_id = ?
          AND COALESCE(co.estado, 'activo') NOT IN ('anulado', 'cancelado', 'borrado')
          AND COALESCE(co.firmado_empleado, 0) = 0
        ORDER BY date(COALESCE(co.fecha_inicio, co.fecha_creacion, '1900-01-01')) DESC, co.id DESC
        LIMIT 100
        """,
        scope_params + [empresa_id],
    ).fetchall()
    conn.close()

    return render_template(
        "rrhh/pendientes.html",
        resumen=resumen,
        correcciones=correcciones,
        solicitudes=solicitudes,
        documentos=documentos,
        contratos=contratos,
    )


@rrhh_bp.route("/portal/auditoria")
@login_required
@_require_rrhh_global
def auditoria_portal_empleado():
    conn = get_db()
    ensure_rrhh_schema(conn)
    empresa_id = _empresa_id()
    empleado_id = _to_int_or_none(request.args.get("empleado_id"))
    accion = (request.args.get("accion") or "").strip()
    desde = request.args.get("desde") or ""
    hasta = request.args.get("hasta") or ""

    filtros = ["a.empresa_id = ?"]
    params = [empresa_id]
    if empleado_id:
        filtros.append("a.empleado_id = ?")
        params.append(empleado_id)
    if accion:
        filtros.append("a.accion = ?")
        params.append(accion)
    if desde:
        filtros.append("date(a.fecha) >= date(?)")
        params.append(desde)
    if hasta:
        filtros.append("date(a.fecha) <= date(?)")
        params.append(hasta)

    where_sql = " AND ".join(filtros)
    accesos = conn.execute(
        f"""
        SELECT a.*,
               TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) AS empleado_nombre,
               u.codigo_acceso,
               u.email AS usuario_email
        FROM rrhh_portal_accesos a
        LEFT JOIN empleados e ON a.empleado_id = e.id AND e.empresa_id = a.empresa_id
        LEFT JOIN usuarios u ON a.usuario_id = u.id AND u.empresa_id = a.empresa_id
        WHERE {where_sql}
        ORDER BY datetime(a.fecha) DESC, a.id DESC
        LIMIT 500
        """,
        params,
    ).fetchall()
    acciones = conn.execute(
        """
        SELECT DISTINCT accion
        FROM rrhh_portal_accesos
        WHERE empresa_id = ?
        ORDER BY accion
        """,
        (empresa_id,),
    ).fetchall()
    empleados = conn.execute(
        """
        SELECT id, nombre, apellidos
        FROM empleados
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY nombre, apellidos
        """,
        (empresa_id,),
    ).fetchall()
    conn.close()
    return render_template(
        "rrhh/portal_auditoria.html",
        accesos=accesos,
        acciones=acciones,
        empleados=empleados,
        filtros={
            "empleado_id": empleado_id or "",
            "accion": accion,
            "desde": desde,
            "hasta": hasta,
        },
    )


# ============================================================
# EMPLEADOS
# ============================================================

@rrhh_bp.route("/empleados")
@login_required
def lista_empleados():
    conn = get_db()
    ensure_rrhh_schema(conn)
    scope_sql, scope_params = _departamento_scope_sql("e")
    empleados_rows = conn.execute(
        f"""
        SELECT e.*, c.nombre categoria_nombre, d.nombre departamento_nombre,
               sd.nombre subdepartamento_nombre, pv.nombre punto_venta_nombre,
               u.nombre usuario_nombre, u.codigo_acceso usuario_codigo_acceso, u.activo usuario_activo
        FROM empleados e
        LEFT JOIN categorias_laborales c ON e.categoria_id = c.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON e.subdepartamento_id = sd.id
        LEFT JOIN puntos_venta pv ON e.punto_venta_id = pv.id
        LEFT JOIN usuarios u
               ON u.empresa_id = e.empresa_id
              AND (u.id = e.usuario_id OR (e.usuario_id IS NULL AND u.empleado_id = e.id))
        WHERE {scope_sql}
        ORDER BY COALESCE(e.activo, 1) DESC, e.nombre, e.apellidos
        """,
        scope_params,
    ).fetchall()
    empleados = []
    empleados_vistos = set()
    for empleado in empleados_rows:
        if empleado["id"] in empleados_vistos:
            continue
        empleados_vistos.add(empleado["id"])
        empleados.append(empleado)
    conn.close()
    return render_template("rrhh/empleados.html", empleados=empleados, puede_editar=_usuario_es_global_rrhh())


@rrhh_bp.route("/empleados/nuevo", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def nuevo_empleado():
    conn = get_db()
    ensure_rrhh_schema(conn)
    _ensure_auth_schema(conn)
    empresa_id = _empresa_id()

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            flash("El nombre del empleado es obligatorio.", "warning")
            return redirect(url_for("rrhh.nuevo_empleado"))
        
        campos = {
            "nombre": nombre,
            "apellidos": request.form.get("apellidos"),
            "nif": request.form.get("nif"),
            "numero_seguridad_social": request.form.get("numero_seguridad_social"),
            "email": request.form.get("email"),
            "telefono": request.form.get("telefono"),
            "fecha_nacimiento": request.form.get("fecha_nacimiento"),
            "nacionalidad": request.form.get("nacionalidad"),
            "estado_civil": request.form.get("estado_civil"),
            "direccion": request.form.get("direccion"),
            "codigo_postal": request.form.get("codigo_postal"),
            "localidad": request.form.get("localidad"),
            "provincia": request.form.get("provincia"),
            "pais": request.form.get("pais"),
            "iban": request.form.get("iban"),
            "contacto_emergencia_nombre": request.form.get("contacto_emergencia_nombre"),
            "contacto_emergencia_telefono": request.form.get("contacto_emergencia_telefono"),
            "contacto_emergencia_relacion": request.form.get("contacto_emergencia_relacion"),
            "nivel_formacion": request.form.get("nivel_formacion"),
            "talla_uniforme": request.form.get("talla_uniforme"),
            "talla_calzado": request.form.get("talla_calzado"),
            "alergias_observaciones": request.form.get("alergias_observaciones"),
            "carnet_manipulador": 1 if request.form.get("carnet_manipulador") else 0,
            "fecha_caducidad_carnet": request.form.get("fecha_caducidad_carnet"),
            "prl_realizada": 1 if request.form.get("prl_realizada") else 0,
            "fecha_prl": request.form.get("fecha_prl"),
            "reconocimiento_medico": 1 if request.form.get("reconocimiento_medico") else 0,
            "fecha_reconocimiento": request.form.get("fecha_reconocimiento"),
            "categoria_id": request.form.get("categoria_id") or None,
            "centro_id": asignar_centro_desde_departamento(conn, request.form.get("departamento_id") or None),
            "departamento_id": request.form.get("departamento_id") or None,
            "subdepartamento_id": request.form.get("subdepartamento_id") or None,
            "punto_venta_id": request.form.get("punto_venta_id") or None,
            "rol": request.form.get("rol"),
            "usuario_id": request.form.get("usuario_id") or None,
            "fecha_alta": request.form.get("fecha_alta") or date.today().isoformat(),
            "observaciones": request.form.get("observaciones"),
            "irpf_pct": max(0.0, min(_to_float(request.form.get("irpf_pct")), 100.0)),
            "modelo145_situacion_familiar": request.form.get("modelo145_situacion_familiar"),
            "modelo145_conyuge_nif": request.form.get("modelo145_conyuge_nif"),
            "modelo145_discapacidad_pct": max(0.0, min(_to_float(request.form.get("modelo145_discapacidad_pct")), 100.0)),
            "modelo145_movilidad_geografica": 1 if request.form.get("modelo145_movilidad_geografica") else 0,
            "modelo145_hijos": max(0, _to_int(request.form.get("modelo145_hijos"), 0)),
            "modelo145_hijos_menores3": max(0, _to_int(request.form.get("modelo145_hijos_menores3"), 0)),
            "modelo145_ascendientes": max(0, _to_int(request.form.get("modelo145_ascendientes"), 0)),
            "modelo145_pension_compensatoria": max(0.0, _to_float(request.form.get("modelo145_pension_compensatoria"))),
            "modelo145_anualidades_alimentos": max(0.0, _to_float(request.form.get("modelo145_anualidades_alimentos"))),
            "modelo145_vivienda_habitual": 1 if request.form.get("modelo145_vivienda_habitual") else 0,
            "modelo145_fecha_firma": request.form.get("modelo145_fecha_firma"),
            "modelo145_fecha_actualizacion": request.form.get("modelo145_fecha_actualizacion") or request.form.get("modelo145_fecha_firma"),
            "modelo145_observaciones": request.form.get("modelo145_observaciones"),
            "empresa_id": empresa_id,
            "activo": 1,
        }
        columnas = ", ".join(campos.keys())
        placeholders = ", ".join("?" for _ in campos)
        cursor = conn.cursor()
        cursor.execute(f"INSERT INTO empleados ({columnas}) VALUES ({placeholders})", list(campos.values()))
        nuevo_id = cursor.lastrowid
        _vincular_usuario_empleado(conn, nuevo_id, request.form.get("usuario_id") or None, empresa_id)
        credencial_creada = None
        if request.form.get("crear_usuario_operativo"):
            password, error = _password_temporal_desde_form()
            if error:
                conn.rollback()
                conn.close()
                flash(error, "warning")
                return redirect(url_for("rrhh.nuevo_empleado"))
            _usuario_id, credencial_creada, error = _crear_usuario_operativo_empleado(
                conn,
                nuevo_id,
                empresa_id,
                request.form.get("rol_acceso") or request.form.get("rol") or "empleado",
                password,
            )
            if error:
                conn.rollback()
                conn.close()
                flash(error, "warning")
                return redirect(url_for("rrhh.nuevo_empleado"))
        _actualizar_pin_fichaje(conn, nuevo_id, request.form.get("pin_fichaje"))
        _registrar_historial(conn, "empleados", nuevo_id, "crear", "Alta inicial de empleado")
        conn.commit()
        conn.close()
        if credencial_creada:
            flash(f"Empleado creado. Acceso: {credencial_creada['codigo']} / {credencial_creada['email']}. Contrasena temporal: {credencial_creada['password']}", "success")
        else:
            flash("Empleado creado correctamente.", "success")
        return redirect(url_for("rrhh.lista_empleados"))

    categorias, departamentos, subdepartamentos, puntos_venta, roles = _listas_maestras(conn)
    usuarios = _usuarios_acceso_select(conn)
    conn.close()
    return render_template(
        "rrhh/empleado_form.html",
        empleado=None,
        categorias=categorias,
        departamentos=departamentos,
        subdepartamentos=subdepartamentos,
        puntos_venta=puntos_venta,
        roles=roles,
        usuarios=usuarios,
    )


@rrhh_bp.route("/empleados/<int:id>/editar", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def editar_empleado(id):
    conn = get_db()
    ensure_rrhh_schema(conn)
    _ensure_auth_schema(conn)
    empresa_id = _empresa_id()
    empleado = conn.execute("SELECT * FROM empleados WHERE id = ? AND empresa_id = ?", (id, empresa_id)).fetchone()
    if not empleado:
        conn.close()
        flash("Empleado no encontrado.", "danger")
        return redirect(url_for("rrhh.lista_empleados"))

    if request.method == "POST":
        campos = {
            "nombre": request.form.get("nombre"),
            "apellidos": request.form.get("apellidos"),
            "nif": request.form.get("nif"),
            "numero_seguridad_social": request.form.get("numero_seguridad_social"),
            "email": request.form.get("email"),
            "telefono": request.form.get("telefono"),
            "fecha_nacimiento": request.form.get("fecha_nacimiento"),
            "nacionalidad": request.form.get("nacionalidad"),
            "estado_civil": request.form.get("estado_civil"),
            "direccion": request.form.get("direccion"),
            "codigo_postal": request.form.get("codigo_postal"),
            "localidad": request.form.get("localidad"),
            "provincia": request.form.get("provincia"),
            "pais": request.form.get("pais"),
            "iban": request.form.get("iban"),
            "contacto_emergencia_nombre": request.form.get("contacto_emergencia_nombre"),
            "contacto_emergencia_telefono": request.form.get("contacto_emergencia_telefono"),
            "contacto_emergencia_relacion": request.form.get("contacto_emergencia_relacion"),
            "nivel_formacion": request.form.get("nivel_formacion"),
            "talla_uniforme": request.form.get("talla_uniforme"),
            "talla_calzado": request.form.get("talla_calzado"),
            "alergias_observaciones": request.form.get("alergias_observaciones"),
            "carnet_manipulador": 1 if request.form.get("carnet_manipulador") else 0,
            "fecha_caducidad_carnet": request.form.get("fecha_caducidad_carnet"),
            "prl_realizada": 1 if request.form.get("prl_realizada") else 0,
            "fecha_prl": request.form.get("fecha_prl"),
            "reconocimiento_medico": 1 if request.form.get("reconocimiento_medico") else 0,
            "fecha_reconocimiento": request.form.get("fecha_reconocimiento"),
            "categoria_id": request.form.get("categoria_id") or None,
            "centro_id": asignar_centro_desde_departamento(conn, request.form.get("departamento_id") or None, empleado["centro_id"] if "centro_id" in empleado.keys() else None),
            "departamento_id": request.form.get("departamento_id") or None,
            "subdepartamento_id": request.form.get("subdepartamento_id") or None,
            "punto_venta_id": request.form.get("punto_venta_id") or None,
            "rol": request.form.get("rol"),
            "usuario_id": request.form.get("usuario_id") or None,
            "fecha_alta": request.form.get("fecha_alta"),
            "fecha_baja": request.form.get("fecha_baja"),
            "irpf_pct": max(0.0, min(_to_float(request.form.get("irpf_pct")), 100.0)),
            "modelo145_situacion_familiar": request.form.get("modelo145_situacion_familiar"),
            "modelo145_conyuge_nif": request.form.get("modelo145_conyuge_nif"),
            "modelo145_discapacidad_pct": max(0.0, min(_to_float(request.form.get("modelo145_discapacidad_pct")), 100.0)),
            "modelo145_movilidad_geografica": 1 if request.form.get("modelo145_movilidad_geografica") else 0,
            "modelo145_hijos": max(0, _to_int(request.form.get("modelo145_hijos"), 0)),
            "modelo145_hijos_menores3": max(0, _to_int(request.form.get("modelo145_hijos_menores3"), 0)),
            "modelo145_ascendientes": max(0, _to_int(request.form.get("modelo145_ascendientes"), 0)),
            "modelo145_pension_compensatoria": max(0.0, _to_float(request.form.get("modelo145_pension_compensatoria"))),
            "modelo145_anualidades_alimentos": max(0.0, _to_float(request.form.get("modelo145_anualidades_alimentos"))),
            "modelo145_vivienda_habitual": 1 if request.form.get("modelo145_vivienda_habitual") else 0,
            "modelo145_fecha_firma": request.form.get("modelo145_fecha_firma"),
            "modelo145_fecha_actualizacion": request.form.get("modelo145_fecha_actualizacion") or request.form.get("modelo145_fecha_firma"),
            "modelo145_observaciones": request.form.get("modelo145_observaciones"),
            "observaciones": request.form.get("observaciones"),
            "activo": 1 if request.form.get("activo") else 0
        }
        _vincular_usuario_empleado(conn, id, request.form.get("usuario_id") or None, empresa_id)
        _safe_update(conn, "empleados", id, campos, request.form.get("motivo_cambio") or "Actualización de ficha")
        _actualizar_pin_fichaje(conn, id, request.form.get("pin_fichaje"))
        conn.commit()
        conn.close()
        flash("Empleado actualizado correctamente.", "success")
        return redirect(url_for("rrhh.lista_empleados"))

    categorias, departamentos, subdepartamentos, puntos_venta, roles = _listas_maestras(conn)
    usuarios = _usuarios_acceso_select(conn)
    _empleado_cred, credenciales_usuario = _usuario_acceso_empleado(conn, id, empresa_id)
    conn.close()
    return render_template(
        "rrhh/empleado_form.html",
        empleado=empleado,
        credenciales={"usuario": credenciales_usuario},
        categorias=categorias,
        departamentos=departamentos,
        subdepartamentos=subdepartamentos,
        puntos_venta=puntos_venta,
        roles=roles,
        usuarios=usuarios,
    )


# ============================================================
# CATEGORÍAS LABORALES
# ============================================================

@rrhh_bp.route("/empleados/<int:id>/credenciales", methods=["POST"])
@login_required
@_require_rrhh_global
def gestionar_credenciales_empleado(id):
    conn = get_db()
    ensure_rrhh_schema(conn)
    empresa_id = _empresa_id()
    empleado, usuario = _usuario_acceso_empleado(conn, id, empresa_id)
    if not empleado:
        conn.close()
        flash("Empleado no encontrado.", "danger")
        return redirect(url_for("rrhh.lista_empleados"))

    accion = request.form.get("accion")
    try:
        if accion == "crear_usuario":
            if usuario:
                flash("Este empleado ya tiene un usuario vinculado.", "warning")
                return redirect(url_for("rrhh.editar_empleado", id=id))
            password, error = _password_temporal_desde_form()
            if error:
                flash(error, "warning")
                return redirect(url_for("rrhh.editar_empleado", id=id))
            email = _email_unico_usuario(conn, empresa_id, id, request.form.get("email") or empleado["email"])
            codigo = _codigo_acceso_unico(conn, empresa_id, id)
            nombre_usuario = f"{empleado['nombre']} {empleado['apellidos'] or ''}".strip()
            rol_id = _rol_empleado_id(conn)
            cursor = conn.execute(
                """
                INSERT INTO usuarios (nombre, email, password_hash, rol_id, empresa_id, activo, empleado_id, codigo_acceso, debe_cambiar_password)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, 1)
                """,
                (nombre_usuario, email, generate_password_hash(password), rol_id, empresa_id, id, codigo),
            )
            usuario_id = cursor.lastrowid
            _vincular_usuario_empleado(conn, id, usuario_id, empresa_id)
            _registrar_historial(conn, "empleados", id, "crear_acceso_portal", "Usuario de portal creado por RRHH")
            conn.commit()
            flash(f"Acceso creado. Codigo: {codigo}. Contrasena temporal: {password}", "success")

        elif accion == "reset_password":
            if not usuario:
                flash("Este empleado no tiene usuario vinculado.", "warning")
                return redirect(url_for("rrhh.editar_empleado", id=id))
            password, error = _password_temporal_desde_form()
            if error:
                flash(error, "warning")
                return redirect(url_for("rrhh.editar_empleado", id=id))
            conn.execute(
                "UPDATE usuarios SET password_hash = ?, debe_cambiar_password = 1 WHERE id = ? AND empresa_id = ?",
                (generate_password_hash(password), usuario["id"], empresa_id),
            )
            _registrar_historial(conn, "empleados", id, "reset_password_portal", "Contrasena de portal reiniciada por RRHH")
            conn.commit()
            flash(f"Contrasena temporal nueva: {password}", "success")

        elif accion == "reset_pin":
            pin = (request.form.get("pin_fichaje") or "").strip() or _generar_pin_temporal()
            if not pin.isdigit() or not 4 <= len(pin) <= 8:
                flash("El PIN debe tener entre 4 y 8 digitos.", "warning")
                return redirect(url_for("rrhh.editar_empleado", id=id))
            _actualizar_pin_fichaje(conn, id, pin)
            _registrar_historial(conn, "empleados", id, "reset_pin_fichaje", "PIN de fichaje reiniciado por RRHH")
            conn.commit()
            flash(f"PIN de fichaje actualizado. Nuevo PIN: {pin}", "success")

        else:
            flash("Accion de credenciales no valida.", "warning")
    finally:
        conn.close()

    return redirect(url_for("rrhh.editar_empleado", id=id))


@rrhh_bp.route("/categorias")
@login_required
def lista_categorias():
    conn = get_db()
    ensure_rrhh_schema(conn)
    categorias = conn.execute(
        "SELECT * FROM categorias_laborales WHERE empresa_id = ? ORDER BY COALESCE(activo, 1) DESC, nombre",
        (_empresa_id(),),
    ).fetchall()
    conn.close()
    return render_template("rrhh/categorias.html", categorias=categorias, puede_editar=_usuario_es_global_rrhh())


@rrhh_bp.route("/categorias/nueva", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def nueva_categoria():
    conn = get_db()
    ensure_rrhh_schema(conn)
    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            flash("El nombre es obligatorio.", "warning")
            return redirect(url_for("rrhh.nueva_categoria"))
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO categorias_laborales (nombre, descripcion, salario_base, convenio, empresa_id, activo)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (nombre, request.form.get("descripcion"), request.form.get("salario_base") or 0, request.form.get("convenio"), _empresa_id()),
        )
        nuevo_id = cursor.lastrowid
        _registrar_historial(conn, "categorias_laborales", nuevo_id, "crear", "Nueva categoría laboral")
        conn.commit()
        conn.close()
        flash("Categoría laboral creada correctamente.", "success")
        return redirect(url_for("rrhh.lista_categorias"))
    conn.close()
    return render_template("rrhh/categoria_form.html", categoria=None)


# ============================================================
# CONTRATOS
# ============================================================

@rrhh_bp.route("/contratos")
@login_required
def lista_contratos():
    conn = get_db()
    ensure_rrhh_schema(conn)
    empresa_id = _empresa_id()
    scope_sql, scope_params = _departamento_scope_sql("e")
    contratos = conn.execute(
        f"""
        SELECT co.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre,
               c.nombre categoria_nombre, d.nombre departamento_nombre
        FROM contratos co
        JOIN empleados e ON co.empleado_id = e.id
        LEFT JOIN categorias_laborales c ON COALESCE(co.categoria_id, e.categoria_id) = c.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        WHERE {scope_sql} AND co.empresa_id = ?
        ORDER BY co.fecha_inicio DESC, co.id DESC
        """,
        scope_params + [empresa_id],
    ).fetchall()
    conn.close()
    return render_template("rrhh/contratos.html", contratos=contratos, puede_editar=_usuario_es_global_rrhh())


@rrhh_bp.route("/contratos/<int:id>/descargar")
@login_required
@_require_rrhh_global
def descargar_contrato_rrhh(id):
    conn = get_db()
    ensure_rrhh_schema(conn)
    contrato = conn.execute(
        "SELECT * FROM contratos WHERE id = ? AND empresa_id = ?",
        (id, _empresa_id()),
    ).fetchone()
    conn.close()
    if not contrato or not contrato["archivo"]:
        abort(404)
    return _enviar_archivo_rrhh(contrato["archivo"], f"contrato_{contrato['id']}.pdf")


@rrhh_bp.route("/contratos/nuevo", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def nuevo_contrato():
    conn = get_db()
    ensure_rrhh_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        if not _empleado_en_ambito_rrhh(conn, request.form.get("empleado_id")):
            conn.close()
            flash("Empleado no encontrado en tu empresa o centro activo.", "warning")
            return redirect(url_for("rrhh.lista_nominas"))
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO contratos
            (empleado_id, tipo_contrato, fecha_inicio, fecha_fin, jornada, horas_semanales, salario_bruto, categoria_id, estado,
             archivo, observaciones, empresa_id, centro_trabajo, puesto_trabajo, grupo_profesional, convenio, tipo_jornada, turno,
             dias_trabajo, periodo_prueba, salario_bruto_anual, salario_bruto_mensual, pagas, vacaciones_dias, fecha_preaviso, motivo_fin)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.form.get("empleado_id"),
                request.form.get("tipo_contrato"),
                request.form.get("fecha_inicio"),
                request.form.get("fecha_fin") or None,
                request.form.get("jornada"),
                request.form.get("horas_semanales") or 0,
                request.form.get("salario_bruto") or 0,
                request.form.get("categoria_id") or None,
                request.form.get("estado") or "activo",
                request.form.get("archivo"),
                request.form.get("observaciones"),
                empresa_id,
                request.form.get("centro_trabajo"),
                request.form.get("puesto_trabajo"),
                request.form.get("grupo_profesional"),
                request.form.get("convenio"),
                request.form.get("tipo_jornada"),
                request.form.get("turno"),
                request.form.get("dias_trabajo"),
                request.form.get("periodo_prueba"),
                request.form.get("salario_bruto_anual") or 0,
                request.form.get("salario_bruto_mensual") or 0,
                request.form.get("pagas") or 14,
                request.form.get("vacaciones_dias") or 30,
                request.form.get("fecha_preaviso"),
                request.form.get("motivo_fin"),
            ),
        )
        nuevo_id = cursor.lastrowid
        _registrar_historial(conn, "contratos", nuevo_id, "crear", "Registro de nuevo contrato")
        conn.commit()
        conn.close()
        flash("Contrato registrado correctamente.", "success")
        return redirect(url_for("rrhh.lista_contratos"))

    empleados = conn.execute(
        "SELECT id, nombre, apellidos FROM empleados WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre, apellidos",
        (empresa_id,),
    ).fetchall()
    categorias, _, _, _, _ = _listas_maestras(conn)
    conn.close()
    return render_template("rrhh/contrato_form.html", contrato=None, empleados=empleados, categorias=categorias)


# ============================================================
# NÓMINAS
# ============================================================

@rrhh_bp.route("/nominas")
@login_required
def lista_nominas():
    conn = get_db()
    ensure_rrhh_schema(conn)
    empresa_id = _empresa_id()
    scope_sql, scope_params = _departamento_scope_sql("e")
    nominas = conn.execute(
        f"""
        SELECT n.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre,
               d.nombre departamento_nombre
        FROM nominas n
        JOIN empleados e ON n.empleado_id = e.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        WHERE {scope_sql} AND n.empresa_id = ?
        ORDER BY n.anio DESC, n.mes DESC, n.id DESC
        """,
        scope_params + [empresa_id],
    ).fetchall()
    conn.close()
    return render_template("rrhh/nominas.html", nominas=nominas, puede_editar=_usuario_es_global_rrhh())


@rrhh_bp.route("/nominas/nueva", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def nueva_nomina():
    conn = get_db()
    ensure_rrhh_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO nominas
            (empleado_id, mes, anio, periodo_desde, periodo_hasta, salario_base, complementos, horas_extra, pluses, prorrata_pagas,
             irpf, seguridad_social_trabajador, seguridad_social_empresa, anticipos_embargos, bruto, neto, coste_empresa, estado,
             fecha_pago, forma_pago, archivo, observaciones, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.form.get("empleado_id"),
                request.form.get("mes"),
                request.form.get("anio"),
                request.form.get("periodo_desde"),
                request.form.get("periodo_hasta"),
                request.form.get("salario_base") or 0,
                request.form.get("complementos") or 0,
                request.form.get("horas_extra") or 0,
                request.form.get("pluses") or 0,
                request.form.get("prorrata_pagas") or 0,
                request.form.get("irpf") or 0,
                request.form.get("seguridad_social_trabajador") or 0,
                request.form.get("seguridad_social_empresa") or 0,
                request.form.get("anticipos_embargos") or 0,
                request.form.get("bruto") or 0,
                request.form.get("neto") or 0,
                request.form.get("coste_empresa") or 0,
                request.form.get("estado") or "pendiente",
                request.form.get("fecha_pago"),
                request.form.get("forma_pago"),
                request.form.get("archivo"),
                request.form.get("observaciones"),
                empresa_id,
            ),
        )
        nuevo_id = cursor.lastrowid
        _registrar_historial(conn, "nominas", nuevo_id, "crear", f"Nómina mes {request.form.get('mes')} año {request.form.get('anio')}")
        conn.commit()
        conn.close()
        flash("Nómina registrada correctamente.", "success")
        return redirect(url_for("rrhh.lista_nominas"))
    empleados = _empleados_select(conn)
    conn.close()
    return render_template("rrhh/nomina_form.html", nomina=None, empleados=empleados)


# ============================================================
# DOCUMENTACIÓN RRHH
# ============================================================

@rrhh_bp.route("/documentos")
@login_required
def lista_documentos():
    conn = get_db()
    ensure_rrhh_schema(conn)
    empresa_id = _empresa_id()
    scope_sql, scope_params = _departamento_scope_sql("e")
    documentos = conn.execute(
        f"""
        SELECT doc.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre, d.nombre departamento_nombre
        FROM documentos_rrhh doc
        JOIN empleados e ON doc.empleado_id = e.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        WHERE {scope_sql} AND doc.empresa_id = ?
        ORDER BY COALESCE(doc.fecha_caducidad, doc.fecha_documento) DESC, doc.id DESC
        """,
        scope_params + [empresa_id],
    ).fetchall()
    conn.close()
    return render_template("rrhh/documentos.html", documentos=documentos, puede_editar=_usuario_es_global_rrhh())


@rrhh_bp.route("/documentos/<int:id>/descargar")
@login_required
@_require_rrhh_global
def descargar_documento_rrhh(id):
    conn = get_db()
    ensure_rrhh_schema(conn)
    documento = conn.execute(
        "SELECT * FROM documentos_rrhh WHERE id = ? AND empresa_id = ?",
        (id, _empresa_id()),
    ).fetchone()
    conn.close()
    if not documento or not documento["archivo"]:
        abort(404)
    return _enviar_archivo_rrhh(documento["archivo"], documento["nombre"] or None)


@rrhh_bp.route("/documentos/<int:id>/aprobar", methods=["POST"])
@login_required
@_require_rrhh_global
def aprobar_documento_rrhh(id):
    conn = get_db()
    ensure_rrhh_schema(conn)
    documento = conn.execute(
        "SELECT id FROM documentos_rrhh WHERE id = ? AND empresa_id = ?",
        (id, _empresa_id()),
    ).fetchone()
    if not documento:
        conn.close()
        flash("Documento no encontrado.", "warning")
        return _redirect_post_rrhh("rrhh.lista_documentos")
    _safe_update(
        conn,
        "documentos_rrhh",
        id,
        {"estado": "vigente", "visible_empleado": 1},
        request.form.get("motivo_cambio") or "Documento aprobado por RRHH",
    )
    conn.commit()
    conn.close()
    flash("Documento aprobado.", "success")
    return _redirect_post_rrhh("rrhh.lista_documentos")


@rrhh_bp.route("/documentos/<int:id>/rechazar", methods=["POST"])
@login_required
@_require_rrhh_global
def rechazar_documento_rrhh(id):
    conn = get_db()
    ensure_rrhh_schema(conn)
    documento = conn.execute(
        "SELECT id FROM documentos_rrhh WHERE id = ? AND empresa_id = ?",
        (id, _empresa_id()),
    ).fetchone()
    if not documento:
        conn.close()
        flash("Documento no encontrado.", "warning")
        return _redirect_post_rrhh("rrhh.lista_documentos")
    _safe_update(
        conn,
        "documentos_rrhh",
        id,
        {"estado": "rechazado", "visible_empleado": 1},
        request.form.get("motivo_cambio") or "Documento rechazado por RRHH",
    )
    conn.commit()
    conn.close()
    flash("Documento rechazado.", "success")
    return _redirect_post_rrhh("rrhh.lista_documentos")


@rrhh_bp.route("/documentos/nuevo", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def nuevo_documento():
    conn = get_db()
    ensure_rrhh_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO documentos_rrhh
            (empleado_id, tipo_documento, nombre, fecha_documento, fecha_caducidad, archivo, estado, observaciones, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.form.get("empleado_id"),
                request.form.get("tipo_documento"),
                request.form.get("nombre"),
                request.form.get("fecha_documento"),
                request.form.get("fecha_caducidad"),
                request.form.get("archivo"),
                request.form.get("estado") or "vigente",
                request.form.get("observaciones"),
                empresa_id,
            ),
        )
        nuevo_id = cursor.lastrowid
        _registrar_historial(conn, "documentos_rrhh", nuevo_id, "crear", f"Documento: {request.form.get('nombre')}")
        conn.commit()
        conn.close()
        flash("Documento RRHH registrado correctamente.", "success")
        return redirect(url_for("rrhh.lista_documentos"))
    empleados = conn.execute(
        "SELECT id, nombre, apellidos FROM empleados WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre, apellidos",
        (empresa_id,),
    ).fetchall()
    conn.close()
    return render_template("rrhh/documento_form.html", empleados=empleados)


# ============================================================
# SEGURIDAD SOCIAL / SISTEMA RED - INTERCAMBIO DE FICHEROS
# ============================================================

def _opciones_seguridad_social():
    return {
        "tipos": [
            ("afiliacion", "Afiliacion"),
            ("cotizacion", "Cotizacion"),
            ("siltra", "SILTRA"),
            ("respuesta", "Respuesta"),
            ("certificado", "Certificado"),
            ("otros", "Otros"),
        ],
        "direcciones": [
            ("salida", "Salida"),
            ("entrada", "Entrada"),
        ],
        "estados": [
            ("preparado", "Preparado"),
            ("enviado", "Enviado"),
            ("recibido", "Recibido"),
            ("procesado", "Procesado"),
            ("rechazado", "Rechazado"),
            ("error", "Error"),
            ("archivado", "Archivado"),
        ],
    }


@rrhh_bp.route("/seguridad-social", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def seguridad_social_ficheros():
    conn = get_db()
    ensure_rrhh_schema(conn)
    empresa_id = _empresa_id()
    opciones = _opciones_seguridad_social()
    tipos_validos = {codigo for codigo, _ in opciones["tipos"]}
    direcciones_validas = {codigo for codigo, _ in opciones["direcciones"]}
    estados_validos = {codigo for codigo, _ in opciones["estados"]}

    if request.method == "POST":
        tipo = (request.form.get("tipo") or "afiliacion").strip().lower()
        direccion = (request.form.get("direccion") or "salida").strip().lower()
        estado = (request.form.get("estado") or "preparado").strip().lower()
        if tipo not in tipos_validos:
            tipo = "otros"
        if direccion not in direcciones_validas:
            direccion = "salida"
        if estado not in estados_validos:
            estado = "preparado"

        try:
            archivo, nombre_original, hash_archivo = _guardar_archivo_seguridad_social(
                request.files.get("archivo"),
                empresa_id,
            )
        except ValueError as exc:
            conn.close()
            flash(str(exc), "warning")
            return redirect(url_for("rrhh.seguridad_social_ficheros"))

        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fecha_envio = ahora if estado == "enviado" else None
        fecha_respuesta = ahora if estado in {"recibido", "procesado", "rechazado", "error"} else None
        cursor = conn.execute(
            """
            INSERT INTO rrhh_seguridad_social_ficheros
            (tipo, direccion, periodo, ccc, autorizado_red, canal, estado, descripcion,
             archivo, nombre_original, hash_archivo, fecha_generacion, fecha_envio,
             fecha_respuesta, respuesta, observaciones, empresa_id, usuario_id, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tipo,
                direccion,
                request.form.get("periodo") or "",
                request.form.get("ccc") or "",
                request.form.get("autorizado_red") or "",
                request.form.get("canal") or "Sistema RED / SILTRA",
                estado,
                request.form.get("descripcion") or "",
                archivo,
                nombre_original,
                hash_archivo,
                ahora,
                fecha_envio,
                fecha_respuesta,
                request.form.get("respuesta") or "",
                request.form.get("observaciones") or "",
                empresa_id,
                session.get("user_id"),
                session.get("user_id"),
            ),
        )
        nuevo_id = cursor.lastrowid
        _registrar_historial(
            conn,
            "rrhh_seguridad_social_ficheros",
            nuevo_id,
            "crear",
            f"Fichero Seguridad Social: {nombre_original}",
        )
        conn.commit()
        conn.close()
        flash("Fichero de Seguridad Social registrado.", "success")
        return redirect(url_for("rrhh.seguridad_social_ficheros"))

    filtros = {
        "tipo": (request.args.get("tipo") or "").strip().lower(),
        "direccion": (request.args.get("direccion") or "").strip().lower(),
        "estado": (request.args.get("estado") or "").strip().lower(),
        "periodo": (request.args.get("periodo") or "").strip(),
        "q": (request.args.get("q") or "").strip(),
    }
    where = ["empresa_id = ?"]
    params = [empresa_id]
    if filtros["tipo"] in tipos_validos:
        where.append("tipo = ?")
        params.append(filtros["tipo"])
    if filtros["direccion"] in direcciones_validas:
        where.append("direccion = ?")
        params.append(filtros["direccion"])
    if filtros["estado"] in estados_validos:
        where.append("estado = ?")
        params.append(filtros["estado"])
    if filtros["periodo"]:
        where.append("periodo LIKE ?")
        params.append(f"%{filtros['periodo']}%")
    if filtros["q"]:
        where.append("(descripcion LIKE ? OR nombre_original LIKE ? OR ccc LIKE ? OR respuesta LIKE ?)")
        like = f"%{filtros['q']}%"
        params.extend([like, like, like, like])

    ficheros = conn.execute(
        f"""
        SELECT *
        FROM rrhh_seguridad_social_ficheros
        WHERE {' AND '.join(where)}
        ORDER BY COALESCE(fecha_respuesta, fecha_envio, fecha_generacion, fecha_creacion) DESC, id DESC
        LIMIT 300
        """,
        params,
    ).fetchall()
    conn.close()
    return render_template(
        "rrhh/seguridad_social.html",
        ficheros=ficheros,
        filtros=filtros,
        opciones=opciones,
    )


@rrhh_bp.route("/seguridad-social/<int:id>/descargar")
@login_required
@_require_rrhh_global
def descargar_seguridad_social(id):
    conn = get_db()
    ensure_rrhh_schema(conn)
    fichero = conn.execute(
        "SELECT archivo, nombre_original FROM rrhh_seguridad_social_ficheros WHERE id = ? AND empresa_id = ?",
        (id, _empresa_id()),
    ).fetchone()
    conn.close()
    if not fichero or not fichero["archivo"]:
        abort(404)
    return _enviar_archivo_rrhh(fichero["archivo"], fichero["nombre_original"] or None)


@rrhh_bp.route("/seguridad-social/<int:id>/estado", methods=["POST"])
@login_required
@_require_rrhh_global
def actualizar_estado_seguridad_social(id):
    conn = get_db()
    ensure_rrhh_schema(conn)
    empresa_id = _empresa_id()
    fichero = conn.execute(
        "SELECT * FROM rrhh_seguridad_social_ficheros WHERE id = ? AND empresa_id = ?",
        (id, empresa_id),
    ).fetchone()
    if not fichero:
        conn.close()
        flash("Fichero no encontrado.", "warning")
        return redirect(url_for("rrhh.seguridad_social_ficheros"))

    estados_validos = {codigo for codigo, _ in _opciones_seguridad_social()["estados"]}
    estado = (request.form.get("estado") or fichero["estado"] or "preparado").strip().lower()
    if estado not in estados_validos:
        estado = fichero["estado"] or "preparado"
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fecha_envio = fichero["fecha_envio"]
    fecha_respuesta = fichero["fecha_respuesta"]
    if estado == "enviado" and not fecha_envio:
        fecha_envio = ahora
    if estado in {"recibido", "procesado", "rechazado", "error"} and not fecha_respuesta:
        fecha_respuesta = ahora

    conn.execute(
        """
        UPDATE rrhh_seguridad_social_ficheros
        SET estado = ?,
            fecha_envio = ?,
            fecha_respuesta = ?,
            respuesta = ?,
            observaciones = ?,
            updated_at = CURRENT_TIMESTAMP,
            updated_by = ?,
            motivo_cambio = ?
        WHERE id = ? AND empresa_id = ?
        """,
        (
            estado,
            fecha_envio,
            fecha_respuesta,
            request.form.get("respuesta") or "",
            request.form.get("observaciones") or fichero["observaciones"] or "",
            session.get("user_id"),
            request.form.get("motivo_cambio") or f"Estado actualizado a {estado}",
            id,
            empresa_id,
        ),
    )
    _registrar_historial(
        conn,
        "rrhh_seguridad_social_ficheros",
        id,
        "actualizar_estado",
        request.form.get("motivo_cambio") or f"Estado: {estado}",
    )
    conn.commit()
    conn.close()
    flash("Estado del fichero actualizado.", "success")
    return redirect(url_for("rrhh.seguridad_social_ficheros"))


@rrhh_bp.route("/seguridad-social/exportar")
@login_required
@_require_rrhh_global
def exportar_seguridad_social():
    conn = get_db()
    ensure_rrhh_schema(conn)
    empresa_id = _empresa_id()
    filas = conn.execute(
        """
        SELECT *
        FROM rrhh_seguridad_social_ficheros
        WHERE empresa_id = ?
        ORDER BY COALESCE(fecha_respuesta, fecha_envio, fecha_generacion, fecha_creacion) DESC, id DESC
        """,
        (empresa_id,),
    ).fetchall()
    conn.close()
    salida = io.StringIO()
    writer = csv.writer(salida, delimiter=";")
    writer.writerow([
        "id", "tipo", "direccion", "periodo", "ccc", "autorizado_red", "canal", "estado",
        "descripcion", "archivo", "hash_archivo", "fecha_generacion", "fecha_envio",
        "fecha_respuesta", "respuesta", "observaciones",
    ])
    for fila in filas:
        writer.writerow([
            fila["id"], fila["tipo"], fila["direccion"], fila["periodo"], fila["ccc"],
            fila["autorizado_red"], fila["canal"], fila["estado"], fila["descripcion"],
            fila["nombre_original"], fila["hash_archivo"], fila["fecha_generacion"],
            fila["fecha_envio"], fila["fecha_respuesta"], fila["respuesta"], fila["observaciones"],
        ])
    contenido = salida.getvalue().encode("utf-8-sig")
    return send_file(
        io.BytesIO(contenido),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"seguridad_social_rrhh_{date.today().isoformat()}.csv",
    )




# ============================================================
# PRE-NÓMINA — utilidades y cálculo interno
# ============================================================

def _to_float(valor, defecto=0.0):
    try:
        if valor in (None, ""):
            return defecto
        return float(str(valor).replace(",", "."))
    except Exception:
        return defecto


def _to_int(valor, defecto=0):
    try:
        if valor in (None, ""):
            return defecto
        return int(valor)
    except Exception:
        return defecto


def _to_int_or_none(valor):
    try:
        if valor in (None, ""):
            return None
        return int(valor)
    except Exception:
        return None


def _subdepartamento_departamento_id(conn, subdepartamento_id, empresa_id=None):
    if not subdepartamento_id or not _table_exists(conn, "subdepartamentos"):
        return None
    params = [subdepartamento_id]
    sql = "SELECT departamento_id FROM subdepartamentos WHERE id = ?"
    if empresa_id is not None:
        sql += " AND empresa_id = ?"
        params.append(empresa_id)
    row = conn.execute(sql, params).fetchone()
    if not row:
        return None
    return _to_int_or_none(row["departamento_id"])


def _normalizar_departamento_subdepartamento(conn, empresa_id, departamento_id, subdepartamento_id):
    """
    Mantiene la combinacion real usada en cuadrantes.
    Si solo llega subdepartamento, intentamos inferir el departamento por uso real
    en empleados antes que por el maestro, porque hay subdepartamentos historicos
    usados bajo departamentos distintos a su padre teorico.
    """
    departamento_id = _to_int_or_none(departamento_id)
    subdepartamento_id = _to_int_or_none(subdepartamento_id)

    if subdepartamento_id is None:
        return departamento_id, None

    if departamento_id is not None:
        return departamento_id, subdepartamento_id

    if _table_exists(conn, "empleados"):
        usos = conn.execute(
            """
            SELECT e.departamento_id, COUNT(*) AS total
            FROM empleados e
            WHERE e.empresa_id = ?
              AND COALESCE(e.activo, 1) = 1
              AND e.subdepartamento_id = ?
              AND e.departamento_id IS NOT NULL
            GROUP BY e.departamento_id
            ORDER BY total DESC
            """,
            (empresa_id, subdepartamento_id),
        ).fetchall()
        if usos:
            if len(usos) == 1 or usos[0]["total"] > usos[1]["total"]:
                return _to_int_or_none(usos[0]["departamento_id"]), subdepartamento_id

    return _subdepartamento_departamento_id(conn, subdepartamento_id, empresa_id), subdepartamento_id


def _iso(dt_text):
    return (dt_text or "").split(" ")[0].split("T")[0]


def _mes_rango(anio, mes):
    anio = _to_int(anio, date.today().year)
    mes = _to_int(mes, date.today().month)
    inicio = date(anio, mes, 1)
    if mes == 12:
        fin = date(anio + 1, 1, 1)
    else:
        fin = date(anio, mes + 1, 1)
    return inicio.isoformat(), fin.isoformat(), anio, mes


def _ensure_prenomina_schema(conn):
    ensure_rrhh_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS turnos_rrhh (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            hora_inicio TEXT,
            hora_fin TEXT,
            tipo TEXT DEFAULT 'normal',
            departamento_id INTEGER,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "empleado_id INTEGER", "fecha TEXT", "hora_inicio TEXT", "hora_fin TEXT", "tipo TEXT DEFAULT 'normal'",
        "departamento_id INTEGER", "observaciones TEXT", "empresa_id INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "turnos_rrhh", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS variables_prenomina (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            concepto TEXT NOT NULL,
            tipo TEXT DEFAULT 'plus',
            unidades REAL DEFAULT 1,
            importe_unitario REAL DEFAULT 0,
            importe_total REAL DEFAULT 0,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "empleado_id INTEGER", "fecha TEXT", "concepto TEXT", "tipo TEXT DEFAULT 'plus'", "unidades REAL DEFAULT 1",
        "importe_unitario REAL DEFAULT 0", "importe_total REAL DEFAULT 0", "observaciones TEXT", "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "variables_prenomina", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS resumen_prenomina (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER NOT NULL,
            mes INTEGER NOT NULL,
            anio INTEGER NOT NULL,
            horas_normales REAL DEFAULT 0,
            horas_extra REAL DEFAULT 0,
            horas_nocturnas REAL DEFAULT 0,
            horas_festivas REAL DEFAULT 0,
            ausencias_horas REAL DEFAULT 0,
            salario_base_mes REAL DEFAULT 0,
            precio_hora REAL DEFAULT 0,
            pluses REAL DEFAULT 0,
            descuentos REAL DEFAULT 0,
            coste_estimado REAL DEFAULT 0,
            observaciones TEXT,
            estado TEXT DEFAULT 'borrador',
            empresa_id INTEGER DEFAULT 1,
            fecha_calculo TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(empleado_id, mes, anio, empresa_id)
        )
        """
    )
    for col in [
        "horas_normales REAL DEFAULT 0", "horas_extra REAL DEFAULT 0", "horas_nocturnas REAL DEFAULT 0", "horas_festivas REAL DEFAULT 0",
        "ausencias_horas REAL DEFAULT 0", "salario_base_mes REAL DEFAULT 0", "precio_hora REAL DEFAULT 0", "pluses REAL DEFAULT 0",
        "descuentos REAL DEFAULT 0", "coste_estimado REAL DEFAULT 0", "observaciones TEXT", "estado TEXT DEFAULT 'borrador'",
        "empresa_id INTEGER DEFAULT 1", "fecha_calculo TEXT DEFAULT CURRENT_TIMESTAMP",
        "horas_extra_aprobadas REAL DEFAULT 0", "horas_extra_no_aprobadas REAL DEFAULT 0",
        "minutos_extra_aprobados INTEGER DEFAULT 0", "minutos_extra_no_aprobados INTEGER DEFAULT 0",
        "periodo_desde TEXT", "periodo_hasta TEXT",
        "horas_ausencia_retribuidas REAL DEFAULT 0", "horas_ausencia_no_retribuidas REAL DEFAULT 0",
        "detalle_ausencias TEXT",
        "irpf_pct REAL DEFAULT 0", "irpf_importe REAL DEFAULT 0", "neto_estimado REAL DEFAULT 0",
        "embargo_importe REAL DEFAULT 0", "embargo_detalle TEXT",
    ]:
        _add_column_if_missing(conn, "resumen_prenomina", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS embargos_rrhh (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER NOT NULL,
            organismo TEXT,
            numero_expediente TEXT,
            tipo TEXT DEFAULT 'embargo',
            fecha_inicio TEXT NOT NULL,
            fecha_fin TEXT,
            importe_total REAL DEFAULT 0,
            saldo_pendiente REAL DEFAULT 0,
            cuota_mensual REAL DEFAULT 0,
            porcentaje_nomina REAL DEFAULT 0,
            prioridad INTEGER DEFAULT 1,
            estado TEXT DEFAULT 'activo',
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            created_by INTEGER,
            updated_by INTEGER,
            motivo_cambio TEXT
        )
        """
    )
    for col in [
        "empleado_id INTEGER", "organismo TEXT", "numero_expediente TEXT", "tipo TEXT DEFAULT 'embargo'",
        "fecha_inicio TEXT", "fecha_fin TEXT", "importe_total REAL DEFAULT 0", "saldo_pendiente REAL DEFAULT 0",
        "cuota_mensual REAL DEFAULT 0", "porcentaje_nomina REAL DEFAULT 0", "prioridad INTEGER DEFAULT 1",
        "estado TEXT DEFAULT 'activo'", "observaciones TEXT", "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP", "updated_at TEXT", "created_by INTEGER",
        "updated_by INTEGER", "motivo_cambio TEXT",
    ]:
        _add_column_if_missing(conn, "embargos_rrhh", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS configuracion_prenomina (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER DEFAULT 1,
            modo_cierre TEXT DEFAULT 'mes_natural',
            dia_inicio INTEGER DEFAULT 1,
            dia_cierre INTEGER DEFAULT 0,
            cerrar_horas_extra INTEGER DEFAULT 1,
            cerrar_ausencias INTEGER DEFAULT 1,
            cerrar_variables INTEGER DEFAULT 1,
            ss_empresa_pct REAL DEFAULT 0.332,
            observaciones TEXT,
            fecha_actualizacion TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(empresa_id)
        )
        """
    )
    for col in [
        "empresa_id INTEGER DEFAULT 1", "modo_cierre TEXT DEFAULT 'mes_natural'", "dia_inicio INTEGER DEFAULT 1",
        "dia_cierre INTEGER DEFAULT 0", "cerrar_horas_extra INTEGER DEFAULT 1", "cerrar_ausencias INTEGER DEFAULT 1",
        "cerrar_variables INTEGER DEFAULT 1", "ss_empresa_pct REAL DEFAULT 0.332",
        "observaciones TEXT", "fecha_actualizacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "configuracion_prenomina", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vacaciones_rrhh (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER NOT NULL,
            fecha_inicio TEXT NOT NULL,
            fecha_fin TEXT NOT NULL,
            tipo TEXT DEFAULT 'vacaciones',
            estado TEXT DEFAULT 'solicitada',
            dias REAL DEFAULT 0,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            usuario_creacion_id INTEGER,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "empleado_id INTEGER", "fecha_inicio TEXT", "fecha_fin TEXT", "tipo TEXT DEFAULT 'vacaciones'",
        "estado TEXT DEFAULT 'solicitada'", "dias REAL DEFAULT 0", "observaciones TEXT", "empresa_id INTEGER DEFAULT 1",
        "usuario_creacion_id INTEGER", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        "requiere_documento INTEGER DEFAULT 0", "documento_id INTEGER", "afecta_prenomina INTEGER DEFAULT 1",
        "porcentaje_cobro REAL DEFAULT 100", "codigo_oficial TEXT", "origen TEXT DEFAULT 'manual'", "color TEXT",
    ]:
        _add_column_if_missing(conn, "vacaciones_rrhh", col)

    permisos_extra = [
        ("ver_prenomina", "Ver cálculos de pre-nómina"),
        ("gestionar_prenomina", "Calcular y cerrar pre-nóminas"),
        ("gestionar_turnos", "Crear y editar turnos RRHH"),
        ("gestionar_variables_prenomina", "Crear variables de pre-nómina"),
    ]
    permisos_extra.append(("gestionar_embargos_rrhh", "Crear y editar embargos de nomina"))
    for nombre, descripcion in permisos_extra:
        conn.execute("INSERT OR IGNORE INTO permisos (nombre, descripcion) VALUES (?, ?)", (nombre, descripcion))
    conn.commit()


def _row_to_dict(row):
    if not row:
        return {}
    return {k: row[k] for k in row.keys()}


def _config_prenomina(conn, empresa_id):
    defaults = {
        "empresa_id": empresa_id,
        "modo_cierre": "mes_natural",
        "dia_inicio": 1,
        "dia_cierre": 0,
        "cerrar_horas_extra": 1,
        "cerrar_ausencias": 1,
        "cerrar_variables": 1,
        "ss_empresa_pct": 0.332,
        "observaciones": "",
    }
    row = conn.execute(
        "SELECT * FROM configuracion_prenomina WHERE empresa_id = ? LIMIT 1",
        (empresa_id,),
    ).fetchone()
    defaults.update(_row_to_dict(row))
    return defaults


def _ultimo_dia_mes(anio, mes):
    if mes == 12:
        siguiente = date(anio + 1, 1, 1)
    else:
        siguiente = date(anio, mes + 1, 1)
    return (siguiente - timedelta(days=1)).day


def _fecha_con_dia_seguro(anio, mes, dia):
    ultimo = _ultimo_dia_mes(anio, mes)
    dia = max(1, min(_to_int(dia, ultimo), ultimo))
    return date(anio, mes, dia)


def _periodo_prenomina(anio, mes, config=None):
    inicio_txt, fin_txt, anio, mes = _mes_rango(anio, mes)
    config = config or {}
    if (config.get("modo_cierre") or "mes_natural") != "dia_cierre":
        return inicio_txt, fin_txt, anio, mes

    dia_inicio = max(1, min(_to_int(config.get("dia_inicio"), 1), 31))
    dia_cierre = max(0, min(_to_int(config.get("dia_cierre"), 0), 31))
    dia_fin = _ultimo_dia_mes(anio, mes) if dia_cierre == 0 else dia_cierre
    fin_inclusivo = _fecha_con_dia_seguro(anio, mes, dia_fin)

    if dia_inicio > fin_inclusivo.day:
        if mes == 1:
            inicio_anio, inicio_mes = anio - 1, 12
        else:
            inicio_anio, inicio_mes = anio, mes - 1
    else:
        inicio_anio, inicio_mes = anio, mes
    inicio = _fecha_con_dia_seguro(inicio_anio, inicio_mes, dia_inicio)
    return inicio.isoformat(), (fin_inclusivo + timedelta(days=1)).isoformat(), anio, mes


def _periodo_prenomina_visible(anio, mes, config=None):
    inicio, fin, anio, mes = _periodo_prenomina(anio, mes, config)
    fin_visible = (date.fromisoformat(fin) - timedelta(days=1)).isoformat()
    return inicio, fin_visible, anio, mes


def _periodo_jornada(conn, anio, mes, empresa_id=None):
    empresa_id = empresa_id or _empresa_id()
    config = _config_prenomina(conn, empresa_id)
    inicio, fin_exclusivo, anio, mes = _periodo_prenomina(anio, mes, config)
    fin_visible = (date.fromisoformat(fin_exclusivo) - timedelta(days=1)).isoformat()
    return inicio, fin_exclusivo, fin_visible, anio, mes


def _cierre_jornada(conn, anio, mes, empresa_id=None):
    empresa_id = empresa_id or _empresa_id()
    if not _table_exists(conn, "cierres_jornada_rrhh"):
        return None
    return conn.execute(
        """
        SELECT *
        FROM cierres_jornada_rrhh
        WHERE empresa_id = ? AND anio = ? AND mes = ?
        LIMIT 1
        """,
        (empresa_id, anio, mes),
    ).fetchone()


def _periodo_cerrado_para_fecha(conn, fecha_txt, empresa_id=None):
    empresa_id = empresa_id or _empresa_id()
    try:
        fecha = date.fromisoformat(_iso(fecha_txt))
    except Exception:
        return None
    if not _table_exists(conn, "cierres_jornada_rrhh"):
        return None
    return conn.execute(
        """
        SELECT *
        FROM cierres_jornada_rrhh
        WHERE empresa_id = ?
          AND estado = 'cerrado'
          AND date(?) BETWEEN date(periodo_desde) AND date(periodo_hasta)
        LIMIT 1
        """,
        (empresa_id, fecha.isoformat()),
    ).fetchone()


def _hash_cierre_jornada(conn, anio, mes, inicio, fin_exclusivo, empresa_id):
    filas = conn.execute(
        """
        SELECT empleado_id, tipo, fecha_hora, origen, COALESCE(estado, 'activo') AS estado, hash_registro
        FROM fichajes
        WHERE empresa_id = ?
          AND datetime(fecha_hora) >= datetime(?)
          AND datetime(fecha_hora) < datetime(?)
          AND COALESCE(estado, 'activo') != 'anulado'
        ORDER BY empleado_id, datetime(fecha_hora), id
        """,
        (empresa_id, inicio + " 00:00:00", fin_exclusivo + " 00:00:00"),
    ).fetchall()
    payload = "|".join(
        f"{f['empleado_id']}:{f['tipo']}:{f['fecha_hora']}:{f['origen'] or ''}:{f['estado']}:{f['hash_registro'] or ''}"
        for f in filas
    )
    return hashlib.sha256(f"{empresa_id}|{anio}|{mes}|{inicio}|{fin_exclusivo}|{payload}".encode("utf-8")).hexdigest()


def _resumen_jornada_periodo(conn, anio, mes, empleado_id=None, departamento_id=None, subdepartamento_id=None, centro_id=None):
    empresa_id = _empresa_id()
    _ensure_cuadrantes_schema(conn)
    _ensure_control_tiempo_schema(conn)
    inicio, fin_exclusivo, fin_visible, anio, mes = _periodo_jornada(conn, anio, mes, empresa_id)
    scope_sql, scope_params = _departamento_scope_sql("e")
    if empleado_id and _usuario_es_empleado_puro() and str(empleado_id) == str(session.get("user_empleado_id")):
        scope_sql, scope_params = "e.empresa_id = ?", [empresa_id]
    filtros = []
    params = list(scope_params) + [empresa_id]
    if empleado_id:
        filtros.append("e.id = ?")
        params.append(empleado_id)
    if departamento_id:
        filtros.append("e.departamento_id = ?")
        params.append(departamento_id)
    if subdepartamento_id:
        filtros.append("e.subdepartamento_id = ?")
        params.append(subdepartamento_id)
    if centro_id:
        filtros.append(
            """
            EXISTS (
                SELECT 1
                FROM turnos_rrhh t
                LEFT JOIN puestos_operativos p ON t.puesto_id = p.id
                WHERE t.empleado_id = e.id
                  AND t.empresa_id = e.empresa_id
                  AND date(t.fecha) >= date(?)
                  AND date(t.fecha) < date(?)
                  AND COALESCE(t.estado, 'planificado') NOT IN ('anulado', 'cancelado', 'borrado')
                  AND COALESCE(t.centro_id, p.centro_id, 0) = ?
            )
            """
        )
        params.extend([inicio, fin_exclusivo, centro_id])
    filtros_sql = (" AND " + " AND ".join(filtros)) if filtros else ""
    empleados = conn.execute(
        f"""
        SELECT e.id, e.nombre, e.apellidos, e.nif, d.nombre AS departamento_nombre,
               sd.nombre AS subdepartamento_nombre
        FROM empleados e
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON e.subdepartamento_id = sd.id
        WHERE {scope_sql}
          AND e.empresa_id = ?
          AND COALESCE(e.activo, 1) = 1
          {filtros_sql}
        ORDER BY d.nombre, e.nombre, e.apellidos
        """,
        params,
    ).fetchall()

    resumen = []
    for empleado in empleados:
        horas_reales = _calcular_horas_fichajes(conn, empleado["id"], inicio, fin_exclusivo)
        horas_turno, _nocturnas, _festivas = _calcular_turnos(conn, empleado["id"], inicio, fin_exclusivo)
        conteo = conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN tipo = 'entrada' THEN 1 ELSE 0 END) AS entradas,
              SUM(CASE WHEN tipo = 'salida' THEN 1 ELSE 0 END) AS salidas,
              SUM(CASE WHEN tipo = 'pausa' THEN 1 ELSE 0 END) AS pausas,
              SUM(CASE WHEN tipo = 'reanudacion' THEN 1 ELSE 0 END) AS reanudaciones
            FROM fichajes
            WHERE empresa_id = ?
              AND empleado_id = ?
              AND datetime(fecha_hora) >= datetime(?)
              AND datetime(fecha_hora) < datetime(?)
              AND COALESCE(estado, 'activo') != 'anulado'
            """,
            (empresa_id, empleado["id"], inicio + " 00:00:00", fin_exclusivo + " 00:00:00"),
        ).fetchone()
        pendientes = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM solicitudes_correccion_fichaje
            WHERE empresa_id = ?
              AND empleado_id = ?
              AND date(fecha) BETWEEN date(?) AND date(?)
              AND estado = 'pendiente'
            """,
            (empresa_id, empleado["id"], inicio, fin_visible),
        ).fetchone()["total"]
        resumen.append(
            {
                "empleado": empleado,
                "horas_reales": round(horas_reales, 2),
                "horas_planificadas": round(horas_turno, 2),
                "diferencia": round(horas_reales - horas_turno, 2),
                "fichajes": conteo["total"] or 0,
                "entradas": conteo["entradas"] or 0,
                "salidas": conteo["salidas"] or 0,
                "pausas": conteo["pausas"] or 0,
                "reanudaciones": conteo["reanudaciones"] or 0,
                "correcciones_pendientes": pendientes or 0,
            }
        )
    return {
        "anio": anio,
        "mes": mes,
        "inicio": inicio,
        "fin_exclusivo": fin_exclusivo,
        "fin_visible": fin_visible,
        "cierre": _cierre_jornada(conn, anio, mes, empresa_id),
        "resumen": resumen,
    }


def _horas_turno_jornada(turno):
    estado = (turno["estado"] if "estado" in turno.keys() else "planificado") or "planificado"
    tipo = (turno["tipo"] if "tipo" in turno.keys() else "real") or "real"
    if estado.strip().lower() in {"anulado", "cancelado", "borrado"}:
        return 0.0
    if tipo.strip().lower() in {"libre", "descanso", "dia_libre", "dia libre", "dÃ­a libre"}:
        return 0.0
    if not turno["hora_inicio"] or not turno["hora_fin"]:
        return 0.0
    try:
        inicio = datetime.fromisoformat(f"{turno['fecha']} {turno['hora_inicio']}")
        fin = datetime.fromisoformat(f"{turno['fecha']} {turno['hora_fin']}")
        if fin <= inicio:
            fin += timedelta(days=1)
        horas = (fin - inicio).total_seconds() / 3600
        return round(max(0.0, min(horas, 18.0)), 2)
    except Exception:
        return 0.0


def _turnos_jornada_dia(conn, empleado_id, fecha_txt, empresa_id):
    turnos = conn.execute(
        """
        SELECT t.*, ct.nombre AS centro_nombre, p.nombre AS puesto_nombre
        FROM turnos_rrhh t
        LEFT JOIN centros_trabajo ct ON t.centro_id = ct.id
        LEFT JOIN puestos_operativos p ON t.puesto_id = p.id
        WHERE t.empresa_id = ?
          AND t.empleado_id = ?
          AND date(t.fecha) = date(?)
          AND COALESCE(t.estado, 'planificado') NOT IN ('anulado', 'cancelado', 'borrado')
        ORDER BY COALESCE(t.hora_inicio, '99:99'), t.id
        """,
        (empresa_id, empleado_id, fecha_txt),
    ).fetchall()
    partes = []
    centros = []
    horas = 0.0
    for turno in turnos:
        tipo = (turno["tipo"] or "real").strip()
        if tipo.lower() in {"libre", "descanso", "dia_libre", "dia libre", "dÃ­a libre"}:
            partes.append("Libre/descanso")
            continue
        rango = f"{turno['hora_inicio'] or '--:--'}-{turno['hora_fin'] or '--:--'}"
        extra = turno["puesto_nombre"] or turno["observaciones"] or tipo
        partes.append(f"{rango} {extra}".strip())
        if turno["centro_nombre"] and turno["centro_nombre"] not in centros:
            centros.append(turno["centro_nombre"])
        horas += _horas_turno_jornada(turno)
    return {
        "texto": "; ".join(partes),
        "centro": "; ".join(centros),
        "horas": round(horas, 2),
    }


def _fichajes_jornada_dia(conn, empleado_id, fecha_txt, empresa_id):
    filas = conn.execute(
        """
        SELECT *
        FROM fichajes
        WHERE empresa_id = ?
          AND empleado_id = ?
          AND date(fecha_hora) = date(?)
          AND COALESCE(estado, 'activo') != 'anulado'
        ORDER BY datetime(fecha_hora) ASC, id ASC
        """,
        (empresa_id, empleado_id, fecha_txt),
    ).fetchall()
    total_horas = 0.0
    pausas_minutos = 0
    abierta = None
    pausa_abierta = None
    marcas = []
    entradas = []
    salidas = []
    incidencias = []
    for fila in filas:
        tipo = (fila["tipo"] or "").strip().lower()
        marca = _parse_dt_local(fila["fecha_hora"])
        if not marca:
            continue
        marcas.append(f"{tipo} {marca.strftime('%H:%M')} ({fila['origen'] or 'manual'})")
        if tipo == "entrada":
            entradas.append(marca)
        elif tipo == "salida":
            salidas.append(marca)

        if tipo in {"entrada", "reanudacion"}:
            if tipo == "reanudacion" and pausa_abierta:
                pausas_minutos += max(0, int(round((marca - pausa_abierta).total_seconds() / 60)))
                pausa_abierta = None
            abierta = marca
        elif tipo in {"salida", "pausa"} and abierta:
            diff = (marca - abierta).total_seconds() / 3600
            if 0 < diff < 18:
                total_horas += diff
            abierta = None
            if tipo == "pausa":
                pausa_abierta = marca

    if filas:
        if not entradas:
            incidencias.append("Sin entrada")
        if not salidas:
            incidencias.append("Sin salida")
        if abierta:
            incidencias.append("Tramo abierto")
        if pausa_abierta:
            incidencias.append("Pausa sin reanudacion")

    return {
        "texto": "; ".join(marcas),
        "primera_entrada": entradas[0].strftime("%H:%M") if entradas else "",
        "ultima_salida": salidas[-1].strftime("%H:%M") if salidas else "",
        "pausas_minutos": pausas_minutos,
        "horas": round(total_horas, 2),
        "incidencias": incidencias,
    }


def _ausencias_jornada_dia(conn, empleado_id, fecha_txt, empresa_id):
    filas = conn.execute(
        """
        SELECT tipo, estado, observaciones
        FROM vacaciones_rrhh
        WHERE empresa_id = ?
          AND empleado_id = ?
          AND date(fecha_inicio) <= date(?)
          AND date(fecha_fin) >= date(?)
          AND COALESCE(estado, 'solicitada') IN ('solicitada', 'aprobada')
        ORDER BY CASE WHEN estado = 'aprobada' THEN 0 ELSE 1 END, id DESC
        """,
        (empresa_id, empleado_id, fecha_txt, fecha_txt),
    ).fetchall()
    return "; ".join(f"{f['tipo'] or 'ausencia'} ({f['estado'] or 'solicitada'})" for f in filas)


def _detalle_jornada_oficial(conn, anio, mes, empleado_id=None, departamento_id=None, subdepartamento_id=None, centro_id=None):
    empresa_id = _empresa_id()
    datos = _resumen_jornada_periodo(conn, anio, mes, empleado_id, departamento_id, subdepartamento_id, centro_id)
    inicio_dt = date.fromisoformat(datos["inicio"])
    fin_dt = date.fromisoformat(datos["fin_exclusivo"])
    cierre = datos["cierre"]
    estado_cierre = cierre["estado"] if cierre else "abierto"
    hash_cierre = cierre["hash_cierre"] if cierre else ""
    dias_semana = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
    filas = []
    for resumen_empleado in datos["resumen"]:
        empleado = resumen_empleado["empleado"]
        dia = inicio_dt
        while dia < fin_dt:
            fecha_txt = dia.isoformat()
            turnos = _turnos_jornada_dia(conn, empleado["id"], fecha_txt, empresa_id)
            fichajes = _fichajes_jornada_dia(conn, empleado["id"], fecha_txt, empresa_id)
            ausencia = _ausencias_jornada_dia(conn, empleado["id"], fecha_txt, empresa_id)
            correcciones = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM solicitudes_correccion_fichaje
                WHERE empresa_id = ?
                  AND empleado_id = ?
                  AND date(fecha) = date(?)
                  AND estado = 'pendiente'
                """,
                (empresa_id, empleado["id"], fecha_txt),
            ).fetchone()["total"] or 0
            incidencias = list(fichajes["incidencias"])
            if turnos["horas"] > 0 and fichajes["horas"] <= 0 and not ausencia:
                incidencias.append("Turno sin fichaje")
            diferencia = round(fichajes["horas"] - turnos["horas"], 2)
            if abs(diferencia) >= 0.25:
                incidencias.append(f"Diferencia {diferencia:.2f} h")
            if correcciones:
                incidencias.append(f"{correcciones} correccion(es) pendiente(s)")
            filas.append(
                {
                    "periodo_desde": datos["inicio"],
                    "periodo_hasta": datos["fin_visible"],
                    "estado_cierre": estado_cierre,
                    "hash_cierre": hash_cierre,
                    "empleado_id": empleado["id"],
                    "empleado": f"{empleado['nombre']} {empleado['apellidos'] or ''}".strip(),
                    "nif": empleado["nif"] or "",
                    "departamento": empleado["departamento_nombre"] or "",
                    "subdepartamento": empleado["subdepartamento_nombre"] or "",
                    "fecha": fecha_txt,
                    "dia_semana": dias_semana[dia.weekday()],
                    "centro": turnos["centro"],
                    "turno_planificado": turnos["texto"],
                    "horas_planificadas": turnos["horas"],
                    "fichajes": fichajes["texto"],
                    "primera_entrada": fichajes["primera_entrada"],
                    "ultima_salida": fichajes["ultima_salida"],
                    "pausas_minutos": fichajes["pausas_minutos"],
                    "horas_trabajadas": fichajes["horas"],
                    "diferencia": diferencia,
                    "ausencia": ausencia,
                    "correcciones_pendientes": correcciones,
                    "incidencias": "; ".join(dict.fromkeys(incidencias)),
                }
            )
            dia += timedelta(days=1)
    datos["detalle"] = filas
    return datos


def _ausencias_periodo_prenomina(conn, inicio, fin, scope_sql, scope_params, empresa_id):
    return conn.execute(
        f"""
        SELECT v.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre,
               d.nombre departamento_nombre
        FROM vacaciones_rrhh v
        JOIN empleados e ON v.empleado_id = e.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        WHERE {scope_sql}
          AND v.empresa_id = ?
          AND COALESCE(v.afecta_prenomina, 1) = 1
          AND COALESCE(v.estado, 'solicitada') IN ('solicitada', 'aprobada')
          AND date(v.fecha_inicio) < date(?)
          AND date(v.fecha_fin) >= date(?)
        ORDER BY date(v.fecha_inicio), e.nombre, e.apellidos
        """,
        scope_params + [empresa_id, fin, inicio],
    ).fetchall()


def _ausencias_prenomina_empleado(conn, empleado_id, inicio, fin, horas_dia):
    filas = conn.execute(
        """
        SELECT fecha_inicio, fecha_fin, tipo, estado, porcentaje_cobro, afecta_prenomina
        FROM vacaciones_rrhh
        WHERE empleado_id = ?
          AND empresa_id = ?
          AND COALESCE(afecta_prenomina, 1) = 1
          AND COALESCE(estado, 'solicitada') IN ('solicitada', 'aprobada')
          AND date(fecha_inicio) < date(?)
          AND date(fecha_fin) >= date(?)
        ORDER BY date(fecha_inicio), id
        """,
        (empleado_id, _empresa_id(), fin, inicio),
    ).fetchall()
    inicio_dt = date.fromisoformat(inicio)
    fin_dt = date.fromisoformat(fin) - timedelta(days=1)
    horas_retribuidas = 0.0
    horas_no_retribuidas = 0.0
    detalle = []
    for fila in filas:
        try:
            desde = max(date.fromisoformat(fila["fecha_inicio"]), inicio_dt)
            hasta = min(date.fromisoformat(fila["fecha_fin"]), fin_dt)
        except Exception:
            continue
        if hasta < desde:
            continue
        dias = (hasta - desde).days + 1
        porcentaje = _to_float(fila["porcentaje_cobro"], 100.0)
        horas = max(0.0, dias * horas_dia)
        horas_retribuidas += horas * max(0.0, min(porcentaje, 100.0)) / 100.0
        horas_no_retribuidas += horas * max(0.0, 100.0 - min(porcentaje, 100.0)) / 100.0
        detalle.append(f"{fila['tipo'] or 'ausencia'} {desde.isoformat()}-{hasta.isoformat()} ({porcentaje:g}%)")
    return round(horas_retribuidas, 2), round(horas_no_retribuidas, 2), "; ".join(detalle)


def _calcular_horas_fichajes(conn, empleado_id, inicio, fin):
    filas = conn.execute(
        """
        SELECT tipo, fecha_hora
        FROM fichajes
        WHERE empleado_id = ? AND datetime(fecha_hora) >= datetime(?) AND datetime(fecha_hora) < datetime(?)
          AND COALESCE(estado, 'activo') != 'anulado'
        ORDER BY datetime(fecha_hora) ASC, id ASC
        """,
        (empleado_id, inicio + " 00:00:00", fin + " 00:00:00"),
    ).fetchall()
    total = 0.0
    abiertas = {}
    for f in filas:
        tipo = (f["tipo"] or "").lower()
        fecha_hora = f["fecha_hora"]
        try:
            marca = datetime.fromisoformat(str(fecha_hora).replace("Z", "").replace("T", " "))
        except Exception:
            continue
        dia = marca.date().isoformat()
        if tipo in {"entrada", "reanudacion"}:
            abiertas[dia] = marca
        elif tipo in {"salida", "pausa"} and dia in abiertas:
            diff = (marca - abiertas[dia]).total_seconds() / 3600
            if 0 < diff < 18:
                total += diff
            abiertas.pop(dia, None)
    return round(total, 2)


def _calcular_turnos(conn, empleado_id, inicio, fin):
    filas = conn.execute(
        """
        SELECT * FROM turnos_rrhh
        WHERE empleado_id = ? AND date(fecha) >= date(?) AND date(fecha) < date(?)
        ORDER BY fecha ASC
        """,
        (empleado_id, inicio, fin),
    ).fetchall()
    horas_planificadas = 0.0
    horas_nocturnas = 0.0
    horas_festivas = 0.0
    for t in filas:
        hi = t["hora_inicio"] or ""
        hf = t["hora_fin"] or ""
        try:
            ini = datetime.fromisoformat(f"{t['fecha']} {hi}")
            fin_dt = datetime.fromisoformat(f"{t['fecha']} {hf}")
            if fin_dt <= ini:
                fin_dt = fin_dt + timedelta(days=1)
            h = max(0, min(18, (fin_dt - ini).total_seconds() / 3600))
        except Exception:
            h = 0
        horas_planificadas += h
        tipo = (t["tipo"] or "normal").lower()
        if "noct" in tipo:
            horas_nocturnas += h
        if "fest" in tipo:
            horas_festivas += h
    return round(horas_planificadas, 2), round(horas_nocturnas, 2), round(horas_festivas, 2)


def _datos_salario_empleado(conn, empleado_id):
    empleado = conn.execute(
        """
        SELECT e.*, c.salario_base categoria_salario
        FROM empleados e
        LEFT JOIN categorias_laborales c ON e.categoria_id = c.id
        WHERE e.id = ?
        """,
        (empleado_id,),
    ).fetchone()
    contrato = conn.execute(
        """
        SELECT * FROM contratos
        WHERE empleado_id = ?
        ORDER BY COALESCE(fecha_inicio, fecha_creacion) DESC, id DESC
        LIMIT 1
        """,
        (empleado_id,),
    ).fetchone()
    salario = 0.0
    horas_semana = 40.0
    if contrato:
        salario = _to_float(contrato["salario_bruto_mensual"] if "salario_bruto_mensual" in contrato.keys() else None)
        if salario <= 0:
            salario = _to_float(contrato["salario_bruto"] if "salario_bruto" in contrato.keys() else None)
        horas_semana = _to_float(contrato["horas_semanales"] if "horas_semanales" in contrato.keys() else None, 40.0) or 40.0
    if salario <= 0 and empleado:
        salario = _to_float(empleado["categoria_salario"] if "categoria_salario" in empleado.keys() else None)
    if salario <= 0:
        salario = 0.0
    horas_mes_teoricas = max(1, horas_semana * 4.333)
    precio_hora = salario / horas_mes_teoricas if salario else 0.0
    return salario, horas_semana, precio_hora


def _irpf_pct_empleado(conn, empleado_id):
    if not _column_exists(conn, "empleados", "irpf_pct"):
        return 0.0
    row = conn.execute("SELECT irpf_pct FROM empleados WHERE id = ?", (empleado_id,)).fetchone()
    if not row:
        return 0.0
    return max(0.0, min(_to_float(row["irpf_pct"]), 100.0))


def _embargos_prenomina_empleado(conn, empleado_id, inicio, fin, base_calculo):
    if not _table_exists(conn, "embargos_rrhh"):
        return 0.0, ""
    filas = conn.execute(
        """
        SELECT *
        FROM embargos_rrhh
        WHERE empleado_id = ?
          AND empresa_id = ?
          AND COALESCE(estado, 'activo') = 'activo'
          AND date(fecha_inicio) < date(?)
          AND (fecha_fin IS NULL OR fecha_fin = '' OR date(fecha_fin) >= date(?))
        ORDER BY prioridad ASC, date(fecha_inicio) ASC, id ASC
        """,
        (empleado_id, _empresa_id(), fin, inicio),
    ).fetchall()
    total = 0.0
    detalle = []
    for fila in filas:
        saldo = _to_float(fila["saldo_pendiente"], 0.0)
        importe_total = _to_float(fila["importe_total"], 0.0)
        pendiente = saldo if saldo > 0 else importe_total
        cuota = _to_float(fila["cuota_mensual"], 0.0)
        porcentaje = _to_float(fila["porcentaje_nomina"], 0.0)
        if cuota <= 0 and porcentaje > 0:
            cuota = max(0.0, base_calculo) * porcentaje / 100.0
        if pendiente > 0:
            cuota = min(cuota, pendiente) if cuota > 0 else pendiente
        if cuota <= 0:
            continue
        total += cuota
        expediente = fila["numero_expediente"] or fila["organismo"] or f"Embargo {fila['id']}"
        detalle.append(f"{expediente}: {cuota:.2f} EUR")
    return round(total, 2), "; ".join(detalle)


def _calcular_prenomina_empleado(conn, empleado_id, anio, mes, guardar=True):
    config = _config_prenomina(conn, _empresa_id())
    inicio, fin, anio, mes = _periodo_prenomina(anio, mes, config)
    periodo_hasta = (date.fromisoformat(fin) - timedelta(days=1)).isoformat()
    horas_reales = _calcular_horas_fichajes(conn, empleado_id, inicio, fin)
    horas_turno, horas_nocturnas, horas_festivas = _calcular_turnos(conn, empleado_id, inicio, fin)
    salario_base, horas_semana, precio_hora = _datos_salario_empleado(conn, empleado_id)
    horas_referencia = horas_turno if horas_turno > 0 else horas_semana * 4.333
    # Horas reales por encima del cuadrante NO se abonan automáticamente.
    # Solo el tiempo extra aprobado por responsable entra como abonable.
    minutos_extra_aprobados, minutos_extra_no_aprobados = _minutos_extra_aprobados_mes(conn, empleado_id, inicio, fin) if 'tiempo_extra_rrhh' in [r['name'] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()] else (0, 0)
    horas_extra_aprobadas = round(minutos_extra_aprobados / 60, 2)
    horas_extra_no_aprobadas = round(minutos_extra_no_aprobados / 60, 2)
    horas_extra = horas_extra_aprobadas
    horas_dia = max(1.0, (horas_semana or 40.0) / 5.0)
    horas_ausencia_retribuidas, horas_ausencia_no_retribuidas, detalle_ausencias = _ausencias_prenomina_empleado(
        conn, empleado_id, inicio, fin, horas_dia
    )
    ausencias_estimadas = horas_ausencia_retribuidas + horas_ausencia_no_retribuidas
    ausencias = max(0, horas_referencia - horas_reales, ausencias_estimadas) if horas_referencia > 0 else ausencias_estimadas

    variables = conn.execute(
        """
        SELECT * FROM variables_prenomina
        WHERE empleado_id = ? AND date(fecha) >= date(?) AND date(fecha) < date(?)
        """,
        (empleado_id, inicio, fin),
    ).fetchall()
    pluses = 0.0
    descuentos = 0.0
    for v in variables:
        total = _to_float(v["importe_total"])
        if total == 0:
            total = _to_float(v["unidades"], 1.0) * _to_float(v["importe_unitario"])
        tipo = (v["tipo"] or "plus").lower()
        if tipo in {"descuento", "ausencia", "anticipo"}:
            descuentos += abs(total)
        else:
            pluses += total

    descuento_ausencias = horas_ausencia_no_retribuidas * precio_hora
    embargo_importe, embargo_detalle = _embargos_prenomina_empleado(
        conn, empleado_id, inicio, fin, salario_base + pluses + (horas_extra_aprobadas * precio_hora * 1.25)
    )
    descuentos_totales = descuentos + descuento_ausencias + embargo_importe
    coste_estimado = salario_base + pluses - descuentos_totales + (horas_extra_aprobadas * precio_hora * 1.25)
    irpf_pct = _irpf_pct_empleado(conn, empleado_id)
    irpf_importe = max(0.0, coste_estimado) * irpf_pct / 100.0
    neto_estimado = max(0.0, coste_estimado - irpf_importe)
    data = {
        "empleado_id": empleado_id,
        "mes": mes,
        "anio": anio,
        "horas_normales": round(min(horas_reales, horas_referencia), 2),
        "horas_extra": round(horas_extra_aprobadas, 2),
        "horas_extra_aprobadas": round(horas_extra_aprobadas, 2),
        "horas_extra_no_aprobadas": round(horas_extra_no_aprobadas, 2),
        "minutos_extra_aprobados": minutos_extra_aprobados,
        "minutos_extra_no_aprobados": minutos_extra_no_aprobados,
        "horas_nocturnas": round(horas_nocturnas, 2),
        "horas_festivas": round(horas_festivas, 2),
        "ausencias_horas": round(ausencias, 2),
        "salario_base_mes": round(salario_base, 2),
        "precio_hora": round(precio_hora, 2),
        "pluses": round(pluses, 2),
        "descuentos": round(descuentos_totales, 2),
        "coste_estimado": round(coste_estimado, 2),
        "irpf_pct": round(irpf_pct, 2),
        "irpf_importe": round(irpf_importe, 2),
        "neto_estimado": round(neto_estimado, 2),
        "embargo_importe": round(embargo_importe, 2),
        "embargo_detalle": embargo_detalle,
        "periodo_desde": inicio,
        "periodo_hasta": periodo_hasta,
        "horas_ausencia_retribuidas": round(horas_ausencia_retribuidas, 2),
        "horas_ausencia_no_retribuidas": round(horas_ausencia_no_retribuidas, 2),
        "detalle_ausencias": detalle_ausencias,
    }
    if guardar:
        conn.execute(
            """
            INSERT INTO resumen_prenomina
            (empleado_id, mes, anio, horas_normales, horas_extra, horas_nocturnas, horas_festivas, ausencias_horas,
             salario_base_mes, precio_hora, pluses, descuentos, coste_estimado, irpf_pct, irpf_importe, neto_estimado, empresa_id,
             horas_extra_aprobadas, horas_extra_no_aprobadas, minutos_extra_aprobados, minutos_extra_no_aprobados,
             periodo_desde, periodo_hasta, horas_ausencia_retribuidas, horas_ausencia_no_retribuidas,
             detalle_ausencias, embargo_importe, embargo_detalle, fecha_calculo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(empleado_id, mes, anio, empresa_id) DO UPDATE SET
                horas_normales = excluded.horas_normales,
                horas_extra = excluded.horas_extra,
                horas_nocturnas = excluded.horas_nocturnas,
                horas_festivas = excluded.horas_festivas,
                ausencias_horas = excluded.ausencias_horas,
                salario_base_mes = excluded.salario_base_mes,
                precio_hora = excluded.precio_hora,
                pluses = excluded.pluses,
                descuentos = excluded.descuentos,
                coste_estimado = excluded.coste_estimado,
                irpf_pct = excluded.irpf_pct,
                irpf_importe = excluded.irpf_importe,
                neto_estimado = excluded.neto_estimado,
                horas_extra_aprobadas = excluded.horas_extra_aprobadas,
                horas_extra_no_aprobadas = excluded.horas_extra_no_aprobadas,
                minutos_extra_aprobados = excluded.minutos_extra_aprobados,
                minutos_extra_no_aprobados = excluded.minutos_extra_no_aprobados,
                periodo_desde = excluded.periodo_desde,
                periodo_hasta = excluded.periodo_hasta,
                horas_ausencia_retribuidas = excluded.horas_ausencia_retribuidas,
                horas_ausencia_no_retribuidas = excluded.horas_ausencia_no_retribuidas,
                detalle_ausencias = excluded.detalle_ausencias,
                embargo_importe = excluded.embargo_importe,
                embargo_detalle = excluded.embargo_detalle,
                fecha_calculo = CURRENT_TIMESTAMP
            """,
            (
                empleado_id, mes, anio, data["horas_normales"], data["horas_extra"], data["horas_nocturnas"],
                data["horas_festivas"], data["ausencias_horas"], data["salario_base_mes"], data["precio_hora"],
                data["pluses"], data["descuentos"], data["coste_estimado"],
                data["irpf_pct"], data["irpf_importe"], data["neto_estimado"], _empresa_id(),
                data["horas_extra_aprobadas"], data["horas_extra_no_aprobadas"],
                data["minutos_extra_aprobados"], data["minutos_extra_no_aprobados"],
                data["periodo_desde"], data["periodo_hasta"],
                data["horas_ausencia_retribuidas"], data["horas_ausencia_no_retribuidas"],
                data["detalle_ausencias"], data["embargo_importe"], data["embargo_detalle"],
            ),
        )
        if _column_exists(conn, "resumen_prenomina", "horas_extra_aprobadas"):
            conn.execute(
                """
                UPDATE resumen_prenomina
                SET horas_extra_aprobadas = ?,
                    horas_extra_no_aprobadas = ?,
                    minutos_extra_aprobados = ?,
                    minutos_extra_no_aprobados = ?
                WHERE empleado_id = ? AND mes = ? AND anio = ? AND empresa_id = ?
                """,
                (
                    data["horas_extra_aprobadas"],
                    data["horas_extra_no_aprobadas"],
                    data["minutos_extra_aprobados"],
                    data["minutos_extra_no_aprobados"],
                    empleado_id, mes, anio, _empresa_id(),
                ),
            )
    return data


# ============================================================
# PRE-NÓMINA — rutas
# ============================================================

@rrhh_bp.route("/prenomina")
@login_required
def lista_prenomina():
    conn = get_db()
    _ensure_prenomina_schema(conn)
    empresa_id = _empresa_id()
    anio = _to_int(request.args.get("anio"), date.today().year)
    mes = _to_int(request.args.get("mes"), date.today().month)
    config_nomina = _config_prenomina(conn, empresa_id)
    periodo_desde, periodo_hasta, anio, mes = _periodo_prenomina_visible(anio, mes, config_nomina)
    _, periodo_fin_exclusivo, _, _ = _periodo_prenomina(anio, mes, config_nomina)
    scope_sql, scope_params = _departamento_scope_sql("e")
    resumen = conn.execute(
        f"""
        SELECT rp.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre,
               d.nombre departamento_nombre, c.nombre categoria_nombre
        FROM resumen_prenomina rp
        JOIN empleados e ON rp.empleado_id = e.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        LEFT JOIN categorias_laborales c ON e.categoria_id = c.id
        WHERE {scope_sql} AND rp.empresa_id = ? AND rp.anio = ? AND rp.mes = ?
        ORDER BY d.nombre, e.nombre, e.apellidos
        """,
        scope_params + [empresa_id, anio, mes],
    ).fetchall()
    empleados = conn.execute(
        f"""
        SELECT e.id, e.nombre, e.apellidos, d.nombre departamento_nombre
        FROM empleados e
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        WHERE {scope_sql} AND COALESCE(e.activo, 1) = 1
        ORDER BY e.nombre, e.apellidos
        """,
        scope_params,
    ).fetchall()
    ausencias_periodo = _ausencias_periodo_prenomina(
        conn, periodo_desde, periodo_fin_exclusivo, scope_sql, scope_params, empresa_id
    )
    conn.close()
    return render_template(
        "rrhh/prenomina.html",
        resumen=resumen,
        empleados=empleados,
        anio=anio,
        mes=mes,
        periodo_desde=periodo_desde,
        periodo_hasta=periodo_hasta,
        config_nomina=config_nomina,
        ausencias_periodo=ausencias_periodo,
        puede_editar=_usuario_puede_gestionar_cuadrantes(),
    )


@rrhh_bp.route("/prenomina/configuracion", methods=["POST"])
@login_required
@_require_rrhh_global
def guardar_configuracion_prenomina():
    conn = get_db()
    _ensure_prenomina_schema(conn)
    empresa_id = _empresa_id()
    anio = _to_int(request.form.get("anio"), date.today().year)
    mes = _to_int(request.form.get("mes"), date.today().month)
    modo_cierre = request.form.get("modo_cierre") or "mes_natural"
    if modo_cierre not in {"mes_natural", "dia_cierre"}:
        modo_cierre = "mes_natural"
    dia_inicio = max(1, min(_to_int(request.form.get("dia_inicio"), 1), 31))
    dia_cierre = max(0, min(_to_int(request.form.get("dia_cierre"), 0), 31))
    cerrar_horas_extra = 1 if request.form.get("cerrar_horas_extra") == "1" else 0
    cerrar_ausencias = 1 if request.form.get("cerrar_ausencias") == "1" else 0
    cerrar_variables = 1 if request.form.get("cerrar_variables") == "1" else 0
    observaciones = request.form.get("observaciones") or ""
    conn.execute(
        """
        INSERT INTO configuracion_prenomina
        (empresa_id, modo_cierre, dia_inicio, dia_cierre, cerrar_horas_extra, cerrar_ausencias,
         cerrar_variables, observaciones, fecha_actualizacion)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(empresa_id) DO UPDATE SET
            modo_cierre = excluded.modo_cierre,
            dia_inicio = excluded.dia_inicio,
            dia_cierre = excluded.dia_cierre,
            cerrar_horas_extra = excluded.cerrar_horas_extra,
            cerrar_ausencias = excluded.cerrar_ausencias,
            cerrar_variables = excluded.cerrar_variables,
            observaciones = excluded.observaciones,
            fecha_actualizacion = CURRENT_TIMESTAMP
        """,
        (
            empresa_id, modo_cierre, dia_inicio, dia_cierre, cerrar_horas_extra,
            cerrar_ausencias, cerrar_variables, observaciones,
        ),
    )
    conn.commit()
    conn.close()
    flash("Configuracion de pre-nomina guardada.", "success")
    return redirect(url_for("rrhh.lista_prenomina", anio=anio, mes=mes))


@rrhh_bp.route("/prenomina/calcular", methods=["POST"])
@login_required
@_require_rrhh_global
def calcular_prenomina():
    conn = get_db()
    _ensure_prenomina_schema(conn)
    anio = _to_int(request.form.get("anio"), date.today().year)
    mes = _to_int(request.form.get("mes"), date.today().month)
    empleado_id = request.form.get("empleado_id")
    if empleado_id:
        empleado = _empleado_en_ambito_rrhh(conn, empleado_id)
        if not empleado:
            conn.close()
            flash("Empleado no encontrado en tu empresa o centro activo.", "warning")
            return redirect(url_for("rrhh.lista_prenomina", anio=anio, mes=mes))
        ids = [empleado["id"]]
    else:
        scope_sql, scope_params = _departamento_scope_sql("e")
        ids = [
            r["id"]
            for r in conn.execute(
                f"""
                SELECT e.id
                FROM empleados e
                WHERE {scope_sql} AND COALESCE(e.activo,1)=1
                ORDER BY e.nombre, e.apellidos
                """,
                scope_params,
            ).fetchall()
        ]
    for eid in ids:
        _calcular_prenomina_empleado(conn, eid, anio, mes, guardar=True)
    conn.commit()
    conn.close()
    flash("Pre-nómina calculada correctamente.", "success")
    return redirect(url_for("rrhh.lista_prenomina", anio=anio, mes=mes))


@rrhh_bp.route("/prenomina/generar-nominas", methods=["POST"])
@login_required
@_require_rrhh_global
def generar_nominas_desde_prenomina():
    conn = get_db()
    _ensure_prenomina_schema(conn)
    ensure_rrhh_schema(conn)
    empresa_id = _empresa_id()
    anio = _to_int(request.form.get("anio"), date.today().year)
    mes = _to_int(request.form.get("mes"), date.today().month)
    empleado_id = request.form.get("empleado_id")
    scope_sql, scope_params = _departamento_scope_sql("e")
    filtros = ""
    params = list(scope_params) + [empresa_id, anio, mes]
    if empleado_id:
        filtros = " AND rp.empleado_id = ?"
        params.append(empleado_id)
    resumen = conn.execute(
        f"""
        SELECT rp.*, e.nombre, e.apellidos
        FROM resumen_prenomina rp
        JOIN empleados e ON rp.empleado_id = e.id
        WHERE {scope_sql}
          AND rp.empresa_id = ?
          AND rp.anio = ?
          AND rp.mes = ?
          {filtros}
        ORDER BY e.nombre, e.apellidos
        """,
        params,
    ).fetchall()
    creadas = 0
    actualizadas = 0
    omitidas = 0
    for r in resumen:
        existente = conn.execute(
            """
            SELECT *
            FROM nominas
            WHERE empleado_id = ? AND empresa_id = ? AND anio = ? AND mes = ?
              AND COALESCE(estado, 'pendiente') != 'anulada'
            LIMIT 1
            """,
            (r["empleado_id"], empresa_id, anio, mes),
        ).fetchone()
        salario_base = _to_float(r["salario_base_mes"], 0.0)
        pluses = _to_float(r["pluses"], 0.0)
        embargo = _to_float(r["embargo_importe"], 0.0)
        irpf = _to_float(r["irpf_importe"], 0.0)
        bruto = max(0.0, salario_base + pluses + (_to_float(r["horas_extra"], 0.0) * _to_float(r["precio_hora"], 0.0) * 1.25))
        neto = _to_float(r["neto_estimado"], 0.0)
        coste_empresa = _to_float(r["coste_estimado"], 0.0)
        observaciones = "Generada desde pre-nomina"
        if r["embargo_detalle"]:
            observaciones += f". Embargos: {r['embargo_detalle']}"
        if existente and (existente["estado"] or "pendiente") in {"pagada", "revisada"}:
            omitidas += 1
            continue
        if existente:
            conn.execute(
                """
                UPDATE nominas
                SET periodo_desde = ?, periodo_hasta = ?, salario_base = ?, complementos = ?,
                    horas_extra = ?, pluses = ?, irpf = ?, anticipos_embargos = ?,
                    bruto = ?, neto = ?, coste_empresa = ?, observaciones = ?,
                    motivo_cambio = ?, updated_at = CURRENT_TIMESTAMP, updated_by = ?
                WHERE id = ?
                """,
                (
                    r["periodo_desde"], r["periodo_hasta"], salario_base, 0,
                    r["horas_extra"], pluses, irpf, embargo,
                    bruto, neto, coste_empresa, observaciones,
                    "Actualizada desde pre-nomina", session.get("user_id"), existente["id"],
                ),
            )
            actualizadas += 1
        else:
            cur = conn.execute(
                """
                INSERT INTO nominas
                (empleado_id, mes, anio, periodo_desde, periodo_hasta, salario_base, complementos,
                 horas_extra, pluses, prorrata_pagas, irpf, seguridad_social_trabajador,
                 seguridad_social_empresa, anticipos_embargos, bruto, neto, coste_empresa, estado,
                 observaciones, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, 0, ?, ?, ?, ?, 'pendiente', ?, ?)
                """,
                (
                    r["empleado_id"], mes, anio, r["periodo_desde"], r["periodo_hasta"],
                    salario_base, 0, r["horas_extra"], pluses, irpf, embargo,
                    bruto, neto, coste_empresa, observaciones, empresa_id,
                ),
            )
            _registrar_historial(conn, "nominas", cur.lastrowid, "crear", f"Nomina {mes}/{anio} generada desde pre-nomina")
            creadas += 1
    conn.commit()
    conn.close()
    flash(f"Nominas generadas: {creadas}. Actualizadas: {actualizadas}. Omitidas: {omitidas}.", "success")
    return redirect(url_for("rrhh.lista_prenomina", anio=anio, mes=mes))


def _filtros_coste_personal(conn, empresa_id, empleado_id=None, departamento_id=None, subdepartamento_id=None):
    empleado_id = _to_int_or_none(empleado_id)
    departamento_id, subdepartamento_id = _normalizar_departamento_subdepartamento(
        conn, empresa_id, departamento_id, subdepartamento_id
    )
    scope_sql, scope_params = _departamento_scope_sql("e")
    filtros = [scope_sql, "COALESCE(e.activo, 1) = 1"]
    params = list(scope_params)
    if empleado_id:
        filtros.append("e.id = ?")
        params.append(empleado_id)
    if departamento_id:
        filtros.append("e.departamento_id = ?")
        params.append(departamento_id)
    if subdepartamento_id:
        filtros.append("e.subdepartamento_id = ?")
        params.append(subdepartamento_id)
    return " AND ".join(filtros), params, empleado_id, departamento_id, subdepartamento_id


def _sumar_coste_personal_grupo(grupos, clave, nombre, fila):
    if clave not in grupos:
        grupos[clave] = {
            "nombre": nombre or "Sin asignar",
            "empleados": 0,
            "horas_total": 0.0,
            "coste_prenomina": 0.0,
            "ss_empresa": 0.0,
            "coste_total": 0.0,
            "coste_nomina": 0.0,
        }
    grupo = grupos[clave]
    grupo["empleados"] += 1
    for campo in ["horas_total", "coste_prenomina", "ss_empresa", "coste_total", "coste_nomina"]:
        grupo[campo] += _to_float(fila.get(campo))


def _coste_personal_periodo(conn, anio, mes, empleado_id=None, departamento_id=None, subdepartamento_id=None, recalcular=False):
    _ensure_prenomina_schema(conn)
    empresa_id = _empresa_id()
    anio = _to_int(anio, date.today().year)
    mes = max(1, min(_to_int(mes, date.today().month), 12))
    config_nomina = _config_prenomina(conn, empresa_id)
    periodo_desde, periodo_hasta, anio, mes = _periodo_prenomina_visible(anio, mes, config_nomina)
    filtros_sql, filtros_params, empleado_id, departamento_id, subdepartamento_id = _filtros_coste_personal(
        conn, empresa_id, empleado_id, departamento_id, subdepartamento_id
    )

    empleados_ids = [
        r["id"]
        for r in conn.execute(
            f"""
            SELECT e.id
            FROM empleados e
            WHERE {filtros_sql}
            ORDER BY e.nombre, e.apellidos
            """,
            filtros_params,
        ).fetchall()
    ]
    existentes = set()
    if empleados_ids:
        placeholders = ",".join("?" for _ in empleados_ids)
        existentes = {
            r["empleado_id"]
            for r in conn.execute(
                f"""
                SELECT empleado_id
                FROM resumen_prenomina
                WHERE empresa_id = ? AND anio = ? AND mes = ? AND empleado_id IN ({placeholders})
                """,
                [empresa_id, anio, mes] + empleados_ids,
            ).fetchall()
        }

    calculados = 0
    for eid in empleados_ids:
        if recalcular or eid not in existentes:
            _calcular_prenomina_empleado(conn, eid, anio, mes, guardar=True)
            calculados += 1
    if calculados:
        conn.commit()

    dept_select = "d.nombre AS departamento_nombre" if _table_exists(conn, "departamentos") else "NULL AS departamento_nombre"
    dept_join = "LEFT JOIN departamentos d ON e.departamento_id = d.id" if _table_exists(conn, "departamentos") else ""
    sub_select = "sd.nombre AS subdepartamento_nombre" if _table_exists(conn, "subdepartamentos") else "NULL AS subdepartamento_nombre"
    sub_join = "LEFT JOIN subdepartamentos sd ON e.subdepartamento_id = sd.id" if _table_exists(conn, "subdepartamentos") else ""
    cat_select = "c.nombre AS categoria_nombre" if _table_exists(conn, "categorias_laborales") else "NULL AS categoria_nombre"
    cat_join = "LEFT JOIN categorias_laborales c ON e.categoria_id = c.id" if _table_exists(conn, "categorias_laborales") else ""
    filas_db = conn.execute(
        f"""
        SELECT rp.*,
               TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) AS empleado_nombre,
               e.nif,
               e.departamento_id,
               e.subdepartamento_id,
               {dept_select},
               {sub_select},
               {cat_select},
               n.id AS nomina_id,
               n.bruto AS nomina_bruto,
               n.neto AS nomina_neto,
               n.coste_empresa AS coste_nomina,
               n.seguridad_social_empresa AS ss_nomina,
               n.anticipos_embargos AS nomina_anticipos_embargos,
               n.estado AS nomina_estado
        FROM resumen_prenomina rp
        JOIN empleados e ON rp.empleado_id = e.id
        {dept_join}
        {sub_join}
        {cat_join}
        LEFT JOIN nominas n ON n.id = (
            SELECT n2.id
            FROM nominas n2
            WHERE n2.empleado_id = rp.empleado_id
              AND n2.empresa_id = rp.empresa_id
              AND n2.anio = rp.anio
              AND n2.mes = rp.mes
              AND COALESCE(n2.estado, 'pendiente') NOT IN ('anulada', 'anulado')
            ORDER BY n2.id DESC
            LIMIT 1
        )
        WHERE {filtros_sql}
          AND rp.empresa_id = ?
          AND rp.anio = ?
          AND rp.mes = ?
        ORDER BY departamento_nombre, subdepartamento_nombre, e.nombre, e.apellidos
        """,
        filtros_params + [empresa_id, anio, mes],
    ).fetchall()

    ss_empresa_pct = max(0.0, _to_float(config_nomina.get("ss_empresa_pct"), 0.332))
    filas = []
    grupos_departamento = {}
    grupos_subdepartamento = {}
    totales = {
        "empleados": 0,
        "horas_total": 0.0,
        "coste_prenomina": 0.0,
        "ss_empresa": 0.0,
        "coste_empresa_estimado": 0.0,
        "coste_nomina": 0.0,
        "coste_total": 0.0,
        "salario_base": 0.0,
        "pluses": 0.0,
        "descuentos": 0.0,
        "embargos": 0.0,
        "nomina_embargos": 0.0,
        "irpf_importe": 0.0,
        "neto_estimado": 0.0,
        "neto_nomina": 0.0,
        "nominas_reales": 0,
    }
    for row in filas_db:
        item = _row_to_dict(row)
        horas_total = _to_float(item.get("horas_normales")) + _to_float(item.get("horas_extra_aprobadas"))
        coste_prenomina = _to_float(item.get("coste_estimado"))
        ss_estimada = round(coste_prenomina * ss_empresa_pct, 2)
        coste_empresa_estimado = round(coste_prenomina + ss_estimada, 2)
        coste_nomina = _to_float(item.get("coste_nomina"))
        ss_nomina = _to_float(item.get("ss_nomina"))
        usa_nomina = coste_nomina > 0
        embargo_prenomina = _to_float(item.get("embargo_importe"))
        embargo_nomina = _to_float(item.get("nomina_anticipos_embargos"))
        neto_nomina = _to_float(item.get("nomina_neto"))
        ss_empresa = ss_nomina if usa_nomina and ss_nomina > 0 else ss_estimada
        coste_total = coste_nomina if usa_nomina else coste_empresa_estimado
        item.update(
            {
                "horas_total": round(horas_total, 2),
                "coste_prenomina": round(coste_prenomina, 2),
                "ss_empresa": round(ss_empresa, 2),
                "coste_empresa_estimado": round(coste_empresa_estimado, 2),
                "coste_nomina": round(coste_nomina, 2),
                "coste_total": round(coste_total, 2),
                "coste_hora_empresa": round(coste_total / horas_total, 2) if horas_total > 0 else 0.0,
                "embargo_aplicado": round(embargo_nomina if usa_nomina else embargo_prenomina, 2),
                "neto_operativo": round(neto_nomina if usa_nomina and neto_nomina > 0 else _to_float(item.get("neto_estimado")), 2),
                "origen_coste": "nomina" if usa_nomina else "estimado",
            }
        )
        filas.append(item)
        totales["empleados"] += 1
        totales["horas_total"] += item["horas_total"]
        totales["coste_prenomina"] += item["coste_prenomina"]
        totales["ss_empresa"] += item["ss_empresa"]
        totales["coste_empresa_estimado"] += item["coste_empresa_estimado"]
        totales["coste_nomina"] += item["coste_nomina"] if usa_nomina else 0.0
        totales["coste_total"] += item["coste_total"]
        totales["salario_base"] += _to_float(item.get("salario_base_mes"))
        totales["pluses"] += _to_float(item.get("pluses"))
        totales["descuentos"] += _to_float(item.get("descuentos"))
        totales["embargos"] += _to_float(item.get("embargo_importe"))
        totales["nomina_embargos"] += _to_float(item.get("nomina_anticipos_embargos")) if usa_nomina else 0.0
        totales["irpf_importe"] += _to_float(item.get("irpf_importe"))
        totales["neto_estimado"] += _to_float(item.get("neto_estimado"))
        totales["neto_nomina"] += _to_float(item.get("nomina_neto")) if usa_nomina else 0.0
        if usa_nomina:
            totales["nominas_reales"] += 1
        dept_key = item.get("departamento_id") or 0
        sub_key = f"{dept_key}:{item.get('subdepartamento_id') or 0}"
        _sumar_coste_personal_grupo(grupos_departamento, dept_key, item.get("departamento_nombre"), item)
        sub_nombre = item.get("subdepartamento_nombre") or "Sin subdepartamento"
        if item.get("departamento_nombre"):
            sub_nombre = f"{item.get('departamento_nombre')} / {sub_nombre}"
        _sumar_coste_personal_grupo(grupos_subdepartamento, sub_key, sub_nombre, item)

    for grupo in list(grupos_departamento.values()) + list(grupos_subdepartamento.values()):
        grupo["coste_hora_empresa"] = round(grupo["coste_total"] / grupo["horas_total"], 2) if grupo["horas_total"] > 0 else 0.0
    totales["coste_hora_empresa"] = round(totales["coste_total"] / totales["horas_total"], 2) if totales["horas_total"] > 0 else 0.0
    for key, value in list(totales.items()):
        if isinstance(value, float):
            totales[key] = round(value, 2)

    return {
        "filas": filas,
        "totales": totales,
        "grupos_departamento": sorted(grupos_departamento.values(), key=lambda g: g["coste_total"], reverse=True),
        "grupos_subdepartamento": sorted(grupos_subdepartamento.values(), key=lambda g: g["coste_total"], reverse=True),
        "anio": anio,
        "mes": mes,
        "periodo_desde": periodo_desde,
        "periodo_hasta": periodo_hasta,
        "config_nomina": config_nomina,
        "ss_empresa_pct": ss_empresa_pct,
        "ss_empresa_porcentaje": round(ss_empresa_pct * 100, 2),
        "empleado_id": empleado_id or "",
        "departamento_id": departamento_id or "",
        "subdepartamento_id": subdepartamento_id or "",
        "calculados": calculados,
    }


@rrhh_bp.route("/coste-personal")
@login_required
@_require_rrhh_global
def coste_personal():
    conn = get_db()
    empresa_id = _empresa_id()
    datos = _coste_personal_periodo(
        conn,
        request.args.get("anio"),
        request.args.get("mes"),
        request.args.get("empleado_id"),
        request.args.get("departamento_id"),
        request.args.get("subdepartamento_id"),
        recalcular=request.args.get("recalcular") == "1",
    )
    empleados = conn.execute(
        "SELECT id, nombre, apellidos FROM empleados WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre, apellidos",
        (empresa_id,),
    ).fetchall()
    departamentos = conn.execute(
        "SELECT id, nombre FROM departamentos WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre",
        (empresa_id,),
    ).fetchall() if _table_exists(conn, "departamentos") else []
    subdepartamentos = conn.execute(
        "SELECT id, nombre, departamento_id FROM subdepartamentos WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre",
        (empresa_id,),
    ).fetchall() if _table_exists(conn, "subdepartamentos") else []
    conn.close()
    return render_template(
        "rrhh/coste_personal.html",
        **datos,
        empleados=empleados,
        departamentos=departamentos,
        subdepartamentos=subdepartamentos,
    )


@rrhh_bp.route("/coste-personal/configuracion", methods=["POST"])
@login_required
@_require_rrhh_global
def guardar_configuracion_coste_personal():
    conn = get_db()
    _ensure_prenomina_schema(conn)
    empresa_id = _empresa_id()
    ss_pct = max(0.0, min(_to_float(request.form.get("ss_empresa_pct"), 33.2), 100.0)) / 100.0
    conn.execute(
        """
        INSERT INTO configuracion_prenomina (empresa_id, ss_empresa_pct, fecha_actualizacion)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(empresa_id) DO UPDATE SET
            ss_empresa_pct = excluded.ss_empresa_pct,
            fecha_actualizacion = CURRENT_TIMESTAMP
        """,
        (empresa_id, ss_pct),
    )
    conn.commit()
    conn.close()
    flash("Configuracion de coste de personal guardada.", "success")
    return redirect(
        url_for(
            "rrhh.coste_personal",
            anio=request.form.get("anio"),
            mes=request.form.get("mes"),
            empleado_id=request.form.get("empleado_id") or "",
            departamento_id=request.form.get("departamento_id") or "",
            subdepartamento_id=request.form.get("subdepartamento_id") or "",
        )
    )


@rrhh_bp.route("/coste-personal.csv")
@login_required
@_require_rrhh_global
def coste_personal_csv():
    conn = get_db()
    datos = _coste_personal_periodo(
        conn,
        request.args.get("anio"),
        request.args.get("mes"),
        request.args.get("empleado_id"),
        request.args.get("departamento_id"),
        request.args.get("subdepartamento_id"),
        recalcular=False,
    )
    conn.close()
    salida = io.StringIO()
    writer = csv.writer(salida, delimiter=";")
    writer.writerow([
        "Empleado", "NIF", "Departamento", "Subdepartamento", "Categoria", "Periodo desde", "Periodo hasta",
        "Horas normales", "Horas extra aprobadas", "Horas totales", "Salario base", "Pluses", "Descuentos", "Embargos",
        "Coste pre-nomina", "IRPF %", "IRPF importe", "Neto operativo", "SS empresa aplicada", "Coste empresa estimado", "Coste nomina real",
        "Coste total empresa", "Coste hora empresa", "Origen coste",
    ])
    for fila in datos["filas"]:
        writer.writerow([
            fila.get("empleado_nombre") or "",
            fila.get("nif") or "",
            fila.get("departamento_nombre") or "",
            fila.get("subdepartamento_nombre") or "",
            fila.get("categoria_nombre") or "",
            fila.get("periodo_desde") or datos["periodo_desde"],
            fila.get("periodo_hasta") or datos["periodo_hasta"],
            fila.get("horas_normales") or 0,
            fila.get("horas_extra_aprobadas") or fila.get("horas_extra") or 0,
            fila.get("horas_total") or 0,
            fila.get("salario_base_mes") or 0,
            fila.get("pluses") or 0,
            fila.get("descuentos") or 0,
            fila.get("embargo_aplicado") or 0,
            fila.get("coste_prenomina") or 0,
            fila.get("irpf_pct") or 0,
            fila.get("irpf_importe") or 0,
            fila.get("neto_operativo") or 0,
            fila.get("ss_empresa") or 0,
            fila.get("coste_empresa_estimado") or 0,
            fila.get("coste_nomina") or 0,
            fila.get("coste_total") or 0,
            fila.get("coste_hora_empresa") or 0,
            fila.get("origen_coste") or "",
        ])
    contenido = salida.getvalue().encode("utf-8-sig")
    return send_file(
        io.BytesIO(contenido),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"coste_personal_{datos['anio']}_{datos['mes']:02d}.csv",
    )


@rrhh_bp.route("/jornada")
@login_required
@_require_rrhh_global
def jornada_mensual():
    conn = get_db()
    anio = _to_int(request.args.get("anio"), date.today().year)
    mes = _to_int(request.args.get("mes"), date.today().month)
    empleado_id = _to_int_or_none(request.args.get("empleado_id"))
    departamento_id = _to_int_or_none(request.args.get("departamento_id"))
    subdepartamento_id = _to_int_or_none(request.args.get("subdepartamento_id"))
    centro_id = _to_int_or_none(request.args.get("centro_id")) or centro_activo_id()
    datos = _resumen_jornada_periodo(conn, anio, mes, empleado_id, departamento_id, subdepartamento_id, centro_id)
    scope_sql, scope_params = _departamento_scope_sql("e")
    empresa_id = _empresa_id()
    empleados = conn.execute(
        f"""
        SELECT e.id, e.nombre, e.apellidos
        FROM empleados e
        WHERE {scope_sql}
          AND e.empresa_id = ?
          AND COALESCE(e.activo, 1) = 1
        ORDER BY e.nombre, e.apellidos
        """,
        scope_params + [empresa_id],
    ).fetchall()
    departamentos = _departamentos_select(conn)
    subdepartamentos = _subdepartamentos_select(conn)
    centros = _centros_select(conn) if _table_exists(conn, "centros_trabajo") else []
    conn.close()
    return render_template(
        "rrhh/jornada_mensual.html",
        **datos,
        empleados=empleados,
        departamentos=departamentos,
        subdepartamentos=subdepartamentos,
        centros=centros,
        empleado_id=empleado_id or "",
        departamento_id=departamento_id or "",
        subdepartamento_id=subdepartamento_id or "",
        centro_id=centro_id or "",
        puede_editar=_usuario_puede_gestionar_cuadrantes(),
    )


@rrhh_bp.route("/jornada/cerrar", methods=["POST"])
@login_required
@_require_rrhh_global
def cerrar_jornada_mensual():
    conn = get_db()
    _ensure_control_tiempo_schema(conn)
    empresa_id = _empresa_id()
    anio = _to_int(request.form.get("anio"), date.today().year)
    mes = _to_int(request.form.get("mes"), date.today().month)
    datos = _resumen_jornada_periodo(conn, anio, mes)
    pendientes = sum(r["correcciones_pendientes"] for r in datos["resumen"])
    if pendientes:
        conn.close()
        flash("No se puede cerrar el mes con correcciones de fichaje pendientes.", "warning")
        return redirect(url_for("rrhh.jornada_mensual", anio=anio, mes=mes))
    hash_cierre = _hash_cierre_jornada(conn, anio, mes, datos["inicio"], datos["fin_exclusivo"], empresa_id)
    conn.execute(
        """
        INSERT INTO cierres_jornada_rrhh
        (empresa_id, anio, mes, periodo_desde, periodo_hasta, estado, cerrado_por, fecha_cierre, observaciones, hash_cierre)
        VALUES (?, ?, ?, ?, ?, 'cerrado', ?, CURRENT_TIMESTAMP, ?, ?)
        ON CONFLICT(empresa_id, anio, mes) DO UPDATE SET
            periodo_desde = excluded.periodo_desde,
            periodo_hasta = excluded.periodo_hasta,
            estado = 'cerrado',
            cerrado_por = excluded.cerrado_por,
            fecha_cierre = CURRENT_TIMESTAMP,
            observaciones = excluded.observaciones,
            hash_cierre = excluded.hash_cierre
        """,
        (empresa_id, anio, mes, datos["inicio"], datos["fin_visible"], session.get("user_id"), request.form.get("observaciones") or "", hash_cierre),
    )
    conn.commit()
    conn.close()
    flash("Mes de jornada cerrado.", "success")
    return redirect(url_for("rrhh.jornada_mensual", anio=anio, mes=mes))


@rrhh_bp.route("/jornada/reabrir", methods=["POST"])
@login_required
@_require_rrhh_global
def reabrir_jornada_mensual():
    conn = get_db()
    _ensure_control_tiempo_schema(conn)
    anio = _to_int(request.form.get("anio"), date.today().year)
    mes = _to_int(request.form.get("mes"), date.today().month)
    conn.execute(
        """
        UPDATE cierres_jornada_rrhh
        SET estado = 'reabierto',
            reabierto_por = ?,
            fecha_reapertura = CURRENT_TIMESTAMP,
            observaciones = ?
        WHERE empresa_id = ? AND anio = ? AND mes = ?
        """,
        (session.get("user_id"), request.form.get("observaciones") or "", _empresa_id(), anio, mes),
    )
    conn.commit()
    conn.close()
    flash("Mes de jornada reabierto.", "success")
    return redirect(url_for("rrhh.jornada_mensual", anio=anio, mes=mes))


@rrhh_bp.route("/jornada/informe.csv")
@login_required
@_require_rrhh_global
def informe_jornada_csv():
    conn = get_db()
    anio = _to_int(request.args.get("anio"), date.today().year)
    mes = _to_int(request.args.get("mes"), date.today().month)
    empleado_id = _to_int_or_none(request.args.get("empleado_id"))
    departamento_id = _to_int_or_none(request.args.get("departamento_id"))
    subdepartamento_id = _to_int_or_none(request.args.get("subdepartamento_id"))
    centro_id = _to_int_or_none(request.args.get("centro_id")) or centro_activo_id()
    datos = _resumen_jornada_periodo(conn, anio, mes, empleado_id, departamento_id, subdepartamento_id, centro_id)
    conn.close()
    salida = io.StringIO()
    writer = csv.writer(salida, delimiter=";")
    writer.writerow(["Empleado", "Departamento", "Subdepartamento", "Periodo desde", "Periodo hasta", "Horas reales", "Horas planificadas", "Diferencia", "Fichajes", "Entradas", "Salidas", "Pausas", "Reanudaciones", "Correcciones pendientes", "Estado cierre", "Hash cierre"])
    cierre = datos["cierre"]
    estado_cierre = cierre["estado"] if cierre else "abierto"
    hash_cierre = cierre["hash_cierre"] if cierre else ""
    for fila in datos["resumen"]:
        e = fila["empleado"]
        writer.writerow([f"{e['nombre']} {e['apellidos'] or ''}".strip(), e["departamento_nombre"] or "", e["subdepartamento_nombre"] or "", datos["inicio"], datos["fin_visible"], fila["horas_reales"], fila["horas_planificadas"], fila["diferencia"], fila["fichajes"], fila["entradas"], fila["salidas"], fila["pausas"], fila["reanudaciones"], fila["correcciones_pendientes"], estado_cierre, hash_cierre])
    contenido = salida.getvalue().encode("utf-8-sig")
    return send_file(io.BytesIO(contenido), mimetype="text/csv", as_attachment=True, download_name=f"registro_jornada_{anio}_{mes:02d}.csv")


@rrhh_bp.route("/jornada/informe-oficial.csv")
@login_required
@_require_rrhh_global
def informe_jornada_oficial_csv():
    conn = get_db()
    anio = _to_int(request.args.get("anio"), date.today().year)
    mes = _to_int(request.args.get("mes"), date.today().month)
    empleado_id = _to_int_or_none(request.args.get("empleado_id"))
    departamento_id = _to_int_or_none(request.args.get("departamento_id"))
    subdepartamento_id = _to_int_or_none(request.args.get("subdepartamento_id"))
    centro_id = _to_int_or_none(request.args.get("centro_id")) or centro_activo_id()
    datos = _detalle_jornada_oficial(conn, anio, mes, empleado_id, departamento_id, subdepartamento_id, centro_id)
    conn.close()

    salida = io.StringIO()
    writer = csv.writer(salida, delimiter=";")
    writer.writerow([
        "Periodo desde", "Periodo hasta", "Estado cierre", "Hash cierre", "Empleado ID", "Empleado", "NIF",
        "Departamento", "Subdepartamento", "Fecha", "Dia semana", "Centro", "Turno planificado",
        "Horas planificadas", "Fichajes", "Primera entrada", "Ultima salida", "Pausas minutos",
        "Horas trabajadas", "Diferencia", "Ausencia", "Correcciones pendientes", "Incidencias",
    ])
    for fila in datos["detalle"]:
        writer.writerow([
            fila["periodo_desde"], fila["periodo_hasta"], fila["estado_cierre"], fila["hash_cierre"],
            fila["empleado_id"], fila["empleado"], fila["nif"], fila["departamento"], fila["subdepartamento"],
            fila["fecha"], fila["dia_semana"], fila["centro"], fila["turno_planificado"], fila["horas_planificadas"],
            fila["fichajes"], fila["primera_entrada"], fila["ultima_salida"], fila["pausas_minutos"],
            fila["horas_trabajadas"], fila["diferencia"], fila["ausencia"], fila["correcciones_pendientes"],
            fila["incidencias"],
        ])
    contenido = salida.getvalue().encode("utf-8-sig")
    return send_file(
        io.BytesIO(contenido),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"registro_jornada_oficial_{anio}_{mes:02d}.csv",
    )


@rrhh_bp.route("/empleado/jornada/informe-oficial.csv")
@login_required
def informe_jornada_empleado_csv():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    _ensure_control_tiempo_schema(conn)
    empleado = _cargar_empleado_actual(conn)
    if not empleado:
        conn.close()
        flash("Tu usuario no esta vinculado a ninguna ficha de empleado activa.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))

    anio = _to_int(request.args.get("anio"), date.today().year)
    mes = max(1, min(_to_int(request.args.get("mes"), date.today().month), 12))
    datos = _detalle_jornada_oficial(conn, anio, mes, empleado["id"])
    _registrar_acceso_portal(conn, empleado["id"], "descargar_jornada_csv", f"{anio}-{mes:02d}")
    conn.commit()
    conn.close()

    salida = io.StringIO()
    writer = csv.writer(salida, delimiter=";")
    writer.writerow([
        "Periodo desde", "Periodo hasta", "Estado cierre", "Hash cierre", "Empleado ID", "Empleado", "NIF",
        "Departamento", "Subdepartamento", "Fecha", "Dia semana", "Centro", "Turno planificado",
        "Horas planificadas", "Fichajes", "Primera entrada", "Ultima salida", "Pausas minutos",
        "Horas trabajadas", "Diferencia", "Ausencia", "Correcciones pendientes", "Incidencias",
    ])
    for fila in datos["detalle"]:
        writer.writerow([
            fila["periodo_desde"], fila["periodo_hasta"], fila["estado_cierre"], fila["hash_cierre"],
            fila["empleado_id"], fila["empleado"], fila["nif"], fila["departamento"], fila["subdepartamento"],
            fila["fecha"], fila["dia_semana"], fila["centro"], fila["turno_planificado"], fila["horas_planificadas"],
            fila["fichajes"], fila["primera_entrada"], fila["ultima_salida"], fila["pausas_minutos"],
            fila["horas_trabajadas"], fila["diferencia"], fila["ausencia"], fila["correcciones_pendientes"],
            fila["incidencias"],
        ])
    contenido = salida.getvalue().encode("utf-8-sig")
    return send_file(
        io.BytesIO(contenido),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"mi_registro_jornada_{anio}_{mes:02d}.csv",
    )


@rrhh_bp.route("/turnos", methods=["GET", "POST"])
@login_required
def lista_turnos():
    conn = get_db()
    _ensure_prenomina_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        if not _usuario_es_global_rrhh():
            conn.close()
            flash("No tienes permisos para crear turnos.", "warning")
            return redirect(url_for("rrhh.lista_turnos"))
        conn.execute(
            """
            INSERT INTO turnos_rrhh (empleado_id, fecha, hora_inicio, hora_fin, tipo, departamento_id, observaciones, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.form.get("empleado_id"), request.form.get("fecha"), request.form.get("hora_inicio"),
                request.form.get("hora_fin"), request.form.get("tipo") or "normal", request.form.get("departamento_id") or None,
                request.form.get("observaciones"), empresa_id,
            ),
        )
        conn.commit()
        conn.close()
        flash("Turno creado correctamente.", "success")
        return redirect(url_for("rrhh.lista_turnos"))

    scope_sql, scope_params = _departamento_scope_sql("e")
    turnos = conn.execute(
        f"""
        SELECT t.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre, d.nombre departamento_nombre
        FROM turnos_rrhh t
        JOIN empleados e ON t.empleado_id = e.id
        LEFT JOIN puestos_operativos p ON t.puesto_id = p.id
        LEFT JOIN departamentos d ON COALESCE(t.departamento_id, p.departamento_id, e.departamento_id) = d.id
        WHERE {scope_sql} AND t.empresa_id = ?
        ORDER BY date(t.fecha) DESC, t.hora_inicio DESC
        LIMIT 300
        """,
        scope_params + [empresa_id],
    ).fetchall()
    empleados = _empleados_select(conn)
    departamentos = _departamentos_select(conn)
    conn.close()
    return render_template("rrhh/turnos.html", turnos=turnos, empleados=empleados, departamentos=departamentos, puede_editar=_usuario_es_global_rrhh())


@rrhh_bp.route("/variables-prenomina", methods=["GET", "POST"])
@login_required
def lista_variables_prenomina():
    conn = get_db()
    _ensure_prenomina_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        if not _usuario_es_global_rrhh():
            conn.close()
            flash("No tienes permisos para crear variables de pre-nómina.", "warning")
            return redirect(url_for("rrhh.lista_variables_prenomina"))
        unidades = _to_float(request.form.get("unidades"), 1.0)
        importe_unitario = _to_float(request.form.get("importe_unitario"), 0.0)
        importe_total = _to_float(request.form.get("importe_total"), 0.0) or unidades * importe_unitario
        conn.execute(
            """
            INSERT INTO variables_prenomina
            (empleado_id, fecha, concepto, tipo, unidades, importe_unitario, importe_total, observaciones, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.form.get("empleado_id"), request.form.get("fecha"), request.form.get("concepto"),
                request.form.get("tipo") or "plus", unidades, importe_unitario, importe_total,
                request.form.get("observaciones"), empresa_id,
            ),
        )
        conn.commit()
        conn.close()
        flash("Variable de pre-nómina registrada.", "success")
        return redirect(url_for("rrhh.lista_variables_prenomina"))

    scope_sql, scope_params = _departamento_scope_sql("e")
    variables = conn.execute(
        f"""
        SELECT v.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre
        FROM variables_prenomina v
        JOIN empleados e ON v.empleado_id = e.id
        WHERE {scope_sql} AND v.empresa_id = ?
        ORDER BY date(v.fecha) DESC, v.id DESC
        LIMIT 300
        """,
        scope_params + [empresa_id],
    ).fetchall()
    empleados = conn.execute("SELECT id, nombre, apellidos FROM empleados WHERE empresa_id = ? AND COALESCE(activo,1)=1 ORDER BY nombre, apellidos", (empresa_id,)).fetchall()
    conn.close()
    return render_template("rrhh/variables_prenomina.html", variables=variables, empleados=empleados, puede_editar=_usuario_es_global_rrhh())


@rrhh_bp.route("/embargos", methods=["GET", "POST"])
@login_required
def lista_embargos_rrhh():
    conn = get_db()
    _ensure_prenomina_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        if not _usuario_es_global_rrhh():
            conn.close()
            flash("No tienes permisos para crear embargos.", "warning")
            return redirect(url_for("rrhh.lista_embargos_rrhh"))
        if not _empleado_en_ambito_rrhh(conn, request.form.get("empleado_id")):
            conn.close()
            flash("Empleado no encontrado en tu empresa o centro activo.", "warning")
            return redirect(url_for("rrhh.lista_embargos_rrhh"))
        importe_total = _to_float(request.form.get("importe_total"), 0.0)
        saldo_pendiente = _to_float(request.form.get("saldo_pendiente"), 0.0) or importe_total
        conn.execute(
            """
            INSERT INTO embargos_rrhh
            (empleado_id, organismo, numero_expediente, tipo, fecha_inicio, fecha_fin,
             importe_total, saldo_pendiente, cuota_mensual, porcentaje_nomina, prioridad,
             estado, observaciones, empresa_id, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.form.get("empleado_id"), request.form.get("organismo"),
                request.form.get("numero_expediente"), request.form.get("tipo") or "embargo",
                request.form.get("fecha_inicio"), request.form.get("fecha_fin") or None,
                importe_total, saldo_pendiente, _to_float(request.form.get("cuota_mensual"), 0.0),
                _to_float(request.form.get("porcentaje_nomina"), 0.0),
                _to_int(request.form.get("prioridad"), 1),
                request.form.get("estado") or "activo", request.form.get("observaciones"),
                empresa_id, session.get("user_id"),
            ),
        )
        conn.commit()
        conn.close()
        flash("Embargo registrado. Recalcula pre-nomina para aplicarlo al periodo.", "success")
        return redirect(url_for("rrhh.lista_embargos_rrhh"))

    scope_sql, scope_params = _departamento_scope_sql("e")
    embargos = conn.execute(
        f"""
        SELECT b.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre,
               d.nombre departamento_nombre
        FROM embargos_rrhh b
        JOIN empleados e ON b.empleado_id = e.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        WHERE {scope_sql} AND b.empresa_id = ?
        ORDER BY CASE COALESCE(b.estado, 'activo') WHEN 'activo' THEN 0 ELSE 1 END,
                 b.prioridad ASC, e.nombre, e.apellidos
        """,
        scope_params + [empresa_id],
    ).fetchall()
    empleados = _empleados_select(conn)
    conn.close()
    return render_template(
        "rrhh/embargos.html",
        embargos=embargos,
        empleados=empleados,
        puede_editar=_usuario_es_global_rrhh(),
    )


@rrhh_bp.route("/embargos/<int:id>/editar", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def editar_embargo_rrhh(id):
    conn = get_db()
    _ensure_prenomina_schema(conn)
    embargo = _registro_empleado_en_ambito_rrhh(conn, "embargos_rrhh", id)
    if not embargo:
        conn.close()
        flash("Embargo no encontrado en tu empresa o centro activo.", "warning")
        return redirect(url_for("rrhh.lista_embargos_rrhh"))
    if request.method == "POST":
        if not _empleado_en_ambito_rrhh(conn, request.form.get("empleado_id")):
            conn.close()
            flash("Empleado no encontrado en tu empresa o centro activo.", "warning")
            return redirect(url_for("rrhh.lista_embargos_rrhh"))
        campos = {
            "empleado_id": request.form.get("empleado_id"),
            "organismo": request.form.get("organismo"),
            "numero_expediente": request.form.get("numero_expediente"),
            "tipo": request.form.get("tipo") or "embargo",
            "fecha_inicio": request.form.get("fecha_inicio"),
            "fecha_fin": request.form.get("fecha_fin") or None,
            "importe_total": _to_float(request.form.get("importe_total"), 0.0),
            "saldo_pendiente": _to_float(request.form.get("saldo_pendiente"), 0.0),
            "cuota_mensual": _to_float(request.form.get("cuota_mensual"), 0.0),
            "porcentaje_nomina": _to_float(request.form.get("porcentaje_nomina"), 0.0),
            "prioridad": _to_int(request.form.get("prioridad"), 1),
            "estado": request.form.get("estado") or "activo",
            "observaciones": request.form.get("observaciones"),
        }
        _safe_update(conn, "embargos_rrhh", id, campos, request.form.get("motivo_cambio"))
        conn.commit()
        conn.close()
        flash("Embargo actualizado. Recalcula pre-nomina si afecta al mes.", "success")
        return redirect(url_for("rrhh.lista_embargos_rrhh"))
    empleados = _empleados_select(conn)
    conn.close()
    return render_template("rrhh/embargo_form.html", embargo=embargo, empleados=empleados)


@rrhh_bp.route("/embargos/<int:id>/cerrar", methods=["POST"])
@login_required
@_require_rrhh_global
def cerrar_embargo_rrhh(id):
    conn = get_db()
    _ensure_prenomina_schema(conn)
    if not _registro_empleado_en_ambito_rrhh(conn, "embargos_rrhh", id):
        conn.close()
        flash("Embargo no encontrado en tu empresa o centro activo.", "warning")
        return redirect(url_for("rrhh.lista_embargos_rrhh"))
    _safe_update(
        conn,
        "embargos_rrhh",
        id,
        {"estado": request.form.get("estado") or "finalizado", "fecha_fin": request.form.get("fecha_fin") or date.today().isoformat()},
        request.form.get("motivo_cambio") or "Embargo cerrado",
    )
    conn.commit()
    conn.close()
    flash("Embargo cerrado.", "success")
    return redirect(url_for("rrhh.lista_embargos_rrhh"))




# ============================================================
# CUADRANTES Y VACACIONES
# ============================================================

def _ensure_cuadrantes_schema(conn):
    _ensure_prenomina_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vacaciones_rrhh (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empleado_id INTEGER NOT NULL,
            fecha_inicio TEXT NOT NULL,
            fecha_fin TEXT NOT NULL,
            tipo TEXT DEFAULT 'vacaciones',
            estado TEXT DEFAULT 'solicitada',
            dias REAL DEFAULT 0,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            usuario_creacion_id INTEGER,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "empleado_id INTEGER", "fecha_inicio TEXT", "fecha_fin TEXT", "tipo TEXT DEFAULT 'vacaciones'",
        "estado TEXT DEFAULT 'solicitada'", "dias REAL DEFAULT 0", "observaciones TEXT", "empresa_id INTEGER DEFAULT 1",
        "usuario_creacion_id INTEGER", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "vacaciones_rrhh", col)

    # CUADRANTE REAL: estructura flexible por centro, departamento, subdepartamento y puesto.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS centros_trabajo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            codigo TEXT,
            direccion TEXT,
            descripcion TEXT,
            empresa_id INTEGER DEFAULT 1,
            activo INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "nombre TEXT", "codigo TEXT", "direccion TEXT", "descripcion TEXT", "empresa_id INTEGER DEFAULT 1",
        "activo INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "centros_trabajo", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS puestos_operativos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            centro_id INTEGER,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            categoria_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            activo INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "nombre TEXT", "descripcion TEXT", "centro_id INTEGER", "departamento_id INTEGER", "subdepartamento_id INTEGER",
        "categoria_id INTEGER", "empresa_id INTEGER DEFAULT 1", "activo INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "puestos_operativos", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS necesidades_operativas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            centro_id INTEGER,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            puesto_id INTEGER,
            dia_semana INTEGER NOT NULL,
            personas_necesarias INTEGER DEFAULT 1,
            observaciones TEXT,
            empresa_id INTEGER DEFAULT 1,
            activo INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "centro_id INTEGER", "departamento_id INTEGER", "subdepartamento_id INTEGER", "puesto_id INTEGER",
        "dia_semana INTEGER", "personas_necesarias INTEGER DEFAULT 1", "observaciones TEXT",
        "empresa_id INTEGER DEFAULT 1", "activo INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
        "hora_inicio TEXT", "hora_fin TEXT", "tipo_turno TEXT", "horas_dia REAL DEFAULT 0",
        "plantilla_objetivo REAL DEFAULT 0", "cobertura_libranzas TEXT", "etiqueta_operativa TEXT",
    ]:
        _add_column_if_missing(conn, "necesidades_operativas", col)

    # Turnos existentes ampliados: no imponemos turnos mañana/tarde; guardamos horario real elegido por el departamento.
    for col in [
        "centro_id INTEGER", "subdepartamento_id INTEGER", "puesto_id INTEGER", "estado TEXT DEFAULT 'planificado'",
        "origen TEXT DEFAULT 'cuadrante_real'",
    ]:
        _add_column_if_missing(conn, "turnos_rrhh", col)

    permisos_extra = [
        ("ver_cuadrantes", "Ver cuadrantes de horarios"),
        ("gestionar_cuadrantes", "Crear y editar cuadrantes de horarios"),
        ("ver_vacaciones", "Ver vacaciones y ausencias"),
        ("gestionar_vacaciones", "Crear, aprobar y rechazar vacaciones"),
    ]
    for nombre, descripcion in permisos_extra:
        conn.execute("INSERT OR IGNORE INTO permisos (nombre, descripcion) VALUES (?, ?)", (nombre, descripcion))
    conn.commit()


def _rango_semana(fecha_txt=None):
    try:
        base = date.fromisoformat(fecha_txt) if fecha_txt else date.today()
    except Exception:
        base = date.today()
    inicio = base - timedelta(days=base.weekday())
    fin = inicio + timedelta(days=6)
    return inicio, fin


def _dias_entre(inicio_txt, fin_txt):
    try:
        ini = date.fromisoformat(inicio_txt)
        fin = date.fromisoformat(fin_txt)
        return max(0, (fin - ini).days + 1)
    except Exception:
        return 0


def _fecha_or_none(valor):
    try:
        return date.fromisoformat(_iso(valor))
    except Exception:
        return None


def _dias_solape(inicio_a, fin_a, inicio_b, fin_b):
    desde = max(inicio_a, inicio_b)
    hasta = min(fin_a, fin_b)
    if hasta < desde:
        return 0
    return (hasta - desde).days + 1


def _contrato_vacaciones_empleado(conn, empleado_id, empresa_id, anio):
    fin_anio = date(anio, 12, 31).isoformat()
    contrato = conn.execute(
        """
        SELECT *
        FROM contratos
        WHERE empresa_id = ?
          AND empleado_id = ?
          AND COALESCE(estado, 'activo') NOT IN ('anulado', 'cancelado', 'borrado')
          AND date(COALESCE(fecha_inicio, '1900-01-01')) <= date(?)
        ORDER BY date(COALESCE(fecha_inicio, fecha_creacion, '1900-01-01')) DESC, id DESC
        LIMIT 1
        """,
        (empresa_id, empleado_id, fin_anio),
    ).fetchone()
    return contrato


def _saldo_vacaciones_empleado(conn, empleado_id, anio=None):
    empresa_id = _empresa_id()
    anio = _to_int(anio, date.today().year)
    inicio_anio = date(anio, 1, 1)
    fin_anio = date(anio, 12, 31)
    hoy = date.today()
    empleado = conn.execute(
        """
        SELECT e.*, d.nombre AS departamento_nombre, sd.nombre AS subdepartamento_nombre
        FROM empleados e
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON e.subdepartamento_id = sd.id
        WHERE e.id = ? AND e.empresa_id = ?
        """,
        (empleado_id, empresa_id),
    ).fetchone()
    if not empleado:
        return None

    contrato = _contrato_vacaciones_empleado(conn, empleado_id, empresa_id, anio)
    derecho_anual = _to_float(contrato["vacaciones_dias"] if contrato and "vacaciones_dias" in contrato.keys() else None, 30.0) or 30.0
    alta = _fecha_or_none(empleado["fecha_alta"] if "fecha_alta" in empleado.keys() else None)
    baja = _fecha_or_none(empleado["fecha_baja"] if "fecha_baja" in empleado.keys() else None)
    if contrato:
        alta_contrato = _fecha_or_none(contrato["fecha_inicio"] if "fecha_inicio" in contrato.keys() else None)
        fin_contrato = _fecha_or_none(contrato["fecha_fin"] if "fecha_fin" in contrato.keys() else None)
        if alta_contrato and (not alta or alta_contrato > alta):
            alta = alta_contrato
        if fin_contrato and (not baja or fin_contrato < baja):
            baja = fin_contrato

    desde_activo = max(inicio_anio, alta or inicio_anio)
    hasta_activo = min(fin_anio, baja or fin_anio)
    dias_anio = (fin_anio - inicio_anio).days + 1
    dias_activo = max(0, (hasta_activo - desde_activo).days + 1) if hasta_activo >= desde_activo else 0
    devengadas = round(derecho_anual * dias_activo / dias_anio, 2) if dias_activo else 0.0

    solicitudes = conn.execute(
        """
        SELECT fecha_inicio, fecha_fin, estado
        FROM vacaciones_rrhh
        WHERE empresa_id = ?
          AND empleado_id = ?
          AND LOWER(COALESCE(tipo, 'vacaciones')) = 'vacaciones'
          AND COALESCE(estado, 'solicitada') IN ('solicitada', 'aprobada')
          AND date(fecha_inicio) <= date(?)
          AND date(fecha_fin) >= date(?)
        """,
        (empresa_id, empleado_id, fin_anio.isoformat(), inicio_anio.isoformat()),
    ).fetchall()
    aprobadas = 0.0
    disfrutadas = 0.0
    solicitadas = 0.0
    for solicitud in solicitudes:
        inicio = _fecha_or_none(solicitud["fecha_inicio"])
        fin = _fecha_or_none(solicitud["fecha_fin"])
        if not inicio or not fin:
            continue
        dias = _dias_solape(inicio, fin, inicio_anio, fin_anio)
        estado = (solicitud["estado"] or "solicitada").lower()
        if estado == "aprobada":
            aprobadas += dias
            hasta_disfrutado = min(fin, hoy, fin_anio)
            if hasta_disfrutado >= max(inicio, inicio_anio):
                disfrutadas += _dias_solape(inicio, hasta_disfrutado, inicio_anio, fin_anio)
        elif estado == "solicitada":
            solicitadas += dias

    aprobadas = round(aprobadas, 2)
    disfrutadas = round(disfrutadas, 2)
    solicitadas = round(solicitadas, 2)
    pendientes_aprobadas = round(max(0.0, aprobadas - disfrutadas), 2)
    disponible_sin_pendientes = round(devengadas - aprobadas, 2)
    disponible = round(devengadas - aprobadas - solicitadas, 2)
    return {
        "empleado": empleado,
        "anio": anio,
        "derecho_anual": round(derecho_anual, 2),
        "dias_activo": dias_activo,
        "devengadas": devengadas,
        "aprobadas": aprobadas,
        "disfrutadas": disfrutadas,
        "pendientes_aprobadas": pendientes_aprobadas,
        "solicitadas": solicitadas,
        "disponible_sin_pendientes": disponible_sin_pendientes,
        "disponible": disponible,
    }


def _saldos_vacaciones_periodo(conn, anio, scope_sql, scope_params):
    empresa_id = _empresa_id()
    empleados = conn.execute(
        f"""
        SELECT e.id
        FROM empleados e
        WHERE {scope_sql}
          AND e.empresa_id = ?
          AND COALESCE(e.activo, 1) = 1
        ORDER BY e.nombre, e.apellidos
        """,
        scope_params + [empresa_id],
    ).fetchall()
    saldos = []
    for empleado in empleados:
        saldo = _saldo_vacaciones_empleado(conn, empleado["id"], anio)
        if saldo:
            saldos.append(saldo)
    return saldos


@rrhh_bp.route("/cuadrantes")
@login_required
def cuadrantes():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    empresa_id = _empresa_id()
    inicio, fin = _rango_semana(request.args.get("fecha"))
    depto, subdepto = _normalizar_departamento_subdepartamento(
        conn,
        empresa_id,
        request.args.get("departamento_id"),
        request.args.get("subdepartamento_id"),
    )
    scope_sql, scope_params = _departamento_scope_sql("e")

    filtros_cuadrante = []
    params = scope_params + [empresa_id, inicio.isoformat(), fin.isoformat()]
    if depto is not None:
        filtros_cuadrante.append("e.departamento_id = ?")
        params.append(depto)
    if subdepto is not None:
        filtros_cuadrante.append("e.subdepartamento_id = ?")
        params.append(subdepto)

    filtro_cuadrante_sql = ""
    if filtros_cuadrante:
        filtro_cuadrante_sql = " AND " + " AND ".join(filtros_cuadrante)

    turnos = conn.execute(
        f"""
        SELECT t.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre,
               d.nombre departamento_nombre,
               sd.nombre subdepartamento_nombre
        FROM turnos_rrhh t
        JOIN empleados e ON t.empleado_id = e.id
        LEFT JOIN puestos_operativos p ON t.puesto_id = p.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON e.subdepartamento_id = sd.id
        WHERE {scope_sql}
          AND t.empresa_id = ?
          AND date(t.fecha) BETWEEN date(?) AND date(?)
          {filtro_cuadrante_sql}
        ORDER BY date(t.fecha), d.nombre, sd.nombre, e.nombre, t.hora_inicio
        """,
        params,
    ).fetchall()

    empleados = conn.execute(
        f"""
        SELECT e.id, e.nombre, e.apellidos, e.departamento_id, d.nombre departamento_nombre
        FROM empleados e
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        WHERE {scope_sql} AND COALESCE(e.activo,1)=1
        ORDER BY d.nombre, e.nombre, e.apellidos
        """,
        scope_params,
    ).fetchall()
    departamentos = _departamentos_select(conn)
    dep_ids_filtros = [d["id"] for d in departamentos]
    if centro_activo_id() and dep_ids_filtros and _table_exists(conn, "subdepartamentos"):
        subdepartamentos = conn.execute(
            f"""
            SELECT sd.id, sd.nombre, sd.departamento_id, d.nombre AS departamento_nombre
            FROM subdepartamentos sd
            LEFT JOIN departamentos d ON sd.departamento_id = d.id
            WHERE sd.empresa_id = ? AND sd.departamento_id IN ({','.join('?' for _ in dep_ids_filtros)})
            ORDER BY d.nombre, sd.nombre
            """,
            [empresa_id] + dep_ids_filtros,
        ).fetchall()
    elif centro_activo_id():
        subdepartamentos = []
    else:
        subdepartamentos = conn.execute(
            """
            SELECT sd.id, sd.nombre, sd.departamento_id, d.nombre AS departamento_nombre
            FROM subdepartamentos sd
            LEFT JOIN departamentos d ON sd.departamento_id = d.id
            WHERE sd.empresa_id = ?
            ORDER BY d.nombre, sd.nombre
            """,
            (empresa_id,),
        ).fetchall() if _table_exists(conn, "subdepartamentos") else []
    filtros_vacaciones = []
    params_vacaciones = scope_params + [empresa_id, fin.isoformat(), inicio.isoformat()]
    if depto is not None:
        filtros_vacaciones.append("e.departamento_id = ?")
        params_vacaciones.append(depto)
    if subdepto is not None:
        filtros_vacaciones.append("e.subdepartamento_id = ?")
        params_vacaciones.append(subdepto)
    filtro_vacaciones_sql = ""
    if filtros_vacaciones:
        filtro_vacaciones_sql = " AND " + " AND ".join(filtros_vacaciones)

    vacaciones = conn.execute(
        f"""
        SELECT v.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre
        FROM vacaciones_rrhh v
        JOIN empleados e ON v.empleado_id = e.id
        WHERE {scope_sql}
          AND v.empresa_id = ?
          AND date(v.fecha_inicio) <= date(?)
          AND date(v.fecha_fin) >= date(?)
          AND v.estado IN ('solicitada','aprobada')
          {filtro_vacaciones_sql}
        ORDER BY v.fecha_inicio
        """,
        params_vacaciones,
    ).fetchall()
    conn.close()

    dias = [inicio + timedelta(days=i) for i in range(7)]
    return render_template(
        "rrhh/cuadrantes.html",
        turnos=turnos,
        empleados=empleados,
        departamentos=departamentos,
        subdepartamentos=subdepartamentos,
        vacaciones=vacaciones,
        dias=dias,
        inicio=inicio,
        fin=fin,
        departamento_id=depto,
        subdepartamento_id=subdepto,
        puede_editar=_usuario_es_global_rrhh(),
        timedelta=timedelta,
    )


@rrhh_bp.route("/cuadrantes/crear", methods=["POST"])
@login_required
@_require_cuadrantes
def crear_turno_cuadrante():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    empresa_id = _empresa_id()
    repetir = _to_int(request.form.get("repetir_semanas"), 1)
    repetir = max(1, min(8, repetir))
    fecha = request.form.get("fecha") or date.today().isoformat()
    try:
        fecha_base = date.fromisoformat(fecha)
    except Exception:
        fecha_base = date.today()
    for i in range(repetir):
        fecha_turno = fecha_base + timedelta(days=7 * i)
        conn.execute(
            """
            INSERT INTO turnos_rrhh (empleado_id, fecha, hora_inicio, hora_fin, tipo, departamento_id, subdepartamento_id, observaciones, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.form.get("empleado_id"), fecha_turno.isoformat(), request.form.get("hora_inicio"),
                request.form.get("hora_fin"), request.form.get("tipo") or "normal", request.form.get("departamento_id") or None,
                request.form.get("subdepartamento_id") or None, request.form.get("observaciones"), empresa_id,
            ),
        )
    conn.commit()
    conn.close()
    flash("Turno añadido al cuadrante.", "success")
    return _redirect_cuadrante_real_con_filtros(fecha)


@rrhh_bp.route("/cuadrantes/<int:id>/eliminar", methods=["POST"])
@login_required
@_require_cuadrantes
def eliminar_turno_cuadrante(id):
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    turno = conn.execute("SELECT fecha FROM turnos_rrhh WHERE id = ? AND empresa_id = ?", (id, _empresa_id())).fetchone()
    fecha = turno["fecha"] if turno else None
    _safe_update(conn, "turnos_rrhh", id, {"estado": "anulado"}, "Anulado desde cuadrante")
    conn.commit()
    conn.close()
    flash("Turno anulado del cuadrante sin borrar histórico.", "success")
    return _redirect_cuadrante_real_con_filtros(fecha)


@rrhh_bp.route("/vacaciones", methods=["GET", "POST"])
@login_required
def vacaciones():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    _ensure_ausencias_extra_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        if not _usuario_es_global_rrhh():
            empleado_id = session.get("user_empleado_id")
        else:
            empleado_id = request.form.get("empleado_id")
        if not empleado_id:
            conn.close()
            flash("No hay empleado vinculado para registrar la solicitud.", "warning")
            return redirect(url_for("rrhh.vacaciones"))
        inicio = request.form.get("fecha_inicio")
        fin = request.form.get("fecha_fin") or inicio
        tipo = _tipo_ausencia_valido(request.form.get("tipo"))
        config = _config_tipo_ausencia(tipo)
        estado = request.form.get("estado") if _usuario_es_global_rrhh() else "solicitada"
        if estado not in {"solicitada", "aprobada", "rechazada", "cancelada"}:
            estado = "solicitada"
        documento_id = request.form.get("documento_id") or None
        try:
            documento_subido_id = _guardar_documento_ausencia(conn, empleado_id, tipo, request.files.get("archivo_documento"))
        except ValueError as exc:
            conn.close()
            flash(str(exc), "warning")
            return redirect(url_for("rrhh.vacaciones"))
        if documento_subido_id:
            documento_id = documento_subido_id
        conn.execute(
            """
            INSERT INTO vacaciones_rrhh
            (empleado_id, fecha_inicio, fecha_fin, tipo, estado, dias, observaciones, empresa_id, usuario_creacion_id,
             requiere_documento, documento_id, afecta_prenomina, porcentaje_cobro, codigo_oficial, origen, color)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                empleado_id,
                inicio,
                fin,
                tipo,
                estado,
                _dias_entre(inicio, fin),
                request.form.get("observaciones"),
                empresa_id,
                session.get("user_id"),
                config["requiere_documento"],
                documento_id,
                config["afecta_prenomina"],
                config["porcentaje_cobro"],
                config["codigo_oficial"],
                request.form.get("origen") or ("manual_rrhh" if _usuario_es_global_rrhh() else "portal_empleado"),
                config["color"],
            ),
        )
        nuevo_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        _registrar_historial(conn, "vacaciones_rrhh", nuevo_id, "crear", f"Solicitud/ausencia: {tipo}")
        conn.commit()
        conn.close()
        flash("Registro de vacaciones/ausencia guardado.", "success")
        return redirect(url_for("rrhh.vacaciones"))

    scope_sql, scope_params = _departamento_scope_sql("e")
    anio = _to_int(request.args.get("anio"), date.today().year)
    tipo_filtro = _tipo_ausencia_valido(request.args.get("tipo")) if request.args.get("tipo") else ""
    estado_filtro = request.args.get("estado") if request.args.get("estado") in {"solicitada", "aprobada", "rechazada", "cancelada"} else ""
    filtros_extra = []
    params = scope_params + [empresa_id, str(anio)]
    if tipo_filtro:
        filtros_extra.append("v.tipo = ?")
        params.append(tipo_filtro)
    if estado_filtro:
        filtros_extra.append("v.estado = ?")
        params.append(estado_filtro)
    filtros_extra_sql = (" AND " + " AND ".join(filtros_extra)) if filtros_extra else ""
    saldos_vacaciones = _saldos_vacaciones_periodo(conn, anio, scope_sql, scope_params)
    registros = conn.execute(
        f"""
        SELECT v.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre,
               d.nombre departamento_nombre,
               doc.nombre AS documento_nombre,
               doc.archivo AS documento_archivo
        FROM vacaciones_rrhh v
        JOIN empleados e ON v.empleado_id = e.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        LEFT JOIN documentos_rrhh doc ON v.documento_id = doc.id AND doc.empresa_id = v.empresa_id
        WHERE {scope_sql} AND v.empresa_id = ? AND strftime('%Y', v.fecha_inicio) = ?
        {filtros_extra_sql}
        ORDER BY date(v.fecha_inicio) DESC, e.nombre
        """,
        params,
    ).fetchall()
    empleados = conn.execute(
        "SELECT id, nombre, apellidos FROM empleados WHERE empresa_id = ? AND COALESCE(activo,1)=1 ORDER BY nombre, apellidos",
        (empresa_id,),
    ).fetchall() if _usuario_es_global_rrhh() else []
    documentos = conn.execute(
        """
        SELECT id, nombre, tipo_documento, empleado_id
        FROM documentos_rrhh
        WHERE empresa_id = ? AND COALESCE(estado, 'vigente') != 'anulado'
        ORDER BY date(COALESCE(fecha_documento, fecha_creacion, '1900-01-01')) DESC, id DESC
        LIMIT 300
        """,
        (empresa_id,),
    ).fetchall() if _usuario_es_global_rrhh() else []
    conn.close()
    return render_template(
        "rrhh/vacaciones.html",
        registros=registros,
        empleados=empleados,
        documentos=documentos,
        saldos_vacaciones=saldos_vacaciones,
        anio=anio,
        tipo_filtro=tipo_filtro,
        estado_filtro=estado_filtro,
        tipos_ausencia=_tipos_ausencia_select(),
        puede_editar=_usuario_es_global_rrhh(),
    )


@rrhh_bp.route("/vacaciones/<int:id>/estado", methods=["POST"])
@login_required
@_require_rrhh_global
def cambiar_estado_vacaciones(id):
    estado = request.form.get("estado")
    if estado not in {"solicitada", "aprobada", "rechazada", "cancelada"}:
        estado = "solicitada"
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    _safe_update(conn, "vacaciones_rrhh", id, {"estado": estado}, request.form.get("motivo_cambio") or f"Solicitud {estado} desde pendientes RRHH")
    conn.commit()
    conn.close()
    flash("Estado actualizado.", "success")
    return _redirect_post_rrhh("rrhh.vacaciones")



# ============================================================
# CUADRANTE REAL — estructura por establecimiento y departamento
# ============================================================



@rrhh_bp.route("/vacaciones/saldos.csv")
@login_required
@_require_rrhh_global
def saldos_vacaciones_csv():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    anio = _to_int(request.args.get("anio"), date.today().year)
    scope_sql, scope_params = _departamento_scope_sql("e")
    saldos = _saldos_vacaciones_periodo(conn, anio, scope_sql, scope_params)
    conn.close()
    salida = io.StringIO()
    writer = csv.writer(salida, delimiter=";")
    writer.writerow([
        "Empleado", "Departamento", "Subdepartamento", "Anio", "Derecho anual", "Dias activo",
        "Generadas", "Aprobadas", "Disfrutadas", "Aprobadas pendientes", "Solicitadas", "Disponible",
    ])
    for saldo in saldos:
        empleado = saldo["empleado"]
        writer.writerow([
            f"{empleado['nombre']} {empleado['apellidos'] or ''}".strip(),
            empleado["departamento_nombre"] or "",
            empleado["subdepartamento_nombre"] or "",
            saldo["anio"],
            saldo["derecho_anual"],
            saldo["dias_activo"],
            saldo["devengadas"],
            saldo["aprobadas"],
            saldo["disfrutadas"],
            saldo["pendientes_aprobadas"],
            saldo["solicitadas"],
            saldo["disponible"],
        ])
    contenido = salida.getvalue().encode("utf-8-sig")
    return send_file(
        io.BytesIO(contenido),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"saldos_vacaciones_{anio}.csv",
    )


def _redirect_cuadrante_real_con_filtros(fecha=None):
    """Vuelve al cuadrante real conservando los filtros seleccionados.
    Evita que al asignar, editar, anular, copiar o marcar libre se pierda la vista actual.
    """
    fecha_destino = (
        fecha
        or request.form.get('filtro_fecha')
        or request.form.get('fecha')
        or request.args.get('fecha')
        or date.today().isoformat()
    )
    return redirect(url_for(
        'rrhh.cuadrante_real',
        fecha=fecha_destino,
        centro_id=request.form.get('filtro_centro_id') or request.form.get('centro_id') or request.args.get('centro_id') or '',
        departamento_id=request.form.get('filtro_departamento_id') or request.form.get('departamento_id') or request.args.get('departamento_id') or '',
        subdepartamento_id=request.form.get('filtro_subdepartamento_id') or request.form.get('subdepartamento_id') or request.args.get('subdepartamento_id') or '',
        puesto_id=request.form.get('filtro_puesto_id') or request.form.get('puesto_id') or request.args.get('puesto_id') or '',
    ))

_DIAS_SEMANA = [
    (0, "Lunes"), (1, "Martes"), (2, "Miércoles"), (3, "Jueves"),
    (4, "Viernes"), (5, "Sábado"), (6, "Domingo"),
]

def _rows_to_dicts(rows):
    return [dict(r) for r in rows]


@rrhh_bp.route("/puestos")
@login_required
def lista_puestos_operativos():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    centro_where = ""
    centro_params = []
    if centro_id:
        dep_ids = ids_departamentos_centro(conn, empresa_id, centro_id)
        condiciones = ["p.centro_id = ?"]
        centro_params.append(centro_id)
        if dep_ids:
            condiciones.append(f"p.departamento_id IN ({','.join('?' for _ in dep_ids)})")
            centro_params.extend(dep_ids)
        centro_where = f" AND ({' OR '.join(condiciones)})"
    puestos = conn.execute(
        f"""
        SELECT p.*, ct.nombre centro_nombre, d.nombre departamento_nombre,
               sd.nombre subdepartamento_nombre, c.nombre categoria_nombre
        FROM puestos_operativos p
        LEFT JOIN centros_trabajo ct ON p.centro_id = ct.id
        LEFT JOIN departamentos d ON p.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON p.subdepartamento_id = sd.id
        LEFT JOIN categorias_laborales c ON p.categoria_id = c.id
        WHERE p.empresa_id = ?{centro_where}
        ORDER BY COALESCE(ct.nombre,''), COALESCE(d.nombre,''), COALESCE(sd.nombre,''), p.nombre
        """,
        [empresa_id] + centro_params,
    ).fetchall()
    conn.close()
    return render_template("rrhh/puestos_operativos.html", puestos=puestos, puede_editar=_usuario_es_global_rrhh())


@rrhh_bp.route("/puestos/nuevo", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def nuevo_puesto_operativo():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        conn.execute(
            """
            INSERT INTO puestos_operativos
            (nombre, descripcion, centro_id, departamento_id, subdepartamento_id, categoria_id, empresa_id, activo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.form.get("nombre"), request.form.get("descripcion"), request.form.get("centro_id") or None,
                request.form.get("departamento_id") or None, request.form.get("subdepartamento_id") or None,
                request.form.get("categoria_id") or None, empresa_id, 1 if request.form.get("activo", "1") else 0,
            ),
        )
        conn.commit()
        conn.close()
        flash("Puesto operativo creado correctamente.", "success")
        return redirect(url_for("rrhh.lista_puestos_operativos"))
    centros = _centros_select(conn)
    departamentos = _departamentos_select(conn)
    subdepartamentos = _subdepartamentos_select(conn)
    categorias = conn.execute("SELECT id, nombre FROM categorias_laborales WHERE empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    conn.close()
    return render_template("rrhh/puesto_form.html", centros=centros, departamentos=departamentos, subdepartamentos=subdepartamentos, categorias=categorias, puesto=None)


@rrhh_bp.route("/centros", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def centros_trabajo():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        cur = conn.execute(
            """
            INSERT INTO centros_trabajo (nombre, codigo, direccion, descripcion, empresa_id, activo)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (request.form.get("nombre"), request.form.get("codigo"), request.form.get("direccion"), request.form.get("descripcion"), empresa_id),
        )
        if request.form.get("crear_estructura_base"):
            from blueprints.departamentos.routes import crear_estructura_base_centro
            crear_estructura_base_centro(conn, empresa_id, cur.lastrowid)
        conn.commit()
        flash("Centro de trabajo creado.", "success")
        if request.form.get("crear_estructura_base"):
            return redirect(url_for("departamentos.index", centro_id=cur.lastrowid))
        return redirect(url_for("rrhh.centros_trabajo"))
    centros = conn.execute("SELECT * FROM centros_trabajo WHERE empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    conn.close()
    return render_template("rrhh/centros_trabajo.html", centros=centros)


def _ids(rows):
    return [row["id"] for row in rows]


def _in_clause(ids):
    return ",".join("?" for _ in ids)


def _fetch_limited(conn, sql, params=(), limit=8):
    return conn.execute(f"{sql} LIMIT {int(limit)}", params).fetchall()


def _centro_scope_conditions(conn, centro_id, departamento_ids, subdepartamento_ids, punto_venta_ids):
    scope = {}
    if _table_exists(conn, "puntos_venta"):
        conds = []
        params = []
        if _column_exists(conn, "puntos_venta", "centro_id"):
            conds.append("pv.centro_id = ?")
            params.append(centro_id)
        if departamento_ids and _column_exists(conn, "puntos_venta", "departamento_id"):
            conds.append(f"pv.departamento_id IN ({_in_clause(departamento_ids)})")
            params.extend(departamento_ids)
        scope["puntos_venta"] = (" OR ".join(conds) or "1=0", params)

    if _table_exists(conn, "almacenes"):
        conds = []
        params = []
        if _column_exists(conn, "almacenes", "centro_id"):
            conds.append("a.centro_id = ?")
            params.append(centro_id)
        if departamento_ids:
            conds.append(f"a.departamento_id IN ({_in_clause(departamento_ids)})")
            params.extend(departamento_ids)
        if subdepartamento_ids and _column_exists(conn, "almacenes", "subdepartamento_id"):
            conds.append(f"a.subdepartamento_id IN ({_in_clause(subdepartamento_ids)})")
            params.extend(subdepartamento_ids)
        scope["almacenes"] = (" OR ".join(conds) or "1=0", params)

    if _table_exists(conn, "empleados"):
        conds = []
        params = []
        if departamento_ids and _column_exists(conn, "empleados", "departamento_id"):
            conds.append(f"e.departamento_id IN ({_in_clause(departamento_ids)})")
            params.extend(departamento_ids)
        if subdepartamento_ids and _column_exists(conn, "empleados", "subdepartamento_id"):
            conds.append(f"e.subdepartamento_id IN ({_in_clause(subdepartamento_ids)})")
            params.extend(subdepartamento_ids)
        if punto_venta_ids and _column_exists(conn, "empleados", "punto_venta_id"):
            conds.append(f"e.punto_venta_id IN ({_in_clause(punto_venta_ids)})")
            params.extend(punto_venta_ids)
        scope["empleados"] = (" OR ".join(conds) or "1=0", params)

    menu_conds = []
    menu_params = []
    if departamento_ids:
        menu_conds.append(f"m.departamento_id IN ({_in_clause(departamento_ids)})")
        menu_params.extend(departamento_ids)
    if punto_venta_ids:
        menu_conds.append(f"m.punto_venta_id IN ({_in_clause(punto_venta_ids)})")
        menu_params.extend(punto_venta_ids)
    scope["menus"] = (" OR ".join(menu_conds) or "1=0", menu_params)

    pedido_conds = []
    pedido_params = []
    if departamento_ids and _column_exists(conn, "pedidos", "departamento_solicitante_id"):
        pedido_conds.append(f"p.departamento_solicitante_id IN ({_in_clause(departamento_ids)})")
        pedido_params.extend(departamento_ids)
    if departamento_ids and _column_exists(conn, "pedidos", "departamento_id"):
        pedido_conds.append(f"p.departamento_id IN ({_in_clause(departamento_ids)})")
        pedido_params.extend(departamento_ids)
    if subdepartamento_ids and _column_exists(conn, "pedidos", "subdepartamento_solicitante_id"):
        pedido_conds.append(f"p.subdepartamento_solicitante_id IN ({_in_clause(subdepartamento_ids)})")
        pedido_params.extend(subdepartamento_ids)
    if punto_venta_ids and _column_exists(conn, "pedidos", "punto_venta_id"):
        pedido_conds.append(f"p.punto_venta_id IN ({_in_clause(punto_venta_ids)})")
        pedido_params.extend(punto_venta_ids)
    scope["pedidos"] = (" OR ".join(pedido_conds) or "1=0", pedido_params)

    return scope


def _build_centro_panel(conn, empresa_id, centro_id):
    centro = conn.execute(
        "SELECT * FROM centros_trabajo WHERE id = ? AND empresa_id = ?",
        (centro_id, empresa_id),
    ).fetchone()
    if not centro:
        return None

    departamentos = conn.execute(
        """
        SELECT *
        FROM departamentos
        WHERE empresa_id = ? AND centro_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY nombre
        """,
        (empresa_id, centro_id),
    ).fetchall() if _table_exists(conn, "departamentos") else []
    departamento_ids = _ids(departamentos)

    subdepartamentos = []
    if departamento_ids and _table_exists(conn, "subdepartamentos"):
        subdepartamentos = conn.execute(
            f"""
            SELECT sd.*, d.nombre AS departamento_nombre
            FROM subdepartamentos sd
            LEFT JOIN departamentos d ON d.id = sd.departamento_id
            WHERE sd.empresa_id = ?
              AND COALESCE(sd.activo, 1) = 1
              AND (sd.centro_id = ? OR sd.departamento_id IN ({_in_clause(departamento_ids)}))
            ORDER BY COALESCE(d.nombre, ''), sd.nombre
            """,
            [empresa_id, centro_id] + departamento_ids,
        ).fetchall()
    subdepartamento_ids = _ids(subdepartamentos)

    puntos_venta = []
    if _table_exists(conn, "puntos_venta"):
        pv_conditions = []
        pv_params = []
        if _column_exists(conn, "puntos_venta", "centro_id"):
            pv_conditions.append("pv.centro_id = ?")
            pv_params.append(centro_id)
        if departamento_ids and _column_exists(conn, "puntos_venta", "departamento_id"):
            pv_conditions.append(f"pv.departamento_id IN ({_in_clause(departamento_ids)})")
            pv_params.extend(departamento_ids)
        puntos_venta = conn.execute(
            f"""
            SELECT pv.*
            FROM puntos_venta pv
            WHERE pv.empresa_id = ? AND COALESCE(pv.activo, 1) = 1
              AND ({' OR '.join(pv_conditions) if pv_conditions else '1=0'})
            ORDER BY pv.nombre
            """,
            [empresa_id] + pv_params,
        ).fetchall()
    punto_venta_ids = _ids(puntos_venta)

    scope = _centro_scope_conditions(conn, centro_id, departamento_ids, subdepartamento_ids, punto_venta_ids)

    almacenes = []
    if _table_exists(conn, "almacenes"):
        cond, params = scope["almacenes"]
        almacenes = conn.execute(
            f"""
            SELECT a.*
            FROM almacenes a
            WHERE a.empresa_id = ? AND COALESCE(a.activo, 1) = 1 AND ({cond})
            ORDER BY a.nombre
            """,
            [empresa_id] + params,
        ).fetchall()

    empleados = []
    if _table_exists(conn, "empleados"):
        cond, params = scope["empleados"]
        empleados = _fetch_limited(
            conn,
            f"""
            SELECT e.*, d.nombre AS departamento_nombre, sd.nombre AS subdepartamento_nombre, pv.nombre AS punto_venta_nombre
            FROM empleados e
            LEFT JOIN departamentos d ON d.id = e.departamento_id
            LEFT JOIN subdepartamentos sd ON sd.id = e.subdepartamento_id
            LEFT JOIN puntos_venta pv ON pv.id = e.punto_venta_id
            WHERE e.empresa_id = ? AND COALESCE(e.activo, 1) = 1 AND ({cond})
            ORDER BY COALESCE(e.apellidos, ''), e.nombre
            """,
            [empresa_id] + params,
            12,
        )

    menus_diarios = []
    if _table_exists(conn, "menus_diarios"):
        cond, params = scope["menus"]
        menus_diarios = _fetch_limited(
            conn,
            f"""
            SELECT m.*, d.nombre AS departamento_nombre, pv.nombre AS punto_venta_nombre
            FROM menus_diarios m
            LEFT JOIN departamentos d ON d.id = m.departamento_id
            LEFT JOIN puntos_venta pv ON pv.id = m.punto_venta_id
            WHERE m.empresa_id = ? AND ({cond})
            ORDER BY COALESCE(m.fecha, '') DESC, m.id DESC
            """,
            [empresa_id] + params,
            8,
        )

    menus_fijos = []
    if _table_exists(conn, "menus"):
        cond, params = scope["menus"]
        menus_fijos = _fetch_limited(
            conn,
            f"""
            SELECT m.*, d.nombre AS departamento_nombre, pv.nombre AS punto_venta_nombre
            FROM menus m
            LEFT JOIN departamentos d ON d.id = m.departamento_id
            LEFT JOIN puntos_venta pv ON pv.id = m.punto_venta_id
            WHERE m.empresa_id = ? AND COALESCE(m.activo, 1) = 1 AND ({cond})
            ORDER BY COALESCE(m.fecha_inicio, '') DESC, m.id DESC
            """,
            [empresa_id] + params,
            8,
        )

    pedidos = []
    if _table_exists(conn, "pedidos"):
        cond, params = scope["pedidos"]
        pedidos = _fetch_limited(
            conn,
            f"""
            SELECT p.*
            FROM pedidos p
            WHERE p.empresa_id = ? AND ({cond})
            ORDER BY COALESCE(p.fecha_pedido, '') DESC, p.id DESC
            """,
            [empresa_id] + params,
            10,
        )

    appcc = []
    if departamento_ids and _table_exists(conn, "appcc_controles_plan"):
        codigo_conditions = [f"codigo LIKE ?"] * len(departamento_ids)
        codigo_params = [f"{dep_id}-%" for dep_id in departamento_ids]
        appcc = _fetch_limited(
            conn,
            f"""
            SELECT *, tipo_control AS nombre
            FROM appcc_controles_plan
            WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
              AND ({' OR '.join(codigo_conditions)})
            ORDER BY area, tipo_control
            """,
            [empresa_id] + codigo_params,
            12,
        )

    equipos = []
    if departamento_ids and _table_exists(conn, "equipos_mantenimiento"):
        equipos = _fetch_limited(
            conn,
            f"""
            SELECT *
            FROM equipos_mantenimiento
            WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
              AND departamento_id IN ({_in_clause(departamento_ids)})
            ORDER BY nombre
            """,
            [empresa_id] + departamento_ids,
            8,
        )

    kpis = {
        "departamentos": len(departamentos),
        "subdepartamentos": len(subdepartamentos),
        "puntos_venta": len(puntos_venta),
        "almacenes": len(almacenes),
        "empleados": len(empleados),
        "menus_diarios": len(menus_diarios),
        "menus_fijos": len(menus_fijos),
        "pedidos": len(pedidos),
        "appcc": len(appcc),
        "equipos": len(equipos),
    }
    tiene_actividad_operativa = any((
        puntos_venta,
        empleados,
        menus_diarios,
        menus_fijos,
        pedidos,
    ))

    return {
        "centro": centro,
        "departamentos": departamentos,
        "subdepartamentos": subdepartamentos,
        "puntos_venta": puntos_venta,
        "almacenes": almacenes,
        "empleados": empleados,
        "menus_diarios": menus_diarios,
        "menus_fijos": menus_fijos,
        "pedidos": pedidos,
        "appcc": appcc,
        "equipos": equipos,
        "kpis": kpis,
        "tiene_actividad_operativa": tiene_actividad_operativa,
    }


def _centro_card_counts(conn, empresa_id, centro):
    departamentos = conn.execute(
        "SELECT id FROM departamentos WHERE empresa_id = ? AND centro_id = ? AND COALESCE(activo, 1) = 1",
        (empresa_id, centro["id"]),
    ).fetchall() if _table_exists(conn, "departamentos") else []
    dep_ids = _ids(departamentos)
    dep_count = len(dep_ids)
    empleados_count = 0
    menus_count = 0
    pedidos_count = 0
    if dep_ids and _table_exists(conn, "empleados"):
        empleados_count = conn.execute(
            f"SELECT COUNT(*) AS total FROM empleados WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 AND departamento_id IN ({_in_clause(dep_ids)})",
            [empresa_id] + dep_ids,
        ).fetchone()["total"]
    if dep_ids and _table_exists(conn, "menus_diarios"):
        menus_count += conn.execute(
            f"SELECT COUNT(*) AS total FROM menus_diarios WHERE empresa_id = ? AND departamento_id IN ({_in_clause(dep_ids)})",
            [empresa_id] + dep_ids,
        ).fetchone()["total"]
    if dep_ids and _table_exists(conn, "menus"):
        menus_count += conn.execute(
            f"SELECT COUNT(*) AS total FROM menus WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 AND departamento_id IN ({_in_clause(dep_ids)})",
            [empresa_id] + dep_ids,
        ).fetchone()["total"]
    if dep_ids and _table_exists(conn, "pedidos"):
        pedido_conditions = []
        pedido_params = []
        if _column_exists(conn, "pedidos", "departamento_solicitante_id"):
            pedido_conditions.append(f"departamento_solicitante_id IN ({_in_clause(dep_ids)})")
            pedido_params.extend(dep_ids)
        if _column_exists(conn, "pedidos", "departamento_id"):
            pedido_conditions.append(f"departamento_id IN ({_in_clause(dep_ids)})")
            pedido_params.extend(dep_ids)
        if pedido_conditions:
            pedidos_count = conn.execute(
                f"SELECT COUNT(*) AS total FROM pedidos WHERE empresa_id = ? AND ({' OR '.join(pedido_conditions)})",
                [empresa_id] + pedido_params,
            ).fetchone()["total"]
    return {
        "centro": centro,
        "departamentos": dep_count,
        "empleados": empleados_count,
        "menus": menus_count,
        "pedidos": pedidos_count,
    }


@rrhh_bp.route("/centros/general")
@login_required
@_require_rrhh_global
def centro_empresa_general():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    empresa_id = _empresa_id()
    centros = conn.execute(
        "SELECT * FROM centros_trabajo WHERE empresa_id = ? ORDER BY COALESCE(activo, 1) DESC, nombre",
        (empresa_id,),
    ).fetchall()
    centro_cards = [_centro_card_counts(conn, empresa_id, centro) for centro in centros]
    sin_centro = {}
    if _table_exists(conn, "departamentos"):
        sin_centro["departamentos"] = conn.execute(
            "SELECT COUNT(*) AS total FROM departamentos WHERE empresa_id = ? AND centro_id IS NULL AND COALESCE(activo, 1) = 1",
            (empresa_id,),
        ).fetchone()["total"]
    else:
        sin_centro["departamentos"] = 0
    total_personal = conn.execute(
        "SELECT COUNT(*) AS total FROM empleados WHERE empresa_id = ? AND COALESCE(activo, 1) = 1",
        (empresa_id,),
    ).fetchone()["total"] if _table_exists(conn, "empleados") else 0
    total_centros = len([c for c in centros if c["activo"]])
    conn.close()
    return render_template(
        "rrhh/centro_panel.html",
        modo_general=True,
        centro_cards=centro_cards,
        sin_centro=sin_centro,
        total_personal=total_personal,
        total_centros=total_centros,
    )


@rrhh_bp.route("/centros/<int:id>")
@login_required
@_require_rrhh_global
def centro_panel(id):
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    empresa_id = _empresa_id()
    panel = _build_centro_panel(conn, empresa_id, id)
    conn.close()
    if not panel:
        flash("Centro no encontrado.", "warning")
        return redirect(url_for("rrhh.centros_trabajo"))
    session["centro_activo_id"] = id
    return render_template("rrhh/centro_panel.html", modo_general=False, panel=panel)


@rrhh_bp.route("/centros/general/seleccionar")
@login_required
@_require_rrhh_global
def seleccionar_empresa_general():
    session["centro_activo_id"] = None
    flash("Vista general de empresa seleccionada.", "success")
    return redirect(url_for("rrhh.centro_empresa_general"))


@rrhh_bp.route("/necesidades", methods=["GET", "POST"])
@rrhh_bp.route("/necesidades-operativas", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def necesidades_operativas():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        conn.execute(
            """
            INSERT INTO necesidades_operativas
            (centro_id, departamento_id, subdepartamento_id, puesto_id, dia_semana, personas_necesarias,
             observaciones, empresa_id, activo, hora_inicio, hora_fin, tipo_turno, horas_dia,
             plantilla_objetivo, cobertura_libranzas, etiqueta_operativa)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.form.get("centro_id") or None, request.form.get("departamento_id") or None,
                request.form.get("subdepartamento_id") or None, request.form.get("puesto_id") or None,
                _to_int(request.form.get("dia_semana"), 0), _to_int(request.form.get("personas_necesarias"), 1),
                request.form.get("observaciones"), empresa_id,
                request.form.get("hora_inicio") or None,
                request.form.get("hora_fin") or None,
                request.form.get("tipo_turno") or None,
                _to_float(request.form.get("horas_dia"), 0),
                _to_float(request.form.get("plantilla_objetivo"), 0),
                request.form.get("cobertura_libranzas") or None,
                request.form.get("etiqueta_operativa") or None,
            ),
        )
        conn.commit()
        flash("Necesidad operativa guardada.", "success")
        return redirect(url_for("rrhh.necesidades_operativas"))
    centro_id = centro_activo_id()
    centro_where = " AND n.centro_id = ?" if centro_id else ""
    centro_params = [centro_id] if centro_id else []
    necesidades = conn.execute(
        f"""
        SELECT n.*, ct.nombre centro_nombre, d.nombre departamento_nombre, sd.nombre subdepartamento_nombre,
               p.nombre puesto_nombre, p.categoria_id AS puesto_categoria_id
        FROM necesidades_operativas n
        LEFT JOIN centros_trabajo ct ON n.centro_id = ct.id
        LEFT JOIN departamentos d ON n.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON n.subdepartamento_id = sd.id
        LEFT JOIN puestos_operativos p ON n.puesto_id = p.id
        WHERE n.empresa_id = ? AND COALESCE(n.activo,1)=1{centro_where}
        ORDER BY COALESCE(ct.nombre,''), COALESCE(d.nombre,''), COALESCE(sd.nombre,''), n.dia_semana, p.nombre
        """,
        [empresa_id] + centro_params,
    ).fetchall()
    centros = _centros_select(conn)
    departamentos = _departamentos_select(conn)
    subdepartamentos = _subdepartamentos_select(conn)
    puestos = _puestos_select(conn)
    conn.close()
    return render_template("rrhh/necesidades_operativas.html", necesidades=necesidades, centros=centros, departamentos=departamentos, subdepartamentos=subdepartamentos, puestos=puestos, dias_semana=_DIAS_SEMANA)


def _slug_rrhh(txt):
    base = "".join(ch.lower() if ch.isalnum() else "-" for ch in (txt or "rol"))
    while "--" in base:
        base = base.replace("--", "-")
    return base.strip("-")[:48] or "rol"


def _grupo_plantilla_operativa(puesto_nombre):
    texto = (puesto_nombre or "").lower()
    if "camarera pisos planta" in texto:
        return "Camarera/o pisos habitaciones"
    if "limpieza zonas comunes" in texto:
        return "Limpieza zonas comunes"
    if "guardia noche pisos" in texto:
        return "Guardia noche pisos"
    if "gobernanta" in texto and "sub" not in texto:
        return "Gobernanta"
    if "subgobernanta" in texto:
        return "Subgobernanta"
    if "camarero buffet" in texto:
        return "Camarero/a buffet"
    if "jefe de rango buffet" in texto:
        return "Jefe/a de rango buffet"
    if "camarero bar" in texto:
        return "Camarero/a bar piscina"
    if "recepcion" in texto:
        return "Recepcionista 24h"
    if "animacion" in texto:
        return "Animacion"
    if "socorrista" in texto:
        return "Socorrista"
    if "tecnico mantenimiento" in texto:
        return "Tecnico mantenimiento"
    if "ayudante mantenimiento" in texto:
        return "Ayudante mantenimiento/piscina"
    if "segundo mantenimiento" in texto:
        return "Segundo mantenimiento"
    if "director" in texto and "sub" not in texto:
        return "Director/a"
    if "subdirector" in texto:
        return "Subdirector/a"
    if "jefe partida desayunos" in texto:
        return "Jefe/a partida desayunos"
    if "cocinero/ayudante desayunos" in texto:
        return "Cocinero/a ayudante desayunos"
    if "jefa partida cuarto frio" in texto:
        return "Jefe/a partida cuarto frio"
    if "ayudante cuarto frio" in texto:
        return "Ayudante cuarto frio"
    if "jefe partida caliente" in texto:
        return "Jefe/a partida caliente"
    if "cocinero caliente" in texto:
        return "Cocinero/a caliente"
    if "segundo cocina" in texto:
        return "Segundo/a cocina"
    if "bufetero" in texto:
        return "Bufetero/a cocina"
    if "tren de lavado" in texto:
        return "Tren de lavado"
    if "plonge" in texto or "lava perolas" in texto:
        return "Plonge / lava perolas"
    if "pinche" in texto:
        return "Pinche apoyo cocina"
    return puesto_nombre or "Puesto operativo"


def _departamento_esperado_por_grupo(grupo):
    texto = (grupo or "").lower()
    if any(x in texto for x in ("desayunos", "cocinero", "cocina", "partida", "cuarto frio", "bufetero", "plonge", "pinche", "tren de lavado")):
        return "cocina"
    if "animacion" in texto:
        return "animacion"
    if "socorrista" in texto:
        return "piscina"
    if any(x in texto for x in ("camarero/a buffet", "jefe/a de rango", "camarero/a bar")):
        return "bares"
    if any(x in texto for x in ("pisos", "gobernanta", "limpieza zonas", "guardia noche")):
        return "pisos"
    if "recepcion" in texto:
        return "recepcion"
    if "mantenimiento" in texto:
        return "mantenimiento"
    if "director" in texto:
        return "direccion"
    return ""


def _calcular_plantilla_operativa(conn, empresa_id, centro_id):
    rows = conn.execute(
        """
        SELECT n.*, p.nombre AS puesto_nombre, d.nombre AS departamento_nombre, sd.nombre AS subdepartamento_nombre
        FROM necesidades_operativas n
        LEFT JOIN puestos_operativos p ON p.id = n.puesto_id
        LEFT JOIN departamentos d ON d.id = n.departamento_id
        LEFT JOIN subdepartamentos sd ON sd.id = n.subdepartamento_id
        WHERE n.empresa_id = ? AND n.centro_id = ? AND COALESCE(n.activo, 1) = 1
        ORDER BY d.nombre, sd.nombre, p.nombre, n.dia_semana
        """,
        (empresa_id, centro_id),
    ).fetchall()
    grupos = {}
    for row in rows:
        grupo = _grupo_plantilla_operativa(row["puesto_nombre"])
        esperado = _departamento_esperado_por_grupo(grupo)
        departamento_real = (row["departamento_nombre"] or "").lower()
        aviso_ubicacion = ""
        if esperado == "cocina" and "cocina" not in departamento_real:
            aviso_ubicacion = "Revisar ubicacion: este grupo debe depender de cocina."
        elif esperado == "animacion" and "animacion" not in departamento_real:
            aviso_ubicacion = "Revisar ubicacion: animacion debe ser departamento propio."
        elif esperado == "piscina" and "piscina" not in departamento_real:
            aviso_ubicacion = "Revisar ubicacion: socorrista debe depender de piscina."
        data = grupos.setdefault(grupo, {
            "grupo": grupo,
            "departamento_id": row["departamento_id"],
            "subdepartamento_id": row["subdepartamento_id"],
            "departamento": row["departamento_nombre"] or "General",
            "subdepartamento": row["subdepartamento_nombre"] or "",
            "puestos": set(),
            "dias_personas": {d: 0 for d, _ in _DIAS_SEMANA},
            "dias_max_servicio": {d: 0 for d, _ in _DIAS_SEMANA},
            "horas_semana": 0.0,
            "necesidades": 0,
            "servicio_pool": grupo in {"Camarero/a buffet"},
            "aviso_ubicacion": aviso_ubicacion,
        })
        data["puestos"].add(row["puesto_nombre"] or grupo)
        dia = _to_int(row["dia_semana"], 0)
        personas = _to_float(row["personas_necesarias"], 0)
        horas = _to_float(row["horas_dia"], 0)
        if data["servicio_pool"]:
            data["dias_max_servicio"][dia] = max(data["dias_max_servicio"][dia], personas)
        else:
            data["dias_personas"][dia] += personas
        data["horas_semana"] += personas * horas
        data["necesidades"] += 1

    resultado = []
    for data in grupos.values():
        dias_personas = data["dias_max_servicio"] if data["servicio_pool"] else data["dias_personas"]
        personas_dia_base = max(dias_personas.values()) if dias_personas else 0
        jornadas_semana = sum(dias_personas.values())
        por_descansos = math.ceil(jornadas_semana / 5) if jornadas_semana else 0
        por_horas = math.ceil(data["horas_semana"] / 40) if data["horas_semana"] else 0
        plantilla_objetivo = max(por_descansos, por_horas, math.ceil(personas_dia_base))
        if data["grupo"] in {"Director/a", "Subdirector/a", "Segundo/a cocina"}:
            plantilla_objetivo = math.ceil(personas_dia_base)
        rotativos = max(0, plantilla_objetivo - math.ceil(personas_dia_base))
        cobertura = round((plantilla_objetivo * 5 / jornadas_semana) * 100, 1) if jornadas_semana else 0
        data.update({
            "puestos": sorted(data["puestos"]),
            "personas_dia_base": personas_dia_base,
            "jornadas_semana": jornadas_semana,
            "horas_semana": round(data["horas_semana"], 2),
            "por_descansos": por_descansos,
            "por_horas": por_horas,
            "plantilla_objetivo": plantilla_objetivo,
            "rotativos": rotativos,
            "cobertura": cobertura,
            "aviso": data["aviso_ubicacion"] or ("Pool por servicio: revisar turnos partidos para que una persona cubra varios servicios." if data["servicio_pool"] else ""),
        })
        resultado.append(data)
    return sorted(resultado, key=lambda x: (x["departamento"], x["subdepartamento"], x["grupo"]))


def _aplicar_cobertura_plantilla(conn, empresa_id, centro_id, grupos):
    for grupo in grupos:
        base = grupo["grupo"]
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM empleados
            WHERE empresa_id = ?
              AND centro_id = ?
              AND COALESCE(activo, 1) = 1
              AND origen_importacion = 'plantilla_operativa_hotel1'
              AND (rol = ? OR rol = ?)
            """,
            (empresa_id, centro_id, base, f"{base} rotativo"),
        ).fetchone()
        empleados = row["total"] if row else 0
        objetivo = int(grupo["plantilla_objetivo"] or 0)
        diferencia = empleados - objetivo
        grupo["empleados_simulados"] = empleados
        grupo["diferencia"] = diferencia
        if diferencia < 0:
            grupo["estado_cobertura"] = "deficit"
        elif diferencia > 0:
            grupo["estado_cobertura"] = "exceso"
        else:
            grupo["estado_cobertura"] = "ok"
    return grupos


_NOMBRES_SIMULADOS_H1 = [
    ("Juan", "Sanchez Camacho"),
    ("Fisa", "Sancho Jimenez"),
    ("Ana", "Martinez Roca"),
    ("Pedro", "Garcia Alonso"),
    ("Maria", "Torres Vidal"),
    ("Carlos", "Navarro Soler"),
    ("Laura", "Perez Medina"),
    ("Miguel", "Romero Gil"),
    ("Sara", "Cabrera Leon"),
    ("David", "Molina Cruz"),
    ("Nuria", "Santos Ortega"),
    ("Ruben", "Vega Martin"),
    ("Elena", "Castro Mora"),
    ("Javier", "Lopez Pons"),
    ("Marta", "Hernandez Rius"),
    ("Sergio", "Dominguez Serra"),
]

_NOMBRES_BASE_SIMULADOS_H1 = [
    "Juan", "Fisa", "Ana", "Pedro", "Maria", "Carlos", "Laura", "Miguel", "Sara", "David",
    "Nuria", "Ruben", "Elena", "Javier", "Marta", "Sergio", "Cristina", "Raul", "Paula", "Toni",
    "Isabel", "Pablo", "Noelia", "Adrian", "Silvia", "Oscar", "Beatriz", "Hector", "Rocio", "Daniel",
    "Alicia", "Marcos", "Carmen", "Victor", "Lorena", "Alba", "Ivan", "Teresa", "Clara", "Manuel",
]

_APELLIDOS_BASE_SIMULADOS_H1 = [
    "Sanchez", "Camacho", "Sancho", "Jimenez", "Martinez", "Roca", "Garcia", "Alonso",
    "Torres", "Vidal", "Navarro", "Soler", "Perez", "Medina", "Romero", "Gil",
    "Cabrera", "Leon", "Molina", "Cruz", "Santos", "Ortega", "Vega", "Martin",
    "Castro", "Mora", "Lopez", "Pons", "Hernandez", "Rius", "Dominguez", "Serra",
    "Fuentes", "Aguilar", "Morales", "Ramos", "Benitez", "Marin", "Suarez", "Prieto",
]


def _nombre_simulado_h1(indice):
    if indice < len(_NOMBRES_SIMULADOS_H1):
        return _NOMBRES_SIMULADOS_H1[indice]
    nombre = _NOMBRES_BASE_SIMULADOS_H1[indice % len(_NOMBRES_BASE_SIMULADOS_H1)]
    a1 = _APELLIDOS_BASE_SIMULADOS_H1[(indice * 2) % len(_APELLIDOS_BASE_SIMULADOS_H1)]
    a2 = _APELLIDOS_BASE_SIMULADOS_H1[(indice * 2 + 7) % len(_APELLIDOS_BASE_SIMULADOS_H1)]
    vuelta = indice // len(_NOMBRES_BASE_SIMULADOS_H1)
    if vuelta:
        a2 = f"{a2} {vuelta + 1}"
    return nombre, f"{a1} {a2}"


def _crear_empleados_simulados_plantilla(conn, empresa_id, centro_id, grupos):
    creados = 0
    existentes = 0
    indice_nombre = 0
    for grupo in grupos:
        slug = _slug_rrhh(grupo["grupo"])
        objetivo = int(grupo["plantilla_objetivo"])
        for i in range(1, objetivo + 1):
            codigo = f"H1-PLANTILLA-{slug}-{i:02d}"
            existe = conn.execute(
                "SELECT id FROM empleados WHERE empresa_id = ? AND codigo_importacion = ? LIMIT 1",
                (empresa_id, codigo),
            ).fetchone()
            rol = grupo["grupo"]
            if i > int(math.ceil(grupo["personas_dia_base"])):
                rol = f"{rol} rotativo"
            if existe:
                conn.execute(
                    """
                    UPDATE empleados
                    SET departamento_id = ?, subdepartamento_id = ?, rol = ?,
                        motivo_cambio = COALESCE(motivo_cambio, 'Normalizacion plantilla operativa por grupo')
                    WHERE id = ? AND empresa_id = ?
                    """,
                    (
                        grupo["departamento_id"],
                        grupo["subdepartamento_id"],
                        rol,
                        existe["id"],
                        empresa_id,
                    ),
                )
                existentes += 1
                continue
            nombre, apellidos = _nombre_simulado_h1(indice_nombre)
            indice_nombre += 1
            conn.execute(
                """
                INSERT INTO empleados
                (nombre, apellidos, departamento_id, subdepartamento_id, rol, activo, empresa_id,
                 fecha_alta, observaciones, tipo_empleado, origen_importacion, codigo_importacion, centro_id)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, 'simulacion', 'plantilla_operativa_hotel1', ?, ?)
                """,
                (
                    nombre,
                    apellidos,
                    grupo["departamento_id"],
                    grupo["subdepartamento_id"],
                    rol,
                    empresa_id,
                    date.today().isoformat(),
                    f"Empleado simulado para Hotel 1. Grupo: {grupo['grupo']}. Plantilla objetivo con descansos.",
                    codigo,
                    centro_id,
                ),
            )
            creados += 1
    return creados, existentes


@rrhh_bp.route("/plantilla-operativa", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def plantilla_operativa():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    empresa_id = _empresa_id()
    centro_id = centro_activo_id() or _to_int(request.args.get("centro_id"), 3) or 3
    grupos = _calcular_plantilla_operativa(conn, empresa_id, centro_id)
    grupos = _aplicar_cobertura_plantilla(conn, empresa_id, centro_id, grupos)
    if request.method == "POST":
        creados, existentes = _crear_empleados_simulados_plantilla(conn, empresa_id, centro_id, grupos)
        conn.commit()
        conn.close()
        flash(f"Plantilla simulada generada: {creados} empleados creados, {existentes} ya existian.", "success")
        return redirect(url_for("rrhh.plantilla_operativa", centro_id=centro_id))
    empleados_simulados = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM empleados
        WHERE empresa_id = ? AND centro_id = ? AND COALESCE(activo, 1)=1
          AND origen_importacion = 'plantilla_operativa_hotel1'
        """,
        (empresa_id, centro_id),
    ).fetchone()["total"]
    centro = conn.execute("SELECT id, nombre FROM centros_trabajo WHERE id=? AND empresa_id=?", (centro_id, empresa_id)).fetchone()
    conn.close()
    totales = {
        "grupos": len(grupos),
        "personas_dia": sum(g["personas_dia_base"] for g in grupos),
        "plantilla": sum(g["plantilla_objetivo"] for g in grupos),
        "rotativos": sum(g["rotativos"] for g in grupos),
        "horas_semana": sum(g["horas_semana"] for g in grupos),
        "empleados_simulados": empleados_simulados,
        "deficit": sum(abs(g["diferencia"]) for g in grupos if g["diferencia"] < 0),
        "exceso": sum(g["diferencia"] for g in grupos if g["diferencia"] > 0),
        "grupos_ok": sum(1 for g in grupos if g["estado_cobertura"] == "ok"),
    }
    return render_template("rrhh/plantilla_operativa.html", grupos=grupos, totales=totales, centro=centro)


def _insertar_o_actualizar_necesidad_copia(conn, origen, dia_destino, empresa_id, centro_id=None, departamento_id=None, subdepartamento_id=None, puesto_id=None, actualizar_existente=False):
    """
    Copia una necesidad operativa a otro día evitando duplicados.
    Si actualizar_existente=True, actualiza personas/observaciones de la necesidad existente.
    """
    centro_id = centro_id if centro_id is not None else origen["centro_id"]
    departamento_id = departamento_id if departamento_id is not None else origen["departamento_id"]
    subdepartamento_id = subdepartamento_id if subdepartamento_id is not None else origen["subdepartamento_id"]
    puesto_id = puesto_id if puesto_id is not None else origen["puesto_id"]

    existente = conn.execute(
        """
        SELECT id
        FROM necesidades_operativas
        WHERE empresa_id = ?
          AND COALESCE(activo,1)=1
          AND COALESCE(centro_id,0) = COALESCE(?,0)
          AND COALESCE(departamento_id,0) = COALESCE(?,0)
          AND COALESCE(subdepartamento_id,0) = COALESCE(?,0)
          AND COALESCE(puesto_id,0) = COALESCE(?,0)
          AND dia_semana = ?
          AND COALESCE(etiqueta_operativa,'') = COALESCE(?, '')
        LIMIT 1
        """,
        (empresa_id, centro_id, departamento_id, subdepartamento_id, puesto_id, dia_destino, origen["etiqueta_operativa"] if "etiqueta_operativa" in origen.keys() else None),
    ).fetchone()

    if existente:
        if actualizar_existente:
            conn.execute(
                """
                UPDATE necesidades_operativas
                SET personas_necesarias = ?,
                    observaciones = ?,
                    hora_inicio = ?,
                    hora_fin = ?,
                    tipo_turno = ?,
                    horas_dia = ?,
                    plantilla_objetivo = ?,
                    cobertura_libranzas = ?,
                    etiqueta_operativa = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND empresa_id = ?
                """,
                (
                    origen["personas_necesarias"],
                    origen["observaciones"],
                    origen["hora_inicio"] if "hora_inicio" in origen.keys() else None,
                    origen["hora_fin"] if "hora_fin" in origen.keys() else None,
                    origen["tipo_turno"] if "tipo_turno" in origen.keys() else None,
                    origen["horas_dia"] if "horas_dia" in origen.keys() else 0,
                    origen["plantilla_objetivo"] if "plantilla_objetivo" in origen.keys() else 0,
                    origen["cobertura_libranzas"] if "cobertura_libranzas" in origen.keys() else None,
                    origen["etiqueta_operativa"] if "etiqueta_operativa" in origen.keys() else None,
                    existente["id"],
                    empresa_id,
                ),
            )
            _registrar_historial(conn, "necesidades_operativas", existente["id"], "copiar_actualizando", "Necesidad copiada actualizando existente")
            return "actualizada"
        return "omitida"

    conn.execute(
        """
        INSERT INTO necesidades_operativas
        (centro_id, departamento_id, subdepartamento_id, puesto_id, dia_semana, personas_necesarias,
         observaciones, empresa_id, activo, hora_inicio, hora_fin, tipo_turno, horas_dia,
         plantilla_objetivo, cobertura_libranzas, etiqueta_operativa)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            centro_id,
            departamento_id,
            subdepartamento_id,
            puesto_id,
            dia_destino,
            origen["personas_necesarias"],
            origen["observaciones"],
            empresa_id,
            origen["hora_inicio"] if "hora_inicio" in origen.keys() else None,
            origen["hora_fin"] if "hora_fin" in origen.keys() else None,
            origen["tipo_turno"] if "tipo_turno" in origen.keys() else None,
            origen["horas_dia"] if "horas_dia" in origen.keys() else 0,
            origen["plantilla_objetivo"] if "plantilla_objetivo" in origen.keys() else 0,
            origen["cobertura_libranzas"] if "cobertura_libranzas" in origen.keys() else None,
            origen["etiqueta_operativa"] if "etiqueta_operativa" in origen.keys() else None,
        ),
    )
    nuevo_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    _registrar_historial(conn, "necesidades_operativas", nuevo_id, "copiar", f"Necesidad copiada desde ID {origen['id']}")
    return "creada"


@rrhh_bp.route("/necesidades/<int:id>/copiar", methods=["POST"])
@login_required
@_require_rrhh_global
def copiar_necesidad_operativa(id):
    conn = get_db()
    _ensure_editable_schema(conn)
    _ensure_cuadrantes_schema(conn)
    empresa_id = _empresa_id()

    origen = conn.execute(
        "SELECT * FROM necesidades_operativas WHERE id = ? AND empresa_id = ? AND COALESCE(activo,1)=1",
        (id, empresa_id),
    ).fetchone()

    if not origen:
        conn.close()
        flash("Necesidad origen no encontrada.", "warning")
        return redirect(url_for("rrhh.necesidades_operativas"))

    modo = request.form.get("modo") or "semana"
    actualizar_existente = request.form.get("actualizar_existente") == "1"

    if modo == "semana":
        dias_destino = [d for d, _nombre in _DIAS_SEMANA if d != origen["dia_semana"]]
    elif modo == "dias":
        dias_destino = [_to_int(d, -1) for d in request.form.getlist("dias_destino")]
        dias_destino = [d for d in dias_destino if 0 <= d <= 6 and d != origen["dia_semana"]]
    else:
        dia = _to_int(request.form.get("dia_destino"), -1)
        dias_destino = [dia] if 0 <= dia <= 6 and dia != origen["dia_semana"] else []

    if not dias_destino:
        conn.close()
        flash("No se ha seleccionado ningún día destino válido.", "warning")
        return redirect(url_for("rrhh.necesidades_operativas"))

    centro_id = request.form.get("centro_id")
    departamento_id = request.form.get("departamento_id")
    subdepartamento_id = request.form.get("subdepartamento_id")
    puesto_id = request.form.get("puesto_id")

    def normalizar_destino(valor):
        if valor in (None, "", "mismo"):
            return None
        return int(valor)

    centro_id = normalizar_destino(centro_id)
    departamento_id = normalizar_destino(departamento_id)
    subdepartamento_id = normalizar_destino(subdepartamento_id)
    puesto_id = normalizar_destino(puesto_id)

    resumen = {"creada": 0, "actualizada": 0, "omitida": 0}
    for dia_destino in dias_destino:
        resultado = _insertar_o_actualizar_necesidad_copia(
            conn, origen, dia_destino, empresa_id,
            centro_id=centro_id, departamento_id=departamento_id,
            subdepartamento_id=subdepartamento_id, puesto_id=puesto_id,
            actualizar_existente=actualizar_existente,
        )
        resumen[resultado] = resumen.get(resultado, 0) + 1

    conn.commit()
    conn.close()
    flash(
        f"Copia realizada: {resumen.get('creada',0)} creadas, {resumen.get('actualizada',0)} actualizadas, {resumen.get('omitida',0)} omitidas por duplicado.",
        "success",
    )
    return redirect(url_for("rrhh.necesidades_operativas"))


@rrhh_bp.route("/cuadrantes/real")
@login_required
def cuadrante_real():
    """
    Cuadrante real — filtros reparados sin recortar el módulo.

    Regla:
    - Sin filtros: se muestra toda la plantilla activa y todos sus turnos.
    - Con departamento/subdepartamento: el trabajador entra por ficha y se pintan sus turnos semanales,
      aunque algunos turnos antiguos no tengan metadatos completos.
    - Con centro/puesto: el trabajador entra por turno operativo o puesto operativo.
    - Si un turno antiguo no tiene centro, no se descarta cuando el resto del ámbito encaja.
    """
    conn = get_db()
    _ensure_cuadrantes_schema(conn)

    empresa_id = _empresa_id()
    inicio, fin = _rango_semana(request.args.get("fecha"))

    centro_id = request.args.get("centro_id") or centro_activo_id() or ""
    departamento_id = request.args.get("departamento_id") or ""
    subdepartamento_id = request.args.get("subdepartamento_id") or ""
    puesto_id = request.args.get("puesto_id") or ""

    # Convertir a int si es posible
    try:
        centro_id = int(centro_id) if centro_id else None
    except ValueError:
        centro_id = None
    try:
        departamento_id = int(departamento_id) if departamento_id else None
    except ValueError:
        departamento_id = None
    try:
        subdepartamento_id = int(subdepartamento_id) if subdepartamento_id else None
    except ValueError:
        subdepartamento_id = None
    try:
        puesto_id = int(puesto_id) if puesto_id else None
    except ValueError:
        puesto_id = None

    departamento_id, subdepartamento_id = _normalizar_departamento_subdepartamento(
        conn,
        empresa_id,
        departamento_id,
        subdepartamento_id,
    )
    filtro_activo = bool(centro_id or departamento_id or subdepartamento_id or puesto_id)

    # ============================================================
    # EMPLEADOS BASE POR FICHA
    # ============================================================
    filtros_base = ["e.empresa_id = ?", "COALESCE(e.activo,1)=1"]
    params_base = [empresa_id]

    if centro_id is not None:
        dep_ids_centro = ids_departamentos_centro(conn, empresa_id, centro_id)
        pv_ids_centro = ids_puntos_venta_centro(conn, empresa_id, centro_id)
        condiciones_centro = []
        if dep_ids_centro:
            condiciones_centro.append(f"e.departamento_id IN ({','.join('?' for _ in dep_ids_centro)})")
            params_base.extend(dep_ids_centro)
        if pv_ids_centro:
            condiciones_centro.append(f"e.punto_venta_id IN ({','.join('?' for _ in pv_ids_centro)})")
            params_base.extend(pv_ids_centro)
        filtros_base.append(f"({' OR '.join(condiciones_centro)})" if condiciones_centro else "1 = 0")

    if departamento_id is not None:
        filtros_base.append("e.departamento_id = ?")
        params_base.append(departamento_id)

    if subdepartamento_id is not None:
        filtros_base.append("e.subdepartamento_id = ?")
        params_base.append(subdepartamento_id)

    if not _usuario_es_global_rrhh() and _session_departamento_id():
        filtros_base.append("e.departamento_id = ?")
        params_base.append(_session_departamento_id())

    empleados_base = []
    # Si no hay filtro, entran todos por ficha.
    # Si se filtra sólo por departamento/subdepartamento, también entran por ficha.
    # Si se filtra por centro o puesto, sólo entran los turnos operativos.
    if puesto_id is None:
        empleados_base = conn.execute(
            f"""
            SELECT
                e.id,
                e.nombre,
                e.apellidos,
                e.departamento_id,
                e.subdepartamento_id,
                e.categoria_id,
                d.nombre AS departamento_nombre,
                sd.nombre AS subdepartamento_nombre,
                c.nombre AS categoria_nombre
            FROM empleados e
            LEFT JOIN departamentos d ON e.departamento_id = d.id
            LEFT JOIN subdepartamentos sd ON e.subdepartamento_id = sd.id
            LEFT JOIN categorias_laborales c ON e.categoria_id = c.id
            WHERE {' AND '.join(filtros_base)}
            """,
            params_base,
        ).fetchall()

    empleados_base_ids = {e["id"] for e in empleados_base}

    # ============================================================
    # EMPLEADOS QUE ENTRAN POR TURNO OPERATIVO
    # ============================================================
    empleados_turno = []
    if filtro_activo and not ((departamento_id is not None or subdepartamento_id is not None) and centro_id is None and puesto_id is None):
        filtros_turno = [
            "t.empresa_id = ?",
            "date(t.fecha) BETWEEN date(?) AND date(?)",
            "COALESCE(t.estado, 'planificado') NOT IN ('anulado','cancelado','borrado')",
        ]
        params_turno = [empresa_id, inicio.isoformat(), fin.isoformat()]

        if centro_id is not None:
            # Centro flexible: si el turno o el puesto tienen centro, debe coincidir.
            # Si ambos están vacíos, lo dejamos pasar para no perder turnos históricos sin centro_id.
            filtros_turno.append("(t.centro_id = ? OR p.centro_id = ?)")
            params_turno.extend([centro_id, centro_id])

        if departamento_id is not None:
            filtros_turno.append("COALESCE(t.departamento_id, p.departamento_id, e.departamento_id) = ?")
            params_turno.append(departamento_id)

        if subdepartamento_id is not None:
            filtros_turno.append("COALESCE(t.subdepartamento_id, p.subdepartamento_id, e.subdepartamento_id) = ?")
            params_turno.append(subdepartamento_id)

        if puesto_id is not None:
            filtros_turno.append("(t.puesto_id = ? OR p.id = ?)")
            params_turno.extend([puesto_id, puesto_id])

        if not _usuario_es_global_rrhh() and _session_departamento_id():
            filtros_turno.append("COALESCE(t.departamento_id, p.departamento_id, e.departamento_id) = ?")
            params_turno.append(_session_departamento_id())

        empleados_turno = conn.execute(
            f"""
            SELECT DISTINCT
                e.id,
                e.nombre,
                e.apellidos,
                e.departamento_id,
                e.subdepartamento_id,
                e.categoria_id,
                d.nombre AS departamento_nombre,
                sd.nombre AS subdepartamento_nombre,
                c.nombre AS categoria_nombre
            FROM turnos_rrhh t
            JOIN empleados e ON t.empleado_id = e.id
            LEFT JOIN puestos_operativos p ON t.puesto_id = p.id
            LEFT JOIN departamentos d ON e.departamento_id = d.id
            LEFT JOIN subdepartamentos sd ON e.subdepartamento_id = sd.id
            LEFT JOIN categorias_laborales c ON e.categoria_id = c.id
            WHERE {' AND '.join(filtros_turno)}
              AND e.empresa_id = ?
              AND COALESCE(e.activo,1)=1
            """,
            params_turno + [empresa_id],
        ).fetchall()

    empleados_map = {}
    for e in list(empleados_base) + list(empleados_turno):
        empleados_map[e["id"]] = e

    empleados = sorted(
        empleados_map.values(),
        key=lambda e: (
            (e["departamento_nombre"] or "").lower(),
            (e["subdepartamento_nombre"] or "").lower(),
            (e["nombre"] or "").lower(),
            (e["apellidos"] or "").lower(),
        ),
    )

    if centro_id is None and puesto_id is None:
        if subdepartamento_id is not None:
            empleados = [e for e in empleados if e["subdepartamento_id"] == subdepartamento_id]
        elif departamento_id is not None:
            empleados = [e for e in empleados if e["departamento_id"] == departamento_id]

    empleado_ids = [e["id"] for e in empleados]

    # ============================================================
    # TURNOS DE LA SEMANA
    # ============================================================
    turnos = []
    if empleado_ids:
        placeholders = ",".join(["?"] * len(empleado_ids))
        turnos_raw = conn.execute(
            f"""
            SELECT
                t.*,
                TRIM(e.nombre || ' ' || COALESCE(e.apellidos,'')) AS empleado_nombre,

                e.departamento_id AS empleado_departamento_id,
                e.subdepartamento_id AS empleado_subdepartamento_id,
                e.categoria_id AS empleado_categoria_id,

                COALESCE(t.departamento_id, p.departamento_id, e.departamento_id) AS departamento_efectivo_id,
                COALESCE(t.subdepartamento_id, p.subdepartamento_id, e.subdepartamento_id) AS subdepartamento_efectivo_id,

                CASE WHEN t.centro_id IS NOT NULL THEN ct.nombre ELSE NULL END AS centro_nombre,
                CASE WHEN t.departamento_id IS NOT NULL THEN d_t.nombre ELSE NULL END AS departamento_nombre,
                CASE WHEN t.subdepartamento_id IS NOT NULL THEN sd_t.nombre ELSE NULL END AS subdepartamento_nombre,

                p.nombre AS puesto_nombre,
                p.categoria_id AS puesto_categoria_id,
                p.centro_id AS puesto_centro_id,
                p.departamento_id AS puesto_departamento_id,
                p.subdepartamento_id AS puesto_subdepartamento_id
            FROM turnos_rrhh t
            JOIN empleados e ON t.empleado_id = e.id
            LEFT JOIN puestos_operativos p ON t.puesto_id = p.id
            LEFT JOIN centros_trabajo ct ON COALESCE(t.centro_id, p.centro_id) = ct.id
            LEFT JOIN departamentos d_t ON t.departamento_id = d_t.id
            LEFT JOIN departamentos d_p ON p.departamento_id = d_p.id
            LEFT JOIN departamentos d_e ON e.departamento_id = d_e.id
            LEFT JOIN subdepartamentos sd_t ON t.subdepartamento_id = sd_t.id
            LEFT JOIN subdepartamentos sd_p ON p.subdepartamento_id = sd_p.id
            LEFT JOIN subdepartamentos sd_e ON e.subdepartamento_id = sd_e.id
            WHERE t.empresa_id = ?
              AND t.empleado_id IN ({placeholders})
              AND date(t.fecha) BETWEEN date(?) AND date(?)
              AND COALESCE(t.estado, 'planificado') NOT IN ('anulado', 'cancelado', 'borrado')
            ORDER BY date(t.fecha), e.nombre, t.hora_inicio
            """,
            [empresa_id] + empleado_ids + [inicio.isoformat(), fin.isoformat()],
        ).fetchall()

        def _eq(valor, filtro):
            return str(valor or "") == str(filtro or "")

        def _turno_tiene_centro(turno):
            return bool(turno["centro_id"] or turno["puesto_centro_id"])

        def _turno_tiene_subdepartamento(turno):
            return bool(turno["subdepartamento_id"] or turno["puesto_subdepartamento_id"])

        def _turno_encaja_operativo(turno):
            if centro_id:
                if _turno_tiene_centro(turno):
                    if not (_eq(turno["centro_id"], centro_id) or _eq(turno["puesto_centro_id"], centro_id)):
                        return False
                # Si el turno no tiene centro en histórico, no lo descartamos aquí.
                # Lo decidirán departamento/subdepartamento/puesto/ficha.

            if departamento_id:
                if turno["departamento_id"] is not None or turno["puesto_departamento_id"] is not None:
                    if not (_eq(turno["departamento_id"], departamento_id) or _eq(turno["puesto_departamento_id"], departamento_id)):
                        return False
                elif not _eq(turno["empleado_departamento_id"], departamento_id):
                    return False

            if subdepartamento_id:
                if _turno_tiene_subdepartamento(turno):
                    if not (_eq(turno["subdepartamento_id"], subdepartamento_id) or _eq(turno["puesto_subdepartamento_id"], subdepartamento_id)):
                        return False
                elif not _eq(turno["empleado_subdepartamento_id"], subdepartamento_id):
                    return False

            if puesto_id and not (_eq(turno["puesto_id"], puesto_id)):
                return False

            return True

        for t in turnos_raw:
            if not filtro_activo:
                turnos.append(t)
                continue

            # Departamento/subdepartamento por ficha: no vaciar horarios por metadatos incompletos,
            # pero sí respetar el filtro cuando el turno apunta a otro ámbito distinto.
            if not centro_id and not puesto_id and t["empleado_id"] in empleados_base_ids:
                if _turno_encaja_operativo(t):
                    turnos.append(t)
                continue

            # Centro + departamento/subdepartamento: si el empleado entra por ficha y el turno no tiene centro,
            # se permite pintar el turno para no dejar celdas vacías.
            if centro_id and t["empleado_id"] in empleados_base_ids and not _turno_tiene_centro(t):
                if _turno_encaja_operativo(t):
                    turnos.append(t)
                continue

            if _turno_encaja_operativo(t):
                turnos.append(t)

    # ============================================================
    # VACACIONES / AUSENCIAS
    # ============================================================
    vacaciones = []
    if empleado_ids:
        placeholders = ",".join(["?"] * len(empleado_ids))
        vacaciones = conn.execute(
            f"""
            SELECT
                v.*,
                TRIM(e.nombre || ' ' || COALESCE(e.apellidos,'')) AS empleado_nombre
            FROM vacaciones_rrhh v
            JOIN empleados e ON v.empleado_id = e.id
            WHERE v.empresa_id = ?
              AND v.empleado_id IN ({placeholders})
              AND date(v.fecha_inicio) <= date(?)
              AND date(v.fecha_fin) >= date(?)
              AND v.estado IN ('solicitada','aprobada')
            ORDER BY v.fecha_inicio
            """,
            [empresa_id] + empleado_ids + [fin.isoformat(), inicio.isoformat()],
        ).fetchall()

    # ============================================================
    # NECESIDADES OPERATIVAS
    # ============================================================
    necesidades_params = [empresa_id]
    necesidades_where = ["n.empresa_id = ?", "COALESCE(n.activo,1)=1"]

    if centro_id:
        necesidades_where.append("n.centro_id = ?")
        necesidades_params.append(centro_id)
    if departamento_id:
        necesidades_where.append("n.departamento_id = ?")
        necesidades_params.append(departamento_id)
    if subdepartamento_id:
        necesidades_where.append("n.subdepartamento_id = ?")
        necesidades_params.append(subdepartamento_id)
    if puesto_id:
        necesidades_where.append("n.puesto_id = ?")
        necesidades_params.append(puesto_id)

    necesidades = conn.execute(
        f"""
        SELECT
            n.*,
            ct.nombre AS centro_nombre,
            d.nombre AS departamento_nombre,
            sd.nombre AS subdepartamento_nombre,
            p.nombre AS puesto_nombre,
            p.categoria_id AS puesto_categoria_id
        FROM necesidades_operativas n
        LEFT JOIN centros_trabajo ct ON n.centro_id = ct.id
        LEFT JOIN departamentos d ON n.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON n.subdepartamento_id = sd.id
        LEFT JOIN puestos_operativos p ON n.puesto_id = p.id
        WHERE {' AND '.join(necesidades_where)}
        ORDER BY n.dia_semana, COALESCE(ct.nombre,''), COALESCE(d.nombre,''), COALESCE(sd.nombre,''), COALESCE(p.nombre,'')
        """,
        necesidades_params,
    ).fetchall()

    # ============================================================
    # LISTAS PARA FILTROS
    # ============================================================
    centros = _centros_select(conn)

    departamentos = _departamentos_select(conn)
    dep_ids_filtros = [d["id"] for d in departamentos]
    if centro_activo_id() and dep_ids_filtros:
        subdepartamentos = conn.execute(
            f"""
            SELECT sd.id, sd.nombre, sd.departamento_id, d.nombre AS departamento_nombre
            FROM subdepartamentos sd
            LEFT JOIN departamentos d ON sd.departamento_id = d.id
            WHERE sd.empresa_id = ? AND sd.departamento_id IN ({','.join('?' for _ in dep_ids_filtros)})
            ORDER BY d.nombre, sd.nombre
            """,
            [empresa_id] + dep_ids_filtros,
        ).fetchall()
    elif centro_activo_id():
        subdepartamentos = []
    else:
        subdepartamentos = conn.execute(
            """
        SELECT sd.id, sd.nombre, sd.departamento_id, d.nombre AS departamento_nombre
        FROM subdepartamentos sd
        LEFT JOIN departamentos d ON sd.departamento_id = d.id
        WHERE sd.empresa_id = ?
        ORDER BY d.nombre, sd.nombre
            """,
            (empresa_id,),
        ).fetchall()

    centro_filtro_id = centro_activo_id()
    puesto_condiciones = ["empresa_id = ?", "COALESCE(activo,1)=1"]
    puesto_params = [empresa_id]
    if centro_filtro_id:
        condiciones_centro = ["centro_id = ?"]
        puesto_params.append(centro_filtro_id)
        if dep_ids_filtros:
            condiciones_centro.append(f"departamento_id IN ({','.join('?' for _ in dep_ids_filtros)})")
            puesto_params.extend(dep_ids_filtros)
        puesto_condiciones.append(f"({' OR '.join(condiciones_centro)})")
    puestos = conn.execute(
        f"""
        SELECT id, nombre, centro_id, departamento_id, subdepartamento_id, categoria_id
        FROM puestos_operativos
        WHERE {' AND '.join(puesto_condiciones)}
        ORDER BY nombre
        """,
        puesto_params,
    ).fetchall()

    conn.close()

    dias = [inicio + timedelta(days=i) for i in range(7)]

    # ============================================================
    # TURNOS POR EMPLEADO / DÍA
    # ============================================================
    turnos_por_empleado_dia = {}
    for t in turnos:
        turno = dict(t)
        fecha_key = str(turno.get("fecha") or "").split(" ")[0].split("T")[0]
        turno["fecha"] = fecha_key
        turnos_por_empleado_dia.setdefault((turno["empleado_id"], fecha_key), []).append(turno)

    # ============================================================
    # VACACIONES POR EMPLEADO / DÍA
    # ============================================================
    vacaciones_por_empleado_dia = {}
    for v in vacaciones:
        try:
            vi = date.fromisoformat(v["fecha_inicio"])
            vf = date.fromisoformat(v["fecha_fin"])
        except Exception:
            continue
        for dia in dias:
            if vi <= dia <= vf:
                vacaciones_por_empleado_dia.setdefault((v["empleado_id"], dia.isoformat()), []).append(dict(v))

    # ============================================================
    # COBERTURA REAL — CONTEO POR NECESIDAD INDIVIDUAL
    # ============================================================
    cobertura = {}
    asignados = {}
    asignados_necesidad = {}

    turnos_contables = []
    for t in turnos:
        td = dict(t)
        estado = (td.get("estado") or "planificado").strip().lower()
        tipo = (td.get("tipo") or "real").strip().lower()

        if estado in {"anulado", "cancelado", "borrado"}:
            continue
        if tipo in {"libre", "descanso", "dia_libre", "día libre"}:
            continue

        try:
            td["_weekday"] = date.fromisoformat(str(td["fecha"]).split(" ")[0].split("T")[0]).weekday()
        except Exception:
            continue

        td["_centro_id"] = td.get("centro_id") or td.get("puesto_centro_id") or 0
        td["_departamento_id"] = td.get("departamento_efectivo_id") or td.get("departamento_id") or td.get("puesto_departamento_id") or td.get("empleado_departamento_id") or 0
        td["_subdepartamento_id"] = td.get("subdepartamento_efectivo_id") or td.get("subdepartamento_id") or td.get("puesto_subdepartamento_id") or td.get("empleado_subdepartamento_id") or 0
        td["_puesto_id"] = td.get("puesto_id") or 0
        td["_empleado_categoria_id"] = td.get("empleado_categoria_id") or 0
        turnos_contables.append(td)

    def _mismo(a, b):
        return str(a or 0) == str(b or 0)

    for n in necesidades:
        nd = dict(n)
        necesidad_id = nd.get("id")
        dia = nd.get("dia_semana")
        centro = nd.get("centro_id") or 0
        depto = nd.get("departamento_id") or 0
        subdepto = nd.get("subdepartamento_id") or 0
        puesto = nd.get("puesto_id") or 0
        puesto_categoria = nd.get("puesto_categoria_id") or 0
        personas_necesarias = nd.get("personas_necesarias") or 0

        key = (dia, puesto, depto, subdepto, centro, necesidad_id)
        key_legacy = (dia, puesto, depto, subdepto)
        cobertura[key] = personas_necesarias
        cobertura[key_legacy] = personas_necesarias

        empleados_contados = set()

        for t in turnos_contables:
            if t["_weekday"] != dia:
                continue

            empleado_turno = t.get("empleado_id")
            if empleado_turno in empleados_contados:
                continue

            if centro and t["_centro_id"] and not _mismo(t["_centro_id"], centro):
                continue
            if subdepto:
                if not _mismo(t["_subdepartamento_id"], subdepto):
                    continue
            elif depto:
                if not _mismo(t["_departamento_id"], depto):
                    continue

            if puesto:
                if _mismo(t["_puesto_id"], puesto):
                    empleados_contados.add(empleado_turno)
                elif not t["_puesto_id"] and puesto_categoria and _mismo(t["_empleado_categoria_id"], puesto_categoria):
                    empleados_contados.add(empleado_turno)
                else:
                    continue
            else:
                empleados_contados.add(empleado_turno)

        total = len(empleados_contados)
        asignados_necesidad[necesidad_id] = total
        asignados[key] = total
        asignados[key_legacy] = total

    return render_template(
        "rrhh/cuadrante_real.html",
        empleados=empleados,
        dias=dias,
        inicio=inicio,
        fin=fin,
        centros=centros,
        departamentos=departamentos,
        subdepartamentos=subdepartamentos,
        puestos=puestos,
        turnos_por_empleado_dia=turnos_por_empleado_dia,
        vacaciones_por_empleado_dia=vacaciones_por_empleado_dia,
        necesidades=necesidades,
        cobertura=cobertura,
        asignados=asignados,
        asignados_necesidad=asignados_necesidad,
        centro_id=centro_id,
        departamento_id=departamento_id,
        subdepartamento_id=subdepartamento_id,
        puesto_id=puesto_id,
        puede_editar=_usuario_es_global_rrhh(),
        timedelta=timedelta,
    )


@rrhh_bp.route("/cuadrantes/real/asignar", methods=["POST"])
@login_required
@_require_cuadrantes
def asignar_cuadrante_real():
    """
    Asigna desde la planilla real y vuelve SIEMPRE a /rrhh/cuadrantes/real.
    Permite tipo libre/descanso sin horas.
    """
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    empresa_id = _empresa_id()

    fecha = request.form.get("fecha")
    empleado_id = request.form.get("empleado_id")
    tipo = (request.form.get("tipo") or "").strip().lower()
    marcar_libre = request.form.get("marcar_libre") == "1" or tipo in {"libre", "descanso", "libranza"}
    hora_inicio = request.form.get("hora_inicio")
    hora_fin = request.form.get("hora_fin")

    if not empleado_id or not fecha:
        conn.close()
        flash("Empleado y fecha son obligatorios.", "warning")
        return redirect(url_for("rrhh.cuadrante_real", fecha=fecha or date.today().isoformat()))

    if marcar_libre:
        tipo = "libre"
        hora_inicio = None
        hora_fin = None
        estado = "planificado"
        observaciones = request.form.get("observaciones") or "LIBRE / DESCANSO"
    else:
        if not hora_inicio or not hora_fin:
            conn.close()
            flash("Hora inicio y hora fin son obligatorias salvo que marques día libre.", "warning")
            return _redirect_cuadrante_real_con_filtros(fecha)
        tipo = request.form.get("tipo") or "real"
        estado = request.form.get("estado") or "planificado"
        observaciones = request.form.get("observaciones")

    conn.execute(
        """
        INSERT INTO turnos_rrhh
        (empleado_id, fecha, hora_inicio, hora_fin, tipo, centro_id, departamento_id, subdepartamento_id, puesto_id, estado, origen, observaciones, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            empleado_id, fecha, hora_inicio, hora_fin, tipo,
            request.form.get("centro_id") or None,
            request.form.get("departamento_id") or None,
            request.form.get("subdepartamento_id") or None,
            request.form.get("puesto_id") or None,
            estado, "cuadrante_real", observaciones, empresa_id,
        ),
    )
    conn.commit()
    conn.close()

    flash("Día libre registrado." if marcar_libre else "Turno asignado al cuadrante real.", "success")
    return _redirect_cuadrante_real_con_filtros(fecha)


# ============================================================
# FICHAJES
# ============================================================

@rrhh_bp.route("/empleado/aviso-jornada/aceptar", methods=["POST"])
@login_required
def aceptar_aviso_jornada_empleado():
    conn = get_db()
    ensure_rrhh_schema(conn)
    empleado = _cargar_empleado_actual(conn)
    if not empleado:
        conn.close()
        flash("Tu usuario no esta vinculado a ninguna ficha de empleado activa.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))
    if request.form.get("acepto") != "1":
        conn.close()
        flash("Debes marcar la aceptacion del aviso.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))
    texto = _texto_aviso_jornada()
    firma = hashlib.sha256(
        f"{_empresa_id()}|{empleado['id']}|{session.get('user_id')}|{AVISO_JORNADA_VERSION}|{texto}".encode("utf-8")
    ).hexdigest()
    if not _acuse_jornada_actual(conn, empleado["id"]):
        conn.execute(
            """
            INSERT INTO rrhh_portal_aceptaciones
            (empleado_id, usuario_id, tipo, version, texto, hash_aceptacion, ip, ip_hash, datos_minimizados, user_agent, empresa_id)
            VALUES (?, ?, 'registro_jornada', ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                empleado["id"],
                session.get("user_id"),
                AVISO_JORNADA_VERSION,
                texto,
                firma,
                "",
                _ip_cliente_hash(),
                str(request.user_agent)[:500],
                _empresa_id(),
            ),
        )
    _registrar_acceso_portal(conn, empleado["id"], "aceptar_aviso_jornada", AVISO_JORNADA_VERSION)
    conn.commit()
    conn.close()
    flash("Aviso de registro de jornada aceptado.", "success")
    return redirect(url_for("rrhh.portal_empleado"))


@rrhh_bp.route("/empleado")
@login_required
def portal_empleado():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    _ensure_control_tiempo_schema(conn)
    empresa_id = _empresa_id()
    empleado = _cargar_empleado_actual(conn)
    if not empleado:
        conn.close()
        flash("Tu usuario no esta vinculado a ninguna ficha de empleado activa.", "warning")
        if _usuario_es_global_rrhh():
            return redirect(url_for("rrhh.lista_empleados"))
        return render_template("rrhh/portal_empleado.html", empleado=None)

    inicio, fin = _rango_semana(request.args.get("fecha"))
    hoy = date.today().isoformat()

    fichajes_hoy = conn.execute(
        """
        SELECT *
        FROM fichajes
        WHERE empresa_id = ? AND empleado_id = ? AND date(fecha_hora) = date(?)
          AND COALESCE(estado, 'activo') != 'anulado'
        ORDER BY datetime(fecha_hora) ASC, id ASC
        """,
        (empresa_id, empleado["id"], hoy),
    ).fetchall()
    ultimo_hoy = fichajes_hoy[-1] if fichajes_hoy else None
    estado_jornada, acciones_permitidas = _estado_jornada_desde_ultimo(ultimo_hoy["tipo"] if ultimo_hoy else None)

    fichajes_recientes = conn.execute(
        """
        SELECT *
        FROM fichajes
        WHERE empresa_id = ? AND empleado_id = ?
          AND COALESCE(estado, 'activo') != 'anulado'
        ORDER BY datetime(fecha_hora) DESC, id DESC
        LIMIT 60
        """,
        (empresa_id, empleado["id"]),
    ).fetchall()

    nominas = conn.execute(
        """
        SELECT *
        FROM nominas
        WHERE empresa_id = ? AND empleado_id = ? AND COALESCE(visible_empleado, 1) = 1
        ORDER BY anio DESC, mes DESC, id DESC
        LIMIT 24
        """,
        (empresa_id, empleado["id"]),
    ).fetchall()

    contratos = conn.execute(
        """
        SELECT *
        FROM contratos
        WHERE empresa_id = ? AND empleado_id = ? AND COALESCE(estado, 'activo') != 'anulado'
        ORDER BY date(COALESCE(fecha_inicio, fecha_creacion, '1900-01-01')) DESC, id DESC
        LIMIT 20
        """,
        (empresa_id, empleado["id"]),
    ).fetchall()

    documentos = conn.execute(
        """
        SELECT *
        FROM documentos_rrhh
        WHERE empresa_id = ? AND empleado_id = ? AND COALESCE(visible_empleado, 1) = 1
        ORDER BY date(COALESCE(fecha_documento, fecha_creacion, '1900-01-01')) DESC, id DESC
        LIMIT 50
        """,
        (empresa_id, empleado["id"]),
    ).fetchall()

    solicitudes = conn.execute(
        """
        SELECT v.*, doc.nombre AS documento_nombre, doc.archivo AS documento_archivo
        FROM vacaciones_rrhh v
        LEFT JOIN documentos_rrhh doc
               ON v.documento_id = doc.id
              AND doc.empleado_id = v.empleado_id
              AND doc.empresa_id = v.empresa_id
        WHERE v.empresa_id = ?
          AND v.empleado_id = ?
        ORDER BY date(COALESCE(v.fecha_inicio, v.fecha_creacion, '1900-01-01')) DESC, v.id DESC
        LIMIT 40
        """,
        (empresa_id, empleado["id"]),
    ).fetchall()

    correcciones_fichaje = conn.execute(
        """
        SELECT *
        FROM solicitudes_correccion_fichaje
        WHERE empresa_id = ?
          AND empleado_id = ?
        ORDER BY datetime(fecha_solicitud) DESC, id DESC
        LIMIT 30
        """,
        (empresa_id, empleado["id"]),
    ).fetchall()

    turnos = conn.execute(
        """
        SELECT t.*, p.nombre AS puesto_nombre
        FROM turnos_rrhh t
        LEFT JOIN puestos_operativos p ON t.puesto_id = p.id
        WHERE t.empresa_id = ?
          AND t.empleado_id = ?
          AND date(t.fecha) BETWEEN date(?) AND date(?)
          AND COALESCE(t.estado, 'planificado') NOT IN ('anulado', 'cancelado', 'borrado')
        ORDER BY date(t.fecha), COALESCE(t.hora_inicio, '99:99'), t.id
        """,
        (empresa_id, empleado["id"], inicio.isoformat(), fin.isoformat()),
    ).fetchall()

    ausencias = conn.execute(
        """
        SELECT *
        FROM vacaciones_rrhh
        WHERE empresa_id = ?
          AND empleado_id = ?
          AND date(fecha_inicio) <= date(?)
          AND date(fecha_fin) >= date(?)
          AND COALESCE(estado, 'solicitada') IN ('solicitada', 'aprobada')
        ORDER BY date(fecha_inicio), id
        """,
        (empresa_id, empleado["id"], fin.isoformat(), inicio.isoformat()),
    ).fetchall()
    saldo_vacaciones = _saldo_vacaciones_empleado(conn, empleado["id"], date.today().year)
    jornada_anio = _to_int(request.args.get("jornada_anio"), date.today().year)
    jornada_mes = max(1, min(_to_int(request.args.get("jornada_mes"), date.today().month), 12))
    jornada_mensual = _resumen_jornada_periodo(conn, jornada_anio, jornada_mes, empleado["id"])
    jornada_resumen = jornada_mensual["resumen"][0] if jornada_mensual["resumen"] else None
    acuse_jornada = _acuse_jornada_actual(conn, empleado["id"])
    aviso_legal_jornada = {
        "version": AVISO_JORNADA_VERSION,
        "texto": _texto_aviso_jornada(),
        "retencion_anios": RETENCION_REGISTRO_JORNADA_ANIOS,
    }
    avisos_portal = _avisos_portal_empleado(conn, empleado["id"], acuse_jornada)
    _registrar_acceso_portal(conn, empleado["id"], "ver_portal", f"semana={inicio.isoformat()} jornada={jornada_anio}-{jornada_mes:02d}")
    conn.commit()
    conn.close()

    dias = [inicio + timedelta(days=i) for i in range(7)]
    turnos_por_dia = {}
    for turno in turnos:
        turnos_por_dia.setdefault(_iso(turno["fecha"]), []).append(turno)

    ausencias_por_dia = {}
    for ausencia in ausencias:
        try:
            desde = date.fromisoformat(_iso(ausencia["fecha_inicio"]))
            hasta = date.fromisoformat(_iso(ausencia["fecha_fin"]))
        except Exception:
            continue
        for dia in dias:
            if desde <= dia <= hasta:
                ausencias_por_dia.setdefault(dia.isoformat(), []).append(ausencia)

    return render_template(
        "rrhh/portal_empleado.html",
        empleado=empleado,
        hoy=hoy,
        inicio=inicio,
        fin=fin,
        dias=dias,
        estado_jornada=estado_jornada,
        acciones_permitidas=acciones_permitidas,
        fichajes_hoy=fichajes_hoy,
        fichajes_recientes=fichajes_recientes,
        nominas=nominas,
        contratos=contratos,
        documentos=documentos,
        solicitudes=solicitudes,
        correcciones_fichaje=correcciones_fichaje,
        saldo_vacaciones=saldo_vacaciones,
        jornada_mensual=jornada_mensual,
        jornada_resumen=jornada_resumen,
        jornada_anio=jornada_anio,
        jornada_mes=jornada_mes,
        acuse_jornada=acuse_jornada,
        aviso_legal_jornada=aviso_legal_jornada,
        avisos_portal=avisos_portal,
        tipos_ausencia=_tipos_ausencia_select(),
        turnos_por_dia=turnos_por_dia,
        ausencias_por_dia=ausencias_por_dia,
        timedelta=timedelta,
    )


@rrhh_bp.route("/empleado/fichar", methods=["POST"])
@login_required
def fichar_empleado():
    conn = get_db()
    _ensure_control_tiempo_schema(conn)
    empleado = _cargar_empleado_actual(conn)
    if not empleado:
        conn.close()
        flash("Tu usuario no esta vinculado a ninguna ficha de empleado activa.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))

    tipo = request.form.get("tipo")
    if tipo not in {"entrada", "salida", "pausa", "reanudacion"}:
        tipo = "entrada"

    hoy = date.today().isoformat()
    cierre = _periodo_cerrado_para_fecha(conn, hoy)
    if cierre:
        conn.close()
        flash("El periodo de jornada de esa fecha esta cerrado. Solicita revision a RRHH.", "warning")
        return redirect(url_for("rrhh.portal_empleado", fecha=request.form.get("fecha") or hoy))

    ultimo = conn.execute(
        """
        SELECT tipo
        FROM fichajes
        WHERE empresa_id = ? AND empleado_id = ? AND date(fecha_hora) = date(?)
          AND COALESCE(estado, 'activo') != 'anulado'
        ORDER BY datetime(fecha_hora) DESC, id DESC
        LIMIT 1
        """,
        (_empresa_id(), empleado["id"], hoy),
    ).fetchone()
    _estado, acciones_permitidas = _estado_jornada_desde_ultimo(ultimo["tipo"] if ultimo else None)
    if tipo not in acciones_permitidas:
        conn.close()
        flash("Ese fichaje no encaja con el ultimo estado registrado.", "warning")
        return redirect(url_for("rrhh.portal_empleado", fecha=request.form.get("fecha") or hoy))

    _insertar_fichaje(conn, empleado["id"], tipo, request.form.get("observaciones"), "portal_empleado")
    _registrar_acceso_portal(conn, empleado["id"], "fichaje_portal", tipo)
    conn.commit()
    conn.close()
    flash(f"Fichaje de {tipo} registrado.", "success")
    return redirect(url_for("rrhh.portal_empleado", fecha=request.form.get("fecha") or hoy))


@rrhh_bp.route("/empleado/pin", methods=["POST"])
@login_required
def cambiar_pin_fichaje_empleado():
    conn = get_db()
    ensure_rrhh_schema(conn)
    empleado = _cargar_empleado_actual(conn)
    if not empleado:
        conn.close()
        flash("Tu usuario no esta vinculado a ninguna ficha de empleado activa.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))

    pin_actual = (request.form.get("pin_actual") or "").strip()
    pin_nuevo = (request.form.get("pin_nuevo") or "").strip()
    pin_repetir = (request.form.get("pin_repetir") or "").strip()
    if not pin_nuevo or pin_nuevo != pin_repetir:
        conn.close()
        flash("El PIN nuevo no coincide.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))
    if not pin_nuevo.isdigit() or not 4 <= len(pin_nuevo) <= 8:
        conn.close()
        flash("El PIN debe tener entre 4 y 8 digitos.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))
    if empleado["pin_fichaje_hash"]:
        try:
            pin_ok = check_password_hash(empleado["pin_fichaje_hash"], pin_actual)
        except Exception:
            pin_ok = False
        if not pin_ok:
            conn.close()
            flash("El PIN actual no es correcto.", "warning")
            return redirect(url_for("rrhh.portal_empleado"))

    _actualizar_pin_fichaje(conn, empleado["id"], pin_nuevo)
    _registrar_historial(conn, "empleados", empleado["id"], "cambiar_pin_portal", "PIN de fichaje actualizado por empleado")
    _registrar_acceso_portal(conn, empleado["id"], "cambiar_pin", "portal_empleado")
    conn.commit()
    conn.close()
    flash("PIN de fichaje actualizado.", "success")
    return redirect(url_for("rrhh.portal_empleado"))


@rrhh_bp.route("/empleado/fichajes/correccion", methods=["POST"])
@login_required
def solicitar_correccion_fichaje_empleado():
    conn = get_db()
    _ensure_control_tiempo_schema(conn)
    empleado = _cargar_empleado_actual(conn)
    if not empleado:
        conn.close()
        flash("Tu usuario no esta vinculado a ninguna ficha de empleado activa.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))

    tipo = request.form.get("tipo")
    if tipo not in {"entrada", "salida", "pausa", "reanudacion"}:
        conn.close()
        flash("Tipo de fichaje no valido.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))
    fecha = request.form.get("fecha")
    hora = request.form.get("hora")
    motivo = (request.form.get("motivo") or "").strip()
    try:
        fecha_dt = date.fromisoformat(fecha or "")
        hora_txt = (hora or "").strip()
        fecha_hora = datetime.fromisoformat(f"{fecha_dt.isoformat()} {hora_txt}")
    except Exception:
        conn.close()
        flash("Indica fecha y hora validas para la correccion.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))
    if fecha_hora > datetime.now() + timedelta(minutes=5):
        conn.close()
        flash("No se puede solicitar un fichaje futuro.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))
    if _periodo_cerrado_para_fecha(conn, fecha_dt.isoformat()):
        conn.close()
        flash("Ese periodo ya esta cerrado. Habla con RRHH para revisarlo.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))
    if not motivo:
        conn.close()
        flash("Indica el motivo de la correccion.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))

    conn.execute(
        """
        INSERT INTO solicitudes_correccion_fichaje
        (empleado_id, fecha, tipo, fecha_hora_solicitada, motivo, estado, solicitado_por, empresa_id)
        VALUES (?, ?, ?, ?, ?, 'pendiente', ?, ?)
        """,
        (
            empleado["id"],
            fecha_dt.isoformat(),
            tipo,
            fecha_hora.strftime("%Y-%m-%d %H:%M:%S"),
            motivo,
            session.get("user_id"),
            _empresa_id(),
        ),
    )
    nuevo_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    _registrar_historial(conn, "solicitudes_correccion_fichaje", nuevo_id, "solicitud_portal", motivo)
    _registrar_acceso_portal(conn, empleado["id"], "solicitar_correccion_fichaje", f"{tipo} {fecha_hora.strftime('%Y-%m-%d %H:%M:%S')}")
    conn.commit()
    conn.close()
    flash("Solicitud de correccion enviada a RRHH.", "success")
    return redirect(url_for("rrhh.portal_empleado"))


@rrhh_bp.route("/empleado/documentos/subir", methods=["POST"])
@login_required
def subir_documento_empleado():
    conn = get_db()
    ensure_rrhh_schema(conn)
    empleado = _cargar_empleado_actual(conn)
    if not empleado:
        conn.close()
        flash("Tu usuario no esta vinculado a ninguna ficha de empleado activa.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))

    archivo = request.files.get("archivo")
    try:
        ruta = _guardar_archivo_empleado(empleado["id"], archivo)
    except ValueError as exc:
        conn.close()
        flash(str(exc), "warning")
        return redirect(url_for("rrhh.portal_empleado"))

    archivo_nombre = getattr(archivo, "filename", None) or "Documento empleado"
    nombre = request.form.get("nombre") or archivo_nombre
    tipo_documento = request.form.get("tipo_documento") or "Documento empleado"
    conn.execute(
        """
        INSERT INTO documentos_rrhh
        (empleado_id, tipo_documento, nombre, fecha_documento, archivo, estado, observaciones,
         empresa_id, visible_empleado, origen, subido_por_usuario_id)
        VALUES (?, ?, ?, ?, ?, 'pendiente_revision', ?, ?, 1, 'portal_empleado', ?)
        """,
        (
            empleado["id"],
            tipo_documento,
            nombre,
            date.today().isoformat(),
            ruta,
            request.form.get("observaciones"),
            _empresa_id(),
            session.get("user_id"),
        ),
    )
    nuevo_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    _registrar_historial(conn, "documentos_rrhh", nuevo_id, "subir_portal", f"Documento subido por empleado: {nombre}")
    _registrar_acceso_portal(conn, empleado["id"], "subir_documento", f"{tipo_documento}: {nombre}")
    conn.commit()
    conn.close()
    flash("Documento subido. Queda pendiente de revision.", "success")
    return redirect(url_for("rrhh.portal_empleado"))


@rrhh_bp.route("/empleado/nominas/<int:id>/descargar")
@login_required
def descargar_nomina_empleado(id):
    conn = get_db()
    ensure_rrhh_schema(conn)
    empleado = _cargar_empleado_actual(conn)
    if not empleado:
        conn.close()
        abort(404)
    nomina = conn.execute(
        """
        SELECT *
        FROM nominas
        WHERE id = ? AND empleado_id = ? AND empresa_id = ? AND COALESCE(visible_empleado, 1) = 1
        """,
        (id, empleado["id"], _empresa_id()),
    ).fetchone()
    if not nomina or not nomina["archivo"]:
        conn.close()
        abort(404)
    _registrar_acceso_portal(conn, empleado["id"], "descargar_nomina", f"nomina_id={id}")
    conn.commit()
    conn.close()
    return _enviar_archivo_rrhh(nomina["archivo"], f"nomina_{nomina['anio']}_{nomina['mes']}.pdf")


@rrhh_bp.route("/empleado/documentos/<int:id>/descargar")
@login_required
def descargar_documento_empleado(id):
    conn = get_db()
    ensure_rrhh_schema(conn)
    empleado = _cargar_empleado_actual(conn)
    if not empleado:
        conn.close()
        abort(404)
    documento = conn.execute(
        """
        SELECT *
        FROM documentos_rrhh
        WHERE id = ? AND empleado_id = ? AND empresa_id = ? AND COALESCE(visible_empleado, 1) = 1
        """,
        (id, empleado["id"], _empresa_id()),
    ).fetchone()
    if not documento or not documento["archivo"]:
        conn.close()
        abort(404)
    _registrar_acceso_portal(conn, empleado["id"], "descargar_documento", f"documento_id={id}")
    conn.commit()
    conn.close()
    return _enviar_archivo_rrhh(documento["archivo"], documento["nombre"] or None)


@rrhh_bp.route("/empleado/contratos/<int:id>/descargar")
@login_required
def descargar_contrato_empleado(id):
    conn = get_db()
    ensure_rrhh_schema(conn)
    empleado = _cargar_empleado_actual(conn)
    if not empleado:
        conn.close()
        abort(404)
    contrato = conn.execute(
        "SELECT * FROM contratos WHERE id = ? AND empleado_id = ? AND empresa_id = ?",
        (id, empleado["id"], _empresa_id()),
    ).fetchone()
    if not contrato or not contrato["archivo"]:
        conn.close()
        abort(404)
    _registrar_acceso_portal(conn, empleado["id"], "descargar_contrato", f"contrato_id={id}")
    conn.commit()
    conn.close()
    return _enviar_archivo_rrhh(contrato["archivo"], f"contrato_{contrato['id']}.pdf")


@rrhh_bp.route("/empleado/contratos/<int:id>/firmar", methods=["POST"])
@login_required
def firmar_contrato_empleado(id):
    conn = get_db()
    ensure_rrhh_schema(conn)
    empleado = _cargar_empleado_actual(conn)
    if not empleado:
        conn.close()
        abort(404)
    contrato = conn.execute(
        "SELECT * FROM contratos WHERE id = ? AND empleado_id = ? AND empresa_id = ?",
        (id, empleado["id"], _empresa_id()),
    ).fetchone()
    if not contrato:
        conn.close()
        abort(404)
    if request.form.get("acepto") != "1":
        conn.close()
        flash("Marca la aceptacion para firmar.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))

    firma_hash = _firma_hash_empleado("contratos", id, empleado["id"])
    conn.execute(
        """
        UPDATE contratos
        SET firmado_empleado = 1,
            firma_empleado_at = CURRENT_TIMESTAMP,
            firma_empleado_ip = ?,
            firma_empleado_user_agent = ?,
            firma_empleado_hash = ?
        WHERE id = ? AND empleado_id = ? AND empresa_id = ?
        """,
        (
            request.remote_addr,
            str(request.user_agent)[:500],
            firma_hash,
            id,
            empleado["id"],
            _empresa_id(),
        ),
    )
    _registrar_historial(conn, "contratos", id, "firma_empleado", "Contrato aceptado desde portal empleado")
    _registrar_acceso_portal(conn, empleado["id"], "firmar_contrato", f"contrato_id={id}")
    conn.commit()
    conn.close()
    flash("Contrato aceptado y registrado.", "success")
    return redirect(url_for("rrhh.portal_empleado"))


@rrhh_bp.route("/empleado/solicitudes/nueva", methods=["POST"])
@login_required
def solicitar_ausencia_empleado():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    empleado = _cargar_empleado_actual(conn)
    if not empleado:
        conn.close()
        flash("Tu usuario no esta vinculado a ninguna ficha de empleado activa.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))

    tipo = _tipo_ausencia_valido(request.form.get("tipo"))
    inicio = request.form.get("fecha_inicio")
    if not inicio:
        conn.close()
        flash("Indica una fecha de inicio valida.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))
    fin = request.form.get("fecha_fin") or inicio
    try:
        inicio_dt = date.fromisoformat(inicio)
        fin_dt = date.fromisoformat(fin)
    except Exception:
        conn.close()
        flash("Indica una fecha de inicio y fin validas.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))
    if fin_dt < inicio_dt:
        conn.close()
        flash("La fecha fin no puede ser anterior a la fecha inicio.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))

    config = _config_tipo_ausencia(tipo)
    archivo = request.files.get("archivo_documento")
    if config["requiere_documento"] and not (archivo and archivo.filename):
        conn.close()
        flash("Este tipo de solicitud requiere justificante.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))

    observaciones = request.form.get("observaciones")
    documento_id = None
    try:
        documento_id = _insertar_documento_portal_empleado(
            conn,
            empleado["id"],
            archivo,
            "justificante_ausencia",
            f"Justificante {tipo.replace('_', ' ')}",
            observaciones,
        )
    except ValueError as exc:
        conn.close()
        flash(str(exc), "warning")
        return redirect(url_for("rrhh.portal_empleado"))
    if documento_id:
        _registrar_historial(conn, "documentos_rrhh", documento_id, "subir_portal", f"Justificante solicitud: {tipo}")

    conn.execute(
        """
        INSERT INTO vacaciones_rrhh
        (empleado_id, fecha_inicio, fecha_fin, tipo, estado, dias, observaciones, empresa_id, usuario_creacion_id,
         requiere_documento, documento_id, afecta_prenomina, porcentaje_cobro, codigo_oficial, origen, color)
        VALUES (?, ?, ?, ?, 'solicitada', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'portal_empleado', ?)
        """,
        (
            empleado["id"],
            inicio_dt.isoformat(),
            fin_dt.isoformat(),
            tipo,
            _dias_entre(inicio_dt.isoformat(), fin_dt.isoformat()),
            observaciones,
            _empresa_id(),
            session.get("user_id"),
            config["requiere_documento"],
            documento_id,
            config["afecta_prenomina"],
            config["porcentaje_cobro"],
            config["codigo_oficial"],
            config["color"],
        ),
    )
    nuevo_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    _registrar_historial(conn, "vacaciones_rrhh", nuevo_id, "solicitud_portal", f"Solicitud empleado: {tipo}")
    _registrar_acceso_portal(conn, empleado["id"], "solicitar_ausencia", f"{tipo} {inicio_dt.isoformat()} {fin_dt.isoformat()}")
    conn.commit()
    conn.close()
    flash("Solicitud enviada. Queda pendiente de revision.", "success")
    return redirect(url_for("rrhh.portal_empleado"))


@rrhh_bp.route("/empleado/solicitudes/<int:id>/cancelar", methods=["POST"])
@login_required
def cancelar_solicitud_empleado(id):
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    empleado = _cargar_empleado_actual(conn)
    if not empleado:
        conn.close()
        abort(404)

    solicitud = conn.execute(
        """
        SELECT id, estado
        FROM vacaciones_rrhh
        WHERE id = ? AND empleado_id = ? AND empresa_id = ?
        """,
        (id, empleado["id"], _empresa_id()),
    ).fetchone()
    if not solicitud:
        conn.close()
        abort(404)
    if (solicitud["estado"] or "solicitada") != "solicitada":
        conn.close()
        flash("Solo puedes cancelar solicitudes pendientes. Si ya esta aprobada, habla con RRHH.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))

    _safe_update(conn, "vacaciones_rrhh", id, {"estado": "cancelada"}, "Cancelada por el empleado desde portal")
    _registrar_acceso_portal(conn, empleado["id"], "cancelar_solicitud", f"solicitud_id={id}")
    conn.commit()
    conn.close()
    flash("Solicitud cancelada.", "success")
    return redirect(url_for("rrhh.portal_empleado"))


def _buscar_empleado_kiosco(conn, codigo):
    codigo = (codigo or "").strip()
    if not codigo:
        return None
    codigo_acceso = "".join(ch for ch in codigo.upper() if ch.isalnum())[:20]

    join_usuario = ""
    condiciones = []
    params = [_empresa_id()]
    if _table_exists(conn, "usuarios") and _column_exists(conn, "usuarios", "codigo_acceso"):
        join_usuario = """
        LEFT JOIN usuarios u
               ON (u.id = e.usuario_id OR u.empleado_id = e.id)
              AND u.empresa_id = e.empresa_id
        """
        condiciones.append("UPPER(COALESCE(u.codigo_acceso, '')) = ?")
        params.append(codigo_acceso)
    condiciones.extend([
        "LOWER(COALESCE(e.nif, '')) = LOWER(?)",
        "LOWER(COALESCE(e.email, '')) = LOWER(?)",
    ])
    params.extend([codigo, codigo])
    if codigo.isdigit():
        condiciones.append("e.id = ?")
        params.append(int(codigo))

    return conn.execute(
        f"""
        SELECT e.id, e.nombre, e.apellidos, e.pin_fichaje_hash
        FROM empleados e
        {join_usuario}
        WHERE e.empresa_id = ?
          AND COALESCE(e.activo, 1) = 1
          AND ({' OR '.join(condiciones)})
        LIMIT 1
        """,
        params,
    ).fetchone()


@rrhh_bp.route("/kiosco")
@login_required
def kiosco_fichaje():
    if not (_usuario_es_global_rrhh() or _usuario_es_kiosco()):
        flash("No tienes acceso al modo tablet de fichaje.", "warning")
        return redirect(url_for("home"))
    return render_template("rrhh/kiosco_fichaje.html")


@rrhh_bp.route("/kiosco/fichar", methods=["POST"])
@login_required
def kiosco_registrar_fichaje():
    if not (_usuario_es_global_rrhh() or _usuario_es_kiosco()):
        flash("No tienes acceso al modo tablet de fichaje.", "warning")
        return redirect(url_for("home"))

    conn = get_db()
    _ensure_control_tiempo_schema(conn)
    empleado = _buscar_empleado_kiosco(conn, request.form.get("codigo"))
    pin = (request.form.get("pin") or "").strip()

    pin_ok = False
    if empleado and empleado["pin_fichaje_hash"] and pin:
        try:
            pin_ok = check_password_hash(empleado["pin_fichaje_hash"], pin)
        except Exception:
            pin_ok = False

    if not empleado or not pin_ok:
        conn.close()
        flash("Codigo o PIN no valido.", "warning")
        return redirect(url_for("rrhh.kiosco_fichaje"))

    tipo = request.form.get("tipo")
    if tipo not in {"entrada", "salida", "pausa", "reanudacion"}:
        conn.close()
        flash("Tipo de fichaje no valido.", "warning")
        return redirect(url_for("rrhh.kiosco_fichaje"))

    hoy = date.today().isoformat()
    cierre = _periodo_cerrado_para_fecha(conn, hoy)
    if cierre:
        conn.close()
        flash("El periodo de jornada esta cerrado.", "warning")
        return redirect(url_for("rrhh.kiosco_fichaje"))

    ultimo = conn.execute(
        """
        SELECT tipo
        FROM fichajes
        WHERE empresa_id = ? AND empleado_id = ? AND date(fecha_hora) = date(?)
          AND COALESCE(estado, 'activo') != 'anulado'
        ORDER BY datetime(fecha_hora) DESC, id DESC
        LIMIT 1
        """,
        (_empresa_id(), empleado["id"], hoy),
    ).fetchone()
    _estado, acciones_permitidas = _estado_jornada_desde_ultimo(ultimo["tipo"] if ultimo else None)
    if tipo not in acciones_permitidas:
        conn.close()
        flash("Ese fichaje no encaja con el ultimo estado registrado.", "warning")
        return redirect(url_for("rrhh.kiosco_fichaje"))

    _insertar_fichaje(conn, empleado["id"], tipo, None, "kiosco_tablet")
    _registrar_acceso_portal(conn, empleado["id"], "fichaje_kiosco", tipo)
    conn.commit()
    conn.close()
    flash("Fichaje registrado.", "success")
    return redirect(url_for("rrhh.kiosco_fichaje"))


@rrhh_bp.route("/fichajes")
@login_required
def lista_fichajes():
    conn = get_db()
    _ensure_control_tiempo_schema(conn)
    empresa_id = _empresa_id()
    scope_sql, scope_params = _departamento_scope_sql("e")
    fichajes = conn.execute(
        f"""
        SELECT f.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre,
               d.nombre departamento_nombre
        FROM fichajes f
        JOIN empleados e ON f.empleado_id = e.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        WHERE {scope_sql} AND f.empresa_id = ?
        ORDER BY f.fecha_hora DESC
        LIMIT 300
        """,
        scope_params + [empresa_id],
    ).fetchall()
    empleados = conn.execute(
        "SELECT id, nombre, apellidos FROM empleados WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre, apellidos",
        (empresa_id,),
    ).fetchall() if _usuario_es_global_rrhh() else []
    solicitudes_correccion = conn.execute(
        f"""
        SELECT s.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre,
               d.nombre departamento_nombre
        FROM solicitudes_correccion_fichaje s
        JOIN empleados e ON s.empleado_id = e.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        WHERE {scope_sql}
          AND s.empresa_id = ?
          AND s.estado = 'pendiente'
        ORDER BY datetime(s.fecha_solicitud) ASC, s.id ASC
        """,
        scope_params + [empresa_id],
    ).fetchall() if _usuario_es_global_rrhh() else []
    conn.close()
    return render_template(
        "rrhh/fichajes.html",
        fichajes=fichajes,
        empleados=empleados,
        solicitudes_correccion=solicitudes_correccion,
        puede_editar=_usuario_es_global_rrhh(),
    )


@rrhh_bp.route("/fichajes/registrar", methods=["POST"])
@login_required
def registrar_fichaje():
    conn = get_db()
    ensure_rrhh_schema(conn)
    if _usuario_es_global_rrhh():
        empleado_id = request.form.get("empleado_id") or session.get("user_empleado_id")
        origen = "manual_rrhh"
    else:
        empleado = _cargar_empleado_actual(conn)
        if not empleado:
            conn.close()
            flash("Tu usuario no esta vinculado a ningun empleado.", "warning")
            return redirect(url_for("rrhh.portal_empleado"))
        solicitado = request.form.get("empleado_id")
        if solicitado and str(solicitado) != str(empleado["id"]):
            conn.close()
            flash("No puedes registrar fichajes de otro empleado.", "warning")
            return redirect(url_for("rrhh.portal_empleado"))
        empleado_id = empleado["id"]
        origen = "portal_empleado"
    tipo = request.form.get("tipo")
    if tipo not in {"entrada", "salida", "pausa", "reanudacion"}:
        tipo = "entrada"
    if not empleado_id:
        conn.close()
        flash("Tu usuario no está vinculado a ningún empleado.", "warning")
        return redirect(url_for("rrhh.portal_empleado"))

    hoy = date.today().isoformat()
    if _periodo_cerrado_para_fecha(conn, hoy):
        conn.close()
        flash("El periodo de jornada esta cerrado.", "warning")
        if origen == "portal_empleado":
            return redirect(url_for("rrhh.portal_empleado"))
        return redirect(url_for("rrhh.lista_fichajes"))

    _insertar_fichaje(conn, empleado_id, tipo, request.form.get("observaciones"), origen)
    conn.commit()
    conn.close()
    flash(f"Fichaje de {tipo} registrado correctamente.", "success")
    if origen == "portal_empleado":
        return redirect(url_for("rrhh.portal_empleado"))
    return redirect(url_for("rrhh.lista_fichajes"))


@rrhh_bp.route("/fichajes/correcciones/<int:id>/aprobar", methods=["POST"])
@login_required
@_require_rrhh_global
def aprobar_correccion_fichaje(id):
    conn = get_db()
    _ensure_control_tiempo_schema(conn)
    solicitud = conn.execute(
        """
        SELECT *
        FROM solicitudes_correccion_fichaje
        WHERE id = ? AND empresa_id = ?
        """,
        (id, _empresa_id()),
    ).fetchone()
    if not solicitud:
        conn.close()
        flash("Solicitud no encontrada.", "warning")
        return _redirect_post_rrhh("rrhh.lista_fichajes")
    if solicitud["estado"] != "pendiente":
        conn.close()
        flash("La solicitud ya esta resuelta.", "warning")
        return _redirect_post_rrhh("rrhh.lista_fichajes")
    if _periodo_cerrado_para_fecha(conn, solicitud["fecha"]):
        conn.close()
        flash("El periodo de esa fecha esta cerrado. Reabre el cierre antes de aprobar cambios.", "warning")
        return _redirect_post_rrhh("rrhh.lista_fichajes")

    respuesta = request.form.get("respuesta_rrhh") or "Correccion aprobada por RRHH"
    fichaje_id = _insertar_fichaje(
        conn,
        solicitud["empleado_id"],
        solicitud["tipo"],
        f"Correccion aprobada: {solicitud['motivo'] or ''}".strip(),
        "correccion_aprobada",
        solicitud["fecha_hora_solicitada"],
    )
    conn.execute(
        """
        UPDATE solicitudes_correccion_fichaje
        SET estado = 'aprobada',
            respuesta_rrhh = ?,
            fichaje_id = ?,
            resuelto_por = ?,
            fecha_resolucion = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (respuesta, fichaje_id, session.get("user_id"), id, _empresa_id()),
    )
    _registrar_historial(conn, "solicitudes_correccion_fichaje", id, "aprobar", respuesta)
    _registrar_historial(conn, "fichajes", fichaje_id, "crear_correccion", respuesta)
    conn.commit()
    conn.close()
    flash("Correccion aprobada y fichaje registrado.", "success")
    return _redirect_post_rrhh("rrhh.lista_fichajes")


@rrhh_bp.route("/fichajes/correcciones/<int:id>/rechazar", methods=["POST"])
@login_required
@_require_rrhh_global
def rechazar_correccion_fichaje(id):
    conn = get_db()
    _ensure_control_tiempo_schema(conn)
    solicitud = conn.execute(
        """
        SELECT *
        FROM solicitudes_correccion_fichaje
        WHERE id = ? AND empresa_id = ?
        """,
        (id, _empresa_id()),
    ).fetchone()
    if not solicitud:
        conn.close()
        flash("Solicitud no encontrada.", "warning")
        return _redirect_post_rrhh("rrhh.lista_fichajes")
    if solicitud["estado"] != "pendiente":
        conn.close()
        flash("La solicitud ya esta resuelta.", "warning")
        return _redirect_post_rrhh("rrhh.lista_fichajes")
    respuesta = request.form.get("respuesta_rrhh") or "Correccion rechazada por RRHH"
    conn.execute(
        """
        UPDATE solicitudes_correccion_fichaje
        SET estado = 'rechazada',
            respuesta_rrhh = ?,
            resuelto_por = ?,
            fecha_resolucion = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (respuesta, session.get("user_id"), id, _empresa_id()),
    )
    _registrar_historial(conn, "solicitudes_correccion_fichaje", id, "rechazar", respuesta)
    conn.commit()
    conn.close()
    flash("Correccion rechazada.", "success")
    return _redirect_post_rrhh("rrhh.lista_fichajes")


# ============================================================
# ROLES Y PERMISOS
# ============================================================

@rrhh_bp.route("/roles")
@login_required
@_require_rrhh_global
def lista_roles():
    conn = get_db()
    ensure_rrhh_schema(conn)
    conn.commit()
    q = (request.args.get("q") or "").strip()
    roles_params = []
    roles_where = ""
    if q:
        roles_where = "WHERE nombre LIKE ?"
        roles_params.append(f"%{q}%")
    roles = conn.execute(
        f"SELECT * FROM roles {roles_where} ORDER BY nombre",
        roles_params,
    ).fetchall() if _table_exists(conn, "roles") else []
    permisos = conn.execute("SELECT * FROM permisos ORDER BY nombre").fetchall() if _table_exists(conn, "permisos") else []
    roles_permisos = conn.execute("SELECT rol_id, permiso_id FROM roles_permisos").fetchall() if _table_exists(conn, "roles_permisos") else []
    asignados = {(r["rol_id"], r["permiso_id"]) for r in roles_permisos}
    usuarios_por_rol = {
        row["rol_id"]: row["total"]
        for row in conn.execute(
            """
            SELECT rol_id, COUNT(*) AS total
            FROM usuarios
            WHERE empresa_id = ?
            GROUP BY rol_id
            """,
            (_empresa_id(),),
        ).fetchall()
    } if _table_exists(conn, "usuarios") else {}
    mapa = {}
    permiso_por_id = {p["id"]: p["nombre"] for p in permisos}
    permiso_row_por_nombre = {p["nombre"]: p for p in permisos}
    for rol_id, permiso_id in asignados:
        mapa.setdefault(rol_id, []).append(permiso_por_id.get(permiso_id, permiso_id))
    roles_por_bloque = {}
    for rol in roles:
        permisos_directos = {p for p in mapa.get(rol["id"], []) if p}
        permisos_base = set(PERMISOS_ROL_BASE.get(normalizar_rol(rol["nombre"]), set()))
        permisos_efectivos = permisos_base | permisos_directos
        bloque = _grupo_rol_por_permisos(rol["nombre"], permisos_efectivos)
        roles_por_bloque.setdefault(bloque, []).append({
            "rol": rol,
            "usuarios": usuarios_por_rol.get(rol["id"], 0),
            "permisos_directos": sorted(permisos_directos),
            "permisos_efectivos_total": len(permisos_efectivos),
        })
    orden_bloques = [
        "Direccion y administracion",
        "RRHH y permisos",
        "Compras, stock y proveedores",
        "Recetas, produccion y APPCC",
        "TPV, sala y bares",
        "Recepcion",
        "Pisos",
        "Mantenimiento",
        "Cliente, experiencia y animacion",
        "Contabilidad e informes",
        "Otros roles",
    ]
    roles_bloques = [
        {"nombre": nombre, "roles": roles_por_bloque[nombre]}
        for nombre in orden_bloques
        if nombre in roles_por_bloque
    ]
    usados = set()
    permisos_bloques = []
    for modulo in MODULOS_AUDITORIA_PERMISOS:
        grupo_permisos = []
        for permiso_nombre in modulo["permisos"]:
            permiso = permiso_row_por_nombre.get(permiso_nombre)
            if permiso and permiso_nombre not in usados:
                grupo_permisos.append(permiso)
                usados.add(permiso_nombre)
        if grupo_permisos:
            permisos_bloques.append({"nombre": modulo["modulo"], "permisos": grupo_permisos})
    otros_permisos = [p for p in permisos if p["nombre"] not in usados]
    if otros_permisos:
        permisos_bloques.append({"nombre": "Otros permisos", "permisos": otros_permisos})
    conn.close()
    return render_template(
        "rrhh/roles.html",
        roles=roles,
        roles_bloques=roles_bloques,
        permisos=permisos,
        permisos_bloques=permisos_bloques,
        asignados=asignados,
        mapa=mapa,
        usuarios_por_rol=usuarios_por_rol,
        q=q,
    )


@rrhh_bp.route("/roles/nuevo", methods=["POST"])
@login_required
@_require_rrhh_global
def nuevo_rol():
    nombre = (request.form.get("nombre") or "").strip()
    if not nombre:
        flash("El nombre del rol es obligatorio.", "warning")
        return redirect(url_for("rrhh.lista_roles"))
    conn = get_db()
    ensure_rrhh_schema(conn)
    if not _table_exists(conn, "roles"):
        conn.execute("CREATE TABLE IF NOT EXISTS roles (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE)")
    existente = conn.execute("SELECT id FROM roles WHERE LOWER(nombre) = LOWER(?)", (nombre,)).fetchone()
    if existente:
        conn.close()
        flash("Ese rol ya existe.", "warning")
        return redirect(url_for("rrhh.lista_roles", q=nombre))
    conn.execute("INSERT INTO roles (nombre) VALUES (?)", (nombre,))
    conn.commit()
    conn.close()
    flash("Rol creado correctamente.", "success")
    return redirect(url_for("rrhh.lista_roles"))


@rrhh_bp.route("/roles/permisos", methods=["POST"])
@login_required
@_require_rrhh_global
def guardar_permisos_rol():
    rol_id = request.form.get("rol_id")
    permisos_ids = request.form.getlist("permisos")
    conn = get_db()
    ensure_rrhh_schema(conn)
    conn.commit()
    conn.execute("DELETE FROM roles_permisos WHERE rol_id = ?", (rol_id,))
    for permiso_id in permisos_ids:
        conn.execute("INSERT INTO roles_permisos (rol_id, permiso_id) VALUES (?, ?)", (rol_id, permiso_id))
    conn.commit()
    conn.close()
    flash("Permisos del rol actualizados.", "success")
    return redirect(url_for("rrhh.lista_roles"))


@rrhh_bp.route("/roles/<int:rol_id>/eliminar", methods=["POST"])
@login_required
@_require_rrhh_global
def eliminar_rol(rol_id):
    conn = get_db()
    ensure_rrhh_schema(conn)
    rol = conn.execute("SELECT * FROM roles WHERE id = ?", (rol_id,)).fetchone()
    if not rol:
        conn.close()
        flash("Rol no encontrado.", "warning")
        return redirect(url_for("rrhh.lista_roles"))
    usuarios = conn.execute(
        "SELECT COUNT(*) AS total FROM usuarios WHERE rol_id = ? AND empresa_id = ?",
        (rol_id, _empresa_id()),
    ).fetchone()["total"] if _table_exists(conn, "usuarios") else 0
    if usuarios:
        conn.close()
        flash(f"No se puede eliminar el rol '{rol['nombre']}' porque tiene {usuarios} usuario(s) asignado(s).", "warning")
        return redirect(url_for("rrhh.lista_roles", q=rol["nombre"]))
    conn.execute("DELETE FROM roles_permisos WHERE rol_id = ?", (rol_id,))
    conn.execute("DELETE FROM roles WHERE id = ?", (rol_id,))
    conn.commit()
    conn.close()
    flash(f"Rol '{rol['nombre']}' eliminado.", "success")
    return redirect(url_for("rrhh.lista_roles"))


# ============================================================
# RRHH EDITABLE COMPLETO — rutas incrementales con trazabilidad
# ============================================================

def _ensure_editable_schema(conn):
    """Delegado a ensure_rrhh_schema para consistencia."""
    ensure_rrhh_schema(conn)


def _registrar_historial(conn, tabla, registro_id, accion, motivo=None):
    _ensure_editable_schema(conn)
    conn.execute(
        """INSERT INTO rrhh_historial_cambios (tabla, registro_id, accion, motivo, usuario_id, empresa_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (tabla, registro_id, accion, motivo or '', session.get('user_id'), _empresa_id()),
    )


def _safe_update(conn, tabla, registro_id, campos, motivo=None):
    sets=[]; params=[]
    for campo, valor in campos.items():
        if _column_exists(conn, tabla, campo):
            sets.append(f"{campo} = ?")
            params.append(valor)
    if _column_exists(conn, tabla, 'updated_at'):
        sets.append('updated_at = CURRENT_TIMESTAMP')
    if _column_exists(conn, tabla, 'updated_by'):
        sets.append('updated_by = ?')
        params.append(session.get('user_id'))
    if _column_exists(conn, tabla, 'motivo_cambio'):
        sets.append('motivo_cambio = ?')
        params.append(motivo or '')
    if not sets:
        return
    params.extend([registro_id, _empresa_id()])
    conn.execute(f"UPDATE {tabla} SET {', '.join(sets)} WHERE id = ? AND empresa_id = ?", params)
    _registrar_historial(conn, tabla, registro_id, 'editar', motivo)


def _get_row(conn, tabla, registro_id):
    return conn.execute(f"SELECT * FROM {tabla} WHERE id = ? AND empresa_id = ?", (registro_id, _empresa_id())).fetchone()


def _empleados_select(conn):
    scope_sql, scope_params = _departamento_scope_sql("e")
    return conn.execute(
        f"""
        SELECT e.id, e.nombre, e.apellidos, e.departamento_id
        FROM empleados e
        WHERE {scope_sql} AND COALESCE(e.activo,1)=1
        ORDER BY e.nombre, e.apellidos
        """,
        scope_params,
    ).fetchall()


def _departamentos_select(conn):
    if not _table_exists(conn, 'departamentos'):
        return []
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    if centro_id:
        dep_ids = ids_departamentos_centro(conn, empresa_id, centro_id)
        if not dep_ids:
            return []
        return conn.execute(
            f"SELECT id, nombre FROM departamentos WHERE empresa_id = ? AND id IN ({','.join('?' for _ in dep_ids)}) ORDER BY nombre",
            [empresa_id] + dep_ids,
        ).fetchall()
    return conn.execute("SELECT id, nombre FROM departamentos WHERE empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()


def _subdepartamentos_select(conn):
    if not _table_exists(conn, 'subdepartamentos'):
        return []
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    if centro_id:
        dep_ids = ids_departamentos_centro(conn, empresa_id, centro_id)
        if not dep_ids:
            return []
        return conn.execute(
            f"""
            SELECT id, nombre, departamento_id
            FROM subdepartamentos
            WHERE empresa_id = ? AND departamento_id IN ({','.join('?' for _ in dep_ids)})
            ORDER BY nombre
            """,
            [empresa_id] + dep_ids,
        ).fetchall()
    return conn.execute("SELECT id, nombre, departamento_id FROM subdepartamentos WHERE empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()


def _centros_select(conn):
    centro_id = centro_activo_id()
    if centro_id:
        return conn.execute(
            "SELECT id, nombre FROM centros_trabajo WHERE empresa_id = ? AND id = ? AND COALESCE(activo,1)=1 ORDER BY nombre",
            (_empresa_id(), centro_id),
        ).fetchall()
    return conn.execute("SELECT id, nombre FROM centros_trabajo WHERE empresa_id = ? AND COALESCE(activo,1)=1 ORDER BY nombre", (_empresa_id(),)).fetchall()


def _puestos_select(conn):
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    if centro_id:
        dep_ids = ids_departamentos_centro(conn, empresa_id, centro_id)
        condiciones = ["centro_id = ?"]
        params = [empresa_id, centro_id]
        if dep_ids:
            condiciones.append(f"departamento_id IN ({','.join('?' for _ in dep_ids)})")
            params.extend(dep_ids)
        return conn.execute(
            f"""
            SELECT id, nombre, centro_id, departamento_id, subdepartamento_id
            FROM puestos_operativos
            WHERE empresa_id = ? AND COALESCE(activo,1)=1 AND ({' OR '.join(condiciones)})
            ORDER BY nombre
            """,
            params,
        ).fetchall()
    return conn.execute("SELECT id, nombre, centro_id, departamento_id, subdepartamento_id FROM puestos_operativos WHERE empresa_id = ? AND COALESCE(activo,1)=1 ORDER BY nombre", (empresa_id,)).fetchall()


@rrhh_bp.route('/categorias/<int:id>/editar', methods=['GET','POST'])
@login_required
@_require_rrhh_global
def editar_categoria(id):
    conn=get_db(); _ensure_editable_schema(conn)
    categoria=_get_row(conn,'categorias_laborales',id)
    if not categoria:
        conn.close(); flash('Categoría no encontrada.', 'warning'); return redirect(url_for('rrhh.lista_categorias'))
    if request.method=='POST':
        _safe_update(conn,'categorias_laborales',id,{
            'nombre':request.form.get('nombre'), 'convenio':request.form.get('convenio'),
            'salario_base':request.form.get('salario_base') or 0, 'descripcion':request.form.get('descripcion'),
            'activo':1 if request.form.get('activo') else 0,
        }, request.form.get('motivo_cambio'))
        conn.commit(); conn.close(); flash('Categoría actualizada.', 'success'); return redirect(url_for('rrhh.lista_categorias'))
    conn.close(); return render_template('rrhh/categoria_form.html', categoria=categoria)


@rrhh_bp.route('/categorias/<int:id>/desactivar', methods=['POST'])
@login_required
@_require_rrhh_global
def desactivar_categoria(id):
    conn=get_db(); _ensure_editable_schema(conn)
    _safe_update(conn,'categorias_laborales',id,{'activo':0},request.form.get('motivo_cambio') or 'Desactivada desde listado')
    conn.commit(); conn.close(); flash('Categoría desactivada.', 'success'); return redirect(url_for('rrhh.lista_categorias'))


@rrhh_bp.route('/contratos/<int:id>/editar', methods=['GET','POST'])
@login_required
@_require_rrhh_global
def editar_contrato(id):
    conn=get_db(); _ensure_editable_schema(conn)
    contrato=_registro_empleado_en_ambito_rrhh(conn,'contratos',id)
    if not contrato:
        conn.close(); flash('Contrato no encontrado.', 'warning'); return redirect(url_for('rrhh.lista_contratos'))
    if request.method=='POST':
        if not _empleado_en_ambito_rrhh(conn, request.form.get('empleado_id')):
            conn.close(); flash('Empleado no encontrado en tu empresa o centro activo.', 'warning'); return redirect(url_for('rrhh.lista_contratos'))
        campos={k:request.form.get(k) for k in ['empleado_id','tipo_contrato','fecha_inicio','fecha_fin','jornada','horas_semanales','salario_bruto','categoria_id','estado','archivo','observaciones','centro_trabajo','puesto_trabajo','grupo_profesional','convenio','tipo_jornada','turno','dias_trabajo','periodo_prueba','salario_bruto_anual','salario_bruto_mensual','pagas','vacaciones_dias','fecha_preaviso','motivo_fin']}
        for k in ['fecha_fin','categoria_id']:
            if campos.get(k)=='': campos[k]=None
        _safe_update(conn,'contratos',id,campos,request.form.get('motivo_cambio'))
        conn.commit(); conn.close(); flash('Contrato actualizado.', 'success'); return redirect(url_for('rrhh.lista_contratos'))
    empleados=_empleados_select(conn); categorias=conn.execute('SELECT id,nombre FROM categorias_laborales WHERE empresa_id=? ORDER BY nombre',(_empresa_id(),)).fetchall(); conn.close()
    return render_template('rrhh/contrato_form.html', contrato=contrato, empleados=empleados, categorias=categorias)


@rrhh_bp.route('/contratos/<int:id>/anular', methods=['POST'])
@login_required
@_require_rrhh_global
def anular_contrato(id):
    conn=get_db(); _ensure_editable_schema(conn)
    if not _registro_empleado_en_ambito_rrhh(conn,'contratos',id):
        conn.close(); flash('Contrato no encontrado en tu empresa o centro activo.', 'warning'); return redirect(url_for('rrhh.lista_contratos'))
    _safe_update(conn,'contratos',id,{'estado':'anulado'},request.form.get('motivo_cambio') or 'Contrato anulado')
    conn.commit(); conn.close(); flash('Contrato marcado como anulado.', 'success'); return redirect(url_for('rrhh.lista_contratos'))


@rrhh_bp.route('/nominas/<int:id>/editar', methods=['GET','POST'])
@login_required
@_require_rrhh_global
def editar_nomina(id):
    conn=get_db(); _ensure_editable_schema(conn)
    nomina=_registro_empleado_en_ambito_rrhh(conn,'nominas',id)
    if not nomina:
        conn.close(); flash('Nómina no encontrada.', 'warning'); return redirect(url_for('rrhh.lista_nominas'))
    if request.method=='POST':
        if not _empleado_en_ambito_rrhh(conn, request.form.get('empleado_id')):
            conn.close(); flash('Empleado no encontrado en tu empresa o centro activo.', 'warning'); return redirect(url_for('rrhh.lista_nominas'))
        campos={k:request.form.get(k) for k in ['empleado_id','mes','anio','periodo_desde','periodo_hasta','salario_base','complementos','horas_extra','pluses','prorrata_pagas','irpf','seguridad_social_trabajador','seguridad_social_empresa','anticipos_embargos','bruto','neto','coste_empresa','estado','fecha_pago','forma_pago','archivo','observaciones']}
        _safe_update(conn,'nominas',id,campos,request.form.get('motivo_cambio'))
        conn.commit(); conn.close(); flash('Nómina actualizada.', 'success'); return redirect(url_for('rrhh.lista_nominas'))
    empleados=_empleados_select(conn); conn.close(); return render_template('rrhh/nomina_form.html', nomina=nomina, empleados=empleados)


@rrhh_bp.route('/nominas/<int:id>/anular', methods=['POST'])
@login_required
@_require_rrhh_global
def anular_nomina(id):
    conn=get_db(); _ensure_editable_schema(conn)
    if not _registro_empleado_en_ambito_rrhh(conn,'nominas',id):
        conn.close(); flash('NÃ³mina no encontrada en tu empresa o centro activo.', 'warning'); return redirect(url_for('rrhh.lista_nominas'))
    _safe_update(conn,'nominas',id,{'estado':'anulada'},request.form.get('motivo_cambio') or 'Nómina anulada')
    conn.commit(); conn.close(); flash('Nómina anulada sin borrar histórico.', 'success'); return redirect(url_for('rrhh.lista_nominas'))


@rrhh_bp.route('/documentos/<int:id>/editar', methods=['GET','POST'])
@login_required
@_require_rrhh_global
def editar_documento(id):
    conn=get_db(); _ensure_editable_schema(conn)
    documento=_registro_empleado_en_ambito_rrhh(conn,'documentos_rrhh',id)
    if not documento:
        conn.close(); flash('Documento no encontrado.', 'warning'); return redirect(url_for('rrhh.lista_documentos'))
    if request.method=='POST':
        if not _empleado_en_ambito_rrhh(conn, request.form.get('empleado_id')):
            conn.close(); flash('Empleado no encontrado en tu empresa o centro activo.', 'warning'); return redirect(url_for('rrhh.lista_documentos'))
        campos={k:request.form.get(k) for k in ['empleado_id','tipo_documento','nombre','fecha_documento','fecha_caducidad','archivo','estado','observaciones']}
        _safe_update(conn,'documentos_rrhh',id,campos,request.form.get('motivo_cambio'))
        conn.commit(); conn.close(); flash('Documento actualizado.', 'success'); return redirect(url_for('rrhh.lista_documentos'))
    empleados=_empleados_select(conn); conn.close(); return render_template('rrhh/documento_form.html', documento=documento, empleados=empleados)


@rrhh_bp.route('/documentos/<int:id>/anular', methods=['POST'])
@login_required
@_require_rrhh_global
def anular_documento(id):
    conn=get_db(); _ensure_editable_schema(conn)
    if not _registro_empleado_en_ambito_rrhh(conn,'documentos_rrhh',id):
        conn.close(); flash('Documento no encontrado en tu empresa o centro activo.', 'warning'); return redirect(url_for('rrhh.lista_documentos'))
    _safe_update(conn,'documentos_rrhh',id,{'estado':'anulado'},request.form.get('motivo_cambio') or 'Documento anulado')
    conn.commit(); conn.close(); flash('Documento anulado.', 'success'); return redirect(url_for('rrhh.lista_documentos'))


@rrhh_bp.route('/turnos/<int:id>/editar', methods=['GET','POST'])
@login_required
@_require_rrhh_global
def editar_turno_rrhh(id):
    conn=get_db(); _ensure_editable_schema(conn)
    turno=_get_row(conn,'turnos_rrhh',id)
    if not turno:
        conn.close(); flash('Turno no encontrado.', 'warning'); return redirect(url_for('rrhh.lista_turnos'))
    if request.method=='POST':
        campos={k:request.form.get(k) for k in ['empleado_id','fecha','hora_inicio','hora_fin','tipo','departamento_id','centro_id','subdepartamento_id','puesto_id','estado','observaciones']}
        for k in ['departamento_id','centro_id','subdepartamento_id','puesto_id']:
            if campos.get(k)=='': campos[k]=None
        _safe_update(conn,'turnos_rrhh',id,campos,request.form.get('motivo_cambio'))
        conn.commit(); conn.close(); flash('Turno actualizado. Recalcula pre-nómina si afecta al mes.', 'success'); return _redirect_cuadrante_real_con_filtros(request.form.get('fecha'))
    empleados=_empleados_select(conn); departamentos=_departamentos_select(conn); subdepartamentos=_subdepartamentos_select(conn); centros=_centros_select(conn); puestos=_puestos_select(conn); conn.close()
    return render_template('rrhh/turno_form.html', turno=turno, empleados=empleados, departamentos=departamentos, subdepartamentos=subdepartamentos, centros=centros, puestos=puestos)


@rrhh_bp.route('/turnos/<int:id>/anular', methods=['POST'])
@login_required
@_require_rrhh_global
def anular_turno_rrhh(id):
    conn = get_db()
    _ensure_editable_schema(conn)

    turno = _get_row(conn, 'turnos_rrhh', id)

    fecha = request.form.get('fecha')
    if not fecha and turno:
        fecha = turno['fecha']
    if not fecha:
        fecha = date.today().isoformat()

    centro_id = request.form.get('centro_id') or request.form.get('filtro_centro_id') or ''
    departamento_id = request.form.get('departamento_id') or request.form.get('filtro_departamento_id') or ''
    subdepartamento_id = request.form.get('subdepartamento_id') or request.form.get('filtro_subdepartamento_id') or ''
    puesto_id = request.form.get('puesto_id') or request.form.get('filtro_puesto_id') or ''

    motivo = request.form.get('motivo_cambio') or 'Turno anulado desde cuadrante real'

    if _column_exists(conn, 'turnos_rrhh', 'estado'):
        _safe_update(conn, 'turnos_rrhh', id, {'estado': 'anulado'}, motivo)
    else:
        conn.execute(
            'DELETE FROM turnos_rrhh WHERE id = ? AND empresa_id = ?',
            (id, _empresa_id()),
        )

    conn.commit()
    conn.close()

    flash('Turno anulado.', 'success')

    return redirect(url_for(
        'rrhh.cuadrante_real',
        fecha=fecha,
        centro_id=centro_id,
        departamento_id=departamento_id,
        subdepartamento_id=subdepartamento_id,
        puesto_id=puesto_id,
    ))


@rrhh_bp.route('/variables-prenomina/<int:id>/editar', methods=['GET','POST'])
@login_required
@_require_rrhh_global
def editar_variable_prenomina(id):
    conn=get_db(); _ensure_editable_schema(conn)
    variable=_get_row(conn,'variables_prenomina',id)
    if not variable:
        conn.close(); flash('Variable no encontrada.', 'warning'); return redirect(url_for('rrhh.lista_variables_prenomina'))
    if request.method=='POST':
        unidades=_to_float(request.form.get('unidades'),1); imp=_to_float(request.form.get('importe_unitario'),0); total=_to_float(request.form.get('importe_total'),0) or unidades*imp
        _safe_update(conn,'variables_prenomina',id,{'empleado_id':request.form.get('empleado_id'),'fecha':request.form.get('fecha'),'concepto':request.form.get('concepto'),'tipo':request.form.get('tipo'),'unidades':unidades,'importe_unitario':imp,'importe_total':total,'observaciones':request.form.get('observaciones'),'estado':request.form.get('estado') or 'activo'},request.form.get('motivo_cambio'))
        conn.commit(); conn.close(); flash('Variable actualizada. Recalcula pre-nómina si procede.', 'success'); return redirect(url_for('rrhh.lista_variables_prenomina'))
    empleados=_empleados_select(conn); conn.close(); return render_template('rrhh/variable_form.html', variable=variable, empleados=empleados)


@rrhh_bp.route('/variables-prenomina/<int:id>/anular', methods=['POST'])
@login_required
@_require_rrhh_global
def anular_variable_prenomina(id):
    conn=get_db(); _ensure_editable_schema(conn)
    _safe_update(conn,'variables_prenomina',id,{'estado':'anulada'},request.form.get('motivo_cambio') or 'Variable anulada')
    conn.commit(); conn.close(); flash('Variable anulada.', 'success'); return redirect(url_for('rrhh.lista_variables_prenomina'))


@rrhh_bp.route('/vacaciones/<int:id>/editar', methods=['GET','POST'])
@login_required
@_require_rrhh_global
def editar_vacacion(id):
    conn=get_db(); _ensure_cuadrantes_schema(conn); _ensure_ausencias_extra_schema(conn)
    registro=_get_row(conn,'vacaciones_rrhh',id)
    if not registro:
        conn.close(); flash('Registro no encontrado.', 'warning'); return redirect(url_for('rrhh.vacaciones'))
    if request.method=='POST':
        dias=_dias_entre(request.form.get('fecha_inicio'), request.form.get('fecha_fin'))
        tipo = _tipo_ausencia_valido(request.form.get('tipo'))
        config = _config_tipo_ausencia(tipo)
        documento_id = request.form.get('documento_id') or None
        try:
            documento_subido_id = _guardar_documento_ausencia(conn, request.form.get('empleado_id'), tipo, request.files.get('archivo_documento'))
        except ValueError as exc:
            conn.close(); flash(str(exc), 'warning'); return redirect(url_for('rrhh.editar_vacacion', id=id))
        if documento_subido_id:
            documento_id = documento_subido_id
        _safe_update(conn,'vacaciones_rrhh',id,{
            'empleado_id':request.form.get('empleado_id'),
            'fecha_inicio':request.form.get('fecha_inicio'),
            'fecha_fin':request.form.get('fecha_fin'),
            'tipo':tipo,
            'estado':request.form.get('estado'),
            'dias':dias,
            'observaciones':request.form.get('observaciones'),
            'requiere_documento':config['requiere_documento'],
            'documento_id':documento_id,
            'afecta_prenomina':config['afecta_prenomina'],
            'porcentaje_cobro':config['porcentaje_cobro'],
            'codigo_oficial':config['codigo_oficial'],
            'origen':request.form.get('origen') or 'manual_rrhh',
            'color':config['color'],
        },request.form.get('motivo_cambio'))
        conn.commit(); conn.close(); flash('Vacaciones/ausencia actualizada.', 'success'); return redirect(url_for('rrhh.vacaciones'))
    empleados=_empleados_select(conn)
    documentos=conn.execute(
        "SELECT id, nombre, tipo_documento FROM documentos_rrhh WHERE empresa_id = ? AND COALESCE(estado,'vigente') != 'anulado' ORDER BY date(COALESCE(fecha_documento, fecha_creacion, '1900-01-01')) DESC, id DESC LIMIT 300",
        (_empresa_id(),),
    ).fetchall()
    conn.close()
    return render_template('rrhh/vacacion_form.html', registro=registro, empleados=empleados, documentos=documentos, tipos_ausencia=_tipos_ausencia_select())


@rrhh_bp.route('/vacaciones/<int:id>/cancelar', methods=['POST'])
@login_required
@_require_rrhh_global
def cancelar_vacacion(id):
    conn=get_db(); _ensure_editable_schema(conn)
    _safe_update(conn,'vacaciones_rrhh',id,{'estado':'cancelada'},request.form.get('motivo_cambio') or 'Cancelada')
    conn.commit(); conn.close(); flash('Vacaciones/ausencia cancelada.', 'success'); return redirect(url_for('rrhh.vacaciones'))


@rrhh_bp.route('/puestos/<int:id>/editar', methods=['GET','POST'])
@login_required
@_require_rrhh_global
def editar_puesto_operativo(id):
    conn=get_db(); _ensure_editable_schema(conn)
    puesto=_get_row(conn,'puestos_operativos',id)
    if not puesto:
        conn.close(); flash('Puesto no encontrado.', 'warning'); return redirect(url_for('rrhh.lista_puestos_operativos'))
    if request.method=='POST':
        campos = dict((k, request.form.get(k)) for k in ['nombre','descripcion','centro_id','departamento_id','subdepartamento_id','categoria_id'])
        for k in ['centro_id','departamento_id','subdepartamento_id','categoria_id']:
            if campos.get(k)=='': campos[k]=None
        campos['activo'] = str(1 if request.form.get('activo') else 0)
        _safe_update(conn,'puestos_operativos',id,campos,request.form.get('motivo_cambio'))
        conn.commit(); conn.close(); flash('Puesto operativo actualizado.', 'success'); return redirect(url_for('rrhh.lista_puestos_operativos'))
    centros=_centros_select(conn); departamentos=_departamentos_select(conn); subdepartamentos=_subdepartamentos_select(conn); categorias=conn.execute('SELECT id,nombre FROM categorias_laborales WHERE empresa_id=? ORDER BY nombre',(_empresa_id(),)).fetchall(); conn.close()
    return render_template('rrhh/puesto_form.html', puesto=puesto, centros=centros, departamentos=departamentos, subdepartamentos=subdepartamentos, categorias=categorias)


@rrhh_bp.route('/puestos/<int:id>/desactivar', methods=['POST'])
@login_required
@_require_rrhh_global
def desactivar_puesto_operativo(id):
    conn=get_db(); _ensure_editable_schema(conn)
    _safe_update(conn,'puestos_operativos',id,{'activo':0},request.form.get('motivo_cambio') or 'Puesto desactivado')
    conn.commit(); conn.close(); flash('Puesto desactivado.', 'success'); return redirect(url_for('rrhh.lista_puestos_operativos'))


@rrhh_bp.route('/centros/<int:id>/editar', methods=['GET','POST'])
@login_required
@_require_rrhh_global
def editar_centro_trabajo(id):
    conn=get_db(); _ensure_editable_schema(conn)
    centro=_get_row(conn,'centros_trabajo',id)
    if not centro:
        conn.close(); flash('Centro no encontrado.', 'warning'); return redirect(url_for('rrhh.centros_trabajo'))
    if request.method=='POST':
        _safe_update(conn,'centros_trabajo',id,{'nombre':request.form.get('nombre'),'codigo':request.form.get('codigo'),'direccion':request.form.get('direccion'),'descripcion':request.form.get('descripcion'),'activo':1 if request.form.get('activo') else 0},request.form.get('motivo_cambio') or 'Actualizacion del centro')
        conn.commit(); conn.close(); flash('Centro actualizado.', 'success'); return redirect(url_for('rrhh.centros_trabajo'))
    conn.close(); return render_template('rrhh/centro_form.html', centro=centro)


@rrhh_bp.route('/centros/editar/<int:id>', methods=['GET','POST'])
@login_required
@_require_rrhh_global
def editar_centro_trabajo_alias(id):
    return editar_centro_trabajo(id)


@rrhh_bp.route('/centros/<int:id>/desactivar', methods=['POST'])
@login_required
@_require_rrhh_global
def desactivar_centro_trabajo(id):
    conn=get_db(); _ensure_editable_schema(conn)
    _safe_update(conn,'centros_trabajo',id,{'activo':0},request.form.get('motivo_cambio') or 'Centro desactivado')
    conn.commit(); conn.close(); flash('Centro desactivado.', 'success'); return redirect(url_for('rrhh.centros_trabajo'))


@rrhh_bp.route('/necesidades/<int:id>/editar', methods=['GET','POST'])
@login_required
@_require_rrhh_global
def editar_necesidad_operativa(id):
    conn=get_db(); _ensure_editable_schema(conn); _ensure_cuadrantes_schema(conn)
    necesidad=_get_row(conn,'necesidades_operativas',id)
    if not necesidad:
        conn.close(); flash('Necesidad no encontrada.', 'warning'); return redirect(url_for('rrhh.necesidades_operativas'))
    if request.method=='POST':
        campos = dict((k, request.form.get(k)) for k in [
            'centro_id','departamento_id','subdepartamento_id','puesto_id','dia_semana','personas_necesarias',
            'observaciones','hora_inicio','hora_fin','tipo_turno','horas_dia','plantilla_objetivo',
            'cobertura_libranzas','etiqueta_operativa'
        ])
        for k in ['centro_id','departamento_id','subdepartamento_id','puesto_id']:
            if campos.get(k)=='': campos[k]=None
        for k in ['hora_inicio','hora_fin','tipo_turno','cobertura_libranzas','etiqueta_operativa']:
            if campos.get(k)=='': campos[k]=None
        campos['horas_dia'] = str(_to_float(campos.get('horas_dia'), 0))
        campos['plantilla_objetivo'] = str(_to_float(campos.get('plantilla_objetivo'), 0))
        campos['activo'] = str(1 if request.form.get('activo') else 0)
        _safe_update(conn,'necesidades_operativas',id,campos,request.form.get('motivo_cambio'))
        conn.commit(); conn.close(); flash('Necesidad operativa actualizada.', 'success'); return redirect(url_for('rrhh.necesidades_operativas'))
    centros=_centros_select(conn); departamentos=_departamentos_select(conn); subdepartamentos=_subdepartamentos_select(conn); puestos=_puestos_select(conn); conn.close()
    return render_template('rrhh/necesidad_form.html', necesidad=necesidad, centros=centros, departamentos=departamentos, subdepartamentos=subdepartamentos, puestos=puestos, dias_semana=_DIAS_SEMANA)


@rrhh_bp.route('/necesidades/<int:id>/desactivar', methods=['POST'])
@login_required
@_require_rrhh_global
def desactivar_necesidad_operativa(id):
    conn=get_db(); _ensure_editable_schema(conn)
    _safe_update(conn,'necesidades_operativas',id,{'activo':0},request.form.get('motivo_cambio') or 'Necesidad desactivada')
    conn.commit(); conn.close(); flash('Necesidad desactivada.', 'success'); return redirect(url_for('rrhh.necesidades_operativas'))


@rrhh_bp.route('/fichajes/<int:id>/corregir', methods=['GET','POST'])
@login_required
@_require_rrhh_global
def corregir_fichaje(id):
    conn=get_db(); _ensure_control_tiempo_schema(conn)
    fichaje=_get_row(conn,'fichajes',id)
    if not fichaje:
        conn.close(); flash('Fichaje no encontrado.', 'warning'); return redirect(url_for('rrhh.lista_fichajes'))
    if request.method=='POST':
        fecha_objetivo = request.form.get('fecha_hora') or fichaje['fecha_hora']
        if _periodo_cerrado_para_fecha(conn, fichaje['fecha_hora']) or _periodo_cerrado_para_fecha(conn, fecha_objetivo):
            conn.close(); flash('Ese periodo esta cerrado. Reabre el cierre antes de corregir.', 'warning'); return redirect(url_for('rrhh.lista_fichajes'))
        if (fichaje['estado'] if 'estado' in fichaje.keys() else 'activo') == 'anulado':
            conn.close(); flash('Este fichaje ya esta anulado.', 'warning'); return redirect(url_for('rrhh.lista_fichajes'))
        motivo=request.form.get('motivo_cambio') or 'Correccion manual de fichaje'
        tipo = request.form.get('tipo')
        if tipo not in {'entrada','salida','pausa','reanudacion'}:
            tipo = fichaje['tipo']
        nuevo_id = _crear_correccion_manual_fichaje(
            conn,
            fichaje,
            request.form.get('empleado_id') or fichaje['empleado_id'],
            tipo,
            fecha_objetivo,
            request.form.get('observaciones'),
            motivo,
        )
        conn.commit(); conn.close(); flash(f'Fichaje corregido. Original anulado y nuevo registro #{nuevo_id} creado.', 'success'); return redirect(url_for('rrhh.lista_fichajes'))
    empleados=_empleados_select(conn); conn.close(); return render_template('rrhh/fichaje_form.html', fichaje=fichaje, empleados=empleados)


@rrhh_bp.route('/historial')
@login_required
@_require_rrhh_global
def historial_rrhh():
    conn=get_db(); _ensure_editable_schema(conn)
    historial=conn.execute('SELECT * FROM rrhh_historial_cambios WHERE empresa_id=? ORDER BY fecha DESC, id DESC LIMIT 500',(_empresa_id(),)).fetchall()
    conn.close(); return render_template('rrhh/historial.html', historial=historial)


# ============================================================
# FIX CUADRANTE REAL — guardado explícito desde la planilla
# ============================================================
@rrhh_bp.route('/cuadrantes/real/turno/<int:id>/guardar', methods=['POST'])
@login_required
@_require_cuadrantes
def guardar_turno_cuadrante_real(id):
    """Guarda cambios del turno directamente desde la planilla de cuadrante real.
    Es una ruta explícita para evitar que una edición parezca guardada pero no actualice la tabla.
    """
    conn = get_db()
    _ensure_editable_schema(conn)
    turno = _get_row(conn, 'turnos_rrhh', id)
    if not turno:
        conn.close()
        flash('Turno no encontrado.', 'warning')
        return redirect(url_for('rrhh.cuadrante_real'))

    campos = {
        'empleado_id': request.form.get('empleado_id') or turno['empleado_id'],
        'fecha': request.form.get('fecha') or turno['fecha'],
        'hora_inicio': request.form.get('hora_inicio') or turno['hora_inicio'],
        'hora_fin': request.form.get('hora_fin') or turno['hora_fin'],
        'tipo': request.form.get('tipo') or turno['tipo'] or 'real',
        'centro_id': request.form.get('centro_id') or None,
        'departamento_id': request.form.get('departamento_id') or None,
        'subdepartamento_id': request.form.get('subdepartamento_id') or None,
        'puesto_id': request.form.get('puesto_id') or None,
        'estado': request.form.get('estado') or turno['estado'] or 'planificado',
        'observaciones': request.form.get('observaciones') or '',
    }
    _safe_update(conn, 'turnos_rrhh', id, campos, request.form.get('motivo_cambio') or 'Editado desde cuadrante real')
    conn.commit()
    conn.close()
    flash('Turno actualizado en el cuadrante.', 'success')
    return _redirect_cuadrante_real_con_filtros(campos.get('fecha'))

# ============================================================
# IMPORTADOR RRHH DESDE EXCEL
# ============================================================
@rrhh_bp.route("/importar-excel", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def importar_excel_rrhh():
    from pathlib import Path
    from werkzeug.utils import secure_filename
    from blueprints.rrhh.importador_excel import importar_rrhh_desde_excel

    resultado = None
    if request.method == "POST":
        archivo = request.files.get("archivo")
        filename = archivo.filename if archivo and archivo.filename else ""
        if not archivo or not filename.lower().endswith(".xlsx"):
            flash("Sube un archivo Excel .xlsx válido.", "warning")
            return redirect(url_for("rrhh.importar_excel_rrhh"))

        upload_dir = Path("uploads") / "rrhh_imports"
        upload_dir.mkdir(parents=True, exist_ok=True)
        ruta = upload_dir / secure_filename(filename)
        archivo.save(str(ruta))

        limite_raw = request.form.get("limite")
        limite = int(limite_raw) if limite_raw and limite_raw.isdigit() else None
        conn = get_db()
        resultado = importar_rrhh_desde_excel(
            conn,
            str(ruta),
            empresa_id=_empresa_id(),
            modo=request.form.get("modo") or "socios",
            hoja_generica=request.form.get("hoja") or "EMPLEADOS",
            limite=limite,
            solo_activos=request.form.get("solo_activos") == "1",
            estrategia_operativa=request.form.get("estrategia_operativa") or "sumatorio",
            tipo_empleado=request.form.get("tipo_empleado") or "plantilla",
        )
        conn.commit()
        conn.close()
        if resultado.get("errores"):
            flash("Importación finalizada con errores.", "danger")
        else:
            flash("Importación RRHH completada.", "success")

    return render_template("rrhh/importar_excel.html", resultado=resultado)
@rrhh_bp.route("/cuadrantes/real/imprimir")
@login_required
def imprimir_cuadrante_real():
    """
    Impresión profesional del cuadrante real.
    Reglas:
    - Respeta filtros activos: semana, centro, departamento, subdepartamento y puesto.
    - Carga trabajadores por ficha y también correturnos con turno dentro del filtro.
    - Carga horarios reales.
    - El resumen cuenta PERSONAS únicas por día y categoría.
    """
    conn = get_db()
    _ensure_cuadrantes_schema(conn)

    empresa_id = _empresa_id()
    inicio, fin = _rango_semana(request.args.get("fecha"))

    centro_id = request.args.get("centro_id") or centro_activo_id() or ""
    departamento_id = request.args.get("departamento_id") or ""
    subdepartamento_id = request.args.get("subdepartamento_id") or ""
    puesto_id = request.args.get("puesto_id") or ""

    # Convertir a int si es posible
    try:
        centro_id = int(centro_id) if centro_id else None
    except ValueError:
        centro_id = None
    try:
        departamento_id = int(departamento_id) if departamento_id else None
    except ValueError:
        departamento_id = None
    try:
        subdepartamento_id = int(subdepartamento_id) if subdepartamento_id else None
    except ValueError:
        subdepartamento_id = None
    try:
        puesto_id = int(puesto_id) if puesto_id else None
    except ValueError:
        puesto_id = None

    if not _usuario_es_global_rrhh() and _session_departamento_id():
        departamento_id = _session_departamento_id()

    departamento_id, subdepartamento_id = _normalizar_departamento_subdepartamento(
        conn,
        empresa_id,
        departamento_id,
        subdepartamento_id,
    )
    filtro_activo = bool(centro_id or departamento_id or subdepartamento_id or puesto_id)

    # ============================================================
    # 1. TRABAJADORES BASE POR FICHA
    # ============================================================
    filtros_base = ["e.empresa_id = ?", "COALESCE(e.activo, 1) = 1"]
    params_base = [empresa_id]

    if centro_id is not None:
        dep_ids_centro = ids_departamentos_centro(conn, empresa_id, centro_id)
        pv_ids_centro = ids_puntos_venta_centro(conn, empresa_id, centro_id)
        condiciones_centro = []
        if dep_ids_centro:
            condiciones_centro.append(f"e.departamento_id IN ({','.join('?' for _ in dep_ids_centro)})")
            params_base.extend(dep_ids_centro)
        if pv_ids_centro:
            condiciones_centro.append(f"e.punto_venta_id IN ({','.join('?' for _ in pv_ids_centro)})")
            params_base.extend(pv_ids_centro)
        filtros_base.append(f"({' OR '.join(condiciones_centro)})" if condiciones_centro else "1 = 0")

    if departamento_id is not None:
        filtros_base.append("e.departamento_id = ?")
        params_base.append(departamento_id)
    if subdepartamento_id is not None:
        filtros_base.append("e.subdepartamento_id = ?")
        params_base.append(subdepartamento_id)

    empleados_base = []
    if puesto_id is None:
        empleados_base = conn.execute(
            f"""
            SELECT
                e.id,
                e.nombre,
                e.apellidos,
                e.departamento_id,
                e.subdepartamento_id,
                e.categoria_id,
                d.nombre AS departamento_nombre,
                sd.nombre AS subdepartamento_nombre,
                c.nombre AS categoria_nombre
            FROM empleados e
            LEFT JOIN departamentos d ON e.departamento_id = d.id
            LEFT JOIN subdepartamentos sd ON e.subdepartamento_id = sd.id
            LEFT JOIN categorias_laborales c ON e.categoria_id = c.id
            WHERE {' AND '.join(filtros_base)}
            """,
            params_base,
        ).fetchall()

    empleados_turno = []
    if filtro_activo and not ((departamento_id is not None or subdepartamento_id is not None) and centro_id is None and puesto_id is None):
        filtros_turno = [
            "t.empresa_id = ?",
            "date(t.fecha) BETWEEN date(?) AND date(?)",
            "COALESCE(t.estado, 'planificado') NOT IN ('anulado','cancelado','borrado')",
        ]
        params_turno = [empresa_id, inicio.isoformat(), fin.isoformat()]

        if centro_id is not None:
            filtros_turno.append("(t.centro_id = ? OR p.centro_id = ?)")
            params_turno.extend([centro_id, centro_id])

        if departamento_id is not None:
            filtros_turno.append("COALESCE(t.departamento_id, p.departamento_id, e.departamento_id) = ?")
            params_turno.append(departamento_id)

        if subdepartamento_id is not None:
            filtros_turno.append("COALESCE(t.subdepartamento_id, p.subdepartamento_id, e.subdepartamento_id) = ?")
            params_turno.append(subdepartamento_id)

        if puesto_id is not None:
            filtros_turno.append("(t.puesto_id = ? OR p.id = ?)")
            params_turno.extend([puesto_id, puesto_id])

        if not _usuario_es_global_rrhh() and _session_departamento_id():
            filtros_turno.append("COALESCE(t.departamento_id, p.departamento_id, e.departamento_id) = ?")
            params_turno.append(_session_departamento_id())

        empleados_turno = conn.execute(
            f"""
            SELECT DISTINCT
                e.id,
                e.nombre,
                e.apellidos,
                e.departamento_id,
                e.subdepartamento_id,
                e.categoria_id,
                d.nombre AS departamento_nombre,
                sd.nombre AS subdepartamento_nombre,
                c.nombre AS categoria_nombre
            FROM turnos_rrhh t
            JOIN empleados e ON t.empleado_id = e.id
            LEFT JOIN puestos_operativos p ON t.puesto_id = p.id
            LEFT JOIN departamentos d ON e.departamento_id = d.id
            LEFT JOIN subdepartamentos sd ON e.subdepartamento_id = sd.id
            LEFT JOIN categorias_laborales c ON e.categoria_id = c.id
            WHERE {' AND '.join(filtros_turno)}
              AND e.empresa_id = ?
              AND COALESCE(e.activo,1)=1
            """,
            params_turno + [empresa_id],
        ).fetchall()

    empleados_map = {}
    for e in list(empleados_base) + list(empleados_turno):
        empleados_map[e["id"]] = e

    empleados_finales = sorted(
        empleados_map.values(),
        key=lambda e: (
            (e["departamento_nombre"] or "").lower(),
            (e["subdepartamento_nombre"] or "").lower(),
            (e["nombre"] or "").lower(),
            (e["apellidos"] or "").lower(),
        ),
    )

    if centro_id is None and puesto_id is None:
        if subdepartamento_id is not None:
            empleados_finales = [e for e in empleados_finales if e["subdepartamento_id"] == subdepartamento_id]
        elif departamento_id is not None:
            empleados_finales = [e for e in empleados_finales if e["departamento_id"] == departamento_id]

    empleado_ids = [e["id"] for e in empleados_finales]
    empleados_base_ids = {e["id"] for e in empleados_base}

    # ============================================================
    # TURNOS DE LA SEMANA
    # ============================================================
    turnos = []
    if empleado_ids:
        placeholders = ",".join(["?"] * len(empleado_ids))
        turnos_raw = conn.execute(
            f"""
            SELECT
                t.*,
                TRIM(e.nombre || ' ' || COALESCE(e.apellidos,'')) AS empleado_nombre,
                e.departamento_id AS empleado_departamento_id,
                e.subdepartamento_id AS empleado_subdepartamento_id,
                e.categoria_id AS empleado_categoria_id,
                COALESCE(t.departamento_id, p.departamento_id, e.departamento_id) AS departamento_efectivo_id,
                COALESCE(t.subdepartamento_id, p.subdepartamento_id, e.subdepartamento_id) AS subdepartamento_efectivo_id,
                CASE WHEN t.centro_id IS NOT NULL THEN ct.nombre ELSE NULL END AS centro_nombre,
                CASE WHEN t.departamento_id IS NOT NULL THEN d_t.nombre ELSE NULL END AS departamento_nombre,
                CASE WHEN t.subdepartamento_id IS NOT NULL THEN sd_t.nombre ELSE NULL END AS subdepartamento_nombre,
                p.nombre AS puesto_nombre,
                p.categoria_id AS puesto_categoria_id,
                p.centro_id AS puesto_centro_id,
                p.departamento_id AS puesto_departamento_id,
                p.subdepartamento_id AS puesto_subdepartamento_id
            FROM turnos_rrhh t
            JOIN empleados e ON t.empleado_id = e.id
            LEFT JOIN puestos_operativos p ON t.puesto_id = p.id
            LEFT JOIN centros_trabajo ct ON COALESCE(t.centro_id, p.centro_id) = ct.id
            LEFT JOIN departamentos d_t ON t.departamento_id = d_t.id
            LEFT JOIN departamentos d_p ON p.departamento_id = d_p.id
            LEFT JOIN departamentos d_e ON e.departamento_id = d_e.id
            LEFT JOIN subdepartamentos sd_t ON t.subdepartamento_id = sd_t.id
            LEFT JOIN subdepartamentos sd_p ON p.subdepartamento_id = sd_p.id
            LEFT JOIN subdepartamentos sd_e ON e.subdepartamento_id = sd_e.id
            WHERE t.empresa_id = ?
              AND t.empleado_id IN ({placeholders})
              AND date(t.fecha) BETWEEN date(?) AND date(?)
              AND COALESCE(t.estado, 'planificado') NOT IN ('anulado', 'cancelado', 'borrado')
            ORDER BY date(t.fecha), e.nombre, t.hora_inicio
            """,
            [empresa_id] + empleado_ids + [inicio.isoformat(), fin.isoformat()],
        ).fetchall()

        def _eq(valor, filtro):
            return str(valor or "") == str(filtro or "")

        def _turno_tiene_centro(turno):
            return bool(turno["centro_id"] or turno["puesto_centro_id"])

        def _turno_tiene_subdepartamento(turno):
            return bool(turno["subdepartamento_id"] or turno["puesto_subdepartamento_id"])

        def _turno_encaja_operativo(turno):
            if centro_id:
                if _turno_tiene_centro(turno):
                    if not (_eq(turno["centro_id"], centro_id) or _eq(turno["puesto_centro_id"], centro_id)):
                        return False

            if departamento_id:
                if turno["departamento_id"] is not None or turno["puesto_departamento_id"] is not None:
                    if not (_eq(turno["departamento_id"], departamento_id) or _eq(turno["puesto_departamento_id"], departamento_id)):
                        return False
                elif not _eq(turno["empleado_departamento_id"], departamento_id):
                    return False

            if subdepartamento_id:
                if _turno_tiene_subdepartamento(turno):
                    if not (_eq(turno["subdepartamento_id"], subdepartamento_id) or _eq(turno["puesto_subdepartamento_id"], subdepartamento_id)):
                        return False
                elif not _eq(turno["empleado_subdepartamento_id"], subdepartamento_id):
                    return False

            if puesto_id and not _eq(turno["puesto_id"], puesto_id):
                return False

            return True

        for t in turnos_raw:
            if not filtro_activo:
                turnos.append(t)
                continue

            if not centro_id and not puesto_id and t["empleado_id"] in empleados_base_ids:
                if _turno_encaja_operativo(t):
                    turnos.append(t)
                continue

            if centro_id and t["empleado_id"] in empleados_base_ids and not _turno_tiene_centro(t):
                if _turno_encaja_operativo(t):
                    turnos.append(t)
                continue

            if _turno_encaja_operativo(t):
                turnos.append(t)

    vacaciones = []
    if empleado_ids:
        placeholders = ",".join(["?"] * len(empleado_ids))
        vacaciones = conn.execute(
            f"""
            SELECT
                v.*,
                TRIM(e.nombre || ' ' || COALESCE(e.apellidos,'')) AS empleado_nombre
            FROM vacaciones_rrhh v
            JOIN empleados e ON v.empleado_id = e.id
            WHERE v.empresa_id = ?
              AND v.empleado_id IN ({placeholders})
              AND date(v.fecha_inicio) <= date(?)
              AND date(v.fecha_fin) >= date(?)
              AND v.estado IN ('solicitada','aprobada')
            ORDER BY v.fecha_inicio
            """,
            [empresa_id] + empleado_ids + [fin.isoformat(), inicio.isoformat()],
        ).fetchall()

    necesidades_params = [empresa_id]
    necesidades_where = ["n.empresa_id = ?", "COALESCE(n.activo,1)=1"]

    if centro_id:
        necesidades_where.append("n.centro_id = ?")
        necesidades_params.append(centro_id)
    if departamento_id:
        necesidades_where.append("n.departamento_id = ?")
        necesidades_params.append(departamento_id)
    if subdepartamento_id:
        necesidades_where.append("n.subdepartamento_id = ?")
        necesidades_params.append(subdepartamento_id)
    if puesto_id:
        necesidades_where.append("n.puesto_id = ?")
        necesidades_params.append(puesto_id)

    necesidades = conn.execute(
        f"""
        SELECT
            n.*,
            ct.nombre AS centro_nombre,
            d.nombre AS departamento_nombre,
            sd.nombre AS subdepartamento_nombre,
            p.nombre AS puesto_nombre,
            p.categoria_id AS puesto_categoria_id
        FROM necesidades_operativas n
        LEFT JOIN centros_trabajo ct ON n.centro_id = ct.id
        LEFT JOIN departamentos d ON n.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON n.subdepartamento_id = sd.id
        LEFT JOIN puestos_operativos p ON n.puesto_id = p.id
        WHERE {' AND '.join(necesidades_where)}
        ORDER BY n.dia_semana, COALESCE(ct.nombre,''), COALESCE(d.nombre,''), COALESCE(sd.nombre,''), COALESCE(p.nombre,'')
        """,
        necesidades_params,
    ).fetchall()

    asignados = {}
    if necesidades:
        turnos_contables = []
        for t in turnos:
            td = dict(t)
            estado = (td.get("estado") or "planificado").strip().lower()
            tipo = (td.get("tipo") or "real").strip().lower()
            if estado in {"anulado", "cancelado", "borrado"}:
                continue
            if tipo in {"libre", "descanso", "dia_libre", "día libre"}:
                continue
            try:
                td["_weekday"] = date.fromisoformat(str(td["fecha"]).split(" ")[0].split("T")[0]).weekday()
            except Exception:
                continue
            td["_centro_id"] = td.get("centro_id") or td.get("puesto_centro_id") or 0
            td["_departamento_id"] = td.get("departamento_efectivo_id") or td.get("departamento_id") or td.get("puesto_departamento_id") or td.get("empleado_departamento_id") or 0
            td["_subdepartamento_id"] = td.get("subdepartamento_efectivo_id") or td.get("subdepartamento_id") or td.get("puesto_subdepartamento_id") or td.get("empleado_subdepartamento_id") or 0
            td["_puesto_id"] = td.get("puesto_id") or 0
            td["_empleado_categoria_id"] = td.get("empleado_categoria_id") or 0
            turnos_contables.append(td)

        def _mismo(a, b):
            return str(a or 0) == str(b or 0)

        for n in necesidades:
            dia = n["dia_semana"]
            centro = n["centro_id"] or 0
            depto = n["departamento_id"] or 0
            subdepto = n["subdepartamento_id"] or 0
            puesto = n["puesto_id"] or 0
            puesto_categoria = n["puesto_categoria_id"] or 0
            empleados_contados = set()

            for t in turnos_contables:
                if t.get("_weekday") != dia:
                    continue
                empleado_turno = t.get("empleado_id")
                if empleado_turno in empleados_contados:
                    continue
                if centro and t.get("_centro_id") and not _mismo(t.get("_centro_id"), centro):
                    continue
                if subdepto:
                    if not _mismo(t.get("_subdepartamento_id"), subdepto):
                        continue
                elif depto:
                    if not _mismo(t.get("_departamento_id"), depto):
                        continue
                if puesto:
                    if _mismo(t.get("_puesto_id"), puesto):
                        empleados_contados.add(empleado_turno)
                    elif not t.get("_puesto_id") and puesto_categoria and _mismo(t.get("_empleado_categoria_id"), puesto_categoria):
                        empleados_contados.add(empleado_turno)
                    else:
                        continue
                else:
                    empleados_contados.add(empleado_turno)

            asignados[(dia, puesto, depto, subdepto, centro, n["id"])] = len(empleados_contados)
            asignados[(dia, puesto, depto, subdepto)] = len(empleados_contados)

    def _nombre(tabla, registro_id):
        if not registro_id:
            return ""
        try:
            row = conn.execute(
                f"SELECT nombre FROM {tabla} WHERE id = ? AND empresa_id = ?",
                (registro_id, empresa_id),
            ).fetchone()
            return row["nombre"] if row else ""
        except Exception:
            return ""

    filtro_centro = _nombre("centros_trabajo", centro_id)
    filtro_departamento = _nombre("departamentos", departamento_id)
    filtro_subdepartamento = _nombre("subdepartamentos", subdepartamento_id)
    filtro_puesto = _nombre("puestos_operativos", puesto_id)

    dias = [inicio + timedelta(days=i) for i in range(7)]
    turnos_por_empleado_dia = {}
    for t in turnos:
        turno = dict(t)
        f_key = str(turno["fecha"]).split(" ")[0].split("T")[0]
        turnos_por_empleado_dia.setdefault((turno["empleado_id"], f_key), []).append(turno)

    vacaciones_por_empleado_dia = {}
    for v in vacaciones:
        try:
            vi = date.fromisoformat(v["fecha_inicio"])
            vf = date.fromisoformat(v["fecha_fin"])
        except Exception:
            continue
        for dia in dias:
            if vi <= dia <= vf:
                vacaciones_por_empleado_dia.setdefault((v["empleado_id"], dia.isoformat()), []).append(dict(v))

    resumen_sets = {}
    total_dia_sets = {dia.isoformat(): set() for dia in dias}

    def _categoria_label(valor):
        return (valor or "Sin categoría").strip()

    def _categoria_key(valor):
        return _categoria_label(valor).casefold()

    categorias_vistas = {}
    for e in empleados_finales:
        key = _categoria_key(e["categoria_nombre"])
        categorias_vistas.setdefault(key, _categoria_label(e["categoria_nombre"]))

    for t in turnos:
        turno = dict(t)
        estado = (turno.get("estado") or "planificado").strip().lower()
        tipo = (turno.get("tipo") or "real").strip().lower()
        if estado in {"anulado", "cancelado", "borrado"} or tipo in {"libre", "descanso", "dia_libre", "día libre"}:
            continue
        fecha = str(turno["fecha"]).split(" ")[0].split("T")[0]
        if fecha not in total_dia_sets:
            continue
        categoria = _categoria_label(turno.get("puesto_nombre") or turno.get("categoria_nombre"))
        key = _categoria_key(categoria)
        categorias_vistas.setdefault(key, categoria)
        resumen_sets.setdefault(key, {}).setdefault(fecha, set()).add(turno["empleado_id"])
        total_dia_sets[fecha].add(turno["empleado_id"])

    for key in categorias_vistas:
        resumen_sets.setdefault(key, {})
    categoria_label_map = dict(categorias_vistas)

    resumen_categorias = []
    for categoria_key in sorted(resumen_sets.keys(), key=lambda x: x.lower()):
        fila = {"categoria": categoria_label_map.get(categoria_key, categoria_key), "dias": {}, "total_semana": 0}
        empleados_semana = set()
        for dia in dias:
            fecha = dia.isoformat()
            empleados_dia = resumen_sets.get(categoria_key, {}).get(fecha, set())
            fila["dias"][fecha] = len(empleados_dia)
            empleados_semana.update(empleados_dia)
        fila["total_semana"] = len(empleados_semana)
        resumen_categorias.append(fila)

    total_por_dia = {}
    for dia in dias:
        fecha = dia.isoformat()
        total_por_dia[fecha] = len(total_dia_sets.get(fecha, set()))
    total_semana_operativos = len(set().union(*total_dia_sets.values())) if total_dia_sets else 0

    conn.close()

    return render_template(
        "rrhh/cuadrante_real_print.html",
        empleados=empleados_finales,
        dias=dias,
        inicio=inicio,
        fin=fin,
        turnos_por_empleado_dia=turnos_por_empleado_dia,
        vacaciones_por_empleado_dia=vacaciones_por_empleado_dia,
        filtro_centro=filtro_centro,
        filtro_departamento=filtro_departamento,
        filtro_subdepartamento=filtro_subdepartamento,
        filtro_puesto=filtro_puesto,
        resumen_categorias=resumen_categorias,
        total_por_dia=total_por_dia,
        total_semana_operativos=total_semana_operativos,
        necesidades=necesidades,
        asignados=asignados,
        generado_en=datetime.now().strftime("%d/%m/%Y %H:%M"),
    )



# ============================================================
# RRHH — CONTROL DE TIEMPO, ALERTAS, EXTRAS Y COPIA SEMANAL
# ============================================================

def _ensure_control_tiempo_schema(conn):
    """Añade la capa de control horario sin borrar ni cambiar lo existente."""
    _ensure_editable_schema(conn)

    # Fichajes: columnas para trazabilidad legal y aprobación de desviaciones.
    for col in [
        "turno_id INTEGER",
        "validado_por INTEGER",
        "fecha_validacion TEXT",
        "minutos_desviacion INTEGER DEFAULT 0",
        "aprobado_extra INTEGER DEFAULT 0",
        "motivo_validacion TEXT",
        "fichaje_origen_id INTEGER",
        "corregido_por INTEGER",
        "fecha_correccion TEXT",
        "motivo_correccion TEXT",
        "ip_correccion TEXT",
    ]:
        _add_column_if_missing(conn, "fichajes", col)

    # Resumen pre-nómina: horas aprobadas por jefe/RRHH y horas reales no aprobadas.
    for col in [
        "horas_extra_aprobadas REAL DEFAULT 0",
        "horas_extra_no_aprobadas REAL DEFAULT 0",
        "minutos_extra_aprobados INTEGER DEFAULT 0",
        "minutos_extra_no_aprobados INTEGER DEFAULT 0",
    ]:
        _add_column_if_missing(conn, "resumen_prenomina", col)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tiempo_extra_rrhh (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            turno_id INTEGER,
            empleado_id INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            tipo_exceso TEXT NOT NULL,
            minutos INTEGER DEFAULT 0,
            estado TEXT DEFAULT 'pendiente',
            motivo TEXT,
            aprobado_por INTEGER,
            fecha_aprobacion TEXT,
            empresa_id INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col in [
        "turno_id INTEGER",
        "empleado_id INTEGER",
        "fecha TEXT",
        "tipo_exceso TEXT",
        "minutos INTEGER DEFAULT 0",
        "estado TEXT DEFAULT 'pendiente'",
        "motivo TEXT",
        "aprobado_por INTEGER",
        "fecha_aprobacion TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP",
    ]:
        _add_column_if_missing(conn, "tiempo_extra_rrhh", col)

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tiempo_extra_rrhh_unico
        ON tiempo_extra_rrhh (empresa_id, turno_id, empleado_id, fecha, tipo_exceso)
        """
    )

    permisos_extra = [
        ("ver_alertas_rrhh", "Ver alertas de fichajes, ausencias y cuadrantes"),
        ("aprobar_tiempo_extra", "Aprobar o rechazar tiempo extra generado por fichajes"),
        ("copiar_cuadrantes", "Copiar cuadrantes de una semana a otra"),
    ]
    for nombre, descripcion in permisos_extra:
        conn.execute("INSERT OR IGNORE INTO permisos (nombre, descripcion) VALUES (?, ?)", (nombre, descripcion))
    conn.commit()


def _parse_dt_local(valor):
    if not valor:
        return None
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "").replace("T", " "))
    except Exception:
        try:
            return datetime.strptime(str(valor), "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None


def _parse_date(valor):
    """Convierte una fecha de formulario sin romper el laboratorio RRHH."""
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor).strip()[:10])
    except (TypeError, ValueError):
        return None


def _parse_turno_dt(fecha, hora, fin=False):
    if not fecha or not hora:
        return None
    try:
        dt = datetime.fromisoformat(f"{fecha} {hora}")
        return dt
    except Exception:
        return None


def _minutos_diff(a, b):
    if not a or not b:
        return 0
    return int(round((a - b).total_seconds() / 60))


def _obtener_fichajes_dia(conn, empleado_id, fecha, empresa_id):
    filas = conn.execute(
        """
        SELECT *
        FROM fichajes
        WHERE empleado_id = ?
          AND empresa_id = ?
          AND date(fecha_hora) = date(?)
          AND COALESCE(estado, 'activo') NOT IN ('anulado', 'cancelado', 'borrado')
        ORDER BY datetime(fecha_hora) ASC, id ASC
        """,
        (empleado_id, empresa_id, fecha),
    ).fetchall()

    entradas = []
    salidas = []
    pausas = []
    reanudaciones = []
    for f in filas:
        tipo = (f["tipo"] or "").strip().lower()
        dt = _parse_dt_local(f["fecha_hora"])
        if not dt:
            continue
        if tipo == "entrada":
            entradas.append((f, dt))
        elif tipo == "salida":
            salidas.append((f, dt))
        elif tipo == "pausa":
            pausas.append((f, dt))
        elif tipo == "reanudacion":
            reanudaciones.append((f, dt))

    return {
        "filas": filas,
        "entrada": entradas[0] if entradas else None,
        "salida": salidas[-1] if salidas else None,
        "pausas": pausas,
        "reanudaciones": reanudaciones,
    }


def _ausencia_empleado_en_fecha(conn, empleado_id, fecha, empresa_id):
    return conn.execute(
        """
        SELECT v.*, doc.nombre AS documento_nombre, doc.archivo AS documento_archivo
        FROM vacaciones_rrhh v
        LEFT JOIN documentos_rrhh doc ON v.documento_id = doc.id
        WHERE v.empleado_id = ?
          AND v.empresa_id = ?
          AND date(v.fecha_inicio) <= date(?)
          AND date(v.fecha_fin) >= date(?)
          AND v.estado IN ('solicitada', 'aprobada')
        ORDER BY CASE WHEN v.estado = 'aprobada' THEN 0 ELSE 1 END, v.id DESC
        LIMIT 1
        """,
        (empleado_id, empresa_id, fecha, fecha),
    ).fetchone()


def _guardar_alerta_tiempo_extra(conn, turno, tipo_exceso, minutos, motivo):
    if not turno or minutos <= 0:
        return None
    empresa_id = _empresa_id()
    existente = conn.execute(
        """
        SELECT *
        FROM tiempo_extra_rrhh
        WHERE empresa_id = ?
          AND turno_id = ?
          AND empleado_id = ?
          AND fecha = ?
          AND tipo_exceso = ?
        LIMIT 1
        """,
        (empresa_id, turno["id"], turno["empleado_id"], turno["fecha"], tipo_exceso),
    ).fetchone()
    if existente:
        if existente["estado"] == "pendiente" and int(existente["minutos"] or 0) != int(minutos):
            conn.execute(
                """
                UPDATE tiempo_extra_rrhh
                SET minutos = ?, motivo = ?, fecha_creacion = CURRENT_TIMESTAMP
                WHERE id = ? AND empresa_id = ?
                """,
                (minutos, motivo, existente["id"], empresa_id),
            )
        return existente

    conn.execute(
        """
        INSERT INTO tiempo_extra_rrhh
        (turno_id, empleado_id, fecha, tipo_exceso, minutos, estado, motivo, empresa_id)
        VALUES (?, ?, ?, ?, ?, 'pendiente', ?, ?)
        """,
        (turno["id"], turno["empleado_id"], turno["fecha"], tipo_exceso, minutos, motivo, empresa_id),
    )
    return conn.execute("SELECT * FROM tiempo_extra_rrhh WHERE id = last_insert_rowid()").fetchone()


def _estado_extra(conn, turno_id, empleado_id, fecha, tipo_exceso):
    return conn.execute(
        """
        SELECT *
        FROM tiempo_extra_rrhh
        WHERE empresa_id = ?
          AND turno_id = ?
          AND empleado_id = ?
          AND fecha = ?
          AND tipo_exceso = ?
        LIMIT 1
        """,
        (_empresa_id(), turno_id, empleado_id, fecha, tipo_exceso),
    ).fetchone()


def _generar_alertas_fichajes(conn, fecha_txt, departamento_id="", subdepartamento_id="", centro_id=""):
    """Genera alertas desde cuadrante + fichajes + ausencias.
    No modifica turnos. Solo crea/actualiza registros pendientes de tiempo extra.
    """
    _ensure_control_tiempo_schema(conn)
    empresa_id = _empresa_id()
    centro_id = _to_int(centro_id, 0) if centro_id not in (None, "") else ""
    departamento_id = _to_int(departamento_id, 0) if departamento_id not in (None, "") else ""
    subdepartamento_id = _to_int(subdepartamento_id, 0) if subdepartamento_id not in (None, "") else ""

    filtros = [
        "t.empresa_id = ?",
        "date(t.fecha) = date(?)",
        "COALESCE(t.estado, 'planificado') NOT IN ('anulado', 'cancelado', 'borrado')",
    ]
    params = [empresa_id, fecha_txt]

    if departamento_id:
        filtros.append("COALESCE(t.departamento_id, p.departamento_id, e.departamento_id) = ?")
        params.append(departamento_id)
    if subdepartamento_id:
        filtros.append("COALESCE(t.subdepartamento_id, p.subdepartamento_id, e.subdepartamento_id) = ?")
        params.append(subdepartamento_id)
    if centro_id:
        filtros.append("(COALESCE(t.centro_id, p.centro_id, 0) = ? OR (t.centro_id IS NULL AND (p.id IS NULL OR p.centro_id IS NULL)))")
        params.append(centro_id)
    if not _usuario_es_global_rrhh() and _session_departamento_id():
        filtros.append("COALESCE(t.departamento_id, p.departamento_id, e.departamento_id) = ?")
        params.append(_session_departamento_id())

    turnos = conn.execute(
        f"""
        SELECT
            t.*,
            TRIM(e.nombre || ' ' || COALESCE(e.apellidos,'')) AS empleado_nombre,
            e.categoria_id AS empleado_categoria_id,
            c.nombre AS categoria_nombre,
            COALESCE(d_t.nombre, d_e.nombre) AS departamento_nombre,
            COALESCE(sd_t.nombre, sd_e.nombre) AS subdepartamento_nombre,
            p.nombre AS puesto_nombre
        FROM turnos_rrhh t
        JOIN empleados e ON t.empleado_id = e.id
        LEFT JOIN categorias_laborales c ON e.categoria_id = c.id
        LEFT JOIN departamentos d_t ON t.departamento_id = d_t.id
        LEFT JOIN departamentos d_e ON e.departamento_id = d_e.id
        LEFT JOIN subdepartamentos sd_t ON t.subdepartamento_id = sd_t.id
        LEFT JOIN subdepartamentos sd_e ON e.subdepartamento_id = sd_e.id
        LEFT JOIN puestos_operativos p ON t.puesto_id = p.id
        WHERE {' AND '.join(filtros)}
        ORDER BY COALESCE(d_t.nombre, d_e.nombre,''), COALESCE(sd_t.nombre, sd_e.nombre,''), e.nombre, t.hora_inicio
        """,
        params,
    ).fetchall()

    alertas = []
    resumen = {
        "sin_fichar": 0,
        "justificadas": 0,
        "ok": 0,
        "libres": 0,
        "extras_pendientes": 0,
        "extras_aprobadas": 0,
        "extras_rechazadas": 0,
        "salidas_anticipadas": 0,
        "fichajes_sin_turno": 0,
        "pausas_abiertas": 0,
        "doble_turno": 0,
    }

    turnos_por_empleado = {}
    for turno in turnos:
        tipo_turno_tmp = (turno["tipo"] or "real").strip().lower()
        if tipo_turno_tmp not in {"libre", "descanso", "dia_libre", "dia libre", "dÃ­a libre"}:
            turnos_por_empleado.setdefault(turno["empleado_id"], []).append(turno)
    doble_turno_notificado = set()

    for turno in turnos:
        tipo_turno = (turno["tipo"] or "real").strip().lower()
        if tipo_turno in {"libre", "descanso", "dia_libre", "día libre"}:
            resumen["libres"] += 1
            continue

        turnos_empleado = turnos_por_empleado.get(turno["empleado_id"], [])
        if len(turnos_empleado) > 1 and turno["empleado_id"] not in doble_turno_notificado:
            doble_turno_notificado.add(turno["empleado_id"])
            resumen["doble_turno"] += 1
            alertas.append({
                "nivel": "warning",
                "codigo": "doble_turno",
                "tipo": "Empleado con mas de un turno",
                "empleado": turno["empleado_nombre"],
                "turno": turno,
                "ausencia": None,
                "extra": None,
                "detalle": f"Tiene {len(turnos_empleado)} turnos el mismo dia. Revisar si es partido real, refuerzo o error de cuadrante.",
            })

        ausencia = _ausencia_empleado_en_fecha(conn, turno["empleado_id"], turno["fecha"], empresa_id)
        fichajes = _obtener_fichajes_dia(conn, turno["empleado_id"], turno["fecha"], empresa_id)
        entrada = fichajes["entrada"]
        salida = fichajes["salida"]

        turno_inicio = _parse_turno_dt(turno["fecha"], turno["hora_inicio"])
        turno_fin = _parse_turno_dt(turno["fecha"], turno["hora_fin"], fin=True)
        if turno_inicio and turno_fin and turno_fin <= turno_inicio:
            turno_fin = turno_fin + timedelta(days=1)

        if ausencia:
            resumen["justificadas"] += 1
            alertas.append({
                "nivel": "info",
                "codigo": "ausencia_justificada",
                "tipo": "Ausencia registrada",
                "empleado": turno["empleado_nombre"],
                "turno": turno,
                "ausencia": ausencia,
                "extra": None,
                "detalle": f"{ausencia['tipo']} · estado {ausencia['estado']}. No debe generar falta de fichaje mientras esté justificada.",
            })
            continue

        if not entrada and not salida:
            resumen["sin_fichar"] += 1
            alertas.append({
                "nivel": "danger",
                "codigo": "sin_fichaje",
                "tipo": "No ha fichado",
                "empleado": turno["empleado_nombre"],
                "turno": turno,
                "ausencia": None,
                "extra": None,
                "detalle": "Tiene turno en cuadrante y no consta entrada ni salida. Puede justificarse como ausencia o corregirse el fichaje.",
            })
            continue

        if entrada and not salida:
            alertas.append({
                "nivel": "warning",
                "codigo": "sin_salida",
                "tipo": "Falta fichaje de salida",
                "empleado": turno["empleado_nombre"],
                "turno": turno,
                "ausencia": None,
                "extra": None,
                "detalle": "Consta entrada pero no salida. Revisar antes de calcular pre-nómina.",
            })

        if salida and not entrada:
            alertas.append({
                "nivel": "warning",
                "codigo": "sin_entrada",
                "tipo": "Falta fichaje de entrada",
                "empleado": turno["empleado_nombre"],
                "turno": turno,
                "ausencia": None,
                "extra": None,
                "detalle": "Consta salida pero no entrada. Revisar antes de calcular pre-nómina.",
            })

        if fichajes["pausas"] and len(fichajes["reanudaciones"]) < len(fichajes["pausas"]):
            resumen["pausas_abiertas"] += 1
            alertas.append({
                "nivel": "warning",
                "codigo": "pausa_sin_reanudar",
                "tipo": "Pausa sin reanudacion",
                "empleado": turno["empleado_nombre"],
                "turno": turno,
                "ausencia": None,
                "extra": None,
                "detalle": "Existe pausa registrada sin reanudacion suficiente. Revisar antes de calcular jornada real.",
            })

        hubo_alerta_extra = False
        if entrada and turno_inicio:
            minutos_antes = _minutos_diff(turno_inicio, entrada[1])
            if minutos_antes > 30:
                motivo = f"Entrada {minutos_antes} min antes del horario de cuadrante."
                extra = _guardar_alerta_tiempo_extra(conn, turno, "entrada_anticipada", minutos_antes, motivo)
                extra = _estado_extra(conn, turno["id"], turno["empleado_id"], turno["fecha"], "entrada_anticipada") or extra
                estado = extra["estado"] if extra else "pendiente"
                if estado == "aprobado":
                    resumen["extras_aprobadas"] += 1
                elif estado == "rechazado":
                    resumen["extras_rechazadas"] += 1
                else:
                    resumen["extras_pendientes"] += 1
                hubo_alerta_extra = True
                alertas.append({
                    "nivel": "warning" if estado == "pendiente" else "info",
                    "codigo": "entrada_anticipada",
                    "tipo": "Entrada anticipada > 30 min",
                    "empleado": turno["empleado_nombre"],
                    "turno": turno,
                    "ausencia": None,
                    "extra": extra,
                    "detalle": f"{motivo} Estado: {estado}. Si no se aprueba, pre-nómina se rige por el cuadrante.",
                })

        if salida and turno_fin:
            minutos_tarde = _minutos_diff(salida[1], turno_fin)
            minutos_antes_salida = _minutos_diff(turno_fin, salida[1])
            if minutos_tarde > 30:
                motivo = f"Salida {minutos_tarde} min después del horario de cuadrante."
                extra = _guardar_alerta_tiempo_extra(conn, turno, "salida_tardia", minutos_tarde, motivo)
                extra = _estado_extra(conn, turno["id"], turno["empleado_id"], turno["fecha"], "salida_tardia") or extra
                estado = extra["estado"] if extra else "pendiente"
                if estado == "aprobado":
                    resumen["extras_aprobadas"] += 1
                elif estado == "rechazado":
                    resumen["extras_rechazadas"] += 1
                else:
                    resumen["extras_pendientes"] += 1
                hubo_alerta_extra = True
                alertas.append({
                    "nivel": "warning" if estado == "pendiente" else "info",
                    "codigo": "salida_tardia",
                    "tipo": "Salida tardía > 30 min",
                    "empleado": turno["empleado_nombre"],
                    "turno": turno,
                    "ausencia": None,
                    "extra": extra,
                    "detalle": f"{motivo} Estado: {estado}. Si no se aprueba, pre-nómina se rige por el cuadrante.",
                })
            elif minutos_antes_salida > 30:
                resumen["salidas_anticipadas"] += 1
                alertas.append({
                    "nivel": "warning",
                    "codigo": "salida_anticipada",
                    "tipo": "Salida antes de hora > 30 min",
                    "empleado": turno["empleado_nombre"],
                    "turno": turno,
                    "ausencia": None,
                    "extra": None,
                    "detalle": f"Salida {minutos_antes_salida} min antes del horario previsto. Requiere revisión/corrección.",
                })

        if entrada and salida and not hubo_alerta_extra:
            resumen["ok"] += 1

    empleados_con_turno = set(turnos_por_empleado.keys())
    fichajes_sin_turno = conn.execute(
        """
        SELECT f.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos,'')) AS empleado_nombre,
               d.nombre AS departamento_nombre, sd.nombre AS subdepartamento_nombre
        FROM fichajes f
        JOIN empleados e ON e.id = f.empleado_id
        LEFT JOIN departamentos d ON d.id = e.departamento_id
        LEFT JOIN subdepartamentos sd ON sd.id = e.subdepartamento_id
        WHERE f.empresa_id = ?
          AND date(f.fecha_hora) = date(?)
          AND COALESCE(f.estado, 'activo') NOT IN ('anulado', 'cancelado', 'borrado')
          AND (? = '' OR COALESCE(e.centro_id, 0) = ?)
        ORDER BY e.nombre, datetime(f.fecha_hora)
        """,
        (empresa_id, fecha_txt, str(centro_id or ""), centro_id or ""),
    ).fetchall()
    fichajes_por_empleado = {}
    for fichaje in fichajes_sin_turno:
        fichajes_por_empleado.setdefault(fichaje["empleado_id"], []).append(fichaje)
    for empleado_id, filas in fichajes_por_empleado.items():
        if empleado_id in empleados_con_turno:
            continue
        resumen["fichajes_sin_turno"] += 1
        primero = filas[0]
        alertas.append({
            "nivel": "warning",
            "codigo": "fichaje_sin_turno",
            "tipo": "Fichaje sin turno",
            "empleado": primero["empleado_nombre"],
            "turno": {
                "id": None,
                "empleado_id": empleado_id,
                "fecha": fecha_txt,
                "hora_inicio": None,
                "hora_fin": None,
                "departamento_nombre": primero["departamento_nombre"],
                "subdepartamento_nombre": primero["subdepartamento_nombre"],
            },
            "ausencia": None,
            "extra": None,
            "detalle": f"Tiene {len(filas)} fichaje(s), pero no existe turno planificado ese dia. Revisar si fue refuerzo no planificado o error.",
        })

    conn.commit()
    return alertas, resumen


@rrhh_bp.route("/alertas")
@login_required
def alertas_rrhh():
    conn = get_db()
    _ensure_control_tiempo_schema(conn)
    empresa_id = _empresa_id()
    fecha = request.args.get("fecha") or date.today().isoformat()
    centro_id = request.args.get("centro_id") or centro_activo_id() or ""
    departamento_id = request.args.get("departamento_id") or ""
    subdepartamento_id = request.args.get("subdepartamento_id") or ""

    alertas, resumen = _generar_alertas_fichajes(conn, fecha, departamento_id, subdepartamento_id, centro_id)
    scope_sql, scope_params = _departamento_scope_sql("e")
    alertas_admin = _alertas_administrativas_rrhh(conn, empresa_id, scope_sql, scope_params)

    centros = _centros_select(conn)
    departamentos = _departamentos_select(conn)
    subdepartamentos = _subdepartamentos_select(conn)

    conn.close()
    return render_template(
        "rrhh/alertas.html",
        alertas=alertas,
        resumen=resumen,
        fecha=fecha,
        centros=centros,
        departamentos=departamentos,
        subdepartamentos=subdepartamentos,
        centro_id=centro_id,
        departamento_id=departamento_id,
        subdepartamento_id=subdepartamento_id,
        alertas_admin=alertas_admin,
        puede_editar=_usuario_es_global_rrhh(),
    )


@rrhh_bp.route("/laboratorio-fichajes")
@login_required
def laboratorio_fichajes():
    conn = get_db()
    _ensure_control_tiempo_schema(conn)
    empresa_id = _empresa_id()
    desde = request.args.get("desde") or "2026-06-10"
    hasta = request.args.get("hasta") or (
        (_parse_date(desde) or date.today()) + timedelta(days=7)
    ).isoformat()
    centro_id = request.args.get("centro_id") or centro_activo_id() or ""

    casos = []
    recomendaciones = []
    resumen = {"casos": 0, "pendientes": 0, "fechas": 0, "empleados": 0}
    if _table_exists(conn, "simulador_rrhh_estres"):
        params = [empresa_id, desde, hasta]
        centro_sql = ""
        if centro_id:
            centro_sql = "AND s.centro_id = ?"
            params.append(centro_id)
        casos = conn.execute(
            f"""
            SELECT s.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos,'')) AS empleado_nombre,
                   e.rol, d.nombre AS departamento_nombre, sd.nombre AS subdepartamento_nombre,
                   t.hora_inicio, t.hora_fin, t.estado AS turno_estado
            FROM simulador_rrhh_estres s
            LEFT JOIN empleados e ON e.id = s.empleado_id
            LEFT JOIN departamentos d ON d.id = e.departamento_id
            LEFT JOIN subdepartamentos sd ON sd.id = e.subdepartamento_id
            LEFT JOIN turnos_rrhh t ON t.id = s.turno_id
            WHERE s.empresa_id = ?
              AND date(s.fecha) >= date(?)
              AND date(s.fecha) <= date(?)
              {centro_sql}
            ORDER BY date(s.fecha), s.caso, s.id
            """,
            params,
        ).fetchall()
        resumen = {
            "casos": len(casos),
            "pendientes": sum(1 for c in casos if (c["estado_revision"] or "pendiente") == "pendiente"),
            "fechas": len({c["fecha"] for c in casos}),
            "empleados": len({c["empleado_id"] for c in casos}),
        }
    if _table_exists(conn, "simulador_rrhh_recomendaciones"):
        recomendaciones = conn.execute(
            """
            SELECT *
            FROM simulador_rrhh_recomendaciones
            WHERE empresa_id = ? AND date(fecha) >= date(?) AND date(fecha) <= date(?)
            ORDER BY CASE prioridad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 ELSE 3 END, id DESC
            """,
            (empresa_id, desde, hasta),
        ).fetchall()
    centros = _centros_select(conn)
    conn.close()
    return render_template(
        "rrhh/laboratorio_fichajes.html",
        casos=casos,
        recomendaciones=recomendaciones,
        resumen=resumen,
        desde=desde,
        hasta=hasta,
        centro_id=centro_id,
        centros=centros,
    )


@rrhh_bp.route("/alertas/imprimir")
@login_required
def imprimir_alertas_rrhh():
    conn = get_db()
    _ensure_control_tiempo_schema(conn)
    fecha = request.args.get("fecha") or date.today().isoformat()
    centro_id = request.args.get("centro_id") or centro_activo_id() or ""
    departamento_id = request.args.get("departamento_id") or ""
    subdepartamento_id = request.args.get("subdepartamento_id") or ""
    alertas, resumen = _generar_alertas_fichajes(conn, fecha, departamento_id, subdepartamento_id, centro_id)
    conn.close()
    return render_template(
        "rrhh/alertas_print.html",
        alertas=alertas,
        resumen=resumen,
        fecha=fecha,
        generado_en=datetime.now().strftime("%d/%m/%Y %H:%M"),
    )


@rrhh_bp.route("/tiempo-extra/<int:id>/aprobar", methods=["POST"])
@login_required
@_require_rrhh_global
def aprobar_tiempo_extra(id):
    conn = get_db()
    _ensure_control_tiempo_schema(conn)
    if not _tiempo_extra_en_ambito_rrhh(conn, id):
        conn.close()
        flash("Tiempo extra no encontrado en tu empresa o centro activo.", "warning")
        return redirect(url_for("rrhh.alertas_rrhh", fecha=request.form.get("fecha") or date.today().isoformat()))
    motivo = request.form.get("motivo") or "Aprobado por responsable"
    conn.execute(
        """
        UPDATE tiempo_extra_rrhh
        SET estado = 'aprobado', motivo = ?, aprobado_por = ?, fecha_aprobacion = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (motivo, session.get("user_id"), id, _empresa_id()),
    )
    _registrar_historial(conn, "tiempo_extra_rrhh", id, "aprobar", motivo)
    conn.commit()
    conn.close()
    flash("Tiempo extra aprobado. Entrará en pre-nómina como abonable.", "success")
    return redirect(url_for("rrhh.alertas_rrhh", fecha=request.form.get("fecha") or date.today().isoformat()))


@rrhh_bp.route("/tiempo-extra/<int:id>/rechazar", methods=["POST"])
@login_required
@_require_rrhh_global
def rechazar_tiempo_extra(id):
    conn = get_db()
    _ensure_control_tiempo_schema(conn)
    if not _tiempo_extra_en_ambito_rrhh(conn, id):
        conn.close()
        flash("Tiempo extra no encontrado en tu empresa o centro activo.", "warning")
        return redirect(url_for("rrhh.alertas_rrhh", fecha=request.form.get("fecha") or date.today().isoformat()))
    motivo = request.form.get("motivo") or "Rechazado por responsable"
    conn.execute(
        """
        UPDATE tiempo_extra_rrhh
        SET estado = 'rechazado', motivo = ?, aprobado_por = ?, fecha_aprobacion = CURRENT_TIMESTAMP
        WHERE id = ? AND empresa_id = ?
        """,
        (motivo, session.get("user_id"), id, _empresa_id()),
    )
    _registrar_historial(conn, "tiempo_extra_rrhh", id, "rechazar", motivo)
    conn.commit()
    conn.close()
    flash("Tiempo extra rechazado. La pre-nómina se regirá por el cuadrante.", "warning")
    return redirect(url_for("rrhh.alertas_rrhh", fecha=request.form.get("fecha") or date.today().isoformat()))



def _config_tipo_ausencia(tipo):
    """Configuración operativa mínima para ausencias, bajas y permisos."""
    codigo = (tipo or "ausencia_injustificada").strip().lower()
    mapa = {
        "vacaciones": {"requiere_documento": 0, "afecta_prenomina": 1, "porcentaje_cobro": 100, "codigo_oficial": "VACACIONES", "color": "#198754"},
        "baja_medica": {"requiere_documento": 1, "afecta_prenomina": 1, "porcentaje_cobro": 75, "codigo_oficial": "BAJA_MEDICA", "color": "#dc3545"},
        "accidente_laboral": {"requiere_documento": 1, "afecta_prenomina": 1, "porcentaje_cobro": 75, "codigo_oficial": "ACCIDENTE_LABORAL", "color": "#fd7e14"},
        "permiso_retribuido": {"requiere_documento": 0, "afecta_prenomina": 1, "porcentaje_cobro": 100, "codigo_oficial": "PERMISO_RETRIBUIDO", "color": "#0dcaf0"},
        "permiso_no_retribuido": {"requiere_documento": 0, "afecta_prenomina": 1, "porcentaje_cobro": 0, "codigo_oficial": "PERMISO_NO_RETRIBUIDO", "color": "#6c757d"},
        "ausencia_justificada": {"requiere_documento": 0, "afecta_prenomina": 1, "porcentaje_cobro": 100, "codigo_oficial": "AUSENCIA_JUSTIFICADA", "color": "#0d6efd"},
        "ausencia_injustificada": {"requiere_documento": 0, "afecta_prenomina": 1, "porcentaje_cobro": 0, "codigo_oficial": "AUSENCIA_INJUSTIFICADA", "color": "#ffc107"},
    }
    return mapa.get(codigo, mapa["ausencia_injustificada"])


def _ensure_ausencias_extra_schema(conn):
    """Columnas extra para registrar ausencias desde alertas con trazabilidad."""
    _ensure_control_tiempo_schema(conn)
    for col in [
        "requiere_documento INTEGER DEFAULT 0",
        "documento_id INTEGER",
        "afecta_prenomina INTEGER DEFAULT 1",
        "porcentaje_cobro REAL DEFAULT 100",
        "codigo_oficial TEXT",
        "origen TEXT DEFAULT 'manual'",
        "color TEXT",
    ]:
        _add_column_if_missing(conn, "vacaciones_rrhh", col)
    conn.commit()


def _guardar_documento_ausencia(conn, empleado_id, tipo, archivo):
    """Guarda PDF/foto asociada a una ausencia y devuelve documento_id."""
    if not archivo or not getattr(archivo, "filename", None):
        return None

    from pathlib import Path
    from werkzeug.utils import secure_filename

    filename = archivo.filename or ""
    if not filename:
        return None

    permitido = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"}
    ext = Path(filename).suffix.lower()
    if ext not in permitido:
        raise ValueError("Documento no válido. Sube PDF o imagen JPG/PNG/WEBP.")

    carpeta = Path("uploads") / "rrhh" / "ausencias"
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre_seguro = secure_filename(filename)
    marca = datetime.now().strftime("%Y%m%d%H%M%S")
    ruta = carpeta / f"{empleado_id}_{tipo}_{marca}_{nombre_seguro}"
    archivo.save(str(ruta))

    conn.execute(
        """
        INSERT INTO documentos_rrhh
        (empleado_id, tipo_documento, nombre, fecha_documento, archivo, estado, observaciones, empresa_id)
        VALUES (?, ?, ?, ?, ?, 'vigente', ?, ?)
        """,
        (
            empleado_id,
            "justificante_ausencia",
            nombre_seguro,
            date.today().isoformat(),
            str(ruta).replace("\\", "/"),
            f"Justificante asociado a {tipo}",
            _empresa_id(),
        ),
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

@rrhh_bp.route("/alertas/justificar-ausencia", methods=["POST"])
@login_required
@_require_rrhh_global
def justificar_ausencia_desde_alerta():
    conn = get_db()
    _ensure_ausencias_extra_schema(conn)
    empleado_id = request.form.get("empleado_id")
    fecha = request.form.get("fecha") or date.today().isoformat()
    tipo = (request.form.get("tipo") or "ausencia_injustificada").strip().lower()
    estado = request.form.get("estado") or "aprobada"
    observaciones = request.form.get("observaciones") or "Justificada desde alertas RRHH"
    config = _config_tipo_ausencia(tipo)

    if not empleado_id:
        conn.close()
        flash("No se pudo justificar: falta empleado.", "warning")
        return redirect(url_for("rrhh.alertas_rrhh", fecha=fecha))
    if not _empleado_en_ambito_rrhh(conn, empleado_id):
        conn.close()
        flash("Empleado no encontrado en tu empresa o centro activo.", "warning")
        return redirect(url_for("rrhh.alertas_rrhh", fecha=fecha))

    documento_id = request.form.get("documento_id") or None
    archivo = request.files.get("archivo_documento")
    try:
        documento_subido_id = _guardar_documento_ausencia(conn, empleado_id, tipo, archivo)
    except ValueError as exc:
        conn.close()
        flash(str(exc), "warning")
        return redirect(url_for("rrhh.alertas_rrhh", fecha=fecha))
    if documento_subido_id:
        documento_id = documento_subido_id

    conn.execute(
        """
        INSERT INTO vacaciones_rrhh
        (empleado_id, fecha_inicio, fecha_fin, tipo, estado, dias, observaciones, empresa_id, usuario_creacion_id,
         requiere_documento, documento_id, afecta_prenomina, porcentaje_cobro, codigo_oficial, origen, color)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, 'alerta_rrhh', ?)
        """,
        (
            empleado_id,
            fecha,
            fecha,
            tipo,
            estado,
            observaciones,
            _empresa_id(),
            session.get("user_id"),
            config["requiere_documento"],
            documento_id,
            config["afecta_prenomina"],
            config["porcentaje_cobro"],
            config["codigo_oficial"],
            config["color"],
        ),
    )
    nuevo_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    _registrar_historial(conn, "vacaciones_rrhh", nuevo_id, "crear_desde_alerta", observaciones)
    conn.commit()
    conn.close()
    flash("Ausencia justificada desde alertas.", "success")
    return redirect(url_for("rrhh.alertas_rrhh", fecha=fecha))


@rrhh_bp.route("/cuadrantes/real/copiar-semana", methods=["POST"])
@login_required
@_require_cuadrantes
def copiar_semana_cuadrante_real():
    conn = get_db()
    _ensure_control_tiempo_schema(conn)
    empresa_id = _empresa_id()

    origen_txt = request.form.get("origen_fecha") or request.form.get("semana_origen") or request.form.get("filtro_fecha") or request.form.get("fecha")
    destino_txt = request.form.get("destino_fecha") or request.form.get("semana_destino")
    estrategia = request.form.get("estrategia") or "vacios"
    centro_id = request.form.get("filtro_centro_id") or request.form.get("centro_id") or ""
    departamento_id = request.form.get("filtro_departamento_id") or request.form.get("departamento_id") or ""
    subdepartamento_id = request.form.get("filtro_subdepartamento_id") or request.form.get("subdepartamento_id") or ""
    puesto_id = request.form.get("filtro_puesto_id") or request.form.get("puesto_id") or ""

    origen_inicio, origen_fin = _rango_semana(origen_txt)
    destino_inicio, _destino_fin = _rango_semana(destino_txt)

    filtros = [
        "t.empresa_id = ?",
        "date(t.fecha) BETWEEN date(?) AND date(?)",
        "COALESCE(t.estado, 'planificado') NOT IN ('anulado', 'cancelado', 'borrado')",
    ]
    params = [empresa_id, origen_inicio.isoformat(), origen_fin.isoformat()]
    if centro_id:
        filtros.append("(COALESCE(t.centro_id, p.centro_id, 0) = ? OR (t.centro_id IS NULL AND (p.id IS NULL OR p.centro_id IS NULL)))")
        params.append(centro_id)
    if departamento_id:
        filtros.append("COALESCE(t.departamento_id, p.departamento_id, e.departamento_id) = ?")
        params.append(departamento_id)
    if subdepartamento_id:
        filtros.append("COALESCE(t.subdepartamento_id, p.subdepartamento_id, e.subdepartamento_id) = ?")
        params.append(subdepartamento_id)
    if puesto_id:
        filtros.append("COALESCE(t.puesto_id,0) = ?")
        params.append(puesto_id)
    scope_sql, scope_params = _departamento_scope_sql("e")
    filtros.append(scope_sql)
    params.extend(scope_params)

    origen_turnos = conn.execute(
        f"""
        SELECT t.*
        FROM turnos_rrhh t
        JOIN empleados e ON t.empleado_id = e.id
        LEFT JOIN puestos_operativos p ON t.puesto_id = p.id
        WHERE {' AND '.join(filtros)}
        ORDER BY date(t.fecha), t.id
        """,
        params,
    ).fetchall()

    creados = actualizados = omitidos = 0
    if estrategia in {"reemplazar", "sobrescribir"}:
        # Solo anula la semana destino dentro de los mismos filtros. No borra histórico.
        where_dest = ["empresa_id = ?", "date(fecha) BETWEEN date(?) AND date(?)"]
        params_dest = [empresa_id, destino_inicio.isoformat(), (destino_inicio + timedelta(days=6)).isoformat()]
        if centro_id:
            where_dest.append("COALESCE(centro_id,0) = ?")
            params_dest.append(centro_id)
        if departamento_id:
            where_dest.append("COALESCE(departamento_id,0) = ?")
            params_dest.append(departamento_id)
        if subdepartamento_id:
            where_dest.append("COALESCE(subdepartamento_id,0) = ?")
            params_dest.append(subdepartamento_id)
        if puesto_id:
            where_dest.append("COALESCE(puesto_id,0) = ?")
            params_dest.append(puesto_id)
        scope_dest_sql, scope_dest_params = _departamento_scope_sql("e")
        where_dest.append(
            f"""empleado_id IN (
                SELECT e.id FROM empleados e WHERE {scope_dest_sql}
            )"""
        )
        params_dest.extend(scope_dest_params)
        conn.execute(f"UPDATE turnos_rrhh SET estado = 'anulado', updated_at = CURRENT_TIMESTAMP WHERE {' AND '.join(where_dest)}", params_dest)

    for t in origen_turnos:
        try:
            fecha_origen = date.fromisoformat(str(t["fecha"]).split(" ")[0].split("T")[0])
        except Exception:
            omitidos += 1
            continue
        fecha_destino = destino_inicio + timedelta(days=fecha_origen.weekday())
        existente = conn.execute(
            """
            SELECT id
            FROM turnos_rrhh
            WHERE empresa_id = ?
              AND empleado_id = ?
              AND date(fecha) = date(?)
              AND COALESCE(estado, 'planificado') NOT IN ('anulado', 'cancelado', 'borrado')
            LIMIT 1
            """,
            (empresa_id, t["empleado_id"], fecha_destino.isoformat()),
        ).fetchone()
        if existente and estrategia == "vacios":
            omitidos += 1
            continue
        if existente and estrategia == "sobrescribir":
            _safe_update(conn, "turnos_rrhh", existente["id"], {"estado": "anulado"}, "Sobrescrito al copiar semana")
            actualizados += 1

        conn.execute(
            """
            INSERT INTO turnos_rrhh
            (empleado_id, fecha, hora_inicio, hora_fin, tipo, centro_id, departamento_id, subdepartamento_id, puesto_id, estado, origen, observaciones, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                t["empleado_id"],
                fecha_destino.isoformat(),
                t["hora_inicio"],
                t["hora_fin"],
                t["tipo"],
                t["centro_id"] if "centro_id" in t.keys() else None,
                t["departamento_id"],
                t["subdepartamento_id"] if "subdepartamento_id" in t.keys() else None,
                t["puesto_id"] if "puesto_id" in t.keys() else None,
                "planificado",
                "copia_semana",
                t["observaciones"],
                empresa_id,
            ),
        )
        creados += 1

    _registrar_historial(conn, "turnos_rrhh", 0, "copiar_semana", f"{origen_inicio} -> {destino_inicio}: {creados} creados, {actualizados} actualizados, {omitidos} omitidos")
    conn.commit()
    conn.close()
    flash(f"Semana copiada: {creados} turnos creados, {actualizados} reemplazados, {omitidos} omitidos.", "success")
    return redirect(url_for("rrhh.cuadrante_real", fecha=destino_inicio.isoformat(), centro_id=centro_id, departamento_id=departamento_id, subdepartamento_id=subdepartamento_id, puesto_id=puesto_id))


@rrhh_bp.route("/cuadrantes/real/turno/<int:id>/copiar", methods=["POST"], endpoint="copiar_turno_cuadrante_real_dia")
@login_required
@_require_cuadrantes
def copiar_turno_cuadrante_real_dia(id):
    conn = get_db()
    _ensure_control_tiempo_schema(conn)
    empresa_id = _empresa_id()
    turno = _turno_en_ambito_rrhh(conn, id)
    if not turno:
        conn.close()
        flash("Turno origen no encontrado en tu empresa o centro activo.", "warning")
        return _redirect_cuadrante_real_con_filtros()

    modo = request.form.get("modo") or "semana"
    estrategia = request.form.get("estrategia") or "vacios"
    try:
        fecha_origen = date.fromisoformat(turno["fecha"])
    except Exception:
        fecha_origen = date.today()
    semana_inicio = fecha_origen - timedelta(days=fecha_origen.weekday())

    if modo == "laborables":
        dias = [0, 1, 2, 3, 4]
    elif modo == "seleccionados":
        dias = [_to_int(d, -1) for d in request.form.getlist("dias_destino")]
        dias = [d for d in dias if 0 <= d <= 6]
    else:
        dias = [0, 1, 2, 3, 4, 5, 6]

    creados = omitidos = reemplazados = 0
    for d in dias:
        fecha_destino = semana_inicio + timedelta(days=d)
        if fecha_destino.isoformat() == turno["fecha"]:
            continue
        existente = conn.execute(
            """
            SELECT id
            FROM turnos_rrhh
            WHERE empresa_id = ?
              AND empleado_id = ?
              AND date(fecha) = date(?)
              AND COALESCE(estado, 'planificado') NOT IN ('anulado', 'cancelado', 'borrado')
            LIMIT 1
            """,
            (empresa_id, turno["empleado_id"], fecha_destino.isoformat()),
        ).fetchone()
        if existente and estrategia == "vacios":
            omitidos += 1
            continue
        if existente and estrategia == "sobrescribir":
            _safe_update(conn, "turnos_rrhh", existente["id"], {"estado": "anulado"}, "Sobrescrito al copiar horario")
            reemplazados += 1
        if existente and estrategia == "duplicar":
            duplicado = conn.execute(
                """
                SELECT id FROM turnos_rrhh
                WHERE empresa_id = ? AND empleado_id = ? AND date(fecha)=date(?)
                  AND COALESCE(hora_inicio,'') = COALESCE(?, '')
                  AND COALESCE(hora_fin,'') = COALESCE(?, '')
                  AND COALESCE(estado,'planificado') NOT IN ('anulado','cancelado','borrado')
                LIMIT 1
                """,
                (empresa_id, turno["empleado_id"], fecha_destino.isoformat(), turno["hora_inicio"], turno["hora_fin"]),
            ).fetchone()
            if duplicado:
                omitidos += 1
                continue
        conn.execute(
            """
            INSERT INTO turnos_rrhh
            (empleado_id, fecha, hora_inicio, hora_fin, tipo, centro_id, departamento_id, subdepartamento_id, puesto_id, estado, origen, observaciones, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'planificado', 'copia_turno', ?, ?)
            """,
            (
                turno["empleado_id"], fecha_destino.isoformat(), turno["hora_inicio"], turno["hora_fin"], turno["tipo"],
                turno["centro_id"] if "centro_id" in turno.keys() else None,
                turno["departamento_id"], turno["subdepartamento_id"] if "subdepartamento_id" in turno.keys() else None,
                turno["puesto_id"] if "puesto_id" in turno.keys() else None,
                turno["observaciones"], empresa_id,
            ),
        )
        creados += 1
    _registrar_historial(conn, "turnos_rrhh", id, "copiar_horario", f"{creados} creados, {reemplazados} reemplazados, {omitidos} omitidos")
    conn.commit()
    conn.close()
    flash(f"Horario copiado: {creados} creados, {reemplazados} reemplazados, {omitidos} omitidos.", "success")
    return _redirect_cuadrante_real_con_filtros(turno["fecha"])


@rrhh_bp.route("/cuadrantes/real/libre/marcar", methods=["POST"], endpoint="marcar_dia_libre_cuadrante_real_v2")
@login_required
@_require_cuadrantes
def marcar_dia_libre_cuadrante_real_v2():
    conn = get_db()
    _ensure_control_tiempo_schema(conn)
    empresa_id = _empresa_id()
    fecha = request.form.get("fecha") or date.today().isoformat()
    empleado_id = request.form.get("empleado_id")
    if not empleado_id:
        conn.close()
        flash("No se pudo marcar libre: falta empleado.", "warning")
        return _redirect_cuadrante_real_con_filtros(fecha)
    if not _empleado_en_ambito_rrhh(conn, empleado_id):
        conn.close()
        flash("Empleado no encontrado en tu empresa o centro activo.", "warning")
        return _redirect_cuadrante_real_con_filtros(fecha)

    conn.execute(
        """
        INSERT INTO turnos_rrhh
        (empleado_id, fecha, hora_inicio, hora_fin, tipo, centro_id, departamento_id, subdepartamento_id, puesto_id, estado, origen, observaciones, empresa_id)
        VALUES (?, ?, NULL, NULL, 'libre', ?, ?, ?, NULL, 'planificado', 'cuadrante_real', 'LIBRE / DESCANSO', ?)
        """,
        (
            empleado_id,
            fecha,
            request.form.get("centro_id") or None,
            request.form.get("departamento_id") or None,
            request.form.get("subdepartamento_id") or None,
            empresa_id,
        ),
    )
    conn.commit()
    conn.close()
    flash("Día libre registrado.", "success")
    return _redirect_cuadrante_real_con_filtros(fecha)


@rrhh_bp.route("/cuadrantes/real/libre/copiar", methods=["POST"], endpoint="copiar_libre_semana_cuadrante_real_v2")
@login_required
@_require_cuadrantes
def copiar_libre_semana_cuadrante_real_v2():
    conn = get_db()
    _ensure_control_tiempo_schema(conn)
    empresa_id = _empresa_id()
    empleado_id = request.form.get("empleado_id")
    fecha = request.form.get("fecha") or date.today().isoformat()
    if not _empleado_en_ambito_rrhh(conn, empleado_id):
        conn.close()
        flash("Empleado no encontrado en tu empresa o centro activo.", "warning")
        return _redirect_cuadrante_real_con_filtros(fecha)
    estrategia = request.form.get("estrategia") or "vacios"
    dias = [_to_int(d, -1) for d in request.form.getlist("dias_destino")]
    dias = [d for d in dias if 0 <= d <= 6]
    try:
        base = date.fromisoformat(fecha)
    except Exception:
        base = date.today()
    inicio_semana = base - timedelta(days=base.weekday())
    creados = omitidos = reemplazados = 0
    for d in dias:
        fd = inicio_semana + timedelta(days=d)
        existente = conn.execute(
            """
            SELECT id FROM turnos_rrhh
            WHERE empresa_id=? AND empleado_id=? AND date(fecha)=date(?)
              AND COALESCE(estado,'planificado') NOT IN ('anulado','cancelado','borrado')
            LIMIT 1
            """,
            (empresa_id, empleado_id, fd.isoformat()),
        ).fetchone()
        if existente and estrategia == "vacios":
            omitidos += 1
            continue
        if existente and estrategia == "sobrescribir":
            _safe_update(conn, "turnos_rrhh", existente["id"], {"estado": "anulado"}, "Sobrescrito por día libre")
            reemplazados += 1
        conn.execute(
            """
            INSERT INTO turnos_rrhh
            (empleado_id, fecha, tipo, centro_id, departamento_id, subdepartamento_id, estado, origen, observaciones, empresa_id)
            VALUES (?, ?, 'libre', ?, ?, ?, 'planificado', 'copia_libre', 'LIBRE / DESCANSO', ?)
            """,
            (empleado_id, fd.isoformat(), request.form.get("centro_id") or None, request.form.get("departamento_id") or None, request.form.get("subdepartamento_id") or None, empresa_id),
        )
        creados += 1
    _registrar_historial(conn, "turnos_rrhh", 0, "copiar_libre", f"{creados} creados, {reemplazados} reemplazados, {omitidos} omitidos")
    conn.commit()
    conn.close()
    flash(f"Libres copiados: {creados} creados, {reemplazados} reemplazados, {omitidos} omitidos.", "success")
    return _redirect_cuadrante_real_con_filtros(fecha)


def _minutos_extra_aprobados_mes(conn, empleado_id, inicio, fin):
    _ensure_control_tiempo_schema(conn)
    filas = conn.execute(
        """
        SELECT estado, SUM(COALESCE(minutos,0)) AS minutos
        FROM tiempo_extra_rrhh
        WHERE empleado_id = ?
          AND empresa_id = ?
          AND date(fecha) >= date(?)
          AND date(fecha) < date(?)
        GROUP BY estado
        """,
        (empleado_id, _empresa_id(), inicio, fin),
    ).fetchall()
    aprobados = 0
    no_aprobados = 0
    for f in filas:
        if f["estado"] == "aprobado":
            aprobados += int(f["minutos"] or 0)
        else:
            no_aprobados += int(f["minutos"] or 0)
    return aprobados, no_aprobados

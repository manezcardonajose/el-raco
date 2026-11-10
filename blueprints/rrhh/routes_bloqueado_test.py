from datetime import datetime, date, timedelta
from functools import wraps

from flask import render_template, request, redirect, url_for, session, flash

from database import get_db
from . import rrhh_bp
from blueprints.auth.routes import login_required


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
        "categoria_id INTEGER",
        "fecha_alta TEXT",
        "fecha_baja TEXT",
        "observaciones TEXT",
        "empresa_id INTEGER DEFAULT 1",
        "subdepartamento_id INTEGER",
        "punto_venta_id INTEGER",
        "rol TEXT",
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
    ]:
        _add_column_if_missing(conn, "empleados", col)

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
    ]:
        _add_column_if_missing(conn, "fichajes", col)

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
    for rol in ["admin", "direccion", "rrhh", "jefe_departamento", "empleado"]:
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
        "empresa_id INTEGER DEFAULT 1", "fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP"
    ]:
        _add_column_if_missing(conn, "documentos_rrhh", col)

    conn.commit()


def _departamento_scope_sql(alias="e"):
    """
    Devuelve SQL y parámetros para visibilidad real por departamento.
    Dirección/admin/RRHH ven todo. El resto se limita a su departamento si existe en sesión.
    """
    empresa_id = _empresa_id()
    if _usuario_es_global_rrhh():
        return f"{alias}.empresa_id = ?", [empresa_id]

    departamento_id = _session_departamento_id()
    if departamento_id:
        return f"{alias}.empresa_id = ? AND {alias}.departamento_id = ?", [empresa_id, departamento_id]

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


def _row_to_dict(row):
    return dict(row) if row else None


def _listas_maestras(conn):
    empresa_id = _empresa_id()
    categorias = conn.execute(
        "SELECT id, nombre FROM categorias_laborales WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre",
        (empresa_id,),
    ).fetchall()
    departamentos = conn.execute(
        "SELECT id, nombre FROM departamentos WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre",
        (empresa_id,),
    ).fetchall()
    subdepartamentos = conn.execute(
        "SELECT id, nombre, departamento_id FROM subdepartamentos WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre",
        (empresa_id,),
    ).fetchall() if _table_exists(conn, "subdepartamentos") else []
    puntos_venta = conn.execute(
        "SELECT id, nombre FROM puntos_venta WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre",
        (empresa_id,),
    ).fetchall() if _table_exists(conn, "puntos_venta") else []
    roles = conn.execute("SELECT id, nombre FROM roles ORDER BY nombre").fetchall() if _table_exists(conn, "roles") else []
    return categorias, departamentos, subdepartamentos, puntos_venta, roles


# ============================================================
# PANEL RRHH
# ============================================================

@rrhh_bp.route("/")
@login_required
def panel_rrhh():
    conn = get_db()
    ensure_rrhh_schema(conn)
    empresa_id = _empresa_id()
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
        ultimos_fichajes=ultimos_fichajes,
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
    empleados = conn.execute(
        f"""
        SELECT e.*, c.nombre categoria_nombre, d.nombre departamento_nombre,
               sd.nombre subdepartamento_nombre, pv.nombre punto_venta_nombre
        FROM empleados e
        LEFT JOIN categorias_laborales c ON e.categoria_id = c.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON e.subdepartamento_id = sd.id
        LEFT JOIN puntos_venta pv ON e.punto_venta_id = pv.id
        WHERE {scope_sql}
        ORDER BY COALESCE(e.activo, 1) DESC, e.nombre, e.apellidos
        """,
        scope_params,
    ).fetchall()
    conn.close()
    return render_template("rrhh/empleados.html", empleados=empleados, puede_editar=_usuario_es_global_rrhh())


@rrhh_bp.route("/empleados/nuevo", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def nuevo_empleado():
    conn = get_db()
    ensure_rrhh_schema(conn)
    empresa_id = _empresa_id()

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            flash("El nombre del empleado es obligatorio.", "warning")
            return redirect(url_for("rrhh.nuevo_empleado"))

        conn.execute(
            """
            INSERT INTO empleados
            (nombre, apellidos, nif, numero_seguridad_social, email, telefono, fecha_nacimiento, nacionalidad, estado_civil,
             direccion, codigo_postal, localidad, provincia, pais, iban, contacto_emergencia_nombre, contacto_emergencia_telefono,
             contacto_emergencia_relacion, nivel_formacion, talla_uniforme, talla_calzado, alergias_observaciones, carnet_manipulador,
             fecha_caducidad_carnet, prl_realizada, fecha_prl, reconocimiento_medico, fecha_reconocimiento, categoria_id, departamento_id,
             subdepartamento_id, punto_venta_id, rol, fecha_alta, observaciones, empresa_id, activo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                nombre,
                request.form.get("apellidos"),
                request.form.get("nif"),
                request.form.get("numero_seguridad_social"),
                request.form.get("email"),
                request.form.get("telefono"),
                request.form.get("fecha_nacimiento"),
                request.form.get("nacionalidad"),
                request.form.get("estado_civil"),
                request.form.get("direccion"),
                request.form.get("codigo_postal"),
                request.form.get("localidad"),
                request.form.get("provincia"),
                request.form.get("pais"),
                request.form.get("iban"),
                request.form.get("contacto_emergencia_nombre"),
                request.form.get("contacto_emergencia_telefono"),
                request.form.get("contacto_emergencia_relacion"),
                request.form.get("nivel_formacion"),
                request.form.get("talla_uniforme"),
                request.form.get("talla_calzado"),
                request.form.get("alergias_observaciones"),
                1 if request.form.get("carnet_manipulador") else 0,
                request.form.get("fecha_caducidad_carnet"),
                1 if request.form.get("prl_realizada") else 0,
                request.form.get("fecha_prl"),
                1 if request.form.get("reconocimiento_medico") else 0,
                request.form.get("fecha_reconocimiento"),
                request.form.get("categoria_id") or None,
                request.form.get("departamento_id") or None,
                request.form.get("subdepartamento_id") or None,
                request.form.get("punto_venta_id") or None,
                request.form.get("rol"),
                request.form.get("fecha_alta") or date.today().isoformat(),
                request.form.get("observaciones"),
                empresa_id,
            ),
        )
        conn.commit()
        conn.close()
        flash("Empleado creado correctamente.", "success")
        return redirect(url_for("rrhh.lista_empleados"))

    categorias, departamentos, subdepartamentos, puntos_venta, roles = _listas_maestras(conn)
    conn.close()
    return render_template(
        "rrhh/empleado_form.html",
        empleado=None,
        categorias=categorias,
        departamentos=departamentos,
        subdepartamentos=subdepartamentos,
        puntos_venta=puntos_venta,
        roles=roles,
    )


@rrhh_bp.route("/empleados/<int:id>/editar", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def editar_empleado(id):
    conn = get_db()
    ensure_rrhh_schema(conn)
    empresa_id = _empresa_id()
    empleado = conn.execute("SELECT * FROM empleados WHERE id = ? AND empresa_id = ?", (id, empresa_id)).fetchone()
    if not empleado:
        conn.close()
        flash("Empleado no encontrado.", "danger")
        return redirect(url_for("rrhh.lista_empleados"))

    if request.method == "POST":
        activo = 1 if request.form.get("activo") else 0
        conn.execute(
            """
            UPDATE empleados
            SET nombre = ?, apellidos = ?, nif = ?, numero_seguridad_social = ?, email = ?, telefono = ?,
                fecha_nacimiento = ?, nacionalidad = ?, estado_civil = ?, direccion = ?, codigo_postal = ?, localidad = ?, provincia = ?,
                pais = ?, iban = ?, contacto_emergencia_nombre = ?, contacto_emergencia_telefono = ?, contacto_emergencia_relacion = ?,
                nivel_formacion = ?, talla_uniforme = ?, talla_calzado = ?, alergias_observaciones = ?, carnet_manipulador = ?,
                fecha_caducidad_carnet = ?, prl_realizada = ?, fecha_prl = ?, reconocimiento_medico = ?, fecha_reconocimiento = ?,
                categoria_id = ?, departamento_id = ?, subdepartamento_id = ?, punto_venta_id = ?, rol = ?,
                fecha_alta = ?, fecha_baja = ?, observaciones = ?, activo = ?
            WHERE id = ? AND empresa_id = ?
            """,
            (
                request.form.get("nombre"),
                request.form.get("apellidos"),
                request.form.get("nif"),
                request.form.get("numero_seguridad_social"),
                request.form.get("email"),
                request.form.get("telefono"),
                request.form.get("fecha_nacimiento"),
                request.form.get("nacionalidad"),
                request.form.get("estado_civil"),
                request.form.get("direccion"),
                request.form.get("codigo_postal"),
                request.form.get("localidad"),
                request.form.get("provincia"),
                request.form.get("pais"),
                request.form.get("iban"),
                request.form.get("contacto_emergencia_nombre"),
                request.form.get("contacto_emergencia_telefono"),
                request.form.get("contacto_emergencia_relacion"),
                request.form.get("nivel_formacion"),
                request.form.get("talla_uniforme"),
                request.form.get("talla_calzado"),
                request.form.get("alergias_observaciones"),
                1 if request.form.get("carnet_manipulador") else 0,
                request.form.get("fecha_caducidad_carnet"),
                1 if request.form.get("prl_realizada") else 0,
                request.form.get("fecha_prl"),
                1 if request.form.get("reconocimiento_medico") else 0,
                request.form.get("fecha_reconocimiento"),
                request.form.get("categoria_id") or None,
                request.form.get("departamento_id") or None,
                request.form.get("subdepartamento_id") or None,
                request.form.get("punto_venta_id") or None,
                request.form.get("rol"),
                request.form.get("fecha_alta"),
                request.form.get("fecha_baja"),
                request.form.get("observaciones"),
                activo,
                id,
                empresa_id,
            ),
        )
        conn.commit()
        conn.close()
        flash("Empleado actualizado correctamente.", "success")
        return redirect(url_for("rrhh.lista_empleados"))

    categorias, departamentos, subdepartamentos, puntos_venta, roles = _listas_maestras(conn)
    conn.close()
    return render_template(
        "rrhh/empleado_form.html",
        empleado=empleado,
        categorias=categorias,
        departamentos=departamentos,
        subdepartamentos=subdepartamentos,
        puntos_venta=puntos_venta,
        roles=roles,
    )


# ============================================================
# CATEGORÍAS LABORALES
# ============================================================

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
        conn.execute(
            """
            INSERT INTO categorias_laborales (nombre, descripcion, salario_base, convenio, empresa_id, activo)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (nombre, request.form.get("descripcion"), request.form.get("salario_base") or 0, request.form.get("convenio"), _empresa_id()),
        )
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


@rrhh_bp.route("/contratos/nuevo", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def nuevo_contrato():
    conn = get_db()
    ensure_rrhh_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        conn.execute(
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
        conn.execute(
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
        conn.commit()
        conn.close()
        flash("Nómina registrada correctamente.", "success")
        return redirect(url_for("rrhh.lista_nominas"))
    empleados = conn.execute(
        "SELECT id, nombre, apellidos FROM empleados WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre, apellidos",
        (empresa_id,),
    ).fetchall()
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


@rrhh_bp.route("/documentos/nuevo", methods=["GET", "POST"])
@login_required
@_require_rrhh_global
def nuevo_documento():
    conn = get_db()
    ensure_rrhh_schema(conn)
    empresa_id = _empresa_id()
    if request.method == "POST":
        conn.execute(
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
    ]:
        _add_column_if_missing(conn, "resumen_prenomina", col)

    permisos_extra = [
        ("ver_prenomina", "Ver cálculos de pre-nómina"),
        ("gestionar_prenomina", "Calcular y cerrar pre-nóminas"),
        ("gestionar_turnos", "Crear y editar turnos RRHH"),
        ("gestionar_variables_prenomina", "Crear variables de pre-nómina"),
    ]
    for nombre, descripcion in permisos_extra:
        conn.execute("INSERT OR IGNORE INTO permisos (nombre, descripcion) VALUES (?, ?)", (nombre, descripcion))
    conn.commit()


def _calcular_horas_fichajes(conn, empleado_id, inicio, fin):
    filas = conn.execute(
        """
        SELECT tipo, fecha_hora
        FROM fichajes
        WHERE empleado_id = ? AND datetime(fecha_hora) >= datetime(?) AND datetime(fecha_hora) < datetime(?)
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


def _calcular_prenomina_empleado(conn, empleado_id, anio, mes, guardar=True):
    inicio, fin, anio, mes = _mes_rango(anio, mes)
    horas_reales = _calcular_horas_fichajes(conn, empleado_id, inicio, fin)
    horas_turno, horas_nocturnas, horas_festivas = _calcular_turnos(conn, empleado_id, inicio, fin)
    salario_base, horas_semana, precio_hora = _datos_salario_empleado(conn, empleado_id)
    horas_referencia = horas_turno if horas_turno > 0 else horas_semana * 4.333
    horas_extra = max(0, horas_reales - horas_referencia)
    ausencias = max(0, horas_referencia - horas_reales) if horas_referencia > 0 else 0

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

    coste_estimado = salario_base + pluses - descuentos + (horas_extra * precio_hora * 1.25)
    data = {
        "empleado_id": empleado_id,
        "mes": mes,
        "anio": anio,
        "horas_normales": round(min(horas_reales, horas_referencia), 2),
        "horas_extra": round(horas_extra, 2),
        "horas_nocturnas": round(horas_nocturnas, 2),
        "horas_festivas": round(horas_festivas, 2),
        "ausencias_horas": round(ausencias, 2),
        "salario_base_mes": round(salario_base, 2),
        "precio_hora": round(precio_hora, 2),
        "pluses": round(pluses, 2),
        "descuentos": round(descuentos, 2),
        "coste_estimado": round(coste_estimado, 2),
    }
    if guardar:
        conn.execute(
            """
            INSERT INTO resumen_prenomina
            (empleado_id, mes, anio, horas_normales, horas_extra, horas_nocturnas, horas_festivas, ausencias_horas,
             salario_base_mes, precio_hora, pluses, descuentos, coste_estimado, empresa_id, fecha_calculo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
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
                fecha_calculo = CURRENT_TIMESTAMP
            """,
            (
                empleado_id, mes, anio, data["horas_normales"], data["horas_extra"], data["horas_nocturnas"],
                data["horas_festivas"], data["ausencias_horas"], data["salario_base_mes"], data["precio_hora"],
                data["pluses"], data["descuentos"], data["coste_estimado"], _empresa_id(),
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
    conn.close()
    return render_template("rrhh/prenomina.html", resumen=resumen, empleados=empleados, anio=anio, mes=mes, puede_editar=_usuario_es_global_rrhh())


@rrhh_bp.route("/prenomina/calcular", methods=["POST"])
@login_required
@_require_rrhh_global
def calcular_prenomina():
    conn = get_db()
    _ensure_prenomina_schema(conn)
    empresa_id = _empresa_id()
    anio = _to_int(request.form.get("anio"), date.today().year)
    mes = _to_int(request.form.get("mes"), date.today().month)
    empleado_id = request.form.get("empleado_id")
    if empleado_id:
        ids = [empleado_id]
    else:
        ids = [r["id"] for r in conn.execute("SELECT id FROM empleados WHERE empresa_id = ? AND COALESCE(activo,1)=1", (empresa_id,)).fetchall()]
    for eid in ids:
        _calcular_prenomina_empleado(conn, eid, anio, mes, guardar=True)
    conn.commit()
    conn.close()
    flash("Pre-nómina calculada correctamente.", "success")
    return redirect(url_for("rrhh.lista_prenomina", anio=anio, mes=mes))


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
        LEFT JOIN departamentos d ON COALESCE(t.departamento_id, e.departamento_id) = d.id
        WHERE {scope_sql} AND t.empresa_id = ?
        ORDER BY date(t.fecha) DESC, t.hora_inicio DESC
        LIMIT 300
        """,
        scope_params + [empresa_id],
    ).fetchall()
    empleados = conn.execute("SELECT id, nombre, apellidos, departamento_id FROM empleados WHERE empresa_id = ? AND COALESCE(activo,1)=1 ORDER BY nombre, apellidos", (empresa_id,)).fetchall()
    departamentos = conn.execute("SELECT id, nombre FROM departamentos WHERE empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
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


@rrhh_bp.route("/cuadrantes")
@login_required
def cuadrantes():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    empresa_id = _empresa_id()
    inicio, fin = _rango_semana(request.args.get("fecha"))
    depto = request.args.get("departamento_id") or ""
    scope_sql, scope_params = _departamento_scope_sql("e")

    filtro_depto = ""
    params = scope_params + [empresa_id, inicio.isoformat(), fin.isoformat()]
    if depto:
        filtro_depto = " AND COALESCE(t.departamento_id, e.departamento_id) = ?"
        params.append(depto)

    turnos = conn.execute(
        f"""
        SELECT t.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre,
               d.nombre departamento_nombre
        FROM turnos_rrhh t
        JOIN empleados e ON t.empleado_id = e.id
        LEFT JOIN departamentos d ON COALESCE(t.departamento_id, e.departamento_id) = d.id
        WHERE {scope_sql}
          AND t.empresa_id = ?
          AND date(t.fecha) BETWEEN date(?) AND date(?)
          {filtro_depto}
        ORDER BY date(t.fecha), d.nombre, e.nombre, t.hora_inicio
        """,
        params,
    ).fetchall()

    empleados = conn.execute(
        """
        SELECT e.id, e.nombre, e.apellidos, e.departamento_id, d.nombre departamento_nombre
        FROM empleados e
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        WHERE e.empresa_id = ? AND COALESCE(e.activo,1)=1
        ORDER BY d.nombre, e.nombre, e.apellidos
        """,
        (empresa_id,),
    ).fetchall()
    departamentos = conn.execute("SELECT id, nombre FROM departamentos WHERE empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
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
        ORDER BY v.fecha_inicio
        """,
        scope_params + [empresa_id, fin.isoformat(), inicio.isoformat()],
    ).fetchall()
    conn.close()

    dias = [inicio + timedelta(days=i) for i in range(7)]
    return render_template(
        "rrhh/cuadrantes.html",
        turnos=turnos,
        empleados=empleados,
        departamentos=departamentos,
        vacaciones=vacaciones,
        dias=dias,
        inicio=inicio,
        fin=fin,
        departamento_id=depto,
        puede_editar=_usuario_es_global_rrhh(),
        timedelta=timedelta,
    )


@rrhh_bp.route("/cuadrantes/crear", methods=["POST"])
@login_required
@_require_rrhh_global
def crear_turno_cuadrante():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    empresa_id = _empresa_id()
    repetir = _to_int(request.form.get("repetir_semanas"), 1)
    repetir = max(1, min(8, repetir))
    fecha = request.form.get("fecha")
    try:
        fecha_base = date.fromisoformat(fecha)
    except Exception:
        fecha_base = date.today()
    for i in range(repetir):
        fecha_turno = fecha_base + timedelta(days=7 * i)
        conn.execute(
            """
            INSERT INTO turnos_rrhh (empleado_id, fecha, hora_inicio, hora_fin, tipo, departamento_id, observaciones, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.form.get("empleado_id"), fecha_turno.isoformat(), request.form.get("hora_inicio"),
                request.form.get("hora_fin"), request.form.get("tipo") or "normal", request.form.get("departamento_id") or None,
                request.form.get("observaciones"), empresa_id,
            ),
        )
    conn.commit()
    conn.close()
    flash("Turno añadido al cuadrante.", "success")
    return redirect(url_for("rrhh.cuadrantes", fecha=fecha))


@rrhh_bp.route("/cuadrantes/<int:id>/eliminar", methods=["POST"])
@login_required
@_require_rrhh_global
def eliminar_turno_cuadrante(id):
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    turno = conn.execute("SELECT fecha FROM turnos_rrhh WHERE id = ? AND empresa_id = ?", (id, _empresa_id())).fetchone()
    fecha = turno["fecha"] if turno else None
    _safe_update(conn, "turnos_rrhh", id, {"estado": "anulado"}, "Anulado desde cuadrante")
    conn.commit()
    conn.close()
    flash("Turno anulado del cuadrante sin borrar histórico.", "success")
    return redirect(url_for("rrhh.cuadrantes", fecha=fecha))


@rrhh_bp.route("/vacaciones", methods=["GET", "POST"])
@login_required
def vacaciones():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
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
        fin = request.form.get("fecha_fin")
        estado = request.form.get("estado") if _usuario_es_global_rrhh() else "solicitada"
        if estado not in {"solicitada", "aprobada", "rechazada", "cancelada"}:
            estado = "solicitada"
        conn.execute(
            """
            INSERT INTO vacaciones_rrhh
            (empleado_id, fecha_inicio, fecha_fin, tipo, estado, dias, observaciones, empresa_id, usuario_creacion_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                empleado_id, inicio, fin, request.form.get("tipo") or "vacaciones", estado,
                _dias_entre(inicio, fin), request.form.get("observaciones"), empresa_id, session.get("user_id"),
            ),
        )
        conn.commit()
        conn.close()
        flash("Registro de vacaciones/ausencia guardado.", "success")
        return redirect(url_for("rrhh.vacaciones"))

    scope_sql, scope_params = _departamento_scope_sql("e")
    anio = _to_int(request.args.get("anio"), date.today().year)
    registros = conn.execute(
        f"""
        SELECT v.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos, '')) empleado_nombre,
               d.nombre departamento_nombre
        FROM vacaciones_rrhh v
        JOIN empleados e ON v.empleado_id = e.id
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        WHERE {scope_sql} AND v.empresa_id = ? AND strftime('%Y', v.fecha_inicio) = ?
        ORDER BY date(v.fecha_inicio) DESC, e.nombre
        """,
        scope_params + [empresa_id, str(anio)],
    ).fetchall()
    empleados = conn.execute(
        "SELECT id, nombre, apellidos FROM empleados WHERE empresa_id = ? AND COALESCE(activo,1)=1 ORDER BY nombre, apellidos",
        (empresa_id,),
    ).fetchall() if _usuario_es_global_rrhh() else []
    conn.close()
    return render_template("rrhh/vacaciones.html", registros=registros, empleados=empleados, anio=anio, puede_editar=_usuario_es_global_rrhh())


@rrhh_bp.route("/vacaciones/<int:id>/estado", methods=["POST"])
@login_required
@_require_rrhh_global
def cambiar_estado_vacaciones(id):
    estado = request.form.get("estado")
    if estado not in {"solicitada", "aprobada", "rechazada", "cancelada"}:
        estado = "solicitada"
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    conn.execute("UPDATE vacaciones_rrhh SET estado = ? WHERE id = ? AND empresa_id = ?", (estado, id, _empresa_id()))
    conn.commit()
    conn.close()
    flash("Estado actualizado.", "success")
    return redirect(url_for("rrhh.vacaciones"))



# ============================================================
# CUADRANTE REAL — estructura por establecimiento y departamento
# ============================================================

_DIAS_SEMANA = [
    (0, "Lunes"), (1, "Martes"), (2, "Miércoles"), (3, "Jueves"),
    (4, "Viernes"), (5, "Sábado"), (6, "Domingo"),
]


def _row_to_dict(row):
    return dict(row) if row is not None else {}


def _rows_to_dicts(rows):
    return [dict(r) for r in rows]


@rrhh_bp.route("/puestos")
@login_required
def lista_puestos_operativos():
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    empresa_id = _empresa_id()
    puestos = conn.execute(
        """
        SELECT p.*, ct.nombre centro_nombre, d.nombre departamento_nombre,
               sd.nombre subdepartamento_nombre, c.nombre categoria_nombre
        FROM puestos_operativos p
        LEFT JOIN centros_trabajo ct ON p.centro_id = ct.id
        LEFT JOIN departamentos d ON p.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON p.subdepartamento_id = sd.id
        LEFT JOIN categorias_laborales c ON p.categoria_id = c.id
        WHERE p.empresa_id = ?
        ORDER BY COALESCE(ct.nombre,''), COALESCE(d.nombre,''), COALESCE(sd.nombre,''), p.nombre
        """,
        (empresa_id,),
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
    centros = conn.execute("SELECT id, nombre FROM centros_trabajo WHERE empresa_id = ? AND COALESCE(activo,1)=1 ORDER BY nombre", (empresa_id,)).fetchall()
    departamentos = conn.execute("SELECT id, nombre FROM departamentos WHERE empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    subdepartamentos = conn.execute("SELECT id, nombre, departamento_id FROM subdepartamentos WHERE empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
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
        conn.execute(
            """
            INSERT INTO centros_trabajo (nombre, codigo, direccion, descripcion, empresa_id, activo)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (request.form.get("nombre"), request.form.get("codigo"), request.form.get("direccion"), request.form.get("descripcion"), empresa_id),
        )
        conn.commit()
        flash("Centro de trabajo creado.", "success")
        return redirect(url_for("rrhh.centros_trabajo"))
    centros = conn.execute("SELECT * FROM centros_trabajo WHERE empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    conn.close()
    return render_template("rrhh/centros_trabajo.html", centros=centros)


@rrhh_bp.route("/necesidades", methods=["GET", "POST"])
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
            (centro_id, departamento_id, subdepartamento_id, puesto_id, dia_semana, personas_necesarias, observaciones, empresa_id, activo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                request.form.get("centro_id") or None, request.form.get("departamento_id") or None,
                request.form.get("subdepartamento_id") or None, request.form.get("puesto_id") or None,
                _to_int(request.form.get("dia_semana"), 0), _to_int(request.form.get("personas_necesarias"), 1),
                request.form.get("observaciones"), empresa_id,
            ),
        )
        conn.commit()
        flash("Necesidad operativa guardada.", "success")
        return redirect(url_for("rrhh.necesidades_operativas"))
    necesidades = conn.execute(
        """
        SELECT n.*, ct.nombre centro_nombre, d.nombre departamento_nombre, sd.nombre subdepartamento_nombre, p.nombre puesto_nombre
        FROM necesidades_operativas n
        LEFT JOIN centros_trabajo ct ON n.centro_id = ct.id
        LEFT JOIN departamentos d ON n.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON n.subdepartamento_id = sd.id
        LEFT JOIN puestos_operativos p ON n.puesto_id = p.id
        WHERE n.empresa_id = ? AND COALESCE(n.activo,1)=1
        ORDER BY COALESCE(ct.nombre,''), COALESCE(d.nombre,''), COALESCE(sd.nombre,''), n.dia_semana, p.nombre
        """,
        (empresa_id,),
    ).fetchall()
    centros = conn.execute("SELECT id, nombre FROM centros_trabajo WHERE empresa_id = ? AND COALESCE(activo,1)=1 ORDER BY nombre", (empresa_id,)).fetchall()
    departamentos = conn.execute("SELECT id, nombre FROM departamentos WHERE empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    subdepartamentos = conn.execute("SELECT id, nombre, departamento_id FROM subdepartamentos WHERE empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    puestos = conn.execute("SELECT id, nombre, departamento_id, subdepartamento_id FROM puestos_operativos WHERE empresa_id = ? AND COALESCE(activo,1)=1 ORDER BY nombre", (empresa_id,)).fetchall()
    conn.close()
    return render_template("rrhh/necesidades_operativas.html", necesidades=necesidades, centros=centros, departamentos=departamentos, subdepartamentos=subdepartamentos, puestos=puestos, dias_semana=_DIAS_SEMANA)


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
        LIMIT 1
        """,
        (empresa_id, centro_id, departamento_id, subdepartamento_id, puesto_id, dia_destino),
    ).fetchone()

    if existente:
        if actualizar_existente:
            conn.execute(
                """
                UPDATE necesidades_operativas
                SET personas_necesarias = ?,
                    observaciones = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND empresa_id = ?
                """,
                (origen["personas_necesarias"], origen["observaciones"], existente["id"], empresa_id),
            )
            _registrar_historial(conn, "necesidades_operativas", existente["id"], "copiar_actualizando", "Necesidad copiada actualizando existente")
            return "actualizada"
        return "omitida"

    conn.execute(
        """
        INSERT INTO necesidades_operativas
        (centro_id, departamento_id, subdepartamento_id, puesto_id, dia_semana, personas_necesarias, observaciones, empresa_id, activo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (centro_id, departamento_id, subdepartamento_id, puesto_id, dia_destino, origen["personas_necesarias"], origen["observaciones"], empresa_id),
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
        return _to_int(valor, None)

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
    Cuadrante real corregido:
    - Los filtros deciden qué TRABAJADORES se muestran.
    - Los turnos se cargan por trabajador + semana, no por metadatos del turno.
      Así, al filtrar por departamento/subdepartamento no desaparecen turnos antiguos
      que fueron guardados sin departamento_id, centro_id o puesto_id.
    - Oculta turnos anulados.
    """
    conn = get_db()
    _ensure_cuadrantes_schema(conn)
    empresa_id = _empresa_id()
    inicio, fin = _rango_semana(request.args.get("fecha"))
    centro_id = request.args.get("centro_id") or ""
    departamento_id = request.args.get("departamento_id") or ""
    subdepartamento_id = request.args.get("subdepartamento_id") or ""
    puesto_id = request.args.get("puesto_id") or ""

    filtros = ["e.empresa_id = ?", "COALESCE(e.activo,1)=1"]
    params = [empresa_id]

    if departamento_id:
        filtros.append("e.departamento_id = ?")
        params.append(departamento_id)

    if subdepartamento_id:
        filtros.append("e.subdepartamento_id = ?")
        params.append(subdepartamento_id)

    if not _usuario_es_global_rrhh() and _session_departamento_id():
        filtros.append("e.departamento_id = ?")
        params.append(_session_departamento_id())

    empleados = conn.execute(
        f"""
        SELECT e.id, e.nombre, e.apellidos, e.departamento_id, e.subdepartamento_id,
               d.nombre departamento_nombre, sd.nombre subdepartamento_nombre, c.nombre categoria_nombre
        FROM empleados e
        LEFT JOIN departamentos d ON e.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON e.subdepartamento_id = sd.id
        LEFT JOIN categorias_laborales c ON e.categoria_id = c.id
        WHERE {' AND '.join(filtros)}
        ORDER BY COALESCE(d.nombre,''), COALESCE(sd.nombre,''), e.nombre, e.apellidos
        """,
        params,
    ).fetchall()

    empleado_ids = [e["id"] for e in empleados]
    turnos = []
    if empleado_ids:
        placeholders = ",".join(["?"] * len(empleado_ids))
        turnos = conn.execute(
            f"""
            SELECT t.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos,'')) empleado_nombre,
                   ct.nombre centro_nombre,
                   COALESCE(d_t.nombre, d_e.nombre) departamento_nombre,
                   COALESCE(sd_t.nombre, sd_e.nombre) subdepartamento_nombre,
                   p.nombre puesto_nombre
            FROM turnos_rrhh t
            JOIN empleados e ON t.empleado_id = e.id
            LEFT JOIN centros_trabajo ct ON t.centro_id = ct.id
            LEFT JOIN departamentos d_t ON t.departamento_id = d_t.id
            LEFT JOIN departamentos d_e ON e.departamento_id = d_e.id
            LEFT JOIN subdepartamentos sd_t ON t.subdepartamento_id = sd_t.id
            LEFT JOIN subdepartamentos sd_e ON e.subdepartamento_id = sd_e.id
            LEFT JOIN puestos_operativos p ON t.puesto_id = p.id
            WHERE t.empresa_id = ?
              AND t.empleado_id IN ({placeholders})
              AND date(t.fecha) BETWEEN date(?) AND date(?)
              AND COALESCE(t.estado, 'planificado') != 'anulado'
            ORDER BY date(t.fecha), e.nombre, t.hora_inicio
            """,
            [empresa_id] + empleado_ids + [inicio.isoformat(), fin.isoformat()],
        ).fetchall()

    vacaciones = []
    if empleado_ids:
        placeholders = ",".join(["?"] * len(empleado_ids))
        vacaciones = conn.execute(
            f"""
            SELECT v.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos,'')) empleado_nombre
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
        SELECT n.*, ct.nombre centro_nombre, d.nombre departamento_nombre, sd.nombre subdepartamento_nombre, p.nombre puesto_nombre
        FROM necesidades_operativas n
        LEFT JOIN centros_trabajo ct ON n.centro_id = ct.id
        LEFT JOIN departamentos d ON n.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON n.subdepartamento_id = sd.id
        LEFT JOIN puestos_operativos p ON n.puesto_id = p.id
        WHERE {' AND '.join(necesidades_where)}
        ORDER BY n.dia_semana, p.nombre
        """,
        necesidades_params,
    ).fetchall()

    centros = conn.execute("SELECT id, nombre FROM centros_trabajo WHERE empresa_id = ? AND COALESCE(activo,1)=1 ORDER BY nombre", (empresa_id,)).fetchall()
    departamentos = conn.execute("SELECT id, nombre FROM departamentos WHERE empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    subdepartamentos = conn.execute("SELECT id, nombre, departamento_id FROM subdepartamentos WHERE empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    puestos = conn.execute("SELECT id, nombre, centro_id, departamento_id, subdepartamento_id FROM puestos_operativos WHERE empresa_id = ? AND COALESCE(activo,1)=1 ORDER BY nombre", (empresa_id,)).fetchall()
    conn.close()

    dias = [inicio + timedelta(days=i) for i in range(7)]

    turnos_por_empleado_dia = {}
    for t in turnos:
        turno = dict(t)
        turnos_por_empleado_dia.setdefault((turno["empleado_id"], turno["fecha"]), []).append(turno)

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

    cobertura = {}
    for n in necesidades:
        key = (n["dia_semana"], n["puesto_id"] or 0, n["departamento_id"] or 0, n["subdepartamento_id"] or 0)
        cobertura[key] = cobertura.get(key, 0) + (n["personas_necesarias"] or 0)

    asignados = {}
    for t in turnos:
        try:
            dia_semana_turno = date.fromisoformat(t["fecha"]).weekday()
        except Exception:
            continue
        key = (dia_semana_turno, t["puesto_id"] or 0, t["departamento_id"] or 0, t["subdepartamento_id"] or 0)
        asignados[key] = asignados.get(key, 0) + 1

    return render_template(
        "rrhh/cuadrante_real.html",
        empleados=empleados, dias=dias, inicio=inicio, fin=fin,
        centros=centros, departamentos=departamentos, subdepartamentos=subdepartamentos, puestos=puestos,
        turnos_por_empleado_dia=turnos_por_empleado_dia,
        vacaciones_por_empleado_dia=vacaciones_por_empleado_dia,
        necesidades=necesidades, cobertura=cobertura, asignados=asignados,
        centro_id=centro_id, departamento_id=departamento_id, subdepartamento_id=subdepartamento_id, puesto_id=puesto_id,
        puede_editar=_usuario_es_global_rrhh(), timedelta=timedelta,
    )


@rrhh_bp.route("/cuadrantes/real/asignar", methods=["POST"])
@login_required
@_require_rrhh_global
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
            return redirect(url_for(
                "rrhh.cuadrante_real",
                fecha=fecha,
                centro_id=request.form.get("centro_id") or "",
                departamento_id=request.form.get("departamento_id") or "",
                subdepartamento_id=request.form.get("subdepartamento_id") or "",
                puesto_id=request.form.get("puesto_id") or "",
            ))
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
    return redirect(url_for(
        "rrhh.cuadrante_real",
        fecha=fecha,
        centro_id=request.form.get("filtro_centro_id") or request.form.get("centro_id") or "",
        departamento_id=request.form.get("filtro_departamento_id") or request.form.get("departamento_id") or "",
        subdepartamento_id=request.form.get("filtro_subdepartamento_id") or request.form.get("subdepartamento_id") or "",
        puesto_id=request.form.get("filtro_puesto_id") or request.form.get("puesto_id") or "",
    ))


# ============================================================
# FICHAJES
# ============================================================

@rrhh_bp.route("/fichajes")
@login_required
def lista_fichajes():
    conn = get_db()
    ensure_rrhh_schema(conn)
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
    conn.close()
    return render_template("rrhh/fichajes.html", fichajes=fichajes, empleados=empleados, puede_editar=_usuario_es_global_rrhh())


@rrhh_bp.route("/fichajes/registrar", methods=["POST"])
@login_required
def registrar_fichaje():
    conn = get_db()
    ensure_rrhh_schema(conn)
    empresa_id = _empresa_id()
    empleado_id = request.form.get("empleado_id") or session.get("user_empleado_id")
    tipo = request.form.get("tipo")
    if tipo not in {"entrada", "salida", "pausa", "reanudacion"}:
        tipo = "entrada"
    if not empleado_id:
        conn.close()
        flash("Tu usuario no está vinculado a ningún empleado.", "warning")
        return redirect(url_for("rrhh.lista_fichajes"))

    conn.execute(
        """
        INSERT INTO fichajes (empleado_id, tipo, fecha_hora, origen, observaciones, empresa_id, usuario_id)
        VALUES (?, ?, datetime('now', 'localtime'), ?, ?, ?, ?)
        """,
        (empleado_id, tipo, "manual", request.form.get("observaciones"), empresa_id, session.get("user_id")),
    )
    conn.commit()
    conn.close()
    flash(f"Fichaje de {tipo} registrado correctamente.", "success")
    return redirect(url_for("rrhh.lista_fichajes"))


# ============================================================
# ROLES Y PERMISOS
# ============================================================

@rrhh_bp.route("/roles")
@login_required
@_require_rrhh_global
def lista_roles():
    conn = get_db()
    ensure_rrhh_schema(conn)
    roles = conn.execute("SELECT * FROM roles ORDER BY nombre").fetchall() if _table_exists(conn, "roles") else []
    permisos = conn.execute("SELECT * FROM permisos ORDER BY nombre").fetchall() if _table_exists(conn, "permisos") else []
    roles_permisos = conn.execute("SELECT rol_id, permiso_id FROM roles_permisos").fetchall() if _table_exists(conn, "roles_permisos") else []
    asignados = {(r["rol_id"], r["permiso_id"]) for r in roles_permisos}
    mapa = {}
    permiso_por_id = {p["id"]: p["nombre"] for p in permisos}
    for rol_id, permiso_id in asignados:
        mapa.setdefault(rol_id, []).append(permiso_por_id.get(permiso_id, permiso_id))
    conn.close()
    return render_template("rrhh/roles.html", roles=roles, permisos=permisos, asignados=asignados, mapa=mapa)


@rrhh_bp.route("/roles/nuevo", methods=["POST"])
@login_required
@_require_rrhh_global
def nuevo_rol():
    nombre = (request.form.get("nombre") or "").strip()
    if not nombre:
        flash("El nombre del rol es obligatorio.", "warning")
        return redirect(url_for("rrhh.lista_roles"))
    conn = get_db()
    if not _table_exists(conn, "roles"):
        conn.execute("CREATE TABLE IF NOT EXISTS roles (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE)")
    conn.execute("INSERT OR IGNORE INTO roles (nombre) VALUES (?)", (nombre,))
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
    conn.execute("DELETE FROM roles_permisos WHERE rol_id = ?", (rol_id,))
    for permiso_id in permisos_ids:
        conn.execute("INSERT INTO roles_permisos (rol_id, permiso_id) VALUES (?, ?)", (rol_id, permiso_id))
    conn.commit()
    conn.close()
    flash("Permisos del rol actualizados.", "success")
    return redirect(url_for("rrhh.lista_roles"))


# ============================================================
# RRHH EDITABLE COMPLETO — rutas incrementales con trazabilidad
# ============================================================

def _ensure_editable_schema(conn):
    """Capa de edición segura. No borra datos: añade columnas y tabla de historial."""
    _ensure_cuadrantes_schema(conn)
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
    for table in [
        'empleados','categorias_laborales','contratos','nominas','documentos_rrhh','fichajes',
        'turnos_rrhh','variables_prenomina','vacaciones_rrhh','centros_trabajo','puestos_operativos','necesidades_operativas'
    ]:
        if _table_exists(conn, table):
            _add_column_if_missing(conn, table, 'updated_at TEXT')
            _add_column_if_missing(conn, table, 'created_by INTEGER')
            _add_column_if_missing(conn, table, 'updated_by INTEGER')
            _add_column_if_missing(conn, table, 'motivo_cambio TEXT')
    for table in ['documentos_rrhh','fichajes','turnos_rrhh','variables_prenomina','vacaciones_rrhh']:
        if _table_exists(conn, table):
            _add_column_if_missing(conn, table, "estado TEXT DEFAULT 'activo'")
    conn.commit()


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
    return conn.execute("SELECT id, nombre, apellidos FROM empleados WHERE empresa_id = ? AND COALESCE(activo,1)=1 ORDER BY nombre, apellidos", (_empresa_id(),)).fetchall()


def _departamentos_select(conn):
    return conn.execute("SELECT id, nombre FROM departamentos WHERE empresa_id = ? ORDER BY nombre", (_empresa_id(),)).fetchall() if _table_exists(conn, 'departamentos') else []


def _subdepartamentos_select(conn):
    return conn.execute("SELECT id, nombre, departamento_id FROM subdepartamentos WHERE empresa_id = ? ORDER BY nombre", (_empresa_id(),)).fetchall() if _table_exists(conn, 'subdepartamentos') else []


def _centros_select(conn):
    return conn.execute("SELECT id, nombre FROM centros_trabajo WHERE empresa_id = ? AND COALESCE(activo,1)=1 ORDER BY nombre", (_empresa_id(),)).fetchall()


def _puestos_select(conn):
    return conn.execute("SELECT id, nombre FROM puestos_operativos WHERE empresa_id = ? AND COALESCE(activo,1)=1 ORDER BY nombre", (_empresa_id(),)).fetchall()


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
    contrato=_get_row(conn,'contratos',id)
    if not contrato:
        conn.close(); flash('Contrato no encontrado.', 'warning'); return redirect(url_for('rrhh.lista_contratos'))
    if request.method=='POST':
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
    _safe_update(conn,'contratos',id,{'estado':'anulado'},request.form.get('motivo_cambio') or 'Contrato anulado')
    conn.commit(); conn.close(); flash('Contrato marcado como anulado.', 'success'); return redirect(url_for('rrhh.lista_contratos'))


@rrhh_bp.route('/nominas/<int:id>/editar', methods=['GET','POST'])
@login_required
@_require_rrhh_global
def editar_nomina(id):
    conn=get_db(); _ensure_editable_schema(conn)
    nomina=_get_row(conn,'nominas',id)
    if not nomina:
        conn.close(); flash('Nómina no encontrada.', 'warning'); return redirect(url_for('rrhh.lista_nominas'))
    if request.method=='POST':
        campos={k:request.form.get(k) for k in ['empleado_id','mes','anio','periodo_desde','periodo_hasta','salario_base','complementos','horas_extra','pluses','prorrata_pagas','irpf','seguridad_social_trabajador','seguridad_social_empresa','anticipos_embargos','bruto','neto','coste_empresa','estado','fecha_pago','forma_pago','archivo','observaciones']}
        _safe_update(conn,'nominas',id,campos,request.form.get('motivo_cambio'))
        conn.commit(); conn.close(); flash('Nómina actualizada.', 'success'); return redirect(url_for('rrhh.lista_nominas'))
    empleados=_empleados_select(conn); conn.close(); return render_template('rrhh/nomina_form.html', nomina=nomina, empleados=empleados)


@rrhh_bp.route('/nominas/<int:id>/anular', methods=['POST'])
@login_required
@_require_rrhh_global
def anular_nomina(id):
    conn=get_db(); _ensure_editable_schema(conn)
    _safe_update(conn,'nominas',id,{'estado':'anulada'},request.form.get('motivo_cambio') or 'Nómina anulada')
    conn.commit(); conn.close(); flash('Nómina anulada sin borrar histórico.', 'success'); return redirect(url_for('rrhh.lista_nominas'))


@rrhh_bp.route('/documentos/<int:id>/editar', methods=['GET','POST'])
@login_required
@_require_rrhh_global
def editar_documento(id):
    conn=get_db(); _ensure_editable_schema(conn)
    documento=_get_row(conn,'documentos_rrhh',id)
    if not documento:
        conn.close(); flash('Documento no encontrado.', 'warning'); return redirect(url_for('rrhh.lista_documentos'))
    if request.method=='POST':
        campos={k:request.form.get(k) for k in ['empleado_id','tipo_documento','nombre','fecha_documento','fecha_caducidad','archivo','estado','observaciones']}
        _safe_update(conn,'documentos_rrhh',id,campos,request.form.get('motivo_cambio'))
        conn.commit(); conn.close(); flash('Documento actualizado.', 'success'); return redirect(url_for('rrhh.lista_documentos'))
    empleados=_empleados_select(conn); conn.close(); return render_template('rrhh/documento_form.html', documento=documento, empleados=empleados)


@rrhh_bp.route('/documentos/<int:id>/anular', methods=['POST'])
@login_required
@_require_rrhh_global
def anular_documento(id):
    conn=get_db(); _ensure_editable_schema(conn)
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
        conn.commit(); conn.close(); flash('Turno actualizado. Recalcula pre-nómina si afecta al mes.', 'success'); return redirect(url_for('rrhh.cuadrante_real', fecha=request.form.get('fecha') or date.today().isoformat()))
    empleados=_empleados_select(conn); departamentos=_departamentos_select(conn); subdepartamentos=_subdepartamentos_select(conn); centros=_centros_select(conn); puestos=_puestos_select(conn); conn.close()
    return render_template('rrhh/turno_form.html', turno=turno, empleados=empleados, departamentos=departamentos, subdepartamentos=subdepartamentos, centros=centros, puestos=puestos)


@rrhh_bp.route('/turnos/<int:id>/anular', methods=['POST'])
@login_required
@_require_rrhh_global
def anular_turno_rrhh(id):
    conn=get_db(); _ensure_editable_schema(conn)
    turno=_get_row(conn,'turnos_rrhh',id)
    fecha=turno['fecha'] if turno else date.today().isoformat()
    if _column_exists(conn,'turnos_rrhh','estado'):
        _safe_update(conn,'turnos_rrhh',id,{'estado':'anulado'},request.form.get('motivo_cambio') or 'Turno anulado')
    else:
        conn.execute('DELETE FROM turnos_rrhh WHERE id=? AND empresa_id=?',(id,_empresa_id()))
    conn.commit(); conn.close(); flash('Turno anulado.', 'success'); return redirect(url_for('rrhh.cuadrante_real', fecha=fecha))


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
    conn=get_db(); _ensure_editable_schema(conn)
    registro=_get_row(conn,'vacaciones_rrhh',id)
    if not registro:
        conn.close(); flash('Registro no encontrado.', 'warning'); return redirect(url_for('rrhh.vacaciones'))
    if request.method=='POST':
        dias=_dias_entre(request.form.get('fecha_inicio'), request.form.get('fecha_fin'))
        _safe_update(conn,'vacaciones_rrhh',id,{'empleado_id':request.form.get('empleado_id'),'fecha_inicio':request.form.get('fecha_inicio'),'fecha_fin':request.form.get('fecha_fin'),'tipo':request.form.get('tipo'),'estado':request.form.get('estado'),'dias':dias,'observaciones':request.form.get('observaciones')},request.form.get('motivo_cambio'))
        conn.commit(); conn.close(); flash('Vacaciones/ausencia actualizada.', 'success'); return redirect(url_for('rrhh.vacaciones'))
    empleados=_empleados_select(conn); conn.close(); return render_template('rrhh/vacacion_form.html', registro=registro, empleados=empleados)


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
        campos={k:request.form.get(k) for k in ['nombre','descripcion','centro_id','departamento_id','subdepartamento_id','categoria_id']}
        for k in ['centro_id','departamento_id','subdepartamento_id','categoria_id']:
            if campos.get(k)=='': campos[k]=None
        campos['activo']=1 if request.form.get('activo') else 0
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
        _safe_update(conn,'centros_trabajo',id,{'nombre':request.form.get('nombre'),'codigo':request.form.get('codigo'),'direccion':request.form.get('direccion'),'descripcion':request.form.get('descripcion'),'activo':1 if request.form.get('activo') else 0},request.form.get('motivo_cambio'))
        conn.commit(); conn.close(); flash('Centro actualizado.', 'success'); return redirect(url_for('rrhh.centros_trabajo'))
    conn.close(); return render_template('rrhh/centro_form.html', centro=centro)


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
    conn=get_db(); _ensure_editable_schema(conn)
    necesidad=_get_row(conn,'necesidades_operativas',id)
    if not necesidad:
        conn.close(); flash('Necesidad no encontrada.', 'warning'); return redirect(url_for('rrhh.necesidades_operativas'))
    if request.method=='POST':
        campos={k:request.form.get(k) for k in ['centro_id','departamento_id','subdepartamento_id','puesto_id','dia_semana','personas_necesarias','observaciones']}
        for k in ['centro_id','departamento_id','subdepartamento_id','puesto_id']:
            if campos.get(k)=='': campos[k]=None
        campos['activo']=1 if request.form.get('activo') else 0
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
    conn=get_db(); _ensure_editable_schema(conn)
    fichaje=_get_row(conn,'fichajes',id)
    if not fichaje:
        conn.close(); flash('Fichaje no encontrado.', 'warning'); return redirect(url_for('rrhh.lista_fichajes'))
    if request.method=='POST':
        motivo=request.form.get('motivo_cambio') or 'Corrección manual de fichaje'
        _safe_update(conn,'fichajes',id,{'empleado_id':request.form.get('empleado_id'),'tipo':request.form.get('tipo'),'fecha_hora':request.form.get('fecha_hora'),'origen':'corregido','observaciones':request.form.get('observaciones'),'estado':request.form.get('estado') or 'corregido'},motivo)
        conn.commit(); conn.close(); flash('Fichaje corregido con histórico.', 'success'); return redirect(url_for('rrhh.lista_fichajes'))
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
@_require_rrhh_global
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
    return redirect(url_for(
        'rrhh.cuadrante_real',
        fecha=campos['fecha'],
        centro_id=request.form.get('filtro_centro_id') or request.form.get('centro_id') or '',
        departamento_id=request.form.get('filtro_departamento_id') or request.form.get('departamento_id') or '',
        subdepartamento_id=request.form.get('filtro_subdepartamento_id') or request.form.get('subdepartamento_id') or '',
        puesto_id=request.form.get('filtro_puesto_id') or request.form.get('puesto_id') or '',
    ))

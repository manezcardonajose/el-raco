# blueprints/departamentos/routes.py
from flask import render_template, request, redirect, url_for, session, flash
from database import get_db
from . import departamentos_bp
from blueprints.auth.routes import login_required
from helpers.centros_helper import centro_activo_id


CAPACIDADES_DEPARTAMENTO = [
    ("es_cocina", "Cocina propia"),
    ("independiente_cocina", "Cocina independiente"),
    ("gestiona_appcc", "APPCC propio"),
    ("gestiona_stock", "Stock / almacenes propios"),
    ("gestiona_mantenimiento", "Mantenimiento propio"),
    ("permite_tpv", "TPV / punto de venta"),
    ("permite_pedidos_internos", "Pedidos internos"),
    ("permite_pedidos_externos", "Pedidos externos"),
]


def _table_columns(conn, table):
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception:
        return []


def _tables_with_column(conn, column):
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    result = []
    for row in tables:
        table = row["name"]
        if column in _table_columns(conn, table):
            result.append(table)
    return result


def _count_column_refs(conn, column, value, empresa_id, exclude=None):
    exclude = set(exclude or [])
    refs = []
    for table in _tables_with_column(conn, column):
        if table in exclude:
            continue
        cols = _table_columns(conn, table)
        params = [value]
        empresa_filter = ""
        if "empresa_id" in cols:
            empresa_filter = " AND empresa_id = ?"
            params.append(empresa_id)
        try:
            count = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = ?{empresa_filter}",
                params,
            ).fetchone()["n"]
        except Exception:
            count = 0
        if count:
            refs.append((table, count))
    return refs


def _add_column_if_missing(conn, table, definition):
    name = definition.strip().split()[0]
    if name not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _ensure_departamentos_schema(conn):
    _add_column_if_missing(conn, "departamentos", "centro_id INTEGER")
    for definition in [
        "tipo_operativo TEXT DEFAULT 'general'",
        "departamento_padre_id INTEGER",
        "es_cocina INTEGER DEFAULT 0",
        "independiente_cocina INTEGER DEFAULT 0",
        "gestiona_appcc INTEGER DEFAULT 0",
        "gestiona_stock INTEGER DEFAULT 0",
        "gestiona_mantenimiento INTEGER DEFAULT 0",
        "permite_tpv INTEGER DEFAULT 0",
        "permite_pedidos_internos INTEGER DEFAULT 0",
        "permite_pedidos_externos INTEGER DEFAULT 0",
        "plantilla_operativa TEXT",
    ]:
        _add_column_if_missing(conn, "departamentos", definition)
    for table in ["subdepartamentos", "almacenes", "puntos_venta"]:
        _add_column_if_missing(conn, table, "centro_id INTEGER")


def _form_capacidades():
    return {key: 1 if request.form.get(key) else 0 for key, _ in CAPACIDADES_DEPARTAMENTO}


def _tipo_operativo_desde_form():
    tipo = (request.form.get("tipo_operativo") or "general").strip().lower()
    return tipo if tipo in {"general", "cocina", "restaurante", "bar", "mantenimiento", "pisos", "recepcion", "almacen"} else "general"


def _departamento_accesible(conn, departamento_id, empresa_id):
    centro = centro_activo_id()
    sql_centro = " AND centro_id = ?" if centro else ""
    params = [departamento_id, empresa_id] + ([centro] if centro else [])
    return conn.execute(
        f"SELECT * FROM departamentos WHERE id = ? AND empresa_id = ?{sql_centro}",
        params,
    ).fetchone()


def _subdepartamento_accesible(conn, subdepartamento_id, empresa_id):
    centro = centro_activo_id()
    sql_centro = " AND centro_id = ?" if centro else ""
    params = [subdepartamento_id, empresa_id] + ([centro] if centro else [])
    return conn.execute(
        f"SELECT * FROM subdepartamentos WHERE id = ? AND empresa_id = ?{sql_centro}",
        params,
    ).fetchone()


def _crear_subdepartamento_si_falta(conn, empresa_id, departamento_id, nombre, descripcion="", centro_id=None):
    row = conn.execute(
        """
        SELECT id
        FROM subdepartamentos
        WHERE empresa_id = ? AND departamento_id = ? AND LOWER(nombre) = LOWER(?)
        LIMIT 1
        """,
        (empresa_id, departamento_id, nombre),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE subdepartamentos SET activo = 1 WHERE id = ? AND empresa_id = ?",
            (row["id"], empresa_id),
        )
        return row["id"]
    cur = conn.execute(
        """
        INSERT INTO subdepartamentos (departamento_id, nombre, descripcion, activo, empresa_id, centro_id)
        VALUES (?, ?, ?, 1, ?, ?)
        """,
        (departamento_id, nombre, descripcion, empresa_id, centro_id),
    )
    return cur.lastrowid


def _crear_almacen_si_falta(conn, empresa_id, departamento_id, nombre, tipo="seco", subdepartamento_id=None, requiere_temp=0, centro_id=None):
    row = conn.execute(
        """
        SELECT id
        FROM almacenes
        WHERE empresa_id = ? AND departamento_id = ? AND LOWER(nombre) = LOWER(?)
        LIMIT 1
        """,
        (empresa_id, departamento_id, nombre),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE almacenes SET activo = 1 WHERE id = ? AND empresa_id = ?",
            (row["id"], empresa_id),
        )
        return row["id"]
    cur = conn.execute(
        """
        INSERT INTO almacenes
        (nombre, tipo, ubicacion, responsable, activo, empresa_id, departamento_id,
         subdepartamento_id, requiere_control_temperatura, rango_min, rango_max, centro_id)
        VALUES (?, ?, ?, '', 1, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            nombre,
            tipo,
            nombre,
            empresa_id,
            departamento_id,
            subdepartamento_id,
            requiere_temp,
            0 if requiere_temp else None,
            5 if requiere_temp else None,
            centro_id,
        ),
    )
    return cur.lastrowid


def _crear_control_appcc_si_falta(conn, empresa_id, departamento, etapa, tipo_control, limite, frecuencia):
    codigo = f"{departamento['id']}-{etapa}".lower().replace(" ", "-")[:40]
    row = conn.execute(
        """
        SELECT id
        FROM appcc_controles_plan
        WHERE empresa_id = ? AND codigo = ?
        LIMIT 1
        """,
        (empresa_id, codigo),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE appcc_controles_plan SET activo = 1 WHERE id = ? AND empresa_id = ?",
            (row["id"], empresa_id),
        )
        return row["id"]
    conn.execute(
        """
        INSERT INTO appcc_controles_plan
        (empresa_id, codigo, area, etapa, tipo_control, peligro, medida_preventiva,
         limite_critico, frecuencia, responsable, accion_correctiva, verificacion,
         modulo_origen, activo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'departamentos', 1)
        """,
        (
            empresa_id,
            codigo,
            departamento["nombre"],
            etapa,
            tipo_control,
            "Riesgo sanitario operativo",
            "Control y registro por responsable del departamento",
            limite,
            frecuencia,
            departamento["nombre"],
            "Bloquear producto/equipo, avisar responsable y registrar incidencia",
            "Revision diaria o por responsable APPCC",
        ),
    )


def _crear_base_operativa_departamento(conn, empresa_id, departamento_id):
    departamento = conn.execute(
        "SELECT * FROM departamentos WHERE id = ? AND empresa_id = ?",
        (departamento_id, empresa_id),
    ).fetchone()
    if not departamento:
        return
    tipo = departamento["tipo_operativo"] if "tipo_operativo" in departamento.keys() else "general"
    centro_id = departamento["centro_id"] if "centro_id" in departamento.keys() else None
    es_cocina = int(departamento["es_cocina"] or 0) == 1
    gestiona_stock = int(departamento["gestiona_stock"] or 0) == 1
    gestiona_appcc = int(departamento["gestiona_appcc"] or 0) == 1
    gestiona_mantenimiento = int(departamento["gestiona_mantenimiento"] or 0) == 1
    tiene_cocina_operativa = es_cocina or tipo == "cocina"

    sub_cocina = None
    sub_frio = None
    if tiene_cocina_operativa:
        sub_cocina = _crear_subdepartamento_si_falta(conn, empresa_id, departamento_id, "Cocina caliente", "Partidas calientes y elaboraciones", centro_id)
        sub_frio = _crear_subdepartamento_si_falta(conn, empresa_id, departamento_id, "Cuarto frio", "Entrantes frios, postres y preparaciones frias", centro_id)
    if tipo in {"recepcion"}:
        _crear_subdepartamento_si_falta(conn, empresa_id, departamento_id, "Reservas", "Reservas, canales y cupos", centro_id)
        _crear_subdepartamento_si_falta(conn, empresa_id, departamento_id, "Check-in / Check-out", "Llegadas, salidas y caja de recepcion", centro_id)
    if tipo in {"restaurante", "bar"}:
        _crear_subdepartamento_si_falta(conn, empresa_id, departamento_id, "Sala", "Servicio, TPV y atencion al cliente", centro_id)
    if gestiona_mantenimiento:
        _crear_subdepartamento_si_falta(conn, empresa_id, departamento_id, "Mantenimiento", "Averias, preventivos y activos del departamento", centro_id)
    if gestiona_stock:
        _crear_almacen_si_falta(conn, empresa_id, departamento_id, f"Almacen {departamento['nombre']}", "seco", centro_id=centro_id)
        if tiene_cocina_operativa:
            _crear_almacen_si_falta(conn, empresa_id, departamento_id, f"Camara frio {departamento['nombre']}", "frio", sub_frio, 1, centro_id)
            _crear_almacen_si_falta(conn, empresa_id, departamento_id, f"Congelador {departamento['nombre']}", "congelado", sub_cocina, 1, centro_id)
    if gestiona_appcc:
        _crear_control_appcc_si_falta(conn, empresa_id, departamento, "Limpieza del departamento", "limpieza", "Zona limpia y desinfectada", "diaria")
        if tiene_cocina_operativa:
            _crear_control_appcc_si_falta(conn, empresa_id, departamento, "Temperatura camaras", "temperatura", "Frio 0-5 C / congelado <= -18 C", "diaria")
            _crear_control_appcc_si_falta(conn, empresa_id, departamento, "Descongelado carnes y pescados", "descongelado", "Producto identificado, temperatura controlada y trazabilidad", "por lote")


def crear_estructura_base_centro(conn, empresa_id, centro_id):
    _ensure_departamentos_schema(conn)
    base = [
        ("Recepcion", "recepcion", "Front desk, reservas, caja y atencion al cliente", {}),
        ("Pisos", "pisos", "Habitaciones, limpieza, lenceria y amenities", {}),
        ("Mantenimiento", "mantenimiento", "Averias, preventivos y activos", {"gestiona_mantenimiento": 1, "permite_pedidos_internos": 1, "permite_pedidos_externos": 1}),
        ("Compras / Almacen", "almacen", "Economato, compras, recepciones y stock central", {"gestiona_stock": 1, "permite_pedidos_externos": 1}),
        ("Cocina central", "cocina", "Produccion central, buffet y preparaciones", {"es_cocina": 1, "gestiona_appcc": 1, "gestiona_stock": 1, "permite_pedidos_internos": 1, "permite_pedidos_externos": 1}),
        ("Bares", "bar", "Barras, bebidas y servicio", {"gestiona_appcc": 1, "gestiona_stock": 1, "permite_tpv": 1, "permite_pedidos_internos": 1}),
        ("Restaurantes", "restaurante", "Salas, cartas y puntos de venta", {"gestiona_appcc": 1, "gestiona_stock": 1, "permite_tpv": 1, "permite_pedidos_internos": 1}),
        ("Administracion", "general", "Direccion, administracion y contabilidad", {"permite_pedidos_externos": 1}),
        ("RRHH", "general", "Personal, cuadrantes y documentacion laboral", {}),
    ]
    creados = []
    for nombre, tipo, descripcion, caps in base:
        row = conn.execute(
            """
            SELECT id
            FROM departamentos
            WHERE empresa_id = ? AND centro_id = ? AND LOWER(nombre) = LOWER(?)
            LIMIT 1
            """,
            (empresa_id, centro_id, nombre),
        ).fetchone()
        values = {
            "es_cocina": caps.get("es_cocina", 0),
            "independiente_cocina": caps.get("independiente_cocina", 0),
            "gestiona_appcc": caps.get("gestiona_appcc", 0),
            "gestiona_stock": caps.get("gestiona_stock", 0),
            "gestiona_mantenimiento": caps.get("gestiona_mantenimiento", 0),
            "permite_tpv": caps.get("permite_tpv", 0),
            "permite_pedidos_internos": caps.get("permite_pedidos_internos", 0),
            "permite_pedidos_externos": caps.get("permite_pedidos_externos", 0),
        }
        if row:
            departamento_id = row["id"]
            conn.execute(
                """
                UPDATE departamentos
                SET activo = 1, tipo_operativo = ?, descripcion = ?,
                    es_cocina = ?, independiente_cocina = ?, gestiona_appcc = ?,
                    gestiona_stock = ?, gestiona_mantenimiento = ?, permite_tpv = ?,
                    permite_pedidos_internos = ?, permite_pedidos_externos = ?
                WHERE id = ? AND empresa_id = ?
                """,
                (
                    tipo, descripcion, values["es_cocina"], values["independiente_cocina"],
                    values["gestiona_appcc"], values["gestiona_stock"], values["gestiona_mantenimiento"],
                    values["permite_tpv"], values["permite_pedidos_internos"],
                    values["permite_pedidos_externos"], departamento_id, empresa_id,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO departamentos
                (nombre, descripcion, activo, empresa_id, centro_id, tipo_operativo,
                 es_cocina, independiente_cocina, gestiona_appcc, gestiona_stock,
                 gestiona_mantenimiento, permite_tpv, permite_pedidos_internos,
                 permite_pedidos_externos)
                VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    nombre, descripcion, empresa_id, centro_id, tipo, values["es_cocina"],
                    values["independiente_cocina"], values["gestiona_appcc"], values["gestiona_stock"],
                    values["gestiona_mantenimiento"], values["permite_tpv"],
                    values["permite_pedidos_internos"], values["permite_pedidos_externos"],
                ),
            )
            departamento_id = cur.lastrowid
            creados.append(departamento_id)
        _crear_base_operativa_departamento(conn, empresa_id, departamento_id)
    return creados


def _clear_column_refs(conn, column, value, empresa_id, tables):
    for table in tables:
        cols = _table_columns(conn, table)
        if column not in cols:
            continue
        params = [value]
        empresa_filter = ""
        if "empresa_id" in cols:
            empresa_filter = " AND empresa_id = ?"
            params.append(empresa_id)
        conn.execute(
            f"UPDATE {table} SET {column} = NULL WHERE {column} = ?{empresa_filter}",
            params,
        )


# =========================
# INDEX / LISTADO
# =========================
@departamentos_bp.route('/')
@login_required
def index():
    conn = get_db()
    _ensure_departamentos_schema(conn)
    conn.commit()
    empresa_id = session.get('user_empresa_id', 1)
    mostrar_inactivos = request.args.get("inactivos") == "1"
    centro_activo = centro_activo_id()
    centro_id = request.args.get("centro_id") or (str(centro_activo) if centro_activo else "")
    centro_sql = ""
    params_extra = []
    if centro_id.isdigit():
        centro_sql = " AND d.centro_id = ?"
        params_extra.append(int(centro_id))

    departamentos = conn.execute(f"""
        SELECT
            d.*,
            (
                SELECT COUNT(*)
                FROM subdepartamentos sd
                WHERE sd.departamento_id = d.id
                  AND (? = 1 OR COALESCE(sd.activo, 1) = 1)
            ) AS num_sub,
            (
                SELECT COUNT(*)
                FROM (
                    SELECT pv.id
                    FROM puntos_venta pv
                    WHERE pv.departamento_id = d.id
                      AND (? = 1 OR COALESCE(pv.activo, 1) = 1)

                    UNION

                    SELECT pvd.punto_venta_id
                    FROM punto_venta_departamentos pvd
                    WHERE pvd.departamento_id = d.id
                ) x
            ) AS num_puntos
        FROM departamentos d
        WHERE d.empresa_id = ?
          AND (? = 1 OR COALESCE(d.activo, 1) = 1)
          {centro_sql}
        ORDER BY d.nombre
    """, (1 if mostrar_inactivos else 0, 1 if mostrar_inactivos else 0, empresa_id, 1 if mostrar_inactivos else 0, *params_extra)).fetchall()
    if "centros_trabajo" in [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
        if centro_activo:
            centros = conn.execute(
                "SELECT id, nombre FROM centros_trabajo WHERE empresa_id = ? AND id = ? AND COALESCE(activo,1)=1 ORDER BY nombre",
                (empresa_id, centro_activo),
            ).fetchall()
        else:
            centros = conn.execute(
                "SELECT id, nombre FROM centros_trabajo WHERE empresa_id = ? AND COALESCE(activo,1)=1 ORDER BY nombre",
                (empresa_id,),
            ).fetchall()
    else:
        centros = []

    conn.close()
    return render_template(
        'departamentos/index.html',
        departamentos=departamentos,
        mostrar_inactivos=mostrar_inactivos,
        centros=centros,
        centro_id=centro_id,
        centro_activo_id=centro_activo,
    )


# Alias por compatibilidad si en algún sitio llamas a listar
@departamentos_bp.route('/listar')
@login_required
def listar_departamentos():
    return redirect(url_for('departamentos.index'))


# =========================
# VER DETALLE
# =========================
@departamentos_bp.route('/<int:id>')
@login_required
def ver_departamento(id):
    conn = get_db()
    _ensure_departamentos_schema(conn)
    conn.commit()
    empresa_id = session.get('user_empresa_id', 1)

    departamento = _departamento_accesible(conn, id, empresa_id)

    if not departamento:
        conn.close()
        flash('❌ Departamento no encontrado', 'danger')
        return redirect(url_for('departamentos.index'))

    subdepartamentos = conn.execute("""
        SELECT *
        FROM subdepartamentos
        WHERE departamento_id = ? AND empresa_id = ?
        ORDER BY nombre
    """, (id, empresa_id)).fetchall()

    puntos_venta = conn.execute("""
        SELECT DISTINCT
            pv.*,
            COALESCE(pvd.rol_operativo, 'principal') AS rol_operativo,
            sd.nombre AS subdepartamento_nombre,
            a.nombre AS almacen_nombre
        FROM puntos_venta pv
        LEFT JOIN punto_venta_departamentos pvd
            ON pvd.punto_venta_id = pv.id
           AND pvd.departamento_id = ?
        LEFT JOIN subdepartamentos sd
            ON pv.subdepartamento_id = sd.id
        LEFT JOIN almacenes a
            ON pv.almacen_id = a.id
        WHERE pv.empresa_id = ?
          AND (
                pv.departamento_id = ?
                OR pvd.departamento_id = ?
          )
        ORDER BY pv.nombre
    """, (id, empresa_id, id, id)).fetchall()

    almacenes = conn.execute("""
        SELECT
            a.*,
            sd.nombre AS subdepartamento_nombre
        FROM almacenes a
        LEFT JOIN subdepartamentos sd
            ON a.subdepartamento_id = sd.id
        WHERE a.departamento_id = ? AND a.empresa_id = ?
        ORDER BY a.nombre
    """, (id, empresa_id)).fetchall()
    controles_appcc = conn.execute("""
        SELECT *
        FROM appcc_controles_plan
        WHERE empresa_id = ?
          AND (LOWER(area) = LOWER(?) OR codigo LIKE ?)
          AND COALESCE(activo, 1) = 1
        ORDER BY etapa, tipo_control
    """, (empresa_id, departamento["nombre"], f"{id}-%")).fetchall()
    equipos_mantenimiento = conn.execute("""
        SELECT *
        FROM equipos_mantenimiento
        WHERE empresa_id = ? AND departamento_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY nombre
        LIMIT 12
    """, (empresa_id, id)).fetchall()

    conn.close()

    return render_template(
        'departamentos/ver.html',
        departamento=departamento,
        subdepartamentos=subdepartamentos,
        puntos_venta=puntos_venta,
        almacenes=almacenes,
        controles_appcc=controles_appcc,
        equipos_mantenimiento=equipos_mantenimiento,
        capacidades=CAPACIDADES_DEPARTAMENTO,
    )


# Alias por compatibilidad con la ruta vieja /ver/<id>
@departamentos_bp.route('/ver/<int:id>')
@login_required
def ver_departamento_alias(id):
    return redirect(url_for('departamentos.ver_departamento', id=id))


# =========================
# NUEVO DEPARTAMENTO
# =========================
@departamentos_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_departamento():
    conn = get_db()
    _ensure_departamentos_schema(conn)
    conn.commit()
    empresa_id = session.get('user_empresa_id', 1)
    centro_usuario = centro_activo_id()
    centro_preseleccionado = str(centro_usuario or request.args.get("centro_id") or "")

    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        descripcion = request.form.get('descripcion', '').strip()
        tipo_operativo = _tipo_operativo_desde_form()
        capacidades = _form_capacidades()
        padre_id = request.form.get("departamento_padre_id") or None

        centro_id = centro_usuario or request.form.get("centro_id") or None
        if centro_id and not conn.execute(
            "SELECT 1 FROM centros_trabajo WHERE id=? AND empresa_id=? AND COALESCE(activo,1)=1",
            (centro_id, empresa_id),
        ).fetchone():
            conn.close()
            flash('Centro de trabajo no valido.', 'danger')
            return redirect(url_for('departamentos.nuevo_departamento'))
        if padre_id and not _departamento_accesible(conn, padre_id, empresa_id):
            padre_id = None
        cur = conn.execute("""
            INSERT INTO departamentos
            (nombre, descripcion, activo, empresa_id, centro_id, tipo_operativo, departamento_padre_id,
             es_cocina, independiente_cocina, gestiona_appcc, gestiona_stock,
             gestiona_mantenimiento, permite_tpv, permite_pedidos_internos,
             permite_pedidos_externos)
            VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            nombre, descripcion, empresa_id, centro_id, tipo_operativo, padre_id,
            capacidades["es_cocina"], capacidades["independiente_cocina"],
            capacidades["gestiona_appcc"], capacidades["gestiona_stock"],
            capacidades["gestiona_mantenimiento"], capacidades["permite_tpv"],
            capacidades["permite_pedidos_internos"], capacidades["permite_pedidos_externos"],
        ))
        if request.form.get("crear_base_operativa"):
            _crear_base_operativa_departamento(conn, empresa_id, cur.lastrowid)

        conn.commit()
        conn.close()

        flash('✅ Departamento creado correctamente', 'success')
        if centro_id:
            return redirect(url_for('departamentos.index', centro_id=centro_id))
        return redirect(url_for('departamentos.ver_departamento', id=cur.lastrowid))

    filtro_centro = " AND centro_id = ?" if centro_usuario else ""
    parametros = [empresa_id] + ([centro_usuario] if centro_usuario else [])
    departamentos = conn.execute(
        f"SELECT id, nombre FROM departamentos WHERE empresa_id = ?{filtro_centro} AND COALESCE(activo, 1) = 1 ORDER BY nombre",
        parametros,
    ).fetchall()
    centros = conn.execute(
        f"SELECT id, nombre FROM centros_trabajo WHERE empresa_id = ?{filtro_centro} AND COALESCE(activo, 1) = 1 ORDER BY nombre",
        parametros,
    ).fetchall()
    conn.close()
    return render_template('departamentos/nuevo.html', capacidades=CAPACIDADES_DEPARTAMENTO, departamentos=departamentos, centros=centros, centro_preseleccionado=centro_preseleccionado)


# =========================
# EDITAR DEPARTAMENTO
# =========================
@departamentos_bp.route('/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar_departamento(id):
    conn = get_db()
    _ensure_departamentos_schema(conn)
    conn.commit()
    empresa_id = session.get('user_empresa_id', 1)

    departamento = _departamento_accesible(conn, id, empresa_id)

    if not departamento:
        conn.close()
        flash('❌ Departamento no encontrado', 'danger')
        return redirect(url_for('departamentos.index'))

    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        descripcion = request.form.get('descripcion', '').strip()
        activo = 1 if request.form.get('activo') else 0
        tipo_operativo = _tipo_operativo_desde_form()
        capacidades = _form_capacidades()
        padre_id = request.form.get("departamento_padre_id") or None
        centro_id = centro_activo_id() or request.form.get("centro_id") or None

        conn.execute("""
            UPDATE departamentos
            SET nombre = ?, descripcion = ?, activo = ?, tipo_operativo = ?,
                centro_id = ?, departamento_padre_id = ?, es_cocina = ?, independiente_cocina = ?,
                gestiona_appcc = ?, gestiona_stock = ?, gestiona_mantenimiento = ?,
                permite_tpv = ?, permite_pedidos_internos = ?, permite_pedidos_externos = ?
            WHERE id = ? AND empresa_id = ?
        """, (
            nombre, descripcion, activo, tipo_operativo, centro_id, padre_id,
            capacidades["es_cocina"], capacidades["independiente_cocina"],
            capacidades["gestiona_appcc"], capacidades["gestiona_stock"],
            capacidades["gestiona_mantenimiento"], capacidades["permite_tpv"],
            capacidades["permite_pedidos_internos"], capacidades["permite_pedidos_externos"],
            id, empresa_id,
        ))
        if request.form.get("crear_base_operativa"):
            _crear_base_operativa_departamento(conn, empresa_id, id)

        conn.commit()
        conn.close()

        flash('✅ Departamento actualizado correctamente', 'success')
        return redirect(url_for('departamentos.ver_departamento', id=id))

    departamentos = conn.execute(
        "SELECT id, nombre FROM departamentos WHERE empresa_id = ? AND id <> ? AND COALESCE(activo, 1) = 1 ORDER BY nombre",
        (empresa_id, id),
    ).fetchall()
    centros = conn.execute(
        "SELECT id, nombre FROM centros_trabajo WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 ORDER BY nombre",
        (empresa_id,),
    ).fetchall()
    conn.close()
    return render_template(
        'departamentos/editar.html',
        departamento=departamento,
        capacidades=CAPACIDADES_DEPARTAMENTO,
        departamentos=departamentos,
        centros=centros,
    )


# Alias por compatibilidad con la ruta vieja /editar/<id>
@departamentos_bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_departamento_alias(id):
    return editar_departamento(id)


# =========================
# ELIMINAR DEPARTAMENTO
# =========================
@departamentos_bp.route('/<int:id>/eliminar', methods=['POST'])
@login_required
def eliminar_departamento(id):
    conn = get_db()
    _ensure_departamentos_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)

    if not _departamento_accesible(conn, id, empresa_id):
        conn.close()
        flash('Departamento no encontrado', 'danger')
        return redirect(url_for('departamentos.index'))

    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        refs_departamento = _count_column_refs(
            conn,
            "departamento_id",
            id,
            empresa_id,
            exclude={"departamentos", "subdepartamentos", "puntos_venta", "almacenes", "punto_venta_departamentos"},
        )
        refs_responsable = _count_column_refs(
            conn,
            "departamento_responsable_id",
            id,
            empresa_id,
            exclude=set(),
        )

        if refs_departamento or refs_responsable:
            conn.execute(
                "UPDATE departamentos SET activo = 0 WHERE id = ? AND empresa_id = ?",
                (id, empresa_id),
            )
            conn.execute(
                "UPDATE subdepartamentos SET activo = 0 WHERE departamento_id = ? AND empresa_id = ?",
                (id, empresa_id),
            )
            conn.commit()
            total_refs = sum(n for _, n in refs_departamento + refs_responsable)
            flash(
                f'Departamento desactivado. Tiene {total_refs} referencias historicas/operativas y no se borra fisicamente para no romper trazabilidad.',
                'warning',
            )
            return redirect(url_for('departamentos.index'))

        sub_ids = conn.execute("""
            SELECT id
            FROM subdepartamentos
            WHERE departamento_id = ? AND empresa_id = ?
        """, (id, empresa_id)).fetchall()

        sub_ids = [row["id"] for row in sub_ids]

        if sub_ids:
            placeholders = ",".join(["?"] * len(sub_ids))

            conn.execute(f"""
                UPDATE puntos_venta
                SET subdepartamento_id = NULL
                WHERE subdepartamento_id IN ({placeholders})
            """, sub_ids)

            conn.execute(f"""
                UPDATE almacenes
                SET subdepartamento_id = NULL
                WHERE subdepartamento_id IN ({placeholders})
            """, sub_ids)

            conn.execute(f"""
                DELETE FROM subdepartamentos
                WHERE id IN ({placeholders})
            """, sub_ids)

        conn.execute("""
            DELETE FROM punto_venta_departamentos
            WHERE departamento_id = ?
        """, (id,))

        conn.execute("""
            UPDATE puntos_venta
            SET departamento_id = NULL
            WHERE departamento_id = ?
        """, (id,))

        conn.execute("""
            UPDATE almacenes
            SET departamento_id = NULL
            WHERE departamento_id = ?
        """, (id,))

        conn.execute("""
            DELETE FROM departamentos
            WHERE id = ? AND empresa_id = ?
        """, (id, empresa_id))

        conn.commit()
        flash('✅ Departamento eliminado', 'success')

    except Exception as e:
        conn.rollback()
        flash(f'❌ Error al eliminar departamento: {e}', 'danger')

    finally:
        conn.close()

    return redirect(url_for('departamentos.index'))


# Alias por compatibilidad con la ruta vieja /eliminar/<id>
@departamentos_bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar_departamento_alias(id):
    return eliminar_departamento(id)


# =========================
# NUEVO SUBDEPARTAMENTO
# =========================
@departamentos_bp.route('/subdepartamentos/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_subdepartamento():
    conn = get_db()
    _ensure_departamentos_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    centro = centro_activo_id()
    centro_sql = " AND centro_id = ?" if centro else ""
    centro_params = [centro] if centro else []

    departamentos = conn.execute("""
        SELECT *
        FROM departamentos
        WHERE activo = 1 AND empresa_id = ?
    """ + centro_sql + """
        ORDER BY nombre
    """, [empresa_id] + centro_params).fetchall()

    if request.method == 'POST':
        departamento_id = request.form.get('departamento_id')
        nombre = request.form.get('nombre', '').strip()
        descripcion = request.form.get('descripcion', '').strip()

        dep = _departamento_accesible(conn, departamento_id, empresa_id)
        if not dep:
            conn.close()
            flash('Departamento no valido para este centro.', 'danger')
            return redirect(url_for('departamentos.nuevo_subdepartamento'))
        centro_id = dep["centro_id"] if dep and "centro_id" in dep.keys() else centro
        conn.execute("""
            INSERT INTO subdepartamentos (departamento_id, nombre, descripcion, activo, empresa_id, centro_id)
            VALUES (?, ?, ?, 1, ?, ?)
        """, (departamento_id, nombre, descripcion, empresa_id, centro_id))

        conn.commit()
        conn.close()

        flash('✅ Subdepartamento creado correctamente', 'success')
        return redirect(url_for('departamentos.ver_departamento', id=departamento_id))

    conn.close()
    return render_template('departamentos/nuevo_sub.html', departamentos=departamentos)


# Alias por compatibilidad por si algún sitio llama a /subdepartamentos/nuevo desde fuera
@departamentos_bp.route('/subdepartamentos/nuevo/')
@login_required
def nuevo_subdepartamento_alias_slash():
    return redirect(url_for('departamentos.nuevo_subdepartamento', ))


# =========================
# EDITAR SUBDEPARTAMENTO
# =========================
@departamentos_bp.route('/subdepartamentos/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar_subdepartamento(id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    sub = _subdepartamento_accesible(conn, id, empresa_id)

    if not sub:
        conn.close()
        flash('❌ Subdepartamento no encontrado', 'danger')
        return redirect(url_for('departamentos.index'))

    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        descripcion = request.form.get('descripcion', '').strip()
        activo = 1 if request.form.get('activo') else 0

        conn.execute("""
            UPDATE subdepartamentos
            SET nombre = ?, descripcion = ?, activo = ?
            WHERE id = ? AND empresa_id = ?
        """, (nombre, descripcion, activo, id, empresa_id))

        conn.commit()
        conn.close()

        flash('✅ Subdepartamento actualizado correctamente', 'success')
        return redirect(url_for('departamentos.ver_departamento', id=sub['departamento_id']))

    conn.close()
    return render_template('departamentos/editar_sub.html', sub=sub)


# =========================
# ELIMINAR SUBDEPARTAMENTO
# =========================
@departamentos_bp.route('/subdepartamentos/<int:id>/eliminar', methods=['POST'])
@login_required
def eliminar_subdepartamento(id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    sub = _subdepartamento_accesible(conn, id, empresa_id)

    if not sub:
        conn.close()
        flash('❌ Subdepartamento no encontrado', 'danger')
        return redirect(url_for('departamentos.index'))

    departamento_id = sub['departamento_id']
    refs = _count_column_refs(
        conn,
        "subdepartamento_id",
        id,
        empresa_id,
        exclude={"subdepartamentos", "puntos_venta", "almacenes", "config_dispositivos_impresion"},
    )

    if refs:
        _clear_column_refs(
            conn,
            "subdepartamento_id",
            id,
            empresa_id,
            ["puntos_venta", "almacenes", "config_dispositivos_impresion"],
        )
        conn.execute("""
            UPDATE subdepartamentos
            SET activo = 0
            WHERE id = ? AND empresa_id = ?
        """, (id, empresa_id))
        conn.commit()
        conn.close()
        total_refs = sum(n for _, n in refs)
        flash(
            f'Subdepartamento desactivado. Tiene {total_refs} referencias historicas/operativas y no se borra fisicamente para no romper trazabilidad.',
            'warning',
        )
        return redirect(url_for('departamentos.ver_departamento', id=departamento_id))

    _clear_column_refs(
        conn,
        "subdepartamento_id",
        id,
        empresa_id,
        ["puntos_venta", "almacenes", "config_dispositivos_impresion"],
    )

    conn.execute("""
        DELETE FROM subdepartamentos
        WHERE id = ? AND empresa_id = ?
    """, (id, empresa_id))

    conn.commit()
    conn.close()

    flash('✅ Subdepartamento eliminado', 'success')
    return redirect(url_for('departamentos.ver_departamento', id=departamento_id))


# =========================
# LISTAR SUBDEPARTAMENTOS
# =========================
@departamentos_bp.route('/subdepartamentos')
@login_required
def listar_subdepartamentos():
    conn = get_db()
    _ensure_departamentos_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    centro = centro_activo_id()
    centro_sql = " AND d.centro_id = ?" if centro else ""
    centro_params = [centro] if centro else []

    subdepartamentos = conn.execute("""
        SELECT sd.*, d.nombre AS departamento_nombre
        FROM subdepartamentos sd
        JOIN departamentos d ON sd.departamento_id = d.id
        WHERE sd.empresa_id = ?
    """ + centro_sql + """
        ORDER BY d.nombre, sd.nombre
    """, [empresa_id] + centro_params).fetchall()

    conn.close()

    return render_template(
        'departamentos/subdepartamentos.html',
        subdepartamentos=subdepartamentos
    )

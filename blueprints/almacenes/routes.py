# blueprints/almacenes/routes.py
from flask import render_template, request, redirect, url_for, session, flash

from database import get_db
from . import almacenes_bp
from blueprints.auth.routes import (
    departamento_scope_sql,
    login_required,
    usuario_departamento_id,
    usuario_es_jefatura_departamento,
    usuario_es_global_operacion,
    usuario_puede_ver_departamento,
)
from helpers.operativa_helper import ensure_equipos_unificados_schema
from helpers.centros_helper import (
    add_columna_si_falta,
    asignar_centro_desde_departamento,
    centro_activo_id,
    filtro_centro_almacen,
)


ROLES_ALMACEN_GLOBAL = {'almacen'}
TIPOS_CON_FRIO = {"refrigerado", "congelado", "botellero", "camara", "vitrina", "nevera"}


def _empresa_id():
    return session.get('user_empresa_id', 1) or 1


def _almacen_scope(alias='a'):
    empresa_id = _empresa_id()
    scope_sql, scope_params = departamento_scope_sql(alias, empresa_id, extra_roles=ROLES_ALMACEN_GLOBAL)
    conn = get_db()
    try:
        add_columna_si_falta(conn, "almacenes", "centro_id INTEGER")
        centro_sql, centro_params = filtro_centro_almacen(conn, alias, empresa_id)
        return scope_sql + centro_sql, scope_params + centro_params
    finally:
        conn.close()


def _departamentos_visibles(conn, empresa_id):
    centro_id = centro_activo_id()
    filtro_centro = ""
    params = [empresa_id]
    if centro_id:
        filtro_centro = " AND centro_id = ?"
        params.append(centro_id)

    if usuario_es_global_operacion(ROLES_ALMACEN_GLOBAL):
        return conn.execute(f"""
            SELECT id, nombre
            FROM departamentos
            WHERE COALESCE(activo, 1) = 1
              AND (empresa_id = ? OR empresa_id IS NULL)
              {filtro_centro}
            ORDER BY nombre
        """, params).fetchall()

    departamento_id = usuario_departamento_id()
    if not departamento_id:
        return []

    params = [departamento_id, empresa_id]
    if centro_id:
        params.append(centro_id)
    return conn.execute(f"""
        SELECT id, nombre
        FROM departamentos
        WHERE id = ?
          AND COALESCE(activo, 1) = 1
          AND (empresa_id = ? OR empresa_id IS NULL)
          {filtro_centro}
        ORDER BY nombre
    """, params).fetchall()


def _subdepartamentos_visibles(conn, empresa_id, departamento_id=None):
    params = [empresa_id]
    filtro_departamento = ""
    filtro_centro = ""
    centro_id = centro_activo_id()
    if centro_id:
        filtro_centro = " AND centro_id = ?"

    if departamento_id:
        filtro_departamento = " AND departamento_id = ?"
        params.append(departamento_id)
    elif not usuario_es_global_operacion(ROLES_ALMACEN_GLOBAL):
        departamento_usuario = usuario_departamento_id()
        if not departamento_usuario:
            return []
        filtro_departamento = " AND departamento_id = ?"
        params.append(departamento_usuario)

    if centro_id:
        params.append(centro_id)

    return conn.execute(f"""
        SELECT id, nombre, departamento_id
        FROM subdepartamentos
        WHERE COALESCE(activo, 1) = 1
          AND (empresa_id = ? OR empresa_id IS NULL)
          {filtro_departamento}
          {filtro_centro}
        ORDER BY nombre
    """, params).fetchall()


def _almacen_por_id(conn, almacen_id):
    ensure_equipos_unificados_schema(conn)
    conn.commit()
    scope_sql, scope_params = _almacen_scope('a')
    return conn.execute(f"""
        SELECT a.*,
               d.nombre AS departamento_nombre,
               sd.nombre AS subdepartamento_nombre,
               em.nombre AS equipo_mantenimiento_nombre,
               em.codigo AS equipo_mantenimiento_codigo,
               em.criticidad AS equipo_mantenimiento_criticidad,
               eq.nombre AS equipo_appcc_nombre
        FROM almacenes a
        LEFT JOIN departamentos d ON a.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON a.subdepartamento_id = sd.id
        LEFT JOIN equipos_mantenimiento em ON em.id = a.equipo_mantenimiento_id
        LEFT JOIN equipos eq ON eq.id = a.equipo_appcc_id
        WHERE a.id = ?
          AND {scope_sql}
    """, [almacen_id, *scope_params]).fetchone()


def _puede_gestionar_almacen():
    return usuario_es_global_operacion(ROLES_ALMACEN_GLOBAL) or usuario_es_jefatura_departamento()


def _bloquear_gestion_almacen(conn):
    if _puede_gestionar_almacen():
        return None
    conn.close()
    flash('Solo el jefe o segundo jefe del departamento puede gestionar almacenes.', 'warning')
    return redirect(url_for('almacenes.almacenes'))


def _float_or_none(value):
    try:
        return float(value) if value not in (None, "") else None
    except Exception:
        return None


def _codigo_equipo(prefix, source_id):
    return f"{prefix}-{int(source_id):04d}"


def _tipo_control_temperatura(tipo, nombre):
    texto = f"{tipo or ''} {nombre or ''}".lower()
    if "congel" in texto:
        return "camara_congelacion", -24, -18
    if any(token in texto for token in ("refriger", "botellero", "camara", "vitrina", "nevera", "frio")):
        return "camara_refrigeracion", 0, 5
    return "almacen", None, None


def _sincronizar_almacen_con_equipos(
    conn,
    almacen_id,
    empresa_id,
    crear_equipo=False,
    controla_temperatura=False,
    criticidad="media",
):
    ensure_equipos_unificados_schema(conn)
    almacen = conn.execute("SELECT * FROM almacenes WHERE id = ? AND empresa_id = ?", (almacen_id, empresa_id)).fetchone()
    if not almacen:
        return

    tipo = almacen["tipo"] or "seco"
    controla_temperatura = bool(controla_temperatura or almacen["requiere_control_temperatura"] or tipo in TIPOS_CON_FRIO)
    if not crear_equipo and not controla_temperatura:
        return

    tipo_control, rango_min_def, rango_max_def = _tipo_control_temperatura(tipo, almacen["nombre"])
    rango_min = _float_or_none(almacen["rango_min"])
    rango_max = _float_or_none(almacen["rango_max"])
    if controla_temperatura:
        rango_min = rango_min if rango_min is not None else rango_min_def
        rango_max = rango_max if rango_max is not None else rango_max_def

    equipo_nombre = almacen["nombre"]
    mantenimiento_id = almacen["equipo_mantenimiento_id"]
    if not mantenimiento_id:
        row = conn.execute(
            """
            SELECT id
            FROM equipos_mantenimiento
            WHERE empresa_id = ? AND (almacen_id = ? OR LOWER(nombre) = LOWER(?))
            ORDER BY id
            LIMIT 1
            """,
            (empresa_id, almacen_id, equipo_nombre),
        ).fetchone()
        mantenimiento_id = row["id"] if row else None

    if not mantenimiento_id:
        conn.execute(
            """
            INSERT INTO equipos_mantenimiento (
                codigo, nombre, tipo, familia, categoria_operativa, clase_activo,
                criticidad, inventariable, requiere_appcc, origen_modulo, almacen_id,
                ubicacion, departamento_id, subdepartamento_id, activo, empresa_id, centro_id
            )
            VALUES (?, ?, ?, 'almacenes', 'almacen', ?, ?, 1, ?, 'almacenes', ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                _codigo_equipo("ALM", almacen_id),
                equipo_nombre,
                tipo_control if controla_temperatura else tipo,
                "almacen_frio" if controla_temperatura else "almacen",
                criticidad or ("alta" if controla_temperatura else "media"),
                1 if controla_temperatura else 0,
                almacen_id,
                almacen["ubicacion"],
                almacen["departamento_id"],
                almacen["subdepartamento_id"],
                empresa_id,
                almacen["centro_id"],
            ),
        )
        mantenimiento_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    else:
        conn.execute(
            """
            UPDATE equipos_mantenimiento
            SET almacen_id = ?,
                nombre = ?,
                tipo = ?,
                criticidad = ?,
                requiere_appcc = ?,
                equipo_appcc_id = ?,
                ubicacion = ?,
                departamento_id = ?,
                subdepartamento_id = ?,
                centro_id = ?
            WHERE id = ? AND empresa_id = ?
            """,
            (
                almacen_id,
                equipo_nombre,
                tipo_control if controla_temperatura else tipo,
                criticidad or ("alta" if controla_temperatura else "media"),
                1 if controla_temperatura else 0,
                None,
                almacen["ubicacion"],
                almacen["departamento_id"],
                almacen["subdepartamento_id"],
                almacen["centro_id"],
                mantenimiento_id,
                empresa_id,
            ),
        )

    appcc_id = almacen["equipo_appcc_id"] if controla_temperatura else None
    if controla_temperatura:
        if not appcc_id:
            row = conn.execute(
                """
                SELECT id
                FROM equipos
                WHERE empresa_id = ? AND (almacen_id = ? OR equipo_mantenimiento_id = ? OR LOWER(nombre) = LOWER(?))
                ORDER BY id
                LIMIT 1
                """,
                (empresa_id, almacen_id, mantenimiento_id, equipo_nombre),
            ).fetchone()
            appcc_id = row["id"] if row else None
        if not appcc_id:
            conn.execute(
                """
                INSERT INTO equipos (
                    nombre, tipo, ubicacion, departamento_id, subdepartamento_id,
                    rango_min, rango_max, responsable, activo, empresa_id, codigo,
                    equipo_mantenimiento_id, almacen_id, requiere_mantenimiento
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 1)
                """,
                (
                    equipo_nombre,
                    tipo_control,
                    almacen["ubicacion"],
                    almacen["departamento_id"],
                    almacen["subdepartamento_id"],
                    rango_min,
                    rango_max,
                    almacen["responsable"] or "",
                    empresa_id,
                    _codigo_equipo("ALM", almacen_id),
                    mantenimiento_id,
                    almacen_id,
                ),
            )
            appcc_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        else:
            conn.execute(
                """
                UPDATE equipos
                SET nombre = ?,
                    tipo = ?,
                    ubicacion = ?,
                    departamento_id = ?,
                    subdepartamento_id = ?,
                    rango_min = ?,
                    rango_max = ?,
                    responsable = ?,
                    equipo_mantenimiento_id = ?,
                    almacen_id = ?
                WHERE id = ? AND empresa_id = ?
                """,
                (
                    equipo_nombre,
                    tipo_control,
                    almacen["ubicacion"],
                    almacen["departamento_id"],
                    almacen["subdepartamento_id"],
                    rango_min,
                    rango_max,
                    almacen["responsable"] or "",
                    mantenimiento_id,
                    almacen_id,
                    appcc_id,
                    empresa_id,
                ),
            )

    conn.execute(
        """
        UPDATE almacenes
        SET equipo_mantenimiento_id = ?,
            equipo_appcc_id = ?,
            requiere_control_temperatura = ?,
            rango_min = ?,
            rango_max = ?
        WHERE id = ? AND empresa_id = ?
        """,
        (mantenimiento_id, appcc_id, 1 if controla_temperatura else 0, rango_min, rango_max, almacen_id, empresa_id),
    )
    if appcc_id:
        conn.execute(
            "UPDATE equipos_mantenimiento SET equipo_appcc_id = ? WHERE id = ? AND empresa_id = ?",
            (appcc_id, mantenimiento_id, empresa_id),
        )


# =========================
# LISTADO
# =========================
@almacenes_bp.route('/')
@login_required
def almacenes():
    conn = get_db()
    ensure_equipos_unificados_schema(conn)
    add_columna_si_falta(conn, "almacenes", "centro_id INTEGER")
    conn.commit()
    scope_sql, scope_params = _almacen_scope('a')

    almacenes = conn.execute(f"""
        SELECT a.*,
               d.nombre AS departamento_nombre,
               sd.nombre AS subdepartamento_nombre,
               em.nombre AS equipo_mantenimiento_nombre,
               em.codigo AS equipo_mantenimiento_codigo,
               em.criticidad AS equipo_mantenimiento_criticidad,
               eq.nombre AS equipo_appcc_nombre
        FROM almacenes a
        LEFT JOIN departamentos d ON a.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON a.subdepartamento_id = sd.id
        LEFT JOIN equipos_mantenimiento em ON em.id = a.equipo_mantenimiento_id
        LEFT JOIN equipos eq ON eq.id = a.equipo_appcc_id
        WHERE {scope_sql}
          AND COALESCE(a.activo, 1) = 1
        ORDER BY a.nombre
    """, scope_params).fetchall()

    conn.close()

    return render_template('almacenes/almacenes.html', almacenes=almacenes)


@almacenes_bp.route('/alta-masiva', methods=['GET', 'POST'])
@login_required
def alta_masiva():
    conn = get_db()
    empresa_id = _empresa_id()
    centro_sesion = centro_activo_id()
    ensure_equipos_unificados_schema(conn)
    add_columna_si_falta(conn, "almacenes", "centro_id INTEGER")
    conn.commit()

    bloqueo = _bloquear_gestion_almacen(conn)
    if bloqueo:
        return bloqueo
    if not centro_sesion:
        conn.close()
        flash('Selecciona un centro antes de crear almacenes por plantas.', 'warning')
        return redirect(url_for('rrhh.centro_empresa_general'))

    departamentos = _departamentos_visibles(conn, empresa_id)
    bloques = conn.execute(
        """
        SELECT id, nombre
        FROM establecimiento_bloques
        WHERE empresa_id = ? AND centro_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY nombre
        """,
        (empresa_id, centro_sesion),
    ).fetchall()

    if request.method == 'POST':
        departamento_id = request.form.get('departamento_id') or None
        if not departamento_id:
            conn.close()
            flash('Selecciona departamento.', 'warning')
            return redirect(url_for('almacenes.alta_masiva'))
        departamento = conn.execute(
            "SELECT id, centro_id FROM departamentos WHERE id = ? AND empresa_id = ? AND COALESCE(activo,1)=1",
            (departamento_id, empresa_id),
        ).fetchone()
        if not departamento or departamento['centro_id'] != centro_sesion:
            conn.close()
            flash('El departamento no pertenece al centro activo.', 'warning')
            return redirect(url_for('almacenes.alta_masiva'))

        try:
            desde = int(request.form.get('planta_desde') or 1)
            hasta = int(request.form.get('planta_hasta') or desde)
            cantidad = max(1, int(request.form.get('cantidad_por_planta') or 1))
        except Exception:
            desde, hasta, cantidad = 1, 1, 1
        if hasta < desde:
            desde, hasta = hasta, desde
        patron = (request.form.get('patron') or 'Office planta {planta}{sufijo}').strip()
        tipo = request.form.get('tipo') or 'seco'
        ubicacion_base = (request.form.get('ubicacion_base') or '').strip()
        crear_equipo = bool(request.form.get('crear_equipo'))
        controla_temperatura = bool(request.form.get('requiere_control_temperatura') or tipo in TIPOS_CON_FRIO)
        criticidad = request.form.get('criticidad') or ('alta' if controla_temperatura else 'media')

        creados = 0
        existentes = 0
        sufijos = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for planta in range(desde, hasta + 1):
            for indice in range(cantidad):
                sufijo = "" if cantidad == 1 else f" {sufijos[indice] if indice < len(sufijos) else indice + 1}"
                nombre = patron.replace('{planta}', str(planta)).replace('{sufijo}', sufijo).strip()
                ubicacion = ubicacion_base.replace('{planta}', str(planta)).replace('{sufijo}', sufijo).strip() or nombre
                row = conn.execute(
                    """
                    SELECT id FROM almacenes
                    WHERE empresa_id = ? AND centro_id = ? AND LOWER(nombre) = LOWER(?)
                    LIMIT 1
                    """,
                    (empresa_id, centro_sesion, nombre),
                ).fetchone()
                if row:
                    existentes += 1
                    continue
                conn.execute(
                    """
                    INSERT INTO almacenes
                    (nombre, tipo, ubicacion, responsable, departamento_id, requiere_control_temperatura,
                     activo, empresa_id, centro_id)
                    VALUES (?, ?, ?, '', ?, ?, 1, ?, ?)
                    """,
                    (nombre, tipo, ubicacion, departamento_id, 1 if controla_temperatura else 0, empresa_id, centro_sesion),
                )
                almacen_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
                _sincronizar_almacen_con_equipos(
                    conn,
                    almacen_id,
                    empresa_id,
                    crear_equipo=crear_equipo or controla_temperatura,
                    controla_temperatura=controla_temperatura,
                    criticidad=criticidad,
                )
                creados += 1
        conn.commit()
        conn.close()
        flash(f'Alta masiva completada: {creados} almacenes creados, {existentes} ya existian.', 'success')
        return redirect(url_for('almacenes.almacenes'))

    conn.close()
    return render_template('almacenes/alta_masiva.html', departamentos=departamentos, bloques=bloques)


# =========================
# NUEVO
# =========================
@almacenes_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_almacen():
    conn = get_db()
    empresa_id = _empresa_id()
    centro_sesion = centro_activo_id()
    ensure_equipos_unificados_schema(conn)
    add_columna_si_falta(conn, "almacenes", "centro_id INTEGER")
    conn.commit()

    bloqueo = _bloquear_gestion_almacen(conn)
    if bloqueo:
        return bloqueo

    if not centro_sesion:
        conn.close()
        flash('Selecciona un centro antes de crear almacenes. No se pueden crear almacenes desde Empresa completa.', 'warning')
        return redirect(url_for('rrhh.centro_empresa_general'))

    departamentos = _departamentos_visibles(conn, empresa_id)
    subdepartamentos = _subdepartamentos_visibles(conn, empresa_id)

    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        tipo = request.form.get('tipo', '').strip()
        ubicacion = request.form.get('ubicacion', '').strip()
        responsable = request.form.get('responsable', '').strip()
        crear_equipo = 1 if request.form.get('crear_equipo') else 0
        controla_temperatura = 1 if request.form.get('requiere_control_temperatura') or tipo in TIPOS_CON_FRIO else 0
        criticidad = request.form.get('criticidad') or ('alta' if controla_temperatura else 'media')

        departamento_id = request.form.get('departamento_id') or None
        subdepartamento_id = request.form.get('subdepartamento_id') or None

        if not usuario_es_global_operacion(ROLES_ALMACEN_GLOBAL) and not departamento_id:
            departamento_id = usuario_departamento_id()

        if not departamento_id:
            conn.close()
            flash('Debes seleccionar departamento para el almacen.', 'warning')
            return redirect(url_for('almacenes.nuevo_almacen'))

        if not usuario_puede_ver_departamento(departamento_id, ROLES_ALMACEN_GLOBAL):
            conn.close()
            flash('No puedes crear almacenes para otro departamento.', 'warning')
            return redirect(url_for('almacenes.almacenes'))

        departamento = conn.execute("""
            SELECT id, centro_id
            FROM departamentos
            WHERE id = ?
              AND COALESCE(activo, 1) = 1
              AND (empresa_id = ? OR empresa_id IS NULL)
            LIMIT 1
        """, (departamento_id, empresa_id)).fetchone()
        if not departamento or (centro_sesion and departamento["centro_id"] != centro_sesion):
            conn.close()
            flash('El departamento seleccionado no pertenece al centro activo.', 'warning')
            return redirect(url_for('almacenes.nuevo_almacen'))

        if subdepartamento_id:
            subdepartamento = conn.execute("""
                SELECT id
                FROM subdepartamentos
                WHERE id = ?
                  AND departamento_id = ?
                  AND (empresa_id = ? OR empresa_id IS NULL)
                LIMIT 1
            """, (subdepartamento_id, departamento_id, empresa_id)).fetchone()
            if not subdepartamento:
                conn.close()
                flash('El subdepartamento no pertenece al departamento seleccionado.', 'warning')
                return redirect(url_for('almacenes.nuevo_almacen'))

        centro_id = asignar_centro_desde_departamento(conn, departamento_id)
        if centro_id != centro_sesion:
            conn.close()
            flash('El almacén no coincide con el centro activo. Guarda desde el centro correcto.', 'warning')
            return redirect(url_for('almacenes.nuevo_almacen'))

        conn.execute("""
            INSERT INTO almacenes (
                nombre, tipo, ubicacion, responsable, departamento_id,
                subdepartamento_id, requiere_control_temperatura, rango_min, rango_max,
                activo, empresa_id, centro_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """, (
            nombre,
            tipo,
            ubicacion,
            responsable,
            departamento_id,
            subdepartamento_id,
            controla_temperatura,
            request.form.get('rango_min') or None,
            request.form.get('rango_max') or None,
            empresa_id,
            centro_id,
        ))
        almacen_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        _sincronizar_almacen_con_equipos(
            conn,
            almacen_id,
            empresa_id,
            crear_equipo=bool(crear_equipo or controla_temperatura),
            controla_temperatura=bool(controla_temperatura),
            criticidad=criticidad,
        )

        ensure_equipos_unificados_schema(conn)
        conn.commit()
        conn.close()

        flash('Almacen creado correctamente', 'success')
        return redirect(url_for('almacenes.almacenes'))

    conn.close()

    return render_template(
        'almacenes/nuevo_almacen.html',
        departamentos=departamentos,
        subdepartamentos=subdepartamentos,
    )


# =========================
# VER
# =========================
@almacenes_bp.route('/<int:almacen_id>')
@login_required
def ver_almacen(almacen_id):
    conn = get_db()
    empresa_id = _empresa_id()
    almacen = _almacen_por_id(conn, almacen_id)

    if not almacen:
        conn.close()
        flash('Almacen no encontrado', 'danger')
        return redirect(url_for('almacenes.almacenes'))

    stock = conn.execute("""
        SELECT
            p.nombre,
            p.codigo,
            s.cantidad,
            p.unidad
        FROM stock_almacen s
        JOIN productos p ON s.producto_id = p.id
        WHERE s.almacen_id = ?
          AND p.empresa_id = ?
        ORDER BY p.nombre
    """, (almacen_id, empresa_id)).fetchall()

    conn.close()
    return render_template('almacenes/ver_almacen.html', almacen=almacen, stock=stock)


# =========================
# EDITAR
# =========================
@almacenes_bp.route('/editar/<int:almacen_id>', methods=['GET', 'POST'])
@login_required
def editar_almacen(almacen_id):
    conn = get_db()
    empresa_id = _empresa_id()
    centro_sesion = centro_activo_id()
    ensure_equipos_unificados_schema(conn)
    conn.commit()
    almacen = _almacen_por_id(conn, almacen_id)

    if not almacen:
        conn.close()
        flash('Almacen no encontrado', 'danger')
        return redirect(url_for('almacenes.almacenes'))

    bloqueo = _bloquear_gestion_almacen(conn)
    if bloqueo:
        return bloqueo

    if not centro_sesion:
        conn.close()
        flash('Selecciona un centro antes de editar almacenes. No se pueden modificar almacenes desde Empresa completa.', 'warning')
        return redirect(url_for('rrhh.centro_empresa_general'))

    departamentos = _departamentos_visibles(conn, empresa_id)
    subdepartamentos = _subdepartamentos_visibles(conn, empresa_id, almacen['departamento_id'])

    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        tipo = request.form.get('tipo', '').strip()
        ubicacion = request.form.get('ubicacion', '').strip()
        responsable = request.form.get('responsable', '').strip()
        crear_equipo = 1 if request.form.get('crear_equipo') else 0
        controla_temperatura = 1 if request.form.get('requiere_control_temperatura') or tipo in TIPOS_CON_FRIO else 0
        criticidad = request.form.get('criticidad') or ('alta' if controla_temperatura else 'media')

        departamento_id = request.form.get('departamento_id') or None
        subdepartamento_id = request.form.get('subdepartamento_id') or None
        activo = 1 if request.form.get('activo') else 0

        if not usuario_es_global_operacion(ROLES_ALMACEN_GLOBAL) and not departamento_id:
            departamento_id = usuario_departamento_id()

        if not departamento_id:
            conn.close()
            flash('Debes seleccionar departamento para el almacen.', 'warning')
            return redirect(url_for('almacenes.editar_almacen', almacen_id=almacen_id))

        if not usuario_puede_ver_departamento(departamento_id, ROLES_ALMACEN_GLOBAL):
            conn.close()
            flash('No puedes mover este almacen a otro departamento.', 'warning')
            return redirect(url_for('almacenes.editar_almacen', almacen_id=almacen_id))

        departamento = conn.execute("""
            SELECT id, centro_id
            FROM departamentos
            WHERE id = ?
              AND COALESCE(activo, 1) = 1
              AND (empresa_id = ? OR empresa_id IS NULL)
            LIMIT 1
        """, (departamento_id, empresa_id)).fetchone()
        if not departamento or (centro_sesion and departamento["centro_id"] != centro_sesion):
            conn.close()
            flash('El departamento seleccionado no pertenece al centro activo.', 'warning')
            return redirect(url_for('almacenes.editar_almacen', almacen_id=almacen_id))

        if subdepartamento_id:
            subdepartamento = conn.execute("""
                SELECT id
                FROM subdepartamentos
                WHERE id = ?
                  AND departamento_id = ?
                  AND (empresa_id = ? OR empresa_id IS NULL)
                LIMIT 1
            """, (subdepartamento_id, departamento_id, empresa_id)).fetchone()
            if not subdepartamento:
                conn.close()
                flash('El subdepartamento no pertenece al departamento seleccionado.', 'warning')
                return redirect(url_for('almacenes.editar_almacen', almacen_id=almacen_id))

        scope_sql, scope_params = _almacen_scope('almacenes')
        conn.execute(f"""
            UPDATE almacenes
            SET nombre=?,
                tipo=?,
                ubicacion=?,
                responsable=?,
                departamento_id=?,
                subdepartamento_id=?,
                requiere_control_temperatura=?,
                rango_min=?,
                rango_max=?,
                activo=?,
                centro_id=?
            WHERE id=?
              AND {scope_sql}
        """, (
            nombre,
            tipo,
            ubicacion,
            responsable,
            departamento_id,
            subdepartamento_id,
            controla_temperatura,
            request.form.get('rango_min') or None,
            request.form.get('rango_max') or None,
            activo,
            asignar_centro_desde_departamento(conn, departamento_id),
            almacen_id,
            *scope_params,
        ))
        _sincronizar_almacen_con_equipos(
            conn,
            almacen_id,
            empresa_id,
            crear_equipo=bool(crear_equipo or controla_temperatura or almacen["equipo_mantenimiento_id"]),
            controla_temperatura=bool(controla_temperatura),
            criticidad=criticidad,
        )

        ensure_equipos_unificados_schema(conn)
        conn.commit()
        conn.close()

        flash('Almacen actualizado correctamente', 'success')
        return redirect(url_for('almacenes.almacenes'))

    conn.close()

    return render_template(
        'almacenes/editar_almacen.html',
        almacen=almacen,
        departamentos=departamentos,
        subdepartamentos=subdepartamentos,
    )


# =========================
# ELIMINAR
# =========================
@almacenes_bp.route('/eliminar/<int:almacen_id>', methods=['POST'])
@login_required
def eliminar_almacen(almacen_id):
    conn = get_db()
    empresa_id = _empresa_id()
    centro_sesion = centro_activo_id()
    bloqueo = _bloquear_gestion_almacen(conn)
    if bloqueo:
        return bloqueo
    if not centro_sesion:
        conn.close()
        flash('Selecciona un centro antes de desactivar almacenes. No se pueden desactivar desde Empresa completa.', 'warning')
        return redirect(url_for('rrhh.centro_empresa_general'))
    scope_sql, scope_params = _almacen_scope('almacenes')

    conn.execute(f"""
        UPDATE almacenes
        SET activo = 0
        WHERE id = ?
          AND empresa_id = ?
          AND centro_id = ?
          AND {scope_sql}
    """, [almacen_id, empresa_id, centro_sesion, *scope_params])
    conn.commit()
    conn.close()

    flash('Almacen desactivado', 'success')
    return redirect(url_for('almacenes.almacenes'))

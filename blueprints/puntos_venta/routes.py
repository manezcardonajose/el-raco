# blueprints/puntos_venta/routes.py
from flask import render_template, request, redirect, url_for, session, flash
from database import get_db
from . import puntos_venta_bp
from blueprints.auth.routes import login_required
from helpers.centros_helper import asignar_centro_desde_departamento, centro_activo_id


def _column_exists(conn, table, column):
    try:
        return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})"))
    except Exception:
        return False


def _ensure_puntos_venta_centros_schema(conn):
    if not _column_exists(conn, "puntos_venta", "centro_id"):
        conn.execute("ALTER TABLE puntos_venta ADD COLUMN centro_id INTEGER")
    for table in ("departamentos", "subdepartamentos", "almacenes"):
        if not _column_exists(conn, table, "centro_id"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN centro_id INTEGER")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS punto_venta_departamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            punto_venta_id INTEGER NOT NULL,
            departamento_id INTEGER NOT NULL,
            rol_operativo TEXT DEFAULT 'general',
            UNIQUE(punto_venta_id, departamento_id, rol_operativo)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS punto_venta_recetas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            punto_venta_id INTEGER NOT NULL,
            receta_id INTEGER NOT NULL,
            precio_venta REAL DEFAULT 0,
            activo INTEGER DEFAULT 1,
            UNIQUE(punto_venta_id, receta_id)
        )
    """)


def _to_float(value, default=0.0):
    try:
        if value in (None, ''):
            return default
        return float(str(value).replace(',', '.'))
    except Exception:
        return default


def _limpiar_relaciones_departamento(departamentos_rel, roles_rel):
    relaciones = []
    usados = set()
    for i, dep_id in enumerate(departamentos_rel):
        dep_id = (dep_id or '').strip()
        if not dep_id or dep_id in usados:
            continue
        rol = 'general'
        if i < len(roles_rel) and (roles_rel[i] or '').strip():
            rol = roles_rel[i].strip()
        relaciones.append({'departamento_id': dep_id, 'rol_operativo': rol})
        usados.add(dep_id)
    return relaciones


def _obtener_relaciones_punto(conn, punto_id):
    return conn.execute(
        """
        SELECT pvd.id, pvd.departamento_id, pvd.rol_operativo, d.nombre AS departamento_nombre
        FROM punto_venta_departamentos pvd
        JOIN departamentos d ON d.id = pvd.departamento_id
        WHERE pvd.punto_venta_id = ?
        ORDER BY d.nombre, pvd.id
        """,
        (punto_id,)
    ).fetchall()


def _categoria_tpv_recetas(conn, empresa_id, punto_id, nombre='Menu fijo'):
    row = conn.execute(
        """
        SELECT id
        FROM tpv_categorias
        WHERE empresa_id = ? AND punto_venta_id = ? AND LOWER(nombre) = LOWER(?)
        LIMIT 1
        """,
        (empresa_id, punto_id, nombre),
    ).fetchone()
    if row:
        return row['id']
    cur = conn.execute(
        """
        INSERT INTO tpv_categorias
        (punto_venta_id, nombre, tipo, color, orden, activo, empresa_id)
        VALUES (?, ?, 'menu_fijo', '#16a34a', 5, 1, ?)
        """,
        (punto_id, nombre, empresa_id),
    )
    return cur.lastrowid


def _publicar_receta_en_tpv(conn, empresa_id, punto_id, receta_id, precio_venta=0):
    receta = conn.execute(
        "SELECT * FROM recetas WHERE id = ? AND empresa_id = ?",
        (receta_id, empresa_id),
    ).fetchone()
    if not receta:
        return False
    categoria_nombre = receta['categoria'] or 'Menu fijo'
    categoria_id = _categoria_tpv_recetas(conn, empresa_id, punto_id, categoria_nombre)
    articulo = conn.execute(
        """
        SELECT *
        FROM tpv_articulos
        WHERE empresa_id = ?
          AND punto_venta_id = ?
          AND receta_id = ?
        LIMIT 1
        """,
        (empresa_id, punto_id, receta_id),
    ).fetchone()
    precio = _to_float(precio_venta, 0)
    if articulo:
        conn.execute(
            """
            UPDATE tpv_articulos
            SET categoria_id = ?, nombre = ?, tipo = 'receta', precio = ?,
                destino_preparacion = 'cocina', visible_tpv = 1,
                visible_comandero = 1, activo = 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND empresa_id = ?
            """,
            (categoria_id, receta['nombre'], precio, articulo['id'], empresa_id),
        )
    else:
        conn.execute(
            """
            INSERT INTO tpv_articulos
            (punto_venta_id, categoria_id, receta_id, nombre, tipo, precio,
             incluido_regimenes, suplemento, destino_preparacion,
             visible_tpv, visible_comandero, orden, activo, empresa_id)
            VALUES (?, ?, ?, ?, 'receta', ?, '', 0, 'cocina', 1, 1, 0, 1, ?)
            """,
            (punto_id, categoria_id, receta_id, receta['nombre'], precio, empresa_id),
        )
    return True


def _despublicar_receta_en_tpv(conn, empresa_id, punto_id, receta_id):
    conn.execute(
        """
        UPDATE tpv_articulos
        SET activo = 0, visible_tpv = 0, visible_comandero = 0,
            updated_at = CURRENT_TIMESTAMP
        WHERE empresa_id = ?
          AND punto_venta_id = ?
          AND receta_id = ?
        """,
        (empresa_id, punto_id, receta_id),
    )


@puntos_venta_bp.route('/')
@login_required
def listar_puntos_venta():
    conn = get_db()
    _ensure_puntos_venta_centros_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    centro = centro_activo_id()
    centro_sql = " AND pv.centro_id = ?" if centro else ""
    centro_params = [centro] if centro else []
    puntos = conn.execute(
        """
        SELECT pv.*, d.nombre AS departamento_nombre, sd.nombre AS subdepartamento_nombre, a.nombre AS almacen_nombre,
               (SELECT COUNT(*) FROM punto_venta_recetas pr WHERE pr.punto_venta_id = pv.id) AS num_recetas
        FROM puntos_venta pv
        LEFT JOIN departamentos d ON pv.departamento_id = d.id
        LEFT JOIN subdepartamentos sd ON pv.subdepartamento_id = sd.id
        LEFT JOIN almacenes a ON pv.almacen_id = a.id
        WHERE pv.empresa_id = ?
        """ + centro_sql + """
        ORDER BY pv.nombre
        """,
        [empresa_id] + centro_params
    ).fetchall()
    puntos_detalle = []
    for punto in puntos:
        relaciones = _obtener_relaciones_punto(conn, punto['id'])
        if not relaciones and punto['departamento_id']:
            relaciones = [{'id': None, 'departamento_id': punto['departamento_id'], 'rol_operativo': 'general', 'departamento_nombre': punto['departamento_nombre']}]
        puntos_detalle.append({'punto': punto, 'relaciones': relaciones})
    conn.close()
    return render_template('puntos_venta/listar.html', puntos=puntos_detalle)


@puntos_venta_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_punto_venta():
    conn = get_db()
    _ensure_puntos_venta_centros_schema(conn)
    empresa_id = session.get('user_empresa_id', 1)
    centro = centro_activo_id()
    centro_sql = " AND centro_id = ?" if centro else ""
    centro_params = [centro] if centro else []
    departamentos = conn.execute("SELECT * FROM departamentos WHERE activo = 1 AND empresa_id = ?" + centro_sql + " ORDER BY nombre", [empresa_id] + centro_params).fetchall()
    subdepartamentos = conn.execute("SELECT * FROM subdepartamentos WHERE activo = 1 AND empresa_id = ?" + centro_sql + " ORDER BY nombre", [empresa_id] + centro_params).fetchall()
    almacenes = conn.execute("SELECT * FROM almacenes WHERE activo = 1 AND empresa_id = ?" + centro_sql + " ORDER BY nombre", [empresa_id] + centro_params).fetchall()
    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        tipo = request.form.get('tipo', '').strip()
        departamento_id = request.form.get('departamento_id') or None
        subdepartamento_id = request.form.get('subdepartamento_id') or None
        almacen_id = request.form.get('almacen_id') or None
        imprimir_comanda = 1 if request.form.get('imprimir_comanda') else 0
        activo = 1 if request.form.get('activo') else 0
        relaciones = _limpiar_relaciones_departamento(request.form.getlist('departamentos_rel[]'), request.form.getlist('roles_rel[]'))
        if not relaciones and departamento_id:
            relaciones = [{'departamento_id': departamento_id, 'rol_operativo': 'general'}]
        centro_id = asignar_centro_desde_departamento(conn, departamento_id)
        cur = conn.execute(
            """
            INSERT INTO puntos_venta (nombre, tipo, departamento_id, subdepartamento_id, almacen_id, imprimir_comanda, activo, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (nombre, tipo, departamento_id, subdepartamento_id, almacen_id, imprimir_comanda, activo, empresa_id, centro_id)
        )
        punto_id = cur.lastrowid
        for relacion in relaciones:
            conn.execute(
                "INSERT INTO punto_venta_departamentos (punto_venta_id, departamento_id, rol_operativo) VALUES (?, ?, ?)",
                (punto_id, relacion['departamento_id'], relacion['rol_operativo'])
            )
        conn.commit()
        conn.close()
        flash('✅ Punto de venta creado correctamente', 'success')
        return redirect(url_for('puntos_venta.listar_puntos_venta'))
    conn.close()
    return render_template('puntos_venta/nuevo.html', departamentos=departamentos, subdepartamentos=subdepartamentos, almacenes=almacenes)


@puntos_venta_bp.route('/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar_punto_venta(id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    punto = conn.execute("SELECT * FROM puntos_venta WHERE id = ? AND empresa_id = ?", (id, empresa_id)).fetchone()
    if not punto:
        conn.close()
        flash('❌ Punto de venta no encontrado', 'danger')
        return redirect(url_for('puntos_venta.listar_puntos_venta'))
    departamentos = conn.execute("SELECT * FROM departamentos WHERE activo = 1 AND empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    subdepartamentos = conn.execute("SELECT * FROM subdepartamentos WHERE activo = 1 AND empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    almacenes = conn.execute("SELECT * FROM almacenes WHERE activo = 1 AND empresa_id = ? ORDER BY nombre", (empresa_id,)).fetchall()
    relaciones = _obtener_relaciones_punto(conn, id)
    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        tipo = request.form.get('tipo', '').strip()
        departamento_id = request.form.get('departamento_id') or None
        subdepartamento_id = request.form.get('subdepartamento_id') or None
        almacen_id = request.form.get('almacen_id') or None
        imprimir_comanda = 1 if request.form.get('imprimir_comanda') else 0
        activo = 1 if request.form.get('activo') else 0
        nuevas_relaciones = _limpiar_relaciones_departamento(request.form.getlist('departamentos_rel[]'), request.form.getlist('roles_rel[]'))
        if not nuevas_relaciones and departamento_id:
            nuevas_relaciones = [{'departamento_id': departamento_id, 'rol_operativo': 'general'}]
        conn.execute(
            """
            UPDATE puntos_venta
            SET nombre = ?, tipo = ?, departamento_id = ?, subdepartamento_id = ?, almacen_id = ?, imprimir_comanda = ?, activo = ?
            WHERE id = ? AND empresa_id = ?
            """,
            (nombre, tipo, departamento_id, subdepartamento_id, almacen_id, imprimir_comanda, activo, id, empresa_id)
        )
        conn.execute("DELETE FROM punto_venta_departamentos WHERE punto_venta_id = ?", (id,))
        for relacion in nuevas_relaciones:
            conn.execute(
                "INSERT INTO punto_venta_departamentos (punto_venta_id, departamento_id, rol_operativo) VALUES (?, ?, ?)",
                (id, relacion['departamento_id'], relacion['rol_operativo'])
            )
        conn.commit()
        conn.close()
        flash('✅ Punto de venta actualizado correctamente', 'success')
        return redirect(url_for('puntos_venta.listar_puntos_venta'))
    if not relaciones and punto['departamento_id']:
        relaciones = [{'id': None, 'departamento_id': punto['departamento_id'], 'rol_operativo': 'general', 'departamento_nombre': None}]
    conn.close()
    return render_template('puntos_venta/editar.html', punto=punto, departamentos=departamentos, subdepartamentos=subdepartamentos, almacenes=almacenes, relaciones=relaciones)


@puntos_venta_bp.route('/<int:id>/eliminar', methods=['POST'])
@login_required
def eliminar_punto_venta(id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    conn.execute("DELETE FROM punto_venta_departamentos WHERE punto_venta_id = ?", (id,))
    conn.execute("DELETE FROM punto_venta_recetas WHERE punto_venta_id = ?", (id,))
    conn.execute("DELETE FROM puntos_venta WHERE id = ? AND empresa_id = ?", (id, empresa_id))
    conn.commit()
    conn.close()
    flash('✅ Punto de venta eliminado', 'success')
    return redirect(url_for('puntos_venta.listar_puntos_venta'))


@puntos_venta_bp.route('/<int:id>/recetas')
@login_required
def ver_recetas_punto(id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    punto = conn.execute("SELECT * FROM puntos_venta WHERE id = ? AND empresa_id = ?", (id, empresa_id)).fetchone()
    if not punto:
        conn.close()
        flash('❌ Punto de venta no encontrado', 'danger')
        return redirect(url_for('puntos_venta.listar_puntos_venta'))
    recetas = conn.execute(
        """
        SELECT r.*, pr.precio_venta, pr.activo AS punto_activo,
               ta.id AS tpv_articulo_id, ta.activo AS tpv_activo, ta.visible_tpv
        FROM punto_venta_recetas pr
        JOIN recetas r ON pr.receta_id = r.id
        LEFT JOIN tpv_articulos ta
          ON ta.punto_venta_id = pr.punto_venta_id
         AND ta.receta_id = pr.receta_id
         AND ta.empresa_id = r.empresa_id
        WHERE pr.punto_venta_id = ?
        ORDER BY r.categoria, r.nombre
        """,
        (id,)
    ).fetchall()
    recetas_disponibles = conn.execute("SELECT * FROM recetas WHERE empresa_id = ? AND id NOT IN (SELECT receta_id FROM punto_venta_recetas WHERE punto_venta_id = ?) ORDER BY nombre", (empresa_id, id)).fetchall()
    conn.close()
    return render_template('puntos_venta/recetas.html', punto=punto, recetas=recetas, recetas_disponibles=recetas_disponibles)


@puntos_venta_bp.route('/<int:id>/recetas/añadir', methods=['POST'])
@login_required
def añadir_receta_punto(id):
    conn = get_db()
    receta_id = request.form.get('receta_id')
    if receta_id:
        conn.execute("INSERT INTO punto_venta_recetas (punto_venta_id, receta_id) VALUES (?, ?)", (id, receta_id))
        conn.commit()
    conn.close()
    return redirect(url_for('puntos_venta.ver_recetas_punto', id=id))


@puntos_venta_bp.route('/<int:id>/recetas/eliminar/<int:receta_id>', methods=['POST'])
@login_required
def eliminar_receta_punto(id, receta_id):
    conn = get_db()
    conn.execute("DELETE FROM punto_venta_recetas WHERE punto_venta_id = ? AND receta_id = ?", (id, receta_id))
    conn.commit()
    conn.close()
    return redirect(url_for('puntos_venta.ver_recetas_punto', id=id))


@puntos_venta_bp.route('/<int:id>/menu/anadir-receta', methods=['POST'])
@login_required
def anadir_receta_menu_punto(id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    receta_id = request.form.get('receta_id')
    precio_venta = _to_float(request.form.get('precio_venta'), 0)
    if receta_id:
        existe = conn.execute(
            "SELECT id FROM punto_venta_recetas WHERE punto_venta_id = ? AND receta_id = ?",
            (id, receta_id)
        ).fetchone()
        if existe:
            conn.execute(
                "UPDATE punto_venta_recetas SET precio_venta = ?, activo = 1 WHERE id = ?",
                (precio_venta, existe['id'])
            )
        else:
            conn.execute(
                "INSERT INTO punto_venta_recetas (punto_venta_id, receta_id, precio_venta, activo) VALUES (?, ?, ?, 1)",
                (id, receta_id, precio_venta)
            )
        _publicar_receta_en_tpv(conn, empresa_id, id, int(receta_id), precio_venta)
        conn.commit()
        flash('Receta añadida al menu del punto y publicada en el TPV.', 'success')
    conn.close()
    return redirect(url_for('puntos_venta.ver_recetas_punto', id=id))


@puntos_venta_bp.route('/<int:id>/menu/eliminar-receta/<int:receta_id>', methods=['POST'])
@login_required
def eliminar_receta_menu_punto(id, receta_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    conn.execute("DELETE FROM punto_venta_recetas WHERE punto_venta_id = ? AND receta_id = ?", (id, receta_id))
    _despublicar_receta_en_tpv(conn, empresa_id, id, receta_id)
    conn.commit()
    conn.close()
    flash('Receta quitada del menu del punto y ocultada del TPV.', 'success')
    return redirect(url_for('puntos_venta.ver_recetas_punto', id=id))

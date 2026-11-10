from flask import render_template, request, redirect, url_for, session, flash
from database import get_db
from . import proveedores_bp
from blueprints.auth.routes import login_required
from helpers.centros_helper import centro_activo_id


def _columns(conn, table_name):
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
    except Exception:
        return []


def _add_column(conn, table_name, column_sql):
    name = column_sql.strip().split()[0]
    if name not in _columns(conn, table_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def asegurar_proveedores_schema(conn):
    _add_column(conn, "proveedores", "centro_id INTEGER")
    _add_column(conn, "proveedores", "proveedor_padre_id INTEGER")
    _add_column(conn, "proveedores", "delegacion TEXT")
    _add_column(conn, "proveedores", "email_pedidos TEXT")
    _add_column(conn, "proveedores", "contacto_pedidos TEXT")
    _add_column(conn, "proveedores", "telefono_pedidos TEXT")
    if "reglas_calendario_proveedores" in {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }:
        _add_column(conn, "reglas_calendario_proveedores", "activo INTEGER DEFAULT 1")
    if "productos_proveedores" in {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }:
        _add_column(conn, "productos_proveedores", "activo INTEGER DEFAULT 1")
        _add_column(conn, "productos_proveedores", "preferente INTEGER DEFAULT 0")
        _add_column(conn, "productos_proveedores", "es_principal INTEGER DEFAULT 0")
    empresas = conn.execute("SELECT DISTINCT empresa_id FROM proveedores WHERE empresa_id IS NOT NULL").fetchall()
    for empresa in empresas:
        empresa_id = empresa["empresa_id"]
        sin_centro = conn.execute("""
            SELECT COUNT(*) AS total
            FROM proveedores
            WHERE empresa_id = ?
              AND centro_id IS NULL
              AND COALESCE(activo, 1) = 1
        """, (empresa_id,)).fetchone()
        alguno_con_centro = conn.execute("""
            SELECT 1 FROM proveedores
            WHERE empresa_id = ?
              AND centro_id IS NOT NULL
            LIMIT 1
        """, (empresa_id,)).fetchone()
        if not (sin_centro and sin_centro["total"] and not alguno_con_centro):
            continue
        centro_base = conn.execute("""
            SELECT id FROM centros_trabajo
            WHERE empresa_id = ?
              AND COALESCE(activo, 1) = 1
              AND LOWER(nombre) LIKE '%pepe%'
            ORDER BY id
            LIMIT 1
        """, (empresa_id,)).fetchone()
        if not centro_base:
            centro_base = conn.execute("""
                SELECT id FROM centros_trabajo
                WHERE empresa_id = ?
                  AND COALESCE(activo, 1) = 1
                ORDER BY id
                LIMIT 1
            """, (empresa_id,)).fetchone()
        if centro_base:
            conn.execute(
                """
                UPDATE proveedores
                SET centro_id = ?
                WHERE empresa_id = ?
                  AND centro_id IS NULL
                  AND COALESCE(activo, 1) = 1
                """,
                (centro_base["id"], empresa_id),
            )


def proveedores_visibles_sql(alias="pr", centro_id=None):
    centro_id = centro_id if centro_id is not None else centro_activo_id()
    if not centro_id:
        return "", []
    return f" AND ({alias}.centro_id IS NULL OR {alias}.centro_id = ?)", [centro_id]


def _proveedor_en_ambito(conn, proveedor_id, empresa_id, alias="pr"):
    scope_sql, scope_params = proveedores_visibles_sql(alias)
    return conn.execute(f"""
        SELECT {alias}.*
        FROM proveedores {alias}
        WHERE {alias}.id = ?
          AND {alias}.empresa_id = ?
          AND COALESCE({alias}.activo, 1) = 1
          {scope_sql}
    """, [proveedor_id, empresa_id] + scope_params).fetchone()


def _centro_form_valido(conn, empresa_id, centro_form, centro_actual):
    if centro_form == "global":
        return None, True
    centro_id = int(centro_form) if str(centro_form or "").isdigit() else centro_actual
    if not centro_id:
        return None, True
    existe = conn.execute("""
        SELECT id
        FROM centros_trabajo
        WHERE id = ? AND empresa_id = ?
          AND COALESCE(activo, 1) = 1
    """, (centro_id, empresa_id)).fetchone()
    return (centro_id if existe else None), bool(existe)


def _proveedor_matriz_valido(conn, empresa_id, proveedor_padre_id, proveedor_id=None):
    if not proveedor_padre_id:
        return None
    if not str(proveedor_padre_id).isdigit():
        return None
    params = [int(proveedor_padre_id), empresa_id]
    sql_extra = ""
    if proveedor_id:
        sql_extra = " AND id <> ?"
        params.append(proveedor_id)
    matriz = conn.execute(f"""
        SELECT id
        FROM proveedores
        WHERE id = ? AND empresa_id = ?
          AND COALESCE(activo, 1) = 1
          {sql_extra}
    """, params).fetchone()
    return matriz["id"] if matriz else None


def proveedores_select(conn, empresa_id, centro_id=None, columnas="id, nombre"):
    asegurar_proveedores_schema(conn)
    scope_sql, scope_params = proveedores_visibles_sql("pr", centro_id)
    return conn.execute(f"""
        SELECT {columnas}
        FROM proveedores pr
        WHERE COALESCE(pr.activo, 1) = 1
          AND pr.empresa_id = ?
          {scope_sql}
        ORDER BY pr.nombre
    """, [empresa_id] + scope_params).fetchall()

# ============================================
# LISTA DE PROVEEDORES
# ============================================
@proveedores_bp.route('/')
@login_required
def proveedores():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    asegurar_proveedores_schema(conn)
    conn.commit()
    centro_id = centro_activo_id()
    scope_sql, scope_params = proveedores_visibles_sql("pr", centro_id)
    
    proveedores = conn.execute(f"""
        SELECT pr.*,
               c.nombre AS centro_nombre,
               matriz.nombre AS proveedor_matriz,
               COUNT(DISTINCT pp.producto_id) AS productos_asignados
        FROM proveedores pr
        LEFT JOIN centros_trabajo c ON c.id = pr.centro_id
        LEFT JOIN proveedores matriz ON matriz.id = pr.proveedor_padre_id
        LEFT JOIN productos_proveedores pp
               ON pp.proveedor_id = pr.id
              AND COALESCE(pp.activo, 1) = 1
              AND COALESCE(pp.es_principal, pp.preferente, 0) = 1
        WHERE pr.activo = 1 AND pr.empresa_id = ?
        {scope_sql}
        GROUP BY pr.id
        ORDER BY pr.nombre
    """, [empresa_id] + scope_params).fetchall()
    conn.close()
    
    return render_template('proveedores/proveedores.html', proveedores=proveedores, centro_id=centro_id)

# ============================================
# NUEVO PROVEEDOR
# ============================================
@proveedores_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_proveedor():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    asegurar_proveedores_schema(conn)
    conn.commit()
    centro_id = centro_activo_id()
    centros = conn.execute("""
        SELECT id, nombre FROM centros_trabajo
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY nombre
    """, (empresa_id,)).fetchall()
    matrices = conn.execute("""
        SELECT id, nombre FROM proveedores
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
          AND centro_id IS NULL
        ORDER BY nombre
    """, (empresa_id,)).fetchall()
    if request.method == "POST":
        nombre = request.form["nombre"]
        nif = request.form.get("nif", "")
        email = request.form.get("email", "")
        email_pedidos = request.form.get("email_pedidos", "")
        telefono = request.form.get("telefono", "")
        telefono_pedidos = request.form.get("telefono_pedidos", "")
        direccion = request.form.get("direccion", "")
        contacto = request.form.get("contacto", "")
        contacto_pedidos = request.form.get("contacto_pedidos", "")
        delegacion = request.form.get("delegacion", "")
        observaciones = request.form.get("observaciones", "")
        proveedor_padre_id = _proveedor_matriz_valido(conn, empresa_id, request.form.get("proveedor_padre_id"))
        centro_form = request.form.get("centro_id")
        centro_asignado, centro_ok = _centro_form_valido(conn, empresa_id, centro_form, centro_id)
        if not centro_ok:
            conn.close()
            flash('Centro no válido para este proveedor', 'danger')
            return redirect(url_for('proveedores.nuevo_proveedor'))
        
        conn.execute("""
            INSERT INTO proveedores (
                nombre, nif, email, telefono, direccion, contacto, observaciones, empresa_id,
                centro_id, proveedor_padre_id, delegacion, email_pedidos, contacto_pedidos, telefono_pedidos
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            nombre, nif, email, telefono, direccion, contacto, observaciones, empresa_id,
            centro_asignado, proveedor_padre_id, delegacion, email_pedidos, contacto_pedidos, telefono_pedidos,
        ))
        conn.commit()
        conn.close()
        
        flash(' Proveedor creado', 'success')
        return redirect(url_for('proveedores.proveedores'))
    
    conn.close()
    return render_template('proveedores/nuevo_proveedor.html', centros=centros, matrices=matrices, centro_id=centro_id)

# ============================================
# VER PROVEEDOR
# ============================================
@proveedores_bp.route('/ver/<int:proveedor_id>')
@login_required
def ver_proveedor(proveedor_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    asegurar_proveedores_schema(conn)
    conn.commit()
    proveedor = _proveedor_en_ambito(conn, proveedor_id, empresa_id, "pr")
    if not proveedor:
        conn.close()
        flash('Proveedor no encontrado', 'danger')
        return redirect(url_for('proveedores.proveedores'))
    
    prod_scope_sql = ""
    prod_scope_params = []
    if proveedor["centro_id"]:
        prod_scope_sql = " AND (p.centro_id IS NULL OR p.centro_id = ?)"
        prod_scope_params = [proveedor["centro_id"]]
    productos = conn.execute("""
        SELECT pp.*, p.nombre as nombre_producto
        FROM productos_proveedores pp
        JOIN productos p ON pp.producto_id = p.id
        WHERE pp.proveedor_id = ? AND p.empresa_id = ?
          AND COALESCE(pp.activo, 1) = 1
          """ + prod_scope_sql + """
        ORDER BY pp.es_principal DESC, p.nombre
    """, [proveedor_id, empresa_id] + prod_scope_params).fetchall()
    
    conn.close()
    
    return render_template('proveedores/ver_proveedor.html', proveedor=proveedor, productos=productos)

# ============================================
# EDITAR PROVEEDOR
# ============================================
@proveedores_bp.route('/editar/<int:proveedor_id>', methods=['GET', 'POST'])
@login_required
def editar_proveedor(proveedor_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    asegurar_proveedores_schema(conn)
    conn.commit()
    centro_id = centro_activo_id()
    centros = conn.execute("""
        SELECT id, nombre FROM centros_trabajo
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        ORDER BY nombre
    """, (empresa_id,)).fetchall()
    matrices = conn.execute("""
        SELECT id, nombre FROM proveedores
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1 AND id <> ?
          AND centro_id IS NULL
        ORDER BY nombre
    """, (empresa_id, proveedor_id)).fetchall()
    proveedor_actual = _proveedor_en_ambito(conn, proveedor_id, empresa_id, "pr")
    if not proveedor_actual:
        conn.close()
        flash('Proveedor no encontrado', 'danger')
        return redirect(url_for('proveedores.proveedores'))

    if request.method == "POST":
        nombre = request.form["nombre"]
        nif = request.form.get("nif", "")
        email = request.form.get("email", "")
        email_pedidos = request.form.get("email_pedidos", "")
        telefono = request.form.get("telefono", "")
        telefono_pedidos = request.form.get("telefono_pedidos", "")
        direccion = request.form.get("direccion", "")
        contacto = request.form.get("contacto", "")
        contacto_pedidos = request.form.get("contacto_pedidos", "")
        delegacion = request.form.get("delegacion", "")
        observaciones = request.form.get("observaciones", "")
        proveedor_padre_id = _proveedor_matriz_valido(conn, empresa_id, request.form.get("proveedor_padre_id"), proveedor_id)
        centro_form = request.form.get("centro_id")
        centro_asignado, centro_ok = _centro_form_valido(conn, empresa_id, centro_form, centro_id)
        if not centro_ok:
            conn.close()
            flash('Centro no válido para este proveedor', 'danger')
            return redirect(url_for('proveedores.editar_proveedor', proveedor_id=proveedor_id))
        activo = 1 if request.form.get("activo") else 0
        
        conn.execute("""
            UPDATE proveedores 
            SET nombre=?, nif=?, email=?, telefono=?, direccion=?, contacto=?, observaciones=?, activo=?,
                centro_id=?, proveedor_padre_id=?, delegacion=?, email_pedidos=?, contacto_pedidos=?, telefono_pedidos=?
            WHERE id=? AND empresa_id=?
        """, (
            nombre, nif, email, telefono, direccion, contacto, observaciones, activo,
            centro_asignado, proveedor_padre_id, delegacion, email_pedidos, contacto_pedidos, telefono_pedidos,
            proveedor_id, empresa_id,
        ))
        conn.commit()
        conn.close()
        
        flash(' Proveedor actualizado', 'success')
        return redirect(url_for('proveedores.proveedores'))
    
    conn.close()
    
    return render_template('proveedores/editar_proveedor.html', proveedor=proveedor_actual, centros=centros, matrices=matrices, centro_id=centro_id)

@proveedores_bp.route('/<int:proveedor_id>/eliminar', methods=['POST'])
@login_required
def eliminar_proveedor(proveedor_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    asegurar_proveedores_schema(conn)
    conn.commit()
    proveedor = _proveedor_en_ambito(conn, proveedor_id, empresa_id, "pr")
    if not proveedor:
        conn.close()
        flash('Proveedor no encontrado', 'danger')
        return redirect(url_for('proveedores.proveedores'))
    
    conn.execute("""
        UPDATE proveedores SET activo = 0 
        WHERE id = ? AND empresa_id = ?
    """, (proveedor_id, empresa_id))
    conn.commit()
    conn.close()
    
    flash('Proveedor eliminado correctamente', 'success')
    return redirect(url_for('proveedores.proveedores'))

# ============================================
# CALENDARIO DEL PROVEEDOR
# ============================================
@proveedores_bp.route('/<int:proveedor_id>/calendario')
@login_required
def calendario_proveedor(proveedor_id):
    from datetime import datetime, timedelta
    
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    asegurar_proveedores_schema(conn)
    conn.commit()
    
    proveedor = _proveedor_en_ambito(conn, proveedor_id, empresa_id, "pr")
    
    if not proveedor:
        conn.close()
        flash('Proveedor no encontrado', 'danger')
        return redirect(url_for('proveedores.proveedores'))
    
    # Obtener mes actual o seleccionado
    mes = request.args.get('mes', datetime.now().month, type=int)
    ao = request.args.get('ao', datetime.now().year, type=int)
    
    # Reglas del proveedor
    reglas = conn.execute("""
        SELECT * FROM reglas_calendario_proveedores
        WHERE proveedor_id = ? AND empresa_id = ?
          AND COALESCE(activo, 1) = 1
        ORDER BY tipo, frecuencia
    """, (proveedor_id, empresa_id)).fetchall()
    
    # Generar das del mes
    primer_dia = datetime(ao, mes, 1)
    if mes == 12:
        ultimo_dia = datetime(ao + 1, 1, 1) - timedelta(days=1)
    else:
        ultimo_dia = datetime(ao, mes + 1, 1) - timedelta(days=1)
    
    dias = []
    fecha_actual = primer_dia
    while fecha_actual <= ultimo_dia:
        dia_semana = fecha_actual.weekday()  # 0=lunes, 6=domingo
        dia_num = fecha_actual.day
        
        # Marcar segn reglas
        marcado = {'pedido': False, 'entrega': False, 'visita': False}
        
        for regla in reglas:
            if regla['frecuencia'] == 'semanal':
                if regla['dia_semana'] == dia_semana:
                    marcado[regla['tipo']] = True
            elif regla['frecuencia'] == 'mensual':
                if regla['dia_mes'] == dia_num:
                    marcado[regla['tipo']] = True
        
        dias.append({
            'fecha': fecha_actual.strftime('%Y-%m-%d'),
            'dia': dia_num,
            'marcado': marcado
        })
        fecha_actual += timedelta(days=1)
    
    conn.close()
    
    return render_template('proveedores/calendario.html',
                           proveedor=proveedor,
                           dias=dias,
                           mes=mes,
                           ao=ao,
                           meses=['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                                  'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'],
                           reglas=reglas)


# ============================================
# LISTA DE REGLAS
# ============================================
@proveedores_bp.route('/<int:proveedor_id>/reglas')
@login_required
def listar_reglas(proveedor_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    asegurar_proveedores_schema(conn)
    conn.commit()
    
    proveedor = _proveedor_en_ambito(conn, proveedor_id, empresa_id, "pr")
    
    if not proveedor:
        conn.close()
        flash('Proveedor no encontrado', 'danger')
        return redirect(url_for('proveedores.proveedores'))
    
    reglas = conn.execute("""
        SELECT * FROM reglas_calendario_proveedores
        WHERE proveedor_id = ? AND empresa_id = ?
          AND COALESCE(activo, 1) = 1
        ORDER BY tipo, frecuencia
    """, (proveedor_id, empresa_id)).fetchall()
    
    conn.close()
    
    return render_template('proveedores/reglas.html',
                           proveedor=proveedor,
                           reglas=reglas)


# ============================================
# NUEVA REGLA
# ============================================
@proveedores_bp.route('/<int:proveedor_id>/reglas/nueva', methods=['GET', 'POST'])
@login_required
def nueva_regla(proveedor_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    asegurar_proveedores_schema(conn)
    conn.commit()
    
    proveedor = _proveedor_en_ambito(conn, proveedor_id, empresa_id, "pr")
    
    if not proveedor:
        conn.close()
        flash('Proveedor no encontrado', 'danger')
        return redirect(url_for('proveedores.proveedores'))
    
    if request.method == 'POST':
        tipo = request.form.get('tipo')
        frecuencia = request.form.get('frecuencia')
        dia_semana = request.form.get('dia_semana', type=int) if frecuencia == 'semanal' else None
        dia_mes = request.form.get('dia_mes', type=int) if frecuencia == 'mensual' else None
        hora = request.form.get('hora', '')
        
        conn.execute("""
            INSERT INTO reglas_calendario_proveedores 
            (proveedor_id, tipo, frecuencia, dia_semana, dia_mes, hora, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (proveedor_id, tipo, frecuencia, dia_semana, dia_mes, hora, empresa_id))
        conn.commit()
        conn.close()
        
        flash('Regla creada correctamente', 'success')
        return redirect(url_for('proveedores.listar_reglas', proveedor_id=proveedor_id))
    
    conn.close()
    return render_template('proveedores/nueva_regla.html', proveedor=proveedor)


# ============================================
# EDITAR REGLA
# ============================================
@proveedores_bp.route('/reglas/<int:regla_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_regla(regla_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    asegurar_proveedores_schema(conn)
    conn.commit()
    
    regla = conn.execute("""
        SELECT r.*, p.nombre as proveedor_nombre, p.id as proveedor_id
        FROM reglas_calendario_proveedores r
        JOIN proveedores p ON r.proveedor_id = p.id
        WHERE r.id = ? AND r.empresa_id = ?
    """, (regla_id, empresa_id)).fetchone()
    
    if not regla or not _proveedor_en_ambito(conn, regla["proveedor_id"], empresa_id, "pr"):
        conn.close()
        flash('Regla no encontrada', 'danger')
        return redirect(url_for('proveedores.proveedores'))
    
    if request.method == 'POST':
        tipo = request.form.get('tipo')
        frecuencia = request.form.get('frecuencia')
        dia_semana = request.form.get('dia_semana', type=int) if frecuencia == 'semanal' else None
        dia_mes = request.form.get('dia_mes', type=int) if frecuencia == 'mensual' else None
        hora = request.form.get('hora', '')
        
        conn.execute("""
            UPDATE reglas_calendario_proveedores
            SET tipo = ?, frecuencia = ?, dia_semana = ?, dia_mes = ?, hora = ?
            WHERE id = ? AND empresa_id = ?
        """, (tipo, frecuencia, dia_semana, dia_mes, hora, regla_id, empresa_id))
        conn.commit()
        conn.close()
        
        flash('Regla actualizada correctamente', 'success')
        return redirect(url_for('proveedores.listar_reglas', proveedor_id=regla['proveedor_id']))
    
    conn.close()
    return render_template('proveedores/editar_regla.html', regla=regla)


# ============================================
# ELIMINAR REGLA
# ============================================
@proveedores_bp.route('/reglas/<int:regla_id>/eliminar', methods=['POST'])
@login_required
def eliminar_regla(regla_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    asegurar_proveedores_schema(conn)
    conn.commit()
    
    regla = conn.execute("""
        SELECT proveedor_id FROM reglas_calendario_proveedores
        WHERE id = ? AND empresa_id = ?
    """, (regla_id, empresa_id)).fetchone()
    
    if regla and _proveedor_en_ambito(conn, regla["proveedor_id"], empresa_id, "pr"):
        conn.execute(
            """
            UPDATE reglas_calendario_proveedores
            SET activo = 0
            WHERE id = ? AND empresa_id = ?
            """,
            (regla_id, empresa_id),
        )
        conn.commit()
        conn.close()
        flash('Regla eliminada correctamente', 'success')
        return redirect(url_for('proveedores.listar_reglas', proveedor_id=regla['proveedor_id']))
    
    conn.close()
    flash('Regla no encontrada', 'danger')
    return redirect(url_for('proveedores.proveedores'))

@proveedores_bp.route('/<int:proveedor_id>/productos/<int:relacion_id>/eliminar', methods=['POST'])
@login_required
def eliminar_producto_proveedor(proveedor_id, relacion_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    asegurar_proveedores_schema(conn)

    proveedor = _proveedor_en_ambito(conn, proveedor_id, empresa_id, "pr")
    if not proveedor:
        conn.close()
        flash('Proveedor no encontrado', 'danger')
        return redirect(url_for('proveedores.proveedores'))
    
    conn.execute("""
        UPDATE productos_proveedores
        SET activo = 0, es_principal = 0, preferente = 0
        WHERE id = ?
          AND proveedor_id = ?
          AND proveedor_id IN (
              SELECT id FROM proveedores WHERE empresa_id = ?
          )
    """, (relacion_id, proveedor_id, empresa_id))
    conn.commit()
    conn.close()
    
    flash('Producto eliminado del proveedor', 'success')
    return redirect(url_for('proveedores.ver_proveedor', proveedor_id=proveedor_id))

from pathlib import Path

from flask import render_template, request, redirect, url_for, session, flash, send_file
from werkzeug.utils import secure_filename

from database import get_db
from blueprints.auth.routes import login_required
import math

from . import productos_bp
from .importador_catalogo import crear_plantilla_importacion, importar_catalogo_desde_archivo
from helpers.ambito_helper import filtro_ambito_operativo
from helpers.centros_helper import centro_activo_id


# ============================================
# FUNCIONES AUXILIARES
# ============================================

def _columnas_tabla(cursor, tabla):
    try:
        return [row[1] for row in cursor.execute(f"PRAGMA table_info({tabla})").fetchall()]
    except Exception:
        return []


def _safe_add_column(cursor, tabla, definicion):
    nombre = definicion.strip().split()[0]
    if nombre not in _columnas_tabla(cursor, tabla):
        cursor.execute(f"ALTER TABLE {tabla} ADD COLUMN {definicion}")


def _to_float_safe(valor, defecto=0.0):
    try:
        txt = str(valor if valor is not None else '').strip().replace(',', '.')
        return float(txt) if txt else defecto
    except Exception:
        return defecto


def _safe_next_url(default=None):
    destino = request.form.get('next') or request.args.get('next') or default or url_for('productos.productos')
    if not destino or not str(destino).startswith('/') or str(destino).startswith('//'):
        return default or url_for('productos.productos')
    return destino


def _scope_sin_empresa(scope_sql, alias="p"):
    prefijo = f"{alias}.empresa_id = ? AND "
    if scope_sql.startswith(prefijo):
        return scope_sql[len(prefijo):]
    if scope_sql == f"{alias}.empresa_id = ?":
        return "1 = 1"
    return scope_sql.replace(f"{alias}.", "")


def _stock_join_centro_sql(conn, centro_id):
    columnas_almacenes = _columnas_tabla(conn.cursor(), "almacenes")
    if centro_id and "centro_id" in columnas_almacenes:
        return (
            "LEFT JOIN almacenes a_stock ON a_stock.id = s.almacen_id "
            "AND a_stock.empresa_id = p.empresa_id "
            "AND a_stock.centro_id = ?",
            [centro_id],
            "a_stock.id IS NOT NULL",
        )
    return "", [], "1 = 1"


def _proveedor_visible_producto_sql(alias="pr", centro_id=None):
    if not centro_id:
        return "", []
    return f" AND (COALESCE({alias}.centro_id, 0) = 0 OR {alias}.centro_id = ?)", [centro_id]


def _asegurar_estructura_producto_proveedor(cursor):
    """Asegura la relación producto-proveedor sin borrar datos existentes."""
    _safe_add_column(cursor, 'productos', 'alergenos TEXT')
    _safe_add_column(cursor, 'productos', 'alergenos_validado INTEGER DEFAULT 0')
    _safe_add_column(cursor, 'productos', 'centro_id INTEGER')
    _safe_add_column(cursor, 'productos', 'departamento_responsable_id INTEGER')
    _safe_add_column(cursor, 'productos', 'subdepartamento_responsable_id INTEGER')

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS productos_proveedores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER,
            proveedor_id INTEGER,
            codigo_proveedor TEXT,
            referencia_proveedor TEXT,
            precio REAL,
            precio_compra REAL,
            precio_ultimo REAL,
            precio_vigente REAL,
            precio_medio REAL,
            activo INTEGER DEFAULT 1,
            preferente INTEGER DEFAULT 0,
            es_principal INTEGER DEFAULT 0
        )
    """)
    _safe_add_column(cursor, 'productos_proveedores', 'proveedor_id INTEGER')
    _safe_add_column(cursor, 'productos_proveedores', 'codigo_proveedor TEXT')
    _safe_add_column(cursor, 'productos_proveedores', 'referencia_proveedor TEXT')
    _safe_add_column(cursor, 'productos_proveedores', 'precio REAL')
    _safe_add_column(cursor, 'productos_proveedores', 'precio_compra REAL')
    _safe_add_column(cursor, 'productos_proveedores', 'precio_ultimo REAL')
    _safe_add_column(cursor, 'productos_proveedores', 'precio_vigente REAL')
    _safe_add_column(cursor, 'productos_proveedores', 'precio_medio REAL')
    _safe_add_column(cursor, 'productos_proveedores', 'activo INTEGER DEFAULT 1')
    _safe_add_column(cursor, 'productos_proveedores', 'preferente INTEGER DEFAULT 0')
    _safe_add_column(cursor, 'productos_proveedores', 'es_principal INTEGER DEFAULT 0')


# ============================================
# FUNCIÓN PARA GENERAR CÓDIGO DE PRODUCTO
# ============================================

def generar_codigo_producto(familia, tipo_conservacion):
    cons_map = {'seco': 'S', 'refrigerado': 'R', 'congelado': 'C'}
    letra_cons = cons_map.get(tipo_conservacion, 'X')

    if len(familia) >= 2:
        letras_familia = familia[:2].upper()
    else:
        letras_familia = familia.upper().ljust(2, 'X')

    prefijo = letra_cons + letras_familia

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT codigo FROM productos
        WHERE codigo LIKE ?
        ORDER BY codigo DESC LIMIT 1
    """, (f'{prefijo}%',))
    ultimo = cursor.fetchone()
    conn.close()

    if ultimo and ultimo['codigo']:
        try:
            num = int(ultimo['codigo'][-5:]) + 1
        except Exception:
            num = 1
    else:
        num = 1

    return f"{prefijo}{str(num).zfill(5)}"


# ============================================
# LISTA DE PRODUCTOS
# ============================================
@productos_bp.route('/')
@login_required
def productos():
    conn = get_db()
    _asegurar_estructura_producto_proveedor(conn.cursor())
    conn.commit()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()

    consulta = request.args.get('consulta', '')
    conservacion = request.args.get('conservacion', '')
    familia = request.args.get('familia', '')
    buscar = request.args.get('buscar', '')
    pagina = request.args.get('pagina', 1, type=int)
    por_pagina = 20
    offset = (pagina - 1) * por_pagina

    stock_join, stock_params, stock_cond = _stock_join_centro_sql(conn, centro_id)
    query = """
        SELECT p.id, p.nombre, p.codigo, p.unidad, p.precio, p.familia,
               p.tipo_conservacion, p.stock_minimo, p.activo,
               p.alergenos, p.alergenos_validado,
               pr.nombre AS proveedor_nombre,
               COALESCE(SUM(CASE WHEN """ + stock_cond + """ THEN s.cantidad ELSE 0 END), 0) as stock_total
        FROM productos p
        LEFT JOIN stock_almacen s ON p.id = s.producto_id
        """ + stock_join + """
        LEFT JOIN proveedores pr ON p.proveedor_id = pr.id
        WHERE p.empresa_id = ? AND COALESCE(p.activo, 1) = 1
    """
    params = stock_params + [empresa_id]
    scope_sql, scope_params = filtro_ambito_operativo(
        conn,
        "p",
        empresa_id,
        "productos",
        departamento_col="departamento_responsable_id",
        subdepartamento_col="subdepartamento_responsable_id",
        punto_venta_col=None,
    )
    scope_cond = _scope_sin_empresa(scope_sql, "p")
    query += f" AND {scope_cond}"
    params.extend(scope_params[1:])

    if buscar:
        query += " AND (p.nombre LIKE ? OR p.codigo LIKE ? OR p.familia LIKE ?)"
        params.extend([f'%{buscar}%', f'%{buscar}%', f'%{buscar}%'])

    if conservacion:
        query += " AND p.tipo_conservacion = ?"
        params.append(conservacion)

    if familia:
        query += " AND p.familia = ?"
        params.append(familia)

    query += " GROUP BY p.id"

    if consulta == 'bajo_stock':
        query += " HAVING COALESCE(SUM(s.cantidad), 0) <= p.stock_minimo AND COALESCE(SUM(s.cantidad), 0) > 0"
    elif consulta == 'agotados':
        query += " HAVING COALESCE(SUM(s.cantidad), 0) <= 0"

    query += " ORDER BY p.nombre LIMIT ? OFFSET ?"
    params.extend([por_pagina, offset])
    productos = conn.execute(query, params).fetchall()

    count_query = """
        SELECT COUNT(*) as total
        FROM productos p
        WHERE p.empresa_id = ? AND COALESCE(p.activo, 1) = 1
    """
    count_params = [empresa_id]
    count_query += f" AND {scope_cond}"
    count_params.extend(scope_params[1:])

    if buscar:
        count_query += " AND (p.nombre LIKE ? OR p.codigo LIKE ? OR p.familia LIKE ?)"
        count_params.extend([f'%{buscar}%', f'%{buscar}%', f'%{buscar}%'])
    if conservacion:
        count_query += " AND p.tipo_conservacion = ?"
        count_params.append(conservacion)
    if familia:
        count_query += " AND p.familia = ?"
        count_params.append(familia)

    total = conn.execute(count_query, count_params).fetchone()['total']
    total_paginas = math.ceil(total / por_pagina) if total else 1
    familias_params = [empresa_id] + scope_params[1:]
    familias = conn.execute(f"""
        SELECT DISTINCT familia
        FROM productos
        WHERE empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND {scope_cond.replace('p.', '')}
          AND familia IS NOT NULL
          AND TRIM(familia) != ''
        ORDER BY familia
    """, familias_params).fetchall()
    conn.close()

    return render_template('productos/productos.html', productos=productos,
                           total_paginas=total_paginas, pagina_actual=pagina,
                           consulta_actual=consulta, familias=familias,
                           familia_actual=familia)


# ============================================
# IMPORTAR CATALOGO PRODUCTOS / PROVEEDORES
# ============================================
@productos_bp.route('/importar-catalogo', methods=['GET', 'POST'])
@login_required
def importar_catalogo():
    resultado = None
    if request.method == 'POST':
        archivo = request.files.get('archivo')
        aplicar = request.form.get('aplicar') == '1'
        if not archivo or not archivo.filename:
            flash('Selecciona un archivo Excel, CSV o PDF con tabla.', 'warning')
            return redirect(url_for('productos.importar_catalogo'))

        extension = Path(archivo.filename).suffix.lower()
        if extension not in {'.xlsx', '.xls', '.xlsm', '.csv', '.tsv', '.pdf'}:
            flash('Formato no soportado. Usa .xlsx, .csv, .tsv o PDF con tabla.', 'danger')
            return redirect(url_for('productos.importar_catalogo'))

        upload_dir = Path('uploads') / 'productos_imports'
        upload_dir.mkdir(parents=True, exist_ok=True)
        filename = secure_filename(archivo.filename)
        if not filename:
            filename = f'importacion_catalogo{extension}'
        ruta = upload_dir / filename
        contador = 1
        while ruta.exists():
            ruta = upload_dir / f"{Path(filename).stem}_{contador}{extension}"
            contador += 1
        archivo.save(ruta)

        try:
            resultado = importar_catalogo_desde_archivo(
                ruta,
                empresa_id=session.get('user_empresa_id', 1),
                aplicar=aplicar,
            )
            resumen = resultado.get('resumen', {})
            if aplicar and resumen.get('errores', 0) == 0:
                flash(
                    f"Importacion aplicada: {resumen.get('productos_creados', 0)} productos nuevos, "
                    f"{resumen.get('productos_actualizados', 0)} actualizados.",
                    'success',
                )
            elif aplicar:
                flash('La importacion no se aplico porque hay errores que revisar.', 'danger')
            else:
                flash('Vista previa generada. Revisa el resumen antes de aplicar.', 'info')
        except Exception as exc:
            flash(f'Error importando catalogo: {exc}', 'danger')

    return render_template('productos/importar_catalogo.html', resultado=resultado)


@productos_bp.route('/plantilla-importacion')
@login_required
def plantilla_importacion():
    output = crear_plantilla_importacion()
    return send_file(
        output,
        as_attachment=True,
        download_name='plantilla_importacion_productos_proveedores.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


# ============================================
# NUEVO PRODUCTO
# ============================================
@productos_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_producto():
    conn = get_db()
    _asegurar_estructura_producto_proveedor(conn.cursor())
    conn.commit()

    if request.method == 'POST':
        nombre = request.form.get('nombre', '')
        cantidad = _to_float_safe(request.form.get('cantidad'), 0.0)
        unidad = request.form.get('unidad', 'kg')
        familia = request.form.get('familia', '')
        precio = _to_float_safe(request.form.get('precio'), 0.0)
        stock_minimo = _to_float_safe(request.form.get('stock_minimo'), 0.0)
        tipo_conservacion = request.form.get('tipo_conservacion', 'seco')
        ubicacion = request.form.get('ubicacion', '')
        alergenos = request.form.get('alergenos', '')
        alergenos_validado = 1 if request.form.get('alergenos_validado') else 0
        empresa_id = session.get('user_empresa_id', 1)
        centro_id = centro_activo_id()
        codigo = generar_codigo_producto(familia, tipo_conservacion)

        conn.execute("""
            INSERT INTO productos (nombre, codigo, cantidad, unidad, familia,
                                   precio, stock_minimo, tipo_conservacion,
                                   ubicacion, alergenos, alergenos_validado, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (nombre, codigo, cantidad, unidad, familia, precio,
              stock_minimo, tipo_conservacion, ubicacion,
              alergenos, alergenos_validado, empresa_id, centro_id))
        conn.commit()
        conn.close()

        flash(f'Producto creado correctamente. Código: {codigo}', 'success')
        return redirect(_safe_next_url())

    conn.close()
    return render_template('productos/nuevo_producto.html', next_url=_safe_next_url())


# ============================================
# VER PRODUCTO
# ============================================
@productos_bp.route('/<int:producto_id>')
@login_required
def ver_producto(producto_id):
    conn = get_db()
    _asegurar_estructura_producto_proveedor(conn.cursor())
    conn.commit()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    centro_sql = " AND centro_id = ?" if centro_id else ""
    params = [producto_id, empresa_id] + ([centro_id] if centro_id else [])
    producto = conn.execute(f"""
        SELECT * FROM productos
        WHERE id = ? AND empresa_id = ?
          AND COALESCE(activo, 1) = 1
          {centro_sql}
    """, params).fetchone()
    conn.close()

    if not producto:
        flash('Producto no encontrado', 'danger')
        return redirect(url_for('productos.productos'))

    return render_template('productos/ver_producto.html', producto=producto, next_url=_safe_next_url())


# ============================================
# EDITAR PRODUCTO
# ============================================
@productos_bp.route('/<int:producto_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_producto(producto_id):
    conn = get_db()
    _asegurar_estructura_producto_proveedor(conn.cursor())
    conn.commit()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    centro_sql = " AND centro_id = ?" if centro_id else ""
    params = [producto_id, empresa_id] + ([centro_id] if centro_id else [])
    producto = conn.execute(f"""
        SELECT * FROM productos
        WHERE id = ? AND empresa_id = ?
          AND COALESCE(activo, 1) = 1
          {centro_sql}
    """, params).fetchone()

    if not producto:
        conn.close()
        flash('Producto no encontrado', 'danger')
        return redirect(url_for('productos.productos'))

    if request.method == 'POST':
        nombre = request.form.get('nombre', '')
        codigo = request.form.get('codigo', '')
        cantidad = _to_float_safe(request.form.get('cantidad'), 0.0)
        unidad = request.form.get('unidad', 'kg')
        familia = request.form.get('familia', '')
        precio = _to_float_safe(request.form.get('precio'), 0.0)
        stock_minimo = _to_float_safe(request.form.get('stock_minimo'), 0.0)
        tipo_conservacion = request.form.get('tipo_conservacion', producto['tipo_conservacion'] or 'seco')
        ubicacion = request.form.get('ubicacion', '')
        alergenos = request.form.get('alergenos', '')
        alergenos_validado = 1 if request.form.get('alergenos_validado') else 0

        conn.execute("""
            UPDATE productos
            SET nombre = ?, codigo = ?, cantidad = ?, unidad = ?, familia = ?,
                precio = ?, stock_minimo = ?, tipo_conservacion = ?, ubicacion = ?,
                alergenos = ?, alergenos_validado = ?
            WHERE id = ? AND empresa_id = ?
        """, (nombre, codigo, cantidad, unidad, familia, precio,
              stock_minimo, tipo_conservacion, ubicacion,
              alergenos, alergenos_validado, producto_id, empresa_id))
        conn.commit()
        conn.close()

        flash('Producto actualizado correctamente', 'success')
        return redirect(_safe_next_url())

    conn.close()
    return render_template('productos/editar_producto.html', producto=producto, next_url=_safe_next_url())


# ============================================
# ELIMINAR PRODUCTO
# ============================================
@productos_bp.route('/<int:producto_id>/eliminar', methods=['POST'])
@login_required
def eliminar_producto(producto_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    centro_sql = " AND centro_id = ?" if centro_id else ""
    params = [producto_id, empresa_id] + ([centro_id] if centro_id else [])
    conn.execute(f"""
        UPDATE productos SET activo = 0
        WHERE id = ? AND empresa_id = ?
          {centro_sql}
    """, params)
    conn.commit()
    conn.close()

    flash('Producto eliminado correctamente', 'success')
    return redirect(_safe_next_url())


# ============================================
# PROVEEDORES DEL PRODUCTO
# ============================================
@productos_bp.route('/<int:producto_id>/proveedores')
@login_required
def producto_proveedores(producto_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    cur = conn.cursor()
    _asegurar_estructura_producto_proveedor(cur)
    conn.commit()

    centro_sql = " AND centro_id = ?" if centro_id else ""
    params = [producto_id, empresa_id] + ([centro_id] if centro_id else [])
    producto = conn.execute(f"""
        SELECT * FROM productos
        WHERE id = ? AND empresa_id = ?
          AND COALESCE(activo, 1) = 1
          {centro_sql}
    """, params).fetchone()

    if not producto:
        conn.close()
        flash('Producto no encontrado', 'danger')
        return redirect(url_for('productos.productos'))

    proveedores = conn.execute("""
        SELECT id, nombre FROM proveedores
        WHERE COALESCE(activo, 1) = 1 AND empresa_id = ?
        """ + _proveedor_visible_producto_sql("proveedores", centro_id)[0] + """
        ORDER BY nombre
    """, [empresa_id] + _proveedor_visible_producto_sql("proveedores", centro_id)[1]).fetchall()

    relaciones = conn.execute("""
        SELECT pp.*, pr.nombre as nombre_proveedor
        FROM productos_proveedores pp
        JOIN proveedores pr ON pp.proveedor_id = pr.id
        WHERE pp.producto_id = ?
          AND COALESCE(pp.activo, 1) = 1
          AND pr.empresa_id = ?
          """ + _proveedor_visible_producto_sql("pr", centro_id)[0] + """
          AND COALESCE(pr.activo, 1) = 1
        ORDER BY COALESCE(pp.es_principal, pp.preferente, 0) DESC, pr.nombre
    """, [producto_id, empresa_id] + _proveedor_visible_producto_sql("pr", centro_id)[1]).fetchall()

    conn.close()

    return render_template('productos/producto_proveedores.html',
                           producto=producto,
                           proveedores=proveedores,
                           relaciones=relaciones)


# ============================================
# AGREGAR / ACTUALIZAR PROVEEDOR A PRODUCTO
# ============================================
@productos_bp.route('/<int:producto_id>/proveedores/agregar', methods=['POST'])
@login_required
def agregar_proveedor(producto_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    cur = conn.cursor()
    _asegurar_estructura_producto_proveedor(cur)

    proveedor_id = request.form.get('proveedor_id')
    precio = _to_float_safe(request.form.get('precio'), 0.0)
    codigo_proveedor = (request.form.get('codigo_proveedor') or request.form.get('referencia_proveedor') or '').strip()
    es_principal = 1 if request.form.get('es_principal') else 0

    if not proveedor_id:
        conn.close()
        flash('Debe seleccionar un proveedor', 'danger')
        return redirect(url_for('productos.producto_proveedores', producto_id=producto_id))

    centro_sql = " AND centro_id = ?" if centro_id else ""
    params = [producto_id, empresa_id] + ([centro_id] if centro_id else [])
    producto = conn.execute(f"""
        SELECT id FROM productos
        WHERE id = ? AND empresa_id = ?
          AND COALESCE(activo, 1) = 1
          {centro_sql}
    """, params).fetchone()
    if not producto:
        conn.close()
        flash('Producto no encontrado', 'danger')
        return redirect(url_for('productos.productos'))

    prov_scope_sql, prov_scope_params = _proveedor_visible_producto_sql("proveedores", centro_id)
    proveedor = conn.execute("""
        SELECT id FROM proveedores
        WHERE id = ? AND empresa_id = ? AND COALESCE(activo, 1) = 1
        """ + prov_scope_sql + """
    """, [proveedor_id, empresa_id] + prov_scope_params).fetchone()
    if not proveedor:
        conn.close()
        flash('Proveedor no encontrado o inactivo', 'danger')
        return redirect(url_for('productos.producto_proveedores', producto_id=producto_id))

    if es_principal:
        cur.execute("""
            UPDATE productos_proveedores
            SET es_principal = 0, preferente = 0
            WHERE producto_id = ? AND COALESCE(activo, 1) = 1
        """, (producto_id,))

    existe = conn.execute("""
        SELECT id FROM productos_proveedores
        WHERE producto_id = ? AND proveedor_id = ?
        LIMIT 1
    """, (producto_id, proveedor_id)).fetchone()

    if existe:
        cur.execute("""
            UPDATE productos_proveedores
            SET precio = ?,
                precio_compra = ?,
                precio_ultimo = ?,
                precio_vigente = ?,
                codigo_proveedor = ?,
                referencia_proveedor = ?,
                es_principal = ?,
                preferente = ?,
                activo = 1
            WHERE id = ?
        """, (precio, precio, precio, precio, codigo_proveedor or None,
              codigo_proveedor or None, es_principal, es_principal, existe['id']))
        flash('Proveedor actualizado correctamente para este producto', 'success')
    else:
        cur.execute("""
            INSERT INTO productos_proveedores (
                producto_id, proveedor_id, codigo_proveedor, referencia_proveedor,
                precio, precio_compra, precio_ultimo, precio_vigente,
                es_principal, preferente, activo
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (producto_id, proveedor_id, codigo_proveedor or None,
              codigo_proveedor or None, precio, precio, precio, precio,
              es_principal, es_principal))
        flash('Proveedor añadido correctamente', 'success')

    conn.commit()
    conn.close()
    return redirect(url_for('productos.producto_proveedores', producto_id=producto_id))


# ============================================
# ELIMINAR PROVEEDOR DE PRODUCTO
# ============================================
@productos_bp.route('/<int:producto_id>/proveedores/eliminar/<int:relacion_id>', methods=['POST'])
@login_required
def eliminar_proveedor(producto_id, relacion_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    centro_id = centro_activo_id()
    cur = conn.cursor()
    _asegurar_estructura_producto_proveedor(cur)

    centro_sql = " AND centro_id = ?" if centro_id else ""
    params = [producto_id, empresa_id] + ([centro_id] if centro_id else [])
    producto = conn.execute(f"""
        SELECT id FROM productos
        WHERE id = ? AND empresa_id = ?
          AND COALESCE(activo, 1) = 1
          {centro_sql}
    """, params).fetchone()
    if not producto:
        conn.close()
        flash('Producto no encontrado', 'danger')
        return redirect(url_for('productos.productos'))

    # No borramos físicamente si hay pedidos históricos: se desactiva para conservar trazabilidad.
    conn.execute("""
        UPDATE productos_proveedores
        SET activo = 0, es_principal = 0, preferente = 0
        WHERE id = ? AND producto_id = ?
    """, (relacion_id, producto_id))
    conn.commit()
    conn.close()

    flash('Proveedor eliminado del producto', 'success')
    return redirect(url_for('productos.producto_proveedores', producto_id=producto_id))

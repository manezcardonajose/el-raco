from flask import render_template, request, redirect, url_for, session, flash

from database import get_db
from . import stock_bp
from blueprints.auth.routes import (
    login_required,
    usuario_es_jefatura_departamento,
    usuario_es_global_operacion,
)
from helpers.ambito_helper import filtro_ambito_operativo
from helpers.centros_helper import add_columna_si_falta, centro_activo_id


ROLES_STOCK_GLOBAL = {'almacen'}


def _empresa_id():
    return session.get('user_empresa_id', 1) or 1


def _almacenes_visibles(conn, empresa_id):
    add_columna_si_falta(conn, "almacenes", "centro_id INTEGER")
    add_columna_si_falta(conn, "almacenes", "subdepartamento_id INTEGER")
    scope_sql, scope_params = filtro_ambito_operativo(
        conn,
        'a',
        empresa_id,
        "almacenes",
        punto_venta_col=None,
        extra_roles=ROLES_STOCK_GLOBAL,
    )
    return conn.execute(f"""
        SELECT a.*
        FROM almacenes a
        WHERE COALESCE(a.activo, 1) = 1
          AND {scope_sql}
        ORDER BY a.nombre
    """, scope_params).fetchall()


def _puede_gestionar_stock():
    return usuario_es_global_operacion(ROLES_STOCK_GLOBAL) or usuario_es_jefatura_departamento()


# ============================================
# VISTA PRINCIPAL DE STOCK
# ============================================
@stock_bp.route('/')
@login_required
def stock():
    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    add_columna_si_falta(conn, "productos", "centro_id INTEGER")
    conn.commit()

    busqueda = request.args.get('busqueda', '').strip()
    familia = request.args.get('familia', '').strip()
    tipo_conservacion = request.args.get('tipo_conservacion', '').strip()

    almacenes = _almacenes_visibles(conn, empresa_id)
    almacen_ids = [a['id'] for a in almacenes]

    stock = []
    if almacen_ids:
        placeholders = ','.join('?' for _ in almacen_ids)
        sql = f"""
            SELECT
                p.id,
                p.nombre,
                p.codigo,
                p.unidad,
                p.precio,
                p.familia,
                p.tipo_conservacion,
                p.stock_minimo,
                p.activo,
                COALESCE(SUM(s.cantidad), 0) as stock_total,
                COUNT(DISTINCT s.almacen_id) as num_almacenes
            FROM productos p
            LEFT JOIN stock_almacen s
                ON p.id = s.producto_id
               AND s.almacen_id IN ({placeholders})
            WHERE p.empresa_id = ? AND p.activo = 1
        """
        params = [*almacen_ids, empresa_id]
        if centro_id:
            sql += " AND p.centro_id = ?"
            params.append(centro_id)

        if busqueda:
            sql += """
                AND (
                    p.nombre LIKE ?
                    OR p.codigo LIKE ?
                )
            """
            like = f"%{busqueda}%"
            params.extend([like, like])

        if familia:
            sql += " AND COALESCE(p.familia, '') = ?"
            params.append(familia)

        if tipo_conservacion:
            sql += " AND COALESCE(p.tipo_conservacion, '') = ?"
            params.append(tipo_conservacion)

        sql += """
            GROUP BY p.id
            ORDER BY p.nombre
        """

        stock = conn.execute(sql, params).fetchall()

    familias = conn.execute("""
        SELECT DISTINCT familia
        FROM productos
        WHERE empresa_id = ?
          AND (? IS NULL OR centro_id = ?)
          AND activo = 1
          AND familia IS NOT NULL
          AND TRIM(familia) <> ''
        ORDER BY familia
    """, (empresa_id, centro_id, centro_id)).fetchall()

    tipos_conservacion = conn.execute("""
        SELECT DISTINCT tipo_conservacion
        FROM productos
        WHERE empresa_id = ?
          AND (? IS NULL OR centro_id = ?)
          AND activo = 1
          AND tipo_conservacion IS NOT NULL
          AND TRIM(tipo_conservacion) <> ''
        ORDER BY tipo_conservacion
    """, (empresa_id, centro_id, centro_id)).fetchall()

    total_valor = 0
    total_con_stock = 0
    total_sin_stock = 0

    for p in stock:
        stock_total = p['stock_total'] or 0
        precio = p['precio'] or 0

        if stock_total > 0:
            total_con_stock += 1
            total_valor += stock_total * precio
        else:
            total_sin_stock += 1

    conn.close()

    return render_template(
        'stock/stock.html',
        stock=stock,
        total_productos=len(stock),
        total_con_stock=total_con_stock,
        total_sin_stock=total_sin_stock,
        total_valor=total_valor,
        familias=familias,
        tipos_conservacion=tipos_conservacion,
        busqueda=busqueda,
        familia=familia,
        tipo_conservacion=tipo_conservacion,
        almacenes=almacenes,
    )


# ============================================
# AJUSTAR STOCK
# ============================================
@stock_bp.route('/ajustar/<int:producto_id>', methods=['GET', 'POST'])
@login_required
def ajustar_stock(producto_id):
    conn = get_db()
    empresa_id = _empresa_id()
    centro_id = centro_activo_id()
    add_columna_si_falta(conn, "productos", "centro_id INTEGER")
    conn.commit()

    producto = conn.execute("""
        SELECT *
        FROM productos
        WHERE id = ? AND empresa_id = ?
          AND COALESCE(activo, 1) = 1
          AND (? IS NULL OR centro_id = ?)
    """, (producto_id, empresa_id, centro_id, centro_id)).fetchone()

    if not producto:
        conn.close()
        flash('Producto no encontrado.', 'danger')
        return redirect(url_for('stock.stock'))

    almacenes = _almacenes_visibles(conn, empresa_id)
    almacenes_ids = {str(a['id']) for a in almacenes}

    if not _puede_gestionar_stock():
        conn.close()
        flash('Solo el jefe o segundo jefe del departamento puede ajustar stock.', 'warning')
        return redirect(url_for('stock.stock'))

    if request.method == "POST":
        almacen_id = request.form.get("almacen_id")
        if almacen_id not in almacenes_ids:
            conn.close()
            flash('No puedes ajustar stock en un almacen de otro departamento.', 'warning')
            return redirect(url_for('stock.ajustar_stock', producto_id=producto_id))

        try:
            cantidad = float(str(request.form.get("cantidad", "0")).replace(',', '.'))
        except Exception:
            cantidad = 0.0

        if cantidad == 0:
            conn.close()
            flash('El ajuste debe tener una cantidad distinta de cero.', 'warning')
            return redirect(url_for('stock.ajustar_stock', producto_id=producto_id))

        motivo = (request.form.get("motivo", "Ajuste manual") or "Ajuste manual").strip()

        existe = conn.execute("""
            SELECT id, cantidad FROM stock_almacen
            WHERE producto_id = ? AND almacen_id = ?
        """, (producto_id, almacen_id)).fetchone()

        stock_actual = float(existe['cantidad'] or 0) if existe else 0.0
        stock_final = stock_actual + cantidad
        if stock_final < 0:
            conn.close()
            flash('El ajuste dejaria el stock en negativo. Revisa la cantidad o registra primero una entrada.', 'danger')
            return redirect(url_for('stock.ajustar_stock', producto_id=producto_id))

        if existe:
            conn.execute("""
                UPDATE stock_almacen
                SET cantidad = ?
                WHERE producto_id = ? AND almacen_id = ?
            """, (stock_final, producto_id, almacen_id))
        else:
            conn.execute("""
                INSERT INTO stock_almacen (producto_id, almacen_id, cantidad)
                VALUES (?, ?, ?)
            """, (producto_id, almacen_id, cantidad))

        conn.execute("""
            INSERT INTO movimientos_stock (
                producto_id,
                almacen_id,
                tipo,
                cantidad,
                motivo,
                usuario,
                empresa_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            producto_id,
            almacen_id,
            'ajuste',
            cantidad,
            motivo,
            session.get('user_nombre', 'admin'),
            empresa_id,
        ))

        conn.commit()
        conn.close()
        flash('Stock ajustado', 'success')
        return redirect(url_for('stock.stock'))

    conn.close()
    return render_template(
        'stock/ajustar_stock.html',
        producto=producto,
        almacenes=almacenes,
    )

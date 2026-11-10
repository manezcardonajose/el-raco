from flask import render_template, request, redirect, url_for, session, flash
from database import get_db
from . import traspasos_bp
from blueprints.auth.routes import login_required

# ============================================
# LISTA DE TRASPASOS
# ============================================
@traspasos_bp.route('/')
@login_required
def traspasos():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    
    traspasos = conn.execute("""
        SELECT t.*, 
            p.nombre as producto,
            o.nombre as origen,
            d.nombre as destino
        FROM traspasos t
        JOIN productos p ON t.producto_id = p.id
        JOIN almacenes o ON t.origen_almacen_id = o.id
        JOIN almacenes d ON t.destino_almacen_id = d.id
        WHERE p.empresa_id = ? AND o.empresa_id = ? AND d.empresa_id = ?
        ORDER BY t.fecha DESC
        LIMIT 100
    """, (empresa_id, empresa_id, empresa_id)).fetchall()
    
    conn.close()
    return render_template('traspasos/traspasos.html', traspasos=traspasos)

# ============================================
# NUEVO TRASPASO
# ============================================
@traspasos_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo_traspaso():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    
    productos = conn.execute("""
        SELECT id, nombre, codigo FROM productos 
        WHERE empresa_id = ? ORDER BY nombre
    """, (empresa_id,)).fetchall()
    
    almacenes = conn.execute("""
        SELECT * FROM almacenes 
        WHERE activo = 1 AND empresa_id = ? ORDER BY nombre
    """, (empresa_id,)).fetchall()
    
    if request.method == "POST":
        origen_id = request.form["origen_id"]
        destino_id = request.form["destino_id"]
        producto_id = request.form["producto_id"]
        cantidad = float(request.form["cantidad"])
        observaciones = request.form.get("observaciones", "")
        
        stock_origen = conn.execute("""
            SELECT cantidad FROM stock_almacen 
            WHERE producto_id = ? AND almacen_id = ?
        """, (producto_id, origen_id)).fetchone()
        
        if not stock_origen or stock_origen['cantidad'] < cantidad:
            flash(' Stock insuficiente en origen', 'danger')
            conn.close()
            return redirect(url_for('traspasos.nuevo_traspaso'))
        
        # Restar del origen
        conn.execute("""
            UPDATE stock_almacen 
            SET cantidad = cantidad - ? 
            WHERE producto_id = ? AND almacen_id = ?
        """, (cantidad, producto_id, origen_id))
        
        # Sumar al destino
        existe_destino = conn.execute("""
            SELECT id FROM stock_almacen 
            WHERE producto_id = ? AND almacen_id = ?
        """, (producto_id, destino_id)).fetchone()
        
        if existe_destino:
            conn.execute("""
                UPDATE stock_almacen 
                SET cantidad = cantidad + ? 
                WHERE producto_id = ? AND almacen_id = ?
            """, (cantidad, producto_id, destino_id))
        else:
            conn.execute("""
                INSERT INTO stock_almacen (producto_id, almacen_id, cantidad)
                VALUES (?, ?, ?)
            """, (producto_id, destino_id, cantidad))
        
        # Registrar traspaso
        conn.execute("""
            INSERT INTO traspasos (origen_almacen_id, destino_almacen_id, producto_id, cantidad, usuario, observaciones)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (origen_id, destino_id, producto_id, cantidad, session.get('user_nombre', 'admin'), observaciones))
        
        # Registrar movimientos
        conn.execute("""
            INSERT INTO movimientos_stock (producto_id, almacen_id, tipo, cantidad, motivo, usuario, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (producto_id, origen_id, 'salida', cantidad, f'Traspaso a almacén {destino_id}', session.get('user_nombre', 'admin'), empresa_id))
        
        conn.execute("""
            INSERT INTO movimientos_stock (producto_id, almacen_id, tipo, cantidad, motivo, usuario, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (producto_id, destino_id, 'entrada', cantidad, f'Traspaso desde almacén {origen_id}', session.get('user_nombre', 'admin'), empresa_id))
        
        conn.commit()
        conn.close()
        
        flash(' Traspaso realizado', 'success')
        return redirect(url_for('traspasos.traspasos'))
    
    conn.close()
    return render_template('traspasos/nuevo_traspaso.html', productos=productos, almacenes=almacenes)


from flask import render_template, request, redirect, url_for, session, flash
from database import get_db
from . import movimientos_bp
from blueprints.auth.routes import login_required

# ============================================
# LISTA DE MOVIMIENTOS
# ============================================
@movimientos_bp.route('/')
@login_required
def movimientos():
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    
    movimientos = conn.execute("""
        SELECT m.*, p.nombre as producto, a.nombre as almacen
        FROM movimientos_stock m
        JOIN productos p ON m.producto_id = p.id
        JOIN almacenes a ON m.almacen_id = a.id
        WHERE p.empresa_id = ? AND a.empresa_id = ?
        ORDER BY m.fecha DESC
        LIMIT 100
    """, (empresa_id, empresa_id)).fetchall()
    
    conn.close()
    
    return render_template('movimientos/movimientos.html', movimientos=movimientos)


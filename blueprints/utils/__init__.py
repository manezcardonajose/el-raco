# blueprints/utils/__init__.py
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, send_file, make_response, current_app
from database import get_db
from datetime import datetime
from io import BytesIO
import pandas as pd
import pdfkit
import os
from blueprints.auth.routes import login_required
from helpers.email_helper import enviar_email

# Crear el blueprint
utils_bp = Blueprint('utils', __name__)

# Configuración PDF
WKHTMLTOPDF_PATH = r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe'
config = pdfkit.configuration(wkhtmltopdf=WKHTMLTOPDF_PATH) if os.path.exists(WKHTMLTOPDF_PATH) else None

# ============================================
# EXPORTACIONES GENERALES
# ============================================
@utils_bp.route('/exportar/<string:modulo>')
@login_required
def exportar(modulo):
    empresa_id = session.get('user_empresa_id', 1)
    conn = get_db()
    
    if modulo == 'productos':
        df = pd.read_sql_query("""
            SELECT codigo, nombre, familia, tipo_conservacion, cantidad as stock, 
                unidad, precio, ubicacion, stock_minimo
            FROM productos 
            WHERE empresa_id = ? AND activo = 1
            ORDER BY familia, nombre
        """, conn, params=(empresa_id,))
        filename = 'productos.xlsx'
    
    elif modulo == 'recetas':
        df = pd.read_sql_query("""
            SELECT r.nombre, r.categoria, r.tiempo_preparacion, 
                COUNT(ri.id) as num_ingredientes,
                SUM(p.precio * ri.cantidad) as coste_total
            FROM recetas r
            LEFT JOIN recetas_ingredientes ri ON r.id = ri.receta_id
            LEFT JOIN productos p ON ri.producto_id = p.id
            WHERE r.empresa_id = ?
            GROUP BY r.id
            ORDER BY r.nombre
        """, conn, params=(empresa_id,))
        filename = 'recetas.xlsx'
    
    elif modulo == 'pedidos':
        df = pd.read_sql_query("""
            SELECT p.numero_pedido, pr.nombre as proveedor, p.fecha_pedido, 
                p.estado, COUNT(lp.id) as num_lineas,
                SUM(lp.cantidad_solicitada * lp.precio_unitario) as total
            FROM pedidos p
            JOIN proveedores pr ON p.proveedor_id = pr.id
            LEFT JOIN lineas_pedido lp ON p.id = lp.pedido_id
            WHERE p.empresa_id = ?
            GROUP BY p.id
            ORDER BY p.fecha_pedido DESC
        """, conn, params=(empresa_id,))
        filename = 'pedidos.xlsx'
    
    elif modulo == 'stock':
        df = pd.read_sql_query("""
            SELECT p.codigo, p.nombre, p.familia, 
                COALESCE(SUM(s.cantidad), 0) as stock_total,
                p.unidad, p.precio,
                (COALESCE(SUM(s.cantidad), 0) * p.precio) as valor_total
            FROM productos p
            LEFT JOIN stock_almacen s ON p.id = s.producto_id
            WHERE p.empresa_id = ? AND p.activo = 1
            GROUP BY p.id
            HAVING stock_total > 0
            ORDER BY p.nombre
        """, conn, params=(empresa_id,))
        filename = 'stock_actual.xlsx'
    
    else:
        conn.close()
        flash('Módulo no válido', 'danger')
        return redirect(request.referrer or '/')
    
    conn.close()
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name=modulo.capitalize(), index=False)
    
    output.seek(0)
    
    return send_file(
        output,
        download_name=filename,
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

# ============================================
# INFORMES PDF
# ============================================
@utils_bp.route('/informe/<string:modulo>/pdf')
@login_required
def informe_pdf(modulo):
    empresa_id = session.get('user_empresa_id', 1)
    conn = get_db()
    
    if modulo == 'stock':
        data = conn.execute("""
            SELECT p.codigo, p.nombre, p.familia, 
                COALESCE(SUM(s.cantidad), 0) as stock_total,
                p.unidad, p.precio,
                (COALESCE(SUM(s.cantidad), 0) * p.precio) as valor_total
            FROM productos p
            LEFT JOIN stock_almacen s ON p.id = s.producto_id
            WHERE p.empresa_id = ? AND p.activo = 1
            GROUP BY p.id
            HAVING stock_total > 0
            ORDER BY p.nombre
        """, (empresa_id,)).fetchall()
        total_valor = sum(row['valor_total'] for row in data)
        html_content = render_template('informes/stock_pdf.html', 
                                     data=data, 
                                     total_valor=total_valor,
                                     fecha=datetime.now().strftime('%d/%m/%Y %H:%M'))
    
    elif modulo == 'pedidos':
        data = conn.execute("""
            SELECT p.numero_pedido, pr.nombre as proveedor, p.fecha_pedido, 
                p.estado, COUNT(lp.id) as num_lineas,
                SUM(lp.cantidad_solicitada * lp.precio_unitario) as total
            FROM pedidos p
            JOIN proveedores pr ON p.proveedor_id = pr.id
            LEFT JOIN lineas_pedido lp ON p.id = lp.pedido_id
            WHERE p.empresa_id = ?
            GROUP BY p.id
            ORDER BY p.fecha_pedido DESC
            LIMIT 100
        """, (empresa_id,)).fetchall()
        html_content = render_template('informes/pedidos_pdf.html',
                                     data=data,
                                     fecha=datetime.now().strftime('%d/%m/%Y %H:%M'))
    
    else:
        conn.close()
        flash('Módulo no válido', 'danger')
        return redirect(request.referrer or '/')
    
    conn.close()
    
    try:
        options = {
            'page-size': 'A4',
            'margin-top': '20mm',
            'margin-right': '15mm',
            'margin-bottom': '20mm',
            'margin-left': '15mm',
            'encoding': 'UTF-8',
            'enable-local-file-access': None
        }
        if not config:
            flash('wkhtmltopdf no está instalado o configurado', 'danger')
            return redirect(request.referrer or '/')
            
        pdf = pdfkit.from_string(html_content, False, options=options, configuration=config)
        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'inline; filename={modulo}_{datetime.now().strftime("%Y%m%d")}.pdf'
        return response
    except Exception as e:
        flash(f'Error generando PDF: {str(e)}', 'danger')
        return redirect(request.referrer or '/')

# ============================================
# TEST EMAIL
# ============================================
@utils_bp.route('/test-email', methods=['GET', 'POST'])
@login_required
def test_email():
    if request.method == 'POST':
        destinatario = request.form.get('email', '')
        asunto = request.form.get('asunto', 'Prueba de email desde El Racó ERP')
        mensaje = request.form.get('mensaje', '<h1>Email de prueba</h1><p>Funciona correctamente.</p>')
        
        if not destinatario:
            flash('❌ Debes especificar un destinatario', 'danger')
            return redirect(url_for('utils.test_email'))
        
        # Obtener mail de current_app en lugar de importarlo
        from flask import current_app
        mail_instance = current_app.extensions.get('mail')
        
        exito, resultado = enviar_email(destinatario, asunto, mensaje, mail_instance=mail_instance)
        
        if exito:
            flash(f'✅ Email enviado a {destinatario}', 'success')
        else:
            flash(f'❌ Error: {resultado}', 'danger')
        
        return redirect(url_for('utils.test_email'))
    
    # Obtener configuración de current_app
    from flask import current_app
    mail_server = current_app.config.get('MAIL_SERVER', 'No configurado')
    mail_port = current_app.config.get('MAIL_PORT', 'No configurado')
    mail_user = current_app.config.get('MAIL_USERNAME', 'No configurado')
    mail_pass = '✅ Configurada' if current_app.config.get('MAIL_PASSWORD') else '❌ No configurada'
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Prueba de Email</title>
        <style>
            body {{ font-family: Arial, sans-serif; background: #1a1e24; color: #e0e0e0; padding: 20px; }}
            .container {{ max-width: 600px; margin: 0 auto; background: #252b33; padding: 30px; border-radius: 8px; }}
            h2 {{ color: #ff6b00; margin-top: 0; }}
            label {{ display: block; margin-bottom: 5px; color: #999; }}
            input, textarea {{ width: 100%; padding: 10px; margin-bottom: 20px; background: #1e252b; border: 1px solid #2c3e50; border-radius: 4px; color: white; }}
            button {{ background: #ff6b00; color: white; padding: 12px 24px; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }}
            button:hover {{ opacity: 0.8; }}
            .info {{ margin-bottom: 20px; padding: 10px; background: #1e2a3a; border-left: 4px solid #17a2b8; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>📧 Prueba de Email</h2>
            <div class="info">
                <strong>Configuración actual:</strong><br>
                Servidor: {mail_server}<br>
                Puerto: {mail_port}<br>
                Usuario: {mail_user}<br>
                Contraseña: {mail_pass}
            </div>
            <form method="POST">
                <div>
                    <label>Destinatario:</label>
                    <input type="email" name="email" value="{session.get('user_email', '')}" required>
                </div>
                <div>
                    <label>Asunto:</label>
                    <input type="text" name="asunto" value="Prueba de email desde El Racó ERP">
                </div>
                <div>
                    <label>Mensaje (HTML):</label>
                    <textarea name="mensaje" rows="6"><h1>Email de prueba</h1>
<p>Este es un email enviado desde El Racó ERP.</p>
<p><strong>Fecha:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</p>
<p><strong>Usuario:</strong> {session.get('user_nombre', 'Desconocido')}</p>
<hr>
<p style="color: #666;">Si recibes esto, el sistema de email funciona correctamente.</p></textarea>
                </div>
                <button type="submit">📧 Enviar email</button>
                <a href="/" style="margin-left: 15px; color: #999; text-decoration: none;">⬅ Volver al inicio</a>
            </form>
            <div style="margin-top: 30px; font-size: 12px; color: #666;">
                <strong>Nota:</strong> Para Gmail necesitas usar una contraseña de aplicación, no tu contraseña normal.
            </div>
        </div>
    </body>
    </html>
    '''

# ============================================
# API DE ALERTAS (para el contador del menú)
# ============================================
@utils_bp.route('/api/alertas/descongelados')
@login_required
def api_alertas_descongelados():
    """API para contar alertas de descongelado pendientes"""
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    texto = "LOWER(COALESCE(p.familia, '') || ' ' || COALESCE(p.nombre, ''))"
    familia = "LOWER(COALESCE(p.familia, ''))"
    conservacion = "LOWER(COALESCE(p.tipo_conservacion, ''))"
    filtro_descongelado = f"""
        (
            {conservacion} LIKE '%congel%'
            OR {texto} LIKE '%congel%'
            OR {texto} LIKE '%cong%'
            OR {texto} LIKE '%freezer%'
            OR {texto} LIKE '%uco%'
        )
        AND {familia} NOT IN ('helados', 'bebidas', 'bebidas_agua', 'bebidas_refrescos', 'bebidas_licores', 'bebidas_premium')
        AND {texto} NOT LIKE '%helado%'
        AND {texto} NOT LIKE '%ice cream%'
        AND {texto} NOT LIKE '%polo%'
        AND {texto} NOT LIKE '%sorbete%'
        AND {texto} NOT LIKE '%granizado%'
    """
    
    total = conn.execute(f"""
        SELECT COUNT(*) as total 
        FROM alertas_programadas a
        JOIN lotes l ON a.lote_id = l.id
        JOIN productos p ON l.producto_id = p.id
        WHERE COALESCE(a.enviada, 0) = 0
          AND COALESCE(a.resuelta, 0) = 0
          AND a.tipo = 'descongelado'
          AND a.fecha_alerta <= datetime('now', '+7 days')
          AND a.empresa_id = ?
          AND {filtro_descongelado}
    """, (empresa_id,)).fetchone()['total'] or 0
    
    conn.close()
    return {'total': total}

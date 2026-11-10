from pathlib import Path
import py_compile
import shutil
from datetime import datetime

ROUTES = Path('blueprints/compras/routes.py')

if not ROUTES.exists():
    raise SystemExit(f'No encuentro {ROUTES}. Ejecuta este script desde la raíz del proyecto pepe4.')

text = ROUTES.read_text(encoding='utf-8')
backup = ROUTES.with_suffix('.py.backup_email_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
shutil.copy2(ROUTES, backup)

start = text.find("@compras_bp.route('/pedidos/<int:pedido_id>/imprimir')")
end = text.find("# ============================================\n# RECEPCIÓN DE PEDIDOS", start)
if start == -1 or end == -1:
    raise SystemExit('No he encontrado el bloque imprimir/enviar pedido. No se ha cambiado nada.')

nuevo_bloque = r'''@compras_bp.route('/pedidos/<int:pedido_id>/imprimir')
@login_required
def imprimir_pedido(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    pedido = conn.execute("""
        SELECT p.*,
               pr.nombre AS proveedor_nombre,
               pr.email,
               pr.telefono,
               pr.nif,
               d.nombre AS departamento_nombre
        FROM pedidos p
        JOIN proveedores pr ON p.proveedor_id = pr.id
        LEFT JOIN departamentos d ON d.id = p.departamento_id
        WHERE p.id = ? AND p.empresa_id = ?
    """, (pedido_id, empresa_id)).fetchone()

    if not pedido:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    lineas = conn.execute("""
        SELECT l.*,
               prod.nombre,
               prod.unidad,
               prod.codigo,
               prod.referencia_proveedor
        FROM lineas_pedido l
        JOIN productos prod ON l.producto_id = prod.id
        WHERE l.pedido_id = ? AND prod.empresa_id = ?
        ORDER BY prod.nombre
    """, (pedido_id, empresa_id)).fetchall()

    conn.close()
    return render_template('compras/imprimir_pedido.html', pedido=pedido, lineas=lineas)


@compras_bp.route('/pedidos/<int:pedido_id>/enviar-email')
@login_required
def enviar_pedido_por_email(pedido_id):
    from flask_mail import Mail, Message

    def _asegurar_config_mail_desde_env():
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:
            pass

        for clave in ['MAIL_SERVER', 'MAIL_USERNAME', 'MAIL_PASSWORD', 'MAIL_DEFAULT_SENDER']:
            valor = os.environ.get(clave)
            if valor and not current_app.config.get(clave):
                current_app.config[clave] = valor

        mail_port = os.environ.get('MAIL_PORT')
        if mail_port and not current_app.config.get('MAIL_PORT'):
            try:
                current_app.config['MAIL_PORT'] = int(mail_port)
            except Exception:
                pass

        for clave_bool in ['MAIL_USE_TLS', 'MAIL_USE_SSL']:
            valor = os.environ.get(clave_bool)
            if valor is not None and current_app.config.get(clave_bool) is None:
                current_app.config[clave_bool] = str(valor).strip().lower() in ('1', 'true', 'yes', 'si', 'sí')

        if current_app.config.get('MAIL_USE_TLS') is None:
            current_app.config['MAIL_USE_TLS'] = True
        if current_app.config.get('MAIL_USE_SSL') is None:
            current_app.config['MAIL_USE_SSL'] = False

        remitente = (
            current_app.config.get('MAIL_DEFAULT_SENDER')
            or current_app.config.get('MAIL_USERNAME')
            or os.environ.get('MAIL_DEFAULT_SENDER')
            or os.environ.get('MAIL_USERNAME')
            or ''
        )
        if remitente and not current_app.config.get('MAIL_DEFAULT_SENDER'):
            current_app.config['MAIL_DEFAULT_SENDER'] = remitente

    def _obtener_mail_actual():
        _asegurar_config_mail_desde_env()
        extension_mail = current_app.extensions.get('mail')
        if extension_mail:
            return extension_mail
        return Mail(current_app)

    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)

    pedido = conn.execute("""
        SELECT p.*,
               pr.nombre AS proveedor_nombre,
               pr.email,
               pr.telefono,
               pr.nif,
               d.nombre AS departamento_nombre
        FROM pedidos p
        JOIN proveedores pr ON p.proveedor_id = pr.id
        LEFT JOIN departamentos d ON d.id = p.departamento_id
        WHERE p.id = ? AND p.empresa_id = ?
    """, (pedido_id, empresa_id)).fetchone()

    if not pedido:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    email_proveedor = (pedido['email'] or '').strip()
    if not email_proveedor:
        conn.close()
        flash('El proveedor no tiene email configurado.', 'warning')
        return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

    lineas = conn.execute("""
        SELECT l.*,
               prod.nombre,
               prod.unidad,
               prod.codigo,
               prod.referencia_proveedor
        FROM lineas_pedido l
        JOIN productos prod ON l.producto_id = prod.id
        WHERE l.pedido_id = ? AND prod.empresa_id = ?
        ORDER BY prod.nombre
    """, (pedido_id, empresa_id)).fetchall()
    conn.close()

    numero_pedido = pedido['numero_pedido']
    departamento = pedido['departamento_nombre'] or '-'
    fecha_entrega = pedido['fecha_entrega'] or '-'

    cuerpo = f"""Estimado proveedor,

Le rogamos atienda el pedido adjunto.

Pedido: {numero_pedido}
Departamento: {departamento}
Fecha prevista de entrega: {fecha_entrega}

Cualquier incidencia, falta de género o modificación, por favor comuníquelo antes de la entrega.

Un saludo.
"""

    html_pedido = render_template('compras/imprimir_pedido.html', pedido=pedido, lineas=lineas)
    pdf_bytes = None
    try:
        from weasyprint import HTML
        pdf_bytes = HTML(string=html_pedido, base_url=request.url_root).write_pdf()
    except Exception:
        pdf_bytes = None

    try:
        mail_actual = _obtener_mail_actual()
        remitente = (
            current_app.config.get('MAIL_DEFAULT_SENDER')
            or current_app.config.get('MAIL_USERNAME')
            or os.environ.get('MAIL_DEFAULT_SENDER')
            or os.environ.get('MAIL_USERNAME')
        )

        msg = Message(
            subject=f'Pedido {numero_pedido}',
            recipients=[email_proveedor],
            body=cuerpo,
            sender=remitente
        )

        if pdf_bytes:
            msg.attach(
                filename=f'pedido_{numero_pedido}.pdf',
                content_type='application/pdf',
                data=pdf_bytes
            )
        else:
            msg.html = html_pedido

        mail_actual.send(msg)

        conn = get_db()
        conn.execute("""
            UPDATE pedidos
            SET estado = 'enviado', fecha_envio = ?
            WHERE id = ? AND empresa_id = ?
        """, (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), pedido_id, empresa_id))
        conn.commit()
        conn.close()

        flash('Email enviado al proveedor y pedido marcado como enviado.', 'success')
    except Exception as e:
        flash(f'Error al enviar email: {str(e)}', 'danger')

    return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

'''

nuevo_texto = text[:start] + nuevo_bloque + text[end:]
ROUTES.write_text(nuevo_texto, encoding='utf-8')
try:
    py_compile.compile(str(ROUTES), doraise=True)
except Exception as e:
    shutil.copy2(backup, ROUTES)
    raise SystemExit(f'El cambio generó error de sintaxis y se restauró el backup: {e}')

print(f'OK. Backup creado: {backup}')
print('Corregido: imprimir pedido con departamento y enviar email sin from app import mail.')

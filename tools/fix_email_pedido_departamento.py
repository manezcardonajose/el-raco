from pathlib import Path
import re
import shutil
import datetime

RUTA = Path(r"C:\Users\jmpep\Desktop\pepe4\blueprints\compras\routes.py")

if not RUTA.exists():
    raise SystemExit(f"No encuentro el archivo: {RUTA}")

texto = RUTA.read_text(encoding="utf-8")
backup = RUTA.with_suffix(f".backup_email_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.py")
shutil.copy2(RUTA, backup)

helpers = r'''

def detectar_numero_albaran_desde_texto(texto):
    """Detecta número de albarán en OCR/PDF sin crear datos."""
    if not texto:
        return None
    texto_limpio = str(texto).upper()
    patrones = [
        r'\bALB[-\s]*(\d{8})[-\s]*(\d{3,6})\b',
        r'\bALBAR[ÁA]N\s*(?:N[ºO]\.?\s*)?(ALB[-\s]*\d{8}[-\s]*\d{3,6})\b',
        r'\bALBAR[ÁA]N\s*(?:N[ºO]\.?\s*)?([A-Z0-9][A-Z0-9\-/]{3,30})\b',
        r'\bN[ºO]\s*ALBAR[ÁA]N\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{3,30})\b',
    ]
    for patron in patrones:
        m = re.search(patron, texto_limpio, flags=re.I)
        if not m:
            continue
        if len(m.groups()) == 2:
            return f"ALB-{m.group(1)}-{m.group(2)}"
        valor = (m.group(1) or '').strip().replace(' ', '')
        if valor:
            return valor
    return None


def _asegurar_config_mail_desde_env():
    """Carga configuración de correo desde .env/entorno si app.py no la ha cargado."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    for clave in ['MAIL_SERVER', 'MAIL_USERNAME', 'MAIL_PASSWORD', 'MAIL_DEFAULT_SENDER']:
        valor = os.environ.get(clave)
        if valor and not current_app.config.get(clave):
            current_app.config[clave] = valor

    puerto = os.environ.get('MAIL_PORT')
    if puerto and not current_app.config.get('MAIL_PORT'):
        try:
            current_app.config['MAIL_PORT'] = int(str(puerto).strip())
        except Exception:
            pass

    for clave_bool in ['MAIL_USE_TLS', 'MAIL_USE_SSL']:
        valor = os.environ.get(clave_bool)
        if valor is not None and current_app.config.get(clave_bool) is None:
            current_app.config[clave_bool] = str(valor).strip().lower() in ('1', 'true', 'yes', 'si', 'sí')

    if not current_app.config.get('MAIL_DEFAULT_SENDER'):
        usuario_mail = current_app.config.get('MAIL_USERNAME') or os.environ.get('MAIL_USERNAME')
        if usuario_mail:
            current_app.config['MAIL_DEFAULT_SENDER'] = usuario_mail


def _obtener_mail_app():
    """Devuelve Flask-Mail sin depender de una variable global llamada mail en routes.py."""
    _asegurar_config_mail_desde_env()
    try:
        from app import mail
        return mail
    except Exception:
        return current_app.extensions.get('mail')
'''

if 'def detectar_numero_albaran_desde_texto' not in texto:
    marca = 'def detectar_lineas_documento_compra(texto):'
    if marca not in texto:
        raise SystemExit('No encuentro detectar_lineas_documento_compra para insertar helpers.')
    texto = texto.replace(marca, helpers + '\n' + marca, 1)
else:
    # Si ya existe albarán pero faltan helpers mail, inserta solo mail antes de detectar_lineas si hace falta.
    if 'def _obtener_mail_app' not in texto:
        mail_helpers = helpers.split('def _asegurar_config_mail_desde_env():', 1)[1]
        texto = texto.replace('def detectar_lineas_documento_compra(texto):', '\ndef _asegurar_config_mail_desde_env():' + mail_helpers + '\ndef detectar_lineas_documento_compra(texto):', 1)

nuevo_imprimir = r'''@compras_bp.route('/pedidos/<int:pedido_id>/imprimir')
@login_required
def imprimir_pedido(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    columnas_productos = _columnas_tabla(cur, 'productos')
    referencia_expr = "prod.referencia_proveedor" if 'referencia_proveedor' in columnas_productos else "''"

    pedido = conn.execute("""
        SELECT p.*, pr.nombre AS proveedor_nombre, pr.email, pr.telefono, pr.nif,
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

    lineas = conn.execute(f"""
        SELECT l.*, prod.nombre, prod.unidad, prod.codigo,
               {referencia_expr} AS referencia_proveedor
        FROM lineas_pedido l
        JOIN productos prod ON l.producto_id = prod.id
        WHERE l.pedido_id = ? AND prod.empresa_id = ?
        ORDER BY prod.nombre
    """, (pedido_id, empresa_id)).fetchall()

    conn.close()
    return render_template('compras/imprimir_pedido.html', pedido=pedido, lineas=lineas)

'''

nuevo_email = r'''@compras_bp.route('/pedidos/<int:pedido_id>/enviar-email')
@login_required
def enviar_pedido_por_email(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    columnas_productos = _columnas_tabla(cur, 'productos')
    referencia_expr = "prod.referencia_proveedor" if 'referencia_proveedor' in columnas_productos else "''"

    pedido = conn.execute("""
        SELECT p.*, pr.nombre AS proveedor_nombre, pr.email, pr.telefono, pr.nif,
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

    lineas = conn.execute(f"""
        SELECT l.*, prod.nombre, prod.unidad, prod.codigo,
               {referencia_expr} AS referencia_proveedor
        FROM lineas_pedido l
        JOIN productos prod ON l.producto_id = prod.id
        WHERE l.pedido_id = ? AND prod.empresa_id = ?
        ORDER BY prod.nombre
    """, (pedido_id, empresa_id)).fetchall()

    html_pedido = render_template('compras/imprimir_pedido.html', pedido=pedido, lineas=lineas)

    cuerpo = f"""Estimado proveedor,

Le rogamos atienda el pedido adjunto.

Pedido: {pedido['numero_pedido']}
Departamento: {pedido['departamento_nombre'] or '-'}
Fecha prevista de entrega: {pedido['fecha_entrega'] or '-'}

Cualquier incidencia, falta de género o cambio en cantidades, por favor comuníquelo antes de la entrega.

Un saludo.
"""

    pdf_bytes = None
    error_pdf = None
    try:
        from weasyprint import HTML
        pdf_bytes = HTML(string=html_pedido, base_url=current_app.root_path).write_pdf()
    except Exception as e:
        error_pdf = str(e)

    try:
        from flask_mail import Message
        mail = _obtener_mail_app()
        if mail is None:
            raise RuntimeError('Flask-Mail no está inicializado en la aplicación.')

        msg = Message(
            subject=f"Pedido {pedido['numero_pedido']} - {pedido['departamento_nombre'] or 'Nexora Kitchen'}",
            recipients=[email_proveedor],
            body=cuerpo,
            html=html_pedido
        )

        if pdf_bytes:
            msg.attach(
                filename=f"pedido_{pedido['numero_pedido']}.pdf",
                content_type='application/pdf',
                data=pdf_bytes
            )

        mail.send(msg)

        conn.execute("""
            UPDATE pedidos
            SET estado = 'enviado', fecha_envio = ?
            WHERE id = ? AND empresa_id = ?
        """, (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), pedido_id, empresa_id))
        conn.commit()

        if pdf_bytes:
            flash('Email enviado al proveedor con PDF adjunto y pedido marcado como enviado.', 'success')
        else:
            flash(f'Email enviado, pero no se pudo generar el PDF adjunto: {error_pdf}', 'warning')

    except Exception as e:
        conn.rollback()
        flash(f'Error al enviar email: {str(e)}', 'danger')

    finally:
        conn.close()

    return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

'''

patron = re.compile(
    r"@compras_bp\.route\('/pedidos/<int:pedido_id>/imprimir'\)\n@login_required\ndef imprimir_pedido\(pedido_id\):.*?"
    r"@compras_bp\.route\('/pedidos/<int:pedido_id>/enviar-email'\)\n@login_required\ndef enviar_pedido_por_email\(pedido_id\):.*?"
    r"return redirect\(url_for\('compras\.ver_pedido', pedido_id=pedido_id\)\)\n",
    re.S
)

texto_nuevo, total = patron.subn(nuevo_imprimir + nuevo_email, texto, count=1)
if total != 1:
    raise SystemExit(f'No se pudo reemplazar imprimir/enviar-email. Coincidencias: {total}')

RUTA.write_text(texto_nuevo, encoding="utf-8")
print(f"OK. Backup creado: {backup}")
print(f"Líneas antes: {len(texto.splitlines())}")
print(f"Líneas después: {len(texto_nuevo.splitlines())}")

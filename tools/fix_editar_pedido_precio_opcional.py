from pathlib import Path
import re
import shutil
from datetime import datetime

ROUTES = Path(r"C:\Users\jmpep\Desktop\pepe4\blueprints\compras\routes.py")

NUEVA_FUNCION = r"""@compras_bp.route('/pedidos/<int:pedido_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_pedido(pedido_id):
    conn = get_db()
    empresa_id = session.get('user_empresa_id', 1)
    cur = conn.cursor()
    _asegurar_estructura_compras(cur)
    conn.commit()

    pedido = conn.execute(
        "SELECT * FROM pedidos WHERE id = ? AND empresa_id = ?",
        (pedido_id, empresa_id)
    ).fetchone()

    if not pedido:
        conn.close()
        flash('Pedido no encontrado.', 'danger')
        return redirect(url_for('compras.lista_pedidos'))

    if request.method == "POST":
        productos = request.form.getlist("producto_id[]")
        cantidades = request.form.getlist("cantidad[]")
        precios = request.form.getlist("precio[]")

        try:
            conn.execute("DELETE FROM lineas_pedido WHERE pedido_id = ?", (pedido_id,))

            lineas_guardadas = 0

            for i in range(len(productos)):
                producto_id = (productos[i] or '').strip()
                cantidad_raw = cantidades[i] if i < len(cantidades) else ''
                precio_raw = precios[i] if i < len(precios) else ''

                cantidad = _to_float_safe(cantidad_raw, 0.0)
                precio = _to_float_safe(precio_raw, 0.0)

                if not producto_id or cantidad <= 0:
                    continue

                conn.execute(\"\"\"
                    INSERT INTO lineas_pedido (
                        pedido_id,
                        producto_id,
                        cantidad_solicitada,
                        precio_unitario
                    )
                    VALUES (?, ?, ?, ?)
                \"\"\", (
                    pedido_id,
                    producto_id,
                    cantidad,
                    precio
                ))

                lineas_guardadas += 1

            if lineas_guardadas == 0:
                conn.rollback()
                conn.close()
                flash('No se ha guardado ninguna línea. Selecciona producto y cantidad.', 'warning')
                return redirect(url_for('compras.editar_pedido', pedido_id=pedido_id))

            conn.commit()
            conn.close()

            flash('Pedido actualizado correctamente.', 'success')
            return redirect(url_for('compras.ver_pedido', pedido_id=pedido_id))

        except Exception as e:
            conn.rollback()
            conn.close()
            flash(f'Error actualizando pedido: {str(e)}', 'danger')
            return redirect(url_for('compras.editar_pedido', pedido_id=pedido_id))

    productos = conn.execute(\"\"\"
        SELECT id, nombre, codigo, precio
        FROM productos
        WHERE empresa_id = ?
        ORDER BY nombre
    \"\"\", (empresa_id,)).fetchall()

    lineas = conn.execute(\"\"\"
        SELECT l.*, p.nombre, p.unidad
        FROM lineas_pedido l
        JOIN productos p ON l.producto_id = p.id
        WHERE l.pedido_id = ?
          AND p.empresa_id = ?
    \"\"\", (pedido_id, empresa_id)).fetchall()

    conn.close()

    return render_template(
        'compras/editar_pedido.html',
        pedido=pedido,
        productos=productos,
        lineas=lineas
    )

"""

def main():
    if not ROUTES.exists():
        raise FileNotFoundError(f"No existe: {ROUTES}")

    texto = ROUTES.read_text(encoding="utf-8", errors="replace")
    patron = (
        r"@compras_bp\.route\('/pedidos/<int:pedido_id>/editar', methods=\['GET', 'POST'\]\)\n"
        r"@login_required\n"
        r"def editar_pedido\(pedido_id\):.*?"
        r"(?=\n@compras_bp\.route\('/pedidos/<int:pedido_id>/cambiar-estado/<string:estado>'\))"
    )

    nuevo, n = re.subn(patron, NUEVA_FUNCION, texto, count=1, flags=re.S)
    if n != 1:
        raise RuntimeError("No se encontró exactamente una función editar_pedido para sustituir. No se ha tocado el archivo.")

    backup = ROUTES.with_suffix(".py.bak_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(ROUTES, backup)
    ROUTES.write_text(nuevo, encoding="utf-8")

    print("OK: editar_pedido corregida.")
    print(f"Backup creado: {backup}")
    print(f"Líneas antes: {len(texto.splitlines())}")
    print(f"Líneas después: {len(nuevo.splitlines())}")

if __name__ == "__main__":
    main()

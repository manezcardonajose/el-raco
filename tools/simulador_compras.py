# tools/simulador_compras.py
# Simulador defensivo para probar compras sin tocar rutas de la ERP.
# Crea: pedido -> líneas -> albarán JPG simple para OCR -> opcional recepción simulada.

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont

DB_PATH = Path(__file__).resolve().parents[1] / "pepe4.db"
SALIDA_DIR = Path(__file__).resolve().parents[1] / "simulaciones_compras"
EMPRESA_ID = 1
DEPARTAMENTO_NOMBRE = "COCINA CENTRAL"
PROVEEDOR_NOMBRE = "pepe"
CREAR_RECEPCION = False  # mantener False para no alterar stock en pruebas OCR


def conectar():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def columnas(conn, tabla):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({tabla})").fetchall()]


def buscar_id(conn, tabla, nombre):
    row = conn.execute(f"SELECT id FROM {tabla} WHERE LOWER(nombre)=LOWER(?) LIMIT 1", (nombre,)).fetchone()
    return row["id"] if row else None


def numero_pedido(conn):
    fecha = datetime.now().strftime("%Y%m%d")
    total = conn.execute("SELECT COUNT(*) AS total FROM pedidos WHERE fecha_pedido=date('now') AND empresa_id=?", (EMPRESA_ID,)).fetchone()["total"] + 1
    return f"PED-{fecha}-{total:03d}"


def productos_con_proveedor(conn, proveedor_id, limite=3):
    rows = conn.execute("""
        SELECT p.id, p.nombre,
               COALESCE(pp.precio_vigente, pp.precio_ultimo, pp.precio_compra, pp.precio, p.precio, 1) AS precio
        FROM productos p
        JOIN productos_proveedores pp ON pp.producto_id = p.id
        WHERE pp.proveedor_id = ?
          AND COALESCE(p.activo,1)=1
          AND COALESCE(pp.activo,1)=1
        ORDER BY p.nombre
        LIMIT ?
    """, (proveedor_id, limite)).fetchall()
    return rows


def crear_pedido(conn):
    proveedor_id = buscar_id(conn, "proveedores", PROVEEDOR_NOMBRE)
    departamento_id = buscar_id(conn, "departamentos", DEPARTAMENTO_NOMBRE)
    if not proveedor_id:
        raise RuntimeError(f"No encuentro proveedor {PROVEEDOR_NOMBRE}")
    if not departamento_id:
        raise RuntimeError(f"No encuentro departamento {DEPARTAMENTO_NOMBRE}")

    productos = productos_con_proveedor(conn, proveedor_id, 3)
    if not productos:
        raise RuntimeError("No hay productos asociados a ese proveedor. Ejecuta sanear_compras y revisa productos_proveedores.")

    numero = numero_pedido(conn)
    fecha_entrega = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO pedidos (numero_pedido, proveedor_id, departamento_id, fecha_entrega, observaciones, creado_por, estado, empresa_id)
        VALUES (?, ?, ?, ?, ?, 'simulador', 'enviado', ?)
    """, (numero, proveedor_id, departamento_id, fecha_entrega, "Pedido creado por simulador de compras", EMPRESA_ID))
    pedido_id = cur.lastrowid

    cantidades = [20, 35, 10]
    for producto, cantidad in zip(productos, cantidades):
        cur.execute("""
            INSERT INTO lineas_pedido (pedido_id, producto_id, cantidad_solicitada, precio_unitario)
            VALUES (?, ?, ?, ?)
        """, (pedido_id, producto["id"], cantidad, float(producto["precio"] or 1)))

    conn.commit()
    return pedido_id, numero


def font(size, bold=False):
    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    return ImageFont.truetype(path, size)


def crear_albaran_jpg(conn, pedido_id):
    SALIDA_DIR.mkdir(exist_ok=True)
    pedido = conn.execute("""
        SELECT p.*, pr.nombre AS proveedor_nombre, d.nombre AS departamento_nombre
        FROM pedidos p
        JOIN proveedores pr ON pr.id=p.proveedor_id
        LEFT JOIN departamentos d ON d.id=p.departamento_id
        WHERE p.id=?
    """, (pedido_id,)).fetchone()
    lineas = conn.execute("""
        SELECT lp.*, prod.nombre AS producto_nombre
        FROM lineas_pedido lp
        JOIN productos prod ON prod.id=lp.producto_id
        WHERE lp.pedido_id=?
        ORDER BY lp.id
    """, (pedido_id,)).fetchall()

    W, H = 1400, 1800
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    y = 70
    d.text((80, y), "ALBARÁN DE ENTREGA", fill="black", font=font(48, True)); y += 80
    d.line((80, y, W-80, y), fill="black", width=3); y += 40
    d.text((80, y), f"Proveedor: {pedido['proveedor_nombre']}", fill="black", font=font(30, True)); y += 45
    d.text((80, y), f"Pedido asociado: {pedido['numero_pedido']}", fill="black", font=font(30, True)); y += 45
    d.text((80, y), f"Nº albarán: ALB-{datetime.now().strftime('%Y%m%d')}-{pedido_id:03d}", fill="black", font=font(28)); y += 45
    d.text((80, y), f"Departamento: {pedido['departamento_nombre'] or '-'}", fill="black", font=font(28)); y += 70

    d.rectangle((80, y, W-80, y+55), fill=(230,230,230), outline="black")
    headers = [("Producto", 110), ("Cant.", 720), ("Precio", 900), ("Total", 1080)]
    for h, x in headers:
        d.text((x, y+12), h, fill="black", font=font(24, True))
    y += 55

    total = 0
    for i, l in enumerate(lineas):
        cantidad = float(l["cantidad_solicitada"] or 0)
        # primer producto con fallo realista: entrega algo menos
        if i == 0 and cantidad > 1:
            cantidad -= 2
        precio = float(l["precio_unitario"] or 0)
        importe = cantidad * precio
        total += importe
        d.rectangle((80, y, W-80, y+55), outline="black")
        d.text((110, y+12), str(l["producto_nombre"])[:34].upper(), fill="black", font=font(24))
        d.text((720, y+12), str(cantidad).replace('.', ','), fill="black", font=font(24))
        d.text((900, y+12), f"{precio:.2f}".replace('.', ','), fill="black", font=font(24))
        d.text((1080, y+12), f"{importe:.2f}".replace('.', ','), fill="black", font=font(24))
        y += 55

    y += 45
    d.text((900, y), f"TOTAL ALBARÁN: {total:.2f} €".replace('.', ','), fill="black", font=font(30, True))
    y += 90
    d.text((80, y), "Observaciones: albarán generado por simulador. Revisar diferencias contra pedido.", fill="black", font=font(24))

    out = SALIDA_DIR / f"albaran_{pedido['numero_pedido']}.jpg"
    img.save(out, quality=95)
    return out


def main():
    print("=== SIMULADOR COMPRAS NEXORA ===")
    conn = conectar()
    try:
        pedido_id, numero = crear_pedido(conn)
        jpg = crear_albaran_jpg(conn, pedido_id)
        print(f"[OK] Pedido creado: {numero} (id {pedido_id})")
        print(f"[OK] Albarán JPG creado: {jpg}")
        print("Sube ese JPG en recepción directa o recepción de pedido para probar OCR.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

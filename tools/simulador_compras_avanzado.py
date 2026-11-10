# tools/simulador_compras_avanzado.py
# Simula ciclo real: proveedor, productos, pedido, albarán con diferencia y recepción preparada.
# Uso: python tools/simulador_compras_avanzado.py --db pepe4.db --crear

import argparse
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

PRODUCTOS = [
    ('ALBONDIGAS MIXTAS', 20, 3.45, 'congelado'),
    ('ALAS POLLO CONG', 35, 3.25, 'congelado'),
    ('JUDIAS VERDES', 10, 2.45, 'refrigerado'),
]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='pepe4.db')
    ap.add_argument('--empresa-id', type=int, default=1)
    ap.add_argument('--crear', action='store_true')
    args = ap.parse_args()
    db = Path(args.db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if not args.crear:
        print('Usa --crear para insertar simulación.')
        return

    dep = cur.execute("SELECT id FROM departamentos WHERE COALESCE(activo,1)=1 LIMIT 1").fetchone()
    alm = cur.execute("SELECT id FROM almacenes WHERE COALESCE(activo,1)=1 LIMIT 1").fetchone()
    if not dep or not alm:
        raise SystemExit('Faltan departamentos o almacenes.')

    prov = cur.execute("SELECT id FROM proveedores WHERE lower(nombre)=lower('SIMULADOR PROVEEDOR') AND empresa_id=?", (args.empresa_id,)).fetchone()
    if prov:
        prov_id = prov['id']
    else:
        cur.execute("INSERT INTO proveedores (nombre, activo, empresa_id, email) VALUES ('SIMULADOR PROVEEDOR',1,?, 'simulador@nexora.local')", (args.empresa_id,))
        prov_id = cur.lastrowid

    producto_ids = []
    for nombre, cantidad, precio, conservacion in PRODUCTOS:
        p = cur.execute('SELECT id FROM productos WHERE lower(nombre)=lower(?) AND empresa_id=? LIMIT 1', (nombre, args.empresa_id)).fetchone()
        if p:
            pid = p['id']
        else:
            cur.execute('INSERT INTO productos (nombre, codigo_interno, activo, empresa_id, precio, tipo_conservacion, almacen_preferente_id) VALUES (?, ?, 1, ?, ?, ?, ?)',
                        (nombre, 'SIM-' + str(len(producto_ids)+1).zfill(4), args.empresa_id, precio, conservacion, alm['id']))
            pid = cur.lastrowid
        producto_ids.append((pid, cantidad, precio))
        pp = cur.execute('SELECT id FROM productos_proveedores WHERE producto_id=? AND proveedor_id=?', (pid, prov_id)).fetchone()
        if not pp:
            cur.execute('INSERT INTO productos_proveedores (producto_id, proveedor_id, codigo_proveedor, precio, precio_compra, precio_ultimo, precio_vigente, es_principal, activo) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)',
                        (pid, prov_id, f'SIMPROV-{pid}', precio, precio, precio, precio))

    fecha = datetime.now().strftime('%Y%m%d')
    total_hoy = cur.execute("SELECT COUNT(*) AS total FROM pedidos WHERE fecha_pedido=date('now') AND empresa_id=?", (args.empresa_id,)).fetchone()['total'] + 1
    numero = f'PED-{fecha}-{total_hoy:03d}'
    cur.execute('INSERT INTO pedidos (numero_pedido, proveedor_id, departamento_id, fecha_entrega, observaciones, creado_por, estado, empresa_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (numero, prov_id, dep['id'], (datetime.now()+timedelta(days=1)).strftime('%Y-%m-%d'), 'Pedido de simulación avanzada', 'simulador', 'enviado', args.empresa_id))
    pedido_id = cur.lastrowid
    for pid, cantidad, precio in producto_ids:
        cur.execute('INSERT INTO lineas_pedido (pedido_id, producto_id, cantidad_solicitada, precio_unitario) VALUES (?, ?, ?, ?)',
                    (pedido_id, pid, cantidad, precio))

    conn.commit()
    conn.close()
    print(f'[OK] Pedido simulación creado: {numero}')
    print('Genera o sube un albarán con una cantidad diferente para probar recepción parcial y ajuste.')

if __name__ == '__main__':
    main()

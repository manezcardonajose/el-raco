# tools/compras_inteligente.py
# Saneamiento real de compras para Nexora Kitchen.
# Uso:
#   python tools/compras_inteligente.py --db pepe4.db --diagnostico
#   python tools/compras_inteligente.py --db pepe4.db --aplicar
#   python tools/compras_inteligente.py --db pepe4.db --aplicar --crear-pendiente

import argparse
import re
import sqlite3
from pathlib import Path
from datetime import datetime

AREA_REGLAS = [
    ('CONG', ['cong', 'congel', 'ultracong', 'helado']),
    ('CAR', ['pollo', 'cerdo', 'ternera', 'vacuno', 'cordero', 'carne', 'hamburg', 'albond', 'salchicha', 'chorizo']),
    ('PES', ['pescado', 'merluza', 'atun', 'atún', 'bacalao', 'calamar', 'gamba', 'langost', 'mejillon', 'mejillón', 'almeja']),
    ('VER', ['judia', 'judía', 'cebolla', 'tomate', 'lechuga', 'calabacin', 'calabacín', 'pimiento', 'zanahoria', 'apio', 'col ', 'berenjena', 'pepino']),
    ('FRU', ['manzana', 'pera', 'platano', 'plátano', 'melon', 'melón', 'sandia', 'sandía', 'naranja', 'limon', 'limón']),
    ('BEB', ['vino', 'agua', 'refresco', 'cerveza', 'zumo', 'bebida']),
    ('LAC', ['leche', 'queso', 'yogur', 'nata', 'mantequilla']),
    ('SEC', ['arroz', 'pasta', 'harina', 'azucar', 'azúcar', 'sal ', 'aceite', 'vinagre', 'conserva', 'lata']),
    ('LIM', ['lejia', 'lejía', 'detergente', 'limpieza', 'bayeta', 'papel', 'guante']),
    ('MAN', ['tutor', 'acero', 'tornillo', 'bombilla', 'ferreter', 'cable', 'pila']),
]

ALMACEN_REGLAS = [
    ('CONG', ['congel']),
    ('CAR', ['carne', 'carnes']),
    ('PES', ['pescado', 'pescados']),
    ('VER', ['fruta', 'verdura', 'frutas', 'verduras']),
    ('FRU', ['fruta', 'verdura', 'frutas', 'verduras']),
    ('BEB', ['economato', 'bebida', 'bar']),
    ('LAC', ['frio', 'frío', 'lacteo', 'lácteo']),
    ('SEC', ['economato', 'seco', 'almacen seco']),
    ('LIM', ['limpieza']),
    ('MAN', ['mantenimiento', 'economato']),
]

def rowdict(row):
    return dict(row) if row else None

def normalizar(txt):
    txt = (txt or '').lower()
    rep = str.maketrans('áéíóúüñ', 'aeiouun')
    txt = txt.translate(rep)
    txt = re.sub(r'[^a-z0-9 ]+', ' ', txt)
    return re.sub(r'\s+', ' ', txt).strip()

def area_producto(nombre):
    n = normalizar(nombre)
    for area, claves in AREA_REGLAS:
        if any(normalizar(c) in n for c in claves):
            return area
    return 'GEN'

def codigo_producto(area, product_id):
    return f'{area}-{int(product_id):05d}'

def asegurar_columnas(cur):
    def cols(tabla):
        return [r[1] for r in cur.execute(f'PRAGMA table_info({tabla})').fetchall()]
    cprod = cols('productos')
    if 'codigo_interno' not in cprod:
        cur.execute('ALTER TABLE productos ADD COLUMN codigo_interno TEXT')
    if 'tipo_conservacion' not in cprod:
        cur.execute('ALTER TABLE productos ADD COLUMN tipo_conservacion TEXT')
    if 'almacen_preferente_id' not in cprod:
        cur.execute('ALTER TABLE productos ADD COLUMN almacen_preferente_id INTEGER')
    cur.execute('''CREATE TABLE IF NOT EXISTS productos_proveedores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        producto_id INTEGER,
        proveedor_id INTEGER,
        codigo_proveedor TEXT,
        precio REAL,
        precio_compra REAL,
        precio_ultimo REAL,
        precio_vigente REAL,
        es_principal INTEGER DEFAULT 0,
        activo INTEGER DEFAULT 1,
        fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

def proveedor_pendiente(cur, empresa_id):
    row = cur.execute("SELECT id FROM proveedores WHERE empresa_id=? AND lower(nombre)=lower('PENDIENTE COMPRAS') LIMIT 1", (empresa_id,)).fetchone()
    if row:
        return row['id']
    cur.execute("INSERT INTO proveedores (nombre, activo, empresa_id) VALUES ('PENDIENTE COMPRAS', 1, ?)", (empresa_id,))
    return cur.lastrowid

def proveedor_por_area(cur, empresa_id, area):
    patrones = {
        'VER': ['fruta', 'verdura', 'brunell'],
        'FRU': ['fruta', 'verdura', 'brunell'],
        'CAR': ['carne', 'carnic'],
        'PES': ['pesc', 'mar'],
        'CONG': ['cong'],
        'BEB': ['beb', 'bar', 'vino'],
        'MAN': ['ferreter', 'marcial'],
    }.get(area, [])
    proveedores = cur.execute('SELECT id, nombre FROM proveedores WHERE COALESCE(activo,1)=1 AND empresa_id=? ORDER BY nombre', (empresa_id,)).fetchall()
    for p in proveedores:
        n = normalizar(p['nombre'])
        if any(normalizar(x) in n for x in patrones):
            return p['id']
    return None

def almacen_por_area(cur, empresa_id, area):
    almacenes = cur.execute('SELECT id, nombre FROM almacenes WHERE COALESCE(activo,1)=1 AND empresa_id=? ORDER BY nombre', (empresa_id,)).fetchall()
    reglas = dict(ALMACEN_REGLAS).get(area, [])
    for a in almacenes:
        n = normalizar(a['nombre'])
        if any(normalizar(x) in n for x in reglas):
            return a['id']
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='pepe4.db')
    ap.add_argument('--empresa-id', type=int, default=1)
    ap.add_argument('--diagnostico', action='store_true')
    ap.add_argument('--aplicar', action='store_true')
    ap.add_argument('--crear-pendiente', action='store_true', help='Crea enlace a proveedor PENDIENTE COMPRAS para productos sin proveedor claro')
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f'No existe BD: {db}')

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    asegurar_columnas(cur)

    productos = cur.execute('SELECT id, nombre, codigo, codigo_interno, tipo_conservacion, almacen_preferente_id FROM productos WHERE COALESCE(activo,1)=1 AND empresa_id=? ORDER BY nombre', (args.empresa_id,)).fetchall()
    sin_codigo = []
    sin_proveedor = []
    cambios = []

    for p in productos:
        area = area_producto(p['nombre'])
        cod = p['codigo_interno'] or p['codigo']
        if not cod:
            sin_codigo.append((p['id'], p['nombre'], codigo_producto(area, p['id'])))
        pp = cur.execute('SELECT COUNT(*) AS total FROM productos_proveedores WHERE producto_id=? AND COALESCE(activo,1)=1', (p['id'],)).fetchone()['total']
        if not pp:
            sin_proveedor.append((p['id'], p['nombre'], area))

    print('=== DIAGNÓSTICO COMPRAS INTELIGENTE ===')
    print(f'BD: {db}')
    print(f'Productos activos: {len(productos)}')
    print(f'Sin código: {len(sin_codigo)}')
    print(f'Sin proveedor activo: {len(sin_proveedor)}')
    print('Primeros sin proveedor:')
    for item in sin_proveedor[:30]:
        print(f'  - {item[0]}: {item[1]} [{item[2]}]')

    if not args.aplicar:
        conn.close()
        print('\nModo diagnóstico. Ejecuta con --aplicar para guardar cambios.')
        return

    for pid, nombre, cod in sin_codigo:
        cur.execute('UPDATE productos SET codigo_interno=? WHERE id=?', (cod, pid))
        cambios.append(f'codigo {pid}->{cod}')

    pendiente_id = None
    if args.crear_pendiente:
        pendiente_id = proveedor_pendiente(cur, args.empresa_id)

    for pid, nombre, area in sin_proveedor:
        prov = proveedor_por_area(cur, args.empresa_id, area)
        if not prov and pendiente_id:
            prov = pendiente_id
        if prov:
            cur.execute('''INSERT INTO productos_proveedores
                (producto_id, proveedor_id, codigo_proveedor, precio, precio_compra, precio_ultimo, precio_vigente, es_principal, activo, fecha_actualizacion)
                VALUES (?, ?, ?, NULL, NULL, NULL, NULL, 1, 1, ?)''',
                (pid, prov, codigo_producto(area, pid), datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            cambios.append(f'proveedor producto {pid}->{prov}')

    for p in productos:
        area = area_producto(p['nombre'])
        almacen = almacen_por_area(cur, args.empresa_id, area)
        conservacion = 'congelado' if area == 'CONG' else ('refrigerado' if area in ('CAR','PES','VER','FRU','LAC') else 'ambiente')
        sets = []
        vals = []
        if not p['tipo_conservacion']:
            sets.append('tipo_conservacion=?')
            vals.append(conservacion)
        if not p['almacen_preferente_id'] and almacen:
            sets.append('almacen_preferente_id=?')
            vals.append(almacen)
        if sets:
            vals.append(p['id'])
            cur.execute(f"UPDATE productos SET {', '.join(sets)} WHERE id=?", vals)

    cur.execute('''UPDATE productos_proveedores SET es_principal=0
                   WHERE producto_id IN (SELECT producto_id FROM productos_proveedores GROUP BY producto_id HAVING COUNT(*) > 1)''')
    cur.execute('''UPDATE productos_proveedores SET es_principal=1
                   WHERE id IN (SELECT MIN(id) FROM productos_proveedores WHERE COALESCE(activo,1)=1 GROUP BY producto_id)''')

    conn.commit()
    conn.close()
    print(f'[OK] Cambios guardados: {len(cambios)}')
    if args.crear_pendiente:
        print('[AVISO] Los productos sin proveedor claro quedan enlazados a PENDIENTE COMPRAS para revisión posterior.')

if __name__ == '__main__':
    main()

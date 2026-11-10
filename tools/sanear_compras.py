# tools/sanear_compras.py
# Saneamiento defensivo de compras/productos para Nexora Kitchen.
# No borra datos. Rellena códigos internos y genera avisos de productos sin proveedor.

import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).resolve().parents[1] / "pepe4.db"
DRY_RUN = False  # pon True para simular sin guardar


def conectar():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def columnas(conn, tabla):
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({tabla})").fetchall()]
    except Exception:
        return []


def existe_tabla(conn, tabla):
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabla,)).fetchone()
    return row is not None


def add_col(conn, tabla, definicion):
    nombre = definicion.strip().split()[0]
    if nombre not in columnas(conn, tabla):
        conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {definicion}")
        print(f"[OK] Columna creada: {tabla}.{nombre}")


def asegurar_estructura(conn):
    if not existe_tabla(conn, "productos"):
        raise RuntimeError("No existe la tabla productos")
    add_col(conn, "productos", "codigo_interno TEXT")
    add_col(conn, "productos", "visible_en_compras INTEGER DEFAULT 1")
    add_col(conn, "productos", "permite_compra_directa INTEGER DEFAULT 1")

    if existe_tabla(conn, "productos_proveedores"):
        add_col(conn, "productos_proveedores", "activo INTEGER DEFAULT 1")
        if "codigo_proveedor" not in columnas(conn, "productos_proveedores"):
            add_col(conn, "productos_proveedores", "codigo_proveedor TEXT")
        if "es_principal" not in columnas(conn, "productos_proveedores"):
            add_col(conn, "productos_proveedores", "es_principal INTEGER DEFAULT 0")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS auditoria_saneamiento_compras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            tipo TEXT,
            referencia TEXT,
            mensaje TEXT
        )
    """)


def normalizar_area(nombre):
    nombre = (nombre or "").lower()
    if any(x in nombre for x in ["vino", "cerveza", "refresco", "agua", "licor"]):
        return "BEB"
    if any(x in nombre for x in ["deterg", "lejia", "limpia", "bayeta", "jab"]):
        return "LIM"
    if any(x in nombre for x in ["tornillo", "tutor", "acero", "plast", "cable", "bombilla"]):
        return "MAN"
    return "ALI"


def generar_codigo(nombre, producto_id):
    area = normalizar_area(nombre)
    return f"{area}-{int(producto_id):06d}"


def sanear_codigos(conn):
    cols = columnas(conn, "productos")
    codigo_col = "codigo" if "codigo" in cols else None
    codigo_interno_col = "codigo_interno" if "codigo_interno" in cols else None

    productos = conn.execute("SELECT id, nombre, codigo_interno{} FROM productos ORDER BY id".format(", codigo" if codigo_col else "")).fetchall()
    actualizados = 0

    for p in productos:
        codigo_actual = (p["codigo_interno"] if codigo_interno_col else None) or (p["codigo"] if codigo_col else None)
        if codigo_actual and str(codigo_actual).strip():
            continue

        nuevo = generar_codigo(p["nombre"], p["id"])
        if codigo_interno_col:
            conn.execute("UPDATE productos SET codigo_interno=? WHERE id=?", (nuevo, p["id"]))
        elif codigo_col:
            conn.execute("UPDATE productos SET codigo=? WHERE id=?", (nuevo, p["id"]))
        actualizados += 1
        conn.execute("INSERT INTO auditoria_saneamiento_compras (tipo, referencia, mensaje) VALUES (?, ?, ?)", (
            "codigo_producto", str(p["id"]), f"Asignado código {nuevo} a {p['nombre']}"
        ))

    print(f"[OK] Productos con código nuevo: {actualizados}")


def revisar_proveedores(conn):
    if not existe_tabla(conn, "productos_proveedores"):
        print("[AVISO] No existe productos_proveedores; no puedo enlazar proveedores.")
        return

    productos_sin = conn.execute("""
        SELECT p.id, p.nombre
        FROM productos p
        WHERE COALESCE(p.activo, 1) = 1
          AND NOT EXISTS (
              SELECT 1 FROM productos_proveedores pp
              WHERE pp.producto_id = p.id
                AND COALESCE(pp.activo, 1) = 1
          )
        ORDER BY p.nombre
    """).fetchall()

    for p in productos_sin:
        conn.execute("INSERT INTO auditoria_saneamiento_compras (tipo, referencia, mensaje) VALUES (?, ?, ?)", (
            "producto_sin_proveedor", str(p["id"]), f"Producto sin proveedor activo: {p['nombre']}"
        ))

    print(f"[AVISO] Productos sin proveedor activo: {len(productos_sin)}")
    if productos_sin[:20]:
        print("Primeros productos sin proveedor:")
        for p in productos_sin[:20]:
            print(f"  - {p['id']}: {p['nombre']}")


def marcar_principal_si_unico(conn):
    if not existe_tabla(conn, "productos_proveedores"):
        return
    cols = columnas(conn, "productos_proveedores")
    if "es_principal" not in cols:
        return

    rows = conn.execute("""
        SELECT producto_id, COUNT(*) AS total, MAX(id) AS pp_id
        FROM productos_proveedores
        WHERE COALESCE(activo, 1) = 1
        GROUP BY producto_id
        HAVING total = 1
    """).fetchall()

    total = 0
    for r in rows:
        conn.execute("UPDATE productos_proveedores SET es_principal=1 WHERE id=? AND COALESCE(es_principal,0)=0", (r["pp_id"],))
        total += conn.total_changes
    print(f"[OK] Proveedores únicos marcados como principal: {len(rows)}")


def main():
    print("=== SANEAMIENTO COMPRAS NEXORA ===")
    print(f"BD: {DB_PATH}")
    conn = conectar()
    try:
        asegurar_estructura(conn)
        sanear_codigos(conn)
        revisar_proveedores(conn)
        marcar_principal_si_unico(conn)
        if DRY_RUN:
            conn.rollback()
            print("[DRY RUN] Cambios descartados")
        else:
            conn.commit()
            print("[OK] Cambios guardados")
    except Exception as e:
        conn.rollback()
        print(f"[ERROR] {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()

import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).resolve().parents[1] / "pepe4.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def column_exists(conn, table, column):
    return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})").fetchall())


def add_column(conn, table, ddl):
    column = ddl.strip().split()[0]
    if not column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def ensure_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventario_articulos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT,
            nombre TEXT NOT NULL,
            familia TEXT DEFAULT 'otros',
            categoria TEXT,
            unidad TEXT DEFAULT 'ud',
            descripcion TEXT,
            stock_minimo REAL DEFAULT 0,
            valor_unitario REAL DEFAULT 0,
            activo INTEGER DEFAULT 1,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventario_existencias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            articulo_id INTEGER NOT NULL,
            cantidad REAL DEFAULT 0,
            estado TEXT DEFAULT 'operativo',
            ubicacion TEXT,
            departamento_id INTEGER,
            subdepartamento_id INTEGER,
            punto_venta_id INTEGER,
            almacen_id INTEGER,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for ddl in [
        "uso_operativo TEXT DEFAULT 'otros'",
        "material TEXT",
        "formato TEXT",
        "capacidad TEXT",
        "compatible_appcc INTEGER DEFAULT 1",
        "apto_caliente INTEGER DEFAULT 0",
        "apto_frio INTEGER DEFAULT 0",
        "apto_horno INTEGER DEFAULT 0",
        "apto_lavavajillas INTEGER DEFAULT 1",
        "rompible INTEGER DEFAULT 0",
        "requiere_reposicion_buffet INTEGER DEFAULT 0",
    ]:
        add_column(conn, "inventario_articulos", ddl)
    add_column(conn, "inventario_existencias", "centro_id INTEGER")


def almacen_id(conn, nombre_like):
    row = conn.execute(
        """
        SELECT id
        FROM almacenes
        WHERE empresa_id = ? AND centro_id = ? AND LOWER(nombre) LIKE LOWER(?)
        ORDER BY id
        LIMIT 1
        """,
        (EMPRESA_ID, CENTRO_ID, f"%{nombre_like}%"),
    ).fetchone()
    return row["id"] if row else None


def item(codigo, nombre, familia, categoria, uso, cantidad, ubicacion, almacen, material="", formato="", capacidad="", minimo=0, valor=0, caliente=0, frio=0, horno=0, lavavajillas=1, rompible=0, reposicion=0):
    return {
        "codigo": codigo,
        "nombre": nombre,
        "familia": familia,
        "categoria": categoria,
        "uso_operativo": uso,
        "cantidad": cantidad,
        "ubicacion": ubicacion,
        "almacen": almacen,
        "material": material,
        "formato": formato,
        "capacidad": capacidad,
        "stock_minimo": minimo,
        "valor_unitario": valor,
        "compatible_appcc": 1,
        "apto_caliente": caliente,
        "apto_frio": frio,
        "apto_horno": horno,
        "apto_lavavajillas": lavavajillas,
        "rompible": rompible,
        "requiere_reposicion_buffet": reposicion,
    }


def upsert_articulo(conn, data):
    row = conn.execute(
        """
        SELECT id
        FROM inventario_articulos
        WHERE empresa_id = ? AND centro_id = ? AND codigo = ?
        """,
        (EMPRESA_ID, CENTRO_ID, data["codigo"]),
    ).fetchone()
    values = (
        data["codigo"], data["nombre"], data["familia"], data["categoria"], "ud",
        data["nombre"], data["stock_minimo"], data["valor_unitario"], data["uso_operativo"],
        data["material"], data["formato"], data["capacidad"], data["compatible_appcc"],
        data["apto_caliente"], data["apto_frio"], data["apto_horno"], data["apto_lavavajillas"],
        data["rompible"], data["requiere_reposicion_buffet"], EMPRESA_ID, CENTRO_ID,
    )
    if row:
        articulo_id = row["id"]
        conn.execute(
            """
            UPDATE inventario_articulos
            SET nombre=?, familia=?, categoria=?, unidad=?, descripcion=?, stock_minimo=?,
                valor_unitario=?, uso_operativo=?, material=?, formato=?, capacidad=?,
                compatible_appcc=?, apto_caliente=?, apto_frio=?, apto_horno=?,
                apto_lavavajillas=?, rompible=?, requiere_reposicion_buffet=?, activo=1
            WHERE id=? AND empresa_id=?
            """,
            values[1:19] + (articulo_id, EMPRESA_ID),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO inventario_articulos
            (codigo, nombre, familia, categoria, unidad, descripcion, stock_minimo,
             valor_unitario, uso_operativo, material, formato, capacidad,
             compatible_appcc, apto_caliente, apto_frio, apto_horno, apto_lavavajillas,
             rompible, requiere_reposicion_buffet, activo, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            values,
        )
        articulo_id = cur.lastrowid
    return articulo_id


def set_existencia(conn, articulo_id, data):
    almacen = data["almacen"]
    row = conn.execute(
        """
        SELECT id
        FROM inventario_existencias
        WHERE articulo_id = ? AND empresa_id = ? AND centro_id = ?
          AND COALESCE(almacen_id, 0) = COALESCE(?, 0)
          AND COALESCE(ubicacion, '') = COALESCE(?, '')
          AND COALESCE(estado, 'operativo') = 'operativo'
        """,
        (articulo_id, EMPRESA_ID, CENTRO_ID, almacen, data["ubicacion"]),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE inventario_existencias
            SET cantidad = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (data["cantidad"], row["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO inventario_existencias
            (articulo_id, cantidad, estado, ubicacion, almacen_id, empresa_id, centro_id, updated_at)
            VALUES (?, ?, 'operativo', ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (articulo_id, data["cantidad"], data["ubicacion"], almacen, EMPRESA_ID, CENTRO_ID),
        )


def sync_menaje_presentacion(conn, data):
    if data["uso_operativo"] not in {"servicio_buffet", "presentacion_buffet"}:
        return
    if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='menaje_presentacion'").fetchone():
        return
    row = conn.execute(
        """
        SELECT id FROM menaje_presentacion
        WHERE empresa_id=? AND centro_id=? AND LOWER(nombre)=LOWER(?)
        """,
        (EMPRESA_ID, CENTRO_ID, data["nombre"]),
    ).fetchone()
    tipo = "gn" if "GN" in data["formato"].upper() or "GN" in data["nombre"].upper() else "fuente"
    if row:
        conn.execute(
            """
            UPDATE menaje_presentacion
            SET tipo=?, estetica=?, color=?, compatible_caliente=?, compatible_frio=?,
                compatible_appcc=1, disponibilidad=1
            WHERE id=?
            """,
            (tipo, data["material"] or "operativo", data["material"] or "varios", data["apto_caliente"], data["apto_frio"], row["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO menaje_presentacion
            (centro_id, nombre, tipo, capacidad, estetica, altura, color, compatible_caliente,
             compatible_frio, compatible_appcc, disponibilidad, empresa_id)
            VALUES (?, ?, ?, 1, ?, 0, ?, ?, ?, 1, 1, ?)
            """,
            (CENTRO_ID, data["nombre"], tipo, data["material"] or "operativo", data["material"] or "varios", data["apto_caliente"], data["apto_frio"], EMPRESA_ID),
        )


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)

    alm_buffet = almacen_id(conn, "ECONOMATO BUFFET")
    alm_cocina = almacen_id(conn, "ECONOMATO COCINA")
    bajo_buffet = almacen_id(conn, "BAJO MOSTRADOR BUFFET")

    catalogo = [
        item("VAJ-PLL-27", "Plato llano buffet 27 cm", "vajilla", "Vajilla cliente", "cliente_buffet", 420, "Buffet comedor", alm_buffet, "porcelana", "27 cm", "", 280, 3.5, frio=1, lavavajillas=1, rompible=1, reposicion=1),
        item("VAJ-PPO-20", "Plato postre 20 cm", "vajilla", "Vajilla cliente", "cliente_buffet", 360, "Buffet comedor", alm_buffet, "porcelana", "20 cm", "", 240, 2.4, frio=1, lavavajillas=1, rompible=1, reposicion=1),
        item("VAJ-PHO-22", "Plato hondo sopa 22 cm", "vajilla", "Vajilla cliente", "cliente_buffet", 220, "Buffet comedor", alm_buffet, "porcelana", "22 cm", "", 160, 3.0, caliente=1, lavavajillas=1, rompible=1, reposicion=1),
        item("VAJ-BOL-CER", "Bowl desayuno cereales", "vajilla", "Vajilla cliente", "cliente_buffet", 180, "Buffet desayuno", alm_buffet, "porcelana", "15 cm", "", 120, 2.7, frio=1, lavavajillas=1, rompible=1, reposicion=1),
        item("VAJ-TAZ-CAF", "Taza cafe desayuno", "vajilla", "Vajilla cliente", "cliente_buffet", 260, "Buffet desayuno", alm_buffet, "porcelana", "180 ml", "180 ml", 180, 2.2, caliente=1, lavavajillas=1, rompible=1, reposicion=1),
        item("CRI-VAS-AGU", "Vaso agua buffet", "cristaleria", "Cristaleria cliente", "cliente_buffet", 350, "Buffet comedor", alm_buffet, "vidrio", "30 cl", "30 cl", 240, 1.2, frio=1, lavavajillas=1, rompible=1, reposicion=1),
        item("CRI-COP-VIN", "Copa vino buffet", "cristaleria", "Cristaleria cliente", "cliente_buffet", 220, "Buffet comedor", alm_buffet, "vidrio", "35 cl", "35 cl", 150, 1.9, frio=1, lavavajillas=1, rompible=1, reposicion=1),
        item("CUB-CUC-MES", "Cuchillo mesa buffet", "cuberteria", "Cuberteria cliente", "cliente_buffet", 420, "Buffet comedor", alm_buffet, "inox", "mesa", "", 280, 0.9, lavavajillas=1, reposicion=1),
        item("CUB-TEN-MES", "Tenedor mesa buffet", "cuberteria", "Cuberteria cliente", "cliente_buffet", 420, "Buffet comedor", alm_buffet, "inox", "mesa", "", 280, 0.8, lavavajillas=1, reposicion=1),
        item("CUB-CUS-SOP", "Cuchara sopera buffet", "cuberteria", "Cuberteria cliente", "cliente_buffet", 260, "Buffet comedor", alm_buffet, "inox", "sopera", "", 180, 0.8, lavavajillas=1, reposicion=1),
        item("CUB-CUC-CAF", "Cuchara cafe postre", "cuberteria", "Cuberteria cliente", "cliente_buffet", 300, "Buffet desayuno", alm_buffet, "inox", "postre/cafe", "", 200, 0.5, lavavajillas=1, reposicion=1),
        item("BUF-GN11-65", "Cubeta GN inox 1/1 65mm", "buffet", "Gastronorm buffet", "servicio_buffet", 40, "Pase buffet caliente/frio", alm_cocina, "inox", "GN 1/1 65mm", "9 L", 24, 16, caliente=1, frio=1, horno=1, lavavajillas=1, reposicion=1),
        item("BUF-GN11-100", "Cubeta GN inox 1/1 100mm", "buffet", "Gastronorm buffet", "servicio_buffet", 30, "Pase buffet caliente/frio", alm_cocina, "inox", "GN 1/1 100mm", "14 L", 18, 19, caliente=1, frio=1, horno=1, lavavajillas=1, reposicion=1),
        item("BUF-GN12-65", "Cubeta GN inox 1/2 65mm", "buffet", "Gastronorm buffet", "servicio_buffet", 35, "Pase buffet", alm_cocina, "inox", "GN 1/2 65mm", "4 L", 20, 10, caliente=1, frio=1, horno=1, lavavajillas=1, reposicion=1),
        item("BUF-GN12-100", "Cubeta GN inox 1/2 100mm", "buffet", "Gastronorm buffet", "servicio_buffet", 30, "Pase buffet", alm_cocina, "inox", "GN 1/2 100mm", "6 L", 18, 12, caliente=1, frio=1, horno=1, lavavajillas=1, reposicion=1),
        item("BUF-GN13-65", "Cubeta GN inox 1/3 65mm", "buffet", "Gastronorm buffet", "servicio_buffet", 25, "Salsas y guarniciones", alm_cocina, "inox", "GN 1/3 65mm", "2,5 L", 14, 8, caliente=1, frio=1, horno=1, lavavajillas=1, reposicion=1),
        item("BUF-TAPA-GN11", "Tapa inox GN 1/1", "buffet", "Gastronorm buffet", "servicio_buffet", 25, "Pase buffet", alm_cocina, "inox", "GN 1/1", "", 16, 7, caliente=1, frio=1, horno=1, lavavajillas=1),
        item("BUF-TAPA-GN12", "Tapa inox GN 1/2", "buffet", "Gastronorm buffet", "servicio_buffet", 25, "Pase buffet", alm_cocina, "inox", "GN 1/2", "", 16, 5, caliente=1, frio=1, horno=1, lavavajillas=1),
        item("BUF-BAN-MEL11", "Bandeja melamina blanca GN 1/1", "buffet", "Presentacion buffet", "presentacion_buffet", 20, "Linea fria buffet", bajo_buffet, "melamina", "GN 1/1", "", 12, 14, frio=1, lavavajillas=1, rompible=1, reposicion=1),
        item("BUF-PIZARRA11", "Pizarra negra presentacion GN 1/1", "buffet", "Presentacion buffet", "presentacion_buffet", 12, "Postres y frio", bajo_buffet, "pizarra", "GN 1/1", "", 8, 18, frio=1, lavavajillas=0, rompible=1),
        item("BUF-PINZA", "Pinza buffet servicio", "utillaje", "Servicio sala", "servicio_buffet", 40, "Buffet y reposicion", bajo_buffet, "inox", "pinza", "", 28, 3, caliente=1, frio=1, lavavajillas=1, reposicion=1),
        item("BUF-CUCHARON", "Cucharon servicio buffet", "utillaje", "Servicio sala", "servicio_buffet", 25, "Buffet caliente", bajo_buffet, "inox", "cucharon", "", 16, 4, caliente=1, lavavajillas=1, reposicion=1),
        item("BUF-PALA", "Pala servicio buffet", "utillaje", "Servicio sala", "servicio_buffet", 25, "Buffet caliente/frio", bajo_buffet, "inox", "pala", "", 16, 4, caliente=1, frio=1, lavavajillas=1, reposicion=1),
        item("BUF-CART-ALG", "Portacartel alergenos buffet", "buffet", "Carteleria APPCC", "servicio_buffet", 80, "Lineas buffet", bajo_buffet, "metacrilato", "A7", "", 50, 1.5, frio=1, lavavajillas=0, reposicion=1),
        item("COC-REJ-GN11", "Rejilla horno GN 1/1", "utillaje", "Horno", "cocina_utillaje", 30, "Cocina caliente", alm_cocina, "inox", "GN 1/1", "", 18, 12, caliente=1, frio=1, horno=1, lavavajillas=1),
        item("COC-BAN-HORNO11", "Bandeja horno GN 1/1", "utillaje", "Horno", "cocina_utillaje", 25, "Cocina caliente", alm_cocina, "inox", "GN 1/1", "", 15, 15, caliente=1, horno=1, lavavajillas=1),
        item("COC-SAR-24", "Sarten 24 cm", "utillaje", "Sartenes", "cocina_utillaje", 8, "Cocina caliente", alm_cocina, "inox/antiadherente", "24 cm", "", 4, 18, caliente=1, lavavajillas=1),
        item("COC-SAR-28", "Sarten 28 cm", "utillaje", "Sartenes", "cocina_utillaje", 8, "Cocina caliente", alm_cocina, "inox/antiadherente", "28 cm", "", 4, 24, caliente=1, lavavajillas=1),
        item("COC-SAR-32", "Sarten 32 cm", "utillaje", "Sartenes", "cocina_utillaje", 6, "Cocina caliente", alm_cocina, "inox/antiadherente", "32 cm", "", 3, 32, caliente=1, lavavajillas=1),
        item("COC-OLLA-20", "Olla 20 litros", "utillaje", "Ollas", "cocina_utillaje", 4, "Cocina caliente", alm_cocina, "inox", "20 L", "20 L", 2, 65, caliente=1, lavavajillas=1),
        item("COC-OLLA-30", "Olla 30 litros", "utillaje", "Ollas", "cocina_utillaje", 4, "Cocina caliente", alm_cocina, "inox", "30 L", "30 L", 2, 85, caliente=1, lavavajillas=1),
        item("COC-OLLA-50", "Olla 50 litros", "utillaje", "Ollas", "cocina_utillaje", 3, "Cocina caliente", alm_cocina, "inox", "50 L", "50 L", 1, 120, caliente=1, lavavajillas=1),
        item("COC-CAZO-5", "Cazo 5 litros", "utillaje", "Cazos", "cocina_utillaje", 5, "Cocina caliente", alm_cocina, "inox", "5 L", "5 L", 3, 28, caliente=1, lavavajillas=1),
        item("COC-CHINO", "Colador chino", "utillaje", "Coladores", "cocina_utillaje", 6, "Cocina caliente/cuarto frio", alm_cocina, "inox", "chino", "", 4, 18, frio=1, caliente=1, lavavajillas=1),
        item("COC-COL-MALLA", "Colador de malla", "utillaje", "Coladores", "cocina_utillaje", 8, "Cocina", alm_cocina, "inox", "malla", "", 5, 10, frio=1, caliente=1, lavavajillas=1),
        item("COC-ESPUM", "Espumadera inox", "utillaje", "Herramientas cocina", "cocina_utillaje", 8, "Cocina caliente", alm_cocina, "inox", "espumadera", "", 5, 7, caliente=1, lavavajillas=1),
        item("COC-VARILLA", "Varilla batidora manual", "utillaje", "Herramientas cocina", "cocina_utillaje", 8, "Cocina", alm_cocina, "inox", "varilla", "", 5, 6, frio=1, caliente=1, lavavajillas=1),
        item("COC-TABLAS-COLOR", "Set tablas corte colores APPCC", "utillaje", "APPCC cocina", "cocina_utillaje", 10, "Cocina y cuarto frio", alm_cocina, "polietileno", "colores APPCC", "set", 6, 35, frio=1, lavavajillas=1),
        item("COC-CUCH-CHEF", "Cuchillo chef", "utillaje", "Cuchilleria cocina", "cocina_utillaje", 12, "Cocina", alm_cocina, "inox", "chef", "", 8, 22, lavavajillas=0),
        item("COC-CUCH-PUNT", "Puntilla cocina", "utillaje", "Cuchilleria cocina", "cocina_utillaje", 12, "Cocina", alm_cocina, "inox", "puntilla", "", 8, 10, lavavajillas=0),
        item("COC-BOL-5", "Bol inox 5 litros", "utillaje", "Boles inox", "cocina_utillaje", 10, "Cocina/cuarto frio", alm_cocina, "inox", "5 L", "5 L", 6, 9, frio=1, lavavajillas=1),
        item("COC-BOL-10", "Bol inox 10 litros", "utillaje", "Boles inox", "cocina_utillaje", 8, "Cocina/cuarto frio", alm_cocina, "inox", "10 L", "10 L", 5, 14, frio=1, lavavajillas=1),
        item("COC-BOL-20", "Bol inox 20 litros", "utillaje", "Boles inox", "cocina_utillaje", 6, "Cocina/cuarto frio", alm_cocina, "inox", "20 L", "20 L", 3, 22, frio=1, lavavajillas=1),
    ]

    for data in catalogo:
        articulo_id = upsert_articulo(conn, data)
        set_existencia(conn, articulo_id, data)
        sync_menaje_presentacion(conn, data)

    conn.commit()
    total = conn.execute(
        "SELECT COUNT(*) AS total FROM inventario_articulos WHERE empresa_id=? AND centro_id=?",
        (EMPRESA_ID, CENTRO_ID),
    ).fetchone()["total"]
    print(f"Inventario Hotel 1 actualizado. Articulos del centro: {total}.")
    conn.close()


if __name__ == "__main__":
    main()

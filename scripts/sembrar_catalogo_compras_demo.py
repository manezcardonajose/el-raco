from __future__ import annotations

import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import DATABASE


EMPRESA_ID = 1


def norm(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text)
    return text


def table_cols(cur: sqlite3.Cursor, table: str) -> set[str]:
    return {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}


def add_col(cur: sqlite3.Cursor, table: str, definition: str) -> None:
    col = definition.strip().split()[0]
    if col not in table_cols(cur, table):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def ensure_schema(cur: sqlite3.Cursor) -> None:
    for definition in [
        "proveedor_id INTEGER",
        "departamento_responsable_id INTEGER",
        "codigo_interno TEXT",
        "tipo_producto TEXT",
        "unidad_base TEXT",
        "control_lote INTEGER DEFAULT 0",
        "control_caducidad INTEGER DEFAULT 0",
        "visible_en_compras INTEGER DEFAULT 1",
        "visible_en_produccion INTEGER DEFAULT 1",
        "visible_en_venta INTEGER DEFAULT 0",
        "permite_compra_directa INTEGER DEFAULT 1",
        "almacen_preferente_id INTEGER",
        "formato_venta TEXT",
        "precio_venta REAL DEFAULT 0",
    ]:
        add_col(cur, "productos", definition)

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS productos_proveedores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            proveedor_id INTEGER NOT NULL,
            precio REAL DEFAULT 0,
            es_principal INTEGER DEFAULT 0,
            fecha_actualizacion DATE DEFAULT CURRENT_DATE
        )
        """
    )
    for definition in [
        "codigo_proveedor TEXT",
        "referencia_proveedor TEXT",
        "precio_compra REAL",
        "precio_ultimo REAL",
        "precio_vigente REAL",
        "precio_medio REAL",
        "activo INTEGER DEFAULT 1",
        "preferente INTEGER DEFAULT 0",
    ]:
        add_col(cur, "productos_proveedores", definition)


def ensure_supplier(
    cur: sqlite3.Cursor,
    nombre: str,
    nif: str,
    email: str,
    telefono: str,
    direccion: str,
    contacto: str,
    observaciones: str,
) -> int:
    rows = cur.execute(
        "SELECT id, nombre FROM proveedores WHERE empresa_id = ?",
        (EMPRESA_ID,),
    ).fetchall()
    for row in rows:
        if norm(row["nombre"]) == norm(nombre):
            cur.execute(
                """
                UPDATE proveedores
                SET nombre = ?, nif = COALESCE(NULLIF(nif, ''), ?),
                    email = COALESCE(NULLIF(email, ''), ?),
                    telefono = COALESCE(NULLIF(telefono, ''), ?),
                    direccion = COALESCE(NULLIF(direccion, ''), ?),
                    contacto = COALESCE(NULLIF(contacto, ''), ?),
                    observaciones = ?,
                    activo = 1
                WHERE id = ?
                """,
                (nombre, nif, email, telefono, direccion, contacto, observaciones, row["id"]),
            )
            return int(row["id"])

    cur.execute(
        """
        INSERT INTO proveedores (
            nombre, nif, email, telefono, direccion, contacto,
            observaciones, activo, empresa_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (nombre, nif, email, telefono, direccion, contacto, observaciones, EMPRESA_ID),
    )
    return int(cur.lastrowid)


def ensure_almacen(
    cur: sqlite3.Cursor,
    nombre: str,
    tipo: str,
    ubicacion: str,
    departamento_id: int | None,
) -> int:
    row = cur.execute(
        """
        SELECT id FROM almacenes
        WHERE empresa_id = ? AND lower(nombre) = lower(?)
        LIMIT 1
        """,
        (EMPRESA_ID, nombre),
    ).fetchone()
    if row:
        return int(row["id"])
    cur.execute(
        """
        INSERT INTO almacenes (
            nombre, tipo, ubicacion, responsable, activo, empresa_id,
            departamento_id
        ) VALUES (?, ?, ?, ?, 1, ?, ?)
        """,
        (nombre, tipo, ubicacion, "Responsable departamento", EMPRESA_ID, departamento_id),
    )
    return int(cur.lastrowid)


SUPPLIERS = {
    "fruta_verdura": (
        "Frutas Brunell",
        "B76010001",
        "pedidos@frutasbrunell.test",
        "928100101",
        "Mercado central",
        "Departamento pedidos",
        "Fruta, verdura fresca y algunos productos refrigerados.",
    ),
    "carnes": (
        "Nexora Carnes Congeladas",
        "B76010002",
        "pedidos@carnesnexora.test",
        "928100102",
        "Poligono alimentario",
        "Comercial carnes",
        "Carnes congeladas y elaborados carnicos para hotel.",
    ),
    "pescados": (
        "Atlantico Pescados y Mariscos",
        "B76010003",
        "pedidos@atlanticopescados.test",
        "928100103",
        "Lonja insular",
        "Comercial pescado",
        "Pescado y marisco congelado.",
    ),
    "despensa": (
        "Despensa Hotelera Canarias",
        "B76010004",
        "pedidos@despensahotelera.test",
        "928100104",
        "Poligono logistica",
        "Ventas horeca",
        "Secos, conservas, aceites, salsas, legumbres, pasta y arroz.",
    ),
    "lacteos": (
        "Lacteos y Huevos Canarias",
        "B76010005",
        "pedidos@lacteoshuevos.test",
        "928100105",
        "Zona industrial norte",
        "Pedidos refrigerados",
        "Lacteos, huevos y quesos.",
    ),
    "bebidas": (
        "Bebidas Hotel Canarias",
        "B76010006",
        "pedidos@bebidashotel.test",
        "928100106",
        "Distribucion bebidas",
        "Comercial bebidas",
        "Agua, refrescos, cerveza, vinos y licores.",
    ),
    "limpieza": (
        "Limpieza Profesional Hotelera",
        "B76010007",
        "pedidos@limpiezahotelera.test",
        "928100107",
        "Poligono servicios",
        "Quimicos y utiles",
        "Productos de limpieza, desinfeccion y utiles APPCC.",
    ),
    "lavanderia": (
        "Lavanderia Industrial Atlantico",
        "B76010008",
        "pedidos@lavanderiaatlantico.test",
        "928100108",
        "Zona industrial lavanderia",
        "Gestion lavanderia",
        "Lenceria, detergentes y servicio externo de lavanderia.",
    ),
    "papeleria": (
        "Papeleria y Oficina Insular",
        "B76010009",
        "pedidos@papeleriainsular.test",
        "928100109",
        "Centro comercial empresarial",
        "Suministros oficina",
        "Papeleria, consumibles de impresora y material de recepcion.",
    ),
    "mantenimiento": (
        "Recambios y Mantenimiento Hotelero",
        "B76010010",
        "pedidos@recambioshotel.test",
        "928100110",
        "Poligono tecnico",
        "Recambios tecnicos",
        "Repuestos generales para mantenimiento preventivo y correctivo.",
    ),
    "amenities": (
        "Amenities y Desechables Hotel",
        "B76010011",
        "pedidos@amenitieshotel.test",
        "928100111",
        "Distribucion hotelera",
        "Amenities",
        "Amenities, desechables y material de habitacion.",
    ),
    "snacks": (
        "Helados y Snacks Canarias",
        "B76010012",
        "pedidos@heladossnacks.test",
        "928100112",
        "Distribucion frio y snacks",
        "Comercial snacks",
        "Helados, snacks, bolsas y productos de impulso.",
    ),
}


PRODUCTOS_DEMO = [
    # Limpieza
    ("Detergente multiusos profesional 5 l", "limpieza", "l", "seco", 8.40, 4, "limpieza", 12, 1, 1, 1, 0, "Garrafa 5 l"),
    ("Desengrasante cocina alimentario 5 l", "limpieza", "l", "seco", 11.20, 4, "limpieza", 12, 1, 1, 1, 0, "Garrafa 5 l"),
    ("Desinfectante banos 5 l", "limpieza", "l", "seco", 9.90, 4, "limpieza", 12, 1, 1, 1, 0, "Garrafa 5 l"),
    ("Lejia alimentaria 5 l", "limpieza", "l", "seco", 4.80, 6, "limpieza", 12, 1, 1, 1, 0, "Garrafa 5 l"),
    ("Lavavajillas manual 5 l", "limpieza", "l", "seco", 7.60, 4, "limpieza", 12, 1, 1, 1, 0, "Garrafa 5 l"),
    ("Abrillantador lavavajillas 5 l", "limpieza", "l", "seco", 14.50, 3, "limpieza", 12, 1, 1, 1, 0, "Garrafa 5 l"),
    ("Desincrustante cal 5 l", "limpieza", "l", "seco", 13.20, 3, "limpieza", 12, 1, 1, 1, 0, "Garrafa 5 l"),
    ("Limpiacristales profesional 5 l", "limpieza", "l", "seco", 6.90, 3, "limpieza", 12, 1, 1, 1, 0, "Garrafa 5 l"),
    ("Gel hidroalcoholico 5 l", "limpieza", "l", "seco", 18.00, 2, "limpieza", 12, 1, 1, 1, 0, "Garrafa 5 l"),
    ("Bayeta microfibra colores", "limpieza", "ud", "seco", 0.85, 60, "limpieza", 12, 0, 0, 1, 0, "Unidad"),
    ("Estropajo fibra verde", "limpieza", "ud", "seco", 0.28, 100, "limpieza", 12, 0, 0, 1, 0, "Unidad"),
    ("Guantes nitrilo caja 100 uds", "limpieza", "caja", "seco", 5.70, 10, "limpieza", 12, 1, 0, 1, 0, "Caja 100 uds"),
    ("Bolsa basura 30 l rollo", "limpieza", "rollo", "seco", 1.90, 20, "limpieza", 12, 0, 0, 1, 0, "Rollo"),
    ("Bolsa basura 100 l rollo", "limpieza", "rollo", "seco", 4.60, 15, "limpieza", 12, 0, 0, 1, 0, "Rollo"),
    ("Papel higienico industrial pack", "limpieza", "pack", "seco", 18.50, 6, "limpieza", 12, 1, 0, 1, 0, "Pack"),
    ("Papel secamanos zig zag caja", "limpieza", "caja", "seco", 22.00, 4, "limpieza", 12, 1, 0, 1, 0, "Caja"),
    # Lavanderia y lenceria
    ("Detergente lavadora profesional 20 l", "lavanderia", "l", "seco", 42.00, 2, "lavanderia", 12, 1, 1, 1, 0, "Bidon 20 l"),
    ("Suavizante textil profesional 20 l", "lavanderia", "l", "seco", 28.00, 2, "lavanderia", 12, 1, 1, 1, 0, "Bidon 20 l"),
    ("Blanqueante oxigenado 20 l", "lavanderia", "l", "seco", 36.00, 1, "lavanderia", 12, 1, 1, 1, 0, "Bidon 20 l"),
    ("Quitamanchas textil 5 l", "lavanderia", "l", "seco", 16.00, 2, "lavanderia", 12, 1, 1, 1, 0, "Garrafa 5 l"),
    ("Sabanas blancas 150 cama", "lavanderia", "ud", "seco", 9.50, 80, "lavanderia", 12, 1, 0, 1, 0, "Unidad"),
    ("Funda almohada blanca", "lavanderia", "ud", "seco", 2.10, 120, "lavanderia", 12, 1, 0, 1, 0, "Unidad"),
    ("Toalla bano blanca", "lavanderia", "ud", "seco", 4.20, 100, "lavanderia", 12, 1, 0, 1, 0, "Unidad"),
    ("Toalla mano blanca", "lavanderia", "ud", "seco", 2.35, 120, "lavanderia", 12, 1, 0, 1, 0, "Unidad"),
    ("Toalla piscina", "lavanderia", "ud", "seco", 6.50, 60, "lavanderia", 12, 1, 0, 1, 0, "Unidad"),
    ("Alfombrin bano", "lavanderia", "ud", "seco", 2.80, 80, "lavanderia", 12, 1, 0, 1, 0, "Unidad"),
    # Papeleria
    ("Folios A4 80g paquete 500", "papeleria", "paquete", "seco", 4.10, 20, "papeleria", 18, 0, 0, 1, 0, "Paquete"),
    ("Toner impresora recepcion negro", "papeleria", "ud", "seco", 58.00, 2, "papeleria", 9, 1, 0, 1, 0, "Unidad"),
    ("Tinta impresora color pack", "papeleria", "pack", "seco", 42.00, 2, "papeleria", 18, 1, 0, 1, 0, "Pack"),
    ("Boligrafo azul caja 50", "papeleria", "caja", "seco", 6.50, 3, "papeleria", 18, 0, 0, 1, 0, "Caja"),
    ("Grapas 24/6 caja", "papeleria", "caja", "seco", 0.90, 10, "papeleria", 18, 0, 0, 1, 0, "Caja"),
    ("Rollos papel termico TPV", "papeleria", "caja", "seco", 18.00, 6, "papeleria", 2, 1, 0, 1, 0, "Caja"),
    ("Etiquetas trazabilidad cocina", "papeleria", "rollo", "seco", 7.50, 10, "papeleria", 1, 1, 0, 1, 0, "Rollo"),
    ("Carpeta archivador A4", "papeleria", "ud", "seco", 1.80, 20, "papeleria", 18, 0, 0, 1, 0, "Unidad"),
    # Mantenimiento
    ("Bombilla LED E27 10W", "mantenimiento", "ud", "seco", 2.40, 30, "mantenimiento", 13, 1, 0, 1, 0, "Unidad"),
    ("Bombilla LED GU10 6W", "mantenimiento", "ud", "seco", 2.20, 30, "mantenimiento", 13, 1, 0, 1, 0, "Unidad"),
    ("Pilas AA pack 24", "mantenimiento", "pack", "seco", 8.50, 5, "mantenimiento", 13, 1, 0, 1, 0, "Pack"),
    ("Pilas AAA pack 24", "mantenimiento", "pack", "seco", 8.50, 5, "mantenimiento", 13, 1, 0, 1, 0, "Pack"),
    ("Filtro aire acondicionado split", "mantenimiento", "ud", "seco", 6.80, 15, "mantenimiento", 13, 1, 0, 1, 0, "Unidad"),
    ("Mando TV universal hotel", "mantenimiento", "ud", "seco", 9.90, 8, "mantenimiento", 13, 1, 0, 1, 0, "Unidad"),
    ("Latiguillo flexible lavabo", "mantenimiento", "ud", "seco", 3.20, 15, "mantenimiento", 13, 1, 0, 1, 0, "Unidad"),
    ("Sifon lavabo botella", "mantenimiento", "ud", "seco", 4.80, 10, "mantenimiento", 13, 1, 0, 1, 0, "Unidad"),
    ("Silicona sanitaria blanca", "mantenimiento", "ud", "seco", 3.70, 12, "mantenimiento", 13, 1, 0, 1, 0, "Unidad"),
    ("Cinta teflon rollo", "mantenimiento", "rollo", "seco", 0.55, 20, "mantenimiento", 13, 0, 0, 1, 0, "Rollo"),
    ("Aireador grifo M24", "mantenimiento", "ud", "seco", 1.25, 30, "mantenimiento", 13, 1, 0, 1, 0, "Unidad"),
    ("Pila cerradura electronica", "mantenimiento", "ud", "seco", 3.90, 20, "mantenimiento", 13, 1, 0, 1, 0, "Unidad"),
    # Amenities y habitacion
    ("Champu monodosis habitacion", "amenities", "ud", "seco", 0.09, 1000, "amenities", 12, 1, 1, 1, 0, "Unidad"),
    ("Gel ducha monodosis habitacion", "amenities", "ud", "seco", 0.09, 1000, "amenities", 12, 1, 1, 1, 0, "Unidad"),
    ("Pastilla jabon hotel", "amenities", "ud", "seco", 0.08, 1000, "amenities", 12, 1, 1, 1, 0, "Unidad"),
    ("Vaso carton habitacion", "amenities", "ud", "seco", 0.04, 1500, "amenities", 12, 1, 0, 1, 0, "Unidad"),
    ("Bolsa lavanderia cliente", "amenities", "ud", "seco", 0.06, 500, "amenities", 12, 1, 0, 1, 0, "Unidad"),
    # TPV y venta
    ("Agua mineral 0.5 l botella", "bebidas_agua", "botella", "seco", 0.22, 240, "venta", 2, 1, 1, 1, 1, "Botella 0.5 l", 1.20),
    ("Agua mineral 1.5 l botella", "bebidas_agua", "botella", "seco", 0.38, 120, "venta", 2, 1, 1, 1, 1, "Botella 1.5 l", 2.20),
    ("Refresco cola lata 330 ml", "bebidas_refrescos", "lata", "seco", 0.42, 240, "venta", 2, 1, 1, 1, 1, "Lata 330 ml", 2.00),
    ("Refresco naranja lata 330 ml", "bebidas_refrescos", "lata", "seco", 0.42, 180, "venta", 2, 1, 1, 1, 1, "Lata 330 ml", 2.00),
    ("Refresco limon lata 330 ml", "bebidas_refrescos", "lata", "seco", 0.42, 180, "venta", 2, 1, 1, 1, 1, "Lata 330 ml", 2.00),
    ("Cerveza botella 330 ml", "bebidas_cerveza", "botella", "seco", 0.55, 180, "venta", 2, 1, 1, 1, 1, "Botella 330 ml", 2.50),
    ("Cerveza barril 30 l", "bebidas_cerveza", "l", "seco", 2.15, 4, "venta", 2, 1, 1, 1, 1, "Barril 30 l", 2.20),
    ("Vino tinto casa botella", "bebidas_premium", "botella", "seco", 3.20, 36, "venta", 2, 1, 1, 1, 1, "Botella", 12.00),
    ("Vino blanco casa botella", "bebidas_premium", "botella", "seco", 3.10, 36, "venta", 2, 1, 1, 1, 1, "Botella", 12.00),
    ("Cava brut botella", "bebidas_premium", "botella", "seco", 4.90, 24, "venta", 2, 1, 1, 1, 1, "Botella", 18.00),
    ("Cafe grano hosteleria kg", "cafeteria", "kg", "seco", 8.90, 20, "venta", 2, 1, 1, 1, 1, "Kg", 1.60),
    ("Patatas fritas bolsa pequena", "snacks", "bolsa", "seco", 0.32, 100, "venta", 2, 1, 0, 1, 1, "Bolsa", 1.50),
    ("Frutos secos bolsa pequena", "snacks", "bolsa", "seco", 0.45, 80, "venta", 2, 1, 0, 1, 1, "Bolsa", 1.80),
    ("Helado cono vainilla chocolate", "helados", "ud", "congelado", 0.65, 80, "venta", 2, 1, 1, 1, 1, "Unidad", 2.50),
]


FAMILY_SUPPLIER = {
    "verduras": "fruta_verdura",
    "frutas": "fruta_verdura",
    "carnes": "carnes",
    "pescados": "pescados",
    "mariscos": "pescados",
    "lacteos": "lacteos",
    "huevos": "lacteos",
    "bebidas_agua": "bebidas",
    "bebidas_refrescos": "bebidas",
    "bebidas_cerveza": "bebidas",
    "bebidas_premium": "bebidas",
    "bebidas_licores": "bebidas",
    "cafeteria": "bebidas",
    "snacks": "snacks",
    "helados": "snacks",
    "limpieza": "limpieza",
    "lavanderia": "lavanderia",
    "papeleria": "papeleria",
    "mantenimiento": "mantenimiento",
    "amenities": "amenities",
}

FAMILY_DEFAULT_PRICE = {
    "verduras": 1.70,
    "frutas": 1.90,
    "carnes": 7.80,
    "pescados": 7.20,
    "mariscos": 9.50,
    "lacteos": 3.20,
    "huevos": 0.22,
    "legumbres": 1.40,
    "pastas_arroces": 1.20,
    "panaderia": 1.60,
    "aceites_salsas": 2.40,
    "especias": 4.50,
    "conservas": 1.80,
    "precocinados": 4.20,
    "frutos_secos": 5.90,
    "bebidas_agua": 0.35,
    "bebidas_refrescos": 0.55,
    "bebidas_cerveza": 0.80,
    "bebidas_premium": 4.50,
    "bebidas_licores": 8.00,
    "cafeteria": 7.50,
    "snacks": 0.50,
    "helados": 0.80,
    "buffet": 1.00,
    "otros": 2.00,
}

FAMILY_ALMACEN = {
    "carnes": "Congelador Carnes",
    "pescados": "Congelador Pescados",
    "mariscos": "Congelador Pescados",
    "verduras": "Camara de Frutas y Verdoras",
    "frutas": "Camara de Frutas y Verdoras",
    "lacteos": "Camara de lacteos, Embutidos, Quesos y huevos",
    "huevos": "Camara de lacteos, Embutidos, Quesos y huevos",
    "panaderia": "Congelador Pasteleria y Pan",
    "precocinados": "Congelados",
    "helados": "Congelados",
    "limpieza": "Almacen Limpieza y Quimicos",
    "lavanderia": "Almacen Lavanderia",
    "papeleria": "Almacen Papeleria y Oficina",
    "mantenimiento": "Almacen Mantenimiento",
    "amenities": "Almacen Pisos",
}


def supplier_key_for_family(familia: str) -> str:
    return FAMILY_SUPPLIER.get(familia, "despensa")


def default_price(familia: str, unidad: str) -> float:
    price = FAMILY_DEFAULT_PRICE.get(familia, 2.0)
    if unidad in {"lata", "botella", "bolsa", "ud"} and familia not in {"huevos"}:
        return min(price, 1.5) if familia not in {"bebidas_premium", "bebidas_licores"} else price
    return price


def code_prefix(familia: str, tipo: str) -> str:
    tipo_prefix = {
        "seco": "S",
        "refrigerado": "R",
        "congelado": "C",
    }.get(tipo, "X")
    fam = re.sub(r"[^A-Za-z0-9]", "", familia.upper())[:3] or "GEN"
    return f"{tipo_prefix}{fam}"


def ensure_product(
    cur: sqlite3.Cursor,
    nombre: str,
    familia: str,
    unidad: str,
    conservacion: str,
    precio: float,
    stock_minimo: float,
    tipo_producto: str,
    departamento_id: int,
    control_lote: int,
    control_caducidad: int,
    visible_compras: int,
    visible_venta: int,
    formato: str,
    precio_venta: float = 0.0,
) -> tuple[int, bool]:
    rows = cur.execute(
        "SELECT id, nombre, precio FROM productos WHERE empresa_id = ?",
        (EMPRESA_ID,),
    ).fetchall()
    producto_id = None
    for row in rows:
        if norm(row["nombre"]) == norm(nombre):
            producto_id = int(row["id"])
            break

    if producto_id is None:
        cur.execute(
            """
            INSERT INTO productos (
                nombre, cantidad, unidad, tipo_conservacion, familia, precio,
                stock_minimo, observaciones, activo, empresa_id,
                departamento_responsable_id, tipo_producto, unidad_base,
                control_lote, control_caducidad, visible_en_compras,
                visible_en_produccion, visible_en_venta,
                permite_compra_directa, formato_venta, precio_venta
            ) VALUES (?, 0, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                nombre,
                unidad,
                conservacion,
                familia,
                precio,
                stock_minimo,
                "Producto demo para pruebas de compras, stock y pedidos.",
                EMPRESA_ID,
                departamento_id,
                tipo_producto,
                unidad,
                control_lote,
                control_caducidad,
                visible_compras,
                1 if tipo_producto not in {"papeleria", "mantenimiento"} else 0,
                visible_venta,
                formato,
                precio_venta,
            ),
        )
        producto_id = int(cur.lastrowid)
        codigo = f"{code_prefix(familia, conservacion)}{producto_id:05d}"
        cur.execute(
            "UPDATE productos SET codigo = ?, codigo_interno = ? WHERE id = ?",
            (codigo, codigo, producto_id),
        )
        return producto_id, True

    cur.execute(
        """
        UPDATE productos
        SET familia = ?,
            unidad = ?,
            unidad_base = ?,
            tipo_conservacion = ?,
            precio = CASE WHEN COALESCE(precio, 0) <= 0 THEN ? ELSE precio END,
            stock_minimo = CASE WHEN COALESCE(stock_minimo, 0) <= 0 THEN ? ELSE stock_minimo END,
            departamento_responsable_id = COALESCE(departamento_responsable_id, ?),
            tipo_producto = COALESCE(tipo_producto, ?),
            control_lote = ?,
            control_caducidad = ?,
            visible_en_compras = ?,
            visible_en_venta = ?,
            permite_compra_directa = 1,
            formato_venta = COALESCE(formato_venta, ?),
            precio_venta = CASE WHEN COALESCE(precio_venta, 0) <= 0 THEN ? ELSE precio_venta END
        WHERE id = ?
        """,
        (
            familia,
            unidad,
            unidad,
            conservacion,
            precio,
            stock_minimo,
            departamento_id,
            tipo_producto,
            control_lote,
            control_caducidad,
            visible_compras,
            visible_venta,
            formato,
            precio_venta,
            producto_id,
        ),
    )
    return producto_id, False


def ensure_relation(
    cur: sqlite3.Cursor,
    producto_id: int,
    proveedor_id: int,
    precio: float,
    ref: str,
    principal: bool = True,
) -> None:
    if principal:
        cur.execute(
            """
            UPDATE productos_proveedores
            SET es_principal = 0, preferente = 0
            WHERE producto_id = ?
            """,
            (producto_id,),
        )

    row = cur.execute(
        """
        SELECT id FROM productos_proveedores
        WHERE producto_id = ? AND proveedor_id = ?
        LIMIT 1
        """,
        (producto_id, proveedor_id),
    ).fetchone()

    if row:
        cur.execute(
            """
            UPDATE productos_proveedores
            SET precio = CASE WHEN COALESCE(precio, 0) <= 0 THEN ? ELSE precio END,
                precio_compra = CASE WHEN COALESCE(precio_compra, 0) <= 0 THEN ? ELSE precio_compra END,
                precio_ultimo = CASE WHEN COALESCE(precio_ultimo, 0) <= 0 THEN ? ELSE precio_ultimo END,
                precio_vigente = CASE WHEN COALESCE(precio_vigente, 0) <= 0 THEN ? ELSE precio_vigente END,
                precio_medio = CASE WHEN COALESCE(precio_medio, 0) <= 0 THEN ? ELSE precio_medio END,
                codigo_proveedor = COALESCE(codigo_proveedor, ?),
                referencia_proveedor = COALESCE(referencia_proveedor, ?),
                es_principal = ?,
                preferente = ?,
                activo = 1,
                fecha_actualizacion = date('now')
            WHERE id = ?
            """,
            (precio, precio, precio, precio, precio, ref, ref, int(principal), int(principal), row["id"]),
        )
    else:
        cur.execute(
            """
            INSERT INTO productos_proveedores (
                producto_id, proveedor_id, codigo_proveedor, referencia_proveedor,
                precio, precio_compra, precio_ultimo, precio_vigente, precio_medio,
                es_principal, preferente, activo, fecha_actualizacion
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, date('now'))
            """,
            (
                producto_id,
                proveedor_id,
                ref,
                ref,
                precio,
                precio,
                precio,
                precio,
                precio,
                int(principal),
                int(principal),
            ),
        )

    cur.execute(
        """
        UPDATE productos
        SET proveedor_id = ?,
            precio = CASE WHEN COALESCE(precio, 0) <= 0 THEN ? ELSE precio END
        WHERE id = ?
        """,
        (proveedor_id, precio, producto_id),
    )


def main() -> None:
    conn = sqlite3.connect(ROOT / DATABASE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    ensure_schema(cur)

    proveedor_ids = {
        key: ensure_supplier(cur, *supplier_data)
        for key, supplier_data in SUPPLIERS.items()
    }

    almacen_ids = {
        "Almacen Limpieza y Quimicos": ensure_almacen(
            cur, "Almacen Limpieza y Quimicos", "limpieza", "Office pisos", 12
        ),
        "Almacen Lavanderia": ensure_almacen(
            cur, "Almacen Lavanderia", "lenceria", "Office pisos", 12
        ),
        "Almacen Papeleria y Oficina": ensure_almacen(
            cur, "Almacen Papeleria y Oficina", "oficina", "Administracion", 18
        ),
    }
    for row in cur.execute("SELECT id, nombre FROM almacenes WHERE empresa_id = ?", (EMPRESA_ID,)):
        almacen_ids.setdefault(row["nombre"], int(row["id"]))

    creados = 0
    for item in PRODUCTOS_DEMO:
        (
            nombre,
            familia,
            unidad,
            conservacion,
            precio,
            stock_minimo,
            tipo_producto,
            departamento_id,
            control_lote,
            control_caducidad,
            visible_compras,
            visible_venta,
            formato,
            *venta,
        ) = item
        precio_venta = float(venta[0]) if venta else 0.0
        producto_id, creado = ensure_product(
            cur,
            nombre,
            familia,
            unidad,
            conservacion,
            precio,
            stock_minimo,
            tipo_producto,
            departamento_id,
            control_lote,
            control_caducidad,
            visible_compras,
            visible_venta,
            formato,
            precio_venta,
        )
        creados += int(creado)
        proveedor_key = supplier_key_for_family(familia)
        ref = f"{proveedor_key[:3].upper()}-{producto_id:05d}"
        ensure_relation(cur, producto_id, proveedor_ids[proveedor_key], precio, ref)

    productos = cur.execute(
        """
        SELECT id, familia, unidad, tipo_conservacion, precio, proveedor_id
        FROM productos
        WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
        """,
        (EMPRESA_ID,),
    ).fetchall()

    for p in productos:
        familia = p["familia"] or "otros"
        unidad = p["unidad"] or "kg"
        proveedor_key = supplier_key_for_family(familia)
        proveedor_id = proveedor_ids[proveedor_key]
        precio = float(p["precio"] or 0)
        if precio <= 0:
            precio = default_price(familia, unidad)
        ref = f"{proveedor_key[:3].upper()}-{int(p['id']):05d}"
        ensure_relation(cur, int(p["id"]), proveedor_id, precio, ref)

        almacen_nombre = FAMILY_ALMACEN.get(familia, "Economato")
        almacen_id = almacen_ids.get(almacen_nombre)
        tipo_producto = {
            "limpieza": "limpieza",
            "lavanderia": "lavanderia",
            "papeleria": "papeleria",
            "mantenimiento": "mantenimiento",
            "amenities": "amenities",
        }.get(familia, "materia_prima")
        visible_venta = 1 if familia.startswith("bebidas_") or familia in {"snacks", "helados", "cafeteria"} else 0
        cur.execute(
            """
            UPDATE productos
            SET unidad_base = COALESCE(unidad_base, unidad),
                tipo_producto = COALESCE(tipo_producto, ?),
                control_lote = CASE
                    WHEN familia IN ('papeleria', 'mantenimiento') THEN control_lote
                    ELSE 1
                END,
                control_caducidad = CASE
                    WHEN tipo_conservacion IN ('refrigerado', 'congelado')
                         OR familia IN ('limpieza', 'lavanderia', 'amenities')
                    THEN 1 ELSE COALESCE(control_caducidad, 0)
                END,
                visible_en_compras = 1,
                visible_en_produccion = CASE
                    WHEN familia IN ('papeleria', 'mantenimiento') THEN COALESCE(visible_en_produccion, 0)
                    ELSE 1
                END,
                visible_en_venta = CASE WHEN ? = 1 THEN 1 ELSE COALESCE(visible_en_venta, 0) END,
                permite_compra_directa = 1,
                almacen_preferente_id = COALESCE(almacen_preferente_id, ?),
                codigo_interno = COALESCE(codigo_interno, codigo)
            WHERE id = ?
            """,
            (tipo_producto, visible_venta, almacen_id, int(p["id"])),
        )

    conn.commit()

    resumen = {
        "proveedores_activos": cur.execute(
            "SELECT COUNT(*) FROM proveedores WHERE empresa_id = ? AND activo = 1",
            (EMPRESA_ID,),
        ).fetchone()[0],
        "productos_total": cur.execute(
            "SELECT COUNT(*) FROM productos WHERE empresa_id = ? AND COALESCE(activo, 1) = 1",
            (EMPRESA_ID,),
        ).fetchone()[0],
        "productos_creados": creados,
        "sin_proveedor_id": cur.execute(
            """
            SELECT COUNT(*) FROM productos
            WHERE empresa_id = ? AND COALESCE(activo, 1) = 1
              AND (proveedor_id IS NULL OR proveedor_id = 0)
            """,
            (EMPRESA_ID,),
        ).fetchone()[0],
        "sin_relacion_proveedor": cur.execute(
            """
            SELECT COUNT(*) FROM productos p
            WHERE p.empresa_id = ? AND COALESCE(p.activo, 1) = 1
              AND NOT EXISTS (
                  SELECT 1 FROM productos_proveedores pp
                  WHERE pp.producto_id = p.id AND COALESCE(pp.activo, 1) = 1
              )
            """,
            (EMPRESA_ID,),
        ).fetchone()[0],
    }

    print("Sembrado catalogo compras demo")
    for key, value in resumen.items():
        print(f"{key}: {value}")
    print("Familias nuevas / no cocina:")
    for row in cur.execute(
        """
        SELECT familia, COUNT(*)
        FROM productos
        WHERE familia IN ('limpieza', 'lavanderia', 'papeleria', 'mantenimiento', 'amenities')
        GROUP BY familia
        ORDER BY familia
        """
    ):
        print(f"{row[0]}: {row[1]}")

    conn.close()


if __name__ == "__main__":
    main()

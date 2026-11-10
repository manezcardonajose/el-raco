# -*- coding: utf-8 -*-
"""Crea proveedores ficticios y precios aproximados para Hotel Albarracin."""

from __future__ import annotations

import argparse
import random
import re
import sqlite3
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "pepe4.db"
EMPRESA_ID = 2
CENTRO_ID = 8


PROVEEDORES = {
    "carnes": {
        "nombre": "Albarracin Carnes y Embutidos SL",
        "nif": "B44210001",
        "email": "pedidos.carnes@proveedores-demo.local",
        "telefono": "978000101",
        "contacto": "Marta Carniceria",
        "dias": [1, 3, 5],
        "hora": "08:30",
    },
    "pescados": {
        "nombre": "Pescados y Congelados Teruel SL",
        "nif": "B44210002",
        "email": "pedidos.pescado@proveedores-demo.local",
        "telefono": "978000102",
        "contacto": "Javier Pescados",
        "dias": [2, 5],
        "hora": "09:00",
    },
    "fruta_verdura": {
        "nombre": "Frutas y Verduras Jiloca SL",
        "nif": "B44210003",
        "email": "pedidos.fruta@proveedores-demo.local",
        "telefono": "978000103",
        "contacto": "Laura Huerta",
        "dias": [1, 3, 5],
        "hora": "07:30",
    },
    "lacteos_huevos": {
        "nombre": "Lacteos Sierra de Albarracin SL",
        "nif": "B44210004",
        "email": "pedidos.lacteos@proveedores-demo.local",
        "telefono": "978000104",
        "contacto": "Pilar Lacteos",
        "dias": [1, 4],
        "hora": "08:00",
    },
    "panaderia": {
        "nombre": "Panaderia Horno del Portal SL",
        "nif": "B44210005",
        "email": "pedidos.pan@proveedores-demo.local",
        "telefono": "978000105",
        "contacto": "Sergio Horno",
        "dias": [1, 2, 3, 4, 5, 6, 7],
        "hora": "06:30",
    },
    "bebidas": {
        "nombre": "Bebidas y Distribuciones Albarracin SL",
        "nif": "B44210006",
        "email": "pedidos.bebidas@proveedores-demo.local",
        "telefono": "978000106",
        "contacto": "Carlos Bebidas",
        "dias": [2, 5],
        "hora": "10:30",
    },
    "cafe_desayuno": {
        "nombre": "Cafes y Desayunos Mudéjar SL",
        "nif": "B44210007",
        "email": "pedidos.cafe@proveedores-demo.local",
        "telefono": "978000107",
        "contacto": "Nuria Cafe",
        "dias": [2, 5],
        "hora": "09:30",
    },
    "secos": {
        "nombre": "Economato Secos Aragon SL",
        "nif": "B44210008",
        "email": "pedidos.secos@proveedores-demo.local",
        "telefono": "978000108",
        "contacto": "Raul Economato",
        "dias": [2],
        "hora": "11:00",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sembrar proveedores y precios Albarracin.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=EMPRESA_ID)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def norm(text: str | None) -> str:
    text = str(text or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS importacion_albarracin_precios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            empresa_id INTEGER,
            centro_id INTEGER,
            productos_actualizados INTEGER DEFAULT 0,
            proveedores_creados INTEGER DEFAULT 0,
            observaciones TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def ensure_provider(conn: sqlite3.Connection, empresa_id: int, centro_id: int, key: str, data: dict) -> int:
    row = conn.execute(
        """
        SELECT id FROM proveedores
        WHERE empresa_id=? AND centro_id=? AND LOWER(nombre)=LOWER(?)
        """,
        (empresa_id, centro_id, data["nombre"]),
    ).fetchone()
    if row:
        provider_id = int(row["id"])
        conn.execute(
            """
            UPDATE proveedores
            SET nif=?, email=?, telefono=?, contacto=?, email_pedidos=?, telefono_pedidos=?,
                contacto_pedidos=?, documento_habitual='albaran', facturacion_habitual='mensual',
                forma_pago='transferencia', dias_pago=30, activo=1,
                observaciones=?
            WHERE id=?
            """,
            (
                data["nif"],
                data["email"],
                data["telefono"],
                data["contacto"],
                data["email"],
                data["telefono"],
                data["contacto"],
                f"Proveedor ficticio para simulacion/precios Albarracin. Familia {key}.",
                provider_id,
            ),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO proveedores
            (nombre, nif, email, telefono, contacto, observaciones, activo, empresa_id,
             centro_id, documento_habitual, facturacion_habitual, forma_pago, dias_pago,
             email_pedidos, contacto_pedidos, telefono_pedidos)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, 'albaran', 'mensual', 'transferencia', 30, ?, ?, ?)
            """,
            (
                data["nombre"],
                data["nif"],
                data["email"],
                data["telefono"],
                data["contacto"],
                f"Proveedor ficticio para simulacion/precios Albarracin. Familia {key}.",
                empresa_id,
                centro_id,
                data["email"],
                data["contacto"],
                data["telefono"],
            ),
        )
        provider_id = int(cur.lastrowid)

    for dia in data["dias"]:
        exists = conn.execute(
            "SELECT id FROM reglas_calendario_proveedores WHERE empresa_id=? AND proveedor_id=? AND tipo='entrega' AND frecuencia='semanal' AND dia_semana=?",
            (empresa_id, provider_id, dia),
        ).fetchone()
        if not exists:
            conn.execute(
                """
                INSERT INTO reglas_calendario_proveedores
                (proveedor_id, tipo, frecuencia, dia_semana, hora, empresa_id)
                VALUES (?, 'entrega', 'semanal', ?, ?, ?)
                """,
                (provider_id, dia, data["hora"], empresa_id),
            )
        cal = conn.execute(
            "SELECT id FROM proveedor_calendario WHERE proveedor_id=? AND tipo='entrega' AND dia_semana=?",
            (provider_id, dia),
        ).fetchone()
        if not cal:
            conn.execute(
                "INSERT INTO proveedor_calendario (proveedor_id, dia_semana, tipo, hora, activo) VALUES (?, ?, 'entrega', ?, 1)",
                (provider_id, dia, data["hora"]),
            )
    return provider_id


def classify(name: str, allergens: str = "") -> tuple[str, str, str, float]:
    n = norm(name)
    a = norm(allergens)
    if not n:
        return "secos", "otros", "seco", 0.0

    if any(x in n for x in ["bacalao", "salmon", "atun", "tilapia", "merluza", "fogonero", "sardina", "calamar", "sepia", "gambon", "gamba", "langost", "almeja", "mejillon", "marisco"]) or any(x in a for x in ["pescado", "crustaceos", "moluscos"]):
        return "pescados", "pescados_mariscos", "congelado", price_for(n, "pescados")
    if any(x in n for x in ["pollo", "pavo", "cerdo", "ternera", "ternasco", "cordero", "ciervo", "secreto", "lomo", "jamon", "chorizo", "salchich", "mortadela", "beicon", "longaniza", "carrillera", "osobuco", "jarrete", "chuleta", "carne", "albondiga", "hamburguesa", "morcilla", "cabecero"]):
        return "carnes", "carnes_embutidos", "refrigerado", price_for(n, "carnes")
    if any(x in n for x in ["leche", "queso", "yogur", "flan", "nata", "mantequilla", "huevo", "huevos"]) or any(x in a for x in ["leche", "huevo"]):
        return "lacteos_huevos", "lacteos_huevos", "refrigerado", price_for(n, "lacteos_huevos")
    if any(x in n for x in ["pan", "croisant", "croissant", "napolitana", "donut", "ensaimada", "bollo", "bolleria", "torrija", "trenza", "brown", "pudding", "macarron", "canelon", "tagliatelle", "harina", "pasta"]) or "gluten" in a:
        return "panaderia", "panaderia_bolleria_pasta", "seco", price_for(n, "panaderia")
    if any(x in n for x in ["vino", "cerveza", "brandy", "agua", "zumo", "refresco", "cafe", "cafes", "infusion", "chocolate"]):
        key = "cafe_desayuno" if any(x in n for x in ["cafe", "infusion", "chocolate"]) else "bebidas"
        return key, "bebidas", "seco", price_for(n, key)
    if any(x in n for x in ["manzana", "naranja", "kiwi", "pera", "platano", "mandarina", "melon", "sandia", "pina", "fruta"]):
        return "fruta_verdura", "fruta", "refrigerado", price_for(n, "fruta")
    if any(x in n for x in ["tomate", "cebolla", "ajo", "puerro", "zanahoria", "patata", "calabacin", "berenjena", "borraja", "alubia", "garbanzo", "lenteja", "brocoli", "verdura", "lechuga", "pimiento", "champi", "judia", "guisante", "apio", "acelga"]):
        return "fruta_verdura", "verdura_legumbre", "refrigerado", price_for(n, "verdura")
    if any(x in n for x in ["aceite", "azucar", "sal", "pimienta", "canela", "arroz", "miel", "mermelada", "nutela", "mostaza", "soja", "caldo", "vinagre", "salsa", "alioli", "mayonesa", "almendra"]):
        return "secos", "secos_economato", "seco", price_for(n, "secos")
    return "secos", "secos_economato", "seco", price_for(n, "generico")


def price_for(n: str, family: str) -> float:
    exact = {
        "aceite de oliva": 6.20,
        "aceite oliva": 6.20,
        "aove": 8.40,
        "aceite girasol": 1.85,
        "sal": 0.35,
        "azucar": 1.10,
        "arroz": 1.45,
        "harina": 0.85,
        "agua": 0.18,
        "cafes": 12.00,
        "cafe": 12.00,
        "leche semi": 0.92,
        "leche entera": 0.95,
        "leche soja": 1.45,
        "huevos": 2.60,
        "huevo": 2.60,
        "pan de molde": 2.20,
        "pan de hogaza": 2.80,
    }
    tokens = set(n.split())
    for key, price in exact.items():
        key_tokens = key.split()
        if (len(key_tokens) == 1 and key in tokens) or (len(key_tokens) > 1 and key in n):
            return price
    if family == "pescados":
        if any(x in n for x in ["gambon", "langost", "marisco"]):
            return 10.80
        if any(x in n for x in ["bacalao", "salmon"]):
            return 9.50
        if any(x in n for x in ["calamar", "sepia", "almeja"]):
            return 7.20
        return 6.40
    if family == "carnes":
        if any(x in n for x in ["ternasco", "cordero"]):
            return 12.50
        if any(x in n for x in ["ternera", "jarrete", "osobuco", "ciervo"]):
            return 9.80
        if any(x in n for x in ["jamon serrano", "embut", "chorizo", "salchichon"]):
            return 7.80
        if "pollo" in n:
            return 4.20
        if "pavo" in n:
            return 5.10
        return 6.10
    if family == "lacteos_huevos":
        if "queso" in n:
            return 7.40
        if "yogur" in n:
            return 1.65
        return 2.20
    if family == "panaderia":
        if any(x in n for x in ["croisant", "croissant", "napolitana", "donut", "ensaimada", "bollo"]):
            return 4.90
        if any(x in n for x in ["macarron", "pasta", "tagliatelle", "canelon"]):
            return 2.10
        return 2.80
    if family in {"fruta", "verdura"}:
        if any(x in n for x in ["kiwi", "pina", "melon", "sandia"]):
            return 2.40
        if any(x in n for x in ["borraja", "champi"]):
            return 3.10
        if any(x in n for x in ["patata", "zanahoria", "cebolla"]):
            return 0.95
        return 1.85
    if family == "bebidas":
        return 1.10
    if family == "cafe_desayuno":
        return 4.80
    if family == "secos":
        if "almendra" in n:
            return 8.50
        if any(x in n for x in ["miel", "nutela"]):
            return 4.20
        if any(x in n for x in ["caldo", "salsa"]):
            return 2.90
        return 1.75
    return 2.50


def upsert_product_provider(conn: sqlite3.Connection, product_id: int, provider_id: int, price: float, code: str) -> None:
    row = conn.execute(
        "SELECT id FROM productos_proveedores WHERE producto_id=? AND proveedor_id=?",
        (product_id, provider_id),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE productos_proveedores
            SET precio=?, precio_compra=?, precio_ultimo=?, precio_vigente=?, precio_medio=?,
                es_principal=1, preferente=1, activo=1, fecha_actualizacion=CURRENT_DATE,
                codigo_proveedor=?, referencia_proveedor=?
            WHERE id=?
            """,
            (price, price, price, price, price, code, code, row["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO productos_proveedores
            (producto_id, proveedor_id, precio, es_principal, fecha_actualizacion,
             codigo_proveedor, precio_compra, precio_ultimo, precio_vigente,
             precio_medio, activo, preferente, referencia_proveedor)
            VALUES (?, ?, ?, 1, CURRENT_DATE, ?, ?, ?, ?, ?, 1, 1, ?)
            """,
            (product_id, provider_id, price, code, price, price, price, price, code),
        )

    row2 = conn.execute(
        "SELECT id FROM producto_proveedor WHERE producto_id=? AND proveedor_id=?",
        (product_id, provider_id),
    ).fetchone()
    if row2:
        conn.execute(
            """
            UPDATE producto_proveedor
            SET codigo_proveedor=?, nombre_proveedor=(SELECT nombre FROM proveedores WHERE id=?),
                formato_compra='kg/unidad segun ficha', unidad_compra='kg',
                factor_conversion=1, precio_ultimo=?, precio_medio=?, precio_vigente=?,
                impuesto=10, preferente=1, activo=1
            WHERE id=?
            """,
            (code, provider_id, price, price, price, row2["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO producto_proveedor
            (producto_id, proveedor_id, codigo_proveedor, nombre_proveedor, formato_compra,
             unidad_compra, factor_conversion, precio_ultimo, precio_medio, precio_vigente,
             impuesto, preferente, activo)
            VALUES (?, ?, ?, (SELECT nombre FROM proveedores WHERE id=?),
                    'kg/unidad segun ficha', 'kg', 1, ?, ?, ?, 10, 1, 1)
            """,
            (product_id, provider_id, code, provider_id, price, price, price),
        )


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        providers = {
            key: ensure_provider(conn, args.empresa_id, args.centro_id, key, data)
            for key, data in PROVEEDORES.items()
        }
        updated = 0
        rng = random.Random(442008)
        for product in conn.execute(
            """
            SELECT id, nombre, alergenos FROM productos
            WHERE empresa_id=? AND centro_id=? AND COALESCE(activo,1)=1
            """,
            (args.empresa_id, args.centro_id),
        ).fetchall():
            name = product["nombre"] or ""
            if not name.strip():
                conn.execute("UPDATE productos SET activo=0, observaciones=COALESCE(observaciones,'') || ' | Desactivado por nombre vacio en carga Albarracin.' WHERE id=?", (product["id"],))
                continue
            key, familia, conservacion, base_price = classify(name, product["alergenos"] or "")
            if base_price <= 0:
                continue
            price = round(base_price * rng.uniform(0.94, 1.08), 2)
            provider_id = providers[key]
            code = f"ALB-{key[:3].upper()}-{int(product['id']):05d}"
            conn.execute(
                """
                UPDATE productos
                SET familia=?, tipo_conservacion=?, precio=?, proveedor_id=?,
                    referencia_proveedor=?, visible_en_compras=1,
                    observaciones=TRIM(COALESCE(observaciones,'') || ' | Precio aproximado demo Albarracin; revisar con tarifa real.')
                WHERE id=?
                """,
                (familia, conservacion, price, provider_id, code, product["id"]),
            )
            upsert_product_provider(conn, int(product["id"]), provider_id, price, code)
            conn.execute(
                """
                INSERT INTO compras_historico_precios
                (empresa_id, producto_id, proveedor_id, precio_anterior, precio_nuevo,
                 origen, referencia_tipo, usuario)
                VALUES (?, ?, ?, 0, ?, 'seed_albarracin_precios', 'precio_aproximado', 'codex')
                """,
                (args.empresa_id, product["id"], provider_id, price),
            )
            updated += 1
        conn.execute(
            """
            INSERT INTO importacion_albarracin_precios
            (empresa_id, centro_id, productos_actualizados, proveedores_creados, observaciones)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                args.empresa_id,
                args.centro_id,
                updated,
                len(PROVEEDORES),
                "Precios aproximados ficticios por familia para empezar gestion de compras.",
            ),
        )
        conn.commit()
        print({"proveedores": len(PROVEEDORES), "productos_actualizados": updated})
    finally:
        conn.close()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Sanea productos comprables sin proveedor en la simulacion.

La ERP no puede pedir un producto si no tiene proveedor principal. Para la
prueba de fuego asignamos proveedores demo por familia y dejamos trazabilidad.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3

PROVEEDOR_POR_FAMILIA = [
    (("coctel", "bebidas", "refrescos", "zumos", "cerveza", "licores"), 25),
    (("snack", "helado"), 31),
    (("amenities", "lavanderia"), 30),
    (("fruta", "verdura"), 6),
    (("carne", "charcuteria"), 21),
    (("pescado", "marisco"), 22),
    (("lact", "queso"), 24),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Asignar proveedores demo a productos comprables sin proveedor.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_saneamiento_productos_compras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            proveedor_id INTEGER NOT NULL,
            motivo TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(producto_id, proveedor_id)
        )
        """
    )


def proveedor_para(familia: str, nombre: str) -> int:
    text = f"{familia or ''} {nombre or ''}".lower()
    for needles, proveedor_id in PROVEEDOR_POR_FAMILIA:
        if any(n in text for n in needles):
            return proveedor_id
    return 23


def ejecutar(conn: sqlite3.Connection, centro_id: int) -> dict:
    ensure_schema(conn)
    rows = conn.execute(
        """
        SELECT id, nombre, familia
        FROM productos
        WHERE empresa_id=? AND COALESCE(activo,1)=1
          AND COALESCE(visible_en_compras,1)=1
          AND COALESCE(proveedor_id,0)=0
          AND (centro_id=? OR centro_id IS NULL)
        ORDER BY familia, nombre
        """,
        (EMPRESA_ID, centro_id),
    ).fetchall()
    actualizados = 0
    muestras = []
    for row in rows:
        proveedor_id = proveedor_para(row["familia"], row["nombre"])
        conn.execute(
            "UPDATE productos SET proveedor_id=? WHERE id=? AND empresa_id=?",
            (proveedor_id, row["id"], EMPRESA_ID),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO simulador_saneamiento_productos_compras
            (producto_id, proveedor_id, motivo, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (row["id"], proveedor_id, "Proveedor demo asignado por familia para prueba de compras.", EMPRESA_ID, centro_id),
        )
        actualizados += 1
        if len(muestras) < 12:
            muestras.append({"producto": row["nombre"], "familia": row["familia"], "proveedor_id": proveedor_id})
    conn.commit()
    return {"productos_sin_proveedor": len(rows), "actualizados": actualizados, "muestras": muestras}


def main() -> None:
    args = parse_args()
    db = Path(args.db)
    if not db.is_absolute():
        db = ROOT / db
    conn = connect(db)
    try:
        print(json.dumps(ejecutar(conn, args.centro_id), ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

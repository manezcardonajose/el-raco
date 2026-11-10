# -*- coding: utf-8 -*-
"""Normaliza estados tecnicos de compras sin borrar datos.

Sirve para limpiar incoherencias antiguas que el simulador detecta, por ejemplo
pedidos con estado cancelado pero flag cancelado a 0.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalizar estados de compras.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--empresa-id", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def add_column_if_missing(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.strip().split()[0]
    if name not in columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def main() -> None:
    args = parse_args()
    db = Path(args.db)
    if not db.is_absolute():
        db = ROOT / db
    conn = connect(db)
    try:
        if not table_exists(conn, "pedidos"):
            result = {"ok": False, "motivo": "No existe tabla pedidos"}
        else:
            add_column_if_missing(conn, "pedidos", "cancelado INTEGER DEFAULT 0")
            add_column_if_missing(conn, "pedidos", "cerrado INTEGER DEFAULT 0")
            cur = conn.execute(
                """
                UPDATE pedidos
                SET cancelado = 1
                WHERE empresa_id = ?
                  AND estado = 'cancelado'
                  AND COALESCE(cancelado, 0) = 0
                """,
                (args.empresa_id,),
            )
            cancelados = cur.rowcount if cur.rowcount is not None else 0
            cur = conn.execute(
                """
                UPDATE pedidos
                SET cerrado = 1
                WHERE empresa_id = ?
                  AND estado = 'cerrado'
                  AND COALESCE(cerrado, 0) = 0
                """,
                (args.empresa_id,),
            )
            cerrados = cur.rowcount if cur.rowcount is not None else 0
            cur = conn.execute(
                """
                UPDATE pedidos
                SET estado = 'recibido'
                WHERE empresa_id = ?
                  AND COALESCE(cancelado, 0) = 0
                  AND COALESCE(cerrado, 0) = 0
                  AND estado IN ('enviado', 'aprobado', 'pendiente')
                  AND estado_recepcion = 'recibido'
                  AND EXISTS (
                      SELECT 1 FROM recepciones r
                      WHERE r.pedido_id = pedidos.id AND r.empresa_id = pedidos.empresa_id
                  )
                """,
                (args.empresa_id,),
            )
            recibidos_estado = cur.rowcount if cur.rowcount is not None else 0
            cur = conn.execute(
                """
                UPDATE pedidos
                SET estado = 'recibido_parcial'
                WHERE empresa_id = ?
                  AND COALESCE(cancelado, 0) = 0
                  AND COALESCE(cerrado, 0) = 0
                  AND estado = 'parcial'
                  AND estado_recepcion = 'parcial'
                  AND EXISTS (
                      SELECT 1 FROM recepciones r
                      WHERE r.pedido_id = pedidos.id AND r.empresa_id = pedidos.empresa_id
                  )
                """,
                (args.empresa_id,),
            )
            parciales_estado = cur.rowcount if cur.rowcount is not None else 0
            conn.commit()
            result = {
                "ok": True,
                "cancelados_normalizados": cancelados,
                "cerrados_normalizados": cerrados,
                "recibidos_estado_normalizados": recibidos_estado,
                "parciales_estado_normalizados": parciales_estado,
            }
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result)


if __name__ == "__main__":
    main()

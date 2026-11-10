# -*- coding: utf-8 -*-
"""Genera pedidos de reposicion para productos bajo minimo.

Sirve para probar que el ERP no solo compra por mise de cocina, sino tambien
por stock operativo: bar, pisos, lavanderia, amenities, bebidas y economato.
No borra datos y evita duplicados por corrida/fecha.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crear pedidos por stock minimo en simulacion.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    parser.add_argument("--fecha", default="")
    parser.add_argument("--estado", default="enviado", choices=["borrador", "enviado"])
    parser.add_argument("--multiplicador", type=float, default=1.5)
    parser.add_argument("--max-productos", type=int, default=160)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_reposicion_stock_minimo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            centro_id INTEGER DEFAULT 3,
            fecha TEXT NOT NULL,
            producto_id INTEGER NOT NULL,
            proveedor_id INTEGER NOT NULL,
            pedido_id INTEGER,
            stock_actual REAL DEFAULT 0,
            stock_minimo REAL DEFAULT 0,
            cantidad_pedida REAL DEFAULT 0,
            familia TEXT,
            estado TEXT DEFAULT 'pedido_generado',
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, fecha, producto_id, proveedor_id)
        )
        """
    )


def latest_fecha(conn: sqlite3.Connection, corrida_id: int) -> str:
    if table_exists(conn, "simulador_vida_kpis_diarios"):
        row = conn.execute("SELECT MAX(fecha) fecha FROM simulador_vida_kpis_diarios WHERE corrida_id=?", (corrida_id,)).fetchone()
        if row and row["fecha"]:
            return str(row["fecha"])[:10]
    return date.today().isoformat()


def log_event(conn: sqlite3.Connection, corrida_id: int, centro_id: int, fecha: str, pedido_id: int, lineas: int, importe: float, proveedor_id: int) -> None:
    if not table_exists(conn, "simulador_vida_eventos"):
        return
    conn.execute(
        """
        INSERT INTO simulador_vida_eventos
        (corrida_id, fecha_simulada, modulo, tipo, severidad, descripcion,
         tabla_ref, registro_id, impacto_json, empresa_id, centro_id)
        VALUES (?, ?, 'compras', 'pedido_stock_minimo', 'media', ?, 'pedidos', ?, ?, ?, ?)
        """,
        (
            corrida_id,
            fecha,
            f"Pedido de reposicion por bajo minimo: {lineas} lineas proveedor {proveedor_id}.",
            pedido_id,
            json.dumps({"lineas": lineas, "importe": round(importe, 2), "proveedor_id": proveedor_id}, ensure_ascii=False),
            EMPRESA_ID,
            centro_id,
        ),
    )


def existing(conn: sqlite3.Connection, fecha: str) -> int:
    prefix = f"BAJO-MIN-H1-{fecha.replace('-', '')}-%"
    row = conn.execute(
        "SELECT COUNT(*) FROM pedidos WHERE empresa_id=? AND numero_pedido LIKE ?",
        (EMPRESA_ID, prefix),
    ).fetchone()
    return int(row[0] or 0)


def load_needs(conn: sqlite3.Connection, args: argparse.Namespace) -> list[dict]:
    rows = conn.execute(
        """
        WITH stock AS (
            SELECT
                p.id producto_id,
                p.nombre,
                p.familia,
                p.tipo_conservacion,
                COALESCE(SUM(sa.cantidad), COALESCE(p.cantidad,0), 0) stock_actual,
                COALESCE(NULLIF(MAX(sa.stock_minimo),0), COALESCE(p.stock_minimo,0), 0) stock_minimo,
                COALESCE(p.precio,0) precio,
                COALESCE(p.proveedor_id,0) proveedor_id,
                COALESCE(p.departamento_responsable_id, 10) departamento_id,
                COALESCE(p.unidad,'ud') unidad
            FROM productos p
            LEFT JOIN stock_almacen sa ON sa.producto_id=p.id
            WHERE p.empresa_id=? AND COALESCE(p.activo,1)=1
              AND COALESCE(p.visible_en_compras,1)=1
              AND COALESCE(p.proveedor_id,0)>0
            GROUP BY p.id
        )
        SELECT *
        FROM stock
        WHERE stock_minimo>0 AND stock_actual <= stock_minimo
        ORDER BY
          CASE WHEN stock_actual=0 THEN 0 ELSE 1 END,
          (stock_minimo-stock_actual) DESC,
          familia, nombre
        LIMIT ?
        """,
        (EMPRESA_ID, int(args.max_productos)),
    ).fetchall()
    needs = []
    for row in rows:
        stock = float(row["stock_actual"] or 0)
        minimo = float(row["stock_minimo"] or 0)
        objetivo = max(minimo * float(args.multiplicador or 1.5), minimo + 1)
        cantidad = round(max(0.0, objetivo - stock), 3)
        if cantidad <= 0:
            continue
        data = dict(row)
        data["cantidad_pedida"] = cantidad
        needs.append(data)
    return needs


def generar(conn: sqlite3.Connection, args: argparse.Namespace) -> dict:
    ensure_schema(conn)
    if existing(conn, args.fecha):
        return {"creados": 0, "lineas": 0, "omitido": "Ya existen pedidos BAJO-MIN-H1 para esta fecha."}
    needs = load_needs(conn, args)
    grupos: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for n in needs:
        grupos[(int(n["proveedor_id"]), int(n["departamento_id"] or 10))].append(n)

    serial = 1
    creados = lineas_total = 0
    importe_total = 0.0
    fecha_entrega_base = date.fromisoformat(args.fecha) + timedelta(days=1)
    for (proveedor_id, departamento_id), lineas in sorted(grupos.items()):
        numero = f"BAJO-MIN-H1-{args.fecha.replace('-', '')}-{serial:03d}"
        fecha_entrega = (fecha_entrega_base + timedelta(days=serial % 3)).isoformat()
        cur = conn.execute(
            """
            INSERT INTO pedidos
            (numero_pedido, proveedor_id, departamento_id, departamento_solicitante_id,
             fecha_pedido, fecha_entrega, estado, observaciones, creado_por, empresa_id,
             solicitado_por, tipo_pedido, origen_documento, validado_economicamente)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Simulador stock minimo', ?,
                    'Simulador stock minimo', 'reposicion_stock_minimo',
                    'simulador_stock_minimo', 0)
            """,
            (
                numero,
                proveedor_id,
                departamento_id,
                departamento_id,
                args.fecha,
                fecha_entrega,
                args.estado,
                "Pedido automatico por productos bajo stock minimo en simulacion.",
                EMPRESA_ID,
            ),
        )
        pedido_id = int(cur.lastrowid)
        creados += 1
        serial += 1
        importe_pedido = 0.0
        for n in lineas:
            cantidad = float(n["cantidad_pedida"] or 0)
            precio = float(n["precio"] or 0)
            conn.execute(
                """
                INSERT INTO lineas_pedido
                (pedido_id, producto_id, cantidad_solicitada, cantidad_recibida, precio_unitario)
                VALUES (?, ?, ?, 0, ?)
                """,
                (pedido_id, int(n["producto_id"]), cantidad, precio),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO simulador_reposicion_stock_minimo
                (corrida_id, centro_id, fecha, producto_id, proveedor_id, pedido_id,
                 stock_actual, stock_minimo, cantidad_pedida, familia, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    args.corrida_id,
                    args.centro_id,
                    args.fecha,
                    int(n["producto_id"]),
                    proveedor_id,
                    pedido_id,
                    float(n["stock_actual"] or 0),
                    float(n["stock_minimo"] or 0),
                    cantidad,
                    n["familia"],
                    EMPRESA_ID,
                ),
            )
            lineas_total += 1
            importe_pedido += cantidad * precio
        importe_total += importe_pedido
        log_event(conn, args.corrida_id, args.centro_id, args.fecha, pedido_id, len(lineas), importe_pedido, proveedor_id)
    conn.commit()
    return {"creados": creados, "lineas": lineas_total, "necesidades": len(needs), "importe_estimado": round(importe_total, 2)}


def main() -> None:
    args = parse_args()
    db = Path(args.db)
    if not db.is_absolute():
        db = ROOT / db
    conn = connect(db)
    try:
        if not args.fecha:
            args.fecha = latest_fecha(conn, args.corrida_id)
        result = generar(conn, args)
        print(json.dumps({"fecha": args.fecha, **result}, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Genera pedidos desde alertas de mise bloqueadas por stock.

Este script cubre el hueco entre planificacion/produccion y compras: si cocina
ya sabe que falta producto para un servicio, debe existir un pedido trazable.
No borra datos y evita duplicados por rango de fechas.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crear pedidos desde alertas mise bloqueadas por stock.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=1)
    parser.add_argument("--desde", default="")
    parser.add_argument("--hasta", default="")
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    parser.add_argument("--estado", default="enviado", choices=["borrador", "enviado"])
    parser.add_argument("--margen-seguridad", type=float, default=1.15)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def latest_range(conn: sqlite3.Connection, corrida_id: int) -> tuple[str, str]:
    row = conn.execute(
        """
        SELECT MIN(fecha_servicio) desde, MAX(fecha_servicio) hasta
        FROM simulador_alertas_mise_en_place
        WHERE corrida_id=? AND COALESCE(estado,'')='bloqueada_stock'
        """,
        (corrida_id,),
    ).fetchone()
    return row["desde"] or "2026-06-01", row["hasta"] or "2026-06-01"


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_compras_alertas_mise (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            centro_id INTEGER DEFAULT 3,
            fecha_desde TEXT NOT NULL,
            fecha_hasta TEXT NOT NULL,
            producto_id INTEGER NOT NULL,
            proveedor_id INTEGER NOT NULL,
            pedido_id INTEGER,
            cantidad_necesaria REAL DEFAULT 0,
            cantidad_pedida REAL DEFAULT 0,
            alertas INTEGER DEFAULT 0,
            estado TEXT DEFAULT 'pedido_generado',
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, fecha_desde, fecha_hasta, producto_id, proveedor_id)
        )
        """
    )


def log_event(conn: sqlite3.Connection, corrida_id: int, fecha: str, severidad: str, descripcion: str, pedido_id: int | None, impacto: dict, centro_id: int) -> None:
    if not table_exists(conn, "simulador_vida_eventos"):
        return
    conn.execute(
        """
        INSERT INTO simulador_vida_eventos
        (corrida_id, fecha_simulada, modulo, tipo, severidad, descripcion,
         tabla_ref, registro_id, impacto_json, empresa_id, centro_id)
        VALUES (?, ?, 'compras', 'pedido_alerta_mise', ?, ?, 'pedidos', ?, ?, ?, ?)
        """,
        (corrida_id, fecha, severidad, descripcion, pedido_id, json.dumps(impacto, ensure_ascii=False), EMPRESA_ID, centro_id),
    )


def existing_for_range(conn: sqlite3.Connection, desde: str, hasta: str, corrida_id: int, centro_id: int) -> int:
    prefix = f"ALERTA-MISE-H1-{desde.replace('-', '')}-{hasta.replace('-', '')}-%"
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM pedidos
        WHERE empresa_id=? AND numero_pedido LIKE ?
        """,
        (EMPRESA_ID, prefix),
    ).fetchone()
    if int(row[0] or 0):
        return int(row[0] or 0)
    if not table_exists(conn, "simulador_compras_alertas_mise"):
        return 0
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM simulador_compras_alertas_mise
        WHERE corrida_id=? AND centro_id=? AND fecha_desde=? AND fecha_hasta=?
        """,
        (corrida_id, centro_id, desde, hasta),
    ).fetchone()
    return int(row[0] or 0)


def load_needs(conn: sqlite3.Connection, args: argparse.Namespace) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            a.producto_id,
            COALESCE(p.nombre, a.producto) AS producto,
            COALESCE(p.proveedor_id, 0) AS proveedor_id,
            COALESCE(p.departamento_responsable_id, 1) AS departamento_id,
            COALESCE(p.precio, 0) AS precio,
            COALESCE(p.cantidad, 0) AS stock_actual,
            COALESCE(p.stock_minimo, 0) AS stock_minimo,
            COALESCE(p.unidad, a.unidad, 'ud') AS unidad,
            SUM(COALESCE(a.cantidad, 0)) AS cantidad_necesaria,
            COUNT(*) AS alertas
        FROM simulador_alertas_mise_en_place a
        JOIN productos p ON p.id=a.producto_id
        WHERE a.corrida_id=?
          AND a.centro_id=?
          AND a.fecha_servicio BETWEEN ? AND ?
          AND COALESCE(a.estado,'')='bloqueada_stock'
          AND a.producto_id IS NOT NULL
          AND COALESCE(p.proveedor_id, 0) > 0
        GROUP BY a.producto_id, proveedor_id, departamento_id
        HAVING cantidad_necesaria > 0
        ORDER BY proveedor_id, departamento_id, producto
        """,
        (args.corrida_id, args.centro_id, args.desde, args.hasta),
    ).fetchall()
    needs = []
    for row in rows:
        cantidad_necesaria = float(row["cantidad_necesaria"] or 0)
        stock_actual = float(row["stock_actual"] or 0)
        stock_minimo = float(row["stock_minimo"] or 0)
        deficit = max(0.0, cantidad_necesaria + stock_minimo - stock_actual)
        cantidad_pedida = round(max(cantidad_necesaria, deficit) * float(args.margen_seguridad or 1), 3)
        if cantidad_pedida <= 0:
            continue
        data = dict(row)
        data["cantidad_pedida"] = cantidad_pedida
        needs.append(data)
    return needs


def generar(conn: sqlite3.Connection, args: argparse.Namespace, needs: list[dict]) -> dict:
    ensure_schema(conn)
    if existing_for_range(conn, args.desde, args.hasta, args.corrida_id, args.centro_id):
        return {"creados": 0, "lineas": 0, "omitido": "Ya existen pedidos ALERTA-MISE-H1 para este rango."}

    grupos: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for need in needs:
        grupos[(int(need["proveedor_id"]), int(need["departamento_id"] or 1))].append(need)

    serial = 1
    creados = 0
    lineas_total = 0
    total_importe = 0.0
    fecha_pedido = date.fromisoformat(args.desde)
    cursor = conn.cursor()
    for (proveedor_id, departamento_id), lineas in sorted(grupos.items()):
        numero = f"ALERTA-MISE-H1-{args.desde.replace('-', '')}-{args.hasta.replace('-', '')}-{serial:03d}"
        fecha_entrega = (fecha_pedido + timedelta(days=1 + (serial % 2))).isoformat()
        cursor.execute(
            """
            INSERT INTO pedidos
            (numero_pedido, proveedor_id, departamento_id, departamento_solicitante_id,
             fecha_pedido, fecha_entrega, estado, observaciones, creado_por, empresa_id,
             solicitado_por, tipo_pedido, origen_documento, validado_economicamente)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Simulador alertas mise', ?,
                    'Simulador alertas mise', 'mise_en_place_urgente',
                    'simulador_alertas_mise_stock', 0)
            """,
            (
                numero,
                proveedor_id,
                departamento_id,
                departamento_id,
                args.desde,
                fecha_entrega,
                args.estado,
                f"Pedido generado por alertas de mise bloqueadas por stock {args.desde} a {args.hasta}.",
                EMPRESA_ID,
            ),
        )
        pedido_id = int(cursor.lastrowid)
        creados += 1
        serial += 1

        for need in lineas:
            cantidad = float(need["cantidad_pedida"] or 0)
            precio = float(need["precio"] or 0)
            cursor.execute(
                """
                INSERT INTO lineas_pedido
                (pedido_id, producto_id, cantidad_solicitada, cantidad_recibida, precio_unitario)
                VALUES (?, ?, ?, 0, ?)
                """,
                (pedido_id, int(need["producto_id"]), cantidad, precio),
            )
            lineas_total += 1
            total_importe += cantidad * precio
            cursor.execute(
                """
                INSERT OR IGNORE INTO simulador_compras_alertas_mise
                (corrida_id, centro_id, fecha_desde, fecha_hasta, producto_id, proveedor_id,
                 pedido_id, cantidad_necesaria, cantidad_pedida, alertas, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    args.corrida_id,
                    args.centro_id,
                    args.desde,
                    args.hasta,
                    int(need["producto_id"]),
                    proveedor_id,
                    pedido_id,
                    float(need["cantidad_necesaria"] or 0),
                    cantidad,
                    int(need["alertas"] or 0),
                    EMPRESA_ID,
                ),
            )

        log_event(
            conn,
            args.corrida_id,
            args.desde,
            "alta",
            f"Pedido urgente mise {numero}: {len(lineas)} productos para desbloquear cocina.",
            pedido_id,
            {"proveedor_id": proveedor_id, "departamento_id": departamento_id, "lineas": len(lineas), "importe": round(sum(float(n["cantidad_pedida"]) * float(n["precio"] or 0) for n in lineas), 2)},
            args.centro_id,
        )

    conn.commit()
    return {"creados": creados, "lineas": lineas_total, "necesidades": len(needs), "importe_estimado": round(total_importe, 2)}


def main() -> None:
    args = parse_args()
    db = Path(args.db)
    if not db.is_absolute():
        db = ROOT / db
    conn = connect(db)
    try:
        if not table_exists(conn, "simulador_alertas_mise_en_place"):
            raise SystemExit("Falta simulador_alertas_mise_en_place.")
        if not args.desde or not args.hasta:
            args.desde, args.hasta = latest_range(conn, args.corrida_id)
        needs = load_needs(conn, args)
        result = generar(conn, args, needs)
        print(json.dumps({"desde": args.desde, "hasta": args.hasta, **result}, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

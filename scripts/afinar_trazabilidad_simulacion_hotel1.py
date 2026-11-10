# -*- coding: utf-8 -*-
"""Afina trazabilidad de datos ya generados por el simulador Hotel 1.

No elimina datos. Completa cierres, recepciones y saldos incoherentes en datos
marcados como simulacion VIDA-H1/SIMH1.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Afinar trazabilidad de simulacion Hotel 1.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def fix_facturas(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "recepcion_facturas"):
        return 0
    rows = conn.execute(
        """
        SELECT id, total, pagado, saldo
        FROM recepcion_facturas
        WHERE serie='SIMH1'
          AND ABS(ROUND(COALESCE(total,0) - COALESCE(pagado,0) - COALESCE(saldo,0), 2)) > 0.05
        """
    ).fetchall()
    for row in rows:
        total = float(row["total"] or 0)
        pagado = min(float(row["pagado"] or 0), total)
        saldo = round(max(0.0, total - pagado), 2)
        estado = "cobrada" if saldo <= 0.05 else "pendiente_cobro"
        conn.execute(
            """
            UPDATE recepcion_facturas
            SET pagado=?, saldo=?, estado=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (round(pagado, 2), saldo, estado, row["id"]),
        )
    return len(rows)


def fix_pedidos(conn: sqlite3.Connection) -> int:
    if not (table_exists(conn, "pedidos") and table_exists(conn, "recepciones")):
        return 0
    rows = conn.execute(
        """
        SELECT p.*
        FROM pedidos p
        WHERE p.numero_pedido LIKE 'PED-VIDA-H1-%'
          AND (p.estado IN ('cerrado','recibido','recepcionado') OR COALESCE(p.cerrado,0)=1)
          AND NOT EXISTS (SELECT 1 FROM recepciones r WHERE r.pedido_id=p.id)
        """
    ).fetchall()
    for row in rows:
        fecha_recepcion = row["fecha_recepcion"] or row["fecha_entrega"] or row["fecha_pedido"]
        conn.execute(
            """
            INSERT INTO recepciones (
                pedido_id, proveedor_id, fecha_recepcion, numero_albaran, observaciones,
                recibido_por, empresa_id, tipo_recepcion, departamento_id,
                departamento_receptor_id, validada_operativamente,
                validada_economicamente, stock_aplicado, estado_facturacion
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["proveedor_id"],
                fecha_recepcion,
                f"ALB-VIDA-{int(row['id']):05d}-AUTO",
                "Recepcion completada por afinador de trazabilidad simulada",
                "Economato simulado",
                row["empresa_id"] or 1,
                "albaran",
                row["departamento_id"],
                row["departamento_solicitante_id"] or row["departamento_id"],
                1,
                0,
                1,
                "pendiente_factura",
            ),
        )
        conn.execute(
            """
            UPDATE pedidos
            SET fecha_recepcion=COALESCE(fecha_recepcion, ?),
                estado_recepcion='recibido',
                cerrado=CASE WHEN estado='cerrado' THEN 1 ELSE COALESCE(cerrado,0) END,
                fecha_cierre=CASE WHEN estado='cerrado' THEN COALESCE(fecha_cierre, ?) ELSE fecha_cierre END
            WHERE id=?
            """,
            (fecha_recepcion, fecha_recepcion, row["id"]),
        )
    return len(rows)


def fix_ordenes(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "ordenes_trabajo"):
        return 0
    rows = conn.execute(
        """
        SELECT id, fecha_apertura, coste_estimado, horas_estimadas
        FROM ordenes_trabajo
        WHERE numero_orden LIKE 'OT-VIDA-H1-%'
          AND estado IN ('cerrada','cerrado','finalizada','completada')
          AND COALESCE(fecha_cierre, fecha_fin, '') = ''
        """
    ).fetchall()
    for row in rows:
        fecha_cierre = row["fecha_apertura"] or "2026-06-01 17:00:00"
        conn.execute(
            """
            UPDATE ordenes_trabajo
            SET fecha_inicio=COALESCE(fecha_inicio, fecha_apertura),
                fecha_fin=?,
                fecha_cierre=?,
                cerrado_por=COALESCE(cerrado_por, 'Mantenimiento simulado'),
                horas_reales=COALESCE(horas_reales, horas_estimadas, 1),
                coste_real=COALESCE(coste_real, coste_estimado, 0),
                trabajo_realizado=COALESCE(trabajo_realizado, 'Cierre completado por simulador')
            WHERE id=?
            """,
            (fecha_cierre, fecha_cierre, row["id"]),
        )
    return len(rows)


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        facturas = fix_facturas(conn)
        pedidos = fix_pedidos(conn)
        ordenes = fix_ordenes(conn)
        conn.commit()
        print({"facturas_afinadas": facturas, "recepciones_creadas": pedidos, "ordenes_cerradas_afinadas": ordenes})
    finally:
        conn.close()


if __name__ == "__main__":
    main()

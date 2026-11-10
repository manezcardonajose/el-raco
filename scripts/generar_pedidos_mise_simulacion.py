"""Genera pedidos de compra desde necesidades de mise en place del simulador.

No borra pedidos existentes. Si ya hay pedidos MISE-H1 para el rango/corrida,
los deja quietos para evitar duplicados.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_DEFAULT = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_DEFAULT))
    parser.add_argument("--corrida-id", type=int, default=1)
    parser.add_argument("--desde", default="2026-05-30")
    parser.add_argument("--hasta", default="2026-06-13")
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    parser.add_argument("--seed", type=int, default=20260530)
    parser.add_argument("--estado", default="borrador", choices=["borrador", "enviado"])
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def log_event(conn: sqlite3.Connection, corrida_id: int, fecha: str, tipo: str, severidad: str, descripcion: str, tabla_ref: str, registro_id: int | None, impacto: dict, centro_id: int) -> None:
    if not table_exists(conn, "simulador_vida_eventos"):
        return
    conn.execute(
        """
        INSERT INTO simulador_vida_eventos
        (corrida_id, fecha_simulada, modulo, tipo, severidad, descripcion,
         tabla_ref, registro_id, impacto_json, empresa_id, centro_id)
        VALUES (?, ?, 'compras', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (corrida_id, fecha, tipo, severidad, descripcion, tabla_ref, registro_id, json.dumps(impacto, ensure_ascii=False), EMPRESA_ID, centro_id),
    )


def existing_for_range(conn: sqlite3.Connection, desde: str, hasta: str) -> int:
    prefix = f"MISE-H1-{desde.replace('-', '')}-{hasta.replace('-', '')}-%"
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM pedidos
        WHERE empresa_id=? AND numero_pedido LIKE ?
        """,
        (EMPRESA_ID, prefix),
    ).fetchone()
    return int(row[0] or 0)


def load_needs(db_path: Path, desde: str, hasta: str, centro_id: int) -> list[dict]:
    os.environ["ERP_DATABASE"] = str(db_path)
    os.environ["PYTHONPATH"] = str(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app import app
    from database import get_db
    from blueprints.compras.routes import _calcular_necesidades_compra

    with app.app_context():
        conn = get_db()
        needs = _calcular_necesidades_compra(
            conn,
            EMPRESA_ID,
            incluir_planificacion=True,
            fecha_desde=desde,
            fecha_hasta=hasta,
            centro_id=centro_id,
        )
        return [
            dict(n)
            for n in needs
            if "mise" in (n.get("origen_necesidad") or "") and n.get("proveedor_id") and float(n.get("cantidad_sugerida") or 0) > 0
        ]


def generar(conn: sqlite3.Connection, needs: list[dict], args: argparse.Namespace) -> dict:
    if existing_for_range(conn, args.desde, args.hasta):
        return {"creados": 0, "lineas": 0, "omitido": "Ya existen pedidos MISE-H1 para este rango."}

    grupos: dict[tuple[int, int], list[dict]] = {}
    for n in needs:
        proveedor_id = int(n["proveedor_id"])
        departamento_id = int(n.get("departamento_id") or 0)
        if not departamento_id:
            continue
        grupos.setdefault((proveedor_id, departamento_id), []).append(n)

    serial = 1
    creados = 0
    lineas_total = 0
    fecha_pedido = args.desde
    entrega_base = date.fromisoformat(args.desde) + timedelta(days=1)
    cursor = conn.cursor()
    for (proveedor_id, departamento_id), lineas in sorted(grupos.items()):
        numero = f"MISE-H1-{args.desde.replace('-', '')}-{args.hasta.replace('-', '')}-{serial:03d}"
        fecha_entrega = (entrega_base + timedelta(days=random.randint(0, 2))).isoformat()
        cursor.execute(
            """
            INSERT INTO pedidos
            (numero_pedido, proveedor_id, departamento_id, departamento_solicitante_id,
             fecha_pedido, fecha_entrega, estado, observaciones, creado_por, empresa_id,
             solicitado_por, tipo_pedido, origen_documento, validado_economicamente)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Simulador mise en place', ?, 'Simulador mise en place',
                    'mise_en_place', 'simulador_mise_en_place', 0)
            """,
            (
                numero,
                proveedor_id,
                departamento_id,
                departamento_id,
                fecha_pedido,
                fecha_entrega,
                args.estado,
                f"Pedido generado desde necesidades de mise en place {args.desde} a {args.hasta}.",
                EMPRESA_ID,
            ),
        )
        pedido_id = int(cursor.lastrowid)
        creados += 1
        serial += 1

        for n in lineas:
            cantidad = round(float(n.get("cantidad_sugerida") or 0), 3)
            precio = round(float(n.get("precio_referencia") or 0), 4)
            cursor.execute(
                """
                INSERT INTO lineas_pedido
                (pedido_id, producto_id, cantidad_solicitada, cantidad_recibida, precio_unitario)
                VALUES (?, ?, ?, 0, ?)
                """,
                (pedido_id, int(n["id"]), cantidad, precio),
            )
            lineas_total += 1

        log_event(
            conn,
            args.corrida_id,
            args.desde,
            "pedido_mise_generado",
            "alta" if any(float(n.get("stock_total") or 0) <= 0 for n in lineas) else "info",
            f"Pedido mise {numero}: {len(lineas)} lineas para proveedor {proveedor_id}.",
            "pedidos",
            pedido_id,
            {"proveedor_id": proveedor_id, "departamento_id": departamento_id, "rango": [args.desde, args.hasta]},
            args.centro_id,
        )

    conn.commit()
    return {"creados": creados, "lineas": lineas_total, "necesidades_mise": len(needs)}


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    needs = load_needs(db_path, args.desde, args.hasta, args.centro_id)
    conn = connect(db_path)
    try:
        result = generar(conn, needs, args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Resume los bloqueos de stock de mise en place por producto.

No modifica pedidos ni productos. Crea una capa de lectura para direccion,
compras y cocina: que producto bloquea, cuanto falta, que ya se pidio y que
accion deberia tomarse.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resumen de brechas stock/mise.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def latest_corrida(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "simulador_vida_corridas"):
        return 0
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_mise_brechas_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            centro_id INTEGER DEFAULT 3,
            producto_id INTEGER,
            producto TEXT,
            familia TEXT,
            unidad TEXT,
            proveedor_id INTEGER,
            proveedor TEXT,
            departamento_id INTEGER,
            departamento TEXT,
            avisos INTEGER DEFAULT 0,
            cantidad_bloqueada REAL DEFAULT 0,
            cantidad_stock REAL DEFAULT 0,
            cantidad_pedida_mise REAL DEFAULT 0,
            cantidad_recibida_mise REAL DEFAULT 0,
            brecha_estimada REAL DEFAULT 0,
            coste_brecha_estimado REAL DEFAULT 0,
            primera_fecha TEXT,
            ultima_fecha TEXT,
            prioridad TEXT,
            accion TEXT,
            lectura TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, producto_id)
        )
        """
    )


def prioridad(avisos: int, brecha: float, coste: float) -> str:
    if avisos >= 500 or brecha > 1000 or coste > 10000:
        return "critica"
    if avisos >= 100 or brecha > 200 or coste > 2500:
        return "alta"
    if avisos:
        return "media"
    return "info"


def accion_text(proveedor: str | None, brecha: float, pedida: float, recibida: float) -> tuple[str, str]:
    if brecha <= 0:
        return (
            "Validar recepciones y liberar stock para cocina.",
            "El pedido/stock cubre la necesidad estimada; el bloqueo parece de recepcion, lote o actualizacion de stock.",
        )
    if not proveedor:
        return (
            "Asignar proveedor preferente antes de pedir.",
            "Hay necesidad real, pero el producto no tiene proveedor claro para compra automatica.",
        )
    if pedida <= 0:
        return (
            "Crear pedido urgente desde mise en place.",
            "No hay pedido MISE asociado suficiente para cubrir la necesidad bloqueada.",
        )
    if recibida <= 0:
        return (
            "Reclamar entrega o buscar proveedor alternativo.",
            "Hay pedido, pero no recepcion suficiente para alimentar cocina.",
        )
    return (
        "Ampliar pedido y revisar stock minimo.",
        "La cantidad pedida/recibida no alcanza para la campana simulada.",
    )


def generar(conn: sqlite3.Connection, corrida_id: int, centro_id: int) -> dict:
    ensure_schema(conn)
    conn.execute(
        "DELETE FROM simulador_mise_brechas_stock WHERE corrida_id=? AND centro_id=?",
        (corrida_id, centro_id),
    )
    rows = conn.execute(
        """
        SELECT a.producto_id,
               COALESCE(p.nombre, a.producto) producto,
               COALESCE(p.familia, '') familia,
               COALESCE(p.unidad, a.unidad, '') unidad,
               p.proveedor_id,
               pr.nombre proveedor,
               p.departamento_responsable_id departamento_id,
               d.nombre departamento,
               COUNT(*) avisos,
               ROUND(SUM(COALESCE(a.cantidad,0)), 3) cantidad_bloqueada,
               ROUND(COALESCE(p.cantidad,0), 3) cantidad_stock,
               ROUND(COALESCE((
                   SELECT SUM(lp.cantidad_solicitada)
                   FROM lineas_pedido lp
                   JOIN pedidos pe ON pe.id=lp.pedido_id
                   WHERE lp.producto_id=a.producto_id
                     AND pe.empresa_id=?
                     AND pe.numero_pedido LIKE 'MISE-H1-%'
               ),0), 3) cantidad_pedida_mise,
               ROUND(COALESCE((
                   SELECT SUM(lp.cantidad_recibida)
                   FROM lineas_pedido lp
                   JOIN pedidos pe ON pe.id=lp.pedido_id
                   WHERE lp.producto_id=a.producto_id
                     AND pe.empresa_id=?
                     AND pe.numero_pedido LIKE 'MISE-H1-%'
               ),0), 3) cantidad_recibida_mise,
               ROUND(COALESCE(p.precio,0), 4) precio,
               MIN(a.fecha_servicio) primera_fecha,
               MAX(a.fecha_servicio) ultima_fecha
        FROM simulador_alertas_mise_en_place a
        LEFT JOIN productos p ON p.id=a.producto_id
        LEFT JOIN proveedores pr ON pr.id=p.proveedor_id
        LEFT JOIN departamentos d ON d.id=p.departamento_responsable_id
        WHERE a.corrida_id=? AND a.centro_id=?
          AND a.estado='bloqueada_stock'
        GROUP BY a.producto_id, COALESCE(p.nombre, a.producto)
        ORDER BY avisos DESC, cantidad_bloqueada DESC
        """,
        (EMPRESA_ID, EMPRESA_ID, corrida_id, centro_id),
    ).fetchall()
    inserted = 0
    for row in rows:
        bloqueada = float(row["cantidad_bloqueada"] or 0)
        stock = float(row["cantidad_stock"] or 0)
        pedida = float(row["cantidad_pedida_mise"] or 0)
        recibida = float(row["cantidad_recibida_mise"] or 0)
        precio = float(row["precio"] or 0)
        cobertura = max(stock, 0) + max(pedida, recibida, 0)
        brecha = round(max(0.0, bloqueada - cobertura), 3)
        coste = round(brecha * precio, 2)
        prio = prioridad(int(row["avisos"] or 0), brecha, coste)
        accion, lectura = accion_text(row["proveedor"], brecha, pedida, recibida)
        conn.execute(
            """
            INSERT INTO simulador_mise_brechas_stock
            (corrida_id, centro_id, producto_id, producto, familia, unidad,
             proveedor_id, proveedor, departamento_id, departamento, avisos,
             cantidad_bloqueada, cantidad_stock, cantidad_pedida_mise,
             cantidad_recibida_mise, brecha_estimada, coste_brecha_estimado,
             primera_fecha, ultima_fecha, prioridad, accion, lectura, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corrida_id,
                centro_id,
                row["producto_id"],
                row["producto"],
                row["familia"],
                row["unidad"],
                row["proveedor_id"],
                row["proveedor"],
                row["departamento_id"],
                row["departamento"],
                int(row["avisos"] or 0),
                bloqueada,
                stock,
                pedida,
                recibida,
                brecha,
                coste,
                row["primera_fecha"],
                row["ultima_fecha"],
                prio,
                accion,
                lectura,
                EMPRESA_ID,
            ),
        )
        inserted += 1
    conn.commit()
    resumen = conn.execute(
        """
        SELECT COUNT(*) productos,
               SUM(avisos) avisos,
               ROUND(SUM(brecha_estimada), 2) brecha,
               ROUND(SUM(coste_brecha_estimado), 2) coste,
               SUM(CASE WHEN prioridad='critica' THEN 1 ELSE 0 END) criticos,
               SUM(CASE WHEN prioridad='alta' THEN 1 ELSE 0 END) altos
        FROM simulador_mise_brechas_stock
        WHERE corrida_id=? AND centro_id=?
        """,
        (corrida_id, centro_id),
    ).fetchone()
    return dict(resumen) | {"insertados": inserted}


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        corrida_id = args.corrida_id or latest_corrida(conn)
        if not corrida_id:
            raise SystemExit("No hay corrida de simulacion.")
        print(generar(conn, corrida_id, args.centro_id))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

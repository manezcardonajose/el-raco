# -*- coding: utf-8 -*-
"""Crea un plan semanal de compras desde brechas de mise.

No crea pedidos reales. Reparte la brecha por semanas y proveedor para que
direccion pueda ver caja, volumen y riesgo antes de convertirlo en compra.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan semanal de compras desde brechas de mise.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    parser.add_argument("--lead-time-dias", type=int, default=2)
    parser.add_argument("--colchon-pct", type=float, default=0.08)
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
        CREATE TABLE IF NOT EXISTS simulador_plan_compras_mise (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            centro_id INTEGER DEFAULT 3,
            semana_inicio TEXT NOT NULL,
            fecha_pedido TEXT NOT NULL,
            fecha_entrega_objetivo TEXT NOT NULL,
            producto_id INTEGER,
            producto TEXT,
            proveedor_id INTEGER,
            proveedor TEXT,
            departamento TEXT,
            unidad TEXT,
            cantidad_planificada REAL DEFAULT 0,
            coste_estimado REAL DEFAULT 0,
            avisos_origen INTEGER DEFAULT 0,
            prioridad TEXT,
            riesgo TEXT,
            accion TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, semana_inicio, producto_id)
        )
        """
    )


def parse_date(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def weeks_between(start: date, end: date) -> list[date]:
    current = monday_of(start)
    weeks: list[date] = []
    while current <= end:
        weeks.append(current)
        current += timedelta(days=7)
    return weeks


def riesgo_text(coste: float, proveedor: str | None, cantidad: float) -> tuple[str, str]:
    if not proveedor:
        return "critico", "Asignar proveedor antes de convertir en pedido."
    if coste >= 20000:
        return "alto", "Validar caja semanal y cupo de proveedor antes de pedir."
    if cantidad <= 0:
        return "sin_carga", "No comprar; revisar calculo."
    return "controlado", "Convertir en pedido semanal si cocina mantiene menu previsto."


def generar(conn: sqlite3.Connection, corrida_id: int, centro_id: int, lead_time: int, colchon_pct: float) -> dict:
    if not table_exists(conn, "simulador_mise_brechas_stock"):
        return {"planes": 0, "omitido": "No hay brechas de stock calculadas."}
    ensure_schema(conn)
    conn.execute(
        "DELETE FROM simulador_plan_compras_mise WHERE corrida_id=? AND centro_id=?",
        (corrida_id, centro_id),
    )
    rows = conn.execute(
        """
        SELECT *
        FROM simulador_mise_brechas_stock
        WHERE corrida_id=? AND centro_id=? AND brecha_estimada > 0
        ORDER BY prioridad, coste_brecha_estimado DESC
        """,
        (corrida_id, centro_id),
    ).fetchall()
    inserted = 0
    for row in rows:
        start = parse_date(row["primera_fecha"])
        end = parse_date(row["ultima_fecha"])
        weeks = weeks_between(start, end)
        if not weeks:
            continue
        total_qty = float(row["brecha_estimada"] or 0) * (1 + max(0.0, colchon_pct))
        total_cost = float(row["coste_brecha_estimado"] or 0) * (1 + max(0.0, colchon_pct))
        qty_week = round(total_qty / len(weeks), 3)
        cost_week = round(total_cost / len(weeks), 2)
        avisos_week = max(1, round(float(row["avisos"] or 0) / len(weeks)))
        for week in weeks:
            entrega = week
            pedido = max(start - timedelta(days=lead_time), week - timedelta(days=lead_time))
            riesgo, accion = riesgo_text(cost_week, row["proveedor"], qty_week)
            if row["prioridad"] == "critica" and riesgo == "controlado":
                riesgo = "medio"
            conn.execute(
                """
                INSERT INTO simulador_plan_compras_mise
                (corrida_id, centro_id, semana_inicio, fecha_pedido, fecha_entrega_objetivo,
                 producto_id, producto, proveedor_id, proveedor, departamento, unidad,
                 cantidad_planificada, coste_estimado, avisos_origen, prioridad,
                 riesgo, accion, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    corrida_id,
                    centro_id,
                    week.isoformat(),
                    pedido.isoformat(),
                    entrega.isoformat(),
                    row["producto_id"],
                    row["producto"],
                    row["proveedor_id"],
                    row["proveedor"],
                    row["departamento"],
                    row["unidad"],
                    qty_week,
                    cost_week,
                    avisos_week,
                    row["prioridad"],
                    riesgo,
                    accion,
                    EMPRESA_ID,
                ),
            )
            inserted += 1
    conn.commit()
    resumen = conn.execute(
        """
        SELECT COUNT(*) lineas,
               COUNT(DISTINCT semana_inicio) semanas,
               COUNT(DISTINCT proveedor_id) proveedores,
               ROUND(SUM(cantidad_planificada), 2) cantidad,
               ROUND(SUM(coste_estimado), 2) coste,
               SUM(CASE WHEN riesgo IN ('critico','alto') THEN 1 ELSE 0 END) riesgos
        FROM simulador_plan_compras_mise
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
        print(generar(conn, corrida_id, args.centro_id, args.lead_time_dias, args.colchon_pct))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

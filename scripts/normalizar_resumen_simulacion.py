"""Normaliza resúmenes diarios de simulación para evitar desviaciones absurdas.

Este script no borra trazabilidad ni partes. Ajusta la capa resumen que usa la IA
para leer campaña: producción kg/pax y coste de compras cuando falta.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_DEFAULT = ROOT / "simulacion_hotel1_vida.db"
CENTRO_ID = 3


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_DEFAULT))
    parser.add_argument("--corrida-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    parser.add_argument("--seed", type=int, default=20260530)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        if not (table_exists(conn, "simulador_vida_kpis_diarios") and table_exists(conn, "simulador_produccion_resumen_diario")):
            raise SystemExit("Faltan tablas de KPIs o producción.")

        rows = conn.execute(
            """
            SELECT
              k.id kpi_id,
              k.fecha,
              COALESCE(k.pax,0) pax,
              COALESCE(k.coste_compras_estimado,0) coste_compras,
              p.id prod_id,
              COALESCE(p.kg_carnes,0) kg_carnes,
              COALESCE(p.kg_pescados,0) kg_pescados,
              COALESCE(p.kg_otros,0) kg_otros
            FROM simulador_vida_kpis_diarios k
            JOIN simulador_produccion_resumen_diario p
              ON p.fecha=k.fecha AND p.corrida_id=k.corrida_id AND p.centro_id=k.centro_id
            WHERE k.corrida_id=? AND k.centro_id=?
            ORDER BY k.fecha
            """,
            (args.corrida_id, args.centro_id),
        ).fetchall()

        ajustados_kg = 0
        ajustados_coste = 0
        for row in rows:
            pax = float(row["pax"] or 0)
            if pax <= 0:
                continue
            kg_total = float(row["kg_carnes"] or 0) + float(row["kg_pescados"] or 0) + float(row["kg_otros"] or 0)
            kg_pax = kg_total / pax if pax else 0
            objetivo_kg_pax = random.uniform(1.18, 1.55)
            if kg_pax > 2.2 or kg_pax < 0.65:
                nuevo_total = round(pax * objetivo_kg_pax, 2)
                carne_ratio = 0.22 + random.uniform(-0.03, 0.03)
                pescado_ratio = 0.18 + random.uniform(-0.03, 0.03)
                kg_carnes = round(nuevo_total * carne_ratio, 2)
                kg_pescados = round(nuevo_total * pescado_ratio, 2)
                kg_otros = round(max(nuevo_total - kg_carnes - kg_pescados, 0), 2)
                conn.execute(
                    """
                    UPDATE simulador_produccion_resumen_diario
                    SET kg_carnes=?, kg_pescados=?, kg_otros=?,
                        lectura='Produccion normalizada contra ocupacion: kg/pax dentro de rango operativo.'
                    WHERE id=?
                    """,
                    (kg_carnes, kg_pescados, kg_otros, row["prod_id"]),
                )
                kg_total = nuevo_total
                ajustados_kg += 1

            if float(row["coste_compras"] or 0) <= 0 and kg_total > 0:
                coste_compras = round(max(pax * random.uniform(6.4, 9.8), kg_total * random.uniform(3.2, 4.4)), 2)
                total = float(conn.execute("SELECT COALESCE(coste_laboral_estimado,0)+COALESCE(coste_fijo_estimado,0)+? FROM simulador_vida_kpis_diarios WHERE id=?", (coste_compras, row["kpi_id"])).fetchone()[0] or 0)
                ingresos = float(conn.execute("SELECT COALESCE(ingresos_habitaciones,0)+COALESCE(ingresos_tpv,0) FROM simulador_vida_kpis_diarios WHERE id=?", (row["kpi_id"],)).fetchone()[0] or 0)
                conn.execute(
                    """
                    UPDATE simulador_vida_kpis_diarios
                    SET coste_compras_estimado=?,
                        coste_total_estimado=?,
                        margen_estimado=?
                    WHERE id=?
                    """,
                    (coste_compras, round(total, 2), round(ingresos - total, 2), row["kpi_id"]),
                )
                ajustados_coste += 1

        conn.commit()
        print(f"Normalizacion resumen: {ajustados_kg} dias con kg/pax ajustado, {ajustados_coste} dias con coste compras rellenado.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

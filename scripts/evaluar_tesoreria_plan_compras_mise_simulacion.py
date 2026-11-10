# -*- coding: utf-8 -*-
"""Evalua impacto mensual del plan de compras MISE sobre margen/caja.

No modifica el resultado mensual real del simulador. Crea una lectura de
direccion para decidir si el plan de compras es asumible o exige cambiar menu,
proveedor, ocupacion/precio o financiacion.
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
    parser = argparse.ArgumentParser(description="Evaluar tesoreria del plan de compras mise.")
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
        CREATE TABLE IF NOT EXISTS simulador_tesoreria_plan_compras_mise (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            centro_id INTEGER DEFAULT 3,
            mes TEXT NOT NULL,
            ingresos_total REAL DEFAULT 0,
            margen_original REAL DEFAULT 0,
            coste_compras_original REAL DEFAULT 0,
            coste_plan_mise REAL DEFAULT 0,
            coste_compras_ajustado REAL DEFAULT 0,
            margen_ajustado REAL DEFAULT 0,
            impacto_margen REAL DEFAULT 0,
            compras_pct_ingresos REAL DEFAULT 0,
            caja_minima_recomendada REAL DEFAULT 0,
            riesgo TEXT,
            decision TEXT,
            condicion_control TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, mes)
        )
        """
    )


def riesgo_y_decision(margen_ajustado: float, compras_pct: float, coste_plan: float) -> tuple[str, str, str]:
    if margen_ajustado < 0:
        return (
            "critico",
            "No ejecutar el plan completo sin cambiar menu, precio, ocupacion o financiacion.",
            "Convertir solo compras imprescindibles y validar margen por servicio antes de pedir.",
        )
    if compras_pct > 35:
        return (
            "alto",
            "Plan posible, pero tensiona compras sobre ingresos.",
            "Trocear pedidos por semana y bloquear compras no ligadas a menu confirmado.",
        )
    if coste_plan > 150000:
        return (
            "medio",
            "Plan asumible con control semanal de caja.",
            "Validar recepcion, caducidad, almacenamiento y coste por pax cada semana.",
        )
    return (
        "controlado",
        "Plan asumible si se mantiene la ocupacion y el menu previsto.",
        "Pasar a pedidos semanales y revisar desviaciones de proveedor.",
    )


def generar(conn: sqlite3.Connection, corrida_id: int, centro_id: int) -> dict:
    if not table_exists(conn, "simulador_resultado_mensual") or not table_exists(conn, "simulador_plan_compras_mise"):
        return {"meses": 0, "omitido": "Falta resultado mensual o plan de compras."}
    ensure_schema(conn)
    conn.execute(
        "DELETE FROM simulador_tesoreria_plan_compras_mise WHERE corrida_id=? AND centro_id=?",
        (corrida_id, centro_id),
    )
    rows = conn.execute(
        """
        SELECT rm.mes,
               rm.ingresos_total,
               rm.margen margen_original,
               rm.coste_compras coste_compras_original,
               COALESCE(pc.coste_plan_mise, 0) coste_plan_mise
        FROM simulador_resultado_mensual rm
        LEFT JOIN (
            SELECT substr(semana_inicio,1,7) mes,
                   SUM(coste_estimado) coste_plan_mise
            FROM simulador_plan_compras_mise
            WHERE corrida_id=? AND centro_id=?
            GROUP BY substr(semana_inicio,1,7)
        ) pc ON pc.mes=rm.mes
        WHERE rm.corrida_id=? AND rm.centro_id=?
        ORDER BY rm.mes
        """,
        (corrida_id, centro_id, corrida_id, centro_id),
    ).fetchall()
    inserted = 0
    for row in rows:
        ingresos = float(row["ingresos_total"] or 0)
        margen = float(row["margen_original"] or 0)
        coste_compras = float(row["coste_compras_original"] or 0)
        coste_plan = float(row["coste_plan_mise"] or 0)
        # El plan MISE representa compra adicional necesaria frente al coste de compras ya simulado.
        impacto = -coste_plan
        margen_ajustado = round(margen + impacto, 2)
        compras_ajustado = round(coste_compras + coste_plan, 2)
        compras_pct = round((compras_ajustado / max(ingresos, 1)) * 100, 1)
        caja_minima = round(coste_plan * 1.15, 2)
        riesgo, decision, condicion = riesgo_y_decision(margen_ajustado, compras_pct, coste_plan)
        conn.execute(
            """
            INSERT INTO simulador_tesoreria_plan_compras_mise
            (corrida_id, centro_id, mes, ingresos_total, margen_original,
             coste_compras_original, coste_plan_mise, coste_compras_ajustado,
             margen_ajustado, impacto_margen, compras_pct_ingresos,
             caja_minima_recomendada, riesgo, decision, condicion_control, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corrida_id,
                centro_id,
                row["mes"],
                round(ingresos, 2),
                round(margen, 2),
                round(coste_compras, 2),
                round(coste_plan, 2),
                compras_ajustado,
                margen_ajustado,
                round(impacto, 2),
                compras_pct,
                caja_minima,
                riesgo,
                decision,
                condicion,
                EMPRESA_ID,
            ),
        )
        inserted += 1
    conn.commit()
    resumen = conn.execute(
        """
        SELECT COUNT(*) meses,
               ROUND(SUM(coste_plan_mise), 2) coste_plan,
               ROUND(SUM(margen_original), 2) margen_original,
               ROUND(SUM(margen_ajustado), 2) margen_ajustado,
               SUM(CASE WHEN riesgo='critico' THEN 1 ELSE 0 END) criticos,
               SUM(CASE WHEN riesgo='alto' THEN 1 ELSE 0 END) altos
        FROM simulador_tesoreria_plan_compras_mise
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

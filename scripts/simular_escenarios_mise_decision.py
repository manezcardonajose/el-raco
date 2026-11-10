# -*- coding: utf-8 -*-
"""Simula alternativas al plan completo de compras MISE.

No modifica pedidos, menus ni resultados reales. Compara decisiones posibles:
comprar todo, recortar productos caros, renegociar proveedor, comprar solo
criticos o cambiar menu para que cocina no bloquee caja.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


SCENARIOS = [
    {
        "codigo": "comprar_todo",
        "nombre": "Comprar todo lo que pide cocina",
        "cost_factor": 1.0,
        "service_factor": 1.0,
        "risk": "alto",
        "description": "Desbloquea cocina, pero tensiona caja y margen.",
    },
    {
        "codigo": "solo_imprescindible",
        "nombre": "Comprar solo imprescindible",
        "cost_factor": 0.58,
        "service_factor": 0.72,
        "risk": "medio",
        "description": "Prioriza platos troncales y reduce variedad temporalmente.",
    },
    {
        "codigo": "cambio_menu_coste",
        "nombre": "Cambiar menu caro por alternativas",
        "cost_factor": 0.46,
        "service_factor": 0.78,
        "risk": "medio",
        "description": "Sustituye pescados/carnes caros por elaboraciones de menor coste y buena percepcion.",
    },
    {
        "codigo": "renegociar_proveedores",
        "nombre": "Renegociar proveedores y formatos",
        "cost_factor": 0.90,
        "service_factor": 0.96,
        "risk": "medio",
        "description": "Reduce precio y formato de compra sin tocar mucho el menu.",
    },
    {
        "codigo": "plan_prudente_combinado",
        "nombre": "Plan prudente combinado",
        "cost_factor": 0.38,
        "service_factor": 0.82,
        "risk": "bajo",
        "description": "Combina compra imprescindible, cambio de menu, negociacion y control semanal.",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simular escenarios MISE de decision.")
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
        CREATE TABLE IF NOT EXISTS simulador_escenarios_mise_decision (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            centro_id INTEGER DEFAULT 3,
            mes TEXT NOT NULL,
            codigo TEXT NOT NULL,
            nombre TEXT,
            descripcion TEXT,
            margen_original REAL DEFAULT 0,
            coste_plan_original REAL DEFAULT 0,
            coste_escenario REAL DEFAULT 0,
            ahorro_vs_plan REAL DEFAULT 0,
            margen_escenario REAL DEFAULT 0,
            cobertura_operativa_pct REAL DEFAULT 0,
            riesgo TEXT,
            veredicto TEXT,
            decision TEXT,
            condicion_control TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, mes, codigo)
        )
        """
    )


def veredicto(margen: float, cobertura: float, risk: str) -> tuple[str, str, str]:
    if margen < 0:
        return (
            "no_aplicar_completo",
            "No salva el mes: exige subir ingresos, cambiar menu mas fuerte o recortar estructura.",
            "No aprobar sin nueva simulacion de precio/ocupacion/cupo.",
        )
    if cobertura < 75:
        return (
            "aplicar_con_cuidado",
            "Salva margen, pero puede empobrecer experiencia si no se comunica bien.",
            "Medir quejas, reposicion, valoracion buffet y consumo real cada servicio.",
        )
    if risk == "bajo":
        return (
            "recomendado",
            "Mejor equilibrio entre margen, cocina y satisfaccion.",
            "Aplicar por semanas y revisar desviaciones antes de ampliar.",
        )
    return (
        "piloto_controlado",
        "Puede funcionar si se controla caja y calidad percibida.",
        "Lanzar como piloto semanal con cupos de compra y revision de margen.",
    )


def generar(conn: sqlite3.Connection, corrida_id: int, centro_id: int) -> dict:
    if not table_exists(conn, "simulador_tesoreria_plan_compras_mise"):
        return {"escenarios": 0, "omitido": "Falta tesoreria del plan MISE."}
    ensure_schema(conn)
    conn.execute(
        "DELETE FROM simulador_escenarios_mise_decision WHERE corrida_id=? AND centro_id=?",
        (corrida_id, centro_id),
    )
    months = conn.execute(
        """
        SELECT mes, margen_original, coste_plan_mise
        FROM simulador_tesoreria_plan_compras_mise
        WHERE corrida_id=? AND centro_id=?
        ORDER BY mes
        """,
        (corrida_id, centro_id),
    ).fetchall()
    inserted = 0
    for month in months:
        margen_original = float(month["margen_original"] or 0)
        coste_plan = float(month["coste_plan_mise"] or 0)
        for scenario in SCENARIOS:
            coste = round(coste_plan * float(scenario["cost_factor"]), 2)
            ahorro = round(coste_plan - coste, 2)
            margen = round(margen_original - coste, 2)
            cobertura = round(float(scenario["service_factor"]) * 100, 1)
            verdict, decision, condition = veredicto(margen, cobertura, scenario["risk"])
            conn.execute(
                """
                INSERT INTO simulador_escenarios_mise_decision
                (corrida_id, centro_id, mes, codigo, nombre, descripcion,
                 margen_original, coste_plan_original, coste_escenario,
                 ahorro_vs_plan, margen_escenario, cobertura_operativa_pct,
                 riesgo, veredicto, decision, condicion_control, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    corrida_id,
                    centro_id,
                    month["mes"],
                    scenario["codigo"],
                    scenario["nombre"],
                    scenario["description"],
                    round(margen_original, 2),
                    round(coste_plan, 2),
                    coste,
                    ahorro,
                    margen,
                    cobertura,
                    scenario["risk"],
                    verdict,
                    decision,
                    condition,
                    EMPRESA_ID,
                ),
            )
            inserted += 1
    conn.commit()
    resumen = conn.execute(
        """
        SELECT COUNT(*) escenarios,
               SUM(CASE WHEN veredicto='recomendado' THEN 1 ELSE 0 END) recomendados,
               SUM(CASE WHEN margen_escenario < 0 THEN 1 ELSE 0 END) negativos,
               ROUND(MAX(margen_escenario), 2) mejor_margen
        FROM simulador_escenarios_mise_decision
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

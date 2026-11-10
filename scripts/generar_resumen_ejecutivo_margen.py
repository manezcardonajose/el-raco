# -*- coding: utf-8 -*-
"""Genera un ranking ejecutivo de palancas para proteger margen."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generar resumen ejecutivo de margen.")
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
        CREATE TABLE IF NOT EXISTS simulador_resumen_ejecutivo_margen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            centro_id INTEGER DEFAULT 3,
            prioridad INTEGER DEFAULT 0,
            area TEXT,
            titulo TEXT,
            problema TEXT,
            decision TEXT,
            impacto_estimado REAL DEFAULT 0,
            indicador_control TEXT,
            estado TEXT DEFAULT 'pendiente',
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def add_action(conn: sqlite3.Connection, corrida_id: int, centro_id: int, prioridad: int, area: str, titulo: str, problema: str, decision: str, impacto: float, indicador: str) -> None:
    conn.execute(
        """
        INSERT INTO simulador_resumen_ejecutivo_margen
        (corrida_id, centro_id, prioridad, area, titulo, problema, decision,
         impacto_estimado, indicador_control, estado, empresa_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', ?)
        """,
        (corrida_id, centro_id, prioridad, area, titulo, problema, decision, round(float(impacto or 0), 2), indicador, EMPRESA_ID),
    )


def generar(conn: sqlite3.Connection, corrida_id: int, centro_id: int) -> dict:
    ensure_schema(conn)
    conn.execute(
        "DELETE FROM simulador_resumen_ejecutivo_margen WHERE corrida_id=? AND centro_id=?",
        (corrida_id, centro_id),
    )

    inserted = 0
    if table_exists(conn, "simulador_plan_integral_margen"):
        plan = conn.execute(
            """
            SELECT COUNT(*) planes, SUM(brecha_margen) brecha,
                   SUM(habitaciones_extra) habitaciones_extra,
                   AVG(ocupacion_objetivo_pct) ocupacion_obj,
                   AVG(adr_objetivo) adr_obj
            FROM simulador_plan_integral_margen
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, centro_id),
        ).fetchone()
        if plan and int(plan["planes"] or 0):
            add_action(
                conn,
                corrida_id,
                centro_id,
                1,
                "direccion",
                "Cerrar brecha de meses ambar",
                f"Quedan {int(plan['planes'] or 0)} meses sin colchon suficiente y una brecha de {float(plan['brecha'] or 0):.2f} EUR.",
                f"Ejecutar plan integral: captar {int(plan['habitaciones_extra'] or 0)} habitaciones extra equivalentes, ocupacion objetivo media {float(plan['ocupacion_obj'] or 0):.1f}% y ADR medio {float(plan['adr_obj'] or 0):.2f} EUR.",
                float(plan["brecha"] or 0),
                "Pickup semanal, ocupacion, ADR neto, TPV/pax y margen proyectado.",
            )
            inserted += 1

    if table_exists(conn, "simulador_recomendaciones_cocina_margen"):
        cocina = conn.execute(
            """
            SELECT COUNT(*) propuestas, SUM(ahorro_estimado) ahorro,
                   SUM(CASE WHEN prioridad='critica' THEN 1 ELSE 0 END) criticas
            FROM simulador_recomendaciones_cocina_margen
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, centro_id),
        ).fetchone()
        top = conn.execute(
            """
            SELECT plato, ahorro_estimado, decision_propuesta
            FROM simulador_recomendaciones_cocina_margen
            WHERE corrida_id=? AND centro_id=?
            ORDER BY CASE prioridad WHEN 'critica' THEN 1 WHEN 'alta' THEN 2 ELSE 3 END,
                     ahorro_estimado DESC
            LIMIT 1
            """,
            (corrida_id, centro_id),
        ).fetchone()
        if cocina and int(cocina["propuestas"] or 0):
            add_action(
                conn,
                corrida_id,
                centro_id,
                2,
                "cocina",
                "Reducir merma y coste por plato",
                f"Hay {int(cocina['propuestas'] or 0)} propuestas, {int(cocina['criticas'] or 0)} criticas. Plato principal: {top['plato'] if top else '-'}",
                top["decision_propuesta"] if top else "Aplicar propuestas de cocina por prioridad.",
                float(cocina["ahorro"] or 0),
                "Coste/racion, sobrante, merma al cierre, quejas y reposicion.",
            )
            inserted += 1

    if table_exists(conn, "simulador_stoploss_semanal"):
        stop = conn.execute(
            """
            SELECT COUNT(*) semanas,
                   SUM(CASE WHEN estado='rojo' THEN 1 ELSE 0 END) rojas,
                   SUM(CASE WHEN estado='ambar' THEN 1 ELSE 0 END) ambar
            FROM simulador_stoploss_semanal
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, centro_id),
        ).fetchone()
        if stop and int(stop["rojas"] or 0):
            add_action(
                conn,
                corrida_id,
                centro_id,
                3,
                "control",
                "Activar stop-loss semanal",
                f"Hay {int(stop['rojas'] or 0)} semanas rojas y {int(stop['ambar'] or 0)} ambar.",
                "No esperar al cierre mensual: si dos semanas fallan ocupacion, ADR, TPV/pax o margen, rehacer tarifas/cupos/compras.",
                0,
                "Semanas rojas, incumplimientos, margen semanal y pickup.",
            )
            inserted += 1

    if table_exists(conn, "simulador_decision_financiera_mensual"):
        labor = conn.execute(
            """
            SELECT COUNT(*) meses,
                   SUM(CASE WHEN indicador_clave LIKE '%laboral 5%' OR indicador_clave LIKE '%laboral 4%' THEN 1 ELSE 0 END) tension
            FROM simulador_decision_financiera_mensual
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, centro_id),
        ).fetchone()
        if labor and int(labor["tension"] or 0):
            add_action(
                conn,
                corrida_id,
                centro_id,
                4,
                "rrhh",
                "Cruzar margen con coste laboral",
                f"{int(labor['tension'] or 0)} meses muestran coste laboral tensionado sobre ingresos.",
                "Revisar turnos, descansos, cobertura real y absentismo antes de ampliar apertura o servicios.",
                0,
                "Coste laboral sobre ingresos, fichajes, extras, ausencias y quejas.",
            )
            inserted += 1

    if table_exists(conn, "simulador_auditoria_cerebro_margen_resumen"):
        audit = conn.execute(
            """
            SELECT score, lectura
            FROM simulador_auditoria_cerebro_margen_resumen
            WHERE corrida_id=? AND centro_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (corrida_id, centro_id),
        ).fetchone()
        if audit:
            add_action(
                conn,
                corrida_id,
                centro_id,
                5,
                "calidad",
                "Validar fiabilidad antes de decidir",
                f"Score cerebro de margen: {float(audit['score'] or 0):.0f}.",
                audit["lectura"],
                float(audit["score"] or 0),
                "Score auditoria, trazabilidad, calidad y prueba de rutas.",
            )
            inserted += 1

    conn.commit()
    resumen = conn.execute(
        """
        SELECT COUNT(*) acciones,
               ROUND(SUM(CASE WHEN area IN ('direccion','cocina') THEN impacto_estimado ELSE 0 END), 2) impacto_estimado
        FROM simulador_resumen_ejecutivo_margen
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

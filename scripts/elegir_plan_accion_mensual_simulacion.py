# -*- coding: utf-8 -*-
"""Elige la mejor accion mensual simulada por mes.

No modifica resultados reales. Crea una capa de decision para direccion:
que accion probar, cuanto mejora y si aun no basta para salvar el mes.
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
    parser = argparse.ArgumentParser(description="Elegir plan mensual de accion de la simulacion.")
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
        CREATE TABLE IF NOT EXISTS simulador_plan_accion_mensual_elegido (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            centro_id INTEGER DEFAULT 3,
            mes TEXT,
            estado_mes TEXT,
            margen_original REAL DEFAULT 0,
            accion_elegida TEXT,
            descripcion TEXT,
            margen_estimado REAL DEFAULT 0,
            mejora_margen REAL DEFAULT 0,
            riesgo TEXT,
            veredicto TEXT,
            decision TEXT,
            condicion_control TEXT,
            prioridad TEXT,
            accion_refuerzo TEXT,
            margen_con_refuerzo REAL DEFAULT 0,
            brecha_restante REAL DEFAULT 0,
            lectura_refuerzo TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, mes)
        )
        """
    )
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(simulador_plan_accion_mensual_elegido)")}
    extras = {
        "accion_refuerzo": "TEXT",
        "margen_con_refuerzo": "REAL DEFAULT 0",
        "brecha_restante": "REAL DEFAULT 0",
        "lectura_refuerzo": "TEXT",
    }
    for name, ddl in extras.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE simulador_plan_accion_mensual_elegido ADD COLUMN {name} {ddl}")


def prioridad(veredicto: str, margen_original: float, margen_estimado: float) -> str:
    if margen_original < 0 and margen_estimado < 0:
        return "critica"
    if margen_original < 0 and margen_estimado >= 0:
        return "alta"
    if veredicto == "probar_controlado":
        return "media"
    return "info"


def decision_text(row: sqlite3.Row, original: float) -> str:
    accion = row["accion"]
    estimado = float(row["margen_estimado"] or 0)
    mejora = float(row["mejora_margen"] or 0)
    if estimado >= 0 and original < 0:
        return "Probar como piloto: convierte el mes en positivo si se controla el riesgo."
    if mejora > 0 and original < 0:
        return "Probar solo como parte de un plan combinado: mejora, pero no salva el mes."
    if accion == "plan_combinado_prudente":
        return "Usar como escenario base para direccion: toca varias palancas sin depender de una sola."
    return "Mantener como referencia; no aplicar sin nueva simulacion."


def refuerzo_text(original: float, principal: sqlite3.Row, acciones: list[sqlite3.Row]) -> dict:
    estimado = float(principal["margen_estimado"] or 0)
    if original >= 0:
        return {
            "accion_refuerzo": "",
            "margen_con_refuerzo": estimado,
            "brecha_restante": 0.0,
            "lectura_refuerzo": "El mes ya es rentable. Usar el plan como mejora controlada, no como urgencia.",
        }
    if estimado >= 0:
        return {
            "accion_refuerzo": "",
            "margen_con_refuerzo": estimado,
            "brecha_restante": 0.0,
            "lectura_refuerzo": "La accion principal podria salvar el mes. No sumar mas cambios hasta medir el piloto.",
        }
    if principal["accion"] == "plan_combinado_prudente":
        return {
            "accion_refuerzo": "",
            "margen_con_refuerzo": estimado,
            "brecha_restante": round(abs(min(estimado, 0.0)), 2),
            "lectura_refuerzo": "El plan combinado ya agrupa las palancas prudentes. Si no salva el mes, el problema es estructural: revisar modelo, costes fijos, apertura/cierre, cupos o inversion.",
        }

    acumulado = estimado
    refuerzos: list[str] = []
    usados = {principal["accion"]}
    for accion in acciones:
        if accion["accion"] in usados:
            continue
        mejora = float(accion["mejora_margen"] or 0)
        if mejora <= 0:
            continue
        refuerzos.append(str(accion["accion"]))
        acumulado += mejora
        usados.add(accion["accion"])
        if acumulado >= 0:
            break

    brecha = min(acumulado, 0.0)
    if acumulado >= 0:
        lectura = "La accion principal no basta, pero combinada con refuerzos podria salvar el mes."
    elif refuerzos:
        lectura = "Ni combinando las palancas simuladas se salva del todo: revisar modelo, costes fijos, apertura/cierre, cupos o inversion."
    else:
        lectura = "No hay refuerzos simulados suficientes. Hace falta replantear el escenario."
    return {
        "accion_refuerzo": ", ".join(refuerzos),
        "margen_con_refuerzo": round(acumulado, 2),
        "brecha_restante": round(abs(brecha), 2),
        "lectura_refuerzo": lectura,
    }


def elegir(conn: sqlite3.Connection, corrida_id: int, centro_id: int) -> dict:
    if not table_exists(conn, "simulador_finanzas_acciones_mensuales"):
        return {"planes": 0, "omitido": "No hay acciones financieras mensuales."}
    ensure_schema(conn)
    conn.execute(
        "DELETE FROM simulador_plan_accion_mensual_elegido WHERE corrida_id=? AND centro_id=?",
        (corrida_id, centro_id),
    )
    meses = conn.execute(
        """
        SELECT rm.mes, rm.margen,
               a.accion, a.descripcion, a.margen_estimado, a.mejora_margen,
               a.riesgo, a.veredicto, a.condicion_control
        FROM simulador_resultado_mensual rm
        JOIN simulador_finanzas_acciones_mensuales a
          ON a.corrida_id=rm.corrida_id
         AND a.centro_id=rm.centro_id
         AND a.mes=rm.mes
        WHERE rm.corrida_id=? AND rm.centro_id=?
        ORDER BY rm.mes,
                 CASE a.veredicto WHEN 'probar_controlado' THEN 1 WHEN 'mejora_pero_no_basta' THEN 2 ELSE 3 END,
                 a.mejora_margen DESC
        """,
        (corrida_id, centro_id),
    ).fetchall()
    by_month: dict[str, sqlite3.Row] = {}
    actions_by_month: dict[str, list[sqlite3.Row]] = {}
    for row in meses:
        by_month.setdefault(row["mes"], row)
        actions_by_month.setdefault(row["mes"], []).append(row)

    inserted = 0
    for mes, row in by_month.items():
        original = float(row["margen"] or 0)
        estimado = float(row["margen_estimado"] or 0)
        estado_mes = "perdida" if original < 0 else "rentable"
        prio = prioridad(row["veredicto"], original, estimado)
        refuerzo = refuerzo_text(original, row, actions_by_month.get(mes, []))
        conn.execute(
            """
            INSERT INTO simulador_plan_accion_mensual_elegido
            (corrida_id, centro_id, mes, estado_mes, margen_original, accion_elegida,
             descripcion, margen_estimado, mejora_margen, riesgo, veredicto,
             decision, condicion_control, prioridad, accion_refuerzo,
             margen_con_refuerzo, brecha_restante, lectura_refuerzo, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corrida_id,
                centro_id,
                mes,
                estado_mes,
                round(original, 2),
                row["accion"],
                row["descripcion"],
                round(estimado, 2),
                round(float(row["mejora_margen"] or 0), 2),
                row["riesgo"],
                row["veredicto"],
                decision_text(row, original),
                row["condicion_control"],
                prio,
                refuerzo["accion_refuerzo"],
                refuerzo["margen_con_refuerzo"],
                refuerzo["brecha_restante"],
                refuerzo["lectura_refuerzo"],
                EMPRESA_ID,
            ),
        )
        inserted += 1
    conn.commit()
    return {"planes": inserted}


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        corrida_id = args.corrida_id or latest_corrida(conn)
        if not corrida_id:
            raise SystemExit("No hay corrida para elegir plan.")
        print(elegir(conn, corrida_id, args.centro_id))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

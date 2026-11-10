# -*- coding: utf-8 -*-
"""Simula rescates para meses que no salva cocina/compras.

Parte del mejor escenario MISE por mes y prueba palancas comerciales,
operativas y de apertura. No modifica reservas, costes reales ni pedidos.
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
    parser = argparse.ArgumentParser(description="Simular rescate de meses criticos.")
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


def capacidad_habitaciones(conn: sqlite3.Connection, centro_id: int) -> int:
    if not table_exists(conn, "habitaciones_recepcion"):
        return 160
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(habitaciones_recepcion)")}
    activa = "AND COALESCE(activa,1)=1" if "activa" in cols else "AND COALESCE(activo,1)=1" if "activo" in cols else ""
    row = conn.execute(
        f"SELECT COUNT(*) total FROM habitaciones_recepcion WHERE empresa_id=? AND centro_id=? {activa}",
        (EMPRESA_ID, centro_id),
    ).fetchone()
    return int(row["total"] or 0) or 160


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_rescate_meses_criticos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            centro_id INTEGER DEFAULT 3,
            mes TEXT NOT NULL,
            accion TEXT NOT NULL,
            descripcion TEXT,
            margen_base_mise REAL DEFAULT 0,
            ingreso_delta REAL DEFAULT 0,
            coste_delta REAL DEFAULT 0,
            ahorro_delta REAL DEFAULT 0,
            margen_estimado REAL DEFAULT 0,
            mejora_vs_base REAL DEFAULT 0,
            ocupacion_objetivo_pct REAL DEFAULT 0,
            adr_objetivo REAL DEFAULT 0,
            tpv_pax_objetivo REAL DEFAULT 0,
            riesgo TEXT,
            veredicto TEXT,
            decision TEXT,
            condicion_control TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, mes, accion)
        )
        """
    )


def verdict(margen: float, riesgo: str) -> tuple[str, str, str]:
    if margen >= 40000 and riesgo != "alto":
        return (
            "recomendado",
            "Puede salvar el mes con margen de seguridad razonable.",
            "Aplicar por semanas y medir pickup, ADR neto, quejas y coste variable.",
        )
    if margen >= 0:
        return (
            "piloto_controlado",
            "Salva el mes, pero con poco colchon.",
            "No abrir cupos masivos; validar antes de comprometer compras y plantilla.",
        )
    return (
        "no_salva",
        "No salva el mes. Hace falta cambio mas fuerte de modelo, cierre parcial o financiacion.",
        "No aprobar como plan principal.",
    )


def scenario_rows(row: sqlite3.Row, capacidad: int) -> list[dict]:
    dias = max(int(row["dias_simulados"] or 1), 1)
    habs = float(row["habitaciones_vendidas"] or 0)
    pax = float(row["pax"] or 0)
    ingresos_hab = float(row["ingresos_hab"] or 0)
    ingresos_tpv = float(row["ingresos_tpv"] or 0)
    coste_laboral = float(row["coste_laboral"] or 0)
    coste_fijo = float(row["coste_fijo"] or 0)
    margen_base = float(row["margen_base_mise"] or 0)
    adr = ingresos_hab / max(habs, 1)
    tpv_pax = ingresos_tpv / max(pax, 1)
    total_rooms = capacidad * dias
    scenarios = [
        {
            "accion": "rescate_comercial_85",
            "descripcion": "Subir ocupacion objetivo al 85% con venta directa, operadores controlados y tarifa neta minima.",
            "occ": 0.85,
            "adr_factor": 0.96,
            "tpv_add": 1.5,
            "labor_save": 0.00,
            "fixed_save": 0.00,
            "risk": "alto",
        },
        {
            "accion": "adr_y_cupos_rentables",
            "descripcion": "Mantener ocupacion, subir ADR neto 8% y cerrar cupos/canales que no cubren coste completo.",
            "occ": habs / max(total_rooms, 1),
            "adr_factor": 1.08,
            "tpv_add": 0.8,
            "labor_save": 0.00,
            "fixed_save": 0.00,
            "risk": "medio",
        },
        {
            "accion": "tpv_y_experiencias",
            "descripcion": "Aumentar TPV por pax con bar piscina, cocktails, snacks, cafe y experiencias sin bajar satisfaccion.",
            "occ": habs / max(total_rooms, 1),
            "adr_factor": 1.00,
            "tpv_add": 4.0,
            "labor_save": 0.00,
            "fixed_save": 0.00,
            "risk": "bajo",
        },
        {
            "accion": "redimensionar_apertura",
            "descripcion": "Cerrar plantas/servicios no rentables y adaptar refuerzos a ocupacion real sin tocar seguridad.",
            "occ": habs / max(total_rooms, 1),
            "adr_factor": 1.00,
            "tpv_add": 0.0,
            "labor_save": 0.12,
            "fixed_save": 0.10,
            "risk": "medio",
        },
        {
            "accion": "rescate_integral",
            "descripcion": "Combinar ocupacion al 78%, ADR +5%, TPV +2.5 EUR/pax y redimensionamiento de estructura.",
            "occ": 0.78,
            "adr_factor": 1.05,
            "tpv_add": 2.5,
            "labor_save": 0.08,
            "fixed_save": 0.06,
            "risk": "medio",
        },
        {
            "accion": "cierre_parcial_controlado",
            "descripcion": "Reducir apertura parcial o servicios deficitarios y operar solo demanda rentable.",
            "occ": habs / max(total_rooms, 1),
            "adr_factor": 1.03,
            "tpv_add": 1.0,
            "labor_save": 0.18,
            "fixed_save": 0.14,
            "risk": "alto",
        },
    ]
    output: list[dict] = []
    for sc in scenarios:
        target_habs = max(habs, total_rooms * float(sc["occ"]))
        extra_habs = max(0.0, target_habs - habs)
        adr_target = max(adr * float(sc["adr_factor"]), adr)
        ingreso_hab_delta = (extra_habs * adr_target) + (habs * max(adr_target - adr, 0))
        pax_extra = extra_habs * (pax / max(habs, 1))
        tpv_delta = (pax + pax_extra) * float(sc["tpv_add"])
        variable_cost = extra_habs * 22 + max(tpv_delta, 0) * 0.30
        ahorro = coste_laboral * float(sc["labor_save"]) + coste_fijo * float(sc["fixed_save"])
        ingreso_delta = ingreso_hab_delta + tpv_delta
        coste_delta = variable_cost
        margen = margen_base + ingreso_delta - coste_delta + ahorro
        ver, decision, condition = verdict(margen, sc["risk"])
        output.append({
            "accion": sc["accion"],
            "descripcion": sc["descripcion"],
            "margen_base_mise": round(margen_base, 2),
            "ingreso_delta": round(ingreso_delta, 2),
            "coste_delta": round(coste_delta, 2),
            "ahorro_delta": round(ahorro, 2),
            "margen_estimado": round(margen, 2),
            "mejora_vs_base": round(margen - margen_base, 2),
            "ocupacion_objetivo_pct": round((target_habs / max(total_rooms, 1)) * 100, 1),
            "adr_objetivo": round(adr_target, 2),
            "tpv_pax_objetivo": round(tpv_pax + float(sc["tpv_add"]), 2),
            "riesgo": sc["risk"],
            "veredicto": ver,
            "decision": decision,
            "condicion_control": condition,
        })
    return output


def generar(conn: sqlite3.Connection, corrida_id: int, centro_id: int) -> dict:
    ensure_schema(conn)
    if not table_exists(conn, "simulador_escenarios_mise_decision"):
        return {"escenarios": 0, "omitido": "Faltan escenarios MISE."}
    capacidad = capacidad_habitaciones(conn, centro_id)
    conn.execute(
        "DELETE FROM simulador_rescate_meses_criticos WHERE corrida_id=? AND centro_id=?",
        (corrida_id, centro_id),
    )
    rows = conn.execute(
        """
        WITH best_mise AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY mes
                ORDER BY CASE veredicto WHEN 'recomendado' THEN 1 WHEN 'piloto_controlado' THEN 2 WHEN 'aplicar_con_cuidado' THEN 3 ELSE 4 END,
                         margen_escenario DESC
            ) rn
            FROM simulador_escenarios_mise_decision
            WHERE corrida_id=? AND centro_id=?
        )
        SELECT rm.*, bm.margen_escenario AS margen_base_mise,
               COALESCE(SUM(k.habitaciones_ocupadas), 0) habitaciones_vendidas,
               COALESCE(SUM(k.pax), 0) pax,
               COALESCE(SUM(k.ingresos_habitaciones), 0) ingresos_hab,
               COALESCE(SUM(k.ingresos_tpv), 0) ingresos_tpv
        FROM simulador_resultado_mensual rm
        JOIN best_mise bm ON bm.mes=rm.mes AND bm.rn=1
        LEFT JOIN simulador_vida_kpis_diarios k
          ON k.corrida_id=rm.corrida_id
         AND k.centro_id=rm.centro_id
         AND substr(k.fecha,1,7)=rm.mes
        WHERE rm.corrida_id=? AND rm.centro_id=?
          AND bm.margen_escenario < 0
        GROUP BY rm.id
        ORDER BY rm.mes
        """,
        (corrida_id, centro_id, corrida_id, centro_id),
    ).fetchall()
    inserted = 0
    for row in rows:
        for sc in scenario_rows(row, capacidad):
            conn.execute(
                """
                INSERT INTO simulador_rescate_meses_criticos
                (corrida_id, centro_id, mes, accion, descripcion, margen_base_mise,
                 ingreso_delta, coste_delta, ahorro_delta, margen_estimado,
                 mejora_vs_base, ocupacion_objetivo_pct, adr_objetivo,
                 tpv_pax_objetivo, riesgo, veredicto, decision,
                 condicion_control, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    corrida_id,
                    centro_id,
                    row["mes"],
                    sc["accion"],
                    sc["descripcion"],
                    sc["margen_base_mise"],
                    sc["ingreso_delta"],
                    sc["coste_delta"],
                    sc["ahorro_delta"],
                    sc["margen_estimado"],
                    sc["mejora_vs_base"],
                    sc["ocupacion_objetivo_pct"],
                    sc["adr_objetivo"],
                    sc["tpv_pax_objetivo"],
                    sc["riesgo"],
                    sc["veredicto"],
                    sc["decision"],
                    sc["condicion_control"],
                    EMPRESA_ID,
                ),
            )
            inserted += 1
    conn.commit()
    resumen = conn.execute(
        """
        SELECT COUNT(*) escenarios,
               COUNT(DISTINCT mes) meses,
               SUM(CASE WHEN veredicto IN ('recomendado','piloto_controlado') THEN 1 ELSE 0 END) salvables,
               ROUND(MAX(margen_estimado), 2) mejor_margen
        FROM simulador_rescate_meses_criticos
        WHERE corrida_id=? AND centro_id=?
        """,
        (corrida_id, centro_id),
    ).fetchone()
    return dict(resumen) | {"insertados": inserted, "capacidad_habitaciones": capacidad}


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

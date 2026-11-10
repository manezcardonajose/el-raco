# -*- coding: utf-8 -*-
"""Genera plan integral para meses que siguen en riesgo tras cocina."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generar plan integral de margen por mes.")
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
    activa = "AND COALESCE(activo,1)=1" if "activo" in cols else "AND COALESCE(activa,1)=1" if "activa" in cols else ""
    row = conn.execute(
        f"SELECT COUNT(*) total FROM habitaciones_recepcion WHERE empresa_id=? AND centro_id=? {activa}",
        (EMPRESA_ID, centro_id),
    ).fetchone()
    return int(row["total"] or 0) or 160


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_plan_integral_margen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            centro_id INTEGER DEFAULT 3,
            mes TEXT NOT NULL,
            estado_origen TEXT,
            margen_actual REAL DEFAULT 0,
            margen_objetivo REAL DEFAULT 0,
            brecha_margen REAL DEFAULT 0,
            ahorro_cocina REAL DEFAULT 0,
            ocupacion_actual_pct REAL DEFAULT 0,
            ocupacion_objetivo_pct REAL DEFAULT 0,
            habitaciones_extra INTEGER DEFAULT 0,
            adr_actual REAL DEFAULT 0,
            adr_objetivo REAL DEFAULT 0,
            tpv_pax_actual REAL DEFAULT 0,
            tpv_pax_objetivo REAL DEFAULT 0,
            compra_maxima REAL DEFAULT 0,
            palancas TEXT,
            decision TEXT,
            condicion_control TEXT,
            stop_loss TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, mes)
        )
        """
    )


def month_stats(conn: sqlite3.Connection, corrida_id: int, centro_id: int, mes: str) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) dias,
               SUM(habitaciones_ocupadas) rooms,
               SUM(pax) pax,
               SUM(ingresos_habitaciones) ing_hab,
               SUM(ingresos_tpv) ing_tpv,
               SUM(coste_compras_estimado) compras,
               SUM(quejas) quejas
        FROM simulador_vida_kpis_diarios
        WHERE corrida_id=? AND centro_id=? AND substr(fecha,1,7)=?
        """,
        (corrida_id, centro_id, mes),
    ).fetchone()
    dias = int(row["dias"] or 0)
    rooms = float(row["rooms"] or 0)
    pax = float(row["pax"] or 0)
    ing_hab = float(row["ing_hab"] or 0)
    ing_tpv = float(row["ing_tpv"] or 0)
    return {
        "dias": dias,
        "rooms": rooms,
        "pax": pax,
        "adr": ing_hab / max(rooms, 1),
        "tpv_pax": ing_tpv / max(pax, 1),
        "compras": float(row["compras"] or 0),
        "quejas": int(row["quejas"] or 0),
    }


def generar(conn: sqlite3.Connection, corrida_id: int, centro_id: int) -> dict:
    ensure_schema(conn)
    required = {"simulador_impacto_cocina_mensual", "simulador_vida_kpis_diarios"}
    if not all(table_exists(conn, table) for table in required):
        return {"planes": 0, "omitido": "Faltan datos de impacto cocina o KPIs diarios."}

    capacidad = capacidad_habitaciones(conn, centro_id)
    conn.execute(
        "DELETE FROM simulador_plan_integral_margen WHERE corrida_id=? AND centro_id=?",
        (corrida_id, centro_id),
    )
    rows = conn.execute(
        """
        SELECT ic.*, df.ingresos_total
        FROM simulador_impacto_cocina_mensual ic
        LEFT JOIN simulador_decision_financiera_mensual df
          ON df.corrida_id=ic.corrida_id AND df.centro_id=ic.centro_id AND df.mes=ic.mes
        WHERE ic.corrida_id=? AND ic.centro_id=? AND ic.estado_despues!='verde'
        ORDER BY ic.mes
        """,
        (corrida_id, centro_id),
    ).fetchall()

    inserted = 0
    for row in rows:
        mes = row["mes"]
        stats = month_stats(conn, corrida_id, centro_id, mes)
        dias = max(stats["dias"], 1)
        total_rooms = capacidad * dias
        occ_actual = stats["rooms"] / max(total_rooms, 1) * 100
        ingresos = float(row["ingresos_total"] or 0)
        margen_obj = max(30000.0, ingresos * 0.06)
        margen_actual = float(row["margen_despues"] or 0)
        brecha = max(0.0, margen_obj - margen_actual)

        guard = conn.execute(
            """
            SELECT *
            FROM simulador_guardarrailes_no_arruinarse
            WHERE corrida_id=? AND centro_id=? AND mes=?
            LIMIT 1
            """,
            (corrida_id, centro_id, mes),
        ).fetchone() if table_exists(conn, "simulador_guardarrailes_no_arruinarse") else None

        adr_actual = stats["adr"]
        tpv_actual = stats["tpv_pax"]
        adr_obj = max(float(guard["adr_minimo"] or 0) if guard else 0, adr_actual * 1.03, adr_actual + 3)
        tpv_obj = max(float(guard["tpv_pax_minimo"] or 0) if guard else 0, tpv_actual + 1.5)
        margen_room = max(adr_obj - 22 + (tpv_obj - tpv_actual) * 1.8, 35)
        rooms_extra = int((brecha / margen_room) + 0.999) if brecha else 0
        occ_obj = min(92.0, max(float(guard["ocupacion_minima_pct"] or 0) if guard else 0, (stats["rooms"] + rooms_extra) / max(total_rooms, 1) * 100))
        compra_max = float(guard["compra_maxima"] or 0) if guard else stats["compras"] * 1.02

        palancas = (
            f"Cocina: aplicar ahorro {float(row['ahorro_estimado'] or 0):.2f} EUR. "
            f"Ventas: captar {rooms_extra} habitaciones adicionales netas o equivalente en ADR. "
            f"Revenue: ADR minimo {adr_obj:.2f} EUR y cerrar cupos que no cubran coste. "
            f"TPV: subir TPV/pax a {tpv_obj:.2f} EUR con bar, snacks y experiencias. "
            f"Compras: no superar {compra_max:.2f} EUR."
        )
        if brecha <= 0:
            decision = "Mantener mejora de cocina y vigilar semana a semana."
        elif rooms_extra <= capacidad * 3:
            decision = "Plan recuperable con venta directa, control de cupos y ejecucion estricta de cocina."
        else:
            decision = "No basta una accion: necesita plan comercial fuerte, cupos, compras y posiblemente estructura."
        condicion = (
            "Revisar cada semana pickup, ocupacion, ADR neto, TPV/pax, compras, merma y quejas. "
            "No comprometer mas compras si el pickup no acompana."
        )
        stop_loss = (
            "Parar campana o rehacer tarifas si dos semanas seguidas quedan por debajo de ocupacion/ADR objetivo "
            "o si margen proyectado cae bajo el colchon."
        )
        conn.execute(
            """
            INSERT INTO simulador_plan_integral_margen
            (corrida_id, centro_id, mes, estado_origen, margen_actual,
             margen_objetivo, brecha_margen, ahorro_cocina,
             ocupacion_actual_pct, ocupacion_objetivo_pct, habitaciones_extra,
             adr_actual, adr_objetivo, tpv_pax_actual, tpv_pax_objetivo,
             compra_maxima, palancas, decision, condicion_control,
             stop_loss, empresa_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corrida_id,
                centro_id,
                mes,
                row["estado_despues"],
                round(margen_actual, 2),
                round(margen_obj, 2),
                round(brecha, 2),
                row["ahorro_estimado"],
                round(occ_actual, 1),
                round(occ_obj, 1),
                rooms_extra,
                round(adr_actual, 2),
                round(adr_obj, 2),
                round(tpv_actual, 2),
                round(tpv_obj, 2),
                round(compra_max, 2),
                palancas,
                decision,
                condicion,
                stop_loss,
                EMPRESA_ID,
            ),
        )
        inserted += 1

    conn.commit()
    resumen = conn.execute(
        """
        SELECT COUNT(*) planes,
               ROUND(SUM(brecha_margen), 2) brecha_total,
               SUM(habitaciones_extra) habitaciones_extra,
               ROUND(AVG(ocupacion_objetivo_pct), 1) ocupacion_objetivo_media,
               ROUND(AVG(adr_objetivo), 2) adr_objetivo_medio
        FROM simulador_plan_integral_margen
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

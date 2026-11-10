# -*- coding: utf-8 -*-
"""Genera control semanal de stop-loss para meses ajustados.

Sirve para que direccion no espere al cierre mensual: compara cada semana
contra ocupacion, ADR, TPV/pax, compras y margen minimo.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generar stop-loss semanal de simulacion.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--centro-id", type=int, default=CENTRO_ID)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


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
        CREATE TABLE IF NOT EXISTS simulador_stoploss_semanal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL,
            centro_id INTEGER DEFAULT 3,
            mes TEXT NOT NULL,
            semana_inicio TEXT NOT NULL,
            dias INTEGER DEFAULT 0,
            habitaciones_ocupadas INTEGER DEFAULT 0,
            pax INTEGER DEFAULT 0,
            ocupacion_pct REAL DEFAULT 0,
            ocupacion_minima_pct REAL DEFAULT 0,
            adr REAL DEFAULT 0,
            adr_minimo REAL DEFAULT 0,
            tpv_pax REAL DEFAULT 0,
            tpv_pax_minimo REAL DEFAULT 0,
            compras REAL DEFAULT 0,
            compra_maxima REAL DEFAULT 0,
            margen REAL DEFAULT 0,
            margen_minimo REAL DEFAULT 0,
            quejas INTEGER DEFAULT 0,
            incumplimientos INTEGER DEFAULT 0,
            estado TEXT,
            causa TEXT,
            accion TEXT,
            empresa_id INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, centro_id, mes, semana_inicio)
        )
        """
    )


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def week_start(value: str) -> str:
    d = parse_date(value)
    return d.fromisocalendar(d.isocalendar().year, d.isocalendar().week, 1).isoformat()


def status_from_breaches(breaches: list[str], margen: float) -> str:
    if margen < 0 or len(breaches) >= 3:
        return "rojo"
    if breaches:
        return "ambar"
    return "verde"


def generar(conn: sqlite3.Connection, corrida_id: int, centro_id: int) -> dict:
    ensure_schema(conn)
    if not table_exists(conn, "simulador_vida_kpis_diarios") or not table_exists(conn, "simulador_decision_financiera_mensual"):
        return {"semanas": 0, "omitido": "Faltan KPIs diarios o decision financiera mensual."}

    capacidad = capacidad_habitaciones(conn, centro_id)
    conn.execute(
        "DELETE FROM simulador_stoploss_semanal WHERE corrida_id=? AND centro_id=?",
        (corrida_id, centro_id),
    )

    decision_rows = conn.execute(
        """
        SELECT *
        FROM simulador_decision_financiera_mensual
        WHERE corrida_id=? AND centro_id=? AND estado IN ('ambar','rojo')
        ORDER BY mes
        """,
        (corrida_id, centro_id),
    ).fetchall()
    if not decision_rows:
        conn.commit()
        return {"semanas": 0, "insertados": 0}

    guardrails = {
        r["mes"]: r
        for r in conn.execute(
            """
            SELECT *
            FROM simulador_guardarrailes_no_arruinarse
            WHERE corrida_id=? AND centro_id=?
            """,
            (corrida_id, centro_id),
        ).fetchall()
    } if table_exists(conn, "simulador_guardarrailes_no_arruinarse") else {}

    inserted = 0
    for month in decision_rows:
        mes = month["mes"]
        daily = conn.execute(
            """
            SELECT *
            FROM simulador_vida_kpis_diarios
            WHERE corrida_id=? AND centro_id=? AND substr(fecha,1,7)=?
            ORDER BY fecha
            """,
            (corrida_id, centro_id, mes),
        ).fetchall()
        if not daily:
            continue

        weekly: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in daily:
            weekly[week_start(row["fecha"])].append(row)

        total_rooms_month = capacidad * len(daily)
        month_rooms = sum(int(r["habitaciones_ocupadas"] or 0) for r in daily)
        month_pax = sum(int(r["pax"] or 0) for r in daily)
        month_ing_hab = sum(float(r["ingresos_habitaciones"] or 0) for r in daily)
        month_tpv = sum(float(r["ingresos_tpv"] or 0) for r in daily)
        month_compras = sum(float(r["coste_compras_estimado"] or 0) for r in daily)
        month_occ = (month_rooms / max(total_rooms_month, 1)) * 100
        month_adr = month_ing_hab / max(month_rooms, 1)
        month_tpv_pax = month_tpv / max(month_pax, 1)
        weeks_in_month = max(len(weekly), 1)

        guard = guardrails.get(mes)
        if guard:
            occ_min = float(guard["ocupacion_minima_pct"] or 0)
            adr_min = float(guard["adr_minimo"] or 0)
            tpv_min = float(guard["tpv_pax_minimo"] or 0)
            compra_max_week = float(guard["compra_maxima"] or 0) / weeks_in_month
            margin_min_week = float(guard["margen_seguridad_objetivo"] or 0) / weeks_in_month
        else:
            occ_min = min(85.0, month_occ + 3.0)
            adr_min = month_adr
            tpv_min = month_tpv_pax
            compra_max_week = (month_compras * 1.02) / weeks_in_month
            margin_min_week = max(7500.0, float(month["ingresos_total"] or 0) * 0.06 / weeks_in_month)

        for inicio, rows in sorted(weekly.items()):
            dias = len(rows)
            rooms = sum(int(r["habitaciones_ocupadas"] or 0) for r in rows)
            pax = sum(int(r["pax"] or 0) for r in rows)
            ing_hab = sum(float(r["ingresos_habitaciones"] or 0) for r in rows)
            tpv = sum(float(r["ingresos_tpv"] or 0) for r in rows)
            compras = sum(float(r["coste_compras_estimado"] or 0) for r in rows)
            margen = sum(float(r["margen_estimado"] or 0) for r in rows)
            quejas = sum(int(r["quejas"] or 0) for r in rows)
            occ = (rooms / max(capacidad * dias, 1)) * 100
            adr = ing_hab / max(rooms, 1)
            tpv_pax = tpv / max(pax, 1)

            breaches = []
            if occ < occ_min:
                breaches.append("ocupacion")
            if adr < adr_min:
                breaches.append("ADR")
            if tpv_pax < tpv_min:
                breaches.append("TPV/pax")
            if compras > compra_max_week:
                breaches.append("compras")
            if margen < margin_min_week:
                breaches.append("margen")
            if quejas > 0:
                breaches.append("quejas")

            estado = status_from_breaches(breaches, margen)
            if not breaches:
                causa = "Semana dentro de limites."
                accion = "Mantener plan semanal y no abrir mas riesgo sin revisar pickup."
            else:
                causa = "Incumple: " + ", ".join(breaches) + "."
                accion = (
                    "Revisar cupos, tarifa neta, compras, turnos, quejas y menu antes de continuar. "
                    "Si se repite dos semanas, activar comite de rescate."
                )

            conn.execute(
                """
                INSERT INTO simulador_stoploss_semanal
                (corrida_id, centro_id, mes, semana_inicio, dias,
                 habitaciones_ocupadas, pax, ocupacion_pct, ocupacion_minima_pct,
                 adr, adr_minimo, tpv_pax, tpv_pax_minimo, compras,
                 compra_maxima, margen, margen_minimo, quejas,
                 incumplimientos, estado, causa, accion, empresa_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    corrida_id,
                    centro_id,
                    mes,
                    inicio,
                    dias,
                    rooms,
                    pax,
                    round(occ, 1),
                    round(occ_min, 1),
                    round(adr, 2),
                    round(adr_min, 2),
                    round(tpv_pax, 2),
                    round(tpv_min, 2),
                    round(compras, 2),
                    round(compra_max_week, 2),
                    round(margen, 2),
                    round(margin_min_week, 2),
                    quejas,
                    len(breaches),
                    estado,
                    causa,
                    accion,
                    EMPRESA_ID,
                ),
            )
            inserted += 1

    conn.commit()
    resumen = conn.execute(
        """
        SELECT COUNT(*) semanas,
               SUM(CASE WHEN estado='verde' THEN 1 ELSE 0 END) verdes,
               SUM(CASE WHEN estado='ambar' THEN 1 ELSE 0 END) ambar,
               SUM(CASE WHEN estado='rojo' THEN 1 ELSE 0 END) rojos,
               ROUND(SUM(margen), 2) margen
        FROM simulador_stoploss_semanal
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

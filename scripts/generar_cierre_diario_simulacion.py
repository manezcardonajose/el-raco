# -*- coding: utf-8 -*-
"""Genera cierre diario operativo de la simulacion.

No modifica datos operativos. Lee KPIs, eventos, RRHH y overbooking para decir
si direccion puede cerrar el dia o debe revisar incidencias antes de avanzar.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"
EMPRESA_ID = 1
CENTRO_ID = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crear cierre diario operativo de simulacion.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--hora-corte", default="01:00", help="Hora a la que se cierra el dia hotelero y se abre el siguiente.")
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def add_column_if_missing(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def normalizar_hora_corte(value: str) -> str:
    raw = (value or "01:00").strip()[:5]
    try:
        datetime.strptime(raw, "%H:%M")
        return raw
    except ValueError:
        return "01:00"


def ventana_operativa(fecha: str, hora_corte: str) -> tuple[str, str]:
    inicio = datetime.strptime(f"{fecha} {hora_corte}", "%Y-%m-%d %H:%M")
    fin = inicio + timedelta(days=1) - timedelta(seconds=1)
    return inicio.strftime("%Y-%m-%d %H:%M:%S"), fin.strftime("%Y-%m-%d %H:%M:%S")


def latest_corrida(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_cierre_diario (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            fecha TEXT,
            hora_corte TEXT DEFAULT '01:00',
            cierre_desde TEXT,
            cierre_hasta TEXT,
            dia_operativo TEXT,
            estado TEXT,
            prioridad TEXT,
            score_riesgo INTEGER DEFAULT 0,
            margen_estimado REAL DEFAULT 0,
            ingresos_total REAL DEFAULT 0,
            coste_total REAL DEFAULT 0,
            coste_fijo REAL DEFAULT 0,
            rrhh_alertas INTEGER DEFAULT 0,
            overbooking_alertas INTEGER DEFAULT 0,
            appcc_alertas INTEGER DEFAULT 0,
            compras_alertas INTEGER DEFAULT 0,
            mantenimiento_alertas INTEGER DEFAULT 0,
            experiencia_alertas INTEGER DEFAULT 0,
            caja_alertas INTEGER DEFAULT 0,
            accion_direccion TEXT,
            bloqueantes_json TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(corrida_id, fecha, centro_id)
        )
        """
    )
    for col in [
        "hora_corte TEXT DEFAULT '01:00'",
        "cierre_desde TEXT",
        "cierre_hasta TEXT",
        "dia_operativo TEXT",
        "coste_fijo REAL DEFAULT 0",
    ]:
        add_column_if_missing(conn, "simulador_cierre_diario", col)


def count_events(conn: sqlite3.Connection, corrida_id: int, fecha: str, modulo: str | None = None, severidades=("critica", "alta")) -> int:
    where = ["corrida_id=?", "fecha_simulada=?", "severidad IN (%s)" % ",".join("?" for _ in severidades)]
    params: list[object] = [corrida_id, fecha, *severidades]
    if modulo:
        where.append("modulo=?")
        params.append(modulo)
    row = conn.execute(
        f"SELECT COUNT(*) total FROM simulador_vida_eventos WHERE {' AND '.join(where)}",
        params,
    ).fetchone()
    return int(row["total"] or 0)


def count_overbooking(conn: sqlite3.Connection, corrida_id: int, fecha: str) -> int:
    if not table_exists(conn, "simulador_overbooking_casos"):
        return 0
    row = conn.execute(
        """
        SELECT COUNT(*) total
        FROM simulador_overbooking_casos
        WHERE corrida_id=? AND fecha_entrada=? AND estado_gestion!='resuelto_simulado'
        """,
        (corrida_id, fecha),
    ).fetchone()
    return int(row["total"] or 0)


def rrhh_day(conn: sqlite3.Connection, corrida_id: int, fecha: str) -> sqlite3.Row | None:
    if not table_exists(conn, "simulador_rrhh_control_diario"):
        return None
    return conn.execute(
        "SELECT * FROM simulador_rrhh_control_diario WHERE corrida_id=? AND fecha=? AND centro_id=?",
        (corrida_id, fecha, CENTRO_ID),
    ).fetchone()


def row_value(row: sqlite3.Row, key: str, default=0):
    return row[key] if key in row.keys() else default


def build_action(bloqueantes: list[dict], margen: float) -> tuple[str, str, str]:
    if margen < 0:
        bloqueantes.append({
            "area": "direccion",
            "prioridad": "alta",
            "tipo": "revision_operativa",
            "motivo": "margen_negativo",
            "dato": round(margen, 2),
        })
    if not bloqueantes:
        return "cerrado", "ok", "Cerrar dia y pasar al siguiente control."
    critical = [
        b for b in bloqueantes
        if b.get("prioridad") == "critica" or b["area"] in {"appcc", "overbooking"}
    ]
    if critical:
        return "bloqueado", "critica", "No cerrar: resolver bloqueantes de seguridad, recepcion o RRHH antes de validar el dia."
    return "revisar", "alta", "Cerrar solo con validacion de responsable y comentario de direccion."


def main() -> None:
    args = parse_args()
    hora_corte = normalizar_hora_corte(args.hora_corte)
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        corrida_id = args.corrida_id or latest_corrida(conn)
        if not corrida_id:
            raise SystemExit("No hay corrida de simulacion.")
        rows = conn.execute(
            "SELECT * FROM simulador_vida_kpis_diarios WHERE corrida_id=? ORDER BY fecha",
            (corrida_id,),
        ).fetchall()
        created = 0
        bloqueados = 0
        for kpi in rows:
            fecha = kpi["fecha"]
            cierre_desde, cierre_hasta = ventana_operativa(fecha, hora_corte)
            rrhh = rrhh_day(conn, corrida_id, fecha)
            rrhh_alertas = 0
            rrhh_bloqueo_nomina = 0
            rrhh_revision = 0
            if rrhh:
                rrhh_bloqueo_nomina = int(rrhh["sin_fichar"] or 0) + int(rrhh["sin_entrada"] or 0) + int(rrhh["sin_salida"] or 0)
                rrhh_revision = int(rrhh["fichaje_sin_turno"] or 0) + int(row_value(rrhh, "salidas_anticipadas", 0) or 0) + int(row_value(rrhh, "extras_pendientes", 0) or 0)
                rrhh_alertas = rrhh_bloqueo_nomina + rrhh_revision
            ob_alertas = count_overbooking(conn, corrida_id, fecha)
            appcc = count_events(conn, corrida_id, fecha, "appcc")
            compras = count_events(conn, corrida_id, fecha, "compras")
            mantenimiento = count_events(conn, corrida_id, fecha, "mantenimiento")
            experiencia = count_events(conn, corrida_id, fecha, "experiencia")
            caja = count_events(conn, corrida_id, fecha, "tpv")
            margen = float(kpi["margen_estimado"] or 0)
            ingresos = float(kpi["ingresos_habitaciones"] or 0) + float(kpi["ingresos_tpv"] or 0)
            coste_fijo = float(row_value(kpi, "coste_fijo_estimado", 0) or 0)
            costes = float(row_value(kpi, "coste_total_estimado", 0) or 0)
            if not costes:
                costes = float(kpi["coste_laboral_estimado"] or 0) + float(kpi["coste_compras_estimado"] or 0) + coste_fijo
            bloqueantes = []
            if rrhh:
                empleados_con_turno = int(rrhh["empleados_con_turno"] or 0)
                ratio_sin_fichar = (float(rrhh["sin_fichar"] or 0) / empleados_con_turno) if empleados_con_turno else 0.0
                if rrhh_bloqueo_nomina:
                    prioridad_rrhh = "critica" if rrhh_bloqueo_nomina >= 10 or ratio_sin_fichar >= 0.25 else "alta"
                    bloqueantes.append({
                        "area": "rrhh",
                        "prioridad": prioridad_rrhh,
                        "tipo": "bloqueo_pre_nomina",
                        "motivo": "fichajes_incompletos",
                        "dato": rrhh_bloqueo_nomina,
                        "ratio_sin_fichar": round(ratio_sin_fichar, 3),
                        "decision": "No lanzar nomina/pre-nomina sin revisar fichajes, ausencias y justificantes.",
                    })
                if rrhh_revision:
                    bloqueantes.append({
                        "area": "rrhh",
                        "prioridad": "alta",
                        "tipo": "revision_operativa",
                        "motivo": "fichajes_fuera_turno_o_salidas",
                        "dato": rrhh_revision,
                        "decision": "Revisar cuadrante y fichajes, pero no mezclarlo con certificados o PRL.",
                    })
            if ob_alertas:
                bloqueantes.append({"area": "overbooking", "prioridad": "critica", "tipo": "bloqueo_servicio", "motivo": "reservas_conflictivas", "dato": ob_alertas})
            if appcc:
                bloqueantes.append({"area": "appcc", "prioridad": "critica", "tipo": "bloqueo_seguridad", "motivo": "no_conformidades", "dato": appcc})
            if compras:
                bloqueantes.append({"area": "compras", "prioridad": "alta", "tipo": "revision_operativa", "motivo": "proveedor_o_pedido", "dato": compras})
            if mantenimiento:
                bloqueantes.append({"area": "mantenimiento", "prioridad": "alta", "tipo": "revision_operativa", "motivo": "averias_altas", "dato": mantenimiento})
            if experiencia:
                bloqueantes.append({"area": "experiencia", "prioridad": "alta", "tipo": "revision_operativa", "motivo": "quejas_altas", "dato": experiencia})
            estado, prioridad, accion = build_action(bloqueantes, margen)
            score = min(100, int(float(kpi["riesgo_operativo"] or 0) + rrhh_alertas * 2 + ob_alertas * 10 + appcc * 8))
            if estado == "bloqueado":
                bloqueados += 1
            conn.execute(
                """
                INSERT INTO simulador_cierre_diario
                (corrida_id, fecha, hora_corte, cierre_desde, cierre_hasta, dia_operativo,
                 estado, prioridad, score_riesgo, margen_estimado,
                 ingresos_total, coste_total, coste_fijo, rrhh_alertas, overbooking_alertas,
                 appcc_alertas, compras_alertas, mantenimiento_alertas,
                 experiencia_alertas, caja_alertas, accion_direccion, bloqueantes_json,
                 empresa_id, centro_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 3)
                ON CONFLICT(corrida_id, fecha, centro_id) DO UPDATE SET
                    hora_corte=excluded.hora_corte,
                    cierre_desde=excluded.cierre_desde,
                    cierre_hasta=excluded.cierre_hasta,
                    dia_operativo=excluded.dia_operativo,
                    estado=excluded.estado, prioridad=excluded.prioridad,
                    score_riesgo=excluded.score_riesgo, margen_estimado=excluded.margen_estimado,
                    ingresos_total=excluded.ingresos_total, coste_total=excluded.coste_total,
                    coste_fijo=excluded.coste_fijo,
                    rrhh_alertas=excluded.rrhh_alertas, overbooking_alertas=excluded.overbooking_alertas,
                    appcc_alertas=excluded.appcc_alertas, compras_alertas=excluded.compras_alertas,
                    mantenimiento_alertas=excluded.mantenimiento_alertas,
                    experiencia_alertas=excluded.experiencia_alertas, caja_alertas=excluded.caja_alertas,
                    accion_direccion=excluded.accion_direccion,
                    bloqueantes_json=excluded.bloqueantes_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    corrida_id, fecha, hora_corte, cierre_desde, cierre_hasta, fecha,
                    estado, prioridad, score, margen, ingresos, costes,
                    coste_fijo, rrhh_alertas, ob_alertas, appcc, compras, mantenimiento, experiencia,
                    caja, accion, json.dumps(bloqueantes, ensure_ascii=False),
                ),
            )
            created += 1
        conn.commit()
        print(f"Cierre diario generado: {created} dias, {bloqueados} bloqueados. Hora corte {hora_corte}.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

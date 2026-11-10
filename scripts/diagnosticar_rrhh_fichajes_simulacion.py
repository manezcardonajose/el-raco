# -*- coding: utf-8 -*-
"""Diagnostico diario RRHH de turnos, fichajes y ausencias.

Lee la simulacion existente y crea una tabla de control. No borra empleados,
turnos ni fichajes.
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
    parser = argparse.ArgumentParser(description="Diagnosticar RRHH/fichajes en simulacion Hotel 1.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--desde", default="")
    parser.add_argument("--hasta", default="")
    parser.add_argument("--corrida-id", type=int, default=0)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def latest_corrida(conn: sqlite3.Connection) -> sqlite3.Row | None:
    if not table_exists(conn, "simulador_vida_corridas"):
        return None
    return conn.execute("SELECT * FROM simulador_vida_corridas ORDER BY id DESC LIMIT 1").fetchone()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_rrhh_control_diario (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            fecha TEXT,
            turnos INTEGER DEFAULT 0,
            empleados_con_turno INTEGER DEFAULT 0,
            fichajes INTEGER DEFAULT 0,
            empleados_fichados INTEGER DEFAULT 0,
            sin_fichar INTEGER DEFAULT 0,
            sin_entrada INTEGER DEFAULT 0,
            sin_salida INTEGER DEFAULT 0,
            fichaje_sin_turno INTEGER DEFAULT 0,
            pausa_sin_reanudar INTEGER DEFAULT 0,
            doble_turno INTEGER DEFAULT 0,
            ausencias_aprobadas INTEGER DEFAULT 0,
            extras_pendientes INTEGER DEFAULT 0,
            minutos_extra_pendientes INTEGER DEFAULT 0,
            salidas_anticipadas INTEGER DEFAULT 0,
            coste_riesgo_estimado REAL DEFAULT 0,
            nivel TEXT,
            lectura TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(corrida_id, fecha, centro_id)
        )
        """
    )


def parse_time(fecha: str, hora: str | None) -> datetime | None:
    if not fecha or not hora:
        return None
    try:
        return datetime.fromisoformat(f"{fecha} {hora}")
    except Exception:
        return None


def fichajes_empleado(conn: sqlite3.Connection, empleado_id: int, fecha: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM fichajes
        WHERE empresa_id=? AND empleado_id=? AND date(fecha_hora)=date(?)
          AND COALESCE(estado, 'activo') NOT IN ('anulado','cancelado','borrado')
        ORDER BY datetime(fecha_hora), id
        """,
        (EMPRESA_ID, empleado_id, fecha),
    ).fetchall()


def has_absence(conn: sqlite3.Connection, empleado_id: int, fecha: str) -> bool:
    if not table_exists(conn, "vacaciones_rrhh"):
        return False
    row = conn.execute(
        """
        SELECT 1 FROM vacaciones_rrhh
        WHERE empresa_id=? AND empleado_id=?
          AND date(fecha_inicio)<=date(?) AND date(fecha_fin)>=date(?)
          AND estado IN ('solicitada','aprobada')
        LIMIT 1
        """,
        (EMPRESA_ID, empleado_id, fecha, fecha),
    ).fetchone()
    return row is not None


def day_range(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def diagnose_day(conn: sqlite3.Connection, corrida_id: int, day: date) -> dict:
    fecha = day.isoformat()
    turnos = conn.execute(
        """
        SELECT *
        FROM turnos_rrhh
        WHERE empresa_id=? AND centro_id=? AND date(fecha)=date(?)
          AND COALESCE(estado, 'planificado') NOT IN ('anulado','cancelado','borrado')
          AND COALESCE(tipo, 'trabajo') NOT IN ('libre','descanso','dia_libre','dia libre')
        """,
        (EMPRESA_ID, CENTRO_ID, fecha),
    ).fetchall()
    empleados_turno = {}
    for turno in turnos:
        empleados_turno.setdefault(turno["empleado_id"], []).append(turno)

    fichajes_rows = conn.execute(
        """
        SELECT *
        FROM fichajes
        WHERE empresa_id=? AND date(fecha_hora)=date(?)
          AND COALESCE(estado, 'activo') NOT IN ('anulado','cancelado','borrado')
        """,
        (EMPRESA_ID, fecha),
    ).fetchall()
    empleados_fichados = {r["empleado_id"] for r in fichajes_rows}

    sin_fichar = sin_entrada = sin_salida = pausa_sin_reanudar = salidas_anticipadas = 0
    minutos_extra = 0
    for empleado_id, emp_turnos in empleados_turno.items():
        rows = fichajes_empleado(conn, empleado_id, fecha)
        if has_absence(conn, empleado_id, fecha):
            continue
        entradas = [r for r in rows if (r["tipo"] or "").lower() == "entrada"]
        salidas = [r for r in rows if (r["tipo"] or "").lower() == "salida"]
        pausas = [r for r in rows if (r["tipo"] or "").lower() == "pausa"]
        reanudaciones = [r for r in rows if (r["tipo"] or "").lower() == "reanudacion"]
        if not entradas and not salidas:
            sin_fichar += 1
            continue
        if not entradas and salidas:
            sin_entrada += 1
        if entradas and not salidas:
            sin_salida += 1
        if len(reanudaciones) < len(pausas):
            pausa_sin_reanudar += 1
        for turno in emp_turnos:
            inicio = parse_time(fecha, turno["hora_inicio"])
            fin = parse_time(fecha, turno["hora_fin"])
            if inicio and fin and fin <= inicio:
                fin += timedelta(days=1)
            if inicio and entradas:
                entrada_dt = datetime.fromisoformat(str(entradas[0]["fecha_hora"]).replace("T", " ")[:19])
                early = int((inicio - entrada_dt).total_seconds() // 60)
                if early > 30:
                    minutos_extra += early
            if fin and salidas:
                salida_dt = datetime.fromisoformat(str(salidas[-1]["fecha_hora"]).replace("T", " ")[:19])
                late = int((salida_dt - fin).total_seconds() // 60)
                early_out = int((fin - salida_dt).total_seconds() // 60)
                if late > 30:
                    minutos_extra += late
                elif early_out > 30:
                    salidas_anticipadas += 1

    fichaje_sin_turno = len([emp for emp in empleados_fichados if emp not in empleados_turno])
    doble_turno = len([emp for emp, rows in empleados_turno.items() if len(rows) > 1])
    ausencias = conn.execute(
        """
        SELECT COUNT(*) total FROM vacaciones_rrhh
        WHERE empresa_id=? AND date(fecha_inicio)<=date(?) AND date(fecha_fin)>=date(?)
          AND estado='aprobada'
        """,
        (EMPRESA_ID, fecha, fecha),
    ).fetchone()["total"] if table_exists(conn, "vacaciones_rrhh") else 0
    extras_pendientes = conn.execute(
        """
        SELECT COUNT(*) total, COALESCE(SUM(minutos),0) minutos
        FROM tiempo_extra_rrhh
        WHERE empresa_id=? AND date(fecha)=date(?) AND estado='pendiente'
        """,
        (EMPRESA_ID, fecha),
    ).fetchone() if table_exists(conn, "tiempo_extra_rrhh") else {"total": 0, "minutos": 0}

    coste = round((sin_fichar * 85) + (sin_entrada + sin_salida) * 22 + fichaje_sin_turno * 18 + (minutos_extra / 60) * 18.5, 2)
    gravedad = sin_fichar * 3 + sin_entrada + sin_salida + fichaje_sin_turno + pausa_sin_reanudar + doble_turno + int(minutos_extra / 60)
    nivel = "critica" if gravedad >= 20 else "alta" if gravedad >= 8 else "media" if gravedad >= 3 else "ok"
    lectura = "RRHH estable."
    if nivel == "critica":
        lectura = "Dia critico: no calcular pre-nomina sin revisar fichajes, ausencias y refuerzos."
    elif nivel == "alta":
        lectura = "Dia tensionado: revisar responsables de turno, fichajes incompletos y extras."
    elif nivel == "media":
        lectura = "Hay incidencias controlables antes de cerrar jornada."
    data = {
        "corrida_id": corrida_id,
        "fecha": fecha,
        "turnos": len(turnos),
        "empleados_con_turno": len(empleados_turno),
        "fichajes": len(fichajes_rows),
        "empleados_fichados": len(empleados_fichados),
        "sin_fichar": sin_fichar,
        "sin_entrada": sin_entrada,
        "sin_salida": sin_salida,
        "fichaje_sin_turno": fichaje_sin_turno,
        "pausa_sin_reanudar": pausa_sin_reanudar,
        "doble_turno": doble_turno,
        "ausencias_aprobadas": int(ausencias or 0),
        "extras_pendientes": int(extras_pendientes["total"] or 0),
        "minutos_extra_pendientes": int(extras_pendientes["minutos"] or 0),
        "salidas_anticipadas": salidas_anticipadas,
        "coste_riesgo_estimado": coste,
        "nivel": nivel,
        "lectura": lectura,
    }
    conn.execute(
        """
        INSERT INTO simulador_rrhh_control_diario
        (corrida_id, fecha, turnos, empleados_con_turno, fichajes, empleados_fichados,
         sin_fichar, sin_entrada, sin_salida, fichaje_sin_turno, pausa_sin_reanudar,
         doble_turno, ausencias_aprobadas, extras_pendientes, minutos_extra_pendientes,
         salidas_anticipadas, coste_riesgo_estimado, nivel, lectura, empresa_id, centro_id)
        VALUES (:corrida_id, :fecha, :turnos, :empleados_con_turno, :fichajes, :empleados_fichados,
                :sin_fichar, :sin_entrada, :sin_salida, :fichaje_sin_turno, :pausa_sin_reanudar,
                :doble_turno, :ausencias_aprobadas, :extras_pendientes, :minutos_extra_pendientes,
                :salidas_anticipadas, :coste_riesgo_estimado, :nivel, :lectura, 1, 3)
        ON CONFLICT(corrida_id, fecha, centro_id) DO UPDATE SET
            turnos=excluded.turnos, empleados_con_turno=excluded.empleados_con_turno,
            fichajes=excluded.fichajes, empleados_fichados=excluded.empleados_fichados,
            sin_fichar=excluded.sin_fichar, sin_entrada=excluded.sin_entrada,
            sin_salida=excluded.sin_salida, fichaje_sin_turno=excluded.fichaje_sin_turno,
            pausa_sin_reanudar=excluded.pausa_sin_reanudar, doble_turno=excluded.doble_turno,
            ausencias_aprobadas=excluded.ausencias_aprobadas,
            extras_pendientes=excluded.extras_pendientes,
            minutos_extra_pendientes=excluded.minutos_extra_pendientes,
            salidas_anticipadas=excluded.salidas_anticipadas,
            coste_riesgo_estimado=excluded.coste_riesgo_estimado,
            nivel=excluded.nivel, lectura=excluded.lectura, updated_at=CURRENT_TIMESTAMP
        """,
        data,
    )
    return data


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        corrida = latest_corrida(conn)
        if not corrida:
            raise SystemExit("No hay corrida de simulacion.")
        corrida_id = args.corrida_id or int(corrida["id"])
        start = date.fromisoformat(args.desde or corrida["fecha_inicio"])
        end = date.fromisoformat(args.hasta or corrida["fecha_fin"])
        revisados = 0
        criticos = 0
        coste = 0.0
        for day in day_range(start, end):
            row = diagnose_day(conn, corrida_id, day)
            revisados += 1
            criticos += 1 if row["nivel"] in {"critica", "alta"} else 0
            coste += float(row["coste_riesgo_estimado"] or 0)
        conn.commit()
        print(f"RRHH diagnosticado: {revisados} dias, {criticos} dias tensionados, coste riesgo {coste:.2f} EUR.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Genera escenarios RRHH comparables desde el diagnostico real simulado.

No toca turnos ni fichajes. Crea una capa de lectura para saber si la ERP
aguanta con una operacion normal, una tensionada o un caos provocado.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "simulacion_hotel1_vida.db"


MODOS = {
    "normal": {"sin_fichar": 0.01, "fichaje_sin_turno": 0.005, "salidas_anticipadas": 0.01, "coste": 0.08},
    "tensionado": {"sin_fichar": 0.04, "fichaje_sin_turno": 0.015, "salidas_anticipadas": 0.03, "coste": 0.28},
    "caos": {"sin_fichar": 1.0, "fichaje_sin_turno": 1.0, "salidas_anticipadas": 1.0, "coste": 1.0},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crear escenarios RRHH para la simulacion.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--corrida-id", type=int, default=0)
    parser.add_argument("--empresa-id", type=int, default=1)
    parser.add_argument("--centro-id", type=int, default=3)
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
        CREATE TABLE IF NOT EXISTS simulador_rrhh_escenarios_diario (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            modo TEXT,
            fecha TEXT,
            turnos INTEGER DEFAULT 0,
            empleados_con_turno INTEGER DEFAULT 0,
            sin_fichar INTEGER DEFAULT 0,
            fichaje_sin_turno INTEGER DEFAULT 0,
            salidas_anticipadas INTEGER DEFAULT 0,
            coste_riesgo_estimado REAL DEFAULT 0,
            nivel TEXT,
            lectura TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, modo, fecha, centro_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS simulador_rrhh_escenarios_resumen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER,
            modo TEXT,
            dias INTEGER DEFAULT 0,
            dias_criticos INTEGER DEFAULT 0,
            dias_altos INTEGER DEFAULT 0,
            sin_fichar INTEGER DEFAULT 0,
            fichaje_sin_turno INTEGER DEFAULT 0,
            salidas_anticipadas INTEGER DEFAULT 0,
            coste_riesgo_estimado REAL DEFAULT 0,
            lectura TEXT,
            empresa_id INTEGER DEFAULT 1,
            centro_id INTEGER DEFAULT 3,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corrida_id, modo, centro_id)
        )
        """
    )


def nivel_para(gravedad: float) -> str:
    if gravedad >= 20:
        return "critica"
    if gravedad >= 8:
        return "alta"
    if gravedad >= 3:
        return "media"
    return "ok"


def lectura_para(modo: str, nivel: str) -> str:
    if nivel == "critica":
        return f"Escenario {modo}: no validar pre-nomina sin intervencion."
    if nivel == "alta":
        return f"Escenario {modo}: operacion tensionada, requiere jefe de turno revisando fichajes."
    if nivel == "media":
        return f"Escenario {modo}: incidencias asumibles si se corrigen antes del cierre."
    return f"Escenario {modo}: RRHH estable."


def generar(conn: sqlite3.Connection, corrida_id: int, empresa_id: int, centro_id: int) -> dict:
    if not table_exists(conn, "simulador_rrhh_control_diario"):
        raise SystemExit("Falta simulador_rrhh_control_diario. Ejecuta primero el diagnostico RRHH.")
    conn.execute("DELETE FROM simulador_rrhh_escenarios_diario WHERE corrida_id=? AND empresa_id=? AND centro_id=?", (corrida_id, empresa_id, centro_id))
    conn.execute("DELETE FROM simulador_rrhh_escenarios_resumen WHERE corrida_id=? AND empresa_id=? AND centro_id=?", (corrida_id, empresa_id, centro_id))
    base_rows = conn.execute(
        """
        SELECT *
        FROM simulador_rrhh_control_diario
        WHERE corrida_id=? AND empresa_id=? AND centro_id=?
        ORDER BY fecha
        """,
        (corrida_id, empresa_id, centro_id),
    ).fetchall()
    resumen = {}
    for modo, coef in MODOS.items():
        acc = {"dias": 0, "critica": 0, "alta": 0, "sin_fichar": 0, "fichaje_sin_turno": 0, "salidas_anticipadas": 0, "coste": 0.0}
        for row in base_rows:
            turnos = int(row["turnos"] or 0)
            empleados = int(row["empleados_con_turno"] or 0)
            if modo == "caos":
                sin_fichar = int(row["sin_fichar"] or 0)
                fichaje_sin_turno = int(row["fichaje_sin_turno"] or 0)
                salidas = int(row["salidas_anticipadas"] or 0)
            else:
                sin_fichar = min(int(row["sin_fichar"] or 0), max(0, round(empleados * coef["sin_fichar"])))
                fichaje_sin_turno = min(int(row["fichaje_sin_turno"] or 0), max(0, round(empleados * coef["fichaje_sin_turno"])))
                salidas = min(int(row["salidas_anticipadas"] or 0), max(0, round(empleados * coef["salidas_anticipadas"])))
            coste = round(float(row["coste_riesgo_estimado"] or 0) * coef["coste"], 2)
            gravedad = sin_fichar * 3 + fichaje_sin_turno + salidas
            nivel = nivel_para(gravedad)
            lectura = lectura_para(modo, nivel)
            conn.execute(
                """
                INSERT INTO simulador_rrhh_escenarios_diario
                (corrida_id, modo, fecha, turnos, empleados_con_turno, sin_fichar,
                 fichaje_sin_turno, salidas_anticipadas, coste_riesgo_estimado,
                 nivel, lectura, empresa_id, centro_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    corrida_id,
                    modo,
                    row["fecha"],
                    turnos,
                    empleados,
                    sin_fichar,
                    fichaje_sin_turno,
                    salidas,
                    coste,
                    nivel,
                    lectura,
                    empresa_id,
                    centro_id,
                ),
            )
            acc["dias"] += 1
            acc["critica"] += 1 if nivel == "critica" else 0
            acc["alta"] += 1 if nivel == "alta" else 0
            acc["sin_fichar"] += sin_fichar
            acc["fichaje_sin_turno"] += fichaje_sin_turno
            acc["salidas_anticipadas"] += salidas
            acc["coste"] += coste
        lectura = "RRHH estable en este modo."
        if acc["critica"]:
            lectura = f"{acc['critica']} dias criticos: este modo rompe pre-nomina y continuidad operativa."
        elif acc["alta"]:
            lectura = f"{acc['alta']} dias altos: viable con supervision diaria."
        conn.execute(
            """
            INSERT INTO simulador_rrhh_escenarios_resumen
            (corrida_id, modo, dias, dias_criticos, dias_altos, sin_fichar,
             fichaje_sin_turno, salidas_anticipadas, coste_riesgo_estimado,
             lectura, empresa_id, centro_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corrida_id,
                modo,
                acc["dias"],
                acc["critica"],
                acc["alta"],
                acc["sin_fichar"],
                acc["fichaje_sin_turno"],
                acc["salidas_anticipadas"],
                round(acc["coste"], 2),
                lectura,
                empresa_id,
                centro_id,
            ),
        )
        resumen[modo] = acc
    conn.commit()
    return resumen


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        corrida_id = args.corrida_id or latest_corrida(conn)
        if not corrida_id:
            raise SystemExit("No hay corrida para RRHH.")
        resumen = generar(conn, corrida_id, args.empresa_id, args.centro_id)
        print({"corrida_id": corrida_id, "modos": {k: {"dias": v["dias"], "criticos": v["critica"], "altos": v["alta"]} for k, v in resumen.items()}})
    finally:
        conn.close()


if __name__ == "__main__":
    main()
